from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any, Dict, List, Optional

from .common import utc_iso
from .models import BookTop
from .stream_worker_contracts import (
    CONTRACT_VERSION,
    EVENT_ACK,
    EVENT_FATAL,
    EVENT_HEALTH,
    EVENT_TOP,
    MARKET_STREAM_CONTROL_CONTRACT,
    MARKET_STREAM_EVENT_CONTRACT,
    MARKET_STREAM_PROVIDER,
    OP_CONFIGURE_MARKET_WATCH,
    OP_SHUTDOWN,
)
from .stream_worker_runtime import (
    StdioJsonWorkerProcess,
    StreamWorkerError,
    resolve_worker_command,
)


class MarketBookFeedError(RuntimeError):
    pass


@dataclasses.dataclass
class _BookEntry:
    top: BookTop
    updated_monotonic: float


class MarketBookFeed:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.ws_url = str(cfg.get("url", "wss://ws-subscriptions-clob.polymarket.com"))
        self.stale_after_sec = float(cfg.get("stale_after_sec", 3.0))
        self.startup_ack_timeout_sec = float(cfg.get("startup_ack_timeout_sec", 10.0))
        self.reconnect_backoff_initial_sec = float(cfg.get("reconnect_backoff_initial_sec", 1.0))
        self.reconnect_backoff_max_sec = float(cfg.get("reconnect_backoff_max_sec", 20.0))
        self.worker_path = cfg.get("worker_path")
        self.worker_name = str(cfg.get("worker_name", "bro-market-stream-worker"))
        self.worker_env_var = str(cfg.get("worker_env_var", "BRO_MARKET_STREAM_WORKER"))
        self.stdout_queue_max = int(cfg.get("stdout_queue_max", 2048))
        self.stderr_tail_lines = int(cfg.get("stderr_tail_lines", 100))
        self.worker_restart_attempt_limit = max(1, int(cfg.get("worker_restart_attempt_limit", 5)))

        self._lock = threading.Lock()
        self._latest_by_token: Dict[str, _BookEntry] = {}
        self._token_ids: List[str] = []
        self._token_set: set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._resubscribe_event = threading.Event()
        self._connected = False
        self._transport_connected = False
        self._reconnects_total = 0
        self._reconnects_steady = 0
        self._reconnects_startup = 0
        self._last_transport_monotonic: Optional[float] = None
        self._last_msg_monotonic: Optional[float] = None
        self._last_error: Optional[str] = None
        self._primed = False
        self._provider = MARKET_STREAM_PROVIDER
        self._contract_version = CONTRACT_VERSION
        self._subscription_state = "idle"
        self._pending_request_id: Optional[str] = None
        self._pending_ack_deadline_mono = 0.0
        self._stderr_tail: List[str] = []
        self._worker_command: Optional[List[str]] = None
        self._worker_usable = False
        self._worker_fatal_reason: Optional[str] = None
        self._worker_restart_exhausted = False
        self._worker_last_good_event_monotonic: Optional[float] = None

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
        self.update_token_ids(token_ids)
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            self._worker_command = resolve_worker_command(
                worker_name=self.worker_name,
                config_path_value=self.worker_path,
                env_var=self.worker_env_var,
            )
        except StreamWorkerError as exc:
            raise MarketBookFeedError(str(exc)) from exc
        self._stop_event.clear()
        self._resubscribe_event.clear()
        with self._lock:
            self._worker_usable = False
            self._worker_fatal_reason = None
            self._worker_restart_exhausted = False
            self._worker_last_good_event_monotonic = None
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
                self._primed = False
                self._subscription_state = "reconfigure_pending"
        if changed:
            self._reset_freshness_state(clear_books=False, mark_disconnected=False)
            self._resubscribe_event.set()

    def request_resubscribe(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._primed = False
            self._subscription_state = "resubscribe_requested"
        self._reset_freshness_state(clear_books=False, mark_disconnected=False)
        self._resubscribe_event.set()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            connected = bool(self._connected and self._last_msg_monotonic is not None)
            transport_connected = bool(self._transport_connected and self._last_transport_monotonic is not None)
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
            provider = self._provider
            contract_version = self._contract_version
            subscription_state = self._subscription_state
            worker_usable = self._worker_usable
            worker_fatal_reason = self._worker_fatal_reason
            worker_restart_exhausted = self._worker_restart_exhausted
            worker_last_good = self._worker_last_good_event_monotonic
        now_mono = time.monotonic()
        transport_age_sec = (now_mono - last_transport_msg) if last_transport_msg is not None else None
        age_sec = (now_mono - last_msg) if last_msg is not None else None
        worker_last_good_age_sec = (now_mono - worker_last_good) if worker_last_good is not None else None
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
            "provider": provider,
            "contract_version": contract_version,
            "subscription_state": subscription_state,
            "worker_usable": worker_usable,
            "worker_fatal_reason": worker_fatal_reason,
            "worker_restart_exhausted": worker_restart_exhausted,
            "last_good_event_age_sec": worker_last_good_age_sec,
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
        backoff = self.reconnect_backoff_initial_sec
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    token_ids = list(self._token_ids)
                if not token_ids:
                    time.sleep(0.25)
                    continue
                if not self._worker_command:
                    raise MarketBookFeedError("market worker command was not resolved before thread start")
                process = StdioJsonWorkerProcess(
                    command=self._worker_command,
                    name="market-stream-worker",
                    stderr_tail_lines=self.stderr_tail_lines,
                    stdout_queue_max=self.stdout_queue_max,
                )
                try:
                    process.start()
                    self._run_worker_session(process)
                    backoff = self.reconnect_backoff_initial_sec
                    consecutive_failures = 0
                except (MarketBookFeedError, StreamWorkerError) as exc:
                    if self._stop_event.is_set():
                        break
                    consecutive_failures += 1
                    self._record_reconnect(error=str(exc))
                    self._stderr_tail = process.stderr_tail()
                    self._reset_freshness_state(clear_books=False, mark_disconnected=True)
                    if consecutive_failures >= self.worker_restart_attempt_limit:
                        self._mark_worker_fatal(
                            reason=f"restart_exhausted:{exc}",
                            restart_exhausted=True,
                        )
                        break
                    sleep_for = min(self.reconnect_backoff_max_sec, backoff)
                    time.sleep(sleep_for)
                    backoff = min(self.reconnect_backoff_max_sec, backoff * 2.0)
                finally:
                    try:
                        self._send_shutdown(process)
                    except StreamWorkerError:
                        pass
                    process.terminate(timeout_sec=2.0)
                    self._stderr_tail = process.stderr_tail()
                    self._reset_freshness_state(clear_books=False, mark_disconnected=True)
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
        self._send_watch_config(process)
        while not self._stop_event.is_set():
            if self._resubscribe_event.is_set():
                self._resubscribe_event.clear()
                self._send_watch_config(process)
            if self._pending_request_id and time.monotonic() > self._pending_ack_deadline_mono:
                raise MarketBookFeedError(f"market worker ack timeout for {self._pending_request_id}")
            item = process.recv(timeout=0.5)
            if item is not None:
                payload = item.get("payload")
                received_monotonic = float(item.get("received_monotonic", time.monotonic()))
                self._handle_worker_payload(payload, received_monotonic=received_monotonic)
                continue
            if process.poll() is not None:
                raise MarketBookFeedError(f"market worker exited rc={process.poll()}")

    def _send_watch_config(self, process: StdioJsonWorkerProcess) -> None:
        with self._lock:
            token_ids = list(self._token_ids)
            request_id = f"watch-{int(time.time() * 1000)}-{len(token_ids)}"
            self._pending_request_id = request_id
            self._pending_ack_deadline_mono = time.monotonic() + max(1.0, self.startup_ack_timeout_sec)
            self._subscription_state = "reconfigure_pending"
        process.send(
            {
                "contract": MARKET_STREAM_CONTROL_CONTRACT,
                "op": OP_CONFIGURE_MARKET_WATCH,
                "request_id": request_id,
                "endpoint": self.ws_url,
                "token_ids": token_ids,
            }
        )

    @staticmethod
    def _send_shutdown(process: StdioJsonWorkerProcess) -> None:
        process.send(
            {
                "contract": MARKET_STREAM_CONTROL_CONTRACT,
                "op": OP_SHUTDOWN,
            }
        )

    def _reset_freshness_state(self, *, clear_books: bool, mark_disconnected: bool) -> None:
        with self._lock:
            if mark_disconnected:
                self._connected = False
                self._transport_connected = False
                if self._subscription_state != "failed_closed":
                    self._subscription_state = "disconnected"
            self._last_transport_monotonic = None
            self._last_msg_monotonic = None
            if clear_books:
                self._latest_by_token.clear()

    def _record_reconnect(self, *, error: str) -> None:
        with self._lock:
            self._connected = False
            self._transport_connected = False
            self._subscription_state = "restart_pending"
            self._worker_usable = False
            stderr_tail = " | ".join(self._stderr_tail[-5:]).strip()
            if stderr_tail:
                self._last_error = f"{error} :: {stderr_tail}"
            else:
                self._last_error = str(error)
            self._reconnects_total += 1
            if self._primed:
                self._reconnects_steady += 1
            else:
                self._reconnects_startup += 1

    def _handle_worker_payload(self, payload: Any, *, received_monotonic: float) -> None:
        if not isinstance(payload, dict):
            raise MarketBookFeedError("market worker protocol violation: non-dict payload")
        event = str(payload.get("event") or "")
        contract = str(payload.get("contract") or "")
        if event == "worker_eof":
            raise MarketBookFeedError("market worker EOF")
        if event == EVENT_FATAL:
            reason = str(payload.get("fatal_reason") or payload.get("error") or "fatal_event").strip() or "fatal_event"
            raise MarketBookFeedError(f"market worker fatal:{reason}")
        if contract not in {"", MARKET_STREAM_EVENT_CONTRACT}:
            raise MarketBookFeedError(f"market worker protocol violation: unexpected contract {contract!r}")
        with self._lock:
            self._last_transport_monotonic = received_monotonic
        if event == EVENT_ACK:
            self._handle_ack(payload)
            return
        if event == EVENT_HEALTH:
            self._handle_health_event(payload, received_monotonic=received_monotonic)
            return
        if event == EVENT_TOP:
            self._handle_top_event(payload, received_monotonic=received_monotonic)
            return
        raise MarketBookFeedError(f"market worker protocol violation: unexpected event {event!r}")

    def _handle_ack(self, payload: Dict[str, Any]) -> None:
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
            self._worker_last_good_event_monotonic = time.monotonic()

    def _handle_health_event(self, payload: Dict[str, Any], *, received_monotonic: float) -> None:
        fatal_reason = str(payload.get("fatal_reason") or "").strip()
        restart_exhausted = bool(payload.get("restart_exhausted", False))
        if fatal_reason or restart_exhausted:
            reason = fatal_reason or "restart_exhausted"
            raise MarketBookFeedError(f"market worker fatal:{reason}")
        connected = bool(payload.get("connected", False))
        transport_connected = bool(payload.get("transport_connected", connected))
        usable = bool(payload.get("usable", True))
        with self._lock:
            self._connected = connected
            self._transport_connected = transport_connected
            self._provider = str(payload.get("provider") or self._provider)
            self._contract_version = str(payload.get("contract_version") or self._contract_version)
            self._subscription_state = str(payload.get("subscription_state") or self._subscription_state)
            if transport_connected:
                self._last_transport_monotonic = received_monotonic
            error = str(payload.get("last_error") or "").strip()
            if error:
                self._last_error = error
            else:
                self._last_error = None
            self._worker_usable = usable
            self._worker_fatal_reason = None
            self._worker_restart_exhausted = False
            self._worker_last_good_event_monotonic = received_monotonic

    def _handle_top_event(self, payload: Dict[str, Any], *, received_monotonic: float) -> None:
        token_id = str(payload.get("token_id") or "").strip()
        if not token_id:
            return
        with self._lock:
            if token_id not in self._token_set:
                return
        top = BookTop(
            token_id=token_id,
            ts_utc=str(payload.get("source_ts_utc") or payload.get("received_ts_utc") or utc_iso()),
            source=str(payload.get("source") or "ws"),
            best_bid_price=_as_float(payload.get("best_bid_price")),
            best_bid_size=_as_float(payload.get("best_bid_size")),
            best_ask_price=_as_float(payload.get("best_ask_price")),
            best_ask_size=_as_float(payload.get("best_ask_size")),
        )
        if top.best_bid_price is None and top.best_ask_price is None:
            return
        with self._lock:
            self._latest_by_token[token_id] = _BookEntry(top=top, updated_monotonic=received_monotonic)
            self._last_msg_monotonic = received_monotonic
            self._connected = True
            self._transport_connected = True
            self._subscription_state = "active"
            self._provider = str(payload.get("provider") or self._provider)
            self._contract_version = str(payload.get("contract_version") or self._contract_version)
            self._primed = True
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

    # Compatibility shim for legacy unit fixtures. Runtime ownership stays on the
    # official worker contract; this only adapts historical in-process samples.
    def _handle_message_obj(self, sample: Any, *, received_monotonic: float) -> None:
        self._handle_worker_payload(
            _legacy_top_payload(sample),
            received_monotonic=received_monotonic,
        )


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


def _extract_legacy_book_payload(sample: Any) -> Dict[str, Any]:
    if not isinstance(sample, dict):
        return {}
    for key in ("payload", "data"):
        nested = sample.get(key)
        if isinstance(nested, dict):
            return nested
    return sample


def _first_level(levels: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(levels, list) or not levels:
        return None, None
    top = levels[0]
    if not isinstance(top, (list, tuple)) or len(top) < 2:
        return None, None
    return _as_float(top[0]), _as_float(top[1])


def _legacy_top_payload(sample: Any) -> Dict[str, Any]:
    payload = _extract_legacy_book_payload(sample)
    bid_price = _as_float(payload.get("best_bid"))
    ask_price = _as_float(payload.get("best_ask"))
    bid_size = _as_float(payload.get("best_bid_size"))
    ask_size = _as_float(payload.get("best_ask_size"))
    if bid_price is None and bid_size is None:
        bid_price, bid_size = _first_level(payload.get("bids"))
    if ask_price is None and ask_size is None:
        ask_price, ask_size = _first_level(payload.get("asks"))
    return {
        "contract": MARKET_STREAM_EVENT_CONTRACT,
        "event": EVENT_TOP,
        "token_id": str(
            payload.get("token_id")
            or payload.get("asset_id")
            or payload.get("market")
            or ""
        ).strip(),
        "source": str(payload.get("source") or "legacy_fixture_top"),
        "best_bid_price": bid_price,
        "best_bid_size": bid_size,
        "best_ask_price": ask_price,
        "best_ask_size": ask_size,
        "source_ts_utc": _iso_from_legacy_timestamp(payload.get("timestamp")),
        "received_ts_utc": utc_iso(),
    }
