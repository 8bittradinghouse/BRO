from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .common import first_non_none, parse_float, parse_ts, utc_iso

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


@dataclasses.dataclass
class ChainlinkTick:
    symbol: str
    price: float
    source_ts_utc: Optional[str]
    received_ts_utc: str
    received_monotonic: float
    topic: str
    msg_type: str


class ChainlinkFeedError(RuntimeError):
    pass


class ChainlinkFeed:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", False))
        self.ws_url = str(cfg.get("ws_url", "wss://ws-live-data.polymarket.com"))
        self.topic = str(cfg.get("topic", "crypto_prices_chainlink"))
        self.symbols = [self._normalize_symbol(str(x)) for x in cfg.get("symbols", ["btc/usd"])]
        self._symbol_filter = {symbol for symbol in self.symbols if symbol}
        self.log_ticks = bool(cfg.get("log_ticks", True))
        self.heartbeat_timeout_sec = float(cfg.get("heartbeat_timeout_sec", 15.0))
        self.ping_interval_sec = float(cfg.get("ping_interval_sec", 5.0))
        self.reconnect_backoff_initial_sec = float(cfg.get("reconnect_backoff_initial_sec", 1.0))
        self.reconnect_backoff_max_sec = float(cfg.get("reconnect_backoff_max_sec", 30.0))
        self.max_queue_size = int(cfg.get("max_queue_size", 10000))

        self._lock = threading.Lock()
        self._latest_by_symbol: Dict[str, ChainlinkTick] = {}
        self._queue: Deque[ChainlinkTick] = deque()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = False
        self._reconnects = 0
        self._dropped_ticks = 0
        self._disorder_dropped_ticks = 0
        self._duplicate_ticks = 0
        self._same_timestamp_revisions = 0
        self._missing_source_ts_dropped_ticks = 0
        self._ordering_decision_counts: Dict[str, int] = {}
        self._ordering_class_counts: Dict[str, int] = {
            "ordered": 0,
            "out_of_order": 0,
            "duplicate": 0,
            "revision": 0,
            "missing_source_time": 0,
        }
        self._last_error: Optional[str] = None
        self._ordering_policy: Dict[str, Any] = {
            "primary": "source_timestamp",
            "fallback": "receive_monotonic",
            "tolerance_ms": 0,
            "tie_breaker": "same_timestamp_price_revision",
        }

    def start(self) -> None:
        if not self.enabled:
            return
        if websockets is None:
            raise ChainlinkFeedError(
                "chainlink feed enabled but websockets dependency is missing. Install with `pip install websockets`."
            )
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._thread_main, name="chainlink-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def pop_ticks(self) -> List[ChainlinkTick]:
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out

    def get_latest(self, symbol: str) -> Optional[ChainlinkTick]:
        normalized = self._normalize_symbol(symbol)
        with self._lock:
            return self._latest_by_symbol.get(normalized)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            last = None
            if self._latest_by_symbol:
                last = max(t.received_monotonic for t in self._latest_by_symbol.values())
            queue_size = len(self._queue)
            dropped = self._dropped_ticks
            connected = self._connected
            reconnects = self._reconnects
            last_error = self._last_error
            disorder_dropped = self._disorder_dropped_ticks
            duplicate_ticks = self._duplicate_ticks
            same_timestamp_revisions = self._same_timestamp_revisions
            missing_source_ts_dropped = self._missing_source_ts_dropped_ticks
            ordering_decision_counts = dict(self._ordering_decision_counts)
            ordering_class_counts = dict(self._ordering_class_counts)
        age_sec = (time.monotonic() - last) if last is not None else None
        return {
            "enabled": self.enabled,
            "connected": bool(connected and last is not None),
            "ws_connected": connected,
            "reconnects": reconnects,
            "last_tick_age_sec": age_sec,
            "queue_size": queue_size,
            "dropped_ticks": dropped,
            "disorder_dropped_ticks": disorder_dropped,
            "duplicate_ticks": duplicate_ticks,
            "same_timestamp_revisions": same_timestamp_revisions,
            "missing_source_ts_dropped_ticks": missing_source_ts_dropped,
            "ordering_policy": dict(self._ordering_policy),
            "ordering_policy_label": "source_ts_authoritative_then_receive_monotonic_fallback",
            "ordering_decision_counts": ordering_decision_counts,
            "ordering_classification_counts": ordering_class_counts,
            "last_error": last_error,
        }

    @staticmethod
    def _normalize_symbol(value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if "/" in text:
            left, _, right = text.partition("/")
            left = left.strip()
            right = right.strip()
            if right in {"usd", "usdt", "usdc"}:
                right = "usd"
            return f"{left}/{right}" if left and right else text
        compact = "".join(ch for ch in text if ch.isalnum())
        if compact.endswith("usdt") and len(compact) > 4:
            return f"{compact[:-4]}/usd"
        if compact.endswith("usdc") and len(compact) > 4:
            return f"{compact[:-4]}/usd"
        if compact.endswith("usd") and len(compact) > 3:
            return f"{compact[:-3]}/usd"
        return text

    @staticmethod
    def _subscription_topics(topic: str) -> List[str]:
        primary = str(topic or "").strip()
        if not primary:
            return []
        topics = [primary]
        if primary.lower().startswith("crypto_prices") and primary.lower() != "crypto_prices":
            topics.append("crypto_prices")
        return topics

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_loop())
        except Exception as exc:
            # The executor loop handles missing ticks through telemetry and logging.
            with self._lock:
                self._connected = False
                self._last_error = f"fatal:{exc}"

    async def _run_loop(self) -> None:
        assert websockets is not None
        backoff = self.reconnect_backoff_initial_sec
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.ping_interval_sec,
                    ping_timeout=self.heartbeat_timeout_sec,
                    close_timeout=5,
                    max_queue=2048,
                ) as ws:
                    await ws.send(json.dumps(self._build_subscribe_message()))
                    with self._lock:
                        self._connected = True
                        self._last_error = None
                    backoff = self.reconnect_backoff_initial_sec
                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.heartbeat_timeout_sec)
                        except asyncio.TimeoutError:
                            pong_waiter = await ws.ping()
                            await asyncio.wait_for(pong_waiter, timeout=self.heartbeat_timeout_sec)
                            continue

                        if isinstance(raw, bytes):
                            text = raw.decode("utf-8", errors="ignore")
                        else:
                            text = str(raw)
                        lower = text.strip().lower()
                        if lower == "ping":
                            await ws.send("pong")
                            continue
                        if lower == "pong":
                            continue
                        try:
                            obj = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        self._handle_message_obj(obj, received_monotonic=time.monotonic())
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                with self._lock:
                    self._connected = False
                    self._reconnects += 1
                    self._last_error = str(exc)
                sleep_for = min(self.reconnect_backoff_max_sec, backoff)
                sleep_for *= 0.85 + (0.30 * random.random())
                await asyncio.sleep(sleep_for)
                backoff = min(self.reconnect_backoff_max_sec, backoff * 2.0)
            finally:
                with self._lock:
                    self._connected = False

    def _build_subscribe_message(self) -> Dict[str, Any]:
        subs = []
        symbols = [s.strip().lower() for s in self.symbols if s.strip()]
        for topic in self._subscription_topics(self.topic):
            # "crypto_prices" updates are delivered with provider-formatted symbols
            # (e.g. "btcusdt"), where server-side filters are unreliable. Subscribe
            # unfiltered and apply symbol filtering locally.
            if topic.lower() == "crypto_prices":
                subs.append({"topic": topic, "type": "*", "filters": ""})
                continue
            if symbols:
                for symbol in symbols:
                    subs.append(
                        {
                            "topic": topic,
                            "type": "*",
                            "filters": json.dumps({"symbol": symbol}),
                        }
                    )
            else:
                subs.append({"topic": topic, "type": "*", "filters": ""})
        return {"action": "subscribe", "subscriptions": subs}

    def _handle_message_obj(self, obj: Any, received_monotonic: float, expected_topic: Optional[str] = None) -> None:
        if isinstance(obj, list):
            for item in obj:
                self._handle_message_obj(item, received_monotonic, expected_topic=expected_topic)
            return
        if not isinstance(obj, dict):
            return

        topic = str(
            obj.get("topic")
            or obj.get("channel")
            or expected_topic
            or (self.topic if "payload" in obj else "")
            or ""
        )
        if not self._topic_matches(topic):
            nested = obj.get("data")
            if nested is not None:
                self._handle_message_obj(nested, received_monotonic)
            return
        msg_type = str(obj.get("type") or "")
        payload = obj.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None

        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            # Live Chainlink feed can deliver batched points in payload.data without symbol field.
            # Treat this as updates for the configured subscription symbol(s).
            points = [row for row in payload.get("data") if isinstance(row, dict)]
            if points:
                symbols = list(self._symbol_filter) if self._symbol_filter else [""]
                for symbol in symbols:
                    for row in points:
                        synthetic = {
                            "topic": topic,
                            "type": msg_type,
                            "payload": {
                                "symbol": symbol,
                                "value": row.get("value"),
                                "timestamp": row.get("timestamp"),
                            },
                        }
                        self._handle_message_obj(synthetic, received_monotonic, expected_topic=topic)
                return

        payload_dict: Optional[Dict[str, Any]] = payload if isinstance(payload, dict) else None
        if payload_dict is None and isinstance(obj.get("data"), dict):
            payload_dict = obj.get("data")
        if payload_dict is None:
            nested = obj.get("data")
            if nested is not None:
                self._handle_message_obj(nested, received_monotonic, expected_topic=topic)
            return

        symbol = self._normalize_symbol(payload_dict.get("symbol") or payload_dict.get("pair") or payload_dict.get("asset"))
        if not symbol:
            return
        if self._symbol_filter and symbol not in self._symbol_filter:
            return
        price = parse_float(
            first_non_none(
                payload_dict.get("value"),
                payload_dict.get("price"),
                payload_dict.get("p"),
                payload_dict.get("mark_price"),
                payload_dict.get("last_price"),
            )
        )
        if price is None:
            return

        source_dt = parse_ts(
            payload_dict.get("timestamp")
            or payload_dict.get("source_ts")
            or payload_dict.get("time")
            or obj.get("timestamp")
        )
        source_ts_utc = utc_iso(source_dt) if source_dt is not None else None
        tick = ChainlinkTick(
            symbol=symbol,
            price=price,
            source_ts_utc=source_ts_utc,
            received_ts_utc=utc_iso(),
            received_monotonic=received_monotonic,
            topic=topic,
            msg_type=msg_type,
        )
        self._ingest_tick(tick)

    @staticmethod
    def _source_epoch(tick: ChainlinkTick) -> Optional[float]:
        parsed = parse_ts(tick.source_ts_utc)
        if parsed is None:
            return None
        return float(parsed.timestamp())

    def _ordering_decision(self, *, latest: ChainlinkTick, incoming: ChainlinkTick) -> tuple[bool, str]:
        incoming_source_epoch = self._source_epoch(incoming)
        latest_source_epoch = self._source_epoch(latest)

        if incoming_source_epoch is not None and latest_source_epoch is not None:
            if incoming_source_epoch < latest_source_epoch:
                return False, "out_of_order_source_ts"
            if incoming_source_epoch == latest_source_epoch:
                if incoming.price == latest.price:
                    return False, "duplicate_source_ts"
                return True, "same_source_ts_revision"
            return True, "newer_source_ts"

        if incoming_source_epoch is None and latest_source_epoch is not None:
            return False, "missing_source_ts_after_timestamped"
        if incoming_source_epoch is not None and latest_source_epoch is None:
            return True, "timestamp_upgrade"

        if incoming.received_monotonic < latest.received_monotonic:
            return False, "out_of_order_receive_monotonic"
        if incoming.received_monotonic == latest.received_monotonic and incoming.price == latest.price:
            return False, "duplicate_receive_monotonic"
        if incoming.received_monotonic == latest.received_monotonic:
            return True, "same_receive_monotonic_revision"
        return True, "newer_receive_monotonic"

    def _ingest_tick(self, tick: ChainlinkTick) -> None:
        with self._lock:
            latest = self._latest_by_symbol.get(tick.symbol)
            if latest is not None:
                accept, decision = self._ordering_decision(latest=latest, incoming=tick)
                self._ordering_decision_counts[decision] = int(self._ordering_decision_counts.get(decision, 0)) + 1
                decision_class = self._decision_class(decision)
                self._ordering_class_counts[decision_class] = int(self._ordering_class_counts.get(decision_class, 0)) + 1
                if not accept:
                    if decision.startswith("duplicate_"):
                        self._duplicate_ticks += 1
                    else:
                        self._disorder_dropped_ticks += 1
                    if decision == "missing_source_ts_after_timestamped":
                        self._missing_source_ts_dropped_ticks += 1
                    return
                if "revision" in decision:
                    self._same_timestamp_revisions += 1
            else:
                self._ordering_decision_counts["first_tick"] = int(self._ordering_decision_counts.get("first_tick", 0)) + 1
                self._ordering_class_counts["ordered"] = int(self._ordering_class_counts.get("ordered", 0)) + 1
            self._latest_by_symbol[tick.symbol] = tick
            if self.max_queue_size > 0:
                while len(self._queue) >= self.max_queue_size:
                    self._queue.popleft()
                    self._dropped_ticks += 1
            self._queue.append(tick)

    @staticmethod
    def _decision_class(decision: str) -> str:
        text = str(decision or "").strip().lower()
        if not text:
            return "out_of_order"
        if text.startswith("out_of_order_"):
            return "out_of_order"
        if text.startswith("duplicate_"):
            return "duplicate"
        if "revision" in text:
            return "revision"
        if "missing_source_ts" in text:
            return "missing_source_time"
        return "ordered"

    def _topic_matches(self, topic: str) -> bool:
        if topic == self.topic:
            return True
        lhs = str(topic or "").strip().lower()
        rhs = str(self.topic or "").strip().lower()
        if not lhs or not rhs:
            return False
        if lhs.startswith("crypto_prices") and rhs.startswith("crypto_prices"):
            return True
        return False
