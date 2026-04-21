from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .common import first_non_none, parse_float, parse_ts, utc_iso, utc_now
from .models import BookTop

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

_BOOK_FEED_WS_EXCEPTIONS: Tuple[type[BaseException], ...] = ()
if websockets is not None:  # pragma: no cover - import shape depends on installed websockets version
    try:
        from websockets.exceptions import WebSocketException

        _BOOK_FEED_WS_EXCEPTIONS = (WebSocketException,)
    except (ImportError, AttributeError, TypeError):
        _BOOK_FEED_WS_EXCEPTIONS = ()


def _parse_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _best_level(levels: Any, is_bid: bool) -> Tuple[Optional[float], Optional[float]]:
    best_price: Optional[float] = None
    best_size: Optional[float] = None
    for row in _parse_json_list(levels):
        price = None
        size = None
        if isinstance(row, dict):
            price = parse_float(first_non_none(row.get("price"), row.get("p"), row.get("px")))
            size = parse_float(
                first_non_none(
                    row.get("size"),
                    row.get("s"),
                    row.get("quantity"),
                    row.get("amount"),
                    row.get("shares"),
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


class MarketBookFeedError(RuntimeError):
    pass


BOOK_FEED_LOOP_EXCEPTIONS = (
    OSError,
    TimeoutError,
    ConnectionError,
    RuntimeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
    asyncio.TimeoutError,
    *_BOOK_FEED_WS_EXCEPTIONS,
)


@dataclasses.dataclass
class _BookEntry:
    top: BookTop
    updated_monotonic: float


class MarketBookFeed:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.ws_url = str(cfg.get("url", "wss://ws-subscriptions-clob.polymarket.com/ws/market"))
        self.channel = str(cfg.get("channel", "market"))
        self.stale_after_sec = float(cfg.get("stale_after_sec", 3.0))
        self.heartbeat_timeout_sec = float(cfg.get("heartbeat_timeout_sec", 12.0))
        self.ping_interval_sec = float(cfg.get("ping_interval_sec", 5.0))
        self.reconnect_backoff_initial_sec = float(cfg.get("reconnect_backoff_initial_sec", 1.0))
        self.reconnect_backoff_max_sec = float(cfg.get("reconnect_backoff_max_sec", 20.0))

        self._lock = threading.Lock()
        self._latest_by_token: Dict[str, _BookEntry] = {}
        self._token_ids: List[str] = []
        self._token_set: set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._resubscribe_event = threading.Event()
        self._connected = False
        self._reconnects_total = 0
        self._reconnects_steady = 0
        self._reconnects_startup = 0
        self._last_transport_monotonic: Optional[float] = None
        self._last_msg_monotonic: Optional[float] = None
        self._last_error: Optional[str] = None
        self._primed = False
        # Guard against malformed venue timestamps that parse but are far from wall-clock.
        self._max_payload_ts_skew_sec = 120.0

    @staticmethod
    def _unique_ordered(values: List[str]) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def start(self, token_ids: List[str]) -> None:
        if not self.enabled:
            return
        if websockets is None:
            raise MarketBookFeedError(
                "market_data.ws.enabled is true but websockets dependency is missing. Install with `pip install websockets`."
            )
        self.update_token_ids(token_ids)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._resubscribe_event.clear()
        self._thread = threading.Thread(target=self._thread_main, name="market-book-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._resubscribe_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._reset_freshness_state(clear_books=True, mark_disconnected=True)

    def update_token_ids(self, token_ids: List[str]) -> None:
        ordered = self._unique_ordered([str(x) for x in token_ids if str(x)])
        token_set = set(ordered)
        changed = False
        with self._lock:
            if ordered != self._token_ids:
                self._token_ids = ordered
                self._token_set = token_set
                changed = True
                self._latest_by_token = {k: v for k, v in self._latest_by_token.items() if k in token_set}
                # Token-universe changes require a fresh priming cycle before
                # reconnects are treated as steady-state reliability events.
                self._primed = False
        if changed:
            # Token-universe changes require a clean freshness window to avoid
            # carrying stale books through resubscribe.
            self._reset_freshness_state(clear_books=True, mark_disconnected=False)
            self._resubscribe_event.set()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            connected = bool(self._connected and self._last_msg_monotonic is not None)
            transport_connected = bool(self._connected and self._last_transport_monotonic is not None)
            reconnects = self._reconnects_total
            reconnects_startup = self._reconnects_startup
            reconnects_steady = self._reconnects_steady
            last_transport_msg = self._last_transport_monotonic
            last_msg = self._last_msg_monotonic
            token_count = len(self._token_ids)
            cached_books = len(self._latest_by_token)
            last_error = self._last_error
            primed = self._primed
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
        transport_age_sec = (time.monotonic() - last_transport_msg) if last_transport_msg is not None else None
        age_sec = (time.monotonic() - last_msg) if last_msg is not None else None
        return {
            "enabled": self.enabled,
            "connected": connected,
            "transport_connected": transport_connected,
            "reconnects": reconnects,
            "reconnects_total": reconnects,
            "reconnects_startup": reconnects_startup,
            "reconnects_steady": reconnects_steady,
            "last_msg_age_sec": age_sec,
            "last_transport_msg_age_sec": transport_age_sec,
            "token_count": token_count,
            "cached_books": cached_books,
            "last_error": last_error,
            "primed": primed,
            "thread_alive": thread_alive,
        }

    def snapshot_books(self, *, max_age_sec: Optional[float] = None) -> Dict[str, BookTop]:
        age_limit = self.stale_after_sec if max_age_sec is None else max_age_sec
        now = time.monotonic()
        out: Dict[str, BookTop] = {}
        with self._lock:
            for token_id, entry in self._latest_by_token.items():
                if age_limit is not None and age_limit > 0 and (now - entry.updated_monotonic) > age_limit:
                    continue
                out[token_id] = entry.top
        return out

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_loop())
        except BOOK_FEED_LOOP_EXCEPTIONS as exc:
            with self._lock:
                self._connected = False
                self._last_error = f"fatal:{exc}"

    async def _run_loop(self) -> None:
        assert websockets is not None
        backoff = self.reconnect_backoff_initial_sec
        while not self._stop_event.is_set():
            token_ids: List[str]
            with self._lock:
                token_ids = list(self._token_ids)
            if not token_ids:
                await asyncio.sleep(0.5)
                continue

            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.ping_interval_sec,
                    ping_timeout=self.heartbeat_timeout_sec,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    await ws.send(json.dumps({"type": self.channel, "assets_ids": token_ids}))
                    with self._lock:
                        self._connected = True
                        self._last_error = None
                        self._last_transport_monotonic = None
                        self._last_msg_monotonic = None
                        # New websocket session must re-prime orderbook freshness.
                        self._latest_by_token = {}
                    self._resubscribe_event.clear()
                    backoff = self.reconnect_backoff_initial_sec

                    while not self._stop_event.is_set():
                        if self._resubscribe_event.is_set():
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.heartbeat_timeout_sec)
                        except asyncio.TimeoutError:
                            pong_waiter = await ws.ping()
                            await asyncio.wait_for(pong_waiter, timeout=self.heartbeat_timeout_sec)
                            with self._lock:
                                self._last_transport_monotonic = time.monotonic()
                            continue

                        now_mono = time.monotonic()
                        with self._lock:
                            self._last_transport_monotonic = now_mono

                        if isinstance(raw, bytes):
                            text = raw.decode("utf-8", errors="ignore")
                        else:
                            text = str(raw)
                        lowered = text.strip().lower()
                        if lowered == "ping":
                            await ws.send("pong")
                            continue
                        if lowered == "pong":
                            continue

                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        self._handle_message_obj(parsed, now_mono)
            except BOOK_FEED_LOOP_EXCEPTIONS as exc:
                if self._stop_event.is_set():
                    break
                self._record_reconnect(error=str(exc))
                self._reset_freshness_state(clear_books=True, mark_disconnected=True)
                sleep_for = min(self.reconnect_backoff_max_sec, backoff)
                sleep_for *= 0.85 + (0.30 * random.random())
                await asyncio.sleep(sleep_for)
                backoff = min(self.reconnect_backoff_max_sec, backoff * 2.0)
            finally:
                self._reset_freshness_state(clear_books=True, mark_disconnected=True)

    def _reset_freshness_state(self, *, clear_books: bool, mark_disconnected: bool) -> None:
        with self._lock:
            if mark_disconnected:
                self._connected = False
            self._last_transport_monotonic = None
            self._last_msg_monotonic = None
            if clear_books:
                self._latest_by_token.clear()

    def _record_reconnect(self, *, error: str) -> None:
        with self._lock:
            self._connected = False
            self._last_error = str(error)
            self._reconnects_total += 1
            if self._primed:
                self._reconnects_steady += 1
            else:
                self._reconnects_startup += 1

    def _handle_message_obj(self, obj: Any, received_monotonic: float) -> None:
        if isinstance(obj, list):
            for item in obj:
                self._handle_message_obj(item, received_monotonic)
            return
        if not isinstance(obj, dict):
            return

        self._handle_payload(obj, received_monotonic)

        for key in ("data", "payload", "message", "events"):
            nested = obj.get(key)
            if nested is not None:
                self._handle_message_obj(nested, received_monotonic)

    def _handle_payload(self, payload: Dict[str, Any], received_monotonic: float) -> None:
        token_raw = first_non_none(payload.get("asset_id"), payload.get("token_id"))
        if token_raw is None:
            return
        token_id = str(token_raw)
        with self._lock:
            if token_id not in self._token_set:
                return

        bid_price, bid_size = _best_level(first_non_none(payload.get("bids"), payload.get("buys")), is_bid=True)
        ask_price, ask_size = _best_level(first_non_none(payload.get("asks"), payload.get("sells")), is_bid=False)

        direct_bid = parse_float(first_non_none(payload.get("best_bid"), payload.get("bestBid")))
        direct_ask = parse_float(first_non_none(payload.get("best_ask"), payload.get("bestAsk")))
        direct_bid_size = parse_float(first_non_none(payload.get("best_bid_size"), payload.get("bestBidSize")))
        direct_ask_size = parse_float(first_non_none(payload.get("best_ask_size"), payload.get("bestAskSize")))

        if direct_bid is not None:
            bid_price = direct_bid
        if direct_ask is not None:
            ask_price = direct_ask
        if direct_bid_size is not None:
            bid_size = direct_bid_size
        if direct_ask_size is not None:
            ask_size = direct_ask_size

        if bid_price is None and ask_price is None:
            return

        ts = parse_ts(first_non_none(payload.get("timestamp"), payload.get("ts"), payload.get("time")))
        if ts is not None:
            skew_sec = abs((utc_now() - ts).total_seconds())
            if skew_sec > self._max_payload_ts_skew_sec:
                ts = None
        top = BookTop(
            token_id=token_id,
            ts_utc=utc_iso(ts),
            source="ws",
            best_bid_price=bid_price,
            best_bid_size=bid_size,
            best_ask_price=ask_price,
            best_ask_size=ask_size,
        )
        with self._lock:
            self._latest_by_token[token_id] = _BookEntry(top=top, updated_monotonic=received_monotonic)
            self._last_msg_monotonic = received_monotonic
            self._primed = True
