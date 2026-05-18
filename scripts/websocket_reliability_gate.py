#!/usr/bin/env python3
"""Websocket reliability SLO gate over status telemetry."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Optional


from prodesk.artifact_identity import build_artifact_identity, candidate_run_log_dirs
from prodesk.error_codes import summarize_error_codes
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.session_phase import enforce_validation_phase
import yaml

DEFAULT_MAX_LINES_PER_FILE = 200000


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _coerce_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _parse_ts(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    return default


def _websocket_truth_required(row: Dict[str, Any]) -> bool:
    if _as_bool(row.get("active_targets_present"), default=False):
        return True
    if _as_bool(row.get("market_truth_required"), default=False):
        return True
    if str(row.get("owned_market_ref") or "").strip():
        return True
    if str(row.get("challenger_market_ref") or "").strip():
        return True
    return False


def _reconnect_counter(payload: Dict[str, Any]) -> Optional[float]:
    # Prefer steady-state reconnect accounting when emitted by runtime.
    steady = _coerce_float(payload.get("reconnects_steady"))
    if steady is not None and steady >= 0.0:
        return steady
    return _coerce_float(payload.get("reconnects"))


def _tail_lines(path: pathlib.Path, *, limit: int) -> List[str]:
    max_lines = max(0, int(limit))
    if max_lines <= 0:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return [line.rstrip("\n") for line in fh]

    with path.open("rb") as fh:
        fh.seek(0, 2)
        file_size = fh.tell()
        block_size = 64 * 1024
        data = b""
        pos = file_size
        newline_count = 0

        while pos > 0 and newline_count <= max_lines:
            read_size = min(block_size, pos)
            pos -= read_size
            fh.seek(pos)
            chunk = fh.read(read_size)
            data = chunk + data
            newline_count = data.count(b"\n")

    lines = data.splitlines()[-max_lines:]
    return [line.decode("utf-8", errors="ignore") for line in lines]


def _load_status_rows(
    log_dir: pathlib.Path,
    run_id: Optional[str],
    *,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    status_paths: Optional[List[pathlib.Path]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    limit = max(0, int(max_lines_per_file))
    source_paths = status_paths if status_paths is not None else sorted(log_dir.glob("status_*.jsonl"))
    for path in source_paths:
        if not path.exists():
            continue
        for text in _tail_lines(path, limit=limit):
            line = text.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if run_id and str(row.get("run_id") or "").strip() != run_id:
                continue
            rows.append(row)
    return rows


def _load_rows_from_slice(path: pathlib.Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _load_budget(path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("budget root must be a mapping")
    return payload


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    clipped = max(0.0, min(1.0, float(q)))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * clipped))
    return float(ordered[idx])


def run_gate(
    *,
    log_dir: pathlib.Path,
    run_id: Optional[str],
    min_status_rows: int,
    max_book_feed_down_ratio: float,
    max_chainlink_down_ratio: float,
    max_book_feed_reconnects_per_hour: float,
    max_chainlink_reconnects_per_hour: float,
    max_book_feed_last_msg_age_sec: float,
    max_chainlink_last_tick_age_sec: float,
    max_book_feed_last_msg_age_p95_sec: float,
    max_chainlink_last_tick_age_p95_sec: float,
    max_chainlink_dropped_ticks: float,
    max_chainlink_queue_size: float,
    max_book_feed_worker_unusable_rows: int = 0,
    max_chainlink_worker_unusable_rows: int = 0,
    max_book_feed_worker_restart_exhausted_rows: int = 0,
    max_chainlink_worker_restart_exhausted_rows: int = 0,
    max_gateway_heartbeat_age_sec: float = 12.0,
    max_gateway_heartbeat_missing_or_invalid_rows: int = 0,
    max_gateway_heartbeat_disabled_resting_rows: int = 0,
    max_gateway_matching_engine_error_rows: int = 0,
    max_gateway_matching_engine_restart_window_age_sec: float = 30.0,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    max_book_feed_last_msg_age_spike_rows: int = 0,
    max_chainlink_last_tick_age_spike_rows: int = 0,
    max_book_feed_last_msg_age_spike_ratio: float = 0.0,
    max_chainlink_last_tick_age_spike_ratio: float = 0.0,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    normalized_phase = enforce_validation_phase(validation_name="websocket_reliability_gate", session_phase=session_phase)
    explicit_run_id = str(run_id or "").strip()
    context_hints: Dict[str, Any] = {"candidate_log_dirs_for_run": []}
    if not explicit_run_id:
        findings.append("websocket_slo_run_id_required")
        resolved_run_id = ""
        rows: List[Dict[str, Any]] = []
        contract = None
    else:
        resolved_run_id = explicit_run_id
        resolved_log_dir = log_dir.resolve()
        contract = resolve_run_contract(
            log_dir=resolved_log_dir,
            run_id=resolved_run_id,
            run_contract_path_override=run_contract_path,
            allow_open=(normalized_phase == "validate_active"),
        )
        status_slice = run_contract_slice_path(contract, stream="status") if contract is not None else None
        if status_slice is not None:
            rows = apply_contract_bounds(list(_load_rows_from_slice(status_slice)), contract)
        else:
            status_paths: Optional[List[pathlib.Path]] = None
            if isinstance(contract, dict):
                status_path_text = str(contract.get("status_path") or "").strip()
                if status_path_text:
                    status_candidate = pathlib.Path(status_path_text).expanduser()
                    if status_candidate.exists():
                        status_paths = [status_candidate]
            rows = _load_status_rows(
                resolved_log_dir,
                resolved_run_id,
                max_lines_per_file=max_lines_per_file,
                status_paths=status_paths,
            )
            rows = apply_contract_bounds(rows, contract)
        if not rows:
            context_hints["candidate_log_dirs_for_run"] = candidate_run_log_dirs(
                log_dir=resolved_log_dir,
                run_id=resolved_run_id,
                max_depth=3,
            )
            if context_hints["candidate_log_dirs_for_run"]:
                warnings.append("websocket_slo_run_context_candidate_log_dirs_present")

    if len(rows) < int(min_status_rows):
        findings.append(f"websocket_slo_status_rows_too_few:{len(rows)}<min:{int(min_status_rows)}")

    ts_vals = [_parse_ts(r.get("ts_utc")) for r in rows]
    ts_vals = [x for x in ts_vals if x is not None]
    duration_sec = 0.0
    if len(ts_vals) >= 2:
        duration_sec = max(0.0, (max(ts_vals) - min(ts_vals)).total_seconds())
    duration_hours = max(duration_sec / 3600.0, 1.0 / 3600.0)

    book_down_count = 0
    chain_down_count = 0
    max_book_reconnects = 0.0
    max_chain_reconnects = 0.0
    max_book_age = 0.0
    max_chain_age = 0.0
    book_age_vals: List[float] = []
    chain_age_vals: List[float] = []
    max_chain_queue = 0.0
    max_chain_dropped = 0.0
    missing_book_count = 0
    missing_chain_count = 0
    missing_book_reconnect_count = 0
    missing_chain_reconnect_count = 0
    missing_book_age_count = 0
    missing_chain_age_count = 0
    missing_chain_queue_count = 0
    missing_chain_dropped_count = 0
    book_age_spike_rows = 0
    chain_age_spike_rows = 0
    gateway_heartbeat_missing_or_invalid_rows = 0
    gateway_heartbeat_disabled_resting_rows = 0
    gateway_heartbeat_stale_rows = 0
    gateway_matching_engine_error_rows = 0
    gateway_matching_engine_restart_window_rows = 0
    max_gateway_heartbeat_age = 0.0
    max_gateway_heartbeat_failures = 0.0
    max_gateway_matching_engine_restart_window_age = 0.0
    book_worker_unusable_rows = 0
    chain_worker_unusable_rows = 0
    book_worker_restart_exhausted_rows = 0
    chain_worker_restart_exhausted_rows = 0
    truth_required_rows = 0
    for row in rows:
        book = _as_dict(row.get("book_feed"))
        chain = _as_dict(row.get("chainlink"))
        gateway = _as_dict(row.get("gateway"))
        truth_required = _websocket_truth_required(row)
        if truth_required:
            truth_required_rows += 1
            if not book:
                missing_book_count += 1
            if not chain:
                missing_chain_count += 1
        book_connected = _as_bool(book.get("connected"), default=False)
        chain_connected = _as_bool(chain.get("connected"), default=False)
        if truth_required and not book_connected:
            book_down_count += 1
        if truth_required and not chain_connected:
            chain_down_count += 1
        if book:
            if bool(book.get("enabled", False)) and not _as_bool(book.get("worker_usable"), default=True):
                book_worker_unusable_rows += 1
            if _as_bool(book.get("worker_restart_exhausted"), default=False):
                book_worker_restart_exhausted_rows += 1
            reconnects = _reconnect_counter(book)
            if truth_required and reconnects is None:
                missing_book_reconnect_count += 1
            else:
                if reconnects is not None:
                    max_book_reconnects = max(max_book_reconnects, float(reconnects))
        if chain:
            if bool(chain.get("enabled", False)) and not _as_bool(chain.get("worker_usable"), default=True):
                chain_worker_unusable_rows += 1
            if _as_bool(chain.get("worker_restart_exhausted"), default=False):
                chain_worker_restart_exhausted_rows += 1
            reconnects = _reconnect_counter(chain)
            if truth_required and reconnects is None:
                missing_chain_reconnect_count += 1
            else:
                if reconnects is not None:
                    max_chain_reconnects = max(max_chain_reconnects, float(reconnects))
        if book:
            book_age_raw = book.get("last_msg_age_sec")
            book_age = _coerce_float(book.get("last_msg_age_sec"))
            if truth_required and book_age is None:
                if book_age_raw is not None or book_connected:
                    missing_book_age_count += 1
            elif truth_required:
                max_book_age = max(max_book_age, float(book_age))
                book_age_vals.append(float(book_age))
                if float(book_age) > float(max_book_feed_last_msg_age_sec):
                    book_age_spike_rows += 1
        if chain:
            chain_age_raw = chain.get("last_tick_age_sec")
            chain_age = _coerce_float(chain.get("last_tick_age_sec"))
            if truth_required and chain_age is None:
                if chain_age_raw is not None or chain_connected:
                    missing_chain_age_count += 1
            elif truth_required:
                max_chain_age = max(max_chain_age, float(chain_age))
                chain_age_vals.append(float(chain_age))
                if float(chain_age) > float(max_chainlink_last_tick_age_sec):
                    chain_age_spike_rows += 1
            queue_size = _coerce_float(chain.get("queue_size"))
            if truth_required and queue_size is None:
                missing_chain_queue_count += 1
            elif queue_size is not None:
                max_chain_queue = max(max_chain_queue, float(queue_size))
            dropped_ticks = _coerce_float(chain.get("dropped_ticks"))
            if truth_required and dropped_ticks is None:
                missing_chain_dropped_count += 1
            elif dropped_ticks is not None:
                max_chain_dropped = max(max_chain_dropped, float(dropped_ticks))
        if gateway:
            resting_orders_present = _as_bool(gateway.get("resting_orders_present"), default=False)
            heartbeat_enabled = _as_bool(gateway.get("heartbeat_enabled"), default=False)
            heartbeat_age = _coerce_float(gateway.get("heartbeat_last_success_age_sec"))
            heartbeat_failures = _coerce_float(gateway.get("heartbeat_failures"))
            if heartbeat_failures is not None:
                max_gateway_heartbeat_failures = max(max_gateway_heartbeat_failures, float(heartbeat_failures))
            if resting_orders_present:
                if not heartbeat_enabled:
                    gateway_heartbeat_disabled_resting_rows += 1
                elif heartbeat_age is None:
                    gateway_heartbeat_missing_or_invalid_rows += 1
                else:
                    max_gateway_heartbeat_age = max(max_gateway_heartbeat_age, float(heartbeat_age))
                    if float(heartbeat_age) > float(max_gateway_heartbeat_age_sec):
                        gateway_heartbeat_stale_rows += 1
            matching_engine_status = str(gateway.get("matching_engine_status") or "").strip().lower()
            if matching_engine_status == "error":
                gateway_matching_engine_error_rows += 1
            elif matching_engine_status == "restart_window":
                gateway_matching_engine_restart_window_rows += 1
                restart_window_age = _coerce_float(gateway.get("matching_engine_restart_window_age_sec"))
                if restart_window_age is not None:
                    max_gateway_matching_engine_restart_window_age = max(
                        max_gateway_matching_engine_restart_window_age,
                        float(restart_window_age),
                    )

    sample_count = len(rows)
    book_down_ratio = (float(book_down_count) / float(truth_required_rows)) if truth_required_rows > 0 else 0.0
    chain_down_ratio = (float(chain_down_count) / float(truth_required_rows)) if truth_required_rows > 0 else 0.0
    book_age_spike_ratio = (float(book_age_spike_rows) / float(truth_required_rows)) if truth_required_rows > 0 else 0.0
    chain_age_spike_ratio = (float(chain_age_spike_rows) / float(truth_required_rows)) if truth_required_rows > 0 else 0.0
    book_reconnects_per_hour = max_book_reconnects / duration_hours
    chain_reconnects_per_hour = max_chain_reconnects / duration_hours
    book_age_p95 = _percentile(book_age_vals, 0.95)
    chain_age_p95 = _percentile(chain_age_vals, 0.95)

    if missing_book_count > 0:
        findings.append(f"websocket_slo_book_feed_missing_rows:{missing_book_count}")
    if missing_chain_count > 0:
        findings.append(f"websocket_slo_chainlink_missing_rows:{missing_chain_count}")
    if missing_book_reconnect_count > 0:
        findings.append(f"websocket_slo_book_feed_reconnects_missing_or_invalid_rows:{missing_book_reconnect_count}")
    if missing_chain_reconnect_count > 0:
        findings.append(f"websocket_slo_chainlink_reconnects_missing_or_invalid_rows:{missing_chain_reconnect_count}")
    if missing_book_age_count > 0:
        findings.append(f"websocket_slo_book_feed_last_msg_age_missing_or_invalid_rows:{missing_book_age_count}")
    if missing_chain_age_count > 0:
        findings.append(f"websocket_slo_chainlink_last_tick_age_missing_or_invalid_rows:{missing_chain_age_count}")
    if missing_chain_queue_count > 0:
        findings.append(f"websocket_slo_chainlink_queue_size_missing_or_invalid_rows:{missing_chain_queue_count}")
    if missing_chain_dropped_count > 0:
        findings.append(f"websocket_slo_chainlink_dropped_ticks_missing_or_invalid_rows:{missing_chain_dropped_count}")
    if book_down_ratio > float(max_book_feed_down_ratio):
        findings.append(
            f"websocket_slo_book_feed_down_ratio_too_high:{book_down_ratio:.6f}>max:{float(max_book_feed_down_ratio):.6f}"
        )
    if chain_down_ratio > float(max_chainlink_down_ratio):
        findings.append(
            f"websocket_slo_chainlink_down_ratio_too_high:{chain_down_ratio:.6f}>max:{float(max_chainlink_down_ratio):.6f}"
        )
    if book_reconnects_per_hour > float(max_book_feed_reconnects_per_hour):
        findings.append(
            "websocket_slo_book_feed_reconnects_per_hour_too_high:"
            + f"{book_reconnects_per_hour:.6f}>max:{float(max_book_feed_reconnects_per_hour):.6f}"
        )
    if chain_reconnects_per_hour > float(max_chainlink_reconnects_per_hour):
        findings.append(
            "websocket_slo_chainlink_reconnects_per_hour_too_high:"
            + f"{chain_reconnects_per_hour:.6f}>max:{float(max_chainlink_reconnects_per_hour):.6f}"
        )
    if (
        book_age_spike_rows > int(max_book_feed_last_msg_age_spike_rows)
        and book_age_spike_ratio > float(max_book_feed_last_msg_age_spike_ratio)
    ):
        findings.append(
            "websocket_slo_book_feed_last_msg_age_too_high:"
            + f"spike_rows:{book_age_spike_rows}>max:{int(max_book_feed_last_msg_age_spike_rows)}"
            + f":spike_ratio:{book_age_spike_ratio:.6f}>max:{float(max_book_feed_last_msg_age_spike_ratio):.6f}"
            + f":peak:{max_book_age:.6f}>max_age:{float(max_book_feed_last_msg_age_sec):.6f}"
        )
    if (
        chain_age_spike_rows > int(max_chainlink_last_tick_age_spike_rows)
        and chain_age_spike_ratio > float(max_chainlink_last_tick_age_spike_ratio)
    ):
        findings.append(
            "websocket_slo_chainlink_last_tick_age_too_high:"
            + f"spike_rows:{chain_age_spike_rows}>max:{int(max_chainlink_last_tick_age_spike_rows)}"
            + f":spike_ratio:{chain_age_spike_ratio:.6f}>max:{float(max_chainlink_last_tick_age_spike_ratio):.6f}"
            + f":peak:{max_chain_age:.6f}>max_age:{float(max_chainlink_last_tick_age_sec):.6f}"
        )
    if book_age_p95 > float(max_book_feed_last_msg_age_p95_sec):
        findings.append(
            "websocket_slo_book_feed_last_msg_age_p95_too_high:"
            + f"{book_age_p95:.6f}>max:{float(max_book_feed_last_msg_age_p95_sec):.6f}"
        )
    if chain_age_p95 > float(max_chainlink_last_tick_age_p95_sec):
        findings.append(
            "websocket_slo_chainlink_last_tick_age_p95_too_high:"
            + f"{chain_age_p95:.6f}>max:{float(max_chainlink_last_tick_age_p95_sec):.6f}"
        )
    if max_chain_dropped > float(max_chainlink_dropped_ticks):
        findings.append(
            f"websocket_slo_chainlink_dropped_ticks_too_high:{max_chain_dropped:.6f}>max:{float(max_chainlink_dropped_ticks):.6f}"
        )
    if max_chain_queue > float(max_chainlink_queue_size):
        findings.append(
            f"websocket_slo_chainlink_queue_size_too_high:{max_chain_queue:.6f}>max:{float(max_chainlink_queue_size):.6f}"
        )
    if book_worker_unusable_rows > int(max_book_feed_worker_unusable_rows):
        findings.append(
            "websocket_slo_book_feed_worker_unusable_rows:"
            + f"{book_worker_unusable_rows}>max:{int(max_book_feed_worker_unusable_rows)}"
        )
    if chain_worker_unusable_rows > int(max_chainlink_worker_unusable_rows):
        findings.append(
            "websocket_slo_chainlink_worker_unusable_rows:"
            + f"{chain_worker_unusable_rows}>max:{int(max_chainlink_worker_unusable_rows)}"
        )
    if book_worker_restart_exhausted_rows > int(max_book_feed_worker_restart_exhausted_rows):
        findings.append(
            "websocket_slo_book_feed_worker_restart_exhausted_rows:"
            + f"{book_worker_restart_exhausted_rows}>max:{int(max_book_feed_worker_restart_exhausted_rows)}"
        )
    if chain_worker_restart_exhausted_rows > int(max_chainlink_worker_restart_exhausted_rows):
        findings.append(
            "websocket_slo_chainlink_worker_restart_exhausted_rows:"
            + f"{chain_worker_restart_exhausted_rows}>max:{int(max_chainlink_worker_restart_exhausted_rows)}"
        )
    if gateway_heartbeat_disabled_resting_rows > int(max_gateway_heartbeat_disabled_resting_rows):
        findings.append(
            "websocket_slo_gateway_heartbeat_disabled_with_resting_orders:"
            + f"{gateway_heartbeat_disabled_resting_rows}>max:{int(max_gateway_heartbeat_disabled_resting_rows)}"
        )
    if gateway_heartbeat_missing_or_invalid_rows > int(max_gateway_heartbeat_missing_or_invalid_rows):
        findings.append(
            "websocket_slo_gateway_heartbeat_missing_or_invalid_rows:"
            + f"{gateway_heartbeat_missing_or_invalid_rows}>max:{int(max_gateway_heartbeat_missing_or_invalid_rows)}"
        )
    if gateway_heartbeat_stale_rows > 0:
        findings.append(
            "websocket_slo_gateway_heartbeat_stale_rows:"
            + f"{gateway_heartbeat_stale_rows}:peak_age:{max_gateway_heartbeat_age:.6f}>max:{float(max_gateway_heartbeat_age_sec):.6f}"
        )
    if gateway_matching_engine_error_rows > int(max_gateway_matching_engine_error_rows):
        findings.append(
            "websocket_slo_gateway_matching_engine_error_rows:"
            + f"{gateway_matching_engine_error_rows}>max:{int(max_gateway_matching_engine_error_rows)}"
        )
    if max_gateway_matching_engine_restart_window_age > float(max_gateway_matching_engine_restart_window_age_sec):
        findings.append(
            "websocket_slo_gateway_matching_engine_restart_window_persistent:"
            + f"{max_gateway_matching_engine_restart_window_age:.6f}>max:{float(max_gateway_matching_engine_restart_window_age_sec):.6f}"
        )
    if gateway_matching_engine_restart_window_rows > 0:
        warnings.append(
            "websocket_slo_gateway_matching_engine_restart_window_rows:"
            + str(int(gateway_matching_engine_restart_window_rows))
        )

    return {
        "log_dir": str(log_dir.resolve()),
        "session_phase": normalized_phase,
        "run_contract_path": str(contract.get("_path", "")) if isinstance(contract, dict) else "",
        "run_id": resolved_run_id or "",
        "run_id_resolution": "explicit" if resolved_run_id else "missing",
        "artifact_identity": build_artifact_identity(log_dir=log_dir.resolve(), run_id=resolved_run_id),
        "context_hints": context_hints,
        "status_row_count": sample_count,
        "websocket_truth_required_row_count": truth_required_rows,
        "metrics": {
            "duration_sec": duration_sec,
            "book_feed_down_ratio": book_down_ratio,
            "chainlink_down_ratio": chain_down_ratio,
            "book_feed_reconnects_per_hour": book_reconnects_per_hour,
            "chainlink_reconnects_per_hour": chain_reconnects_per_hour,
            "book_feed_last_msg_age_max_sec": max_book_age,
            "book_feed_last_msg_age_p95_sec": book_age_p95,
            "book_feed_last_msg_age_spike_rows": book_age_spike_rows,
            "book_feed_last_msg_age_spike_ratio": book_age_spike_ratio,
            "chainlink_last_tick_age_max_sec": max_chain_age,
            "chainlink_last_tick_age_p95_sec": chain_age_p95,
            "chainlink_last_tick_age_spike_rows": chain_age_spike_rows,
            "chainlink_last_tick_age_spike_ratio": chain_age_spike_ratio,
            "chainlink_dropped_ticks_max": max_chain_dropped,
            "chainlink_queue_size_max": max_chain_queue,
            "book_feed_worker_unusable_rows": book_worker_unusable_rows,
            "chainlink_worker_unusable_rows": chain_worker_unusable_rows,
            "book_feed_worker_restart_exhausted_rows": book_worker_restart_exhausted_rows,
            "chainlink_worker_restart_exhausted_rows": chain_worker_restart_exhausted_rows,
            "gateway_heartbeat_age_max_sec": max_gateway_heartbeat_age,
            "gateway_heartbeat_failures_max": max_gateway_heartbeat_failures,
            "gateway_heartbeat_missing_or_invalid_rows": gateway_heartbeat_missing_or_invalid_rows,
            "gateway_heartbeat_disabled_resting_rows": gateway_heartbeat_disabled_resting_rows,
            "gateway_heartbeat_stale_rows": gateway_heartbeat_stale_rows,
            "gateway_matching_engine_error_rows": gateway_matching_engine_error_rows,
            "gateway_matching_engine_restart_window_rows": gateway_matching_engine_restart_window_rows,
            "gateway_matching_engine_restart_window_age_max_sec": max_gateway_matching_engine_restart_window_age,
            "book_feed_missing_rows": missing_book_count,
            "chainlink_missing_rows": missing_chain_count,
            "book_feed_reconnects_missing_or_invalid_rows": missing_book_reconnect_count,
            "chainlink_reconnects_missing_or_invalid_rows": missing_chain_reconnect_count,
            "book_feed_last_msg_age_missing_or_invalid_rows": missing_book_age_count,
            "chainlink_last_tick_age_missing_or_invalid_rows": missing_chain_age_count,
            "chainlink_queue_size_missing_or_invalid_rows": missing_chain_queue_count,
            "chainlink_dropped_ticks_missing_or_invalid_rows": missing_chain_dropped_count,
        },
        "thresholds": {
            "min_status_rows": int(min_status_rows),
            "max_book_feed_down_ratio": float(max_book_feed_down_ratio),
            "max_chainlink_down_ratio": float(max_chainlink_down_ratio),
            "max_book_feed_reconnects_per_hour": float(max_book_feed_reconnects_per_hour),
            "max_chainlink_reconnects_per_hour": float(max_chainlink_reconnects_per_hour),
            "max_book_feed_last_msg_age_sec": float(max_book_feed_last_msg_age_sec),
            "max_chainlink_last_tick_age_sec": float(max_chainlink_last_tick_age_sec),
            "max_book_feed_last_msg_age_spike_rows": int(max_book_feed_last_msg_age_spike_rows),
            "max_chainlink_last_tick_age_spike_rows": int(max_chainlink_last_tick_age_spike_rows),
            "max_book_feed_last_msg_age_spike_ratio": float(max_book_feed_last_msg_age_spike_ratio),
            "max_chainlink_last_tick_age_spike_ratio": float(max_chainlink_last_tick_age_spike_ratio),
            "max_book_feed_last_msg_age_p95_sec": float(max_book_feed_last_msg_age_p95_sec),
            "max_chainlink_last_tick_age_p95_sec": float(max_chainlink_last_tick_age_p95_sec),
            "max_chainlink_dropped_ticks": float(max_chainlink_dropped_ticks),
            "max_chainlink_queue_size": float(max_chainlink_queue_size),
            "max_book_feed_worker_unusable_rows": int(max_book_feed_worker_unusable_rows),
            "max_chainlink_worker_unusable_rows": int(max_chainlink_worker_unusable_rows),
            "max_book_feed_worker_restart_exhausted_rows": int(max_book_feed_worker_restart_exhausted_rows),
            "max_chainlink_worker_restart_exhausted_rows": int(max_chainlink_worker_restart_exhausted_rows),
            "max_gateway_heartbeat_age_sec": float(max_gateway_heartbeat_age_sec),
            "max_gateway_heartbeat_missing_or_invalid_rows": int(max_gateway_heartbeat_missing_or_invalid_rows),
            "max_gateway_heartbeat_disabled_resting_rows": int(max_gateway_heartbeat_disabled_resting_rows),
            "max_gateway_matching_engine_error_rows": int(max_gateway_matching_engine_error_rows),
            "max_gateway_matching_engine_restart_window_age_sec": float(
                max_gateway_matching_engine_restart_window_age_sec
            ),
            "max_lines_per_file": int(max(0, int(max_lines_per_file))),
        },
        "finding_count": len(findings),
        "findings": findings,
        "warning_count": len(warnings),
        "warnings": sorted(set(warnings)),
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro websocket reliability SLO gate")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Run_id filter")
    parser.add_argument(
        "--run-contract",
        default="",
        help="Optional run contract JSON path for deterministic replay",
    )
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument("--min-status-rows", type=int, default=20)
    parser.add_argument("--max-book-feed-down-ratio", type=float, default=0.2)
    parser.add_argument("--max-chainlink-down-ratio", type=float, default=0.2)
    parser.add_argument("--max-book-feed-reconnects-per-hour", type=float, default=40.0)
    parser.add_argument("--max-chainlink-reconnects-per-hour", type=float, default=40.0)
    parser.add_argument("--max-book-feed-last-msg-age-sec", type=float, default=12.0)
    parser.add_argument("--max-chainlink-last-tick-age-sec", type=float, default=30.0)
    parser.add_argument("--max-book-feed-last-msg-age-spike-rows", type=int, default=0)
    parser.add_argument("--max-chainlink-last-tick-age-spike-rows", type=int, default=0)
    parser.add_argument("--max-book-feed-last-msg-age-spike-ratio", type=float, default=0.0)
    parser.add_argument("--max-chainlink-last-tick-age-spike-ratio", type=float, default=0.0)
    parser.add_argument("--max-book-feed-last-msg-age-p95-sec", type=float, default=8.0)
    parser.add_argument("--max-chainlink-last-tick-age-p95-sec", type=float, default=12.0)
    parser.add_argument("--max-chainlink-dropped-ticks", type=float, default=0.0)
    parser.add_argument("--max-chainlink-queue-size", type=float, default=10000.0)
    parser.add_argument("--max-book-feed-worker-unusable-rows", type=int, default=0)
    parser.add_argument("--max-chainlink-worker-unusable-rows", type=int, default=0)
    parser.add_argument("--max-book-feed-worker-restart-exhausted-rows", type=int, default=0)
    parser.add_argument("--max-chainlink-worker-restart-exhausted-rows", type=int, default=0)
    parser.add_argument("--max-gateway-heartbeat-age-sec", type=float, default=12.0)
    parser.add_argument("--max-gateway-heartbeat-missing-or-invalid-rows", type=int, default=0)
    parser.add_argument("--max-gateway-heartbeat-disabled-resting-rows", type=int, default=0)
    parser.add_argument("--max-gateway-matching-engine-error-rows", type=int, default=0)
    parser.add_argument("--max-gateway-matching-engine-restart-window-age-sec", type=float, default=30.0)
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per status JSONL file; set 0 for full-file scans",
    )
    parser.add_argument("--budget", default="", help="Optional YAML budget file to source thresholds")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    cfg: Dict[str, Any] = {}
    raw_budget = str(args.budget).strip()
    if raw_budget:
        cfg = _load_budget(pathlib.Path(raw_budget).resolve())
    budget_cfg: Dict[str, Any]
    if isinstance(cfg.get("websocket"), dict):
        budget_cfg = dict(cfg.get("websocket", {}) or {})
    else:
        budget_cfg = cfg

    def _cfg_float(key: str, fallback: float) -> float:
        return _safe_float(budget_cfg.get(key), default=fallback)

    def _cfg_int(key: str, fallback: int) -> int:
        try:
            return int(budget_cfg.get(key))
        except (TypeError, ValueError):
            return fallback

    result = run_gate(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id).strip() or None,
        min_status_rows=max(1, _cfg_int("min_status_rows", int(args.min_status_rows))),
        max_book_feed_down_ratio=max(0.0, min(1.0, _cfg_float("max_book_feed_down_ratio", float(args.max_book_feed_down_ratio)))),
        max_chainlink_down_ratio=max(0.0, min(1.0, _cfg_float("max_chainlink_down_ratio", float(args.max_chainlink_down_ratio)))),
        max_book_feed_reconnects_per_hour=max(
            0.0,
            _cfg_float("max_book_feed_reconnects_per_hour", float(args.max_book_feed_reconnects_per_hour)),
        ),
        max_chainlink_reconnects_per_hour=max(
            0.0,
            _cfg_float("max_chainlink_reconnects_per_hour", float(args.max_chainlink_reconnects_per_hour)),
        ),
        max_book_feed_last_msg_age_sec=max(
            0.0,
            _cfg_float("max_book_feed_last_msg_age_sec", float(args.max_book_feed_last_msg_age_sec)),
        ),
        max_chainlink_last_tick_age_sec=max(
            0.0,
            _cfg_float("max_chainlink_last_tick_age_sec", float(args.max_chainlink_last_tick_age_sec)),
        ),
        max_book_feed_last_msg_age_spike_rows=max(
            0,
            _cfg_int("max_book_feed_last_msg_age_spike_rows", int(args.max_book_feed_last_msg_age_spike_rows)),
        ),
        max_chainlink_last_tick_age_spike_rows=max(
            0,
            _cfg_int("max_chainlink_last_tick_age_spike_rows", int(args.max_chainlink_last_tick_age_spike_rows)),
        ),
        max_book_feed_last_msg_age_spike_ratio=max(
            0.0,
            min(
                1.0,
                _cfg_float(
                    "max_book_feed_last_msg_age_spike_ratio",
                    float(args.max_book_feed_last_msg_age_spike_ratio),
                ),
            ),
        ),
        max_chainlink_last_tick_age_spike_ratio=max(
            0.0,
            min(
                1.0,
                _cfg_float(
                    "max_chainlink_last_tick_age_spike_ratio",
                    float(args.max_chainlink_last_tick_age_spike_ratio),
                ),
            ),
        ),
        max_book_feed_last_msg_age_p95_sec=max(
            0.0,
            _cfg_float("max_book_feed_last_msg_age_p95_sec", float(args.max_book_feed_last_msg_age_p95_sec)),
        ),
        max_chainlink_last_tick_age_p95_sec=max(
            0.0,
            _cfg_float("max_chainlink_last_tick_age_p95_sec", float(args.max_chainlink_last_tick_age_p95_sec)),
        ),
        max_chainlink_dropped_ticks=max(0.0, _cfg_float("max_chainlink_dropped_ticks", float(args.max_chainlink_dropped_ticks))),
        max_chainlink_queue_size=max(0.0, _cfg_float("max_chainlink_queue_size", float(args.max_chainlink_queue_size))),
        max_book_feed_worker_unusable_rows=max(
            0,
            _cfg_int("max_book_feed_worker_unusable_rows", int(args.max_book_feed_worker_unusable_rows)),
        ),
        max_chainlink_worker_unusable_rows=max(
            0,
            _cfg_int("max_chainlink_worker_unusable_rows", int(args.max_chainlink_worker_unusable_rows)),
        ),
        max_book_feed_worker_restart_exhausted_rows=max(
            0,
            _cfg_int(
                "max_book_feed_worker_restart_exhausted_rows",
                int(args.max_book_feed_worker_restart_exhausted_rows),
            ),
        ),
        max_chainlink_worker_restart_exhausted_rows=max(
            0,
            _cfg_int(
                "max_chainlink_worker_restart_exhausted_rows",
                int(args.max_chainlink_worker_restart_exhausted_rows),
            ),
        ),
        max_gateway_heartbeat_age_sec=max(
            0.0,
            _cfg_float("max_gateway_heartbeat_age_sec", float(args.max_gateway_heartbeat_age_sec)),
        ),
        max_gateway_heartbeat_missing_or_invalid_rows=max(
            0,
            _cfg_int(
                "max_gateway_heartbeat_missing_or_invalid_rows",
                int(args.max_gateway_heartbeat_missing_or_invalid_rows),
            ),
        ),
        max_gateway_heartbeat_disabled_resting_rows=max(
            0,
            _cfg_int(
                "max_gateway_heartbeat_disabled_resting_rows",
                int(args.max_gateway_heartbeat_disabled_resting_rows),
            ),
        ),
        max_gateway_matching_engine_error_rows=max(
            0,
            _cfg_int(
                "max_gateway_matching_engine_error_rows",
                int(args.max_gateway_matching_engine_error_rows),
            ),
        ),
        max_gateway_matching_engine_restart_window_age_sec=max(
            0.0,
            _cfg_float(
                "max_gateway_matching_engine_restart_window_age_sec",
                float(args.max_gateway_matching_engine_restart_window_age_sec),
            ),
        ),
        max_lines_per_file=max(0, _cfg_int("max_lines_per_file", int(args.max_lines_per_file))),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
