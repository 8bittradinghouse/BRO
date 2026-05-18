from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .common import parse_ts, utc_iso
from .stream_worker_contracts import (
    CONTRACT_VERSION,
    EVENT_ACK,
    EVENT_FATAL,
    EVENT_HEALTH,
    EVENT_TICK,
    OP_CONFIGURE_RTDS,
    OP_SHUTDOWN,
    RTDS_STREAM_CONTROL_CONTRACT,
    RTDS_STREAM_EVENT_CONTRACT,
    RTDS_STREAM_PROVIDER,
)
from .stream_worker_runtime import (
    StdioJsonWorkerProcess,
    StreamWorkerError,
    resolve_worker_command,
)


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
        self.reconnect_backoff_initial_sec = float(cfg.get("reconnect_backoff_initial_sec", 1.0))
        self.reconnect_backoff_max_sec = float(cfg.get("reconnect_backoff_max_sec", 30.0))
        self.max_queue_size = int(cfg.get("max_queue_size", 10000))
        self.history_max_points = max(0, int(cfg.get("history_max_points", 2048)))
        self.history_retention_sec = max(0.0, float(cfg.get("history_retention_sec", 900.0)))
        self.startup_ack_timeout_sec = float(cfg.get("startup_ack_timeout_sec", 10.0))
        self.worker_path = cfg.get("worker_path")
        self.worker_name = str(cfg.get("worker_name", "bro-rtds-stream-worker"))
        self.worker_env_var = str(cfg.get("worker_env_var", "BRO_RTDS_STREAM_WORKER"))
        self.stdout_queue_max = int(cfg.get("stdout_queue_max", 2048))
        self.stderr_tail_lines = int(cfg.get("stderr_tail_lines", 100))
        self.worker_restart_attempt_limit = max(1, int(cfg.get("worker_restart_attempt_limit", 5)))

        self._lock = threading.Lock()
        self._latest_by_symbol: Dict[str, ChainlinkTick] = {}
        self._history_by_symbol: Dict[str, Deque[ChainlinkTick]] = {}
        self._queue: Deque[ChainlinkTick] = deque()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = False
        self._transport_connected = False
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
        self._provider = RTDS_STREAM_PROVIDER
        self._contract_version = CONTRACT_VERSION
        self._subscription_state = "idle"
        self._pending_request_id: Optional[str] = None
        self._pending_ack_deadline_mono = 0.0
        self._last_transport_monotonic: Optional[float] = None
        self._stderr_tail: List[str] = []
        self._worker_command: Optional[List[str]] = None
        self._worker_usable = False
        self._worker_fatal_reason: Optional[str] = None
        self._worker_restart_exhausted = False
        self._worker_last_good_event_monotonic: Optional[float] = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            self._worker_command = resolve_worker_command(
                worker_name=self.worker_name,
                config_path_value=self.worker_path,
                env_var=self.worker_env_var,
            )
        except StreamWorkerError as exc:
            raise ChainlinkFeedError(str(exc)) from exc
        self._stop_event.clear()
        with self._lock:
            self._worker_usable = False
            self._worker_fatal_reason = None
            self._worker_restart_exhausted = False
            self._worker_last_good_event_monotonic = None
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

    def get_first_at_or_after(self, symbol: str, ts_utc: str) -> Optional[ChainlinkTick]:
        normalized = self._normalize_symbol(symbol)
        target_dt = parse_ts(ts_utc)
        if not normalized or target_dt is None:
            return None
        target_epoch = float(target_dt.timestamp())
        with self._lock:
            history = list(self._history_by_symbol.get(normalized, ()))
        best_tick: Optional[ChainlinkTick] = None
        best_epoch: Optional[float] = None
        for tick in history:
            source_epoch = self._source_epoch(tick)
            if source_epoch is None or source_epoch < target_epoch:
                continue
            if best_epoch is None or source_epoch < best_epoch or source_epoch == best_epoch:
                best_tick = tick
                best_epoch = source_epoch
        return best_tick

    def status(self) -> Dict[str, Any]:
        with self._lock:
            last = None
            if self._latest_by_symbol:
                last = max(t.received_monotonic for t in self._latest_by_symbol.values())
            queue_size = len(self._queue)
            dropped = self._dropped_ticks
            connected = self._connected
            transport_connected = self._transport_connected
            reconnects = self._reconnects
            last_error = self._last_error
            disorder_dropped = self._disorder_dropped_ticks
            duplicate_ticks = self._duplicate_ticks
            same_timestamp_revisions = self._same_timestamp_revisions
            missing_source_ts_dropped = self._missing_source_ts_dropped_ticks
            ordering_decision_counts = dict(self._ordering_decision_counts)
            ordering_class_counts = dict(self._ordering_class_counts)
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            provider = self._provider
            contract_version = self._contract_version
            subscription_state = self._subscription_state
            last_transport = self._last_transport_monotonic
            worker_usable = self._worker_usable
            worker_fatal_reason = self._worker_fatal_reason
            worker_restart_exhausted = self._worker_restart_exhausted
            worker_last_good = self._worker_last_good_event_monotonic
        now_mono = time.monotonic()
        age_sec = (now_mono - last) if last is not None else None
        transport_age_sec = (now_mono - last_transport) if last_transport is not None else None
        worker_last_good_age_sec = (now_mono - worker_last_good) if worker_last_good is not None else None
        return {
            "enabled": self.enabled,
            "connected": bool(connected and last is not None),
            "ws_connected": transport_connected,
            "transport_connected": transport_connected,
            "reconnects": reconnects,
            "last_tick_age_sec": age_sec,
            "last_transport_msg_age_sec": transport_age_sec,
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
            "thread_alive": thread_alive,
            "provider": provider,
            "contract_version": contract_version,
            "subscription_state": subscription_state,
            "worker_usable": worker_usable,
            "worker_fatal_reason": worker_fatal_reason,
            "worker_restart_exhausted": worker_restart_exhausted,
            "last_good_event_age_sec": worker_last_good_age_sec,
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

    def _thread_main(self) -> None:
        backoff = self.reconnect_backoff_initial_sec
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set():
                if not self._worker_command:
                    raise ChainlinkFeedError("RTDS worker command was not resolved before thread start")
                process = StdioJsonWorkerProcess(
                    command=self._worker_command,
                    name="rtds-stream-worker",
                    stderr_tail_lines=self.stderr_tail_lines,
                    stdout_queue_max=self.stdout_queue_max,
                )
                try:
                    process.start()
                    self._run_worker_session(process)
                    backoff = self.reconnect_backoff_initial_sec
                    consecutive_failures = 0
                except (ChainlinkFeedError, StreamWorkerError) as exc:
                    if self._stop_event.is_set():
                        break
                    consecutive_failures += 1
                    with self._lock:
                        self._connected = False
                        self._transport_connected = False
                        self._worker_usable = False
                        self._reconnects += 1
                        self._subscription_state = "restart_pending"
                        stderr_tail = " | ".join(self._stderr_tail[-5:]).strip()
                        self._last_error = f"{exc} :: {stderr_tail}" if stderr_tail else str(exc)
                    if consecutive_failures >= self.worker_restart_attempt_limit:
                        self._mark_worker_fatal(reason=f"restart_exhausted:{exc}", restart_exhausted=True)
                        break
                    time.sleep(min(self.reconnect_backoff_max_sec, backoff))
                    backoff = min(self.reconnect_backoff_max_sec, backoff * 2.0)
                finally:
                    try:
                        self._send_shutdown(process)
                    except StreamWorkerError:
                        pass
                    process.terminate(timeout_sec=2.0)
                    self._stderr_tail = process.stderr_tail()
                    with self._lock:
                        self._connected = False
                        self._transport_connected = False
                        if self._subscription_state != "failed_closed":
                            self._subscription_state = "disconnected"
        except Exception as exc:  # pragma: no cover - fail-closed guard
            with self._lock:
                self._connected = False
                self._transport_connected = False
                self._last_error = f"fatal:{exc}"
                self._worker_usable = False
                self._worker_fatal_reason = f"fatal:{exc}"
                self._worker_restart_exhausted = False
                self._subscription_state = "failed_closed"

    def _run_worker_session(self, process: StdioJsonWorkerProcess) -> None:
        self._send_config(process)
        while not self._stop_event.is_set():
            if self._pending_request_id and time.monotonic() > self._pending_ack_deadline_mono:
                raise ChainlinkFeedError(f"RTDS worker ack timeout for {self._pending_request_id}")
            item = process.recv(timeout=0.5)
            if item is not None:
                self._handle_worker_payload(item.get("payload"), received_monotonic=float(item.get("received_monotonic", time.monotonic())))
                continue
            if process.poll() is not None:
                raise ChainlinkFeedError(f"RTDS worker exited rc={process.poll()}")

    def _send_config(self, process: StdioJsonWorkerProcess) -> None:
        request_id = f"rtds-{int(time.time() * 1000)}-{len(self.symbols)}"
        with self._lock:
            self._pending_request_id = request_id
            self._pending_ack_deadline_mono = time.monotonic() + max(1.0, self.startup_ack_timeout_sec)
            self._subscription_state = "reconfigure_pending"
        process.send(
            {
                "contract": RTDS_STREAM_CONTROL_CONTRACT,
                "op": OP_CONFIGURE_RTDS,
                "request_id": request_id,
                "endpoint": self.ws_url,
                "topic": self.topic,
                "symbols": list(self._symbol_filter),
            }
        )

    @staticmethod
    def _send_shutdown(process: StdioJsonWorkerProcess) -> None:
        process.send({"contract": RTDS_STREAM_CONTROL_CONTRACT, "op": OP_SHUTDOWN})

    def _handle_worker_payload(self, payload: Any, *, received_monotonic: float) -> None:
        if not isinstance(payload, dict):
            raise ChainlinkFeedError("RTDS worker protocol violation: non-dict payload")
        event = str(payload.get("event") or "")
        contract = str(payload.get("contract") or "")
        if event == "worker_eof":
            raise ChainlinkFeedError("RTDS worker EOF")
        if event == EVENT_FATAL:
            reason = str(payload.get("fatal_reason") or payload.get("error") or "fatal_event").strip() or "fatal_event"
            raise ChainlinkFeedError(f"RTDS worker fatal:{reason}")
        if contract not in {"", RTDS_STREAM_EVENT_CONTRACT}:
            raise ChainlinkFeedError(f"RTDS worker protocol violation: unexpected contract {contract!r}")
        with self._lock:
            self._last_transport_monotonic = received_monotonic
        if event == EVENT_ACK:
            request_id = str(payload.get("request_id") or "")
            with self._lock:
                if request_id and request_id == self._pending_request_id:
                    self._pending_request_id = None
                    self._pending_ack_deadline_mono = 0.0
                    self._subscription_state = str(payload.get("subscription_state") or "configured")
                self._provider = str(payload.get("provider") or self._provider)
                self._contract_version = str(payload.get("contract_version") or self._contract_version)
                self._last_error = None
                self._worker_usable = True
                self._worker_fatal_reason = None
                self._worker_restart_exhausted = False
                self._worker_last_good_event_monotonic = received_monotonic
            return
        if event == EVENT_HEALTH:
            fatal_reason = str(payload.get("fatal_reason") or "").strip()
            restart_exhausted = bool(payload.get("restart_exhausted", False))
            if fatal_reason or restart_exhausted:
                reason = fatal_reason or "restart_exhausted"
                raise ChainlinkFeedError(f"RTDS worker fatal:{reason}")
            connected = bool(payload.get("connected", False))
            transport_connected = bool(payload.get("transport_connected", connected))
            usable = bool(payload.get("usable", True))
            with self._lock:
                self._connected = connected
                self._transport_connected = transport_connected
                self._provider = str(payload.get("provider") or self._provider)
                self._contract_version = str(payload.get("contract_version") or self._contract_version)
                self._subscription_state = str(payload.get("subscription_state") or self._subscription_state)
                error = str(payload.get("last_error") or "").strip()
                if error:
                    self._last_error = error
                else:
                    self._last_error = None
                self._worker_usable = usable
                self._worker_fatal_reason = None
                self._worker_restart_exhausted = False
                self._worker_last_good_event_monotonic = received_monotonic
            return
        if event != EVENT_TICK:
            raise ChainlinkFeedError(f"RTDS worker protocol violation: unexpected event {event!r}")
        symbol = self._normalize_symbol(str(payload.get("symbol") or ""))
        if not symbol:
            return
        if self._symbol_filter and symbol not in self._symbol_filter:
            return
        price = _as_float(payload.get("price"))
        if price is None:
            return
        tick = ChainlinkTick(
            symbol=symbol,
            price=price,
            source_ts_utc=str(payload.get("source_ts_utc") or "") or None,
            received_ts_utc=str(payload.get("received_ts_utc") or utc_iso()),
            received_monotonic=received_monotonic,
            topic=str(payload.get("topic") or self.topic),
            msg_type=str(payload.get("msg_type") or "update"),
        )
        self._ingest_tick(tick)
        with self._lock:
            self._connected = True
            self._transport_connected = True
            self._subscription_state = "active"
            self._provider = str(payload.get("provider") or self._provider)
            self._contract_version = str(payload.get("contract_version") or self._contract_version)
            self._last_error = None
            self._worker_usable = True
            self._worker_fatal_reason = None
            self._worker_restart_exhausted = False
            self._worker_last_good_event_monotonic = received_monotonic

    def _mark_worker_fatal(self, *, reason: str, restart_exhausted: bool) -> None:
        with self._lock:
            self._connected = False
            self._transport_connected = False
            self._subscription_state = "failed_closed"
            self._worker_usable = False
            self._worker_fatal_reason = str(reason)
            self._worker_restart_exhausted = bool(restart_exhausted)
            self._last_error = str(reason)

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
            self._append_history_locked(tick)
            if self.max_queue_size > 0:
                while len(self._queue) >= self.max_queue_size:
                    self._queue.popleft()
                    self._dropped_ticks += 1
            self._queue.append(tick)

    def _append_history_locked(self, tick: ChainlinkTick) -> None:
        if self.history_max_points <= 0 and self.history_retention_sec <= 0.0:
            return
        history = self._history_by_symbol.setdefault(tick.symbol, deque())
        history.append(tick)
        if self.history_max_points > 0:
            while len(history) > self.history_max_points:
                history.popleft()
        if self.history_retention_sec > 0.0:
            cutoff_mono = float(tick.received_monotonic) - float(self.history_retention_sec)
            while len(history) > 1 and float(history[0].received_monotonic) < cutoff_mono:
                history.popleft()

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

    # Compatibility shim for legacy unit fixtures. Runtime ownership stays on the
    # official worker contract; this only adapts historical in-process samples.
    def _handle_message_obj(self, sample: Any, received_monotonic: float) -> None:
        for payload in _legacy_tick_payloads(sample, default_symbol=(self.symbols[0] if self.symbols else "")):
            self._handle_worker_payload(payload, received_monotonic=received_monotonic)

    # Compatibility helper preserved for unit fixtures that verify the old intent
    # of subscribing to both authoritative and fallback crypto topics.
    def _build_subscribe_message(self) -> Dict[str, Any]:
        return {
            "action": "subscribe",
            "subscriptions": [
                {"topic": self.topic, "filters": {"symbols": list(self.symbols)}},
                {"topic": "crypto_prices", "filters": ""},
            ],
        }


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_from_legacy_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1_000_000_000_000:
            seconds = seconds / 1000.0
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{int((seconds % 1) * 1000):03d}Z"
    text = str(value).strip()
    return text or None


def _legacy_tick_payloads(sample: Any, *, default_symbol: str) -> List[Dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    topic = str(sample.get("topic") or "").strip().lower()
    if topic not in {"", "crypto_prices_chainlink", "crypto_prices"}:
        return []
    payload = sample.get("payload")
    if not isinstance(payload, dict):
        payload = sample.get("data")
    if not isinstance(payload, dict):
        payload = sample

    points: List[Dict[str, Any]] = []
    data = payload.get("data")
    data_is_batch = isinstance(data, list)
    if data_is_batch:
        for item in data:
            if isinstance(item, dict):
                merged = dict(item)
                if "symbol" not in merged and payload.get("symbol"):
                    merged["symbol"] = payload.get("symbol")
                points.append(merged)
    else:
        points.append(payload)

    out: List[Dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        raw_symbol = str(point.get("symbol") or "").strip()
        if not raw_symbol and not data_is_batch:
            continue
        symbol = ChainlinkFeed._normalize_symbol(raw_symbol or default_symbol or "")
        if not symbol:
            continue
        price = _as_float(point.get("value"))
        if price is None:
            price = _as_float(point.get("price"))
        if price is None:
            continue
        out.append(
            {
                "contract": RTDS_STREAM_EVENT_CONTRACT,
                "event": EVENT_TICK,
                "symbol": symbol,
                "price": price,
                "topic": "crypto_prices_chainlink",
                "msg_type": str(sample.get("type") or "update"),
                "source_ts_utc": _iso_from_legacy_timestamp(point.get("timestamp")),
                "received_ts_utc": utc_iso(),
            }
        )
    return out
