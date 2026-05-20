#!/usr/bin/env python3
"""Audit websocket/feed resilience settings in execution config."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple


from prodesk.config import load_execution_config
from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import DEFAULT_MAX_LINES_PER_FILE, load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.session_phase import enforce_validation_phase


ORDERING_POLICY_REQUIRED_KEYS = ("primary", "fallback", "tolerance_ms", "tie_breaker")
ORDERING_CLASS_REQUIRED_KEYS = ("ordered", "out_of_order", "duplicate", "revision", "missing_source_time")


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _reconnect_counter(payload: Dict[str, Any]) -> Optional[float]:
    steady = _safe_float(payload.get("reconnects_steady"))
    if steady is not None:
        return steady
    return _safe_float(payload.get("reconnects"))


def _validate_backoff(
    *,
    section: str,
    initial_raw: Any,
    maximum_raw: Any,
    findings: List[str],
    warnings: List[str],
) -> None:
    initial = _safe_float(initial_raw)
    maximum = _safe_float(maximum_raw)
    if initial is None or initial <= 0:
        findings.append(f"{section}:reconnect_backoff_initial_invalid:{initial_raw!r}")
    if maximum is None or maximum <= 0:
        findings.append(f"{section}:reconnect_backoff_max_invalid:{maximum_raw!r}")
    if initial is not None and maximum is not None and initial > maximum:
        findings.append(f"{section}:reconnect_backoff_initial_gt_max:{initial}>{maximum}")
    if initial is not None and initial < 0.25:
        warnings.append(f"{section}:reconnect_backoff_initial_low:{initial}")
    if maximum is not None and maximum > 120.0:
        warnings.append(f"{section}:reconnect_backoff_max_high:{maximum}")


def _validate_endpoint(
    *,
    section: str,
    enabled: bool,
    url: Any,
    startup_ack_raw: Any,
    worker_name_raw: Any,
    findings: List[str],
    warnings: List[str],
) -> Optional[float]:
    if not enabled:
        return None

    text_url = str(url or "").strip()
    if not text_url:
        findings.append(f"{section}:url_missing")
    elif not text_url.lower().startswith("wss://"):
        findings.append(f"{section}:url_not_wss:{text_url}")

    worker_name = str(worker_name_raw or "").strip()
    if not worker_name:
        findings.append(f"{section}:worker_name_missing")

    startup_ack = _safe_float(startup_ack_raw)
    if startup_ack is None or startup_ack <= 0:
        findings.append(f"{section}:startup_ack_timeout_invalid:{startup_ack_raw!r}")
    elif startup_ack > 60.0:
        warnings.append(f"{section}:startup_ack_timeout_high:{startup_ack}")
    return startup_ack


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


def _load_rows_from_slice(path: pathlib.Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
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
                    rows.append(row)
    except OSError:
        return []
    return rows


def _load_status_rows(
    *,
    log_dir: pathlib.Path,
    run_id: Optional[str],
    max_lines_per_file: int,
    status_paths: Optional[List[pathlib.Path]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    source_paths = status_paths if status_paths is not None else sorted(log_dir.glob("status_*.jsonl"))
    for row in load_jsonl(source_paths, max_lines_per_file=max(0, int(max_lines_per_file))):
        if run_id and str(row.get("run_id") or "").strip() != run_id:
            continue
        rows.append(row)
    return rows


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


def _safe_nonnegative_int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out < 0:
        return None
    return out


def run_audit(
    *,
    config_path: pathlib.Path,
    log_dir: Optional[pathlib.Path] = None,
    run_id: Optional[str] = None,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="websocket_hardening_audit", session_phase=session_phase)
    cfg = load_execution_config(config_path.resolve())
    findings: List[str] = []
    warnings: List[str] = []

    md = cfg.get("market_data", {}) if isinstance(cfg.get("market_data"), dict) else {}
    ws = md.get("ws", {}) if isinstance(md.get("ws"), dict) else {}
    ws_enabled = bool(ws.get("enabled", False))
    ws_startup_ack = _validate_endpoint(
        section="market_data.ws",
        enabled=ws_enabled,
        url=ws.get("url"),
        startup_ack_raw=ws.get("startup_ack_timeout_sec"),
        worker_name_raw=ws.get("worker_name"),
        findings=findings,
        warnings=warnings,
    )
    _validate_backoff(
        section="market_data.ws",
        initial_raw=ws.get("reconnect_backoff_initial_sec"),
        maximum_raw=ws.get("reconnect_backoff_max_sec"),
        findings=findings,
        warnings=warnings,
    )
    stale_after = _safe_float(ws.get("stale_after_sec"))
    if ws_enabled:
        if stale_after is None or stale_after <= 0:
            findings.append(f"market_data.ws:stale_after_invalid:{ws.get('stale_after_sec')!r}")
        if stale_after is not None and ws_startup_ack is not None and stale_after >= ws_startup_ack:
            warnings.append(f"market_data.ws:stale_after_ge_startup_ack:{stale_after}>={ws_startup_ack}")

    chain = cfg.get("chainlink", {}) if isinstance(cfg.get("chainlink"), dict) else {}
    chain_enabled = bool(chain.get("enabled", False))
    chain_startup_ack = _validate_endpoint(
        section="chainlink",
        enabled=chain_enabled,
        url=chain.get("ws_url"),
        startup_ack_raw=chain.get("startup_ack_timeout_sec"),
        worker_name_raw=chain.get("worker_name"),
        findings=findings,
        warnings=warnings,
    )
    _validate_backoff(
        section="chainlink",
        initial_raw=chain.get("reconnect_backoff_initial_sec"),
        maximum_raw=chain.get("reconnect_backoff_max_sec"),
        findings=findings,
        warnings=warnings,
    )
    max_queue = chain.get("max_queue_size")
    if chain_enabled:
        max_queue_i = None
        try:
            max_queue_i = int(max_queue)
        except (TypeError, ValueError):
            pass
        if max_queue_i is None or max_queue_i < 1000:
            findings.append(f"chainlink:max_queue_size_too_low_or_invalid:{max_queue!r}")
        elif max_queue_i < 5000:
            warnings.append(f"chainlink:max_queue_size_low:{max_queue_i}")

    evidence: Dict[str, Any] = {}
    resolved_log_dir = log_dir.resolve() if log_dir is not None else None
    selected_run_id = str(run_id or "").strip() or None
    resolved_contract_path = ""
    run_id_resolution = "missing"
    contract: Optional[Dict[str, Any]] = None
    if selected_run_id:
        run_id_resolution = "explicit"

    if run_contract_path is not None:
        resolve_log_dir = resolved_log_dir if resolved_log_dir is not None else pathlib.Path(".").resolve()
        try:
            contract = resolve_run_contract(
                log_dir=resolve_log_dir,
                run_id=selected_run_id,
                run_contract_path_override=run_contract_path,
                allow_open=(normalized_phase == "validate_active"),
            )
        except ValueError as exc:
            findings.append(str(exc))
            contract = None
        if isinstance(contract, dict):
            resolved_contract_path = str(contract.get("_path") or "")
            if not selected_run_id:
                selected_run_id = str(contract.get("run_id") or "").strip() or None
                if selected_run_id:
                    run_id_resolution = "contract"
            if resolved_log_dir is None:
                log_root = str(contract.get("log_root") or "").strip()
                if log_root:
                    resolved_log_dir = pathlib.Path(log_root).expanduser().resolve()
    elif resolved_log_dir is not None and not selected_run_id:
        findings.append("websocket_hardening_run_id_required_when_log_dir_provided")

    if resolved_log_dir is not None and not findings:
        rows: List[Dict[str, Any]] = []
        status_slice = run_contract_slice_path(contract, stream="status") if isinstance(contract, dict) else None
        if status_slice is not None:
            rows = apply_contract_bounds(_load_rows_from_slice(status_slice), contract)
        else:
            status_paths: Optional[List[pathlib.Path]] = None
            if isinstance(contract, dict):
                status_text = str(contract.get("status_path") or "").strip()
                if status_text:
                    status_path = pathlib.Path(status_text).expanduser().resolve()
                    if status_path.exists():
                        status_paths = [status_path]
            rows = _load_status_rows(
                log_dir=resolved_log_dir,
                run_id=selected_run_id,
                max_lines_per_file=max(0, int(max_lines_per_file)),
                status_paths=status_paths,
            )
            rows = apply_contract_bounds(rows, contract)
        sample_count = len(rows)
        evidence["status_rows"] = sample_count
        if sample_count > 0:
            ts_vals = [_parse_ts(r.get("ts_utc")) for r in rows]
            ts_vals = [x for x in ts_vals if x is not None]
            duration_sec = 0.0
            if len(ts_vals) >= 2:
                duration_sec = max(0.0, (max(ts_vals) - min(ts_vals)).total_seconds())
            duration_hours = max(duration_sec / 3600.0, 1.0 / 3600.0)

            book_connected_false = 0
            chain_connected_false = 0
            max_book_reconnects = 0.0
            max_chain_reconnects = 0.0
            max_book_age = 0.0
            max_chain_age = 0.0
            max_chain_queue_size = 0.0
            max_chain_dropped_ticks = 0.0
            book_thread_dead_rows = 0
            chainlink_thread_dead_rows = 0
            book_worker_unusable_rows = 0
            chainlink_worker_unusable_rows = 0
            book_worker_restart_exhausted_rows = 0
            chainlink_worker_restart_exhausted_rows = 0
            book_worker_fatal_rows = 0
            chainlink_worker_fatal_rows = 0
            ordering_policy_specs: Dict[str, int] = {}
            ordering_policy_missing_rows = 0
            ordering_policy_invalid_rows = 0
            ordering_class_missing_rows = 0
            ordering_class_invalid_rows = 0
            ordering_class_totals: Dict[str, int] = {key: 0 for key in ORDERING_CLASS_REQUIRED_KEYS}
            truth_required_rows = 0
            for row in rows:
                book = _as_dict(row.get("book_feed"))
                chain_status = _as_dict(row.get("chainlink"))
                book_connected = _as_bool(book.get("connected"), default=True)
                chain_connected = _as_bool(chain_status.get("connected"), default=True)
                truth_required = _websocket_truth_required(row)
                if truth_required:
                    truth_required_rows += 1
                    if not book_connected:
                        book_connected_false += 1
                    if not chain_connected:
                        chain_connected_false += 1
                reconnect_book = _reconnect_counter(book)
                reconnect_chain = _reconnect_counter(chain_status)
                age_book = _safe_float(book.get("last_msg_age_sec"))
                age_chain = _safe_float(chain_status.get("last_tick_age_sec"))
                qsize_chain = _safe_float(chain_status.get("queue_size"))
                dropped_chain = _safe_float(chain_status.get("dropped_ticks"))
                book_thread_alive = _as_bool(book.get("thread_alive"), default=True)
                chainlink_thread_alive = _as_bool(chain_status.get("thread_alive"), default=True)
                if reconnect_book is not None:
                    max_book_reconnects = max(max_book_reconnects, reconnect_book)
                if reconnect_chain is not None:
                    max_chain_reconnects = max(max_chain_reconnects, reconnect_chain)
                if truth_required and age_book is not None:
                    max_book_age = max(max_book_age, age_book)
                if truth_required and age_chain is not None:
                    max_chain_age = max(max_chain_age, age_chain)
                if qsize_chain is not None:
                    max_chain_queue_size = max(max_chain_queue_size, qsize_chain)
                if dropped_chain is not None:
                    max_chain_dropped_ticks = max(max_chain_dropped_ticks, dropped_chain)
                if bool(book.get("enabled", False)) and (not bool(book_thread_alive)):
                    book_thread_dead_rows += 1
                if bool(chain_status.get("enabled", False)) and (not bool(chainlink_thread_alive)):
                    chainlink_thread_dead_rows += 1
                if (
                    truth_required
                    and bool(book.get("enabled", False))
                    and not _as_bool(book.get("worker_usable"), default=True)
                ):
                    book_worker_unusable_rows += 1
                if (
                    truth_required
                    and bool(chain_status.get("enabled", False))
                    and not _as_bool(chain_status.get("worker_usable"), default=True)
                ):
                    chainlink_worker_unusable_rows += 1
                if truth_required and _as_bool(book.get("worker_restart_exhausted"), default=False):
                    book_worker_restart_exhausted_rows += 1
                if truth_required and _as_bool(chain_status.get("worker_restart_exhausted"), default=False):
                    chainlink_worker_restart_exhausted_rows += 1
                if str(book.get("worker_fatal_reason") or "").strip():
                    book_worker_fatal_rows += 1
                if str(chain_status.get("worker_fatal_reason") or "").strip():
                    chainlink_worker_fatal_rows += 1
                ordering_policy_raw = chain_status.get("ordering_policy")
                if ordering_policy_raw is None:
                    ordering_policy_missing_rows += 1
                elif isinstance(ordering_policy_raw, dict):
                    missing_policy_keys = [key for key in ORDERING_POLICY_REQUIRED_KEYS if key not in ordering_policy_raw]
                    if missing_policy_keys:
                        ordering_policy_invalid_rows += 1
                    else:
                        policy_fingerprint = json.dumps(
                            {key: ordering_policy_raw.get(key) for key in ORDERING_POLICY_REQUIRED_KEYS},
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                        ordering_policy_specs[policy_fingerprint] = int(ordering_policy_specs.get(policy_fingerprint, 0)) + 1
                else:
                    ordering_policy_invalid_rows += 1

                ordering_counts_raw = chain_status.get("ordering_classification_counts")
                if not isinstance(ordering_counts_raw, dict):
                    ordering_class_missing_rows += 1
                else:
                    missing_class_keys = [key for key in ORDERING_CLASS_REQUIRED_KEYS if key not in ordering_counts_raw]
                    if missing_class_keys:
                        ordering_class_invalid_rows += 1
                    else:
                        for key in ORDERING_CLASS_REQUIRED_KEYS:
                            parsed = _safe_nonnegative_int(ordering_counts_raw.get(key))
                            if parsed is None:
                                ordering_class_invalid_rows += 1
                                break
                            ordering_class_totals[key] = max(ordering_class_totals[key], int(parsed))

            evidence["websocket_truth_required_rows"] = int(truth_required_rows)
            book_down_ratio = (
                float(book_connected_false) / float(truth_required_rows)
                if truth_required_rows > 0
                else 0.0
            )
            chain_down_ratio = (
                float(chain_connected_false) / float(truth_required_rows)
                if truth_required_rows > 0
                else 0.0
            )
            book_reconnects_per_hour = max_book_reconnects / duration_hours
            chain_reconnects_per_hour = max_chain_reconnects / duration_hours

            evidence.update(
                {
                    "duration_sec": duration_sec,
                    "book_feed_down_ratio": book_down_ratio,
                    "chainlink_down_ratio": chain_down_ratio,
                    "book_feed_reconnects_max": max_book_reconnects,
                    "chainlink_reconnects_max": max_chain_reconnects,
                    "book_feed_reconnects_per_hour": book_reconnects_per_hour,
                    "chainlink_reconnects_per_hour": chain_reconnects_per_hour,
                    "book_feed_last_msg_age_max_sec": max_book_age,
                    "chainlink_last_tick_age_max_sec": max_chain_age,
                    "chainlink_queue_size_max": max_chain_queue_size,
                    "chainlink_dropped_ticks_max": max_chain_dropped_ticks,
                    "book_feed_thread_dead_rows": int(book_thread_dead_rows),
                    "chainlink_thread_dead_rows": int(chainlink_thread_dead_rows),
                    "book_feed_worker_unusable_rows": int(book_worker_unusable_rows),
                    "chainlink_worker_unusable_rows": int(chainlink_worker_unusable_rows),
                    "book_feed_worker_restart_exhausted_rows": int(book_worker_restart_exhausted_rows),
                    "chainlink_worker_restart_exhausted_rows": int(chainlink_worker_restart_exhausted_rows),
                    "book_feed_worker_fatal_rows": int(book_worker_fatal_rows),
                    "chainlink_worker_fatal_rows": int(chainlink_worker_fatal_rows),
                    "ordering_policy_required_keys": list(ORDERING_POLICY_REQUIRED_KEYS),
                    "ordering_policy_observed_specs": [json.loads(text) for text in sorted(ordering_policy_specs.keys())],
                    "ordering_policy_missing_rows": int(ordering_policy_missing_rows),
                    "ordering_policy_invalid_rows": int(ordering_policy_invalid_rows),
                    "ordering_class_required_keys": list(ORDERING_CLASS_REQUIRED_KEYS),
                    "ordering_classification_missing_rows": int(ordering_class_missing_rows),
                    "ordering_classification_invalid_rows": int(ordering_class_invalid_rows),
                    "ordering_classification_totals": dict(ordering_class_totals),
                }
            )

            max_book_down_ratio = 0.20
            max_chain_down_ratio = 0.20
            max_book_reconnects_per_hour = 40.0
            max_chain_reconnects_per_hour = 40.0
            max_chain_dropped_ticks_allowed = 0.0
            max_chain_queue_size_allowed = float(max(1000, int(chain.get("max_queue_size", 10000))))
            ws_stale_after_cfg = _safe_float(ws.get("stale_after_sec")) or 3.0
            ws_startup_ack_cfg = _safe_float(ws.get("startup_ack_timeout_sec")) or 10.0
            chain_startup_ack_cfg = _safe_float(chain.get("startup_ack_timeout_sec")) or 10.0
            max_book_age_sec = max(2.0 * ws_stale_after_cfg, ws_startup_ack_cfg)
            max_chain_age_sec = max(2.0 * chain_startup_ack_cfg, 15.0)

            if book_down_ratio > max_book_down_ratio:
                findings.append(
                    f"websocket_evidence_book_feed_down_ratio_too_high:{book_down_ratio:.6f}>max:{max_book_down_ratio:.6f}"
                )
            if chain_down_ratio > max_chain_down_ratio:
                findings.append(
                    f"websocket_evidence_chainlink_down_ratio_too_high:{chain_down_ratio:.6f}>max:{max_chain_down_ratio:.6f}"
                )
            if book_reconnects_per_hour > max_book_reconnects_per_hour:
                findings.append(
                    "websocket_evidence_book_feed_reconnects_per_hour_too_high:"
                    + f"{book_reconnects_per_hour:.6f}>max:{max_book_reconnects_per_hour:.6f}"
                )
            if chain_reconnects_per_hour > max_chain_reconnects_per_hour:
                findings.append(
                    "websocket_evidence_chainlink_reconnects_per_hour_too_high:"
                    + f"{chain_reconnects_per_hour:.6f}>max:{max_chain_reconnects_per_hour:.6f}"
                )
            if max_book_age > max_book_age_sec:
                findings.append(
                    f"websocket_evidence_book_feed_last_msg_age_too_high:{max_book_age:.6f}>max:{max_book_age_sec:.6f}"
                )
            if max_chain_age > max_chain_age_sec:
                findings.append(
                    f"websocket_evidence_chainlink_last_tick_age_too_high:{max_chain_age:.6f}>max:{max_chain_age_sec:.6f}"
                )
            if max_chain_dropped_ticks > max_chain_dropped_ticks_allowed:
                findings.append(
                    "websocket_evidence_chainlink_dropped_ticks_too_high:"
                    + f"{max_chain_dropped_ticks:.6f}>max:{max_chain_dropped_ticks_allowed:.6f}"
                )
            if max_chain_queue_size > max_chain_queue_size_allowed:
                findings.append(
                    "websocket_evidence_chainlink_queue_size_too_high:"
                    + f"{max_chain_queue_size:.6f}>max:{max_chain_queue_size_allowed:.6f}"
                )
            if book_thread_dead_rows > 0:
                findings.append(f"websocket_evidence_book_feed_thread_dead_rows:{int(book_thread_dead_rows)}")
            if chainlink_thread_dead_rows > 0:
                findings.append(f"websocket_evidence_chainlink_thread_dead_rows:{int(chainlink_thread_dead_rows)}")
            if book_worker_unusable_rows > 0:
                findings.append(f"websocket_evidence_book_feed_worker_unusable_rows:{int(book_worker_unusable_rows)}")
            if chainlink_worker_unusable_rows > 0:
                findings.append(f"websocket_evidence_chainlink_worker_unusable_rows:{int(chainlink_worker_unusable_rows)}")
            if book_worker_restart_exhausted_rows > 0:
                findings.append(
                    f"websocket_evidence_book_feed_worker_restart_exhausted_rows:{int(book_worker_restart_exhausted_rows)}"
                )
            if chainlink_worker_restart_exhausted_rows > 0:
                findings.append(
                    f"websocket_evidence_chainlink_worker_restart_exhausted_rows:{int(chainlink_worker_restart_exhausted_rows)}"
                )
            if book_worker_fatal_rows > 0:
                findings.append(f"websocket_evidence_book_feed_worker_fatal_rows:{int(book_worker_fatal_rows)}")
            if chainlink_worker_fatal_rows > 0:
                findings.append(f"websocket_evidence_chainlink_worker_fatal_rows:{int(chainlink_worker_fatal_rows)}")
            if ordering_policy_missing_rows > 0:
                findings.append(
                    f"websocket_ordering_policy_missing_rows:{int(ordering_policy_missing_rows)}"
                )
            if ordering_policy_invalid_rows > 0:
                findings.append(
                    f"websocket_ordering_policy_invalid_rows:{int(ordering_policy_invalid_rows)}"
                )
            if len(ordering_policy_specs) == 0:
                findings.append("websocket_ordering_policy_unobserved")
            if len(ordering_policy_specs) > 1:
                findings.append(
                    f"websocket_ordering_policy_non_deterministic_specs:{int(len(ordering_policy_specs))}"
                )
            if ordering_class_missing_rows > 0:
                findings.append(
                    f"websocket_ordering_classification_missing_rows:{int(ordering_class_missing_rows)}"
                )
            if ordering_class_invalid_rows > 0:
                findings.append(
                    f"websocket_ordering_classification_invalid_rows:{int(ordering_class_invalid_rows)}"
                )
            if int(ordering_class_totals.get("ordered", 0)) <= 0:
                findings.append("websocket_ordering_classification_ordered_missing")

    return {
        "config_path": str(config_path.resolve()),
        "log_dir": str(resolved_log_dir) if resolved_log_dir is not None else "",
        "run_id": selected_run_id or "",
        "run_id_resolution": run_id_resolution,
        "session_phase": normalized_phase,
        "run_contract_path": resolved_contract_path,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "error_codes": summarize_error_codes(findings),
        "evidence": evidence,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro websocket hardening audit")
    parser.add_argument(
        "--config",
        default="configs/profiles/paper_universal.yaml",
        help="Execution config path",
    )
    parser.add_argument("--log-dir", default="", help="Optional log dir for websocket reliability evidence")
    parser.add_argument("--run-id", default="", help="Optional run_id filter when --log-dir is provided")
    parser.add_argument("--run-contract", default="", help="Optional run contract JSON path for deterministic replay")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per status JSONL file; set 0 for full-file scans",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    raw_log_dir = str(args.log_dir).strip()
    raw_run_contract = str(args.run_contract).strip()
    result = run_audit(
        config_path=pathlib.Path(args.config),
        log_dir=(pathlib.Path(raw_log_dir) if raw_log_dir else None),
        run_id=str(args.run_id).strip() or None,
        run_contract_path=(pathlib.Path(raw_run_contract) if raw_run_contract else None),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
