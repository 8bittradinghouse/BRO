#!/usr/bin/env python3
"""Unified paper-soak hardening gate from a budget policy file."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Optional

import yaml


from prodesk.artifact_identity import build_artifact_identity
from prodesk.error_codes import summarize_error_codes
from prodesk.reporting import decision_item
from prodesk.session_phase import enforce_validation_phase
from prodesk.runtime_semantics import (
    RUNTIME_CLASS_INVALID_DEADLOCK,
    RUNTIME_CLASS_INVALID_SAFETY,
    RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION,
)
from scripts.nightly_soak_report import build_report
from scripts.performance_budget_gate import run_gate as run_performance_budget_gate
from scripts.readiness_gate import (
    _comparison_tolerance_payload as _readiness_comparison_tolerance_payload,
    _load_policy,
    _metric_epsilon as _readiness_metric_epsilon,
    run_readiness_gate,
)
from scripts.run_integrity_audit import run_audit as run_integrity_audit
from scripts.websocket_reliability_gate import run_gate as run_websocket_reliability_gate

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SOAK_BUDGET_PATH = (ROOT_DIR / "ops" / "soak_budget.yaml").resolve()
DEFAULT_RAMP_POLICY_PATH = (ROOT_DIR / "ops" / "ramp_policy.yaml").resolve()


def _f(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _i(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_budget(path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("budget file root must be a mapping")
    return payload


def _lane_for_finding(finding: str) -> str:
    text = str(finding or "")
    reliability_prefixes = (
        "status_rows_below_min:",
        "latest_status_ts_missing",
        "latest_status_stale:",
        "status_ts_",
        "status_counter_non_monotonic:",
        "websocket_",
        "soak_runtime_invalid:",
        "soak_quote_uptime_",
        "soak_active_target_execution_participation_missing:",
        "soak_book_updates_",
        "soak_execution_quality_",
        "performance_cycle_",
        "performance_process_rss_",
        "performance_latency_inactive_cycles_",
    )
    utilization_prefixes = (
        "soak_duration_",
        "soak_readiness_",
        "soak_error_rows_",
        "soak_maker_submits_",
        "soak_maker_fill_rate_",
        "soak_taker_bonus_submits_",
        "soak_taker_bonus_fills_",
        "soak_taker_bonus_fill_rate_",
        "performance_order_capacity_",
        "performance_cancel_capacity_",
        "soak_runtime_non_promotable:",
    )
    if text.startswith(reliability_prefixes):
        return "reliability"
    if text.startswith(utilization_prefixes):
        return "utilization"
    return "reliability"


def _passes_min(value: float, threshold: float, eps: float) -> bool:
    return float(value) + max(0.0, float(eps)) >= float(threshold)


def _passes_max(value: float, threshold: float, eps: float) -> bool:
    return float(value) <= float(threshold) + max(0.0, float(eps))


def _metric_epsilon(
    metric: str,
    *,
    kind: str,
    default_min_eps: float,
    default_max_eps: float,
    metric_eps_cfg: Dict[str, Any],
) -> float:
    metric_name = str(metric or "").strip()
    payload = metric_eps_cfg.get(metric_name)
    if isinstance(payload, dict):
        key = "min_eps" if kind == "min" else "max_eps"
        value = payload.get(key)
        return max(0.0, _f(value, default_min_eps if kind == "min" else default_max_eps))
    if isinstance(payload, (int, float)):
        return max(0.0, float(payload))
    return max(0.0, default_min_eps if kind == "min" else default_max_eps)


def _resolve_repo_owned_path(path: pathlib.Path, *, repo_default: pathlib.Path) -> pathlib.Path:
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    if str(path).strip():
        return (ROOT_DIR / path).resolve()
    return repo_default.resolve()


def run_gate(
    *,
    log_dir: pathlib.Path,
    run_id: Optional[str],
    budget_path: pathlib.Path,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="soak_hardening_gate", session_phase=session_phase)
    resolved_budget_path = _resolve_repo_owned_path(pathlib.Path(budget_path), repo_default=DEFAULT_SOAK_BUDGET_PATH)
    budget = _load_budget(resolved_budget_path)
    findings: List[str] = []
    decision_trace: List[Dict[str, Any]] = []

    integrity_cfg = dict(budget.get("integrity", {}) or {})
    perf_cfg = dict(budget.get("performance", {}) or {})
    ws_cfg = dict(budget.get("websocket", {}) or {})
    readiness_cfg = dict(budget.get("readiness", {}) or {})
    soak_cfg = dict(budget.get("soak", {}) or {})
    comparison_cfg = dict(budget.get("comparison_tolerance", {}) or {})
    metric_eps_cfg = dict(comparison_cfg.get("metrics", {}) or {})
    default_min_eps = max(0.0, _f(comparison_cfg.get("min_default"), 0.0))
    default_max_eps = max(0.0, _f(comparison_cfg.get("max_default"), 0.0))
    gate_mode = str(budget.get("gate_mode", "both")).strip().lower() or "both"

    resolved_log_dir = log_dir.resolve()
    selected_run_id = str(run_id or "").strip() or None
    if not selected_run_id:
        selected_run_id = None
        findings.append("soak_gate_run_id_required")

    integrity = run_integrity_audit(
        log_dir=resolved_log_dir,
        run_id=(selected_run_id or ""),
        min_status_rows=max(1, _i(integrity_cfg.get("min_status_rows"), 20)),
        status_tail_lines=max(20, _i(integrity_cfg.get("status_tail_lines"), 1000)),
        event_tail_lines=max(20, _i(integrity_cfg.get("event_tail_lines"), 1000)),
        max_status_age_sec=max(1.0, _f(integrity_cfg.get("max_status_age_sec"), 180.0)),
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )
    findings.extend(str(x) for x in integrity.get("findings", []))

    performance = run_performance_budget_gate(
        log_dir=resolved_log_dir,
        run_id=selected_run_id,
        max_cycle_latency_p95_ms=max(0.0, _f(perf_cfg.get("max_cycle_latency_p95_ms"), 800.0)),
        max_cycle_latency_max_ms=max(0.0, _f(perf_cfg.get("max_cycle_latency_max_ms"), 2000.0)),
        max_process_rss_mb=max(0.0, _f(perf_cfg.get("max_process_rss_mb"), 1024.0)),
        max_order_capacity_used_ratio=max(0.0, _f(perf_cfg.get("max_order_capacity_used_ratio"), 0.98)),
        max_cancel_capacity_used_ratio=max(0.0, _f(perf_cfg.get("max_cancel_capacity_used_ratio"), 0.98)),
        max_order_capacity_breach_rows=max(0, _i(perf_cfg.get("max_order_capacity_breach_rows"), 0)),
        max_cancel_capacity_breach_rows=max(0, _i(perf_cfg.get("max_cancel_capacity_breach_rows"), 0)),
        max_order_capacity_breach_ratio=max(0.0, _f(perf_cfg.get("max_order_capacity_breach_ratio"), 0.0)),
        max_cancel_capacity_breach_ratio=max(0.0, _f(perf_cfg.get("max_cancel_capacity_breach_ratio"), 0.0)),
        max_latency_inactive_cycles=max(0.0, _f(perf_cfg.get("max_latency_inactive_cycles"), 60.0)),
        max_market_data_span_ms=max(0.0, _f(perf_cfg.get("max_market_data_span_ms"), 2000.0)),
        max_strategy_exec_span_ms=max(0.0, _f(perf_cfg.get("max_strategy_exec_span_ms"), 2000.0)),
        max_state_io_span_ms=max(0.0, _f(perf_cfg.get("max_state_io_span_ms"), 1000.0)),
        max_status_io_span_ms=max(0.0, _f(perf_cfg.get("max_status_io_span_ms"), 1000.0)),
        max_cycle_residual_span_ms=max(0.0, _f(perf_cfg.get("max_cycle_residual_span_ms"), 1000.0)),
        min_status_rows=max(1, _i(perf_cfg.get("min_status_rows"), 20)),
    )
    findings.extend(str(x) for x in performance.get("findings", []))

    websocket = run_websocket_reliability_gate(
        log_dir=resolved_log_dir,
        run_id=selected_run_id,
        min_status_rows=max(1, _i(ws_cfg.get("min_status_rows"), 20)),
        max_book_feed_down_ratio=max(0.0, _f(ws_cfg.get("max_book_feed_down_ratio"), 0.20)),
        max_chainlink_down_ratio=max(0.0, _f(ws_cfg.get("max_chainlink_down_ratio"), 0.20)),
        max_book_feed_reconnects_per_hour=max(0.0, _f(ws_cfg.get("max_book_feed_reconnects_per_hour"), 40.0)),
        max_chainlink_reconnects_per_hour=max(0.0, _f(ws_cfg.get("max_chainlink_reconnects_per_hour"), 40.0)),
        max_book_feed_last_msg_age_sec=max(0.0, _f(ws_cfg.get("max_book_feed_last_msg_age_sec"), 12.0)),
        max_chainlink_last_tick_age_sec=max(0.0, _f(ws_cfg.get("max_chainlink_last_tick_age_sec"), 30.0)),
        max_book_feed_last_msg_age_spike_rows=max(0, _i(ws_cfg.get("max_book_feed_last_msg_age_spike_rows"), 0)),
        max_chainlink_last_tick_age_spike_rows=max(0, _i(ws_cfg.get("max_chainlink_last_tick_age_spike_rows"), 0)),
        max_book_feed_last_msg_age_spike_ratio=max(0.0, _f(ws_cfg.get("max_book_feed_last_msg_age_spike_ratio"), 0.0)),
        max_chainlink_last_tick_age_spike_ratio=max(0.0, _f(ws_cfg.get("max_chainlink_last_tick_age_spike_ratio"), 0.0)),
        max_book_feed_last_msg_age_p95_sec=max(0.0, _f(ws_cfg.get("max_book_feed_last_msg_age_p95_sec"), 8.0)),
        max_chainlink_last_tick_age_p95_sec=max(0.0, _f(ws_cfg.get("max_chainlink_last_tick_age_p95_sec"), 12.0)),
        max_chainlink_dropped_ticks=max(0.0, _f(ws_cfg.get("max_chainlink_dropped_ticks"), 0.0)),
        max_chainlink_queue_size=max(0.0, _f(ws_cfg.get("max_chainlink_queue_size"), 10000.0)),
        max_lines_per_file=max(
            0,
            _i(
                ws_cfg.get("max_lines_per_file"),
                _i(soak_cfg.get("report_max_lines_per_file"), 50000),
            ),
        ),
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )
    findings.extend(str(x) for x in websocket.get("findings", []))

    policy_path = _resolve_repo_owned_path(
        pathlib.Path(str(readiness_cfg.get("policy", "ops/ramp_policy.yaml"))),
        repo_default=DEFAULT_RAMP_POLICY_PATH,
    )
    required_stage = str(readiness_cfg.get("required_stage", "paper")).strip() or "paper"
    readiness_lines = max(
        0,
        _i(
            readiness_cfg.get("report_max_lines_per_file"),
            _i(soak_cfg.get("report_max_lines_per_file"), 50000),
        ),
    )
    policy = _load_policy(policy_path)
    readiness = run_readiness_gate(
        log_dir=resolved_log_dir,
        policy=policy,
        run_id=selected_run_id,
        report_max_lines_per_file=readiness_lines,
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )
    stage_order = list(policy.get("stage_order", []))
    readiness_comparison_cfg = _readiness_comparison_tolerance_payload(policy)
    highest = str(readiness.get("highest_passing_stage") or "")
    readiness_runtime_findings = [
        str(finding).strip()
        for finding in list(readiness.get("runtime_findings", []) or [])
        if str(finding).strip()
    ]
    readiness_required_stage_failure_causes: List[str] = []
    for stage_result in list(readiness.get("stage_results", []) or []):
        if str(stage_result.get("stage") or "").strip() != required_stage:
            continue
        for check in list(stage_result.get("checks", []) or []):
            if bool(check.get("passed", False)):
                continue
            criterion = str(check.get("criterion") or "").strip()
            if criterion:
                readiness_required_stage_failure_causes.append(criterion)
    if required_stage in stage_order:
        if (highest not in stage_order) or (stage_order.index(highest) < stage_order.index(required_stage)):
            causes_list = sorted(set(readiness_required_stage_failure_causes))
            if not causes_list and readiness_runtime_findings:
                causes_list = sorted(set(readiness_runtime_findings))
            causes = ",".join(causes_list) or "unknown"
            findings.append(
                f"soak_readiness_below_required_stage:required={required_stage}:highest={highest or 'none'}:causes={causes}"
            )
    decision_trace.append(
        decision_item(
            check="readiness_required_stage",
            level="hard_fail",
            metric="readiness_stage_met",
            comparator="min",
            value=1.0
            if ((required_stage not in stage_order) or (highest in stage_order and stage_order.index(highest) >= stage_order.index(required_stage)))
            else 0.0,
            threshold=1.0,
            passed=((required_stage not in stage_order) or (highest in stage_order and stage_order.index(highest) >= stage_order.index(required_stage))),
            note=(
                f"required={required_stage} highest={highest or 'none'} "
                + f"causes={','.join(sorted(set(readiness_required_stage_failure_causes))) or 'unknown'}"
            ),
        )
    )

    # Default to bounded deterministic scan while preserving an explicit 0=full-scan override.
    report_tail = max(0, _i(soak_cfg.get("report_max_lines_per_file"), 50000))
    report = build_report(
        resolved_log_dir,
        run_id=selected_run_id,
        max_lines_per_file=report_tail,
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )
    runtime_classification = report.get("runtime_classification", {})
    if not isinstance(runtime_classification, dict):
        runtime_classification = {}
    runtime_metrics = runtime_classification.get("metrics", {})
    if not isinstance(runtime_metrics, dict):
        runtime_metrics = {}
    runtime_classification_name = str(runtime_classification.get("classification") or "").strip().upper()
    runtime_promotion_eligible = bool(runtime_classification.get("promotion_eligible", False))
    runtime_primary_suppression_cause = str(
        report.get("primary_suppression_cause") or runtime_classification.get("primary_suppression_cause") or "none"
    ).strip() or "none"
    runtime_contributing_suppression_causes = report.get("contributing_suppression_causes", [])
    if not isinstance(runtime_contributing_suppression_causes, list):
        runtime_contributing_suppression_causes = []
    runtime_ambiguous_suppression_cause = bool(
        report.get("ambiguous_suppression_cause", runtime_classification.get("ambiguous_suppression_cause", False))
    )
    suppression_dominated_run = bool(report.get("suppression_dominated_run", False))
    execution_starvation_mode = str(report.get("execution_starvation_mode") or "unknown").strip() or "unknown"
    protected_no_trade_explanation = str(report.get("protected_no_trade_explanation") or "").strip()
    control_authority_clarity = report.get("control_authority_clarity", {})
    if not isinstance(control_authority_clarity, dict):
        control_authority_clarity = {}
    protection_path_trigger_chain = report.get("protection_path_trigger_chain", {})
    if not isinstance(protection_path_trigger_chain, dict):
        protection_path_trigger_chain = {}
    runtime_active_targets_seen = _f(runtime_metrics.get("active_targets_seen"), 0.0)
    runtime_meaningful_participation = _f(runtime_metrics.get("meaningful_participation"), 0.0)
    if runtime_classification_name in {RUNTIME_CLASS_INVALID_DEADLOCK, RUNTIME_CLASS_INVALID_SAFETY}:
        findings.append(f"soak_runtime_invalid:{runtime_classification_name}")
    if not runtime_promotion_eligible:
        if runtime_classification_name == RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION:
            findings.append(f"soak_runtime_non_promotable:{runtime_classification_name}")
        elif runtime_classification_name:
            findings.append(f"soak_runtime_non_promotable:{runtime_classification_name}")
        else:
            findings.append("soak_runtime_non_promotable:UNKNOWN")
    if runtime_active_targets_seen >= 0.5 and runtime_meaningful_participation < 0.5:
        findings.append(
            "soak_active_target_execution_participation_missing:"
            + f"active_targets_seen={runtime_active_targets_seen:.6f}:"
            + f"meaningful_participation={runtime_meaningful_participation:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="runtime_classification_promotion_eligibility",
            level="hard_fail",
            metric="runtime_promotion_eligible",
            comparator="eq",
            value=1.0 if runtime_promotion_eligible else 0.0,
            threshold=1.0,
            passed=bool(runtime_promotion_eligible),
            note=f"classification={runtime_classification_name or 'UNKNOWN'}",
        )
    )
    decision_trace.append(
        decision_item(
            check="runtime_active_target_meaningful_participation",
            level="hard_fail",
            metric="meaningful_participation_when_active_targets_seen",
            comparator="eq",
            value=1.0
            if (runtime_active_targets_seen < 0.5 or runtime_meaningful_participation >= 0.5)
            else 0.0,
            threshold=1.0,
            passed=(runtime_active_targets_seen < 0.5 or runtime_meaningful_participation >= 0.5),
            note=(
                f"active_targets_seen={runtime_active_targets_seen:.6f} "
                + f"meaningful_participation={runtime_meaningful_participation:.6f}"
            ),
        )
    )
    min_duration = _f(soak_cfg.get("min_duration_minutes"), 20.0)
    min_uptime = _f(soak_cfg.get("min_quote_uptime_ratio"), 0.5)
    max_errors = _f(soak_cfg.get("max_error_rows"), 0.0)
    min_maker_submits = _f(soak_cfg.get("min_maker_submits"), 0.0)
    max_maker_fill_rate = _f(soak_cfg.get("max_maker_fill_rate"), 1.0)
    maker_fill_rate_enforcement_min_submits = max(
        0.0,
        _f(soak_cfg.get("maker_fill_rate_enforcement_min_submits"), 5.0),
    )
    min_taker_bonus_submits = _f(soak_cfg.get("min_taker_bonus_submits"), 0.0)
    min_taker_bonus_fills = _f(soak_cfg.get("min_taker_bonus_fills"), 0.0)
    max_taker_bonus_fill_rate = _f(soak_cfg.get("max_taker_bonus_fill_rate"), 1.0)
    min_execution_quality_capture_minus_adverse_raw = soak_cfg.get(
        "min_execution_quality_capture_minus_adverse"
    )
    execution_quality_floor_enabled = (
        min_execution_quality_capture_minus_adverse_raw is not None
        and str(min_execution_quality_capture_minus_adverse_raw).strip() != ""
    )
    min_execution_quality_capture_minus_adverse = _f(
        min_execution_quality_capture_minus_adverse_raw,
        0.0,
    )
    max_preexpiry_ws_missing_or_unusable_anomaly_count = _f(
        soak_cfg.get("max_preexpiry_ws_missing_or_unusable_anomaly_count"),
        0.0,
    )
    max_lifecycle_context_mismatch_count = _f(soak_cfg.get("max_lifecycle_context_mismatch_count"), 0.0)
    max_lifecycle_context_missing_sec_to_expiry_count = _f(
        soak_cfg.get("max_lifecycle_context_missing_sec_to_expiry_count"),
        0.0,
    )
    max_valuation_hard_degraded_unrecovered_count = _f(
        soak_cfg.get("max_valuation_hard_degraded_unrecovered_count"),
        1_000_000.0,
    )
    max_held_unpriceable_unrecovered_count = _f(
        soak_cfg.get("max_held_unpriceable_unrecovered_count"),
        1_000_000.0,
    )
    min_book_updates_ws_delta = _f(ws_cfg.get("min_book_updates_ws_delta"), 0.0)
    min_book_updates_total_delta = _f(ws_cfg.get("min_book_updates_total_delta"), 0.0)
    duration = _f(report.get("duration_minutes"), 0.0)
    uptime = _f(report.get("quote_uptime_ratio"), 0.0)
    errors = _f(report.get("error_rows"), 0.0)
    paths = dict(report.get("execution_paths", {}) or {})
    quote_diagnostics = dict(report.get("quote_diagnostics", {}) or {})
    execution_quality = dict(report.get("execution_quality", {}) or {})
    market_data_source = dict(report.get("market_data_source", {}) or {})
    valuation_truth = dict(report.get("valuation_truth", {}) or {})
    maker_submits = _f(paths.get("maker_submits"), 0.0)
    maker_fill_rate = _f(paths.get("maker_fill_rate"), 0.0)
    taker_bonus_submits = _f(paths.get("taker_bonus_submits"), 0.0)
    taker_bonus_fills = _f(paths.get("taker_bonus_fills"), 0.0)
    taker_bonus_fill_rate = _f(paths.get("taker_bonus_fill_rate"), 0.0)
    execution_quality_capture_minus_adverse = _f(
        execution_quality.get(
            "capture_minus_adverse",
            execution_quality.get("immediate_capture_minus_adverse"),
        ),
        0.0,
    )
    execution_quality_capture_minus_adverse_source = "execution_quality.capture_minus_adverse"
    decision_reference_lane_attribution = dict(
        report.get("execution_quality_decision_reference_lane_attribution", {}) or {}
    )
    decision_reference_lane_total = dict(decision_reference_lane_attribution.get("total", {}) or {})
    if _f(decision_reference_lane_total.get("immediate_fills_scored"), 0.0) > 0.0:
        execution_quality_capture_minus_adverse = _f(
            decision_reference_lane_total.get("immediate_capture_minus_adverse"),
            execution_quality_capture_minus_adverse,
        )
        execution_quality_capture_minus_adverse_source = (
            "execution_quality_decision_reference_lane_attribution.total."
            "immediate_capture_minus_adverse"
    )
    book_updates_ws_delta = _f(market_data_source.get("book_updates_ws_delta"), 0.0)
    book_updates_total_delta = _f(market_data_source.get("book_updates_total_delta"), 0.0)
    pair_truth_missing_pair_row_ratio = _f(market_data_source.get("pair_truth_missing_pair_row_ratio"), 0.0)
    pair_truth_missing_pair_count_max = _f(market_data_source.get("pair_truth_missing_pair_count_max"), 0.0)
    pair_truth_one_sided_row_ratio = _f(market_data_source.get("pair_truth_one_sided_row_ratio"), 0.0)
    preexpiry_ws_missing_or_unusable_anomaly_count = _f(
        valuation_truth.get("preexpiry_ws_missing_or_unusable_anomaly_count"),
        0.0,
    )
    lifecycle_context_mismatch_count = _f(valuation_truth.get("lifecycle_context_mismatch_count"), 0.0)
    lifecycle_context_missing_sec_to_expiry_count = _f(
        valuation_truth.get("lifecycle_context_missing_sec_to_expiry_count"),
        0.0,
    )
    settlement_hold_required_count = _f(valuation_truth.get("settlement_hold_required_count"), 0.0)
    open_order_cleanup_required_count = _f(valuation_truth.get("open_order_cleanup_required_count"), 0.0)
    unresolved_lifecycle_obligation_count = _f(
        valuation_truth.get("unresolved_lifecycle_obligation_count"),
        0.0,
    )
    cancel_fail_closed_count = _f(valuation_truth.get("cancel_fail_closed_count"), 0.0)
    valuation_hard_degraded_enter_count = _f(valuation_truth.get("valuation_hard_degraded_enter_count"), 0.0)
    valuation_hard_degraded_clear_count = _f(valuation_truth.get("valuation_hard_degraded_clear_count"), 0.0)
    held_unpriceable_started_count = _f(valuation_truth.get("held_unpriceable_started_count"), 0.0)
    held_unpriceable_recovered_count = _f(valuation_truth.get("held_unpriceable_recovered_count"), 0.0)
    valuation_hard_degraded_unrecovered_count = max(
        0.0,
        valuation_hard_degraded_enter_count - valuation_hard_degraded_clear_count,
    )
    held_unpriceable_unrecovered_raw_count = max(
        0.0,
        held_unpriceable_started_count - held_unpriceable_recovered_count,
    )
    held_unpriceable_unrecovered_non_defect_count = _f(
        valuation_truth.get("held_unpriceable_unrecovered_non_defect_count"),
        _f(
            valuation_truth.get("held_unpriceable_unrecovered_dust_exempted_count"),
            0.0,
        ),
    )
    held_unpriceable_unrecovered_count = _f(
        valuation_truth.get("held_unpriceable_unrecovered_meaningful_count"),
        max(
            0.0,
            held_unpriceable_unrecovered_raw_count
            - held_unpriceable_unrecovered_non_defect_count,
        ),
    )
    quote_uptime_applicable = bool(quote_diagnostics.get("quote_uptime_applicable", False))

    # Explicit, machine-verifiable maker opportunity policy.
    maker_enforcement_cfg = dict(soak_cfg.get("maker_submit_enforcement", {}) or {})
    maker_enforcement_mode = str(maker_enforcement_cfg.get("mode", "absolute")).strip().lower() or "absolute"
    min_maker_opportunity_rows = max(0.0, _f(maker_enforcement_cfg.get("min_opportunity_rows"), 1.0))
    edge_truth = report.get("edge_truth", {})
    if not isinstance(edge_truth, dict):
        edge_truth = {}
    maker_block_reason_distribution = edge_truth.get("maker_block_reason_distribution")
    maker_scope_reason_surface_present = isinstance(maker_block_reason_distribution, dict)
    if not maker_scope_reason_surface_present:
        maker_block_reason_distribution = {}
    maker_rows_surface_present = isinstance(edge_truth.get("maker_rows"), (int, float))
    default_non_actionable_reasons = [
        "maker_no_submission",
        "maker_timing_gate_closed",
        "phase_disallow_maker",
        "fair_probability_missing",
        "market_probability_missing",
        "time_remaining_sec_invalid",
        "oracle_unavailable_or_stale",
        "missing_expiry_metadata",
        "missing_threshold_metadata",
        "missing_side_metadata",
        "open_order_cleanup_required",
        "settlement_hold_required",
        "unresolved_lifecycle_obligation",
        "cancel_fail_closed",
    ]
    configured_non_actionable = maker_enforcement_cfg.get("non_actionable_block_reasons")
    if isinstance(configured_non_actionable, list):
        non_actionable_reasons = [str(x).strip() for x in configured_non_actionable if str(x).strip()]
    else:
        non_actionable_reasons = list(default_non_actionable_reasons)
    maker_rows_total = max(0.0, _f(edge_truth.get("maker_rows"), 0.0))
    maker_non_actionable_block_rows = 0.0
    for reason in non_actionable_reasons:
        maker_non_actionable_block_rows += max(0.0, _f(maker_block_reason_distribution.get(reason), 0.0))
    maker_non_actionable_block_rows = min(maker_non_actionable_block_rows, maker_rows_total)
    maker_actionable_opportunity_rows = max(0.0, maker_rows_total - maker_non_actionable_block_rows)
    maker_opportunity_surface_missing: List[str] = []
    if not maker_rows_surface_present:
        maker_opportunity_surface_missing.append("edge_truth.maker_rows")
    if not maker_scope_reason_surface_present:
        maker_opportunity_surface_missing.append("edge_truth.maker_block_reason_distribution")
    maker_opportunity_surface_ok = len(maker_opportunity_surface_missing) == 0
    if maker_enforcement_mode == "absolute":
        maker_min_enforcement_applied = True
        maker_min_enforcement_reason = "absolute_mode"
        maker_submits_required = min_maker_submits
    elif maker_enforcement_mode == "opportunity_aware":
        if maker_opportunity_surface_ok:
            maker_min_enforcement_applied = maker_actionable_opportunity_rows >= min_maker_opportunity_rows
            maker_min_enforcement_reason = (
                "opportunity_rows_met" if maker_min_enforcement_applied else "insufficient_actionable_opportunity_rows"
            )
            maker_submits_required = min(min_maker_submits, maker_actionable_opportunity_rows)
        else:
            findings.append(
                "soak_maker_opportunity_surface_unverifiable:"
                + ",".join(sorted(maker_opportunity_surface_missing))
            )
            maker_min_enforcement_applied = True
            maker_min_enforcement_reason = "unverifiable_surface_fail_closed"
            maker_submits_required = min_maker_submits
    else:
        findings.append(f"soak_maker_submit_enforcement_mode_invalid:{maker_enforcement_mode}")
        maker_min_enforcement_applied = True
        maker_min_enforcement_reason = "invalid_mode_fail_closed"
        maker_submits_required = min_maker_submits

    duration_min_eps = _metric_epsilon(
        "duration_minutes",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    duration_pass = _passes_min(duration, min_duration, duration_min_eps)
    if not duration_pass:
        findings.append(f"soak_duration_too_short:{duration:.6f}<min:{min_duration:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_duration_minutes",
            level="hard_fail",
            metric="duration_minutes",
            comparator="min",
            value=duration,
            threshold=min_duration,
            passed=duration_pass,
            note=f"minimum soak time eps={duration_min_eps:.6f}",
        )
    )
    uptime_min_eps = _readiness_metric_epsilon(
        "quote_uptime_ratio",
        kind="min",
        comparison_cfg=readiness_comparison_cfg,
    )
    uptime_pass = (not quote_uptime_applicable) or _passes_min(uptime, min_uptime, uptime_min_eps)
    if quote_uptime_applicable and not uptime_pass:
        findings.append(f"soak_quote_uptime_too_low:{uptime:.6f}<min:{min_uptime:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_quote_uptime_ratio",
            level="hard_fail",
            metric="quote_uptime_ratio",
            comparator="min",
            value=uptime,
            threshold=min_uptime,
            passed=uptime_pass,
            note=(
                f"minimum uptime eps={uptime_min_eps:.6f} "
                + f"applicable={1 if quote_uptime_applicable else 0}"
            ),
        )
    )
    error_max_eps = _metric_epsilon(
        "error_rows",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    errors_pass = _passes_max(errors, max_errors, error_max_eps)
    if not errors_pass:
        findings.append(f"soak_error_rows_too_high:{errors:.6f}>max:{max_errors:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_error_rows",
            level="hard_fail",
            metric="error_rows",
            comparator="max",
            value=errors,
            threshold=max_errors,
            passed=errors_pass,
            note=f"maximum error rows eps={error_max_eps:.6f}",
        )
    )
    preexpiry_ws_missing_or_unusable_count_max_eps = _metric_epsilon(
        "preexpiry_ws_missing_or_unusable_anomaly_count",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    preexpiry_ws_missing_or_unusable_count_pass = _passes_max(
        preexpiry_ws_missing_or_unusable_anomaly_count,
        max_preexpiry_ws_missing_or_unusable_anomaly_count,
        preexpiry_ws_missing_or_unusable_count_max_eps,
    )
    if not preexpiry_ws_missing_or_unusable_count_pass:
        findings.append(
            "soak_preexpiry_ws_missing_or_unusable_anomaly_count_too_high:"
            + f"{preexpiry_ws_missing_or_unusable_anomaly_count:.6f}>max:{max_preexpiry_ws_missing_or_unusable_anomaly_count:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_preexpiry_ws_missing_or_unusable_anomaly_count",
            level="hard_fail",
            metric="preexpiry_ws_missing_or_unusable_anomaly_count",
            comparator="max",
            value=preexpiry_ws_missing_or_unusable_anomaly_count,
            threshold=max_preexpiry_ws_missing_or_unusable_anomaly_count,
            passed=preexpiry_ws_missing_or_unusable_count_pass,
            note=(
                "pre-expiry ws missing or unusable anomaly budget eps="
                + f"{preexpiry_ws_missing_or_unusable_count_max_eps:.6f}"
            ),
        )
    )
    lifecycle_mismatch_max_eps = _metric_epsilon(
        "lifecycle_context_mismatch_count",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    lifecycle_mismatch_pass = _passes_max(
        lifecycle_context_mismatch_count,
        max_lifecycle_context_mismatch_count,
        lifecycle_mismatch_max_eps,
    )
    if not lifecycle_mismatch_pass:
        findings.append(
            "soak_lifecycle_context_mismatch_count_too_high:"
            + f"{lifecycle_context_mismatch_count:.6f}>max:{max_lifecycle_context_mismatch_count:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_lifecycle_context_mismatch_count",
            level="hard_fail",
            metric="lifecycle_context_mismatch_count",
            comparator="max",
            value=lifecycle_context_mismatch_count,
            threshold=max_lifecycle_context_mismatch_count,
            passed=lifecycle_mismatch_pass,
            note=f"lifecycle context coherence budget eps={lifecycle_mismatch_max_eps:.6f}",
        )
    )
    lifecycle_missing_max_eps = _metric_epsilon(
        "lifecycle_context_missing_sec_to_expiry_count",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    lifecycle_missing_pass = _passes_max(
        lifecycle_context_missing_sec_to_expiry_count,
        max_lifecycle_context_missing_sec_to_expiry_count,
        lifecycle_missing_max_eps,
    )
    if not lifecycle_missing_pass:
        findings.append(
            "soak_lifecycle_context_missing_sec_to_expiry_count_too_high:"
            + f"{lifecycle_context_missing_sec_to_expiry_count:.6f}>max:{max_lifecycle_context_missing_sec_to_expiry_count:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_lifecycle_context_missing_sec_to_expiry_count",
            level="hard_fail",
            metric="lifecycle_context_missing_sec_to_expiry_count",
            comparator="max",
            value=lifecycle_context_missing_sec_to_expiry_count,
            threshold=max_lifecycle_context_missing_sec_to_expiry_count,
            passed=lifecycle_missing_pass,
            note=f"explicit sec_to_expiry coherence budget eps={lifecycle_missing_max_eps:.6f}",
        )
    )
    hard_degraded_unrecovered_max_eps = _metric_epsilon(
        "valuation_hard_degraded_unrecovered_count",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    hard_degraded_unrecovered_pass = _passes_max(
        valuation_hard_degraded_unrecovered_count,
        max_valuation_hard_degraded_unrecovered_count,
        hard_degraded_unrecovered_max_eps,
    )
    if not hard_degraded_unrecovered_pass:
        findings.append(
            "soak_valuation_hard_degraded_unrecovered_count_too_high:"
            + f"{valuation_hard_degraded_unrecovered_count:.6f}>max:{max_valuation_hard_degraded_unrecovered_count:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_valuation_hard_degraded_unrecovered_count",
            level="hard_fail",
            metric="valuation_hard_degraded_unrecovered_count",
            comparator="max",
            value=valuation_hard_degraded_unrecovered_count,
            threshold=max_valuation_hard_degraded_unrecovered_count,
            passed=hard_degraded_unrecovered_pass,
            note=(
                f"enter_count={valuation_hard_degraded_enter_count:.6f} "
                + f"clear_count={valuation_hard_degraded_clear_count:.6f} "
                + f"eps={hard_degraded_unrecovered_max_eps:.6f}"
            ),
        )
    )
    held_unpriceable_unrecovered_max_eps = _metric_epsilon(
        "held_unpriceable_unrecovered_count",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    held_unpriceable_unrecovered_pass = _passes_max(
        held_unpriceable_unrecovered_count,
        max_held_unpriceable_unrecovered_count,
        held_unpriceable_unrecovered_max_eps,
    )
    if not held_unpriceable_unrecovered_pass:
        findings.append(
            "soak_held_unpriceable_unrecovered_count_too_high:"
            + f"{held_unpriceable_unrecovered_count:.6f}>max:{max_held_unpriceable_unrecovered_count:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_held_unpriceable_unrecovered_count",
            level="hard_fail",
            metric="held_unpriceable_unrecovered_count",
            comparator="max",
            value=held_unpriceable_unrecovered_count,
            threshold=max_held_unpriceable_unrecovered_count,
            passed=held_unpriceable_unrecovered_pass,
            note=(
                f"raw_count={held_unpriceable_unrecovered_raw_count:.6f} "
                + f"non_defect_count={held_unpriceable_unrecovered_non_defect_count:.6f} "
                + f"started_count={held_unpriceable_started_count:.6f} "
                + f"recovered_count={held_unpriceable_recovered_count:.6f} "
                + f"eps={held_unpriceable_unrecovered_max_eps:.6f}"
            ),
        )
    )
    maker_submits_min_eps = _metric_epsilon(
        "maker_submits",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    maker_submits_pass = (not maker_min_enforcement_applied) or _passes_min(
        maker_submits,
        maker_submits_required,
        maker_submits_min_eps,
    )
    if maker_min_enforcement_applied and not maker_submits_pass:
        findings.append(f"soak_maker_submits_too_low:{maker_submits:.6f}<min:{maker_submits_required:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_maker_submits",
            level="hard_fail",
            metric="maker_submits",
            comparator="min",
            value=maker_submits,
            threshold=maker_submits_required,
            passed=maker_submits_pass,
            note=(
                "execution-path health signal "
                + f"eps={maker_submits_min_eps:.6f} "
                + f"enforcement_applied={int(maker_min_enforcement_applied)} "
                + f"mode={maker_enforcement_mode} "
                + f"actionable_rows={maker_actionable_opportunity_rows:.6f} "
                + f"required_submits={maker_submits_required:.6f} "
                + f"min_opportunity_rows={min_maker_opportunity_rows:.6f}"
            ),
        )
    )
    maker_fill_rate_max_eps = _metric_epsilon(
        "maker_fill_rate",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    maker_fill_rate_enforcement_applied = maker_submits >= maker_fill_rate_enforcement_min_submits
    maker_fill_rate_pass = (not maker_fill_rate_enforcement_applied) or _passes_max(
        maker_fill_rate,
        max_maker_fill_rate,
        maker_fill_rate_max_eps,
    )
    if maker_fill_rate_enforcement_applied and not maker_fill_rate_pass:
        findings.append(f"soak_maker_fill_rate_too_high:{maker_fill_rate:.6f}>max:{max_maker_fill_rate:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_maker_fill_rate",
            level="hard_fail",
            metric="maker_fill_rate",
            comparator="max",
            value=maker_fill_rate,
            threshold=max_maker_fill_rate,
            passed=maker_fill_rate_pass,
            note=(
                f"maker_submits={maker_submits:.6f} "
                + f"enforcement_min_submits={maker_fill_rate_enforcement_min_submits:.6f} "
                + f"enforcement_applied={int(maker_fill_rate_enforcement_applied)} "
                + f"eps={maker_fill_rate_max_eps:.6f}"
            ),
        )
    )
    taker_submits_min_eps = _metric_epsilon(
        "taker_bonus_submits",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    taker_submits_pass = _passes_min(taker_bonus_submits, min_taker_bonus_submits, taker_submits_min_eps)
    if not taker_submits_pass:
        findings.append(
            f"soak_taker_bonus_submits_too_low:{taker_bonus_submits:.6f}<min:{min_taker_bonus_submits:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_taker_bonus_submits",
            level="hard_fail",
            metric="taker_bonus_submits",
            comparator="min",
            value=taker_bonus_submits,
            threshold=min_taker_bonus_submits,
            passed=taker_submits_pass,
            note=f"execution-path health signal eps={taker_submits_min_eps:.6f}",
        )
    )
    taker_fills_min_eps = _metric_epsilon(
        "taker_bonus_fills",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    taker_fills_pass = _passes_min(taker_bonus_fills, min_taker_bonus_fills, taker_fills_min_eps)
    if not taker_fills_pass:
        findings.append(f"soak_taker_bonus_fills_too_low:{taker_bonus_fills:.6f}<min:{min_taker_bonus_fills:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_taker_bonus_fills",
            level="hard_fail",
            metric="taker_bonus_fills",
            comparator="min",
            value=taker_bonus_fills,
            threshold=min_taker_bonus_fills,
            passed=taker_fills_pass,
            note=f"execution-path health signal eps={taker_fills_min_eps:.6f}",
        )
    )
    taker_fill_rate_max_eps = _metric_epsilon(
        "taker_bonus_fill_rate",
        kind="max",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    taker_fill_rate_pass = (taker_bonus_submits <= 0) or _passes_max(
        taker_bonus_fill_rate,
        max_taker_bonus_fill_rate,
        taker_fill_rate_max_eps,
    )
    if taker_bonus_submits > 0 and not taker_fill_rate_pass:
        findings.append(
            f"soak_taker_bonus_fill_rate_too_high:{taker_bonus_fill_rate:.6f}>max:{max_taker_bonus_fill_rate:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_taker_bonus_fill_rate",
            level="hard_fail",
            metric="taker_bonus_fill_rate",
            comparator="max",
            value=taker_bonus_fill_rate,
            threshold=max_taker_bonus_fill_rate,
            passed=taker_fill_rate_pass,
            note=f"taker_bonus_submits={taker_bonus_submits:.6f} eps={taker_fill_rate_max_eps:.6f}",
        )
    )
    execution_quality_min_eps = _metric_epsilon(
        "execution_quality_capture_minus_adverse",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    execution_quality_pass = (not execution_quality_floor_enabled) or _passes_min(
        execution_quality_capture_minus_adverse,
        min_execution_quality_capture_minus_adverse,
        execution_quality_min_eps,
    )
    if execution_quality_floor_enabled and not execution_quality_pass:
        findings.append(
            "soak_execution_quality_capture_minus_adverse_too_low:"
            + f"{execution_quality_capture_minus_adverse:.6f}<min:{min_execution_quality_capture_minus_adverse:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_execution_quality_capture_minus_adverse",
            level="hard_fail",
            metric="execution_quality_capture_minus_adverse",
            comparator="min",
            value=execution_quality_capture_minus_adverse,
            threshold=min_execution_quality_capture_minus_adverse,
            passed=execution_quality_pass,
            note=(
                f"enabled={int(execution_quality_floor_enabled)} "
                + f"eps={execution_quality_min_eps:.6f}"
            ),
        )
    )
    total_updates_min_eps = _metric_epsilon(
        "book_updates_total_delta",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    total_updates_pass = _passes_min(book_updates_total_delta, min_book_updates_total_delta, total_updates_min_eps)
    if not total_updates_pass:
        findings.append(
            f"soak_book_updates_total_too_low:{book_updates_total_delta:.6f}<min:{min_book_updates_total_delta:.6f}"
        )
    decision_trace.append(
        decision_item(
            check="soak_book_updates_total_delta",
            level="hard_fail",
            metric="book_updates_total_delta",
            comparator="min",
            value=book_updates_total_delta,
            threshold=min_book_updates_total_delta,
            passed=total_updates_pass,
            note=f"market data continuity signal eps={total_updates_min_eps:.6f}",
        )
    )
    ws_updates_min_eps = _metric_epsilon(
        "book_updates_ws_delta",
        kind="min",
        default_min_eps=default_min_eps,
        default_max_eps=default_max_eps,
        metric_eps_cfg=metric_eps_cfg,
    )
    ws_updates_pass = _passes_min(book_updates_ws_delta, min_book_updates_ws_delta, ws_updates_min_eps)
    if not ws_updates_pass:
        findings.append(f"soak_book_updates_ws_too_low:{book_updates_ws_delta:.6f}<min:{min_book_updates_ws_delta:.6f}")
    decision_trace.append(
        decision_item(
            check="soak_book_updates_ws_delta",
            level="hard_fail",
            metric="book_updates_ws_delta",
            comparator="min",
            value=book_updates_ws_delta,
            threshold=min_book_updates_ws_delta,
            passed=ws_updates_pass,
            note=f"ws primary source continuity eps={ws_updates_min_eps:.6f}",
        )
    )
    unique = sorted(set(findings))
    reliability_findings = [f for f in unique if _lane_for_finding(f) == "reliability"]
    utilization_findings = [f for f in unique if _lane_for_finding(f) == "utilization"]
    reliability_ok = len(reliability_findings) == 0
    utilization_ok = len(utilization_findings) == 0
    effective_mode = gate_mode if gate_mode in {"both", "reliability", "utilization"} else "both"
    if effective_mode == "reliability":
        overall_ok = reliability_ok
    elif effective_mode == "utilization":
        overall_ok = utilization_ok
    else:
        overall_ok = reliability_ok and utilization_ok
    return {
        "ok": overall_ok,
        "finding_count": len(unique),
        "findings": unique,
        "error_codes": summarize_error_codes(unique),
        "decision_trace": decision_trace,
        "threshold_semantics": {
            "hard_fail": [
                "run_integrity_audit findings",
                "performance_budget_gate findings",
                "websocket_reliability_gate findings",
                "readiness required stage",
                "soak duration/uptime/error rows",
                "valuation/lifecycle counter coherence and anomaly budgets",
                "active-target meaningful participation when targets are present",
                "market-data liveliness floors",
                "execution-quality immediate capture-minus-adverse floor when configured",
                "maker/taker bonus execution-path minimums",
            ],
            "warning": [],
            "advisory": [
                "lane splitting via gate_mode",
            ],
        },
        "gate_mode": effective_mode,
        "lanes": {
            "reliability": {
                "ok": reliability_ok,
                "finding_count": len(reliability_findings),
                "findings": reliability_findings,
                "error_codes": summarize_error_codes(reliability_findings),
            },
            "utilization": {
                "ok": utilization_ok,
                "finding_count": len(utilization_findings),
                "findings": utilization_findings,
                "error_codes": summarize_error_codes(utilization_findings),
            },
        },
        "log_dir": str(resolved_log_dir),
        "session_phase": normalized_phase,
        "run_contract_path": str(run_contract_path.resolve()) if isinstance(run_contract_path, pathlib.Path) else "",
        "run_id": selected_run_id or "",
        "applicability": "support_only_soak_hardening_policy",
        "authoritative_for_runtime_blocker_truth": False,
        "authoritative_for_execution_lane_blocker_truth": False,
        "execution_lane_blocker_owner_artifact": "maker_blocker_ledger.json",
        "owner_boundary": "reliability_policy_only",
        "blocker_truth_boundary": "postrun_reliability_only",
        "artifact_identity": build_artifact_identity(log_dir=resolved_log_dir, run_id=selected_run_id),
        "budget_path": str(resolved_budget_path),
        "integrity": integrity,
        "performance": performance,
        "websocket": websocket,
        "readiness": readiness,
        "soak_report": {
            "duration_minutes": duration,
            "quote_uptime_ratio": uptime,
            "quote_uptime_applicable": quote_uptime_applicable,
            "error_rows": errors,
            "maker_submits": maker_submits,
            "maker_fill_rate": maker_fill_rate,
            "taker_bonus_submits": taker_bonus_submits,
            "taker_bonus_fills": taker_bonus_fills,
            "taker_bonus_fill_rate": taker_bonus_fill_rate,
            "execution_quality_capture_minus_adverse": execution_quality_capture_minus_adverse,
            "execution_quality_capture_minus_adverse_source": execution_quality_capture_minus_adverse_source,
            "book_updates_ws_delta": book_updates_ws_delta,
            "book_updates_total_delta": book_updates_total_delta,
            "pair_truth_missing_pair_row_ratio": pair_truth_missing_pair_row_ratio,
            "pair_truth_missing_pair_count_max": pair_truth_missing_pair_count_max,
            "pair_truth_one_sided_row_ratio": pair_truth_one_sided_row_ratio,
            "preexpiry_ws_missing_or_unusable_anomaly_count": preexpiry_ws_missing_or_unusable_anomaly_count,
            "lifecycle_context_mismatch_count": lifecycle_context_mismatch_count,
            "lifecycle_context_missing_sec_to_expiry_count": lifecycle_context_missing_sec_to_expiry_count,
            "settlement_hold_required_count": settlement_hold_required_count,
            "open_order_cleanup_required_count": open_order_cleanup_required_count,
            "unresolved_lifecycle_obligation_count": unresolved_lifecycle_obligation_count,
            "cancel_fail_closed_count": cancel_fail_closed_count,
            "valuation_hard_degraded_enter_count": valuation_hard_degraded_enter_count,
            "valuation_hard_degraded_clear_count": valuation_hard_degraded_clear_count,
            "held_unpriceable_started_count": held_unpriceable_started_count,
            "held_unpriceable_recovered_count": held_unpriceable_recovered_count,
            "valuation_hard_degraded_unrecovered_count": valuation_hard_degraded_unrecovered_count,
            "held_unpriceable_unrecovered_raw_count": held_unpriceable_unrecovered_raw_count,
            "held_unpriceable_unrecovered_non_defect_count": held_unpriceable_unrecovered_non_defect_count,
            "held_unpriceable_unrecovered_meaningful_count": held_unpriceable_unrecovered_count,
            "held_unpriceable_unrecovered_count": held_unpriceable_unrecovered_count,
            "report_max_lines_per_file": report_tail,
            "runtime_classification": runtime_classification_name,
            "runtime_promotion_eligible": runtime_promotion_eligible,
            "runtime_primary_suppression_cause": runtime_primary_suppression_cause,
            "runtime_contributing_suppression_causes": sorted(
                {str(x).strip() for x in runtime_contributing_suppression_causes if str(x).strip()}
            ),
            "runtime_ambiguous_suppression_cause": runtime_ambiguous_suppression_cause,
            "suppression_dominated_run": suppression_dominated_run,
            "execution_starvation_mode": execution_starvation_mode,
            "protected_no_trade_explanation": protected_no_trade_explanation,
            "control_authority_clarity": control_authority_clarity,
            "protection_path_trigger_chain": protection_path_trigger_chain,
            "comparison_tolerance": {
                "min_default": default_min_eps,
                "max_default": default_max_eps,
                "metric_overrides": {
                    **metric_eps_cfg,
                    "quote_uptime_ratio": dict(
                        (readiness_comparison_cfg.get("metrics", {}) or {}).get("quote_uptime_ratio", {})
                    ),
                },
            },
            "valuation_counter_limits": {
                "max_preexpiry_ws_missing_or_unusable_anomaly_count": max_preexpiry_ws_missing_or_unusable_anomaly_count,
                "max_lifecycle_context_mismatch_count": max_lifecycle_context_mismatch_count,
                "max_lifecycle_context_missing_sec_to_expiry_count": max_lifecycle_context_missing_sec_to_expiry_count,
                "max_valuation_hard_degraded_unrecovered_count": max_valuation_hard_degraded_unrecovered_count,
                "max_held_unpriceable_unrecovered_count": max_held_unpriceable_unrecovered_count,
            },
            "readiness_required_stage_failure_causes": sorted(set(readiness_required_stage_failure_causes)),
            "maker_submit_enforcement": {
                "mode": maker_enforcement_mode,
                "applied": bool(maker_min_enforcement_applied),
                "reason": maker_min_enforcement_reason,
                "required_submits": maker_submits_required,
                "opportunity_surface_ok": bool(maker_opportunity_surface_ok),
                "opportunity_surface_source": "edge_truth.maker_block_reason_distribution",
                "opportunity_surface_missing": sorted(maker_opportunity_surface_missing),
                "min_opportunity_rows": min_maker_opportunity_rows,
                "maker_rows_total": maker_rows_total,
                "maker_non_actionable_block_rows": maker_non_actionable_block_rows,
                "maker_actionable_opportunity_rows": maker_actionable_opportunity_rows,
                "non_actionable_block_reasons": list(non_actionable_reasons),
                "maker_block_reason_distribution": dict(maker_block_reason_distribution),
            },
            "maker_fill_rate_enforcement": {
                "min_submits": maker_fill_rate_enforcement_min_submits,
                "applied": bool(maker_fill_rate_enforcement_applied),
            },
            "execution_quality_enforcement": {
                "enabled": bool(execution_quality_floor_enabled),
                "min_capture_minus_adverse": min_execution_quality_capture_minus_adverse,
                "capture_minus_adverse": execution_quality_capture_minus_adverse,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro unified soak hardening gate")
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--run-contract",
        default="",
        help="Optional run contract JSON path for deterministic replay",
    )
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_postrun)",
    )
    parser.add_argument("--budget", default="ops/soak_budget.yaml")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    result = run_gate(
        log_dir=pathlib.Path(args.log_dir),
        run_id=(str(args.run_id).strip() or None),
        budget_path=pathlib.Path(args.budget),
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
