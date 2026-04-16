from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import threading
import time
import contextlib
from typing import Any, Dict, Optional

from .common import utc_now


_SENSITIVE_EXACT_KEYS = {
    "secret",
    "password",
    "private_key",
    "api_key",
    "token",
    "token_id",
    "session_token",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "funder",
}
_SENSITIVE_KEY_FAMILIES = (
    "token_id",
    "session_token",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "private_key",
    "api_key",
    "password",
    "secret",
    "funder",
)
_KEY_NORMALIZE_RE = re.compile(r"[^a-z0-9_]+")


def _normalize_key(key: Any) -> str:
    text = str(key or "").strip().lower()
    return _KEY_NORMALIZE_RE.sub("_", text).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if not normalized:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    for family in _SENSITIVE_KEY_FAMILIES:
        if normalized.startswith(f"{family}_") or normalized.endswith(f"_{family}"):
            return True
    return False


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = redact_sensitive(item)
        return out
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


class DailyJsonlWriter:
    def __init__(
        self,
        log_dir: pathlib.Path,
        prefix: str,
        *,
        flush_every_records: int = 1,
        flush_interval_sec: float = 0.25,
        async_flush: bool = False,
        fsync_on_flush: bool = False,
    ):
        self.log_dir = log_dir
        self.prefix = prefix
        self.flush_every_records = max(1, int(flush_every_records))
        self.flush_interval_sec = max(0.01, float(flush_interval_sec))
        self.async_flush = bool(async_flush)
        self.fsync_on_flush = bool(fsync_on_flush)
        self._lock = threading.Lock()
        self._fh = None
        self._day = None
        self._pending: list[str] = []
        self._last_flush_mono = time.monotonic()
        self._stop_event = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self.current_path: Optional[pathlib.Path] = None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.async_flush:
            self._flush_thread = threading.Thread(target=self._flush_loop, name=f"{prefix}-jsonl-flush", daemon=True)
            self._flush_thread.start()

    def _ensure_open(self) -> None:
        day = utc_now().date().isoformat()
        if self._fh is not None and self._day == day:
            return
        self._flush_unlocked()
        self._close_file_unlocked()
        self._day = day
        self.current_path = self.log_dir / f"{self.prefix}_{day}.jsonl"
        self._fh = self.current_path.open("a", encoding="utf-8")

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=True, default=str) + "\n"
        with self._lock:
            self._ensure_open()
            self._pending.append(line)
            should_flush = (
                len(self._pending) >= self.flush_every_records
                or (time.monotonic() - self._last_flush_mono) >= self.flush_interval_sec
            )
            if should_flush:
                self._flush_unlocked()

    def close(self) -> None:
        self._stop_event.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=5)
        self._flush_thread = None
        with self._lock:
            self._flush_unlocked()
            self._close_file_unlocked()

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self.flush_interval_sec):
            with self._lock:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if self._fh is None:
            return
        if not self._pending:
            return
        self._fh.write("".join(self._pending))
        self._pending.clear()
        self._fh.flush()
        if self.fsync_on_flush:
            with contextlib.suppress(Exception):
                fileno = self._fh.fileno()
                if fileno >= 0:
                    os.fsync(fileno)
        self._last_flush_mono = time.monotonic()

    def _close_file_unlocked(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
        self._fh = None


class EventLogger:
    def __init__(
        self,
        log_dir: pathlib.Path,
        writer_cfg: Optional[Dict[str, Any]] = None,
        default_fields: Optional[Dict[str, Any]] = None,
    ):
        cfg = writer_cfg or {}
        writer_kwargs = {
            "flush_every_records": int(cfg.get("flush_every_records", 1)),
            "flush_interval_sec": float(cfg.get("flush_interval_sec", 0.25)),
            "async_flush": bool(cfg.get("async_flush", False)),
            "fsync_on_flush": bool(cfg.get("fsync_on_flush", False)),
        }
        self.default_fields = dict(default_fields or {})
        self.events = DailyJsonlWriter(log_dir, "events", **writer_kwargs)
        self.status = DailyJsonlWriter(log_dir, "status", **writer_kwargs)
        self.errors = DailyJsonlWriter(log_dir, "errors", **writer_kwargs)

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        row = redact_sensitive({"event_type": event_type, **self.default_fields, **payload})
        if "ts_event_utc" not in row and "ts_utc" in row:
            row["ts_event_utc"] = row.get("ts_utc")
        if "ts_receive_utc" not in row and "received_ts_utc" in row:
            row["ts_receive_utc"] = row.get("received_ts_utc")
        if "ts_source_utc" not in row and "source_ts_utc" in row:
            row["ts_source_utc"] = row.get("source_ts_utc")
        # Timestamp domain contract is additive-first: always surface all time domains.
        # Unknown domains remain explicit nulls rather than implicit omission.
        if "ts_event_utc" not in row:
            row["ts_event_utc"] = row.get("ts_utc")
        if "ts_receive_utc" not in row:
            row["ts_receive_utc"] = None
        if "ts_source_utc" not in row:
            row["ts_source_utc"] = None
        if "ts_decision_utc" not in row:
            row["ts_decision_utc"] = row.get("ts_utc")
        self.events.write(row)

    def log_status(self, payload: Dict[str, Any]) -> None:
        row = redact_sensitive({**self.default_fields, **payload})
        if "ts_status_utc" not in row and "ts_utc" in row:
            row["ts_status_utc"] = row.get("ts_utc")
        self.status.write(row)

    def log_error(self, payload: Dict[str, Any]) -> None:
        row = redact_sensitive({**self.default_fields, **payload})
        if "ts_error_utc" not in row and "ts_utc" in row:
            row["ts_error_utc"] = row.get("ts_utc")
        self.errors.write(row)

    def close(self) -> None:
        self.events.close()
        self.status.close()
        self.errors.close()


def configure_console_logging(level: int = logging.INFO) -> None:
    class _RedactingFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                if isinstance(record.msg, dict):
                    record.msg = redact_sensitive(record.msg)
                if record.args:
                    args = record.args if isinstance(record.args, tuple) else (record.args,)
                    record.args = tuple(redact_sensitive(arg) for arg in args)
            except Exception:
                return True
            return True

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    root_logger = logging.getLogger()
    has_filter = any(isinstance(f, _RedactingFilter) for f in root_logger.filters)
    if not has_filter:
        root_logger.addFilter(_RedactingFilter())
