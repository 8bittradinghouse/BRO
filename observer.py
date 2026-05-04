#!/usr/bin/env python3
"""Read-only Polymarket micro-markets observer.

Collects public CLOB market microstructure for selected token IDs:
- WebSocket stream (primary): market channel updates
- REST /book snapshot polling (fallback when WS is stale/down)
- BTC spot price sampling (Coinbase, Kraken fallback)

No authenticated endpoints are used.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import math
import pathlib
import signal
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from prodesk.config import _load_raw_with_extends

try:
    import websockets
except ImportError:  # pragma: no cover - exercised in dependency-missing environments
    websockets = None  # type: ignore[assignment]


LOG = logging.getLogger("observer")


DEFAULTS: Dict[str, Any] = {
    "bot_name": "Bro",
    "ws": {
        "url": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        "channel": "market",
        "stale_after_sec": 10,
        "ping_interval_sec": 20,
        "ping_timeout_sec": 20,
        "reconnect_backoff_initial_sec": 1.0,
        "reconnect_backoff_max_sec": 30.0,
    },
    "rest": {
        "clob_url": "https://clob.polymarket.com",
        "book_path": "/book",
        "poll_interval_sec": 1.0,
        "timeout_sec": 8,
        "max_retries": 3,
    },
    "gamma": {
        "url": "https://gamma-api.polymarket.com",
        "markets_path": "/markets",
        "timeout_sec": 10,
        "page_limit": 200,
        "max_pages": 10,
    },
    "targets": {
        "manual_token_ids": [],
        "discovery": {
            "enabled": False,
            "symbols": ["BTC", "ETH", "SOL", "XRP"],
            "keywords_any": ["5 minute", "5-minute", "up or down"],
            "tags_any": [],
            "max_markets": 500,
        },
    },
    "spot": {
        "enabled": True,
        "interval_sec": 1.0,
        "providers": ["coinbase", "kraken"],
    },
    "storage": {
        "log_dir": "./logs",
        "parquet_on_exit": False,
        "parquet_path": "./logs/market_combined.parquet",
    },
    "runtime": {
        "status_interval_sec": 30.0,
    },
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def parse_iso_or_epoch(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000.0
        try:
            return dt.datetime.fromtimestamp(raw, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def to_iso_utc(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_side(label: Any, index: Optional[int] = None) -> str:
    if isinstance(label, str):
        text = label.strip().upper()
        if text in {"YES", "Y", "UP", "TRUE", "LONG"}:
            return "YES"
        if text in {"NO", "N", "DOWN", "FALSE", "SHORT"}:
            return "NO"
    if index == 0:
        return "YES"
    if index == 1:
        return "NO"
    return "UNKNOWN"


def normalize_trade_side(label: Any) -> Optional[str]:
    if not isinstance(label, str):
        return None
    text = label.strip().upper()
    if text in {"BUY", "BID", "TAKER_BUY"}:
        return "BUY"
    if text in {"SELL", "ASK", "TAKER_SELL"}:
        return "SELL"
    return text or None


def parse_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def extract_tag_strings(tags_value: Any) -> List[str]:
    tags = []
    for item in parse_json_list(tags_value):
        if isinstance(item, str):
            tags.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "slug", "id"):
                maybe = item.get(key)
                if isinstance(maybe, str) and maybe.strip():
                    tags.append(maybe.strip())
    return tags


def extract_symbol(text: str) -> Optional[str]:
    upper = text.upper()
    for sym in ("BTC", "ETH", "SOL", "XRP"):
        if sym in upper:
            return sym
    return None


def deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def best_level(levels: Any, is_bid: bool) -> Tuple[Optional[float], Optional[float]]:
    rows = parse_json_list(levels)
    best_price: Optional[float] = None
    best_size: Optional[float] = None
    for row in rows:
        price: Optional[float] = None
        size: Optional[float] = None
        if isinstance(row, dict):
            price = parse_float(first_non_none(row.get("price"), row.get("p"), row.get("px")))
            size = parse_float(
                first_non_none(
                    row.get("size"),
                    row.get("s"),
                    row.get("amount"),
                    row.get("quantity"),
                    row.get("shares"),
                    row.get("qty"),
                )
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = parse_float(row[0])
            size = parse_float(row[1])
        if price is None:
            continue
        if best_price is None:
            best_price, best_size = price, size
            continue
        if is_bid and price > best_price:
            best_price, best_size = price, size
        if (not is_bid) and price < best_price:
            best_price, best_size = price, size
    return best_price, best_size


def market_text_blob(market: Dict[str, Any]) -> str:
    parts = []
    for key in ("question", "title", "slug", "description"):
        val = market.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    parts.extend(extract_tag_strings(market.get("tags")))
    return " | ".join(parts)


def parse_end_time(market: Dict[str, Any]) -> Optional[dt.datetime]:
    for key in ("endDateIso", "endDate", "end_date", "end_time", "expiration_time"):
        parsed = parse_iso_or_epoch(market.get(key))
        if parsed is not None:
            return parsed
    return None


@dataclasses.dataclass
class TokenMeta:
    token_id: str
    side: str = "UNKNOWN"
    condition_id: Optional[str] = None
    market_id: Optional[str] = None
    market_slug: Optional[str] = None
    question: Optional[str] = None
    end_time: Optional[dt.datetime] = None
    symbol: Optional[str] = None

    def merge_missing(self, other: "TokenMeta") -> None:
        if self.side == "UNKNOWN" and other.side in {"YES", "NO"}:
            self.side = other.side
        if self.condition_id is None and other.condition_id:
            self.condition_id = other.condition_id
        if self.market_id is None and other.market_id:
            self.market_id = other.market_id
        if self.market_slug is None and other.market_slug:
            self.market_slug = other.market_slug
        if self.question is None and other.question:
            self.question = other.question
        if self.end_time is None and other.end_time:
            self.end_time = other.end_time
        if self.symbol is None and other.symbol:
            self.symbol = other.symbol


@dataclasses.dataclass
class TokenState:
    best_bid_price: Optional[float] = None
    best_bid_size: Optional[float] = None
    best_ask_price: Optional[float] = None
    best_ask_size: Optional[float] = None
    last_trade_price: Optional[float] = None
    last_trade_side: Optional[str] = None
    last_trade_ts: Optional[str] = None
    last_update_monotonic: float = 0.0
    ws_message_count: int = 0


class DailyJsonlWriter:
    def __init__(self, log_dir: pathlib.Path, prefix: str):
        self.log_dir = log_dir
        self.prefix = prefix
        self._fh: Optional[Any] = None
        self._day: Optional[str] = None
        self.current_path: Optional[pathlib.Path] = None
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_open(self) -> None:
        day = utc_now().date().isoformat()
        if self._fh is not None and self._day == day:
            return
        self.close()
        self._day = day
        self.current_path = self.log_dir / f"{self.prefix}_{day}.jsonl"
        self._fh = self.current_path.open("a", encoding="utf-8")

    def write(self, record: Dict[str, Any]) -> None:
        self._ensure_open()
        assert self._fh is not None
        self._fh.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
        self._fh = None


def http_get_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 10,
    max_retries: int = 3,
) -> Any:
    delay = 0.5
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout_sec)
        except requests.RequestException:
            if attempt >= max_retries:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
            continue

        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()
            retry_after = parse_float(resp.headers.get("Retry-After"))
            sleep_for = delay if retry_after is None else max(0.0, min(retry_after, 30.0))
            time.sleep(sleep_for)
            delay = min(delay * 2, 10.0)
            continue

        if resp.status_code >= 500 and attempt < max_retries:
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
            continue

        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {url}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


def token_metas_from_market(market: Dict[str, Any]) -> List[TokenMeta]:
    token_ids = [str(x) for x in parse_json_list(market.get("clobTokenIds") or market.get("clob_token_ids"))]
    outcomes = [str(x) for x in parse_json_list(market.get("outcomes"))]
    if not token_ids:
        return []

    condition_id = first_non_none(market.get("conditionId"), market.get("condition_id"))
    condition_id = str(condition_id) if condition_id is not None else None
    market_id = market.get("id")
    market_id = str(market_id) if market_id is not None else None
    slug = market.get("slug")
    slug = str(slug) if isinstance(slug, str) else None
    question = first_non_none(market.get("question"), market.get("title"))
    question = str(question) if isinstance(question, str) else None
    end_time = parse_end_time(market)
    blob = market_text_blob(market)
    symbol = extract_symbol(blob)

    metas: List[TokenMeta] = []
    for idx, token_id in enumerate(token_ids):
        outcome = outcomes[idx] if idx < len(outcomes) else None
        side = normalize_side(outcome, idx)
        metas.append(
            TokenMeta(
                token_id=token_id,
                side=side,
                condition_id=condition_id,
                market_id=market_id,
                market_slug=slug,
                question=question,
                end_time=end_time,
                symbol=symbol,
            )
        )
    return metas


def market_matches_filters(
    market: Dict[str, Any],
    symbols: List[str],
    keywords_any: List[str],
    tags_any: List[str],
) -> bool:
    blob = market_text_blob(market).upper()
    market_tags = [x.upper() for x in extract_tag_strings(market.get("tags"))]

    if symbols:
        if not any(sym.upper() in blob for sym in symbols):
            return False

    if keywords_any:
        if not any(keyword.upper() in blob for keyword in keywords_any):
            return False

    if tags_any:
        if not any(tag.upper() in market_tags for tag in tags_any):
            return False

    return True


def discover_tokens(
    config: Dict[str, Any],
    session: requests.Session,
    symbols_override: Optional[List[str]],
) -> Dict[str, TokenMeta]:
    discovery_cfg = config["targets"]["discovery"]
    if not discovery_cfg.get("enabled", False):
        return {}

    gamma_cfg = config["gamma"]
    gamma_base = str(gamma_cfg["url"]).rstrip("/")
    markets_path = str(gamma_cfg["markets_path"])
    markets_url = f"{gamma_base}{markets_path}"
    page_limit = int(gamma_cfg["page_limit"])
    max_pages = int(gamma_cfg["max_pages"])
    timeout_sec = float(gamma_cfg["timeout_sec"])

    symbols = symbols_override or [str(x).upper() for x in discovery_cfg.get("symbols", [])]
    keywords_any = [str(x) for x in discovery_cfg.get("keywords_any", [])]
    tags_any = [str(x) for x in discovery_cfg.get("tags_any", [])]
    max_markets = int(discovery_cfg.get("max_markets", 0) or 0)

    discovered: Dict[str, TokenMeta] = {}
    scanned_markets = 0

    for page_idx in range(max_pages):
        offset = page_idx * page_limit
        params = {
            "active": True,
            "closed": False,
            "archived": False,
            "limit": page_limit,
            "offset": offset,
        }
        try:
            payload = http_get_json(session, markets_url, params=params, timeout_sec=timeout_sec, max_retries=3)
        except Exception as exc:
            LOG.warning("Discovery request failed at page %s: %s", page_idx, exc)
            break
        if isinstance(payload, dict):
            markets = payload.get("markets") or payload.get("data") or []
        else:
            markets = payload
        if not isinstance(markets, list) or not markets:
            break

        for market in markets:
            if not isinstance(market, dict):
                continue
            scanned_markets += 1
            if max_markets and scanned_markets > max_markets:
                break
            if not market_matches_filters(market, symbols, keywords_any, tags_any):
                continue
            for meta in token_metas_from_market(market):
                existing = discovered.get(meta.token_id)
                if existing is None:
                    discovered[meta.token_id] = meta
                else:
                    existing.merge_missing(meta)

        if max_markets and scanned_markets > max_markets:
            break
        if len(markets) < page_limit:
            break

    LOG.info("Discovery scanned %s markets, selected %s token IDs", scanned_markets, len(discovered))
    return discovered


def manual_tokens_from_config(config: Dict[str, Any]) -> Dict[str, TokenMeta]:
    targets_cfg = config.get("targets", {})
    entries = targets_cfg.get("manual_token_ids")
    if entries is None:
        entries = targets_cfg.get("manual_tokens", [])
    if not isinstance(entries, list):
        return {}

    tokens: Dict[str, TokenMeta] = {}
    for entry in entries:
        if isinstance(entry, str):
            token_id = entry.strip()
            if token_id:
                tokens[token_id] = TokenMeta(token_id=token_id)
            continue
        if isinstance(entry, dict):
            token_id = entry.get("token_id") or entry.get("id")
            if token_id is None:
                continue
            token_id_str = str(token_id).strip()
            if not token_id_str:
                continue
            side = normalize_side(entry.get("side"))
            end_time = parse_iso_or_epoch(entry.get("end_time"))
            tokens[token_id_str] = TokenMeta(
                token_id=token_id_str,
                side=side,
                condition_id=str(entry.get("condition_id")) if entry.get("condition_id") is not None else None,
                market_id=str(entry.get("market_id")) if entry.get("market_id") is not None else None,
                market_slug=str(entry.get("market_slug")) if entry.get("market_slug") is not None else None,
                question=str(entry.get("question")) if entry.get("question") is not None else None,
                end_time=end_time,
                symbol=str(entry.get("symbol")).upper() if entry.get("symbol") is not None else None,
            )
    return tokens


def enrich_tokens_with_gamma(config: Dict[str, Any], session: requests.Session, tokens: Dict[str, TokenMeta]) -> None:
    if not tokens:
        return
    gamma_cfg = config["gamma"]
    gamma_base = str(gamma_cfg["url"]).rstrip("/")
    markets_path = str(gamma_cfg["markets_path"])
    markets_url = f"{gamma_base}{markets_path}"
    timeout_sec = float(gamma_cfg["timeout_sec"])

    for token_id, meta in tokens.items():
        needs = meta.side not in {"YES", "NO"} or meta.condition_id is None or meta.market_id is None or meta.symbol is None
        if not needs:
            continue
        params = {"clob_token_ids": token_id, "active": True, "limit": 10}
        try:
            payload = http_get_json(session, markets_url, params=params, timeout_sec=timeout_sec, max_retries=2)
        except requests.RequestException:
            continue
        except Exception:
            continue

        if isinstance(payload, dict):
            markets = payload.get("markets") or payload.get("data") or []
        else:
            markets = payload
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict):
                continue
            for candidate in token_metas_from_market(market):
                if candidate.token_id == token_id:
                    meta.merge_missing(candidate)
                    break


def apply_symbol_filter(tokens: Dict[str, TokenMeta], symbols: Optional[List[str]]) -> Dict[str, TokenMeta]:
    if not symbols:
        return tokens
    symbols_upper = [x.upper() for x in symbols]
    out: Dict[str, TokenMeta] = {}
    for token_id, meta in tokens.items():
        blob = " ".join(
            [
                meta.symbol or "",
                meta.question or "",
                meta.market_slug or "",
            ]
        ).upper()
        if any(sym in blob for sym in symbols_upper):
            out[token_id] = meta
            continue
        # If metadata is sparse, keep manual token IDs instead of dropping silently.
        if not blob.strip():
            out[token_id] = meta
    return out


def load_config(path: pathlib.Path) -> Dict[str, Any]:
    user_cfg, _ = _load_raw_with_extends(path.resolve())
    if not isinstance(user_cfg, dict):
        raise ValueError("config root must be a mapping")
    return deep_merge(DEFAULTS, user_cfg)


def _require_positive_number(name: str, value: Any, allow_zero: bool = False) -> float:
    parsed = parse_float(value)
    if parsed is None:
        raise ValueError(f"{name} must be a number, got {value!r}")
    if allow_zero:
        if parsed < 0:
            raise ValueError(f"{name} must be >= 0, got {parsed}")
        return parsed
    if parsed <= 0:
        raise ValueError(f"{name} must be > 0, got {parsed}")
    return parsed


def validate_config(config: Dict[str, Any]) -> None:
    bot_name = str(config.get("bot_name", "")).strip()
    if not bot_name:
        raise ValueError("bot_name must be a non-empty string")

    _require_positive_number("ws.stale_after_sec", config["ws"]["stale_after_sec"])
    _require_positive_number("ws.ping_interval_sec", config["ws"]["ping_interval_sec"])
    _require_positive_number("ws.ping_timeout_sec", config["ws"]["ping_timeout_sec"])
    _require_positive_number("ws.reconnect_backoff_initial_sec", config["ws"]["reconnect_backoff_initial_sec"])
    _require_positive_number("ws.reconnect_backoff_max_sec", config["ws"]["reconnect_backoff_max_sec"])
    _require_positive_number("rest.poll_interval_sec", config["rest"]["poll_interval_sec"])
    _require_positive_number("rest.timeout_sec", config["rest"]["timeout_sec"])
    _require_positive_number("rest.max_retries", config["rest"]["max_retries"], allow_zero=True)
    _require_positive_number("gamma.timeout_sec", config["gamma"]["timeout_sec"])
    _require_positive_number("gamma.page_limit", config["gamma"]["page_limit"])
    _require_positive_number("gamma.max_pages", config["gamma"]["max_pages"])
    _require_positive_number("spot.interval_sec", config["spot"]["interval_sec"])
    _require_positive_number("runtime.status_interval_sec", config["runtime"]["status_interval_sec"])

    if not isinstance(config["ws"]["url"], str) or not config["ws"]["url"].strip():
        raise ValueError("ws.url must be a non-empty string")
    if not isinstance(config["rest"]["clob_url"], str) or not config["rest"]["clob_url"].strip():
        raise ValueError("rest.clob_url must be a non-empty string")
    if not isinstance(config["gamma"]["url"], str) or not config["gamma"]["url"].strip():
        raise ValueError("gamma.url must be a non-empty string")
    if float(config["ws"]["reconnect_backoff_max_sec"]) < float(config["ws"]["reconnect_backoff_initial_sec"]):
        raise ValueError("ws.reconnect_backoff_max_sec must be >= ws.reconnect_backoff_initial_sec")
    if float(config["ws"]["ping_interval_sec"]) > float(config["ws"]["ping_timeout_sec"]):
        raise ValueError("ws.ping_interval_sec must be <= ws.ping_timeout_sec")


def _new_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "polymarket-bro-observer/0.1"})
    return session


def maybe_export_parquet(log_dir: pathlib.Path, parquet_path: pathlib.Path) -> None:
    market_files = sorted(log_dir.glob("market_*.jsonl"))
    if not market_files:
        LOG.info("Parquet export skipped: no market_*.jsonl files in %s", log_dir)
        return
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        LOG.warning("pyarrow not installed, skipping parquet export")
        return

    rows: List[Dict[str, Any]] = []
    for file_path in market_files:
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        LOG.info("Parquet export skipped: market files were empty")
        return

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, parquet_path)
    LOG.info("Wrote parquet: %s (%s rows)", parquet_path, len(rows))


class Observer:
    def __init__(self, config: Dict[str, Any], token_metas: Dict[str, TokenMeta]):
        self.config = config
        self.bot_name = str(config.get("bot_name", "Bro")).strip() or "Bro"
        self.token_metas = token_metas
        self.token_states: Dict[str, TokenState] = {token_id: TokenState() for token_id in token_metas}

        storage_cfg = config["storage"]
        self.log_dir = pathlib.Path(storage_cfg["log_dir"]).resolve()
        self.market_writer = DailyJsonlWriter(self.log_dir, "market")
        self.spot_writer = DailyJsonlWriter(self.log_dir, "spot")

        ws_cfg = config["ws"]
        self.ws_url = str(ws_cfg["url"])
        self.ws_channel = str(ws_cfg["channel"])
        self.ws_stale_after_sec = float(ws_cfg["stale_after_sec"])
        self.ws_ping_interval_sec = float(ws_cfg["ping_interval_sec"])
        self.ws_ping_timeout_sec = float(ws_cfg["ping_timeout_sec"])
        self.ws_backoff_initial = float(ws_cfg["reconnect_backoff_initial_sec"])
        self.ws_backoff_max = float(ws_cfg["reconnect_backoff_max_sec"])

        rest_cfg = config["rest"]
        self.rest_book_url = f"{str(rest_cfg['clob_url']).rstrip('/')}{str(rest_cfg['book_path'])}"
        self.rest_poll_interval_sec = float(rest_cfg["poll_interval_sec"])
        self.rest_timeout_sec = float(rest_cfg["timeout_sec"])
        self.rest_max_retries = int(rest_cfg["max_retries"])

        spot_cfg = config["spot"]
        self.spot_enabled = bool(spot_cfg.get("enabled", True))
        self.spot_interval_sec = float(spot_cfg["interval_sec"])
        self.spot_providers = [str(x).lower() for x in spot_cfg.get("providers", ["coinbase", "kraken"])]

        runtime_cfg = config["runtime"]
        self.status_interval_sec = float(runtime_cfg["status_interval_sec"])

        self.rest_session = _new_http_session()
        self.spot_session = _new_http_session()

        self.stop_event = asyncio.Event()
        self.ws_connected = False
        self.ws_last_msg_monotonic = time.monotonic()
        self.rest_fallback_active = False

        self.ws_messages_received = 0
        self.rest_poll_requests = 0
        self.spot_samples = 0
        self.reconnect_count = 0

    @staticmethod
    def _pick_present_value(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Tuple[bool, Any]:
        for key in keys:
            if key in payload:
                return True, payload.get(key)
        return False, None

    @staticmethod
    def _looks_like_trade_payload(payload: Dict[str, Any], event_type: Optional[str]) -> bool:
        if event_type and "trade" in event_type:
            return True
        trade_keys = (
            "last_trade_price",
            "last_trade_ts",
            "last_trade_side",
            "taker_side",
            "trade_id",
            "trade",
            "trades",
        )
        return any(key in payload for key in trade_keys)

    def request_stop(self) -> None:
        if not self.stop_event.is_set():
            LOG.info("Shutdown requested")
            self.stop_event.set()

    def _seconds_to_expiry(self, meta: TokenMeta, now: dt.datetime) -> Optional[float]:
        if meta.end_time is None:
            return None
        return (meta.end_time - now).total_seconds()

    def _build_record(
        self,
        token_id: str,
        source: str,
        event_type: Optional[str],
        trade_event: bool,
    ) -> Dict[str, Any]:
        now = utc_now()
        state = self.token_states[token_id]
        meta = self.token_metas[token_id]
        midpoint = None
        spread = None
        if state.best_bid_price is not None and state.best_ask_price is not None:
            midpoint = (state.best_bid_price + state.best_ask_price) / 2.0
            spread = state.best_ask_price - state.best_bid_price
        return {
            "bot_name": self.bot_name,
            "ts_utc": to_iso_utc(now),
            "market_id": meta.market_id,
            "condition_id": meta.condition_id,
            "token_id": token_id,
            "side": meta.side,
            "best_bid_price": state.best_bid_price,
            "best_bid_size": state.best_bid_size,
            "best_ask_price": state.best_ask_price,
            "best_ask_size": state.best_ask_size,
            "midpoint": midpoint,
            "spread": spread,
            "last_trade_price": state.last_trade_price,
            "last_trade_side": state.last_trade_side,
            "last_trade_ts": state.last_trade_ts,
            "seconds_to_expiry": self._seconds_to_expiry(meta, now),
            "source": source,
            "event_type": event_type,
            "trade_event": trade_event,
            "symbol": meta.symbol,
            "market_slug": meta.market_slug,
            "question": meta.question,
        }

    def _update_quotes_from_payload(self, state: TokenState, payload: Dict[str, Any]) -> bool:
        changed = False

        has_bid_levels, bid_levels = self._pick_present_value(payload, ("bids", "buys"))
        has_ask_levels, ask_levels = self._pick_present_value(payload, ("asks", "sells"))

        bid_price, bid_size = best_level(bid_levels, is_bid=True)
        ask_price, ask_size = best_level(ask_levels, is_bid=False)

        has_direct_bid, direct_bid_raw = self._pick_present_value(payload, ("best_bid", "bestBid"))
        has_direct_ask, direct_ask_raw = self._pick_present_value(payload, ("best_ask", "bestAsk"))
        has_direct_bid_size, direct_bid_size_raw = self._pick_present_value(payload, ("best_bid_size", "bestBidSize"))
        has_direct_ask_size, direct_ask_size_raw = self._pick_present_value(payload, ("best_ask_size", "bestAskSize"))

        direct_bid = parse_float(direct_bid_raw)
        direct_ask = parse_float(direct_ask_raw)
        direct_bid_size = parse_float(direct_bid_size_raw)
        direct_ask_size = parse_float(direct_ask_size_raw)

        if has_direct_bid:
            if direct_bid is not None:
                bid_price = direct_bid
            elif direct_bid_raw is None and state.best_bid_price is not None:
                state.best_bid_price = None
                changed = True
        if has_direct_ask:
            if direct_ask is not None:
                ask_price = direct_ask
            elif direct_ask_raw is None and state.best_ask_price is not None:
                state.best_ask_price = None
                changed = True
        if has_direct_bid_size:
            if direct_bid_size is not None:
                bid_size = direct_bid_size
            elif direct_bid_size_raw is None and state.best_bid_size is not None:
                state.best_bid_size = None
                changed = True
        if has_direct_ask_size:
            if direct_ask_size is not None:
                ask_size = direct_ask_size
            elif direct_ask_size_raw is None and state.best_ask_size is not None:
                state.best_ask_size = None
                changed = True

        if has_bid_levels and bid_price is None and state.best_bid_price is not None:
            state.best_bid_price = None
            changed = True
        if has_bid_levels and bid_size is None and state.best_bid_size is not None:
            state.best_bid_size = None
            changed = True
        if has_ask_levels and ask_price is None and state.best_ask_price is not None:
            state.best_ask_price = None
            changed = True
        if has_ask_levels and ask_size is None and state.best_ask_size is not None:
            state.best_ask_size = None
            changed = True

        if bid_price is not None and bid_price != state.best_bid_price:
            state.best_bid_price = bid_price
            changed = True
        if bid_size is not None and bid_size != state.best_bid_size:
            state.best_bid_size = bid_size
            changed = True
        if ask_price is not None and ask_price != state.best_ask_price:
            state.best_ask_price = ask_price
            changed = True
        if ask_size is not None and ask_size != state.best_ask_size:
            state.best_ask_size = ask_size
            changed = True

        return changed

    def _update_trade_from_payload(
        self,
        state: TokenState,
        payload: Dict[str, Any],
        is_trade_payload: bool,
    ) -> bool:
        if not is_trade_payload:
            return False
        changed = False
        price = parse_float(first_non_none(payload.get("last_trade_price"), payload.get("price")))
        side = normalize_trade_side(
            first_non_none(payload.get("last_trade_side"), payload.get("taker_side"), payload.get("side"))
        )
        raw_ts = first_non_none(
            payload.get("last_trade_ts"),
            payload.get("trade_ts"),
            payload.get("timestamp"),
            payload.get("ts"),
            payload.get("time"),
        )
        trade_dt = parse_iso_or_epoch(raw_ts)
        trade_ts = to_iso_utc(trade_dt) if trade_dt is not None else None

        if price is not None and price != state.last_trade_price:
            state.last_trade_price = price
            changed = True
        if side is not None and side != state.last_trade_side:
            state.last_trade_side = side
            changed = True
        if trade_ts is not None and trade_ts != state.last_trade_ts:
            state.last_trade_ts = trade_ts
            changed = True
        return changed

    async def _process_market_payload(self, payload: Dict[str, Any], source: str) -> None:
        token_id_raw = first_non_none(payload.get("asset_id"), payload.get("token_id"))
        if token_id_raw is None:
            return
        token_id = str(token_id_raw)
        if token_id not in self.token_states:
            return

        raw_event_type = first_non_none(payload.get("event_type"), payload.get("type"))
        event_type = str(raw_event_type).strip().lower() if raw_event_type is not None else None
        if event_type == "":
            event_type = None
        state = self.token_states[token_id]
        is_trade_payload = self._looks_like_trade_payload(payload, event_type)

        quotes_changed = self._update_quotes_from_payload(state, payload)
        trade_changed = self._update_trade_from_payload(state, payload, is_trade_payload)
        changed = quotes_changed or trade_changed or is_trade_payload
        if source == "rest":
            has_snapshot_keys = any(
                key in payload
                for key in (
                    "bids",
                    "asks",
                    "buys",
                    "sells",
                    "best_bid",
                    "best_ask",
                    "best_bid_size",
                    "best_ask_size",
                    "price",
                    "last_trade_price",
                )
            )
            changed = changed or has_snapshot_keys
        if not changed:
            return

        state.last_update_monotonic = time.monotonic()
        if source == "ws":
            state.ws_message_count += 1
            self.ws_messages_received += 1

        record = self._build_record(
            token_id=token_id,
            source=source,
            event_type=event_type,
            trade_event=is_trade_payload,
        )
        self.market_writer.write(record)

    async def _handle_ws_obj(self, ws: Any, obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                await self._handle_ws_obj(ws, item)
            return
        if not isinstance(obj, dict):
            return

        msg_type = str(obj.get("type") or obj.get("event_type") or "").lower()
        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return

        await self._process_market_payload(obj, source="ws")

        for key in ("data", "message", "events", "payload"):
            nested = obj.get(key)
            if nested is not None:
                await self._handle_ws_obj(ws, nested)

    async def _ws_loop(self) -> None:
        if websockets is None:
            raise RuntimeError(
                "Missing dependency: websockets. Install with `pip install -r requirements.txt` before running observer."
            )

        token_ids = list(self.token_metas.keys())
        if not token_ids:
            raise RuntimeError("No token IDs configured")

        backoff = self.ws_backoff_initial
        ws_recv_timeout = max(1.0, min(10.0, self.ws_stale_after_sec / 2.0))
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.ws_ping_interval_sec,
                    ping_timeout=self.ws_ping_timeout_sec,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    subscribe_msg = {"type": self.ws_channel, "assets_ids": token_ids}
                    await ws.send(json.dumps(subscribe_msg))
                    self.ws_connected = True
                    self.ws_last_msg_monotonic = time.monotonic()
                    backoff = self.ws_backoff_initial
                    LOG.info("WebSocket connected and subscribed (%s tokens)", len(token_ids))

                    while not self.stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=ws_recv_timeout)
                        except asyncio.TimeoutError:
                            pong_waiter = await ws.ping()
                            await asyncio.wait_for(pong_waiter, timeout=self.ws_ping_timeout_sec)
                            # Treat successful ping/pong as healthy transport activity.
                            self.ws_last_msg_monotonic = time.monotonic()
                            continue

                        self.ws_last_msg_monotonic = time.monotonic()
                        if isinstance(raw, bytes):
                            text = raw.decode("utf-8", errors="ignore")
                        else:
                            text = str(raw)

                        text_strip = text.strip().lower()
                        if text_strip == "ping":
                            await ws.send("pong")
                            continue

                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_ws_obj(ws, parsed)

            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.ws_connected = False
                self.reconnect_count += 1
                LOG.warning("WebSocket disconnected: %s | reconnect in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, self.ws_backoff_max)
            finally:
                self.ws_connected = False

    async def _poll_rest_once(self, token_id: str) -> None:
        params = {"token_id": token_id}
        try:
            payload = await asyncio.to_thread(
                http_get_json,
                self.rest_session,
                self.rest_book_url,
                params=params,
                timeout_sec=self.rest_timeout_sec,
                max_retries=self.rest_max_retries,
            )
        except Exception as exc:
            LOG.warning("REST /book failed for token %s: %s", token_id, exc)
            return

        if not isinstance(payload, dict):
            return
        if "token_id" not in payload and "asset_id" not in payload:
            payload["token_id"] = token_id
        await self._process_market_payload(payload, source="rest")
        self.rest_poll_requests += 1

    async def _rest_fallback_loop(self) -> None:
        while not self.stop_event.is_set():
            now_mono = time.monotonic()
            ws_age = now_mono - self.ws_last_msg_monotonic
            stale_ws = ws_age > self.ws_stale_after_sec

            if stale_ws:
                if not self.rest_fallback_active:
                    self.rest_fallback_active = True
                    LOG.warning(
                        "WS stale/down for %.1fs (threshold %.1fs): enabling REST fallback polling",
                        ws_age,
                        self.ws_stale_after_sec,
                    )
                started = time.monotonic()
                for token_id in self.token_metas.keys():
                    if self.stop_event.is_set():
                        break
                    await self._poll_rest_once(token_id)
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self.rest_poll_interval_sec - elapsed))
                continue

            if self.rest_fallback_active:
                self.rest_fallback_active = False
                LOG.info("WS healthy: disabling REST fallback")
            await asyncio.sleep(0.5)

    async def _fetch_coinbase_spot(self) -> Optional[float]:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        payload = await asyncio.to_thread(
            http_get_json,
            self.spot_session,
            url,
            params=None,
            timeout_sec=6,
            max_retries=1,
        )
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return parse_float(data.get("amount"))

    async def _fetch_kraken_spot(self) -> Optional[float]:
        url = "https://api.kraken.com/0/public/Ticker"
        payload = await asyncio.to_thread(
            http_get_json,
            self.spot_session,
            url,
            params={"pair": "XBTUSD"},
            timeout_sec=6,
            max_retries=1,
        )
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict) or not result:
            return None
        first = next(iter(result.values()))
        if not isinstance(first, dict):
            return None
        close_arr = first.get("c")
        if isinstance(close_arr, list) and close_arr:
            return parse_float(close_arr[0])
        return None

    async def _fetch_spot_price(self) -> Tuple[Optional[float], Optional[str]]:
        for provider in self.spot_providers:
            try:
                if provider == "coinbase":
                    price = await self._fetch_coinbase_spot()
                elif provider == "kraken":
                    price = await self._fetch_kraken_spot()
                else:
                    continue
                if price is not None:
                    return price, provider
            except Exception as exc:
                LOG.warning("Spot provider %s failed: %s", provider, exc)
        return None, None

    async def _spot_loop(self) -> None:
        if not self.spot_enabled:
            LOG.info("BTC spot sampling disabled")
            return
        while not self.stop_event.is_set():
            started = time.monotonic()
            price, provider = await self._fetch_spot_price()
            if price is not None:
                self.spot_writer.write(
                    {
                        "ts_utc": utc_now_iso(),
                        "symbol": "BTCUSD",
                        "price": price,
                        "provider": provider,
                    }
                )
                self.spot_samples += 1
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, self.spot_interval_sec - elapsed))

    async def _status_loop(self) -> None:
        while not self.stop_event.is_set():
            ages = []
            now_mono = time.monotonic()
            for token_id, state in self.token_states.items():
                if state.last_update_monotonic <= 0:
                    age_txt = "never"
                else:
                    age_txt = f"{now_mono - state.last_update_monotonic:.1f}s"
                ages.append(f"{token_id}:{age_txt}")

            LOG.info(
                "status bot=%s ws_msgs=%s reconnects=%s ws_connected=%s rest_fallback=%s rest_polls=%s spot_samples=%s market_file=%s spot_file=%s token_ages=%s",
                self.bot_name,
                self.ws_messages_received,
                self.reconnect_count,
                self.ws_connected,
                self.rest_fallback_active,
                self.rest_poll_requests,
                self.spot_samples,
                str(self.market_writer.current_path) if self.market_writer.current_path else "n/a",
                str(self.spot_writer.current_path) if self.spot_writer.current_path else "n/a",
                ", ".join(ages),
            )
            await asyncio.sleep(self.status_interval_sec)

    async def run(self, duration_min: Optional[float]) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.request_stop)

        tasks = [
            asyncio.create_task(self._ws_loop(), name="ws_loop"),
            asyncio.create_task(self._rest_fallback_loop(), name="rest_fallback"),
            asyncio.create_task(self._status_loop(), name="status"),
        ]
        if self.spot_enabled:
            tasks.append(asyncio.create_task(self._spot_loop(), name="spot_loop"))

        def _task_done(task: asyncio.Task[Any]) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                LOG.error("Task %s crashed: %s", task.get_name(), exc)
                self.request_stop()

        for task in tasks:
            task.add_done_callback(_task_done)

        timer_task: Optional[asyncio.Task[Any]] = None
        if duration_min is not None and duration_min > 0:
            duration_sec = duration_min * 60.0

            async def stop_later() -> None:
                await asyncio.sleep(duration_sec)
                LOG.info("Duration reached (%.2f min), stopping", duration_min)
                self.request_stop()

            timer_task = asyncio.create_task(stop_later(), name="duration_timer")

        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        if timer_task is not None:
            timer_task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        if timer_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await timer_task

        self.market_writer.close()
        self.spot_writer.close()
        self.rest_session.close()
        self.spot_session.close()

        storage_cfg = self.config["storage"]
        if bool(storage_cfg.get("parquet_on_exit", False)):
            parquet_path = pathlib.Path(storage_cfg.get("parquet_path", "./logs/market_combined.parquet")).resolve()
            maybe_export_parquet(self.log_dir, parquet_path)


def build_target_tokens(config: Dict[str, Any], symbols_override: Optional[List[str]]) -> Dict[str, TokenMeta]:
    session = _new_http_session()
    try:
        manual = manual_tokens_from_config(config)
        discovered = discover_tokens(config, session, symbols_override)

        merged: Dict[str, TokenMeta] = {}
        for source in (discovered, manual):
            for token_id, meta in source.items():
                existing = merged.get(token_id)
                if existing is None:
                    merged[token_id] = meta
                else:
                    existing.merge_missing(meta)

        enrich_tokens_with_gamma(config, session, merged)
        merged = apply_symbol_filter(merged, symbols_override)

        if not merged:
            raise RuntimeError("No target token IDs found. Check manual_token_ids or discovery filters.")
        return merged
    finally:
        session.close()


def parse_symbols_arg(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    values = [part.strip().upper() for part in raw.split(",") if part.strip()]
    return values or None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro Polymarket Micro-Markets Observer (read-only)")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--duration-min", type=float, default=None, help="Optional runtime duration in minutes")
    parser.add_argument("--log-dir", default=None, help="Override storage.log_dir from config")
    parser.add_argument("--symbols", default=None, help="Optional comma-separated symbol filter, e.g. BTC,ETH")
    args = parser.parse_args()

    configure_logging()

    config_path = pathlib.Path(args.config).resolve()
    config = load_config(config_path)

    if args.log_dir:
        config.setdefault("storage", {})
        config["storage"]["log_dir"] = args.log_dir

    validate_config(config)

    if websockets is None:
        raise SystemExit("Missing dependency: websockets. Install with `pip install -r requirements.txt`.")

    symbols_override = parse_symbols_arg(args.symbols)
    tokens = build_target_tokens(config, symbols_override)

    yes_count = sum(1 for meta in tokens.values() if meta.side == "YES")
    no_count = sum(1 for meta in tokens.values() if meta.side == "NO")
    unknown_count = sum(1 for meta in tokens.values() if meta.side not in {"YES", "NO"})
    LOG.info(
        "Loaded %s token targets for bot=%s (YES=%s, NO=%s, UNKNOWN=%s)",
        len(tokens),
        str(config.get("bot_name", "Bro")).strip() or "Bro",
        yes_count,
        no_count,
        unknown_count,
    )

    observer = Observer(config, tokens)
    try:
        asyncio.run(observer.run(args.duration_min))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
