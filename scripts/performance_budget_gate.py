#!/usr/bin/env python3
"""Enforce runtime performance budgets from status telemetry."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List

from prodesk.artifact_identity import candidate_run_log_dirs
from prodesk.error_codes import summarize_error_codes
from prodesk.reporting import decision_item


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _row_is_scan_phase(row: Dict[str, Any]) -> bool:
    lifecycle_phase = str(row.get("lifecycle_phase") or "").strip().lower()
    if lifecycle_phase:
        return lifecycle_phase == "scan"
    return False


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    clipped = max(0.0, min(1.0, float(q)))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * clipped))
    return float(ordered[idx])


def _load_status_rows(log_dir: pathlib.Path, run_id: Optional[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(log_dir.glob("status_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for text in lines:
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


def run_gate(
    *,
    log_dir: pathlib.Path,
    run_id: Optional[str],
    max_cycle_latency_p95_ms: float,
    max_cycle_latency_max_ms: float,
    max_process_rss_mb: float,
    max_order_capacity_used_ratio: float,
    max_cancel_capacity_used_ratio: float,
    max_order_capacity_breach_rows: int,
    max_cancel_capacity_breach_rows: int,
    max_order_capacity_breach_ratio: float,
    max_cancel_capacity_breach_ratio: float,
    max_latency_inactive_cycles: float,
    max_market_data_span_ms: float,
    max_strategy_exec_span_ms: float,
    max_state_io_span_ms: float,
    max_status_io_span_ms: float,
    max_cycle_residual_span_ms: float,
    min_status_rows: int,
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    advisories: List[str] = []
    decision_trace: List[Dict[str, Any]] = []
    selected_run_id = str(run_id or "").strip()
    context_hints: Dict[str, Any] = {"candidate_log_dirs_for_run": []}
    if not selected_run_id:
        findings.append("performance_run_id_required")
        rows: List[Dict[str, Any]] = []
    else:
        resolved_log_dir = log_dir.resolve()
        context_hints["candidate_log_dirs_for_run"] = candidate_run_log_dirs(
            log_dir=resolved_log_dir,
            run_id=selected_run_id,
            max_depth=3,
        )
        rows = _load_status_rows(resolved_log_dir, selected_run_id)
        if not rows and context_hints["candidate_log_dirs_for_run"]:
            warnings.append("performance_run_context_candidate_log_dirs_present")

    if len(rows) < int(min_status_rows):
        findings.append(f"performance_status_rows_too_few:{len(rows)}<min:{int(min_status_rows)}")
    decision_trace.append(
        decision_item(
            check="status_row_count",
            level="hard_fail",
            metric="status_row_count",
            comparator="min",
            value=float(len(rows)),
            threshold=float(min_status_rows),
            passed=(len(rows) >= int(min_status_rows)),
            note="minimum evidence rows required",
        )
    )

    cycle_lat = [_safe_float(r.get("gauge.cycle_latency_ms"), default=-1.0) for r in rows]
    cycle_lat = [x for x in cycle_lat if x >= 0]
    cycle_p95 = _percentile(cycle_lat, 0.95) if cycle_lat else 0.0
    cycle_max = max(cycle_lat) if cycle_lat else 0.0

    rss_vals = [_safe_float(r.get("gauge.process_rss_mb"), default=-1.0) for r in rows]
    rss_vals = [x for x in rss_vals if x >= 0]
    rss_max = max(rss_vals) if rss_vals else 0.0
    cpu_vals = [_safe_float(r.get("gauge.process_cpu_percent"), default=-1.0) for r in rows]
    cpu_vals = [x for x in cpu_vals if x >= 0]
    cpu_max = max(cpu_vals) if cpu_vals else 0.0
    cpu_p95 = _percentile(cpu_vals, 0.95) if cpu_vals else 0.0
    load1_vals = [_safe_float(r.get("gauge.system_load1"), default=-1.0) for r in rows]
    load1_vals = [x for x in load1_vals if x >= 0]
    load1_max = max(load1_vals) if load1_vals else 0.0
    mem_available_vals = [_safe_float(r.get("gauge.system_mem_available_mb"), default=-1.0) for r in rows]
    mem_available_vals = [x for x in mem_available_vals if x >= 0]
    mem_available_min = min(mem_available_vals) if mem_available_vals else 0.0
    swap_used_vals = [_safe_float(r.get("gauge.system_swap_used_mb"), default=-1.0) for r in rows]
    swap_used_vals = [x for x in swap_used_vals if x >= 0]
    swap_used_max = max(swap_used_vals) if swap_used_vals else 0.0

    order_used = [_safe_float(r.get("gauge.orders_used_60s"), default=0.0) for r in rows]
    order_limit = [_safe_float(r.get("gauge.orders_limit_60s"), default=0.0) for r in rows]
    cancel_used = [_safe_float(r.get("gauge.cancels_used_60s"), default=0.0) for r in rows]
    cancel_limit = [_safe_float(r.get("gauge.cancels_limit_60s"), default=0.0) for r in rows]
    order_ratio = max(
        ((u / l) if l > 0 else 0.0) for u, l in zip(order_used, order_limit)
    ) if order_used else 0.0
    cancel_ratio = max(
        ((u / l) if l > 0 else 0.0) for u, l in zip(cancel_used, cancel_limit)
    ) if cancel_used else 0.0
    order_breach_rows = sum(
        1 for u, l in zip(order_used, order_limit) if l > 0 and (u / l) > float(max_order_capacity_used_ratio)
    )
    cancel_breach_rows = sum(
        1 for u, l in zip(cancel_used, cancel_limit) if l > 0 and (u / l) > float(max_cancel_capacity_used_ratio)
    )
    sample_count = max(len(rows), 1)
    order_breach_ratio = float(order_breach_rows) / float(sample_count)
    cancel_breach_ratio = float(cancel_breach_rows) / float(sample_count)

    inactive_vals = [
        _safe_float(r.get("gauge.latency_sampling_inactive_cycles"), default=0.0)
        for r in rows
        if not _row_is_scan_phase(r)
    ]
    inactive_max = max(inactive_vals) if inactive_vals else 0.0
    span_market_data_vals = [_safe_float(r.get("gauge.cycle_span_market_data_ms"), default=-1.0) for r in rows]
    span_market_data_vals = [x for x in span_market_data_vals if x >= 0]
    span_market_data_max = max(span_market_data_vals) if span_market_data_vals else 0.0
    span_strategy_exec_vals = [_safe_float(r.get("gauge.cycle_span_strategy_exec_ms"), default=-1.0) for r in rows]
    span_strategy_exec_vals = [x for x in span_strategy_exec_vals if x >= 0]
    span_strategy_exec_max = max(span_strategy_exec_vals) if span_strategy_exec_vals else 0.0
    span_state_io_vals = [_safe_float(r.get("gauge.cycle_span_state_io_ms"), default=-1.0) for r in rows]
    span_state_io_vals = [x for x in span_state_io_vals if x >= 0]
    span_state_io_max = max(span_state_io_vals) if span_state_io_vals else 0.0
    span_status_io_vals = [_safe_float(r.get("gauge.cycle_span_status_io_ms"), default=-1.0) for r in rows]
    span_status_io_vals = [x for x in span_status_io_vals if x >= 0]
    span_status_io_max = max(span_status_io_vals) if span_status_io_vals else 0.0
    span_residual_vals = [_safe_float(r.get("gauge.cycle_span_residual_ms"), default=-1.0) for r in rows]
    span_residual_vals = [x for x in span_residual_vals if x >= 0]
    span_residual_max = max(span_residual_vals) if span_residual_vals else 0.0

    if cycle_p95 > float(max_cycle_latency_p95_ms):
        findings.append(
            f"performance_cycle_latency_p95_too_high:{cycle_p95:.6f}>max:{float(max_cycle_latency_p95_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_latency_p95",
            level="hard_fail",
            metric="cycle_latency_p95_ms",
            comparator="max",
            value=cycle_p95,
            threshold=float(max_cycle_latency_p95_ms),
            passed=(cycle_p95 <= float(max_cycle_latency_p95_ms)),
            note="hard fail threshold",
        )
    )
    if cycle_max > float(max_cycle_latency_max_ms):
        findings.append(
            f"performance_cycle_latency_max_too_high:{cycle_max:.6f}>max:{float(max_cycle_latency_max_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_latency_max",
            level="hard_fail",
            metric="cycle_latency_max_ms",
            comparator="max",
            value=cycle_max,
            threshold=float(max_cycle_latency_max_ms),
            passed=(cycle_max <= float(max_cycle_latency_max_ms)),
            note="hard fail threshold",
        )
    )
    if rss_max > float(max_process_rss_mb):
        findings.append(f"performance_process_rss_too_high:{rss_max:.6f}>max:{float(max_process_rss_mb):.6f}")
    decision_trace.append(
        decision_item(
            check="process_rss_max",
            level="hard_fail",
            metric="process_rss_mb_max",
            comparator="max",
            value=rss_max,
            threshold=float(max_process_rss_mb),
            passed=(rss_max <= float(max_process_rss_mb)),
            note="hard fail threshold",
        )
    )
    if order_ratio > float(max_order_capacity_used_ratio):
        advisories.append(
            "order_capacity_peak_ratio_high:"
            + f"{order_ratio:.6f}>max_ratio:{float(max_order_capacity_used_ratio):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="order_capacity_peak_ratio",
            level="advisory",
            metric="order_capacity_used_ratio_max",
            comparator="max",
            value=order_ratio,
            threshold=float(max_order_capacity_used_ratio),
            passed=(order_ratio <= float(max_order_capacity_used_ratio)),
            note="peak ratio is advisory; hard fail uses breach rows + breach ratio",
        )
    )
    if order_breach_rows > int(max_order_capacity_breach_rows) and order_breach_ratio > float(max_order_capacity_breach_ratio):
        findings.append(
            "performance_order_capacity_used_ratio_too_high:"
            + f"breach_rows:{order_breach_rows}>max:{int(max_order_capacity_breach_rows)}"
            + f":breach_ratio:{order_breach_ratio:.6f}>max:{float(max_order_capacity_breach_ratio):.6f}"
            + f":peak:{order_ratio:.6f}>max_ratio:{float(max_order_capacity_used_ratio):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="order_capacity_breach_rows",
            level="hard_fail",
            metric="order_capacity_breach_rows",
            comparator="max",
            value=float(order_breach_rows),
            threshold=float(max_order_capacity_breach_rows),
            passed=(order_breach_rows <= int(max_order_capacity_breach_rows)),
            note="must pass with breach ratio check",
        )
    )
    decision_trace.append(
        decision_item(
            check="order_capacity_breach_ratio",
            level="hard_fail",
            metric="order_capacity_breach_ratio",
            comparator="max",
            value=order_breach_ratio,
            threshold=float(max_order_capacity_breach_ratio),
            passed=(order_breach_ratio <= float(max_order_capacity_breach_ratio)),
            note="must pass with breach rows check",
        )
    )
    if cancel_ratio > float(max_cancel_capacity_used_ratio):
        advisories.append(
            "cancel_capacity_peak_ratio_high:"
            + f"{cancel_ratio:.6f}>max_ratio:{float(max_cancel_capacity_used_ratio):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cancel_capacity_peak_ratio",
            level="advisory",
            metric="cancel_capacity_used_ratio_max",
            comparator="max",
            value=cancel_ratio,
            threshold=float(max_cancel_capacity_used_ratio),
            passed=(cancel_ratio <= float(max_cancel_capacity_used_ratio)),
            note="peak ratio is advisory; hard fail uses breach rows + breach ratio",
        )
    )
    if cancel_breach_rows > int(max_cancel_capacity_breach_rows) and cancel_breach_ratio > float(max_cancel_capacity_breach_ratio):
        findings.append(
            "performance_cancel_capacity_used_ratio_too_high:"
            + f"breach_rows:{cancel_breach_rows}>max:{int(max_cancel_capacity_breach_rows)}"
            + f":breach_ratio:{cancel_breach_ratio:.6f}>max:{float(max_cancel_capacity_breach_ratio):.6f}"
            + f":peak:{cancel_ratio:.6f}>max_ratio:{float(max_cancel_capacity_used_ratio):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cancel_capacity_breach_rows",
            level="hard_fail",
            metric="cancel_capacity_breach_rows",
            comparator="max",
            value=float(cancel_breach_rows),
            threshold=float(max_cancel_capacity_breach_rows),
            passed=(cancel_breach_rows <= int(max_cancel_capacity_breach_rows)),
            note="must pass with breach ratio check",
        )
    )
    decision_trace.append(
        decision_item(
            check="cancel_capacity_breach_ratio",
            level="hard_fail",
            metric="cancel_capacity_breach_ratio",
            comparator="max",
            value=cancel_breach_ratio,
            threshold=float(max_cancel_capacity_breach_ratio),
            passed=(cancel_breach_ratio <= float(max_cancel_capacity_breach_ratio)),
            note="must pass with breach rows check",
        )
    )
    if inactive_max > float(max_latency_inactive_cycles):
        findings.append(
            f"performance_latency_inactive_cycles_too_high:{inactive_max:.6f}>max:{float(max_latency_inactive_cycles):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="latency_sampling_inactive_cycles_max",
            level="hard_fail",
            metric="latency_sampling_inactive_cycles_max",
            comparator="max",
            value=inactive_max,
            threshold=float(max_latency_inactive_cycles),
            passed=(inactive_max <= float(max_latency_inactive_cycles)),
            note="hard fail threshold",
        )
    )
    if span_market_data_max > float(max_market_data_span_ms):
        findings.append(
            f"performance_cycle_span_market_data_too_high:{span_market_data_max:.6f}>max:{float(max_market_data_span_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_span_market_data_max",
            level="warning",
            metric="cycle_span_market_data_max_ms",
            comparator="max",
            value=span_market_data_max,
            threshold=float(max_market_data_span_ms),
            passed=(span_market_data_max <= float(max_market_data_span_ms)),
            note="execution-pipeline decomposition signal",
        )
    )
    if span_strategy_exec_max > float(max_strategy_exec_span_ms):
        findings.append(
            f"performance_cycle_span_strategy_exec_too_high:{span_strategy_exec_max:.6f}>max:{float(max_strategy_exec_span_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_span_strategy_exec_max",
            level="warning",
            metric="cycle_span_strategy_exec_max_ms",
            comparator="max",
            value=span_strategy_exec_max,
            threshold=float(max_strategy_exec_span_ms),
            passed=(span_strategy_exec_max <= float(max_strategy_exec_span_ms)),
            note="execution-pipeline decomposition signal",
        )
    )
    if span_state_io_max > float(max_state_io_span_ms):
        findings.append(
            f"performance_cycle_span_state_io_too_high:{span_state_io_max:.6f}>max:{float(max_state_io_span_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_span_state_io_max",
            level="warning",
            metric="cycle_span_state_io_max_ms",
            comparator="max",
            value=span_state_io_max,
            threshold=float(max_state_io_span_ms),
            passed=(span_state_io_max <= float(max_state_io_span_ms)),
            note="execution-pipeline decomposition signal",
        )
    )
    if span_status_io_max > float(max_status_io_span_ms):
        findings.append(
            f"performance_cycle_span_status_io_too_high:{span_status_io_max:.6f}>max:{float(max_status_io_span_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_span_status_io_max",
            level="warning",
            metric="cycle_span_status_io_max_ms",
            comparator="max",
            value=span_status_io_max,
            threshold=float(max_status_io_span_ms),
            passed=(span_status_io_max <= float(max_status_io_span_ms)),
            note="execution-pipeline decomposition signal",
        )
    )
    if span_residual_max > float(max_cycle_residual_span_ms):
        findings.append(
            f"performance_cycle_span_residual_too_high:{span_residual_max:.6f}>max:{float(max_cycle_residual_span_ms):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="cycle_span_residual_max",
            level="warning",
            metric="cycle_span_residual_max_ms",
            comparator="max",
            value=span_residual_max,
            threshold=float(max_cycle_residual_span_ms),
            passed=(span_residual_max <= float(max_cycle_residual_span_ms)),
            note="execution-pipeline decomposition signal",
        )
    )
    warnings.extend(
        [
            f"decision_trace_warn:{item['check']}"
            for item in decision_trace
            if (item.get("level") == "warning" and not bool(item.get("passed", False)))
        ]
    )

    return {
        "log_dir": str(log_dir.resolve()),
        "run_id": selected_run_id,
        "status_row_count": len(rows),
        "metrics": {
            "cycle_latency_p95_ms": cycle_p95,
            "cycle_latency_max_ms": cycle_max,
            "process_rss_mb_max": rss_max,
            "process_cpu_percent_p95": cpu_p95,
            "process_cpu_percent_max": cpu_max,
            "system_load1_max": load1_max,
            "system_mem_available_mb_min": mem_available_min,
            "system_swap_used_mb_max": swap_used_max,
            "order_capacity_used_ratio_max": order_ratio,
            "order_capacity_breach_rows": order_breach_rows,
            "order_capacity_breach_ratio": order_breach_ratio,
            "cancel_capacity_used_ratio_max": cancel_ratio,
            "cancel_capacity_breach_rows": cancel_breach_rows,
            "cancel_capacity_breach_ratio": cancel_breach_ratio,
            "latency_sampling_inactive_cycles_max": inactive_max,
            "latency_sampling_inactive_cycles_scoped_row_count": float(len(inactive_vals)),
            "cycle_span_market_data_max_ms": span_market_data_max,
            "cycle_span_strategy_exec_max_ms": span_strategy_exec_max,
            "cycle_span_state_io_max_ms": span_state_io_max,
            "cycle_span_status_io_max_ms": span_status_io_max,
            "cycle_span_residual_max_ms": span_residual_max,
        },
        "thresholds": {
            "max_cycle_latency_p95_ms": float(max_cycle_latency_p95_ms),
            "max_cycle_latency_max_ms": float(max_cycle_latency_max_ms),
            "max_process_rss_mb": float(max_process_rss_mb),
            "max_order_capacity_used_ratio": float(max_order_capacity_used_ratio),
            "max_cancel_capacity_used_ratio": float(max_cancel_capacity_used_ratio),
            "max_order_capacity_breach_rows": int(max_order_capacity_breach_rows),
            "max_cancel_capacity_breach_rows": int(max_cancel_capacity_breach_rows),
            "max_order_capacity_breach_ratio": float(max_order_capacity_breach_ratio),
            "max_cancel_capacity_breach_ratio": float(max_cancel_capacity_breach_ratio),
            "max_latency_inactive_cycles": float(max_latency_inactive_cycles),
            "max_market_data_span_ms": float(max_market_data_span_ms),
            "max_strategy_exec_span_ms": float(max_strategy_exec_span_ms),
            "max_state_io_span_ms": float(max_state_io_span_ms),
            "max_status_io_span_ms": float(max_status_io_span_ms),
            "max_cycle_residual_span_ms": float(max_cycle_residual_span_ms),
            "min_status_rows": int(min_status_rows),
        },
        "finding_count": len(findings),
        "findings": findings,
        "warning_count": len(warnings),
        "warnings": sorted(set(warnings)),
        "advisory_count": len(advisories),
        "advisories": sorted(set(advisories)),
        "context_hints": context_hints,
        "decision_trace": decision_trace,
        "threshold_semantics": {
            "hard_fail": [
                "status_row_count",
                "cycle_latency_p95_ms",
                "cycle_latency_max_ms",
                "process_rss_mb_max",
                "order_capacity_breach_rows + order_capacity_breach_ratio",
                "cancel_capacity_breach_rows + cancel_capacity_breach_ratio",
                "latency_sampling_inactive_cycles_max",
            ],
            "warning": [
                "cycle_span_market_data_max_ms",
                "cycle_span_strategy_exec_max_ms",
                "cycle_span_state_io_max_ms",
                "cycle_span_status_io_max_ms",
                "cycle_span_residual_max_ms",
            ],
            "advisory": [
                "order_capacity_used_ratio_max",
                "cancel_capacity_used_ratio_max",
            ],
        },
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro performance budget gate")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Run_id filter")
    parser.add_argument("--max-cycle-latency-p95-ms", type=float, default=1000.0)
    parser.add_argument("--max-cycle-latency-max-ms", type=float, default=3000.0)
    parser.add_argument("--max-process-rss-mb", type=float, default=2048.0)
    parser.add_argument("--max-order-capacity-used-ratio", type=float, default=1.0)
    parser.add_argument("--max-cancel-capacity-used-ratio", type=float, default=1.0)
    parser.add_argument("--max-order-capacity-breach-rows", type=int, default=0)
    parser.add_argument("--max-cancel-capacity-breach-rows", type=int, default=0)
    parser.add_argument("--max-order-capacity-breach-ratio", type=float, default=0.0)
    parser.add_argument("--max-cancel-capacity-breach-ratio", type=float, default=0.0)
    parser.add_argument("--max-latency-inactive-cycles", type=float, default=120.0)
    parser.add_argument("--max-market-data-span-ms", type=float, default=3000.0)
    parser.add_argument("--max-strategy-exec-span-ms", type=float, default=3000.0)
    parser.add_argument("--max-state-io-span-ms", type=float, default=3000.0)
    parser.add_argument("--max-status-io-span-ms", type=float, default=3000.0)
    parser.add_argument("--max-cycle-residual-span-ms", type=float, default=3000.0)
    parser.add_argument("--min-status-rows", type=int, default=5)
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_gate(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id).strip() or None,
        max_cycle_latency_p95_ms=max(0.0, float(args.max_cycle_latency_p95_ms)),
        max_cycle_latency_max_ms=max(0.0, float(args.max_cycle_latency_max_ms)),
        max_process_rss_mb=max(0.0, float(args.max_process_rss_mb)),
        max_order_capacity_used_ratio=max(0.0, float(args.max_order_capacity_used_ratio)),
        max_cancel_capacity_used_ratio=max(0.0, float(args.max_cancel_capacity_used_ratio)),
        max_order_capacity_breach_rows=max(0, int(args.max_order_capacity_breach_rows)),
        max_cancel_capacity_breach_rows=max(0, int(args.max_cancel_capacity_breach_rows)),
        max_order_capacity_breach_ratio=max(0.0, min(1.0, float(args.max_order_capacity_breach_ratio))),
        max_cancel_capacity_breach_ratio=max(0.0, min(1.0, float(args.max_cancel_capacity_breach_ratio))),
        max_latency_inactive_cycles=max(0.0, float(args.max_latency_inactive_cycles)),
        max_market_data_span_ms=max(0.0, float(args.max_market_data_span_ms)),
        max_strategy_exec_span_ms=max(0.0, float(args.max_strategy_exec_span_ms)),
        max_state_io_span_ms=max(0.0, float(args.max_state_io_span_ms)),
        max_status_io_span_ms=max(0.0, float(args.max_status_io_span_ms)),
        max_cycle_residual_span_ms=max(0.0, float(args.max_cycle_residual_span_ms)),
        min_status_rows=max(1, int(args.min_status_rows)),
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
