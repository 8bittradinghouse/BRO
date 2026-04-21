from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from .common import first_non_none, parse_float, parse_ts, utc_iso
from .http_session import build_hardened_session


_DATE_ONLY_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_json_list(value: Any) -> List[Any]:
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


def _extract_tags(tags_value: Any) -> List[str]:
    tags: List[str] = []
    for item in _parse_json_list(tags_value):
        if isinstance(item, str):
            tags.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "slug", "id"):
                maybe = item.get(key)
                if isinstance(maybe, str) and maybe.strip():
                    tags.append(maybe.strip())
    return tags


def _market_text_blob(market: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("question", "title", "slug", "description"):
        val = market.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    parts.extend(_extract_tags(market.get("tags")))
    return " | ".join(parts)


def _parse_end_time(market: Dict[str, Any]) -> Optional[dt.datetime]:
    # Date-only fields are ambiguous for short-window execution and are not
    # trusted as authoritative expiry times.
    for key in ("endDate", "end_date", "end_time", "expiration_time", "endDateIso"):
        raw = market.get(key)
        if isinstance(raw, str) and _DATE_ONLY_ISO_RE.fullmatch(raw.strip()):
            continue
        ts = parse_ts(raw)
        if ts is not None:
            return ts
    return None


def _parse_strike_price(market: Dict[str, Any]) -> Optional[float]:
    for key in (
        "strikePrice",
        "strike_price",
        "targetPrice",
        "target_price",
        "referencePrice",
        "reference_price",
        "openPrice",
        "open_price",
    ):
        val = parse_float(market.get(key))
        if val is not None and val > 0:
            return val
    blob = _market_text_blob(market)
    # Capture values like "$98,420.50" or "98420.50".
    matches = re.findall(r"\$?\s*([0-9]{4,}(?:,[0-9]{3})*(?:\.[0-9]+)?)", blob)
    for raw in matches:
        cleaned = raw.replace(",", "")
        val = parse_float(cleaned)
        if val is not None and val > 0:
            return val
    return None


def _token_side_map_for_market(market: Dict[str, Any], token_ids: List[str]) -> Dict[str, str]:
    if len(token_ids) < 2:
        return {}
    outcomes = [str(x).strip().upper() for x in _parse_json_list(market.get("outcomes")) if str(x).strip()]
    side_map: Dict[str, str] = {}
    if len(outcomes) >= 2:
        if outcomes[0] in {"YES", "NO"}:
            side_map[token_ids[0]] = outcomes[0]
        if outcomes[1] in {"YES", "NO"}:
            side_map[token_ids[1]] = outcomes[1]
    if token_ids[0] not in side_map:
        side_map[token_ids[0]] = "YES"
    if token_ids[1] not in side_map:
        side_map[token_ids[1]] = "NO"
    return side_map


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "enabled", "on"}:
            return True
        if text in {"false", "0", "no", "disabled", "off"}:
            return False
    return None


def _http_get_json(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout_sec: float,
    max_retries: int,
) -> Any:
    delay = 0.5
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout_sec)
        except requests.RequestException:
            if attempt >= max_retries:
                raise
            time.sleep(delay)
            delay = min(4.0, delay * 2.0)
            continue
        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()
            time.sleep(delay)
            delay = min(8.0, delay * 2.0)
            continue
        if resp.status_code >= 500 and attempt < max_retries:
            time.sleep(delay)
            delay = min(4.0, delay * 2.0)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"exhausted retries for {url}")


@dataclass
class DiscoveryResult:
    token_ids: List[str]
    market_count: int
    pairs_selected: int
    scanned_markets: int
    fee_eligible_markets: int = 0
    contract_rejected_pairs: int = 0
    allowlist_enabled: bool = False
    allowlist_rejected_pairs: int = 0
    token_expiry_utc_by_token: Dict[str, str] = field(default_factory=dict)
    token_side_by_token: Dict[str, str] = field(default_factory=dict)
    token_strike_by_token: Dict[str, float] = field(default_factory=dict)
    token_market_key_by_token: Dict[str, str] = field(default_factory=dict)


