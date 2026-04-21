#!/usr/bin/env python3
"""Evaluate Bro soak logs against staged deployment gate criteria."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import yaml


from prodesk.artifact_identity import candidate_run_log_dirs
from prodesk.config import load_execution_config
from prodesk.paths import validate_runtime_write_paths
from prodesk.session_phase import enforce_validation_phase
from prodesk.runtime_semantics import (
    RUNTIME_CLASS_INVALID_DEADLOCK,
    RUNTIME_CLASS_INVALID_SAFETY,
    RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION,
)

try:
    from scripts.nightly_soak_report import build_report
except ModuleNotFoundError:  # pragma: no cover - direct script invocation path
    from nightly_soak_report import build_report

DEFAULT_REPORT_MAX_LINES_PER_FILE = 50000

KNOWN_METRICS = {
    "status_rows",
    "error_rows",
    "quote_uptime_ratio",
    "capture_minus_adverse",
    "total_rejects",
    "reject_ratio_order_rate_limit",
    "reject_ratio_stale_book",
    "kill_switch_events",
    "safe_stop_transitions",
    "maker_only_transitions",
    "latest_operating_mode_state",
    "runtime_promotion_eligible",
    "runtime_active_targets_seen",
    "runtime_meaningful_participation",
    "runtime_deadlock_rows",
    "runtime_safety_rows",
    "runtime_required_book_feed_disconnected_rows",
    "runtime_no_target_standdown_ratio",
    "suppression_dominated_run",
    "runtime_ambiguous_suppression_cause",
    "quote_window_ratio",
    "quote_active_within_window_ratio",
    "participation_ratio",
    "participation_within_quote_window_ratio",
    "maker_reference_direct_midpoint_activity",
    "maker_reference_bounded_fallback_activity",
    "maker_reference_direct_midpoint_action_activity",
    "maker_reference_bounded_fallback_action_activity",
    "maker_market_reference_fallback_bid_count",
    "maker_market_reference_fallback_ask_count",
    "preexpiry_404_anomaly_count",
    "lifecycle_context_mismatch_count",
    "lifecycle_context_missing_sec_to_expiry_count",
    "preexpiry_emergency_taker_attempt_count",
    "preexpiry_emergency_taker_fill_count",
    "preexpiry_emergency_taker_block_count",
    "valuation_hard_degraded_enter_count",
    "valuation_hard_degraded_clear_count",
    "held_unpriceable_started_count",
    "held_unpriceable_recovered_count",
    "resource_status_rows",
    "resource_process_cpu_percent_p95",
    "resource_process_cpu_percent_max",
    "resource_process_cpu_percent_normalized_p95",
    "resource_process_cpu_percent_normalized_max",
    "resource_process_rss_mb_max",
    "resource_system_load1_p95",
    "resource_system_load1_max",
    "resource_system_load5_p95",
    "resource_system_load15_p95",
    "resource_system_mem_available_mb_min",
    "resource_system_mem_available_ratio_min",
    "resource_system_swap_used_mb_max",
    "resource_system_swap_used_ratio_max",
}


def _resolve_effective_log_dir(log_dir: pathlib.Path) -> pathlib.Path:
    raw = str(log_dir or "").strip()
    if raw == "/logs" or raw.startswith("/logs/"):
        host_root = pathlib.Path(str(os.getenv("BRO_LOG_DIR", "./logs_exec"))).expanduser()
        if not host_root.is_absolute():
            host_root = (pathlib.Path.cwd() / host_root).resolve()
        rel = PurePosixPath(raw).relative_to("/logs")
        return (host_root / pathlib.Path(*rel.parts)).resolve()
    return log_dir.resolve()


def _parse_criterion_key(key: str) -> Tuple[str, str]:
    text = str(key).strip()
    if text.startswith("min_"):
        return "min", text[4:]
    if text.startswith("max_"):
        return "max", text[4:]
    if text.startswith("eq_"):
        return "eq", text[3:]
    raise ValueError(f"invalid criterion key {key!r}: expected min_/max_/eq_ prefix")


def _validate_stage_criteria(stage_name: str, criteria: Dict[str, Any]) -> None:
    for key, threshold_raw in criteria.items():
        _rule, metric_name = _parse_criterion_key(str(key))
        if metric_name not in KNOWN_METRICS:
            known = ",".join(sorted(KNOWN_METRICS))
            raise ValueError(f"unknown metric {metric_name!r} in stage {stage_name!r}; known={known}")
        try:
            float(threshold_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"non-numeric threshold for {key!r} in stage {stage_name!r}: {threshold_raw!r}"
            ) from exc


def _load_policy(path: pathlib.Path) -> Dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("policy file root must be a mapping")
    stages = raw.get("stages")
    if not isinstance(stages, dict) or not stages:
        raise ValueError("policy requires non-empty stages mapping")
    stage_order = raw.get("stage_order")
    if not isinstance(stage_order, list) or not stage_order:
        stage_order = list(stages.keys())
    stage_order = [str(x).strip() for x in stage_order if str(x).strip()]
    if not stage_order:
        raise ValueError("policy stage_order must contain at least one stage")
    for stage in stage_order:
        if stage not in stages:
            raise ValueError(f"stage_order contains unknown stage: {stage}")
    for stage_name, raw_criteria in stages.items():
        if not isinstance(raw_criteria, dict):
            raise ValueError(f"stage {stage_name!r} criteria must be a mapping")
        _validate_stage_criteria(str(stage_name), raw_criteria)
    return {"stage_order": stage_order, "stages": stages}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
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


def _collect_derived_metrics(
    log_dir: pathlib.Path,
    report: Dict[str, Any],
    *,
    run_id: Optional[str],
    max_lines_per_file: int,
) -> Dict[str, float]:
    _ = (log_dir, run_id)  # reserved for future diagnostics hooks
    lines_limit = max(0, int(max_lines_per_file))
    reject_dist = report.get("reject_reason_distribution", {})
    if not isinstance(reject_dist, dict):
        reject_dist = {}
    total_rejects = float(sum(_safe_float(v) for v in reject_dist.values()))
    order_rate_rejects = 0.0
    stale_book_rejects = 0.0
    for reason_raw, count_raw in reject_dist.items():
        reason = str(reason_raw)
        count = _safe_float(count_raw)
        if "order_rate_limit" in reason:
            order_rate_rejects += count
        if "stale_book" in reason:
            stale_book_rejects += count

    latest_mode_state = _safe_float(report.get("latest_operating_mode_state"))

    execution_quality = report.get("execution_quality", {})
    if not isinstance(execution_quality, dict):
        execution_quality = {}
    runtime_classification = report.get("runtime_classification", {})
    if not isinstance(runtime_classification, dict):
        runtime_classification = {}
    quote_diagnostics = report.get("quote_diagnostics", {})
    if not isinstance(quote_diagnostics, dict):
        quote_diagnostics = {}
    runtime_metrics = runtime_classification.get("metrics", {})
    if not isinstance(runtime_metrics, dict):
        runtime_metrics = {}
    valuation_truth = report.get("valuation_truth", {})
    if not isinstance(valuation_truth, dict):
        valuation_truth = {}
    runtime_resource = report.get("runtime_resource", {})
    if not isinstance(runtime_resource, dict):
        runtime_resource = {}
    runtime_class_name = str(runtime_classification.get("classification") or "").strip().upper()
    runtime_promotion_eligible = bool(runtime_classification.get("promotion_eligible", False))
    runtime_primary_suppression_cause = str(
        report.get("primary_suppression_cause") or runtime_classification.get("primary_suppression_cause") or "none"
    ).strip()
    runtime_contributing_suppression_causes = report.get("contributing_suppression_causes", [])
    if not isinstance(runtime_contributing_suppression_causes, list):
        runtime_contributing_suppression_causes = []
    runtime_ambiguous_suppression_cause = bool(
        report.get("ambiguous_suppression_cause", runtime_classification.get("ambiguous_suppression_cause", False))
    )
    suppression_dominated_run = bool(report.get("suppression_dominated_run", False))
    execution_starvation_mode = str(report.get("execution_starvation_mode") or "unknown").strip() or "unknown"
    protected_no_trade_explanation = str(report.get("protected_no_trade_explanation") or "").strip()
    standdown_rows = _safe_float(runtime_metrics.get("standdown_rows"))
    status_rows_total = max(1.0, _safe_float(runtime_metrics.get("status_rows"), default=0.0))
    standdown_ratio = standdown_rows / status_rows_total if status_rows_total > 0 else 0.0

    metrics = {
        "status_rows": _safe_float(report.get("status_rows")),
        "error_rows": _safe_float(report.get("error_rows")),
        "quote_uptime_ratio": _safe_float(report.get("quote_uptime_ratio")),
        "capture_minus_adverse": _safe_float(execution_quality.get("capture_minus_adverse")),
        "total_rejects": total_rejects,
        "reject_ratio_order_rate_limit": (order_rate_rejects / total_rejects) if total_rejects > 0 else 0.0,
        "reject_ratio_stale_book": (stale_book_rejects / total_rejects) if total_rejects > 0 else 0.0,
        "kill_switch_events": _safe_float(report.get("kill_switch_events")),
        "safe_stop_transitions": _safe_float(report.get("safe_stop_transitions")),
        "maker_only_transitions": _safe_float(report.get("maker_only_transitions")),
        "latest_operating_mode_state": latest_mode_state,
        "runtime_promotion_eligible": 1.0 if runtime_promotion_eligible else 0.0,
        "runtime_active_targets_seen": _safe_float(runtime_metrics.get("active_targets_seen")),
        "runtime_meaningful_participation": _safe_float(runtime_metrics.get("meaningful_participation")),
        "runtime_deadlock_rows": _safe_float(runtime_metrics.get("deadlock_rows")),
        "runtime_safety_rows": _safe_float(runtime_metrics.get("safety_rows")),
        "runtime_required_book_feed_disconnected_rows": _safe_float(
            runtime_metrics.get("required_book_feed_disconnected_rows")
        ),
        "runtime_no_target_standdown_ratio": standdown_ratio,
        "suppression_dominated_run": 1.0 if suppression_dominated_run else 0.0,
        "runtime_ambiguous_suppression_cause": 1.0 if runtime_ambiguous_suppression_cause else 0.0,
        "quote_window_ratio": _safe_float(quote_diagnostics.get("quote_window_ratio")),
        "quote_active_within_window_ratio": _safe_float(quote_diagnostics.get("quote_active_within_window_ratio")),
        "participation_ratio": _safe_float(quote_diagnostics.get("participation_ratio")),
        "participation_within_quote_window_ratio": _safe_float(
            quote_diagnostics.get("participation_within_window_ratio")
        ),
        "maker_reference_direct_midpoint_activity": _safe_float(
            report.get("maker_reference_direct_midpoint_activity")
        ),
        "maker_reference_bounded_fallback_activity": _safe_float(
            report.get("maker_reference_bounded_fallback_activity")
        ),
        "maker_reference_direct_midpoint_action_activity": _safe_float(
            report.get("maker_reference_direct_midpoint_action_activity")
        ),
        "maker_reference_bounded_fallback_action_activity": _safe_float(
            report.get("maker_reference_bounded_fallback_action_activity")
        ),
        "maker_market_reference_fallback_bid_count": _safe_float(
            report.get("maker_market_reference_fallback_bid_count")
        ),
        "maker_market_reference_fallback_ask_count": _safe_float(
            report.get("maker_market_reference_fallback_ask_count")
        ),
        "preexpiry_404_anomaly_count": _safe_float(valuation_truth.get("preexpiry_404_anomaly_count")),
        "lifecycle_context_mismatch_count": _safe_float(valuation_truth.get("lifecycle_context_mismatch_count")),
        "lifecycle_context_missing_sec_to_expiry_count": _safe_float(
            valuation_truth.get("lifecycle_context_missing_sec_to_expiry_count")
        ),
        "preexpiry_emergency_taker_attempt_count": _safe_float(
            valuation_truth.get("preexpiry_emergency_taker_attempt_count")
        ),
        "preexpiry_emergency_taker_fill_count": _safe_float(
            valuation_truth.get("preexpiry_emergency_taker_fill_count")
        ),
        "preexpiry_emergency_taker_block_count": _safe_float(
            valuation_truth.get("preexpiry_emergency_taker_block_count")
        ),
        "valuation_hard_degraded_enter_count": _safe_float(
            valuation_truth.get("valuation_hard_degraded_enter_count")
        ),
        "valuation_hard_degraded_clear_count": _safe_float(
            valuation_truth.get("valuation_hard_degraded_clear_count")
        ),
        "held_unpriceable_started_count": _safe_float(
            valuation_truth.get("held_unpriceable_started_count")
        ),
        "held_unpriceable_recovered_count": _safe_float(
            valuation_truth.get("held_unpriceable_recovered_count")
        ),
        "resource_status_rows": _safe_float(runtime_resource.get("resource_status_rows")),
        "resource_process_cpu_percent_p95": _safe_float(runtime_resource.get("process_cpu_percent_p95")),
        "resource_process_cpu_percent_max": _safe_float(runtime_resource.get("process_cpu_percent_max")),
        "resource_process_cpu_percent_normalized_p95": _safe_float(
            runtime_resource.get("process_cpu_percent_normalized_p95")
        ),
        "resource_process_cpu_percent_normalized_max": _safe_float(
            runtime_resource.get("process_cpu_percent_normalized_max")
        ),
        "resource_process_rss_mb_max": _safe_float(runtime_resource.get("process_rss_mb_max")),
        "resource_system_load1_p95": _safe_float(runtime_resource.get("system_load1_p95")),
        "resource_system_load1_max": _safe_float(runtime_resource.get("system_load1_max")),
        "resource_system_load5_p95": _safe_float(runtime_resource.get("system_load5_p95")),
        "resource_system_load15_p95": _safe_float(runtime_resource.get("system_load15_p95")),
        "resource_system_mem_available_mb_min": _safe_float(runtime_resource.get("system_mem_available_mb_min")),
        "resource_system_mem_available_ratio_min": _safe_float(
            runtime_resource.get("system_mem_available_ratio_min")
        ),
        "resource_system_swap_used_mb_max": _safe_float(runtime_resource.get("system_swap_used_mb_max")),
        "resource_system_swap_used_ratio_max": _safe_float(
            runtime_resource.get("system_swap_used_ratio_max")
        ),
        "runtime_classification_name": runtime_class_name,
        "runtime_primary_suppression_cause": runtime_primary_suppression_cause,
        "runtime_contributing_suppression_causes": sorted(
            {str(x).strip() for x in runtime_contributing_suppression_causes if str(x).strip()}
        ),
        "execution_starvation_mode": execution_starvation_mode,
        "protected_no_trade_explanation": protected_no_trade_explanation,
    }
    return metrics


def _evaluate_stage(metrics: Dict[str, float], criteria: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    checks: List[Dict[str, Any]] = []
    stage_pass = True
    for key, threshold_raw in criteria.items():
        rule, metric_name = _parse_criterion_key(str(key))

        metric = metrics.get(metric_name)
        threshold = _safe_float(threshold_raw)
        if metric is None:
            passed = False
            metric_val = None
        elif rule == "min":
            passed = float(metric) >= threshold
            metric_val = float(metric)
        elif rule == "max":
            passed = float(metric) <= threshold
            metric_val = float(metric)
        else:
            passed = abs(float(metric) - threshold) <= 1e-12
            metric_val = float(metric)

        checks.append(
            {
                "criterion": str(key),
                "metric": metric_name,
                "rule": rule,
                "threshold": threshold,
                "value": metric_val,
                "passed": passed,
            }
        )
        if not passed:
            stage_pass = False
    return stage_pass, checks


def run_readiness_gate(
    *,
    log_dir: pathlib.Path,
    policy: Dict[str, Any],
    run_id: Optional[str] = None,
    report_max_lines_per_file: int = DEFAULT_REPORT_MAX_LINES_PER_FILE,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="readiness_gate", session_phase=session_phase)
    explicit_run_id = str(run_id or "").strip()
    resolved_run_id = explicit_run_id or None
    lines_limit = max(0, int(report_max_lines_per_file))
    if not explicit_run_id:
        stage_order = list(policy.get("stage_order", []))
        first_stage = stage_order[0] if stage_order else None
        return {
            "log_dir": str(log_dir),
            "session_phase": normalized_phase,
            "run_id_filter": "",
            "run_id_resolution": "missing",
            "highest_passing_stage": None,
            "blocking_stage": first_stage,
            "recommended_next_stage": first_stage,
            "metrics": {"context_hints": {"candidate_log_dirs_for_run": []}},
            "stage_results": [],
            "report": {"finding_count": 1, "findings": ["readiness_run_id_required"], "ok": False},
        }
    report = build_report(
        log_dir,
        run_id=resolved_run_id,
        auto_resolve_run_id=False,
        max_lines_per_file=lines_limit,
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )
    metrics = _collect_derived_metrics(
        log_dir,
        report,
        run_id=resolved_run_id,
        max_lines_per_file=lines_limit,
    )
    runtime_findings: List[str] = []
    runtime_classification_name = str(metrics.get("runtime_classification_name", "")).strip().upper()
    if runtime_classification_name in {RUNTIME_CLASS_INVALID_DEADLOCK, RUNTIME_CLASS_INVALID_SAFETY}:
        runtime_findings.append(f"readiness_runtime_invalid:{runtime_classification_name}")
    if metrics.get("runtime_promotion_eligible", 0.0) < 1.0:
        if runtime_classification_name in {
            RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION,
            RUNTIME_CLASS_INVALID_DEADLOCK,
            RUNTIME_CLASS_INVALID_SAFETY,
        }:
            runtime_findings.append(f"readiness_runtime_non_promotable:{runtime_classification_name}")
        elif runtime_classification_name:
            runtime_findings.append(f"readiness_runtime_non_promotable:{runtime_classification_name}")
        else:
            runtime_findings.append("readiness_runtime_non_promotable:UNKNOWN")
    metrics["context_hints"] = {
        "candidate_log_dirs_for_run": candidate_run_log_dirs(
            log_dir=pathlib.Path(log_dir).resolve(),
            run_id=explicit_run_id,
            max_depth=3,
        )
    }
    metrics["runtime_findings"] = list(runtime_findings)
    metrics["readiness_report_max_lines_per_file"] = float(lines_limit)

    stage_results: List[Dict[str, Any]] = []
    highest_passing_stage: Optional[str] = None
    blocking_stage: Optional[str] = None
    contiguous_passing = True
    stage_order = policy["stage_order"]
    stage_cfg = policy["stages"]
    for stage in stage_order:
        raw_criteria = stage_cfg.get(stage) or {}
        criteria = raw_criteria if isinstance(raw_criteria, dict) else {}
        passed, checks = _evaluate_stage(metrics, criteria)
        if contiguous_passing and passed:
            highest_passing_stage = stage
        if contiguous_passing and (not passed):
            blocking_stage = stage
            contiguous_passing = False
        stage_results.append({"stage": stage, "passed": passed, "checks": checks})

    if runtime_findings:
        highest_passing_stage = None
        blocking_stage = stage_order[0] if stage_order else None

    recommended_next_stage = None
    if highest_passing_stage is not None:
        idx = stage_order.index(highest_passing_stage)
        if idx + 1 < len(stage_order):
            recommended_next_stage = stage_order[idx + 1]
    elif stage_order:
        recommended_next_stage = stage_order[0]

    return {
        "log_dir": str(log_dir),
        "session_phase": normalized_phase,
        "run_contract_path": str(run_contract_path.resolve()) if isinstance(run_contract_path, pathlib.Path) else "",
        "run_id_filter": resolved_run_id,
        "run_id_resolution": "explicit",
        "highest_passing_stage": highest_passing_stage,
        "blocking_stage": blocking_stage,
        "recommended_next_stage": recommended_next_stage,
        "metrics": metrics,
        "suppression_summary": {
            "primary_suppression_cause": str(metrics.get("runtime_primary_suppression_cause") or "none"),
            "contributing_suppression_causes": list(metrics.get("runtime_contributing_suppression_causes") or []),
            "ambiguous_suppression_cause": bool(metrics.get("runtime_ambiguous_suppression_cause", 0.0) >= 0.5),
            "suppression_dominated_run": bool(metrics.get("suppression_dominated_run", 0.0) >= 0.5),
            "execution_starvation_mode": str(metrics.get("execution_starvation_mode") or "unknown"),
            "protected_no_trade_explanation": str(metrics.get("protected_no_trade_explanation") or ""),
        },
        "stage_results": stage_results,
        "runtime_findings": runtime_findings,
        "report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro readiness gate evaluator")
    parser.add_argument("--log-dir", default="", help="Execution log directory")
    parser.add_argument("--policy", default="./ops/ramp_policy.yaml", help="Ramp policy YAML path")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument("--run-id", default="", help="Run_id filter")
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
    parser.add_argument(
        "--report-max-lines-per-file",
        type=int,
        default=DEFAULT_REPORT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file used by readiness report/metrics; set 0 for full-file scans",
    )
    parser.add_argument("--config", default="", help="Optional execution config path used for writable-path checks")
    parser.add_argument(
        "--check-writable-paths",
        action="store_true",
        help="Fail fast if configured runtime write paths are not writable",
    )
    parser.add_argument(
        "--check-writable-paths-only",
        action="store_true",
        help="Run writable-path checks and exit without evaluating readiness stages",
    )
    args = parser.parse_args()

    if bool(args.check_writable_paths or args.check_writable_paths_only):
        cfg_path = pathlib.Path(str(args.config).strip() or "execution_config.yaml").resolve()
        cfg = load_execution_config(cfg_path)
        path_findings = validate_runtime_write_paths(cfg)
        if path_findings:
            print("writable_path_check=failed")
            for finding in path_findings:
                print(f"finding={finding}")
            raise SystemExit(2)
        print("writable_path_check=ok")
    if bool(args.check_writable_paths_only):
        raise SystemExit(0)
    if not str(args.log_dir).strip():
        raise SystemExit("readiness_gate requires --log-dir")
    if not str(args.run_id).strip():
        raise SystemExit("readiness_gate requires --run-id")

    log_dir = _resolve_effective_log_dir(pathlib.Path(args.log_dir))
    policy_path = pathlib.Path(args.policy).resolve()
    policy = _load_policy(policy_path)
    run_id = str(args.run_id).strip() or None
    result = run_readiness_gate(
        log_dir=log_dir,
        policy=policy,
        run_id=run_id,
        report_max_lines_per_file=max(0, int(args.report_max_lines_per_file)),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )

    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"log_dir={result['log_dir']}")
    print(f"highest_passing_stage={result['highest_passing_stage']}")
    print(f"blocking_stage={result['blocking_stage']}")
    print(f"recommended_next_stage={result['recommended_next_stage']}")
    metrics = result["metrics"]
    print(f"quote_uptime_ratio={metrics['quote_uptime_ratio']:.4f}")
    print(f"error_rows={int(metrics['error_rows'])}")
    print(f"kill_switch_events={int(metrics['kill_switch_events'])}")
    print(f"safe_stop_transitions={int(metrics['safe_stop_transitions'])}")
    print(f"runtime_classification={metrics.get('runtime_classification_name', '')}")
    print(f"runtime_promotion_eligible={int(metrics.get('runtime_promotion_eligible', 0.0))}")


if __name__ == "__main__":
    main()
