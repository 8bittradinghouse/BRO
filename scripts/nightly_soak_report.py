#!/usr/bin/env python3
"""Generate nightly soak metrics from Bro execution logs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from prodesk.artifact_identity import build_artifact_identity
from prodesk.edge_truth_contract import (
    EDGE_AUTH_MAKER_NEW_RISK_FIELD,
    effective_stage_from_payload,
    is_taker_decision_event_type,
    is_taker_reason,
    is_taker_stage_window_semantic_check_event_type,
    is_taker_submit_event_type,
    stage_bucket_from_payload,
    stage_surface_fields,
)
from prodesk.execution_quality import ExecutionQualityModel
from prodesk.jsonl_utils import load_jsonl
from prodesk.models import BookTop, OrderIntent
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.runtime_semantics import classify_runtime
from prodesk.session_phase import enforce_validation_phase
from scripts.paper_harness_realism_contract import (
    EXERCISED_HARNESS_REALISM_FIELD,
    HARNESS_REALISM_GRADE_AUTHORITY,
    HARNESS_REALISM_GRADE_SEMANTICS,
    build_exercised_harness_realism_surface,
    empty_harness_realism_breakdown,
    normalize_nightly_exercised_harness_realism,
)

REPORT_SCHEMA_VERSION = 2
DEFAULT_MAX_LINES_PER_FILE = 200000
ADMISSION_RUBRIC_VERSION = 1
MAKER_CANNON_SHADOW_VERSION = 1
MAKER_MID_WINDOW_PROBE_VERSION = 1
MAKER_ZERO_SUBMIT_AUDIT_VERSION = 1
MAKER_QUOTE_INTEGRITY_AUDIT_VERSION = 1
MAKER_SELECTION_AUTHORITY_AUDIT_VERSION = 1
MAKER_CANNON_TARGET_NOTIONAL_USD = 350.0
MAKER_CANNON_MIN_DEPTH_MULTIPLE = 1.5
MAKER_CANNON_STACK_SOFT_MAX = 4
MAKER_CANNON_STACK_HARD_MAX = 6
MAKER_QUOTE_INTEGRITY_PRIMARY_RUN_ID = "484e533d-c9a1-4ac4-bc0d-ce379c624e09"
MAKER_QUOTE_INTEGRITY_EVENT_TYPES = (
    "maker_fight_admission_shadow",
    "maker_queue_pressure_adjustment",
    "pre_submit_cross_guard_adjusted",
    "order_submit",
    "order_cancel",
    "order_cancel_suppressed",
    "edge_evaluation",
)
MAKER_ZERO_SUBMIT_SPECIMEN_RUN_IDS = (
    "76a4be3b-9b26-461d-a952-4ad90fbf7f1b",
    "7117fb46-fd75-4e8d-85fe-6d5a70eab731",
)
MAKER_ZERO_SUBMIT_SPECIMEN_LABEL_BY_RUN_ID = {
    "76a4be3b-9b26-461d-a952-4ad90fbf7f1b": "packet_b_350",
    "7117fb46-fd75-4e8d-85fe-6d5a70eab731": "caliber_250",
}


def parse_ts(value: Any) -> Optional[dt.datetime]:
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


def _is_taker_submit_reason(reason: Any) -> bool:
    return is_taker_reason(reason)


def _maker_cannon_window_class(sec_to_expiry: Any) -> str:
    if not isinstance(sec_to_expiry, (int, float)):
        return "unknown"
    sec = float(sec_to_expiry)
    if sec < 0.0:
        return "expired"
    if sec <= 10.0:
        return "le_10s"
    if sec <= 15.0:
        return "10_to_15s"
    if sec <= 20.0:
        return "15_to_20s"
    return "gt_20s"


def _maker_shadow_timing_band_class(sec_to_expiry: Any) -> str:
    if not isinstance(sec_to_expiry, (int, float)):
        return "unknown"
    sec = float(sec_to_expiry)
    if sec < 0.0:
        return "expired"
    if sec <= 10.0:
        return "le_10s"
    if sec <= 15.0:
        return "10_to_15s"
    if sec <= 20.0:
        return "15_to_20s"
    if sec <= 30.0:
        return "20_to_30s"
    if sec <= 45.0:
        return "30_to_45s"
    if sec <= 60.0:
        return "45_to_60s"
    if sec <= 90.0:
        return "60_to_90s"
    return "gt_90s"


def _maker_shadow_stack_pressure_class(open_maker_orders_total: Any) -> str:
    if not isinstance(open_maker_orders_total, (int, float)):
        return "unknown"
    count = int(float(open_maker_orders_total))
    if count < MAKER_CANNON_STACK_SOFT_MAX:
        return "below_soft_cap"
    if count <= MAKER_CANNON_STACK_HARD_MAX:
        return "within_hard_cap"
    return "over_hard_cap"


def _maker_shadow_session_regime_class(ts_decision_utc: Any) -> str:
    parsed = parse_ts(ts_decision_utc)
    if parsed is None:
        return "unknown"
    hour = int(parsed.astimezone(dt.timezone.utc).hour)
    if 0 <= hour < 8:
        return "asia_dominant_heuristic"
    if 12 <= hour < 20:
        return "usa_europe_peak_heuristic"
    return "transition_heuristic"


def _maker_cannon_market_probability_band(market_probability: Any) -> str:
    if not isinstance(market_probability, (int, float)):
        return "unknown"
    value = float(market_probability)
    if value <= 0.01 + 1e-9:
        return "le_0p01"
    if value >= 0.99 - 1e-9:
        return "ge_0p99"
    if value <= 0.05 + 1e-9:
        return "0p01_to_0p05"
    if value >= 0.95 - 1e-9:
        return "0p95_to_0p99"
    return "interior"


def _maker_cannon_favored_depth_class(
    visible_depth_shares: Any,
    *,
    zero_imputed: bool,
) -> str:
    if isinstance(visible_depth_shares, (int, float)):
        value = float(visible_depth_shares)
        if value <= 1e-9:
            return "zero_imputed" if zero_imputed else "zero_reported"
        return "positive"
    return "unknown"


def _apply_maker_cannon_shadow_fields(row: Dict[str, Any]) -> None:
    def _optional_float_local(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    sec_to_expiry = row.get("sec_to_expiry")
    cannon_target_notional_usd = _optional_float_local(row.get("cannon_target_notional_usd"))
    if not isinstance(cannon_target_notional_usd, (int, float)) or float(cannon_target_notional_usd) <= 0.0:
        cannon_target_notional_usd = float(MAKER_CANNON_TARGET_NOTIONAL_USD)
    cannon_min_depth_multiple = _optional_float_local(row.get("cannon_min_depth_multiple"))
    if not isinstance(cannon_min_depth_multiple, (int, float)) or float(cannon_min_depth_multiple) <= 0.0:
        cannon_min_depth_multiple = float(MAKER_CANNON_MIN_DEPTH_MULTIPLE)
    row["maker_cannon_shadow_version"] = MAKER_CANNON_SHADOW_VERSION
    row["cannon_target_notional_usd"] = float(cannon_target_notional_usd)
    row["cannon_min_depth_multiple"] = float(cannon_min_depth_multiple)
    row["cannon_stack_soft_max"] = int(MAKER_CANNON_STACK_SOFT_MAX)
    row["cannon_stack_hard_max"] = int(MAKER_CANNON_STACK_HARD_MAX)
    row["cannon_window_class"] = _maker_cannon_window_class(sec_to_expiry)
    row["maker_timing_band_class"] = _maker_shadow_timing_band_class(sec_to_expiry)

    parsed_ts = parse_ts(row.get("ts_decision_utc"))
    row["decision_hour_utc"] = (
        int(parsed_ts.astimezone(dt.timezone.utc).hour)
        if parsed_ts is not None
        else None
    )
    row["session_regime_class"] = _maker_shadow_session_regime_class(
        row.get("ts_decision_utc")
    )

    desired_quote_price = row.get("desired_quote_price")
    visible_depth_shares = row.get("visible_depth_shares")
    visible_depth_notional_usd = None
    if isinstance(desired_quote_price, (int, float)) and isinstance(visible_depth_shares, (int, float)):
        visible_depth_notional_usd = float(desired_quote_price) * float(visible_depth_shares)
    row["visible_depth_notional_usd"] = (
        float(visible_depth_notional_usd)
        if isinstance(visible_depth_notional_usd, (int, float))
        else None
    )
    depth_multiple_vs_cannon_target = None
    if isinstance(visible_depth_notional_usd, (int, float)) and float(cannon_target_notional_usd) > 0.0:
        depth_multiple_vs_cannon_target = float(visible_depth_notional_usd) / float(
            cannon_target_notional_usd
        )
    row["depth_multiple_vs_cannon_target"] = (
        float(depth_multiple_vs_cannon_target)
        if isinstance(depth_multiple_vs_cannon_target, (int, float))
        else None
    )
    if isinstance(depth_multiple_vs_cannon_target, (int, float)):
        row["cannon_depth_requirement_met"] = bool(
            float(depth_multiple_vs_cannon_target) >= float(cannon_min_depth_multiple)
        )
    else:
        row["cannon_depth_requirement_met"] = None

    row["stack_pressure_class"] = _maker_shadow_stack_pressure_class(
        row.get("open_maker_orders_total")
    )
    selection_gate_min_sec_to_expiry = _optional_float_local(
        row.get("selection_gate_min_sec_to_expiry")
    )
    selection_gate_max_sec_to_expiry = _optional_float_local(
        row.get("selection_gate_max_sec_to_expiry")
    )
    row["selection_gate_min_sec_to_expiry"] = selection_gate_min_sec_to_expiry
    row["selection_gate_max_sec_to_expiry"] = selection_gate_max_sec_to_expiry
    if (
        row.get("launch_safe_selection_timing_window_met") is None
        and (
            selection_gate_min_sec_to_expiry is not None
            or selection_gate_max_sec_to_expiry is not None
        )
    ):
        if isinstance(sec_to_expiry, (int, float)):
            timing_window_met = True
            if selection_gate_min_sec_to_expiry is not None:
                timing_window_met = bool(
                    timing_window_met
                    and float(sec_to_expiry) >= float(selection_gate_min_sec_to_expiry) - 1e-9
                )
            if selection_gate_max_sec_to_expiry is not None:
                timing_window_met = bool(
                    timing_window_met
                    and float(sec_to_expiry) <= float(selection_gate_max_sec_to_expiry) + 1e-9
                )
            row["launch_safe_selection_timing_window_met"] = bool(timing_window_met)
        else:
            row["launch_safe_selection_timing_window_met"] = None


def _load_run_manifest(log_dir: pathlib.Path, run_id: Optional[str]) -> Dict[str, Any]:
    target = str(run_id or "").strip()
    if not target:
        return {}
    manifest_path = log_dir / f"run_manifest_{target}.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_outcome_truth_records(log_dir: pathlib.Path, run_id: Optional[str]) -> List[Dict[str, Any]]:
    target = str(run_id or "").strip()
    if not target:
        return []
    records_path = log_dir / "reports" / target / "outcome_truth_records.jsonl"
    if not records_path.exists():
        return []
    rows = load_jsonl([records_path], max_lines_per_file=0)
    return [row for row in rows if isinstance(row, dict)]


def _dated_log_file_date(prefix: str, path: pathlib.Path) -> Optional[dt.date]:
    name = str(path.name or "")
    if not name.startswith(f"{prefix}_") or not name.endswith(".jsonl"):
        return None
    raw = name[len(prefix) + 1 : -6]
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def _select_run_scoped_files(*, log_dir: pathlib.Path, prefix: str, run_id: Optional[str]) -> List[pathlib.Path]:
    files = sorted(log_dir.glob(f"{prefix}_*.jsonl"))
    if not files:
        return []
    target = str(run_id or "").strip()
    if not target:
        return files

    manifest = _load_run_manifest(log_dir, target)
    manifest_key = f"{prefix}_path"
    raw_manifest_path = str(manifest.get(manifest_key) or "").strip()
    if not raw_manifest_path:
        return files
    manifest_name = pathlib.Path(raw_manifest_path).name
    if not manifest_name:
        return files

    matched_indices = [idx for idx, path in enumerate(files) if path.name == manifest_name]
    if not matched_indices:
        return files

    selected_indices = set(matched_indices)

    start_ts = parse_ts(manifest.get("start_ts"))
    end_ts = parse_ts(manifest.get("end_ts"))
    desired_dates: set[dt.date] = set()
    if start_ts is not None:
        desired_dates.add(start_ts.date())
    if end_ts is not None:
        desired_dates.add(end_ts.date())
    elif start_ts is not None:
        # Active run: include current UTC date in case it crossed midnight.
        desired_dates.add(dt.datetime.now(dt.timezone.utc).date())

    if desired_dates:
        date_to_idx: Dict[dt.date, int] = {}
        for idx, path in enumerate(files):
            row_date = _dated_log_file_date(prefix, path)
            if row_date is not None:
                date_to_idx[row_date] = idx
        for wanted in desired_dates:
            idx = date_to_idx.get(wanted)
            if idx is not None:
                selected_indices.add(idx)

    return [files[idx] for idx in sorted(selected_indices)]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return None


def _is_quote_active_row(row: Dict[str, Any]) -> bool:
    open_orders = row.get("gauge.open_orders")
    if isinstance(open_orders, (int, float)) and float(open_orders) > 0:
        return True
    quote_active = row.get("gauge.quote_active")
    if isinstance(quote_active, (int, float)) and float(quote_active) > 0:
        return True
    # Rolling 60s usage gauges are authoritative status-row activity signals and
    # prevent undercounting when cumulative counters are emitted sparsely.
    orders_used_60s = row.get("gauge.orders_used_60s")
    if isinstance(orders_used_60s, (int, float)) and float(orders_used_60s) > 0:
        return True
    cancels_used_60s = row.get("gauge.cancels_used_60s")
    if isinstance(cancels_used_60s, (int, float)) and float(cancels_used_60s) > 0:
        return True
    actions_last_cycle = row.get("gauge.actions_last_cycle")
    if isinstance(actions_last_cycle, (int, float)) and float(actions_last_cycle) > 0:
        return True
    taker_actions_last_cycle = row.get("gauge.taker_actions_last_cycle")
    if isinstance(taker_actions_last_cycle, (int, float)) and float(taker_actions_last_cycle) > 0:
        return True
    taker_submitted_last_cycle = row.get("gauge.taker_submitted_last_cycle")
    if isinstance(taker_submitted_last_cycle, (int, float)) and float(taker_submitted_last_cycle) > 0:
        return True
    taker_fills_last_cycle = row.get("gauge.taker_fills_last_cycle")
    if isinstance(taker_fills_last_cycle, (int, float)) and float(taker_fills_last_cycle) > 0:
        return True
    return False


def _is_no_target_standdown_row(row: Dict[str, Any]) -> bool:
    state = str(row.get("runtime_state") or "").strip().lower()
    if state == "no_target_standdown":
        return True
    for key in ("no_target_standdown", "gauge.no_target_standdown"):
        value = row.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and float(value) > 0:
            return True
    return False


def _is_quote_window_row(row: Dict[str, Any]) -> bool:
    if _is_no_target_standdown_row(row):
        return False
    for key in (
        "active_targets_present",
        "gauge.active_targets_present",
        "gauge.target_discovery_active_targets",
        "target_count",
        "gauge.target_count",
    ):
        value = row.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and float(value) > 0:
            return True
    return False


def _status_activity_diagnostics(status_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    total_rows = float(len(status_rows))
    if total_rows <= 0:
        return {
            "status_rows_total": 0.0,
            "quote_window_rows": 0.0,
            "quote_window_ratio": 0.0,
            "quote_active_rows": 0.0,
            "quote_active_ratio": 0.0,
            "quote_active_within_window_ratio": 0.0,
            "participation_rows": 0.0,
            "participation_ratio": 0.0,
            "participation_within_window_ratio": 0.0,
        }

    quote_window_rows = 0.0
    quote_active_rows = 0.0
    participation_rows = 0.0
    prev_counters: Dict[str, float] = {}

    for row in status_rows:
        in_window = _is_quote_window_row(row)
        if in_window:
            quote_window_rows += 1.0

        is_active = _is_quote_active_row(row)
        if is_active:
            quote_active_rows += 1.0

        participated = False
        for key in (
            "gauge.actions_last_cycle",
            "gauge.taker_actions_last_cycle",
            "gauge.taker_submitted_last_cycle",
            "gauge.taker_fills_last_cycle",
            "gauge.order_submission_attempts_last_cycle",
            "order_submission_attempts_last_cycle",
        ):
            value = row.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                participated = True
                break

        for key in (
            "counter.orders_submitted",
            "counter.orders_canceled",
            "counter.taker_orders_submitted",
            "counter.taker_orders_filled",
            "counter.risk_rejects",
        ):
            value = row.get(key)
            if not isinstance(value, (int, float)):
                continue
            current = float(value)
            prior = prev_counters.get(key)
            if prior is not None and current > prior:
                participated = True
            prev_counters[key] = current

        if participated:
            participation_rows += 1.0

    return {
        "status_rows_total": total_rows,
        "quote_window_rows": quote_window_rows,
        "quote_window_ratio": (quote_window_rows / total_rows) if total_rows > 0 else 0.0,
        "quote_active_rows": quote_active_rows,
        "quote_active_ratio": (quote_active_rows / total_rows) if total_rows > 0 else 0.0,
        "quote_active_within_window_ratio": (quote_active_rows / quote_window_rows) if quote_window_rows > 0 else 0.0,
        "participation_rows": participation_rows,
        "participation_ratio": (participation_rows / total_rows) if total_rows > 0 else 0.0,
        "participation_within_window_ratio": (participation_rows / quote_window_rows) if quote_window_rows > 0 else 0.0,
    }


def _runtime_resource_stats(status_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    def _row_metric(row: Dict[str, Any], key: str) -> Optional[float]:
        runtime_resource = row.get("runtime_resource")
        if isinstance(runtime_resource, dict):
            nested = _safe_float(runtime_resource.get(key), default=float("nan"))
            if nested == nested:
                return nested
        direct = _safe_float(row.get(f"gauge.{key}"), default=float("nan"))
        if direct != direct:
            return None
        return direct

    cpu_vals: List[float] = []
    cpu_norm_vals: List[float] = []
    rss_vals: List[float] = []
    load1_vals: List[float] = []
    load5_vals: List[float] = []
    load15_vals: List[float] = []
    mem_avail_vals: List[float] = []
    mem_avail_ratio_vals: List[float] = []
    swap_used_vals: List[float] = []
    swap_used_ratio_vals: List[float] = []

    for row in status_rows:
        cpu = _row_metric(row, "process_cpu_percent")
        if isinstance(cpu, float):
            cpu_vals.append(cpu)
        cpu_norm = _row_metric(row, "process_cpu_percent_normalized")
        if isinstance(cpu_norm, float):
            cpu_norm_vals.append(cpu_norm)
        rss = _row_metric(row, "process_rss_mb")
        if isinstance(rss, float):
            rss_vals.append(rss)
        load1 = _row_metric(row, "system_load1")
        if isinstance(load1, float):
            load1_vals.append(load1)
        load5 = _row_metric(row, "system_load5")
        if isinstance(load5, float):
            load5_vals.append(load5)
        load15 = _row_metric(row, "system_load15")
        if isinstance(load15, float):
            load15_vals.append(load15)
        mem_avail = _row_metric(row, "system_mem_available_mb")
        if isinstance(mem_avail, float):
            mem_avail_vals.append(mem_avail)
        mem_avail_ratio = _row_metric(row, "system_mem_available_ratio")
        if isinstance(mem_avail_ratio, float):
            mem_avail_ratio_vals.append(mem_avail_ratio)
        swap_used = _row_metric(row, "system_swap_used_mb")
        if isinstance(swap_used, float):
            swap_used_vals.append(swap_used)
        swap_used_ratio = _row_metric(row, "system_swap_used_ratio")
        if isinstance(swap_used_ratio, float):
            swap_used_ratio_vals.append(swap_used_ratio)

    return {
        "resource_status_rows": float(len(status_rows)),
        "process_cpu_percent_p95": _percentile(cpu_vals, 0.95),
        "process_cpu_percent_max": max(cpu_vals) if cpu_vals else 0.0,
        "process_cpu_percent_normalized_p95": _percentile(cpu_norm_vals, 0.95),
        "process_cpu_percent_normalized_max": max(cpu_norm_vals) if cpu_norm_vals else 0.0,
        "process_rss_mb_p95": _percentile(rss_vals, 0.95),
        "process_rss_mb_max": max(rss_vals) if rss_vals else 0.0,
        "system_load1_p95": _percentile(load1_vals, 0.95),
        "system_load1_max": max(load1_vals) if load1_vals else 0.0,
        "system_load5_p95": _percentile(load5_vals, 0.95),
        "system_load15_p95": _percentile(load15_vals, 0.95),
        "system_mem_available_mb_min": min(mem_avail_vals) if mem_avail_vals else 0.0,
        "system_mem_available_mb_p50": _percentile(mem_avail_vals, 0.50),
        "system_mem_available_ratio_min": min(mem_avail_ratio_vals) if mem_avail_ratio_vals else 0.0,
        "system_swap_used_mb_max": max(swap_used_vals) if swap_used_vals else 0.0,
        "system_swap_used_ratio_max": max(swap_used_ratio_vals) if swap_used_ratio_vals else 0.0,
    }


def _quote_uptime_ratio(status_rows: List[Dict[str, Any]]) -> float:
    if not status_rows:
        return 0.0
    active_rows = 0
    prev_submitted: Optional[float] = None
    prev_canceled: Optional[float] = None
    prev_risk_rejects: Optional[float] = None
    timeline: List[Tuple[Optional[dt.datetime], bool]] = []

    for row in status_rows:
        active = _is_quote_active_row(row)
        submitted = _safe_float(row.get("counter.orders_submitted"), default=float("nan"))
        canceled = _safe_float(row.get("counter.orders_canceled"), default=float("nan"))
        risk_rejects = _safe_float(row.get("counter.risk_rejects"), default=float("nan"))
        if submitted != submitted:
            submitted = None
        if canceled != canceled:
            canceled = None
        if risk_rejects != risk_rejects:
            risk_rejects = None
        if submitted is not None and prev_submitted is not None and float(submitted) > float(prev_submitted):
            active = True
        if canceled is not None and prev_canceled is not None and float(canceled) > float(prev_canceled):
            active = True
        if risk_rejects is not None and prev_risk_rejects is not None and float(risk_rejects) > float(prev_risk_rejects):
            active = True
        if active:
            active_rows += 1
        timeline.append((parse_ts(row.get("ts_utc")), active))
        if submitted is not None:
            prev_submitted = float(submitted)
        if canceled is not None:
            prev_canceled = float(canceled)
        if risk_rejects is not None:
            prev_risk_rejects = float(risk_rejects)

    # Prefer time-weighted occupancy when status timestamps are available.
    if len(timeline) >= 2:
        deltas: List[float] = []
        weighted_total = 0.0
        weighted_active = 0.0
        for idx in range(len(timeline) - 1):
            ts_cur, active_cur = timeline[idx]
            ts_next, _ = timeline[idx + 1]
            if ts_cur is None or ts_next is None:
                continue
            dt_sec = (ts_next - ts_cur).total_seconds()
            if dt_sec <= 0:
                continue
            deltas.append(dt_sec)
            weighted_total += dt_sec
            if active_cur:
                weighted_active += dt_sec
        if weighted_total > 0 and deltas:
            tail_dt = float(median(deltas))
            tail_active = bool(timeline[-1][1])
            weighted_total += tail_dt
            if tail_active:
                weighted_active += tail_dt
            return weighted_active / weighted_total

    return float(active_rows) / float(len(status_rows))


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    clipped = max(0.0, min(1.0, float(q)))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * clipped))
    return float(ordered[idx])


def _counter_delta(current_value: Any, prev_value: Optional[float]) -> Tuple[float, Optional[float]]:
    current = _safe_float(current_value, default=float("nan"))
    if current != current:  # NaN
        return 0.0, prev_value
    current = float(current)
    if prev_value is None:
        return 0.0, current
    if current >= prev_value:
        return max(0.0, current - prev_value), current
    # Counter reset/restart: treat current value as post-reset accumulation.
    return max(0.0, current), current


def _market_data_source_stats(status_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    ws_delta = 0.0
    rest_delta = 0.0
    total_delta = 0.0
    ws_rows = 0.0
    rest_rows = 0.0

    prev_ws: Optional[float] = None
    prev_rest: Optional[float] = None
    prev_total: Optional[float] = None
    for row in status_rows:
        ws_inc, prev_ws = _counter_delta(row.get("counter.book_updates_ws"), prev_ws)
        rest_inc, prev_rest = _counter_delta(row.get("counter.book_updates_rest"), prev_rest)
        total_inc, prev_total = _counter_delta(row.get("counter.book_updates"), prev_total)
        ws_delta += ws_inc
        rest_delta += rest_inc
        total_delta += total_inc
        if ws_inc > 0.0:
            ws_rows += 1.0
        if rest_inc > 0.0:
            rest_rows += 1.0

    source_total = ws_delta + rest_delta
    source_rest_ratio = (rest_delta / source_total) if source_total > 0.0 else 0.0
    return {
        "book_updates_ws_delta": float(ws_delta),
        "book_updates_rest_delta": float(rest_delta),
        "book_updates_total_delta": float(total_delta),
        "book_updates_rest_ratio": float(source_rest_ratio),
        "status_rows_with_ws_updates": float(ws_rows),
        "status_rows_with_rest_updates": float(rest_rows),
    }


def _harness_realism_grade(
    *,
    events: List[Dict[str, Any]],
    edge_truth: Dict[str, Any],
) -> Tuple[int, Dict[str, int]]:
    breakdown: Dict[str, int] = empty_harness_realism_breakdown()

    fill_rows = [evt for evt in events if str(evt.get("event_type") or "") == "fill"]
    if any(
        isinstance(evt.get("paper_liquidity_depth_multiplier"), (int, float))
        and float(evt.get("paper_liquidity_depth_multiplier")) != 1.0
        for evt in fill_rows
    ):
        breakdown["tod_liquidity_scaling"] = 20

    if any(
        str(evt.get("paper_queue_position_mode") or "").strip().lower() == "bounded_top_depth_proxy"
        and isinstance(evt.get("paper_maker_depth_consumption_ratio"), (int, float))
        for evt in fill_rows
    ):
        breakdown["maker_queue_proxy_depth_model"] = 20

    if any(
        str(evt.get("fill_policy_basis") or "").strip().lower()
        in {"bounded_visible_liquidity_top_of_book", "bounded_visible_liquidity_top_of_book_with_queue_proxy"}
        for evt in fill_rows
    ):
        breakdown["taker_depth_slippage_model"] = 20

    lag_rows = [
        evt for evt in fill_rows if str(evt.get("paper_chainlink_lag_class") or "").strip().lower()
    ]
    if lag_rows:
        unknown_fail_closed_ok = True
        for row in lag_rows:
            lag_class = str(row.get("paper_chainlink_lag_class") or "").strip().lower()
            penalty = _safe_float(row.get("paper_chainlink_lag_penalty_bps"), default=0.0)
            if lag_class == "unknown" and abs(float(penalty)) > 1e-9:
                unknown_fail_closed_ok = False
                break
        if unknown_fail_closed_ok:
            breakdown["taker_lag_emulation_with_unknown_guard"] = 20

    if float(_safe_float(edge_truth.get("rows_total"), 0.0)) > 0.0:
        breakdown["truth_surface_completeness"] = 20

    grade = int(sum(int(v) for v in breakdown.values()))
    return grade, breakdown


def _fill_capture_stats(events: List[Dict[str, Any]]) -> Dict[str, float]:
    latest_mid_by_token: Dict[str, float] = {}
    capture = 0.0
    adverse = 0.0
    fills = 0
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "")
            mid = evt.get("midpoint")
            if token_id and isinstance(mid, (int, float)):
                latest_mid_by_token[token_id] = float(mid)
            continue
        if event_type != "fill":
            continue
        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        mid = latest_mid_by_token.get(token_id)
        price = evt.get("price")
        size = evt.get("size")
        if mid is None or not isinstance(price, (int, float)) or not isinstance(size, (int, float)):
            continue
        qty = float(size)
        if side == "BUY":
            delta = (float(mid) - float(price)) * qty
        elif side == "SELL":
            delta = (float(price) - float(mid)) * qty
        else:
            continue
        if delta >= 0:
            capture += delta
        else:
            adverse += abs(delta)
        fills += 1
    immediate_net = capture - adverse
    return {
        "fills_scored": float(fills),
        "immediate_horizon_sec": 0.0,
        "immediate_capture": capture,
        "immediate_adverse_selection": adverse,
        "immediate_capture_minus_adverse": immediate_net,
        # Legacy aliases kept for compatibility; prefer immediate_* fields.
        "realized_capture": capture,
        "adverse_selection": adverse,
        "capture_minus_adverse": immediate_net,
    }


def _taker_stage_net_breakout(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    latest_mid_by_token: Dict[str, float] = {}
    taker_stage_by_order_id: Dict[str, str] = {}
    stage_capture: Counter[str] = Counter()
    stage_adverse: Counter[str] = Counter()
    stage_fills: Counter[str] = Counter()

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "").strip()
            midpoint = evt.get("midpoint")
            if token_id and isinstance(midpoint, (int, float)):
                latest_mid_by_token[token_id] = float(midpoint)
            continue
        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            if not _is_taker_submit_reason(reason):
                continue
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            stage = str(evt.get("stage") or "").strip().upper()
            comp = evt.get("taker_competitiveness")
            if not stage and isinstance(comp, dict):
                stage = str(comp.get("stage") or "").strip().upper()
            if not stage:
                stage = "UNKNOWN"
            taker_stage_by_order_id[order_id] = stage
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if not order_id:
            continue
        stage = taker_stage_by_order_id.get(order_id)
        if not stage:
            continue
        token_id = str(evt.get("token_id") or "").strip()
        side = str(evt.get("side") or "").strip().upper()
        mid = latest_mid_by_token.get(token_id)
        price = evt.get("price")
        size = evt.get("size")
        if mid is None or not isinstance(price, (int, float)) or not isinstance(size, (int, float)):
            continue
        qty = float(size)
        if side == "BUY":
            delta = (float(mid) - float(price)) * qty
        elif side == "SELL":
            delta = (float(price) - float(mid)) * qty
        else:
            continue
        if delta >= 0.0:
            stage_capture[stage] += float(delta)
        else:
            stage_adverse[stage] += abs(float(delta))
        stage_fills[stage] += 1

    out: Dict[str, Dict[str, float]] = {}
    for stage in sorted(set(stage_fills.keys()) | set(stage_capture.keys()) | set(stage_adverse.keys())):
        capture = float(stage_capture.get(stage, 0.0))
        adverse = float(stage_adverse.get(stage, 0.0))
        out[stage] = {
            "fills_scored": float(stage_fills.get(stage, 0)),
            "capture": capture,
            "adverse_selection": adverse,
            "net": capture - adverse,
        }
    return out


def _edge_quality_by_regime(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    order_reason_by_id: Dict[str, str] = {}
    regime = "disarmed"
    taker_submits = Counter()
    taker_fills = Counter()

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "latency_regime_change":
            regime = str(evt.get("state") or regime)
        elif is_taker_submit_event_type(event_type):
            taker_submits[regime] += 1
        elif event_type == "order_submit":
            order_id = str(evt.get("order_id") or "")
            reason = str(evt.get("reason") or "")
            if order_id:
                order_reason_by_id[order_id] = reason
        elif event_type == "fill":
            order_id = str(evt.get("order_id") or "")
            reason = order_reason_by_id.get(order_id, "")
            if _is_taker_submit_reason(reason):
                taker_fills[regime] += 1

    out: Dict[str, Dict[str, float]] = {}
    regimes = set(taker_submits.keys()) | set(taker_fills.keys())
    for name in sorted(regimes):
        submits = float(taker_submits.get(name, 0))
        fills = float(taker_fills.get(name, 0))
        fill_rate = (fills / submits) if submits > 0 else 0.0
        out[name] = {"taker_submits": submits, "taker_fills": fills, "taker_fill_rate": fill_rate}
    return out


def _filter_rows_by_run_id(rows: List[Dict[str, Any]], run_id: Optional[str]) -> List[Dict[str, Any]]:
    if not run_id:
        return rows
    target = str(run_id).strip()
    if not target:
        return rows
    out: List[Dict[str, Any]] = []
    for row in rows:
        value = row.get("run_id")
        if value is None:
            continue
        if str(value) == target:
            out.append(row)
    return out


def _load_contract_scoped_files(
    *,
    contract: Optional[Dict[str, Any]],
    prefix: str,
) -> List[pathlib.Path]:
    if contract is None:
        return []
    stream = {"status": "status", "events": "events", "errors": "errors"}.get(prefix, prefix)
    slice_path = run_contract_slice_path(contract, stream=stream)
    if slice_path is not None:
        return [slice_path]
    key = f"{prefix}_path"
    raw = str(contract.get(key) or "").strip()
    if not raw:
        return []
    path = pathlib.Path(raw).expanduser().resolve()
    if path.exists():
        return [path]
    return []


def _run_duration_minutes(events: List[Dict[str, Any]], status: List[Dict[str, Any]], errors: List[Dict[str, Any]]) -> float:
    ts_values: List[dt.datetime] = []
    for row in (events + status + errors):
        ts = parse_ts(row.get("ts_utc"))
        if ts is not None:
            ts_values.append(ts)
    if len(ts_values) < 2:
        return 0.0
    span = max(ts_values) - min(ts_values)
    return max(0.0, span.total_seconds() / 60.0)


def _stale_data_stats(events: List[Dict[str, Any]]) -> Dict[str, float]:
    stale_book_rejects = 0.0
    stale_oracle_blocks = 0.0
    disarmed_blocks = 0.0
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "risk_reject":
            reason = str(evt.get("reason") or "")
            if "stale_book" in reason:
                stale_book_rejects += 1.0
        elif event_type == "edge_evaluation":
            action = str(evt.get("action_taken") or "").strip().lower()
            if action != "none":
                continue
            reason = str(evt.get("block_reason") or "").strip().lower()
            if "stale" in reason:
                stale_oracle_blocks += 1.0
            if "latency_not_armed" in reason or "token_lag_not_verified" in reason:
                disarmed_blocks += 1.0
    return {
        "stale_book_rejects": stale_book_rejects,
        "stale_oracle_edge_blocks": stale_oracle_blocks,
        "disarmed_edge_blocks": disarmed_blocks,
    }


def _latency_distribution(events: List[Dict[str, Any]]) -> Dict[str, float]:
    lag_ms: List[float] = []
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type not in {"latency_sample", "leadlag_book_move"}:
            continue
        lag = _safe_float(evt.get("reaction_lag_ms", evt.get("lag_ms")), default=-1.0)
        if lag >= 0:
            lag_ms.append(lag)
    if not lag_ms:
        return {
            "sample_count": 0.0,
            "median_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "sample_count": float(len(lag_ms)),
        "median_ms": _percentile(lag_ms, 0.5),
        "p90_ms": _percentile(lag_ms, 0.9),
        "p95_ms": _percentile(lag_ms, 0.95),
        "max_ms": max(lag_ms),
    }


def _taker_summary_stats(events: List[Dict[str, Any]], duration_minutes: float) -> Dict[str, float]:
    taker_order_ids: set[str] = set()
    latest_mid_by_token: Dict[str, float] = {}
    submits = 0.0
    fills = 0.0
    wins = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "")
            midpoint = evt.get("midpoint")
            if token_id and isinstance(midpoint, (int, float)):
                latest_mid_by_token[token_id] = float(midpoint)
            continue
        if event_type == "order_submit" and _is_taker_submit_reason(evt.get("reason")):
            submits += 1.0
            order_id = str(evt.get("order_id") or "")
            if order_id:
                taker_order_ids.add(order_id)
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "")
        if order_id not in taker_order_ids:
            continue
        fills += 1.0
        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        fill_price = _safe_float(evt.get("price"), default=-1.0)
        mid = latest_mid_by_token.get(token_id)
        if fill_price <= 0 or mid is None:
            continue
        if side == "BUY" and mid >= fill_price:
            wins += 1.0
        elif side == "SELL" and mid <= fill_price:
            wins += 1.0

    return {
        "submits": submits,
        "fills": fills,
        "fill_rate": (fills / submits) if submits > 0 else 0.0,
        # Midpoint-relative proxy only; not realized pnl/win attribution truth.
        "midpoint_win_rate_proxy": (wins / fills) if fills > 0 else 0.0,
        "fire_rate_per_min": (submits / duration_minutes) if duration_minutes > 0 else 0.0,
    }


def _execution_path_stats(events: List[Dict[str, Any]], duration_minutes: float) -> Dict[str, float]:
    maker_order_ids: set[str] = set()
    taker_bonus_order_ids: set[str] = set()
    maker_filled_order_ids: set[str] = set()
    taker_bonus_filled_order_ids: set[str] = set()
    maker_submits = 0.0
    taker_bonus_submits = 0.0
    maker_fills = 0.0
    taker_bonus_fills = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            order_id = str(evt.get("order_id") or "")
            if _is_taker_submit_reason(reason):
                taker_bonus_submits += 1.0
                if order_id:
                    taker_bonus_order_ids.add(order_id)
            else:
                maker_submits += 1.0
                if order_id:
                    maker_order_ids.add(order_id)
            continue
        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "")
        if order_id in taker_bonus_order_ids:
            taker_bonus_fills += 1.0
            if order_id:
                taker_bonus_filled_order_ids.add(order_id)
        elif order_id in maker_order_ids:
            maker_fills += 1.0
            if order_id:
                maker_filled_order_ids.add(order_id)

    return {
        "maker_submits": maker_submits,
        "maker_fills": maker_fills,
        "maker_filled_orders": float(len(maker_filled_order_ids)),
        "maker_fill_rate": (float(len(maker_filled_order_ids)) / maker_submits) if maker_submits > 0 else 0.0,
        "maker_fire_rate_per_min": (maker_submits / duration_minutes) if duration_minutes > 0 else 0.0,
        "taker_bonus_submits": taker_bonus_submits,
        "taker_bonus_fills": taker_bonus_fills,
        "taker_bonus_filled_orders": float(len(taker_bonus_filled_order_ids)),
        "taker_bonus_fill_rate": (
            float(len(taker_bonus_filled_order_ids)) / taker_bonus_submits
        )
        if taker_bonus_submits > 0
        else 0.0,
        "taker_bonus_fire_rate_per_min": (taker_bonus_submits / duration_minutes) if duration_minutes > 0 else 0.0,
    }


def _financial_submission_lane(submission_lane: Any, reason: Any) -> str:
    lane = str(submission_lane or "").strip().lower()
    if lane:
        if "taker" in lane:
            return "taker"
        if "maker" in lane:
            return "maker"
        if lane in {"normal", "unknown"}:
            return lane
    reason_text = str(reason or "").strip().lower()
    if _is_taker_submit_reason(reason_text):
        return "taker"
    if reason_text:
        return "maker"
    return "unknown"


def _new_financial_bucket() -> Dict[str, Any]:
    return {
        "submitted_order_count": 0,
        "filled_order_count": 0,
        "filled_trade_candidate_count": 0,
        "fill_event_count": 0,
        "closed_trade_count": 0,
        "winning_trade_count": 0,
        "losing_trade_count": 0,
        "flat_trade_count": 0,
        "gross_submitted_notional_usd": 0.0,
        "gross_submitted_size_shares": 0.0,
        "gross_filled_notional_usd": 0.0,
        "gross_filled_size_shares": 0.0,
        "gross_settlement_notional_usd": 0.0,
        "net_pnl_usd": 0.0,
        "_closed_trade_pnls": [],
    }


def _finalize_financial_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    closed_trade_pnls = list(bucket.pop("_closed_trade_pnls", []))
    submitted_order_count = int(bucket.get("submitted_order_count", 0))
    filled_order_count = int(bucket.get("filled_order_count", 0))
    fill_event_count = int(bucket.get("fill_event_count", 0))
    closed_trade_count = int(bucket.get("closed_trade_count", 0))
    winning_trade_count = int(bucket.get("winning_trade_count", 0))
    losing_trade_count = int(bucket.get("losing_trade_count", 0))
    gross_profit_usd = float(sum(value for value in closed_trade_pnls if value > 1e-9))
    gross_loss_usd = float(sum(value for value in closed_trade_pnls if value < -1e-9))
    bucket["gross_profit_usd"] = gross_profit_usd
    bucket["gross_loss_usd"] = gross_loss_usd
    bucket["win_rate"] = (
        float(winning_trade_count) / float(closed_trade_count)
        if closed_trade_count > 0
        else 0.0
    )
    bucket["loss_rate"] = (
        float(losing_trade_count) / float(closed_trade_count)
        if closed_trade_count > 0
        else 0.0
    )
    bucket["flat_rate"] = (
        float(int(bucket.get("flat_trade_count", 0))) / float(closed_trade_count)
        if closed_trade_count > 0
        else 0.0
    )
    bucket["avg_trade_pnl_usd"] = (
        float(sum(closed_trade_pnls)) / float(closed_trade_count)
        if closed_trade_count > 0
        else 0.0
    )
    bucket["median_trade_pnl_usd"] = float(median(closed_trade_pnls)) if closed_trade_pnls else 0.0
    bucket["avg_win_usd"] = (
        float(gross_profit_usd) / float(winning_trade_count)
        if winning_trade_count > 0
        else 0.0
    )
    bucket["avg_loss_usd"] = (
        float(gross_loss_usd) / float(losing_trade_count)
        if losing_trade_count > 0
        else 0.0
    )
    bucket["avg_submitted_order_notional_usd"] = (
        float(bucket.get("gross_submitted_notional_usd", 0.0)) / float(submitted_order_count)
        if submitted_order_count > 0
        else 0.0
    )
    bucket["avg_submitted_order_size_shares"] = (
        float(bucket.get("gross_submitted_size_shares", 0.0)) / float(submitted_order_count)
        if submitted_order_count > 0
        else 0.0
    )
    bucket["avg_filled_order_notional_usd"] = (
        float(bucket.get("gross_filled_notional_usd", 0.0)) / float(filled_order_count)
        if filled_order_count > 0
        else 0.0
    )
    bucket["avg_filled_order_size_shares"] = (
        float(bucket.get("gross_filled_size_shares", 0.0)) / float(filled_order_count)
        if filled_order_count > 0
        else 0.0
    )
    bucket["avg_fill_event_size_shares"] = (
        float(bucket.get("gross_filled_size_shares", 0.0)) / float(fill_event_count)
        if fill_event_count > 0
        else 0.0
    )
    bucket["expectancy_per_trade_usd"] = bucket["avg_trade_pnl_usd"]
    if gross_loss_usd < -1e-9:
        bucket["profit_factor"] = float(gross_profit_usd / abs(gross_loss_usd))
        bucket["profit_factor_status"] = "defined"
    elif gross_profit_usd > 1e-9:
        bucket["profit_factor"] = None
        bucket["profit_factor_status"] = "no_losses"
    elif closed_trade_count > 0:
        bucket["profit_factor"] = 0.0
        bucket["profit_factor_status"] = "no_gross_profit"
    else:
        bucket["profit_factor"] = None
        bucket["profit_factor_status"] = "no_closed_trades"
    return bucket


def _financial_capital_progression_summary(
    run_manifest: Dict[str, Any],
    status_rows: List[Dict[str, Any]],
    latest_total_pnl_usd: Optional[float],
) -> Dict[str, Any]:
    config = run_manifest.get("config") if isinstance(run_manifest, dict) else {}
    wallet_cfg = config.get("wallet") if isinstance(config, dict) else {}

    configured_base_capital_usd = (
        float(wallet_cfg.get("paper_starting_usdc"))
        if isinstance(wallet_cfg, dict) and isinstance(wallet_cfg.get("paper_starting_usdc"), (int, float))
        else None
    )
    configured_protected_reserve_usd = (
        float(wallet_cfg.get("protected_usdc_reserve"))
        if isinstance(wallet_cfg, dict) and isinstance(wallet_cfg.get("protected_usdc_reserve"), (int, float))
        else None
    )
    configured_starting_deployable_capital_usd = (
        float(configured_base_capital_usd - configured_protected_reserve_usd)
        if isinstance(configured_base_capital_usd, (int, float))
        and isinstance(configured_protected_reserve_usd, (int, float))
        else None
    )

    opening_wallet_ts_utc: Optional[str] = None
    opening_wallet_authority_status_class = "missing"
    opening_wallet_stable_balance_total_usd: Optional[float] = None
    opening_wallet_deployable_capital_usd: Optional[float] = None
    opening_wallet_protected_reserve_usd: Optional[float] = None
    opening_wallet_source = "missing"
    ending_wallet_ts_utc: Optional[str] = None
    ending_wallet_authority_status_class = "missing"
    ending_wallet_stable_balance_total_usd: Optional[float] = None
    ending_wallet_deployable_capital_usd: Optional[float] = None
    ending_wallet_protected_reserve_usd: Optional[float] = None
    ending_wallet_open_reserved_usd: Optional[float] = None
    ending_wallet_source = "missing"

    ordered_status_rows = [
        row
        for _, row in sorted(
            enumerate(status_rows),
            key=lambda pair: (
                parse_ts(pair[1].get("ts_utc")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                pair[0],
            ),
        )
    ]

    for row in ordered_status_rows:
        wallet_contract = row.get("wallet_contract")
        if not isinstance(wallet_contract, dict):
            continue
        opening_wallet_ts_utc = str(row.get("ts_utc") or "").strip() or None
        opening_wallet_authority_status_class = (
            str(wallet_contract.get("authority_status_class") or "unknown").strip().lower() or "unknown"
        )
        opening_wallet_stable_balance_total_usd = (
            float(wallet_contract.get("stable_balance_total"))
            if isinstance(wallet_contract.get("stable_balance_total"), (int, float))
            else None
        )
        opening_wallet_deployable_capital_usd = (
            float(wallet_contract.get("deployable_capital"))
            if isinstance(wallet_contract.get("deployable_capital"), (int, float))
            else None
        )
        opening_wallet_protected_reserve_usd = (
            float(wallet_contract.get("protected_reserve"))
            if isinstance(wallet_contract.get("protected_reserve"), (int, float))
            else None
        )
        opening_wallet_source = "wallet_contract"
        break

    for row in reversed(ordered_status_rows):
        wallet_contract = row.get("wallet_contract")
        if not isinstance(wallet_contract, dict):
            continue
        ending_wallet_ts_utc = str(row.get("ts_utc") or "").strip() or None
        ending_wallet_authority_status_class = (
            str(wallet_contract.get("authority_status_class") or "unknown").strip().lower() or "unknown"
        )
        ending_wallet_stable_balance_total_usd = (
            float(wallet_contract.get("stable_balance_total"))
            if isinstance(wallet_contract.get("stable_balance_total"), (int, float))
            else None
        )
        ending_wallet_deployable_capital_usd = (
            float(wallet_contract.get("deployable_capital"))
            if isinstance(wallet_contract.get("deployable_capital"), (int, float))
            else None
        )
        ending_wallet_protected_reserve_usd = (
            float(wallet_contract.get("protected_reserve"))
            if isinstance(wallet_contract.get("protected_reserve"), (int, float))
            else None
        )
        ending_wallet_open_reserved_usd = (
            float(wallet_contract.get("open_reserved"))
            if isinstance(wallet_contract.get("open_reserved"), (int, float))
            else None
        )
        ending_wallet_source = "wallet_contract"
        break

    opening_wallet_matches_configured_base_capital = (
        bool(abs(opening_wallet_stable_balance_total_usd - configured_base_capital_usd) <= 1e-6)
        if isinstance(opening_wallet_stable_balance_total_usd, (int, float))
        and isinstance(configured_base_capital_usd, (int, float))
        else None
    )
    opening_wallet_matches_configured_deployable_capital = (
        bool(abs(opening_wallet_deployable_capital_usd - configured_starting_deployable_capital_usd) <= 1e-6)
        if isinstance(opening_wallet_deployable_capital_usd, (int, float))
        and isinstance(configured_starting_deployable_capital_usd, (int, float))
        else None
    )
    ending_wallet_minus_opening_stable_balance_usd = (
        float(ending_wallet_stable_balance_total_usd - opening_wallet_stable_balance_total_usd)
        if isinstance(ending_wallet_stable_balance_total_usd, (int, float))
        and isinstance(opening_wallet_stable_balance_total_usd, (int, float))
        else None
    )
    ending_wallet_matches_opening_plus_total_pnl = (
        bool(
            abs(
                ending_wallet_stable_balance_total_usd
                - (opening_wallet_stable_balance_total_usd + latest_total_pnl_usd)
            )
            <= 1e-6
        )
        if isinstance(ending_wallet_stable_balance_total_usd, (int, float))
        and isinstance(opening_wallet_stable_balance_total_usd, (int, float))
        and isinstance(latest_total_pnl_usd, (int, float))
        else None
    )

    return {
        "basis": "run_manifest_wallet_config_with_opening_wallet_contract_crosscheck",
        "configured_base_capital_usd": configured_base_capital_usd,
        "configured_protected_reserve_usd": configured_protected_reserve_usd,
        "configured_starting_deployable_capital_usd": configured_starting_deployable_capital_usd,
        "opening_wallet_ts_utc": opening_wallet_ts_utc,
        "opening_wallet_authority_status_class": opening_wallet_authority_status_class,
        "opening_wallet_stable_balance_total_usd": opening_wallet_stable_balance_total_usd,
        "opening_wallet_deployable_capital_usd": opening_wallet_deployable_capital_usd,
        "opening_wallet_protected_reserve_usd": opening_wallet_protected_reserve_usd,
        "opening_wallet_source": opening_wallet_source,
        "opening_wallet_matches_configured_base_capital": opening_wallet_matches_configured_base_capital,
        "opening_wallet_matches_configured_deployable_capital": (
            opening_wallet_matches_configured_deployable_capital
        ),
        "ending_wallet_ts_utc": ending_wallet_ts_utc,
        "ending_wallet_authority_status_class": ending_wallet_authority_status_class,
        "ending_wallet_stable_balance_total_usd": ending_wallet_stable_balance_total_usd,
        "ending_wallet_deployable_capital_usd": ending_wallet_deployable_capital_usd,
        "ending_wallet_protected_reserve_usd": ending_wallet_protected_reserve_usd,
        "ending_wallet_open_reserved_usd": ending_wallet_open_reserved_usd,
        "ending_wallet_source": ending_wallet_source,
        "ending_wallet_minus_opening_stable_balance_usd": ending_wallet_minus_opening_stable_balance_usd,
        "ending_wallet_matches_opening_plus_total_pnl": ending_wallet_matches_opening_plus_total_pnl,
    }


def _financial_performance_summary(
    events: List[Dict[str, Any]],
    status_rows: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    ordered_events = [
        evt
        for _, evt in sorted(
            enumerate(events),
            key=lambda pair: (
                parse_ts(pair[1].get("ts_utc")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                pair[0],
            ),
        )
    ]
    latest_total_pnl_usd: Optional[float] = None
    latest_total_pnl_ts_utc: Optional[str] = None
    for row in status_rows:
        pnl_value = row.get("gauge.total_pnl")
        if isinstance(pnl_value, (int, float)):
            latest_total_pnl_usd = float(pnl_value)
            latest_total_pnl_ts_utc = str(row.get("ts_utc") or "").strip() or None

    order_meta: Dict[str, Dict[str, Any]] = {}
    campaign_ledger: Dict[str, Dict[str, Any]] = {}
    settlement_gap_event_count = 0
    settlement_gap_shares = 0.0
    settlement_gap_notional_usd = 0.0

    def _campaign_key(*, target_ref: Any, token_id: Any, order_id: str) -> str:
        target = str(target_ref or "").strip()
        token = str(token_id or "").strip()
        if target:
            return f"target_ref:{target}"
        if token and token != "[REDACTED]":
            return f"token:{token}"
        return f"order:{order_id}"

    def _ensure_campaign(key: str) -> Dict[str, Any]:
        return campaign_ledger.setdefault(
            key,
            {
                "campaign_key": key,
                "lane_set": set(),
                "submitted_order_ids": set(),
                "filled_order_ids": set(),
                "submit_ts": None,
                "gross_settlement_notional_usd": 0.0,
                "cashflow_pnl_usd": 0.0,
                "net_shares": 0.0,
            },
        )

    overall_bucket = _new_financial_bucket()
    lane_buckets: Dict[str, Dict[str, Any]] = {
        "maker": _new_financial_bucket(),
        "taker": _new_financial_bucket(),
        "mixed": _new_financial_bucket(),
        "unknown": _new_financial_bucket(),
    }

    for evt in ordered_events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "order_submit":
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            lane = _financial_submission_lane(evt.get("submission_lane"), evt.get("reason"))
            token_id = str(evt.get("token_id") or "").strip()
            campaign_key = _campaign_key(
                target_ref=evt.get("target_ref"),
                token_id=token_id,
                order_id=order_id,
            )
            order_meta[order_id] = {
                "lane": lane,
                "token_id": token_id,
                "campaign_key": campaign_key,
            }
            campaign = _ensure_campaign(campaign_key)
            campaign["lane_set"].add(lane)
            submit_ts = parse_ts(evt.get("ts_utc"))
            if submit_ts is not None:
                if campaign.get("submit_ts") is None or submit_ts < campaign.get("submit_ts"):
                    campaign["submit_ts"] = submit_ts
            if order_id not in campaign["submitted_order_ids"]:
                campaign["submitted_order_ids"].add(order_id)
                overall_bucket["submitted_order_count"] += 1
                lane_buckets[lane]["submitted_order_count"] += 1
                submitted_price = _safe_float(evt.get("price"))
                submitted_size = _safe_float(evt.get("size"))
                if submitted_price > 0.0 and submitted_size > 0.0:
                    submitted_notional_usd = float(submitted_price * submitted_size)
                    overall_bucket["gross_submitted_notional_usd"] += submitted_notional_usd
                    overall_bucket["gross_submitted_size_shares"] += submitted_size
                    lane_buckets[lane]["gross_submitted_notional_usd"] += submitted_notional_usd
                    lane_buckets[lane]["gross_submitted_size_shares"] += submitted_size
            continue

        if event_type == "fill":
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            meta = order_meta.get(order_id, {})
            lane = str(meta.get("lane") or _financial_submission_lane(evt.get("submission_lane"), evt.get("reason")))
            token_id = str(evt.get("token_id") or "").strip()
            campaign_key = str(
                meta.get("campaign_key")
                or _campaign_key(
                    target_ref=evt.get("target_ref"),
                    token_id=token_id,
                    order_id=order_id,
                )
            )
            order_meta[order_id] = {
                "lane": lane,
                "token_id": token_id,
                "campaign_key": campaign_key,
            }
            campaign = _ensure_campaign(campaign_key)
            campaign["lane_set"].add(lane)
            side = str(evt.get("side") or "").strip().upper()
            price = _safe_float(evt.get("price"))
            size = _safe_float(evt.get("size"))
            if side not in {"BUY", "SELL"} or price <= 0.0 or size <= 0.0:
                continue
            notional = float(price * size)
            if order_id not in campaign["filled_order_ids"]:
                campaign["filled_order_ids"].add(order_id)
                overall_bucket["filled_order_count"] += 1
                lane_buckets[lane]["filled_order_count"] += 1
            overall_bucket["fill_event_count"] += 1
            lane_buckets[lane]["fill_event_count"] += 1
            overall_bucket["gross_filled_notional_usd"] += notional
            overall_bucket["gross_filled_size_shares"] += size
            lane_buckets[lane]["gross_filled_notional_usd"] += notional
            lane_buckets[lane]["gross_filled_size_shares"] += size
            campaign["cashflow_pnl_usd"] += (-notional if side == "BUY" else notional)
            campaign["net_shares"] += (size if side == "BUY" else -size)
            continue

        if event_type != "wallet_position_settled":
            continue
        settlement_side = str(evt.get("settlement_side") or "").strip().upper()
        settlement_size = _safe_float(evt.get("settlement_size_shares"))
        settlement_price = _safe_float(evt.get("settlement_price"))
        if (
            settlement_side not in {"BUY", "SELL"}
            or settlement_size <= 0.0
            or settlement_price < 0.0
        ):
            continue

        matching_campaigns = [
            campaign
            for campaign in campaign_ledger.values()
            if (
                (settlement_side == "SELL" and float(campaign.get("net_shares", 0.0)) > 1e-9)
                or (settlement_side == "BUY" and float(campaign.get("net_shares", 0.0)) < -1e-9)
            )
        ]
        exact_matches = [
            campaign
            for campaign in matching_campaigns
            if abs(abs(float(campaign.get("net_shares", 0.0))) - float(settlement_size)) <= 1e-6
        ]
        matched_campaign: Optional[Dict[str, Any]] = None
        if len(exact_matches) == 1:
            matched_campaign = exact_matches[0]
        elif len(exact_matches) == 0 and len(matching_campaigns) == 1:
            candidate = matching_campaigns[0]
            if abs(abs(float(candidate.get("net_shares", 0.0))) - float(settlement_size)) <= 1e-3:
                matched_campaign = candidate
        if matched_campaign is None:
            settlement_gap_event_count += 1
            settlement_gap_shares += float(settlement_size)
            settlement_gap_notional_usd += float(settlement_size * settlement_price)
            continue
        settlement_notional = float(settlement_size * settlement_price)
        matched_campaign["gross_settlement_notional_usd"] += settlement_notional
        matched_campaign["cashflow_pnl_usd"] += (
            settlement_notional if settlement_side == "SELL" else -settlement_notional
        )
        if settlement_side == "SELL":
            matched_campaign["net_shares"] = float(matched_campaign.get("net_shares", 0.0) - settlement_size)
        else:
            matched_campaign["net_shares"] = float(matched_campaign.get("net_shares", 0.0) + settlement_size)

    unresolved_campaigns: List[str] = []
    for campaign_key, campaign in sorted(campaign_ledger.items(), key=lambda item: item[0]):
        lane_set = {str(value or "unknown") for value in campaign.get("lane_set", set())}
        lane_set.discard("")
        if lane_set == {"maker"}:
            lane = "maker"
        elif lane_set == {"taker"}:
            lane = "taker"
        elif not lane_set:
            lane = "unknown"
        else:
            lane = "mixed"

        campaign_pnl = float(campaign.get("cashflow_pnl_usd", 0.0))
        settlement_notional_usd = float(campaign.get("gross_settlement_notional_usd", 0.0))
        overall_bucket["filled_trade_candidate_count"] += 1
        lane_buckets[lane]["filled_trade_candidate_count"] += 1
        overall_bucket["gross_settlement_notional_usd"] += settlement_notional_usd
        lane_buckets[lane]["gross_settlement_notional_usd"] += settlement_notional_usd
        overall_bucket["net_pnl_usd"] += campaign_pnl
        lane_buckets[lane]["net_pnl_usd"] += campaign_pnl
        if abs(float(campaign.get("net_shares", 0.0))) > 1e-9:
            unresolved_campaigns.append(str(campaign_key))
            continue
        overall_bucket["closed_trade_count"] += 1
        lane_buckets[lane]["closed_trade_count"] += 1
        overall_bucket["_closed_trade_pnls"].append(campaign_pnl)
        lane_buckets[lane]["_closed_trade_pnls"].append(campaign_pnl)
        if campaign_pnl > 1e-9:
            overall_bucket["winning_trade_count"] += 1
            lane_buckets[lane]["winning_trade_count"] += 1
        elif campaign_pnl < -1e-9:
            overall_bucket["losing_trade_count"] += 1
            lane_buckets[lane]["losing_trade_count"] += 1
        else:
            overall_bucket["flat_trade_count"] += 1
            lane_buckets[lane]["flat_trade_count"] += 1

    overall_bucket = _finalize_financial_bucket(overall_bucket)
    finalized_lanes = {lane: _finalize_financial_bucket(bucket) for lane, bucket in lane_buckets.items()}
    overall_bucket["closed_trade_coverage_ratio"] = (
        float(overall_bucket.get("closed_trade_count", 0))
        / float(overall_bucket.get("filled_trade_candidate_count", 0))
        if int(overall_bucket.get("filled_trade_candidate_count", 0)) > 0
        else 0.0
    )
    for lane_bucket in finalized_lanes.values():
        lane_bucket["closed_trade_coverage_ratio"] = (
            float(lane_bucket.get("closed_trade_count", 0))
            / float(lane_bucket.get("filled_trade_candidate_count", 0))
            if int(lane_bucket.get("filled_trade_candidate_count", 0)) > 0
            else 0.0
        )

    reconciliation_gap_usd: Optional[float] = None
    reconciled_with_status_total_pnl: Optional[bool] = None
    if latest_total_pnl_usd is not None:
        reconciliation_gap_usd = float(latest_total_pnl_usd - float(overall_bucket.get("net_pnl_usd", 0.0)))
        reconciled_with_status_total_pnl = bool(abs(reconciliation_gap_usd) <= 1e-6)

    return {
        "ledger_version": 2,
        "accounting_basis": "fill_cashflow_plus_wallet_position_settled",
        "win_rate_basis": "closed_target_ref_campaigns_with_zero_remaining_position",
        "lane_mapping_basis": "order_submit.submission_lane_with_reason_fallback",
        "capital_progression": _financial_capital_progression_summary(
            run_manifest,
            status_rows,
            latest_total_pnl_usd,
        ),
        "overall": overall_bucket,
        "by_lane": finalized_lanes,
        "latest_total_pnl_usd": latest_total_pnl_usd,
        "latest_total_pnl_ts_utc": latest_total_pnl_ts_utc,
        "reconciliation_to_status_total_pnl_usd": reconciliation_gap_usd,
        "reconciled_with_status_total_pnl": reconciled_with_status_total_pnl,
        "closed_trade_unit": "target_ref_campaign",
        "unresolved_campaign_count": int(len(unresolved_campaigns)),
        "unresolved_campaign_ids": list(unresolved_campaigns),
        "settlement_allocation_gap_event_count": int(settlement_gap_event_count),
        "settlement_allocation_gap_shares": float(settlement_gap_shares),
        "settlement_allocation_gap_notional_usd": float(settlement_gap_notional_usd),
    }


def _wallet_authority_stats(status_rows: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    event_counts: Counter[str] = Counter()
    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type.startswith("wallet_"):
            event_counts[event_type] += 1

    latest_contract: Dict[str, Any] = {}
    contract_surface_source = "missing"
    fallback_used = False
    for row in reversed(status_rows):
        contract = row.get("wallet_contract")
        if isinstance(contract, dict):
            latest_contract = dict(contract)
            contract_surface_source = "wallet_contract"
            break
        if "wallet_health_ok" in row:
            # Legacy-only fallback for older status rows that predate wallet_contract.
            # This reconstructed surface is intentionally non-authoritative and must
            # never be treated as readiness truth for order permission.
            latest_contract = {
                "gas_balance": _safe_float(row.get("wallet_gas_balance")),
                "gas_reserve_min": _safe_float(row.get("wallet_gas_reserve_min")),
                "gas_ok": bool(row.get("wallet_gas_ok", False)),
                "stable_balance_total": _safe_float(row.get("wallet_stable_balance_total")),
                "protected_reserve": _safe_float(row.get("wallet_protected_reserve")),
                "open_reserved": _safe_float(row.get("wallet_open_reserved")),
                "deployable_capital": _safe_float(row.get("wallet_deployable_capital")),
                "approval_ok": bool(row.get("wallet_approval_ok", False)),
                "nonce_ok": bool(row.get("wallet_nonce_ok", False)),
                "reconcile_ok": bool(row.get("wallet_reconcile_ok", False)),
                "wallet_health_ok": bool(row.get("wallet_health_ok", False)),
                "wallet_health_reasons": list(row.get("wallet_health_reasons", [])),
                "authority_status_class": "legacy_fallback_non_authoritative",
                "order_capable_live": False,
                "order_submit_eligible": False,
                "canonical_live_nonce_available": False,
                "canonical_live_pending_wallet_tx_available": False,
                "live_truth_gap_reasons": ["legacy_wallet_contract_fallback_reconstructed_surface"],
                "reservation_mismatch_candidate": False,
                "reservation_mismatch_delta_usdc": 0.0,
                "reservation_mismatch_detail": "legacy_wallet_contract_fallback_reconstructed_surface",
            }
            contract_surface_source = "legacy_reconstructed_wallet_surface"
            fallback_used = True
            break

    return {
        "event_counts": dict(sorted(event_counts.items(), key=lambda kv: kv[0])),
        "latest_contract": latest_contract,
        "wallet_contract_surface_source": contract_surface_source,
        "legacy_fallback_used": bool(fallback_used),
        "authoritative_wallet_contract_present": bool(contract_surface_source == "wallet_contract"),
        "authority_status_class": str(latest_contract.get("authority_status_class") or "unknown"),
        "order_capable_live": bool(latest_contract.get("order_capable_live", False)),
        "order_submit_eligible": bool(latest_contract.get("order_submit_eligible", False)),
        "canonical_live_nonce_available": bool(latest_contract.get("canonical_live_nonce_available", False)),
        "canonical_live_pending_wallet_tx_available": bool(
            latest_contract.get("canonical_live_pending_wallet_tx_available", False)
        ),
        "live_truth_gap_reasons": list(latest_contract.get("live_truth_gap_reasons") or []),
        "reservation_mismatch_candidate": bool(latest_contract.get("reservation_mismatch_candidate", False)),
        "reservation_mismatch_delta_usdc": _safe_float(latest_contract.get("reservation_mismatch_delta_usdc")),
        "reservation_mismatch_detail": str(latest_contract.get("reservation_mismatch_detail") or ""),
        "bootstrap_non_authoritative": bool(
            str(latest_contract.get("authority_status_class") or "").strip().lower() == "bootstrap_non_authoritative"
        ),
    }


def _valuation_truth_stats(
    status_rows: List[Dict[str, Any]],
    event_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    def _reason_family(reason: Any) -> str:
        text = str(reason or "").strip()
        if not text:
            return "unknown"
        family = text.split(":", 1)[0].strip().lower()
        return family or "unknown"

    def _dominant_counter_key(counter_like: Dict[str, Any]) -> str:
        best_key = "none"
        best_value = 0.0
        for key, raw in sorted(counter_like.items(), key=lambda kv: kv[0]):
            value = _safe_float(raw)
            if value > best_value:
                best_key = str(key or "").strip() or "unknown"
                best_value = value
        return best_key if best_value > 0.0 else "none"

    degraded_rows = 0.0
    hard_degraded_rows = 0.0
    pnl_degraded_rows = 0.0
    loss_guard_degraded_rows = 0.0
    held_unpriceable_escalation_rows = 0.0
    held_unpriceable_defect_candidate_rows = 0.0
    held_book_not_found_404_rows = 0.0
    preexpiry_404_anomaly_rows = 0.0
    held_dust_shadow_candidate_rows = 0.0
    held_dust_shadow_active_rows = 0.0
    held_dust_enforced_rows = 0.0
    valuation_hard_degraded_enter_count = 0.0
    valuation_hard_degraded_clear_count = 0.0
    held_unpriceable_started_count = 0.0
    held_unpriceable_recovered_count = 0.0
    preexpiry_404_anomaly_count = 0.0
    lifecycle_context_mismatch_count = 0.0
    lifecycle_context_missing_sec_to_expiry_count = 0.0
    preexpiry_emergency_taker_attempt_count = 0.0
    preexpiry_emergency_taker_fill_count = 0.0
    preexpiry_emergency_taker_block_count = 0.0
    held_dust_hard_degraded_exempt_count = 0.0
    held_dust_count_max = 0.0
    held_dust_quarantined_count_max = 0.0
    held_dust_total_notional_upper_bound_usd_max = 0.0
    source_totals = {
        "live_mid": 0.0,
        "live_side_conservative_quote": 0.0,
        "last_known_mid": 0.0,
        "conservative_bound_hard_degraded": 0.0,
        "hard_degraded": 0.0,
    }
    degraded_source_totals = {
        "live_mid": 0.0,
        "live_side_conservative_quote": 0.0,
        "last_known_mid": 0.0,
        "conservative_bound_hard_degraded": 0.0,
        "hard_degraded": 0.0,
    }
    held_unpriceable_cause_counts_run: Counter[str] = Counter()
    valuation_degraded_reason_family_counts_run: Counter[str] = Counter()
    preexpiry_emergency_taker_block_reasons_run_max: Counter[str] = Counter()
    latest: Dict[str, Any] = {}
    for row in status_rows:
        row_valuation_degraded = bool(row.get("valuation_degraded", False))
        if row_valuation_degraded:
            degraded_rows += 1.0
        if bool(row.get("valuation_hard_degraded", False)):
            hard_degraded_rows += 1.0
        if bool(row.get("pnl_degraded", False)):
            pnl_degraded_rows += 1.0
        if bool(row.get("loss_guard_degraded", False)):
            loss_guard_degraded_rows += 1.0
        if bool(row.get("held_unpriceable_escalation_active", False)):
            held_unpriceable_escalation_rows += 1.0
        if bool(row.get("held_unpriceable_defect_candidate", False)):
            held_unpriceable_defect_candidate_rows += 1.0
        degraded_reasons = [str(reason) for reason in list(row.get("valuation_degraded_reasons") or [])]
        for reason in degraded_reasons:
            valuation_degraded_reason_family_counts_run[_reason_family(reason)] += 1
        if any("held_book_not_found_404" in reason for reason in degraded_reasons):
            held_book_not_found_404_rows += 1.0
        if bool(row.get("preexpiry_404_anomaly_active", False)):
            preexpiry_404_anomaly_rows += 1.0
        if bool(row.get("held_dust_shadow_candidate_active", False)):
            held_dust_shadow_candidate_rows += 1.0
        if bool(row.get("held_dust_shadow_active", False)):
            held_dust_shadow_active_rows += 1.0
        if bool(row.get("held_dust_enforced_this_cycle", False)):
            held_dust_enforced_rows += 1.0
        valuation_hard_degraded_enter_count = max(
            valuation_hard_degraded_enter_count,
            _safe_float(row.get("valuation_hard_degraded_enter_count")),
        )
        valuation_hard_degraded_clear_count = max(
            valuation_hard_degraded_clear_count,
            _safe_float(row.get("valuation_hard_degraded_clear_count")),
        )
        held_unpriceable_started_count = max(
            held_unpriceable_started_count,
            _safe_float(row.get("held_unpriceable_started_count")),
        )
        held_unpriceable_recovered_count = max(
            held_unpriceable_recovered_count,
            _safe_float(row.get("held_unpriceable_recovered_count")),
        )
        preexpiry_404_anomaly_count = max(
            preexpiry_404_anomaly_count,
            _safe_float(row.get("preexpiry_404_anomaly_count")),
        )
        lifecycle_context_mismatch_count = max(
            lifecycle_context_mismatch_count,
            _safe_float(row.get("lifecycle_context_mismatch_count")),
        )
        lifecycle_context_missing_sec_to_expiry_count = max(
            lifecycle_context_missing_sec_to_expiry_count,
            _safe_float(row.get("lifecycle_context_missing_sec_to_expiry_count")),
        )
        preexpiry_emergency_taker_attempt_count = max(
            preexpiry_emergency_taker_attempt_count,
            _safe_float(row.get("preexpiry_emergency_taker_attempt_count")),
        )
        preexpiry_emergency_taker_fill_count = max(
            preexpiry_emergency_taker_fill_count,
            _safe_float(row.get("preexpiry_emergency_taker_fill_count")),
        )
        preexpiry_emergency_taker_block_count = max(
            preexpiry_emergency_taker_block_count,
            _safe_float(row.get("preexpiry_emergency_taker_block_count")),
        )
        held_dust_hard_degraded_exempt_count = max(
            held_dust_hard_degraded_exempt_count,
            _safe_float(row.get("held_dust_hard_degraded_exempt_count")),
        )
        held_dust_count_max = max(
            held_dust_count_max,
            _safe_float(row.get("held_dust_count")),
        )
        held_dust_quarantined_count_max = max(
            held_dust_quarantined_count_max,
            _safe_float(row.get("held_dust_quarantined_count")),
        )
        held_dust_total_notional_upper_bound_usd_max = max(
            held_dust_total_notional_upper_bound_usd_max,
            _safe_float(row.get("held_dust_total_notional_upper_bound_usd")),
        )
        row_counts_raw = row.get("valuation_mid_source_counts")
        row_counts = row_counts_raw if isinstance(row_counts_raw, dict) else {}
        live_mid = _safe_float(row_counts.get("live_mid", row_counts.get("fresh_live_mid")))
        live_side = _safe_float(
            row_counts.get(
                "live_side_conservative_quote",
                row_counts.get("fresh_live_side_conservative_quote"),
            )
        )
        last_known = _safe_float(row_counts.get("last_known_mid", row_counts.get("fresh_last_known_mid")))
        conservative = _safe_float(
            row_counts.get(
                "conservative_bound_hard_degraded",
                row_counts.get("conservative_bound"),
            )
        )
        hard = _safe_float(row_counts.get("hard_degraded", conservative))
        source_totals["live_mid"] += live_mid
        source_totals["live_side_conservative_quote"] += live_side
        source_totals["last_known_mid"] += last_known
        source_totals["conservative_bound_hard_degraded"] += conservative
        source_totals["hard_degraded"] += hard
        if row_valuation_degraded:
            degraded_source_totals["live_mid"] += live_mid
            degraded_source_totals["live_side_conservative_quote"] += live_side
            degraded_source_totals["last_known_mid"] += last_known
            degraded_source_totals["conservative_bound_hard_degraded"] += conservative
            degraded_source_totals["hard_degraded"] += hard
        row_cause_counts_raw = row.get("held_unpriceable_cause_counts")
        row_cause_counts = row_cause_counts_raw if isinstance(row_cause_counts_raw, dict) else {}
        for cause, count in row_cause_counts.items():
            cause_name = str(cause or "").strip()
            if not cause_name:
                continue
            held_unpriceable_cause_counts_run[cause_name] += _safe_float(count)
        row_emergency_block_reasons_raw = row.get("preexpiry_emergency_taker_block_reasons")
        row_emergency_block_reasons = (
            row_emergency_block_reasons_raw if isinstance(row_emergency_block_reasons_raw, dict) else {}
        )
        for reason, count in row_emergency_block_reasons.items():
            reason_name = str(reason or "").strip().lower()
            if not reason_name:
                continue
            preexpiry_emergency_taker_block_reasons_run_max[reason_name] = max(
                preexpiry_emergency_taker_block_reasons_run_max.get(reason_name, 0.0),
                _safe_float(count),
            )
    # Fallback signal path: when status-row counters are unavailable/truncated,
    # derive emergency unwind counters and block reasons directly from events.
    if event_rows:
        event_attempt_count = 0.0
        event_fill_count = 0.0
        event_block_count = 0.0
        event_block_reasons: Counter[str] = Counter()
        for evt in event_rows:
            if str(evt.get("event_type") or "").strip() != "preexpiry_emergency_taker_unwind":
                continue
            event_weight = max(1.0, _safe_float(evt.get("repeat_count_delta"), 1.0))
            event_attempt_count += event_weight
            outcome = str(evt.get("outcome") or "").strip().lower()
            if outcome == "filled":
                event_fill_count += event_weight
            elif outcome == "blocked":
                event_block_count += event_weight
                reason_name = (
                    str(
                        evt.get("blocked_reason")
                        or evt.get("taker_submit_reject_reason")
                        or evt.get("reason")
                        or evt.get("outcome_reason")
                        or "unknown"
                    )
                    .strip()
                    .lower()
                )
                if reason_name.startswith("blocked_"):
                    reason_name = reason_name[len("blocked_") :]
                if not reason_name:
                    reason_name = "unknown"
                event_block_reasons[reason_name] += int(event_weight)
        preexpiry_emergency_taker_attempt_count = max(preexpiry_emergency_taker_attempt_count, event_attempt_count)
        preexpiry_emergency_taker_fill_count = max(preexpiry_emergency_taker_fill_count, event_fill_count)
        preexpiry_emergency_taker_block_count = max(preexpiry_emergency_taker_block_count, event_block_count)
        for reason_name, reason_count in event_block_reasons.items():
            preexpiry_emergency_taker_block_reasons_run_max[reason_name] = max(
                preexpiry_emergency_taker_block_reasons_run_max.get(reason_name, 0.0),
                float(reason_count),
            )
    for row in reversed(status_rows):
        if "valuation_degraded" in row:
            latest = dict(row)
            break
    sample_count = float(len(status_rows))
    held_unpriceable_unrecovered_raw_count = max(
        0.0,
        float(held_unpriceable_started_count) - float(held_unpriceable_recovered_count),
    )
    latest_held_unpriceable_token_ids = [
        str(token_id)
        for token_id in list(latest.get("held_unpriceable_token_ids") or [])
        if str(token_id).strip()
    ]
    latest_valuation_degraded_reasons = [str(reason) for reason in list(latest.get("valuation_degraded_reasons") or [])]
    latest_valuation_degraded_reason_family_counts = Counter(
        _reason_family(reason) for reason in latest_valuation_degraded_reasons
    )
    latest_held_dust_token_ids = [
        str(token_id)
        for token_id in list(latest.get("held_dust_token_ids") or [])
        if str(token_id).strip()
    ]
    latest_held_dust_exempt_count = _safe_float(
        latest.get("held_dust_hard_degraded_exempt_count")
    )
    latest_dust_exemption_active = bool(
        latest.get("dust_classifier_enforce_enabled", False)
        and latest.get("held_dust_enforced_this_cycle", False)
        and latest_held_dust_exempt_count > 0.0
    )
    latest_dust_exempted_unpriceable_token_count = 0.0
    if latest_dust_exemption_active:
        latest_dust_exempted_unpriceable_token_count = float(
            len(set(latest_held_unpriceable_token_ids).intersection(latest_held_dust_token_ids))
        )
    held_unpriceable_unrecovered_dust_exempted_count = min(
        held_unpriceable_unrecovered_raw_count,
        latest_held_dust_exempt_count,
        latest_dust_exempted_unpriceable_token_count,
    )
    held_unpriceable_unrecovered_meaningful_count = max(
        0.0,
        held_unpriceable_unrecovered_raw_count - held_unpriceable_unrecovered_dust_exempted_count,
    )
    if degraded_rows <= 0.0 and valuation_hard_degraded_enter_count <= 0.0 and held_unpriceable_started_count <= 0.0:
        valuation_bruise_state = "none"
    elif held_unpriceable_unrecovered_meaningful_count > 0.0:
        valuation_bruise_state = "open_meaningful_unpriceable"
    elif held_unpriceable_unrecovered_raw_count > 0.0:
        valuation_bruise_state = "open_dust_only_unpriceable"
    elif valuation_hard_degraded_enter_count > valuation_hard_degraded_clear_count:
        valuation_bruise_state = "hard_degraded_not_fully_cleared"
    elif held_unpriceable_started_count > held_unpriceable_recovered_count:
        valuation_bruise_state = "held_unpriceable_not_fully_recovered"
    else:
        valuation_bruise_state = "recovered_clean"
    return {
        "status_rows": sample_count,
        "valuation_degraded_rows": float(degraded_rows),
        "valuation_hard_degraded_rows": float(hard_degraded_rows),
        "pnl_degraded_rows": float(pnl_degraded_rows),
        "loss_guard_degraded_rows": float(loss_guard_degraded_rows),
        "held_unpriceable_escalation_rows": float(held_unpriceable_escalation_rows),
        "held_unpriceable_defect_candidate_rows": float(held_unpriceable_defect_candidate_rows),
        "held_book_not_found_404_rows": float(held_book_not_found_404_rows),
        "preexpiry_404_anomaly_rows": float(preexpiry_404_anomaly_rows),
        "held_dust_shadow_candidate_rows": float(held_dust_shadow_candidate_rows),
        "held_dust_shadow_active_rows": float(held_dust_shadow_active_rows),
        "held_dust_enforced_rows": float(held_dust_enforced_rows),
        "valuation_hard_degraded_enter_count": float(valuation_hard_degraded_enter_count),
        "valuation_hard_degraded_clear_count": float(valuation_hard_degraded_clear_count),
        "held_unpriceable_started_count": float(held_unpriceable_started_count),
        "held_unpriceable_recovered_count": float(held_unpriceable_recovered_count),
        "held_unpriceable_unrecovered_raw_count": float(held_unpriceable_unrecovered_raw_count),
        "held_unpriceable_unrecovered_dust_exempted_count": float(
            held_unpriceable_unrecovered_dust_exempted_count
        ),
        "held_unpriceable_unrecovered_meaningful_count": float(
            held_unpriceable_unrecovered_meaningful_count
        ),
        "preexpiry_404_anomaly_count": float(preexpiry_404_anomaly_count),
        "lifecycle_context_mismatch_count": float(lifecycle_context_mismatch_count),
        "lifecycle_context_missing_sec_to_expiry_count": float(
            lifecycle_context_missing_sec_to_expiry_count
        ),
        "preexpiry_emergency_taker_attempt_count": float(preexpiry_emergency_taker_attempt_count),
        "preexpiry_emergency_taker_fill_count": float(preexpiry_emergency_taker_fill_count),
        "preexpiry_emergency_taker_block_count": float(preexpiry_emergency_taker_block_count),
        "held_dust_hard_degraded_exempt_count": float(held_dust_hard_degraded_exempt_count),
        "held_dust_count_max": float(held_dust_count_max),
        "held_dust_quarantined_count_max": float(held_dust_quarantined_count_max),
        "held_dust_total_notional_upper_bound_usd_max": float(held_dust_total_notional_upper_bound_usd_max),
        "valuation_degraded_ratio": (degraded_rows / sample_count) if sample_count > 0 else 0.0,
        "valuation_hard_degraded_ratio": (hard_degraded_rows / sample_count) if sample_count > 0 else 0.0,
        "held_unpriceable_escalation_ratio": (
            held_unpriceable_escalation_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "held_unpriceable_defect_candidate_ratio": (
            held_unpriceable_defect_candidate_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "held_book_not_found_404_ratio": (
            held_book_not_found_404_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "preexpiry_404_anomaly_ratio": (
            preexpiry_404_anomaly_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "held_dust_shadow_candidate_ratio": (
            held_dust_shadow_candidate_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "held_dust_shadow_active_ratio": (
            held_dust_shadow_active_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "held_dust_enforced_ratio": (
            held_dust_enforced_rows / sample_count
        )
        if sample_count > 0
        else 0.0,
        "latest_valuation_degraded": bool(latest.get("valuation_degraded", False)),
        "latest_valuation_hard_degraded": bool(latest.get("valuation_hard_degraded", False)),
        "latest_valuation_raw_degraded": bool(latest.get("valuation_raw_degraded", False)),
        "latest_valuation_raw_hard_degraded": bool(latest.get("valuation_raw_hard_degraded", False)),
        "latest_pnl_degraded": bool(latest.get("pnl_degraded", False)),
        "latest_loss_guard_degraded": bool(latest.get("loss_guard_degraded", False)),
        "latest_valuation_degraded_reasons": list(latest_valuation_degraded_reasons),
        "latest_valuation_mid_source_counts": dict(latest.get("valuation_mid_source_counts") or {}),
        "latest_valuation_degraded_reason_family_counts": dict(
            sorted(latest_valuation_degraded_reason_family_counts.items(), key=lambda kv: kv[0])
        ),
        "latest_held_unpriceable_escalation_active": bool(latest.get("held_unpriceable_escalation_active", False)),
        "latest_held_unpriceable_token_count": _safe_float(
            latest.get("held_unpriceable_token_count", latest.get("held_unpriceable_count"))
        ),
        "latest_held_unpriceable_token_ids": list(latest_held_unpriceable_token_ids),
        "latest_held_unpriceable_defect_candidate": bool(latest.get("held_unpriceable_defect_candidate", False)),
        "latest_held_unpriceable_escalation_token_ids": list(latest.get("held_unpriceable_escalation_token_ids") or []),
        "latest_held_unpriceable_dust_exempted_escalation_token_ids": list(
            latest.get("held_unpriceable_dust_exempted_escalation_token_ids") or []
        ),
        "latest_held_unpriceable_meaningful_escalation_token_ids": list(
            latest.get("held_unpriceable_meaningful_escalation_token_ids") or []
        ),
        "latest_held_unpriceable_escalation_reasons": list(latest.get("held_unpriceable_escalation_reasons") or []),
        "latest_held_unpriceable_operator_action": str(latest.get("held_unpriceable_operator_action") or ""),
        "latest_held_unpriceable_escalation_threshold_sec": _safe_float(
            latest.get("held_unpriceable_escalation_threshold_sec")
        ),
        "latest_held_unpriceable_escalation_max_age_sec": _safe_float(
            latest.get("held_unpriceable_escalation_max_age_sec")
        ),
        "latest_valuation_hard_degraded_enter_count": _safe_float(
            latest.get("valuation_hard_degraded_enter_count")
        ),
        "latest_valuation_hard_degraded_clear_count": _safe_float(
            latest.get("valuation_hard_degraded_clear_count")
        ),
        "latest_held_unpriceable_started_count": _safe_float(
            latest.get("held_unpriceable_started_count")
        ),
        "latest_held_unpriceable_recovered_count": _safe_float(
            latest.get("held_unpriceable_recovered_count")
        ),
        "latest_preexpiry_404_anomaly_active": bool(latest.get("preexpiry_404_anomaly_active", False)),
        "latest_preexpiry_404_anomaly_count": _safe_float(
            latest.get("preexpiry_404_anomaly_count")
        ),
        "latest_lifecycle_context_mismatch_count": _safe_float(
            latest.get("lifecycle_context_mismatch_count")
        ),
        "latest_lifecycle_context_missing_sec_to_expiry_count": _safe_float(
            latest.get("lifecycle_context_missing_sec_to_expiry_count")
        ),
        "latest_preexpiry_emergency_taker_attempt_count": _safe_float(
            latest.get("preexpiry_emergency_taker_attempt_count")
        ),
        "latest_preexpiry_emergency_taker_fill_count": _safe_float(
            latest.get("preexpiry_emergency_taker_fill_count")
        ),
        "latest_preexpiry_emergency_taker_block_count": _safe_float(
            latest.get("preexpiry_emergency_taker_block_count")
        ),
        "latest_preexpiry_emergency_taker_block_reasons": dict(
            latest.get("preexpiry_emergency_taker_block_reasons") or {}
        ),
        "latest_held_dust_shadow_candidate_active": bool(latest.get("held_dust_shadow_candidate_active", False)),
        "latest_held_dust_shadow_active": bool(latest.get("held_dust_shadow_active", False)),
        "latest_held_dust_enforced_this_cycle": bool(latest.get("held_dust_enforced_this_cycle", False)),
        "latest_held_dust_hard_degraded_exempt_count": _safe_float(
            latest.get("held_dust_hard_degraded_exempt_count")
        ),
        "latest_held_dust_count": _safe_float(latest.get("held_dust_count")),
        "latest_held_dust_quarantined_count": _safe_float(latest.get("held_dust_quarantined_count")),
        "latest_held_dust_total_notional_upper_bound_usd": _safe_float(
            latest.get("held_dust_total_notional_upper_bound_usd")
        ),
        "latest_dust_classifier_enforce_enabled": bool(latest.get("dust_classifier_enforce_enabled", False)),
        "latest_runtime_expiry_boundary_epsilon_sec": _safe_float(
            latest.get("runtime_expiry_boundary_epsilon_sec")
        ),
        "valuation_bruise_state": valuation_bruise_state,
        "valuation_dominant_reason_family_run": _dominant_counter_key(valuation_degraded_reason_family_counts_run),
        "valuation_dominant_held_unpriceable_cause_run": _dominant_counter_key(held_unpriceable_cause_counts_run),
        "valuation_dominant_source_degraded_rows": _dominant_counter_key(degraded_source_totals),
        "valuation_degraded_reason_family_counts_run": dict(
            sorted(valuation_degraded_reason_family_counts_run.items(), key=lambda kv: kv[0])
        ),
        "valuation_source_counts_run": dict(source_totals),
        "valuation_source_counts_degraded_rows": dict(degraded_source_totals),
        "held_unpriceable_cause_counts_run": dict(sorted(held_unpriceable_cause_counts_run.items(), key=lambda kv: kv[0])),
        "held_unpriceable_cause_counts_latest": dict(latest.get("held_unpriceable_cause_counts") or {}),
        "preexpiry_emergency_taker_block_reasons_run_max": dict(
            sorted(preexpiry_emergency_taker_block_reasons_run_max.items(), key=lambda kv: kv[0])
        ),
        "preexpiry_emergency_taker_block_reason_counts": dict(
            sorted(preexpiry_emergency_taker_block_reasons_run_max.items(), key=lambda kv: kv[0])
        ),
        "valuation_source_counts_latest": {
            "live_mid": _safe_float((latest.get("valuation_mid_source_counts") or {}).get("live_mid")),
            "live_side_conservative_quote": _safe_float(
                (latest.get("valuation_mid_source_counts") or {}).get("live_side_conservative_quote")
            ),
            "last_known_mid": _safe_float((latest.get("valuation_mid_source_counts") or {}).get("last_known_mid")),
            "conservative_bound_hard_degraded": _safe_float(
                (latest.get("valuation_mid_source_counts") or {}).get("conservative_bound_hard_degraded")
            ),
            "hard_degraded": _safe_float((latest.get("valuation_mid_source_counts") or {}).get("hard_degraded")),
        },
    }


def _maker_regression_sentinel(
    execution_paths: Dict[str, Any],
    edge_truth: Dict[str, Any],
    duration_minutes: float,
) -> Dict[str, Any]:
    maker_submits = _safe_float(execution_paths.get("maker_submits"))
    maker_fills = _safe_float(execution_paths.get("maker_fills"))
    maker_fill_rate = _safe_float(execution_paths.get("maker_fill_rate"))
    maker_fire_rate_per_min = _safe_float(execution_paths.get("maker_fire_rate_per_min"))

    near_zero_submit_rate_threshold_per_min = 0.25
    near_zero_fill_count_threshold = 1.0
    low_fill_rate_threshold = 0.05
    low_fill_rate_requires_submits = 5.0

    submit_count_threshold = (
        max(1.0, near_zero_submit_rate_threshold_per_min * duration_minutes)
        if duration_minutes > 0
        else 1.0
    )
    near_zero_submits = maker_submits <= submit_count_threshold
    near_zero_fills = maker_fills <= near_zero_fill_count_threshold
    fill_rate_collapse = (
        maker_submits >= low_fill_rate_requires_submits and maker_fill_rate < low_fill_rate_threshold
    )
    triggered = (near_zero_submits and near_zero_fills) or fill_rate_collapse

    regression_reasons: List[str] = []
    if near_zero_submits and near_zero_fills:
        regression_reasons.append("near_zero_maker_submit_fill_pattern")
    if fill_rate_collapse:
        regression_reasons.append("maker_fill_rate_collapse")

    maker_no_submission_categories = edge_truth.get("maker_no_submission_category_distribution")
    if not isinstance(maker_no_submission_categories, dict):
        maker_no_submission_categories = {}

    watch_categories = {
        "quote_quality_skip_fill_probability": int(
            _safe_float(maker_no_submission_categories.get("quote_quality_skip_fill_probability"))
        ),
        "quote_quality_skip_queue_depth": int(
            _safe_float(maker_no_submission_categories.get("quote_quality_skip_queue_depth"))
        ),
        "replace_guard_min_rest": int(
            _safe_float(maker_no_submission_categories.get("replace_guard_min_rest"))
        ),
    }

    return {
        "observational_only": True,
        "maker_behavior_freeze_state": "provisional_freeze_no_runtime_change",
        "triggered": bool(triggered),
        "regression_reasons": regression_reasons,
        "maker_submits": float(maker_submits),
        "maker_fills": float(maker_fills),
        "maker_fill_rate": float(maker_fill_rate),
        "maker_fire_rate_per_min": float(maker_fire_rate_per_min),
        "thresholds": {
            "near_zero_submit_rate_threshold_per_min": float(near_zero_submit_rate_threshold_per_min),
            "near_zero_submit_count_threshold": float(submit_count_threshold),
            "near_zero_fill_count_threshold": float(near_zero_fill_count_threshold),
            "low_fill_rate_threshold": float(low_fill_rate_threshold),
            "low_fill_rate_requires_submits": float(low_fill_rate_requires_submits),
        },
        "watch_item_primary": "quote_quality_skip_and_replace_guard_distribution",
        "watch_item_distribution": watch_categories,
    }


def _edge_truth_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [evt for evt in events if str(evt.get("event_type") or "").strip() == "edge_evaluation"]
    maker_rows = 0
    taker_rows = 0
    action_rows = 0
    blocked_rows = 0
    maker_blocked_rows = 0
    taker_blocked_rows = 0
    reasons = Counter()
    maker_reasons = Counter()
    taker_reasons = Counter()
    maker_no_submission_causes = Counter()
    maker_no_submission_categories = Counter()
    maker_market_reference_modes = Counter()
    maker_market_reference_fallback_bid_count = 0
    maker_market_reference_fallback_ask_count = 0
    maker_reference_direct_midpoint_activity = 0
    maker_reference_bounded_fallback_activity = 0
    maker_reference_direct_midpoint_action_activity = 0
    maker_reference_bounded_fallback_action_activity = 0
    for row in rows:
        scope = str(row.get("evaluation_scope") or "").strip().lower()
        action = str(row.get("action_taken") or "").strip().lower()
        market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower() or "missing"
        market_reference_source_side = str(row.get("market_reference_source_side") or "").strip().lower() or "none"
        if scope == "maker":
            maker_rows += 1
            maker_market_reference_modes[market_reference_mode] += 1
            if market_reference_mode == "direct_midpoint":
                maker_reference_direct_midpoint_activity += 1
                if action == "maker":
                    maker_reference_direct_midpoint_action_activity += 1
            elif market_reference_mode == "bounded_single_side_touch":
                maker_reference_bounded_fallback_activity += 1
                if action == "maker":
                    maker_reference_bounded_fallback_action_activity += 1
                if market_reference_source_side == "bid":
                    maker_market_reference_fallback_bid_count += 1
                elif market_reference_source_side == "ask":
                    maker_market_reference_fallback_ask_count += 1
        elif scope == "taker":
            taker_rows += 1
        if action in {"maker", "taker"}:
            action_rows += 1
        elif action == "none":
            blocked_rows += 1
            reason = str(row.get("block_reason") or "").strip() or "missing"
            reasons[reason] += 1
            if scope == "maker":
                maker_blocked_rows += 1
                maker_reasons[reason] += 1
                if reason == "maker_no_submission":
                    cause = str(row.get("maker_no_submission_cause") or "").strip().lower() or "unspecified"
                    maker_no_submission_causes[cause] += 1
                    category = str(row.get("maker_no_submission_category") or "").strip().lower() or "unknown"
                    maker_no_submission_categories[category] += 1
            elif scope == "taker":
                taker_blocked_rows += 1
                taker_reasons[reason] += 1
    return {
        "rows_total": float(len(rows)),
        "maker_rows": float(maker_rows),
        "taker_rows": float(taker_rows),
        "action_rows": float(action_rows),
        "blocked_rows": float(blocked_rows),
        "maker_blocked_rows": float(maker_blocked_rows),
        "taker_blocked_rows": float(taker_blocked_rows),
        "block_reason_distribution": dict(sorted(reasons.items(), key=lambda item: item[0])),
        "maker_block_reason_distribution": dict(sorted(maker_reasons.items(), key=lambda item: item[0])),
        "taker_block_reason_distribution": dict(sorted(taker_reasons.items(), key=lambda item: item[0])),
        "maker_no_submission_cause_distribution": dict(
            sorted(maker_no_submission_causes.items(), key=lambda item: item[0])
        ),
        "maker_no_submission_category_distribution": dict(
            sorted(maker_no_submission_categories.items(), key=lambda item: item[0])
        ),
        "maker_market_reference_mode_distribution": dict(
            sorted(maker_market_reference_modes.items(), key=lambda item: item[0])
        ),
        "maker_market_reference_fallback_count": float(maker_reference_bounded_fallback_activity),
        "maker_market_reference_fallback_bid_count": float(maker_market_reference_fallback_bid_count),
        "maker_market_reference_fallback_ask_count": float(maker_market_reference_fallback_ask_count),
        "maker_reference_direct_midpoint_activity": float(maker_reference_direct_midpoint_activity),
        "maker_reference_bounded_fallback_activity": float(maker_reference_bounded_fallback_activity),
        "maker_reference_direct_midpoint_action_activity": float(
            maker_reference_direct_midpoint_action_activity
        ),
        "maker_reference_bounded_fallback_action_activity": float(
            maker_reference_bounded_fallback_action_activity
        ),
    }


def _maker_fireability_window_stats(
    events: List[Dict[str, Any]],
    *,
    taker_config_gate_posture: Dict[str, Any],
) -> Dict[str, Any]:
    def _optional_float_local(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    maker_gate = (
        taker_config_gate_posture.get("maker_gate_posture", {})
        if isinstance(taker_config_gate_posture, dict)
        else {}
    )
    timing_gate_min_sec_to_expiry = _optional_float_local(
        maker_gate.get("timing_gate_min_sec_to_expiry")
    )
    timing_gate_max_sec_to_expiry = _optional_float_local(
        maker_gate.get("timing_gate_max_sec_to_expiry")
    )
    maker_competitive_min_notional_usd = _optional_float_local(
        maker_gate.get("maker_competitive_min_notional_usd")
    )
    maker_competitive_max_shares = _optional_float_local(
        maker_gate.get("maker_competitive_max_shares")
    )
    min_expected_fill_prob = _optional_float_local(maker_gate.get("min_expected_fill_prob"))
    max_queue_ahead_size = _optional_float_local(maker_gate.get("max_queue_ahead_size"))
    viability_price_floor = None
    if (
        maker_competitive_min_notional_usd is not None
        and maker_competitive_min_notional_usd > 0.0
        and maker_competitive_max_shares is not None
        and maker_competitive_max_shares > 0.0
    ):
        viability_price_floor = float(maker_competitive_min_notional_usd / maker_competitive_max_shares)
    config_complete = (
        timing_gate_min_sec_to_expiry is not None
        and timing_gate_max_sec_to_expiry is not None
    )

    active_window_row_count = 0.0
    active_window_submit_count = 0.0
    active_window_replace_guard_count = 0.0
    active_window_quote_quality_skip_fill_probability_count = 0.0
    active_window_quote_quality_skip_queue_depth_count = 0.0
    active_window_sizing_reject_count = 0.0
    active_window_viable_row_count = 0.0
    active_window_impossible_row_count = 0.0
    active_window_unknown_viability_row_count = 0.0
    active_window_block_reasons: Counter[str] = Counter()
    active_window_stage_distribution: Counter[str] = Counter()
    active_window_target_rows: defaultdict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "window_row_count": 0.0,
            "submitted_count": 0.0,
            "replace_guard_min_rest_count": 0.0,
            "quote_quality_skip_fill_probability_count": 0.0,
            "quote_quality_skip_queue_depth_count": 0.0,
            "sizing_reject_count": 0.0,
            "submit_sec_to_expiry": [],
            "market_probability": [],
            "viable_viability_row_count": 0.0,
            "impossible_viability_row_count": 0.0,
            "unknown_viability_row_count": 0.0,
        }
    )

    fill_probability_delta_bins: Counter[str] = Counter()
    queue_depth_delta_bins: Counter[str] = Counter()
    raw_quote_quality_skip_event_count = 0.0
    raw_queue_depth_event_count = 0.0
    low_price_conflict_prices: List[float] = []

    if config_complete:
        for evt in events:
            if str(evt.get("event_type") or "").strip() != "edge_evaluation":
                continue
            if str(evt.get("evaluation_scope") or "").strip().lower() != "maker":
                continue
            sec_to_expiry = _safe_float(
                evt.get("time_remaining_sec", evt.get("sec_to_expiry")),
                default=-1.0,
            )
            if sec_to_expiry < 0.0:
                continue
            if not (
                float(timing_gate_min_sec_to_expiry)
                <= float(sec_to_expiry)
                <= float(timing_gate_max_sec_to_expiry)
            ):
                continue
            active_window_row_count += 1.0
            stage_value = str(evt.get("stage") or "").strip().upper() or "UNKNOWN"
            active_window_stage_distribution[stage_value] += 1
            target_ref = str(evt.get("target_ref") or evt.get("token_id") or "unknown")
            target_row = active_window_target_rows[target_ref]
            target_row["window_row_count"] += 1.0

            market_probability = _safe_float(evt.get("market_probability"), default=-1.0)
            if market_probability >= 0.0:
                target_row["market_probability"].append(float(market_probability))
            if viability_price_floor is None or market_probability < 0.0:
                active_window_unknown_viability_row_count += 1.0
                target_row["unknown_viability_row_count"] += 1.0
            elif market_probability + 1e-9 < viability_price_floor:
                active_window_impossible_row_count += 1.0
                target_row["impossible_viability_row_count"] += 1.0
                low_price_conflict_prices.append(float(market_probability))
            else:
                active_window_viable_row_count += 1.0
                target_row["viable_viability_row_count"] += 1.0

            submitted = _as_bool(evt.get("submitted")) is True
            if submitted:
                active_window_submit_count += 1.0
                active_window_block_reasons["submitted"] += 1
                target_row["submitted_count"] += 1.0
                target_row["submit_sec_to_expiry"].append(float(sec_to_expiry))
                continue

            category = str(evt.get("maker_no_submission_category") or "").strip().lower()
            reason_key = category or (str(evt.get("block_reason") or "").strip().lower() or "none")
            active_window_block_reasons[reason_key] += 1
            if reason_key == "replace_guard_min_rest":
                active_window_replace_guard_count += 1.0
                target_row["replace_guard_min_rest_count"] += 1.0
            elif reason_key == "quote_quality_skip_fill_probability":
                active_window_quote_quality_skip_fill_probability_count += 1.0
                target_row["quote_quality_skip_fill_probability_count"] += 1.0
            elif reason_key == "quote_quality_skip_queue_depth":
                active_window_quote_quality_skip_queue_depth_count += 1.0
                target_row["quote_quality_skip_queue_depth_count"] += 1.0
            elif reason_key == "sizing_reject":
                active_window_sizing_reject_count += 1.0
                target_row["sizing_reject_count"] += 1.0

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "quote_quality_skip":
            continue
        if _as_bool(evt.get("reduce_only_recovery_active")) is True:
            continue
        raw_quote_quality_skip_event_count += 1.0
        skip_reason = str(evt.get("skip_reason") or "").strip().lower()
        if skip_reason == "expected_fill_prob_below_min":
            expected_fill_prob = _safe_float(evt.get("expected_fill_prob"), default=-1.0)
            effective_min_expected_fill_prob = _safe_float(
                evt.get("min_expected_fill_prob", evt.get("effective_min_expected_fill_prob")),
                default=-1.0,
            )
            if expected_fill_prob >= 0.0 and effective_min_expected_fill_prob >= 0.0:
                delta = float(effective_min_expected_fill_prob - expected_fill_prob)
                if delta <= 0.005:
                    fill_probability_delta_bins["within_0p005"] += 1
                elif delta <= 0.015:
                    fill_probability_delta_bins["0p005_to_0p015"] += 1
                else:
                    fill_probability_delta_bins["gt_0p015"] += 1
        elif skip_reason == "queue_ahead_too_deep":
            raw_queue_depth_event_count += 1.0
            queue_ahead_size = _safe_float(evt.get("queue_ahead_size"), default=-1.0)
            effective_max_queue_ahead_size = _safe_float(
                evt.get("max_queue_ahead_size", evt.get("effective_max_queue_ahead_size")),
                default=-1.0,
            )
            if queue_ahead_size >= 0.0 and effective_max_queue_ahead_size >= 0.0:
                delta = float(queue_ahead_size - effective_max_queue_ahead_size)
                if delta <= 25.0:
                    queue_depth_delta_bins["within_25"] += 1
                elif delta <= 50.0:
                    queue_depth_delta_bins["25_to_50"] += 1
                else:
                    queue_depth_delta_bins["gt_50"] += 1

    active_window_quote_quality_skip_total_count = (
        active_window_quote_quality_skip_fill_probability_count
        + active_window_quote_quality_skip_queue_depth_count
    )
    active_window_target_summary: List[Dict[str, Any]] = []
    active_window_viable_target_count = 0.0
    active_window_impossible_target_count = 0.0
    active_window_mixed_viability_target_count = 0.0
    active_window_unknown_viability_target_count = 0.0
    active_window_queue_depth_on_viable_targets_count = 0.0
    active_window_queue_depth_on_impossible_targets_count = 0.0
    active_window_queue_depth_on_mixed_targets_count = 0.0
    active_window_queue_depth_on_unknown_targets_count = 0.0
    for target_ref, target_row in active_window_target_rows.items():
        submit_secs = sorted(
            (float(value) for value in target_row["submit_sec_to_expiry"] if isinstance(value, (int, float))),
            reverse=True,
        )
        submit_gap_sec_sample = [
            round(submit_secs[index] - submit_secs[index + 1], 6)
            for index in range(len(submit_secs) - 1)
        ]
        market_probabilities = sorted(
            (float(value) for value in target_row["market_probability"] if isinstance(value, (int, float)))
        )
        viable_viability_row_count = float(target_row["viable_viability_row_count"])
        impossible_viability_row_count = float(target_row["impossible_viability_row_count"])
        unknown_viability_row_count = float(target_row["unknown_viability_row_count"])
        if impossible_viability_row_count > 0.0 and viable_viability_row_count > 0.0:
            viability_class = "mixed_viability"
            active_window_mixed_viability_target_count += 1.0
            active_window_queue_depth_on_mixed_targets_count += float(
                target_row["quote_quality_skip_queue_depth_count"]
            )
        elif impossible_viability_row_count > 0.0:
            viability_class = (
                "impossible_with_unknown"
                if unknown_viability_row_count > 0.0
                else "impossible_only"
            )
            active_window_impossible_target_count += 1.0
            active_window_queue_depth_on_impossible_targets_count += float(
                target_row["quote_quality_skip_queue_depth_count"]
            )
        elif viable_viability_row_count > 0.0:
            viability_class = (
                "viable_with_unknown"
                if unknown_viability_row_count > 0.0
                else "viable_only"
            )
            active_window_viable_target_count += 1.0
            active_window_queue_depth_on_viable_targets_count += float(
                target_row["quote_quality_skip_queue_depth_count"]
            )
        else:
            viability_class = "unknown_viability"
            active_window_unknown_viability_target_count += 1.0
            active_window_queue_depth_on_unknown_targets_count += float(
                target_row["quote_quality_skip_queue_depth_count"]
            )
        active_window_target_summary.append(
            {
                "target_ref": target_ref,
                "window_row_count": float(target_row["window_row_count"]),
                "submitted_count": float(target_row["submitted_count"]),
                "replace_guard_min_rest_count": float(target_row["replace_guard_min_rest_count"]),
                "quote_quality_skip_fill_probability_count": float(
                    target_row["quote_quality_skip_fill_probability_count"]
                ),
                "quote_quality_skip_queue_depth_count": float(
                    target_row["quote_quality_skip_queue_depth_count"]
                ),
                "sizing_reject_count": float(target_row["sizing_reject_count"]),
                "viability_class": viability_class,
                "viable_viability_row_count": viable_viability_row_count,
                "impossible_viability_row_count": impossible_viability_row_count,
                "unknown_viability_row_count": unknown_viability_row_count,
                "market_probability_min": min(market_probabilities) if market_probabilities else None,
                "market_probability_p50": _percentile(market_probabilities, 0.50),
                "market_probability_max": max(market_probabilities) if market_probabilities else None,
                "submit_sec_to_expiry_sample": submit_secs[:6],
                "submit_gap_sec_sample": submit_gap_sec_sample[:6],
            }
        )
    active_window_target_summary.sort(
        key=lambda item: (
            -float(item["window_row_count"]),
            -float(item["submitted_count"]),
            str(item["target_ref"]),
        )
    )
    low_price_conflict_price_band = {
        "min": min(low_price_conflict_prices) if low_price_conflict_prices else None,
        "p50": _percentile(low_price_conflict_prices, 0.50),
        "max": max(low_price_conflict_prices) if low_price_conflict_prices else None,
    }
    raw_queue_depth_near_threshold_event_count = float(
        queue_depth_delta_bins.get("within_25", 0)
        + queue_depth_delta_bins.get("25_to_50", 0)
    )
    raw_queue_depth_hard_miss_event_count = float(queue_depth_delta_bins.get("gt_50", 0))
    raw_queue_depth_unknown_severity_event_count = float(
        max(
            0.0,
            raw_queue_depth_event_count
            - raw_queue_depth_near_threshold_event_count
            - raw_queue_depth_hard_miss_event_count,
        )
    )

    return {
        "claim_boundary": (
            "report_only_maker_fireability_window; active-window counts are derived from maker "
            "edge-evaluation rows inside the manifest-configured maker timing gate and paired with "
            "raw non-recovery quote_quality_skip event severity bins"
        ),
        "config_complete": bool(config_complete),
        "timing_gate_min_sec_to_expiry": timing_gate_min_sec_to_expiry,
        "timing_gate_max_sec_to_expiry": timing_gate_max_sec_to_expiry,
        "maker_competitive_min_notional_usd": maker_competitive_min_notional_usd,
        "maker_competitive_max_shares": maker_competitive_max_shares,
        "viability_geometry_complete": bool(viability_price_floor is not None),
        "active_window_low_price_viability_floor": viability_price_floor,
        "min_expected_fill_prob": min_expected_fill_prob,
        "max_queue_ahead_size": max_queue_ahead_size,
        "active_window_row_count": float(active_window_row_count),
        "active_window_submit_count": float(active_window_submit_count),
        "active_window_replace_guard_count": float(active_window_replace_guard_count),
        "active_window_quote_quality_skip_fill_probability_count": float(
            active_window_quote_quality_skip_fill_probability_count
        ),
        "active_window_quote_quality_skip_queue_depth_count": float(
            active_window_quote_quality_skip_queue_depth_count
        ),
        "active_window_sizing_reject_count": float(active_window_sizing_reject_count),
        "active_window_quote_quality_skip_total_count": float(
            active_window_quote_quality_skip_total_count
        ),
        "active_window_submit_rate": (
            float(active_window_submit_count / active_window_row_count)
            if active_window_row_count > 0.0
            else 0.0
        ),
        "active_window_replace_guard_rate": (
            float(active_window_replace_guard_count / active_window_row_count)
            if active_window_row_count > 0.0
            else 0.0
        ),
        "active_window_quote_quality_skip_rate": (
            float(active_window_quote_quality_skip_total_count / active_window_row_count)
            if active_window_row_count > 0.0
            else 0.0
        ),
        "active_window_sizing_reject_rate": (
            float(active_window_sizing_reject_count / active_window_row_count)
            if active_window_row_count > 0.0
            else 0.0
        ),
        "active_window_viable_row_count": float(active_window_viable_row_count),
        "active_window_impossible_row_count": float(active_window_impossible_row_count),
        "active_window_unknown_viability_row_count": float(active_window_unknown_viability_row_count),
        "active_window_viable_target_count": float(active_window_viable_target_count),
        "active_window_impossible_target_count": float(active_window_impossible_target_count),
        "active_window_mixed_viability_target_count": float(active_window_mixed_viability_target_count),
        "active_window_unknown_viability_target_count": float(active_window_unknown_viability_target_count),
        "active_window_low_price_conflict_price_band": low_price_conflict_price_band,
        "active_window_queue_depth_on_viable_targets_count": float(
            active_window_queue_depth_on_viable_targets_count
        ),
        "active_window_queue_depth_on_impossible_targets_count": float(
            active_window_queue_depth_on_impossible_targets_count
        ),
        "active_window_queue_depth_on_mixed_targets_count": float(
            active_window_queue_depth_on_mixed_targets_count
        ),
        "active_window_queue_depth_on_unknown_targets_count": float(
            active_window_queue_depth_on_unknown_targets_count
        ),
        "active_window_block_reason_distribution": dict(
            sorted(active_window_block_reasons.items(), key=lambda item: item[0])
        ),
        "active_window_stage_distribution": dict(
            sorted(active_window_stage_distribution.items(), key=lambda item: item[0])
        ),
        "active_window_target_summary": active_window_target_summary[:8],
        "active_window_viability_claim_boundary": (
            "geometry_only_classification; viable vs impossible rows are derived from maker market_probability "
            "against manifest sizing floor min_notional/max_shares and do not assign broader causal blame"
        ),
        "active_window_queue_depth_shadow_claim_boundary": (
            "queue-depth target burden uses maker edge-evaluation no-submit assignments by target; raw near-threshold "
            "vs hard-miss counts come from non-recovery quote_quality_skip event deltas and are a different population"
        ),
        "raw_queue_depth_event_count": float(raw_queue_depth_event_count),
        "raw_queue_depth_near_threshold_event_count": raw_queue_depth_near_threshold_event_count,
        "raw_queue_depth_hard_miss_event_count": raw_queue_depth_hard_miss_event_count,
        "raw_queue_depth_unknown_severity_event_count": raw_queue_depth_unknown_severity_event_count,
        "raw_quote_quality_skip_severity": {
            "claim_boundary": (
                "raw_local_reject_sparks_non_recovery_only; quote_quality_skip events are event-level "
                "reject sparks and are not the same population as maker edge-evaluation no-submit assignments"
            ),
            "raw_quote_quality_skip_event_count": float(raw_quote_quality_skip_event_count),
            "fill_probability_delta_bins": dict(
                sorted(fill_probability_delta_bins.items(), key=lambda item: item[0])
            ),
            "queue_depth_delta_bins": dict(
                sorted(queue_depth_delta_bins.items(), key=lambda item: item[0])
            ),
        },
    }


def _mode_transition_timeline(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for evt in events:
        if str(evt.get("event_type") or "") != "operating_mode_transition":
            continue
        out.append(
            {
                "ts_utc": str(evt.get("ts_utc") or ""),
                "state": str(evt.get("state") or ""),
                "previous_state": str(evt.get("previous_state") or ""),
                "reason": str(evt.get("reason") or ""),
            }
        )
    return out


def _control_authority_clarity(status: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest = status[-1] if status else {}
    if not isinstance(latest, dict):
        latest = {}
    semantics_obj = latest.get("alert_semantics")
    if not isinstance(semantics_obj, dict):
        semantics_obj = {}

    transport_enabled_raw = semantics_obj.get("transport_enabled", latest.get("alert_transport_enabled"))
    auto_stop_authority_raw = semantics_obj.get(
        "auto_stop_control_authority_enabled",
        latest.get("auto_stop_control_authority_enabled"),
    )
    transport_enabled = _as_bool(transport_enabled_raw)
    auto_stop_control_authority_enabled = _as_bool(auto_stop_authority_raw)
    transport_disable_control_authority_unchanged = _as_bool(
        semantics_obj.get(
            "transport_disable_control_authority_unchanged",
            latest.get("transport_disable_control_authority_unchanged"),
        )
    )
    fields_present = bool(
        (transport_enabled is not None)
        and (auto_stop_control_authority_enabled is not None)
    )
    if (
        fields_present
        and (transport_enabled is False)
        and (auto_stop_control_authority_enabled is True)
        and (transport_disable_control_authority_unchanged is None)
    ):
        transport_disable_control_authority_unchanged = True

    if not fields_present:
        semantics = "unknown_status_fields_missing"
        observation_status = "unknown_missing_fields"
    elif (transport_enabled is False) and bool(transport_disable_control_authority_unchanged):
        semantics = "transport_disabled_control_authority_unchanged"
        observation_status = "explicit"
    else:
        semantics = "transport_and_control_enabled_or_not_applicable"
        observation_status = "explicit"

    return {
        "alert_transport_enabled": transport_enabled,
        "auto_stop_control_authority_enabled": auto_stop_control_authority_enabled,
        "transport_disable_control_authority_unchanged": transport_disable_control_authority_unchanged,
        "transport_layer_class": str(semantics_obj.get("transport_layer_class") or "notification_transport"),
        "control_authority_class": str(semantics_obj.get("control_authority_class") or "risk_control_authority"),
        "transport_disable_semantics": semantics,
        "control_authority_observation_status": observation_status,
    }


def _resolve_starvation_mode(
    *,
    order_submit_total: float,
    fill_total: float,
    runtime_classification: Dict[str, Any],
    edge_truth: Dict[str, Any],
    kill_switch_events: float,
    safe_stop_transitions: float,
    maker_only_transitions: float,
) -> Dict[str, Any]:
    runtime_class_name = str(runtime_classification.get("classification") or "").strip().upper()
    primary = str(runtime_classification.get("primary_suppression_cause") or "").strip()
    contributing = [str(x).strip() for x in (runtime_classification.get("contributing_suppression_causes") or []) if str(x).strip()]
    ambiguous = bool(runtime_classification.get("ambiguous_suppression_cause", False))
    no_orders = order_submit_total <= 0.0
    no_fills = fill_total <= 0.0
    suppressed = bool(
        no_orders
        and (
            kill_switch_events > 0.0
            or safe_stop_transitions > 0.0
            or maker_only_transitions > 0.0
            or runtime_class_name in {"INVALID_DEADLOCK", "INVALID_SAFETY", "NON_PROMOTABLE_NO_PARTICIPATION"}
            or bool(primary)
            or bool(contributing)
        )
    )

    if not suppressed:
        mode = "none"
        explanation = "execution_not_suppression_dominated"
    elif kill_switch_events > 0.0:
        mode = "kill_switch"
        explanation = "kill_switch_events_detected_with_zero_submits"
    elif runtime_class_name in {"INVALID_DEADLOCK", "INVALID_SAFETY"} or safe_stop_transitions > 0.0:
        mode = "safety_halt"
        explanation = "runtime_safety_or_safe_stop_suppression_with_zero_submits"
    elif maker_only_transitions > 0.0:
        mode = "maker_only_gate"
        explanation = "maker_only_transitions_with_zero_submits"
    elif runtime_class_name == "NON_PROMOTABLE_NO_PARTICIPATION":
        mode = "readiness_hold"
        explanation = "non_promotable_no_participation_with_zero_submits"
    else:
        block_dist_raw = edge_truth.get("block_reason_distribution")
        block_dist = block_dist_raw if isinstance(block_dist_raw, dict) else {}
        top_block_reason = ""
        top_block_count = 0.0
        for reason, raw_count in block_dist.items():
            count = _safe_float(raw_count)
            if count <= top_block_count:
                continue
            top_block_reason = str(reason or "").strip()
            top_block_count = float(count)
        inferred_mode = {
            "normal_taker_authority_closed": "late_window_authority_gate",
            "stage_disallow_taker": "late_window_authority_gate",
            "latency_not_armed": "latency_gate",
            "fair_probability_missing": "probability_gate",
            "taker_requires_ws_book_source": "book_source_gate",
            "maker_timing_gate_closed": "maker_timing_gate",
            "token_lag_not_verified_for_maker": "lag_verification_gate",
            "quote_quality_skip_queue_depth": "maker_quality_gate",
            "quote_quality_skip_fill_probability": "maker_quality_gate",
            "sizing_reject": "maker_sizing_gate",
        }.get(top_block_reason, "")
        if inferred_mode:
            mode = inferred_mode
            explanation = (
                "suppression_inferred_from_block_reason:"
                + f"{top_block_reason}:count={top_block_count:.0f}"
            )
        else:
            mode = "unknown"
            explanation = "suppression_detected_without_unique_mode"

    result = {
        "suppression_dominated_run": suppressed,
        "execution_starvation_mode": mode,
        "protected_no_trade_explanation": explanation,
        "order_submit_total": float(order_submit_total),
        "fill_total": float(fill_total),
        "runtime_primary_suppression_cause": primary or "none",
        "runtime_contributing_suppression_causes": sorted(set(contributing)),
        "runtime_ambiguous_suppression_cause": ambiguous,
    }
    if "suppression_inferred_from_block_reason:" in explanation:
        parts = explanation.split(":")
        inferred_reason = parts[1] if len(parts) > 1 else ""
        inferred_count = 0.0
        if len(parts) > 2 and parts[2].startswith("count="):
            inferred_count = _safe_float(parts[2].split("=", 1)[1])
        result["inferred_suppression_reason"] = inferred_reason
        result["inferred_suppression_reason_count"] = float(inferred_count)
    else:
        result["inferred_suppression_reason"] = ""
        result["inferred_suppression_reason_count"] = 0.0
    return result


def _first_event(events: List[Dict[str, Any]], event_type: str) -> Dict[str, Any]:
    for evt in events:
        if str(evt.get("event_type") or "").strip() != event_type:
            continue
        return {
            "event_type": event_type,
            "ts_utc": str(evt.get("ts_utc") or ""),
            "reason": str(evt.get("reason") or ""),
            "reasons": list(evt.get("reasons") or []) if isinstance(evt.get("reasons"), list) else [],
            "state": str(evt.get("state") or ""),
            "previous_state": str(evt.get("previous_state") or ""),
        }
    return {}


def _first_block_reason(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        action = str(evt.get("action_taken") or "").strip().lower()
        block_reason = str(evt.get("block_reason") or "").strip()
        if action != "none" or not block_reason:
            continue
        return {
            "event_type": "edge_evaluation",
            "ts_utc": str(evt.get("ts_utc") or ""),
            "block_reason": block_reason,
        }
    return {}


def _protection_path_trigger_chain(
    *,
    events: List[Dict[str, Any]],
    runtime_classification: Dict[str, Any],
    starvation: Dict[str, Any],
) -> Dict[str, Any]:
    ordered_events = sorted(
        [evt for evt in events if isinstance(evt, dict)],
        key=lambda evt: str(evt.get("ts_utc") or ""),
    )
    first_block = _first_block_reason(ordered_events)
    first_mode_transition = _first_event(ordered_events, "operating_mode_transition")
    first_auto_stop = _first_event(ordered_events, "alert_policy_auto_stop")
    first_kill_switch_cancel = _first_event(ordered_events, "kill_switch_cancel_all")
    first_latency_regime_change = _first_event(ordered_events, "latency_regime_change")
    suppression_dominated = bool(starvation.get("suppression_dominated_run", False))
    if suppression_dominated:
        interpretation = "causal_suppression_chain"
    else:
        interpretation = "observational_timeline_only"
    return {
        "trigger_chain_interpretation": interpretation,
        "suppression_dominated_run": suppression_dominated,
        "first_blocked_edge_signal": first_block,
        "first_latency_regime_change": first_latency_regime_change,
        "first_operating_mode_transition": first_mode_transition,
        "first_alert_policy_auto_stop": first_auto_stop,
        "first_kill_switch_cancel_all": first_kill_switch_cancel,
        "runtime_primary_suppression_cause": str(runtime_classification.get("primary_suppression_cause") or "none"),
        "runtime_contributing_suppression_causes": list(runtime_classification.get("contributing_suppression_causes") or []),
        "runtime_ambiguous_suppression_cause": bool(runtime_classification.get("ambiguous_suppression_cause", False)),
        "final_execution_starvation_mode": str(starvation.get("execution_starvation_mode") or "unknown"),
        "final_protected_no_trade_explanation": str(starvation.get("protected_no_trade_explanation") or ""),
        "inferred_suppression_reason": str(starvation.get("inferred_suppression_reason") or ""),
        "inferred_suppression_reason_count": float(_safe_float(starvation.get("inferred_suppression_reason_count"))),
    }


def _pickoff_indicator(events: List[Dict[str, Any]], *, horizon_sec: float = 3.0, adverse_threshold: float = 0.003) -> Dict[str, float]:
    rows: List[Tuple[dt.datetime, Dict[str, Any]]] = []
    for evt in events:
        ts = parse_ts(evt.get("ts_utc"))
        if ts is not None:
            rows.append((ts, evt))
    rows.sort(key=lambda item: item[0])

    fills: List[Tuple[dt.datetime, Dict[str, Any]]] = [
        (ts, evt) for ts, evt in rows if str(evt.get("event_type") or "") == "fill"
    ]
    books_by_token: Dict[str, List[Tuple[dt.datetime, float]]] = {}
    for ts, evt in rows:
        if str(evt.get("event_type") or "") != "book_top":
            continue
        token_id = str(evt.get("token_id") or "")
        midpoint = evt.get("midpoint")
        if token_id and isinstance(midpoint, (int, float)):
            books_by_token.setdefault(token_id, []).append((ts, float(midpoint)))

    scored = 0.0
    adverse = 0.0
    for fill_ts, fill_evt in fills:
        token_id = str(fill_evt.get("token_id") or "")
        side = str(fill_evt.get("side") or "").upper()
        fill_price = _safe_float(fill_evt.get("price"), default=-1.0)
        if token_id == "" or fill_price <= 0 or side not in {"BUY", "SELL"}:
            continue
        books = books_by_token.get(token_id, [])
        horizon = fill_ts + dt.timedelta(seconds=max(0.1, float(horizon_sec)))
        future_mid: Optional[float] = None
        for ts, mid in books:
            if ts <= fill_ts:
                continue
            if ts > horizon:
                break
            future_mid = mid
            break
        if future_mid is None:
            continue
        scored += 1.0
        if side == "BUY":
            delta = future_mid - fill_price
        else:
            delta = fill_price - future_mid
        if delta < (-abs(float(adverse_threshold))):
            adverse += 1.0

    return {
        "fills_scored": scored,
        "horizon_outcome_horizon_sec": float(horizon_sec),
        "horizon_outcome_adverse_after_fill_count": adverse,
        "horizon_outcome_adverse_after_fill_ratio": (adverse / scored) if scored > 0 else 0.0,
        "horizon_outcome_adverse_threshold": float(adverse_threshold),
        # Legacy aliases kept for compatibility; prefer horizon_outcome_* fields.
        "adverse_after_fill_count": adverse,
        "adverse_after_fill_ratio": (adverse / scored) if scored > 0 else 0.0,
        "horizon_sec": float(horizon_sec),
        "adverse_threshold": float(adverse_threshold),
    }


def _execution_quality_lane_attribution(
    events: List[Dict[str, Any]],
    *,
    capture_stats: Dict[str, Any],
    horizon_stats: Dict[str, Any],
    horizon_sec: float = 3.0,
    adverse_threshold: float = 0.003,
) -> Dict[str, Any]:
    order_lane_by_id: Dict[str, str] = {}
    order_stage_by_id: Dict[str, str] = {}
    order_edge_bucket_by_id: Dict[str, str] = {}
    latest_mid_by_token: Dict[str, float] = {}

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _normalize_stage(value: Any) -> str:
        stage = str(value or "").strip().upper()
        return stage or "UNKNOWN"

    def _is_recovery_override_submit(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        candidate_payloads = [
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            _payload_dict(evt.get("risk_decision_basis")),
            evt,
        ]
        for payload in candidate_payloads:
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            reason = str(payload.get("reduce_only_recovery_reason") or "").strip()
            if reason:
                return True
        return False

    def _has_normal_competitiveness_payload(comp: Dict[str, Any]) -> bool:
        return any(
            key in comp
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    def _classify_submit(evt: Dict[str, Any]) -> Tuple[str, str, str]:
        reason = str(evt.get("reason") or "").strip().lower()
        comp = _payload_dict(evt.get("taker_competitiveness"))
        stage = _normalize_stage(comp.get("stage") or evt.get("stage"))
        edge_bucket = _taker_edge_bucket(comp.get("edge_abs") if comp else evt.get("edge_abs"))
        if _is_taker_submit_reason(reason):
            if _is_recovery_override_submit(evt, comp):
                return "reduce_only_recovery_taker", stage, edge_bucket
            if _has_normal_competitiveness_payload(comp):
                return "normal_taker", stage, edge_bucket
            return "unknown_taker", stage, edge_bucket
        return "maker", stage, edge_bucket

    def _lane_row() -> Dict[str, Any]:
        return {
            "submit_count": 0.0,
            "fill_event_count": 0.0,
            "filled_order_ids": set(),
            "notional": 0.0,
            "immediate_fills_scored": 0.0,
            "immediate_capture_fill_count": 0.0,
            "immediate_adverse_fill_count": 0.0,
            "immediate_unscored_fill_count": 0.0,
            "immediate_capture": 0.0,
            "immediate_adverse_selection": 0.0,
            "horizon_fills_scored": 0.0,
            "horizon_adverse_after_fill_count": 0.0,
            "submit_stage_distribution": Counter(),
            "submit_edge_bucket_distribution": Counter(),
            "fill_stage_distribution": Counter(),
            "fill_edge_bucket_distribution": Counter(),
        }

    lane_rows: Dict[str, Dict[str, Any]] = {}

    def _row(lane: str) -> Dict[str, Any]:
        row = lane_rows.get(lane)
        if isinstance(row, dict):
            return row
        row = _lane_row()
        lane_rows[lane] = row
        return row

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "order_submit":
            order_id = str(evt.get("order_id") or "").strip()
            lane, stage, edge_bucket = _classify_submit(evt)
            row = _row(lane)
            row["submit_count"] += 1.0
            row["submit_stage_distribution"][stage] += 1
            row["submit_edge_bucket_distribution"][edge_bucket] += 1
            if order_id:
                order_lane_by_id[order_id] = lane
                order_stage_by_id[order_id] = stage
                order_edge_bucket_by_id[order_id] = edge_bucket
            continue
        if event_type == "book_top":
            token_id = str(evt.get("token_id") or "").strip()
            midpoint = evt.get("midpoint")
            if token_id and isinstance(midpoint, (int, float)):
                latest_mid_by_token[token_id] = float(midpoint)
            continue
        if event_type != "fill":
            continue

        order_id = str(evt.get("order_id") or "").strip()
        lane = order_lane_by_id.get(order_id, "unlinked")
        row = _row(lane)
        row["fill_event_count"] += 1.0
        if order_id:
            row["filled_order_ids"].add(order_id)
        stage = order_stage_by_id.get(order_id, "UNKNOWN")
        edge_bucket = order_edge_bucket_by_id.get(order_id, "unknown")
        row["fill_stage_distribution"][stage] += 1
        row["fill_edge_bucket_distribution"][edge_bucket] += 1

        price = evt.get("price")
        size = evt.get("size")
        if isinstance(price, (int, float)) and isinstance(size, (int, float)):
            row["notional"] += float(price) * float(size)

        token_id = str(evt.get("token_id") or "").strip()
        side = str(evt.get("side") or "").strip().upper()
        mid = latest_mid_by_token.get(token_id)
        if (
            mid is None
            or not isinstance(price, (int, float))
            or not isinstance(size, (int, float))
            or side not in {"BUY", "SELL"}
        ):
            row["immediate_unscored_fill_count"] += 1.0
            continue

        qty = float(size)
        if side == "BUY":
            delta = (float(mid) - float(price)) * qty
        else:
            delta = (float(price) - float(mid)) * qty
        row["immediate_fills_scored"] += 1.0
        if delta >= 0.0:
            row["immediate_capture"] += float(delta)
            row["immediate_capture_fill_count"] += 1.0
        else:
            row["immediate_adverse_selection"] += abs(float(delta))
            row["immediate_adverse_fill_count"] += 1.0

    rows_with_ts: List[Tuple[dt.datetime, Dict[str, Any]]] = []
    for evt in events:
        ts = parse_ts(evt.get("ts_utc"))
        if ts is not None:
            rows_with_ts.append((ts, evt))
    rows_with_ts.sort(key=lambda item: item[0])

    books_by_token: Dict[str, List[Tuple[dt.datetime, float]]] = {}
    for ts, evt in rows_with_ts:
        if str(evt.get("event_type") or "") != "book_top":
            continue
        token_id = str(evt.get("token_id") or "").strip()
        midpoint = evt.get("midpoint")
        if token_id and isinstance(midpoint, (int, float)):
            books_by_token.setdefault(token_id, []).append((ts, float(midpoint)))

    for fill_ts, fill_evt in rows_with_ts:
        if str(fill_evt.get("event_type") or "") != "fill":
            continue
        order_id = str(fill_evt.get("order_id") or "").strip()
        lane = order_lane_by_id.get(order_id, "unlinked")
        token_id = str(fill_evt.get("token_id") or "").strip()
        side = str(fill_evt.get("side") or "").strip().upper()
        fill_price = _safe_float(fill_evt.get("price"), default=-1.0)
        if not token_id or fill_price <= 0.0 or side not in {"BUY", "SELL"}:
            continue
        horizon = fill_ts + dt.timedelta(seconds=max(0.1, float(horizon_sec)))
        future_mid: Optional[float] = None
        for ts, mid in books_by_token.get(token_id, []):
            if ts <= fill_ts:
                continue
            if ts > horizon:
                break
            future_mid = mid
            break
        if future_mid is None:
            continue
        row = _row(lane)
        row["horizon_fills_scored"] += 1.0
        delta = future_mid - fill_price if side == "BUY" else fill_price - future_mid
        if delta < (-abs(float(adverse_threshold))):
            row["horizon_adverse_after_fill_count"] += 1.0

    by_lane: Dict[str, Dict[str, Any]] = {}
    total = _lane_row()
    total["filled_order_ids"] = set()
    for lane, row in sorted(lane_rows.items(), key=lambda item: item[0]):
        row["immediate_capture_minus_adverse"] = float(
            row["immediate_capture"] - row["immediate_adverse_selection"]
        )
        row["horizon_adverse_after_fill_ratio"] = (
            float(row["horizon_adverse_after_fill_count"] / row["horizon_fills_scored"])
            if row["horizon_fills_scored"] > 0.0
            else 0.0
        )
        by_lane[lane] = {
            "submit_count": float(row["submit_count"]),
            "fill_event_count": float(row["fill_event_count"]),
            "filled_order_count": float(len(row["filled_order_ids"])),
            "notional": float(row["notional"]),
            "immediate_fills_scored": float(row["immediate_fills_scored"]),
            "immediate_capture_fill_count": float(row["immediate_capture_fill_count"]),
            "immediate_adverse_fill_count": float(row["immediate_adverse_fill_count"]),
            "immediate_unscored_fill_count": float(row["immediate_unscored_fill_count"]),
            "immediate_capture": float(row["immediate_capture"]),
            "immediate_adverse_selection": float(row["immediate_adverse_selection"]),
            "immediate_capture_minus_adverse": float(row["immediate_capture_minus_adverse"]),
            "horizon_fills_scored": float(row["horizon_fills_scored"]),
            "horizon_adverse_after_fill_count": float(row["horizon_adverse_after_fill_count"]),
            "horizon_adverse_after_fill_ratio": float(row["horizon_adverse_after_fill_ratio"]),
            "submit_stage_distribution": dict(
                sorted(row["submit_stage_distribution"].items(), key=lambda item: item[0])
            ),
            "submit_edge_bucket_distribution": dict(
                sorted(row["submit_edge_bucket_distribution"].items(), key=lambda item: item[0])
            ),
            "fill_stage_distribution": dict(
                sorted(row["fill_stage_distribution"].items(), key=lambda item: item[0])
            ),
            "fill_edge_bucket_distribution": dict(
                sorted(row["fill_edge_bucket_distribution"].items(), key=lambda item: item[0])
            ),
        }
        for key in (
            "submit_count",
            "fill_event_count",
            "notional",
            "immediate_fills_scored",
            "immediate_capture_fill_count",
            "immediate_adverse_fill_count",
            "immediate_unscored_fill_count",
            "immediate_capture",
            "immediate_adverse_selection",
            "horizon_fills_scored",
            "horizon_adverse_after_fill_count",
        ):
            total[key] += float(row[key])
        total["filled_order_ids"].update(row["filled_order_ids"])

    total_capture_minus_adverse = float(total["immediate_capture"] - total["immediate_adverse_selection"])
    total_horizon_ratio = (
        float(total["horizon_adverse_after_fill_count"] / total["horizon_fills_scored"])
        if total["horizon_fills_scored"] > 0.0
        else 0.0
    )
    expected_immediate_net = _safe_float(
        capture_stats.get("immediate_capture_minus_adverse", capture_stats.get("capture_minus_adverse")),
        default=0.0,
    )
    expected_horizon_scored = _safe_float(
        horizon_stats.get("horizon_outcome_adverse_after_fill_count", horizon_stats.get("adverse_after_fill_count")),
        default=0.0,
    )
    expected_horizon_fills = _safe_float(horizon_stats.get("fills_scored"), default=0.0)
    immediate_delta = float(total_capture_minus_adverse - expected_immediate_net)
    horizon_adverse_delta = float(total["horizon_adverse_after_fill_count"] - expected_horizon_scored)
    horizon_fills_delta = float(total["horizon_fills_scored"] - expected_horizon_fills)

    return {
        "classification_policy": (
            "order_submit reason classified by canonical taker identity helper is taker; "
            "reduce_only/preexpiry recovery flags classify recovery; normal TakerCompetitivenessEngine payload classifies normal; "
            "remaining order_submit rows are maker to match execution_paths."
        ),
        "horizon_sec": float(horizon_sec),
        "horizon_adverse_threshold": float(adverse_threshold),
        "by_lane": by_lane,
        "total": {
            "submit_count": float(total["submit_count"]),
            "fill_event_count": float(total["fill_event_count"]),
            "filled_order_count": float(len(total["filled_order_ids"])),
            "notional": float(total["notional"]),
            "immediate_fills_scored": float(total["immediate_fills_scored"]),
            "immediate_capture": float(total["immediate_capture"]),
            "immediate_adverse_selection": float(total["immediate_adverse_selection"]),
            "immediate_capture_minus_adverse": float(total_capture_minus_adverse),
            "horizon_fills_scored": float(total["horizon_fills_scored"]),
            "horizon_adverse_after_fill_count": float(total["horizon_adverse_after_fill_count"]),
            "horizon_adverse_after_fill_ratio": float(total_horizon_ratio),
        },
        "reconciliation": {
            "immediate_expected_capture_minus_adverse": float(expected_immediate_net),
            "immediate_lane_capture_minus_adverse": float(total_capture_minus_adverse),
            "immediate_capture_minus_adverse_delta": float(immediate_delta),
            "immediate_capture_minus_adverse_reconciles": bool(abs(immediate_delta) <= 1e-9),
            "horizon_expected_fills_scored": float(expected_horizon_fills),
            "horizon_lane_fills_scored": float(total["horizon_fills_scored"]),
            "horizon_fills_scored_delta": float(horizon_fills_delta),
            "horizon_fills_scored_reconciles": bool(abs(horizon_fills_delta) <= 1e-9),
            "horizon_expected_adverse_after_fill_count": float(expected_horizon_scored),
            "horizon_lane_adverse_after_fill_count": float(total["horizon_adverse_after_fill_count"]),
            "horizon_adverse_after_fill_count_delta": float(horizon_adverse_delta),
            "horizon_adverse_after_fill_count_reconciles": bool(abs(horizon_adverse_delta) <= 1e-9),
        },
    }


def _execution_quality_decision_reference_lane_attribution(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    orders_by_id: Dict[str, Dict[str, Any]] = {}
    lane_rows: Dict[str, Dict[str, Any]] = {}

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _normalize_stage(value: Any) -> str:
        stage = str(value or "").strip().upper()
        return stage or "UNKNOWN"

    def _is_recovery_submit(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        for payload in (
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            _payload_dict(evt.get("risk_decision_basis")),
            evt,
        ):
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            if str(payload.get("reduce_only_recovery_reason") or "").strip():
                return True
        return False

    def _has_normal_competitiveness_payload(comp: Dict[str, Any]) -> bool:
        return any(
            key in comp
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    def _classify_submit(evt: Dict[str, Any]) -> Tuple[str, str]:
        reason = str(evt.get("reason") or "").strip().lower()
        lane = str(evt.get("submission_lane") or "").strip().lower()
        comp = _payload_dict(evt.get("taker_competitiveness"))
        if not comp:
            comp = _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness"))
        stage = _normalize_stage(comp.get("stage") or evt.get("stage"))
        if _is_taker_submit_reason(reason) or lane == "taker":
            if _is_recovery_submit(evt, comp):
                return "reduce_only_recovery_taker", stage
            if _has_normal_competitiveness_payload(comp):
                return "normal_taker", stage
            return "unknown_taker", stage
        return "maker", stage

    def _lane_row() -> Dict[str, Any]:
        return {
            "submit_count": 0.0,
            "fill_event_count": 0.0,
            "filled_order_ids": set(),
            "notional": 0.0,
            "immediate_fills_scored": 0.0,
            "immediate_unscored_fill_count": 0.0,
            "immediate_capture": 0.0,
            "immediate_adverse_selection": 0.0,
            "submit_stage_distribution": Counter(),
            "fill_stage_distribution": Counter(),
        }

    def _row(lane: str) -> Dict[str, Any]:
        row = lane_rows.get(lane)
        if isinstance(row, dict):
            return row
        row = _lane_row()
        lane_rows[lane] = row
        return row

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "order_submit":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if not order_id:
            continue
        lane, stage = _classify_submit(evt)
        row = _row(lane)
        row["submit_count"] += 1.0
        row["submit_stage_distribution"][stage] += 1
        orders_by_id[order_id] = {
            "lane": lane,
            "stage": stage,
            "decision_reference_midpoint": _safe_float(
                evt.get("decision_reference_midpoint", evt.get("midpoint")),
                default=-1.0,
            ),
        }

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        order = orders_by_id.get(order_id)
        if not isinstance(order, dict):
            continue
        lane = str(order.get("lane") or "unknown")
        row = _row(lane)
        row["fill_event_count"] += 1.0
        if order_id:
            row["filled_order_ids"].add(order_id)
        stage = str(order.get("stage") or "UNKNOWN")
        row["fill_stage_distribution"][stage] += 1
        price = evt.get("price")
        size = evt.get("size")
        if isinstance(price, (int, float)) and isinstance(size, (int, float)):
            row["notional"] += float(price) * float(size)
        side = str(evt.get("side") or "").strip().upper()
        midpoint = _safe_float(order.get("decision_reference_midpoint"), default=-1.0)
        if (
            midpoint <= 0.0
            or not isinstance(price, (int, float))
            or not isinstance(size, (int, float))
            or side not in {"BUY", "SELL"}
        ):
            row["immediate_unscored_fill_count"] += 1.0
            continue
        qty = float(size)
        delta = (midpoint - float(price)) * qty if side == "BUY" else (float(price) - midpoint) * qty
        row["immediate_fills_scored"] += 1.0
        if delta >= 0.0:
            row["immediate_capture"] += float(delta)
        else:
            row["immediate_adverse_selection"] += abs(float(delta))

    by_lane: Dict[str, Dict[str, Any]] = {}
    total = _lane_row()
    total["filled_order_ids"] = set()
    for lane, row in sorted(lane_rows.items(), key=lambda item: item[0]):
        net = float(row["immediate_capture"] - row["immediate_adverse_selection"])
        by_lane[lane] = {
            "submit_count": float(row["submit_count"]),
            "fill_event_count": float(row["fill_event_count"]),
            "filled_order_count": float(len(row["filled_order_ids"])),
            "notional": float(row["notional"]),
            "immediate_fills_scored": float(row["immediate_fills_scored"]),
            "immediate_unscored_fill_count": float(row["immediate_unscored_fill_count"]),
            "immediate_capture": float(row["immediate_capture"]),
            "immediate_adverse_selection": float(row["immediate_adverse_selection"]),
            "immediate_capture_minus_adverse": float(net),
            "immediate_adverse_to_notional_ratio": (
                float(row["immediate_adverse_selection"] / row["notional"])
                if row["notional"] > 0.0
                else 0.0
            ),
            "immediate_net_to_notional_ratio": (
                float(net / row["notional"]) if row["notional"] > 0.0 else 0.0
            ),
            "submit_stage_distribution": dict(
                sorted(row["submit_stage_distribution"].items(), key=lambda item: item[0])
            ),
            "fill_stage_distribution": dict(
                sorted(row["fill_stage_distribution"].items(), key=lambda item: item[0])
            ),
        }
        for key in (
            "submit_count",
            "fill_event_count",
            "notional",
            "immediate_fills_scored",
            "immediate_unscored_fill_count",
            "immediate_capture",
            "immediate_adverse_selection",
        ):
            total[key] += float(row[key])
        total["filled_order_ids"].update(row["filled_order_ids"])

    total_net = float(total["immediate_capture"] - total["immediate_adverse_selection"])
    return {
        "claim_boundary": (
            "report_only_decision_reference_lane_attribution; fill economics are scored against "
            "order_submit decision_reference_midpoint/midpoint, not token_id book joins"
        ),
        "by_lane": by_lane,
        "total": {
            "submit_count": float(total["submit_count"]),
            "fill_event_count": float(total["fill_event_count"]),
            "filled_order_count": float(len(total["filled_order_ids"])),
            "notional": float(total["notional"]),
            "immediate_fills_scored": float(total["immediate_fills_scored"]),
            "immediate_unscored_fill_count": float(total["immediate_unscored_fill_count"]),
            "immediate_capture": float(total["immediate_capture"]),
            "immediate_adverse_selection": float(total["immediate_adverse_selection"]),
            "immediate_capture_minus_adverse": float(total_net),
            "immediate_adverse_to_notional_ratio": (
                float(total["immediate_adverse_selection"] / total["notional"])
                if total["notional"] > 0.0
                else 0.0
            ),
            "immediate_net_to_notional_ratio": (
                float(total_net / total["notional"]) if total["notional"] > 0.0 else 0.0
            ),
        },
    }


def _taker_intent_gate_posture_matrix(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    config_fingerprint_distribution: Counter[str] = Counter()
    profile_distribution: Counter[str] = Counter()
    event_class_distribution: Counter[str] = Counter()
    event_type_distribution_by_intent: Dict[str, Counter[str]] = {}
    required_min_edge_by_intent: Dict[str, Dict[str, List[float]]] = {}
    min_new_exposure_sec_by_intent: Dict[str, Dict[str, List[float]]] = {}
    timing_window_by_intent: Dict[str, Counter[str]] = {}
    stage_distribution_by_intent: Dict[str, Counter[str]] = {}
    stage_window_checks = 0.0
    latest_stage_window_semantics: Dict[str, Any] = {}
    latest_stage_window_ts: str = ""
    normal_below_required_min_edge_count = 0.0
    recovery_below_required_min_edge_count = 0.0
    submit_events_by_intent: Counter[str] = Counter()

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _normalize_stage(value: Any) -> str:
        stage = str(value or "").strip().upper()
        return stage or "UNKNOWN"

    def _counter_for(mapping: Dict[str, Counter[str]], key: str) -> Counter[str]:
        counter = mapping.get(key)
        if isinstance(counter, Counter):
            return counter
        counter = Counter()
        mapping[key] = counter
        return counter

    def _append_metric(
        mapping: Dict[str, Dict[str, List[float]]],
        *,
        intent: str,
        stage: str,
        value: Any,
    ) -> None:
        parsed = _safe_float(value, default=-1.0)
        if parsed < 0.0:
            return
        by_stage = mapping.setdefault(intent, {})
        by_stage.setdefault(stage, []).append(float(parsed))

    def _summarize_values(values: List[float]) -> Dict[str, Any]:
        clean = sorted(float(value) for value in values if isinstance(value, (int, float)))
        if not clean:
            return {"count": 0.0, "min": 0.0, "p50": 0.0, "max": 0.0, "unique_values": []}
        unique_values: List[float] = []
        for value in clean:
            rounded = float(round(value, 9))
            if rounded not in unique_values:
                unique_values.append(rounded)
        return {
            "count": float(len(clean)),
            "min": float(clean[0]),
            "p50": float(_percentile(clean, 0.50)),
            "max": float(clean[-1]),
            "unique_values": unique_values[:12],
        }

    def _summarize_nested(mapping: Dict[str, Dict[str, List[float]]]) -> Dict[str, Dict[str, Any]]:
        return {
            intent: {
                stage: _summarize_values(values)
                for stage, values in sorted(by_stage.items(), key=lambda item: item[0])
            }
            for intent, by_stage in sorted(mapping.items(), key=lambda item: item[0])
        }

    def _is_recovery(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        for payload in (
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            _payload_dict(evt.get("risk_decision_basis")),
            evt,
        ):
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            if str(payload.get("reduce_only_recovery_reason") or "").strip():
                return True
        return False

    def _has_normal_payload(payload: Dict[str, Any]) -> bool:
        return any(
            key in payload
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    def _classify_taker_event(evt: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        event_type = str(evt.get("event_type") or "").strip()
        evaluation_scope = str(evt.get("evaluation_scope") or "").strip().lower()
        comp = _payload_dict(evt.get("taker_competitiveness"))
        if not comp:
            comp = _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness"))
        reason = str(evt.get("reason") or "").strip().lower()
        lane = str(evt.get("submission_lane") or "").strip().lower()
        is_taker_event = is_taker_decision_event_type(event_type) or is_taker_submit_event_type(event_type)
        is_taker_submit = _is_taker_submit_reason(reason) or lane == "taker"
        is_taker_edge_eval = event_type == "edge_evaluation" and evaluation_scope == "taker"
        if not is_taker_event and not is_taker_submit and not is_taker_edge_eval:
            return "", comp
        if _is_recovery(evt, comp):
            return "recovery_override", comp
        if _has_normal_payload(comp) or _has_normal_payload(evt):
            return "normal_competitiveness", comp
        if is_taker_edge_eval:
            return "normal_competitiveness", comp
        return "true_unknown", comp

    for evt in events:
        fingerprint = str(evt.get("config_fingerprint_sha256") or "").strip()
        if fingerprint:
            config_fingerprint_distribution[fingerprint] += 1
        profile = str(evt.get("profile_name") or "").strip()
        if profile:
            profile_distribution[profile] += 1

        event_type = str(evt.get("event_type") or "").strip()
        if is_taker_stage_window_semantic_check_event_type(event_type):
            stage_window_checks += 1.0
            latest_stage_window_ts = str(evt.get("ts_utc") or evt.get("timestamp_utc") or "")
            latest_stage_window_semantics = {
                "final_window_enabled": bool(evt.get("final_window_enabled", False)),
                "default_final_window_sec": _safe_float(evt.get("default_final_window_sec")),
                "semantic_dead_by_construction_count": _safe_float(
                    evt.get("semantic_dead_by_construction_count")
                ),
                "semantic_status": str(evt.get("semantic_status") or "unknown"),
                "stage_rows": evt.get("stage_rows") if isinstance(evt.get("stage_rows"), dict) else {},
            }
            continue

        intent, comp = _classify_taker_event(evt)
        if not intent:
            continue
        event_class_distribution[intent] += 1
        _counter_for(event_type_distribution_by_intent, intent)[event_type] += 1
        if event_type == "order_submit" or is_taker_submit_event_type(event_type):
            submit_events_by_intent[intent] += 1

        stage = _normalize_stage(comp.get("stage") or evt.get("stage"))
        _counter_for(stage_distribution_by_intent, intent)[stage] += 1
        timing_window = str(comp.get("timing_window_class") or evt.get("timing_window_class") or "").strip().lower()
        if timing_window:
            _counter_for(timing_window_by_intent, intent)[timing_window] += 1

        required_min_edge = (
            comp.get("required_min_edge")
            if "required_min_edge" in comp
            else evt.get("required_min_edge")
        )
        _append_metric(
            required_min_edge_by_intent,
            intent=intent,
            stage=stage,
            value=required_min_edge,
        )
        risk_basis = _payload_dict(evt.get("risk_decision_basis"))
        _append_metric(
            min_new_exposure_sec_by_intent,
            intent=intent,
            stage=stage,
            value=risk_basis.get("min_sec_to_expiry_for_new_exposure"),
        )

        edge_abs = _safe_float(comp.get("edge_abs", evt.get("edge_abs")), default=-1.0)
        if edge_abs < 0.0:
            edge_signed = _safe_float(
                comp.get("edge_abs", comp.get("edge", comp.get("edge_value", evt.get("edge", evt.get("edge_value"))))),
                default=-1.0,
            )
            if edge_signed >= 0.0:
                edge_abs = abs(float(edge_signed))
        required_edge = _safe_float(required_min_edge, default=-1.0)
        if edge_abs >= 0.0 and required_edge >= 0.0 and edge_abs + 1e-12 < required_edge:
            if intent == "recovery_override":
                recovery_below_required_min_edge_count += 1.0
            elif intent == "normal_competitiveness":
                normal_below_required_min_edge_count += 1.0

    normal_submit_count = float(submit_events_by_intent.get("normal_competitiveness", 0))
    recovery_submit_count = float(submit_events_by_intent.get("recovery_override", 0))
    unknown_submit_count = float(submit_events_by_intent.get("true_unknown", 0))
    normal_event_count = float(event_class_distribution.get("normal_competitiveness", 0))
    recovery_event_count = float(event_class_distribution.get("recovery_override", 0))
    unknown_event_count = float(event_class_distribution.get("true_unknown", 0))
    if normal_submit_count > 0.0 and recovery_submit_count > 0.0:
        observed_intent_classification = "mixed_normal_and_recovery_taker_activity_observed"
    elif recovery_submit_count > 0.0:
        observed_intent_classification = "recovery_only_taker_activity_observed"
    elif normal_submit_count > 0.0:
        observed_intent_classification = "normal_taker_activity_observed"
    elif unknown_submit_count > 0.0:
        observed_intent_classification = "unknown_taker_activity_observed"
    elif normal_event_count > 0.0 and recovery_event_count > 0.0:
        observed_intent_classification = "mixed_normal_and_recovery_taker_gate_activity_observed_no_submit"
    elif recovery_event_count > 0.0:
        observed_intent_classification = "recovery_only_taker_gate_activity_observed_no_submit"
    elif normal_event_count > 0.0:
        observed_intent_classification = "normal_taker_gate_activity_observed_no_submit"
    elif unknown_event_count > 0.0:
        observed_intent_classification = "unknown_taker_gate_activity_observed_no_submit"
    else:
        observed_intent_classification = "no_taker_activity_observed"

    return {
        "claim_boundary": (
            "report_only_taker_intent_gate_posture; values are derived from emitted run events "
            "and run fingerprints, not from current profile files"
        ),
        "observed_intent_classification": observed_intent_classification,
        "profile_distribution": dict(sorted(profile_distribution.items(), key=lambda item: item[0])),
        "config_fingerprint_distribution": dict(
            sorted(config_fingerprint_distribution.items(), key=lambda item: item[0])
        ),
        "event_class_distribution": dict(sorted(event_class_distribution.items(), key=lambda item: item[0])),
        "submit_event_distribution": dict(sorted(submit_events_by_intent.items(), key=lambda item: item[0])),
        "event_type_distribution_by_intent": {
            intent: dict(sorted(counter.items(), key=lambda item: item[0]))
            for intent, counter in sorted(event_type_distribution_by_intent.items(), key=lambda item: item[0])
        },
        "stage_distribution_by_intent": {
            intent: dict(sorted(counter.items(), key=lambda item: item[0]))
            for intent, counter in sorted(stage_distribution_by_intent.items(), key=lambda item: item[0])
        },
        "timing_window_distribution_by_intent": {
            intent: dict(sorted(counter.items(), key=lambda item: item[0]))
            for intent, counter in sorted(timing_window_by_intent.items(), key=lambda item: item[0])
        },
        "required_min_edge_by_intent_stage": _summarize_nested(required_min_edge_by_intent),
        "min_new_exposure_sec_by_intent_stage": _summarize_nested(min_new_exposure_sec_by_intent),
        "normal_below_required_min_edge_count": float(normal_below_required_min_edge_count),
        "recovery_override_below_required_min_edge_count": float(recovery_below_required_min_edge_count),
        "recovery_override_crossed_normal_edge_gate_observed": bool(
            recovery_below_required_min_edge_count > 0.0
        ),
        "stage_window_semantic_check_count": float(stage_window_checks),
        "latest_stage_window_semantic_check_ts": latest_stage_window_ts,
        "latest_stage_window_semantics": latest_stage_window_semantics,
    }


def _taker_doctrine_breach_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    block_reason_counts: Counter[str] = Counter()
    hard_window_submit_count = 0.0

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _is_taker_order_submit(evt: Dict[str, Any]) -> bool:
        reason = str(evt.get("reason") or "").strip().lower()
        lane = str(evt.get("submission_lane") or "").strip().lower()
        return bool(_is_taker_submit_reason(reason) or lane == "taker")

    def _is_recovery_submit(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        for payload in (
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            _payload_dict(evt.get("risk_decision_basis")),
            evt,
        ):
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            if str(payload.get("reduce_only_recovery_reason") or "").strip():
                return True
        return False

    def _has_normal_payload(comp: Dict[str, Any]) -> bool:
        return any(
            key in comp
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "edge_evaluation" and str(evt.get("evaluation_scope") or "").strip().lower() == "taker":
            reason = str(evt.get("block_reason") or "").strip().lower()
            if reason:
                block_reason_counts[reason] += 1
            continue

        if event_type != "order_submit" or not _is_taker_order_submit(evt):
            continue
        comp = _payload_dict(evt.get("taker_competitiveness"))
        if _is_recovery_submit(evt, comp):
            continue
        if not _has_normal_payload(comp):
            continue
        sec_to_expiry = _safe_float(comp.get("sec_to_expiry", evt.get("sec_to_expiry")), default=-1.0)
        if sec_to_expiry > 7.0 + 1e-9:
            hard_window_submit_count += 1.0

    return {
        "claim_boundary": (
            "report_only_doctrine_breach_counters; values are derived from emitted taker edge-evaluation "
            "and order-submit rows"
        ),
        "hard_window_submit_violation_count": float(hard_window_submit_count),
        "maker_to_taker_recovery_handoff_disabled_count": float(
            block_reason_counts.get("maker_to_taker_recovery_handoff_disabled", 0)
        ),
        "taker_recovery_disabled_in_taker_scope_count": float(
            block_reason_counts.get("taker_recovery_disabled_in_taker_scope", 0)
        ),
        "block_reason_distribution": dict(sorted(block_reason_counts.items(), key=lambda item: item[0])),
    }


def _taker_config_gate_posture(run_manifest: Dict[str, Any]) -> Dict[str, Any]:
    def _dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _optional_float(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    def _optional_bool(value: Any) -> Optional[bool]:
        parsed = _as_bool(value)
        if parsed is None:
            return None
        return bool(parsed)

    def _clean_number_mapping(value: Any) -> Dict[str, float]:
        if not isinstance(value, dict):
            return {}
        cleaned: Dict[str, float] = {}
        for key, raw in value.items():
            parsed = _optional_float(raw)
            if parsed is not None:
                cleaned[str(key)] = float(parsed)
        return dict(sorted(cleaned.items(), key=lambda item: item[0]))

    config = _dict(run_manifest.get("config"))
    if not config:
        return {
            "claim_boundary": (
                "report_only_manifest_config_gate_posture; no run manifest config snapshot was available"
            ),
            "config_present": False,
        }

    runtime = _dict(config.get("runtime"))
    risk = _dict(config.get("risk"))
    latency = _dict(config.get("latency_verifier"))
    taker = _dict(config.get("taker"))
    taker_comp = _dict(taker.get("competitiveness"))
    strategy = _dict(config.get("strategy"))
    sizing = _dict(config.get("sizing"))
    maker_comp = _dict(strategy.get("maker_competitiveness"))
    execution_quality = _dict(strategy.get("execution_quality"))
    dynamic_scaling = _dict(risk.get("dynamic_scaling"))

    held_reduce_only_sec = _optional_float(runtime.get("held_preexpiry_reduce_only_sec"))
    preexpiry_emergency_sec = _optional_float(runtime.get("preexpiry_emergency_taker_window_sec"))
    terminal_halt_new_risk_sec = _optional_float(runtime.get("terminal_unwind_halt_new_risk_sec"))
    min_new_exposure_sec_global = _optional_float(risk.get("min_sec_to_expiry_for_new_exposure"))
    min_new_exposure_sec_by_lane = _clean_number_mapping(risk.get("min_sec_to_expiry_for_new_exposure_by_lane"))
    min_new_exposure_sec = min_new_exposure_sec_by_lane.get("taker", min_new_exposure_sec_global)
    min_new_exposure_sec_source = "lane_override" if "taker" in min_new_exposure_sec_by_lane else "global"
    final_window_sec = _optional_float(taker_comp.get("final_window_sec"))
    stage_final_windows = _clean_number_mapping(taker_comp.get("stage_final_window_sec_by_stage"))

    boundary_values = [
        value
        for value in (
            held_reduce_only_sec,
            preexpiry_emergency_sec,
            terminal_halt_new_risk_sec,
            min_new_exposure_sec,
        )
        if value is not None
    ]
    boundaries_complete = len(boundary_values) == 4
    aligned_terminal_boundary = bool(
        boundaries_complete and max(boundary_values) - min(boundary_values) <= 1e-9
    )
    normal_can_open_inside_recovery = bool(
        held_reduce_only_sec is not None
        and min_new_exposure_sec is not None
        and min_new_exposure_sec < held_reduce_only_sec
    )
    if normal_can_open_inside_recovery:
        boundary_class = "normal_new_exposure_allowed_inside_held_recovery_window"
    elif aligned_terminal_boundary:
        boundary_class = "terminal_boundaries_aligned"
    elif boundaries_complete:
        boundary_class = "terminal_boundaries_not_aligned_no_overlap_detected"
    else:
        boundary_class = "terminal_boundary_config_incomplete"

    normal_allowed_final_window_by_stage: Dict[str, Dict[str, Any]] = {}
    stages = sorted(set(stage_final_windows.keys()) | ({"default"} if final_window_sec is not None else set()))
    final_window_overlap_stages: List[str] = []
    max_normal_entry_width_inside_final_window_sec = 0.0
    for stage in stages:
        stage_window = final_window_sec if stage == "default" else stage_final_windows.get(stage)
        allowed_width = None
        if stage_window is not None and min_new_exposure_sec is not None:
            allowed_width = float(max(0.0, stage_window - min_new_exposure_sec))
            if allowed_width > 1e-9:
                final_window_overlap_stages.append(str(stage))
                max_normal_entry_width_inside_final_window_sec = max(
                    max_normal_entry_width_inside_final_window_sec,
                    allowed_width,
                )
        normal_allowed_final_window_by_stage[stage] = {
            "effective_final_window_sec": stage_window,
            "min_sec_to_expiry_for_new_exposure": min_new_exposure_sec,
            "normal_entry_width_inside_final_window_sec": allowed_width,
        }
    normal_can_open_inside_taker_final_window = bool(final_window_overlap_stages)

    posture_flags: List[str] = []
    if _optional_bool(taker.get("require_lag_verification")) is False:
        posture_flags.append("taker_require_lag_verification_false")
    latency_hit_threshold = _optional_float(latency.get("hit_threshold_ms"))
    if latency_hit_threshold is not None and latency_hit_threshold <= 40.0:
        posture_flags.append("latency_verifier_hit_threshold_le_40ms")
    maker_score_min = _optional_float(latency.get("score_min_for_maker"))
    if maker_score_min is not None and maker_score_min <= 0.08:
        posture_flags.append("maker_latency_score_min_le_0p08")
    if final_window_sec is not None and final_window_sec >= 60.0:
        posture_flags.append("taker_default_final_window_ge_60s")
    mts_window = stage_final_windows.get("MAKER_TAKER_SELECTIVE")
    if mts_window is not None and mts_window >= 60.0:
        posture_flags.append("maker_taker_selective_final_window_ge_60s")
    if normal_can_open_inside_recovery:
        posture_flags.append("normal_entry_recovery_boundary_overlap")
    if normal_can_open_inside_taker_final_window:
        posture_flags.append("normal_entry_taker_final_window_overlap")
    if _optional_bool(dynamic_scaling.get("tod_enabled")) is True:
        posture_flags.append("risk_tod_dynamic_scaling_enabled")

    if normal_can_open_inside_taker_final_window and (
        min_new_exposure_sec_source == "lane_override" or not normal_can_open_inside_recovery
    ):
        boundary_class = "normal_new_exposure_allowed_inside_taker_final_window"

    return {
        "claim_boundary": (
            "report_only_manifest_config_gate_posture; values are copied from the run manifest "
            "config snapshot and do not assert optimality or causal provenance"
        ),
        "config_present": True,
        "run_id": str(run_manifest.get("run_id") or ""),
        "profile_name": str(run_manifest.get("profile_name") or ""),
        "git_commit": str(run_manifest.get("git_commit") or ""),
        "config_fingerprint_sha256": str(run_manifest.get("config_fingerprint_sha256") or ""),
        "config_source_sha256": str(run_manifest.get("config_source_sha256") or ""),
        "boundary_class": boundary_class,
        "boundary_alignment": {
            "held_preexpiry_reduce_only_sec": held_reduce_only_sec,
            "preexpiry_emergency_taker_window_sec": preexpiry_emergency_sec,
            "terminal_unwind_halt_new_risk_sec": terminal_halt_new_risk_sec,
            "min_sec_to_expiry_for_new_exposure": min_new_exposure_sec,
            "min_sec_to_expiry_for_new_exposure_global": min_new_exposure_sec_global,
            "min_sec_to_expiry_for_new_exposure_by_lane": min_new_exposure_sec_by_lane,
            "min_sec_to_expiry_for_new_exposure_source": min_new_exposure_sec_source,
            "aligned_terminal_boundary": aligned_terminal_boundary,
            "normal_can_open_inside_held_recovery_window": normal_can_open_inside_recovery,
            "normal_can_open_inside_taker_final_window": normal_can_open_inside_taker_final_window,
            "normal_taker_final_window_overlap_stages": list(final_window_overlap_stages),
            "max_normal_entry_width_inside_final_window_sec": (
                float(max_normal_entry_width_inside_final_window_sec)
                if normal_can_open_inside_taker_final_window
                else 0.0
            ),
        },
        "taker_lag_gate": {
            "require_lag_verification": _optional_bool(taker.get("require_lag_verification")),
            "lag_min_samples": _optional_float(taker.get("lag_min_samples")),
            "lag_min_median_ms": _optional_float(taker.get("lag_min_median_ms")),
            "lag_min_hit_rate": _optional_float(taker.get("lag_min_hit_rate")),
            "lag_hit_threshold_ms": _optional_float(taker.get("lag_hit_threshold_ms")),
            "max_chainlink_tick_age_sec": _optional_float(taker.get("max_chainlink_tick_age_sec")),
        },
        "latency_verifier": {
            "enabled": _optional_bool(latency.get("enabled")),
            "require_armed_for_maker": _optional_bool(latency.get("require_armed_for_maker")),
            "require_armed_for_taker": _optional_bool(latency.get("require_armed_for_taker")),
            "min_samples": _optional_float(latency.get("min_samples")),
            "hit_threshold_ms": latency_hit_threshold,
            "armed_min_median_ms": _optional_float(latency.get("armed_min_median_ms")),
            "armed_min_hit_rate": _optional_float(latency.get("armed_min_hit_rate")),
            "probation_min_median_ms": _optional_float(latency.get("probation_min_median_ms")),
            "probation_min_hit_rate": _optional_float(latency.get("probation_min_hit_rate")),
            "score_min_for_maker": maker_score_min,
            "score_min_for_taker": _optional_float(latency.get("score_min_for_taker")),
        },
        "normal_taker_entry_gates": {
            "min_sec_to_expiry_for_new_exposure": min_new_exposure_sec,
            "min_sec_to_expiry_for_new_exposure_global": min_new_exposure_sec_global,
            "min_sec_to_expiry_for_new_exposure_by_lane": min_new_exposure_sec_by_lane,
            "min_sec_to_expiry_for_new_exposure_source": min_new_exposure_sec_source,
            "default_min_edge": _optional_float(taker.get("min_edge")),
            "max_orders_per_cycle": _optional_float(taker.get("max_orders_per_cycle")),
            "per_token_cooldown_sec": _optional_float(taker.get("per_token_cooldown_sec")),
            "per_token_cooldown_sec_by_stage": _clean_number_mapping(
                taker.get("per_token_cooldown_sec_by_stage")
            ),
            "hard_min_target_usd": _optional_float(taker_comp.get("hard_min_target_usd")),
            "hard_min_enforcement": str(taker_comp.get("hard_min_enforcement") or ""),
            "dynamic_size_enabled": _optional_bool(taker_comp.get("dynamic_size_enabled")),
            "dynamic_size_edge_start_abs": _optional_float(taker_comp.get("dynamic_size_edge_start_abs")),
            "dynamic_size_edge_full_abs": _optional_float(taker_comp.get("dynamic_size_edge_full_abs")),
            "dynamic_size_target_usd_cap": _optional_float(taker_comp.get("dynamic_size_target_usd_cap")),
            "final_window_enabled": _optional_bool(taker_comp.get("final_window_enabled")),
            "final_window_sec": final_window_sec,
            "price_aggress_bps_max": _optional_float(taker_comp.get("price_aggress_bps_max")),
            "multi_oracle_boost_enabled": _optional_bool(taker_comp.get("multi_oracle_boost_enabled")),
            "multi_oracle_boost_window_sec": _optional_float(taker_comp.get("multi_oracle_boost_window_sec")),
            "multi_oracle_edge_threshold_abs": _optional_float(taker_comp.get("multi_oracle_edge_threshold_abs")),
            "multi_oracle_target_usd_cap": _optional_float(taker_comp.get("multi_oracle_target_usd_cap")),
            "multi_oracle_capital_pct_cap": _optional_float(taker_comp.get("multi_oracle_capital_pct_cap")),
        },
        "maker_gate_posture": {
            "timing_gate_enabled": _optional_bool(maker_comp.get("timing_gate_enabled")),
            "timing_gate_min_sec_to_expiry": _optional_float(maker_comp.get("timing_gate_min_sec_to_expiry")),
            "timing_gate_max_sec_to_expiry": _optional_float(maker_comp.get("timing_gate_max_sec_to_expiry")),
            "one_sided_enabled": _optional_bool(maker_comp.get("one_sided_enabled")),
            "one_sided_edge_threshold_abs": _optional_float(maker_comp.get("one_sided_edge_threshold_abs")),
            "maker_competitive_min_notional_usd": _optional_float(sizing.get("maker_competitive_min_notional_usd")),
            "maker_competitive_max_shares": _optional_float(sizing.get("maker_competitive_max_shares")),
            "min_expected_fill_prob": _optional_float(execution_quality.get("min_expected_fill_prob")),
            "max_queue_ahead_size": _optional_float(execution_quality.get("max_queue_ahead_size")),
            "reduce_only_recovery_min_expected_fill_prob_floor": _optional_float(
                execution_quality.get("reduce_only_recovery_min_expected_fill_prob_floor")
            ),
            "reduce_only_recovery_max_queue_ahead_size_multiplier": _optional_float(
                execution_quality.get("reduce_only_recovery_max_queue_ahead_size_multiplier")
            ),
        },
        "risk_dynamic_scaling": {
            "enabled": _optional_bool(dynamic_scaling.get("enabled")),
            "tod_enabled": _optional_bool(dynamic_scaling.get("tod_enabled")),
            "tod_start_hour_utc": _optional_float(dynamic_scaling.get("tod_start_hour_utc")),
            "tod_end_hour_utc": _optional_float(dynamic_scaling.get("tod_end_hour_utc")),
            "tod_thin_liquidity_mult": _optional_float(dynamic_scaling.get("tod_thin_liquidity_mult")),
            "edge_start_abs": _optional_float(dynamic_scaling.get("edge_start_abs")),
            "edge_full_abs": _optional_float(dynamic_scaling.get("edge_full_abs")),
            "edge_mult_max": _optional_float(dynamic_scaling.get("edge_mult_max")),
            "unknown_input_policy": str(dynamic_scaling.get("unknown_input_policy") or ""),
        },
        "posture_flags": posture_flags,
    }


def _preexpiry_recovery_churn_stats(events: List[Dict[str, Any]], *, adjacency_window_sec: float = 2.0) -> Dict[str, Any]:
    order_class_by_id: Dict[str, str] = {}
    order_submit_ts_by_id: Dict[str, dt.datetime] = {}
    normal_submit_sec_to_expiry: List[float] = []
    recovery_submit_sec_to_expiry: List[float] = []
    normal_fill_ts: List[dt.datetime] = []
    recovery_fill_ts: List[dt.datetime] = []
    held_preexpiry_values: List[float] = []
    min_new_exposure_values: List[float] = []
    normal_submit_inside_held_preexpiry_window = 0.0
    normal_submit_inside_allowed_overlap_window = 0.0
    normal_submit_count = 0.0
    recovery_submit_count = 0.0

    def _summary(values: List[float]) -> Dict[str, float]:
        points = [float(value) for value in values if isinstance(value, (int, float))]
        if not points:
            return {
                "count": 0.0,
                "min": 0.0,
                "p50": 0.0,
                "p90": 0.0,
                "max": 0.0,
                "mean": 0.0,
                "median": 0.0,
            }
        ordered = sorted(points)
        return {
            "count": float(len(ordered)),
            "min": float(ordered[0]),
            "p50": float(_percentile(ordered, 0.50)),
            "p90": float(_percentile(ordered, 0.90)),
            "max": float(ordered[-1]),
            "mean": float(sum(ordered) / len(ordered)),
            "median": float(median(ordered)),
        }

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _is_recovery_submit(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        for payload in (
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            _payload_dict(evt.get("risk_decision_basis")),
            evt,
        ):
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            if str(payload.get("reduce_only_recovery_reason") or "").strip():
                return True
        return False

    def _has_normal_payload(comp: Dict[str, Any]) -> bool:
        return any(
            key in comp
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "order_submit":
            order_id = str(evt.get("order_id") or "").strip()
            reason = str(evt.get("reason") or "").strip().lower()
            is_taker = _is_taker_submit_reason(reason)
            if not is_taker:
                if order_id:
                    order_class_by_id[order_id] = "maker"
                continue
            comp = _payload_dict(evt.get("taker_competitiveness"))
            risk_basis = _payload_dict(evt.get("risk_decision_basis"))
            sec_to_expiry = _safe_float(comp.get("sec_to_expiry", evt.get("sec_to_expiry")), default=-1.0)
            held_preexpiry = _safe_float(comp.get("held_preexpiry_reduce_only_sec"), default=-1.0)
            min_new_exposure = _safe_float(risk_basis.get("min_sec_to_expiry_for_new_exposure"), default=-1.0)
            if held_preexpiry >= 0.0:
                held_preexpiry_values.append(float(held_preexpiry))
            if min_new_exposure >= 0.0:
                min_new_exposure_values.append(float(min_new_exposure))
            submit_ts = parse_ts(evt.get("ts_utc"))
            if order_id and submit_ts is not None:
                order_submit_ts_by_id[order_id] = submit_ts
            if _is_recovery_submit(evt, comp):
                submit_class = "reduce_only_recovery_taker"
                recovery_submit_count += 1.0
                if sec_to_expiry >= 0.0:
                    recovery_submit_sec_to_expiry.append(float(sec_to_expiry))
            elif _has_normal_payload(comp):
                submit_class = "normal_taker"
                normal_submit_count += 1.0
                if sec_to_expiry >= 0.0:
                    normal_submit_sec_to_expiry.append(float(sec_to_expiry))
                if held_preexpiry > 0.0 and sec_to_expiry >= 0.0 and sec_to_expiry <= (held_preexpiry + 1e-9):
                    normal_submit_inside_held_preexpiry_window += 1.0
                if (
                    held_preexpiry > 0.0
                    and min_new_exposure >= 0.0
                    and sec_to_expiry > (min_new_exposure + 1e-9)
                    and sec_to_expiry <= (held_preexpiry + 1e-9)
                ):
                    normal_submit_inside_allowed_overlap_window += 1.0
            else:
                submit_class = "unknown_taker"
            if order_id:
                order_class_by_id[order_id] = submit_class
            continue

        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        submit_class = order_class_by_id.get(order_id, "")
        fill_ts = parse_ts(evt.get("ts_utc"))
        if fill_ts is None:
            continue
        if submit_class == "normal_taker":
            normal_fill_ts.append(fill_ts)
        elif submit_class == "reduce_only_recovery_taker":
            recovery_fill_ts.append(fill_ts)

    recovery_fill_ts = sorted(recovery_fill_ts)
    normal_with_recovery_within_window = 0.0
    for fill_ts in sorted(normal_fill_ts):
        for recovery_ts in recovery_fill_ts:
            delta = float((recovery_ts - fill_ts).total_seconds())
            if delta < 0.0:
                continue
            if delta > float(adjacency_window_sec):
                break
            normal_with_recovery_within_window += 1.0
            break

    observed_held_preexpiry = max(held_preexpiry_values) if held_preexpiry_values else 0.0
    observed_min_new_exposure = max(min_new_exposure_values) if min_new_exposure_values else 0.0
    overlap_detected = bool(
        normal_submit_inside_allowed_overlap_window > 0.0
        and observed_held_preexpiry > (observed_min_new_exposure + 1e-9)
    )
    adjacency_ratio = (
        float(normal_with_recovery_within_window / len(normal_fill_ts))
        if normal_fill_ts
        else 0.0
    )
    return {
        "claim_boundary": (
            "report_only_timing_diagnostic; recovery adjacency is temporal because redacted token ids "
            "can prevent exact token lineage claims"
        ),
        "adjacency_window_sec": float(adjacency_window_sec),
        "observed_held_preexpiry_reduce_only_sec_max": float(observed_held_preexpiry),
        "observed_min_sec_to_expiry_for_new_exposure_max": float(observed_min_new_exposure),
        "boundary_overlap_detected": bool(overlap_detected),
        "normal_taker_submit_count": float(normal_submit_count),
        "recovery_taker_submit_count": float(recovery_submit_count),
        "normal_taker_submit_inside_held_preexpiry_window_count": float(
            normal_submit_inside_held_preexpiry_window
        ),
        "normal_taker_submit_inside_allowed_overlap_window_count": float(
            normal_submit_inside_allowed_overlap_window
        ),
        "normal_taker_fill_count": float(len(normal_fill_ts)),
        "recovery_taker_fill_count": float(len(recovery_fill_ts)),
        "normal_taker_fill_with_recovery_fill_within_window_count": float(
            normal_with_recovery_within_window
        ),
        "normal_taker_fill_with_recovery_fill_within_window_ratio": float(adjacency_ratio),
        "normal_taker_submit_sec_to_expiry": _summary(normal_submit_sec_to_expiry),
        "recovery_taker_submit_sec_to_expiry": _summary(recovery_submit_sec_to_expiry),
    }


def _terminal_handoff_deadband_stats(
    events: List[Dict[str, Any]],
    *,
    taker_config_gate_posture: Dict[str, Any],
) -> Dict[str, Any]:
    def _optional_float_local(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    boundary = (
        taker_config_gate_posture.get("boundary_alignment", {})
        if isinstance(taker_config_gate_posture, dict)
        else {}
    )
    maker_gate = (
        taker_config_gate_posture.get("maker_gate_posture", {})
        if isinstance(taker_config_gate_posture, dict)
        else {}
    )

    held_preexpiry_reduce_only_sec = _optional_float_local(boundary.get("held_preexpiry_reduce_only_sec"))
    preexpiry_emergency_taker_window_sec = _optional_float_local(
        boundary.get("preexpiry_emergency_taker_window_sec")
    )
    maker_timing_gate_min_sec_to_expiry = _optional_float_local(
        maker_gate.get("timing_gate_min_sec_to_expiry")
    )
    maker_timing_gate_max_sec_to_expiry = _optional_float_local(
        maker_gate.get("timing_gate_max_sec_to_expiry")
    )

    config_complete = all(
        value is not None
        for value in (
            held_preexpiry_reduce_only_sec,
            preexpiry_emergency_taker_window_sec,
            maker_timing_gate_min_sec_to_expiry,
        )
    )
    maker_gate_closes_at_reduce_only_boundary = bool(
        config_complete
        and abs(
            float(maker_timing_gate_min_sec_to_expiry) - float(held_preexpiry_reduce_only_sec)
        )
        <= 1e-9
    )

    action_distribution: Counter[str] = Counter()
    block_reason_distribution: Counter[str] = Counter()
    stage_distribution: Counter[str] = Counter()
    allowance_distribution: Counter[str] = Counter()
    candidate_sec_to_expiry: List[float] = []
    candidate_eval_count = 0.0
    waiting_for_maker_exit_count = 0.0
    action_taker_count = 0.0

    if config_complete:
        for evt in events:
            if str(evt.get("event_type") or "").strip() != "edge_evaluation":
                continue
            if str(evt.get("evaluation_scope") or "").strip().lower() != "taker":
                continue
            if _as_bool(evt.get("reduce_only_recovery_active")) is not True:
                continue
            sec_to_expiry = _safe_float(
                evt.get("time_remaining_sec", evt.get("sec_to_expiry")),
                default=-1.0,
            )
            if sec_to_expiry < 0.0:
                continue
            if not (
                float(preexpiry_emergency_taker_window_sec)
                < float(sec_to_expiry)
                <= float(held_preexpiry_reduce_only_sec)
            ):
                continue
            candidate_eval_count += 1.0
            candidate_sec_to_expiry.append(float(sec_to_expiry))
            action_taken = str(evt.get("action_taken") or "").strip().lower() or "unknown"
            block_reason = str(evt.get("block_reason") or "").strip().lower() or "none"
            stage_value = str(evt.get("stage") or "").strip().upper() or "UNKNOWN"
            maker_allowed = str(evt.get("maker_allowed")).strip().lower() or "unknown"
            taker_allowed = str(evt.get("taker_allowed")).strip().lower() or "unknown"
            action_distribution[action_taken] += 1
            block_reason_distribution[block_reason] += 1
            stage_distribution[stage_value] += 1
            allowance_distribution[f"maker_{maker_allowed}_taker_{taker_allowed}"] += 1
            if block_reason == "reduce_only_recovery_waiting_for_maker_exit":
                waiting_for_maker_exit_count += 1.0
            if action_taken == "taker":
                action_taker_count += 1.0

    classification = "config_incomplete"
    if config_complete:
        if candidate_eval_count <= 0.0:
            classification = "no_recovery_eval_in_candidate_window"
        elif waiting_for_maker_exit_count <= 0.0:
            classification = "candidate_window_without_waiting_block"
        elif action_taker_count > 0.0:
            classification = "mixed_activity_in_candidate_window"
        elif maker_gate_closes_at_reduce_only_boundary:
            classification = "wait_only_deadband_candidate"
        else:
            classification = "waiting_block_before_emergency_window"

    def _summary(values: List[float]) -> Dict[str, float]:
        points = [float(value) for value in values if isinstance(value, (int, float))]
        if not points:
            return {"count": 0.0, "min": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
        ordered = sorted(points)
        return {
            "count": float(len(ordered)),
            "min": float(ordered[0]),
            "p50": float(_percentile(ordered, 0.50)),
            "p90": float(_percentile(ordered, 0.90)),
            "max": float(ordered[-1]),
        }

    return {
        "claim_boundary": (
            "report_only_terminal_handoff_deadband; this is a timing-and-authority diagnostic over "
            "recovery-active taker edge-evaluation rows, not a profitability verdict"
        ),
        "config_complete": bool(config_complete),
        "held_preexpiry_reduce_only_sec": held_preexpiry_reduce_only_sec,
        "preexpiry_emergency_taker_window_sec": preexpiry_emergency_taker_window_sec,
        "maker_timing_gate_min_sec_to_expiry": maker_timing_gate_min_sec_to_expiry,
        "maker_timing_gate_max_sec_to_expiry": maker_timing_gate_max_sec_to_expiry,
        "maker_gate_closes_at_reduce_only_boundary": bool(maker_gate_closes_at_reduce_only_boundary),
        "candidate_recovery_edge_eval_count": float(candidate_eval_count),
        "waiting_for_maker_exit_count": float(waiting_for_maker_exit_count),
        "action_taker_count": float(action_taker_count),
        "action_distribution": dict(sorted(action_distribution.items(), key=lambda item: item[0])),
        "block_reason_distribution": dict(sorted(block_reason_distribution.items(), key=lambda item: item[0])),
        "stage_distribution": dict(sorted(stage_distribution.items(), key=lambda item: item[0])),
        "allowance_distribution": dict(sorted(allowance_distribution.items(), key=lambda item: item[0])),
        "candidate_sec_to_expiry": _summary(candidate_sec_to_expiry),
        "classification": classification,
    }


def _recovery_cost_benefit_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    recovery_orders: Dict[str, Dict[str, Any]] = {}
    submit_class_distribution: Counter[str] = Counter()
    fill_class_distribution: Counter[str] = Counter()
    submit_refinement_class_distribution: Counter[str] = Counter()
    fill_refinement_class_distribution: Counter[str] = Counter()
    fill_side_distribution: Counter[str] = Counter()
    fill_stage_distribution: Counter[str] = Counter()
    fill_posture_distribution: Counter[str] = Counter()
    fill_reason_distribution: Counter[str] = Counter()
    emergency_outcome_distribution: Counter[str] = Counter()
    emergency_block_reason_distribution: Counter[str] = Counter()
    emergency_block_class_distribution: Counter[str] = Counter()
    emergency_maker_no_submission_distribution: Counter[str] = Counter()
    emergency_filled_maker_no_submission_distribution: Counter[str] = Counter()
    emergency_blocked_maker_no_submission_distribution: Counter[str] = Counter()
    recovery_taker_edge_block_reason_distribution: Counter[str] = Counter()
    recovery_taker_edge_action_distribution: Counter[str] = Counter()
    recovery_taker_edge_stage_distribution: Counter[str] = Counter()
    recovery_taker_edge_allowance_distribution: Counter[str] = Counter()
    submit_sec_to_expiry: List[float] = []
    fill_sec_to_expiry: List[float] = []
    recovery_taker_edge_sec_to_expiry: List[float] = []
    fill_size_cap_shares: List[float] = []
    fill_net_shares_abs: List[float] = []
    fill_cost_to_notional_ratios: List[float] = []
    fill_net_to_notional_ratios: List[float] = []
    fill_count = 0.0
    unlinked_fill_count = 0.0
    immediate_scored = 0.0
    immediate_unscored = 0.0
    immediate_capture = 0.0
    immediate_adverse = 0.0
    fill_notional = 0.0
    submitted_notional = 0.0
    emergency_attempt_count = 0.0
    emergency_fill_count = 0.0
    emergency_block_count = 0.0
    emergency_maker_blocked_count = 0.0

    def _summary(values: List[float]) -> Dict[str, float]:
        points = [float(value) for value in values if isinstance(value, (int, float))]
        if not points:
            return {"count": 0.0, "min": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
        ordered = sorted(points)
        return {
            "count": float(len(ordered)),
            "min": float(ordered[0]),
            "p50": float(_percentile(ordered, 0.50)),
            "p90": float(_percentile(ordered, 0.90)),
            "max": float(ordered[-1]),
        }

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _norm(value: Any, default: str = "unknown") -> str:
        text = str(value or "").strip().lower()
        return text or default

    def _bool_label(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return _norm(value)

    def _stage(value: Any) -> str:
        text = str(value or "").strip().upper()
        return text or "UNKNOWN"

    def _is_recovery_submit(evt: Dict[str, Any], comp: Dict[str, Any], risk_basis: Dict[str, Any]) -> bool:
        for payload in (
            comp,
            _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness")),
            risk_basis,
            evt,
        ):
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            if str(payload.get("reduce_only_recovery_reason") or "").strip():
                return True
        return False

    def _first_float(*values: Any, default: float = 0.0) -> float:
        for value in values:
            parsed = _safe_float(value, default=-1.0)
            if parsed >= 0.0:
                return float(parsed)
        return float(default)

    def _recovery_class(*, exposure_class: str, below_min_size: bool, net_abs: float, min_size: float, notional: float) -> str:
        if str(exposure_class).strip().upper() in {"DUST_ELIGIBLE", "DUST_QUARANTINED"}:
            return "tiny_or_dust_recovery_exit"
        if bool(below_min_size):
            return "tiny_or_dust_recovery_exit"
        if min_size > 0.0 and net_abs > 0.0 and net_abs < (min_size - 1e-9):
            return "tiny_or_dust_recovery_exit"
        if str(exposure_class).strip().upper() == "MEANINGFUL":
            return "meaningful_recovery_exit"
        if notional > 0.0 and (min_size <= 0.0 or net_abs >= (min_size - 1e-9)):
            return "meaningful_recovery_exit"
        return "unknown_recovery_exit"

    def _refinement_class(
        *,
        recovery_class: str,
        reason: str,
        posture: str,
        sec_to_expiry: float,
    ) -> str:
        if str(recovery_class or "") == "tiny_or_dust_recovery_exit":
            return "dust_or_below_min_exit"
        normalized_reason = str(reason or "").strip().lower()
        normalized_posture = str(posture or "").strip().upper()
        if normalized_reason == "expired_reduce_only_grace_active":
            return "necessary_expired_grace_exit"
        if (
            normalized_reason == "preexpiry_reduce_only_window_active"
            and normalized_posture in {"PREEXPIRY_REDUCE_ONLY", "HALT_NEW_RISK", "HARD_DEGRADED_REDUCE_ONLY"}
            and float(sec_to_expiry) >= 0.0
        ):
            return "necessary_terminal_risk_exit"
        return "unknown_recovery_exit"

    def _emergency_block_class(block_reason: str) -> str:
        normalized = str(block_reason or "").strip().lower()
        if normalized in {
            "risk_reject_size_too_small",
            "reduce_only_recovery_size_cap_below_min_order_size",
        }:
            return "blocked_dust_or_below_min"
        if normalized in {"reduce_only_recovery_no_reducing_side", "reduce_only_recovery_size_cap_unavailable"}:
            return "blocked_flat_or_no_reducing_side"
        if normalized == "taker_token_cooldown":
            return "blocked_recovery_cooldown"
        if normalized.startswith("quote_quality_skip"):
            return "blocked_passive_quality"
        return "blocked_other"

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "order_submit":
            continue
        reason = str(evt.get("reason") or "").strip().lower()
        lane = str(evt.get("submission_lane") or "").strip().lower()
        if (not _is_taker_submit_reason(reason)) and lane != "taker":
            continue
        comp = _payload_dict(evt.get("taker_competitiveness"))
        if not comp:
            comp = _payload_dict(_payload_dict(evt.get("size_resolution")).get("taker_competitiveness"))
        risk_basis = _payload_dict(evt.get("risk_decision_basis"))
        if not _is_recovery_submit(evt, comp, risk_basis):
            continue

        order_id = str(evt.get("order_id") or "").strip()
        if not order_id:
            continue
        price = _safe_float(evt.get("price"), default=0.0)
        size = _safe_float(evt.get("size"), default=0.0)
        order_notional = (
            float(price * size)
            if price > 0.0 and size > 0.0
            else _safe_float(_payload_dict(evt.get("size_resolution")).get("resolved_notional_usd"), default=0.0)
        )
        sec_to_expiry = _first_float(comp.get("sec_to_expiry"), evt.get("sec_to_expiry"), risk_basis.get("sec_to_expiry"), default=-1.0)
        min_size = _safe_float(comp.get("reduce_only_min_order_size_shares"), default=0.0)
        size_cap = _safe_float(comp.get("reduce_only_size_cap_shares", evt.get("reduce_only_size_cap_shares")), default=0.0)
        net_abs = abs(_safe_float(comp.get("reduce_only_net_shares"), default=0.0))
        exposure_class = str(risk_basis.get("intent_exposure_class") or "UNKNOWN").strip().upper() or "UNKNOWN"
        below_min_size = bool(_as_bool(comp.get("reduce_only_size_cap_below_min_order_size")) is True)
        recovery_class = _recovery_class(
            exposure_class=exposure_class,
            below_min_size=below_min_size,
            net_abs=float(net_abs),
            min_size=float(min_size),
            notional=float(order_notional),
        )
        reason_value = _norm(comp.get("reduce_only_recovery_reason"), default="unknown")
        posture_value = _stage(comp.get("financial_posture_class") or evt.get("financial_posture_class"))
        refinement_class = _refinement_class(
            recovery_class=str(recovery_class),
            reason=str(reason_value),
            posture=str(posture_value),
            sec_to_expiry=float(sec_to_expiry),
        )
        submit_class_distribution[recovery_class] += 1
        submit_refinement_class_distribution[refinement_class] += 1
        if sec_to_expiry >= 0.0:
            submit_sec_to_expiry.append(float(sec_to_expiry))
        submitted_notional += float(order_notional)
        recovery_orders[order_id] = {
            "class": recovery_class,
            "refinement_class": refinement_class,
            "stage": _stage(comp.get("stage") or evt.get("stage")),
            "posture": posture_value,
            "reason": reason_value,
            "side": str(evt.get("side") or "").strip().upper(),
            "decision_midpoint": _safe_float(evt.get("decision_reference_midpoint", evt.get("midpoint")), default=-1.0),
            "sec_to_expiry": float(sec_to_expiry),
            "size_cap": float(size_cap),
            "net_abs": float(net_abs),
            "notional": float(order_notional),
        }

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "preexpiry_emergency_taker_unwind":
            event_weight = max(1.0, _safe_float(evt.get("repeat_count_delta"), 1.0))
            emergency_attempt_count += event_weight
            outcome = _norm(evt.get("outcome"))
            emergency_outcome_distribution[outcome] += int(event_weight)
            if outcome == "filled":
                emergency_fill_count += event_weight
            elif outcome == "blocked":
                emergency_block_count += event_weight
                block_reason = _norm(
                    evt.get("blocked_reason")
                    or evt.get("taker_submit_reject_reason")
                    or evt.get("reason")
                    or evt.get("outcome_reason")
                )
                if block_reason.startswith("blocked_"):
                    block_reason = block_reason[len("blocked_") :]
                emergency_block_reason_distribution[block_reason] += int(event_weight)
                emergency_block_class_distribution[_emergency_block_class(block_reason)] += int(event_weight)
            if _as_bool(evt.get("maker_reduce_only_exit_blocked")) is True:
                emergency_maker_blocked_count += 1.0
            maker_reason = _norm(evt.get("maker_no_submission_reason"), default="")
            if maker_reason:
                emergency_maker_no_submission_distribution[maker_reason] += 1
                if outcome == "filled":
                    emergency_filled_maker_no_submission_distribution[maker_reason] += 1
                elif outcome == "blocked":
                    emergency_blocked_maker_no_submission_distribution[maker_reason] += 1
            continue

        if event_type == "edge_evaluation":
            if str(evt.get("evaluation_scope") or "").strip().lower() == "taker" and _as_bool(
                evt.get("reduce_only_recovery_active")
            ) is True:
                block_reason = _norm(evt.get("block_reason"), default="none")
                action_taken = _norm(evt.get("action_taken"))
                stage_value = _stage(evt.get("stage"))
                maker_allowed = _norm(evt.get("maker_allowed"), default="unknown")
                taker_allowed = _norm(evt.get("taker_allowed"), default="unknown")
                recovery_taker_edge_block_reason_distribution[block_reason] += 1
                recovery_taker_edge_action_distribution[action_taken] += 1
                recovery_taker_edge_stage_distribution[stage_value] += 1
                recovery_taker_edge_allowance_distribution[
                    f"maker_{maker_allowed}_taker_{taker_allowed}"
                ] += 1
                sec_to_expiry = _first_float(
                    evt.get("time_remaining_sec"),
                    evt.get("sec_to_expiry"),
                    default=-1.0,
                )
                if sec_to_expiry >= 0.0:
                    recovery_taker_edge_sec_to_expiry.append(float(sec_to_expiry))
            continue

        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        order = recovery_orders.get(order_id)
        if not isinstance(order, dict):
            continue
        fill_count += 1.0
        price = _safe_float(evt.get("price"), default=-1.0)
        size = _safe_float(evt.get("size"), default=-1.0)
        side = str(evt.get("side") or order.get("side") or "").strip().upper()
        notional = float(price * size) if price > 0.0 and size > 0.0 else 0.0
        fill_notional += float(notional)
        fill_class_distribution[str(order.get("class") or "unknown_recovery_exit")] += 1
        fill_refinement_class_distribution[str(order.get("refinement_class") or "unknown_recovery_exit")] += 1
        fill_side_distribution[side or "UNKNOWN"] += 1
        fill_stage_distribution[str(order.get("stage") or "UNKNOWN")] += 1
        fill_posture_distribution[str(order.get("posture") or "UNKNOWN")] += 1
        fill_reason_distribution[str(order.get("reason") or "unknown")] += 1
        sec_to_expiry = _safe_float(order.get("sec_to_expiry"), default=-1.0)
        if sec_to_expiry >= 0.0:
            fill_sec_to_expiry.append(float(sec_to_expiry))
        size_cap = _safe_float(order.get("size_cap"), default=0.0)
        net_abs = _safe_float(order.get("net_abs"), default=0.0)
        if size_cap > 0.0:
            fill_size_cap_shares.append(float(size_cap))
        if net_abs > 0.0:
            fill_net_shares_abs.append(float(net_abs))
        midpoint = _safe_float(order.get("decision_midpoint"), default=-1.0)
        if price <= 0.0 or size <= 0.0 or midpoint <= 0.0 or side not in {"BUY", "SELL"}:
            immediate_unscored += 1.0
            continue
        delta = (midpoint - price) * size if side == "BUY" else (price - midpoint) * size
        immediate_scored += 1.0
        if delta >= 0.0:
            immediate_capture += float(delta)
        else:
            adverse = abs(float(delta))
            immediate_adverse += adverse
            if notional > 0.0:
                fill_cost_to_notional_ratios.append(float(adverse / notional))
        if notional > 0.0:
            fill_net_to_notional_ratios.append(float(delta / notional))

    recovery_order_ids = set(recovery_orders.keys())
    filled_recovery_order_ids = {
        str(evt.get("order_id") or "").strip()
        for evt in events
        if str(evt.get("event_type") or "").strip() == "fill"
        and str(evt.get("order_id") or "").strip() in recovery_order_ids
    }
    unlinked_fill_count = float(max(0, len(filled_recovery_order_ids) - int(fill_count)))
    immediate_net = float(immediate_capture - immediate_adverse)
    return {
        "claim_boundary": (
            "report_only_recovery_cost_benefit; fill economics are scored against the order_submit "
            "decision_reference_midpoint to avoid redacted-token book joins; this is not final settlement PnL"
        ),
        "recovery_submit_count": float(len(recovery_orders)),
        "recovery_filled_order_count": float(len(filled_recovery_order_ids)),
        "recovery_fill_event_count": float(fill_count),
        "recovery_unlinked_fill_count": float(unlinked_fill_count),
        "submitted_notional": float(submitted_notional),
        "fill_notional": float(fill_notional),
        "immediate_fills_scored": float(immediate_scored),
        "immediate_unscored_fill_count": float(immediate_unscored),
        "immediate_capture": float(immediate_capture),
        "immediate_adverse_selection": float(immediate_adverse),
        "immediate_capture_minus_adverse": float(immediate_net),
        "immediate_adverse_to_notional_ratio": (
            float(immediate_adverse / fill_notional) if fill_notional > 0.0 else 0.0
        ),
        "immediate_net_to_notional_ratio": (
            float(immediate_net / fill_notional) if fill_notional > 0.0 else 0.0
        ),
        "per_fill_adverse_to_notional_ratio": _summary(fill_cost_to_notional_ratios),
        "per_fill_net_to_notional_ratio": _summary(fill_net_to_notional_ratios),
        "submit_class_distribution": dict(sorted(submit_class_distribution.items(), key=lambda item: item[0])),
        "fill_class_distribution": dict(sorted(fill_class_distribution.items(), key=lambda item: item[0])),
        "submit_refinement_class_distribution": dict(
            sorted(submit_refinement_class_distribution.items(), key=lambda item: item[0])
        ),
        "fill_refinement_class_distribution": dict(
            sorted(fill_refinement_class_distribution.items(), key=lambda item: item[0])
        ),
        "fill_side_distribution": dict(sorted(fill_side_distribution.items(), key=lambda item: item[0])),
        "fill_stage_distribution": dict(sorted(fill_stage_distribution.items(), key=lambda item: item[0])),
        "fill_financial_posture_distribution": dict(
            sorted(fill_posture_distribution.items(), key=lambda item: item[0])
        ),
        "fill_reason_distribution": dict(sorted(fill_reason_distribution.items(), key=lambda item: item[0])),
        "submit_sec_to_expiry": _summary(submit_sec_to_expiry),
        "fill_sec_to_expiry": _summary(fill_sec_to_expiry),
        "fill_size_cap_shares": _summary(fill_size_cap_shares),
        "fill_net_shares_abs": _summary(fill_net_shares_abs),
        "preexpiry_emergency_attempt_count": float(emergency_attempt_count),
        "preexpiry_emergency_fill_count": float(emergency_fill_count),
        "preexpiry_emergency_block_count": float(emergency_block_count),
        "preexpiry_emergency_maker_blocked_count": float(emergency_maker_blocked_count),
        "preexpiry_emergency_outcome_distribution": dict(
            sorted(emergency_outcome_distribution.items(), key=lambda item: item[0])
        ),
        "preexpiry_emergency_block_reason_distribution": dict(
            sorted(emergency_block_reason_distribution.items(), key=lambda item: item[0])
        ),
        "preexpiry_emergency_block_class_distribution": dict(
            sorted(emergency_block_class_distribution.items(), key=lambda item: item[0])
        ),
        "preexpiry_emergency_maker_no_submission_distribution": dict(
            sorted(emergency_maker_no_submission_distribution.items(), key=lambda item: item[0])
        ),
        "preexpiry_emergency_filled_maker_no_submission_distribution": dict(
            sorted(emergency_filled_maker_no_submission_distribution.items(), key=lambda item: item[0])
        ),
        "preexpiry_emergency_blocked_maker_no_submission_distribution": dict(
            sorted(emergency_blocked_maker_no_submission_distribution.items(), key=lambda item: item[0])
        ),
        "recovery_taker_edge_eval_count": float(
            sum(recovery_taker_edge_action_distribution.values())
        ),
        "recovery_taker_edge_block_reason_distribution": dict(
            sorted(recovery_taker_edge_block_reason_distribution.items(), key=lambda item: item[0])
        ),
        "recovery_taker_edge_action_distribution": dict(
            sorted(recovery_taker_edge_action_distribution.items(), key=lambda item: item[0])
        ),
        "recovery_taker_edge_stage_distribution": dict(
            sorted(recovery_taker_edge_stage_distribution.items(), key=lambda item: item[0])
        ),
        "recovery_taker_edge_allowance_distribution": dict(
            sorted(recovery_taker_edge_allowance_distribution.items(), key=lambda item: item[0])
        ),
        "recovery_taker_edge_sec_to_expiry": _summary(recovery_taker_edge_sec_to_expiry),
    }


def _maker_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    timing_gate_blocked_edge_eval = 0.0
    timing_gate_blocked_decision = 0.0
    one_sided_decision_buy = 0.0
    one_sided_decision_sell = 0.0
    one_sided_submit_buy = 0.0
    one_sided_submit_sell = 0.0
    queue_pressure_candidate_count = 0.0
    queue_pressure_adopted_count = 0.0
    queue_pressure_gate_conversion_count = 0.0
    queue_pressure_replace_guard_blocked_count = 0.0
    edge_bucket_submit = Counter()
    edge_bucket_fill = Counter()
    aggressiveness_application_counts = Counter()
    order_bucket_by_id: Dict[str, str] = {}

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type == "edge_evaluation":
            if (
                str(evt.get("evaluation_scope") or "").strip().lower() == "maker"
                and str(evt.get("action_taken") or "").strip().lower() == "none"
                and str(evt.get("block_reason") or "").strip().lower() == "maker_timing_gate_closed"
            ):
                timing_gate_blocked_edge_eval += 1.0
            continue

        if event_type == "maker_competitiveness_decision":
            if bool(evt.get("timing_gate_blocked", False)):
                timing_gate_blocked_decision += 1.0
            if bool(evt.get("one_sided_active", False)):
                policy = str(evt.get("side_policy") or "").strip().upper()
                if policy == "BUY_ONLY":
                    one_sided_decision_buy += 1.0
                elif policy == "SELL_ONLY":
                    one_sided_decision_sell += 1.0
            continue

        if event_type == "maker_queue_pressure_adjustment":
            queue_pressure_candidate_count += 1.0
            if bool(evt.get("adopted", False)):
                queue_pressure_adopted_count += 1.0
            if bool(evt.get("gate_conversion", False)):
                queue_pressure_gate_conversion_count += 1.0
            if bool(evt.get("replace_guard_blocked", False)):
                queue_pressure_replace_guard_blocked_count += 1.0
            continue

        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            if _is_taker_submit_reason(reason):
                continue
            comp = evt.get("maker_competitiveness")
            if not isinstance(comp, dict):
                continue
            bucket = str(comp.get("edge_bucket") or "unknown").strip().lower() or "unknown"
            edge_bucket_submit[bucket] += 1
            order_id = str(evt.get("order_id") or "").strip()
            if order_id:
                order_bucket_by_id[order_id] = bucket

            if _safe_float(comp.get("size_multiplier_competitiveness"), 1.0) > 1.0:
                aggressiveness_application_counts["size_scaled"] += 1
            if _safe_float(comp.get("spread_multiplier_competitiveness"), 1.0) < 1.0:
                aggressiveness_application_counts["spread_tightened"] += 1
            if _safe_float(comp.get("requote_delta_multiplier_competitiveness"), 1.0) < 1.0:
                aggressiveness_application_counts["requote_tightened"] += 1

            if bool(comp.get("one_sided_active", False)):
                policy = str(comp.get("side_policy") or "").strip().upper()
                if policy == "BUY_ONLY":
                    one_sided_submit_buy += 1.0
                elif policy == "SELL_ONLY":
                    one_sided_submit_sell += 1.0
            continue

        if event_type == "fill":
            order_id = str(evt.get("order_id") or "").strip()
            if not order_id:
                continue
            bucket = order_bucket_by_id.get(order_id)
            if bucket:
                edge_bucket_fill[bucket] += 1

    return {
        "timing_gate_blocked_count_edge_eval": float(timing_gate_blocked_edge_eval),
        "timing_gate_blocked_count_decision": float(timing_gate_blocked_decision),
        "one_sided_activation_decision_buy_count": float(one_sided_decision_buy),
        "one_sided_activation_decision_sell_count": float(one_sided_decision_sell),
        "one_sided_activation_submit_buy_count": float(one_sided_submit_buy),
        "one_sided_activation_submit_sell_count": float(one_sided_submit_sell),
        "maker_queue_pressure_candidate_count": float(queue_pressure_candidate_count),
        "maker_queue_pressure_adopted_count": float(queue_pressure_adopted_count),
        "maker_queue_pressure_gate_conversion_count": float(queue_pressure_gate_conversion_count),
        "maker_queue_pressure_replace_guard_blocked_count": float(
            queue_pressure_replace_guard_blocked_count
        ),
        "maker_submit_edge_bucket_distribution": dict(
            sorted(edge_bucket_submit.items(), key=lambda item: item[0])
        ),
        "maker_fill_edge_bucket_distribution": dict(
            sorted(edge_bucket_fill.items(), key=lambda item: item[0])
        ),
        "aggressiveness_application_counts": dict(
            sorted(aggressiveness_application_counts.items(), key=lambda item: item[0])
        ),
    }


def _maker_complete_outcome_rates(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    complete_rows = [
        row
        for row in records
        if str(row.get("submission_lane_truth") or "").strip().lower() == "maker"
        and str(row.get("outcome_truth_status") or "").strip().lower() == "complete"
    ]
    complete_count = float(len(complete_rows))
    correct_count = float(
        sum(1 for row in complete_rows if str(row.get("decision_quality") or "").strip().lower() == "correct")
    )
    incorrect_count = float(
        sum(1 for row in complete_rows if str(row.get("decision_quality") or "").strip().lower() == "incorrect")
    )
    neutral_count = float(
        sum(1 for row in complete_rows if str(row.get("decision_quality") or "").strip().lower() == "neutral")
    )
    if complete_count <= 0.0:
        return {
            "maker_complete_count": 0.0,
            "maker_complete_correct_rate": 0.0,
            "maker_complete_incorrect_rate": 0.0,
            "maker_complete_neutral_rate": 0.0,
        }
    return {
        "maker_complete_count": float(complete_count),
        "maker_complete_correct_rate": float(correct_count / complete_count),
        "maker_complete_incorrect_rate": float(incorrect_count / complete_count),
        "maker_complete_neutral_rate": float(neutral_count / complete_count),
    }


def _maker_fight_admission_population_class(row: Dict[str, Any]) -> str:
    posture = str(row.get("financial_posture_class") or "").strip().upper()
    if bool(row.get("reduce_only_recovery_active", False)):
        return "external_blocked"
    if posture and posture not in {"NORMAL", "UNKNOWN"}:
        return "external_blocked"
    if posture != "NORMAL":
        return "truth_thin"
    if str(row.get("market_reference_class") or "").strip().lower() != "authoritative":
        return "truth_thin"
    required_numeric_fields = (
        "queue_delta_shares",
        "fill_prob_margin",
        "same_target_side_shadow_count_prior",
    )
    for field in required_numeric_fields:
        if not isinstance(row.get(field), (int, float)):
            return "truth_thin"
    if not isinstance(row.get("sizing_conflict"), bool):
        return "truth_thin"
    viability_class = str(row.get("viability_class") or "").strip().lower()
    if viability_class not in {"viable_only", "impossible_only"}:
        return "truth_thin"
    return "candidate"


def _maker_fight_admission_score(row: Dict[str, Any]) -> Dict[str, Any]:
    viability_class = str(row.get("viability_class") or "").strip().lower()
    sizing_conflict = bool(row.get("sizing_conflict", False))
    queue_delta = float(row.get("queue_delta_shares") or 0.0)
    fill_prob_margin = float(row.get("fill_prob_margin") or 0.0)
    repeat_count = int(float(row.get("same_target_side_shadow_count_prior") or 0.0))
    ratio = row.get("size_to_visible_depth_ratio")

    geometry_score = 30 if viability_class == "viable_only" and not sizing_conflict else 0
    if queue_delta <= 0.0:
        queue_score = 25
    elif queue_delta <= 25.0:
        queue_score = 12
    elif queue_delta <= 50.0:
        queue_score = 5
    else:
        queue_score = 0
    if fill_prob_margin >= 0.015:
        fill_prob_score = 20
    elif fill_prob_margin >= 0.0:
        fill_prob_score = 10
    else:
        fill_prob_score = 0
    if repeat_count <= 0:
        repeat_score = 15
    elif repeat_count == 1:
        repeat_score = 8
    else:
        repeat_score = 0
    if isinstance(ratio, (int, float)):
        ratio_value = float(ratio)
        if ratio_value <= 0.5:
            size_liquidity_score = 10
        elif ratio_value <= 1.0:
            size_liquidity_score = 5
        else:
            size_liquidity_score = 0
    else:
        size_liquidity_score = 0

    component_scores = {
        "geometry": int(geometry_score),
        "queue": int(queue_score),
        "fill_probability": int(fill_prob_score),
        "repeat_pressure": int(repeat_score),
        "size_liquidity": int(size_liquidity_score),
    }
    hard_fail_reasons: List[str] = []
    if viability_class != "viable_only" or sizing_conflict:
        hard_fail_reasons.append("non_viable_geometry_or_sizing_conflict")
    if queue_delta > 50.0:
        hard_fail_reasons.append("queue_delta_gt_50")
    if fill_prob_margin < -0.015:
        hard_fail_reasons.append("fill_prob_margin_lt_neg_0p015")

    soft_driver_flags: List[str] = []
    if queue_delta > 0.0:
        soft_driver_flags.append("queue_pressure")
    if fill_prob_margin < 0.015:
        soft_driver_flags.append("fill_prob_cushion_thin")
    if repeat_count >= 1:
        soft_driver_flags.append("repeat_target_side_pressure")
    if not isinstance(ratio, (int, float)) or float(ratio) > 1.0:
        soft_driver_flags.append("size_liquidity_pressure")
    if viability_class != "viable_only":
        soft_driver_flags.append("geometry_pressure")
    if sizing_conflict:
        soft_driver_flags.append("sizing_conflict")

    score = int(sum(component_scores.values()))
    if hard_fail_reasons:
        admission_class = "trash"
    elif score >= 80:
        admission_class = "clean"
    elif score >= 55:
        admission_class = "borderline"
    else:
        admission_class = "trash"

    deficits = {
        "geometry_pressure": 30 - geometry_score,
        "queue_pressure": 25 - queue_score,
        "fill_prob_cushion_thin": 20 - fill_prob_score,
        "repeat_target_side_pressure": 15 - repeat_score,
        "size_liquidity_pressure": 10 - size_liquidity_score,
    }
    dominant_driver = max(deficits.items(), key=lambda item: (item[1], item[0]))[0]
    if hard_fail_reasons:
        dominant_driver = hard_fail_reasons[0]

    return {
        "admission_score": int(score),
        "admission_class": admission_class,
        "component_scores": component_scores,
        "hard_fail_reasons": hard_fail_reasons,
        "soft_driver_flags": sorted(set(soft_driver_flags)),
        "dominant_driver": dominant_driver,
    }


def _maker_outcome_lookup(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for record in records:
        order_submit_id = str(record.get("order_submit_id") or "").strip()
        if order_submit_id:
            lookup[order_submit_id] = record
    return lookup


def _maker_fight_admission_examples(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    for row in rows[:5]:
        examples.append(
            {
                "target_side_ref": str(row.get("target_side_ref") or ""),
                "order_submit_id": str(row.get("order_submit_id") or ""),
                "admission_score": row.get("admission_score"),
                "admission_class": row.get("admission_class"),
                "dominant_driver": row.get("dominant_driver"),
                "decision_result": row.get("decision_result"),
                "decision_block_reason": row.get("decision_block_reason"),
                "decision_quality": row.get("decision_quality"),
                "outcome_truth_status": row.get("outcome_truth_status"),
                "queue_delta_shares": row.get("queue_delta_shares"),
                "fill_prob_margin": row.get("fill_prob_margin"),
                "same_target_side_shadow_count_prior": row.get("same_target_side_shadow_count_prior"),
            }
        )
    return examples


def _dict_path(payload: Dict[str, Any], path: Tuple[str, ...], default: Any = None) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _extract_target_ref_from_decision_linkage_key(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    prefix = "target_ref:"
    if not text.startswith(prefix):
        return None
    target_ref = text[len(prefix) :].split("|", 1)[0].strip()
    return target_ref or None


def _infer_side_from_edge_value(edge_value: Any) -> Optional[str]:
    value = _safe_float(edge_value, default=0.0)
    if value > 0.0:
        return "BUY"
    if value < 0.0:
        return "SELL"
    return None


def _legacy_maker_admission_thresholds(run_manifest: Dict[str, Any]) -> Dict[str, float]:
    min_expected_fill_prob = _safe_float(
        _dict_path(run_manifest, ("config", "strategy", "execution_quality", "min_expected_fill_prob"), 0.045),
        default=0.045,
    )
    max_queue_ahead_size = _safe_float(
        _dict_path(run_manifest, ("config", "strategy", "execution_quality", "max_queue_ahead_size"), 300.0),
        default=300.0,
    )
    reduce_only_fill_prob_floor = _safe_float(
        _dict_path(
            run_manifest,
            ("config", "strategy", "execution_quality", "reduce_only_recovery_min_expected_fill_prob_floor"),
            min_expected_fill_prob,
        ),
        default=min_expected_fill_prob,
    )
    reduce_only_queue_multiplier = _safe_float(
        _dict_path(
            run_manifest,
            ("config", "strategy", "execution_quality", "reduce_only_recovery_max_queue_ahead_size_multiplier"),
            1.0,
        ),
        default=1.0,
    )
    maker_min_notional = _safe_float(
        _dict_path(run_manifest, ("config", "sizing", "maker_competitive_min_notional_usd"), 0.0),
        default=0.0,
    )
    maker_max_shares = _safe_float(
        _dict_path(run_manifest, ("config", "sizing", "maker_competitive_max_shares"), 0.0),
        default=0.0,
    )
    geometry_floor_price = 0.0
    if maker_min_notional > 0.0 and maker_max_shares > 0.0:
        geometry_floor_price = maker_min_notional / maker_max_shares
    maker_timing_gate_min_sec_to_expiry = _safe_float(
        _dict_path(
            run_manifest,
            ("config", "strategy", "maker_competitiveness", "timing_gate_min_sec_to_expiry"),
            15.0,
        ),
        default=15.0,
    )
    maker_timing_gate_max_sec_to_expiry = _safe_float(
        _dict_path(
            run_manifest,
            ("config", "strategy", "maker_competitiveness", "timing_gate_max_sec_to_expiry"),
            20.0,
        ),
        default=20.0,
    )
    selection_gate_min_sec_to_expiry_raw = _dict_path(
        run_manifest,
        ("config", "strategy", "maker_competitiveness", "selection_gate", "min_sec_to_expiry"),
        None,
    )
    selection_gate_max_sec_to_expiry_raw = _dict_path(
        run_manifest,
        ("config", "strategy", "maker_competitiveness", "selection_gate", "max_sec_to_expiry"),
        None,
    )
    selection_gate_min_sec_to_expiry = (
        float(selection_gate_min_sec_to_expiry_raw)
        if isinstance(selection_gate_min_sec_to_expiry_raw, (int, float))
        else None
    )
    selection_gate_max_sec_to_expiry = (
        float(selection_gate_max_sec_to_expiry_raw)
        if isinstance(selection_gate_max_sec_to_expiry_raw, (int, float))
        else None
    )
    if selection_gate_min_sec_to_expiry is not None:
        maker_timing_gate_min_sec_to_expiry = float(selection_gate_min_sec_to_expiry)
    if selection_gate_max_sec_to_expiry is not None:
        maker_timing_gate_max_sec_to_expiry = float(selection_gate_max_sec_to_expiry)
    return {
        "min_expected_fill_prob": float(min_expected_fill_prob),
        "max_queue_ahead_size": float(max_queue_ahead_size),
        "reduce_only_fill_prob_floor": float(reduce_only_fill_prob_floor),
        "reduce_only_queue_multiplier": float(reduce_only_queue_multiplier),
        "geometry_floor_price": float(geometry_floor_price),
        "maker_timing_gate_min_sec_to_expiry": float(maker_timing_gate_min_sec_to_expiry),
        "maker_timing_gate_max_sec_to_expiry": float(maker_timing_gate_max_sec_to_expiry),
    }


MAKER_CANNON_LATE_WINDOW_PROBE_VERSION = 3


def _maker_cannon_probe_latent_market_quality(row: Dict[str, Any]) -> Dict[str, Any]:
    missing_fields: List[str] = []
    required_fields = (
        "market_reference_class",
        "market_probability",
        "probe_favored_side",
        "probe_visible_depth_shares",
    )
    for field_name in required_fields:
        value = row.get(field_name)
        if value is None:
            missing_fields.append(field_name)
            continue
        if isinstance(value, str) and not value.strip():
            missing_fields.append(field_name)

    if missing_fields:
        return {
            "latent_market_truth_class": "truth_thin",
            "latent_market_candidate": False,
            "latent_market_full_cannon_candidate": False,
            "latent_market_reject_reasons": [],
            "latent_market_dominant_reject_reason": None,
            "latent_market_missing_fields": missing_fields,
        }

    reject_reasons: List[str] = []
    if str(row.get("market_reference_class") or "").strip().lower() != "authoritative":
        reject_reasons.append("market_reference_not_authoritative")
    if row.get("geometry_viable") is not True:
        reject_reasons.append("non_viable_geometry")
    if row.get("secondary_oracle_confirmation") is not True:
        status = str(row.get("secondary_oracle_status") or "unknown").strip().lower() or "unknown"
        reject_reasons.append(f"secondary_oracle_{status}")
    if row.get("cannon_depth_requirement_met") is not True:
        reject_reasons.append("insufficient_depth_multiple")

    return {
        "latent_market_truth_class": "evaluable",
        "latent_market_candidate": True,
        "latent_market_full_cannon_candidate": bool(not reject_reasons),
        "latent_market_reject_reasons": list(reject_reasons),
        "latent_market_dominant_reject_reason": reject_reasons[0] if reject_reasons else None,
        "latent_market_missing_fields": [],
    }


def _maker_cannon_probe_population_class(row: Dict[str, Any]) -> Tuple[str, List[str]]:
    posture = str(row.get("financial_posture_class") or "").strip().upper()
    if posture in {
        "HALT_NEW_RISK",
        "HARD_DEGRADED_REDUCE_ONLY",
        "PREEXPIRY_REDUCE_ONLY",
    } or bool(row.get("reduce_only_recovery_active", False)):
        reasons = []
        if posture:
            reasons.append(f"financial_posture_{posture.lower()}")
        if bool(row.get("reduce_only_recovery_active", False)):
            reasons.append("reduce_only_recovery_active")
        return "external_blocked", reasons or ["external_blocked"]

    missing_fields: List[str] = []
    required_fields = (
        "ts_decision_utc",
        "sec_to_expiry",
        "market_reference_class",
        "fair_probability",
        "market_probability",
        "probe_favored_side",
        "probe_visible_depth_shares",
        "open_maker_orders_total",
        "secondary_oracle_status",
        "secondary_oracle_confirmation",
        "financial_posture_class",
    )
    for field_name in required_fields:
        value = row.get(field_name)
        if field_name == "secondary_oracle_confirmation":
            if value is None:
                missing_fields.append(field_name)
            continue
        if value is None:
            missing_fields.append(field_name)
            continue
        if isinstance(value, str) and not value.strip():
            missing_fields.append(field_name)
    return ("truth_thin", missing_fields) if missing_fields else ("candidate", [])


def _maker_cannon_book_top_index(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_token: Dict[str, List[Dict[str, Any]]] = {}
    for evt in events:
        if str(evt.get("event_type") or "").strip() != "book_top":
            continue
        token_id = str(evt.get("token_id") or "").strip()
        ts = parse_ts(evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"))
        if not token_id or ts is None:
            continue
        by_token.setdefault(token_id, []).append(
            {
                "ts": ts,
                "best_bid_price": evt.get("best_bid_price"),
                "best_bid_size": evt.get("best_bid_size"),
                "best_ask_price": evt.get("best_ask_price"),
                "best_ask_size": evt.get("best_ask_size"),
                "source": evt.get("source"),
            }
        )
    for entries in by_token.values():
        entries.sort(key=lambda item: item["ts"])
    return by_token


def _maker_cannon_backfilled_book_pair(
    entries: List[Dict[str, Any]],
    decision_ts: Optional[dt.datetime],
    *,
    max_pair_delta_sec: float = 0.10,
) -> Optional[Dict[str, Any]]:
    if decision_ts is None or not entries:
        return None
    best_bid: Optional[Tuple[float, Dict[str, Any]]] = None
    best_ask: Optional[Tuple[float, Dict[str, Any]]] = None
    for entry in entries:
        delta = abs((entry["ts"] - decision_ts).total_seconds())
        if delta > max_pair_delta_sec:
            continue
        if isinstance(entry.get("best_bid_price"), (int, float)):
            if best_bid is None or delta < best_bid[0]:
                best_bid = (delta, entry)
        if isinstance(entry.get("best_ask_price"), (int, float)):
            if best_ask is None or delta < best_ask[0]:
                best_ask = (delta, entry)
    if best_bid is None or best_ask is None:
        return None
    bid_entry = best_bid[1]
    ask_entry = best_ask[1]
    bid_price = _safe_float(bid_entry.get("best_bid_price"), default=0.0)
    ask_price = _safe_float(ask_entry.get("best_ask_price"), default=0.0)
    if bid_price <= 0.0 or ask_price <= 0.0 or bid_price > ask_price + 1e-9:
        return None
    return {
        "midpoint": float((bid_price + ask_price) / 2.0),
        "best_bid_price": float(bid_price),
        "best_bid_size": (
            float(bid_entry["best_bid_size"])
            if isinstance(bid_entry.get("best_bid_size"), (int, float))
            else None
        ),
        "best_ask_price": float(ask_price),
        "best_ask_size": (
            float(ask_entry["best_ask_size"])
            if isinstance(ask_entry.get("best_ask_size"), (int, float))
            else None
        ),
        "max_pair_delta_sec": float(max(best_bid[0], best_ask[0])),
        "sources": sorted(
            {
                str(bid_entry.get("source") or "").strip() or "unknown",
                str(ask_entry.get("source") or "").strip() or "unknown",
            }
        ),
    }


def _maker_cannon_probe_rows(
    *,
    events: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    thresholds = _legacy_maker_admission_thresholds(run_manifest)
    book_top_by_token = _maker_cannon_book_top_index(events)
    geometry_floor_price = float(thresholds.get("geometry_floor_price", 0.0) or 0.0)
    total_maker_edge_eval_rows = 0
    late_window_raw_row_count = 0
    rows: List[Dict[str, Any]] = []

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        if str(evt.get("evaluation_scope") or "").strip().lower() != "maker":
            continue
        total_maker_edge_eval_rows += 1
        sec_to_expiry = evt.get("time_remaining_sec")
        if not isinstance(sec_to_expiry, (int, float)):
            continue
        sec_value = float(sec_to_expiry)
        if sec_value < 0.0 or sec_value > 20.0:
            continue
        late_window_raw_row_count += 1
        favored_side = (
            str(evt.get("probe_favored_side") or "").strip().upper()
            or _infer_side_from_edge_value(evt.get("edge_value"))
            or "UNKNOWN"
        )
        target_ref = str(evt.get("target_ref") or "").strip() or None
        target_side_ref = _legacy_target_side_ref(target_ref, evt.get("token_id"), favored_side)
        row = {
            "maker_cannon_probe_version": int(MAKER_CANNON_LATE_WINDOW_PROBE_VERSION),
            "probe_source_class": "runtime_edge_evaluation_v1",
            "run_id": evt.get("run_id"),
            "token_id": evt.get("token_id"),
            "target_ref": target_ref,
            "target_side_ref": target_side_ref,
            "side": favored_side,
            "stage": evt.get("stage"),
            "cycle_index": evt.get("cycle_index"),
            "ts_decision_utc": evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"),
            "sec_to_expiry": sec_value,
            "fair_probability": evt.get("fair_probability"),
            "market_probability": evt.get("market_probability"),
            "edge_value": evt.get("edge_value"),
            "market_reference_mode": evt.get("market_reference_mode"),
            "market_reference_basis": evt.get("market_reference_basis"),
            "market_reference_source_side": evt.get("market_reference_source_side"),
            "market_reference_class": evt.get("market_reference_class"),
            "financial_posture_class": evt.get("financial_posture_class"),
            "reduce_only_recovery_active": bool(evt.get("reduce_only_recovery_active", False)),
            "maker_allowed": bool(evt.get("maker_allowed")),
            "maker_new_risk_allowed": _maker_new_risk_allowed_from_row(evt),
            "block_reason": evt.get("block_reason"),
            "maker_no_submission_cause": evt.get("maker_no_submission_cause"),
            "maker_no_submission_category": evt.get("maker_no_submission_category"),
            "probe_favored_side": favored_side,
            "secondary_fair_probability": evt.get("secondary_fair_probability"),
            "secondary_oracle_status": evt.get("secondary_oracle_status"),
            "secondary_oracle_confirmation": evt.get("secondary_oracle_confirmation"),
            "chainlink_spot_price": evt.get("chainlink_spot_price"),
            "secondary_oracle_spot_price": evt.get("secondary_oracle_spot_price"),
            "secondary_oracle_price_delta_abs": evt.get("secondary_oracle_price_delta_abs"),
            "secondary_oracle_price_delta_bps": evt.get("secondary_oracle_price_delta_bps"),
            "desired_quote_price": evt.get("market_probability"),
            "probe_visible_depth_shares": evt.get("probe_visible_depth_shares"),
            "visible_depth_shares": evt.get("probe_visible_depth_shares"),
            "open_maker_orders_total": evt.get("open_maker_orders_total"),
        }
        row["market_reference_backfill_applied"] = False
        row["market_reference_backfill_pair_delta_sec"] = None
        decision_ts = parse_ts(row.get("ts_decision_utc"))
        book_pair = _maker_cannon_backfilled_book_pair(
            book_top_by_token.get(str(evt.get("token_id") or "").strip(), []),
            decision_ts,
        )
        if book_pair is not None:
            market_reference_class = str(row.get("market_reference_class") or "").strip().lower()
            backfill_visible = market_reference_class in {"", "not_available"}
            if backfill_visible:
                row["market_reference_backfill_applied"] = True
                row["market_reference_backfill_pair_delta_sec"] = float(
                    book_pair["max_pair_delta_sec"]
                )
            if favored_side == "UNKNOWN":
                fair_probability = row.get("fair_probability")
                midpoint = row.get("market_probability")
                if not isinstance(midpoint, (int, float)):
                    midpoint = float(book_pair["midpoint"])
                if isinstance(fair_probability, (int, float)) and isinstance(midpoint, (int, float)):
                    if float(fair_probability) > float(midpoint) + 1e-12:
                        favored_side = "BUY"
                    elif float(fair_probability) < float(midpoint) - 1e-12:
                        favored_side = "SELL"
                    row["probe_favored_side"] = favored_side
                    row["side"] = favored_side
                    row["target_side_ref"] = _legacy_target_side_ref(
                        row.get("target_ref"), evt.get("token_id"), favored_side
                    )
            if row.get("probe_visible_depth_shares") is None:
                if favored_side == "BUY" and isinstance(book_pair.get("best_bid_size"), (int, float)):
                    row["probe_visible_depth_shares"] = float(book_pair["best_bid_size"])
                    row["visible_depth_shares"] = float(book_pair["best_bid_size"])
                elif favored_side == "SELL" and isinstance(book_pair.get("best_ask_size"), (int, float)):
                    row["probe_visible_depth_shares"] = float(book_pair["best_ask_size"])
                    row["visible_depth_shares"] = float(book_pair["best_ask_size"])
        row["probe_visible_depth_fail_closed_zero_imputed"] = False
        market_reference_class = str(row.get("market_reference_class") or "").strip().lower()
        if (
            row.get("probe_visible_depth_shares") is None
            and favored_side in {"BUY", "SELL"}
            and market_reference_class in {"authoritative", "bounded_approximation"}
        ):
            row["probe_visible_depth_shares"] = 0.0
            row["visible_depth_shares"] = 0.0
            row["probe_visible_depth_fail_closed_zero_imputed"] = True
        _apply_maker_cannon_shadow_fields(row)
        row["market_probability_band"] = _maker_cannon_market_probability_band(row.get("market_probability"))
        row["favored_side_depth_class"] = _maker_cannon_favored_depth_class(
            row.get("probe_visible_depth_shares"),
            zero_imputed=bool(row.get("probe_visible_depth_fail_closed_zero_imputed", False)),
        )
        market_probability = row.get("market_probability")
        geometry_viable = None
        if geometry_floor_price > 0.0 and isinstance(market_probability, (int, float)):
            geometry_viable = bool(float(market_probability) + 1e-9 >= geometry_floor_price)
        row["geometry_floor_price"] = float(geometry_floor_price) if geometry_floor_price > 0.0 else None
        row["geometry_viable"] = geometry_viable
        population_class, reasons = _maker_cannon_probe_population_class(row)
        row["population_class"] = population_class
        row["population_reasons"] = list(reasons)
        row.update(_maker_cannon_probe_latent_market_quality(row))
        reject_reasons: List[str] = []
        if population_class == "candidate":
            if str(row.get("market_reference_class") or "").strip().lower() != "authoritative":
                reject_reasons.append("market_reference_not_authoritative")
            if geometry_viable is not True:
                reject_reasons.append("non_viable_geometry")
            if not bool(row.get("secondary_oracle_confirmation")):
                status = str(row.get("secondary_oracle_status") or "unknown").strip().lower() or "unknown"
                reject_reasons.append(f"secondary_oracle_{status}")
            if row.get("cannon_depth_requirement_met") is not True:
                reject_reasons.append("insufficient_depth_multiple")
            open_orders_total = row.get("open_maker_orders_total")
            if isinstance(open_orders_total, (int, float)) and int(float(open_orders_total)) >= int(MAKER_CANNON_STACK_SOFT_MAX):
                reject_reasons.append("stack_soft_cap_reached")
        else:
            reject_reasons.extend(reasons)
        row["full_cannon_candidate"] = bool(population_class == "candidate" and not reject_reasons)
        row["reject_reasons"] = list(reject_reasons)
        row["dominant_reject_reason"] = reject_reasons[0] if reject_reasons else None
        rows.append(row)

    rows.sort(
        key=lambda item: (
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("token_id") or ""),
        )
    )
    counts = {
        "total_maker_edge_eval_rows": int(total_maker_edge_eval_rows),
        "late_window_raw_row_count": int(late_window_raw_row_count),
        "ignored_non_late_window_row_count": int(max(0, total_maker_edge_eval_rows - late_window_raw_row_count)),
    }
    for row in rows:
        row.update(counts)
    return rows, counts


def _maker_mid_window_probe_rows(
    *,
    events: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    thresholds = _legacy_maker_admission_thresholds(run_manifest)
    book_top_by_token = _maker_cannon_book_top_index(events)
    geometry_floor_price = float(thresholds.get("geometry_floor_price", 0.0) or 0.0)
    total_maker_edge_eval_rows = 0
    mid_window_raw_row_count = 0
    rows: List[Dict[str, Any]] = []

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        if str(evt.get("evaluation_scope") or "").strip().lower() != "maker":
            continue
        total_maker_edge_eval_rows += 1
        sec_to_expiry = evt.get("time_remaining_sec")
        if not isinstance(sec_to_expiry, (int, float)):
            continue
        sec_value = float(sec_to_expiry)
        if sec_value <= 20.0 or sec_value > 45.0:
            continue
        mid_window_raw_row_count += 1
        favored_side = (
            str(evt.get("probe_favored_side") or "").strip().upper()
            or _infer_side_from_edge_value(evt.get("edge_value"))
            or "UNKNOWN"
        )
        target_ref = str(evt.get("target_ref") or "").strip() or None
        target_side_ref = _legacy_target_side_ref(target_ref, evt.get("token_id"), favored_side)
        row = {
            "maker_mid_window_probe_version": int(MAKER_MID_WINDOW_PROBE_VERSION),
            "probe_source_class": "runtime_edge_evaluation_v1",
            "run_id": evt.get("run_id"),
            "token_id": evt.get("token_id"),
            "target_ref": target_ref,
            "target_side_ref": target_side_ref,
            "side": favored_side,
            "stage": evt.get("stage"),
            "cycle_index": evt.get("cycle_index"),
            "ts_decision_utc": evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"),
            "sec_to_expiry": sec_value,
            "fair_probability": evt.get("fair_probability"),
            "market_probability": evt.get("market_probability"),
            "edge_value": evt.get("edge_value"),
            "market_reference_mode": evt.get("market_reference_mode"),
            "market_reference_basis": evt.get("market_reference_basis"),
            "market_reference_source_side": evt.get("market_reference_source_side"),
            "market_reference_class": evt.get("market_reference_class"),
            "financial_posture_class": evt.get("financial_posture_class"),
            "reduce_only_recovery_active": bool(evt.get("reduce_only_recovery_active", False)),
            "maker_allowed": bool(evt.get("maker_allowed")),
            "maker_new_risk_allowed": _maker_new_risk_allowed_from_row(evt),
            "block_reason": evt.get("block_reason"),
            "maker_no_submission_cause": evt.get("maker_no_submission_cause"),
            "maker_no_submission_category": evt.get("maker_no_submission_category"),
            "probe_favored_side": favored_side,
            "secondary_fair_probability": evt.get("secondary_fair_probability"),
            "secondary_oracle_status": evt.get("secondary_oracle_status"),
            "secondary_oracle_confirmation": evt.get("secondary_oracle_confirmation"),
            "chainlink_spot_price": evt.get("chainlink_spot_price"),
            "secondary_oracle_spot_price": evt.get("secondary_oracle_spot_price"),
            "secondary_oracle_price_delta_abs": evt.get("secondary_oracle_price_delta_abs"),
            "secondary_oracle_price_delta_bps": evt.get("secondary_oracle_price_delta_bps"),
            "desired_quote_price": evt.get("market_probability"),
            "probe_visible_depth_shares": evt.get("probe_visible_depth_shares"),
            "visible_depth_shares": evt.get("probe_visible_depth_shares"),
            "open_maker_orders_total": evt.get("open_maker_orders_total"),
        }
        row["market_reference_backfill_applied"] = False
        row["market_reference_backfill_pair_delta_sec"] = None
        decision_ts = parse_ts(row.get("ts_decision_utc"))
        book_pair = _maker_cannon_backfilled_book_pair(
            book_top_by_token.get(str(evt.get("token_id") or "").strip(), []),
            decision_ts,
        )
        if book_pair is not None:
            market_reference_class = str(row.get("market_reference_class") or "").strip().lower()
            backfill_visible = market_reference_class in {"", "not_available"}
            if backfill_visible:
                row["market_reference_backfill_applied"] = True
                row["market_reference_backfill_pair_delta_sec"] = float(
                    book_pair["max_pair_delta_sec"]
                )
            if favored_side == "UNKNOWN":
                fair_probability = row.get("fair_probability")
                midpoint = row.get("market_probability")
                if not isinstance(midpoint, (int, float)):
                    midpoint = float(book_pair["midpoint"])
                if isinstance(fair_probability, (int, float)) and isinstance(midpoint, (int, float)):
                    if float(fair_probability) > float(midpoint) + 1e-12:
                        favored_side = "BUY"
                    elif float(fair_probability) < float(midpoint) - 1e-12:
                        favored_side = "SELL"
                    row["probe_favored_side"] = favored_side
                    row["side"] = favored_side
                    row["target_side_ref"] = _legacy_target_side_ref(
                        row.get("target_ref"), evt.get("token_id"), favored_side
                    )
            if row.get("probe_visible_depth_shares") is None:
                if favored_side == "BUY" and isinstance(book_pair.get("best_bid_size"), (int, float)):
                    row["probe_visible_depth_shares"] = float(book_pair["best_bid_size"])
                    row["visible_depth_shares"] = float(book_pair["best_bid_size"])
                elif favored_side == "SELL" and isinstance(book_pair.get("best_ask_size"), (int, float)):
                    row["probe_visible_depth_shares"] = float(book_pair["best_ask_size"])
                    row["visible_depth_shares"] = float(book_pair["best_ask_size"])
        row["probe_visible_depth_fail_closed_zero_imputed"] = False
        market_reference_class = str(row.get("market_reference_class") or "").strip().lower()
        if (
            row.get("probe_visible_depth_shares") is None
            and favored_side in {"BUY", "SELL"}
            and market_reference_class in {"authoritative", "bounded_approximation"}
        ):
            row["probe_visible_depth_shares"] = 0.0
            row["visible_depth_shares"] = 0.0
            row["probe_visible_depth_fail_closed_zero_imputed"] = True
        _apply_maker_cannon_shadow_fields(row)
        row["market_probability_band"] = _maker_cannon_market_probability_band(row.get("market_probability"))
        row["favored_side_depth_class"] = _maker_cannon_favored_depth_class(
            row.get("probe_visible_depth_shares"),
            zero_imputed=bool(row.get("probe_visible_depth_fail_closed_zero_imputed", False)),
        )
        market_probability = row.get("market_probability")
        geometry_viable = None
        if geometry_floor_price > 0.0 and isinstance(market_probability, (int, float)):
            geometry_viable = bool(float(market_probability) + 1e-9 >= geometry_floor_price)
        row["geometry_floor_price"] = float(geometry_floor_price) if geometry_floor_price > 0.0 else None
        row["geometry_viable"] = geometry_viable
        population_class, reasons = _maker_cannon_probe_population_class(row)
        row["population_class"] = population_class
        row["population_reasons"] = list(reasons)
        row.update(_maker_cannon_probe_latent_market_quality(row))
        reject_reasons: List[str] = []
        if population_class == "candidate":
            if str(row.get("market_reference_class") or "").strip().lower() != "authoritative":
                reject_reasons.append("market_reference_not_authoritative")
            if geometry_viable is not True:
                reject_reasons.append("non_viable_geometry")
            if not bool(row.get("secondary_oracle_confirmation")):
                status = str(row.get("secondary_oracle_status") or "unknown").strip().lower() or "unknown"
                reject_reasons.append(f"secondary_oracle_{status}")
            if row.get("cannon_depth_requirement_met") is not True:
                reject_reasons.append("insufficient_depth_multiple")
            open_orders_total = row.get("open_maker_orders_total")
            if isinstance(open_orders_total, (int, float)) and int(float(open_orders_total)) >= int(MAKER_CANNON_STACK_SOFT_MAX):
                reject_reasons.append("stack_soft_cap_reached")
        else:
            reject_reasons.extend(reasons)
        row["full_mid_window_candidate"] = bool(population_class == "candidate" and not reject_reasons)
        row["reject_reasons"] = list(reject_reasons)
        row["dominant_reject_reason"] = reject_reasons[0] if reject_reasons else None
        rows.append(row)

    rows.sort(
        key=lambda item: (
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("token_id") or ""),
        )
    )
    counts = {
        "total_maker_edge_eval_rows": int(total_maker_edge_eval_rows),
        "mid_window_raw_row_count": int(mid_window_raw_row_count),
        "ignored_non_mid_window_row_count": int(max(0, total_maker_edge_eval_rows - mid_window_raw_row_count)),
    }
    for row in rows:
        row.update(counts)
    return rows, counts


def _maker_cannon_late_window_probe_bundle(
    *,
    events: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    rows, counts = _maker_cannon_probe_rows(events=events, run_manifest=run_manifest)
    population_counts: Counter[str] = Counter()
    reject_reason_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    market_reference_class_counts: Counter[str] = Counter()
    market_reference_mode_counts: Counter[str] = Counter()
    market_reference_source_side_counts: Counter[str] = Counter()
    market_probability_band_counts: Counter[str] = Counter()
    favored_side_depth_class_counts: Counter[str] = Counter()
    financial_posture_counts: Counter[str] = Counter()
    window_counts: Counter[str] = Counter()
    session_regime_counts: Counter[str] = Counter()
    stack_pressure_counts: Counter[str] = Counter()
    secondary_oracle_status_counts: Counter[str] = Counter()
    secondary_oracle_confirmation_counts: Counter[str] = Counter()
    maker_new_risk_allowed_counts: Counter[str] = Counter()
    probe_visible_depth_fail_closed_zero_counts: Counter[str] = Counter()
    geometry_viable_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    latent_market_truth_class_counts: Counter[str] = Counter()
    latent_market_reject_reason_counts: Counter[str] = Counter()
    latent_market_dominant_reject_reason_counts: Counter[str] = Counter()
    latent_market_full_candidate_population_counts: Counter[str] = Counter()
    external_blocked_latent_market_reject_reason_counts: Counter[str] = Counter()
    external_blocked_latent_full_examples: List[Dict[str, Any]] = []
    top_full_candidate_target_side_counts: Counter[str] = Counter()
    top_reject_target_side_counts: Counter[str] = Counter()
    depth_multiple_values: List[float] = []
    full_cannon_candidate_count = 0
    full_candidate_runtime_stage_disallow_count = 0
    latent_market_full_cannon_candidate_count = 0
    external_blocked_latent_market_evaluable_count = 0
    external_blocked_latent_market_full_cannon_candidate_count = 0

    for row in rows:
        population_counts[str(row.get("population_class") or "unknown")] += 1
        stage_counts[str(row.get("stage") or "unknown")] += 1
        market_reference_class_counts[str(row.get("market_reference_class") or "unknown")] += 1
        market_reference_mode_counts[str(row.get("market_reference_mode") or "unknown")] += 1
        market_reference_source_side_counts[str(row.get("market_reference_source_side") or "unknown")] += 1
        market_probability_band_counts[str(row.get("market_probability_band") or "unknown")] += 1
        favored_side_depth_class_counts[str(row.get("favored_side_depth_class") or "unknown")] += 1
        financial_posture_counts[str(row.get("financial_posture_class") or "unknown")] += 1
        window_counts[str(row.get("cannon_window_class") or "unknown")] += 1
        session_regime_counts[str(row.get("session_regime_class") or "unknown")] += 1
        stack_pressure_counts[str(row.get("stack_pressure_class") or "unknown")] += 1
        secondary_oracle_status_counts[str(row.get("secondary_oracle_status") or "unknown")] += 1
        secondary_oracle_confirmation_counts[
            "confirmed" if bool(row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
        ] += 1
        maker_new_risk_allowed_counts[
            "allowed" if _maker_new_risk_allowed_from_row(row) else "disallowed"
        ] += 1
        probe_visible_depth_fail_closed_zero_counts[
            "imputed_zero" if bool(row.get("probe_visible_depth_fail_closed_zero_imputed", False)) else "reported_or_not_needed"
        ] += 1
        geometry_value = row.get("geometry_viable")
        if isinstance(geometry_value, bool):
            geometry_viable_counts["viable" if geometry_value else "not_viable"] += 1
        else:
            geometry_viable_counts["unknown"] += 1
        if isinstance(row.get("cannon_depth_requirement_met"), bool):
            cannon_depth_requirement_counts[
                "met" if bool(row.get("cannon_depth_requirement_met")) else "not_met"
            ] += 1
        else:
            cannon_depth_requirement_counts["unknown"] += 1
        if isinstance(row.get("depth_multiple_vs_cannon_target"), (int, float)):
            depth_multiple_values.append(float(row.get("depth_multiple_vs_cannon_target")))
        latent_truth_class = str(row.get("latent_market_truth_class") or "unknown")
        latent_market_truth_class_counts[latent_truth_class] += 1
        if bool(row.get("latent_market_candidate", False)):
            if bool(row.get("latent_market_full_cannon_candidate", False)):
                latent_market_full_cannon_candidate_count += 1
                latent_market_full_candidate_population_counts[
                    str(row.get("population_class") or "unknown")
                ] += 1
                if (
                    str(row.get("population_class") or "") == "external_blocked"
                    and len(external_blocked_latent_full_examples) < 5
                ):
                    external_blocked_latent_full_examples.append(
                        {
                            "target_side_ref": str(row.get("target_side_ref") or ""),
                            "stage": str(row.get("stage") or ""),
                            "financial_posture_class": str(row.get("financial_posture_class") or ""),
                            "reduce_only_recovery_active": bool(
                                row.get("reduce_only_recovery_active", False)
                            ),
                            "sec_to_expiry": row.get("sec_to_expiry"),
                            "market_reference_class": row.get("market_reference_class"),
                            "secondary_oracle_status": row.get("secondary_oracle_status"),
                            "depth_multiple_vs_cannon_target": row.get(
                                "depth_multiple_vs_cannon_target"
                            ),
                        }
                    )
            dominant_reason = str(row.get("latent_market_dominant_reject_reason") or "").strip()
            if dominant_reason:
                latent_market_dominant_reject_reason_counts[dominant_reason] += 1
            for reason in list(row.get("latent_market_reject_reasons") or []):
                latent_market_reject_reason_counts[str(reason or "unknown")] += 1
                if str(row.get("population_class") or "") == "external_blocked":
                    external_blocked_latent_market_reject_reason_counts[
                        str(reason or "unknown")
                    ] += 1
            if str(row.get("population_class") or "") == "external_blocked":
                external_blocked_latent_market_evaluable_count += 1
                if bool(row.get("latent_market_full_cannon_candidate", False)):
                    external_blocked_latent_market_full_cannon_candidate_count += 1
        if bool(row.get("full_cannon_candidate", False)):
            full_cannon_candidate_count += 1
            if not _maker_new_risk_allowed_from_row(row):
                full_candidate_runtime_stage_disallow_count += 1
            top_full_candidate_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
        else:
            top_reject_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
        for reason in list(row.get("reject_reasons") or []):
            reject_reason_counts[str(reason or "unknown")] += 1

    summary = {
        "maker_cannon_probe_version": int(MAKER_CANNON_LATE_WINDOW_PROBE_VERSION),
        "row_count": int(len(rows)),
        **counts,
        "population_class_counts": {
            key: int(population_counts[key]) for key in sorted(population_counts)
        },
        "full_cannon_candidate_count": int(full_cannon_candidate_count),
        "full_candidate_runtime_stage_disallow_count": int(full_candidate_runtime_stage_disallow_count),
        "reject_reason_distribution": {
            key: int(reject_reason_counts[key]) for key in sorted(reject_reason_counts)
        },
        "stage_distribution": {key: int(stage_counts[key]) for key in sorted(stage_counts)},
        "market_reference_class_distribution": {
            key: int(market_reference_class_counts[key]) for key in sorted(market_reference_class_counts)
        },
        "market_reference_mode_distribution": {
            key: int(market_reference_mode_counts[key]) for key in sorted(market_reference_mode_counts)
        },
        "market_reference_source_side_distribution": {
            key: int(market_reference_source_side_counts[key]) for key in sorted(market_reference_source_side_counts)
        },
        "market_probability_band_distribution": {
            key: int(market_probability_band_counts[key]) for key in sorted(market_probability_band_counts)
        },
        "favored_side_depth_class_distribution": {
            key: int(favored_side_depth_class_counts[key]) for key in sorted(favored_side_depth_class_counts)
        },
        "financial_posture_class_distribution": {
            key: int(financial_posture_counts[key]) for key in sorted(financial_posture_counts)
        },
        "cannon_window_class_distribution": {
            key: int(window_counts[key]) for key in sorted(window_counts)
        },
        "session_regime_class_distribution": {
            key: int(session_regime_counts[key]) for key in sorted(session_regime_counts)
        },
        "stack_pressure_class_distribution": {
            key: int(stack_pressure_counts[key]) for key in sorted(stack_pressure_counts)
        },
        "secondary_oracle_status_distribution": {
            key: int(secondary_oracle_status_counts[key]) for key in sorted(secondary_oracle_status_counts)
        },
        "secondary_oracle_confirmation_distribution": {
            key: int(secondary_oracle_confirmation_counts[key])
            for key in sorted(secondary_oracle_confirmation_counts)
        },
        "maker_new_risk_allowed_distribution": {
            key: int(maker_new_risk_allowed_counts[key])
            for key in sorted(maker_new_risk_allowed_counts)
        },
        "probe_visible_depth_fail_closed_zero_distribution": {
            key: int(probe_visible_depth_fail_closed_zero_counts[key])
            for key in sorted(probe_visible_depth_fail_closed_zero_counts)
        },
        "geometry_viable_counts": {
            key: int(geometry_viable_counts[key]) for key in sorted(geometry_viable_counts)
        },
        "cannon_depth_requirement_counts": {
            key: int(cannon_depth_requirement_counts[key]) for key in sorted(cannon_depth_requirement_counts)
        },
        "depth_multiple_vs_cannon_target_summary": {
            "count": float(len(depth_multiple_values)),
            "min": float(min(depth_multiple_values)) if depth_multiple_values else 0.0,
            "p50": float(_percentile(sorted(depth_multiple_values), 0.50)) if depth_multiple_values else 0.0,
            "p90": float(_percentile(sorted(depth_multiple_values), 0.90)) if depth_multiple_values else 0.0,
            "max": float(max(depth_multiple_values)) if depth_multiple_values else 0.0,
            "mean": float(sum(depth_multiple_values) / len(depth_multiple_values)) if depth_multiple_values else 0.0,
            "median": float(median(depth_multiple_values)) if depth_multiple_values else 0.0,
        },
        "latent_market_truth_class_counts": {
            key: int(latent_market_truth_class_counts[key])
            for key in sorted(latent_market_truth_class_counts)
        },
        "latent_market_full_cannon_candidate_count": int(
            latent_market_full_cannon_candidate_count
        ),
        "latent_market_full_candidate_population_class_distribution": {
            key: int(latent_market_full_candidate_population_counts[key])
            for key in sorted(latent_market_full_candidate_population_counts)
        },
        "latent_market_reject_reason_distribution": {
            key: int(latent_market_reject_reason_counts[key])
            for key in sorted(latent_market_reject_reason_counts)
        },
        "latent_market_dominant_reject_reason_distribution": {
            key: int(latent_market_dominant_reject_reason_counts[key])
            for key in sorted(latent_market_dominant_reject_reason_counts)
        },
        "external_blocked_latent_market_evaluable_count": int(
            external_blocked_latent_market_evaluable_count
        ),
        "external_blocked_latent_market_full_cannon_candidate_count": int(
            external_blocked_latent_market_full_cannon_candidate_count
        ),
        "external_blocked_latent_market_reject_reason_distribution": {
            key: int(external_blocked_latent_market_reject_reason_counts[key])
            for key in sorted(external_blocked_latent_market_reject_reason_counts)
        },
        "external_blocked_latent_full_examples": external_blocked_latent_full_examples,
        "top_full_candidate_target_side_ref_counts": {
            key: int(value) for key, value in top_full_candidate_target_side_counts.most_common(10)
        },
        "top_reject_target_side_ref_counts": {
            key: int(value) for key, value in top_reject_target_side_counts.most_common(10)
        },
    }
    return {"rows": rows, "summary": summary}


def _maker_mid_window_probe_bundle(
    *,
    events: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    rows, counts = _maker_mid_window_probe_rows(events=events, run_manifest=run_manifest)
    population_counts: Counter[str] = Counter()
    reject_reason_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    market_reference_class_counts: Counter[str] = Counter()
    market_reference_mode_counts: Counter[str] = Counter()
    market_reference_source_side_counts: Counter[str] = Counter()
    market_probability_band_counts: Counter[str] = Counter()
    favored_side_depth_class_counts: Counter[str] = Counter()
    financial_posture_counts: Counter[str] = Counter()
    timing_band_counts: Counter[str] = Counter()
    session_regime_counts: Counter[str] = Counter()
    stack_pressure_counts: Counter[str] = Counter()
    secondary_oracle_status_counts: Counter[str] = Counter()
    secondary_oracle_confirmation_counts: Counter[str] = Counter()
    maker_new_risk_allowed_counts: Counter[str] = Counter()
    probe_visible_depth_fail_closed_zero_counts: Counter[str] = Counter()
    geometry_viable_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    latent_market_truth_class_counts: Counter[str] = Counter()
    latent_market_reject_reason_counts: Counter[str] = Counter()
    latent_market_dominant_reject_reason_counts: Counter[str] = Counter()
    latent_market_full_candidate_population_counts: Counter[str] = Counter()
    external_blocked_latent_market_reject_reason_counts: Counter[str] = Counter()
    external_blocked_latent_full_examples: List[Dict[str, Any]] = []
    top_full_candidate_target_side_counts: Counter[str] = Counter()
    top_reject_target_side_counts: Counter[str] = Counter()
    depth_multiple_values: List[float] = []
    full_mid_window_candidate_count = 0
    full_candidate_runtime_stage_disallow_count = 0
    latent_market_full_mid_window_candidate_count = 0
    external_blocked_latent_market_evaluable_count = 0
    external_blocked_latent_market_full_candidate_count = 0

    for row in rows:
        population_counts[str(row.get("population_class") or "unknown")] += 1
        stage_counts[str(row.get("stage") or "unknown")] += 1
        market_reference_class_counts[str(row.get("market_reference_class") or "unknown")] += 1
        market_reference_mode_counts[str(row.get("market_reference_mode") or "unknown")] += 1
        market_reference_source_side_counts[str(row.get("market_reference_source_side") or "unknown")] += 1
        market_probability_band_counts[str(row.get("market_probability_band") or "unknown")] += 1
        favored_side_depth_class_counts[str(row.get("favored_side_depth_class") or "unknown")] += 1
        financial_posture_counts[str(row.get("financial_posture_class") or "unknown")] += 1
        timing_band_counts[str(row.get("maker_timing_band_class") or "unknown")] += 1
        session_regime_counts[str(row.get("session_regime_class") or "unknown")] += 1
        stack_pressure_counts[str(row.get("stack_pressure_class") or "unknown")] += 1
        secondary_oracle_status_counts[str(row.get("secondary_oracle_status") or "unknown")] += 1
        secondary_oracle_confirmation_counts[
            "confirmed" if bool(row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
        ] += 1
        maker_new_risk_allowed_counts[
            "allowed" if _maker_new_risk_allowed_from_row(row) else "disallowed"
        ] += 1
        probe_visible_depth_fail_closed_zero_counts[
            "imputed_zero" if bool(row.get("probe_visible_depth_fail_closed_zero_imputed", False)) else "reported_or_not_needed"
        ] += 1
        geometry_value = row.get("geometry_viable")
        if isinstance(geometry_value, bool):
            geometry_viable_counts["viable" if geometry_value else "not_viable"] += 1
        else:
            geometry_viable_counts["unknown"] += 1
        if isinstance(row.get("cannon_depth_requirement_met"), bool):
            cannon_depth_requirement_counts[
                "met" if bool(row.get("cannon_depth_requirement_met")) else "not_met"
            ] += 1
        else:
            cannon_depth_requirement_counts["unknown"] += 1
        if isinstance(row.get("depth_multiple_vs_cannon_target"), (int, float)):
            depth_multiple_values.append(float(row.get("depth_multiple_vs_cannon_target")))
        latent_truth_class = str(row.get("latent_market_truth_class") or "unknown")
        latent_market_truth_class_counts[latent_truth_class] += 1
        if bool(row.get("latent_market_candidate", False)):
            if bool(row.get("latent_market_full_cannon_candidate", False)):
                latent_market_full_mid_window_candidate_count += 1
                latent_market_full_candidate_population_counts[
                    str(row.get("population_class") or "unknown")
                ] += 1
                if (
                    str(row.get("population_class") or "") == "external_blocked"
                    and len(external_blocked_latent_full_examples) < 5
                ):
                    external_blocked_latent_full_examples.append(
                        {
                            "target_side_ref": str(row.get("target_side_ref") or ""),
                            "stage": str(row.get("stage") or ""),
                            "financial_posture_class": str(row.get("financial_posture_class") or ""),
                            "reduce_only_recovery_active": bool(
                                row.get("reduce_only_recovery_active", False)
                            ),
                            "sec_to_expiry": row.get("sec_to_expiry"),
                            "market_reference_class": row.get("market_reference_class"),
                            "secondary_oracle_status": row.get("secondary_oracle_status"),
                            "depth_multiple_vs_cannon_target": row.get(
                                "depth_multiple_vs_cannon_target"
                            ),
                        }
                    )
            dominant_reason = str(row.get("latent_market_dominant_reject_reason") or "").strip()
            if dominant_reason:
                latent_market_dominant_reject_reason_counts[dominant_reason] += 1
            for reason in list(row.get("latent_market_reject_reasons") or []):
                latent_market_reject_reason_counts[str(reason or "unknown")] += 1
                if str(row.get("population_class") or "") == "external_blocked":
                    external_blocked_latent_market_reject_reason_counts[
                        str(reason or "unknown")
                    ] += 1
            if str(row.get("population_class") or "") == "external_blocked":
                external_blocked_latent_market_evaluable_count += 1
                if bool(row.get("latent_market_full_cannon_candidate", False)):
                    external_blocked_latent_market_full_candidate_count += 1
        if bool(row.get("full_mid_window_candidate", False)):
            full_mid_window_candidate_count += 1
            if not _maker_new_risk_allowed_from_row(row):
                full_candidate_runtime_stage_disallow_count += 1
            top_full_candidate_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
        else:
            top_reject_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
        for reason in list(row.get("reject_reasons") or []):
            reject_reason_counts[str(reason or "unknown")] += 1

    summary = {
        "maker_mid_window_probe_version": int(MAKER_MID_WINDOW_PROBE_VERSION),
        "row_count": int(len(rows)),
        **counts,
        "population_class_counts": {
            key: int(population_counts[key]) for key in sorted(population_counts)
        },
        "full_mid_window_candidate_count": int(full_mid_window_candidate_count),
        "full_candidate_runtime_stage_disallow_count": int(full_candidate_runtime_stage_disallow_count),
        "reject_reason_distribution": {
            key: int(reject_reason_counts[key]) for key in sorted(reject_reason_counts)
        },
        "stage_distribution": {key: int(stage_counts[key]) for key in sorted(stage_counts)},
        "market_reference_class_distribution": {
            key: int(market_reference_class_counts[key]) for key in sorted(market_reference_class_counts)
        },
        "market_reference_mode_distribution": {
            key: int(market_reference_mode_counts[key]) for key in sorted(market_reference_mode_counts)
        },
        "market_reference_source_side_distribution": {
            key: int(market_reference_source_side_counts[key]) for key in sorted(market_reference_source_side_counts)
        },
        "market_probability_band_distribution": {
            key: int(market_probability_band_counts[key]) for key in sorted(market_probability_band_counts)
        },
        "favored_side_depth_class_distribution": {
            key: int(favored_side_depth_class_counts[key]) for key in sorted(favored_side_depth_class_counts)
        },
        "financial_posture_class_distribution": {
            key: int(financial_posture_counts[key]) for key in sorted(financial_posture_counts)
        },
        "maker_timing_band_class_distribution": {
            key: int(timing_band_counts[key]) for key in sorted(timing_band_counts)
        },
        "session_regime_class_distribution": {
            key: int(session_regime_counts[key]) for key in sorted(session_regime_counts)
        },
        "stack_pressure_class_distribution": {
            key: int(stack_pressure_counts[key]) for key in sorted(stack_pressure_counts)
        },
        "secondary_oracle_status_distribution": {
            key: int(secondary_oracle_status_counts[key]) for key in sorted(secondary_oracle_status_counts)
        },
        "secondary_oracle_confirmation_distribution": {
            key: int(secondary_oracle_confirmation_counts[key])
            for key in sorted(secondary_oracle_confirmation_counts)
        },
        "maker_new_risk_allowed_distribution": {
            key: int(maker_new_risk_allowed_counts[key])
            for key in sorted(maker_new_risk_allowed_counts)
        },
        "probe_visible_depth_fail_closed_zero_distribution": {
            key: int(probe_visible_depth_fail_closed_zero_counts[key])
            for key in sorted(probe_visible_depth_fail_closed_zero_counts)
        },
        "geometry_viable_counts": {
            key: int(geometry_viable_counts[key]) for key in sorted(geometry_viable_counts)
        },
        "cannon_depth_requirement_counts": {
            key: int(cannon_depth_requirement_counts[key]) for key in sorted(cannon_depth_requirement_counts)
        },
        "depth_multiple_vs_cannon_target_summary": {
            "count": float(len(depth_multiple_values)),
            "min": float(min(depth_multiple_values)) if depth_multiple_values else 0.0,
            "p50": float(_percentile(sorted(depth_multiple_values), 0.50)) if depth_multiple_values else 0.0,
            "p90": float(_percentile(sorted(depth_multiple_values), 0.90)) if depth_multiple_values else 0.0,
            "max": float(max(depth_multiple_values)) if depth_multiple_values else 0.0,
            "mean": float(sum(depth_multiple_values) / len(depth_multiple_values)) if depth_multiple_values else 0.0,
            "median": float(median(depth_multiple_values)) if depth_multiple_values else 0.0,
        },
        "latent_market_truth_class_counts": {
            key: int(latent_market_truth_class_counts[key])
            for key in sorted(latent_market_truth_class_counts)
        },
        "latent_market_full_mid_window_candidate_count": int(
            latent_market_full_mid_window_candidate_count
        ),
        "latent_market_full_candidate_population_class_distribution": {
            key: int(latent_market_full_candidate_population_counts[key])
            for key in sorted(latent_market_full_candidate_population_counts)
        },
        "latent_market_reject_reason_distribution": {
            key: int(latent_market_reject_reason_counts[key])
            for key in sorted(latent_market_reject_reason_counts)
        },
        "latent_market_dominant_reject_reason_distribution": {
            key: int(latent_market_dominant_reject_reason_counts[key])
            for key in sorted(latent_market_dominant_reject_reason_counts)
        },
        "external_blocked_latent_market_evaluable_count": int(
            external_blocked_latent_market_evaluable_count
        ),
        "external_blocked_latent_market_full_candidate_count": int(
            external_blocked_latent_market_full_candidate_count
        ),
        "external_blocked_latent_market_reject_reason_distribution": {
            key: int(external_blocked_latent_market_reject_reason_counts[key])
            for key in sorted(external_blocked_latent_market_reject_reason_counts)
        },
        "external_blocked_latent_full_examples": external_blocked_latent_full_examples,
        "top_full_candidate_target_side_ref_counts": {
            key: int(value) for key, value in top_full_candidate_target_side_counts.most_common(10)
        },
        "top_reject_target_side_ref_counts": {
            key: int(value) for key, value in top_reject_target_side_counts.most_common(10)
        },
    }
    return {"rows": rows, "summary": summary}


def _legacy_status_samples(status: List[Dict[str, Any]]) -> List[Tuple[dt.datetime, str]]:
    samples: List[Tuple[dt.datetime, str]] = []
    for row in status:
        ts = parse_ts(row.get("ts_decision_utc") or row.get("ts_status_utc") or row.get("ts_utc"))
        posture = str(row.get("financial_posture_class") or "").strip().upper()
        if ts is None or not posture:
            continue
        samples.append((ts, posture))
    samples.sort(key=lambda item: item[0])
    return samples


def _legacy_financial_posture_at(
    status_samples: List[Tuple[dt.datetime, str]],
    decision_ts: Optional[dt.datetime],
) -> Optional[str]:
    if decision_ts is None or not status_samples:
        return None
    best_posture: Optional[str] = None
    best_delta: Optional[float] = None
    for sample_ts, posture in status_samples:
        delta = abs((sample_ts - decision_ts).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_posture = posture
    if best_delta is not None and best_delta <= 5.0:
        return best_posture
    return None


def _legacy_edge_entries(events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    entries: List[Dict[str, Any]] = []
    by_order_id: Dict[str, Dict[str, Any]] = {}
    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        if str(evt.get("evaluation_scope") or "").strip().lower() != "maker":
            continue
        ts = parse_ts(evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"))
        target_ref = str(evt.get("target_ref") or "").strip() or None
        entry = {
            "ts": ts,
            "target_ref": target_ref,
            "token_id": str(evt.get("token_id") or "").strip(),
            "inferred_side": _infer_side_from_edge_value(evt.get("edge_value")),
            "edge_value": evt.get("edge_value"),
            "fair_probability": evt.get("fair_probability"),
            "market_probability": evt.get("market_probability"),
            "market_reference_class": evt.get("market_reference_class"),
            "cycle_index": evt.get("cycle_index"),
            "sec_to_expiry": evt.get("time_remaining_sec"),
            "effective_stage": effective_stage_from_payload(evt),
            "stage_bucket": stage_bucket_from_payload(evt),
            "stage": effective_stage_from_payload(evt),
            "raw_stage": stage_bucket_from_payload(evt),
            "reduce_only_recovery_active": evt.get("reduce_only_recovery_active"),
            "block_reason": evt.get("block_reason"),
            "submitted": bool(evt.get("submitted")),
        }
        entries.append(entry)
        order_id = str(evt.get("order_id") or "").strip()
        if order_id:
            by_order_id[order_id] = entry
    entries.sort(key=lambda item: item.get("ts") or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return entries, by_order_id


def _match_legacy_edge_entry(
    entries: List[Dict[str, Any]],
    *,
    order_id: Optional[str] = None,
    by_order_id: Optional[Dict[str, Dict[str, Any]]] = None,
    side: Optional[str] = None,
    decision_ts: Optional[dt.datetime] = None,
    reference_price: Optional[float] = None,
    target_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if order_id and isinstance(by_order_id, dict):
        matched = by_order_id.get(order_id)
        if matched is not None:
            return matched
    best_entry: Optional[Dict[str, Any]] = None
    best_score: Optional[Tuple[float, float, float]] = None
    for entry in entries:
        entry_side = str(entry.get("inferred_side") or "").strip().upper()
        if side and entry_side and entry_side != side:
            continue
        if target_ref and entry.get("target_ref") and entry.get("target_ref") != target_ref:
            continue
        entry_ts = entry.get("ts")
        time_delta = 9999.0
        if isinstance(entry_ts, dt.datetime) and isinstance(decision_ts, dt.datetime):
            time_delta = abs((entry_ts - decision_ts).total_seconds())
            if time_delta > 2.0:
                continue
        market_probability = entry.get("market_probability")
        price_delta = 9999.0
        if isinstance(reference_price, (int, float)) and isinstance(market_probability, (int, float)):
            price_delta = abs(float(reference_price) - float(market_probability))
        target_missing_penalty = 0.0 if entry.get("target_ref") else 1.0
        score = (time_delta, price_delta, target_missing_penalty)
        if best_score is None or score < best_score:
            best_score = score
            best_entry = entry
    return best_entry


def _legacy_target_side_ref(target_ref: Optional[str], token_id: Any, side: Optional[str]) -> str:
    normalized_side = str(side or "UNKNOWN").strip().upper() or "UNKNOWN"
    target = str(target_ref or "").strip()
    if target:
        return f"{target}|{normalized_side}"
    token = str(token_id or "").strip() or "unknown_token"
    return f"legacy_token:{token}|{normalized_side}"


def _legacy_sizing_conflict(price_used: Any, geometry_floor_price: float) -> bool:
    price_value = _safe_float(price_used, default=0.0)
    return bool(geometry_floor_price > 0.0 and price_value > 0.0 and price_value + 1e-9 < geometry_floor_price)


def _legacy_maker_fight_admission_rows(
    *,
    events: List[Dict[str, Any]],
    status: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    thresholds = _legacy_maker_admission_thresholds(run_manifest)
    status_samples = _legacy_status_samples(status)
    edge_entries, edge_by_order_id = _legacy_edge_entries(events)
    rows: List[Dict[str, Any]] = []

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type not in {"order_submit", "quote_quality_skip"}:
            continue

        if event_type == "order_submit" and str(evt.get("submission_lane") or "").strip().lower() != "maker":
            continue

        decision_ts = parse_ts(evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"))
        side = str(evt.get("side") or "").strip().upper() or None
        reference_price = _safe_float(evt.get("price"), default=0.0)
        target_ref = str(evt.get("target_ref") or "").strip() or None
        order_id = str(evt.get("order_id") or "").strip() or None
        matched_edge = _match_legacy_edge_entry(
            edge_entries,
            order_id=order_id,
            by_order_id=edge_by_order_id,
            side=side,
            decision_ts=decision_ts,
            reference_price=reference_price if reference_price > 0.0 else None,
            target_ref=target_ref,
        )

        size_resolution = evt.get("size_resolution") if isinstance(evt.get("size_resolution"), dict) else {}
        maker_ctx = evt.get("maker_competitiveness") if isinstance(evt.get("maker_competitiveness"), dict) else {}
        if not target_ref:
            target_ref = (
                _extract_target_ref_from_decision_linkage_key(evt.get("decision_linkage_key"))
                or str((matched_edge or {}).get("target_ref") or "").strip()
                or None
            )
        if side is None:
            side = (
                _infer_side_from_edge_value((matched_edge or {}).get("edge_value"))
                or _infer_side_from_edge_value(maker_ctx.get("edge_signed"))
            )
        if side is None:
            side = "UNKNOWN"

        financial_posture_class = (
            str(evt.get("financial_posture_class") or "").strip().upper()
            or str(maker_ctx.get("financial_posture_class") or "").strip().upper()
            or _legacy_financial_posture_at(status_samples, decision_ts)
            or ""
        )
        reduce_only_active = (
            bool(evt.get("reduce_only_recovery_active", False))
            or bool(maker_ctx.get("reduce_only_recovery_active", False))
            or bool((matched_edge or {}).get("reduce_only_recovery_active", False))
        )
        fair_probability = maker_ctx.get("fair_probability")
        if fair_probability is None and matched_edge is not None:
            fair_probability = matched_edge.get("fair_probability")
        secondary_fair_probability = maker_ctx.get("secondary_fair_probability")
        market_probability = maker_ctx.get("market_probability")
        if market_probability is None and matched_edge is not None:
            market_probability = matched_edge.get("market_probability")
        edge_value = maker_ctx.get("edge_signed")
        if edge_value is None and matched_edge is not None:
            edge_value = matched_edge.get("edge_value")
        sec_to_expiry = maker_ctx.get("sec_to_expiry")
        if sec_to_expiry is None and matched_edge is not None:
            sec_to_expiry = matched_edge.get("sec_to_expiry")
        market_reference_class = (
            str((matched_edge or {}).get("market_reference_class") or "").strip()
            or ("authoritative" if bool(evt.get("decision_reference_recoverable")) else "")
        )
        expected_fill_prob = evt.get("expected_fill_prob")
        default_min_fill = thresholds["min_expected_fill_prob"]
        effective_min_fill = _safe_float(evt.get("effective_min_expected_fill_prob"), default=default_min_fill)
        if event_type == "order_submit" and reduce_only_active:
            effective_min_fill = min(default_min_fill, thresholds["reduce_only_fill_prob_floor"])
        max_queue_ahead = _safe_float(evt.get("effective_max_queue_ahead_size"), default=thresholds["max_queue_ahead_size"])
        if event_type == "order_submit" and reduce_only_active:
            max_queue_ahead = thresholds["max_queue_ahead_size"] * thresholds["reduce_only_queue_multiplier"]
        queue_ahead_size = evt.get("queue_ahead_size")
        intended_size_shares = evt.get("size")
        if intended_size_shares is None:
            intended_size_shares = size_resolution.get("resolved_shares")
        visible_depth_shares = size_resolution.get("visible_depth_shares")
        sizing_price_used = (
            size_resolution.get("price_used")
            if isinstance(size_resolution, dict) and size_resolution.get("price_used") is not None
            else evt.get("price")
        )
        if sizing_price_used is None:
            sizing_price_used = market_probability
        intended_notional_usd = None
        if isinstance(intended_size_shares, (int, float)) and isinstance(sizing_price_used, (int, float)):
            intended_notional_usd = float(intended_size_shares) * float(sizing_price_used)
        fill_prob_margin = None
        if isinstance(expected_fill_prob, (int, float)):
            fill_prob_margin = float(expected_fill_prob) - float(effective_min_fill)
        queue_delta_shares = None
        if isinstance(queue_ahead_size, (int, float)):
            queue_delta_shares = float(queue_ahead_size) - float(max_queue_ahead)
        size_to_visible_depth_ratio = None
        if isinstance(intended_size_shares, (int, float)) and isinstance(visible_depth_shares, (int, float)):
            if float(visible_depth_shares) > 0.0:
                size_to_visible_depth_ratio = float(intended_size_shares) / float(visible_depth_shares)
        geometry_floor_price = thresholds["geometry_floor_price"]
        sizing_conflict = _legacy_sizing_conflict(sizing_price_used, geometry_floor_price)
        viability_class = "impossible_only" if sizing_conflict else "viable_only"
        decision_result = "submitted" if event_type == "order_submit" else "quote_quality_skip"
        decision_block_reason = (
            evt.get("skip_reason")
            or evt.get("reason")
            or (matched_edge or {}).get("block_reason")
        )
        effective_stage = maker_ctx.get("effective_stage") or maker_ctx.get("stage")
        if not effective_stage:
            effective_stage = (matched_edge or {}).get("effective_stage") or (matched_edge or {}).get("stage") or evt.get("stage")
        stage_bucket = maker_ctx.get("stage_bucket") or maker_ctx.get("raw_stage")
        if not stage_bucket:
            stage_bucket = (
                (matched_edge or {}).get("stage_bucket")
                or (matched_edge or {}).get("raw_stage")
                or evt.get("raw_stage")
            )
        row = {
            "admission_shadow_id": f"legacy-{event_type}-{len(rows) + 1}",
            "shadow_source_class": "legacy_quote_or_submit_backfill_v1",
            "run_id": evt.get("run_id"),
            "token_id": evt.get("token_id"),
            "target_ref": target_ref,
            "target_side_ref": _legacy_target_side_ref(target_ref, evt.get("token_id"), side),
            "side": side,
            **stage_surface_fields(
                effective_stage=effective_stage,
                stage_bucket=stage_bucket,
            ),
            "stage": effective_stage,
            "cycle_index": (matched_edge or {}).get("cycle_index"),
            "ts_decision_utc": evt.get("ts_decision_utc") or evt.get("ts_event_utc") or evt.get("ts_utc"),
            "fair_probability": fair_probability,
            "market_probability": market_probability,
            "edge_value": edge_value,
            "sec_to_expiry": sec_to_expiry,
            "market_reference_class": market_reference_class,
            "secondary_fair_probability": secondary_fair_probability,
            "secondary_edge_value": maker_ctx.get("secondary_edge_value"),
            "secondary_oracle_status": maker_ctx.get("secondary_oracle_status"),
            "secondary_oracle_confirmation": maker_ctx.get("secondary_oracle_confirmation"),
            "chainlink_spot_price": maker_ctx.get("chainlink_spot_price"),
            "secondary_oracle_spot_price": maker_ctx.get("secondary_oracle_spot_price"),
            "secondary_oracle_price_delta_abs": maker_ctx.get("secondary_oracle_price_delta_abs"),
            "secondary_oracle_price_delta_bps": maker_ctx.get("secondary_oracle_price_delta_bps"),
            "viability_class": viability_class,
            "geometry_floor_price": geometry_floor_price,
            "sizing_price_used": sizing_price_used,
            "sizing_conflict": sizing_conflict,
            "desired_quote_price": evt.get("price"),
            "expected_fill_prob": expected_fill_prob,
            "min_expected_fill_prob": effective_min_fill,
            "fill_prob_margin": fill_prob_margin,
            "queue_ahead_size": queue_ahead_size,
            "max_queue_ahead_size": max_queue_ahead,
            "queue_delta_shares": queue_delta_shares,
            "visible_depth_shares": visible_depth_shares,
            "intended_size_shares": intended_size_shares,
            "intended_notional_usd": intended_notional_usd,
            "size_to_visible_depth_ratio": size_to_visible_depth_ratio,
            "same_target_side_shadow_count_prior": 0,
            "same_target_side_submit_count_prior": 0,
            "open_maker_orders_total": maker_ctx.get("open_maker_orders_total"),
            "open_orders_for_token_count": maker_ctx.get("open_orders_for_token_count"),
            "open_orders_same_side_count": maker_ctx.get("open_orders_same_side_count"),
            "financial_posture_class": financial_posture_class or None,
            "reduce_only_recovery_active": reduce_only_active,
            "decision_result": decision_result,
            "decision_block_reason": decision_block_reason,
            "order_submit_id": order_id if event_type == "order_submit" else None,
        }
        rows.append(row)

    rows.sort(
        key=lambda item: (
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("admission_shadow_id") or ""),
        )
    )
    shadow_counts: Counter[str] = Counter()
    submit_counts: Counter[str] = Counter()
    for row in rows:
        target_side_ref = str(row.get("target_side_ref") or "")
        row["same_target_side_shadow_count_prior"] = int(shadow_counts[target_side_ref])
        row["same_target_side_submit_count_prior"] = int(submit_counts[target_side_ref])
        shadow_counts[target_side_ref] += 1
        if str(row.get("decision_result") or "").strip().lower() == "submitted":
            submit_counts[target_side_ref] += 1
    return rows


def _maker_fight_admission_shadow_bundle(
    *,
    events: List[Dict[str, Any]],
    status: List[Dict[str, Any]],
    outcome_truth_records: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    def _summary(values: List[float]) -> Dict[str, float]:
        points = [float(value) for value in values if isinstance(value, (int, float))]
        if not points:
            return {
                "count": 0.0,
                "min": 0.0,
                "p50": 0.0,
                "p90": 0.0,
                "max": 0.0,
                "mean": 0.0,
                "median": 0.0,
            }
        ordered = sorted(points)
        return {
            "count": float(len(ordered)),
            "min": float(ordered[0]),
            "p50": float(_percentile(ordered, 0.50)),
            "p90": float(_percentile(ordered, 0.90)),
            "max": float(ordered[-1]),
            "mean": float(sum(ordered) / len(ordered)),
            "median": float(median(ordered)),
        }

    runtime_shadow_rows = [
        dict(evt)
        for evt in events
        if str(evt.get("event_type") or "").strip() == "maker_fight_admission_shadow"
    ]
    shadow_rows_raw = runtime_shadow_rows or _legacy_maker_fight_admission_rows(
        events=events,
        status=status,
        run_manifest=run_manifest,
    )
    outcome_by_submit_id = _maker_outcome_lookup(outcome_truth_records)
    normalized_rows: List[Dict[str, Any]] = []
    population_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    dominant_driver_counts: Counter[str] = Counter()
    submit_counts_by_class: Counter[str] = Counter()
    complete_joined_counts_by_class: Counter[str] = Counter()
    complete_bad_counts_by_class: Counter[str] = Counter()
    multifill_complete_counts_by_class: Counter[str] = Counter()
    multifill_incorrect_counts_by_class: Counter[str] = Counter()
    outcome_truth_status_by_class: Dict[str, Counter[str]] = defaultdict(Counter)
    claim_boundary_by_class: Dict[str, Counter[str]] = defaultdict(Counter)
    evaluation_horizon_by_class: Dict[str, Counter[str]] = defaultdict(Counter)
    top_trash_target_side_counts: Counter[str] = Counter()
    top_clean_target_side_counts: Counter[str] = Counter()
    source_class_counts: Counter[str] = Counter()
    cannon_window_counts: Counter[str] = Counter()
    maker_timing_band_counts: Counter[str] = Counter()
    session_regime_counts: Counter[str] = Counter()
    stack_pressure_counts: Counter[str] = Counter()
    secondary_oracle_status_counts: Counter[str] = Counter()
    secondary_oracle_confirmation_counts: Counter[str] = Counter()
    launch_safe_selection_timing_window_met_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    cannon_depth_multiple_values: List[float] = []
    candidate_counts_by_timing_band: Counter[str] = Counter()
    admission_class_counts_by_timing_band: Dict[str, Counter[str]] = defaultdict(Counter)
    submitted_counts_by_timing_band: Counter[str] = Counter()
    complete_joined_counts_by_timing_band: Counter[str] = Counter()
    complete_bad_counts_by_timing_band: Counter[str] = Counter()
    multifill_complete_counts_by_timing_band: Counter[str] = Counter()
    multifill_incorrect_counts_by_timing_band: Counter[str] = Counter()

    for raw in shadow_rows_raw:
        row = dict(raw)
        row["admission_rubric_version"] = ADMISSION_RUBRIC_VERSION
        row["shadow_source_class"] = str(
            row.get("shadow_source_class")
            or ("runtime_raw" if runtime_shadow_rows else "legacy_quote_or_submit_backfill_v1")
        )
        _apply_maker_cannon_shadow_fields(row)
        population_class = _maker_fight_admission_population_class(row)
        row["population_class"] = population_class
        row["admission_score"] = None
        row["admission_class"] = None
        row["component_scores"] = None
        row["hard_fail_reasons"] = []
        row["soft_driver_flags"] = []
        row["dominant_driver"] = None
        source_class_counts[str(row.get("shadow_source_class") or "unknown")] += 1
        cannon_window_counts[str(row.get("cannon_window_class") or "unknown")] += 1
        maker_timing_band = str(row.get("maker_timing_band_class") or "unknown")
        maker_timing_band_counts[maker_timing_band] += 1
        session_regime_counts[str(row.get("session_regime_class") or "unknown")] += 1
        stack_pressure_counts[str(row.get("stack_pressure_class") or "unknown")] += 1
        secondary_oracle_status_counts[str(row.get("secondary_oracle_status") or "unknown")] += 1
        secondary_oracle_confirmation_counts[
            "confirmed" if bool(row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
        ] += 1
        if isinstance(row.get("launch_safe_selection_timing_window_met"), bool):
            launch_safe_selection_timing_window_met_counts[
                "met" if bool(row.get("launch_safe_selection_timing_window_met")) else "not_met"
            ] += 1
        else:
            launch_safe_selection_timing_window_met_counts["unknown"] += 1
        if isinstance(row.get("cannon_depth_requirement_met"), bool):
            cannon_depth_requirement_counts[
                "met" if bool(row.get("cannon_depth_requirement_met")) else "not_met"
            ] += 1
        else:
            cannon_depth_requirement_counts["unknown"] += 1
        if isinstance(row.get("depth_multiple_vs_cannon_target"), (int, float)):
            cannon_depth_multiple_values.append(float(row["depth_multiple_vs_cannon_target"]))

        if population_class == "candidate":
            scored = _maker_fight_admission_score(row)
            row.update(scored)
            admission_class = str(row.get("admission_class") or "")
            candidate_counts_by_timing_band[maker_timing_band] += 1
            admission_class_counts_by_timing_band[maker_timing_band][admission_class] += 1
            class_counts[admission_class] += 1
            dominant_driver_counts[str(row.get("dominant_driver") or "unknown")] += 1
            if admission_class == "trash":
                top_trash_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
            elif admission_class == "clean":
                top_clean_target_side_counts[str(row.get("target_side_ref") or "unknown")] += 1
            if str(row.get("decision_result") or "").strip().lower() == "submitted":
                submit_counts_by_class[admission_class] += 1
                submitted_counts_by_timing_band[maker_timing_band] += 1

        population_counts[population_class] += 1
        order_submit_id = str(row.get("order_submit_id") or "").strip()
        outcome_row = outcome_by_submit_id.get(order_submit_id)
        if isinstance(outcome_row, dict):
            row["outcome_truth_status"] = outcome_row.get("outcome_truth_status")
            row["claim_boundary_class"] = outcome_row.get("claim_boundary_class")
            row["evaluation_horizon_ms"] = outcome_row.get("evaluation_horizon_ms")
            row["decision_quality"] = outcome_row.get("decision_quality")
            row["fill_count"] = outcome_row.get("fill_count")
            if population_class == "candidate" and row.get("admission_class") is not None:
                admission_class = str(row.get("admission_class") or "")
                outcome_truth_status_by_class[admission_class][
                    str(outcome_row.get("outcome_truth_status") or "unknown")
                ] += 1
                claim_boundary_by_class[admission_class][
                    str(outcome_row.get("claim_boundary_class") or "unknown")
                ] += 1
                evaluation_horizon_by_class[admission_class][
                    str(outcome_row.get("evaluation_horizon_ms") or "unknown")
                ] += 1
                if str(outcome_row.get("outcome_truth_status") or "").strip().lower() == "complete":
                    complete_joined_counts_by_class[admission_class] += 1
                    complete_joined_counts_by_timing_band[maker_timing_band] += 1
                    if str(outcome_row.get("decision_quality") or "").strip().lower() == "incorrect":
                        complete_bad_counts_by_class[admission_class] += 1
                        complete_bad_counts_by_timing_band[maker_timing_band] += 1
                    fill_count = int(_safe_float(outcome_row.get("fill_count")))
                    if fill_count >= 2:
                        multifill_complete_counts_by_class[admission_class] += 1
                        multifill_complete_counts_by_timing_band[maker_timing_band] += 1
                        if str(outcome_row.get("decision_quality") or "").strip().lower() == "incorrect":
                            multifill_incorrect_counts_by_class[admission_class] += 1
                            multifill_incorrect_counts_by_timing_band[maker_timing_band] += 1
        else:
            row["outcome_truth_status"] = None
            row["claim_boundary_class"] = None
            row["evaluation_horizon_ms"] = None
            row["decision_quality"] = None
            row["fill_count"] = None

        normalized_rows.append(row)

    normalized_rows.sort(
        key=lambda item: (
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("admission_shadow_id") or ""),
        )
    )
    normalized_rows = _maker_shadow_rows_with_submit_history(
        shadow_rows=normalized_rows,
        events=events,
        outcome_truth_records=outcome_truth_records,
    )

    candidate_rows = [row for row in normalized_rows if str(row.get("population_class") or "") == "candidate"]
    clean_but_bad_rows = [
        row
        for row in candidate_rows
        if str(row.get("admission_class") or "") == "clean"
        and str(row.get("outcome_truth_status") or "").strip().lower() == "complete"
        and str(row.get("decision_quality") or "").strip().lower() == "incorrect"
    ]
    trash_but_okay_rows = [
        row
        for row in candidate_rows
        if str(row.get("admission_class") or "") == "trash"
        and str(row.get("outcome_truth_status") or "").strip().lower() == "complete"
        and str(row.get("decision_quality") or "").strip().lower() in {"correct", "neutral"}
    ]

    summary = {
        "admission_rubric_version": ADMISSION_RUBRIC_VERSION,
        "maker_cannon_shadow_version": MAKER_CANNON_SHADOW_VERSION,
        "row_count": int(len(normalized_rows)),
        "shadow_source_class_distribution": {
            key: int(source_class_counts[key]) for key in sorted(source_class_counts)
        },
        "population_class_counts": {key: int(population_counts[key]) for key in sorted(population_counts)},
        "admission_class_counts": {key: int(class_counts[key]) for key in ("clean", "borderline", "trash")},
        "submit_rate_by_class": {
            class_name: (
                float(submit_counts_by_class[class_name] / class_counts[class_name])
                if class_counts[class_name] > 0
                else 0.0
            )
            for class_name in ("clean", "borderline", "trash")
        },
        "complete_joined_count_by_class": {
            key: int(complete_joined_counts_by_class[key]) for key in ("clean", "borderline", "trash")
        },
        "complete_bad_ratio_by_class": {
            class_name: (
                float(complete_bad_counts_by_class[class_name] / complete_joined_counts_by_class[class_name])
                if complete_joined_counts_by_class[class_name] > 0
                else 0.0
            )
            for class_name in ("clean", "borderline", "trash")
        },
        "multifill_incorrect_ratio_by_class": {
            class_name: (
                float(multifill_incorrect_counts_by_class[class_name] / multifill_complete_counts_by_class[class_name])
                if multifill_complete_counts_by_class[class_name] > 0
                else 0.0
            )
            for class_name in ("clean", "borderline", "trash")
        },
        "dominant_driver_distribution": {
            key: int(dominant_driver_counts[key]) for key in sorted(dominant_driver_counts)
        },
        "top_trash_target_side_ref_counts": {
            key: int(value) for key, value in top_trash_target_side_counts.most_common(10)
        },
        "top_clean_target_side_ref_counts": {
            key: int(value) for key, value in top_clean_target_side_counts.most_common(10)
        },
        "cannon_window_class_distribution": {
            key: int(cannon_window_counts[key]) for key in sorted(cannon_window_counts)
        },
        "maker_timing_band_class_distribution": {
            key: int(maker_timing_band_counts[key]) for key in sorted(maker_timing_band_counts)
        },
        "candidate_count_by_timing_band": {
            key: int(candidate_counts_by_timing_band[key]) for key in sorted(candidate_counts_by_timing_band)
        },
        "admission_class_distribution_by_timing_band": {
            band: {
                class_name: int(counter[class_name])
                for class_name in ("clean", "borderline", "trash")
            }
            for band, counter in sorted(admission_class_counts_by_timing_band.items())
        },
        "submitted_count_by_timing_band": {
            key: int(submitted_counts_by_timing_band[key]) for key in sorted(submitted_counts_by_timing_band)
        },
        "complete_joined_count_by_timing_band": {
            key: int(complete_joined_counts_by_timing_band[key])
            for key in sorted(complete_joined_counts_by_timing_band)
        },
        "complete_bad_ratio_by_timing_band": {
            band: (
                float(complete_bad_counts_by_timing_band[band] / complete_joined_counts_by_timing_band[band])
                if complete_joined_counts_by_timing_band[band] > 0
                else 0.0
            )
            for band in sorted(candidate_counts_by_timing_band)
        },
        "multifill_incorrect_ratio_by_timing_band": {
            band: (
                float(
                    multifill_incorrect_counts_by_timing_band[band]
                    / multifill_complete_counts_by_timing_band[band]
                )
                if multifill_complete_counts_by_timing_band[band] > 0
                else 0.0
            )
            for band in sorted(candidate_counts_by_timing_band)
        },
        "session_regime_class_distribution": {
            key: int(session_regime_counts[key]) for key in sorted(session_regime_counts)
        },
        "stack_pressure_class_distribution": {
            key: int(stack_pressure_counts[key]) for key in sorted(stack_pressure_counts)
        },
        "secondary_oracle_status_distribution": {
            key: int(secondary_oracle_status_counts[key]) for key in sorted(secondary_oracle_status_counts)
        },
        "secondary_oracle_confirmation_distribution": {
            key: int(secondary_oracle_confirmation_counts[key])
            for key in sorted(secondary_oracle_confirmation_counts)
        },
        "launch_safe_selection_timing_window_met_distribution": {
            key: int(launch_safe_selection_timing_window_met_counts[key])
            for key in sorted(launch_safe_selection_timing_window_met_counts)
        },
        "cannon_depth_requirement_counts": {
            key: int(cannon_depth_requirement_counts[key])
            for key in sorted(cannon_depth_requirement_counts)
        },
        "depth_multiple_vs_cannon_target_summary": _summary(cannon_depth_multiple_values),
    }
    calibration_audit = {
        "admission_rubric_version": ADMISSION_RUBRIC_VERSION,
        "maker_cannon_shadow_version": MAKER_CANNON_SHADOW_VERSION,
        "shadow_source_class_distribution": summary["shadow_source_class_distribution"],
        "population_class_counts": summary["population_class_counts"],
        "admission_class_counts": summary["admission_class_counts"],
        "complete_joined_count_by_class": summary["complete_joined_count_by_class"],
        "complete_bad_ratio_by_class": summary["complete_bad_ratio_by_class"],
        "multifill_incorrect_ratio_by_class": summary["multifill_incorrect_ratio_by_class"],
        "cannon_window_class_distribution": summary["cannon_window_class_distribution"],
        "maker_timing_band_class_distribution": summary["maker_timing_band_class_distribution"],
        "candidate_count_by_timing_band": summary["candidate_count_by_timing_band"],
        "admission_class_distribution_by_timing_band": summary["admission_class_distribution_by_timing_band"],
        "submitted_count_by_timing_band": summary["submitted_count_by_timing_band"],
        "complete_joined_count_by_timing_band": summary["complete_joined_count_by_timing_band"],
        "complete_bad_ratio_by_timing_band": summary["complete_bad_ratio_by_timing_band"],
        "multifill_incorrect_ratio_by_timing_band": summary["multifill_incorrect_ratio_by_timing_band"],
        "session_regime_class_distribution": summary["session_regime_class_distribution"],
        "stack_pressure_class_distribution": summary["stack_pressure_class_distribution"],
        "secondary_oracle_status_distribution": summary["secondary_oracle_status_distribution"],
        "secondary_oracle_confirmation_distribution": summary["secondary_oracle_confirmation_distribution"],
        "launch_safe_selection_timing_window_met_distribution": summary[
            "launch_safe_selection_timing_window_met_distribution"
        ],
        "cannon_depth_requirement_counts": summary["cannon_depth_requirement_counts"],
        "depth_multiple_vs_cannon_target_summary": summary["depth_multiple_vs_cannon_target_summary"],
        "outcome_truth_status_distribution_by_class": {
            class_name: {key: int(counter[key]) for key in sorted(counter)}
            for class_name, counter in sorted(outcome_truth_status_by_class.items())
        },
        "claim_boundary_class_distribution_by_class": {
            class_name: {key: int(counter[key]) for key in sorted(counter)}
            for class_name, counter in sorted(claim_boundary_by_class.items())
        },
        "evaluation_horizon_ms_distribution_by_class": {
            class_name: {key: int(counter[key]) for key in sorted(counter)}
            for class_name, counter in sorted(evaluation_horizon_by_class.items())
        },
        "clean_but_bad_examples": _maker_fight_admission_examples(clean_but_bad_rows),
        "trash_but_okay_examples": _maker_fight_admission_examples(trash_but_okay_rows),
    }
    return {
        "rows": normalized_rows,
        "summary": summary,
        "calibration_audit": calibration_audit,
    }


def _maker_selection_window_bounds(run_manifest: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    config = run_manifest.get("config") if isinstance(run_manifest, dict) else {}
    strategy = config.get("strategy") if isinstance(config, dict) else {}
    maker_competitiveness = (
        strategy.get("maker_competitiveness") if isinstance(strategy, dict) else {}
    )
    selection_gate = (
        maker_competitiveness.get("selection_gate")
        if isinstance(maker_competitiveness, dict)
        else {}
    )
    min_sec = selection_gate.get("min_sec_to_expiry") if isinstance(selection_gate, dict) else None
    max_sec = selection_gate.get("max_sec_to_expiry") if isinstance(selection_gate, dict) else None
    min_value = float(min_sec) if isinstance(min_sec, (int, float)) else None
    max_value = float(max_sec) if isinstance(max_sec, (int, float)) else None
    return min_value, max_value


def _maker_shadow_match_index(
    shadow_rows: List[Dict[str, Any]],
) -> Dict[str, List[Tuple[dt.datetime, Dict[str, Any]]]]:
    by_target_side_ref: Dict[str, List[Tuple[dt.datetime, Dict[str, Any]]]] = defaultdict(list)
    for row in shadow_rows:
        target_side_ref = str(row.get("target_side_ref") or "").strip()
        decision_ts = parse_ts(
            row.get("ts_decision_utc") or row.get("ts_event_utc") or row.get("ts_utc")
        )
        if not target_side_ref or decision_ts is None:
            continue
        by_target_side_ref[target_side_ref].append((decision_ts, row))
    for entries in by_target_side_ref.values():
        entries.sort(key=lambda item: item[0])
    return by_target_side_ref


def _nearest_shadow_match(
    shadow_index: Dict[str, List[Tuple[dt.datetime, Dict[str, Any]]]],
    *,
    target_side_ref: str,
    decision_ts: Optional[dt.datetime],
    max_delta_sec: float = 1.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    if decision_ts is None:
        return None, None
    candidates = shadow_index.get(str(target_side_ref or "").strip(), [])
    best_row: Optional[Dict[str, Any]] = None
    best_delta: Optional[float] = None
    for candidate_ts, row in candidates:
        delta = abs((candidate_ts - decision_ts).total_seconds())
        if delta > max_delta_sec:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = float(delta)
            best_row = row
    return best_row, best_delta


def _maker_probe_rows_with_shadow_truth(
    *,
    probe_rows: List[Dict[str, Any]],
    shadow_rows: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    selection_min_sec, selection_max_sec = _maker_selection_window_bounds(run_manifest)
    shadow_index = _maker_shadow_match_index(shadow_rows)
    enriched_rows: List[Dict[str, Any]] = []
    for raw in probe_rows:
        row = dict(raw)
        decision_ts = parse_ts(row.get("ts_decision_utc") or row.get("ts_event_utc") or row.get("ts_utc"))
        shadow_match, shadow_delta_sec = _nearest_shadow_match(
            shadow_index,
            target_side_ref=str(row.get("target_side_ref") or ""),
            decision_ts=decision_ts,
        )
        row["matched_shadow_present"] = bool(shadow_match is not None)
        row["matched_shadow_delta_sec"] = (
            float(shadow_delta_sec) if isinstance(shadow_delta_sec, (int, float)) else None
        )
        row["matched_shadow_decision_result"] = (
            str(shadow_match.get("decision_result") or "").strip().lower()
            if isinstance(shadow_match, dict)
            else None
        )
        row["matched_shadow_decision_block_reason"] = (
            str(shadow_match.get("decision_block_reason") or "").strip().lower()
            if isinstance(shadow_match, dict) and str(shadow_match.get("decision_block_reason") or "").strip()
            else None
        )
        row["matched_shadow_order_submit_id"] = (
            str(shadow_match.get("order_submit_id") or "").strip()
            if isinstance(shadow_match, dict) and str(shadow_match.get("order_submit_id") or "").strip()
            else None
        )
        row["matched_shadow_selection_primary_reject_reason"] = None
        row["matched_shadow_selection_reject_reasons"] = []
        if isinstance(shadow_match, dict):
            primary_reject_reason = _normalize_selection_reject_reason(
                shadow_match.get("selection_gate_primary_reject_reason")
                or shadow_match.get("decision_block_reason")
            )
            reject_reasons = [
                reason
                for reason in (
                    _normalize_selection_reject_reason(raw)
                    for raw in list(shadow_match.get("selection_gate_all_reject_reasons") or [])
                )
                if reason
            ]
            if not reject_reasons and primary_reject_reason:
                reject_reasons = [primary_reject_reason]
            row["matched_shadow_selection_primary_reject_reason"] = primary_reject_reason
            row["matched_shadow_selection_reject_reasons"] = reject_reasons
        if row.get("launch_safe_selection_timing_window_met") is None and isinstance(
            row.get("sec_to_expiry"), (int, float)
        ):
            timing_window_met = True
            if selection_min_sec is not None:
                timing_window_met = bool(
                    timing_window_met
                    and float(row["sec_to_expiry"]) >= float(selection_min_sec) - 1e-9
                )
            if selection_max_sec is not None:
                timing_window_met = bool(
                    timing_window_met
                    and float(row["sec_to_expiry"]) <= float(selection_max_sec) + 1e-9
                )
            row["launch_safe_selection_timing_window_met"] = bool(timing_window_met)
        maker_no_submission_cause = str(row.get("maker_no_submission_cause") or "").strip().lower()
        if shadow_match is not None:
            row["desired_quote_present"] = True
        elif maker_no_submission_cause == "no_desired_quote":
            row["desired_quote_present"] = False
        else:
            row["desired_quote_present"] = None
        row["off_band_opportunity"] = bool(
            row.get("full_cannon_candidate")
            and row.get("launch_safe_selection_timing_window_met") is False
        )
        row["effective_stage"] = effective_stage_from_payload(row)
        row["stage_bucket"] = stage_bucket_from_payload(row)
        row["stage"] = row["effective_stage"]
        row["raw_stage"] = row["stage_bucket"]
        enriched_rows.append(row)
    return enriched_rows


def _counter_to_sorted_int_dict(counter: Counter[str]) -> Dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _normalize_selection_reject_reason(reason: Any) -> Optional[str]:
    text = str(reason or "").strip().lower()
    if not text:
        return None
    if text.startswith("launch_safe_selection_"):
        text = text.removeprefix("launch_safe_selection_")
    return text or None


def _normalized_shadow_support_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_rows: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        decision_result = str(row.get("decision_result") or "").strip().lower()
        primary_reason = _normalize_selection_reject_reason(
            row.get("selection_gate_primary_reject_reason") or row.get("decision_block_reason")
        )
        reject_reasons = [
            reason
            for reason in (
                _normalize_selection_reject_reason(value)
                for value in list(row.get("selection_gate_all_reject_reasons") or [])
            )
            if reason
        ]
        if decision_result == "selection_rejected":
            if primary_reason and not reject_reasons:
                reject_reasons = [primary_reason]
            if primary_reason:
                row["selection_gate_primary_reject_reason"] = primary_reason
            if reject_reasons:
                row["selection_gate_all_reject_reasons"] = reject_reasons
        normalized_rows.append(row)
    return normalized_rows


def _canonical_maker_selection_counterfactual_policy() -> Dict[str, Any]:
    return {
        "policy_name": "paper_universal_minimal_canonical_selection_authority",
        "version": int(MAKER_SELECTION_AUTHORITY_AUDIT_VERSION),
        "authority_contract": EDGE_AUTH_MAKER_NEW_RISK_FIELD,
        "allowed_stages": ["MAKER_POSITION", "MAKER_TAKER_SELECTIVE"],
        "require_secondary_oracle_confirmation": True,
        "require_one_sided_active": False,
        "max_same_target_submit_count_prior": 1,
        "max_same_target_side_submit_count_prior": 1,
        "min_depth_multiple": 0.0,
        "timing_authority": "external_existing_maker_timing_gate_only",
        "market_family_authority": "deferred_missing_clean_truth_surface",
    }


def _maker_selection_runtime_config(run_manifest: Dict[str, Any]) -> Dict[str, Any]:
    config = run_manifest.get("config") if isinstance(run_manifest, dict) else {}
    strategy = config.get("strategy") if isinstance(config, dict) else {}
    maker_comp = strategy.get("maker_competitiveness") if isinstance(strategy, dict) else {}
    selection_gate = maker_comp.get("selection_gate") if isinstance(maker_comp, dict) else {}
    if not isinstance(selection_gate, dict):
        selection_gate = {}
    return {
        "enabled": bool(selection_gate.get("enabled", False)),
        "authority_contract": EDGE_AUTH_MAKER_NEW_RISK_FIELD,
        "allowed_stages": [
            str(stage or "").strip().upper()
            for stage in list(selection_gate.get("allowed_stages") or [])
            if str(stage or "").strip()
        ],
        "require_secondary_oracle_confirmation": bool(
            selection_gate.get("require_secondary_oracle_confirmation", True)
        ),
        "require_one_sided_active": bool(selection_gate.get("require_one_sided_active", False)),
        "max_same_target_submit_count_prior": int(
            _safe_float(selection_gate.get("max_same_target_submit_count_prior"), 1.0)
        ),
        "max_same_target_side_submit_count_prior": int(
            _safe_float(selection_gate.get("max_same_target_side_submit_count_prior"), 1.0)
        ),
        "min_depth_multiple": float(_safe_float(selection_gate.get("min_depth_multiple"), 1.5)),
    }


def _maker_new_risk_allowed_from_row(row: Dict[str, Any]) -> bool:
    if EDGE_AUTH_MAKER_NEW_RISK_FIELD in row:
        return bool(row.get(EDGE_AUTH_MAKER_NEW_RISK_FIELD))
    stage = str(row.get("effective_stage") or row.get("stage") or "").strip().upper()
    return stage in {"MAKER_POSITION", "MAKER_TAKER_SELECTIVE"}


def _maker_shadow_rows_with_submit_history(
    *,
    shadow_rows: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    outcome_truth_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    maker_submit_by_order_id: Dict[str, Dict[str, Any]] = {}
    for evt in events:
        if str(evt.get("event_type") or "").strip() != "order_submit":
            continue
        if str(evt.get("submission_lane") or "").strip().lower() != "maker":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if order_id:
            maker_submit_by_order_id[order_id] = dict(evt)
    outcome_by_submit_id = _maker_outcome_lookup(outcome_truth_records)
    source_rows = [dict(row) for row in shadow_rows]
    rows = [dict(row) for row in source_rows]
    rows.sort(
        key=lambda item: (
            _maker_quote_integrity_event_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            str(item.get("admission_shadow_id") or ""),
        )
    )
    target_shadow_counts: Counter[str] = Counter()
    target_submit_counts: Counter[str] = Counter()
    target_side_shadow_counts: Counter[str] = Counter()
    target_side_submit_counts: Counter[str] = Counter()
    enriched_rows: List[Dict[str, Any]] = []
    for row in rows:
        target_ref = str(row.get("target_ref") or "").strip()
        side = str(row.get("side") or "").strip().upper()
        target_side_ref = str(row.get("target_side_ref") or "").strip() or _legacy_target_side_ref(
            target_ref or None,
            row.get("token_id"),
            side,
        )
        order_submit_id = str(row.get("order_submit_id") or "").strip()
        submit_evt = maker_submit_by_order_id.get(order_submit_id)
        maker_comp = (
            dict((submit_evt or {}).get("maker_competitiveness") or {})
            if isinstance((submit_evt or {}).get("maker_competitiveness"), dict)
            else {}
        )
        row["target_side_ref"] = target_side_ref
        row["same_target_shadow_count_prior"] = int(target_shadow_counts[target_ref]) if target_ref else 0
        row["same_target_submit_count_prior"] = int(target_submit_counts[target_ref]) if target_ref else 0
        row["same_target_side_shadow_count_prior"] = int(target_side_shadow_counts[target_side_ref])
        row["same_target_side_submit_count_prior"] = int(target_side_submit_counts[target_side_ref])
        if row.get("one_sided_active") is None:
            one_sided_active = maker_comp.get("one_sided_active")
            row["one_sided_active"] = bool(one_sided_active) if isinstance(one_sided_active, bool) else None
        if not str(row.get("side_policy") or "").strip():
            side_policy = str(maker_comp.get("side_policy") or "").strip().upper()
            row["side_policy"] = side_policy or None
        if not str(row.get("market_reference_mode") or "").strip():
            market_reference_mode = str(maker_comp.get("market_reference_mode") or "").strip().lower()
            row["market_reference_mode"] = market_reference_mode or None
        if isinstance(outcome_by_submit_id.get(order_submit_id), dict):
            outcome_row = outcome_by_submit_id[order_submit_id]
            row["decision_quality"] = outcome_row.get("decision_quality")
            row["execution_quality"] = outcome_row.get("execution_quality")
            row["edge_realized_x_size"] = outcome_row.get("edge_realized_x_size")
            row["outcome_truth_status"] = outcome_row.get("outcome_truth_status")
        target_shadow_counts[target_ref] += 1
        target_side_shadow_counts[target_side_ref] += 1
        if str(row.get("decision_result") or "").strip().lower() == "submitted":
            if target_ref:
                target_submit_counts[target_ref] += 1
            target_side_submit_counts[target_side_ref] += 1
        enriched_rows.append(row)
    by_shadow_id = {
        str(row.get("admission_shadow_id") or ""): row for row in enriched_rows if str(row.get("admission_shadow_id") or "")
    }
    ordered_rows: List[Dict[str, Any]] = []
    for row in source_rows:
        shadow_id = str(row.get("admission_shadow_id") or "")
        ordered_rows.append(dict(by_shadow_id.get(shadow_id) or row))
    return ordered_rows


def _maker_selection_authority_bundle(
    *,
    events: List[Dict[str, Any]],
    shadow_rows: List[Dict[str, Any]],
    outcome_truth_records: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    profile_name = str(((run_manifest.get("config") or {}).get("profile") or {}).get("name") or "").strip()
    runtime_config = _maker_selection_runtime_config(run_manifest)
    counterfactual_policy = _canonical_maker_selection_counterfactual_policy()
    chronology_rows = _maker_shadow_rows_with_submit_history(
        shadow_rows=shadow_rows,
        events=events,
        outcome_truth_records=outcome_truth_records,
    )
    chronology_rows.sort(
        key=lambda item: (
            _maker_quote_integrity_event_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            str(item.get("admission_shadow_id") or ""),
        )
    )
    submitted_counts_by_one_sided_active: Counter[str] = Counter()
    counterfactual_block_counts: Counter[str] = Counter()
    current_decision_counts: Counter[str] = Counter()
    counterfactual_decision_counts: Counter[str] = Counter()
    keep_order_submit_ids: List[str] = []
    block_order_submit_ids: List[str] = []
    audit_rows: List[Dict[str, Any]] = []
    for row in chronology_rows:
        stage = str(row.get("stage") or "").strip().upper()
        target_ref = str(row.get("target_ref") or "").strip()
        target_side_ref = str(row.get("target_side_ref") or "").strip()
        one_sided_active = bool(row.get("one_sided_active", False))
        current_decision = str(row.get("decision_result") or "").strip().lower() or "unknown"
        current_decision_counts[current_decision] += 1
        if current_decision == "submitted":
            submitted_counts_by_one_sided_active[
                "one_sided_active" if one_sided_active else "not_one_sided"
            ] += 1

        reject_reasons: List[str] = []
        counterfactual_applied = _maker_new_risk_allowed_from_row(row)
        if (
            not counterfactual_applied
            and EDGE_AUTH_MAKER_NEW_RISK_FIELD not in row
        ):
            counterfactual_applied = stage in set(counterfactual_policy.get("allowed_stages") or [])
        if counterfactual_applied:
            if (
                bool(counterfactual_policy.get("require_secondary_oracle_confirmation", True))
                and not bool(row.get("secondary_oracle_confirmation", False))
            ):
                reject_reasons.append("secondary_oracle_not_confirmed")
            if (
                bool(counterfactual_policy.get("require_one_sided_active", False))
                and not one_sided_active
            ):
                reject_reasons.append("selection_non_one_sided")
            if int(_safe_float(row.get("same_target_submit_count_prior"))) > int(
                counterfactual_policy.get("max_same_target_submit_count_prior", 0)
            ):
                reject_reasons.append("selection_prior_target_submit")
            if int(_safe_float(row.get("same_target_side_submit_count_prior"))) > int(
                counterfactual_policy.get("max_same_target_side_submit_count_prior", 0)
            ):
                reject_reasons.append("selection_prior_same_side_submit")
        counterfactual_decision = "blocked" if reject_reasons else "admitted"
        counterfactual_decision_counts[counterfactual_decision] += 1
        primary_reject_reason = reject_reasons[0] if reject_reasons else None
        if counterfactual_decision == "admitted":
            if str(row.get("order_submit_id") or "").strip():
                keep_order_submit_ids.append(str(row.get("order_submit_id") or "").strip())
        else:
            counterfactual_block_counts[primary_reject_reason or "unknown"] += 1
            if str(row.get("order_submit_id") or "").strip():
                block_order_submit_ids.append(str(row.get("order_submit_id") or "").strip())
        audit_rows.append(
            {
                "run_id": run_id,
                "profile_name": profile_name,
                "admission_shadow_id": row.get("admission_shadow_id"),
                "order_submit_id": row.get("order_submit_id"),
                "target_ref": target_ref or None,
                "target_side_ref": target_side_ref or None,
                "side": row.get("side"),
                "stage": stage or None,
                EDGE_AUTH_MAKER_NEW_RISK_FIELD: bool(_maker_new_risk_allowed_from_row(row)),
                "ts_decision_utc": row.get("ts_decision_utc"),
                "runtime_decision_result": current_decision,
                "runtime_decision_block_reason": row.get("decision_block_reason"),
                "selector_enabled_runtime": bool(runtime_config.get("enabled", False)),
                "selector_applied_counterfactual": bool(counterfactual_applied),
                "one_sided_active": one_sided_active,
                "side_policy": row.get("side_policy"),
                "market_reference_mode": row.get("market_reference_mode"),
                "same_target_submit_count_prior": int(_safe_float(row.get("same_target_submit_count_prior"))),
                "same_target_side_submit_count_prior": int(
                    _safe_float(row.get("same_target_side_submit_count_prior"))
                ),
                "counterfactual_decision": counterfactual_decision,
                "counterfactual_primary_reject_reason": primary_reject_reason,
                "counterfactual_reject_reasons": reject_reasons,
                "decision_quality": row.get("decision_quality"),
                "execution_quality": row.get("execution_quality"),
                "edge_realized_x_size": row.get("edge_realized_x_size"),
                "outcome_truth_status": row.get("outcome_truth_status"),
            }
        )
    summary = {
        "maker_selection_authority_audit_version": int(MAKER_SELECTION_AUTHORITY_AUDIT_VERSION),
        "profile_name": profile_name,
        "runtime_selector_enabled": bool(runtime_config.get("enabled", False)),
        "runtime_selector_config": runtime_config,
        "counterfactual_policy": counterfactual_policy,
        "row_count": int(len(audit_rows)),
        "current_decision_distribution": _counter_to_sorted_int_dict(current_decision_counts),
        "counterfactual_decision_distribution": _counter_to_sorted_int_dict(counterfactual_decision_counts),
        "submitted_count_by_one_sided_active": _counter_to_sorted_int_dict(
            submitted_counts_by_one_sided_active
        ),
        "blocked_count_by_canonical_reject_reason": _counter_to_sorted_int_dict(
            counterfactual_block_counts
        ),
        "counterfactual_keep_order_submit_ids": keep_order_submit_ids,
        "counterfactual_block_order_submit_ids": block_order_submit_ids,
        "authoritative_for_canonical_selection": True,
    }
    counterfactual = {
        "maker_selection_authority_audit_version": int(MAKER_SELECTION_AUTHORITY_AUDIT_VERSION),
        "profile_name": profile_name,
        "policy_name": counterfactual_policy["policy_name"],
        "row_count": int(len(audit_rows)),
        "keep_order_submit_ids": keep_order_submit_ids,
        "block_order_submit_ids": block_order_submit_ids,
        "rows": audit_rows,
    }
    return {
        "audit": summary,
        "counterfactual": counterfactual,
        "rows": audit_rows,
    }


def _maker_truth_favored_side_depth_state(row: Dict[str, Any]) -> str:
    favored_depth_class = str(row.get("favored_side_depth_class") or "").strip().lower()
    if favored_depth_class in {"nonzero_visible", "zero_visible", "unknown"}:
        return favored_depth_class
    visible_depth = row.get("probe_visible_depth_shares")
    if isinstance(visible_depth, (int, float)):
        return "zero_visible" if float(visible_depth) <= 1e-12 else "nonzero_visible"
    return "unknown"


def _maker_truth_readiness(row: Dict[str, Any]) -> Tuple[str, str]:
    market_reference_class = str(row.get("market_reference_class") or "").strip().lower()
    market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower()
    market_reference_source_side = str(row.get("market_reference_source_side") or "").strip().lower()
    fair_present = isinstance(row.get("fair_probability"), (int, float))
    market_present = isinstance(row.get("market_probability"), (int, float))
    depth_state = _maker_truth_favored_side_depth_state(row)

    if market_reference_class == "authoritative" and fair_present and market_present and market_reference_mode:
        readiness = "authoritative_complete" if depth_state != "unknown" else "authoritative_incomplete"
    elif market_reference_class == "authoritative":
        readiness = "authoritative_incomplete"
    elif market_reference_class == "bounded_approximation":
        readiness = "bounded_only"
    else:
        readiness = "missing_truth_input"

    if readiness == "authoritative_complete":
        primary = "none"
    elif market_reference_class == "bounded_approximation":
        if not fair_present or not market_present:
            primary = "missing_probability_inputs"
        elif market_reference_mode == "bounded_single_side_touch" and depth_state == "zero_visible":
            primary = "bounded_single_side_touch_zero_favored_depth"
        elif market_reference_mode == "bounded_single_side_touch" and market_reference_source_side in {"", "unknown", "none"}:
            primary = "bounded_single_side_touch_unknown_side"
        elif market_reference_mode == "bounded_single_side_touch":
            primary = "reference_mode_weakness"
        elif depth_state == "zero_visible":
            primary = "zero_favored_side_depth"
        elif depth_state == "unknown":
            primary = "unknown_favored_side_depth"
        else:
            primary = "mixed_truth_degradation"
    elif market_reference_class == "authoritative":
        if not fair_present or not market_present:
            primary = "missing_probability_inputs"
        elif not market_reference_mode:
            primary = "reference_mode_weakness"
        elif depth_state == "unknown":
            primary = "unknown_favored_side_depth"
        else:
            primary = "authoritative_incomplete"
    else:
        primary = "missing_truth_input"
    return readiness, primary


def _maker_live_quote_actionable(row: Dict[str, Any]) -> bool:
    truth_readiness_state, _ = _maker_truth_readiness(row)
    market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower()
    return bool(
        truth_readiness_state == "authoritative_complete"
        and str(market_reference_mode or "").strip().lower() in {"direct_midpoint", "backfilled_paired_touch"}
    )


def _maker_band_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    market_reference_counts: Counter[str] = Counter()
    no_submit_cause_counts: Counter[str] = Counter()
    desired_quote_counts: Counter[str] = Counter()
    shadow_decision_counts: Counter[str] = Counter()
    row_count = 0
    authoritative_reference_count = 0
    dual_oracle_confirmed_count = 0
    depth_met_count = 0
    geometry_viable_count = 0
    desired_quote_present_count = 0
    full_cannon_candidate_count = 0
    off_band_opportunity_count = 0
    runtime_stage_allowed_count = 0
    for row in rows:
        row_count += 1
        market_reference_class = str(row.get("market_reference_class") or "unknown").strip().lower() or "unknown"
        market_reference_counts[market_reference_class] += 1
        if market_reference_class == "authoritative":
            authoritative_reference_count += 1
        if bool(row.get("secondary_oracle_confirmation", False)):
            dual_oracle_confirmed_count += 1
        if row.get("cannon_depth_requirement_met") is True:
            depth_met_count += 1
        if row.get("geometry_viable") is True:
            geometry_viable_count += 1
        desired_quote_present = row.get("desired_quote_present")
        if desired_quote_present is True:
            desired_quote_present_count += 1
            desired_quote_counts["present"] += 1
        elif desired_quote_present is False:
            desired_quote_counts["missing"] += 1
        else:
            desired_quote_counts["unknown"] += 1
        if bool(row.get("full_cannon_candidate", False)):
            full_cannon_candidate_count += 1
        if bool(row.get("off_band_opportunity", False)):
            off_band_opportunity_count += 1
        if _maker_new_risk_allowed_from_row(row):
            runtime_stage_allowed_count += 1
        cause = str(row.get("maker_no_submission_cause") or "").strip().lower()
        if cause:
            no_submit_cause_counts[cause] += 1
        decision_result = str(row.get("matched_shadow_decision_result") or "").strip().lower()
        if decision_result:
            shadow_decision_counts[decision_result] += 1
    return {
        "row_count": int(row_count),
        "maker_new_risk_allowed_count": int(runtime_stage_allowed_count),
        "authoritative_reference_count": int(authoritative_reference_count),
        "dual_oracle_confirmed_count": int(dual_oracle_confirmed_count),
        "depth_met_count": int(depth_met_count),
        "geometry_viable_count": int(geometry_viable_count),
        "desired_quote_present_count": int(desired_quote_present_count),
        "full_cannon_candidate_count": int(full_cannon_candidate_count),
        "off_band_opportunity_count": int(off_band_opportunity_count),
        "market_reference_class_distribution": _counter_to_sorted_int_dict(market_reference_counts),
        "desired_quote_presence_distribution": _counter_to_sorted_int_dict(desired_quote_counts),
        "no_submit_cause_distribution": _counter_to_sorted_int_dict(no_submit_cause_counts),
        "matched_shadow_decision_distribution": _counter_to_sorted_int_dict(shadow_decision_counts),
    }


def _maker_quote_starvation_bundle(
    *,
    probe_rows: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    selection_min_sec, selection_max_sec = _maker_selection_window_bounds(run_manifest)
    rows: List[Dict[str, Any]] = []
    cause_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    market_reference_counts: Counter[str] = Counter()
    market_reference_mode_counts: Counter[str] = Counter()
    market_reference_source_side_counts: Counter[str] = Counter()
    secondary_confirmation_counts: Counter[str] = Counter()
    geometry_counts: Counter[str] = Counter()
    desired_quote_presence_counts: Counter[str] = Counter()
    fair_probability_presence_counts: Counter[str] = Counter()
    market_probability_presence_counts: Counter[str] = Counter()
    favored_depth_truth_state_counts: Counter[str] = Counter()
    for row in probe_rows:
        if not _maker_new_risk_allowed_from_row(row):
            continue
        block_reason = str(row.get("block_reason") or "").strip().lower()
        maker_no_submission_cause = str(row.get("maker_no_submission_cause") or "").strip().lower()
        maker_no_submission_category = (
            str(row.get("maker_no_submission_category") or "").strip().lower()
        )
        if block_reason != "maker_no_submission" and not maker_no_submission_cause and not maker_no_submission_category:
            continue
        if bool(row.get("matched_shadow_present", False)):
            continue
        desired_quote_present = row.get("desired_quote_present")
        if desired_quote_present is True:
            continue
        if not _maker_live_quote_actionable(row):
            continue
        audit_row = {
            "run_id": row.get("run_id"),
            "token_id": row.get("token_id"),
            "target_ref": row.get("target_ref"),
            "target_side_ref": row.get("target_side_ref"),
            "side": row.get("side"),
            "effective_stage": effective_stage_from_payload(row),
            "stage_bucket": stage_bucket_from_payload(row),
            "raw_stage": stage_bucket_from_payload(row),
            "stage": effective_stage_from_payload(row),
            "ts_decision_utc": row.get("ts_decision_utc"),
            "sec_to_expiry": row.get("sec_to_expiry"),
            "maker_new_risk_allowed": _maker_new_risk_allowed_from_row(row),
            "selection_gate_min_sec_to_expiry": selection_min_sec,
            "selection_gate_max_sec_to_expiry": selection_max_sec,
            "launch_safe_selection_timing_window_met": row.get("launch_safe_selection_timing_window_met"),
            "secondary_oracle_confirmation": bool(row.get("secondary_oracle_confirmation", False)),
            "market_reference_class": row.get("market_reference_class"),
            "market_reference_mode": row.get("market_reference_mode"),
            "market_reference_source_side": row.get("market_reference_source_side"),
            "fair_probability": row.get("fair_probability"),
            "market_probability": row.get("market_probability"),
            "geometry_viable": row.get("geometry_viable"),
            "depth_multiple_vs_cannon_target": row.get("depth_multiple_vs_cannon_target"),
            "favored_side_depth_truth_state": _maker_truth_favored_side_depth_state(row),
            "desired_quote_present": desired_quote_present,
            "maker_no_submission_cause": maker_no_submission_cause or None,
            "maker_no_submission_category": maker_no_submission_category or None,
            "matched_shadow_present": False,
            "matched_shadow_delta_sec": None,
        }
        rows.append(audit_row)
        cause = str(audit_row.get("maker_no_submission_cause") or "").strip().lower() or "unknown"
        category = str(audit_row.get("maker_no_submission_category") or "").strip().lower() or "unknown"
        desired_key = (
            "present"
            if desired_quote_present is True
            else "missing" if desired_quote_present is False else "unknown"
        )
        cause_counts[cause] += 1
        category_counts[category] += 1
        desired_quote_presence_counts[desired_key] += 1
        market_reference_counts[
            str(audit_row.get("market_reference_class") or "unknown").strip().lower() or "unknown"
        ] += 1
        market_reference_mode_counts[
            str(audit_row.get("market_reference_mode") or "unknown").strip().lower() or "unknown"
        ] += 1
        market_reference_source_side_counts[
            str(audit_row.get("market_reference_source_side") or "unknown").strip().lower() or "unknown"
        ] += 1
        secondary_confirmation_counts[
            "confirmed" if bool(audit_row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
        ] += 1
        fair_probability_presence_counts[
            "present" if isinstance(audit_row.get("fair_probability"), (int, float)) else "missing"
        ] += 1
        market_probability_presence_counts[
            "present" if isinstance(audit_row.get("market_probability"), (int, float)) else "missing"
        ] += 1
        favored_depth_truth_state_counts[
            str(audit_row.get("favored_side_depth_truth_state") or "unknown").strip().lower() or "unknown"
        ] += 1
        geometry_value = audit_row.get("geometry_viable")
        if geometry_value is True:
            geometry_counts["viable"] += 1
        elif geometry_value is False:
            geometry_counts["not_viable"] += 1
        else:
            geometry_counts["unknown"] += 1
    summary = {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "row_count": int(len(rows)),
        "quote_starvation_row_count": int(len(rows)),
        "maker_no_submission_cause_distribution": _counter_to_sorted_int_dict(cause_counts),
        "maker_no_submission_category_distribution": _counter_to_sorted_int_dict(category_counts),
        "desired_quote_presence_distribution": _counter_to_sorted_int_dict(desired_quote_presence_counts),
        "market_reference_class_distribution": _counter_to_sorted_int_dict(market_reference_counts),
        "market_reference_mode_distribution": _counter_to_sorted_int_dict(market_reference_mode_counts),
        "market_reference_source_side_distribution": _counter_to_sorted_int_dict(
            market_reference_source_side_counts
        ),
        "fair_probability_presence_distribution": _counter_to_sorted_int_dict(
            fair_probability_presence_counts
        ),
        "market_probability_presence_distribution": _counter_to_sorted_int_dict(
            market_probability_presence_counts
        ),
        "favored_side_depth_truth_state_distribution": _counter_to_sorted_int_dict(
            favored_depth_truth_state_counts
        ),
        "secondary_oracle_confirmation_distribution": _counter_to_sorted_int_dict(
            secondary_confirmation_counts
        ),
        "geometry_viability_distribution": _counter_to_sorted_int_dict(geometry_counts),
        "primary_starvation_cause": (
            max(sorted(cause_counts), key=lambda key: cause_counts[key]) if cause_counts else "none"
        ),
    }
    return {"rows": rows, "summary": summary}


def _maker_prequote_prereq_pass_rows(probe_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        row
        for row in probe_rows
        if row.get("launch_safe_selection_timing_window_met") is True
        and _maker_new_risk_allowed_from_row(row)
        and bool(row.get("secondary_oracle_confirmation", False))
    ]


def _maker_truth_reference_starvation_bundle(
    *,
    probe_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    readiness_counts: Counter[str] = Counter()
    deprivation_counts: Counter[str] = Counter()
    market_reference_mode_counts: Counter[str] = Counter()
    market_reference_source_side_counts: Counter[str] = Counter()
    favored_depth_truth_state_counts: Counter[str] = Counter()
    fair_probability_presence_counts: Counter[str] = Counter()
    market_probability_presence_counts: Counter[str] = Counter()

    for raw in _maker_prequote_prereq_pass_rows(probe_rows):
        row = dict(raw)
        market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower() or None
        market_reference_source_side = (
            str(row.get("market_reference_source_side") or "").strip().lower() or None
        )
        truth_readiness_state, truth_primary_deprivation_reason = _maker_truth_readiness(row)
        favored_side_depth_truth_state = _maker_truth_favored_side_depth_state(row)
        audit_row = {
            "run_id": row.get("run_id"),
            "token_id": row.get("token_id"),
            "target_ref": row.get("target_ref"),
            "target_side_ref": row.get("target_side_ref"),
            "side": row.get("side"),
            "effective_stage": effective_stage_from_payload(row),
            "stage_bucket": stage_bucket_from_payload(row),
            "raw_stage": stage_bucket_from_payload(row),
            "stage": effective_stage_from_payload(row),
            "ts_decision_utc": row.get("ts_decision_utc"),
            "sec_to_expiry": row.get("sec_to_expiry"),
            "market_reference_class": row.get("market_reference_class"),
            "market_reference_mode": market_reference_mode,
            "market_reference_source_side": market_reference_source_side,
            "fair_probability": row.get("fair_probability"),
            "market_probability": row.get("market_probability"),
            "geometry_viable": row.get("geometry_viable"),
            "depth_multiple_vs_cannon_target": row.get("depth_multiple_vs_cannon_target"),
            "favored_side_depth_truth_state": favored_side_depth_truth_state,
            "truth_readiness_state": truth_readiness_state,
            "truth_primary_deprivation_reason": truth_primary_deprivation_reason,
            "desired_quote_present": row.get("desired_quote_present"),
            "maker_no_submission_cause": row.get("maker_no_submission_cause"),
            "maker_no_submission_category": row.get("maker_no_submission_category"),
            "matched_shadow_present": bool(row.get("matched_shadow_present", False)),
            "matched_shadow_selection_primary_reject_reason": row.get(
                "matched_shadow_selection_primary_reject_reason"
            ),
        }
        rows.append(audit_row)
        readiness_counts[truth_readiness_state] += 1
        deprivation_counts[truth_primary_deprivation_reason] += 1
        market_reference_mode_counts[
            str(market_reference_mode or "unknown").strip().lower() or "unknown"
        ] += 1
        market_reference_source_side_counts[
            str(market_reference_source_side or "unknown").strip().lower() or "unknown"
        ] += 1
        favored_depth_truth_state_counts[favored_side_depth_truth_state] += 1
        fair_probability_presence_counts[
            "present" if isinstance(audit_row.get("fair_probability"), (int, float)) else "missing"
        ] += 1
        market_probability_presence_counts[
            "present" if isinstance(audit_row.get("market_probability"), (int, float)) else "missing"
        ] += 1

    summary = {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "row_count": int(len(rows)),
        "truth_readiness_state_distribution": _counter_to_sorted_int_dict(readiness_counts),
        "truth_readiness_distribution": _counter_to_sorted_int_dict(readiness_counts),
        "truth_primary_deprivation_reason_distribution": _counter_to_sorted_int_dict(
            deprivation_counts
        ),
        "market_reference_mode_distribution": _counter_to_sorted_int_dict(
            market_reference_mode_counts
        ),
        "market_reference_source_side_distribution": _counter_to_sorted_int_dict(
            market_reference_source_side_counts
        ),
        "favored_side_depth_truth_state_distribution": _counter_to_sorted_int_dict(
            favored_depth_truth_state_counts
        ),
        "fair_probability_presence_distribution": _counter_to_sorted_int_dict(
            fair_probability_presence_counts
        ),
        "market_probability_presence_distribution": _counter_to_sorted_int_dict(
            market_probability_presence_counts
        ),
        "primary_truth_starvation_cause": (
            max(sorted(deprivation_counts), key=lambda key: deprivation_counts[key])
            if deprivation_counts
            else "none"
        ),
    }
    return {"rows": rows, "summary": summary}


def _maker_quote_construction_bundle(
    *,
    truth_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    cause_counts: Counter[str] = Counter()
    truth_sound_rows = [row for row in truth_rows if row.get("truth_readiness_state") == "authoritative_complete"]
    for raw in truth_sound_rows:
        if raw.get("desired_quote_present") is True:
            continue
        cause = "unknown_quote_construction_failure"
        favored_depth_truth_state = str(raw.get("favored_side_depth_truth_state") or "unknown")
        maker_no_submission_category = str(raw.get("maker_no_submission_category") or "").strip().lower()
        if maker_no_submission_category == "one_sided_mode_disallow_side":
            cause = "one_sided_mode_disallow_side"
        elif not isinstance(raw.get("fair_probability"), (int, float)) or not isinstance(
            raw.get("market_probability"), (int, float)
        ):
            cause = "probability_input_missing"
        elif favored_depth_truth_state == "zero_visible" or float(
            _safe_float(raw.get("depth_multiple_vs_cannon_target"))
        ) <= 0.0:
            cause = "depth_zero_or_too_thin"
        elif raw.get("geometry_viable") is False:
            cause = "geometry_floor_failure"
        elif str(raw.get("maker_no_submission_cause") or "").strip().lower() == "no_desired_quote":
            cause = "expected_no_quote_under_doctrine"
        rows.append(
            {
                **raw,
                "quote_construction_primary_cause": cause,
            }
        )
        cause_counts[cause] += 1

    summary = {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "truth_sound_row_count": int(len(truth_sound_rows)),
        "authoritative_complete_row_count": int(len(truth_sound_rows)),
        "row_count": int(len(rows)),
        "quote_construction_primary_cause_distribution": _counter_to_sorted_int_dict(cause_counts),
        "primary_quote_construction_cause": (
            max(sorted(cause_counts), key=lambda key: cause_counts[key]) if cause_counts else "none"
        ),
    }
    return {"rows": rows, "summary": summary}


def _maker_timing_band_diagnostic_matrix(
    *,
    probe_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    def _rows_for_band(rows: List[Dict[str, Any]], band_name: str) -> List[Dict[str, Any]]:
        if band_name == "aggregate_10_to_20s":
            return [
                row
                for row in rows
                if str(row.get("cannon_window_class") or "") in {"10_to_15s", "15_to_20s"}
            ]
        return [row for row in rows if str(row.get("cannon_window_class") or "") == band_name]

    matrix: Dict[str, Any] = {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "aggregate_band_name": "aggregate_10_to_20s",
        "bands": {},
    }
    for band_name in ("10_to_15s", "15_to_20s", "le_10s", "aggregate_10_to_20s"):
        band_rows = _rows_for_band(probe_rows, band_name)
        runtime_active_rows = [
            row
            for row in band_rows
            if _maker_new_risk_allowed_from_row(row)
            and row.get("launch_safe_selection_timing_window_met") is True
        ]
        matrix["bands"][band_name] = {
            "band_kind": "derived_aggregate" if band_name == "aggregate_10_to_20s" else "atomic",
            "observational_candidate_quality": _maker_band_stats(band_rows),
            "runtime_eligible_active_band_quality": _maker_band_stats(runtime_active_rows),
        }
    return matrix


def _maker_timing_band_decision_bundle(
    *,
    probe_rows: List[Dict[str, Any]],
    truth_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    truth_by_key = {
        (
            str(row.get("target_side_ref") or ""),
            str(row.get("ts_decision_utc") or ""),
        ): row
        for row in truth_rows
    }

    band_rows: Dict[str, List[Dict[str, Any]]] = {"10_to_15s": [], "15_to_20s": [], "le_10s": []}
    for raw in probe_rows:
        band_name = str(raw.get("cannon_window_class") or "")
        if band_name not in band_rows:
            continue
        row = dict(raw)
        truth_row = truth_by_key.get((str(row.get("target_side_ref") or ""), str(row.get("ts_decision_utc") or "")))
        row["truth_readiness_state"] = truth_row.get("truth_readiness_state") if isinstance(truth_row, dict) else None
        row["truth_primary_deprivation_reason"] = (
            truth_row.get("truth_primary_deprivation_reason") if isinstance(truth_row, dict) else None
        )
        band_rows[band_name].append(row)

    band_summaries: Dict[str, Any] = {}
    for band_name, rows in band_rows.items():
        truth_sufficient_rows = [
            row for row in rows if row.get("truth_readiness_state") == "authoritative_complete"
        ]
        quoteable_rows = [row for row in truth_sufficient_rows if row.get("desired_quote_present") is True]
        fireable_rows = [
            row
            for row in quoteable_rows
            if bool(row.get("secondary_oracle_confirmation", False))
            and row.get("geometry_viable") is True
            and row.get("cannon_depth_requirement_met") is True
        ]
        band_summaries[band_name] = {
            "observed_row_count": int(len(rows)),
            "truth_sufficient_count": int(len(truth_sufficient_rows)),
            "quoteable_count": int(len(quoteable_rows)),
            "depth_met_count": int(sum(1 for row in rows if row.get("cannon_depth_requirement_met") is True)),
            "full_cannon_candidate_count": int(
                sum(1 for row in rows if bool(row.get("full_cannon_candidate", False)))
            ),
            "off_band_opportunity_count": int(
                sum(1 for row in rows if bool(row.get("off_band_opportunity", False)))
            ),
            "fireable_candidate_count": int(len(fireable_rows)),
        }

    recommended_action = "no_timing_change_truth_or_quoteability_dominant"
    if (
        band_summaries["10_to_15s"]["fireable_candidate_count"] <= 0
        and band_summaries["15_to_20s"]["fireable_candidate_count"] > 0
    ):
        recommended_action = "recommend_15_to_20s_runtime_experiment"
    elif (
        band_summaries["10_to_15s"]["fireable_candidate_count"] > 0
        and band_summaries["10_to_15s"]["fireable_candidate_count"]
        >= band_summaries["15_to_20s"]["fireable_candidate_count"]
    ):
        recommended_action = "keep_10_to_15s"
    elif all(summary["fireable_candidate_count"] <= 0 for summary in band_summaries.values()):
        recommended_action = "no_timing_change_truth_or_quoteability_dominant"

    return {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "bands": band_summaries,
        "recommended_action": recommended_action,
        "recommended_timing_action": recommended_action,
    }


def _maker_edge_event_target_side_ref(evt: Dict[str, Any]) -> str:
    favored_side = (
        str(evt.get("probe_favored_side") or "").strip().upper()
        or _infer_side_from_edge_value(evt.get("edge_value"))
        or "UNKNOWN"
    )
    target_ref = str(evt.get("target_ref") or "").strip() or None
    return _legacy_target_side_ref(target_ref, evt.get("token_id"), favored_side)


def _maker_edge_event_order_ids(evt: Dict[str, Any]) -> List[str]:
    order_ids: List[str] = []
    raw_ids = evt.get("submitted_order_ids")
    if isinstance(raw_ids, list):
        for raw in raw_ids:
            order_id = str(raw or "").strip()
            if order_id:
                order_ids.append(order_id)
    single_order_id = str(evt.get("order_id") or "").strip()
    if single_order_id and single_order_id not in order_ids:
        order_ids.append(single_order_id)
    return order_ids


def _maker_participation_waterfall_bundle(
    *,
    events: List[Dict[str, Any]],
    probe_rows: List[Dict[str, Any]],
    shadow_rows: List[Dict[str, Any]],
    outcome_truth_records: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
    truth_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    thresholds = _legacy_maker_admission_thresholds(run_manifest)
    timing_gate_min_sec_to_expiry = float(thresholds.get("maker_timing_gate_min_sec_to_expiry", 15.0) or 15.0)
    timing_gate_max_sec_to_expiry = float(thresholds.get("maker_timing_gate_max_sec_to_expiry", 20.0) or 20.0)
    maker_rows = [
        evt
        for evt in events
        if str(evt.get("event_type") or "").strip() == "edge_evaluation"
        and str(evt.get("evaluation_scope") or "").strip().lower() == "maker"
    ]
    outcome_by_submit_id = _maker_outcome_lookup(outcome_truth_records)
    terminal_path_counts: Counter[str] = Counter()
    stage_band_exclusion_reason_counts: Counter[str] = Counter()
    prequote_prereq_block_reason_counts: Counter[str] = Counter()
    truth_reference_insufficient_reason_counts: Counter[str] = Counter()
    desired_quote_missing_reason_counts: Counter[str] = Counter()
    selection_reject_reason_counts: Counter[str] = Counter()
    shadow_non_submit_reason_counts: Counter[str] = Counter()
    submit_attempt_reject_reason_counts: Counter[str] = Counter()
    submit_success_outcome_counts: Counter[str] = Counter()
    active_band_probe_rows = [row for row in probe_rows if row.get("launch_safe_selection_timing_window_met") is True]
    prequote_rows = _maker_prequote_prereq_pass_rows(probe_rows)
    truth_rows = list(truth_rows or [])
    truth_by_key = {
        (
            str(row.get("target_side_ref") or ""),
            str(row.get("ts_decision_utc") or ""),
            str(row.get("token_id") or ""),
        ): row
        for row in truth_rows
    }
    actionable_prequote_rows: List[Dict[str, Any]] = []
    for row in prequote_rows:
        key = (
            str(row.get("target_side_ref") or ""),
            str(row.get("ts_decision_utc") or ""),
            str(row.get("token_id") or ""),
        )
        truth_row = truth_by_key.get(key)
        if isinstance(truth_row, dict):
            truth_readiness_state = str(truth_row.get("truth_readiness_state") or "").strip().lower()
            truth_primary_deprivation_reason = (
                str(truth_row.get("truth_primary_deprivation_reason") or "").strip().lower() or "unknown"
            )
            market_reference_mode = str(truth_row.get("market_reference_mode") or "").strip().lower()
            actionable = bool(
                truth_readiness_state == "authoritative_complete"
                and market_reference_mode in {"direct_midpoint", "backfilled_paired_touch"}
            )
        else:
            truth_readiness_state, truth_primary_deprivation_reason = _maker_truth_readiness(row)
            market_reference_mode = str(row.get("market_reference_mode") or "").strip().lower()
            actionable = bool(
                truth_readiness_state == "authoritative_complete"
                and market_reference_mode in {"direct_midpoint", "backfilled_paired_touch"}
            )
        if actionable:
            actionable_prequote_rows.append(row)
            continue
        terminal_path_counts["truth_reference_insufficient"] += 1
        truth_reference_insufficient_reason_counts[truth_primary_deprivation_reason] += 1
    desired_quote_present_rows = [row for row in actionable_prequote_rows if row.get("desired_quote_present") is True]
    shadow_runtime_rows = [
        row for row in actionable_prequote_rows if bool(row.get("matched_shadow_present", False))
    ]
    pre_shadow_non_shadow_reason_counts: Counter[str] = Counter()

    for evt in maker_rows:
        sec_value = evt.get("time_remaining_sec")
        in_stage_band = False
        if isinstance(sec_value, (int, float)):
            in_stage_band = (
                float(timing_gate_min_sec_to_expiry) - 1e-9
                <= float(sec_value)
                <= float(timing_gate_max_sec_to_expiry) + 1e-9
            )
        if not in_stage_band:
            terminal_path_counts["stage_band_excluded"] += 1
            exclusion_reason = "outside_active_maker_band"
            if isinstance(sec_value, (int, float)) and float(sec_value) < float(timing_gate_min_sec_to_expiry):
                exclusion_reason = "earlier_or_post_window_outside_active_maker_band"
            stage_band_exclusion_reason_counts[exclusion_reason] += 1

    for row in active_band_probe_rows:
        if _maker_new_risk_allowed_from_row(row) and bool(
            row.get("secondary_oracle_confirmation", False)
        ):
            continue
        terminal_path_counts["prequote_prereq_blocked"] += 1
        if not _maker_new_risk_allowed_from_row(row):
            prequote_prereq_block_reason_counts[
                str(row.get("block_reason") or "runtime_stage_disallowed").strip().lower()
                or "runtime_stage_disallowed"
            ] += 1
        else:
            prequote_prereq_block_reason_counts["secondary_oracle_not_confirmed"] += 1

    for row in actionable_prequote_rows:
        if row.get("desired_quote_present") is True:
            continue
        terminal_path_counts["desired_quote_missing"] += 1
        reason = (
            str(row.get("maker_no_submission_cause") or row.get("maker_no_submission_category") or "")
            .strip()
            .lower()
            or "no_desired_quote"
        )
        desired_quote_missing_reason_counts[reason] += 1

    for row in desired_quote_present_rows:
        if bool(row.get("matched_shadow_present", False)):
            continue
        terminal_path_counts["pre_shadow_non_shadow"] += 1
        pre_shadow_non_shadow_reason_counts["shadow_missing_after_quote_present"] += 1

    for row in shadow_runtime_rows:
        decision_result = str(row.get("matched_shadow_decision_result") or "").strip().lower() or "unknown"
        if decision_result == "selection_rejected":
            terminal_path_counts["selection_rejected"] += 1
            reason = (
                _normalize_selection_reject_reason(
                    row.get("matched_shadow_selection_primary_reject_reason")
                    or row.get("matched_shadow_decision_block_reason")
                )
                or "selection_rejected"
            )
            selection_reject_reason_counts[reason] += 1
        elif decision_result == "submit_rejected":
            terminal_path_counts["submit_attempt_rejected"] += 1
            reason = (
                str(row.get("matched_shadow_decision_block_reason") or "").strip().lower()
                or "submit_rejected"
            )
            submit_attempt_reject_reason_counts[reason] += 1
        elif decision_result == "submitted":
            submit_order_id = str(row.get("matched_shadow_order_submit_id") or "").strip()
            outcome = outcome_by_submit_id.get(submit_order_id)
            if isinstance(outcome, dict) and int(_safe_float(outcome.get("fill_count"))) > 0:
                terminal_path_counts["fill"] += 1
                submit_success_outcome_counts["filled"] += 1
            else:
                terminal_path_counts["submit_success_unfilled"] += 1
                submit_success_outcome_counts["unfilled_or_missing_outcome"] += 1
        else:
            terminal_path_counts["shadow_non_submit_blocked"] += 1
            reason = (
                str(row.get("matched_shadow_decision_block_reason") or "").strip().lower()
                or decision_result
                or "shadow_non_submit_blocked"
            )
            shadow_non_submit_reason_counts[reason] += 1

    total_maker_rows = int(len(maker_rows))
    stage_band_allowed_rows = int(len(active_band_probe_rows))
    prequote_prereq_pass_rows = int(len(prequote_rows))
    truth_reference_sufficient_rows = int(len(actionable_prequote_rows))
    truth_reference_insufficient_rows = int(terminal_path_counts["truth_reference_insufficient"])
    desired_quote_missing_rows = int(terminal_path_counts["desired_quote_missing"])
    pre_shadow_non_shadow_rows = int(terminal_path_counts["pre_shadow_non_shadow"])
    selection_rejected_rows = int(terminal_path_counts["selection_rejected"])
    shadow_non_submit_blocked_rows = int(terminal_path_counts["shadow_non_submit_blocked"])
    submit_attempt_rejected_rows = int(terminal_path_counts["submit_attempt_rejected"])
    submit_success_unfilled_rows = int(terminal_path_counts["submit_success_unfilled"])
    fill_rows = int(terminal_path_counts["fill"])
    shadow_eligible_rows = int(len(shadow_runtime_rows))
    submit_attempt_rows = int(submit_attempt_rejected_rows + submit_success_unfilled_rows + fill_rows)
    submit_success_rows = int(submit_success_unfilled_rows + fill_rows)

    def _pct(count: int, prior: int) -> float:
        return float(count / prior) if prior > 0 else 0.0

    stages = {
        "total_maker_rows": {
            "count": total_maker_rows,
            "prior_stage": None,
            "percent_of_prior_stage": 1.0 if total_maker_rows > 0 else 0.0,
            "loss_reason_distribution": {},
        },
        "stage_band_allowed_rows": {
            "count": stage_band_allowed_rows,
            "prior_stage": "total_maker_rows",
            "percent_of_prior_stage": _pct(stage_band_allowed_rows, total_maker_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(stage_band_exclusion_reason_counts),
        },
        "prequote_prereq_pass_rows": {
            "count": prequote_prereq_pass_rows,
            "prior_stage": "stage_band_allowed_rows",
            "percent_of_prior_stage": _pct(prequote_prereq_pass_rows, stage_band_allowed_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(prequote_prereq_block_reason_counts),
        },
        "truth_reference_sufficient_rows": {
            "count": truth_reference_sufficient_rows,
            "prior_stage": "prequote_prereq_pass_rows",
            "percent_of_prior_stage": _pct(truth_reference_sufficient_rows, prequote_prereq_pass_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(truth_reference_insufficient_reason_counts),
        },
        "truth_reference_insufficient_rows": {
            "count": truth_reference_insufficient_rows,
            "prior_stage": "prequote_prereq_pass_rows",
            "percent_of_prior_stage": _pct(truth_reference_insufficient_rows, prequote_prereq_pass_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(truth_reference_insufficient_reason_counts),
        },
        "desired_quote_present_rows": {
            "count": int(len(desired_quote_present_rows)),
            "prior_stage": "truth_reference_sufficient_rows",
            "percent_of_prior_stage": _pct(int(len(desired_quote_present_rows)), truth_reference_sufficient_rows),
            "loss_reason_distribution": {"no_desired_quote": desired_quote_missing_rows}
            if desired_quote_missing_rows > 0
            else {},
        },
        "desired_quote_missing_rows": {
            "count": desired_quote_missing_rows,
            "prior_stage": "truth_reference_sufficient_rows",
            "percent_of_prior_stage": _pct(desired_quote_missing_rows, truth_reference_sufficient_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(desired_quote_missing_reason_counts),
        },
        "pre_shadow_non_shadow_rows": {
            "count": pre_shadow_non_shadow_rows,
            "prior_stage": "desired_quote_present_rows",
            "percent_of_prior_stage": _pct(pre_shadow_non_shadow_rows, int(len(desired_quote_present_rows))),
            "loss_reason_distribution": _counter_to_sorted_int_dict(pre_shadow_non_shadow_reason_counts),
        },
        "shadow_rows": {
            "count": shadow_eligible_rows,
            "prior_stage": "desired_quote_present_rows",
            "percent_of_prior_stage": _pct(shadow_eligible_rows, int(len(desired_quote_present_rows))),
            "loss_reason_distribution": {},
        },
        "selection_rejected_rows": {
            "count": selection_rejected_rows,
            "prior_stage": "shadow_rows",
            "percent_of_prior_stage": _pct(selection_rejected_rows, shadow_eligible_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(selection_reject_reason_counts),
        },
        "submit_attempt_rows": {
            "count": submit_attempt_rows,
            "prior_stage": "shadow_rows",
            "percent_of_prior_stage": _pct(submit_attempt_rows, shadow_eligible_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(submit_attempt_reject_reason_counts),
        },
        "submit_success_rows": {
            "count": submit_success_rows,
            "prior_stage": "submit_attempt_rows",
            "percent_of_prior_stage": _pct(submit_success_rows, submit_attempt_rows),
            "loss_reason_distribution": _counter_to_sorted_int_dict(submit_success_outcome_counts),
        },
        "fill_rows": {
            "count": fill_rows,
            "prior_stage": "submit_success_rows",
            "percent_of_prior_stage": _pct(fill_rows, submit_success_rows),
            "loss_reason_distribution": {},
        },
    }

    terminal_path_dict = _counter_to_sorted_int_dict(terminal_path_counts)
    total_accounted = int(sum(terminal_path_dict.values()))
    return {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "stages": stages,
        "terminal_path_counts": terminal_path_dict,
        "reconciliation": {
            "maker_rows_total": total_maker_rows,
            "terminal_rows_accounted": total_accounted,
            "accounting_closed": bool(total_accounted == total_maker_rows),
            "unaccounted_rows": int(max(0, total_maker_rows - total_accounted)),
        },
    }


def _maker_zero_submit_root_cause_bundle(
    *,
    probe_rows: List[Dict[str, Any]],
    shadow_rows: List[Dict[str, Any]],
    quote_starvation_rows: List[Dict[str, Any]],
    quote_starvation_summary: Dict[str, Any],
    waterfall: Dict[str, Any],
    runtime_classification: Dict[str, Any],
    truth_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    active_band_runtime_rows = _maker_prequote_prereq_pass_rows(probe_rows)
    truth_rows = list(truth_rows or [])
    active_band_truth_rows = [
        row
        for row in truth_rows
        if str(row.get("truth_readiness_state") or "").strip().lower() == "authoritative_complete"
        and str(row.get("market_reference_mode") or "").strip().lower() in {"direct_midpoint", "backfilled_paired_touch"}
    ]
    off_band_full_candidates = [
        row for row in probe_rows if bool(row.get("off_band_opportunity", False))
    ]
    zero_shadow_rows = int(waterfall.get("stages", {}).get("shadow_rows", {}).get("count", 0)) == 0
    runtime_eligible_pre_shadow_rows = int(len(quote_starvation_rows))

    active_band_reason_counts: Counter[str] = Counter()
    for row in active_band_runtime_rows:
        for reason in list(row.get("reject_reasons") or []):
            active_band_reason_counts[str(reason or "unknown")] += 1
    shadow_selection_rejected_row_count = int(
        sum(
            1
            for row in shadow_rows
            if str(row.get("decision_result") or "").strip().lower() == "selection_rejected"
        )
    )

    contradiction_ledger: List[Dict[str, Any]] = []
    if zero_shadow_rows and runtime_eligible_pre_shadow_rows > 0:
        contradiction_ledger.append(
            {
                "code": "zero_shadow_with_runtime_eligible_pre_shadow_rows",
                "severity": "high",
                "detail": f"shadow_rows=0 while runtime_eligible_pre_shadow_rows={runtime_eligible_pre_shadow_rows}",
            }
        )
    if off_band_full_candidates:
        contradiction_ledger.append(
            {
                "code": "off_band_full_cannon_opportunities_present",
                "severity": "high",
                "detail": f"off_band_full_cannon_candidate_count={len(off_band_full_candidates)}",
            }
        )
    active_dual_oracle_confirmed = int(
        sum(1 for row in active_band_runtime_rows if bool(row.get("secondary_oracle_confirmation", False)))
    )
    active_desired_quote_present = int(
        sum(1 for row in active_band_truth_rows if row.get("desired_quote_present") is True)
    )
    if active_dual_oracle_confirmed > 0 and active_band_truth_rows and active_desired_quote_present == 0:
        contradiction_ledger.append(
            {
                "code": "active_band_dual_oracle_confirmed_but_no_desired_quote_presence",
                "severity": "high",
                "detail": (
                    f"active_band_dual_oracle_confirmed={active_dual_oracle_confirmed},"
                    f" desired_quote_present={active_desired_quote_present}"
                ),
            }
        )
    runtime_class_name = str(runtime_classification.get("runtime_class_name") or "").strip()
    if runtime_class_name == "NON_PROMOTABLE_NO_PARTICIPATION" and off_band_full_candidates:
        contradiction_ledger.append(
            {
                "code": "non_promotable_no_participation_with_off_band_opportunity",
                "severity": "medium",
                "detail": f"off_band_full_cannon_candidate_count={len(off_band_full_candidates)}",
            }
        )

    ranked_cause_stack: List[Dict[str, Any]] = []
    stage_map = {
        "stage_band_allowed_rows": "stage_band_excluded",
        "prequote_prereq_pass_rows": "prequote_prereq_blocked",
        "truth_reference_insufficient_rows": "truth_reference_insufficient",
        "pre_shadow_non_shadow_rows": "pre_shadow_non_shadow",
        "desired_quote_missing_rows": "desired_quote_missing",
        "selection_rejected_rows": "selection_rejected",
    }
    for stage_name, stage_key in stage_map.items():
        stage = dict(waterfall.get("stages", {}).get(stage_name) or {})
        for reason, count in sorted(
            (stage.get("loss_reason_distribution") or {}).items(),
            key=lambda item: (-int(_safe_float(item[1])), item[0]),
        ):
            if int(_safe_float(count)) <= 0:
                continue
            ranked_cause_stack.append(
                {
                    "stage": stage_key,
                    "reason": str(reason or "unknown"),
                    "count": int(_safe_float(count)),
                }
            )
    ranked_cause_stack.sort(key=lambda item: (-item["count"], item["stage"], item["reason"]))

    timing_band_mismatch = bool(off_band_full_candidates)
    quoteability_starvation = int(
        _safe_float((quote_starvation_summary.get("maker_no_submission_cause_distribution") or {}).get("no_desired_quote"))
    ) > 0
    upstream_starvation = any(
        key in active_band_reason_counts
        for key in (
            "market_reference_not_authoritative",
            "insufficient_depth_multiple",
            "fair_probability",
            "market_probability",
            "probe_visible_depth_shares",
        )
    ) or int(waterfall.get("terminal_path_counts", {}).get("prequote_prereq_blocked", 0)) > 0
    intended_selector_strictness = (
        int(waterfall.get("stages", {}).get("selection_rejected_rows", {}).get("count", 0)) > 0
        and not quoteability_starvation
        and not timing_band_mismatch
        and not upstream_starvation
    )
    classification_factors: List[str] = []
    if intended_selector_strictness:
        classification_factors.append("intended_selector_strictness")
    if upstream_starvation:
        classification_factors.append("upstream_starvation")
    if timing_band_mismatch:
        classification_factors.append("timing_band_mismatch")
    if quoteability_starvation:
        classification_factors.append("quoteability_starvation")
    if len(classification_factors) > 1:
        zero_submit_classification = "mixed-cause starvation"
    elif classification_factors:
        zero_submit_classification = classification_factors[0]
    else:
        zero_submit_classification = "unknown"

    measurement_gaps: List[str] = []
    for row in quote_starvation_rows:
        if not str(row.get("stage_bucket") or row.get("raw_stage") or "").strip():
            measurement_gaps.append("missing_raw_stage")
        if not str(row.get("market_reference_mode") or "").strip():
            measurement_gaps.append("missing_market_reference_mode")
        if not str(row.get("market_reference_source_side") or "").strip():
            measurement_gaps.append("missing_market_reference_source_side")
    unmatched_shadow_rejections = int(
        max(
            0,
            shadow_selection_rejected_row_count
            - int(waterfall.get("stages", {}).get("selection_rejected_rows", {}).get("count", 0)),
        )
    )
    if unmatched_shadow_rejections > int(len(off_band_full_candidates)):
        measurement_gaps.append("unmatched_shadow_selection_rejections")
    normalized_measurement_gaps = sorted(set(measurement_gaps))
    decision_readiness = (
        "measurement_incomplete" if normalized_measurement_gaps else "ready_for_truth_packet"
    )

    active_band_summary = {
        "row_count": int(len(active_band_runtime_rows)),
        "dual_oracle_confirmed_count": active_dual_oracle_confirmed,
        "authoritative_reference_count": int(
            sum(
                1
                for row in active_band_runtime_rows
                if str(row.get("market_reference_class") or "").strip().lower() == "authoritative"
            )
        ),
        "depth_met_count": int(
            sum(1 for row in active_band_runtime_rows if row.get("cannon_depth_requirement_met") is True)
        ),
        "desired_quote_present_count": active_desired_quote_present,
        "full_cannon_candidate_count": int(
            sum(1 for row in active_band_runtime_rows if bool(row.get("full_cannon_candidate", False)))
        ),
        "reject_reason_distribution": _counter_to_sorted_int_dict(active_band_reason_counts),
    }
    off_band_examples = [
        {
            "target_side_ref": str(row.get("target_side_ref") or ""),
            "stage": str(row.get("stage") or ""),
            "sec_to_expiry": row.get("sec_to_expiry"),
            "market_reference_class": row.get("market_reference_class"),
            "secondary_oracle_status": row.get("secondary_oracle_status"),
            "depth_multiple_vs_cannon_target": row.get("depth_multiple_vs_cannon_target"),
        }
        for row in off_band_full_candidates[:5]
    ]
    known_truths = {
        "packet_b_350": {
            "quote_starvation_row_count": 10 if runtime_eligible_pre_shadow_rows == 10 and shadow_selection_rejected_row_count == 0 else None,
            "shadow_row_count": 0 if shadow_selection_rejected_row_count == 0 and zero_shadow_rows else None,
        },
        "caliber_250": {
            "quote_starvation_row_count": 20 if runtime_eligible_pre_shadow_rows == 20 else None,
            "shadow_selection_rejected_row_count": (
                shadow_selection_rejected_row_count if shadow_selection_rejected_row_count > 0 else None
            ),
            "off_band_full_cannon_candidate_count": int(len(off_band_full_candidates)),
        },
        "row_universe_caveats": normalized_measurement_gaps,
    }
    return {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "runtime_classification": runtime_class_name or "UNKNOWN",
        "zero_submit_classification": zero_submit_classification,
        "classification_factors": classification_factors,
        "decision_readiness": decision_readiness,
        "measurement_gaps": normalized_measurement_gaps,
        "known_truths": known_truths,
        "ranked_cause_stack": ranked_cause_stack,
        "contradiction_ledger": contradiction_ledger,
        "active_band_runtime_summary": active_band_summary,
        "shadow_row_count": int(len(shadow_rows)),
        "shadow_selection_rejected_row_count": shadow_selection_rejected_row_count,
        "probe_matched_selection_rejected_row_count": int(
            waterfall.get("stages", {}).get("selection_rejected_rows", {}).get("count", 0)
        ),
        "unmatched_selection_rejected_shadow_row_count": unmatched_shadow_rejections,
        "off_band_full_cannon_candidate_count": int(len(off_band_full_candidates)),
        "off_band_full_cannon_candidate_examples": off_band_examples,
    }


def _taker_edge_bucket(edge_abs: Any) -> str:
    value = _safe_float(edge_abs, default=-1.0)
    if value < 0.0:
        return "unknown"
    if value <= 0.10:
        return "le_0p10"
    if value <= 0.30:
        return "0p10_0p30"
    if value <= 0.60:
        return "0p30_0p60"
    return "gt_0p60"


def _conviction_bucket(conviction_score: Any) -> str:
    value = _safe_float(conviction_score, default=-1.0)
    if value < 0.0:
        return "unknown"
    if value <= 0.33:
        return "le_0p33"
    if value <= 0.66:
        return "0p33_0p66"
    return "gt_0p66"


def _taker_opportunity_suppression_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_eval_count = 0.0
    lane_eval_count = Counter()
    lane_action_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_stage_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_block_reason_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_reject_reason_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_suppression_class_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_book_source_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_latency_state_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_edge_bucket_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_submit_candidate_count = Counter()
    lane_taker_action_count = Counter()
    lane_non_stage_eval_count = Counter()
    lane_authority_class_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_normal_taker_allowed_count: Dict[str, Counter[str]] = {"normal": Counter(), "recovery": Counter()}
    lane_reduce_only_recovery_allowed_count: Dict[str, Counter[str]] = {
        "normal": Counter(),
        "recovery": Counter(),
    }
    lane_preexpiry_emergency_taker_allowed_count: Dict[str, Counter[str]] = {
        "normal": Counter(),
        "recovery": Counter(),
    }

    def _norm(value: Any, default: str = "unknown") -> str:
        text = str(value or "").strip().lower()
        return text or default

    def _bool_label(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return _norm(value)

    def _stage(value: Any) -> str:
        text = str(value or "").strip().upper()
        return text or "UNKNOWN"

    def _edge_abs_from_eval(evt: Dict[str, Any]) -> float:
        explicit = _safe_float(evt.get("edge_abs"), default=-1.0)
        if explicit >= 0.0:
            return explicit
        fair = _safe_float(evt.get("fair_probability"), default=-1.0)
        market = _safe_float(evt.get("market_probability"), default=-1.0)
        if fair >= 0.0 and market >= 0.0:
            return abs(fair - market)
        return -1.0

    def _classify(block_reason: str, reject_reason: str, action_taken: str) -> str:
        if action_taken == "taker":
            return "submitted"
        if not block_reason:
            return "unknown_no_action"
        if block_reason in {"normal_taker_authority_closed", "stage_disallow_taker"}:
            return "late_window_authority_gate"
        if block_reason in {"taker_outside_final_window", "maker_timing_gate_closed"}:
            return "timing_window"
        if block_reason == "edge_below_min":
            return "edge_filter"
        if block_reason in {"fair_probability_missing", "market_probability_missing"}:
            return "truth_missing"
        if block_reason in {"latency_not_armed", "taker_requires_ws_book_source", "token_score_below_taker_min"}:
            return "source_or_quality_gate"
        if block_reason == "taker_token_cooldown":
            return "cooldown"
        if block_reason.startswith("reduce_only_recovery"):
            return "recovery_policy"
        if block_reason == "taker_submit_rejected":
            if reject_reason in {
                "risk_reject_new_exposure_expiry_gate_blocked",
                "terminal_unwind_halt_new_risk_blocked",
            }:
                return "risk_terminal_or_expiry_gate"
            if reject_reason in {
                "risk_reject_size_too_small",
                "size_too_small",
                "risk_reject_notional_cap",
                "notional_cap",
                "global_exposure_cap",
                "size_notional_bounds",
            }:
                return "risk_size_or_exposure"
            return "risk_reject"
        return "other"

    for evt in events:
        if str(evt.get("event_type") or "").strip() != "edge_evaluation":
            continue
        if str(evt.get("evaluation_scope") or "").strip().lower() != "taker":
            continue
        total_eval_count += 1.0
        recovery_active = _as_bool(evt.get("reduce_only_recovery_active")) is True
        lane = "recovery" if recovery_active else "normal"
        lane_eval_count[lane] += 1
        action_taken = _norm(evt.get("action_taken"), default="unknown")
        block_reason = _norm(evt.get("block_reason"), default="")
        reject_reason = _norm(evt.get("taker_submit_reject_reason"), default="")
        stage = _stage(evt.get("stage"))
        lane_action_count[lane][action_taken] += 1
        lane_stage_count[lane][stage] += 1
        if block_reason:
            lane_block_reason_count[lane][block_reason] += 1
        if reject_reason:
            lane_reject_reason_count[lane][reject_reason] += 1
        suppression_class = _classify(block_reason, reject_reason, action_taken)
        lane_suppression_class_count[lane][suppression_class] += 1
        lane_authority_class_count[lane][_norm(evt.get("late_window_authority_class"))] += 1
        lane_normal_taker_allowed_count[lane][_bool_label(evt.get("normal_taker_allowed"))] += 1
        lane_reduce_only_recovery_allowed_count[lane][_bool_label(evt.get("reduce_only_recovery_allowed"))] += 1
        lane_preexpiry_emergency_taker_allowed_count[lane][
            _bool_label(evt.get("preexpiry_emergency_taker_allowed"))
        ] += 1
        lane_book_source_count[lane][_norm(evt.get("book_source"))] += 1
        lane_latency_state_count[lane][_norm(evt.get("latency_state"))] += 1
        lane_edge_bucket_count[lane][_taker_edge_bucket(_edge_abs_from_eval(evt))] += 1
        if block_reason not in {"normal_taker_authority_closed", "stage_disallow_taker"}:
            lane_non_stage_eval_count[lane] += 1
        if action_taken == "taker":
            lane_taker_action_count[lane] += 1
            lane_submit_candidate_count[lane] += 1
        elif block_reason == "taker_submit_rejected":
            lane_submit_candidate_count[lane] += 1

    def _counter(counter: Counter[str]) -> Dict[str, int]:
        return dict(sorted(counter.items(), key=lambda item: item[0]))

    def _lane_payload(lane: str) -> Dict[str, Any]:
        eval_count = float(lane_eval_count.get(lane, 0))
        non_stage_count = float(lane_non_stage_eval_count.get(lane, 0))
        submit_candidate_count = float(lane_submit_candidate_count.get(lane, 0))
        taker_action_count = float(lane_taker_action_count.get(lane, 0))
        return {
            "edge_eval_count": eval_count,
            "taker_enabled_stage_eval_count": non_stage_count,
            "taker_enabled_stage_eval_ratio": float(non_stage_count / eval_count) if eval_count > 0.0 else 0.0,
            "submit_candidate_count": submit_candidate_count,
            "action_taken_taker_count": taker_action_count,
            "submit_candidate_to_action_rate": (
                float(taker_action_count / submit_candidate_count)
                if submit_candidate_count > 0.0
                else 0.0
            ),
            "action_taken_distribution": _counter(lane_action_count[lane]),
            "stage_distribution": _counter(lane_stage_count[lane]),
            "block_reason_distribution": _counter(lane_block_reason_count[lane]),
            "submit_reject_reason_distribution": _counter(lane_reject_reason_count[lane]),
            "suppression_class_distribution": _counter(lane_suppression_class_count[lane]),
            "late_window_authority_class_distribution": _counter(lane_authority_class_count[lane]),
            "normal_taker_allowed_distribution": _counter(lane_normal_taker_allowed_count[lane]),
            "reduce_only_recovery_allowed_distribution": _counter(
                lane_reduce_only_recovery_allowed_count[lane]
            ),
            "preexpiry_emergency_taker_allowed_distribution": _counter(
                lane_preexpiry_emergency_taker_allowed_count[lane]
            ),
            "book_source_distribution": _counter(lane_book_source_count[lane]),
            "latency_state_distribution": _counter(lane_latency_state_count[lane]),
            "edge_bucket_distribution": _counter(lane_edge_bucket_count[lane]),
        }

    return {
        "claim_boundary": (
            "report_only_edge_evaluation_attribution; edge buckets are computed from event edge_abs "
            "when present, otherwise abs(fair_probability-market_probability)"
        ),
        "total_taker_edge_eval_count": float(total_eval_count),
        "normal": _lane_payload("normal"),
        "recovery": _lane_payload("recovery"),
    }


def _taker_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    decision_count = 0.0
    submit_capable_static_decision_count = 0.0
    submit_capable_dynamic_predicted_count = 0.0
    submit_capable_dynamic_predicted_unknown_count = 0.0
    submit_capable_decision_count = 0.0
    blocked_decision_count = 0.0
    actual_submit_count = 0.0
    fill_count = 0.0
    decision_timing_window = Counter()
    decision_edge_bucket = Counter()
    decision_conviction_bucket = Counter()
    decision_block_reason = Counter()
    decision_aggressiveness = Counter()
    decision_multi_oracle_status = Counter()
    decision_normal_taker_side_class = Counter()
    decision_normal_side_policy = Counter()
    edge_eval_submit_reject_reason = Counter()
    submit_edge_bucket = Counter()
    submit_conviction_bucket = Counter()
    submit_timing_window = Counter()
    submit_multi_oracle_status = Counter()
    submit_normal_taker_side_class = Counter()
    submit_normal_side_policy = Counter()
    submit_class_distribution = Counter()
    submit_recovery_override_edge_bucket = Counter()
    submit_recovery_override_stage_distribution = Counter()
    submit_recovery_override_reason_distribution = Counter()
    submit_true_unknown_stage_distribution = Counter()
    submit_decision_to_submit_latency_ms: List[float] = []
    submit_decision_to_submit_latency_ms_normal: List[float] = []
    fill_edge_bucket = Counter()
    fill_stage_distribution = Counter()
    fill_class_distribution = Counter()
    lag_class_distribution = Counter()
    aggressiveness_application_counts = Counter()
    order_edge_bucket_by_id: Dict[str, str] = {}
    order_is_taker_by_id: Dict[str, bool] = {}
    order_stage_by_id: Dict[str, str] = {}
    order_submit_class_by_id: Dict[str, str] = {}
    submit_stage_distribution = Counter()
    hard_min_unachievable_count = 0.0
    dynamic_size_capped_by_risk_count = 0.0
    multi_oracle_available_count = 0.0
    multi_oracle_confirmation_count = 0.0
    multi_oracle_boost_eligible_count = 0.0
    multi_oracle_boost_applied_count = 0.0
    normal_taker_same_token_sell_blocked_count = 0.0
    complement_token_mapping_failure_count = 0.0
    outside_window_blocked_count_edge_eval = 0.0
    submit_without_competitiveness_payload_count = 0.0
    submit_unknown_stage_count = 0.0
    fill_without_submit_stage_count = 0.0
    risk_reject_after_capable_count = 0.0
    decision_predicted_reject_reason = Counter()
    normal_competitiveness_submit_count = 0.0
    recovery_override_submit_count = 0.0
    true_unknown_submit_count = 0.0
    partial_competitiveness_payload_count = 0.0
    recovery_override_without_normal_payload_count = 0.0

    stage_funnel: Dict[str, Dict[str, float]] = {}
    stage_reduction_causes: Dict[str, Counter[str]] = {}
    stage_reduction_primary_causes: Dict[str, Counter[str]] = {}
    stage_final_risk_reject_reasons: Dict[str, Counter[str]] = {}
    stage_last_submit_ts: Dict[str, dt.datetime] = {}
    stage_submit_inter_submit_deltas: Dict[str, List[float]] = {}

    def _normalize_stage(stage_value: Any) -> str:
        stage = str(stage_value or "").strip().upper()
        return stage or "UNKNOWN"

    def _stage_row(stage_name: str) -> Dict[str, float]:
        row = stage_funnel.get(stage_name)
        if isinstance(row, dict):
            return row
        row = {
            "decision_count": 0.0,
            "submit_capable_static_count": 0.0,
            "submit_capable_dynamic_predicted_count": 0.0,
            "submit_capable_dynamic_predicted_unknown_count": 0.0,
            "submit_capable_decision_count": 0.0,
            "blocked_decision_count": 0.0,
            "actual_submit_count": 0.0,
            "fill_count": 0.0,
            "reduction_due_to_dynamic_preview": 0.0,
            "reduction_due_to_timing_gate": 0.0,
            "reduction_due_to_cooldown": 0.0,
            "reduction_due_to_final_risk_reject": 0.0,
            "risk_reject_after_capable_count": 0.0,
            "multi_oracle_available_count": 0.0,
            "multi_oracle_confirmation_count": 0.0,
            "multi_oracle_boost_eligible_count": 0.0,
            "multi_oracle_boost_applied_count": 0.0,
        }
        stage_funnel[stage_name] = row
        return row

    def _stage_reduction_counter(stage_name: str) -> Counter[str]:
        counter = stage_reduction_causes.get(stage_name)
        if isinstance(counter, Counter):
            return counter
        counter = Counter()
        stage_reduction_causes[stage_name] = counter
        return counter

    def _stage_reject_counter(stage_name: str) -> Counter[str]:
        counter = stage_final_risk_reject_reasons.get(stage_name)
        if isinstance(counter, Counter):
            return counter
        counter = Counter()
        stage_final_risk_reject_reasons[stage_name] = counter
        return counter

    def _stage_primary_reduction_counter(stage_name: str) -> Counter[str]:
        counter = stage_reduction_primary_causes.get(stage_name)
        if isinstance(counter, Counter):
            return counter
        counter = Counter()
        stage_reduction_primary_causes[stage_name] = counter
        return counter

    def _is_multi_oracle_available(status_value: Any) -> bool:
        status = str(status_value or "").strip().lower()
        if not status:
            return False
        if status in {"disabled", "unknown", "error", "failed"}:
            return False
        if status.startswith("unavailable"):
            return False
        return True

    def _payload_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _is_recovery_override_submit(evt: Dict[str, Any], comp: Dict[str, Any]) -> bool:
        candidate_payloads = [
            comp,
            _payload_dict(evt.get("size_resolution")).get("taker_competitiveness"),
            evt.get("risk_decision_basis"),
            evt,
        ]
        for payload in candidate_payloads:
            if not isinstance(payload, dict):
                continue
            if _as_bool(payload.get("reduce_only_recovery_active")) is True:
                return True
            if _as_bool(payload.get("preexpiry_reduce_only_active")) is True:
                return True
            reason = str(payload.get("reduce_only_recovery_reason") or "").strip().lower()
            if reason:
                return True
        return False

    def _has_normal_competitiveness_payload(comp: Dict[str, Any]) -> bool:
        return any(
            key in comp
            for key in (
                "conviction_score",
                "timing_window_class",
                "multi_oracle_status",
                "submit_capable_static",
                "submit_capable_dynamic_predicted",
            )
        )

    def _latency_summary(values: List[float]) -> Dict[str, float]:
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
            "median_ms": _percentile(cleaned, 0.5),
            "p90_ms": _percentile(cleaned, 0.9),
            "p95_ms": _percentile(cleaned, 0.95),
            "max_ms": float(max(cleaned)),
        }

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()

        if event_type == "edge_evaluation":
            if str(evt.get("evaluation_scope") or "").strip().lower() != "taker":
                continue
            stage = _normalize_stage(evt.get("stage"))
            stage_row = _stage_row(stage)
            stage_reductions = _stage_reduction_counter(stage)
            block_reason = str(evt.get("block_reason") or "").strip().lower()
            if (
                str(evt.get("action_taken") or "").strip().lower() == "none"
                and block_reason == "taker_outside_final_window"
            ):
                outside_window_blocked_count_edge_eval += 1.0
            if (
                str(evt.get("action_taken") or "").strip().lower() == "none"
                and block_reason == "taker_token_cooldown"
            ):
                stage_row["reduction_due_to_cooldown"] += 1.0
                stage_reductions["reduction_due_to_cooldown"] += 1
            if (
                str(evt.get("action_taken") or "").strip().lower() == "none"
                and block_reason == "taker_submit_rejected"
            ):
                stage_row["reduction_due_to_final_risk_reject"] += 1.0
                stage_row["risk_reject_after_capable_count"] += 1.0
                stage_reductions["reduction_due_to_final_risk_reject"] += 1
                risk_reject_after_capable_count += 1.0
                reject_reason = str(evt.get("taker_submit_reject_reason") or "unknown").strip().lower() or "unknown"
                _stage_reject_counter(stage)[reject_reason] += 1
                edge_eval_submit_reject_reason[
                    reject_reason
                ] += 1
                _stage_primary_reduction_counter(stage)["reduction_due_to_final_risk_reject"] += 1
            continue

        if is_taker_decision_event_type(event_type):
            stage = _normalize_stage(evt.get("stage"))
            stage_row = _stage_row(stage)
            stage_reductions = _stage_reduction_counter(stage)
            decision_count += 1.0
            stage_row["decision_count"] += 1.0
            timing_window = str(evt.get("timing_window_class") or "unknown").strip().lower() or "unknown"
            decision_timing_window[timing_window] += 1
            edge_bucket = _taker_edge_bucket(evt.get("edge_abs"))
            conviction_bucket = _conviction_bucket(evt.get("conviction_score"))
            decision_edge_bucket[edge_bucket] += 1
            decision_conviction_bucket[conviction_bucket] += 1

            submit_capable_static = bool(evt.get("submit_capable_static", evt.get("should_submit", False)))
            if submit_capable_static:
                submit_capable_static_decision_count += 1.0
                stage_row["submit_capable_static_count"] += 1.0

            submit_capable_dynamic_predicted = _as_bool(evt.get("submit_capable_dynamic_predicted"))
            if submit_capable_dynamic_predicted is True:
                submit_capable_dynamic_predicted_count += 1.0
                stage_row["submit_capable_dynamic_predicted_count"] += 1.0
            elif submit_capable_dynamic_predicted is None:
                submit_capable_dynamic_predicted_unknown_count += 1.0
                stage_row["submit_capable_dynamic_predicted_unknown_count"] += 1.0

            if submit_capable_static and submit_capable_dynamic_predicted is False:
                stage_row["reduction_due_to_dynamic_preview"] += 1.0
                stage_reductions["reduction_due_to_dynamic_preview"] += 1
                predicted_reason = str(evt.get("predicted_reject_reason") or "").strip().lower()
                if predicted_reason:
                    decision_predicted_reject_reason[predicted_reason] += 1

            should_submit = bool(evt.get("should_submit", False))
            if should_submit:
                submit_capable_decision_count += 1.0
                stage_row["submit_capable_decision_count"] += 1.0
            else:
                blocked_decision_count += 1.0
                stage_row["blocked_decision_count"] += 1.0
            block_reason = str(evt.get("block_reason") or "").strip().lower()
            if block_reason:
                decision_block_reason[block_reason] += 1
            if block_reason == "taker_outside_final_window":
                stage_row["reduction_due_to_timing_gate"] += 1.0
                stage_reductions["reduction_due_to_timing_gate"] += 1
                _stage_primary_reduction_counter(stage)["reduction_due_to_timing_gate"] += 1
            elif block_reason == "taker_token_cooldown":
                _stage_primary_reduction_counter(stage)["reduction_due_to_cooldown"] += 1
            elif block_reason == "taker_hard_min_notional_unachievable":
                _stage_primary_reduction_counter(stage)["reduction_due_to_hard_min_unachievable"] += 1
            elif block_reason == "taker_order_budget_exhausted":
                _stage_primary_reduction_counter(stage)["reduction_due_to_order_budget"] += 1
            elif (not should_submit) and block_reason:
                _stage_primary_reduction_counter(stage)["reduction_due_to_other_block"] += 1
            aggressiveness_level = str(evt.get("aggressiveness_level") or "unknown").strip().lower() or "unknown"
            decision_aggressiveness[aggressiveness_level] += 1
            multi_oracle_status = str(evt.get("multi_oracle_status") or "unknown").strip().lower() or "unknown"
            decision_multi_oracle_status[multi_oracle_status] += 1
            normal_side_class = str(evt.get("normal_taker_side_class") or "unknown").strip().lower() or "unknown"
            decision_normal_taker_side_class[normal_side_class] += 1
            normal_side_policy = str(evt.get("normal_side_policy") or "unknown").strip().lower() or "unknown"
            decision_normal_side_policy[normal_side_policy] += 1
            if normal_side_class == "same_token_sell_blocked":
                normal_taker_same_token_sell_blocked_count += 1.0
            if block_reason == "complement_token_mapping_unavailable":
                complement_token_mapping_failure_count += 1.0
            if _is_multi_oracle_available(multi_oracle_status):
                multi_oracle_available_count += 1.0
                stage_row["multi_oracle_available_count"] += 1.0
            if bool(evt.get("hard_min_unachievable", False)):
                hard_min_unachievable_count += 1.0
            if bool(evt.get("dynamic_size_capped_by_risk", False)):
                dynamic_size_capped_by_risk_count += 1.0
            if bool(evt.get("multi_oracle_confirmation", False)):
                multi_oracle_confirmation_count += 1.0
                stage_row["multi_oracle_confirmation_count"] += 1.0
            if bool(evt.get("multi_oracle_boost_eligible", False)):
                multi_oracle_boost_eligible_count += 1.0
                stage_row["multi_oracle_boost_eligible_count"] += 1.0
            if bool(evt.get("multi_oracle_boost_applied", False)):
                multi_oracle_boost_applied_count += 1.0
                stage_row["multi_oracle_boost_applied_count"] += 1.0
            continue

        if event_type == "order_submit":
            reason = str(evt.get("reason") or "").strip().lower()
            is_taker_order = _is_taker_submit_reason(reason)
            order_id = str(evt.get("order_id") or "").strip()
            if order_id:
                order_is_taker_by_id[order_id] = is_taker_order
            if not is_taker_order:
                continue
            actual_submit_count += 1.0
            comp = evt.get("taker_competitiveness")
            comp_dict = comp if isinstance(comp, dict) else {}
            recovery_override = _is_recovery_override_submit(evt, comp_dict)
            has_normal_payload = bool(comp_dict) and _has_normal_competitiveness_payload(comp_dict)
            if recovery_override:
                submit_class = "reduce_only_recovery_override"
                recovery_override_submit_count += 1.0
                if not has_normal_payload:
                    recovery_override_without_normal_payload_count += 1.0
            elif isinstance(comp, dict) and has_normal_payload:
                submit_class = "normal_competitiveness"
                normal_competitiveness_submit_count += 1.0
            else:
                submit_class = "true_unknown"
                true_unknown_submit_count += 1.0
                if isinstance(comp, dict):
                    partial_competitiveness_payload_count += 1.0
                else:
                    submit_without_competitiveness_payload_count += 1.0
            submit_class_distribution[submit_class] += 1
            if order_id:
                order_submit_class_by_id[order_id] = submit_class

            stage = _normalize_stage(comp_dict.get("stage") or evt.get("stage"))
            if stage == "UNKNOWN":
                submit_unknown_stage_count += 1.0
            stage_row = _stage_row(stage)
            stage_row["actual_submit_count"] += 1.0
            submit_stage_distribution[stage] += 1
            if order_id:
                order_stage_by_id[order_id] = stage
            edge_bucket = _taker_edge_bucket(comp_dict.get("edge_abs"))
            if order_id:
                order_edge_bucket_by_id[order_id] = edge_bucket

            submit_latency_ms = _safe_float(evt.get("decision_to_submit_latency_ms"), default=-1.0)
            if submit_latency_ms >= 0.0:
                submit_decision_to_submit_latency_ms.append(float(submit_latency_ms))
                if submit_class == "normal_competitiveness":
                    submit_decision_to_submit_latency_ms_normal.append(float(submit_latency_ms))

            if submit_class == "reduce_only_recovery_override":
                submit_recovery_override_edge_bucket[edge_bucket] += 1
                submit_recovery_override_stage_distribution[stage] += 1
                reason_value = str(comp_dict.get("reduce_only_recovery_reason") or "unknown").strip().lower() or "unknown"
                submit_recovery_override_reason_distribution[reason_value] += 1
                continue

            if submit_class == "true_unknown":
                submit_true_unknown_stage_distribution[stage] += 1
                continue

            conviction_bucket = _conviction_bucket(comp_dict.get("conviction_score"))
            timing_window = str(comp_dict.get("timing_window_class") or "unknown").strip().lower() or "unknown"
            submit_edge_bucket[edge_bucket] += 1
            submit_conviction_bucket[conviction_bucket] += 1
            submit_timing_window[timing_window] += 1
            submit_normal_taker_side_class[
                str(comp_dict.get("normal_taker_side_class") or "unknown").strip().lower() or "unknown"
            ] += 1
            submit_normal_side_policy[
                str(comp_dict.get("normal_side_policy") or "unknown").strip().lower() or "unknown"
            ] += 1
            submit_multi_oracle_status[
                str(comp_dict.get("multi_oracle_status") or "unknown").strip().lower() or "unknown"
            ] += 1
            ts_submit = parse_ts(evt.get("ts_utc") or evt.get("timestamp_utc"))
            if ts_submit is not None:
                prev_submit_ts = stage_last_submit_ts.get(stage)
                if prev_submit_ts is not None:
                    delta_sec = float((ts_submit - prev_submit_ts).total_seconds())
                    if delta_sec >= 0.0:
                        stage_submit_inter_submit_deltas.setdefault(stage, []).append(delta_sec)
                stage_last_submit_ts[stage] = ts_submit
            if _safe_float(comp_dict.get("price_aggress_bps_applied"), 0.0) > 0.0:
                aggressiveness_application_counts["price_aggressed"] += 1
            if bool(comp_dict.get("hard_min_floor_applied", False)):
                aggressiveness_application_counts["hard_min_floor_applied"] += 1
            if bool(comp_dict.get("dynamic_size_capped_by_risk", False)):
                aggressiveness_application_counts["dynamic_size_capped_by_risk"] += 1
            if bool(comp_dict.get("multi_oracle_confirmation", False)):
                aggressiveness_application_counts["multi_oracle_confirmation"] += 1
            if bool(comp_dict.get("multi_oracle_boost_applied", False)):
                aggressiveness_application_counts["multi_oracle_boost_applied"] += 1
            continue

        if event_type != "fill":
            continue
        order_id = str(evt.get("order_id") or "").strip()
        if not order_id or not bool(order_is_taker_by_id.get(order_id)):
            continue
        fill_count += 1.0
        edge_bucket = order_edge_bucket_by_id.get(order_id, "unknown")
        fill_edge_bucket[edge_bucket] += 1
        stage = _normalize_stage(order_stage_by_id.get(order_id))
        if stage == "UNKNOWN":
            fill_without_submit_stage_count += 1.0
        stage_row = _stage_row(stage)
        stage_row["fill_count"] += 1.0
        fill_stage_distribution[stage] += 1
        fill_class_distribution[order_submit_class_by_id.get(order_id, "true_unknown")] += 1
        lag_class = str(evt.get("paper_chainlink_lag_class") or "unknown").strip().lower() or "unknown"
        lag_class_distribution[lag_class] += 1

    decision_to_submit_rate = (actual_submit_count / decision_count) if decision_count > 0.0 else 0.0
    submit_capable_to_submit_rate = (
        actual_submit_count / submit_capable_decision_count
        if submit_capable_decision_count > 0.0
        else 0.0
    )
    submit_capable_dynamic_to_submit_rate = (
        actual_submit_count / submit_capable_dynamic_predicted_count
        if submit_capable_dynamic_predicted_count > 0.0
        else 0.0
    )
    fill_rate_from_submits = (fill_count / actual_submit_count) if actual_submit_count > 0.0 else 0.0

    stage_hidden_blockage_detector: Dict[str, Dict[str, Any]] = {}
    stage_funnel_sorted: Dict[str, Dict[str, float]] = {}
    stage_reduction_sorted: Dict[str, Dict[str, int]] = {}
    stage_primary_reduction_sorted: Dict[str, Dict[str, int]] = {}
    stage_risk_reject_reasons_sorted: Dict[str, Dict[str, int]] = {}
    stage_reduction_delta_accounting: Dict[str, Dict[str, Any]] = {}
    stage_inter_submit_delta_summary: Dict[str, Dict[str, float]] = {}
    for stage in sorted(stage_funnel.keys()):
        stage_row = stage_funnel.get(stage, {})
        static_count = float(stage_row.get("submit_capable_static_count", 0.0))
        dynamic_count = float(stage_row.get("submit_capable_dynamic_predicted_count", 0.0))
        submit_count = float(stage_row.get("actual_submit_count", 0.0))
        fills = float(stage_row.get("fill_count", 0.0))
        decision_count_stage = float(stage_row.get("decision_count", 0.0))
        decision_to_submit_delta_stage = float(max(0.0, decision_count_stage - submit_count))

        stage_hidden_blockage_detector[stage] = {
            "decision_to_dynamic_predicted_delta": float(
                max(0.0, decision_count_stage - dynamic_count)
            ),
            "dynamic_predicted_to_submit_delta": float(max(0.0, dynamic_count - submit_count)),
            "submit_to_fill_delta": float(max(0.0, submit_count - fills)),
            "reduction_reason_counters": dict(
                sorted(_stage_reduction_counter(stage).items(), key=lambda item: item[0])
            ),
        }
        stage_funnel_sorted[stage] = {
            **{key: float(value) for key, value in stage_row.items()},
            "submit_capable_static_to_submit_rate": (
                float(submit_count / static_count) if static_count > 0.0 else 0.0
            ),
            "submit_capable_dynamic_to_submit_rate": (
                float(submit_count / dynamic_count) if dynamic_count > 0.0 else 0.0
            ),
            "fill_rate_from_submits": (float(fills / submit_count) if submit_count > 0.0 else 0.0),
        }
        stage_reduction_sorted[stage] = dict(sorted(_stage_reduction_counter(stage).items(), key=lambda item: item[0]))
        stage_primary_reduction_sorted[stage] = dict(
            sorted(_stage_primary_reduction_counter(stage).items(), key=lambda item: item[0])
        )
        stage_risk_reject_reasons_sorted[stage] = dict(
            sorted(_stage_reject_counter(stage).items(), key=lambda item: item[0])
        )
        primary_total = int(sum(stage_primary_reduction_sorted[stage].values()))
        primary_delta_difference = float(primary_total) - decision_to_submit_delta_stage
        primary_total_matches_delta = abs(primary_delta_difference) <= 1e-9
        primary_overlap_possible = primary_delta_difference > 1e-9
        stage_reduction_delta_accounting[stage] = {
            "decision_to_submit_delta": float(decision_to_submit_delta_stage),
            "primary_reduction_cause_total": float(primary_total),
            "primary_reduction_cause_total_matches_delta": bool(primary_total_matches_delta),
            "primary_reduction_cause_total_delta_difference": float(primary_delta_difference),
            "primary_reduction_cause_total_exceeds_delta": bool(primary_overlap_possible),
            "primary_reduction_cause_overlap_possible": bool(primary_overlap_possible),
            "primary_reduction_cause_accounting_note": (
                "primary counters include event-row reductions and can exceed net decision-to-submit delta"
                if primary_overlap_possible
                else "primary counters match or undercount net decision-to-submit delta"
            ),
            "primary_reduction_cause_counters": stage_primary_reduction_sorted[stage],
        }

    def _summarize_deltas(values: List[float]) -> Dict[str, float]:
        if not values:
            return {"count": 0.0, "p50_sec": 0.0, "p90_sec": 0.0, "min_sec": 0.0, "max_sec": 0.0}
        ordered = sorted(float(v) for v in values if isinstance(v, (int, float)))
        if not ordered:
            return {"count": 0.0, "p50_sec": 0.0, "p90_sec": 0.0, "min_sec": 0.0, "max_sec": 0.0}

        def _pct(points: List[float], ratio: float) -> float:
            if not points:
                return 0.0
            idx = int(round((len(points) - 1) * ratio))
            idx = max(0, min(len(points) - 1, idx))
            return float(points[idx])

        return {
            "count": float(len(ordered)),
            "p50_sec": float(_pct(ordered, 0.50)),
            "p90_sec": float(_pct(ordered, 0.90)),
            "min_sec": float(ordered[0]),
            "max_sec": float(ordered[-1]),
        }

    for stage, deltas in sorted(stage_submit_inter_submit_deltas.items(), key=lambda item: item[0]):
        stage_inter_submit_delta_summary[stage] = _summarize_deltas(deltas)

    hidden_blockage_detector = {
        "decision_to_dynamic_predicted_delta": float(max(0.0, decision_count - submit_capable_dynamic_predicted_count)),
        "dynamic_predicted_to_submit_delta": float(
            max(0.0, submit_capable_dynamic_predicted_count - actual_submit_count)
        ),
        "submit_to_fill_delta": float(max(0.0, actual_submit_count - fill_count)),
        "reduction_reason_counters": {
            "reduction_due_to_dynamic_preview": int(
                sum(counter.get("reduction_due_to_dynamic_preview", 0) for counter in stage_reduction_causes.values())
            ),
            "reduction_due_to_timing_gate": int(
                sum(counter.get("reduction_due_to_timing_gate", 0) for counter in stage_reduction_causes.values())
            ),
            "reduction_due_to_cooldown": int(
                sum(counter.get("reduction_due_to_cooldown", 0) for counter in stage_reduction_causes.values())
            ),
            "reduction_due_to_final_risk_reject": int(
                sum(counter.get("reduction_due_to_final_risk_reject", 0) for counter in stage_reduction_causes.values())
            ),
        },
    }
    stage_first_claim_guard = {
        "stage_evidence_required_before_aggregate_claim": True,
        "stage_reduction_delta_accounting": stage_reduction_delta_accounting,
    }

    return {
        "decision_count": float(decision_count),
        "submit_capable_static_decision_count": float(submit_capable_static_decision_count),
        "submit_capable_dynamic_predicted_count": float(submit_capable_dynamic_predicted_count),
        "submit_capable_dynamic_predicted_unknown_count": float(submit_capable_dynamic_predicted_unknown_count),
        "submit_capable_decision_count": float(submit_capable_decision_count),
        "blocked_decision_count": float(blocked_decision_count),
        "actual_submit_count": float(actual_submit_count),
        "fill_count": float(fill_count),
        "normal_competitiveness_submit_count": float(normal_competitiveness_submit_count),
        "recovery_override_submit_count": float(recovery_override_submit_count),
        "true_unknown_submit_count": float(true_unknown_submit_count),
        "partial_competitiveness_payload_count": float(partial_competitiveness_payload_count),
        "recovery_override_without_normal_payload_count": float(
            recovery_override_without_normal_payload_count
        ),
        "decision_to_submit_rate": float(decision_to_submit_rate),
        "normal_competitiveness_decision_to_submit_rate": (
            float(normal_competitiveness_submit_count / decision_count)
            if decision_count > 0.0
            else 0.0
        ),
        "submit_capable_to_submit_rate": float(submit_capable_to_submit_rate),
        "normal_competitiveness_submit_capable_to_submit_rate": (
            float(normal_competitiveness_submit_count / submit_capable_decision_count)
            if submit_capable_decision_count > 0.0
            else 0.0
        ),
        "submit_capable_dynamic_to_submit_rate": float(submit_capable_dynamic_to_submit_rate),
        "normal_competitiveness_submit_capable_dynamic_to_submit_rate": (
            float(normal_competitiveness_submit_count / submit_capable_dynamic_predicted_count)
            if submit_capable_dynamic_predicted_count > 0.0
            else 0.0
        ),
        "fill_rate_from_submits": float(fill_rate_from_submits),
        "submit_without_competitiveness_payload_count": float(submit_without_competitiveness_payload_count),
        "outside_window_blocked_count_edge_eval": float(outside_window_blocked_count_edge_eval),
        "risk_reject_after_capable_count_edge_eval": float(risk_reject_after_capable_count),
        "decision_timing_window_distribution": dict(sorted(decision_timing_window.items(), key=lambda item: item[0])),
        "decision_edge_bucket_distribution": dict(sorted(decision_edge_bucket.items(), key=lambda item: item[0])),
        "decision_conviction_bucket_distribution": dict(
            sorted(decision_conviction_bucket.items(), key=lambda item: item[0])
        ),
        "decision_block_reason_distribution": dict(
            sorted(decision_block_reason.items(), key=lambda item: item[0])
        ),
        "decision_aggressiveness_distribution": dict(
            sorted(decision_aggressiveness.items(), key=lambda item: item[0])
        ),
        "decision_multi_oracle_status_distribution": dict(
            sorted(decision_multi_oracle_status.items(), key=lambda item: item[0])
        ),
        "decision_normal_taker_side_class_distribution": dict(
            sorted(decision_normal_taker_side_class.items(), key=lambda item: item[0])
        ),
        "decision_normal_side_policy_distribution": dict(
            sorted(decision_normal_side_policy.items(), key=lambda item: item[0])
        ),
        "edge_eval_submit_reject_reason_distribution": dict(
            sorted(edge_eval_submit_reject_reason.items(), key=lambda item: item[0])
        ),
        "submit_class_distribution": dict(sorted(submit_class_distribution.items(), key=lambda item: item[0])),
        "submit_edge_bucket_distribution": dict(sorted(submit_edge_bucket.items(), key=lambda item: item[0])),
        "submit_conviction_bucket_distribution": dict(
            sorted(submit_conviction_bucket.items(), key=lambda item: item[0])
        ),
        "submit_timing_window_distribution": dict(
            sorted(submit_timing_window.items(), key=lambda item: item[0])
        ),
        "submit_multi_oracle_status_distribution": dict(
            sorted(submit_multi_oracle_status.items(), key=lambda item: item[0])
        ),
        "submit_normal_taker_side_class_distribution": dict(
            sorted(submit_normal_taker_side_class.items(), key=lambda item: item[0])
        ),
        "submit_normal_side_policy_distribution": dict(
            sorted(submit_normal_side_policy.items(), key=lambda item: item[0])
        ),
        "submit_recovery_override_edge_bucket_distribution": dict(
            sorted(submit_recovery_override_edge_bucket.items(), key=lambda item: item[0])
        ),
        "submit_recovery_override_stage_distribution": dict(
            sorted(submit_recovery_override_stage_distribution.items(), key=lambda item: item[0])
        ),
        "submit_recovery_override_reason_distribution": dict(
            sorted(submit_recovery_override_reason_distribution.items(), key=lambda item: item[0])
        ),
        "decision_to_submit_latency_ms_summary": _latency_summary(submit_decision_to_submit_latency_ms),
        "normal_competitiveness_decision_to_submit_latency_ms_summary": _latency_summary(
            submit_decision_to_submit_latency_ms_normal
        ),
        "submit_true_unknown_stage_distribution": dict(
            sorted(submit_true_unknown_stage_distribution.items(), key=lambda item: item[0])
        ),
        "submit_stage_distribution": dict(sorted(submit_stage_distribution.items(), key=lambda item: item[0])),
        "fill_edge_bucket_distribution": dict(sorted(fill_edge_bucket.items(), key=lambda item: item[0])),
        "fill_stage_distribution": dict(sorted(fill_stage_distribution.items(), key=lambda item: item[0])),
        "fill_class_distribution": dict(sorted(fill_class_distribution.items(), key=lambda item: item[0])),
        "submit_unknown_stage_count": float(submit_unknown_stage_count),
        "fill_without_submit_stage_count": float(fill_without_submit_stage_count),
        "lag_class_distribution": dict(sorted(lag_class_distribution.items(), key=lambda item: item[0])),
        "aggressiveness_application_counts": dict(
            sorted(aggressiveness_application_counts.items(), key=lambda item: item[0])
        ),
        "decision_predicted_reject_reason_distribution": dict(
            sorted(decision_predicted_reject_reason.items(), key=lambda item: item[0])
        ),
        "hard_min_unachievable_count_decision": float(hard_min_unachievable_count),
        "dynamic_size_capped_by_risk_count_decision": float(dynamic_size_capped_by_risk_count),
        "multi_oracle_available_count_decision": float(multi_oracle_available_count),
        "multi_oracle_confirmation_count_decision": float(multi_oracle_confirmation_count),
        "multi_oracle_boost_eligible_count_decision": float(multi_oracle_boost_eligible_count),
        "multi_oracle_boost_applied_count_decision": float(multi_oracle_boost_applied_count),
        "normal_taker_same_token_sell_blocked_count_decision": float(normal_taker_same_token_sell_blocked_count),
        "complement_token_mapping_failure_count_decision": float(complement_token_mapping_failure_count),
        "stage_funnel_metrics": stage_funnel_sorted,
        "stage_reduction_cause_counters": stage_reduction_sorted,
        "stage_reduction_primary_cause_counters": stage_primary_reduction_sorted,
        "stage_reduction_delta_accounting": stage_reduction_delta_accounting,
        "stage_final_risk_reject_reason_distribution": stage_risk_reject_reasons_sorted,
        "stage_hidden_blockage_detector": stage_hidden_blockage_detector,
        "hidden_blockage_detector": hidden_blockage_detector,
        "stage_submit_inter_submit_delta_sec": stage_inter_submit_delta_summary,
        "stage_first_claim_guard": stage_first_claim_guard,
    }


def _secondary_oracle_pyth_stats(status_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    states = Counter()
    sample_count = 0.0
    enabled_sample_count = 0.0
    connected_sample_count = 0.0
    unavailable_sample_count = 0.0
    latest: Dict[str, Any] = {}

    for row in status_rows:
        secondary = row.get("secondary_oracle")
        if not isinstance(secondary, dict):
            continue
        pyth = secondary.get("pyth")
        if not isinstance(pyth, dict):
            continue
        sample_count += 1.0
        enabled = bool(pyth.get("enabled", False))
        connected = bool(pyth.get("connected", False))
        if enabled:
            enabled_sample_count += 1.0
            if connected:
                connected_sample_count += 1.0
            else:
                unavailable_sample_count += 1.0
        state = str(pyth.get("operational_state") or "").strip().lower() or "unknown"
        states[state] += 1
        latest = pyth

    connected_ratio = (connected_sample_count / enabled_sample_count) if enabled_sample_count > 0.0 else 0.0
    latest_error = str(latest.get("last_error") or "") if isinstance(latest, dict) else ""
    latest_http_status = latest.get("last_http_status") if isinstance(latest, dict) else None
    latest_http_status_num = int(latest_http_status) if isinstance(latest_http_status, int) else None

    return {
        "sample_count": float(sample_count),
        "enabled_sample_count": float(enabled_sample_count),
        "connected_sample_count": float(connected_sample_count),
        "unavailable_sample_count": float(unavailable_sample_count),
        "connected_ratio_when_enabled": float(connected_ratio),
        "operational_state_distribution": dict(sorted(states.items(), key=lambda item: item[0])),
        "latest": {
            "enabled": bool(latest.get("enabled", False)) if isinstance(latest, dict) else False,
            "connected": bool(latest.get("connected", False)) if isinstance(latest, dict) else False,
            "requests": int(_safe_float(latest.get("requests"), 0.0)) if isinstance(latest, dict) else 0,
            "errors": int(_safe_float(latest.get("errors"), 0.0)) if isinstance(latest, dict) else 0,
            "last_error": latest_error,
            "last_http_status": latest_http_status_num,
            "operational_state": (
                str(latest.get("operational_state") or "").strip().lower()
                if isinstance(latest, dict)
                else ""
            ),
            "feed_id": str(latest.get("feed_id") or "") if isinstance(latest, dict) else "",
            "symbol": str(latest.get("symbol") or "") if isinstance(latest, dict) else "",
            "last_tick_age_sec": (
                float(latest.get("last_tick_age_sec"))
                if isinstance(latest, dict) and isinstance(latest.get("last_tick_age_sec"), (int, float))
                else None
            ),
        },
    }


def _risk_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    decision_count_by_lane = Counter()
    reject_count_by_lane = Counter()
    reject_reason_distribution = Counter()
    scaling_class_distribution = Counter()
    exposure_utilization_ratios: List[float] = []
    near_cap_count = 0.0

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        if event_type not in {"order_submit", "risk_reject"}:
            continue
        lane = str(evt.get("submission_lane") or "unknown").strip().lower() or "unknown"
        if event_type == "risk_reject":
            reason = str(evt.get("reason") or "unknown").strip().lower() or "unknown"
            reject_reason_distribution[reason] += 1
            reject_count_by_lane[lane] += 1
        basis = evt.get("risk_decision_basis")
        if not isinstance(basis, dict):
            continue
        decision_count_by_lane[lane] += 1
        dynamic_scaling = basis.get("dynamic_scaling")
        if isinstance(dynamic_scaling, dict):
            scaling_class = str(dynamic_scaling.get("scaling_class") or "unknown").strip().lower() or "unknown"
            scaling_class_distribution[scaling_class] += 1
        global_guard = basis.get("global_exposure_guard")
        if isinstance(global_guard, dict):
            ratio = _safe_float(global_guard.get("projected_to_cap_ratio"), default=-1.0)
            if ratio >= 0.0:
                exposure_utilization_ratios.append(float(ratio))
            if bool(global_guard.get("near_cap", False)):
                near_cap_count += 1.0

    return {
        "decision_count_by_lane": dict(sorted(decision_count_by_lane.items(), key=lambda item: item[0])),
        "reject_count_by_lane": dict(sorted(reject_count_by_lane.items(), key=lambda item: item[0])),
        "reject_reason_distribution": dict(sorted(reject_reason_distribution.items(), key=lambda item: item[0])),
        "scaling_class_distribution": dict(sorted(scaling_class_distribution.items(), key=lambda item: item[0])),
        "global_exposure_utilization_sample_count": float(len(exposure_utilization_ratios)),
        "global_exposure_utilization_ratio_p50": _percentile(exposure_utilization_ratios, 0.50),
        "global_exposure_utilization_ratio_p90": _percentile(exposure_utilization_ratios, 0.90),
        "global_exposure_utilization_ratio_max": (
            max(exposure_utilization_ratios) if exposure_utilization_ratios else 0.0
        ),
        "global_exposure_near_cap_count": float(near_cap_count),
        "global_exposure_cap_reject_count": float(reject_reason_distribution.get("global_exposure_cap", 0)),
    }


def _reduce_only_recovery_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    waiting_for_maker_exit_rows = 0
    local_size_cap_unavailable_rows = 0
    local_size_cap_flat_or_wrong_side_rows = 0
    local_size_cap_nonflat_or_unknown_rows = 0
    local_reject_lane_distribution: Counter[str] = Counter()
    local_reject_posture_distribution: Counter[str] = Counter()
    local_reject_cap_source_distribution: Counter[str] = Counter()
    accepted_or_reserved_recovery_rows = 0

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip()
        reason = str(evt.get("reason") or "").strip().lower()
        block_reason = str(evt.get("block_reason") or "").strip().lower()
        recovery_active = _as_bool(evt.get("reduce_only_recovery_active"))
        if recovery_active is None:
            risk_context = evt.get("risk_context")
            if isinstance(risk_context, dict):
                recovery_active = _as_bool(risk_context.get("reduce_only_recovery_active"))

        if event_type == "edge_evaluation" and block_reason == "reduce_only_recovery_waiting_for_maker_exit":
            waiting_for_maker_exit_rows += 1

        if event_type in {
            "order_submit",
            "order_submission_reserved",
            "order_submission_transport_attempted",
            "order_submission_accepted",
        } and bool(recovery_active):
            accepted_or_reserved_recovery_rows += 1

        if event_type != "order_submission_rejected_local" or reason != "reduce_only_recovery_size_cap_unavailable":
            continue

        local_size_cap_unavailable_rows += 1
        lane = str(evt.get("submission_lane") or "unknown").strip().lower() or "unknown"
        posture = str(evt.get("financial_posture_class") or "UNKNOWN").strip().upper() or "UNKNOWN"
        cap_source = (
            str(evt.get("reduce_only_dynamic_size_cap_source") or "unknown").strip().lower()
            or "unknown"
        )
        local_reject_lane_distribution[lane] += 1
        local_reject_posture_distribution[posture] += 1
        local_reject_cap_source_distribution[cap_source] += 1

        net_shares = _safe_float(evt.get("reduce_only_net_shares_live"), 0.0)
        size_cap = _safe_float(evt.get("reduce_only_size_cap_shares"), 0.0)
        flat_or_wrong_side = bool(
            abs(float(net_shares)) <= 1e-9
            and float(size_cap) <= 1e-9
            and cap_source == "live_position_flat_or_wrong_side"
        )
        if flat_or_wrong_side:
            local_size_cap_flat_or_wrong_side_rows += 1
        else:
            local_size_cap_nonflat_or_unknown_rows += 1

    if local_size_cap_unavailable_rows <= 0:
        size_cap_classification = "none"
    elif local_size_cap_nonflat_or_unknown_rows <= 0:
        size_cap_classification = "flat_or_wrong_side_noop_only"
    else:
        size_cap_classification = "nonflat_or_unknown_present"

    return {
        "edge_waiting_for_maker_exit_rows": float(waiting_for_maker_exit_rows),
        "accepted_or_reserved_recovery_rows": float(accepted_or_reserved_recovery_rows),
        "local_size_cap_unavailable_rows": float(local_size_cap_unavailable_rows),
        "local_size_cap_flat_or_wrong_side_rows": float(local_size_cap_flat_or_wrong_side_rows),
        "local_size_cap_nonflat_or_unknown_rows": float(local_size_cap_nonflat_or_unknown_rows),
        "local_size_cap_classification": str(size_cap_classification),
        "local_reject_lane_distribution": dict(
            sorted(local_reject_lane_distribution.items(), key=lambda item: item[0])
        ),
        "local_reject_posture_distribution": dict(
            sorted(local_reject_posture_distribution.items(), key=lambda item: item[0])
        ),
        "local_reject_cap_source_distribution": dict(
            sorted(local_reject_cap_source_distribution.items(), key=lambda item: item[0])
        ),
    }


def _maker_sizing_competitiveness_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    maker_submit_rows = 0
    maker_size_resolution_rows = 0
    maker_sizing_reject_rows = 0
    maker_min_notional_max_shares_conflict_rows = 0
    hard_min_notional_floor_applied = 0
    hard_min_share_floor_applied = 0
    depth_target_notional_floor_applied = 0
    hard_max_notional_cap_applied = 0
    hard_max_share_cap_applied = 0
    hard_floor_active_rows = 0
    depth_scaling_active_rows = 0
    reject_reason_distribution: Counter[str] = Counter()
    reject_stage_distribution: Counter[str] = Counter()
    reject_financial_posture_distribution: Counter[str] = Counter()
    resolved_notional_values: List[float] = []
    reject_price_values: List[float] = []
    reject_max_shares_notional_values: List[float] = []
    visible_depth_values: List[float] = []
    effective_depth_values: List[float] = []
    depth_target_ratio_values: List[float] = []

    for evt in events:
        event_type = str(evt.get("event_type") or "").strip().lower()
        lane = str(evt.get("submission_lane") or "").strip().lower()
        if (
            event_type == "risk_reject"
            and lane == "maker"
            and str(evt.get("reason") or "").strip().lower() == "size_notional_bounds"
        ):
            size_resolution = evt.get("size_resolution")
            if not isinstance(size_resolution, dict):
                continue
            maker_sizing_reject_rows += 1
            reasons = [
                str(item).strip().lower()
                for item in list(size_resolution.get("size_decision_reasons") or [])
                if str(item).strip()
            ]
            if not reasons:
                reasons = ["unknown"]
            for reason in reasons:
                reject_reason_distribution[reason] += 1
            stage = str(evt.get("stage") or "UNKNOWN").strip().upper() or "UNKNOWN"
            reject_stage_distribution[stage] += 1
            posture = str(evt.get("financial_posture_class") or "UNKNOWN").strip().upper() or "UNKNOWN"
            reject_financial_posture_distribution[posture] += 1
            price_used = _safe_float(size_resolution.get("price_used"), 0.0)
            if price_used > 0.0:
                reject_price_values.append(float(price_used))
            hard_notional_range = size_resolution.get("maker_hard_notional_range_usd")
            hard_share_range = size_resolution.get("maker_hard_share_range")
            min_notional = (
                _safe_float(hard_notional_range.get("min"), 0.0)
                if isinstance(hard_notional_range, dict)
                else _safe_float(size_resolution.get("maker_hard_min_notional_usd"), 0.0)
            )
            max_shares = (
                _safe_float(hard_share_range.get("max"), 0.0)
                if isinstance(hard_share_range, dict)
                else _safe_float(size_resolution.get("maker_hard_max_shares"), 0.0)
            )
            if price_used > 0.0 and max_shares > 0.0:
                max_shares_notional = float(price_used * max_shares)
                reject_max_shares_notional_values.append(max_shares_notional)
                if min_notional > 0.0 and max_shares_notional + 1e-9 < min_notional:
                    maker_min_notional_max_shares_conflict_rows += 1
            continue

        if event_type != "order_submit":
            continue
        if lane != "maker":
            continue
        maker_submit_rows += 1
        size_resolution = evt.get("size_resolution")
        if not isinstance(size_resolution, dict):
            continue
        maker_size_resolution_rows += 1
        reasons = {
            str(item).strip().lower()
            for item in list(size_resolution.get("size_decision_reasons") or [])
            if str(item).strip()
        }
        if "maker_hard_min_notional_floor" in reasons:
            hard_min_notional_floor_applied += 1
        if "maker_hard_min_shares_floor" in reasons:
            hard_min_share_floor_applied += 1
        if "maker_depth_target_notional_floor" in reasons:
            depth_target_notional_floor_applied += 1
        if "maker_hard_max_notional_cap" in reasons:
            hard_max_notional_cap_applied += 1
        if "maker_hard_max_shares_cap" in reasons:
            hard_max_share_cap_applied += 1

        hard_floor_active = _as_bool(size_resolution.get("maker_hard_floor_active"))
        if hard_floor_active is True:
            hard_floor_active_rows += 1
        elif hard_floor_active is None:
            # backward-safe inference for older rows where explicit boolean may be absent
            min_notional = _safe_float(size_resolution.get("maker_hard_min_notional_usd"), 0.0)
            min_shares = _safe_float(size_resolution.get("maker_hard_min_shares"), 0.0)
            if min_notional > 0.0 or min_shares > 0.0:
                hard_floor_active_rows += 1

        depth_scaling_active = _as_bool(size_resolution.get("maker_depth_scaling_active"))
        if depth_scaling_active is True:
            depth_scaling_active_rows += 1

        resolved_notional = size_resolution.get("resolved_notional_usd")
        if isinstance(resolved_notional, (int, float)):
            resolved_notional_values.append(float(resolved_notional))
        visible_depth = size_resolution.get("visible_depth_shares")
        if isinstance(visible_depth, (int, float)):
            visible_depth_values.append(float(visible_depth))
        effective_depth = size_resolution.get("effective_depth_shares")
        if isinstance(effective_depth, (int, float)):
            effective_depth_values.append(float(effective_depth))
        depth_ratio = size_resolution.get("maker_depth_target_ratio_applied")
        if isinstance(depth_ratio, (int, float)):
            depth_target_ratio_values.append(float(depth_ratio))

    return {
        "maker_submit_rows": float(maker_submit_rows),
        "maker_size_resolution_rows": float(maker_size_resolution_rows),
        "maker_sizing_reject_rows": float(maker_sizing_reject_rows),
        "maker_min_notional_max_shares_conflict_rows": float(maker_min_notional_max_shares_conflict_rows),
        "maker_sizing_reject_reason_distribution": dict(
            sorted(reject_reason_distribution.items(), key=lambda item: item[0])
        ),
        "maker_sizing_reject_stage_distribution": dict(
            sorted(reject_stage_distribution.items(), key=lambda item: item[0])
        ),
        "maker_sizing_reject_financial_posture_distribution": dict(
            sorted(reject_financial_posture_distribution.items(), key=lambda item: item[0])
        ),
        "maker_sizing_reject_price_min": min(reject_price_values) if reject_price_values else 0.0,
        "maker_sizing_reject_price_p50": _percentile(reject_price_values, 0.50),
        "maker_sizing_reject_price_max": max(reject_price_values) if reject_price_values else 0.0,
        "maker_sizing_reject_max_shares_notional_p50": _percentile(reject_max_shares_notional_values, 0.50),
        "maker_sizing_reject_max_shares_notional_max": (
            max(reject_max_shares_notional_values) if reject_max_shares_notional_values else 0.0
        ),
        "hard_min_notional_floor_applied_count": float(hard_min_notional_floor_applied),
        "hard_min_share_floor_applied_count": float(hard_min_share_floor_applied),
        "depth_target_notional_floor_applied_count": float(depth_target_notional_floor_applied),
        "hard_max_notional_cap_applied_count": float(hard_max_notional_cap_applied),
        "hard_max_share_cap_applied_count": float(hard_max_share_cap_applied),
        "hard_floor_active_rows": float(hard_floor_active_rows),
        "depth_scaling_active_rows": float(depth_scaling_active_rows),
        "resolved_notional_usd_p50": _percentile(resolved_notional_values, 0.50),
        "resolved_notional_usd_p90": _percentile(resolved_notional_values, 0.90),
        "resolved_notional_usd_max": max(resolved_notional_values) if resolved_notional_values else 0.0,
        "visible_depth_shares_p50": _percentile(visible_depth_values, 0.50),
        "effective_depth_shares_p50": _percentile(effective_depth_values, 0.50),
        "depth_target_ratio_applied_p50": _percentile(depth_target_ratio_values, 0.50),
    }


def build_report(
    log_dir: pathlib.Path,
    *,
    run_id: Optional[str] = None,
    auto_resolve_run_id: bool = True,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    include_support_artifacts: bool = False,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="nightly_soak_report", session_phase=session_phase)
    explicit_run_id = str(run_id or "").strip() or None
    resolved_log_dir = log_dir.resolve()
    contract = resolve_run_contract(
        log_dir=resolved_log_dir,
        run_id=explicit_run_id,
        run_contract_path_override=run_contract_path,
        allow_open=(normalized_phase == "validate_active"),
    )
    if contract is not None:
        contract_run_id = str(contract.get("run_id") or "").strip()
        if explicit_run_id and contract_run_id != explicit_run_id:
            raise ValueError(f"run_contract_run_id_mismatch:{contract_run_id}!={explicit_run_id}")
        resolved_run_id = contract_run_id or explicit_run_id
        event_files = _load_contract_scoped_files(contract=contract, prefix="events")
        status_files = _load_contract_scoped_files(contract=contract, prefix="status")
        error_files = _load_contract_scoped_files(contract=contract, prefix="errors")
        if not event_files:
            event_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="events", run_id=resolved_run_id)
        if not status_files:
            status_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="status", run_id=resolved_run_id)
        if not error_files:
            error_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="errors", run_id=resolved_run_id)
        # Contract-scoped files are run slices; do full-load for deterministic replay.
        events = apply_contract_bounds(load_jsonl(event_files, max_lines_per_file=0), contract)
        status = apply_contract_bounds(load_jsonl(status_files, max_lines_per_file=0), contract)
        errors = apply_contract_bounds(load_jsonl(error_files, max_lines_per_file=0), contract)
    else:
        resolved_run_id = explicit_run_id
        event_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="events", run_id=resolved_run_id)
        status_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="status", run_id=resolved_run_id)
        error_files = _select_run_scoped_files(log_dir=resolved_log_dir, prefix="errors", run_id=resolved_run_id)
        events = _filter_rows_by_run_id(load_jsonl(event_files, max_lines_per_file=max_lines_per_file), resolved_run_id)
        status = _filter_rows_by_run_id(load_jsonl(status_files, max_lines_per_file=max_lines_per_file), resolved_run_id)
        errors = _filter_rows_by_run_id(load_jsonl(error_files, max_lines_per_file=max_lines_per_file), resolved_run_id)
    run_manifest = _load_run_manifest(resolved_log_dir, resolved_run_id)
    outcome_truth_records = _load_outcome_truth_records(resolved_log_dir, resolved_run_id)
    maker_fight_admission_shadow_bundle = _maker_fight_admission_shadow_bundle(
        events=events,
        status=status,
        outcome_truth_records=outcome_truth_records,
        run_manifest=run_manifest,
    )
    maker_cannon_late_window_probe_bundle = _maker_cannon_late_window_probe_bundle(
        events=events,
        run_manifest=run_manifest,
    )
    maker_mid_window_probe_bundle = _maker_mid_window_probe_bundle(
        events=events,
        run_manifest=run_manifest,
    )
    maker_probe_rows_with_shadow_truth = _maker_probe_rows_with_shadow_truth(
        probe_rows=maker_cannon_late_window_probe_bundle["rows"],
        shadow_rows=maker_fight_admission_shadow_bundle["rows"],
        run_manifest=run_manifest,
    )
    maker_quote_starvation_bundle = _maker_quote_starvation_bundle(
        probe_rows=maker_probe_rows_with_shadow_truth,
        run_manifest=run_manifest,
    )
    maker_truth_reference_starvation_bundle = _maker_truth_reference_starvation_bundle(
        probe_rows=maker_probe_rows_with_shadow_truth,
    )
    maker_quote_construction_bundle = _maker_quote_construction_bundle(
        truth_rows=maker_truth_reference_starvation_bundle["rows"],
    )
    reject_reasons = Counter(
        str(evt.get("reason") or "unknown")
        for evt in events
        if str(evt.get("event_type") or "") == "risk_reject"
    )
    status_rows = len(status)
    quote_uptime = _quote_uptime_ratio(status)
    quote_diagnostics = _status_activity_diagnostics(status)
    by_component = Counter(str(err.get("component") or "unknown") for err in errors)
    capture_stats = _fill_capture_stats(events)
    taker_stage_net_breakout = _taker_stage_net_breakout(events)
    edge_quality = _edge_quality_by_regime(events)
    maker_competitiveness = _maker_competitiveness_stats(events)
    maker_complete_outcomes = _maker_complete_outcome_rates(outcome_truth_records)
    taker_competitiveness = _taker_competitiveness_stats(events)
    taker_intent_gate_posture_matrix = _taker_intent_gate_posture_matrix(events)
    taker_config_gate_posture = _taker_config_gate_posture(run_manifest)
    taker_opportunity_suppression = _taker_opportunity_suppression_stats(events)
    taker_doctrine_breaches = _taker_doctrine_breach_stats(events)
    risk_competitiveness = _risk_competitiveness_stats(events)
    wallet_authority = _wallet_authority_stats(status, events)
    valuation_truth = _valuation_truth_stats(status, events)
    secondary_oracle_pyth = _secondary_oracle_pyth_stats(status)
    maker_sizing_competitiveness = _maker_sizing_competitiveness_stats(events)
    reduce_only_recovery = _reduce_only_recovery_stats(events)
    duration_minutes = _run_duration_minutes(events, status, errors)
    stale_stats = _stale_data_stats(events)
    latency_stats = _latency_distribution(events)
    taker = _taker_summary_stats(events, duration_minutes)
    execution_paths = _execution_path_stats(events, duration_minutes)
    financial_performance = _financial_performance_summary(events, status, run_manifest)
    edge_truth = _edge_truth_summary(events)
    harness_realism_grade, harness_realism_grade_breakdown = _harness_realism_grade(
        events=events,
        edge_truth=edge_truth,
    )
    exercised_harness_realism = build_exercised_harness_realism_surface(
        grade=harness_realism_grade,
        breakdown=harness_realism_grade_breakdown,
    )
    maker_regression_sentinel = _maker_regression_sentinel(
        execution_paths=execution_paths,
        edge_truth=edge_truth,
        duration_minutes=duration_minutes,
    )
    maker_fireability = _maker_fireability_window_stats(
        events,
        taker_config_gate_posture=taker_config_gate_posture,
    )
    mode_timeline = _mode_transition_timeline(events)
    pickoff = _pickoff_indicator(events)
    runtime_classification = classify_runtime(status_rows=status, events=events)
    runtime_resource = _runtime_resource_stats(status)
    control_authority = _control_authority_clarity(status)
    market_data_source = _market_data_source_stats(status)
    maker_participation_waterfall = _maker_participation_waterfall_bundle(
        events=events,
        probe_rows=maker_probe_rows_with_shadow_truth,
        shadow_rows=maker_fight_admission_shadow_bundle["rows"],
        outcome_truth_records=outcome_truth_records,
        run_manifest=run_manifest,
        truth_rows=maker_truth_reference_starvation_bundle["rows"],
    )
    maker_timing_band_diagnostic_matrix = _maker_timing_band_diagnostic_matrix(
        probe_rows=maker_probe_rows_with_shadow_truth,
    )
    maker_timing_band_decision = _maker_timing_band_decision_bundle(
        probe_rows=maker_probe_rows_with_shadow_truth,
        truth_rows=maker_truth_reference_starvation_bundle["rows"],
    )
    maker_zero_submit_root_cause_audit = _maker_zero_submit_root_cause_bundle(
        probe_rows=maker_probe_rows_with_shadow_truth,
        shadow_rows=maker_fight_admission_shadow_bundle["rows"],
        quote_starvation_rows=maker_quote_starvation_bundle["rows"],
        quote_starvation_summary=maker_quote_starvation_bundle["summary"],
        waterfall=maker_participation_waterfall,
        runtime_classification=runtime_classification,
        truth_rows=maker_truth_reference_starvation_bundle["rows"],
    )
    maker_quote_integrity_bundle = _maker_quote_integrity_bundle(
        events=events,
        shadow_rows=maker_fight_admission_shadow_bundle["rows"],
        run_manifest=run_manifest,
        run_id=resolved_run_id,
    )
    maker_selection_authority_bundle = _maker_selection_authority_bundle(
        events=events,
        shadow_rows=maker_fight_admission_shadow_bundle["rows"],
        outcome_truth_records=outcome_truth_records,
        run_manifest=run_manifest,
        run_id=resolved_run_id,
    )
    kill_switch_events = float(
        sum(1 for evt in events if str(evt.get("event_type") or "") == "kill_switch_cancel_all")
    )
    safe_stop_transitions = float(
        sum(1 for evt in mode_timeline if str(evt.get("state") or "") == "safe_stop")
    )
    maker_only_transitions = float(
        sum(1 for evt in mode_timeline if str(evt.get("state") or "") == "maker_only")
    )
    execution_quality_lane_attribution = _execution_quality_lane_attribution(
        events,
        capture_stats=capture_stats,
        horizon_stats=pickoff,
    )
    execution_quality_decision_reference_lane_attribution = (
        _execution_quality_decision_reference_lane_attribution(events)
    )
    preexpiry_recovery_churn = _preexpiry_recovery_churn_stats(events)
    recovery_cost_benefit = _recovery_cost_benefit_stats(events)
    terminal_handoff_deadband = _terminal_handoff_deadband_stats(
        events,
        taker_config_gate_posture=taker_config_gate_posture,
    )
    order_submit_total = float(_safe_float(execution_paths.get("maker_submits")) + _safe_float(execution_paths.get("taker_bonus_submits")))
    fill_total = float(_safe_float(execution_paths.get("maker_fills")) + _safe_float(execution_paths.get("taker_bonus_fills")))
    starvation = _resolve_starvation_mode(
        order_submit_total=order_submit_total,
        fill_total=fill_total,
        runtime_classification=runtime_classification,
        edge_truth=edge_truth,
        kill_switch_events=kill_switch_events,
        safe_stop_transitions=safe_stop_transitions,
        maker_only_transitions=maker_only_transitions,
    )
    protection_path_trigger_chain = _protection_path_trigger_chain(
        events=events,
        runtime_classification=runtime_classification,
        starvation=starvation,
    )
    latest_operating_mode_state = 0.0
    if status:
        mode_val = status[-1].get("gauge.operating_mode_state")
        if isinstance(mode_val, (int, float)):
            latest_operating_mode_state = float(mode_val)
    artifact_identity = build_artifact_identity(log_dir=resolved_log_dir, run_id=resolved_run_id)
    run_commit_lineage = {
        "run_id": str(artifact_identity.get("run_id") or ""),
        "git_commit": str(artifact_identity.get("git_commit") or ""),
        "config_fingerprint_sha256": str(artifact_identity.get("config_fingerprint_sha256") or ""),
        "code_fingerprint_sha256": str(artifact_identity.get("code_fingerprint_sha256") or ""),
        "complete": all(
            bool(str(artifact_identity.get(key) or "").strip())
            for key in ("run_id", "git_commit", "config_fingerprint_sha256", "code_fingerprint_sha256")
        ),
    }

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ts_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "log_dir": str(resolved_log_dir),
        "session_phase": normalized_phase,
        "run_contract_path": str(contract.get("_path", "")) if isinstance(contract, dict) else "",
        "run_id_filter": resolved_run_id,
        "run_id_resolution": (
            "explicit"
            if explicit_run_id
            else "all_runs"
        ),
        "artifact_identity": artifact_identity,
        "run_commit_lineage": run_commit_lineage,
        "event_files": len(event_files),
        "status_files": len(status_files),
        "error_files": len(error_files),
        "duration_minutes": duration_minutes,
        "status_rows": status_rows,
        "error_rows": len(errors),
        "quote_uptime_ratio": quote_uptime,
        "quote_diagnostics": quote_diagnostics,
        "reject_reason_distribution": dict(sorted(reject_reasons.items(), key=lambda x: x[0])),
        "errors_by_component": dict(sorted(by_component.items(), key=lambda x: x[0])),
        "stale_data": stale_stats,
        "latency_distribution_ms": latency_stats,
        "taker": taker,
        "execution_paths": execution_paths,
        "financial_performance": financial_performance,
        "maker_regression_sentinel": maker_regression_sentinel,
        "maker_fireability": maker_fireability,
        "edge_truth": edge_truth,
        "maker_competitiveness": maker_competitiveness,
        "maker_selection_authority": maker_selection_authority_bundle["audit"],
        **maker_complete_outcomes,
        "taker_competitiveness": taker_competitiveness,
        "taker_intent_gate_posture_matrix": taker_intent_gate_posture_matrix,
        "taker_config_gate_posture": taker_config_gate_posture,
        "taker_opportunity_suppression": taker_opportunity_suppression,
        "taker_doctrine_breaches": taker_doctrine_breaches,
        "risk_competitiveness": risk_competitiveness,
        "wallet_authority": wallet_authority,
        "valuation_truth": valuation_truth,
        "secondary_oracle_pyth": secondary_oracle_pyth,
        "maker_sizing_competitiveness": maker_sizing_competitiveness,
        "reduce_only_recovery": reduce_only_recovery,
        EXERCISED_HARNESS_REALISM_FIELD: exercised_harness_realism,
        "maker_market_reference_fallback_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_count")
        ),
        "maker_market_reference_fallback_bid_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_bid_count")
        ),
        "maker_market_reference_fallback_ask_count": _safe_float(
            edge_truth.get("maker_market_reference_fallback_ask_count")
        ),
        "maker_reference_direct_midpoint_activity": _safe_float(
            edge_truth.get("maker_reference_direct_midpoint_activity")
        ),
        "maker_reference_bounded_fallback_activity": _safe_float(
            edge_truth.get("maker_reference_bounded_fallback_activity")
        ),
        "maker_reference_direct_midpoint_action_activity": _safe_float(
            edge_truth.get("maker_reference_direct_midpoint_action_activity")
        ),
        "maker_reference_bounded_fallback_action_activity": _safe_float(
            edge_truth.get("maker_reference_bounded_fallback_action_activity")
        ),
        "mode_transitions": mode_timeline,
        "kill_switch_events": kill_switch_events,
        "safe_stop_transitions": safe_stop_transitions,
        "maker_only_transitions": maker_only_transitions,
        "primary_suppression_cause": str(runtime_classification.get("primary_suppression_cause") or "none"),
        "contributing_suppression_causes": list(runtime_classification.get("contributing_suppression_causes") or []),
        "ambiguous_suppression_cause": bool(runtime_classification.get("ambiguous_suppression_cause", False)),
        "suppression_dominated_run": bool(starvation.get("suppression_dominated_run", False)),
        "execution_starvation_mode": str(starvation.get("execution_starvation_mode") or "unknown"),
        "protected_no_trade_explanation": str(starvation.get("protected_no_trade_explanation") or ""),
        "inferred_suppression_reason": str(starvation.get("inferred_suppression_reason") or ""),
        "inferred_suppression_reason_count": float(_safe_float(starvation.get("inferred_suppression_reason_count"))),
        "control_authority_clarity": control_authority,
        "protection_path_trigger_chain": protection_path_trigger_chain,
        "latest_operating_mode_state": latest_operating_mode_state,
        "pickoff_indicator": pickoff,
        "execution_quality_immediate_midpoint": capture_stats,
        "execution_quality_horizon_outcome": pickoff,
        "execution_quality_lane_attribution": execution_quality_lane_attribution,
        "execution_quality_decision_reference_lane_attribution": (
            execution_quality_decision_reference_lane_attribution
        ),
        "preexpiry_recovery_churn": preexpiry_recovery_churn,
        "recovery_cost_benefit": recovery_cost_benefit,
        "terminal_handoff_deadband": terminal_handoff_deadband,
        "market_data_source": market_data_source,
        "execution_quality": capture_stats,
        "taker_stage_net_breakout": taker_stage_net_breakout,
        "edge_activation_quality_by_regime": edge_quality,
        "runtime_classification": runtime_classification,
        "runtime_resource": runtime_resource,
    }
    if include_support_artifacts:
        report["_support_artifacts"] = {
            "maker_fight_admission_shadow_rows": maker_fight_admission_shadow_bundle["rows"],
            "maker_fight_admission_shadow_summary": maker_fight_admission_shadow_bundle["summary"],
            "maker_fight_admission_calibration_audit": maker_fight_admission_shadow_bundle["calibration_audit"],
            "maker_cannon_late_window_probe_rows": maker_cannon_late_window_probe_bundle["rows"],
            "maker_cannon_late_window_probe_summary": maker_cannon_late_window_probe_bundle["summary"],
            "maker_mid_window_probe_rows": maker_mid_window_probe_bundle["rows"],
            "maker_mid_window_probe_summary": maker_mid_window_probe_bundle["summary"],
            "maker_quote_starvation_audit_rows": maker_quote_starvation_bundle["rows"],
            "maker_quote_starvation_summary": maker_quote_starvation_bundle["summary"],
            "maker_truth_reference_starvation_rows": maker_truth_reference_starvation_bundle["rows"],
            "maker_truth_reference_starvation_summary": maker_truth_reference_starvation_bundle["summary"],
            "maker_quote_construction_audit_rows": maker_quote_construction_bundle["rows"],
            "maker_quote_construction_summary": maker_quote_construction_bundle["summary"],
            "maker_participation_waterfall": maker_participation_waterfall,
            "maker_timing_band_diagnostic_matrix": maker_timing_band_diagnostic_matrix,
            "maker_timing_band_decision": maker_timing_band_decision,
            "maker_zero_submit_root_cause_audit": maker_zero_submit_root_cause_audit,
            "maker_quote_integrity_manifest": maker_quote_integrity_bundle["manifest"],
            "maker_quote_integrity_trace_rows": maker_quote_integrity_bundle["trace_rows"],
            "maker_execution_quality_semantics": maker_quote_integrity_bundle[
                "execution_quality_semantics"
            ],
            "maker_quote_mutation_summary": maker_quote_integrity_bundle[
                "quote_mutation_summary"
            ],
            "maker_resting_order_survival_audit": maker_quote_integrity_bundle[
                "resting_order_survival_audit"
            ],
            "maker_quote_integrity_summary": maker_quote_integrity_bundle["summary"],
            "maker_selection_authority_audit": maker_selection_authority_bundle["audit"],
            "maker_selection_authority_counterfactual": maker_selection_authority_bundle[
                "counterfactual"
            ],
        }
    return report


def render_human_summary(report: Dict[str, Any]) -> str:
    top_reject = sorted(report.get("reject_reason_distribution", {}).items(), key=lambda x: x[1], reverse=True)[:5]
    latency = report.get("latency_distribution_ms", {})
    taker = report.get("taker", report.get("taker", {}))
    paths = report.get("execution_paths", {})
    financial = report.get("financial_performance", {}) if isinstance(report.get("financial_performance"), dict) else {}
    financial_capital = (
        financial.get("capital_progression", {})
        if isinstance(financial.get("capital_progression"), dict)
        else {}
    )
    financial_overall = financial.get("overall", {}) if isinstance(financial.get("overall"), dict) else {}
    financial_by_lane = financial.get("by_lane", {}) if isinstance(financial.get("by_lane"), dict) else {}
    financial_maker = financial_by_lane.get("maker", {}) if isinstance(financial_by_lane.get("maker"), dict) else {}
    financial_taker = financial_by_lane.get("taker", {}) if isinstance(financial_by_lane.get("taker"), dict) else {}
    stale = report.get("stale_data", {})
    pickoff = report.get("pickoff_indicator", {})
    market_data_source = report.get("market_data_source", {})
    mode_transitions = report.get("mode_transitions", [])
    eq = report.get("execution_quality", {})
    eq_immediate = report.get("execution_quality_immediate_midpoint", eq)
    eq_horizon = report.get("execution_quality_horizon_outcome", pickoff)
    lane_attr = (
        report.get("execution_quality_lane_attribution", {})
        if isinstance(report.get("execution_quality_lane_attribution"), dict)
        else {}
    )
    lane_attr_by_lane = lane_attr.get("by_lane", {}) if isinstance(lane_attr.get("by_lane"), dict) else {}
    lane_net = {
        str(lane): _safe_float(metrics.get("immediate_capture_minus_adverse"))
        for lane, metrics in lane_attr_by_lane.items()
        if isinstance(metrics, dict)
    }
    lane_fills = {
        str(lane): int(_safe_float(metrics.get("fill_event_count")))
        for lane, metrics in lane_attr_by_lane.items()
        if isinstance(metrics, dict)
    }
    decision_lane_attr = (
        report.get("execution_quality_decision_reference_lane_attribution", {})
        if isinstance(report.get("execution_quality_decision_reference_lane_attribution"), dict)
        else {}
    )
    decision_lane_by_lane = (
        decision_lane_attr.get("by_lane", {})
        if isinstance(decision_lane_attr.get("by_lane"), dict)
        else {}
    )
    decision_lane_net = {
        str(lane): _safe_float(metrics.get("immediate_capture_minus_adverse"))
        for lane, metrics in decision_lane_by_lane.items()
        if isinstance(metrics, dict)
    }
    decision_lane_fills = {
        str(lane): int(_safe_float(metrics.get("fill_event_count")))
        for lane, metrics in decision_lane_by_lane.items()
        if isinstance(metrics, dict)
    }
    preexpiry_churn = (
        report.get("preexpiry_recovery_churn", {})
        if isinstance(report.get("preexpiry_recovery_churn"), dict)
        else {}
    )
    valuation_truth = report.get("valuation_truth", {}) if isinstance(report.get("valuation_truth"), dict) else {}
    taker_stage_net = report.get("taker_stage_net_breakout", {}) if isinstance(report.get("taker_stage_net_breakout"), dict) else {}
    edge_truth = report.get("edge_truth", {}) if isinstance(report.get("edge_truth"), dict) else {}
    maker_comp = report.get("maker_competitiveness", {}) if isinstance(report.get("maker_competitiveness"), dict) else {}
    maker_selection = (
        report.get("maker_selection_authority", {})
        if isinstance(report.get("maker_selection_authority"), dict)
        else {}
    )
    taker_comp = report.get("taker_competitiveness", {}) if isinstance(report.get("taker_competitiveness"), dict) else {}
    taker_gate_posture = (
        report.get("taker_intent_gate_posture_matrix", {})
        if isinstance(report.get("taker_intent_gate_posture_matrix"), dict)
        else {}
    )
    taker_config_gate = (
        report.get("taker_config_gate_posture", {})
        if isinstance(report.get("taker_config_gate_posture"), dict)
        else {}
    )
    taker_suppression = (
        report.get("taker_opportunity_suppression", {})
        if isinstance(report.get("taker_opportunity_suppression"), dict)
        else {}
    )
    taker_doctrine_breaches = (
        report.get("taker_doctrine_breaches", {})
        if isinstance(report.get("taker_doctrine_breaches"), dict)
        else {}
    )
    taker_suppression_normal = (
        taker_suppression.get("normal", {}) if isinstance(taker_suppression.get("normal"), dict) else {}
    )
    taker_suppression_recovery = (
        taker_suppression.get("recovery", {}) if isinstance(taker_suppression.get("recovery"), dict) else {}
    )
    taker_stage_funnel = (
        taker_comp.get("stage_funnel_metrics", {})
        if isinstance(taker_comp.get("stage_funnel_metrics"), dict)
        else {}
    )
    taker_stage_delta_accounting = (
        taker_comp.get("stage_reduction_delta_accounting", {})
        if isinstance(taker_comp.get("stage_reduction_delta_accounting"), dict)
        else {}
    )
    risk_comp = report.get("risk_competitiveness", {}) if isinstance(report.get("risk_competitiveness"), dict) else {}
    wallet_comp = report.get("wallet_authority", {}) if isinstance(report.get("wallet_authority"), dict) else {}
    pyth_comp = report.get("secondary_oracle_pyth", {}) if isinstance(report.get("secondary_oracle_pyth"), dict) else {}
    maker_size_comp = (
        report.get("maker_sizing_competitiveness", {})
        if isinstance(report.get("maker_sizing_competitiveness"), dict)
        else {}
    )
    reduce_only = (
        report.get("reduce_only_recovery", {})
        if isinstance(report.get("reduce_only_recovery"), dict)
        else {}
    )
    recovery_cost = (
        report.get("recovery_cost_benefit", {})
        if isinstance(report.get("recovery_cost_benefit"), dict)
        else {}
    )
    terminal_handoff_deadband = (
        report.get("terminal_handoff_deadband", {})
        if isinstance(report.get("terminal_handoff_deadband"), dict)
        else {}
    )
    runtime_class = report.get("runtime_classification", {}) if isinstance(report.get("runtime_classification"), dict) else {}
    runtime_class_name = str(runtime_class.get("classification") or "")
    runtime_promotable = bool(runtime_class.get("promotion_eligible", False))
    primary_suppression_cause = str(report.get("primary_suppression_cause") or "none")
    starvation_mode = str(report.get("execution_starvation_mode") or "unknown")
    suppression_dominated_run = bool(report.get("suppression_dominated_run", False))
    exercised_harness_realism = normalize_nightly_exercised_harness_realism(report)

    lines = [
        f"log_dir={report.get('log_dir')}",
        f"duration_minutes={_safe_float(report.get('duration_minutes')):.2f}",
        f"quote_uptime_ratio={_safe_float(report.get('quote_uptime_ratio')):.4f}",
        f"error_rows={int(_safe_float(report.get('error_rows')))}",
        (
            "exercised_harness_realism="
            + f"grade={int(_safe_float(exercised_harness_realism.get('grade')))},"
            + f"semantics={str(exercised_harness_realism.get('semantics') or HARNESS_REALISM_GRADE_SEMANTICS)},"
            + f"authority={str(exercised_harness_realism.get('authority') or HARNESS_REALISM_GRADE_AUTHORITY)},"
            + f"breakdown={json.dumps(exercised_harness_realism.get('breakdown', {}), sort_keys=True)}"
        ),
        f"top_rejects={top_reject}",
        (
            "latency_ms="
            + f"samples={int(_safe_float(latency.get('sample_count')))},"
            + f"median={_safe_float(latency.get('median_ms')):.2f},"
            + f"p90={_safe_float(latency.get('p90_ms')):.2f},"
            + f"p95={_safe_float(latency.get('p95_ms')):.2f}"
        ),
        (
            "taker="
            + f"submits={int(_safe_float(taker.get('submits')))},"
            + f"fills={int(_safe_float(taker.get('fills')))},"
            + f"fill_rate={_safe_float(taker.get('fill_rate')):.4f},"
            + f"midpoint_win_rate_proxy={_safe_float(taker.get('midpoint_win_rate_proxy')):.4f},"
            + f"fire_rate_per_min={_safe_float(taker.get('fire_rate_per_min')):.4f}"
        ),
        (
            "execution_paths="
            + f"maker_submits={int(_safe_float(paths.get('maker_submits')))},"
            + f"maker_fills={int(_safe_float(paths.get('maker_fills')))},"
            + f"taker_bonus_submits={int(_safe_float(paths.get('taker_bonus_submits')))},"
            + f"taker_bonus_fills={int(_safe_float(paths.get('taker_bonus_fills')))}"
        ),
        (
            "financial_performance="
            + f"base_capital_start_usd={_safe_float(financial_capital.get('configured_base_capital_usd')):.4f},"
            + "starting_deployable_capital_usd="
            + f"{_safe_float(financial_capital.get('configured_starting_deployable_capital_usd')):.4f},"
            + "ending_stable_balance_total_usd="
            + f"{_safe_float(financial_capital.get('ending_wallet_stable_balance_total_usd')):.4f},"
            + "ending_deployable_capital_usd="
            + f"{_safe_float(financial_capital.get('ending_wallet_deployable_capital_usd')):.4f},"
            + f"net_pnl_usd={_safe_float(financial_overall.get('net_pnl_usd')):.4f},"
            + f"latest_total_pnl_usd={_safe_float(financial.get('latest_total_pnl_usd')):.4f},"
            + f"win_rate={_safe_float(financial_overall.get('win_rate')):.4f},"
            + f"closed_trades={int(_safe_float(financial_overall.get('closed_trade_count')))},"
            + "avg_submitted_order_notional_usd="
            + f"{_safe_float(financial_overall.get('avg_submitted_order_notional_usd')):.4f},"
            + "avg_filled_order_notional_usd="
            + f"{_safe_float(financial_overall.get('avg_filled_order_notional_usd')):.4f},"
            + "avg_submitted_order_size_shares="
            + f"{_safe_float(financial_overall.get('avg_submitted_order_size_shares')):.4f},"
            + "avg_filled_order_size_shares="
            + f"{_safe_float(financial_overall.get('avg_filled_order_size_shares')):.4f},"
            + f"maker_net_pnl_usd={_safe_float(financial_maker.get('net_pnl_usd')):.4f},"
            + f"maker_win_rate={_safe_float(financial_maker.get('win_rate')):.4f},"
            + f"taker_net_pnl_usd={_safe_float(financial_taker.get('net_pnl_usd')):.4f},"
            + f"taker_win_rate={_safe_float(financial_taker.get('win_rate')):.4f}"
        ),
        (
            "maker_regression_sentinel="
            + f"triggered={1 if bool((report.get('maker_regression_sentinel') or {}).get('triggered', False)) else 0},"
            + f"reasons={list((report.get('maker_regression_sentinel') or {}).get('regression_reasons') or [])}"
        ),
        (
            "maker_reference_activity="
            + f"direct_midpoint={int(_safe_float(edge_truth.get('maker_reference_direct_midpoint_activity')))},"
            + f"bounded_fallback={int(_safe_float(edge_truth.get('maker_reference_bounded_fallback_activity')))},"
            + f"fallback_bid={int(_safe_float(edge_truth.get('maker_market_reference_fallback_bid_count')))},"
            + f"fallback_ask={int(_safe_float(edge_truth.get('maker_market_reference_fallback_ask_count')))}"
        ),
        (
            "maker_competitiveness="
            + f"timing_gate_blocked={int(_safe_float(maker_comp.get('timing_gate_blocked_count_edge_eval')))},"
            + f"one_sided_submit_buy={int(_safe_float(maker_comp.get('one_sided_activation_submit_buy_count')))},"
            + f"one_sided_submit_sell={int(_safe_float(maker_comp.get('one_sided_activation_submit_sell_count')))},"
            + f"aggressiveness={json.dumps(maker_comp.get('aggressiveness_application_counts', {}), sort_keys=True)}"
        ),
        (
            "maker_selection_authority="
            + f"runtime_enabled={bool(maker_selection.get('runtime_selector_enabled', False))},"
            + "submitted_by_one_sided="
            + f"{json.dumps(maker_selection.get('submitted_count_by_one_sided_active', {}), sort_keys=True)},"
            + "blocked_by_reason="
            + f"{json.dumps(maker_selection.get('blocked_count_by_canonical_reject_reason', {}), sort_keys=True)}"
        ),
        (
            "taker_stage_first_evidence="
            + f"funnel={json.dumps(taker_stage_funnel, sort_keys=True)},"
            + f"delta_accounting={json.dumps(taker_stage_delta_accounting, sort_keys=True)}"
        ),
        (
            "taker_competitiveness="
            + f"decisions={int(_safe_float(taker_comp.get('decision_count')))},"
            + f"submit_capable_static={int(_safe_float(taker_comp.get('submit_capable_static_decision_count')))},"
            + f"submit_capable_dynamic={int(_safe_float(taker_comp.get('submit_capable_dynamic_predicted_count')))},"
            + f"submit_capable_decisions={int(_safe_float(taker_comp.get('submit_capable_decision_count')))},"
            + f"actual_submits={int(_safe_float(taker_comp.get('actual_submit_count')))},"
            + f"normal_submits={int(_safe_float(taker_comp.get('normal_competitiveness_submit_count')))},"
            + f"recovery_override_submits={int(_safe_float(taker_comp.get('recovery_override_submit_count')))},"
            + f"true_unknown_submits={int(_safe_float(taker_comp.get('true_unknown_submit_count')))},"
            + f"fills={int(_safe_float(taker_comp.get('fill_count')))},"
            + f"decision_to_submit_rate={_safe_float(taker_comp.get('decision_to_submit_rate')):.4f},"
            + "normal_decision_to_submit_rate="
            + f"{_safe_float(taker_comp.get('normal_competitiveness_decision_to_submit_rate')):.4f},"
            + f"submit_capable_to_submit_rate={_safe_float(taker_comp.get('submit_capable_to_submit_rate')):.4f},"
            + "normal_submit_capable_to_submit_rate="
            + f"{_safe_float(taker_comp.get('normal_competitiveness_submit_capable_to_submit_rate')):.4f},"
            + f"submit_capable_dynamic_to_submit_rate={_safe_float(taker_comp.get('submit_capable_dynamic_to_submit_rate')):.4f},"
            + f"fill_rate_from_submits={_safe_float(taker_comp.get('fill_rate_from_submits')):.4f},"
            + f"submit_classes={json.dumps(taker_comp.get('submit_class_distribution', {}), sort_keys=True)},"
            + f"outside_window_blocked={int(_safe_float(taker_comp.get('outside_window_blocked_count_edge_eval')))},"
            + f"risk_reject_after_capable={int(_safe_float(taker_comp.get('risk_reject_after_capable_count_edge_eval')))},"
            + f"hard_min_unachievable={int(_safe_float(taker_comp.get('hard_min_unachievable_count_decision')))},"
            + f"dynamic_capped={int(_safe_float(taker_comp.get('dynamic_size_capped_by_risk_count_decision')))},"
            + f"aggressiveness={json.dumps(taker_comp.get('aggressiveness_application_counts', {}), sort_keys=True)}"
        ),
        (
            "taker_intent_gate_posture="
            + f"classification={str(taker_gate_posture.get('observed_intent_classification') or 'unknown')},"
            + "classes="
            + f"{json.dumps(taker_gate_posture.get('event_class_distribution', {}), sort_keys=True)},"
            + "required_min_edge="
            + f"{json.dumps(taker_gate_posture.get('required_min_edge_by_intent_stage', {}), sort_keys=True)},"
            + "stage_windows="
            + f"{json.dumps((taker_gate_posture.get('latest_stage_window_semantics') or {}).get('stage_rows', {}), sort_keys=True)},"
            + "recovery_below_required_min_edge="
            + f"{int(_safe_float(taker_gate_posture.get('recovery_override_below_required_min_edge_count')))}"
        ),
        (
            "taker_config_gate_posture="
            + f"boundary_class={str(taker_config_gate.get('boundary_class') or 'unknown')},"
            + "normal_can_open_inside_recovery="
            + f"{1 if bool((taker_config_gate.get('boundary_alignment') or {}).get('normal_can_open_inside_held_recovery_window', False)) else 0},"
            + "normal_can_open_inside_final_window="
            + f"{1 if bool((taker_config_gate.get('boundary_alignment') or {}).get('normal_can_open_inside_taker_final_window', False)) else 0},"
            + "final_window_overlap_max_sec="
            + f"{_safe_float((taker_config_gate.get('boundary_alignment') or {}).get('max_normal_entry_width_inside_final_window_sec')):.2f},"
            + "require_lag_verification="
            + f"{json.dumps((taker_config_gate.get('taker_lag_gate') or {}).get('require_lag_verification'))},"
            + "latency_hit_threshold_ms="
            + f"{_safe_float((taker_config_gate.get('latency_verifier') or {}).get('hit_threshold_ms')):.2f},"
            + "stage_final_windows="
            + f"{json.dumps((taker_config_gate.get('normal_taker_entry_gates') or {}).get('stage_final_window_sec_by_stage', {}), sort_keys=True)},"
            + f"flags={json.dumps(taker_config_gate.get('posture_flags', []), sort_keys=True)}"
        ),
        (
            "maker_taker_terminal_handoff="
            + "maker_gate_min_sec="
            + f"{_safe_float((taker_config_gate.get('maker_gate_posture') or {}).get('timing_gate_min_sec_to_expiry')):.2f},"
            + "maker_gate_max_sec="
            + f"{_safe_float((taker_config_gate.get('maker_gate_posture') or {}).get('timing_gate_max_sec_to_expiry')):.2f},"
            + "held_preexpiry_reduce_only_sec="
            + f"{_safe_float((taker_config_gate.get('boundary_alignment') or {}).get('held_preexpiry_reduce_only_sec')):.2f},"
            + "preexpiry_emergency_taker_window_sec="
            + f"{_safe_float((taker_config_gate.get('boundary_alignment') or {}).get('preexpiry_emergency_taker_window_sec')):.2f},"
            + "taker_min_new_exposure_sec="
            + f"{_safe_float((taker_config_gate.get('boundary_alignment') or {}).get('min_sec_to_expiry_for_new_exposure')):.2f},"
            + "maker_gate_closes_at_reduce_only_boundary="
            + (
                "1"
                if abs(
                    _safe_float((taker_config_gate.get('maker_gate_posture') or {}).get('timing_gate_min_sec_to_expiry'))
                    - _safe_float((taker_config_gate.get('boundary_alignment') or {}).get('held_preexpiry_reduce_only_sec'))
                )
                <= 1e-9
                else "0"
            )
        ),
        (
            "terminal_handoff_deadband="
            + f"classification={str(terminal_handoff_deadband.get('classification') or 'unknown')},"
            + f"candidate_evals={int(_safe_float(terminal_handoff_deadband.get('candidate_recovery_edge_eval_count')))},"
            + "waiting_for_maker_exit="
            + f"{int(_safe_float(terminal_handoff_deadband.get('waiting_for_maker_exit_count')))},"
            + f"action_taker={int(_safe_float(terminal_handoff_deadband.get('action_taker_count')))},"
            + "maker_gate_closes_at_reduce_only_boundary="
            + f"{1 if bool(terminal_handoff_deadband.get('maker_gate_closes_at_reduce_only_boundary', False)) else 0},"
            + "block_reasons="
            + f"{json.dumps(terminal_handoff_deadband.get('block_reason_distribution', {}), sort_keys=True)},"
            + "actions="
            + f"{json.dumps(terminal_handoff_deadband.get('action_distribution', {}), sort_keys=True)},"
            + "allowance="
            + f"{json.dumps(terminal_handoff_deadband.get('allowance_distribution', {}), sort_keys=True)},"
            + "sec_to_expiry="
            + f"{json.dumps(terminal_handoff_deadband.get('candidate_sec_to_expiry', {}), sort_keys=True)}"
        ),
        (
            "taker_opportunity_suppression="
            + f"edge_evals={int(_safe_float(taker_suppression.get('total_taker_edge_eval_count')))},"
            + f"normal_evals={int(_safe_float(taker_suppression_normal.get('edge_eval_count')))},"
            + f"normal_enabled_stage_evals={int(_safe_float(taker_suppression_normal.get('taker_enabled_stage_eval_count')))},"
            + f"normal_submit_candidates={int(_safe_float(taker_suppression_normal.get('submit_candidate_count')))},"
            + f"normal_actions={int(_safe_float(taker_suppression_normal.get('action_taken_taker_count')))},"
            + "normal_classes="
            + f"{json.dumps(taker_suppression_normal.get('suppression_class_distribution', {}), sort_keys=True)},"
            + f"recovery_evals={int(_safe_float(taker_suppression_recovery.get('edge_eval_count')))},"
            + f"recovery_submit_candidates={int(_safe_float(taker_suppression_recovery.get('submit_candidate_count')))},"
            + f"recovery_actions={int(_safe_float(taker_suppression_recovery.get('action_taken_taker_count')))},"
            + "recovery_classes="
            + f"{json.dumps(taker_suppression_recovery.get('suppression_class_distribution', {}), sort_keys=True)}"
        ),
        (
            "secondary_oracle_pyth="
            + f"samples={int(_safe_float(pyth_comp.get('sample_count')))},"
            + f"enabled_samples={int(_safe_float(pyth_comp.get('enabled_sample_count')))},"
            + f"connected_samples={int(_safe_float(pyth_comp.get('connected_sample_count')))},"
            + f"connected_ratio_when_enabled={_safe_float(pyth_comp.get('connected_ratio_when_enabled')):.4f},"
            + f"states={json.dumps(pyth_comp.get('operational_state_distribution', {}), sort_keys=True)},"
            + f"latest={json.dumps(pyth_comp.get('latest', {}), sort_keys=True)}"
        ),
        (
            "risk_competitiveness="
            + f"decisions={json.dumps(risk_comp.get('decision_count_by_lane', {}), sort_keys=True)},"
            + f"rejects={json.dumps(risk_comp.get('reject_count_by_lane', {}), sort_keys=True)},"
            + f"scaling_classes={json.dumps(risk_comp.get('scaling_class_distribution', {}), sort_keys=True)},"
            + f"global_exposure_rejects={int(_safe_float(risk_comp.get('global_exposure_cap_reject_count')))}"
        ),
        (
            "wallet_authority="
            + f"latest_contract={json.dumps(wallet_comp.get('latest_contract', {}), sort_keys=True)},"
            + f"event_counts={json.dumps(wallet_comp.get('event_counts', {}), sort_keys=True)}"
        ),
        (
            "maker_sizing_competitiveness="
            + f"submit_rows={int(_safe_float(maker_size_comp.get('maker_submit_rows')))},"
            + f"sizing_rejects={int(_safe_float(maker_size_comp.get('maker_sizing_reject_rows')))},"
            + "min_notional_max_shares_conflicts="
            + f"{int(_safe_float(maker_size_comp.get('maker_min_notional_max_shares_conflict_rows')))},"
            + f"hard_min_notional_applied={int(_safe_float(maker_size_comp.get('hard_min_notional_floor_applied_count')))},"
            + f"depth_target_applied={int(_safe_float(maker_size_comp.get('depth_target_notional_floor_applied_count')))},"
            + f"resolved_notional_p50={_safe_float(maker_size_comp.get('resolved_notional_usd_p50')):.2f},"
            + f"resolved_notional_p90={_safe_float(maker_size_comp.get('resolved_notional_usd_p90')):.2f}"
        ),
        (
            "reduce_only_recovery="
            + f"waiting_for_maker_exit={int(_safe_float(reduce_only.get('edge_waiting_for_maker_exit_rows')))},"
            + f"local_size_cap_unavailable={int(_safe_float(reduce_only.get('local_size_cap_unavailable_rows')))},"
            + f"flat_or_wrong_side={int(_safe_float(reduce_only.get('local_size_cap_flat_or_wrong_side_rows')))},"
            + f"nonflat_or_unknown={int(_safe_float(reduce_only.get('local_size_cap_nonflat_or_unknown_rows')))},"
            + f"classification={str(reduce_only.get('local_size_cap_classification') or 'none')},"
            + f"cap_sources={json.dumps(reduce_only.get('local_reject_cap_source_distribution', {}), sort_keys=True)}"
        ),
        (
            "recovery_cost_benefit="
            + f"submits={int(_safe_float(recovery_cost.get('recovery_submit_count')))},"
            + f"fills={int(_safe_float(recovery_cost.get('recovery_fill_event_count')))},"
            + f"fill_notional={_safe_float(recovery_cost.get('fill_notional')):.6f},"
            + f"immediate_capture={_safe_float(recovery_cost.get('immediate_capture')):.6f},"
            + f"immediate_adverse={_safe_float(recovery_cost.get('immediate_adverse_selection')):.6f},"
            + f"immediate_net={_safe_float(recovery_cost.get('immediate_capture_minus_adverse')):.6f},"
            + "fill_classes="
            + f"{json.dumps(recovery_cost.get('fill_class_distribution', {}), sort_keys=True)},"
            + "refinement_classes="
            + f"{json.dumps(recovery_cost.get('fill_refinement_class_distribution', {}), sort_keys=True)},"
            + "emergency_blocks="
            + f"{json.dumps(recovery_cost.get('preexpiry_emergency_block_reason_distribution', {}), sort_keys=True)},"
            + "emergency_block_classes="
            + f"{json.dumps(recovery_cost.get('preexpiry_emergency_block_class_distribution', {}), sort_keys=True)}"
        ),
        (
            "preexpiry_emergency_handoff="
            + f"attempts={int(_safe_float(recovery_cost.get('preexpiry_emergency_attempt_count')))},"
            + f"fills={int(_safe_float(recovery_cost.get('preexpiry_emergency_fill_count')))},"
            + f"blocks={int(_safe_float(recovery_cost.get('preexpiry_emergency_block_count')))},"
            + f"maker_blocked={int(_safe_float(recovery_cost.get('preexpiry_emergency_maker_blocked_count')))},"
            + "maker_blocked_ratio="
            + (
                f"{_safe_float(recovery_cost.get('preexpiry_emergency_maker_blocked_count')) / _safe_float(recovery_cost.get('preexpiry_emergency_attempt_count')):.4f}"
                if _safe_float(recovery_cost.get('preexpiry_emergency_attempt_count')) > 0.0
                else "0.0000"
            )
            + ",maker_no_submission="
            + f"{json.dumps(recovery_cost.get('preexpiry_emergency_maker_no_submission_distribution', {}), sort_keys=True)},"
            + "filled_maker_no_submission="
            + f"{json.dumps(recovery_cost.get('preexpiry_emergency_filled_maker_no_submission_distribution', {}), sort_keys=True)},"
            + "blocked_maker_no_submission="
            + f"{json.dumps(recovery_cost.get('preexpiry_emergency_blocked_maker_no_submission_distribution', {}), sort_keys=True)}"
        ),
        (
            "preexpiry_recovery_taker_gate="
            + f"evals={int(_safe_float(recovery_cost.get('recovery_taker_edge_eval_count')))},"
            + "actions="
            + f"{json.dumps(recovery_cost.get('recovery_taker_edge_action_distribution', {}), sort_keys=True)},"
            + "block_reasons="
            + f"{json.dumps(recovery_cost.get('recovery_taker_edge_block_reason_distribution', {}), sort_keys=True)},"
            + "stages="
            + f"{json.dumps(recovery_cost.get('recovery_taker_edge_stage_distribution', {}), sort_keys=True)},"
            + "allowance="
            + f"{json.dumps(recovery_cost.get('recovery_taker_edge_allowance_distribution', {}), sort_keys=True)},"
            + "sec_to_expiry="
            + f"{json.dumps(recovery_cost.get('recovery_taker_edge_sec_to_expiry', {}), sort_keys=True)}"
        ),
        (
            "taker_doctrine_breaches="
            + "hard_window_submit_violations="
            + f"{int(_safe_float(taker_doctrine_breaches.get('hard_window_submit_violation_count')))},"
            + "maker_to_taker_recovery_handoff_disabled="
            + f"{int(_safe_float(taker_doctrine_breaches.get('maker_to_taker_recovery_handoff_disabled_count')))},"
            + "taker_recovery_disabled_in_taker_scope="
            + f"{int(_safe_float(taker_doctrine_breaches.get('taker_recovery_disabled_in_taker_scope_count')))},"
            + "block_reasons="
            + f"{json.dumps(taker_doctrine_breaches.get('block_reason_distribution', {}), sort_keys=True)}"
        ),
        (
            "stale="
            + f"stale_book_rejects={int(_safe_float(stale.get('stale_book_rejects')))},"
            + f"stale_oracle_blocks={int(_safe_float(stale.get('stale_oracle_edge_blocks')))},"
            + f"disarmed_blocks={int(_safe_float(stale.get('disarmed_edge_blocks')))}"
        ),
        (
            "pickoff="
            + f"horizon_sec={_safe_float(eq_horizon.get('horizon_outcome_horizon_sec', pickoff.get('horizon_sec'))):.2f},"
            + f"fills_scored={int(_safe_float(eq_horizon.get('fills_scored', pickoff.get('fills_scored'))))},"
            + f"horizon_adverse_count={int(_safe_float(eq_horizon.get('horizon_outcome_adverse_after_fill_count', pickoff.get('adverse_after_fill_count'))))},"
            + f"horizon_adverse_ratio={_safe_float(eq_horizon.get('horizon_outcome_adverse_after_fill_ratio', pickoff.get('adverse_after_fill_ratio'))):.4f}"
        ),
        (
            "market_data_source="
            + f"ws_delta={int(_safe_float(market_data_source.get('book_updates_ws_delta')))},"
            + f"rest_delta={int(_safe_float(market_data_source.get('book_updates_rest_delta')))},"
            + f"rest_ratio={_safe_float(market_data_source.get('book_updates_rest_ratio')):.4f}"
        ),
        (
            "execution_quality_immediate_midpoint="
            + f"fills_scored={int(_safe_float(eq_immediate.get('fills_scored', eq.get('fills_scored'))))},"
            + f"immediate_capture={_safe_float(eq_immediate.get('immediate_capture', eq.get('realized_capture'))):.6f},"
            + f"immediate_adverse={_safe_float(eq_immediate.get('immediate_adverse_selection', eq.get('adverse_selection'))):.6f},"
            + f"immediate_net={_safe_float(eq_immediate.get('immediate_capture_minus_adverse', eq.get('capture_minus_adverse'))):.6f}"
        ),
        (
            "execution_quality_lane_attribution="
            + "immediate_reconciles="
            + f"{1 if bool((lane_attr.get('reconciliation') or {}).get('immediate_capture_minus_adverse_reconciles', False)) else 0},"
            + "horizon_reconciles="
            + f"{1 if bool((lane_attr.get('reconciliation') or {}).get('horizon_adverse_after_fill_count_reconciles', False)) else 0},"
            + f"lane_net={json.dumps(lane_net, sort_keys=True)},"
            + f"lane_fills={json.dumps(lane_fills, sort_keys=True)}"
        ),
        (
            "execution_quality_decision_reference_lane_attribution="
            + f"lane_net={json.dumps(decision_lane_net, sort_keys=True)},"
            + f"lane_fills={json.dumps(decision_lane_fills, sort_keys=True)},"
            + "total_net="
            + f"{_safe_float((decision_lane_attr.get('total') or {}).get('immediate_capture_minus_adverse')):.6f}"
        ),
        (
            "preexpiry_recovery_churn="
            + f"overlap_detected={1 if bool(preexpiry_churn.get('boundary_overlap_detected', False)) else 0},"
            + "normal_inside_overlap="
            + f"{int(_safe_float(preexpiry_churn.get('normal_taker_submit_inside_allowed_overlap_window_count')))},"
            + "normal_fill_recovery_within_window="
            + f"{int(_safe_float(preexpiry_churn.get('normal_taker_fill_with_recovery_fill_within_window_count')))},"
            + "normal_fill_recovery_within_window_ratio="
            + f"{_safe_float(preexpiry_churn.get('normal_taker_fill_with_recovery_fill_within_window_ratio')):.4f},"
            + "held_preexpiry_sec="
            + f"{_safe_float(preexpiry_churn.get('observed_held_preexpiry_reduce_only_sec_max')):.2f},"
            + "min_new_exposure_sec="
            + f"{_safe_float(preexpiry_churn.get('observed_min_sec_to_expiry_for_new_exposure_max')):.2f}"
        ),
        (
            "valuation_truth="
            + "bruise_state="
            + str(valuation_truth.get("valuation_bruise_state") or "unknown")
            + ","
            + f"degraded_ratio={_safe_float(valuation_truth.get('valuation_degraded_ratio')):.4f},"
            + f"hard_degraded_ratio={_safe_float(valuation_truth.get('valuation_hard_degraded_ratio')):.4f},"
            + f"held_book_not_found_404_ratio={_safe_float(valuation_truth.get('held_book_not_found_404_ratio')):.4f},"
            + f"preexpiry_404_anomaly_ratio={_safe_float(valuation_truth.get('preexpiry_404_anomaly_ratio')):.4f},"
            + f"held_unpriceable_escalation_ratio={_safe_float(valuation_truth.get('held_unpriceable_escalation_ratio')):.4f},"
            + f"held_unpriceable_defect_candidate_ratio={_safe_float(valuation_truth.get('held_unpriceable_defect_candidate_ratio')):.4f},"
            + f"held_dust_shadow_active_ratio={_safe_float(valuation_truth.get('held_dust_shadow_active_ratio')):.4f},"
            + f"held_dust_enforced_ratio={_safe_float(valuation_truth.get('held_dust_enforced_ratio')):.4f},"
            + f"hard_degraded_enter_count={int(_safe_float(valuation_truth.get('valuation_hard_degraded_enter_count')))},"
            + f"hard_degraded_clear_count={int(_safe_float(valuation_truth.get('valuation_hard_degraded_clear_count')))},"
            + f"held_unpriceable_started_count={int(_safe_float(valuation_truth.get('held_unpriceable_started_count')))},"
            + f"held_unpriceable_recovered_count={int(_safe_float(valuation_truth.get('held_unpriceable_recovered_count')))},"
            + f"lifecycle_context_mismatch_count={int(_safe_float(valuation_truth.get('lifecycle_context_mismatch_count')))},"
            + f"lifecycle_context_missing_sec_to_expiry_count={int(_safe_float(valuation_truth.get('lifecycle_context_missing_sec_to_expiry_count')))},"
            + f"preexpiry_emergency_taker_attempt_count={int(_safe_float(valuation_truth.get('preexpiry_emergency_taker_attempt_count')))},"
            + f"preexpiry_emergency_taker_fill_count={int(_safe_float(valuation_truth.get('preexpiry_emergency_taker_fill_count')))},"
            + f"preexpiry_emergency_taker_block_count={int(_safe_float(valuation_truth.get('preexpiry_emergency_taker_block_count')))},"
            + f"held_dust_hard_degraded_exempt_count={int(_safe_float(valuation_truth.get('held_dust_hard_degraded_exempt_count')))},"
            + f"dominant_reason_family_run={json.dumps(str(valuation_truth.get('valuation_dominant_reason_family_run') or 'none'), sort_keys=True)},"
            + f"dominant_held_cause_run={json.dumps(str(valuation_truth.get('valuation_dominant_held_unpriceable_cause_run') or 'none'), sort_keys=True)},"
            + f"dominant_source_degraded_rows={json.dumps(str(valuation_truth.get('valuation_dominant_source_degraded_rows') or 'none'), sort_keys=True)},"
            + f"latest_reasons={json.dumps(valuation_truth.get('latest_valuation_degraded_reasons', []), sort_keys=True)},"
            + f"latest_escalation_reasons={json.dumps(valuation_truth.get('latest_held_unpriceable_escalation_reasons', []), sort_keys=True)},"
            + f"latest_operator_action={json.dumps(str(valuation_truth.get('latest_held_unpriceable_operator_action') or ''), sort_keys=True)},"
            + f"held_unpriceable_cause_counts_latest={json.dumps(valuation_truth.get('held_unpriceable_cause_counts_latest', {}), sort_keys=True)},"
            + f"valuation_degraded_reason_family_counts_run={json.dumps(valuation_truth.get('valuation_degraded_reason_family_counts_run', {}), sort_keys=True)},"
            + f"preexpiry_emergency_taker_block_reasons_run_max={json.dumps(valuation_truth.get('preexpiry_emergency_taker_block_reasons_run_max', {}), sort_keys=True)},"
            + f"source_counts_degraded_rows={json.dumps(valuation_truth.get('valuation_source_counts_degraded_rows', {}), sort_keys=True)},"
            + f"source_counts_run={json.dumps(valuation_truth.get('valuation_source_counts_run', {}), sort_keys=True)}"
        ),
        f"taker_stage_net_breakout={json.dumps(taker_stage_net, sort_keys=True)}",
        f"runtime_classification={runtime_class_name or 'UNKNOWN'}",
        f"runtime_promotion_eligible={1 if runtime_promotable else 0}",
        f"primary_suppression_cause={primary_suppression_cause}",
        f"suppression_dominated_run={1 if suppression_dominated_run else 0}",
        f"execution_starvation_mode={starvation_mode}",
        (
            "inferred_suppression_reason="
            + str(report.get("inferred_suppression_reason") or "none")
            + f":count={int(_safe_float(report.get('inferred_suppression_reason_count')))}"
        ),
        f"mode_transition_count={len(mode_transitions)}",
    ]
    if mode_transitions:
        last = mode_transitions[-1]
        lines.append(
            "last_mode_transition="
            + f"{last.get('ts_utc')}:{last.get('previous_state')}->{last.get('state')}:{last.get('reason')}"
        )
    return "\n".join(lines) + "\n"


def _artifact_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _maker_zero_submit_specimen_manifest(
    *,
    report_dir: pathlib.Path,
    support_artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    key_artifacts = {
        "maker_participation_waterfall": dict(support_artifacts.get("maker_participation_waterfall") or {}),
        "maker_quote_starvation_summary": dict(support_artifacts.get("maker_quote_starvation_summary") or {}),
        "maker_truth_reference_starvation_summary": dict(
            support_artifacts.get("maker_truth_reference_starvation_summary") or {}
        ),
        "maker_quote_construction_summary": dict(
            support_artifacts.get("maker_quote_construction_summary") or {}
        ),
        "maker_timing_band_diagnostic_matrix": dict(
            support_artifacts.get("maker_timing_band_diagnostic_matrix") or {}
        ),
        "maker_zero_submit_root_cause_audit": dict(
            support_artifacts.get("maker_zero_submit_root_cause_audit") or {}
        ),
        "maker_timing_band_decision": dict(support_artifacts.get("maker_timing_band_decision") or {}),
    }
    waterfall = key_artifacts["maker_participation_waterfall"]
    quote_starvation = key_artifacts["maker_quote_starvation_summary"]
    root_cause = key_artifacts["maker_zero_submit_root_cause_audit"]
    return {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "run_id": str(report_dir.name),
        "profile_name": str(report_dir.parent.parent.name),
        "report_path": str(report_dir),
        "artifact_names": sorted(
            [
                "maker_participation_waterfall.json",
                "maker_quote_starvation_summary.json",
                "maker_truth_reference_starvation_summary.json",
                "maker_quote_construction_summary.json",
                "maker_timing_band_diagnostic_matrix.json",
                "maker_timing_band_decision.json",
                "maker_zero_submit_root_cause_audit.json",
            ]
        ),
        "row_count_anchors": {
            "maker_rows_total": int(waterfall.get("reconciliation", {}).get("maker_rows_total", 0)),
            "stage_band_allowed_rows": int(
                waterfall.get("stages", {}).get("stage_band_allowed_rows", {}).get("count", 0)
            ),
            "prequote_prereq_pass_rows": int(
                waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get("count", 0)
            ),
            "quote_starvation_rows": int(
                quote_starvation.get("quote_starvation_row_count", quote_starvation.get("row_count", 0))
            ),
            "shadow_row_count": int(root_cause.get("shadow_row_count", 0)),
            "off_band_full_cannon_candidate_count": int(
                root_cause.get("off_band_full_cannon_candidate_count", 0)
            ),
        },
        "artifact_sha256": {key: _artifact_sha256(value) for key, value in key_artifacts.items()},
    }


def _load_json_if_exists(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _maker_zero_submit_specimen_snapshot(
    *,
    run_id: str,
    profile_name: str,
    root_cause: Dict[str, Any],
    waterfall: Dict[str, Any],
    quote_starvation: Dict[str, Any],
    timing_decision: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "profile_name": profile_name,
        "zero_submit_classification": root_cause.get("zero_submit_classification"),
        "decision_readiness": root_cause.get("decision_readiness"),
        "maker_rows_total": int(waterfall.get("reconciliation", {}).get("maker_rows_total", 0)),
        "stage_band_allowed_rows": int(
            waterfall.get("stages", {}).get("stage_band_allowed_rows", {}).get("count", 0)
        ),
        "prequote_prereq_pass_rows": int(
            waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get("count", 0)
        ),
        "desired_quote_missing_rows": int(
            waterfall.get("stages", {}).get("desired_quote_missing_rows", {}).get("count", 0)
        ),
        "quote_starvation_row_count": int(
            quote_starvation.get("quote_starvation_row_count", quote_starvation.get("row_count", 0))
        ),
        "shadow_row_count": int(root_cause.get("shadow_row_count", 0)),
        "shadow_selection_rejected_row_count": int(
            root_cause.get("shadow_selection_rejected_row_count", 0)
        ),
        "probe_matched_selection_rejected_row_count": int(
            root_cause.get("probe_matched_selection_rejected_row_count", 0)
        ),
        "off_band_full_cannon_candidate_count": int(
            root_cause.get("off_band_full_cannon_candidate_count", 0)
        ),
        "timing_recommendation": timing_decision.get("recommended_timing_action")
        or timing_decision.get("recommended_action"),
    }


def _maker_zero_submit_named_specimens(
    *,
    report_dir: pathlib.Path,
    support_artifacts: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    run_id = str(report_dir.name)
    current_label = MAKER_ZERO_SUBMIT_SPECIMEN_LABEL_BY_RUN_ID.get(run_id)
    if not current_label:
        return {}
    logs_root = report_dir.parent.parent.parent
    specimens: Dict[str, Dict[str, Any]] = {
        current_label: _maker_zero_submit_specimen_snapshot(
            run_id=run_id,
            profile_name=str(manifest.get("profile_name") or ""),
            root_cause=dict(support_artifacts.get("maker_zero_submit_root_cause_audit") or {}),
            waterfall=dict(support_artifacts.get("maker_participation_waterfall") or {}),
            quote_starvation=dict(support_artifacts.get("maker_quote_starvation_summary") or {}),
            timing_decision=dict(support_artifacts.get("maker_timing_band_decision") or {}),
        )
    }
    for peer_run_id in MAKER_ZERO_SUBMIT_SPECIMEN_RUN_IDS:
        if peer_run_id == run_id:
            continue
        peer_label = MAKER_ZERO_SUBMIT_SPECIMEN_LABEL_BY_RUN_ID.get(peer_run_id)
        if not peer_label:
            continue
        matches = list(logs_root.glob(f"*/reports/{peer_run_id}"))
        if not matches:
            continue
        peer_report_dir = matches[0]
        peer_manifest = _load_json_if_exists(peer_report_dir / "maker_zero_submit_specimen_manifest.json")
        peer_root_cause = _load_json_if_exists(peer_report_dir / "maker_zero_submit_root_cause_audit.json")
        peer_waterfall = _load_json_if_exists(peer_report_dir / "maker_participation_waterfall.json")
        peer_quote_starvation = _load_json_if_exists(peer_report_dir / "maker_quote_starvation_summary.json")
        peer_timing_decision = _load_json_if_exists(peer_report_dir / "maker_timing_band_decision.json")
        if not (peer_root_cause and peer_waterfall):
            continue
        specimens[peer_label] = _maker_zero_submit_specimen_snapshot(
            run_id=peer_run_id,
            profile_name=str(peer_manifest.get("profile_name") or ""),
            root_cause=peer_root_cause,
            waterfall=peer_waterfall,
            quote_starvation=peer_quote_starvation,
            timing_decision=peer_timing_decision,
        )
    return specimens


def _maker_zero_submit_specimen_comparison(
    *,
    report_dir: pathlib.Path,
    support_artifacts: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    run_id = str(report_dir.name)
    current_label = MAKER_ZERO_SUBMIT_SPECIMEN_LABEL_BY_RUN_ID.get(run_id)
    specimens = _maker_zero_submit_named_specimens(
        report_dir=report_dir,
        support_artifacts=support_artifacts,
        manifest=manifest,
    )
    peer_run_id = next((rid for rid in MAKER_ZERO_SUBMIT_SPECIMEN_RUN_IDS if rid != run_id), None)
    peer_label = MAKER_ZERO_SUBMIT_SPECIMEN_LABEL_BY_RUN_ID.get(peer_run_id or "")
    comparison = {
        "maker_zero_submit_audit_version": int(MAKER_ZERO_SUBMIT_AUDIT_VERSION),
        "current_run_id": run_id,
        "peer_run_id": peer_run_id,
        "current_run_focus": current_label,
        "comparison_ready": bool(
            current_label and peer_label and current_label in specimens and peer_label in specimens
        ),
        "specimens": specimens,
    }
    if current_label and current_label in specimens:
        comparison["current"] = specimens[current_label]
    if peer_label and peer_label in specimens:
        comparison["peer"] = specimens[peer_label]
    return comparison


def _maker_quote_integrity_event_ts(row: Dict[str, Any]) -> Optional[dt.datetime]:
    return parse_ts(
        row.get("ts_event_utc")
        or row.get("ts_decision_utc")
        or row.get("timestamp_utc")
        or row.get("ts_utc")
    )


def _maker_quote_integrity_event_ts_text(row: Dict[str, Any]) -> Optional[str]:
    for key in ("ts_event_utc", "ts_decision_utc", "timestamp_utc", "ts_utc"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _maker_quote_crosses_touch(
    *,
    side: Any,
    price: Any,
    best_bid_price: Any,
    best_ask_price: Any,
) -> Optional[bool]:
    if not isinstance(price, (int, float)):
        return None
    norm_side = str(side or "").strip().upper()
    if norm_side == "SELL" and isinstance(best_bid_price, (int, float)):
        return bool(float(price) <= float(best_bid_price) + 1e-12)
    if norm_side == "BUY" and isinstance(best_ask_price, (int, float)):
        return bool(float(price) >= float(best_ask_price) - 1e-12)
    return None


def _maker_price_tick_delta(price_a: Any, price_b: Any, tick_size: Any) -> Optional[float]:
    if not isinstance(price_a, (int, float)) or not isinstance(price_b, (int, float)):
        return None
    tick = float(tick_size) if isinstance(tick_size, (int, float)) and float(tick_size) > 0.0 else 0.001
    return abs(float(price_a) - float(price_b)) / tick


def _maker_depth_multiple_for_price(
    *,
    price: Any,
    visible_depth_shares: Any,
    cannon_target_notional_usd: Any,
) -> Optional[float]:
    if not (
        isinstance(price, (int, float))
        and isinstance(visible_depth_shares, (int, float))
        and isinstance(cannon_target_notional_usd, (int, float))
        and float(cannon_target_notional_usd) > 0.0
    ):
        return None
    return float(price) * float(visible_depth_shares) / float(cannon_target_notional_usd)


def _maker_quote_integrity_tick_size(run_manifest: Dict[str, Any]) -> float:
    config = run_manifest.get("config") if isinstance(run_manifest, dict) else {}
    strategy = config.get("strategy") if isinstance(config, dict) else {}
    tick_size = strategy.get("tick_size") if isinstance(strategy, dict) else None
    return float(tick_size) if isinstance(tick_size, (int, float)) and float(tick_size) > 0.0 else 0.001


def _maker_execution_quality_cfg(run_manifest: Dict[str, Any]) -> Dict[str, Any]:
    config = run_manifest.get("config") if isinstance(run_manifest, dict) else {}
    strategy = config.get("strategy") if isinstance(config, dict) else {}
    execution_quality = (
        strategy.get("execution_quality") if isinstance(strategy, dict) else {}
    )
    return dict(execution_quality) if isinstance(execution_quality, dict) else {}


def _nearest_event_for_quote_integrity(
    rows: List[Dict[str, Any]],
    *,
    pivot_ts: Optional[dt.datetime],
    predicate,
    max_delta_sec: float,
    direction: str = "both",
) -> Optional[Dict[str, Any]]:
    if pivot_ts is None:
        return None
    best_row: Optional[Dict[str, Any]] = None
    best_abs_delta: Optional[float] = None
    for row in rows:
        if not predicate(row):
            continue
        candidate_ts = _maker_quote_integrity_event_ts(row)
        if candidate_ts is None:
            continue
        delta = (candidate_ts - pivot_ts).total_seconds()
        if direction == "before" and delta > 1e-9:
            continue
        if direction == "after" and delta < -1e-9:
            continue
        abs_delta = abs(delta)
        if abs_delta > max_delta_sec:
            continue
        if best_abs_delta is None or abs_delta < best_abs_delta:
            best_abs_delta = abs_delta
            best_row = row
    return best_row


def _maker_next_shadow_after_submit(
    *,
    shadow_index: Dict[str, List[Tuple[dt.datetime, Dict[str, Any]]]],
    target_side_ref: str,
    submit_ts: Optional[dt.datetime],
    max_delta_sec: float = 3.0,
) -> Optional[Dict[str, Any]]:
    if submit_ts is None:
        return None
    candidates = shadow_index.get(str(target_side_ref or "").strip(), [])
    for candidate_ts, row in candidates:
        delta = (candidate_ts - submit_ts).total_seconds()
        if delta <= 1e-9:
            continue
        if delta > max_delta_sec:
            break
        if str(row.get("decision_result") or "").strip().lower() == "submitted":
            continue
        return row
    return None


def _maker_quality_snapshot_from_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "expected_fill_prob": (
            float(row.get("expected_fill_prob"))
            if isinstance(row.get("expected_fill_prob"), (int, float))
            else None
        ),
        "queue_ahead_size": (
            float(row.get("queue_ahead_size"))
            if isinstance(row.get("queue_ahead_size"), (int, float))
            else None
        ),
        "distance_to_touch": (
            float(row.get("distance_to_touch"))
            if isinstance(row.get("distance_to_touch"), (int, float))
            else None
        ),
    }


def _maker_quality_equal(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    for key in ("expected_fill_prob", "queue_ahead_size", "distance_to_touch"):
        left_val = left.get(key)
        right_val = right.get(key)
        if left_val is None and right_val is None:
            continue
        if not isinstance(left_val, (int, float)) or not isinstance(right_val, (int, float)):
            return False
        if abs(float(left_val) - float(right_val)) > 1e-12:
            return False
    return True


def _maker_quality_equal_fillprob_queue(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    for key in ("expected_fill_prob", "queue_ahead_size"):
        left_val = left.get(key)
        right_val = right.get(key)
        if not isinstance(left_val, (int, float)) or not isinstance(right_val, (int, float)):
            return False
        if abs(float(left_val) - float(right_val)) > 1e-12:
            return False
    return True


def _maker_quote_mutation_materiality(trace_row: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    model_plane = dict(trace_row.get("model_plane") or {})
    quote_plane = dict(trace_row.get("quote_plane") or {})
    mutation_flags = dict(quote_plane.get("mutation_flags") or {})
    certified_price = model_plane.get("desired_quote_price")
    submitted_price = quote_plane.get("submitted_price")
    tick_delta = quote_plane.get("certified_to_submitted_tick_delta")
    certified_crosses_touch = quote_plane.get("certified_quote_crosses_touch")
    submitted_crosses_touch = quote_plane.get("submitted_quote_crosses_touch")
    observed_quality_equal = bool(quote_plane.get("observed_quality_metrics_equal", False))
    observed_fillprob_queue_equal = bool(
        quote_plane.get("observed_fillprob_and_queue_equal", False)
    )
    shadow_depth_multiple = model_plane.get("depth_multiple_vs_cannon_target")
    submitted_depth_multiple = quote_plane.get("submitted_quote_depth_multiple_vs_cannon_target")
    depth_semantics_differ = False
    if isinstance(shadow_depth_multiple, (int, float)) and isinstance(submitted_depth_multiple, (int, float)):
        depth_semantics_differ = (
            (float(shadow_depth_multiple) >= MAKER_CANNON_MIN_DEPTH_MULTIPLE)
            != (float(submitted_depth_multiple) >= MAKER_CANNON_MIN_DEPTH_MULTIPLE)
        )
    crossing_transition = (
        certified_crosses_touch is True and submitted_crosses_touch is False
    )
    quality_blind_mutation = (
        isinstance(certified_price, (int, float))
        and isinstance(submitted_price, (int, float))
        and abs(float(certified_price) - float(submitted_price)) > 1e-12
        and (observed_quality_equal or observed_fillprob_queue_equal)
    )

    material = bool(
        crossing_transition
        or depth_semantics_differ
        or quality_blind_mutation
        or (isinstance(tick_delta, (int, float)) and float(tick_delta) >= 1.0)
    )
    queue_mutated = bool(mutation_flags.get("queue_pressure_mutated_quote", False))
    cross_guard_mutated = bool(mutation_flags.get("cross_guard_mutated_quote", False))

    if not queue_mutated and not cross_guard_mutated:
        mutation_class = "none"
    elif queue_mutated and cross_guard_mutated:
        mutation_class = "material_multi_step_mutation" if material else "minor_non_material"
    elif cross_guard_mutated:
        mutation_class = "material_cross_guard_only" if material else "minor_non_material"
    elif queue_mutated:
        mutation_class = "material_queue_pressure_only" if material else "minor_non_material"
    else:
        mutation_class = "minor_non_material"

    return mutation_class, {
        "certified_to_submitted_tick_delta": tick_delta,
        "crossing_transition": crossing_transition,
        "depth_semantics_differ": depth_semantics_differ,
        "observed_quality_metrics_equal": observed_quality_equal,
        "observed_fillprob_and_queue_equal": observed_fillprob_queue_equal,
        "quality_blind_mutation": quality_blind_mutation,
        "material": material,
    }


def _maker_survival_counterfactual_summary(
    trace_row: Dict[str, Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    survival_plane = dict(trace_row.get("survival_plane") or {})
    cancel_reason = str(survival_plane.get("cancel_reason") or "").strip().lower()
    cancel_class = str(survival_plane.get("cancel_class") or "").strip().lower()
    next_reject_reason = str(survival_plane.get("next_cycle_selection_reject_reason") or "").strip().lower()
    next_reject_reasons = [
        str(reason or "").strip().lower()
        for reason in list(survival_plane.get("next_cycle_selection_reject_reasons") or [])
        if str(reason or "").strip()
    ]
    counterfactuals = dict(survival_plane.get("counterfactuals") or {})
    cf_a = dict(counterfactuals.get("counterfactual_a_certified_quote") or {})
    cf_b = dict(counterfactuals.get("counterfactual_b_resting_submitted_quote") or {})
    cf_c = dict(counterfactuals.get("counterfactual_c_entry_gate_only") or {})

    certified_met = cf_a.get("depth_requirement_met")
    resting_met = cf_b.get("depth_requirement_met")
    entry_gate_only_cancel_required = cf_c.get("cancel_would_still_be_required")

    if not cancel_reason:
        return None, {
            "counterfactual_a_certified_quote": cf_a,
            "counterfactual_b_resting_submitted_quote": cf_b,
            "counterfactual_c_entry_gate_only": cf_c,
        }

    if cancel_reason == "commitment_window_ended" or cancel_class == "terminal_window_end":
        return "terminal_commitment_window_end_cleanup", {
            "counterfactual_a_certified_quote": cf_a,
            "counterfactual_b_resting_submitted_quote": cf_b,
            "counterfactual_c_entry_gate_only": cf_c,
        }

    only_depth_revalidation = bool(
        cancel_reason == "launch_safe_selection_reject"
        and next_reject_reason == "insufficient_depth_multiple"
        and all(reason == "insufficient_depth_multiple" for reason in next_reject_reasons or [next_reject_reason])
    )
    if entry_gate_only_cancel_required is None and only_depth_revalidation:
        cf_c["cancel_would_still_be_required"] = False
        entry_gate_only_cancel_required = False

    if resting_met is True and certified_met is False:
        classification = "cancel_only_due_to_quote_reference_shift"
    elif only_depth_revalidation and entry_gate_only_cancel_required is False:
        classification = "cancel_only_due_to_aggressive_survival_policy"
    elif certified_met is False and resting_met is False:
        classification = "cancel_still_required"
    else:
        classification = "indeterminate_due_to_missing_truth"

    return classification, {
        "counterfactual_a_certified_quote": cf_a,
        "counterfactual_b_resting_submitted_quote": cf_b,
        "counterfactual_c_entry_gate_only": cf_c,
    }


def _quote_quality_to_dict(quality: Any) -> Dict[str, Any]:
    return {
        "expected_fill_prob": float(getattr(quality, "expected_fill_prob", 0.0)),
        "adverse_selection_risk": float(getattr(quality, "adverse_selection_risk", 0.0)),
        "expected_quality_score": float(getattr(quality, "expected_quality_score", 0.0)),
        "queue_ahead_size": float(getattr(quality, "queue_ahead_size", 0.0)),
        "distance_to_touch": float(getattr(quality, "distance_to_touch", 0.0)),
    }


def _maker_execution_quality_semantics_bundle(
    *,
    trace_rows: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    cfg = _maker_execution_quality_cfg(run_manifest)
    model = ExecutionQualityModel(cfg)
    specimen_comparisons: List[Dict[str, Any]] = []
    identical_material_pairs = 0
    identical_fillprob_queue_material_pairs = 0
    for row in trace_rows:
        model_plane = dict(row.get("model_plane") or {})
        quote_plane = dict(row.get("quote_plane") or {})
        certified_price = model_plane.get("desired_quote_price")
        submitted_price = quote_plane.get("submitted_price")
        comparison = {
            "order_id": row.get("order_id"),
            "target_side_ref": row.get("target_side_ref"),
            "certified_quote_price": certified_price,
            "submitted_quote_price": submitted_price,
            "certified_to_submitted_tick_delta": quote_plane.get("certified_to_submitted_tick_delta"),
            "certified_quality": {
                "expected_fill_prob": model_plane.get("expected_fill_prob"),
                "queue_ahead_size": model_plane.get("queue_ahead_size"),
                "distance_to_touch": model_plane.get("distance_to_touch"),
            },
            "submitted_quality": {
                "expected_fill_prob": quote_plane.get("submitted_expected_fill_prob"),
                "queue_ahead_size": quote_plane.get("submitted_queue_ahead_size"),
                "distance_to_touch": quote_plane.get("submitted_distance_to_touch"),
            },
            "quality_metrics_identical": bool(quote_plane.get("observed_quality_metrics_equal", False)),
            "fillprob_and_queue_identical": bool(
                quote_plane.get("observed_fillprob_and_queue_equal", False)
            ),
        }
        specimen_comparisons.append(comparison)
        if (
            bool(comparison["quality_metrics_identical"])
            and isinstance(certified_price, (int, float))
            and isinstance(submitted_price, (int, float))
            and abs(float(certified_price) - float(submitted_price)) > 1e-12
        ):
            identical_material_pairs += 1
        if (
            bool(comparison["fillprob_and_queue_identical"])
            and isinstance(certified_price, (int, float))
            and isinstance(submitted_price, (int, float))
            and abs(float(certified_price) - float(submitted_price)) > 1e-12
        ):
            identical_fillprob_queue_material_pairs += 1

    synthetic_top_sell = BookTop(
        token_id="synthetic-sell",
        ts_utc="2026-04-29T00:00:00Z",
        source="synthetic",
        best_bid_price=0.60,
        best_bid_size=100.0,
        best_ask_price=0.62,
        best_ask_size=120.0,
    )
    synthetic_top_buy = BookTop(
        token_id="synthetic-buy",
        ts_utc="2026-04-29T00:00:00Z",
        source="synthetic",
        best_bid_price=0.40,
        best_bid_size=140.0,
        best_ask_price=0.42,
        best_ask_size=110.0,
    )
    synthetic_examples = {
        "sell": [],
        "buy": [],
    }
    for label, price in (
        ("deep_inside_spread", 0.601),
        ("near_touch_inside_spread", 0.619),
        ("touch_or_cross_guard_line", 0.62),
    ):
        synthetic_examples["sell"].append(
            {
                "label": label,
                "price": price,
                "quality": _quote_quality_to_dict(
                    model.assess_quote(
                        intent=OrderIntent(
                            token_id="synthetic-sell",
                            side="SELL",
                            price=price,
                            size=10.0,
                        ),
                        top=synthetic_top_sell,
                    )
                ),
            }
        )
    for label, price in (
        ("deep_inside_spread", 0.401),
        ("near_touch_inside_spread", 0.419),
        ("touch_or_cross_guard_line", 0.42),
    ):
        synthetic_examples["buy"].append(
            {
                "label": label,
                "price": price,
                "quality": _quote_quality_to_dict(
                    model.assess_quote(
                        intent=OrderIntent(
                            token_id="synthetic-buy",
                            side="BUY",
                            price=price,
                            size=10.0,
                        ),
                        top=synthetic_top_buy,
                    )
                ),
            }
        )

    sell_inside_equal = False
    buy_inside_equal = False
    if len(synthetic_examples["sell"]) >= 2:
        left = synthetic_examples["sell"][0]["quality"]
        right = synthetic_examples["sell"][1]["quality"]
        sell_inside_equal = _maker_quality_equal(left, right)
    if len(synthetic_examples["buy"]) >= 2:
        left = synthetic_examples["buy"][0]["quality"]
        right = synthetic_examples["buy"][1]["quality"]
        buy_inside_equal = _maker_quality_equal(left, right)

    quality_model_semantics = "unknown"
    if identical_material_pairs > 0 or identical_fillprob_queue_material_pairs > 0 or sell_inside_equal or buy_inside_equal:
        quality_model_semantics = "inside_spread_blind_spot_present"
    elif trace_rows:
        quality_model_semantics = "distance_sensitive_inside_spread"

    return {
        "maker_quote_integrity_audit_version": int(MAKER_QUOTE_INTEGRITY_AUDIT_VERSION),
        "quality_model_semantics": quality_model_semantics,
        "specimen_comparisons": specimen_comparisons,
        "synthetic_truth_table": synthetic_examples,
        "synthetic_inside_spread_same_metrics": {
            "sell_inside_spread_prices_equal_quality": bool(sell_inside_equal),
            "buy_inside_spread_prices_equal_quality": bool(buy_inside_equal),
        },
        "inside_spread_blind_spot_present": bool(quality_model_semantics == "inside_spread_blind_spot_present"),
        "direct_observed_identical_material_pair_count": int(identical_material_pairs),
        "direct_observed_fillprob_queue_identical_material_pair_count": int(
            identical_fillprob_queue_material_pairs
        ),
    }


def _maker_quote_integrity_bundle(
    *,
    events: List[Dict[str, Any]],
    shadow_rows: List[Dict[str, Any]],
    run_manifest: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    tick_size = _maker_quote_integrity_tick_size(run_manifest)
    shadow_index = _maker_shadow_match_index(shadow_rows)
    run_profile_name = str(
        ((run_manifest.get("config") or {}).get("profile") or {}).get("name") or ""
    )
    consulted_event_types = list(MAKER_QUOTE_INTEGRITY_EVENT_TYPES)
    observed_event_counts: Dict[str, int] = {}
    events_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for event_type in consulted_event_types:
        filtered = [
            evt
            for evt in events
            if str(evt.get("event_type") or "").strip() == event_type
        ]
        filtered.sort(
            key=lambda item: (
                _maker_quote_integrity_event_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                str(item.get("order_id") or ""),
            )
        )
        events_by_type[event_type] = filtered
        observed_event_counts[event_type] = int(len(filtered))

    submit_events = {
        str(evt.get("order_id") or "").strip(): evt
        for evt in events_by_type.get("order_submit", [])
        if str(evt.get("order_id") or "").strip()
        and str(evt.get("submission_lane") or "").strip().lower() == "maker"
    }
    cancel_events_by_order_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    cancel_class_counts: Counter[str] = Counter()
    for evt in events_by_type.get("order_cancel", []):
        order_id = str(evt.get("order_id") or "").strip()
        if order_id:
            cancel_events_by_order_id[order_id].append(evt)
            cancel_submission_lane = str(evt.get("submission_lane") or "").strip().lower()
            if cancel_submission_lane == "maker" or order_id in submit_events:
                cancel_class = str(evt.get("cancel_class") or "").strip().lower() or "unknown"
                cancel_class_counts[cancel_class] += 1
    suppressed_cancel_reason_counts: Counter[str] = Counter()
    for evt in events_by_type.get("order_cancel_suppressed", []):
        if str(evt.get("submission_lane") or "").strip().lower() != "maker":
            continue
        requested_reason = str(evt.get("requested_cancel_reason") or "").strip().lower() or "unknown"
        suppressed_cancel_reason_counts[requested_reason] += 1
    edge_events = events_by_type.get("edge_evaluation", [])

    submitted_shadow_rows = [
        row
        for row in shadow_rows
        if str(row.get("decision_result") or "").strip().lower() == "submitted"
        and str(row.get("order_submit_id") or "").strip()
    ]
    submitted_shadow_rows.sort(
        key=lambda item: (
            _maker_quote_integrity_event_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            str(item.get("order_submit_id") or ""),
        )
    )

    trace_rows: List[Dict[str, Any]] = []
    mutation_rows: List[Dict[str, Any]] = []
    survival_rows: List[Dict[str, Any]] = []
    mutation_class_counts: Counter[str] = Counter()
    survival_class_counts: Counter[str] = Counter()

    for shadow_row in submitted_shadow_rows:
        order_id = str(shadow_row.get("order_submit_id") or "").strip()
        if not order_id:
            continue
        shadow_ts = _maker_quote_integrity_event_ts(shadow_row)
        submit_evt = submit_events.get(order_id)
        submit_ts = _maker_quote_integrity_event_ts(submit_evt or shadow_row)
        token_id = str(
            (submit_evt or {}).get("token_id")
            or shadow_row.get("token_id")
            or ""
        ).strip()
        side = str(
            (submit_evt or {}).get("side")
            or shadow_row.get("side")
            or ""
        ).strip().upper()
        cross_guard_evt = _nearest_event_for_quote_integrity(
            events_by_type.get("pre_submit_cross_guard_adjusted", []),
            pivot_ts=submit_ts,
            predicate=lambda evt: (
                str(evt.get("submission_lane") or "").strip().lower() == "maker"
                and str(evt.get("side") or "").strip().upper() == side
                and (
                    not token_id
                    or str(evt.get("token_id") or "").strip() == token_id
                )
            ),
            max_delta_sec=0.5,
            direction="before",
        )
        queue_pressure_evt = _nearest_event_for_quote_integrity(
            events_by_type.get("maker_queue_pressure_adjustment", []),
            pivot_ts=submit_ts,
            predicate=lambda evt: (
                str(evt.get("side") or "").strip().upper() == side
                and (
                    not token_id
                    or str(evt.get("token_id") or "").strip() == token_id
                )
            ),
            max_delta_sec=3.0,
            direction="before",
        )
        next_shadow = _maker_next_shadow_after_submit(
            shadow_index=shadow_index,
            target_side_ref=str(shadow_row.get("target_side_ref") or ""),
            submit_ts=submit_ts,
        )
        cancel_evt = _nearest_event_for_quote_integrity(
            cancel_events_by_order_id.get(order_id, []),
            pivot_ts=submit_ts,
            predicate=lambda evt: True,
            max_delta_sec=5.0,
            direction="after",
        )
        edge_evt = _nearest_event_for_quote_integrity(
            edge_events,
            pivot_ts=submit_ts,
            predicate=lambda evt: (
                str(evt.get("evaluation_scope") or "").strip().lower() == "maker"
                and (
                    str(evt.get("order_id") or "").strip() == order_id
                    or order_id in list(evt.get("submitted_order_ids") or [])
                )
            ),
            max_delta_sec=1.0,
            direction="both",
        )

        shadow_quality = _maker_quality_snapshot_from_event(shadow_row)
        submitted_quality = _maker_quality_snapshot_from_event(submit_evt or {})
        shadow_depth_multiple = (
            float(shadow_row.get("depth_multiple_vs_cannon_target"))
            if isinstance(shadow_row.get("depth_multiple_vs_cannon_target"), (int, float))
            else None
        )
        submitted_depth_multiple = _maker_depth_multiple_for_price(
            price=(submit_evt or {}).get("price"),
            visible_depth_shares=shadow_row.get("visible_depth_shares"),
            cannon_target_notional_usd=shadow_row.get("cannon_target_notional_usd"),
        )
        quote_plane = {
            "queue_pressure_base_price": (
                float(queue_pressure_evt.get("base_price"))
                if isinstance((queue_pressure_evt or {}).get("base_price"), (int, float))
                else None
            ),
            "queue_pressure_adjusted_price": (
                float(queue_pressure_evt.get("adjusted_price"))
                if isinstance((queue_pressure_evt or {}).get("adjusted_price"), (int, float))
                else None
            ),
            "pre_submit_cross_guard_original_price": (
                float(cross_guard_evt.get("original_price"))
                if isinstance((cross_guard_evt or {}).get("original_price"), (int, float))
                else None
            ),
            "pre_submit_cross_guard_adjusted_price": (
                float(cross_guard_evt.get("adjusted_price"))
                if isinstance((cross_guard_evt or {}).get("adjusted_price"), (int, float))
                else None
            ),
            "submitted_price": (
                float(submit_evt.get("price"))
                if isinstance((submit_evt or {}).get("price"), (int, float))
                else None
            ),
            "best_bid_price_at_clamp_time": (
                float(cross_guard_evt.get("best_bid_price"))
                if isinstance((cross_guard_evt or {}).get("best_bid_price"), (int, float))
                else None
            ),
            "best_ask_price_at_clamp_time": (
                float(cross_guard_evt.get("best_ask_price"))
                if isinstance((cross_guard_evt or {}).get("best_ask_price"), (int, float))
                else None
            ),
            "submitted_expected_fill_prob": submitted_quality.get("expected_fill_prob"),
            "submitted_queue_ahead_size": submitted_quality.get("queue_ahead_size"),
            "submitted_distance_to_touch": submitted_quality.get("distance_to_touch"),
            "submitted_quote_depth_multiple_vs_cannon_target": submitted_depth_multiple,
            "observed_quality_metrics_equal": bool(
                _maker_quality_equal(shadow_quality, submitted_quality)
            ),
            "observed_fillprob_and_queue_equal": bool(
                _maker_quality_equal_fillprob_queue(shadow_quality, submitted_quality)
            ),
        }
        quote_plane["mutation_flags"] = {
            "queue_pressure_mutated_quote": bool(
                isinstance(quote_plane["queue_pressure_base_price"], (int, float))
                and isinstance(quote_plane["queue_pressure_adjusted_price"], (int, float))
                and abs(
                    float(quote_plane["queue_pressure_base_price"])
                    - float(quote_plane["queue_pressure_adjusted_price"])
                )
                > 1e-12
            ),
            "cross_guard_mutated_quote": bool(
                isinstance(quote_plane["pre_submit_cross_guard_original_price"], (int, float))
                and isinstance(quote_plane["pre_submit_cross_guard_adjusted_price"], (int, float))
                and abs(
                    float(quote_plane["pre_submit_cross_guard_original_price"])
                    - float(quote_plane["pre_submit_cross_guard_adjusted_price"])
                )
                > 1e-12
            ),
            "submitted_quote_differs_from_certified_quote": bool(
                isinstance(quote_plane["submitted_price"], (int, float))
                and isinstance(shadow_row.get("desired_quote_price"), (int, float))
                and abs(
                    float(quote_plane["submitted_price"])
                    - float(shadow_row.get("desired_quote_price"))
                )
                > 1e-12
            ),
        }
        quote_plane["certified_to_submitted_tick_delta"] = _maker_price_tick_delta(
            shadow_row.get("desired_quote_price"),
            quote_plane.get("submitted_price"),
            tick_size,
        )
        quote_plane["certified_quote_crosses_touch"] = _maker_quote_crosses_touch(
            side=side,
            price=shadow_row.get("desired_quote_price"),
            best_bid_price=quote_plane.get("best_bid_price_at_clamp_time"),
            best_ask_price=quote_plane.get("best_ask_price_at_clamp_time"),
        )
        quote_plane["submitted_quote_crosses_touch"] = _maker_quote_crosses_touch(
            side=side,
            price=quote_plane.get("submitted_price"),
            best_bid_price=quote_plane.get("best_bid_price_at_clamp_time"),
            best_ask_price=quote_plane.get("best_ask_price_at_clamp_time"),
        )

        next_cycle_reject_reason = None
        next_cycle_reject_reasons: List[str] = []
        if isinstance(next_shadow, dict):
            next_cycle_reject_reason = _normalize_selection_reject_reason(
                next_shadow.get("selection_gate_primary_reject_reason")
                or next_shadow.get("decision_block_reason")
            )
            next_cycle_reject_reasons = [
                reason
                for reason in (
                    _normalize_selection_reject_reason(reason)
                    for reason in list(next_shadow.get("selection_gate_all_reject_reasons") or [])
                )
                if reason
            ]
        survival_plane = {
            "actual_resting_order_price": quote_plane.get("submitted_price"),
            "next_cycle_desired_quote_price": (
                float(next_shadow.get("desired_quote_price"))
                if isinstance((next_shadow or {}).get("desired_quote_price"), (int, float))
                else None
            ),
            "next_cycle_selection_reject_reason": next_cycle_reject_reason,
            "next_cycle_selection_reject_reasons": next_cycle_reject_reasons,
            "next_cycle_depth_multiple_vs_cannon_target": (
                float(next_shadow.get("depth_multiple_vs_cannon_target"))
                if isinstance((next_shadow or {}).get("depth_multiple_vs_cannon_target"), (int, float))
                else None
            ),
            "next_cycle_visible_depth_shares": (
                float(next_shadow.get("visible_depth_shares"))
                if isinstance((next_shadow or {}).get("visible_depth_shares"), (int, float))
                else None
            ),
            "next_cycle_same_target_side_submit_count_prior": (
                int(_safe_float(next_shadow.get("same_target_side_submit_count_prior")))
                if isinstance((next_shadow or {}).get("same_target_side_submit_count_prior"), (int, float))
                else None
            ),
            "next_cycle_replace_guard_would_block": (
                bool(next_shadow.get("replace_guard_would_block"))
                if isinstance((next_shadow or {}).get("replace_guard_would_block"), bool)
                else None
            ),
            "cancel_reason": str((cancel_evt or {}).get("reason") or "").strip() or None,
            "cancel_class": str((cancel_evt or {}).get("cancel_class") or "").strip() or None,
            "survival_quote_reference": (
                "rederived_desired_quote"
                if isinstance(next_shadow, dict)
                else "unknown"
            ),
            "evaluated_on_actual_resting_quote": False if isinstance(next_shadow, dict) else None,
            "resting_price_counterfactual_depth_multiple": _maker_depth_multiple_for_price(
                price=quote_plane.get("submitted_price"),
                visible_depth_shares=(next_shadow or {}).get("visible_depth_shares"),
                cannon_target_notional_usd=(
                    (next_shadow or {}).get("cannon_target_notional_usd")
                    or shadow_row.get("cannon_target_notional_usd")
                ),
            ),
            "counterfactuals": {
                "counterfactual_a_certified_quote": {
                    "depth_multiple": (
                        float(next_shadow.get("depth_multiple_vs_cannon_target"))
                        if isinstance((next_shadow or {}).get("depth_multiple_vs_cannon_target"), (int, float))
                        else None
                    ),
                    "depth_requirement_met": (
                        bool(
                            float(next_shadow.get("depth_multiple_vs_cannon_target"))
                            >= float(
                                (next_shadow or {}).get("cannon_min_depth_multiple")
                                or shadow_row.get("cannon_min_depth_multiple")
                                or MAKER_CANNON_MIN_DEPTH_MULTIPLE
                            )
                        )
                        if isinstance((next_shadow or {}).get("depth_multiple_vs_cannon_target"), (int, float))
                        else None
                    ),
                },
                "counterfactual_b_resting_submitted_quote": {
                    "depth_multiple": _maker_depth_multiple_for_price(
                        price=quote_plane.get("submitted_price"),
                        visible_depth_shares=(next_shadow or {}).get("visible_depth_shares"),
                        cannon_target_notional_usd=(
                            (next_shadow or {}).get("cannon_target_notional_usd")
                            or shadow_row.get("cannon_target_notional_usd")
                        ),
                    ),
                    "depth_requirement_met": None,
                },
                "counterfactual_c_entry_gate_only": {
                    "cancel_would_still_be_required": None,
                },
            },
        }
        cf_b_depth = survival_plane["counterfactuals"]["counterfactual_b_resting_submitted_quote"].get(
            "depth_multiple"
        )
        if isinstance(cf_b_depth, (int, float)):
            survival_plane["counterfactuals"]["counterfactual_b_resting_submitted_quote"][
                "depth_requirement_met"
            ] = bool(
                float(cf_b_depth)
                >= float(
                    (next_shadow or {}).get("cannon_min_depth_multiple")
                    or shadow_row.get("cannon_min_depth_multiple")
                    or MAKER_CANNON_MIN_DEPTH_MULTIPLE
                )
            )

        trace_row = {
            "run_id": run_id,
            "profile_name": run_profile_name,
            "order_id": order_id,
            "target_ref": shadow_row.get("target_ref"),
            "target_side_ref": shadow_row.get("target_side_ref"),
            "side": side,
            "logic_pathology_specimen_only": bool(run_id == MAKER_QUOTE_INTEGRITY_PRIMARY_RUN_ID),
            "event_linkage": {
                "certified_shadow_ts_utc": _maker_quote_integrity_event_ts_text(shadow_row),
                "submit_ts_utc": _maker_quote_integrity_event_ts_text(submit_evt or {}),
                "cross_guard_ts_utc": _maker_quote_integrity_event_ts_text(cross_guard_evt or {}),
                "queue_pressure_ts_utc": _maker_quote_integrity_event_ts_text(queue_pressure_evt or {}),
                "next_shadow_ts_utc": _maker_quote_integrity_event_ts_text(next_shadow or {}),
                "cancel_ts_utc": _maker_quote_integrity_event_ts_text(cancel_evt or {}),
                "edge_eval_ts_utc": _maker_quote_integrity_event_ts_text(edge_evt or {}),
            },
            "model_plane": {
                "desired_quote_price": (
                    float(shadow_row.get("desired_quote_price"))
                    if isinstance(shadow_row.get("desired_quote_price"), (int, float))
                    else None
                ),
                "expected_fill_prob": shadow_quality.get("expected_fill_prob"),
                "queue_ahead_size": shadow_quality.get("queue_ahead_size"),
                "distance_to_touch": shadow_quality.get("distance_to_touch"),
                "visible_depth_shares": (
                    float(shadow_row.get("visible_depth_shares"))
                    if isinstance(shadow_row.get("visible_depth_shares"), (int, float))
                    else None
                ),
                "depth_multiple_vs_cannon_target": shadow_depth_multiple,
            },
            "quote_plane": quote_plane,
            "survival_plane": survival_plane,
        }
        mutation_class, mutation_detail = _maker_quote_mutation_materiality(trace_row)
        trace_row["quote_plane"]["mutation_classification"] = mutation_class
        trace_row["quote_plane"]["mutation_detail"] = mutation_detail
        survival_classification, counterfactuals = _maker_survival_counterfactual_summary(trace_row)
        trace_row["survival_plane"]["counterfactuals"] = counterfactuals
        trace_row["survival_plane"]["survival_classification"] = survival_classification
        trace_rows.append(trace_row)

        mutation_rows.append(
            {
                "order_id": order_id,
                "target_side_ref": trace_row.get("target_side_ref"),
                "mutation_classification": mutation_class,
                **mutation_detail,
            }
        )
        mutation_class_counts[mutation_class] += 1

        if str((cancel_evt or {}).get("reason") or "").strip():
            survival_rows.append(
                {
                    "order_id": order_id,
                    "target_side_ref": trace_row.get("target_side_ref"),
                    "cancel_reason": str(cancel_evt.get("reason") or "").strip(),
                    "survival_classification": survival_classification,
                    "survival_quote_reference": survival_plane.get("survival_quote_reference"),
                    "next_cycle_selection_reject_reason": next_cycle_reject_reason,
                    "next_cycle_selection_reject_reasons": next_cycle_reject_reasons,
                    "counterfactuals": counterfactuals,
                    "resting_price_counterfactual_depth_multiple": survival_plane.get(
                        "resting_price_counterfactual_depth_multiple"
                    ),
                }
            )
            if survival_classification:
                survival_class_counts[survival_classification] += 1

    semantics = _maker_execution_quality_semantics_bundle(
        trace_rows=trace_rows,
        run_manifest=run_manifest,
    )
    dominant_mutation_class = (
        max(sorted(mutation_class_counts), key=lambda key: mutation_class_counts[key])
        if mutation_class_counts
        else "none"
    )
    dominant_survival_classification = (
        max(sorted(survival_class_counts), key=lambda key: survival_class_counts[key])
        if survival_class_counts
        else "none"
    )

    next_repair_lane = "D. Peak-hours confirmation specimen"
    next_repair_lane_reason = (
        "Model, quote, and survival planes came back internally consistent; peak-hours confirmation is the next clean proof step."
    )
    if semantics.get("quality_model_semantics") == "inside_spread_blind_spot_present":
        next_repair_lane = "A. Quality-model repair"
        next_repair_lane_reason = (
            "The execution-quality model treated materially different inside-spread quotes as equivalent, so downstream quote and survival judgments are not trustworthy enough yet."
        )
    elif dominant_mutation_class in {
        "material_cross_guard_only",
        "material_queue_pressure_only",
        "material_multi_step_mutation",
    }:
        next_repair_lane = "B. Quote-consistency repair"
        next_repair_lane_reason = (
            "The maker lane is certifying one quote and launching a materially different quote, which invalidates the current audit path."
        )
    elif dominant_survival_classification == "cancel_only_due_to_aggressive_survival_policy":
        next_repair_lane = "C. Resting-order survival repair"
        next_repair_lane_reason = (
            "Accepted maker orders are being canceled by next-cycle survival logic even when the core issue is post-entry depth revalidation, not entry-time invalidity."
        )

    specimen_anchor_ts = None
    if trace_rows:
        specimen_anchor_ts = parse_ts(
            trace_rows[0].get("event_linkage", {}).get("submit_ts_utc")
            or trace_rows[0].get("event_linkage", {}).get("certified_shadow_ts_utc")
        )
    specimen_local_central = specimen_anchor_ts.astimezone(ZoneInfo("America/Chicago")) if specimen_anchor_ts else None
    overnight_logic_specimen = bool(
        specimen_local_central is not None and 0 <= specimen_local_central.hour < 6
    )
    summary = {
        "maker_quote_integrity_audit_version": int(MAKER_QUOTE_INTEGRITY_AUDIT_VERSION),
        "primary_run_id": str(run_id),
        "logic_pathology_specimen_only": bool(run_id == MAKER_QUOTE_INTEGRITY_PRIMARY_RUN_ID),
        "specimen_regime_class": (
            "overnight_logic_specimen" if overnight_logic_specimen else "non_overnight_logic_specimen"
        ),
        "peak_hours_economic_conclusion_allowed": False,
        "specimen_local_time_central": (
            specimen_local_central.isoformat() if specimen_local_central is not None else None
        ),
        "accepted_maker_submit_count": int(len(trace_rows)),
        "accepted_then_canceled_count": int(len(survival_rows)),
        "suppressed_routine_cancel_request_count": int(sum(suppressed_cancel_reason_counts.values())),
        "quality_model_semantics": semantics.get("quality_model_semantics"),
        "dominant_quote_mutation_class": dominant_mutation_class,
        "dominant_survival_classification": dominant_survival_classification,
        "order_cancel_class_distribution": _counter_to_sorted_int_dict(cancel_class_counts),
        "order_cancel_suppressed_requested_reason_distribution": _counter_to_sorted_int_dict(
            suppressed_cancel_reason_counts
        ),
        "next_repair_lane": next_repair_lane,
        "next_repair_lane_reason": next_repair_lane_reason,
        "disclosures": [
            "The repaired $250 specimen occurred around 02:04 AM Central, so economic fill-rate and PnL conclusions remain non-authoritative.",
            "Logic findings from quote certification, launch mutation, execution-quality semantics, and submit-to-cancel survival remain authoritative.",
        ],
    }
    manifest = {
        "maker_quote_integrity_audit_version": int(MAKER_QUOTE_INTEGRITY_AUDIT_VERSION),
        "primary_run_id": str(run_id),
        "profile_name": run_profile_name,
        "consulted_event_types": consulted_event_types,
        "observed_event_type_counts": {
            key: int(observed_event_counts.get(key, 0)) for key in consulted_event_types
        },
        "used_existing_events_only": True,
        "required_minimal_runtime_fields": False,
        "logic_pathology_specimen_only": bool(run_id == MAKER_QUOTE_INTEGRITY_PRIMARY_RUN_ID),
        "peak_hours_economic_conclusion_allowed": False,
        "order_cancel_class_distribution": _counter_to_sorted_int_dict(cancel_class_counts),
        "order_cancel_suppressed_requested_reason_distribution": _counter_to_sorted_int_dict(
            suppressed_cancel_reason_counts
        ),
        "field_provenance": {
            "direct_logged_fields": [
                "desired_quote_price",
                "expected_fill_prob",
                "queue_ahead_size",
                "visible_depth_shares",
                "depth_multiple_vs_cannon_target",
                "pre_submit_cross_guard_original_price",
                "pre_submit_cross_guard_adjusted_price",
                "best_bid_price",
                "best_ask_price",
                "order_submit.price",
                "order_cancel.reason",
                "selection_gate_primary_reject_reason",
                "selection_gate_all_reject_reasons",
                "replace_guard_would_block",
                "same_target_side_submit_count_prior",
            ],
            "report_reconstructed_fields": [
                "submitted_quote_differs_from_certified_quote",
                "certified_to_submitted_tick_delta",
                "certified_quote_crosses_touch",
                "submitted_quote_crosses_touch",
                "submitted_quote_depth_multiple_vs_cannon_target",
                "resting_price_counterfactual_depth_multiple",
                "quote_mutation_classification",
                "survival_classification",
                "next_repair_lane",
            ],
        },
    }
    mutation_summary = {
        "maker_quote_integrity_audit_version": int(MAKER_QUOTE_INTEGRITY_AUDIT_VERSION),
        "row_count": int(len(mutation_rows)),
        "mutation_classification_distribution": _counter_to_sorted_int_dict(mutation_class_counts),
        "dominant_mutation_classification": dominant_mutation_class,
        "rows": mutation_rows,
    }
    survival_audit = {
        "maker_quote_integrity_audit_version": int(MAKER_QUOTE_INTEGRITY_AUDIT_VERSION),
        "row_count": int(len(survival_rows)),
        "survival_classification_distribution": _counter_to_sorted_int_dict(survival_class_counts),
        "dominant_survival_classification": dominant_survival_classification,
        "rows": survival_rows,
    }
    return {
        "manifest": manifest,
        "trace_rows": trace_rows,
        "execution_quality_semantics": semantics,
        "quote_mutation_summary": mutation_summary,
        "resting_order_survival_audit": survival_audit,
        "summary": summary,
    }


def _write_support_artifacts(report_dir: pathlib.Path, support_artifacts: Dict[str, Any]) -> None:
    rows = _normalized_shadow_support_rows(
        list(support_artifacts.get("maker_fight_admission_shadow_rows") or [])
    )
    summary = dict(support_artifacts.get("maker_fight_admission_shadow_summary") or {})
    calibration_audit = dict(support_artifacts.get("maker_fight_admission_calibration_audit") or {})
    cannon_probe_rows = list(support_artifacts.get("maker_cannon_late_window_probe_rows") or [])
    cannon_probe_summary = dict(support_artifacts.get("maker_cannon_late_window_probe_summary") or {})
    mid_window_probe_rows = list(support_artifacts.get("maker_mid_window_probe_rows") or [])
    mid_window_probe_summary = dict(support_artifacts.get("maker_mid_window_probe_summary") or {})
    quote_starvation_rows = list(support_artifacts.get("maker_quote_starvation_audit_rows") or [])
    quote_starvation_summary = dict(support_artifacts.get("maker_quote_starvation_summary") or {})
    truth_reference_rows = list(support_artifacts.get("maker_truth_reference_starvation_rows") or [])
    truth_reference_summary = dict(
        support_artifacts.get("maker_truth_reference_starvation_summary") or {}
    )
    quote_construction_rows = list(support_artifacts.get("maker_quote_construction_audit_rows") or [])
    quote_construction_summary = dict(support_artifacts.get("maker_quote_construction_summary") or {})
    maker_participation_waterfall = dict(support_artifacts.get("maker_participation_waterfall") or {})
    maker_timing_band_diagnostic_matrix = dict(
        support_artifacts.get("maker_timing_band_diagnostic_matrix") or {}
    )
    maker_timing_band_decision = dict(support_artifacts.get("maker_timing_band_decision") or {})
    maker_zero_submit_root_cause_audit = dict(
        support_artifacts.get("maker_zero_submit_root_cause_audit") or {}
    )
    maker_quote_integrity_manifest = dict(
        support_artifacts.get("maker_quote_integrity_manifest") or {}
    )
    maker_quote_integrity_trace_rows = list(
        support_artifacts.get("maker_quote_integrity_trace_rows") or []
    )
    maker_execution_quality_semantics = dict(
        support_artifacts.get("maker_execution_quality_semantics") or {}
    )
    maker_quote_mutation_summary = dict(
        support_artifacts.get("maker_quote_mutation_summary") or {}
    )
    maker_resting_order_survival_audit = dict(
        support_artifacts.get("maker_resting_order_survival_audit") or {}
    )
    maker_quote_integrity_summary = dict(
        support_artifacts.get("maker_quote_integrity_summary") or {}
    )
    maker_selection_authority_audit = dict(
        support_artifacts.get("maker_selection_authority_audit") or {}
    )
    maker_selection_authority_counterfactual = dict(
        support_artifacts.get("maker_selection_authority_counterfactual") or {}
    )
    maker_submit_count = sum(
        1 for row in rows if str(row.get("decision_result") or "").strip().lower() == "submitted"
    )
    if maker_submit_count > 0:
        for artifact in (
            maker_participation_waterfall,
            quote_construction_summary,
            truth_reference_summary,
            calibration_audit,
        ):
            artifact["authoritative_for_canonical_selection"] = False
            artifact["applicability"] = "descriptive_only"

    rows_path = report_dir / "maker_fight_admission_shadow.jsonl"
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    (report_dir / "maker_fight_admission_shadow_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_fight_admission_calibration_audit.json").write_text(
        json.dumps(calibration_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cannon_probe_rows_path = report_dir / "maker_cannon_late_window_probe.jsonl"
    with cannon_probe_rows_path.open("w", encoding="utf-8") as handle:
        for row in cannon_probe_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_cannon_late_window_probe_summary.json").write_text(
        json.dumps(cannon_probe_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    mid_window_probe_rows_path = report_dir / "maker_mid_window_probe.jsonl"
    with mid_window_probe_rows_path.open("w", encoding="utf-8") as handle:
        for row in mid_window_probe_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_mid_window_probe_summary.json").write_text(
        json.dumps(mid_window_probe_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    quote_starvation_rows_path = report_dir / "maker_quote_starvation_audit.jsonl"
    with quote_starvation_rows_path.open("w", encoding="utf-8") as handle:
        for row in quote_starvation_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_quote_starvation_summary.json").write_text(
        json.dumps(quote_starvation_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    truth_reference_rows_path = report_dir / "maker_truth_reference_starvation_audit.jsonl"
    with truth_reference_rows_path.open("w", encoding="utf-8") as handle:
        for row in truth_reference_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_truth_reference_starvation_summary.json").write_text(
        json.dumps(truth_reference_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    quote_construction_rows_path = report_dir / "maker_quote_construction_audit.jsonl"
    with quote_construction_rows_path.open("w", encoding="utf-8") as handle:
        for row in quote_construction_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_quote_construction_summary.json").write_text(
        json.dumps(quote_construction_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_participation_waterfall.json").write_text(
        json.dumps(maker_participation_waterfall, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_timing_band_diagnostic_matrix.json").write_text(
        json.dumps(maker_timing_band_diagnostic_matrix, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_timing_band_decision.json").write_text(
        json.dumps(maker_timing_band_decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_zero_submit_root_cause_audit.json").write_text(
        json.dumps(maker_zero_submit_root_cause_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_quote_integrity_manifest.json").write_text(
        json.dumps(maker_quote_integrity_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    quote_integrity_trace_path = report_dir / "maker_quote_integrity_trace.jsonl"
    with quote_integrity_trace_path.open("w", encoding="utf-8") as handle:
        for row in maker_quote_integrity_trace_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (report_dir / "maker_execution_quality_semantics.json").write_text(
        json.dumps(maker_execution_quality_semantics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_quote_mutation_summary.json").write_text(
        json.dumps(maker_quote_mutation_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_resting_order_survival_audit.json").write_text(
        json.dumps(maker_resting_order_survival_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_quote_integrity_summary.json").write_text(
        json.dumps(maker_quote_integrity_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_selection_authority_audit.json").write_text(
        json.dumps(maker_selection_authority_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "maker_selection_authority_counterfactual.json").write_text(
        json.dumps(maker_selection_authority_counterfactual, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    specimen_manifest = _maker_zero_submit_specimen_manifest(
        report_dir=report_dir,
        support_artifacts=support_artifacts,
    )
    named_specimens = _maker_zero_submit_named_specimens(
        report_dir=report_dir,
        support_artifacts=support_artifacts,
        manifest=specimen_manifest,
    )
    if named_specimens:
        maker_zero_submit_root_cause_audit["known_truths"] = {
            "packet_b_350": {
                "quote_starvation_row_count": int(
                    named_specimens.get("packet_b_350", {}).get("quote_starvation_row_count", 0)
                )
                if "packet_b_350" in named_specimens
                else None,
                "shadow_row_count": int(
                    named_specimens.get("packet_b_350", {}).get("shadow_row_count", 0)
                )
                if "packet_b_350" in named_specimens
                else None,
            },
            "caliber_250": {
                "quote_starvation_row_count": int(
                    named_specimens.get("caliber_250", {}).get("quote_starvation_row_count", 0)
                )
                if "caliber_250" in named_specimens
                else None,
                "shadow_selection_rejected_row_count": int(
                    named_specimens.get("caliber_250", {}).get(
                        "shadow_selection_rejected_row_count", 0
                    )
                )
                if "caliber_250" in named_specimens
                else None,
                "off_band_full_cannon_candidate_count": int(
                    named_specimens.get("caliber_250", {}).get(
                        "off_band_full_cannon_candidate_count", 0
                    )
                )
                if "caliber_250" in named_specimens
                else None,
            },
            "row_universe_caveats": list(maker_zero_submit_root_cause_audit.get("known_truths", {}).get("row_universe_caveats", [])),
        }
        (report_dir / "maker_zero_submit_root_cause_audit.json").write_text(
            json.dumps(maker_zero_submit_root_cause_audit, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (report_dir / "maker_zero_submit_specimen_manifest.json").write_text(
        json.dumps(specimen_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    specimen_comparison = _maker_zero_submit_specimen_comparison(
        report_dir=report_dir,
        support_artifacts=support_artifacts,
        manifest=specimen_manifest,
    )
    (report_dir / "maker_zero_submit_specimen_comparison.json").write_text(
        json.dumps(specimen_comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro nightly soak report")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument("--summary-out", default="", help="Optional human-readable summary path")
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
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file; set 0 for full-file scans",
    )
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve()
    run_id = str(args.run_id).strip() or None
    report = build_report(
        log_dir,
        run_id=run_id,
        auto_resolve_run_id=False,
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        include_support_artifacts=bool(str(args.out).strip()),
    )
    support_artifacts = report.pop("_support_artifacts", None)
    summary_text = render_human_summary(report)

    json_out_path: Optional[pathlib.Path] = None
    if args.out:
        json_out_path = pathlib.Path(args.out).resolve()
        json_out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if isinstance(support_artifacts, dict):
            _write_support_artifacts(json_out_path.parent, support_artifacts)

    summary_out = str(args.summary_out).strip()
    if summary_out:
        summary_path = pathlib.Path(summary_out).resolve()
    elif json_out_path is not None:
        summary_path = json_out_path.with_suffix(".txt")
    else:
        summary_path = None
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary_text, encoding="utf-8")

    print(summary_text, end="")


if __name__ == "__main__":
    main()