class MarketDiscovery:
    def __init__(self, cfg: Dict[str, Any]):
        targets_cfg = cfg.get("targets", {})
        disc = targets_cfg.get("discovery", {})
        self.enabled = bool(disc.get("enabled", False))

        self.gamma_url = str(disc.get("gamma_url", "https://gamma-api.polymarket.com")).rstrip("/")
        self.markets_path = str(disc.get("markets_path", "/markets"))
        self.page_limit = int(disc.get("page_limit", 200))
        self.max_pages = int(disc.get("max_pages", 10))
        self.max_markets_scan = int(disc.get("max_markets_scan", 1200))
        self.max_pairs = int(disc.get("max_pairs", 4))
        self.require_binary = bool(disc.get("require_binary_outcomes", True))
        self.require_fee_enabled = bool(disc.get("require_fee_enabled", True))
        self.refresh_interval_sec = float(disc.get("refresh_interval_sec", 60.0))
        self.symbols = [str(x).upper() for x in disc.get("symbols", ["BTC"])]
        self.allow_token_ids = {
            str(x).strip()
            for x in disc.get("allow_token_ids", [])
            if str(x).strip()
        }
        self.keywords_any = [str(x).upper() for x in disc.get("keywords_any", ["5 minute", "5-minute", "up or down", "up/down"])]
        self.tags_any = [str(x).upper() for x in disc.get("tags_any", [])]
        self.max_target_horizon_sec = max(0.0, float(disc.get("max_target_horizon_sec", 0.0)))
        # Canonical mode keeps slug probing off unless explicitly enabled in config.
        self.event_slug_probe_enabled = bool(disc.get("event_slug_probe_enabled", False))
        configured_slug_prefix = str(disc.get("event_slug_prefix", "")).strip().lower()
        default_symbol = self.symbols[0].lower() if self.symbols else ""
        self.event_slug_prefix = configured_slug_prefix or (f"{default_symbol}-updown-5m" if default_symbol else "")
        self.event_slug_probe_span = max(0, int(disc.get("event_slug_probe_span", 2)))

        md_cfg = cfg.get("market_data", {})
        self.timeout_sec = float(disc.get("timeout_sec", md_cfg.get("timeout_sec", 8)))
        self.max_retries = int(disc.get("max_retries", md_cfg.get("max_retries", 2)))

        self.session = build_hardened_session(user_agent="polymarket-bro-executor/0.1")

    def close(self) -> None:
        self.session.close()

    def _market_matches(self, market: Dict[str, Any]) -> bool:
        blob = _market_text_blob(market).upper()
        tags = [x.upper() for x in _extract_tags(market.get("tags"))]

        if self.symbols and not any(sym in blob for sym in self.symbols):
            return False
        if self.keywords_any and not any(key in blob for key in self.keywords_any):
            return False
        if self.tags_any and not any(tag in tags for tag in self.tags_any):
            return False
        return True

    def _market_fee_enabled(self, market: Dict[str, Any]) -> bool:
        if not self.require_fee_enabled:
            return True

        saw_signal = False
        bool_keys = (
            "feeEnabled",
            "fee_enabled",
            "feesEnabled",
            "fees_enabled",
            "isFeeEnabled",
            "enableOrderBookFees",
            "orderBookFeesEnabled",
            "makerRewardsEnabled",
            "maker_rewards_enabled",
            "rewardsEnabled",
        )
        for key in bool_keys:
            parsed = _parse_bool(market.get(key))
            if parsed is not None:
                saw_signal = True
                return parsed

        numeric_keys = (
            "takerFeeRate",
            "taker_fee_rate",
            "feeRate",
            "fee_rate",
            "makerRewardRate",
            "maker_reward_rate",
        )
        for key in numeric_keys:
            value = parse_float(market.get(key))
            if value is not None:
                saw_signal = True
                return value > 0

        rewards_obj = market.get("makerRewards")
        if isinstance(rewards_obj, dict):
            for key in ("enabled", "active", "isActive"):
                parsed = _parse_bool(rewards_obj.get(key))
                if parsed is not None:
                    saw_signal = True
                    return parsed
            for key in ("rate", "makerRate", "maker_rate"):
                value = parse_float(rewards_obj.get(key))
                if value is not None:
                    saw_signal = True
                    return value > 0

        blob = _market_text_blob(market).lower()
        if "rebate" in blob and "fee" in blob:
            return True
        if saw_signal:
            return False
        return True

    def _market_contract_active(self, market: Dict[str, Any]) -> bool:
        active = _parse_bool(market.get("active"))
        if active is False:
            return False
        for key in (
            "closed",
            "archived",
            "resolved",
            "isClosed",
            "isArchived",
            "isResolved",
        ):
            parsed = _parse_bool(market.get(key))
            if parsed is True:
                return False
        return True

    def _market_contract_pair_ids(self, market: Dict[str, Any]) -> Optional[List[str]]:
        raw = _parse_json_list(first_non_none(market.get("clobTokenIds"), market.get("clob_token_ids")))
        pair_ids = [str(x).strip() for x in raw if str(x).strip()]
        if len(pair_ids) < 2:
            return None
        pair = pair_ids[:2]
        if len(set(pair)) < 2:
            return None
        return pair

    def _validate_target_contract(
        self,
        *,
        market: Dict[str, Any],
        now: dt.datetime,
    ) -> Optional[Tuple[dt.datetime, List[str]]]:
        if not self._market_contract_active(market):
            return None
        end_time = _parse_end_time(market)
        if end_time is None or end_time <= now:
            return None
        if self.max_target_horizon_sec > 0:
            if (end_time - now).total_seconds() > float(self.max_target_horizon_sec):
                return None
        pair_ids = self._market_contract_pair_ids(market)
        if pair_ids is None:
            return None
        return end_time, pair_ids

    def discover(self) -> DiscoveryResult:
        if not self.enabled:
            return DiscoveryResult(
                token_ids=[],
                market_count=0,
                pairs_selected=0,
                scanned_markets=0,
                allowlist_enabled=bool(self.allow_token_ids),
            )

        markets_url = f"{self.gamma_url}{self.markets_path}"
        now = dt.datetime.now(dt.timezone.utc)
        scanned = 0
        fee_eligible_markets = 0
        contract_rejected_pairs = 0
        candidates: List[Tuple[Optional[dt.datetime], Dict[str, Any]]] = []

        for page_idx in range(self.max_pages):
            offset = page_idx * self.page_limit
            params = {
                "active": True,
                "closed": False,
                "archived": False,
                "limit": self.page_limit,
                "offset": offset,
            }
            payload = _http_get_json(
                self.session,
                markets_url,
                params=params,
                timeout_sec=self.timeout_sec,
                max_retries=self.max_retries,
            )
            markets = payload.get("markets") if isinstance(payload, dict) else payload
            if isinstance(payload, dict) and markets is None:
                markets = payload.get("data")
            if not isinstance(markets, list) or not markets:
                break

            for market in markets:
                if not isinstance(market, dict):
                    continue
                scanned += 1
                if self.max_markets_scan and scanned > self.max_markets_scan:
                    break
                if not self._market_matches(market):
                    continue
                if not self._market_fee_enabled(market):
                    continue
                fee_eligible_markets += 1
                validated = self._validate_target_contract(market=market, now=now)
                if validated is None:
                    contract_rejected_pairs += 1
                    continue
                end_time, pair_ids = validated
                market_candidate = dict(market)
                market_candidate["_bro_pair_token_ids"] = list(pair_ids)
                candidates.append((end_time, market_candidate))
            if self.max_markets_scan and scanned > self.max_markets_scan:
                break
            if len(markets) < self.page_limit:
                break

        if not candidates and self.event_slug_probe_enabled and self.event_slug_prefix:
            candidates.extend(self._discover_by_event_slug(markets_url=markets_url, now=now))

        def _sort_key(item: Tuple[Optional[dt.datetime], Dict[str, Any]]) -> Tuple[int, dt.datetime]:
            end_time = item[0]
            if end_time is None:
                return (1, dt.datetime.max.replace(tzinfo=dt.timezone.utc))
            return (0, end_time)

        candidates.sort(key=_sort_key)

        token_ids_out: List[str] = []
        token_expiry_utc_by_token: Dict[str, str] = {}
        token_side_by_token: Dict[str, str] = {}
        token_strike_by_token: Dict[str, float] = {}
        token_market_key_by_token: Dict[str, str] = {}
        allowlist_rejected_pairs = 0
        seen_markets: set[str] = set()
        pairs_selected = 0
        for _end_time, market in candidates:
            condition = first_non_none(market.get("conditionId"), market.get("condition_id"), market.get("id"))
            condition_key = str(condition) if condition is not None else f"market:{len(seen_markets)}"
            if condition_key in seen_markets:
                continue
            raw_ids = [str(x).strip() for x in _parse_json_list(market.get("_bro_pair_token_ids")) if str(x).strip()]
            if len(raw_ids) < 2:
                raw_ids = [
                    str(x).strip()
                    for x in _parse_json_list(first_non_none(market.get("clobTokenIds"), market.get("clob_token_ids")))
                    if str(x).strip()
                ]
            if len(raw_ids) < 2:
                continue
            pair_ids = raw_ids[:2]
            if self.allow_token_ids and any(token_id not in self.allow_token_ids for token_id in pair_ids):
                allowlist_rejected_pairs += 1
                continue
            token_ids_out.extend(pair_ids)
            side_map = _token_side_map_for_market(market, pair_ids)
            token_side_by_token.update(side_map)
            strike = _parse_strike_price(market)
            strike_text = "na"
            if strike is not None:
                for token_id in pair_ids:
                    token_strike_by_token[token_id] = float(strike)
                strike_text = f"{float(strike):.8f}"
            expiry_utc = ""
            if _end_time is not None:
                expiry_utc = utc_iso(_end_time)
                for token_id in pair_ids:
                    token_expiry_utc_by_token[token_id] = expiry_utc
            for token_id in pair_ids:
                side = side_map.get(token_id, "UNK")
                token_market_key_by_token[token_id] = f"{condition_key}|{expiry_utc or 'na'}|{strike_text}|{side}"
            seen_markets.add(condition_key)
            pairs_selected += 1
            if pairs_selected >= self.max_pairs:
                break

        return DiscoveryResult(
            token_ids=token_ids_out,
            market_count=len(seen_markets),
            pairs_selected=pairs_selected,
            scanned_markets=scanned,
            fee_eligible_markets=fee_eligible_markets,
            contract_rejected_pairs=contract_rejected_pairs,
            allowlist_enabled=bool(self.allow_token_ids),
            allowlist_rejected_pairs=allowlist_rejected_pairs,
            token_expiry_utc_by_token=token_expiry_utc_by_token,
            token_side_by_token=token_side_by_token,
            token_strike_by_token=token_strike_by_token,
            token_market_key_by_token=token_market_key_by_token,
        )

    def _discover_by_event_slug(self, *, markets_url: str, now: dt.datetime) -> List[Tuple[Optional[dt.datetime], Dict[str, Any]]]:
        candidates: List[Tuple[Optional[dt.datetime], Dict[str, Any]]] = []
        seen_condition: set[str] = set()
        bucket = int(now.timestamp()) // 300 * 300
        for step in range(-self.event_slug_probe_span, self.event_slug_probe_span + 1):
            ts_bucket = bucket + (step * 300)
            slug = f"{self.event_slug_prefix}-{ts_bucket}"
            payload = _http_get_json(
                self.session,
                markets_url,
                params={"slug": slug, "limit": 5, "offset": 0},
                timeout_sec=self.timeout_sec,
                max_retries=self.max_retries,
            )
            markets = payload.get("markets") if isinstance(payload, dict) else payload
            if isinstance(payload, dict) and markets is None:
                markets = payload.get("data")
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, dict):
                    continue
                if not self._market_fee_enabled(market):
                    continue
                raw_ids = _parse_json_list(first_non_none(market.get("clobTokenIds"), market.get("clob_token_ids")))
                if len(raw_ids) < 2:
                    continue
                condition = first_non_none(market.get("conditionId"), market.get("condition_id"), market.get("id"), slug)
                condition_key = str(condition)
                if condition_key in seen_condition:
                    continue
                end_time = _parse_end_time(market)
                raw_end_iso = market.get("endDateIso")
                has_date_only_end_iso = isinstance(raw_end_iso, str) and _DATE_ONLY_ISO_RE.fullmatch(raw_end_iso.strip()) is not None
                if end_time is None or has_date_only_end_iso:
                    # Derive expected 5m close from slug timestamp when API omits endDate fields.
                    end_time = dt.datetime.fromtimestamp(ts_bucket + 300, tz=dt.timezone.utc)
                    market = dict(market)
                    normalized_close = utc_iso(end_time)
                    market["endDateIso"] = normalized_close
                    if not str(market.get("endDate", "")).strip() or (
                        isinstance(market.get("endDate"), str)
                        and _DATE_ONLY_ISO_RE.fullmatch(str(market.get("endDate")).strip()) is not None
                    ):
                        market["endDate"] = normalized_close
                validated = self._validate_target_contract(market=market, now=now)
                if validated is None:
                    continue
                end_time_validated, pair_ids = validated
                market_candidate = dict(market)
                market_candidate["_bro_pair_token_ids"] = list(pair_ids)
                seen_condition.add(condition_key)
                candidates.append((end_time_validated, market_candidate))
        return candidates
