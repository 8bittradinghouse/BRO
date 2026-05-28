#!/usr/bin/env python3
"""Audit clock/time discipline controls from config + status stream."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


from prodesk.config import load_execution_config
from prodesk.edge_truth_contract import is_taker_decision_event_type
from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import DEFAULT_MAX_LINES_PER_FILE, load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.session_phase import enforce_validation_phase

TIME_POLICY_REQUIRED_KEYS = ("source_of_truth", "fallback_logic", "skew_tolerance_ms", "monotonicity_rule")
TIMESTAMP_DOMAIN_FIELDS = ("ts_event_utc", "ts_receive_utc", "ts_source_utc", "ts_decision_utc")
HOST_SYNC_SAMPLE_ARTIFACT = "host_time_sync_active_samples.jsonl"
HOST_SYNC_REQUIRED_BOOL_FIELDS = ("system_clock_synchronized", "ntp_service_active")
HOST_SYNC_REQUIRED_NUMERIC_FIELDS = ("stratum", "offset_ms", "jitter_ms", "root_distance_ms")
TIMING_WATCH_WARN_RATIO = 0.75


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _status_rows(
    *,
    status_paths: List[pathlib.Path],
    run_id: str = "",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    files = list(status_paths)
    rows = load_jsonl(files, max_lines_per_file=max(0, int(max_lines_per_file)))
    scoped_rows: List[Dict[str, Any]] = []
    run_filter = str(run_id or "").strip()
    for row in rows:
        if run_filter and str(row.get("run_id") or "").strip() != run_filter:
            continue
        scoped_rows.append(row)
    return apply_contract_bounds(scoped_rows, run_contract)


def _latest_status_ts(
    *,
    status_paths: List[pathlib.Path],
    run_id: str = "",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[datetime], int, int]:
    latest_ts: Optional[datetime] = None
    checked = 0
    non_monotonic = 0
    bounded_rows = _status_rows(
        status_paths=status_paths,
        run_id=run_id,
        max_lines_per_file=max_lines_per_file,
        run_contract=run_contract,
    )
    prev: Optional[datetime] = None
    for row in bounded_rows:
        ts = _parse_ts(row.get("ts_utc"))
        if ts is None:
            continue
        checked += 1
        if prev is not None and ts < prev:
            non_monotonic += 1
        prev = ts
        latest_ts = ts if latest_ts is None else max(latest_ts, ts)
    return latest_ts, checked, non_monotonic


def _validate_time_policy(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    missing_rows = 0
    invalid_rows = 0
    specs: Dict[str, int] = {}
    selected_policy: Dict[str, Any] = {}
    for row in rows:
        payload = row.get("time_policy")
        if payload is None:
            missing_rows += 1
            continue
        if not isinstance(payload, dict):
            invalid_rows += 1
            continue
        missing_keys = [key for key in TIME_POLICY_REQUIRED_KEYS if key not in payload]
        if missing_keys:
            invalid_rows += 1
            continue
        try:
            skew_tolerance_ms = float(payload.get("skew_tolerance_ms"))
        except (TypeError, ValueError):
            invalid_rows += 1
            continue
        if skew_tolerance_ms < 0:
            invalid_rows += 1
            continue
        normalized = {
            "source_of_truth": str(payload.get("source_of_truth") or "").strip(),
            "fallback_logic": str(payload.get("fallback_logic") or "").strip(),
            "skew_tolerance_ms": skew_tolerance_ms,
            "monotonicity_rule": str(payload.get("monotonicity_rule") or "").strip(),
        }
        if not normalized["source_of_truth"] or not normalized["fallback_logic"] or not normalized["monotonicity_rule"]:
            invalid_rows += 1
            continue
        fingerprint = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        specs[fingerprint] = int(specs.get(fingerprint, 0)) + 1
        selected_policy = normalized
    if missing_rows > 0:
        findings.append(f"time_policy_missing_rows:{missing_rows}")
    if invalid_rows > 0:
        findings.append(f"time_policy_invalid_rows:{invalid_rows}")
    if len(specs) == 0:
        findings.append("time_policy_unobserved")
    if len(specs) > 1:
        findings.append(f"time_policy_non_deterministic_specs:{len(specs)}")
    evidence = {
        "required_keys": list(TIME_POLICY_REQUIRED_KEYS),
        "observed_specs": [json.loads(text) for text in sorted(specs.keys())],
        "missing_rows": int(missing_rows),
        "invalid_rows": int(invalid_rows),
        "selected_policy": selected_policy,
    }
    return evidence, findings


def _event_domain_audit(
    *,
    event_rows: List[Dict[str, Any]],
    skew_tolerance_ms: float,
) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    required_presence_missing = 0
    invalid_event_ts_rows = 0
    invalid_receive_ts_rows = 0
    invalid_source_ts_rows = 0
    invalid_decision_ts_rows = 0
    ts_event_mismatch_rows = 0
    cross_domain_skew_exceeded_rows = 0
    cross_domain_skew_checked_rows = 0
    cross_domain_skew_exempt_rows = 0
    decision_after_event_rows = 0
    timing_capable_cross_domain_rows = 0

    for row in event_rows:
        missing_any = False
        for key in TIMESTAMP_DOMAIN_FIELDS:
            if key not in row:
                missing_any = True
        if missing_any:
            required_presence_missing += 1
            continue

        ts_utc = _parse_ts(row.get("ts_utc"))
        ts_event = _parse_ts(row.get("ts_event_utc"))
        if ts_event is None:
            invalid_event_ts_rows += 1
            continue
        if ts_utc is not None and ts_event != ts_utc:
            ts_event_mismatch_rows += 1

        ts_receive_raw = row.get("ts_receive_utc")
        ts_source_raw = row.get("ts_source_utc")
        ts_decision_raw = row.get("ts_decision_utc")

        ts_receive = _parse_ts(ts_receive_raw) if ts_receive_raw is not None else None
        ts_source = _parse_ts(ts_source_raw) if ts_source_raw is not None else None
        ts_decision = _parse_ts(ts_decision_raw) if ts_decision_raw is not None else None

        if ts_receive_raw not in (None, "") and ts_receive is None:
            invalid_receive_ts_rows += 1
        if ts_source_raw not in (None, "") and ts_source is None:
            invalid_source_ts_rows += 1
        if ts_decision is None:
            invalid_decision_ts_rows += 1
        elif ts_decision > ts_event:
            decision_after_event_rows += 1

        if ts_source is not None and ts_receive is not None:
            event_type = str(row.get("event_type") or "").strip().lower()
            msg_type = str(row.get("msg_type") or "").strip().lower()
            # Chainlink source timestamps describe oracle publication time, not transport-wire
            # timing, so source->receive skew is freshness context rather than latency truth.
            if event_type == "chainlink_tick":
                cross_domain_skew_exempt_rows += 1
                continue
            timing_capable_cross_domain_rows += 1
            cross_domain_skew_checked_rows += 1
            skew_ms = abs((ts_receive - ts_source).total_seconds() * 1000.0)
            if skew_ms > float(skew_tolerance_ms):
                cross_domain_skew_exceeded_rows += 1

    if required_presence_missing > 0:
        findings.append(f"event_timestamp_domain_fields_missing_rows:{required_presence_missing}")
    if invalid_event_ts_rows > 0:
        findings.append(f"event_ts_event_invalid_rows:{invalid_event_ts_rows}")
    if invalid_receive_ts_rows > 0:
        findings.append(f"event_ts_receive_invalid_rows:{invalid_receive_ts_rows}")
    if invalid_source_ts_rows > 0:
        findings.append(f"event_ts_source_invalid_rows:{invalid_source_ts_rows}")
    if invalid_decision_ts_rows > 0:
        findings.append(f"event_ts_decision_invalid_rows:{invalid_decision_ts_rows}")
    if ts_event_mismatch_rows > 0:
        findings.append(f"event_ts_event_mismatch_rows:{ts_event_mismatch_rows}")
    if decision_after_event_rows > 0:
        findings.append(f"event_ts_decision_after_event_rows:{decision_after_event_rows}")
    if timing_capable_cross_domain_rows > 0 and cross_domain_skew_checked_rows <= 0:
        findings.append(f"event_ts_cross_domain_skew_unchecked_rows:{timing_capable_cross_domain_rows}")
    if cross_domain_skew_exceeded_rows > 0:
        findings.append(
            f"event_ts_cross_domain_skew_exceeded_rows:{cross_domain_skew_exceeded_rows}>tolerance_ms:{float(skew_tolerance_ms):.3f}"
        )

    evidence = {
        "required_domain_fields": list(TIMESTAMP_DOMAIN_FIELDS),
        "event_rows_considered": int(len(event_rows)),
        "required_presence_missing_rows": int(required_presence_missing),
        "invalid_event_ts_rows": int(invalid_event_ts_rows),
        "invalid_receive_ts_rows": int(invalid_receive_ts_rows),
        "invalid_source_ts_rows": int(invalid_source_ts_rows),
        "invalid_decision_ts_rows": int(invalid_decision_ts_rows),
        "ts_event_mismatch_rows": int(ts_event_mismatch_rows),
        "decision_after_event_rows": int(decision_after_event_rows),
        "cross_domain_skew_exemption_policy": "chainlink_tick[source_ts_is_oracle_publication_time]",
        "timing_capable_cross_domain_rows": int(timing_capable_cross_domain_rows),
        "cross_domain_skew_checked_rows": int(cross_domain_skew_checked_rows),
        "cross_domain_skew_exempt_rows": int(cross_domain_skew_exempt_rows),
        "cross_domain_skew_exceeded_rows": int(cross_domain_skew_exceeded_rows),
        "cross_domain_skew_tolerance_ms": float(skew_tolerance_ms),
    }
    return evidence, findings


def _contract_session_report_root(contract: Optional[Dict[str, Any]], log_dir: pathlib.Path, run_id: str) -> pathlib.Path:
    if isinstance(contract, dict):
        session_id = str(contract.get("session_id") or "").strip()
        if session_id:
            return (log_dir / "sessions" / session_id / "reports").resolve()
    return (log_dir / "reports" / str(run_id or "").strip()).resolve()


def _host_sync_thresholds(preflight_cfg: Dict[str, Any]) -> Dict[str, float]:
    return {
        "max_clock_stratum": float(preflight_cfg.get("max_clock_stratum", 3) or 3),
        "max_clock_offset_ms": float(preflight_cfg.get("max_clock_offset_ms", 10.0) or 10.0),
        "max_clock_jitter_ms": float(preflight_cfg.get("max_clock_jitter_ms", 10.0) or 10.0),
        "max_clock_root_distance_ms": float(preflight_cfg.get("max_clock_root_distance_ms", 100.0) or 100.0),
    }


def _load_jsonl_objects(path: pathlib.Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _read_json_object(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = int(round((len(ordered) - 1) * ratio))
    idx = max(0, min(len(ordered) - 1, idx))
    return float(ordered[idx])


def _latency_summary_ms(values: List[float]) -> Dict[str, float]:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return {
            "sample_count": 0.0,
            "median_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "sample_count": float(len(cleaned)),
        "median_ms": _percentile(cleaned, 0.50),
        "p90_ms": _percentile(cleaned, 0.90),
        "p95_ms": _percentile(cleaned, 0.95),
        "max_ms": float(max(cleaned)),
    }


def _evaluate_host_sync_payload(
    *,
    payload: Dict[str, Any],
    thresholds: Dict[str, float],
    label: str,
) -> List[str]:
    findings: List[str] = []
    clock_state = str(payload.get("clock_state") or "").strip().lower()
    if clock_state != "synced":
        findings.append(f"host_time_sync_{label}_not_synced:{clock_state or 'unknown'}")
    for key in HOST_SYNC_REQUIRED_BOOL_FIELDS:
        if payload.get(key) is not True:
            findings.append(f"host_time_sync_{label}_{key}_not_true")
    stratum = payload.get("stratum")
    if not isinstance(stratum, (int, float)):
        findings.append(f"host_time_sync_{label}_stratum_missing")
    elif float(stratum) > float(thresholds["max_clock_stratum"]):
        findings.append(
            f"host_time_sync_{label}_stratum_exceeded:{float(stratum):.3f}>max:{float(thresholds['max_clock_stratum']):.3f}"
        )
    for key in ("offset_ms", "jitter_ms", "root_distance_ms"):
        value = payload.get(key)
        if not isinstance(value, (int, float)):
            findings.append(f"host_time_sync_{label}_{key}_missing")
            continue
        limit_key = f"max_clock_{key}"
        limit = float(thresholds.get(limit_key, 0.0) or 0.0)
        observed = abs(float(value)) if key == "offset_ms" else float(value)
        if observed > limit:
            findings.append(
                f"host_time_sync_{label}_{key}_exceeded:{observed:.3f}>max:{float(limit):.3f}"
            )
    return findings


def _host_time_sync_audit(
    *,
    contract: Optional[Dict[str, Any]],
    log_dir: pathlib.Path,
    run_id: str,
    preflight_cfg: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    report_root = _contract_session_report_root(contract, log_dir, run_id)
    start_path = (report_root / "host_time_sync_active_start.json").resolve()
    stop_path = (report_root / "host_time_sync_active_stop.json").resolve()
    sample_path = (report_root / HOST_SYNC_SAMPLE_ARTIFACT).resolve()
    findings: List[str] = []
    thresholds = _host_sync_thresholds(preflight_cfg)
    start_payload = _read_json_object(start_path)
    stop_payload = _read_json_object(stop_path)
    sample_payloads = _load_jsonl_objects(sample_path)
    if start_payload is None:
        findings.append("host_time_sync_active_start_missing")
    if stop_payload is None:
        findings.append("host_time_sync_active_stop_missing")
    if not sample_path.exists():
        findings.append("host_time_sync_active_samples_missing")
    elif len(sample_payloads) <= 0:
        findings.append("host_time_sync_active_samples_empty")

    if isinstance(start_payload, dict):
        findings.extend(_evaluate_host_sync_payload(payload=start_payload, thresholds=thresholds, label="active_start"))
    if isinstance(stop_payload, dict):
        findings.extend(_evaluate_host_sync_payload(payload=stop_payload, thresholds=thresholds, label="active_stop"))

    sample_elapsed_non_monotonic_rows = 0
    previous_elapsed_sec: Optional[float] = None
    for idx, payload in enumerate(sample_payloads):
        findings.extend(_evaluate_host_sync_payload(payload=payload, thresholds=thresholds, label=f"active_sample_{idx}"))
        elapsed_active_sec = payload.get("elapsed_active_sec")
        if not isinstance(elapsed_active_sec, (int, float)):
            findings.append(f"host_time_sync_active_sample_elapsed_missing:{idx}")
            continue
        current_elapsed_sec = float(elapsed_active_sec)
        if previous_elapsed_sec is not None and current_elapsed_sec < previous_elapsed_sec:
            sample_elapsed_non_monotonic_rows += 1
        previous_elapsed_sec = current_elapsed_sec
    if sample_elapsed_non_monotonic_rows > 0:
        findings.append(
            f"host_time_sync_active_sample_elapsed_non_monotonic_rows:{int(sample_elapsed_non_monotonic_rows)}"
        )

    def _payload_compact(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        return {
            "clock_state": str(payload.get("clock_state") or "").strip().lower() or None,
            "stratum": payload.get("stratum") if isinstance(payload.get("stratum"), (int, float)) else None,
            "offset_ms": payload.get("offset_ms") if isinstance(payload.get("offset_ms"), (int, float)) else None,
            "jitter_ms": payload.get("jitter_ms") if isinstance(payload.get("jitter_ms"), (int, float)) else None,
            "root_distance_ms": (
                payload.get("root_distance_ms") if isinstance(payload.get("root_distance_ms"), (int, float)) else None
            ),
        }

    observed_payloads = [payload for payload in [start_payload, *sample_payloads, stop_payload] if isinstance(payload, dict)]
    observed_offsets = [abs(float(payload.get("offset_ms"))) for payload in observed_payloads if isinstance(payload.get("offset_ms"), (int, float))]
    observed_jitters = [float(payload.get("jitter_ms")) for payload in observed_payloads if isinstance(payload.get("jitter_ms"), (int, float))]
    observed_root_distances = [
        float(payload.get("root_distance_ms"))
        for payload in observed_payloads
        if isinstance(payload.get("root_distance_ms"), (int, float))
    ]
    observed_strata = [int(float(payload.get("stratum"))) for payload in observed_payloads if isinstance(payload.get("stratum"), (int, float))]

    evidence = {
        "report_root": str(report_root),
        "start_path": str(start_path),
        "stop_path": str(stop_path),
        "sample_path": str(sample_path),
        "sample_count": int(len(sample_payloads)),
        "thresholds": thresholds,
        "active_start": _payload_compact(start_payload),
        "active_stop": _payload_compact(stop_payload),
        "max_abs_offset_ms": (float(max(observed_offsets)) if observed_offsets else None),
        "max_jitter_ms": (float(max(observed_jitters)) if observed_jitters else None),
        "max_root_distance_ms": (float(max(observed_root_distances)) if observed_root_distances else None),
        "max_stratum": (int(max(observed_strata)) if observed_strata else None),
    }
    return evidence, findings


def _critical_timing_evidence_audit(event_rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    taker_decision_rows = 0
    taker_decision_missing_decision_ts_rows = 0
    taker_decision_missing_sec_to_expiry_rows = 0
    taker_decision_missing_timing_window_rows = 0
    accepted_submit_rows = 0
    accepted_submit_missing_decision_anchor_rows = 0
    accepted_submit_missing_submit_latency_rows = 0
    accepted_taker_submit_rows = 0
    accepted_taker_submit_missing_context_rows = 0
    maker_timing_rows_observed = 0
    maker_timing_missing_context_rows = 0

    for row in event_rows:
        event_type = str(row.get("event_type") or "").strip().lower()
        if is_taker_decision_event_type(event_type):
            taker_decision_rows += 1
            if _parse_ts(row.get("ts_decision_utc")) is None:
                taker_decision_missing_decision_ts_rows += 1
            if not isinstance(row.get("sec_to_expiry"), (int, float)):
                taker_decision_missing_sec_to_expiry_rows += 1
            if not str(row.get("timing_window_class") or "").strip():
                taker_decision_missing_timing_window_rows += 1
            continue

        if event_type != "order_submit":
            if event_type == "maker_market_viability_decision":
                maker_timing_rows_observed += 1
                if not isinstance(row.get("sec_to_expiry"), (int, float)) or not str(
                    row.get("market_reference_mode") or ""
                ).strip():
                    maker_timing_missing_context_rows += 1
            continue

        if str(row.get("submission_state") or "").strip().lower() != "accepted":
            continue
        accepted_submit_rows += 1
        if _parse_ts(row.get("decision_reference_ts_utc")) is None:
            accepted_submit_missing_decision_anchor_rows += 1
        latency_ms = row.get("decision_to_submit_latency_ms")
        if not isinstance(latency_ms, (int, float)) or float(latency_ms) < 0.0:
            accepted_submit_missing_submit_latency_rows += 1
        submission_lane = str(row.get("submission_lane") or "").strip().lower()
        if submission_lane == "taker":
            accepted_taker_submit_rows += 1
            taker_context = row.get("taker_competitiveness")
            if (
                not isinstance(taker_context, dict)
                or not isinstance(row.get("sec_to_expiry"), (int, float))
                or not str(taker_context.get("timing_window_class") or row.get("timing_window_class") or "").strip()
                or not isinstance(latency_ms, (int, float))
                or float(latency_ms) <= 0.0
            ):
                accepted_taker_submit_missing_context_rows += 1
        elif submission_lane == "maker":
            maker_context = row.get("maker_market_viability")
            maker_timing_rows_observed += 1
            if (
                not isinstance(maker_context, dict)
                or not isinstance(maker_context.get("sec_to_expiry", row.get("sec_to_expiry")), (int, float))
                or not isinstance(maker_context.get("maker_phase_allowed"), bool)
                or not isinstance(maker_context.get("maker_gate_open"), bool)
                or not str(
                    (maker_context if isinstance(maker_context, dict) else {}).get("market_reference_mode") or ""
                ).strip()
                or not str(
                    (maker_context if isinstance(maker_context, dict) else {}).get("market_reference_class") or ""
                ).strip()
            ):
                maker_timing_missing_context_rows += 1

    if taker_decision_missing_decision_ts_rows > 0:
        findings.append(
            f"taker_decision_missing_decision_ts_rows:{taker_decision_missing_decision_ts_rows}"
        )
    if taker_decision_missing_sec_to_expiry_rows > 0:
        findings.append(
            f"taker_decision_missing_sec_to_expiry_rows:{taker_decision_missing_sec_to_expiry_rows}"
        )
    if taker_decision_missing_timing_window_rows > 0:
        findings.append(
            f"taker_decision_missing_timing_window_rows:{taker_decision_missing_timing_window_rows}"
        )
    if accepted_submit_missing_decision_anchor_rows > 0:
        findings.append(
            f"accepted_order_submit_missing_decision_anchor_rows:{accepted_submit_missing_decision_anchor_rows}"
        )
    if accepted_submit_missing_submit_latency_rows > 0:
        findings.append(
            f"accepted_order_submit_missing_submit_latency_rows:{accepted_submit_missing_submit_latency_rows}"
        )
    if accepted_taker_submit_missing_context_rows > 0:
        findings.append(
            f"accepted_taker_submit_missing_timing_context_rows:{accepted_taker_submit_missing_context_rows}"
        )
    if maker_timing_rows_observed > 0 and maker_timing_missing_context_rows > 0:
        findings.append(
            f"maker_timing_rows_missing_context:{maker_timing_missing_context_rows}/{maker_timing_rows_observed}"
        )

    evidence = {
        "taker_decision_rows": int(taker_decision_rows),
        "taker_decision_missing_decision_ts_rows": int(taker_decision_missing_decision_ts_rows),
        "taker_decision_missing_sec_to_expiry_rows": int(taker_decision_missing_sec_to_expiry_rows),
        "taker_decision_missing_timing_window_rows": int(taker_decision_missing_timing_window_rows),
        "accepted_submit_rows": int(accepted_submit_rows),
        "accepted_submit_missing_decision_anchor_rows": int(accepted_submit_missing_decision_anchor_rows),
        "accepted_submit_missing_submit_latency_rows": int(accepted_submit_missing_submit_latency_rows),
        "accepted_taker_submit_rows": int(accepted_taker_submit_rows),
        "accepted_taker_submit_missing_context_rows": int(accepted_taker_submit_missing_context_rows),
        "maker_timing_rows_observed": int(maker_timing_rows_observed),
        "maker_timing_missing_context_rows": int(maker_timing_missing_context_rows),
    }
    return evidence, findings


def _timing_watchboard(
    *,
    cfg: Dict[str, Any],
    scoped_status_rows: List[Dict[str, Any]],
    event_rows: List[Dict[str, Any]],
    host_time_sync_evidence: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    preflight_cfg = cfg.get("preflight", {}) if isinstance(cfg.get("preflight"), dict) else {}
    operating_mode_cfg = cfg.get("operating_mode", {}) if isinstance(cfg.get("operating_mode"), dict) else {}
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else {}
    lane_thresholds = (
        risk_cfg.get("min_sec_to_expiry_for_new_exposure_by_lane", {})
        if isinstance(risk_cfg.get("min_sec_to_expiry_for_new_exposure_by_lane"), dict)
        else {}
    )

    thresholds = {
        "host_offset_ms": _safe_float(preflight_cfg.get("max_clock_offset_ms")) or 10.0,
        "host_jitter_ms": _safe_float(preflight_cfg.get("max_clock_jitter_ms")) or 10.0,
        "host_root_distance_ms": _safe_float(preflight_cfg.get("max_clock_root_distance_ms")) or 100.0,
        "book_last_msg_age_sec": _safe_float(operating_mode_cfg.get("ws_slo_max_book_last_msg_age_sec")) or 12.0,
        "chainlink_last_tick_age_sec": (
            _safe_float(operating_mode_cfg.get("ws_slo_max_chainlink_last_tick_age_sec")) or 30.0
        ),
        "warn_ratio": float(TIMING_WATCH_WARN_RATIO),
    }

    book_ages = []
    chain_ages = []
    for row in scoped_status_rows:
        book_feed = row.get("book_feed") if isinstance(row.get("book_feed"), dict) else {}
        chainlink = row.get("chainlink") if isinstance(row.get("chainlink"), dict) else {}
        book_age = _safe_float(book_feed.get("last_msg_age_sec"))
        chain_age = _safe_float(chainlink.get("last_tick_age_sec"))
        if book_age is not None:
            book_ages.append(float(book_age))
        if chain_age is not None:
            chain_ages.append(float(chain_age))

    submit_latencies = []
    for row in event_rows:
        if str(row.get("event_type") or "").strip().lower() != "order_submit":
            continue
        if str(row.get("submission_state") or "").strip().lower() != "accepted":
            continue
        latency_ms = _safe_float(row.get("decision_to_submit_latency_ms"))
        if latency_ms is not None and latency_ms >= 0.0:
            submit_latencies.append(float(latency_ms))

    host_thresholds = host_time_sync_evidence.get("thresholds", {}) if isinstance(host_time_sync_evidence, dict) else {}
    host_summary = {
        "max_abs_offset_ms": host_time_sync_evidence.get("max_abs_offset_ms"),
        "max_jitter_ms": host_time_sync_evidence.get("max_jitter_ms"),
        "max_root_distance_ms": host_time_sync_evidence.get("max_root_distance_ms"),
        "max_stratum": host_time_sync_evidence.get("max_stratum"),
        "thresholds": host_thresholds,
    }

    offset_max = _safe_float(host_summary.get("max_abs_offset_ms"))
    jitter_max = _safe_float(host_summary.get("max_jitter_ms"))
    root_distance_max = _safe_float(host_summary.get("max_root_distance_ms"))
    offset_warn = float(thresholds["host_offset_ms"]) * float(TIMING_WATCH_WARN_RATIO)
    jitter_warn = float(thresholds["host_jitter_ms"]) * float(TIMING_WATCH_WARN_RATIO)
    root_distance_warn = float(thresholds["host_root_distance_ms"]) * float(TIMING_WATCH_WARN_RATIO)
    if offset_max is not None and offset_max > offset_warn:
        warnings.append(
            f"timing_watch_host_offset_warn_band:{offset_max:.3f}>warn:{offset_warn:.3f}:limit:{float(thresholds['host_offset_ms']):.3f}"
        )
    if jitter_max is not None and jitter_max > jitter_warn:
        warnings.append(
            f"timing_watch_host_jitter_warn_band:{jitter_max:.3f}>warn:{jitter_warn:.3f}:limit:{float(thresholds['host_jitter_ms']):.3f}"
        )
    if root_distance_max is not None and root_distance_max > root_distance_warn:
        warnings.append(
            "timing_watch_host_root_distance_warn_band:"
            + f"{root_distance_max:.3f}>warn:{root_distance_warn:.3f}:limit:{float(thresholds['host_root_distance_ms']):.3f}"
        )

    book_age_summary = _latency_summary_ms(book_ages)
    chain_age_summary = _latency_summary_ms(chain_ages)
    book_warn = float(thresholds["book_last_msg_age_sec"]) * float(TIMING_WATCH_WARN_RATIO)
    chain_warn = float(thresholds["chainlink_last_tick_age_sec"]) * float(TIMING_WATCH_WARN_RATIO)
    if book_age_summary["sample_count"] > 0.0 and book_age_summary["p95_ms"] / 1000.0 > book_warn:
        warnings.append(
            "timing_watch_book_feed_last_msg_age_warn_band_p95:"
            + f"{book_age_summary['p95_ms'] / 1000.0:.3f}>warn:{book_warn:.3f}:limit:{float(thresholds['book_last_msg_age_sec']):.3f}"
        )
    if book_age_summary["sample_count"] > 0.0 and book_age_summary["max_ms"] / 1000.0 > book_warn:
        warnings.append(
            "timing_watch_book_feed_last_msg_age_warn_band_max:"
            + f"{book_age_summary['max_ms'] / 1000.0:.3f}>warn:{book_warn:.3f}:limit:{float(thresholds['book_last_msg_age_sec']):.3f}"
        )
    if chain_age_summary["sample_count"] > 0.0 and chain_age_summary["p95_ms"] / 1000.0 > chain_warn:
        warnings.append(
            "timing_watch_chainlink_last_tick_age_warn_band_p95:"
            + f"{chain_age_summary['p95_ms'] / 1000.0:.3f}>warn:{chain_warn:.3f}:limit:{float(thresholds['chainlink_last_tick_age_sec']):.3f}"
        )
    if chain_age_summary["sample_count"] > 0.0 and chain_age_summary["max_ms"] / 1000.0 > chain_warn:
        warnings.append(
            "timing_watch_chainlink_last_tick_age_warn_band_max:"
            + f"{chain_age_summary['max_ms'] / 1000.0:.3f}>warn:{chain_warn:.3f}:limit:{float(thresholds['chainlink_last_tick_age_sec']):.3f}"
        )

    lifecycle_cfg = cfg.get("lifecycle", {}) if isinstance(cfg, dict) else {}
    if not isinstance(lifecycle_cfg, dict):
        lifecycle_cfg = {}
    lifecycle_selection_cfg = lifecycle_cfg.get("selection", {})
    if not isinstance(lifecycle_selection_cfg, dict):
        lifecycle_selection_cfg = {}
    lifecycle_phase_cfg = lifecycle_cfg.get("phase", {})
    if not isinstance(lifecycle_phase_cfg, dict):
        lifecycle_phase_cfg = {}
    maker_gate_min = _safe_float(
        lifecycle_phase_cfg.get("maker_window_close_sec", lifecycle_phase_cfg.get("taker_window_open_sec"))
    )
    maker_gate_max = _safe_float(lifecycle_phase_cfg.get("maker_window_open_sec"))
    risk_global_min = _safe_float(risk_cfg.get("min_sec_to_expiry_for_new_exposure"))
    risk_maker_min = _safe_float(lane_thresholds.get("maker"))
    risk_effective_maker_min = risk_maker_min if risk_maker_min is not None else risk_global_min
    layered_left_edge_split_active = False

    return {
        "host_sync": host_summary,
        "freshness": {
            "status_row_count": int(len(scoped_status_rows)),
            "thresholds_sec": {
                "book_last_msg_age_sec": float(thresholds["book_last_msg_age_sec"]),
                "chainlink_last_tick_age_sec": float(thresholds["chainlink_last_tick_age_sec"]),
            },
            "book_last_msg_age_summary_sec": {
                "sample_count": float(book_age_summary["sample_count"]),
                "median_sec": float(book_age_summary["median_ms"] / 1000.0),
                "p90_sec": float(book_age_summary["p90_ms"] / 1000.0),
                "p95_sec": float(book_age_summary["p95_ms"] / 1000.0),
                "max_sec": float(book_age_summary["max_ms"] / 1000.0),
            },
            "chainlink_last_tick_age_summary_sec": {
                "sample_count": float(chain_age_summary["sample_count"]),
                "median_sec": float(chain_age_summary["median_ms"] / 1000.0),
                "p90_sec": float(chain_age_summary["p90_ms"] / 1000.0),
                "p95_sec": float(chain_age_summary["p95_ms"] / 1000.0),
                "max_sec": float(chain_age_summary["max_ms"] / 1000.0),
            },
        },
        "submit_latency": {
            "accepted_submit_latency_ms_summary": _latency_summary_ms(submit_latencies),
        },
        "ownership_entry_authority": {
            "enabled": bool(lifecycle_selection_cfg.get("enabled", True)),
            "max_sec_to_expiry": _safe_float(lifecycle_selection_cfg.get("max_sec_to_expiry")),
            "min_market_age_sec": _safe_float(lifecycle_selection_cfg.get("min_market_age_sec")),
        },
        "maker_timing_authority": {
            "timing_gate_enabled": bool(
                (lifecycle_cfg.get("lane_gates") or {}).get("maker", {}).get("timing_gate_enabled", False)
                if isinstance((lifecycle_cfg.get("lane_gates") or {}).get("maker", {}), dict)
                else False
            ),
            "timing_gate_min_sec_to_expiry": maker_gate_min,
            "timing_gate_max_sec_to_expiry": maker_gate_max,
            "risk_min_sec_to_expiry_for_new_exposure_global": risk_global_min,
            "risk_min_sec_to_expiry_for_new_exposure_maker_effective": risk_effective_maker_min,
            "selection_gate_timing_min_sec_to_expiry": maker_gate_min,
            "selection_gate_timing_max_sec_to_expiry": maker_gate_max,
            "selection_gate_timing_duplicate_owner_active": False,
            "layered_left_edge_split_active": bool(layered_left_edge_split_active),
        },
        "warning_band_ratio": float(TIMING_WATCH_WARN_RATIO),
    }


def run_audit(
    *,
    config_path: pathlib.Path,
    max_allowed_skew_sec: float,
    max_status_age_sec: float,
    min_status_rows: int,
    log_dir: Optional[pathlib.Path] = None,
    run_id: Optional[str] = None,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="time_discipline_audit", session_phase=session_phase)
    cfg = load_execution_config(config_path.resolve())
    findings: List[str] = []
    warnings: List[str] = []
    preflight = cfg.get("preflight", {})
    configured_time_policy = cfg.get("time_policy", {}) if isinstance(cfg.get("time_policy"), dict) else {}

    if not bool(preflight.get("check_clock_sync", False)):
        findings.append("preflight_clock_sync_disabled")

    configured_skew = float(preflight.get("max_clock_skew_sec", 0.0) or 0.0)
    if configured_skew <= 0:
        findings.append("preflight_max_clock_skew_invalid")
    elif configured_skew > float(max_allowed_skew_sec):
        findings.append(f"preflight_max_clock_skew_too_loose:{configured_skew:.3f}>max:{float(max_allowed_skew_sec):.3f}")

    configured_log_dir = pathlib.Path(str(cfg.get("storage", {}).get("log_dir", "./logs_exec"))).resolve()
    effective_log_dir = log_dir.resolve() if isinstance(log_dir, pathlib.Path) else configured_log_dir
    run_filter = str(run_id or "").strip()
    run_id_resolution = "missing"
    if run_filter:
        run_id_resolution = "explicit"

    contract: Optional[Dict[str, Any]] = None
    resolved_contract_path = ""
    if run_contract_path is not None:
        resolve_log_dir = effective_log_dir if effective_log_dir is not None else pathlib.Path(".").resolve()
        try:
            contract = resolve_run_contract(
                log_dir=resolve_log_dir,
                run_id=run_filter or None,
                run_contract_path_override=run_contract_path,
                allow_open=(normalized_phase == "validate_active"),
            )
        except ValueError as exc:
            findings.append(str(exc))
            contract = None
        if isinstance(contract, dict):
            resolved_contract_path = str(contract.get("_path") or "")
            if not run_filter:
                run_filter = str(contract.get("run_id") or "").strip()
                if run_filter:
                    run_id_resolution = "contract"
            log_root = str(contract.get("log_root") or "").strip()
            if log_root:
                effective_log_dir = pathlib.Path(log_root).expanduser().resolve()

    status_paths = sorted(effective_log_dir.glob("status_*.jsonl"))
    if isinstance(contract, dict):
        status_slice = run_contract_slice_path(contract, stream="status")
        if status_slice is not None:
            status_paths = [status_slice]

    latest_ts, checked_rows, non_monotonic = _latest_status_ts(
        status_paths=status_paths,
        run_id=run_filter,
        max_lines_per_file=max(0, int(max_lines_per_file)),
        run_contract=contract,
    )
    scoped_status_rows = _status_rows(
        status_paths=status_paths,
        run_id=run_filter,
        max_lines_per_file=max(0, int(max_lines_per_file)),
        run_contract=contract,
    )
    if checked_rows < int(min_status_rows):
        findings.append(f"status_rows_too_few:{checked_rows}<min:{int(min_status_rows)}")
    if non_monotonic > 0:
        findings.append(f"status_ts_non_monotonic_rows:{non_monotonic}")

    time_policy_evidence, time_policy_findings = _validate_time_policy(scoped_status_rows)
    contract_authority_level = (
        str(contract.get("authority_level") or "").strip().lower()
        if isinstance(contract, dict)
        else ""
    )
    strict_time_policy_enforcement = contract_authority_level == "authoritative"
    if strict_time_policy_enforcement:
        findings.extend(time_policy_findings)
    else:
        warnings.extend(time_policy_findings)

    event_paths = sorted(effective_log_dir.glob("events_*.jsonl"))
    if isinstance(contract, dict):
        events_slice = run_contract_slice_path(contract, stream="events")
        if events_slice is not None:
            event_paths = [events_slice]
    event_rows: List[Dict[str, Any]] = []
    for row in load_jsonl(event_paths, max_lines_per_file=max(0, int(max_lines_per_file))):
        if run_filter and str(row.get("run_id") or "").strip() != run_filter:
            continue
        event_rows.append(row)
    event_rows = apply_contract_bounds(event_rows, contract)

    selected_policy = time_policy_evidence.get("selected_policy", {})
    policy_skew_tolerance_ms = float(
        selected_policy.get("skew_tolerance_ms")
        if isinstance(selected_policy, dict) and selected_policy.get("skew_tolerance_ms") is not None
        else max_allowed_skew_sec * 1000.0
    )
    event_domain_evidence, event_domain_findings = _event_domain_audit(
        event_rows=event_rows,
        skew_tolerance_ms=policy_skew_tolerance_ms,
    )
    if strict_time_policy_enforcement:
        findings.extend(event_domain_findings)
    else:
        warnings.extend(event_domain_findings)

    configured_time_policy_skew_ms = float(configured_time_policy.get("skew_tolerance_ms", 0.0) or 0.0)
    if configured_time_policy_skew_ms <= 0.0:
        findings.append("configured_time_policy_skew_tolerance_invalid")
    elif policy_skew_tolerance_ms > configured_time_policy_skew_ms:
        message = (
            "time_policy_skew_tolerance_too_loose:"
            + f"{policy_skew_tolerance_ms:.3f}>configured_time_policy_skew_ms:{configured_time_policy_skew_ms:.3f}"
        )
        if strict_time_policy_enforcement:
            findings.append(message)
        else:
            warnings.append(message)

    critical_timing_evidence, critical_timing_findings = _critical_timing_evidence_audit(event_rows)
    if strict_time_policy_enforcement:
        findings.extend(critical_timing_findings)
    else:
        warnings.extend(critical_timing_findings)

    host_time_sync_evidence: Dict[str, Any] = {}
    if strict_time_policy_enforcement:
        host_time_sync_evidence, host_time_sync_findings = _host_time_sync_audit(
            contract=contract,
            log_dir=effective_log_dir,
            run_id=run_filter,
            preflight_cfg=preflight,
        )
        findings.extend(host_time_sync_findings)

    if configured_skew > 0:
        configured_skew_ms = configured_skew * 1000.0
        if configured_time_policy_skew_ms > configured_skew_ms:
            message = (
                "configured_time_policy_skew_tolerance_exceeds_fallback_skew:"
                + f"{configured_time_policy_skew_ms:.3f}>configured_max_clock_skew_ms:{configured_skew_ms:.3f}"
            )
            if strict_time_policy_enforcement:
                findings.append(message)
            else:
                warnings.append(message)

    status_age_sec = None
    if latest_ts is None:
        findings.append("status_ts_missing")
    else:
        now = datetime.now(timezone.utc)
        status_age_sec = max(0.0, (now - latest_ts).total_seconds())
        if status_age_sec > float(max_status_age_sec):
            findings.append(f"status_ts_too_stale:{status_age_sec:.3f}>max:{float(max_status_age_sec):.3f}")

    timing_watchboard = _timing_watchboard(
        cfg=cfg,
        scoped_status_rows=scoped_status_rows,
        event_rows=event_rows,
        host_time_sync_evidence=host_time_sync_evidence,
        warnings=warnings,
    )

    return {
        "config_path": str(config_path.resolve()),
        "session_phase": normalized_phase,
        "log_dir": str(effective_log_dir),
        "configured_log_dir": str(configured_log_dir),
        "run_contract_path": resolved_contract_path,
        "contract_authority_level": contract_authority_level,
        "run_id_filter": run_filter,
        "run_id_resolution": run_id_resolution,
        "status_source_paths": [str(path.resolve()) for path in status_paths],
        "event_source_paths": [str(path.resolve()) for path in event_paths],
        "checked_status_rows": int(checked_rows),
        "checked_event_rows": int(len(event_rows)),
        "non_monotonic_rows": int(non_monotonic),
        "status_age_sec": status_age_sec,
        "configured_max_clock_skew_sec": configured_skew,
        "configured_time_policy_skew_tolerance_ms": configured_time_policy_skew_ms,
        "time_policy": time_policy_evidence,
        "event_timestamp_domain_audit": event_domain_evidence,
        "critical_timing_evidence": critical_timing_evidence,
        "host_time_sync": host_time_sync_evidence,
        "timing_watchboard": timing_watchboard,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro clock/time discipline audit")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--max-allowed-skew-sec", type=float, default=0.25, help="Strict upper bound for preflight.max_clock_skew_sec")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Maximum allowed age of latest status ts_utc")
    parser.add_argument("--min-status-rows", type=int, default=5, help="Minimum status rows required to evaluate monotonicity")
    parser.add_argument("--log-dir", default="", help="Optional explicit status log directory override")
    parser.add_argument("--run-id", default="", help="Optional run_id filter")
    parser.add_argument("--run-contract", default="", help="Optional explicit run contract path")
    parser.add_argument("--session-phase", default="validate_postrun", help="Validation phase context")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Max lines per jsonl file (0 means full file)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        config_path=pathlib.Path(args.config),
        max_allowed_skew_sec=max(0.1, float(args.max_allowed_skew_sec)),
        max_status_age_sec=max(1.0, float(args.max_status_age_sec)),
        min_status_rows=max(1, int(args.min_status_rows)),
        log_dir=(pathlib.Path(args.log_dir).resolve() if str(args.log_dir).strip() else None),
        run_id=(str(args.run_id).strip() or None),
        run_contract_path=(pathlib.Path(args.run_contract).resolve() if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
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
