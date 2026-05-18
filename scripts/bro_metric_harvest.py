#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import pathlib
import statistics
from collections import Counter, defaultdict
from typing import Any

from prodesk.edge_truth_contract import lifecycle_phase_from_payload, maker_phase_allowed_from_payload
from prodesk.historical_recovery_replay_compat import (
    HISTORICAL_RECOVERY_ACTIVE_FIELD as HISTORICAL_LIFECYCLE_RESIDUE_ACTIVE_FIELD,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = REPO_ROOT / "logs_exec" / "paper_universal" / "reports"
DEFAULT_OUT_DIR = REPO_ROOT / "logs_exec" / "paper_universal" / "metric_harvest"
TOOL_NAME = "Forge Masters Archiver"
TOOL_ALIAS = "FMA"
TOOL_SCHEMA_VERSION = 9
MANIFEST_SCHEMA_VERSION = 1
REPORT_FILES = [
    "validation_summary.json",
    "canonical_paper_validation.json",
    "readiness_gate.json",
    "nightly_soak_report.json",
    "edge_truth_audit.json",
    "order_lifecycle_audit.json",
    "outcome_truth_audit.json",
    "soak_hardening_gate.json",
]
SUPPORT_JSON_FILES = [
    "maker_fight_admission_shadow_summary.json",
    "maker_fight_admission_calibration_audit.json",
    "maker_cannon_late_window_probe_summary.json",
    "maker_mid_window_probe_summary.json",
]
SUPPORT_JSONL_FILES = [
    "maker_fight_admission_shadow.jsonl",
    "maker_cannon_late_window_probe.jsonl",
    "maker_mid_window_probe.jsonl",
]
CSV_FIELDS = [
    "run_id",
    "run_id_source",
    "profile_name",
    "runtime_classification",
    "runtime_classification_source",
    "validation_status",
    "validation_status_source",
    "validation_ok",
    "validation_policy_failed",
    "validation_determinism_consistent",
    "gate_passed",
    "highest_passing_stage",
    "blocking_stage",
    "available_report_count",
    "missing_report_count",
    "load_error_count",
    "duration_minutes",
    "quote_uptime_ratio",
    "maker_submits",
    "maker_fills",
    "maker_filled_orders",
    "maker_fill_rate",
    "maker_fills_per_filled_order",
    "maker_timing_gate_blocked_decision",
    "maker_no_submit_total_count",
    "maker_quote_quality_skip_total_count",
    "maker_sizing_reject_total_count",
    "maker_replace_guard_min_rest_count",
    "maker_window_active_row_count",
    "maker_window_submit_count",
    "maker_window_replace_guard_count",
    "maker_window_quote_quality_skip_total_count",
    "maker_window_submit_rate",
    "maker_window_replace_guard_rate",
    "maker_window_quote_quality_skip_rate",
    "maker_window_sizing_reject_count",
    "maker_window_sizing_reject_rate",
    "maker_window_low_price_viability_floor",
    "maker_window_viable_row_count",
    "maker_window_impossible_row_count",
    "maker_min_notional_max_shares_conflict_rows",
    "maker_window_queue_depth_on_viable_targets_count",
    "maker_window_queue_depth_on_impossible_targets_count",
    "maker_raw_queue_depth_near_threshold_event_count",
    "maker_raw_queue_depth_hard_miss_event_count",
    "maker_cannon_probe_row_count",
    "maker_cannon_probe_candidate_count",
    "maker_cannon_probe_full_candidate_count",
    "maker_cannon_probe_latent_market_full_candidate_count",
    "maker_cannon_probe_external_blocked_latent_market_full_candidate_count",
    "maker_reference_missing_ratio",
    "maker_complete_record_count",
    "maker_incomplete_record_count",
    "maker_complete_bad_ratio",
    "maker_incomplete_bad_ratio",
    "maker_multifill_complete_count",
    "maker_multifill_complete_incorrect_ratio",
    "maker_execution_rescue_overcome_count",
    "maker_outcome_horizon_ms",
    "maker_eval_basis_requires_reconstructed_midpoint_flag",
    "maker_same_target_repeat_cluster_count",
    "maker_complement_pair_cluster_count",
    "maker_complement_pair_cluster_decision_debt_sum",
    "taker_submits",
    "taker_fills",
    "taker_fill_rate",
    "taker_decision_count",
    "taker_decision_to_submit_rate",
    "taker_submit_capable_to_submit_rate",
    "taker_final_window_decision_count",
    "taker_final_window_decision_ratio",
    "taker_outside_window_decision_count",
    "taker_outside_window_decision_ratio",
    "wallet_deployable_capital",
    "wallet_reserved_ratio",
    "wallet_order_submit_eligible",
    "wallet_authority_status_class",
    "risk_global_exposure_utilization_ratio_max",
    "valuation_degraded_ratio",
    "valuation_hard_degraded_ratio",
    "valuation_bruise_state",
    "valuation_dominant_reason_family_run",
    "valuation_dominant_held_unpriceable_cause_run",
    "settlement_hold_required_count",
    "open_order_cleanup_required_count",
    "unresolved_lifecycle_obligation_count",
    "cancel_fail_closed_count",
    "risk_reject_total_count",
    "market_data_pair_truth_missing_ratio",
    "market_data_pair_truth_one_sided_ratio",
    "market_data_ws_ratio",
    "chainlink_down_ratio",
    "book_feed_down_ratio",
    "book_feed_worker_unusable_rows",
    "chainlink_worker_unusable_rows",
    "book_feed_worker_restart_exhausted_rows",
    "chainlink_worker_restart_exhausted_rows",
    "gateway_heartbeat_missing_or_invalid_rows",
    "gateway_heartbeat_disabled_resting_rows",
    "gateway_matching_engine_error_rows",
    "outcome_attribution_usability_ratio",
    "outcome_truth_attribution_usability_ratio",
    "error_rows",
    "runtime_primary_suppression_cause",
]
MISSING = object()

_LEGACY_STAGE_TO_LIFECYCLE_PHASE = {
    "OBSERVE": "prepare",
    "EVALUATE": "prepare",
    "MAKER_POSITION": "prepare",
    "MAKER_TAKER_SELECTIVE": "prepare",
    "SNIPER_PRIMARY": "prepare",
    "LATE_DIAGNOSTIC": "prepare",
    "EXTREME_ONLY": "prepare",
    "MAKER_LATE_WINDOW": "maker_window",
    "TAKER_COMMITMENT": "taker_window",
    "EXPIRED": "resolve",
}


def _safe_get(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _serialize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_lifecycle_phase(row: dict[str, Any]) -> str:
    raw_phase = str(row.get("lifecycle_phase") or "").strip().lower()
    if raw_phase:
        return raw_phase
    lifecycle_phase = str(lifecycle_phase_from_payload(row) or "").strip().lower()
    return lifecycle_phase or "unknown"


def _probe_maker_phase_allowed(row: dict[str, Any]) -> bool:
    if "maker_phase_allowed" in row:
        return bool(row.get("maker_phase_allowed"))
    return bool(maker_phase_allowed_from_payload(row))


def _merge_counter(counter: Counter[str], value: dict[str, Any] | None) -> None:
    if not isinstance(value, dict):
        return
    for key, raw in value.items():
        if isinstance(raw, (int, float)):
            counter[key] += raw


def _merge_nested_counter(
    target: defaultdict[str, Counter[str]],
    value: dict[str, Any] | None,
) -> None:
    if not isinstance(value, dict):
        return
    for outer_key, inner_value in value.items():
        if not isinstance(inner_value, dict):
            continue
        bucket = target[str(outer_key)]
        for inner_key, raw in inner_value.items():
            if isinstance(raw, (int, float)):
                bucket[str(inner_key)] += raw


def _merge_reference_basis_summary(
    decision_counter: Counter[str],
    eval_counter: Counter[str],
    value: dict[str, Any] | None,
) -> None:
    if not isinstance(value, dict):
        return
    _merge_counter(decision_counter, value.get("decision_reference_basis_distribution"))
    _merge_counter(eval_counter, value.get("eval_reference_basis_distribution"))


def _first_present(
    loaded: dict[str, Any],
    candidates: list[tuple[str, tuple[str, ...]]],
) -> tuple[Any, str | None, tuple[str, ...] | None]:
    for source_name, path in candidates:
        source = loaded.get(source_name)
        value = _safe_get(source, path) if source is not None else MISSING
        if value is not MISSING:
            return value, source_name, path
    return MISSING, None, None


def _normalize_validation_status(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    return None


def _sum_counter_values(value: dict[str, Any] | None) -> float | None:
    if not isinstance(value, dict):
        return None
    total = 0.0
    for raw in value.values():
        if isinstance(raw, (int, float)):
            total += raw
    return total


def _sum_matching_counter_values(value: dict[str, Any] | None, prefix: str) -> float | None:
    if not isinstance(value, dict):
        return None
    total = 0.0
    for key, raw in value.items():
        if key.startswith(prefix) and isinstance(raw, (int, float)):
            total += raw
    return total


def _counter_value_or_zero(value: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(key, 0.0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _numeric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None, "median": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def _numeric_summary_with_percentiles(values: list[float]) -> dict[str, float | None]:
    summary = _numeric_summary(values)
    if not values:
        return {
            **summary,
            "p50": None,
            "p90": None,
        }
    ordered = sorted(values)
    p90_index = int((len(ordered) - 1) * 0.9)
    return {
        **summary,
        "p50": statistics.median(ordered),
        "p90": ordered[p90_index],
    }


def _safe_ratio(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _coerce_fill_count(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _maker_shadow_timing_band_class(sec_to_expiry: Any) -> str:
    value = _coerce_float(sec_to_expiry)
    if value is None:
        return "unknown"
    if value < 0.0:
        return "expired"
    if value <= 10.0:
        return "le_10s"
    if value <= 15.0:
        return "10_to_15s"
    if value <= 20.0:
        return "15_to_20s"
    if value <= 30.0:
        return "20_to_30s"
    if value <= 45.0:
        return "30_to_45s"
    if value <= 60.0:
        return "45_to_60s"
    if value <= 90.0:
        return "60_to_90s"
    return "gt_90s"


def _sorted_counter_dict(counter: Counter[str]) -> dict[str, float]:
    return {key: counter[key] for key in sorted(counter.keys())}


def _derive_maker_window_viability_target_summary(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "target_ref": item.get("target_ref"),
                "viability_class": item.get("viability_class"),
                "window_row_count": item.get("window_row_count"),
                "submitted_count": item.get("submitted_count"),
                "sizing_reject_count": item.get("sizing_reject_count"),
                "impossible_viability_row_count": item.get("impossible_viability_row_count"),
                "viable_viability_row_count": item.get("viable_viability_row_count"),
                "unknown_viability_row_count": item.get("unknown_viability_row_count"),
                "quote_quality_skip_queue_depth_count": item.get("quote_quality_skip_queue_depth_count"),
                "market_probability_min": item.get("market_probability_min"),
                "market_probability_p50": item.get("market_probability_p50"),
                "market_probability_max": item.get("market_probability_max"),
            }
        )
    entries.sort(
        key=lambda item: (
            -float(_coerce_float(item.get("impossible_viability_row_count")) or 0.0),
            -float(_coerce_float(item.get("viable_viability_row_count")) or 0.0),
            -float(_coerce_float(item.get("window_row_count")) or 0.0),
            str(item.get("target_ref") or ""),
        )
    )
    return entries[:8]


def _derive_maker_window_queue_depth_target_summary(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        queue_depth_count = float(_coerce_float(item.get("quote_quality_skip_queue_depth_count")) or 0.0)
        if queue_depth_count <= 0.0:
            continue
        entries.append(
            {
                "target_ref": item.get("target_ref"),
                "viability_class": item.get("viability_class"),
                "quote_quality_skip_queue_depth_count": queue_depth_count,
                "window_row_count": item.get("window_row_count"),
                "submitted_count": item.get("submitted_count"),
                "sizing_reject_count": item.get("sizing_reject_count"),
                "impossible_viability_row_count": item.get("impossible_viability_row_count"),
                "viable_viability_row_count": item.get("viable_viability_row_count"),
                "market_probability_p50": item.get("market_probability_p50"),
            }
        )
    entries.sort(
        key=lambda item: (
            -float(_coerce_float(item.get("quote_quality_skip_queue_depth_count")) or 0.0),
            -float(_coerce_float(item.get("window_row_count")) or 0.0),
            str(item.get("target_ref") or ""),
        )
    )
    return entries[:8]


def _load_jsonl_records(path: pathlib.Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _parse_utc_ts(value: Any) -> dt.datetime | None:
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


def _maker_session_bucket_from_ts(value: Any) -> str:
    parsed = _parse_utc_ts(value)
    if parsed is None:
        return "unknown"
    hour = int(parsed.hour)
    if 0 <= hour < 8:
        return "asia_dominant_heuristic"
    if 12 <= hour < 20:
        return "usa_europe_peak_heuristic"
    return "transition_heuristic"


def _select_maker_outcome_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for record in records:
        lane = str(record.get("submission_lane_truth") or record.get("submission_scope_hint") or "").strip().lower()
        if lane == "maker":
            selected.append(record)
    return selected


def _classify_maker_lifecycle_gap(record: dict[str, Any]) -> str:
    status = str(record.get("outcome_truth_status") or "").strip().lower()
    fill_count = _coerce_fill_count(record.get("fill_count")) or 0
    if status == "complete":
        return "complete_multifill" if fill_count >= 2 else "complete_single_fill"
    if fill_count >= 1:
        return "partial_fill_incomplete"
    return "no_fill_incomplete"


def _build_fill_count_quality_distribution(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    distribution: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        fill_count = _coerce_fill_count(record.get("fill_count"))
        if fill_count is None:
            continue
        quality = str(record.get("decision_quality") or "unknown")
        distribution[str(fill_count)][quality] += 1
    return {
        fill_count: _sorted_counter_dict(counter)
        for fill_count, counter in sorted(distribution.items(), key=lambda item: int(item[0]))
    }


def _build_target_cluster_summaries(
    records: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    by_target: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        target_ref = str(record.get("target_ref") or "unknown")
        by_target[target_ref].append(record)

    repeated_targets: list[dict[str, Any]] = []
    repeat_summary: list[dict[str, Any]] = []
    for target_ref, subset in by_target.items():
        submit_count = len(subset)
        if submit_count <= 1:
            continue
        complete_subset = [
            record
            for record in subset
            if str(record.get("outcome_truth_status") or "").strip().lower() == "complete"
        ]
        complete_quality = Counter(str(record.get("decision_quality") or "unknown") for record in complete_subset)
        complete_decision_debt_sum = sum(_coerce_float(record.get("decision_component_x_size")) or 0.0 for record in complete_subset)
        repeated_targets.append(
            {
                "target_ref": target_ref,
                "submit_count": submit_count,
                "complete_count": len(complete_subset),
                "complete_decision_debt_sum": complete_decision_debt_sum,
            }
        )
        repeat_summary.append(
            {
                "target_ref": target_ref,
                "submit_count": submit_count,
                "complete_count": len(complete_subset),
                "complete_incorrect_count": int(complete_quality.get("incorrect", 0)),
                "complete_decision_debt_sum": complete_decision_debt_sum,
                "order_submit_ids_sample": [
                    str(record.get("order_submit_id") or "")
                    for record in sorted(subset, key=lambda item: str(item.get("order_submit_id") or ""))[:4]
                ],
            }
        )

    sort_key = lambda item: (-int(item["submit_count"]), -abs(float(item["complete_decision_debt_sum"])), str(item["target_ref"]))
    repeated_targets.sort(key=sort_key)
    repeat_summary.sort(key=sort_key)
    return len(repeated_targets), repeated_targets[:8], repeat_summary[:8]


def _detect_complement_pair_clusters(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_rows = [
        record
        for record in records
        if str(record.get("outcome_truth_status") or "").strip().lower() == "complete"
        and str(record.get("decision_quality") or "").strip().lower() == "incorrect"
    ]
    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(candidate_rows):
        left_side = str(left.get("order_side") or "").strip().upper()
        left_target = str(left.get("target_ref") or "")
        left_decision_mid = _coerce_float(left.get("mid_price_decision"))
        left_eval_mid = _coerce_float(left.get("mid_price_eval"))
        left_edge_expected = _coerce_float(left.get("edge_expected"))
        left_fill_size = _coerce_float(left.get("fill_total_size"))
        left_decision_debt = _coerce_float(left.get("decision_component_x_size")) or 0.0
        left_fill_count = _coerce_fill_count(left.get("fill_count"))
        if left_side not in {"BUY", "SELL"}:
            continue
        if (
            left_decision_mid is None
            or left_eval_mid is None
            or left_edge_expected is None
            or left_fill_size is None
            or left_fill_size <= 0
        ):
            continue
        for right in candidate_rows[left_index + 1 :]:
            right_side = str(right.get("order_side") or "").strip().upper()
            right_target = str(right.get("target_ref") or "")
            if right_side not in {"BUY", "SELL"} or right_side == left_side:
                continue
            if not left_target or not right_target or left_target == right_target:
                continue
            right_decision_mid = _coerce_float(right.get("mid_price_decision"))
            right_eval_mid = _coerce_float(right.get("mid_price_eval"))
            right_edge_expected = _coerce_float(right.get("edge_expected"))
            right_fill_size = _coerce_float(right.get("fill_total_size"))
            right_decision_debt = _coerce_float(right.get("decision_component_x_size")) or 0.0
            right_fill_count = _coerce_fill_count(right.get("fill_count"))
            if (
                right_decision_mid is None
                or right_eval_mid is None
                or right_edge_expected is None
                or right_fill_size is None
                or right_fill_size <= 0
            ):
                continue
            decision_mid_sum = left_decision_mid + right_decision_mid
            eval_mid_sum = left_eval_mid + right_eval_mid
            edge_expected_sum = left_edge_expected + right_edge_expected
            fill_size_relative_diff = abs(left_fill_size - right_fill_size) / max(left_fill_size, right_fill_size)
            if abs(decision_mid_sum - 1.0) > 0.02:
                continue
            if abs(eval_mid_sum - 1.0) > 0.02:
                continue
            if abs(edge_expected_sum) > 0.03:
                continue
            if fill_size_relative_diff > 0.001:
                continue
            pair_score = abs(decision_mid_sum - 1.0) + abs(eval_mid_sum - 1.0) + abs(edge_expected_sum) + fill_size_relative_diff
            candidates.append(
                {
                    "order_submit_id_a": str(left.get("order_submit_id") or ""),
                    "order_submit_id_b": str(right.get("order_submit_id") or ""),
                    "target_ref_a": left_target,
                    "target_ref_b": right_target,
                    "order_side_a": left_side,
                    "order_side_b": right_side,
                    "fill_count_a": left_fill_count,
                    "fill_count_b": right_fill_count,
                    "fill_total_size_a": left_fill_size,
                    "fill_total_size_b": right_fill_size,
                    "decision_mid_sum": decision_mid_sum,
                    "eval_mid_sum": eval_mid_sum,
                    "edge_expected_sum": edge_expected_sum,
                    "fill_size_relative_diff": fill_size_relative_diff,
                    "combined_decision_debt_sum": left_decision_debt + right_decision_debt,
                    "pair_score": pair_score,
                }
            )

    candidates.sort(
        key=lambda item: (
            item["pair_score"],
            -abs(item["combined_decision_debt_sum"]),
            item["order_submit_id_a"],
            item["order_submit_id_b"],
        )
    )
    used_order_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["order_submit_id_a"] in used_order_ids or candidate["order_submit_id_b"] in used_order_ids:
            continue
        used_order_ids.add(candidate["order_submit_id_a"])
        used_order_ids.add(candidate["order_submit_id_b"])
        selected.append(candidate)
    selected.sort(key=lambda item: (-abs(item["combined_decision_debt_sum"]), item["pair_score"]))
    return selected


def _build_maker_outcome_forensics(records: list[dict[str, Any]]) -> dict[str, Any]:
    maker_records = _select_maker_outcome_records(records)
    if not maker_records:
        return {}

    complete_records = [
        record for record in maker_records if str(record.get("outcome_truth_status") or "").strip().lower() == "complete"
    ]
    incomplete_records = [record for record in maker_records if record not in complete_records]

    complete_incorrect_count = sum(
        1 for record in complete_records if str(record.get("decision_quality") or "").strip().lower() == "incorrect"
    )
    incomplete_incorrect_count = sum(
        1 for record in incomplete_records if str(record.get("decision_quality") or "").strip().lower() == "incorrect"
    )

    multifill_complete_records = [
        record for record in complete_records if (_coerce_fill_count(record.get("fill_count")) or 0) >= 2
    ]
    multifill_incorrect_count = sum(
        1 for record in multifill_complete_records if str(record.get("decision_quality") or "").strip().lower() == "incorrect"
    )

    lifecycle_gap_counts = Counter(_classify_maker_lifecycle_gap(record) for record in maker_records)
    fill_count_quality_distribution = _build_fill_count_quality_distribution(complete_records)
    target_cluster_count, target_cluster_summary, same_target_repeat_summary = _build_target_cluster_summaries(
        maker_records
    )
    complement_pair_examples = _detect_complement_pair_clusters(maker_records)

    rescue_ratios: list[float] = []
    rescue_overcome_count = 0
    for record in complete_records:
        decision_debt = _coerce_float(record.get("decision_component_x_size"))
        execution_rescue = _coerce_float(record.get("execution_component_x_size"))
        if decision_debt is None or execution_rescue is None:
            continue
        if abs(execution_rescue) > abs(decision_debt):
            rescue_overcome_count += 1
        if decision_debt != 0:
            rescue_ratios.append(abs(execution_rescue) / abs(decision_debt))

    decision_basis_counts = Counter(str(record.get("decision_reference_basis") or "unknown") for record in maker_records)
    eval_basis_counts = Counter(str(record.get("eval_reference_basis") or "unknown") for record in maker_records)
    horizon_values = sorted(
        {
            int(value)
            for value in (_coerce_fill_count(record.get("evaluation_horizon_ms")) for record in maker_records)
            if value is not None
        }
    )
    dominant_eval_basis = eval_basis_counts.most_common(1)[0][0] if eval_basis_counts else None

    multifill_fill_count_distribution = _build_fill_count_quality_distribution(multifill_complete_records)
    multifill_decision_debt_by_fill_count: Counter[str] = Counter()
    for record in multifill_complete_records:
        fill_count = _coerce_fill_count(record.get("fill_count"))
        decision_debt = _coerce_float(record.get("decision_component_x_size"))
        if fill_count is None or decision_debt is None:
            continue
        multifill_decision_debt_by_fill_count[str(fill_count)] += decision_debt

    return {
        "maker_complete_record_count": len(complete_records),
        "maker_incomplete_record_count": len(incomplete_records),
        "maker_complete_bad_ratio": _safe_ratio(complete_incorrect_count, len(complete_records)),
        "maker_incomplete_bad_ratio": _safe_ratio(incomplete_incorrect_count, len(incomplete_records)),
        "maker_multifill_complete_count": len(multifill_complete_records),
        "maker_multifill_complete_incorrect_ratio": _safe_ratio(multifill_incorrect_count, len(multifill_complete_records)),
        "maker_fill_count_quality_distribution": fill_count_quality_distribution,
        "maker_execution_rescue_overcome_count": rescue_overcome_count,
        "maker_execution_rescue_ratio_summary": _numeric_summary_with_percentiles(rescue_ratios),
        "maker_complement_pair_cluster_count": len(complement_pair_examples),
        "maker_complement_pair_cluster_decision_debt_sum": sum(
            float(example.get("combined_decision_debt_sum") or 0.0) for example in complement_pair_examples
        ),
        "maker_target_cluster_summary": target_cluster_summary,
        "maker_lifecycle_gap_class_distribution": dict(
            sorted((key, float(value)) for key, value in lifecycle_gap_counts.items())
        ),
        "maker_lifecycle_gap_complete_vs_incomplete_split": {
            "complete": float(len(complete_records)),
            "incomplete": float(len(incomplete_records)),
        },
        "maker_outcome_horizon_ms": horizon_values[0] if len(horizon_values) == 1 else None,
        "maker_reference_basis_summary": {
            "decision_reference_basis_distribution": _sorted_counter_dict(decision_basis_counts),
            "eval_reference_basis_distribution": _sorted_counter_dict(eval_basis_counts),
        },
        "maker_horizon_sensitivity_note": (
            "Canonical maker outcome severity is horizon-sensitive; treat the fixed 5000ms lens as the current ruler, not invariant truth."
            if horizon_values
            else None
        ),
        "maker_eval_basis_requires_reconstructed_midpoint_flag": dominant_eval_basis == "edge_market_midpoint_series"
        if dominant_eval_basis is not None
        else None,
        "maker_same_target_repeat_cluster_count": target_cluster_count,
        "maker_same_target_repeat_cluster_summary": same_target_repeat_summary,
        "maker_complement_pair_cluster_examples": complement_pair_examples[:5],
        "maker_multifill_wound_summary": {
            "complete_multifill_count": len(multifill_complete_records),
            "complete_multifill_incorrect_count": multifill_incorrect_count,
            "complete_multifill_incorrect_ratio": _safe_ratio(multifill_incorrect_count, len(multifill_complete_records)),
            "fill_count_quality_distribution": multifill_fill_count_distribution,
            "decision_debt_by_fill_count": dict(
                sorted((key, float(value)) for key, value in multifill_decision_debt_by_fill_count.items())
            ),
        },
    }


def _maker_truth_population_note() -> dict[str, Any]:
    return {
        "decision_cycle_truth": {
            "surface": "edge_truth.action_rows",
            "population": "decision cycles",
            "warning": "Decision-cycle rows are not submit counts and must not be treated as completed outcomes.",
        },
        "submit_truth": {
            "surface": "outcome_truth_records.jsonl",
            "population": "order_submit records",
            "warning": "Submit truth includes records that never fill or never mature into complete outcomes.",
        },
        "filled_order_truth": {
            "surface": "execution_paths.maker_filled_orders",
            "population": "orders with at least one fill",
            "warning": "Filled-order counts sit between submit truth and fill-event truth; do not read them as full execution quality.",
        },
        "fill_event_truth": {
            "surface": "execution_quality_decision_reference_lane_attribution",
            "population": "fill events",
            "warning": "Fill-event economics can look favorable even when completed order-level decision quality is bad.",
        },
        "complete_outcome_truth": {
            "surface": "outcome_truth_audit complete records",
            "population": "matured complete order outcomes",
            "warning": "Complete-outcome truth is the right lane for decision-quality debt, but it is still measured under a fixed short-horizon lens.",
        },
        "warnings": {
            "maker_fill_rate": "Order-completion rate, not fill-event rate.",
            "maker_fills_per_filled_order": "High values can indicate multi-fill churn rather than healthy maker participation.",
            "execution_quality_decision_reference_lane_attribution": "Report-only fill-event execution surface; do not equate it with order-level outcome truth.",
            "outcome_truth_complete_subset": "Complete outcome records are a subset of submits; compare them against incomplete records deliberately, not implicitly.",
        },
    }


def _update_metric_catalog(
    catalog: dict[str, dict[str, dict[str, Any]]],
    source_name: str,
    data: Any,
    prefix: str = "",
) -> None:
    source_bucket = catalog.setdefault(source_name, {})
    key = prefix or "__root__"
    entry = source_bucket.setdefault(key, {"presence_count": 0, "types": Counter()})
    entry["presence_count"] += 1
    entry["types"][type(data).__name__] += 1
    if isinstance(data, dict):
        for child_key, child_value in data.items():
            child_prefix = f"{prefix}.{child_key}" if prefix else child_key
            _update_metric_catalog(catalog, source_name, child_value, child_prefix)


def _load_reports(run_dir: pathlib.Path) -> tuple[dict[str, Any], list[str], list[str], dict[str, str]]:
    loaded: dict[str, Any] = {}
    available: list[str] = []
    missing: list[str] = []
    load_errors: dict[str, str] = {}
    for report_name in REPORT_FILES:
        report_path = run_dir / report_name
        if not report_path.exists():
            missing.append(report_name)
            continue
        try:
            loaded[report_name] = json.loads(report_path.read_text(encoding="utf-8"))
            available.append(report_name)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover - guarded by tests on result surface
            load_errors[report_name] = str(exc)
    return loaded, available, missing, load_errors


def _load_support_artifacts(
    run_dir: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, str]]:
    loaded_json: dict[str, Any] = {}
    loaded_jsonl: dict[str, list[dict[str, Any]]] = {}
    load_errors: dict[str, str] = {}
    for name in SUPPORT_JSON_FILES:
        path = run_dir / name
        if not path.exists():
            continue
        try:
            loaded_json[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive honesty
            load_errors[name] = str(exc)
    for name in SUPPORT_JSONL_FILES:
        path = run_dir / name
        try:
            rows = _load_jsonl_records(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive honesty
            load_errors[name] = str(exc)
            continue
        if rows is not None:
            loaded_jsonl[name] = rows
    return loaded_json, loaded_jsonl, load_errors


def _load_run_contract(
    run_dir: pathlib.Path,
    loaded: dict[str, Any],
    run_id_hint: str | None,
) -> tuple[dict[str, Any] | None, pathlib.Path | None, str | None]:
    candidate_paths: list[pathlib.Path] = []
    nightly_report = loaded.get("nightly_soak_report.json")
    if isinstance(nightly_report, dict):
        report_contract_path = nightly_report.get("run_contract_path")
        if isinstance(report_contract_path, str) and report_contract_path.strip():
            candidate_paths.append(pathlib.Path(report_contract_path))
    report_root = run_dir.parent.parent if run_dir.parent.name == "reports" else run_dir.parent
    if run_id_hint:
        candidate_paths.append(report_root / f"run_contract_{run_id_hint}.json")
    seen: set[pathlib.Path] = set()
    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            continue
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive honesty
            return None, resolved, str(exc)
        if isinstance(payload, dict):
            return payload, resolved, None
    return None, None, None


def _record_path(
    row: dict[str, Any],
    provenance: dict[str, Any],
    loaded: dict[str, Any],
    field_name: str,
    source_name: str,
    path: tuple[str, ...],
) -> None:
    source = loaded.get(source_name)
    value = _safe_get(source, path) if source is not None else MISSING
    row[field_name] = None if value is MISSING else value
    provenance[field_name] = {
        "source": source_name,
        "path": ".".join(path),
        "present": value is not MISSING,
    }


def _record_first_present(
    row: dict[str, Any],
    provenance: dict[str, Any],
    loaded: dict[str, Any],
    field_name: str,
    candidates: list[tuple[str, tuple[str, ...]]],
) -> None:
    value, source_name, path = _first_present(loaded, candidates)
    row[field_name] = None if value is MISSING else value
    provenance[field_name] = {
        "source": source_name,
        "path": ".".join(path) if path is not None else None,
        "present": value is not MISSING,
        "candidate_count": len(candidates),
    }


def _record_derived(
    row: dict[str, Any],
    provenance: dict[str, Any],
    field_name: str,
    value: Any,
    source_name: str,
    derivation: str,
) -> None:
    row[field_name] = value
    provenance[field_name] = {
        "source": source_name,
        "path": None,
        "present": value is not None,
        "derivation": derivation,
    }


def _count_list_field(source: dict[str, Any] | None, path: tuple[str, ...]) -> int | None:
    value = _safe_get(source, path) if source is not None else MISSING
    if value is MISSING:
        return None
    if isinstance(value, list):
        return len(value)
    return None


def normalize_run(run_dir: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    loaded, available, missing, load_errors = _load_reports(run_dir)
    support_loaded, support_rows, support_load_errors = _load_support_artifacts(run_dir)
    loaded_all = dict(loaded)
    loaded_all.update(support_loaded)
    outcome_truth_records_error: str | None = None
    outcome_truth_records: list[dict[str, Any]] | None = None
    outcome_truth_records_path = run_dir / "outcome_truth_records.jsonl"
    try:
        outcome_truth_records = _load_jsonl_records(outcome_truth_records_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive artifact honesty
        outcome_truth_records_error = str(exc)
    row: dict[str, Any] = {
        "report_dir": str(run_dir),
        "report_dir_name": run_dir.name,
        "available_reports": available,
        "missing_reports": missing,
        "load_errors": load_errors,
        "support_artifact_errors": {
            **({"outcome_truth_records.jsonl": outcome_truth_records_error} if outcome_truth_records_error is not None else {}),
            **support_load_errors,
        },
    }
    provenance: dict[str, Any] = {}
    loaded = loaded_all

    _record_first_present(
        row,
        provenance,
        loaded_all,
        "run_id",
        [
            ("canonical_paper_validation.json", ("run_id",)),
            ("validation_summary.json", ("run_id",)),
            ("nightly_soak_report.json", ("artifact_identity", "run_id")),
        ],
    )
    if row["run_id"] is None:
        _record_derived(row, provenance, "run_id", run_dir.name, "report_dir", "fallback to run directory name")
    _record_derived(
        row,
        provenance,
        "run_id_source",
        provenance["run_id"]["source"] or "report_dir",
        provenance["run_id"]["source"] or "report_dir",
        "selected source for run_id",
    )
    run_contract_payload, run_contract_path, run_contract_error = _load_run_contract(
        run_dir=run_dir,
        loaded=loaded_all,
        run_id_hint=str(row.get("run_id") or ""),
    )
    _record_derived(
        row,
        provenance,
        "run_contract_path",
        str(run_contract_path) if run_contract_path is not None else None,
        "run_contract" if run_contract_path is not None else "none",
        "resolved run-contract path for historical timing/session truth",
    )
    if run_contract_error is not None:
        row["support_artifact_errors"]["run_contract"] = run_contract_error
    if run_contract_payload is not None:
        _record_derived(
            row,
            provenance,
            "run_session_type",
            run_contract_payload.get("session_type"),
            "run_contract",
            "copied from run contract",
        )
        _record_derived(
            row,
            provenance,
            "run_start_ts_utc",
            run_contract_payload.get("start_ts"),
            "run_contract",
            "copied from run contract",
        )
        _record_derived(
            row,
            provenance,
            "run_stop_ts_utc",
            run_contract_payload.get("stop_ts"),
            "run_contract",
            "copied from run contract",
        )
        start_ts_utc = str(run_contract_payload.get("start_ts") or "")
        parsed_start = _parse_utc_ts(start_ts_utc)
        _record_derived(
            row,
            provenance,
            "run_start_hour_utc",
            int(parsed_start.hour) if parsed_start is not None else None,
            "run_contract",
            "derived UTC start hour from run contract",
        )
        _record_derived(
            row,
            provenance,
            "run_start_date_utc",
            parsed_start.date().isoformat() if parsed_start is not None else None,
            "run_contract",
            "derived UTC start date from run contract",
        )
        _record_derived(
            row,
            provenance,
            "run_start_session_bucket",
            _maker_session_bucket_from_ts(start_ts_utc),
            "run_contract",
            "derived heuristic session bucket from run start timestamp",
        )
    _record_first_present(
        row,
        provenance,
        loaded,
        "session_phase",
        [
            ("canonical_paper_validation.json", ("session_phase",)),
            ("validation_summary.json", ("session_phase",)),
            ("nightly_soak_report.json", ("session_phase",)),
            ("soak_hardening_gate.json", ("session_phase",)),
        ],
    )
    _record_first_present(
        row,
        provenance,
        loaded,
        "runtime_classification",
        [
            ("nightly_soak_report.json", ("runtime_classification", "classification")),
            ("canonical_paper_validation.json", ("runtime_classification",)),
        ],
    )
    _record_derived(
        row,
        provenance,
        "runtime_classification_source",
        provenance["runtime_classification"]["source"],
        provenance["runtime_classification"]["source"] or "none",
        "selected source for runtime classification",
    )
    _record_first_present(
        row,
        provenance,
        loaded,
        "highest_passing_stage",
        [
            ("readiness_gate.json", ("highest_passing_stage",)),
            ("canonical_paper_validation.json", ("highest_passing_stage",)),
            ("soak_hardening_gate.json", ("readiness", "highest_passing_stage")),
        ],
    )
    _record_first_present(
        row,
        provenance,
        loaded,
        "blocking_stage",
        [
            ("readiness_gate.json", ("blocking_stage",)),
            ("canonical_paper_validation.json", ("blocking_stage",)),
            ("soak_hardening_gate.json", ("readiness", "blocking_stage")),
        ],
    )
    _record_first_present(
        row,
        provenance,
        loaded,
        "recommended_next_stage",
        [
            ("readiness_gate.json", ("recommended_next_stage",)),
            ("canonical_paper_validation.json", ("recommended_next_stage",)),
        ],
    )
    _record_first_present(
        row,
        provenance,
        loaded,
        "promotion_eligible",
        [
            ("nightly_soak_report.json", ("runtime_classification", "promotion_eligible")),
            ("canonical_paper_validation.json", ("promotion_eligible",)),
        ],
    )
    direct_specs = [
        ("profile_name", "nightly_soak_report.json", ("artifact_identity", "profile_name")),
        ("validation_ok", "validation_summary.json", ("ok",)),
        ("validation_overall_exit_code", "validation_summary.json", ("overall_exit_code",)),
        ("validation_validator_determinism_ok", "validation_summary.json", ("validator_determinism_ok",)),
        ("validation_edge_truth_determinism_ok", "validation_summary.json", ("edge_truth_determinism_ok",)),
        ("validation_non_edge_determinism_ok", "validation_summary.json", ("non_edge_determinism_ok",)),
        ("validation_outcome_truth_usability", "validation_summary.json", ("outcome_truth_usability",)),
        ("validation_policy_failed", "canonical_paper_validation.json", ("policy_failed",)),
        ("validation_execution_error", "canonical_paper_validation.json", ("execution_error",)),
        ("validation_determinism_consistent", "canonical_paper_validation.json", ("determinism_consistent",)),
        ("validation_known_policy_exit", "canonical_paper_validation.json", ("known_policy_exit",)),
        ("validation_script_exit_code", "canonical_paper_validation.json", ("script_exit_code",)),
        ("validation_missing_reports", "canonical_paper_validation.json", ("missing_reports",)),
        ("validation_parse_error_reports", "canonical_paper_validation.json", ("parse_error_reports",)),
        ("gate_passed", "canonical_paper_validation.json", ("gate_passed",)),
        ("reports_complete", "canonical_paper_validation.json", ("reports_complete",)),
        ("runtime_classification_name", "nightly_soak_report.json", ("runtime_classification", "classification")),
        ("runtime_primary_suppression_cause", "nightly_soak_report.json", ("runtime_classification", "primary_suppression_cause")),
        ("runtime_promotion_eligible", "nightly_soak_report.json", ("runtime_classification", "promotion_eligible")),
        ("runtime_active_targets_seen", "nightly_soak_report.json", ("runtime_classification", "metrics", "active_targets_seen")),
        ("runtime_meaningful_participation", "nightly_soak_report.json", ("runtime_classification", "metrics", "meaningful_participation")),
        ("runtime_decision_events", "nightly_soak_report.json", ("runtime_classification", "metrics", "decision_events")),
        ("runtime_required_market_truth_disconnected_rows", "nightly_soak_report.json", ("runtime_classification", "metrics", "required_market_truth_disconnected_rows")),
        ("duration_minutes", "nightly_soak_report.json", ("duration_minutes",)),
        ("error_rows", "nightly_soak_report.json", ("error_rows",)),
        ("quote_uptime_ratio", "nightly_soak_report.json", ("quote_uptime_ratio",)),
        ("quote_active_ratio", "nightly_soak_report.json", ("quote_diagnostics", "quote_active_ratio")),
        ("participation_ratio", "nightly_soak_report.json", ("quote_diagnostics", "participation_ratio")),
        ("maker_submits", "nightly_soak_report.json", ("execution_paths", "maker_submits")),
        ("maker_fills", "nightly_soak_report.json", ("execution_paths", "maker_fills")),
        ("maker_filled_orders", "nightly_soak_report.json", ("execution_paths", "maker_filled_orders")),
        ("maker_fill_rate", "nightly_soak_report.json", ("execution_paths", "maker_fill_rate")),
        ("maker_fire_rate_per_min", "nightly_soak_report.json", ("execution_paths", "maker_fire_rate_per_min")),
        ("maker_timing_gate_blocked_decision", "nightly_soak_report.json", ("maker_competitiveness", "timing_gate_blocked_count_decision")),
        ("maker_timing_gate_blocked_edge_eval", "nightly_soak_report.json", ("maker_competitiveness", "timing_gate_blocked_count_edge_eval")),
        ("maker_reference_direct_midpoint_activity", "nightly_soak_report.json", ("edge_truth", "maker_reference_direct_midpoint_activity")),
        ("maker_reference_missing_activity", "nightly_soak_report.json", ("edge_truth", "maker_reference_missing_activity")),
        ("maker_reference_direct_midpoint_action_activity", "nightly_soak_report.json", ("edge_truth", "maker_reference_direct_midpoint_action_activity")),
        ("maker_reference_missing_action_activity", "nightly_soak_report.json", ("edge_truth", "maker_reference_missing_action_activity")),
        ("maker_regression_triggered", "nightly_soak_report.json", ("maker_regression_sentinel", "triggered")),
        ("maker_regression_freeze_state", "nightly_soak_report.json", ("maker_regression_sentinel", "maker_behavior_freeze_state")),
        ("maker_regression_watch_item_primary", "nightly_soak_report.json", ("maker_regression_sentinel", "watch_item_primary")),
        ("maker_window_active_row_count", "nightly_soak_report.json", ("maker_fireability", "active_window_row_count")),
        ("maker_window_submit_count", "nightly_soak_report.json", ("maker_fireability", "active_window_submit_count")),
        (
            "maker_window_replace_guard_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_replace_guard_count"),
        ),
        (
            "maker_window_quote_quality_skip_fill_probability_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_quote_quality_skip_fill_probability_count"),
        ),
        (
            "maker_window_quote_quality_skip_queue_depth_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_quote_quality_skip_queue_depth_count"),
        ),
        (
            "maker_window_sizing_reject_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_sizing_reject_count"),
        ),
        (
            "maker_window_low_price_viability_floor",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_low_price_viability_floor"),
        ),
        (
            "maker_window_viable_row_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_viable_row_count"),
        ),
        (
            "maker_window_impossible_row_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_impossible_row_count"),
        ),
        (
            "maker_window_unknown_viability_row_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_unknown_viability_row_count"),
        ),
        (
            "maker_window_viable_target_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_viable_target_count"),
        ),
        (
            "maker_window_impossible_target_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_impossible_target_count"),
        ),
        (
            "maker_window_mixed_viability_target_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_mixed_viability_target_count"),
        ),
        (
            "maker_window_unknown_viability_target_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_unknown_viability_target_count"),
        ),
        (
            "maker_window_low_price_conflict_price_band",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_low_price_conflict_price_band"),
        ),
        (
            "maker_window_queue_depth_on_viable_targets_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_queue_depth_on_viable_targets_count"),
        ),
        (
            "maker_window_queue_depth_on_impossible_targets_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_queue_depth_on_impossible_targets_count"),
        ),
        (
            "maker_window_queue_depth_on_mixed_targets_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_queue_depth_on_mixed_targets_count"),
        ),
        (
            "maker_window_queue_depth_on_unknown_targets_count",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_queue_depth_on_unknown_targets_count"),
        ),
        (
            "maker_window_target_summary",
            "nightly_soak_report.json",
            ("maker_fireability", "active_window_target_summary"),
        ),
        (
            "maker_quote_quality_skip_fill_probability_severity_bins",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_quote_quality_skip_severity", "fill_probability_delta_bins"),
        ),
        (
            "maker_quote_quality_skip_queue_depth_severity_bins",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_quote_quality_skip_severity", "queue_depth_delta_bins"),
        ),
        (
            "maker_raw_queue_depth_event_count",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_queue_depth_event_count"),
        ),
        (
            "maker_raw_queue_depth_near_threshold_event_count",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_queue_depth_near_threshold_event_count"),
        ),
        (
            "maker_raw_queue_depth_hard_miss_event_count",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_queue_depth_hard_miss_event_count"),
        ),
        (
            "maker_raw_queue_depth_unknown_severity_event_count",
            "nightly_soak_report.json",
            ("maker_fireability", "raw_queue_depth_unknown_severity_event_count"),
        ),
        (
            "maker_min_notional_max_shares_conflict_rows",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_min_notional_max_shares_conflict_rows"),
        ),
        (
            "maker_size_resolution_rows",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_size_resolution_rows"),
        ),
        (
            "maker_submit_rows",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_submit_rows"),
        ),
        (
            "maker_sizing_reject_price_min",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_sizing_reject_price_min"),
        ),
        (
            "maker_sizing_reject_price_p50",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_sizing_reject_price_p50"),
        ),
        (
            "maker_sizing_reject_price_max",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_sizing_reject_price_max"),
        ),
        (
            "maker_sizing_reject_reason_distribution",
            "nightly_soak_report.json",
            ("maker_sizing_competitiveness", "maker_sizing_reject_reason_distribution"),
        ),
        ("maker_no_submission_cause_distribution", "nightly_soak_report.json", ("edge_truth", "maker_no_submission_cause_distribution")),
        ("maker_block_reason_distribution", "nightly_soak_report.json", ("edge_truth", "maker_block_reason_distribution")),
        ("taker_submits", "nightly_soak_report.json", ("execution_paths", "taker_bonus_submits")),
        ("taker_fills", "nightly_soak_report.json", ("execution_paths", "taker_bonus_fills")),
        ("taker_filled_orders", "nightly_soak_report.json", ("execution_paths", "taker_bonus_filled_orders")),
        ("taker_fill_rate", "nightly_soak_report.json", ("execution_paths", "taker_bonus_fill_rate")),
        ("taker_fire_rate_per_min", "nightly_soak_report.json", ("execution_paths", "taker_bonus_fire_rate_per_min")),
        ("taker_decision_count", "nightly_soak_report.json", ("taker_competitiveness", "decision_count")),
        ("taker_decision_to_submit_rate", "nightly_soak_report.json", ("taker_competitiveness", "decision_to_submit_rate")),
        ("taker_submit_capable_to_submit_rate", "nightly_soak_report.json", ("taker_competitiveness", "submit_capable_to_submit_rate")),
        ("taker_submit_capable_dynamic_to_submit_rate", "nightly_soak_report.json", ("taker_competitiveness", "submit_capable_dynamic_to_submit_rate")),
        ("taker_blocked_decision_count", "nightly_soak_report.json", ("taker_competitiveness", "blocked_decision_count")),
        ("taker_risk_reject_after_capable_count", "nightly_soak_report.json", ("taker_competitiveness", "risk_reject_after_capable_count_edge_eval")),
        ("taker_fill_without_submit_stage_count", "nightly_soak_report.json", ("taker_competitiveness", "fill_without_submit_stage_count")),
        ("taker_hidden_blockage_decision_to_dynamic_predicted_delta", "nightly_soak_report.json", ("taker_competitiveness", "hidden_blockage_detector", "decision_to_dynamic_predicted_delta")),
        ("taker_hidden_blockage_dynamic_predicted_to_submit_delta", "nightly_soak_report.json", ("taker_competitiveness", "hidden_blockage_detector", "dynamic_predicted_to_submit_delta")),
        ("taker_hidden_blockage_submit_to_fill_delta", "nightly_soak_report.json", ("taker_competitiveness", "hidden_blockage_detector", "submit_to_fill_delta")),
        ("taker_decision_timing_window_distribution", "nightly_soak_report.json", ("taker_competitiveness", "decision_timing_window_distribution")),
        ("taker_submit_timing_window_distribution", "nightly_soak_report.json", ("taker_competitiveness", "submit_timing_window_distribution")),
        ("taker_fill_stage_distribution", "nightly_soak_report.json", ("taker_competitiveness", "fill_stage_distribution")),
        ("taker_decision_predicted_reject_reason_distribution", "nightly_soak_report.json", ("taker_competitiveness", "decision_predicted_reject_reason_distribution")),
        ("taker_stage_final_risk_reject_reason_distribution", "nightly_soak_report.json", ("taker_competitiveness", "stage_final_risk_reject_reason_distribution")),
        ("wallet_authority_status_class", "nightly_soak_report.json", ("wallet_authority", "authority_status_class")),
        ("wallet_authoritative_contract_present", "nightly_soak_report.json", ("wallet_authority", "authoritative_wallet_contract_present")),
        ("wallet_order_capable_live", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "order_capable_live")),
        ("wallet_order_submit_eligible", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "order_submit_eligible")),
        ("wallet_deployable_capital", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "deployable_capital")),
        ("wallet_stable_balance_total", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "stable_balance_total")),
        ("wallet_open_reserved", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "open_reserved")),
        ("wallet_protected_reserve", "nightly_soak_report.json", ("wallet_authority", "latest_contract", "protected_reserve")),
        ("wallet_reservation_mismatch_candidate", "nightly_soak_report.json", ("wallet_authority", "reservation_mismatch_candidate")),
        ("wallet_reservation_mismatch_delta_usdc", "nightly_soak_report.json", ("wallet_authority", "reservation_mismatch_delta_usdc")),
        ("wallet_live_truth_gap_reasons", "nightly_soak_report.json", ("wallet_authority", "live_truth_gap_reasons")),
        ("wallet_contract_source", "nightly_soak_report.json", ("wallet_authority", "wallet_contract_surface_source")),
        ("risk_global_exposure_cap_reject_count", "nightly_soak_report.json", ("risk_competitiveness", "global_exposure_cap_reject_count")),
        ("risk_global_exposure_near_cap_count", "nightly_soak_report.json", ("risk_competitiveness", "global_exposure_near_cap_count")),
        ("risk_global_exposure_utilization_ratio_max", "nightly_soak_report.json", ("risk_competitiveness", "global_exposure_utilization_ratio_max")),
        ("risk_global_exposure_utilization_ratio_p50", "nightly_soak_report.json", ("risk_competitiveness", "global_exposure_utilization_ratio_p50")),
        ("risk_global_exposure_utilization_ratio_p90", "nightly_soak_report.json", ("risk_competitiveness", "global_exposure_utilization_ratio_p90")),
        ("risk_reject_reason_distribution", "nightly_soak_report.json", ("risk_competitiveness", "reject_reason_distribution")),
        ("risk_reject_count_by_lane", "nightly_soak_report.json", ("risk_competitiveness", "reject_count_by_lane")),
        ("valuation_degraded_ratio", "nightly_soak_report.json", ("valuation_truth", "valuation_degraded_ratio")),
        ("valuation_hard_degraded_ratio", "nightly_soak_report.json", ("valuation_truth", "valuation_hard_degraded_ratio")),
        ("valuation_bruise_state", "nightly_soak_report.json", ("valuation_truth", "valuation_bruise_state")),
        ("valuation_dominant_reason_family_run", "nightly_soak_report.json", ("valuation_truth", "valuation_dominant_reason_family_run")),
        (
            "valuation_dominant_held_unpriceable_cause_run",
            "nightly_soak_report.json",
            ("valuation_truth", "valuation_dominant_held_unpriceable_cause_run"),
        ),
        (
            "valuation_dominant_source_degraded_rows",
            "nightly_soak_report.json",
            ("valuation_truth", "valuation_dominant_source_degraded_rows"),
        ),
        (
            "valuation_degraded_reason_family_counts_run",
            "nightly_soak_report.json",
            ("valuation_truth", "valuation_degraded_reason_family_counts_run"),
        ),
        (
            "valuation_source_counts_degraded_rows",
            "nightly_soak_report.json",
            ("valuation_truth", "valuation_source_counts_degraded_rows"),
        ),
        (
            "held_unpriceable_cause_counts_run",
            "nightly_soak_report.json",
            ("valuation_truth", "held_unpriceable_cause_counts_run"),
        ),
        ("valuation_hard_degraded_enter_count", "nightly_soak_report.json", ("valuation_truth", "valuation_hard_degraded_enter_count")),
        ("valuation_hard_degraded_clear_count", "nightly_soak_report.json", ("valuation_truth", "valuation_hard_degraded_clear_count")),
        ("held_unpriceable_started_count", "nightly_soak_report.json", ("valuation_truth", "held_unpriceable_started_count")),
        ("held_unpriceable_recovered_count", "nightly_soak_report.json", ("valuation_truth", "held_unpriceable_recovered_count")),
        ("held_unpriceable_unrecovered_meaningful_count", "nightly_soak_report.json", ("valuation_truth", "held_unpriceable_unrecovered_meaningful_count")),
        ("held_unpriceable_unrecovered_non_defect_count", "nightly_soak_report.json", ("valuation_truth", "held_unpriceable_unrecovered_non_defect_count")),
        ("held_unpriceable_escalation_ratio", "nightly_soak_report.json", ("valuation_truth", "held_unpriceable_escalation_ratio")),
        ("settlement_hold_required_count", "nightly_soak_report.json", ("valuation_truth", "settlement_hold_required_count")),
        ("open_order_cleanup_required_count", "nightly_soak_report.json", ("valuation_truth", "open_order_cleanup_required_count")),
        (
            "unresolved_lifecycle_obligation_count",
            "nightly_soak_report.json",
            ("valuation_truth", "unresolved_lifecycle_obligation_count"),
        ),
        ("cancel_fail_closed_count", "nightly_soak_report.json", ("valuation_truth", "cancel_fail_closed_count")),
        (
            "market_data_pair_truth_missing_ratio",
            "nightly_soak_report.json",
            ("market_data_source", "pair_truth_missing_pair_row_ratio"),
        ),
        (
            "market_data_pair_truth_one_sided_ratio",
            "nightly_soak_report.json",
            ("market_data_source", "pair_truth_one_sided_row_ratio"),
        ),
        ("market_data_ws_delta", "nightly_soak_report.json", ("market_data_source", "book_updates_ws_delta")),
        ("market_data_total_delta", "nightly_soak_report.json", ("market_data_source", "book_updates_total_delta")),
        ("stale_data_disarmed_edge_blocks", "nightly_soak_report.json", ("stale_data", "disarmed_edge_blocks")),
        ("secondary_oracle_connected_ratio_when_enabled", "nightly_soak_report.json", ("secondary_oracle_pyth", "connected_ratio_when_enabled")),
        ("secondary_oracle_unavailable_sample_count", "nightly_soak_report.json", ("secondary_oracle_pyth", "unavailable_sample_count")),
        ("latency_median_ms", "nightly_soak_report.json", ("latency_distribution_ms", "median_ms")),
        ("latency_p90_ms", "nightly_soak_report.json", ("latency_distribution_ms", "p90_ms")),
        ("latency_p95_ms", "nightly_soak_report.json", ("latency_distribution_ms", "p95_ms")),
        ("latency_sample_count", "nightly_soak_report.json", ("latency_distribution_ms", "sample_count")),
        ("execution_capture_minus_adverse", "nightly_soak_report.json", ("execution_quality", "capture_minus_adverse")),
        ("execution_realized_capture", "nightly_soak_report.json", ("execution_quality", "realized_capture")),
        ("execution_adverse_selection", "nightly_soak_report.json", ("execution_quality", "adverse_selection")),
        ("execution_fills_scored", "nightly_soak_report.json", ("execution_quality", "fills_scored")),
        ("outcome_attribution_usability_ratio", "outcome_truth_audit.json", ("attribution_usability_ratio",)),
        ("outcome_filled_complete_ratio", "outcome_truth_audit.json", ("filled_complete_ratio",)),
        ("outcome_complete_classification_ratio", "outcome_truth_audit.json", ("complete_classification_ratio",)),
        ("outcome_record_claim_boundary_distribution", "outcome_truth_audit.json", ("record_claim_boundary_distribution",)),
        ("outcome_status_distribution", "outcome_truth_audit.json", ("outcome_truth_status_distribution",)),
        ("slippage_summary", "outcome_truth_audit.json", ("slippage_summary",)),
        ("adverse_selection_summary", "outcome_truth_audit.json", ("adverse_selection_summary",)),
        ("integrity_ok", "soak_hardening_gate.json", ("integrity", "ok")),
        ("integrity_finding_count", "soak_hardening_gate.json", ("integrity", "finding_count")),
        ("integrity_warning_count", "soak_hardening_gate.json", ("integrity", "warning_count")),
        ("integrity_fill_event_count", "soak_hardening_gate.json", ("integrity", "fill_event_count")),
        ("integrity_cancel_all_event_count", "soak_hardening_gate.json", ("integrity", "cancel_all_event_count")),
        ("integrity_duplicate_fill_trade_id_count", "soak_hardening_gate.json", ("integrity", "duplicate_fill_trade_id_count")),
        ("integrity_event_row_count", "soak_hardening_gate.json", ("integrity", "event_row_count")),
        ("integrity_status_row_count", "soak_hardening_gate.json", ("integrity", "status_row_count")),
        ("chainlink_down_ratio", "soak_hardening_gate.json", ("websocket", "metrics", "chainlink_down_ratio")),
        ("chainlink_last_tick_age_p95_sec", "soak_hardening_gate.json", ("websocket", "metrics", "chainlink_last_tick_age_p95_sec")),
        ("book_feed_down_ratio", "soak_hardening_gate.json", ("websocket", "metrics", "book_feed_down_ratio")),
        ("book_feed_last_msg_age_p95_sec", "soak_hardening_gate.json", ("websocket", "metrics", "book_feed_last_msg_age_p95_sec")),
        ("book_feed_worker_unusable_rows", "soak_hardening_gate.json", ("websocket", "metrics", "book_feed_worker_unusable_rows")),
        ("chainlink_worker_unusable_rows", "soak_hardening_gate.json", ("websocket", "metrics", "chainlink_worker_unusable_rows")),
        (
            "book_feed_worker_restart_exhausted_rows",
            "soak_hardening_gate.json",
            ("websocket", "metrics", "book_feed_worker_restart_exhausted_rows"),
        ),
        (
            "chainlink_worker_restart_exhausted_rows",
            "soak_hardening_gate.json",
            ("websocket", "metrics", "chainlink_worker_restart_exhausted_rows"),
        ),
        (
            "gateway_heartbeat_missing_or_invalid_rows",
            "soak_hardening_gate.json",
            ("websocket", "metrics", "gateway_heartbeat_missing_or_invalid_rows"),
        ),
        (
            "gateway_heartbeat_disabled_resting_rows",
            "soak_hardening_gate.json",
            ("websocket", "metrics", "gateway_heartbeat_disabled_resting_rows"),
        ),
        (
            "gateway_matching_engine_error_rows",
            "soak_hardening_gate.json",
            ("websocket", "metrics", "gateway_matching_engine_error_rows"),
        ),
        ("lifecycle_ok", "order_lifecycle_audit.json", ("ok",)),
        ("lifecycle_finding_count", "order_lifecycle_audit.json", ("finding_count",)),
        ("lifecycle_warning_count", "order_lifecycle_audit.json", ("warning_count",)),
        ("lifecycle_order_submit_decision_missing_count", "order_lifecycle_audit.json", ("order_submit_decision_missing_count",)),
        ("lifecycle_edge_decision_ingest_missing_count", "order_lifecycle_audit.json", ("edge_decision_ingest_missing_count",)),
        ("lifecycle_edge_decision_submit_missing_count", "order_lifecycle_audit.json", ("edge_decision_submit_missing_count",)),
        ("lifecycle_duplicate_fill_trade_id_count", "order_lifecycle_audit.json", ("duplicate_fill_trade_ids",)),
        ("lifecycle_duplicate_order_submit_id_count", "order_lifecycle_audit.json", ("duplicate_order_submit_ids",)),
        ("lifecycle_counts", "order_lifecycle_audit.json", ("lifecycle_counts",)),
        ("soak_gate_ok", "soak_hardening_gate.json", ("ok",)),
        ("soak_gate_readiness_blocking_stage", "soak_hardening_gate.json", ("readiness", "blocking_stage")),
        ("soak_gate_readiness_highest_passing_stage", "soak_hardening_gate.json", ("readiness", "highest_passing_stage")),
        ("soak_gate_websocket_ok", "soak_hardening_gate.json", ("websocket", "ok")),
        ("soak_gate_websocket_finding_count", "soak_hardening_gate.json", ("websocket", "finding_count")),
        ("soak_gate_integrity_ok", "soak_hardening_gate.json", ("integrity", "ok")),
    ]
    for field_name, source_name, path in direct_specs:
        _record_path(row, provenance, loaded, field_name, source_name, path)

    if row.get("gate_passed") is None and isinstance(row.get("validation_ok"), bool):
        _record_derived(
            row,
            provenance,
            "gate_passed",
            bool(row.get("validation_ok")),
            "validation_summary.json",
            "derived from validation ok when canonical summary is unavailable",
        )

    if row.get("valuation_bruise_state") is None:
        degraded_ratio = row.get("valuation_degraded_ratio")
        hard_enter_count = row.get("valuation_hard_degraded_enter_count")
        hard_clear_count = row.get("valuation_hard_degraded_clear_count")
        held_started_count = row.get("held_unpriceable_started_count")
        held_recovered_count = row.get("held_unpriceable_recovered_count")
        meaningful_unrecovered_count = row.get("held_unpriceable_unrecovered_meaningful_count")
        dust_unrecovered_count = row.get("held_unpriceable_unrecovered_non_defect_count")
        if dust_unrecovered_count is None:
            dust_unrecovered_count = row.get("held_unpriceable_unrecovered_dust_exempted_count")
        has_bruise_evidence = any(
            isinstance(value, (int, float)) and value > 0
            for value in (degraded_ratio, hard_enter_count, held_started_count)
        )
        if not has_bruise_evidence:
            bruise_state = "none"
        elif isinstance(meaningful_unrecovered_count, (int, float)) and meaningful_unrecovered_count > 0:
            bruise_state = "open_meaningful_unpriceable"
        elif isinstance(dust_unrecovered_count, (int, float)) and dust_unrecovered_count > 0:
            bruise_state = "open_dust_only_unpriceable"
        elif (
            isinstance(hard_enter_count, (int, float))
            and isinstance(hard_clear_count, (int, float))
            and hard_enter_count > hard_clear_count
        ):
            bruise_state = "hard_degraded_not_fully_cleared"
        elif (
            isinstance(held_started_count, (int, float))
            and isinstance(held_recovered_count, (int, float))
            and held_started_count > held_recovered_count
        ):
            bruise_state = "held_unpriceable_not_fully_recovered"
        else:
            bruise_state = "recovered_clean"
        _record_derived(
            row,
            provenance,
            "valuation_bruise_state",
            bruise_state,
            "derived",
            "derived from valuation/held-unpriceable counters when explicit bruise state is unavailable",
        )

    if row.get("valuation_dominant_held_unpriceable_cause_run") is None:
        cause_counts = row.get("held_unpriceable_cause_counts_run")
        dominant_cause = None
        if isinstance(cause_counts, dict) and cause_counts:
            dominant_cause = "none"
            dominant_value = 0.0
            for key, raw in sorted(cause_counts.items(), key=lambda kv: kv[0]):
                if isinstance(raw, (int, float)) and raw > dominant_value:
                    dominant_cause = str(key)
                    dominant_value = float(raw)
        _record_derived(
            row,
            provenance,
            "valuation_dominant_held_unpriceable_cause_run",
            dominant_cause or "none",
            "derived",
            "dominant key from held_unpriceable_cause_counts_run",
        )

    if row.get("valuation_dominant_source_degraded_rows") is None:
        source_counts = row.get("valuation_source_counts_degraded_rows")
        source_name = "valuation_source_counts_degraded_rows"
        if not isinstance(source_counts, dict) or not source_counts:
            source_counts = row.get("valuation_source_counts_run")
            source_name = "valuation_source_counts_run"
        dominant_source = None
        if isinstance(source_counts, dict) and source_counts:
            dominant_source = "none"
            dominant_value = 0.0
            for key, raw in sorted(source_counts.items(), key=lambda kv: kv[0]):
                if isinstance(raw, (int, float)) and raw > dominant_value:
                    dominant_source = str(key)
                    dominant_value = float(raw)
        _record_derived(
            row,
            provenance,
            "valuation_dominant_source_degraded_rows",
            dominant_source or "none",
            "derived",
            f"dominant key from {source_name}",
        )
    _record_first_present(
        row,
        provenance,
        loaded,
        "validation_status",
        [
            ("canonical_paper_validation.json", ("status",)),
            ("validation_summary.json", ("status",)),
        ],
    )
    if row["validation_status"] is None:
        fallback_status = _normalize_validation_status(row.get("validation_ok"))
        _record_derived(
            row,
            provenance,
            "validation_status",
            fallback_status,
            "derived",
            "normalized from validation_ok when explicit status is unavailable",
        )
    _record_derived(
        row,
        provenance,
        "validation_status_source",
        provenance["validation_status"]["source"] or "derived",
        provenance["validation_status"]["source"] or "derived",
        "selected source for validation status",
    )

    _record_derived(row, provenance, "available_report_count", len(available), "report_loader", "len(available_reports)")
    _record_derived(row, provenance, "missing_report_count", len(missing), "report_loader", "len(missing_reports)")
    _record_derived(row, provenance, "load_error_count", len(load_errors), "report_loader", "len(load_errors)")

    lifecycle_source = loaded.get("order_lifecycle_audit.json")
    _record_derived(
        row,
        provenance,
        "lifecycle_fill_without_submit_count",
        _count_list_field(lifecycle_source, ("fill_without_submit_order_ids",)),
        "order_lifecycle_audit.json",
        "len(fill_without_submit_order_ids)",
    )
    _record_derived(
        row,
        provenance,
        "lifecycle_cancel_without_submit_count",
        _count_list_field(lifecycle_source, ("cancel_without_submit_order_ids",)),
        "order_lifecycle_audit.json",
        "len(cancel_without_submit_order_ids)",
    )
    duplicate_fills = _count_list_field(lifecycle_source, ("duplicate_fill_trade_ids",))
    duplicate_orders = _count_list_field(lifecycle_source, ("duplicate_order_submit_ids",))
    if duplicate_fills is not None:
        row["lifecycle_duplicate_fill_trade_id_count"] = duplicate_fills
        provenance["lifecycle_duplicate_fill_trade_id_count"] = {
            "source": "order_lifecycle_audit.json",
            "path": "duplicate_fill_trade_ids",
            "present": True,
            "derivation": "len(duplicate_fill_trade_ids)",
        }
    if duplicate_orders is not None:
        row["lifecycle_duplicate_order_submit_id_count"] = duplicate_orders
        provenance["lifecycle_duplicate_order_submit_id_count"] = {
            "source": "order_lifecycle_audit.json",
            "path": "duplicate_order_submit_ids",
            "present": True,
            "derivation": "len(duplicate_order_submit_ids)",
        }
    final_window = None
    timing_dist = row.get("taker_decision_timing_window_distribution")
    if isinstance(timing_dist, dict):
        final_window = timing_dist.get("final_window")
    _record_derived(
        row,
        provenance,
        "taker_final_window_decision_count",
        final_window,
        "nightly_soak_report.json",
        "taker_competitiveness.decision_timing_window_distribution.final_window",
    )
    outside_window = None
    final15 = None
    if isinstance(timing_dist, dict):
        outside_window = timing_dist.get("outside_window")
        final15 = timing_dist.get("final15")
    _record_derived(
        row,
        provenance,
        "taker_outside_window_decision_count",
        outside_window,
        "nightly_soak_report.json",
        "taker_competitiveness.decision_timing_window_distribution.outside_window",
    )
    _record_derived(
        row,
        provenance,
        "taker_final15_decision_count",
        final15,
        "nightly_soak_report.json",
        "taker_competitiveness.decision_timing_window_distribution.final15",
    )
    decision_count = row.get("taker_decision_count")
    if isinstance(decision_count, (int, float)) and decision_count > 0:
        final_window_ratio = final_window / decision_count if isinstance(final_window, (int, float)) else None
        outside_window_ratio = outside_window / decision_count if isinstance(outside_window, (int, float)) else None
        decision_to_fill_rate = row["taker_fills"] / decision_count if isinstance(row.get("taker_fills"), (int, float)) else None
    else:
        final_window_ratio = None
        outside_window_ratio = None
        decision_to_fill_rate = None
    _record_derived(
        row,
        provenance,
        "taker_final_window_decision_ratio",
        final_window_ratio,
        "derived",
        "taker_final_window_decision_count / taker_decision_count",
    )
    _record_derived(
        row,
        provenance,
        "taker_outside_window_decision_ratio",
        outside_window_ratio,
        "derived",
        "taker_outside_window_decision_count / taker_decision_count",
    )
    _record_derived(
        row,
        provenance,
        "taker_decision_to_fill_rate",
        decision_to_fill_rate,
        "derived",
        "taker_fills / taker_decision_count",
    )
    direct_mid = row.get("maker_reference_direct_midpoint_activity")
    missing_reference = row.get("maker_reference_missing_activity")
    total_reference = 0.0
    if isinstance(direct_mid, (int, float)):
        total_reference += direct_mid
    if isinstance(missing_reference, (int, float)):
        total_reference += missing_reference
    missing_ratio = None
    if total_reference > 0 and isinstance(missing_reference, (int, float)):
        missing_ratio = missing_reference / total_reference
    _record_derived(
        row,
        provenance,
        "maker_reference_missing_ratio",
        missing_ratio,
        "derived",
        "maker_reference_missing_activity / (maker_reference_direct_midpoint_activity + maker_reference_missing_activity)",
    )
    _record_derived(
        row,
        provenance,
        "maker_fills_per_filled_order",
        _safe_ratio(row.get("maker_fills"), row.get("maker_filled_orders")),
        "derived",
        "maker_fills / maker_filled_orders",
    )
    stable_balance = row.get("wallet_stable_balance_total")
    open_reserved = row.get("wallet_open_reserved")
    reserved_ratio = None
    if isinstance(stable_balance, (int, float)) and stable_balance > 0 and isinstance(open_reserved, (int, float)):
        reserved_ratio = open_reserved / stable_balance
    _record_derived(
        row,
        provenance,
        "wallet_reserved_ratio",
        reserved_ratio,
        "derived",
        "wallet_open_reserved / wallet_stable_balance_total",
    )
    maker_no_submit = row.get("maker_no_submission_cause_distribution")
    _record_derived(
        row,
        provenance,
        "maker_no_submit_total_count",
        _sum_counter_values(maker_no_submit),
        "derived",
        "sum(maker_no_submission_cause_distribution.values())",
    )
    _record_derived(
        row,
        provenance,
        "maker_quote_quality_skip_total_count",
        _sum_matching_counter_values(maker_no_submit, "submit_rejected_quote_quality_"),
        "derived",
        "sum submit_rejected_quote_quality_* maker no-submit causes",
    )
    _record_derived(
        row,
        provenance,
        "maker_sizing_reject_total_count",
        _counter_value_or_zero(maker_no_submit, "submit_rejected_sizing_reject"),
        "derived",
        "maker_no_submission_cause_distribution.submit_rejected_sizing_reject",
    )
    _record_derived(
        row,
        provenance,
        "maker_replace_guard_min_rest_count",
        _counter_value_or_zero(maker_no_submit, "replace_guard_min_rest"),
        "derived",
        "maker_no_submission_cause_distribution.replace_guard_min_rest",
    )
    maker_window_active_rows = row.get("maker_window_active_row_count")
    maker_window_submits = row.get("maker_window_submit_count")
    maker_window_replace_guard = row.get("maker_window_replace_guard_count")
    maker_window_quote_quality_fill = row.get("maker_window_quote_quality_skip_fill_probability_count")
    maker_window_quote_quality_queue = row.get("maker_window_quote_quality_skip_queue_depth_count")
    maker_window_quote_quality_total = None
    if isinstance(maker_window_quote_quality_fill, (int, float)) or isinstance(maker_window_quote_quality_queue, (int, float)):
        maker_window_quote_quality_total = float(
            (maker_window_quote_quality_fill if isinstance(maker_window_quote_quality_fill, (int, float)) else 0.0)
            + (maker_window_quote_quality_queue if isinstance(maker_window_quote_quality_queue, (int, float)) else 0.0)
        )
    _record_derived(
        row,
        provenance,
        "maker_window_quote_quality_skip_total_count",
        maker_window_quote_quality_total,
        "derived",
        "maker_window_quote_quality_skip_fill_probability_count + maker_window_quote_quality_skip_queue_depth_count",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_submit_rate",
        _safe_ratio(maker_window_submits, maker_window_active_rows),
        "derived",
        "maker_window_submit_count / maker_window_active_row_count",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_replace_guard_rate",
        _safe_ratio(maker_window_replace_guard, maker_window_active_rows),
        "derived",
        "maker_window_replace_guard_count / maker_window_active_row_count",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_quote_quality_skip_rate",
        _safe_ratio(maker_window_quote_quality_total, maker_window_active_rows),
        "derived",
        "maker_window_quote_quality_skip_total_count / maker_window_active_row_count",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_sizing_reject_rate",
        _safe_ratio(row.get("maker_window_sizing_reject_count"), maker_window_active_rows),
        "derived",
        "maker_window_sizing_reject_count / maker_window_active_row_count",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_viability_target_summary",
        _derive_maker_window_viability_target_summary(row.get("maker_window_target_summary")),
        "derived",
        "derived from maker_window_target_summary viability fields",
    )
    _record_derived(
        row,
        provenance,
        "maker_window_queue_depth_target_summary",
        _derive_maker_window_queue_depth_target_summary(row.get("maker_window_target_summary")),
        "derived",
        "derived from maker_window_target_summary queue-depth burden fields",
    )
    risk_reject_reasons = row.get("risk_reject_reason_distribution")
    _record_derived(
        row,
        provenance,
        "risk_reject_total_count",
        _sum_counter_values(risk_reject_reasons),
        "derived",
        "sum(risk_reject_reason_distribution.values())",
    )
    total_book_updates = row.get("market_data_total_delta")
    ws_updates = row.get("market_data_ws_delta")
    ws_ratio = None
    if isinstance(total_book_updates, (int, float)) and total_book_updates > 0 and isinstance(ws_updates, (int, float)):
        ws_ratio = ws_updates / total_book_updates
    _record_derived(
        row,
        provenance,
        "market_data_ws_ratio",
        ws_ratio,
        "derived",
        "market_data_ws_delta / market_data_total_delta",
    )
    _record_derived(
        row,
        provenance,
        "outcome_truth_attribution_usability_ratio",
        row.get("outcome_attribution_usability_ratio"),
        "derived",
        "alias of outcome_attribution_usability_ratio for source-name clarity",
    )
    if outcome_truth_records is not None:
        maker_forensics = _build_maker_outcome_forensics(outcome_truth_records)
        for field_name, value in maker_forensics.items():
            _record_derived(
                row,
                provenance,
                field_name,
                value,
                "outcome_truth_records.jsonl",
                "derived maker outcome forensic surface from maker outcome truth records",
            )
    else:
        for field_name in (
            "maker_complete_record_count",
            "maker_incomplete_record_count",
            "maker_complete_bad_ratio",
            "maker_incomplete_bad_ratio",
            "maker_multifill_complete_count",
            "maker_multifill_complete_incorrect_ratio",
            "maker_fill_count_quality_distribution",
            "maker_execution_rescue_overcome_count",
            "maker_execution_rescue_ratio_summary",
            "maker_complement_pair_cluster_count",
            "maker_complement_pair_cluster_decision_debt_sum",
            "maker_target_cluster_summary",
            "maker_lifecycle_gap_class_distribution",
            "maker_lifecycle_gap_complete_vs_incomplete_split",
            "maker_outcome_horizon_ms",
            "maker_reference_basis_summary",
            "maker_horizon_sensitivity_note",
            "maker_eval_basis_requires_reconstructed_midpoint_flag",
            "maker_same_target_repeat_cluster_count",
            "maker_same_target_repeat_cluster_summary",
            "maker_complement_pair_cluster_examples",
            "maker_multifill_wound_summary",
        ):
            _record_derived(
                row,
                provenance,
                field_name,
                None,
                "outcome_truth_records.jsonl",
                "maker outcome truth records unavailable for forensic derivation",
            )
    if support_rows.get("maker_fight_admission_shadow.jsonl") is not None:
        _record_derived(
            row,
            provenance,
            "maker_admission_shadow_row_count",
            float(len(support_rows.get("maker_fight_admission_shadow.jsonl") or [])),
            "maker_fight_admission_shadow.jsonl",
            "count of normalized maker fight admission shadow rows",
        )
    else:
        _record_derived(
            row,
            provenance,
            "maker_admission_shadow_row_count",
            None,
            "maker_fight_admission_shadow.jsonl",
            "maker fight admission shadow row artifact unavailable",
        )
    if support_rows.get("maker_cannon_late_window_probe.jsonl") is not None:
        _record_derived(
            row,
            provenance,
            "maker_cannon_probe_row_count",
            float(len(support_rows.get("maker_cannon_late_window_probe.jsonl") or [])),
            "maker_cannon_late_window_probe.jsonl",
            "count of normalized maker cannon late-window probe rows",
        )
    else:
        _record_derived(
            row,
            provenance,
            "maker_cannon_probe_row_count",
            None,
            "maker_cannon_late_window_probe.jsonl",
            "maker cannon late-window probe row artifact unavailable",
        )
    shadow_specs = [
        ("admission_rubric_version", "maker_fight_admission_shadow_summary.json", ("admission_rubric_version",)),
        ("maker_cannon_shadow_version", "maker_fight_admission_shadow_summary.json", ("maker_cannon_shadow_version",)),
        ("maker_admission_candidate_count", "maker_fight_admission_shadow_summary.json", ("population_class_counts", "candidate")),
        ("maker_admission_external_blocked_count", "maker_fight_admission_shadow_summary.json", ("population_class_counts", "external_blocked")),
        ("maker_admission_truth_thin_count", "maker_fight_admission_shadow_summary.json", ("population_class_counts", "truth_thin")),
        ("maker_admission_clean_count", "maker_fight_admission_shadow_summary.json", ("admission_class_counts", "clean")),
        ("maker_admission_borderline_count", "maker_fight_admission_shadow_summary.json", ("admission_class_counts", "borderline")),
        ("maker_admission_trash_count", "maker_fight_admission_shadow_summary.json", ("admission_class_counts", "trash")),
        ("maker_admission_submit_rate_by_class", "maker_fight_admission_shadow_summary.json", ("submit_rate_by_class",)),
        ("maker_admission_complete_joined_count_by_class", "maker_fight_admission_shadow_summary.json", ("complete_joined_count_by_class",)),
        ("maker_admission_complete_bad_ratio_by_class", "maker_fight_admission_shadow_summary.json", ("complete_bad_ratio_by_class",)),
        ("maker_admission_multifill_incorrect_ratio_by_class", "maker_fight_admission_shadow_summary.json", ("multifill_incorrect_ratio_by_class",)),
        ("maker_admission_dominant_driver_distribution", "maker_fight_admission_shadow_summary.json", ("dominant_driver_distribution",)),
        ("maker_admission_top_trash_target_side_ref_counts", "maker_fight_admission_shadow_summary.json", ("top_trash_target_side_ref_counts",)),
        ("maker_admission_top_clean_target_side_ref_counts", "maker_fight_admission_shadow_summary.json", ("top_clean_target_side_ref_counts",)),
        ("maker_admission_cannon_window_class_distribution", "maker_fight_admission_shadow_summary.json", ("cannon_window_class_distribution",)),
        ("maker_admission_timing_band_class_distribution", "maker_fight_admission_shadow_summary.json", ("maker_timing_band_class_distribution",)),
        ("maker_admission_candidate_count_by_timing_band", "maker_fight_admission_shadow_summary.json", ("candidate_count_by_timing_band",)),
        ("maker_admission_admission_class_distribution_by_timing_band", "maker_fight_admission_shadow_summary.json", ("admission_class_distribution_by_timing_band",)),
        ("maker_admission_submitted_count_by_timing_band", "maker_fight_admission_shadow_summary.json", ("submitted_count_by_timing_band",)),
        ("maker_admission_complete_joined_count_by_timing_band", "maker_fight_admission_shadow_summary.json", ("complete_joined_count_by_timing_band",)),
        ("maker_admission_complete_bad_ratio_by_timing_band", "maker_fight_admission_shadow_summary.json", ("complete_bad_ratio_by_timing_band",)),
        ("maker_admission_multifill_incorrect_ratio_by_timing_band", "maker_fight_admission_shadow_summary.json", ("multifill_incorrect_ratio_by_timing_band",)),
        ("maker_admission_session_regime_class_distribution", "maker_fight_admission_shadow_summary.json", ("session_regime_class_distribution",)),
        ("maker_admission_stack_pressure_class_distribution", "maker_fight_admission_shadow_summary.json", ("stack_pressure_class_distribution",)),
        ("maker_admission_secondary_oracle_status_distribution", "maker_fight_admission_shadow_summary.json", ("secondary_oracle_status_distribution",)),
        ("maker_admission_secondary_oracle_confirmation_distribution", "maker_fight_admission_shadow_summary.json", ("secondary_oracle_confirmation_distribution",)),
        ("maker_admission_cannon_depth_requirement_counts", "maker_fight_admission_shadow_summary.json", ("cannon_depth_requirement_counts",)),
        ("maker_admission_depth_multiple_vs_cannon_target_summary", "maker_fight_admission_shadow_summary.json", ("depth_multiple_vs_cannon_target_summary",)),
        ("maker_admission_outcome_truth_status_distribution_by_class", "maker_fight_admission_calibration_audit.json", ("outcome_truth_status_distribution_by_class",)),
        ("maker_admission_claim_boundary_class_distribution_by_class", "maker_fight_admission_calibration_audit.json", ("claim_boundary_class_distribution_by_class",)),
        ("maker_admission_evaluation_horizon_ms_distribution_by_class", "maker_fight_admission_calibration_audit.json", ("evaluation_horizon_ms_distribution_by_class",)),
        ("maker_admission_clean_but_bad_examples", "maker_fight_admission_calibration_audit.json", ("clean_but_bad_examples",)),
        ("maker_admission_trash_but_okay_examples", "maker_fight_admission_calibration_audit.json", ("trash_but_okay_examples",)),
    ]
    for field_name, source_name, path in shadow_specs:
        _record_path(row, provenance, loaded, field_name, source_name, path)
    cannon_probe_specs = [
        ("maker_cannon_probe_version", "maker_cannon_late_window_probe_summary.json", ("maker_cannon_probe_version",)),
        ("maker_cannon_probe_candidate_count", "maker_cannon_late_window_probe_summary.json", ("population_class_counts", "candidate")),
        ("maker_cannon_probe_external_blocked_count", "maker_cannon_late_window_probe_summary.json", ("population_class_counts", "external_blocked")),
        ("maker_cannon_probe_truth_thin_count", "maker_cannon_late_window_probe_summary.json", ("population_class_counts", "truth_thin")),
        ("maker_cannon_probe_full_candidate_count", "maker_cannon_late_window_probe_summary.json", ("full_cannon_candidate_count",)),
        ("maker_cannon_probe_reject_reason_distribution", "maker_cannon_late_window_probe_summary.json", ("reject_reason_distribution",)),
        ("maker_cannon_probe_market_reference_class_distribution", "maker_cannon_late_window_probe_summary.json", ("market_reference_class_distribution",)),
        ("maker_cannon_probe_market_reference_mode_distribution", "maker_cannon_late_window_probe_summary.json", ("market_reference_mode_distribution",)),
        ("maker_cannon_probe_market_reference_source_side_distribution", "maker_cannon_late_window_probe_summary.json", ("market_reference_source_side_distribution",)),
        ("maker_cannon_probe_market_probability_band_distribution", "maker_cannon_late_window_probe_summary.json", ("market_probability_band_distribution",)),
        ("maker_cannon_probe_favored_side_depth_class_distribution", "maker_cannon_late_window_probe_summary.json", ("favored_side_depth_class_distribution",)),
        ("maker_cannon_probe_financial_posture_class_distribution", "maker_cannon_late_window_probe_summary.json", ("financial_posture_class_distribution",)),
        ("maker_cannon_probe_window_class_distribution", "maker_cannon_late_window_probe_summary.json", ("cannon_window_class_distribution",)),
        ("maker_cannon_probe_session_regime_class_distribution", "maker_cannon_late_window_probe_summary.json", ("session_regime_class_distribution",)),
        ("maker_cannon_probe_stack_pressure_class_distribution", "maker_cannon_late_window_probe_summary.json", ("stack_pressure_class_distribution",)),
        ("maker_cannon_probe_secondary_oracle_status_distribution", "maker_cannon_late_window_probe_summary.json", ("secondary_oracle_status_distribution",)),
        ("maker_cannon_probe_secondary_oracle_confirmation_distribution", "maker_cannon_late_window_probe_summary.json", ("secondary_oracle_confirmation_distribution",)),
        ("maker_cannon_probe_visible_depth_fail_closed_zero_distribution", "maker_cannon_late_window_probe_summary.json", ("probe_visible_depth_fail_closed_zero_distribution",)),
        ("maker_cannon_probe_geometry_viable_counts", "maker_cannon_late_window_probe_summary.json", ("geometry_viable_counts",)),
        ("maker_cannon_probe_cannon_depth_requirement_counts", "maker_cannon_late_window_probe_summary.json", ("cannon_depth_requirement_counts",)),
        ("maker_cannon_probe_depth_multiple_vs_cannon_target_summary", "maker_cannon_late_window_probe_summary.json", ("depth_multiple_vs_cannon_target_summary",)),
        ("maker_cannon_probe_latent_market_truth_class_counts", "maker_cannon_late_window_probe_summary.json", ("latent_market_truth_class_counts",)),
        ("maker_cannon_probe_latent_market_full_candidate_count", "maker_cannon_late_window_probe_summary.json", ("latent_market_full_cannon_candidate_count",)),
        ("maker_cannon_probe_latent_market_full_candidate_population_class_distribution", "maker_cannon_late_window_probe_summary.json", ("latent_market_full_candidate_population_class_distribution",)),
        ("maker_cannon_probe_latent_market_reject_reason_distribution", "maker_cannon_late_window_probe_summary.json", ("latent_market_reject_reason_distribution",)),
        ("maker_cannon_probe_latent_market_dominant_reject_reason_distribution", "maker_cannon_late_window_probe_summary.json", ("latent_market_dominant_reject_reason_distribution",)),
        ("maker_cannon_probe_external_blocked_latent_market_evaluable_count", "maker_cannon_late_window_probe_summary.json", ("external_blocked_latent_market_evaluable_count",)),
        ("maker_cannon_probe_external_blocked_latent_market_full_candidate_count", "maker_cannon_late_window_probe_summary.json", ("external_blocked_latent_market_full_cannon_candidate_count",)),
        ("maker_cannon_probe_external_blocked_latent_market_reject_reason_distribution", "maker_cannon_late_window_probe_summary.json", ("external_blocked_latent_market_reject_reason_distribution",)),
        ("maker_cannon_probe_total_maker_edge_eval_rows", "maker_cannon_late_window_probe_summary.json", ("total_maker_edge_eval_rows",)),
        ("maker_cannon_probe_late_window_raw_row_count", "maker_cannon_late_window_probe_summary.json", ("late_window_raw_row_count",)),
        ("maker_cannon_probe_ignored_non_late_window_row_count", "maker_cannon_late_window_probe_summary.json", ("ignored_non_late_window_row_count",)),
    ]
    for field_name, source_name, path in cannon_probe_specs:
        _record_path(row, provenance, loaded, field_name, source_name, path)
    _record_path(
        row,
        provenance,
        loaded,
        "maker_cannon_probe_lifecycle_phase_distribution",
        "maker_cannon_late_window_probe_summary.json",
        ("lifecycle_phase_distribution",),
    )
    _record_path(
        row,
        provenance,
        loaded,
        "maker_cannon_probe_maker_phase_allowed_distribution",
        "maker_cannon_late_window_probe_summary.json",
        ("maker_phase_allowed_distribution",),
    )
    _record_path(
        row,
        provenance,
        loaded,
        "maker_cannon_probe_full_candidate_runtime_phase_disallow_count",
        "maker_cannon_late_window_probe_summary.json",
        ("full_candidate_runtime_phase_disallow_count",),
    )
    row["field_provenance"] = provenance
    return row, loaded, provenance


def _load_run_ids_from_file(run_id_file: pathlib.Path | None) -> list[str] | None:
    if run_id_file is None:
        return None
    resolved = run_id_file.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"run id file not found: {resolved}")
    run_ids: list[str] = []
    seen: set[str] = set()
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        normalized = raw_line.split("#", 1)[0].strip()
        if not normalized or normalized in seen:
            continue
        run_ids.append(normalized)
        seen.add(normalized)
    if not run_ids:
        raise ValueError(f"run id file contained no usable run ids: {resolved}")
    return run_ids


def _discover_run_dirs(
    report_root: pathlib.Path,
    run_id: str | None,
    limit: int | None,
    run_ids: list[str] | None = None,
) -> list[pathlib.Path]:
    if run_id and run_ids:
        raise ValueError("run_id and run_ids are mutually exclusive")
    if run_ids:
        selected: list[pathlib.Path] = []
        for rid in run_ids:
            target = report_root / rid
            if not target.is_dir():
                raise FileNotFoundError(f"run id not found under report root: {rid}")
            selected.append(target)
        return selected
    if run_id:
        target = report_root / run_id
        if not target.is_dir():
            raise FileNotFoundError(f"run id not found under report root: {run_id}")
        return [target]
    run_dirs = [path for path in report_root.iterdir() if path.is_dir()]
    run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if limit is not None:
        run_dirs = run_dirs[:limit]
    run_dirs.reverse()
    return run_dirs


def build_metric_catalog(catalog_accumulator: dict[str, dict[str, dict[str, Any]]], run_count: int) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    total_metric_keys = 0
    for source_name, path_map in sorted(catalog_accumulator.items()):
        normalized_paths: dict[str, Any] = {}
        for path, info in sorted(path_map.items()):
            normalized_paths[path] = {
                "presence_count": info["presence_count"],
                "presence_ratio": (info["presence_count"] / run_count) if run_count else None,
                "types": dict(sorted(info["types"].items())),
            }
        total_metric_keys += len(normalized_paths)
        root_entry = path_map.get("__root__", {"presence_count": 0})
        sources[source_name] = {
            "presence_count": root_entry["presence_count"],
            "presence_ratio": (root_entry["presence_count"] / run_count) if run_count else None,
            "paths": normalized_paths,
        }
    return {
        "tool_name": TOOL_NAME,
        "tool_alias": TOOL_ALIAS,
        "schema_version": TOOL_SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_count": run_count,
        "source_count": len(sources),
        "total_metric_keys": total_metric_keys,
        "sources": sources,
    }


def build_field_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    field_counts: dict[str, dict[str, Any]] = {}
    if not rows:
        return field_counts
    candidate_fields = sorted({key for row in rows for key in row.keys() if key != "field_provenance"})
    for field in candidate_fields:
        present = sum(1 for row in rows if row.get(field) is not None)
        field_counts[field] = {
            "present_count": present,
            "missing_count": len(rows) - present,
            "present_ratio": present / len(rows),
        }
    return field_counts


def _important_coverage_fields() -> list[str]:
    return [
        "runtime_classification",
        "validation_status",
        "wallet_authority_status_class",
        "valuation_bruise_state",
        "valuation_degraded_ratio",
        "outcome_attribution_usability_ratio",
        "taker_decision_timing_window_distribution",
        "maker_no_submission_cause_distribution",
        "maker_window_active_row_count",
        "maker_window_target_summary",
        "maker_window_viable_row_count",
        "maker_window_impossible_row_count",
        "maker_min_notional_max_shares_conflict_rows",
        "maker_admission_candidate_count",
        "maker_admission_clean_count",
        "maker_admission_trash_count",
        "maker_complete_record_count",
        "maker_lifecycle_gap_class_distribution",
        "maker_reference_basis_summary",
        "risk_reject_reason_distribution",
    ]


def _build_engineer_focus(rows: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    thin_spots: list[dict[str, Any]] = []
    for field in _important_coverage_fields():
        info = coverage.get(field)
        if not info or info["present_count"] == len(rows):
            continue
        thin_spots.append(
            {
                "field": field,
                "present_count": info["present_count"],
                "missing_count": info["missing_count"],
                "present_ratio": info["present_ratio"],
            }
        )
    thin_spots.sort(key=lambda item: (item["present_ratio"], item["field"]))
    return {
        "run_scope": "single_run" if len(rows) == 1 else "corpus",
        "maker_quote_quality_skip_total_count": sum(row.get("maker_quote_quality_skip_total_count") or 0 for row in rows),
        "maker_sizing_reject_total_count": sum(row.get("maker_sizing_reject_total_count") or 0 for row in rows),
        "maker_replace_guard_min_rest_count": sum(row.get("maker_replace_guard_min_rest_count") or 0 for row in rows),
        "maker_window_active_row_count_total": sum(row.get("maker_window_active_row_count") or 0 for row in rows),
        "maker_window_submit_count_total": sum(row.get("maker_window_submit_count") or 0 for row in rows),
        "maker_window_replace_guard_count_total": sum(row.get("maker_window_replace_guard_count") or 0 for row in rows),
        "maker_window_quote_quality_skip_total_count": sum(
            row.get("maker_window_quote_quality_skip_total_count") or 0 for row in rows
        ),
        "maker_window_sizing_reject_count_total": sum(row.get("maker_window_sizing_reject_count") or 0 for row in rows),
        "maker_window_viable_row_count_total": sum(row.get("maker_window_viable_row_count") or 0 for row in rows),
        "maker_window_impossible_row_count_total": sum(row.get("maker_window_impossible_row_count") or 0 for row in rows),
        "maker_min_notional_max_shares_conflict_rows_total": sum(
            row.get("maker_min_notional_max_shares_conflict_rows") or 0 for row in rows
        ),
        "maker_window_queue_depth_on_viable_targets_count_total": sum(
            row.get("maker_window_queue_depth_on_viable_targets_count") or 0 for row in rows
        ),
        "maker_window_queue_depth_on_impossible_targets_count_total": sum(
            row.get("maker_window_queue_depth_on_impossible_targets_count") or 0 for row in rows
        ),
        "risk_reject_total_count": sum(row.get("risk_reject_total_count") or 0 for row in rows),
        "settlement_hold_required_count_total": sum(row.get("settlement_hold_required_count") or 0 for row in rows),
        "open_order_cleanup_required_count_total": sum(row.get("open_order_cleanup_required_count") or 0 for row in rows),
        "unresolved_lifecycle_obligation_count_total": sum(
            row.get("unresolved_lifecycle_obligation_count") or 0 for row in rows
        ),
        "cancel_fail_closed_count_total": sum(row.get("cancel_fail_closed_count") or 0 for row in rows),
        "valuation_bruise_open_run_count": sum(
            1 for row in rows if row.get("valuation_bruise_state") not in (None, "none", "recovered_clean")
        ),
        "valuation_recovered_clean_run_count": sum(
            1 for row in rows if row.get("valuation_bruise_state") == "recovered_clean"
        ),
        "coverage_thin_spots": thin_spots,
    }


def build_anomaly_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flagged_runs: dict[str, list[str]] = defaultdict(list)
    primary_suppression = Counter()
    risk_reject_reasons = Counter()
    maker_no_submit = Counter()
    maker_block_reasons = Counter()
    maker_lifecycle_gap_classes = Counter()
    maker_basis_decision = Counter()
    maker_basis_eval = Counter()
    maker_fill_count_quality: defaultdict[str, Counter[str]] = defaultdict(Counter)
    taker_windows = Counter()
    taker_predicted_rejects = Counter()
    taker_stage_fills = Counter()
    lifecycle_residue_counts = Counter()
    valuation_bruise_states = Counter()
    valuation_reason_families = Counter()
    valuation_held_causes = Counter()
    valuation_degraded_sources = Counter()
    wallet_status_counts = Counter()
    runtime_classification_counts = Counter()
    maker_complete_bad_ratios: list[float] = []
    maker_incomplete_bad_ratios: list[float] = []
    maker_multifill_incorrect_ratios: list[float] = []
    maker_fills_per_filled_order: list[float] = []
    maker_execution_rescue_overcome_counts: list[float] = []
    maker_execution_rescue_ratio_means: list[float] = []
    maker_same_target_repeat_cluster_counts: list[float] = []
    maker_complement_pair_cluster_counts: list[float] = []
    maker_complement_pair_cluster_debt_sums: list[float] = []
    maker_window_submit_rates: list[float] = []
    maker_window_replace_guard_rates: list[float] = []
    maker_window_quote_quality_skip_rates: list[float] = []
    maker_window_sizing_reject_rates: list[float] = []
    maker_window_active_row_total = 0.0
    maker_window_submit_total = 0.0
    maker_window_replace_guard_total = 0.0
    maker_window_quote_quality_skip_total = 0.0
    maker_window_sizing_reject_total = 0.0
    maker_window_viable_row_total = 0.0
    maker_window_impossible_row_total = 0.0
    maker_min_notional_max_shares_conflict_total = 0.0
    maker_window_queue_depth_on_viable_targets_total = 0.0
    maker_window_queue_depth_on_impossible_targets_total = 0.0
    maker_raw_queue_depth_near_threshold_total = 0.0
    maker_raw_queue_depth_hard_miss_total = 0.0
    maker_quote_quality_skip_fill_probability_severity_bins = Counter()
    maker_quote_quality_skip_queue_depth_severity_bins = Counter()
    maker_complete_record_total = 0.0
    maker_incomplete_record_total = 0.0
    maker_multifill_complete_total = 0.0
    maker_admission_population_counts = Counter()
    maker_admission_class_counts = Counter()
    maker_admission_dominant_driver_counts = Counter()
    maker_admission_top_trash_target_side_counts = Counter()
    maker_admission_top_clean_target_side_counts = Counter()
    maker_admission_cannon_window_counts = Counter()
    maker_admission_timing_band_counts = Counter()
    maker_admission_candidate_count_by_timing_band = Counter()
    maker_admission_submitted_count_by_timing_band = Counter()
    maker_admission_complete_joined_count_by_timing_band = Counter()
    maker_admission_session_regime_counts = Counter()
    maker_admission_stack_pressure_counts = Counter()
    maker_admission_secondary_oracle_status_counts = Counter()
    maker_admission_secondary_oracle_confirmation_counts = Counter()
    maker_admission_cannon_depth_requirement_counts = Counter()
    maker_admission_depth_multiple_means: list[float] = []
    maker_admission_submit_rate_by_class: defaultdict[str, list[float]] = defaultdict(list)
    maker_admission_complete_bad_ratio_by_class: defaultdict[str, list[float]] = defaultdict(list)
    maker_admission_multifill_incorrect_ratio_by_class: defaultdict[str, list[float]] = defaultdict(list)
    maker_admission_complete_bad_ratio_by_timing_band: defaultdict[str, list[float]] = defaultdict(list)
    maker_admission_multifill_incorrect_ratio_by_timing_band: defaultdict[str, list[float]] = defaultdict(list)
    maker_admission_class_distribution_by_timing_band: defaultdict[str, Counter[str]] = defaultdict(Counter)
    maker_admission_clean_but_bad_examples: list[dict[str, Any]] = []
    maker_admission_trash_but_okay_examples: list[dict[str, Any]] = []
    maker_cannon_probe_population_counts = Counter()
    maker_cannon_probe_reject_reason_counts = Counter()
    maker_cannon_probe_lifecycle_phase_counts = Counter()
    maker_cannon_probe_maker_phase_allowed_counts = Counter()
    maker_cannon_probe_financial_posture_counts = Counter()
    maker_cannon_probe_window_counts = Counter()
    maker_cannon_probe_session_regime_counts = Counter()
    maker_cannon_probe_stack_pressure_counts = Counter()
    maker_cannon_probe_secondary_oracle_status_counts = Counter()
    maker_cannon_probe_secondary_oracle_confirmation_counts = Counter()
    maker_cannon_probe_geometry_viable_counts = Counter()
    maker_cannon_probe_cannon_depth_requirement_counts = Counter()
    maker_cannon_probe_depth_multiple_means: list[float] = []
    maker_cannon_probe_full_candidate_total = 0.0
    maker_cannon_probe_latent_market_truth_class_counts = Counter()
    maker_cannon_probe_latent_market_reject_reason_counts = Counter()
    maker_cannon_probe_latent_market_dominant_reject_reason_counts = Counter()
    maker_cannon_probe_latent_market_full_candidate_population_counts = Counter()
    maker_cannon_probe_external_blocked_latent_market_reject_reason_counts = Counter()
    maker_cannon_probe_latent_market_full_candidate_total = 0.0
    maker_cannon_probe_external_blocked_latent_market_evaluable_total = 0.0
    maker_cannon_probe_external_blocked_latent_market_full_candidate_total = 0.0

    for row in rows:
        run_id = row.get("run_id") or pathlib.Path(row["report_dir"]).name
        if not row.get("validation_ok") or row.get("validation_overall_exit_code") not in (0, None) or row.get("gate_passed") is False:
            flagged_runs["validation_not_ok"].append(run_id)
        if row.get("reports_complete") is False:
            flagged_runs["reports_incomplete"].append(run_id)
        if row.get("runtime_classification") not in (None, "VALID_ACTIVE"):
            flagged_runs["runtime_non_valid_active"].append(run_id)
        if row.get("runtime_primary_suppression_cause") not in (None, "none"):
            flagged_runs["suppression_present"].append(run_id)
        if (row.get("error_rows") or 0) > 0:
            flagged_runs["error_rows_present"].append(run_id)
        if (row.get("valuation_hard_degraded_ratio") or 0) > 0:
            flagged_runs["valuation_hard_degraded_present"].append(run_id)
        if (row.get("held_unpriceable_unrecovered_meaningful_count") or 0) > 0:
            flagged_runs["meaningful_unpriceable_present"].append(run_id)
        if row.get("wallet_authority_status_class") not in (None, "authoritative"):
            flagged_runs["wallet_non_authoritative"].append(run_id)
        if row.get("wallet_reservation_mismatch_candidate") is True:
            flagged_runs["wallet_reservation_mismatch"].append(run_id)
        if row.get("valuation_bruise_state") not in (None, "none", "recovered_clean"):
            flagged_runs["valuation_bruise_open_or_unrecovered"].append(run_id)
        if (row.get("lifecycle_finding_count") or 0) > 0 or (row.get("integrity_finding_count") or 0) > 0:
            flagged_runs["integrity_or_lifecycle_findings"].append(run_id)
        if (row.get("lifecycle_fill_without_submit_count") or 0) > 0:
            flagged_runs["fill_without_submit_present"].append(run_id)
        if (row.get("lifecycle_duplicate_fill_trade_id_count") or 0) > 0 or (row.get("integrity_duplicate_fill_trade_id_count") or 0) > 0:
            flagged_runs["duplicate_fill_trade_ids_present"].append(run_id)
        if row.get("outcome_attribution_usability_ratio") is not None and row.get("outcome_attribution_usability_ratio") < 1.0:
            flagged_runs["outcome_attribution_incomplete"].append(run_id)
        if isinstance(row.get("maker_complete_bad_ratio"), (int, float)) and row.get("maker_complete_bad_ratio", 0.0) > 0.5:
            flagged_runs["maker_complete_outcomes_bad_majority"].append(run_id)
        if (
            isinstance(row.get("maker_multifill_complete_count"), (int, float))
            and row.get("maker_multifill_complete_count", 0.0) > 0
            and isinstance(row.get("maker_multifill_complete_incorrect_ratio"), (int, float))
            and row.get("maker_multifill_complete_incorrect_ratio", 0.0) > 0.5
        ):
            flagged_runs["maker_multifill_wound_present"].append(run_id)
        if (row.get("maker_complement_pair_cluster_count") or 0) > 0:
            flagged_runs["maker_complement_pair_clusters_present"].append(run_id)
        if (row.get("maker_same_target_repeat_cluster_count") or 0) > 0:
            flagged_runs["maker_same_target_repeat_clusters_present"].append(run_id)

        primary = row.get("runtime_primary_suppression_cause")
        if primary is not None:
            primary_suppression[str(primary)] += 1
        wallet_status = row.get("wallet_authority_status_class")
        if wallet_status is not None:
            wallet_status_counts[str(wallet_status)] += 1
        classification = row.get("runtime_classification")
        if classification is not None:
            runtime_classification_counts[str(classification)] += 1
        valuation_bruise_state = row.get("valuation_bruise_state")
        if valuation_bruise_state is not None:
            valuation_bruise_states[str(valuation_bruise_state)] += 1
        _merge_counter(risk_reject_reasons, row.get("risk_reject_reason_distribution"))
        _merge_counter(maker_no_submit, row.get("maker_no_submission_cause_distribution"))
        _merge_counter(maker_block_reasons, row.get("maker_block_reason_distribution"))
        _merge_counter(taker_windows, row.get("taker_decision_timing_window_distribution"))
        _merge_counter(taker_predicted_rejects, row.get("taker_decision_predicted_reject_reason_distribution"))
        _merge_counter(taker_stage_fills, row.get("taker_fill_stage_distribution"))
        lifecycle_residue_counts["settlement_hold_required_count"] += int(
            float(row.get("settlement_hold_required_count") or 0.0)
        )
        lifecycle_residue_counts["open_order_cleanup_required_count"] += int(
            float(row.get("open_order_cleanup_required_count") or 0.0)
        )
        lifecycle_residue_counts["unresolved_lifecycle_obligation_count"] += int(
            float(row.get("unresolved_lifecycle_obligation_count") or 0.0)
        )
        lifecycle_residue_counts["cancel_fail_closed_count"] += int(
            float(row.get("cancel_fail_closed_count") or 0.0)
        )
        _merge_counter(valuation_reason_families, row.get("valuation_degraded_reason_family_counts_run"))
        _merge_counter(valuation_held_causes, row.get("held_unpriceable_cause_counts_run"))
        _merge_counter(valuation_degraded_sources, row.get("valuation_source_counts_degraded_rows"))
        _merge_counter(maker_lifecycle_gap_classes, row.get("maker_lifecycle_gap_class_distribution"))
        _merge_nested_counter(maker_fill_count_quality, row.get("maker_fill_count_quality_distribution"))
        _merge_reference_basis_summary(
            maker_basis_decision,
            maker_basis_eval,
            row.get("maker_reference_basis_summary"),
        )
        if isinstance(row.get("maker_complete_bad_ratio"), (int, float)):
            maker_complete_bad_ratios.append(float(row["maker_complete_bad_ratio"]))
        if isinstance(row.get("maker_incomplete_bad_ratio"), (int, float)):
            maker_incomplete_bad_ratios.append(float(row["maker_incomplete_bad_ratio"]))
        if isinstance(row.get("maker_multifill_complete_incorrect_ratio"), (int, float)):
            maker_multifill_incorrect_ratios.append(float(row["maker_multifill_complete_incorrect_ratio"]))
        if isinstance(row.get("maker_fills_per_filled_order"), (int, float)):
            maker_fills_per_filled_order.append(float(row["maker_fills_per_filled_order"]))
        if isinstance(row.get("maker_execution_rescue_overcome_count"), (int, float)):
            maker_execution_rescue_overcome_counts.append(float(row["maker_execution_rescue_overcome_count"]))
        rescue_summary = row.get("maker_execution_rescue_ratio_summary")
        if isinstance(rescue_summary, dict) and isinstance(rescue_summary.get("mean"), (int, float)):
            maker_execution_rescue_ratio_means.append(float(rescue_summary["mean"]))
        if isinstance(row.get("maker_same_target_repeat_cluster_count"), (int, float)):
            maker_same_target_repeat_cluster_counts.append(float(row["maker_same_target_repeat_cluster_count"]))
        if isinstance(row.get("maker_complement_pair_cluster_count"), (int, float)):
            maker_complement_pair_cluster_counts.append(float(row["maker_complement_pair_cluster_count"]))
        if isinstance(row.get("maker_complement_pair_cluster_decision_debt_sum"), (int, float)):
            maker_complement_pair_cluster_debt_sums.append(float(row["maker_complement_pair_cluster_decision_debt_sum"]))
        if isinstance(row.get("maker_window_submit_rate"), (int, float)):
            maker_window_submit_rates.append(float(row["maker_window_submit_rate"]))
        if isinstance(row.get("maker_window_replace_guard_rate"), (int, float)):
            maker_window_replace_guard_rates.append(float(row["maker_window_replace_guard_rate"]))
        if isinstance(row.get("maker_window_quote_quality_skip_rate"), (int, float)):
            maker_window_quote_quality_skip_rates.append(float(row["maker_window_quote_quality_skip_rate"]))
        if isinstance(row.get("maker_window_sizing_reject_rate"), (int, float)):
            maker_window_sizing_reject_rates.append(float(row["maker_window_sizing_reject_rate"]))
        maker_window_active_row_total += float(row.get("maker_window_active_row_count") or 0.0)
        maker_window_submit_total += float(row.get("maker_window_submit_count") or 0.0)
        maker_window_replace_guard_total += float(row.get("maker_window_replace_guard_count") or 0.0)
        maker_window_quote_quality_skip_total += float(row.get("maker_window_quote_quality_skip_total_count") or 0.0)
        maker_window_sizing_reject_total += float(row.get("maker_window_sizing_reject_count") or 0.0)
        maker_window_viable_row_total += float(row.get("maker_window_viable_row_count") or 0.0)
        maker_window_impossible_row_total += float(row.get("maker_window_impossible_row_count") or 0.0)
        maker_min_notional_max_shares_conflict_total += float(
            row.get("maker_min_notional_max_shares_conflict_rows") or 0.0
        )
        maker_window_queue_depth_on_viable_targets_total += float(
            row.get("maker_window_queue_depth_on_viable_targets_count") or 0.0
        )
        maker_window_queue_depth_on_impossible_targets_total += float(
            row.get("maker_window_queue_depth_on_impossible_targets_count") or 0.0
        )
        maker_raw_queue_depth_near_threshold_total += float(
            row.get("maker_raw_queue_depth_near_threshold_event_count") or 0.0
        )
        maker_raw_queue_depth_hard_miss_total += float(
            row.get("maker_raw_queue_depth_hard_miss_event_count") or 0.0
        )
        _merge_counter(
            maker_quote_quality_skip_fill_probability_severity_bins,
            row.get("maker_quote_quality_skip_fill_probability_severity_bins"),
        )
        _merge_counter(
            maker_quote_quality_skip_queue_depth_severity_bins,
            row.get("maker_quote_quality_skip_queue_depth_severity_bins"),
        )
        maker_complete_record_total += float(row.get("maker_complete_record_count") or 0.0)
        maker_incomplete_record_total += float(row.get("maker_incomplete_record_count") or 0.0)
        maker_multifill_complete_total += float(row.get("maker_multifill_complete_count") or 0.0)
        maker_admission_population_counts["candidate"] += int(
            float(row.get("maker_admission_candidate_count") or 0.0)
        )
        maker_admission_population_counts["external_blocked"] += int(
            float(row.get("maker_admission_external_blocked_count") or 0.0)
        )
        maker_admission_population_counts["truth_thin"] += int(
            float(row.get("maker_admission_truth_thin_count") or 0.0)
        )
        maker_admission_class_counts["clean"] += int(float(row.get("maker_admission_clean_count") or 0.0))
        maker_admission_class_counts["borderline"] += int(
            float(row.get("maker_admission_borderline_count") or 0.0)
        )
        maker_admission_class_counts["trash"] += int(float(row.get("maker_admission_trash_count") or 0.0))
        _merge_counter(
            maker_admission_dominant_driver_counts,
            row.get("maker_admission_dominant_driver_distribution"),
        )
        _merge_counter(
            maker_admission_top_trash_target_side_counts,
            row.get("maker_admission_top_trash_target_side_ref_counts"),
        )
        _merge_counter(
            maker_admission_top_clean_target_side_counts,
            row.get("maker_admission_top_clean_target_side_ref_counts"),
        )
        _merge_counter(
            maker_admission_cannon_window_counts,
            row.get("maker_admission_cannon_window_class_distribution"),
        )
        _merge_counter(
            maker_admission_timing_band_counts,
            row.get("maker_admission_timing_band_class_distribution"),
        )
        _merge_counter(
            maker_admission_candidate_count_by_timing_band,
            row.get("maker_admission_candidate_count_by_timing_band"),
        )
        _merge_counter(
            maker_admission_submitted_count_by_timing_band,
            row.get("maker_admission_submitted_count_by_timing_band"),
        )
        _merge_counter(
            maker_admission_complete_joined_count_by_timing_band,
            row.get("maker_admission_complete_joined_count_by_timing_band"),
        )
        _merge_counter(
            maker_admission_session_regime_counts,
            row.get("maker_admission_session_regime_class_distribution"),
        )
        _merge_counter(
            maker_admission_stack_pressure_counts,
            row.get("maker_admission_stack_pressure_class_distribution"),
        )
        _merge_counter(
            maker_admission_secondary_oracle_status_counts,
            row.get("maker_admission_secondary_oracle_status_distribution"),
        )
        _merge_counter(
            maker_admission_secondary_oracle_confirmation_counts,
            row.get("maker_admission_secondary_oracle_confirmation_distribution"),
        )
        _merge_counter(
            maker_admission_cannon_depth_requirement_counts,
            row.get("maker_admission_cannon_depth_requirement_counts"),
        )
        depth_multiple_summary = row.get("maker_admission_depth_multiple_vs_cannon_target_summary")
        if isinstance(depth_multiple_summary, dict) and isinstance(depth_multiple_summary.get("mean"), (int, float)):
            maker_admission_depth_multiple_means.append(float(depth_multiple_summary["mean"]))
        submit_rate_by_class = row.get("maker_admission_submit_rate_by_class")
        if isinstance(submit_rate_by_class, dict):
            for class_name, value in submit_rate_by_class.items():
                if isinstance(value, (int, float)):
                    maker_admission_submit_rate_by_class[str(class_name)].append(float(value))
        complete_bad_ratio_by_class = row.get("maker_admission_complete_bad_ratio_by_class")
        if isinstance(complete_bad_ratio_by_class, dict):
            for class_name, value in complete_bad_ratio_by_class.items():
                if isinstance(value, (int, float)):
                    maker_admission_complete_bad_ratio_by_class[str(class_name)].append(float(value))
        multifill_incorrect_ratio_by_class = row.get("maker_admission_multifill_incorrect_ratio_by_class")
        if isinstance(multifill_incorrect_ratio_by_class, dict):
            for class_name, value in multifill_incorrect_ratio_by_class.items():
                if isinstance(value, (int, float)):
                    maker_admission_multifill_incorrect_ratio_by_class[str(class_name)].append(float(value))
        complete_bad_ratio_by_timing_band = row.get("maker_admission_complete_bad_ratio_by_timing_band")
        if isinstance(complete_bad_ratio_by_timing_band, dict):
            for band_name, value in complete_bad_ratio_by_timing_band.items():
                if isinstance(value, (int, float)):
                    maker_admission_complete_bad_ratio_by_timing_band[str(band_name)].append(float(value))
        multifill_incorrect_ratio_by_timing_band = row.get(
            "maker_admission_multifill_incorrect_ratio_by_timing_band"
        )
        if isinstance(multifill_incorrect_ratio_by_timing_band, dict):
            for band_name, value in multifill_incorrect_ratio_by_timing_band.items():
                if isinstance(value, (int, float)):
                    maker_admission_multifill_incorrect_ratio_by_timing_band[str(band_name)].append(float(value))
        admission_class_distribution_by_timing_band = row.get(
            "maker_admission_admission_class_distribution_by_timing_band"
        )
        if isinstance(admission_class_distribution_by_timing_band, dict):
            for band_name, counts in admission_class_distribution_by_timing_band.items():
                if not isinstance(counts, dict):
                    continue
                for class_name, value in counts.items():
                    if isinstance(value, (int, float)):
                        maker_admission_class_distribution_by_timing_band[str(band_name)][
                            str(class_name)
                        ] += int(float(value))
        clean_but_bad_examples = row.get("maker_admission_clean_but_bad_examples")
        if isinstance(clean_but_bad_examples, list):
            for example in clean_but_bad_examples[:2]:
                if isinstance(example, dict):
                    maker_admission_clean_but_bad_examples.append(dict(example))
        trash_but_okay_examples = row.get("maker_admission_trash_but_okay_examples")
        if isinstance(trash_but_okay_examples, list):
            for example in trash_but_okay_examples[:2]:
                if isinstance(example, dict):
                    maker_admission_trash_but_okay_examples.append(dict(example))
        maker_cannon_probe_population_counts["candidate"] += int(
            float(row.get("maker_cannon_probe_candidate_count") or 0.0)
        )
        maker_cannon_probe_population_counts["external_blocked"] += int(
            float(row.get("maker_cannon_probe_external_blocked_count") or 0.0)
        )
        maker_cannon_probe_population_counts["truth_thin"] += int(
            float(row.get("maker_cannon_probe_truth_thin_count") or 0.0)
        )
        maker_cannon_probe_full_candidate_total += float(
            row.get("maker_cannon_probe_full_candidate_count") or 0.0
        )
        maker_cannon_probe_latent_market_full_candidate_total += float(
            row.get("maker_cannon_probe_latent_market_full_candidate_count") or 0.0
        )
        maker_cannon_probe_external_blocked_latent_market_evaluable_total += float(
            row.get("maker_cannon_probe_external_blocked_latent_market_evaluable_count") or 0.0
        )
        maker_cannon_probe_external_blocked_latent_market_full_candidate_total += float(
            row.get("maker_cannon_probe_external_blocked_latent_market_full_candidate_count")
            or 0.0
        )
        _merge_counter(
            maker_cannon_probe_reject_reason_counts,
            row.get("maker_cannon_probe_reject_reason_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_lifecycle_phase_counts,
            row.get("maker_cannon_probe_lifecycle_phase_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_maker_phase_allowed_counts,
            row.get("maker_cannon_probe_maker_phase_allowed_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_financial_posture_counts,
            row.get("maker_cannon_probe_financial_posture_class_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_window_counts,
            row.get("maker_cannon_probe_window_class_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_session_regime_counts,
            row.get("maker_cannon_probe_session_regime_class_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_stack_pressure_counts,
            row.get("maker_cannon_probe_stack_pressure_class_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_secondary_oracle_status_counts,
            row.get("maker_cannon_probe_secondary_oracle_status_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_secondary_oracle_confirmation_counts,
            row.get("maker_cannon_probe_secondary_oracle_confirmation_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_geometry_viable_counts,
            row.get("maker_cannon_probe_geometry_viable_counts"),
        )
        _merge_counter(
            maker_cannon_probe_cannon_depth_requirement_counts,
            row.get("maker_cannon_probe_cannon_depth_requirement_counts"),
        )
        _merge_counter(
            maker_cannon_probe_latent_market_truth_class_counts,
            row.get("maker_cannon_probe_latent_market_truth_class_counts"),
        )
        _merge_counter(
            maker_cannon_probe_latent_market_reject_reason_counts,
            row.get("maker_cannon_probe_latent_market_reject_reason_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_latent_market_dominant_reject_reason_counts,
            row.get("maker_cannon_probe_latent_market_dominant_reject_reason_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_latent_market_full_candidate_population_counts,
            row.get("maker_cannon_probe_latent_market_full_candidate_population_class_distribution"),
        )
        _merge_counter(
            maker_cannon_probe_external_blocked_latent_market_reject_reason_counts,
            row.get("maker_cannon_probe_external_blocked_latent_market_reject_reason_distribution"),
        )
        cannon_probe_depth_summary = row.get("maker_cannon_probe_depth_multiple_vs_cannon_target_summary")
        if isinstance(cannon_probe_depth_summary, dict) and isinstance(cannon_probe_depth_summary.get("mean"), (int, float)):
            maker_cannon_probe_depth_multiple_means.append(float(cannon_probe_depth_summary["mean"]))

    runtime_counts = dict(runtime_classification_counts.most_common())
    wallet_counts = dict(wallet_status_counts.most_common())
    suppression_counts = dict(primary_suppression.most_common())
    coverage = {
        "field_coverage": build_field_coverage(rows),
        "report_availability": {
            report_name: {
                "present_count": sum(1 for row in rows if report_name in row.get("available_reports", [])),
                "missing_count": sum(1 for row in rows if report_name in row.get("missing_reports", [])),
            }
            for report_name in REPORT_FILES
        },
    }
    maker_fill_count_quality_distribution = {
        fill_count: _sorted_counter_dict(counter)
        for fill_count, counter in sorted(maker_fill_count_quality.items(), key=lambda item: int(item[0]))
    }
    return {
        "tool_name": TOOL_NAME,
        "tool_alias": TOOL_ALIAS,
        "schema_version": TOOL_SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_count": len(rows),
        "runtime_classification_counts": runtime_counts,
        "wallet_authority_status_counts": wallet_counts,
        "primary_suppression_cause_counts": suppression_counts,
        "maker_truth_population_note": _maker_truth_population_note(),
        "coverage": coverage,
        "engineer_focus": _build_engineer_focus(rows, coverage["field_coverage"]),
        "flag_counts": {name: len(values) for name, values in sorted(flagged_runs.items())},
        "flagged_runs": dict(sorted(flagged_runs.items())),
        "maker_forensics": {
            "run_scope": "single_run" if len(rows) == 1 else "corpus",
            "maker_complete_record_count_total": maker_complete_record_total,
            "maker_incomplete_record_count_total": maker_incomplete_record_total,
            "maker_multifill_complete_count_total": maker_multifill_complete_total,
            "maker_complete_bad_ratio_summary": _numeric_summary(maker_complete_bad_ratios),
            "maker_incomplete_bad_ratio_summary": _numeric_summary(maker_incomplete_bad_ratios),
            "maker_multifill_complete_incorrect_ratio_summary": _numeric_summary(maker_multifill_incorrect_ratios),
            "maker_fills_per_filled_order_summary": _numeric_summary(maker_fills_per_filled_order),
            "maker_execution_rescue_overcome_count_summary": _numeric_summary(maker_execution_rescue_overcome_counts),
            "maker_execution_rescue_ratio_mean_summary": _numeric_summary(maker_execution_rescue_ratio_means),
            "maker_same_target_repeat_cluster_count_summary": _numeric_summary(maker_same_target_repeat_cluster_counts),
            "maker_complement_pair_cluster_count_summary": _numeric_summary(maker_complement_pair_cluster_counts),
            "maker_complement_pair_cluster_decision_debt_sum_summary": _numeric_summary(maker_complement_pair_cluster_debt_sums),
            "maker_window_active_row_count_total": maker_window_active_row_total,
            "maker_window_submit_count_total": maker_window_submit_total,
            "maker_window_replace_guard_count_total": maker_window_replace_guard_total,
            "maker_window_quote_quality_skip_total_count": maker_window_quote_quality_skip_total,
            "maker_window_sizing_reject_count_total": maker_window_sizing_reject_total,
            "maker_window_submit_rate_summary": _numeric_summary(maker_window_submit_rates),
            "maker_window_replace_guard_rate_summary": _numeric_summary(maker_window_replace_guard_rates),
            "maker_window_quote_quality_skip_rate_summary": _numeric_summary(maker_window_quote_quality_skip_rates),
            "maker_window_sizing_reject_rate_summary": _numeric_summary(maker_window_sizing_reject_rates),
            "maker_window_viable_row_count_total": maker_window_viable_row_total,
            "maker_window_impossible_row_count_total": maker_window_impossible_row_total,
            "maker_min_notional_max_shares_conflict_rows_total": maker_min_notional_max_shares_conflict_total,
            "maker_window_queue_depth_on_viable_targets_count_total": (
                maker_window_queue_depth_on_viable_targets_total
            ),
            "maker_window_queue_depth_on_impossible_targets_count_total": (
                maker_window_queue_depth_on_impossible_targets_total
            ),
            "maker_raw_queue_depth_near_threshold_event_count_total": (
                maker_raw_queue_depth_near_threshold_total
            ),
            "maker_raw_queue_depth_hard_miss_event_count_total": maker_raw_queue_depth_hard_miss_total,
            "maker_quote_quality_skip_fill_probability_severity_bins": dict(
                maker_quote_quality_skip_fill_probability_severity_bins.most_common()
            ),
            "maker_quote_quality_skip_queue_depth_severity_bins": dict(
                maker_quote_quality_skip_queue_depth_severity_bins.most_common()
            ),
            "maker_lifecycle_gap_class_counts": dict(maker_lifecycle_gap_classes.most_common()),
            "maker_fill_count_quality_distribution": maker_fill_count_quality_distribution,
            "maker_reference_basis_summary": {
                "decision_reference_basis_distribution": dict(maker_basis_decision.most_common()),
                "eval_reference_basis_distribution": dict(maker_basis_eval.most_common()),
            },
        },
        "maker_admission_shadow": {
            "run_scope": "single_run" if len(rows) == 1 else "corpus",
            "population_class_counts": {
                key: int(maker_admission_population_counts[key])
                for key in ("candidate", "external_blocked", "truth_thin")
            },
            "admission_class_counts": {
                key: int(maker_admission_class_counts[key])
                for key in ("clean", "borderline", "trash")
            },
            "submit_rate_by_class_summary": {
                key: _numeric_summary(values)
                for key, values in sorted(maker_admission_submit_rate_by_class.items())
            },
            "complete_bad_ratio_by_class_summary": {
                key: _numeric_summary(values)
                for key, values in sorted(maker_admission_complete_bad_ratio_by_class.items())
            },
            "multifill_incorrect_ratio_by_class_summary": {
                key: _numeric_summary(values)
                for key, values in sorted(maker_admission_multifill_incorrect_ratio_by_class.items())
            },
            "dominant_driver_counts": dict(maker_admission_dominant_driver_counts.most_common()),
            "top_trash_target_side_ref_counts": dict(
                maker_admission_top_trash_target_side_counts.most_common(10)
            ),
            "top_clean_target_side_ref_counts": dict(
                maker_admission_top_clean_target_side_counts.most_common(10)
            ),
            "cannon_window_class_counts": dict(
                maker_admission_cannon_window_counts.most_common()
            ),
            "maker_timing_band_class_counts": dict(
                maker_admission_timing_band_counts.most_common()
            ),
            "candidate_count_by_timing_band": dict(
                maker_admission_candidate_count_by_timing_band.most_common()
            ),
            "submitted_count_by_timing_band": dict(
                maker_admission_submitted_count_by_timing_band.most_common()
            ),
            "complete_joined_count_by_timing_band": dict(
                maker_admission_complete_joined_count_by_timing_band.most_common()
            ),
            "admission_class_distribution_by_timing_band": {
                band: {
                    class_name: int(counter[class_name])
                    for class_name in ("clean", "borderline", "trash")
                }
                for band, counter in sorted(maker_admission_class_distribution_by_timing_band.items())
            },
            "complete_bad_ratio_by_timing_band_summary": {
                key: _numeric_summary(values)
                for key, values in sorted(maker_admission_complete_bad_ratio_by_timing_band.items())
            },
            "multifill_incorrect_ratio_by_timing_band_summary": {
                key: _numeric_summary(values)
                for key, values in sorted(maker_admission_multifill_incorrect_ratio_by_timing_band.items())
            },
            "session_regime_class_counts": dict(
                maker_admission_session_regime_counts.most_common()
            ),
            "stack_pressure_class_counts": dict(
                maker_admission_stack_pressure_counts.most_common()
            ),
            "secondary_oracle_status_counts": dict(
                maker_admission_secondary_oracle_status_counts.most_common()
            ),
            "secondary_oracle_confirmation_counts": dict(
                maker_admission_secondary_oracle_confirmation_counts.most_common()
            ),
            "cannon_depth_requirement_counts": dict(
                maker_admission_cannon_depth_requirement_counts.most_common()
            ),
            "depth_multiple_vs_cannon_target_summary": _numeric_summary(
                maker_admission_depth_multiple_means
            ),
            "clean_but_bad_examples": maker_admission_clean_but_bad_examples[:5],
            "trash_but_okay_examples": maker_admission_trash_but_okay_examples[:5],
        },
        "maker_cannon_probe": {
            "run_scope": "single_run" if len(rows) == 1 else "corpus",
            "population_class_counts": {
                key: int(maker_cannon_probe_population_counts[key])
                for key in ("candidate", "external_blocked", "truth_thin")
            },
            "full_cannon_candidate_count": int(maker_cannon_probe_full_candidate_total),
            "latent_market_full_cannon_candidate_count": int(
                maker_cannon_probe_latent_market_full_candidate_total
            ),
            "external_blocked_latent_market_evaluable_count": int(
                maker_cannon_probe_external_blocked_latent_market_evaluable_total
            ),
            "external_blocked_latent_market_full_cannon_candidate_count": int(
                maker_cannon_probe_external_blocked_latent_market_full_candidate_total
            ),
            "reject_reason_counts": dict(maker_cannon_probe_reject_reason_counts.most_common()),
            "lifecycle_phase_counts": dict(maker_cannon_probe_lifecycle_phase_counts.most_common()),
            "maker_phase_allowed_counts": dict(
                maker_cannon_probe_maker_phase_allowed_counts.most_common()
            ),
            "financial_posture_class_counts": dict(
                maker_cannon_probe_financial_posture_counts.most_common()
            ),
            "cannon_window_class_counts": dict(maker_cannon_probe_window_counts.most_common()),
            "session_regime_class_counts": dict(
                maker_cannon_probe_session_regime_counts.most_common()
            ),
            "stack_pressure_class_counts": dict(
                maker_cannon_probe_stack_pressure_counts.most_common()
            ),
            "secondary_oracle_status_counts": dict(
                maker_cannon_probe_secondary_oracle_status_counts.most_common()
            ),
            "secondary_oracle_confirmation_counts": dict(
                maker_cannon_probe_secondary_oracle_confirmation_counts.most_common()
            ),
            "geometry_viable_counts": dict(
                maker_cannon_probe_geometry_viable_counts.most_common()
            ),
            "cannon_depth_requirement_counts": dict(
                maker_cannon_probe_cannon_depth_requirement_counts.most_common()
            ),
            "latent_market_truth_class_counts": dict(
                maker_cannon_probe_latent_market_truth_class_counts.most_common()
            ),
            "latent_market_reject_reason_counts": dict(
                maker_cannon_probe_latent_market_reject_reason_counts.most_common()
            ),
            "latent_market_dominant_reject_reason_counts": dict(
                maker_cannon_probe_latent_market_dominant_reject_reason_counts.most_common()
            ),
            "latent_market_full_candidate_population_class_counts": dict(
                maker_cannon_probe_latent_market_full_candidate_population_counts.most_common()
            ),
            "external_blocked_latent_market_reject_reason_counts": dict(
                maker_cannon_probe_external_blocked_latent_market_reject_reason_counts.most_common()
            ),
            "depth_multiple_vs_cannon_target_summary": _numeric_summary(
                maker_cannon_probe_depth_multiple_means
            ),
        },
        "aggregates": {
            "primary_suppression_cause_counts": suppression_counts,
            "wallet_authority_status_counts": wallet_counts,
            "runtime_classification_counts": runtime_counts,
            "risk_reject_reason_counts": dict(risk_reject_reasons.most_common()),
            "maker_no_submission_cause_counts": dict(maker_no_submit.most_common()),
            "maker_block_reason_counts": dict(maker_block_reasons.most_common()),
            "taker_decision_window_counts": dict(taker_windows.most_common()),
            "taker_predicted_reject_reason_counts": dict(taker_predicted_rejects.most_common()),
            "taker_fill_stage_counts": dict(taker_stage_fills.most_common()),
            "lifecycle_residue_counts": dict(lifecycle_residue_counts.most_common()),
            "valuation_bruise_state_counts": dict(valuation_bruise_states.most_common()),
            "valuation_degraded_reason_family_counts": dict(valuation_reason_families.most_common()),
            "valuation_held_unpriceable_cause_counts": dict(valuation_held_causes.most_common()),
            "valuation_source_counts_degraded_rows": dict(valuation_degraded_sources.most_common()),
            "maker_lifecycle_gap_class_counts": dict(maker_lifecycle_gap_classes.most_common()),
            "maker_fill_count_quality_distribution": maker_fill_count_quality_distribution,
            "maker_quote_quality_skip_fill_probability_severity_bins": dict(
                maker_quote_quality_skip_fill_probability_severity_bins.most_common()
            ),
            "maker_quote_quality_skip_queue_depth_severity_bins": dict(
                maker_quote_quality_skip_queue_depth_severity_bins.most_common()
            ),
        },
        "numeric_ranges": {
            "wallet_deployable_capital": _numeric_summary([row["wallet_deployable_capital"] for row in rows if isinstance(row.get("wallet_deployable_capital"), (int, float))]),
            "valuation_degraded_ratio": _numeric_summary([row["valuation_degraded_ratio"] for row in rows if isinstance(row.get("valuation_degraded_ratio"), (int, float))]),
            "market_data_pair_truth_missing_ratio": _numeric_summary(
                [
                    row["market_data_pair_truth_missing_ratio"]
                    for row in rows
                    if isinstance(row.get("market_data_pair_truth_missing_ratio"), (int, float))
                ]
            ),
            "market_data_pair_truth_one_sided_ratio": _numeric_summary(
                [
                    row["market_data_pair_truth_one_sided_ratio"]
                    for row in rows
                    if isinstance(row.get("market_data_pair_truth_one_sided_ratio"), (int, float))
                ]
            ),
            "quote_uptime_ratio": _numeric_summary([row["quote_uptime_ratio"] for row in rows if isinstance(row.get("quote_uptime_ratio"), (int, float))]),
            "maker_fill_rate": _numeric_summary([row["maker_fill_rate"] for row in rows if isinstance(row.get("maker_fill_rate"), (int, float))]),
            "maker_fills_per_filled_order": _numeric_summary([row["maker_fills_per_filled_order"] for row in rows if isinstance(row.get("maker_fills_per_filled_order"), (int, float))]),
            "maker_complete_bad_ratio": _numeric_summary(maker_complete_bad_ratios),
            "maker_multifill_complete_incorrect_ratio": _numeric_summary(maker_multifill_incorrect_ratios),
            "maker_window_submit_rate": _numeric_summary(maker_window_submit_rates),
            "maker_window_replace_guard_rate": _numeric_summary(maker_window_replace_guard_rates),
            "maker_window_quote_quality_skip_rate": _numeric_summary(maker_window_quote_quality_skip_rates),
            "taker_fill_rate": _numeric_summary([row["taker_fill_rate"] for row in rows if isinstance(row.get("taker_fill_rate"), (int, float))]),
            "chainlink_down_ratio": _numeric_summary([row["chainlink_down_ratio"] for row in rows if isinstance(row.get("chainlink_down_ratio"), (int, float))]),
            "book_feed_down_ratio": _numeric_summary([row["book_feed_down_ratio"] for row in rows if isinstance(row.get("book_feed_down_ratio"), (int, float))]),
        },
    }


def _format_counter_lines(counter_map: dict[str, Any], top_n: int = 8) -> list[str]:
    items = list(counter_map.items())[:top_n]
    return [f"- `{key}`: `{value}`" for key, value in items]


def build_maker_research_pack(rows: list[dict[str, Any]], anomaly_summary: dict[str, Any]) -> str:
    validation_ok = sum(1 for row in rows if row.get("validation_ok") is True)
    validation_status_counts = Counter(str(row.get("validation_status")) for row in rows if row.get("validation_status") is not None)
    highest_stage_counts = Counter(str(row.get("highest_passing_stage")) for row in rows if row.get("highest_passing_stage") is not None)
    blocking_stage_counts = Counter(str(row.get("blocking_stage")) for row in rows if row.get("blocking_stage") is not None)
    policy_failed_runs = sum(1 for row in rows if row.get("validation_policy_failed") is True)
    reports_complete_runs = sum(1 for row in rows if row.get("reports_complete") is True)
    determinism_consistent_runs = sum(1 for row in rows if row.get("validation_determinism_consistent") is True)
    deployable_values = [row["wallet_deployable_capital"] for row in rows if isinstance(row.get("wallet_deployable_capital"), (int, float))]
    attribution_values = [row["outcome_attribution_usability_ratio"] for row in rows if isinstance(row.get("outcome_attribution_usability_ratio"), (int, float))]
    pair_missing_ratio_values = [
        row["market_data_pair_truth_missing_ratio"]
        for row in rows
        if isinstance(row.get("market_data_pair_truth_missing_ratio"), (int, float))
    ]
    total_maker_submits = sum(row.get("maker_submits") or 0 for row in rows)
    total_maker_fills = sum(row.get("maker_fills") or 0 for row in rows)
    total_taker_decisions = sum(row.get("taker_decision_count") or 0 for row in rows)
    total_taker_submits = sum(row.get("taker_submits") or 0 for row in rows)
    total_taker_fills = sum(row.get("taker_fills") or 0 for row in rows)
    total_settlement_hold_required = sum(row.get("settlement_hold_required_count") or 0 for row in rows)
    total_open_order_cleanup_required = sum(row.get("open_order_cleanup_required_count") or 0 for row in rows)
    total_unresolved_lifecycle_obligation = sum(
        row.get("unresolved_lifecycle_obligation_count") or 0 for row in rows
    )
    total_cancel_fail_closed = sum(row.get("cancel_fail_closed_count") or 0 for row in rows)
    total_maker_quote_quality_skips = sum(row.get("maker_quote_quality_skip_total_count") or 0 for row in rows)
    total_maker_sizing_rejects = sum(row.get("maker_sizing_reject_total_count") or 0 for row in rows)
    total_maker_replace_guard = sum(row.get("maker_replace_guard_min_rest_count") or 0 for row in rows)
    total_risk_rejects = sum(row.get("risk_reject_total_count") or 0 for row in rows)
    coverage = anomaly_summary["coverage"]["field_coverage"]
    engineer_focus = anomaly_summary.get("engineer_focus", {})
    maker_forensics = anomaly_summary.get("maker_forensics", {})
    maker_admission_shadow = anomaly_summary.get("maker_admission_shadow", {})
    maker_cannon_probe = anomaly_summary.get("maker_cannon_probe", {})
    maker_truth_population_note = anomaly_summary.get("maker_truth_population_note", {})
    core_coverage_fields = [
        "runtime_classification",
        "wallet_authority_status_class",
        "valuation_degraded_ratio",
        "outcome_attribution_usability_ratio",
        "taker_decision_timing_window_distribution",
        "maker_window_active_row_count",
        "maker_complete_record_count",
    ]
    thin_spot_lines = []
    for field in core_coverage_fields:
        info = coverage.get(field)
        if not info:
            continue
        thin_spot_lines.append(
            f"- `{field}` coverage: `{info['present_count']}/{len(rows)}`"
        )
    lines = [
        f"# {TOOL_NAME} Research Pack",
        "",
        f"{TOOL_ALIAS} engineer-first research compression over the report corpus.",
        "",
        "This pack is for weapon-building truth: windows, suppression, submit funnels, wallet authority, money posture, valuation degradation, and feed quality.",
        "",
        "## Corpus Snapshot",
        f"- Runs harvested: `{len(rows)}`",
        f"- Validation-ok runs: `{validation_ok}`",
        f"- Runtime classification coverage: `{coverage['runtime_classification']['present_count']}/{len(rows)}`",
        f"- Wallet authority coverage: `{coverage['wallet_authority_status_class']['present_count']}/{len(rows)}`",
        f"- Valuation coverage: `{coverage['valuation_degraded_ratio']['present_count']}/{len(rows)}`",
        f"- Outcome attribution coverage: `{coverage['outcome_attribution_usability_ratio']['present_count']}/{len(rows)}`",
        f"- Runtime classifications: `{json.dumps(anomaly_summary['aggregates']['runtime_classification_counts'], sort_keys=True)}`",
        f"- Wallet authority classes: `{json.dumps(anomaly_summary['aggregates']['wallet_authority_status_counts'], sort_keys=True)}`",
        "",
        "## Validation / Authority Quick Read",
        f"- Validation status counts: `{json.dumps(dict(validation_status_counts), sort_keys=True)}`",
        f"- Reports complete: `{reports_complete_runs}/{len(rows)}`",
        f"- Policy-failed runs: `{policy_failed_runs}`",
        f"- Determinism-consistent runs: `{determinism_consistent_runs}/{len(rows)}`",
        f"- Highest passing stages: `{json.dumps(dict(highest_stage_counts), sort_keys=True)}`",
        f"- Blocking stages: `{json.dumps(dict(blocking_stage_counts), sort_keys=True)}`",
        "",
        "## Maker Surfaces",
        f"- Total maker submits: `{total_maker_submits}`",
        f"- Total maker fills: `{total_maker_fills}`",
        f"- Fills per filled order summary: `{json.dumps(maker_forensics.get('maker_fills_per_filled_order_summary', {}), sort_keys=True)}`",
        f"- Quote-quality skip total: `{total_maker_quote_quality_skips}`",
        f"- Sizing reject total: `{total_maker_sizing_rejects}`",
        f"- Replace-guard min-rest total: `{total_maker_replace_guard}`",
        *(_format_counter_lines(anomaly_summary["aggregates"]["maker_no_submission_cause_counts"]) or ["- No maker no-submit causes harvested."]),
        *(_format_counter_lines(anomaly_summary["aggregates"]["maker_block_reason_counts"]) or ["- No maker block reasons harvested."]),
        "",
        "## Maker Fireability / Window Surfaces",
        f"- Active-window row total: `{maker_forensics.get('maker_window_active_row_count_total')}`",
        f"- Active-window submit total: `{maker_forensics.get('maker_window_submit_count_total')}`",
        f"- Active-window replace-guard total: `{maker_forensics.get('maker_window_replace_guard_count_total')}`",
        f"- Active-window quote-quality skip total: `{maker_forensics.get('maker_window_quote_quality_skip_total_count')}`",
        f"- Active-window submit-rate summary: `{json.dumps(maker_forensics.get('maker_window_submit_rate_summary', {}), sort_keys=True)}`",
        f"- Active-window replace-guard-rate summary: `{json.dumps(maker_forensics.get('maker_window_replace_guard_rate_summary', {}), sort_keys=True)}`",
        f"- Active-window quote-quality-skip-rate summary: `{json.dumps(maker_forensics.get('maker_window_quote_quality_skip_rate_summary', {}), sort_keys=True)}`",
        f"- Fill-probability severity bins: `{json.dumps(maker_forensics.get('maker_quote_quality_skip_fill_probability_severity_bins', {}), sort_keys=True)}`",
        f"- Queue-depth severity bins: `{json.dumps(maker_forensics.get('maker_quote_quality_skip_queue_depth_severity_bins', {}), sort_keys=True)}`",
        "",
        "## Maker Outcome / Lifecycle Surfaces",
        f"- Complete maker records total: `{maker_forensics.get('maker_complete_record_count_total')}`",
        f"- Incomplete maker records total: `{maker_forensics.get('maker_incomplete_record_count_total')}`",
        f"- Multi-fill complete maker records total: `{maker_forensics.get('maker_multifill_complete_count_total')}`",
        f"- Complete bad-ratio summary: `{json.dumps(maker_forensics.get('maker_complete_bad_ratio_summary', {}), sort_keys=True)}`",
        f"- Incomplete bad-ratio summary: `{json.dumps(maker_forensics.get('maker_incomplete_bad_ratio_summary', {}), sort_keys=True)}`",
        f"- Multi-fill incorrect-ratio summary: `{json.dumps(maker_forensics.get('maker_multifill_complete_incorrect_ratio_summary', {}), sort_keys=True)}`",
        f"- Execution rescue overcome summary: `{json.dumps(maker_forensics.get('maker_execution_rescue_overcome_count_summary', {}), sort_keys=True)}`",
        f"- Same-target repeat cluster summary: `{json.dumps(maker_forensics.get('maker_same_target_repeat_cluster_count_summary', {}), sort_keys=True)}`",
        f"- Complement-pair cluster summary: `{json.dumps(maker_forensics.get('maker_complement_pair_cluster_count_summary', {}), sort_keys=True)}`",
        f"- Complement-pair debt summary: `{json.dumps(maker_forensics.get('maker_complement_pair_cluster_decision_debt_sum_summary', {}), sort_keys=True)}`",
        f"- Lifecycle gap classes: `{json.dumps(maker_forensics.get('maker_lifecycle_gap_class_counts', {}), sort_keys=True)}`",
        f"- Fill-count quality distribution: `{json.dumps(maker_forensics.get('maker_fill_count_quality_distribution', {}), sort_keys=True)}`",
        f"- Reference basis summary: `{json.dumps(maker_forensics.get('maker_reference_basis_summary', {}), sort_keys=True)}`",
        "",
        "## Maker Admission Shadow",
        f"- Population counts: `{json.dumps(maker_admission_shadow.get('population_class_counts', {}), sort_keys=True)}`",
        f"- Admission class counts: `{json.dumps(maker_admission_shadow.get('admission_class_counts', {}), sort_keys=True)}`",
        f"- Submit-rate-by-class summary: `{json.dumps(maker_admission_shadow.get('submit_rate_by_class_summary', {}), sort_keys=True)}`",
        f"- Complete bad-ratio-by-class summary: `{json.dumps(maker_admission_shadow.get('complete_bad_ratio_by_class_summary', {}), sort_keys=True)}`",
        f"- Multifill incorrect-ratio-by-class summary: `{json.dumps(maker_admission_shadow.get('multifill_incorrect_ratio_by_class_summary', {}), sort_keys=True)}`",
        f"- Dominant drivers: `{json.dumps(maker_admission_shadow.get('dominant_driver_counts', {}), sort_keys=True)}`",
        f"- Top trash target-side cohorts: `{json.dumps(maker_admission_shadow.get('top_trash_target_side_ref_counts', {}), sort_keys=True)}`",
        f"- Top clean target-side cohorts: `{json.dumps(maker_admission_shadow.get('top_clean_target_side_ref_counts', {}), sort_keys=True)}`",
        f"- Cannon window classes: `{json.dumps(maker_admission_shadow.get('cannon_window_class_counts', {}), sort_keys=True)}`",
        f"- Maker timing bands: `{json.dumps(maker_admission_shadow.get('maker_timing_band_class_counts', {}), sort_keys=True)}`",
        f"- Candidate count by timing band: `{json.dumps(maker_admission_shadow.get('candidate_count_by_timing_band', {}), sort_keys=True)}`",
        f"- Submitted count by timing band: `{json.dumps(maker_admission_shadow.get('submitted_count_by_timing_band', {}), sort_keys=True)}`",
        f"- Complete joined count by timing band: `{json.dumps(maker_admission_shadow.get('complete_joined_count_by_timing_band', {}), sort_keys=True)}`",
        f"- Admission-class distribution by timing band: `{json.dumps(maker_admission_shadow.get('admission_class_distribution_by_timing_band', {}), sort_keys=True)}`",
        f"- Complete bad-ratio-by-timing-band summary: `{json.dumps(maker_admission_shadow.get('complete_bad_ratio_by_timing_band_summary', {}), sort_keys=True)}`",
        f"- Multifill incorrect-ratio-by-timing-band summary: `{json.dumps(maker_admission_shadow.get('multifill_incorrect_ratio_by_timing_band_summary', {}), sort_keys=True)}`",
        f"- Session regime classes: `{json.dumps(maker_admission_shadow.get('session_regime_class_counts', {}), sort_keys=True)}`",
        f"- Stack pressure classes: `{json.dumps(maker_admission_shadow.get('stack_pressure_class_counts', {}), sort_keys=True)}`",
        f"- Secondary-oracle status counts: `{json.dumps(maker_admission_shadow.get('secondary_oracle_status_counts', {}), sort_keys=True)}`",
        f"- Secondary-oracle confirmation counts: `{json.dumps(maker_admission_shadow.get('secondary_oracle_confirmation_counts', {}), sort_keys=True)}`",
        f"- Cannon depth-requirement counts: `{json.dumps(maker_admission_shadow.get('cannon_depth_requirement_counts', {}), sort_keys=True)}`",
        f"- Depth-multiple-vs-cannon summary: `{json.dumps(maker_admission_shadow.get('depth_multiple_vs_cannon_target_summary', {}), sort_keys=True)}`",
        f"- Clean-but-bad examples: `{json.dumps(maker_admission_shadow.get('clean_but_bad_examples', []), sort_keys=True)}`",
        f"- Trash-but-okay examples: `{json.dumps(maker_admission_shadow.get('trash_but_okay_examples', []), sort_keys=True)}`",
        "",
        "## Maker Cannon Late-Window Probe",
        f"- Population counts: `{json.dumps(maker_cannon_probe.get('population_class_counts', {}), sort_keys=True)}`",
        f"- Full cannon candidate count: `{json.dumps(maker_cannon_probe.get('full_cannon_candidate_count', 0), sort_keys=True)}`",
        f"- Latent-market full cannon candidate count: `{json.dumps(maker_cannon_probe.get('latent_market_full_cannon_candidate_count', 0), sort_keys=True)}`",
        f"- External-blocked latent-market evaluable count: `{json.dumps(maker_cannon_probe.get('external_blocked_latent_market_evaluable_count', 0), sort_keys=True)}`",
        f"- External-blocked latent-market full candidate count: `{json.dumps(maker_cannon_probe.get('external_blocked_latent_market_full_cannon_candidate_count', 0), sort_keys=True)}`",
        f"- Reject reasons: `{json.dumps(maker_cannon_probe.get('reject_reason_counts', {}), sort_keys=True)}`",
        f"- Latent-market reject reasons: `{json.dumps(maker_cannon_probe.get('latent_market_reject_reason_counts', {}), sort_keys=True)}`",
        f"- External-blocked latent-market reject reasons: `{json.dumps(maker_cannon_probe.get('external_blocked_latent_market_reject_reason_counts', {}), sort_keys=True)}`",
        f"- Lifecycle-phase counts: `{json.dumps(maker_cannon_probe.get('lifecycle_phase_counts', {}), sort_keys=True)}`",
        f"- Maker-phase-allowed counts: `{json.dumps(maker_cannon_probe.get('maker_phase_allowed_counts', {}), sort_keys=True)}`",
        f"- Financial posture classes: `{json.dumps(maker_cannon_probe.get('financial_posture_class_counts', {}), sort_keys=True)}`",
        f"- Cannon window classes: `{json.dumps(maker_cannon_probe.get('cannon_window_class_counts', {}), sort_keys=True)}`",
        f"- Session regime classes: `{json.dumps(maker_cannon_probe.get('session_regime_class_counts', {}), sort_keys=True)}`",
        f"- Stack pressure classes: `{json.dumps(maker_cannon_probe.get('stack_pressure_class_counts', {}), sort_keys=True)}`",
        f"- Secondary-oracle status counts: `{json.dumps(maker_cannon_probe.get('secondary_oracle_status_counts', {}), sort_keys=True)}`",
        f"- Secondary-oracle confirmation counts: `{json.dumps(maker_cannon_probe.get('secondary_oracle_confirmation_counts', {}), sort_keys=True)}`",
        f"- Geometry viable counts: `{json.dumps(maker_cannon_probe.get('geometry_viable_counts', {}), sort_keys=True)}`",
        f"- Cannon depth-requirement counts: `{json.dumps(maker_cannon_probe.get('cannon_depth_requirement_counts', {}), sort_keys=True)}`",
        f"- Depth-multiple-vs-cannon summary: `{json.dumps(maker_cannon_probe.get('depth_multiple_vs_cannon_target_summary', {}), sort_keys=True)}`",
        "",
        "## Taker / Window Surfaces",
        f"- Total taker decisions: `{total_taker_decisions}`",
        f"- Total taker submits: `{total_taker_submits}`",
        f"- Total taker fills: `{total_taker_fills}`",
        f"- Window coverage: `{coverage['taker_decision_timing_window_distribution']['present_count']}/{len(rows)}`",
        *(_format_counter_lines(anomaly_summary["aggregates"]["taker_decision_window_counts"]) or ["- No taker window distribution harvested."]),
        *(_format_counter_lines(anomaly_summary["aggregates"]["taker_predicted_reject_reason_counts"]) or ["- No taker predicted reject distribution harvested."]),
        "",
        "## Money / Authority Surfaces",
        f"- Deployable capital summary: `{json.dumps(_numeric_summary(deployable_values), sort_keys=True)}`",
        f"- Total risk rejects harvested: `{total_risk_rejects}`",
        f"- Lifecycle residue settlement/open/unresolved/cancel: `{total_settlement_hold_required}` / `{total_open_order_cleanup_required}` / `{total_unresolved_lifecycle_obligation}` / `{total_cancel_fail_closed}`",
        *(_format_counter_lines(anomaly_summary["aggregates"]["risk_reject_reason_counts"]) or ["- No risk reject distribution harvested."]),
        *(_format_counter_lines(anomaly_summary["aggregates"]["lifecycle_residue_counts"]) or ["- No lifecycle residue counts harvested."]),
        "",
        "## Valuation / Bruise Surfaces",
        f"- Bruise states: `{json.dumps(anomaly_summary['aggregates']['valuation_bruise_state_counts'], sort_keys=True)}`",
        *(_format_counter_lines(anomaly_summary["aggregates"]["valuation_degraded_reason_family_counts"]) or ["- No valuation degraded reason families harvested."]),
        *(_format_counter_lines(anomaly_summary["aggregates"]["valuation_held_unpriceable_cause_counts"]) or ["- No held-unpriceable cause counts harvested."]),
        *(_format_counter_lines(anomaly_summary["aggregates"]["valuation_source_counts_degraded_rows"]) or ["- No degraded-row valuation source counts harvested."]),
        "",
        "## Feed / Data Surfaces",
        f"- Market-data pair-missing ratio summary: `{json.dumps(_numeric_summary(pair_missing_ratio_values), sort_keys=True)}`",
        f"- Chainlink down-ratio summary: `{json.dumps(anomaly_summary['numeric_ranges']['chainlink_down_ratio'], sort_keys=True)}`",
        f"- Book-feed down-ratio summary: `{json.dumps(anomaly_summary['numeric_ranges']['book_feed_down_ratio'], sort_keys=True)}`",
        "",
        "## Integrity / Claim Boundaries",
        f"- Outcome attribution usability summary: `{json.dumps(_numeric_summary(attribution_values), sort_keys=True)}`",
        *(_format_counter_lines(anomaly_summary["flag_counts"]) or ["- No anomaly flags triggered."]),
        "",
        "## Maker Truth Population Guardrails",
        f"- Population map: `{json.dumps(maker_truth_population_note, sort_keys=True)}`",
        "",
        "## Coverage / Thin Spots",
        *(thin_spot_lines or ["- Core engineer fields are fully covered in this harvest slice."]),
        f"- Engineer focus scope: `{engineer_focus.get('run_scope', 'unknown')}`",
        f"- Machine-readable thin spots tracked: `{len(engineer_focus.get('coverage_thin_spots', []))}`",
        "",
        "## Research Leads",
        "- These are engineering leads, not strategy instructions.",
        "- Repeated maker no-submit and block distributions identify where the weapon is being blunted before it can fire.",
        "- Window distributions and submit-capable-to-submit rates help separate edge visibility from execution conversion loss.",
        "- Deployable capital, reservation mismatch state, and authority posture show whether money truth is constraining the weapon or merely observing it.",
        "- Valuation bruise state, degraded reason families, held-data causes, and degraded-row source counts show whether the evidence layer was warped by fallback data, hard-degraded pricing, or persistent held-token gaps.",
        "- Feed down-ratios and REST/WS mix help test whether theory failures are actually data-plane failures.",
        "- Coverage counts matter. Missing surfaces should be read as missing instrumentation or older report shape, not as zeros.",
        "",
    ]
    return "\n".join(lines)


def _build_maker_admission_target_side_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "shadow_row_count": 0.0,
            "candidate_count": 0.0,
            "clean_count": 0.0,
            "borderline_count": 0.0,
            "trash_count": 0.0,
            "submitted_count": 0.0,
            "complete_joined_count": 0.0,
            "complete_bad_count": 0.0,
            "dominant_driver_counts": Counter(),
            "source_runs": set(),
        }
    )
    for row in rows:
        target_side_ref = str(row.get("target_side_ref") or "").strip()
        if not target_side_ref:
            continue
        bucket = grouped[target_side_ref]
        bucket["shadow_row_count"] += 1.0
        run_id = str(row.get("run_id") or "").strip()
        if run_id:
            bucket["source_runs"].add(run_id)
        if str(row.get("population_class") or "") == "candidate":
            bucket["candidate_count"] += 1.0
        admission_class = str(row.get("admission_class") or "")
        if admission_class in {"clean", "borderline", "trash"}:
            bucket[f"{admission_class}_count"] += 1.0
        if str(row.get("decision_result") or "").strip().lower() == "submitted":
            bucket["submitted_count"] += 1.0
        if str(row.get("outcome_truth_status") or "").strip().lower() == "complete":
            bucket["complete_joined_count"] += 1.0
            if str(row.get("decision_quality") or "").strip().lower() == "incorrect":
                bucket["complete_bad_count"] += 1.0
        dominant_driver = str(row.get("dominant_driver") or "").strip()
        if dominant_driver:
            bucket["dominant_driver_counts"][dominant_driver] += 1
    summary: list[dict[str, Any]] = []
    for target_side_ref, bucket in grouped.items():
        complete_joined_count = float(bucket["complete_joined_count"])
        summary.append(
            {
                "target_side_ref": target_side_ref,
                "shadow_row_count": float(bucket["shadow_row_count"]),
                "candidate_count": float(bucket["candidate_count"]),
                "clean_count": float(bucket["clean_count"]),
                "borderline_count": float(bucket["borderline_count"]),
                "trash_count": float(bucket["trash_count"]),
                "submitted_count": float(bucket["submitted_count"]),
                "complete_joined_count": complete_joined_count,
                "complete_bad_count": float(bucket["complete_bad_count"]),
                "complete_bad_ratio": _safe_ratio(bucket["complete_bad_count"], complete_joined_count),
                "dominant_driver_counts": dict(
                    sorted(
                        (key, int(value)) for key, value in bucket["dominant_driver_counts"].items()
                    )
                ),
                "source_runs": sorted(bucket["source_runs"]),
            }
        )
    summary.sort(
        key=lambda item: (
            -float(_coerce_float(item.get("trash_count")) or 0.0),
            -float(_coerce_float(item.get("complete_bad_count")) or 0.0),
            str(item.get("target_side_ref") or ""),
        )
    )
    return summary


def _maker_admission_example_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for row in rows[:5]:
        examples.append(
            {
                "run_id": str(row.get("run_id") or ""),
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


def _build_maker_admission_bundle_outputs(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    population_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    dominant_driver_counts: Counter[str] = Counter()
    submit_counts_by_class: Counter[str] = Counter()
    complete_joined_counts_by_class: Counter[str] = Counter()
    complete_bad_counts_by_class: Counter[str] = Counter()
    multifill_complete_counts_by_class: Counter[str] = Counter()
    multifill_incorrect_counts_by_class: Counter[str] = Counter()
    outcome_truth_status_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    claim_boundary_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    evaluation_horizon_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    top_trash_target_side_counts: Counter[str] = Counter()
    top_clean_target_side_counts: Counter[str] = Counter()
    rubric_version_counts: Counter[str] = Counter()
    cannon_shadow_version_counts: Counter[str] = Counter()
    cannon_window_counts: Counter[str] = Counter()
    maker_timing_band_counts: Counter[str] = Counter()
    candidate_counts_by_timing_band: Counter[str] = Counter()
    submitted_counts_by_timing_band: Counter[str] = Counter()
    complete_joined_counts_by_timing_band: Counter[str] = Counter()
    complete_bad_counts_by_timing_band: Counter[str] = Counter()
    multifill_complete_counts_by_timing_band: Counter[str] = Counter()
    multifill_incorrect_counts_by_timing_band: Counter[str] = Counter()
    admission_class_counts_by_timing_band: dict[str, Counter[str]] = defaultdict(Counter)
    session_regime_counts: Counter[str] = Counter()
    stack_pressure_counts: Counter[str] = Counter()
    secondary_oracle_status_counts: Counter[str] = Counter()
    secondary_oracle_confirmation_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    depth_multiple_values: list[float] = []

    for row in rows:
        rubric_version = row.get("admission_rubric_version")
        if rubric_version is not None:
            rubric_version_counts[str(rubric_version)] += 1
        cannon_shadow_version = row.get("maker_cannon_shadow_version")
        if cannon_shadow_version is not None:
            cannon_shadow_version_counts[str(cannon_shadow_version)] += 1
        population_class = str(row.get("population_class") or "")
        if population_class:
            population_counts[population_class] += 1
        cannon_window_counts[str(row.get("cannon_window_class") or "unknown")] += 1
        maker_timing_band = str(
            row.get("maker_timing_band_class") or _maker_shadow_timing_band_class(row.get("sec_to_expiry"))
        )
        maker_timing_band_counts[maker_timing_band] += 1
        session_regime_counts[str(row.get("session_regime_class") or "unknown")] += 1
        stack_pressure_counts[str(row.get("stack_pressure_class") or "unknown")] += 1
        secondary_oracle_status_counts[str(row.get("secondary_oracle_status") or "unknown")] += 1
        secondary_oracle_confirmation_counts[
            "confirmed" if bool(row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
        ] += 1
        if isinstance(row.get("cannon_depth_requirement_met"), bool):
            cannon_depth_requirement_counts[
                "met" if bool(row.get("cannon_depth_requirement_met")) else "not_met"
            ] += 1
        else:
            cannon_depth_requirement_counts["unknown"] += 1
        if isinstance(row.get("depth_multiple_vs_cannon_target"), (int, float)):
            depth_multiple_values.append(float(row["depth_multiple_vs_cannon_target"]))
        admission_class = str(row.get("admission_class") or "")
        if population_class != "candidate" or admission_class not in {"clean", "borderline", "trash"}:
            continue
        candidate_counts_by_timing_band[maker_timing_band] += 1
        admission_class_counts_by_timing_band[maker_timing_band][admission_class] += 1
        class_counts[admission_class] += 1
        dominant_driver_counts[str(row.get("dominant_driver") or "unknown")] += 1
        target_side_ref = str(row.get("target_side_ref") or "unknown")
        if admission_class == "trash":
            top_trash_target_side_counts[target_side_ref] += 1
        elif admission_class == "clean":
            top_clean_target_side_counts[target_side_ref] += 1
        if str(row.get("decision_result") or "").strip().lower() == "submitted":
            submit_counts_by_class[admission_class] += 1
            submitted_counts_by_timing_band[maker_timing_band] += 1
        outcome_truth_status = str(row.get("outcome_truth_status") or "").strip().lower()
        if outcome_truth_status:
            outcome_truth_status_by_class[admission_class][outcome_truth_status] += 1
        claim_boundary = str(row.get("claim_boundary_class") or "").strip()
        if claim_boundary:
            claim_boundary_by_class[admission_class][claim_boundary] += 1
        evaluation_horizon = row.get("evaluation_horizon_ms")
        if evaluation_horizon is not None:
            evaluation_horizon_by_class[admission_class][str(evaluation_horizon)] += 1
        if outcome_truth_status != "complete":
            continue
        complete_joined_counts_by_class[admission_class] += 1
        complete_joined_counts_by_timing_band[maker_timing_band] += 1
        if str(row.get("decision_quality") or "").strip().lower() == "incorrect":
            complete_bad_counts_by_class[admission_class] += 1
            complete_bad_counts_by_timing_band[maker_timing_band] += 1
        fill_count = int(_coerce_float(row.get("fill_count")) or 0.0)
        if fill_count >= 2:
            multifill_complete_counts_by_class[admission_class] += 1
            multifill_complete_counts_by_timing_band[maker_timing_band] += 1
            if str(row.get("decision_quality") or "").strip().lower() == "incorrect":
                multifill_incorrect_counts_by_class[admission_class] += 1
                multifill_incorrect_counts_by_timing_band[maker_timing_band] += 1

    clean_but_bad_rows = [
        row
        for row in rows
        if str(row.get("population_class") or "") == "candidate"
        and str(row.get("admission_class") or "") == "clean"
        and str(row.get("outcome_truth_status") or "").strip().lower() == "complete"
        and str(row.get("decision_quality") or "").strip().lower() == "incorrect"
    ]
    trash_but_okay_rows = [
        row
        for row in rows
        if str(row.get("population_class") or "") == "candidate"
        and str(row.get("admission_class") or "") == "trash"
        and str(row.get("outcome_truth_status") or "").strip().lower() == "complete"
        and str(row.get("decision_quality") or "").strip().lower() in {"correct", "neutral"}
    ]

    bundle_rubric_version: str | int | None = None
    if rubric_version_counts:
        if len(rubric_version_counts) == 1:
            only_key = next(iter(rubric_version_counts))
            bundle_rubric_version = int(only_key) if only_key.isdigit() else only_key
        else:
            bundle_rubric_version = "mixed"
    bundle_cannon_shadow_version: str | int | None = None
    if cannon_shadow_version_counts:
        if len(cannon_shadow_version_counts) == 1:
            only_key = next(iter(cannon_shadow_version_counts))
            bundle_cannon_shadow_version = int(only_key) if only_key.isdigit() else only_key
        else:
            bundle_cannon_shadow_version = "mixed"

    summary = {
        "admission_rubric_version": bundle_rubric_version,
        "maker_cannon_shadow_version": bundle_cannon_shadow_version,
        "admission_rubric_version_distribution": {
            key: int(value) for key, value in sorted(rubric_version_counts.items())
        },
        "maker_cannon_shadow_version_distribution": {
            key: int(value) for key, value in sorted(cannon_shadow_version_counts.items())
        },
        "row_count": int(len(rows)),
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
            class_name: int(complete_joined_counts_by_class[class_name])
            for class_name in ("clean", "borderline", "trash")
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
        "cannon_depth_requirement_counts": {
            key: int(cannon_depth_requirement_counts[key]) for key in sorted(cannon_depth_requirement_counts)
        },
        "depth_multiple_vs_cannon_target_summary": _numeric_summary(depth_multiple_values),
    }
    calibration_audit = {
        "admission_rubric_version": summary["admission_rubric_version"],
        "maker_cannon_shadow_version": summary["maker_cannon_shadow_version"],
        "admission_rubric_version_distribution": summary["admission_rubric_version_distribution"],
        "maker_cannon_shadow_version_distribution": summary["maker_cannon_shadow_version_distribution"],
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
        "clean_but_bad_examples": _maker_admission_example_rows(clean_but_bad_rows),
        "trash_but_okay_examples": _maker_admission_example_rows(trash_but_okay_rows),
    }
    return summary, calibration_audit


def _row_has_lifecycle_residue_truth(row: dict[str, Any]) -> bool:
    current_lifecycle_truth = bool(
        row.get("open_order_cleanup_required", False)
        or row.get("settlement_hold_required", False)
        or row.get("unresolved_lifecycle_obligation", False)
        or row.get("cancel_fail_closed", False)
    )
    if current_lifecycle_truth:
        return True
    return bool(row.get(HISTORICAL_LIFECYCLE_RESIDUE_ACTIVE_FIELD, False))


def _build_maker_cannon_probe_bundle_outputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    population_counts: Counter[str] = Counter()
    reject_reason_counts: Counter[str] = Counter()
    latent_market_reject_reason_counts: Counter[str] = Counter()
    latent_market_dominant_reject_reason_counts: Counter[str] = Counter()
    latent_market_truth_class_counts: Counter[str] = Counter()
    latent_market_full_candidate_population_counts: Counter[str] = Counter()
    external_blocked_latent_market_reject_reason_counts: Counter[str] = Counter()
    lifecycle_phase_counts: Counter[str] = Counter()
    financial_posture_counts: Counter[str] = Counter()
    window_counts: Counter[str] = Counter()
    session_regime_counts: Counter[str] = Counter()
    stack_pressure_counts: Counter[str] = Counter()
    secondary_oracle_status_counts: Counter[str] = Counter()
    secondary_oracle_confirmation_counts: Counter[str] = Counter()
    geometry_viable_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    full_candidate_counts: Counter[str] = Counter()
    version_counts: Counter[str] = Counter()
    depth_multiple_values: list[float] = []
    total_maker_edge_eval_rows = 0
    late_window_raw_row_count = 0
    ignored_non_late_window_row_count = 0
    external_blocked_latent_market_evaluable_count = 0
    external_blocked_latent_market_full_cannon_candidate_count = 0
    external_blocked_latent_full_examples: list[dict[str, Any]] = []

    for row in rows:
        version = row.get("maker_cannon_probe_version")
        if version is not None:
            version_counts[str(version)] += 1
        population_counts[str(row.get("population_class") or "unknown")] += 1
        lifecycle_phase_counts[_probe_lifecycle_phase(row)] += 1
        financial_posture_counts[str(row.get("financial_posture_class") or "unknown")] += 1
        window_counts[str(row.get("cannon_window_class") or "unknown")] += 1
        session_regime_counts[str(row.get("session_regime_class") or "unknown")] += 1
        stack_pressure_counts[str(row.get("stack_pressure_class") or "unknown")] += 1
        secondary_oracle_status_counts[str(row.get("secondary_oracle_status") or "unknown")] += 1
        secondary_oracle_confirmation_counts[
            "confirmed" if bool(row.get("secondary_oracle_confirmation", False)) else "not_confirmed"
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
        if bool(row.get("full_cannon_candidate", False)):
            full_candidate_counts["full"] += 1
        if bool(row.get("latent_market_candidate", False)):
            if bool(row.get("latent_market_full_cannon_candidate", False)):
                full_candidate_counts["latent_full"] += 1
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
                            "lifecycle_phase": _probe_lifecycle_phase(row),
                            "financial_posture_class": str(
                                row.get("financial_posture_class") or ""
                            ),
                            "lifecycle_residue_active": _row_has_lifecycle_residue_truth(row),
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
        for reason in list(row.get("reject_reasons") or []):
            reject_reason_counts[str(reason or "unknown")] += 1
        if isinstance(row.get("total_maker_edge_eval_rows"), (int, float)):
            total_maker_edge_eval_rows = max(total_maker_edge_eval_rows, int(float(row["total_maker_edge_eval_rows"])))
        if isinstance(row.get("late_window_raw_row_count"), (int, float)):
            late_window_raw_row_count = max(late_window_raw_row_count, int(float(row["late_window_raw_row_count"])))
        if isinstance(row.get("ignored_non_late_window_row_count"), (int, float)):
            ignored_non_late_window_row_count = max(
                ignored_non_late_window_row_count,
                int(float(row["ignored_non_late_window_row_count"])),
            )

    bundle_version: str | int | None = None
    if version_counts:
        if len(version_counts) == 1:
            only_key = next(iter(version_counts))
            bundle_version = int(only_key) if only_key.isdigit() else only_key
        else:
            bundle_version = "mixed"

    return {
        "maker_cannon_probe_version": bundle_version,
        "maker_cannon_probe_version_distribution": {
            key: int(value) for key, value in sorted(version_counts.items())
        },
        "row_count": int(len(rows)),
        "population_class_counts": {
            key: int(population_counts[key]) for key in sorted(population_counts)
        },
        "full_cannon_candidate_count": int(full_candidate_counts["full"]),
        "latent_market_truth_class_counts": {
            key: int(latent_market_truth_class_counts[key])
            for key in sorted(latent_market_truth_class_counts)
        },
        "latent_market_full_cannon_candidate_count": int(full_candidate_counts["latent_full"]),
        "latent_market_full_candidate_population_class_distribution": {
            key: int(latent_market_full_candidate_population_counts[key])
            for key in sorted(latent_market_full_candidate_population_counts)
        },
        "reject_reason_distribution": {
            key: int(reject_reason_counts[key]) for key in sorted(reject_reason_counts)
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
        "lifecycle_phase_distribution": {
            key: int(lifecycle_phase_counts[key]) for key in sorted(lifecycle_phase_counts)
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
        "geometry_viable_counts": {
            key: int(geometry_viable_counts[key]) for key in sorted(geometry_viable_counts)
        },
        "cannon_depth_requirement_counts": {
            key: int(cannon_depth_requirement_counts[key]) for key in sorted(cannon_depth_requirement_counts)
        },
        "depth_multiple_vs_cannon_target_summary": _numeric_summary(depth_multiple_values),
        "external_blocked_latent_full_examples": external_blocked_latent_full_examples,
        "total_maker_edge_eval_rows": int(total_maker_edge_eval_rows),
        "late_window_raw_row_count": int(late_window_raw_row_count),
        "ignored_non_late_window_row_count": int(ignored_non_late_window_row_count),
    }


def _build_maker_cannon_probe_session_sweep(
    run_rows: list[dict[str, Any]],
    probe_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    run_summary_by_id: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        run_id = str(row.get("run_id") or pathlib.Path(str(row.get("report_dir") or "")).name).strip()
        if not run_id:
            continue
        run_summary_by_id[run_id] = {
            "run_id": run_id,
            "report_dir": row.get("report_dir"),
            "runtime_classification": row.get("runtime_classification"),
            "duration_minutes": _coerce_float(row.get("duration_minutes")),
            "run_start_ts_utc": row.get("run_start_ts_utc"),
            "run_stop_ts_utc": row.get("run_stop_ts_utc"),
            "run_start_hour_utc": row.get("run_start_hour_utc"),
            "run_start_session_bucket": row.get("run_start_session_bucket") or "unknown",
            "session_type": row.get("run_session_type"),
            "row_count": 0,
            "candidate_count": 0,
            "external_blocked_count": 0,
            "truth_thin_count": 0,
            "full_cannon_candidate_count": 0,
            "latent_market_evaluable_count": 0,
            "latent_market_full_cannon_candidate_count": 0,
            "external_blocked_latent_market_evaluable_count": 0,
            "external_blocked_latent_market_full_cannon_candidate_count": 0,
            "authoritative_reference_count": 0,
            "not_available_reference_count": 0,
            "positive_favored_depth_count": 0,
            "zero_imputed_favored_depth_count": 0,
            "depth_requirement_met_count": 0,
            "geometry_viable_count": 0,
            "secondary_oracle_confirmed_count": 0,
            "reject_reason_counts": Counter(),
            "latent_market_reject_reason_counts": Counter(),
            "depth_multiple_values": [],
        }

    for probe_row in probe_rows:
        run_id = str(probe_row.get("run_id") or "").strip()
        if not run_id:
            continue
        summary = run_summary_by_id.setdefault(
            run_id,
            {
                "run_id": run_id,
                "report_dir": None,
                "runtime_classification": None,
                "duration_minutes": None,
                "run_start_ts_utc": None,
                "run_stop_ts_utc": None,
                "run_start_hour_utc": None,
                "run_start_session_bucket": "unknown",
                "session_type": None,
                "row_count": 0,
                "candidate_count": 0,
                "external_blocked_count": 0,
                "truth_thin_count": 0,
                "full_cannon_candidate_count": 0,
                "latent_market_evaluable_count": 0,
                "latent_market_full_cannon_candidate_count": 0,
                "external_blocked_latent_market_evaluable_count": 0,
                "external_blocked_latent_market_full_cannon_candidate_count": 0,
                "authoritative_reference_count": 0,
                "not_available_reference_count": 0,
                "positive_favored_depth_count": 0,
                "zero_imputed_favored_depth_count": 0,
                "depth_requirement_met_count": 0,
                "geometry_viable_count": 0,
                "secondary_oracle_confirmed_count": 0,
                "reject_reason_counts": Counter(),
                "latent_market_reject_reason_counts": Counter(),
                "depth_multiple_values": [],
            },
        )
        summary["row_count"] += 1
        population_class = str(probe_row.get("population_class") or "unknown")
        if population_class == "candidate":
            summary["candidate_count"] += 1
        elif population_class == "external_blocked":
            summary["external_blocked_count"] += 1
        elif population_class == "truth_thin":
            summary["truth_thin_count"] += 1
        if bool(probe_row.get("full_cannon_candidate", False)):
            summary["full_cannon_candidate_count"] += 1
        if bool(probe_row.get("latent_market_candidate", False)):
            summary["latent_market_evaluable_count"] += 1
            if bool(probe_row.get("latent_market_full_cannon_candidate", False)):
                summary["latent_market_full_cannon_candidate_count"] += 1
            if population_class == "external_blocked":
                summary["external_blocked_latent_market_evaluable_count"] += 1
                if bool(probe_row.get("latent_market_full_cannon_candidate", False)):
                    summary["external_blocked_latent_market_full_cannon_candidate_count"] += 1
        reference_class = str(probe_row.get("market_reference_class") or "unknown")
        if reference_class == "authoritative":
            summary["authoritative_reference_count"] += 1
        elif reference_class == "not_available":
            summary["not_available_reference_count"] += 1
        else:
            summary["not_available_reference_count"] += 1
        depth_class = str(probe_row.get("favored_side_depth_class") or "unknown")
        if depth_class == "positive":
            summary["positive_favored_depth_count"] += 1
        elif depth_class == "zero_imputed":
            summary["zero_imputed_favored_depth_count"] += 1
        if bool(probe_row.get("cannon_depth_requirement_met")):
            summary["depth_requirement_met_count"] += 1
        if probe_row.get("geometry_viable") is True:
            summary["geometry_viable_count"] += 1
        if bool(probe_row.get("secondary_oracle_confirmation", False)):
            summary["secondary_oracle_confirmed_count"] += 1
        if isinstance(probe_row.get("depth_multiple_vs_cannon_target"), (int, float)):
            summary["depth_multiple_values"].append(float(probe_row["depth_multiple_vs_cannon_target"]))
        for reason in list(probe_row.get("reject_reasons") or []):
            summary["reject_reason_counts"][str(reason or "unknown")] += 1
        for reason in list(probe_row.get("latent_market_reject_reasons") or []):
            summary["latent_market_reject_reason_counts"][str(reason or "unknown")] += 1

    session_bucket_summaries: dict[str, dict[str, Any]] = {}
    run_entries: list[dict[str, Any]] = []
    for run_id in sorted(run_summary_by_id):
        summary = run_summary_by_id[run_id]
        bucket = str(summary.get("run_start_session_bucket") or "unknown")
        bucket_summary = session_bucket_summaries.setdefault(
            bucket,
            {
                "run_count": 0,
                "run_with_probe_count": 0,
                "row_count": 0,
                "candidate_count": 0,
                "external_blocked_count": 0,
                "truth_thin_count": 0,
                "full_cannon_candidate_count": 0,
                "latent_market_evaluable_count": 0,
                "latent_market_full_cannon_candidate_count": 0,
                "external_blocked_latent_market_evaluable_count": 0,
                "external_blocked_latent_market_full_cannon_candidate_count": 0,
                "authoritative_reference_count": 0,
                "not_available_reference_count": 0,
                "positive_favored_depth_count": 0,
                "zero_imputed_favored_depth_count": 0,
                "depth_requirement_met_count": 0,
                "geometry_viable_count": 0,
                "secondary_oracle_confirmed_count": 0,
                "reject_reason_counts": Counter(),
                "latent_market_reject_reason_counts": Counter(),
                "depth_multiple_values": [],
                "full_candidate_run_ids": [],
                "latent_full_candidate_run_ids": [],
            },
        )
        bucket_summary["run_count"] += 1
        if int(summary["row_count"]) > 0:
            bucket_summary["run_with_probe_count"] += 1
        for field_name in (
            "row_count",
            "candidate_count",
            "external_blocked_count",
            "truth_thin_count",
            "full_cannon_candidate_count",
            "latent_market_evaluable_count",
            "latent_market_full_cannon_candidate_count",
            "external_blocked_latent_market_evaluable_count",
            "external_blocked_latent_market_full_cannon_candidate_count",
            "authoritative_reference_count",
            "not_available_reference_count",
            "positive_favored_depth_count",
            "zero_imputed_favored_depth_count",
            "depth_requirement_met_count",
            "geometry_viable_count",
            "secondary_oracle_confirmed_count",
        ):
            bucket_summary[field_name] += int(summary[field_name])
        bucket_summary["reject_reason_counts"].update(summary["reject_reason_counts"])
        bucket_summary["latent_market_reject_reason_counts"].update(
            summary["latent_market_reject_reason_counts"]
        )
        bucket_summary["depth_multiple_values"].extend(summary["depth_multiple_values"])
        if int(summary["full_cannon_candidate_count"]) > 0:
            bucket_summary["full_candidate_run_ids"].append(run_id)
        if int(summary["latent_market_full_cannon_candidate_count"]) > 0:
            bucket_summary["latent_full_candidate_run_ids"].append(run_id)

        run_entries.append(
            {
                "run_id": run_id,
                "report_dir": summary["report_dir"],
                "runtime_classification": summary["runtime_classification"],
                "duration_minutes": summary["duration_minutes"],
                "run_start_ts_utc": summary["run_start_ts_utc"],
                "run_stop_ts_utc": summary["run_stop_ts_utc"],
                "run_start_hour_utc": summary["run_start_hour_utc"],
                "run_start_session_bucket": bucket,
                "session_type": summary["session_type"],
                "row_count": int(summary["row_count"]),
                "candidate_count": int(summary["candidate_count"]),
                "external_blocked_count": int(summary["external_blocked_count"]),
                "truth_thin_count": int(summary["truth_thin_count"]),
                "full_cannon_candidate_count": int(summary["full_cannon_candidate_count"]),
                "latent_market_evaluable_count": int(summary["latent_market_evaluable_count"]),
                "latent_market_full_cannon_candidate_count": int(
                    summary["latent_market_full_cannon_candidate_count"]
                ),
                "external_blocked_latent_market_evaluable_count": int(
                    summary["external_blocked_latent_market_evaluable_count"]
                ),
                "external_blocked_latent_market_full_cannon_candidate_count": int(
                    summary["external_blocked_latent_market_full_cannon_candidate_count"]
                ),
                "authoritative_reference_count": int(summary["authoritative_reference_count"]),
                "not_available_reference_count": int(summary["not_available_reference_count"]),
                "positive_favored_depth_count": int(summary["positive_favored_depth_count"]),
                "zero_imputed_favored_depth_count": int(summary["zero_imputed_favored_depth_count"]),
                "depth_requirement_met_count": int(summary["depth_requirement_met_count"]),
                "geometry_viable_count": int(summary["geometry_viable_count"]),
                "secondary_oracle_confirmed_count": int(summary["secondary_oracle_confirmed_count"]),
                "reject_reason_counts": dict(summary["reject_reason_counts"].most_common()),
                "latent_market_reject_reason_counts": dict(
                    summary["latent_market_reject_reason_counts"].most_common()
                ),
                "depth_multiple_vs_cannon_target_summary": _numeric_summary(summary["depth_multiple_values"]),
            }
        )

    normalized_bucket_summaries: dict[str, Any] = {}
    for bucket in sorted(session_bucket_summaries):
        bucket_summary = session_bucket_summaries[bucket]
        normalized_bucket_summaries[bucket] = {
            "run_count": int(bucket_summary["run_count"]),
            "run_with_probe_count": int(bucket_summary["run_with_probe_count"]),
            "run_without_probe_count": int(
                bucket_summary["run_count"] - bucket_summary["run_with_probe_count"]
            ),
            "row_count": int(bucket_summary["row_count"]),
            "candidate_count": int(bucket_summary["candidate_count"]),
            "external_blocked_count": int(bucket_summary["external_blocked_count"]),
            "truth_thin_count": int(bucket_summary["truth_thin_count"]),
            "full_cannon_candidate_count": int(bucket_summary["full_cannon_candidate_count"]),
            "latent_market_evaluable_count": int(bucket_summary["latent_market_evaluable_count"]),
            "latent_market_full_cannon_candidate_count": int(
                bucket_summary["latent_market_full_cannon_candidate_count"]
            ),
            "external_blocked_latent_market_evaluable_count": int(
                bucket_summary["external_blocked_latent_market_evaluable_count"]
            ),
            "external_blocked_latent_market_full_cannon_candidate_count": int(
                bucket_summary["external_blocked_latent_market_full_cannon_candidate_count"]
            ),
            "authoritative_reference_count": int(bucket_summary["authoritative_reference_count"]),
            "not_available_reference_count": int(bucket_summary["not_available_reference_count"]),
            "positive_favored_depth_count": int(bucket_summary["positive_favored_depth_count"]),
            "zero_imputed_favored_depth_count": int(
                bucket_summary["zero_imputed_favored_depth_count"]
            ),
            "depth_requirement_met_count": int(bucket_summary["depth_requirement_met_count"]),
            "geometry_viable_count": int(bucket_summary["geometry_viable_count"]),
            "secondary_oracle_confirmed_count": int(
                bucket_summary["secondary_oracle_confirmed_count"]
            ),
            "reject_reason_counts": dict(bucket_summary["reject_reason_counts"].most_common()),
            "latent_market_reject_reason_counts": dict(
                bucket_summary["latent_market_reject_reason_counts"].most_common()
            ),
            "depth_multiple_vs_cannon_target_summary": _numeric_summary(
                bucket_summary["depth_multiple_values"]
            ),
            "full_candidate_run_ids": sorted(bucket_summary["full_candidate_run_ids"]),
            "latent_full_candidate_run_ids": sorted(
                bucket_summary["latent_full_candidate_run_ids"]
            ),
        }

    run_entries.sort(
        key=lambda item: (
            str(item.get("run_start_ts_utc") or ""),
            str(item.get("run_id") or ""),
        )
    )
    return {
        "run_count": int(len(run_summary_by_id)),
        "probe_row_count": int(len(probe_rows)),
        "session_bucket_summary": normalized_bucket_summaries,
        "run_summaries": run_entries,
    }


def _build_maker_mid_window_probe_bundle_outputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    population_counts: Counter[str] = Counter()
    reject_reason_counts: Counter[str] = Counter()
    latent_market_reject_reason_counts: Counter[str] = Counter()
    latent_market_dominant_reject_reason_counts: Counter[str] = Counter()
    latent_market_truth_class_counts: Counter[str] = Counter()
    latent_market_full_candidate_population_counts: Counter[str] = Counter()
    external_blocked_latent_market_reject_reason_counts: Counter[str] = Counter()
    lifecycle_phase_counts: Counter[str] = Counter()
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
    maker_phase_allowed_counts: Counter[str] = Counter()
    probe_visible_depth_fail_closed_zero_counts: Counter[str] = Counter()
    geometry_viable_counts: Counter[str] = Counter()
    cannon_depth_requirement_counts: Counter[str] = Counter()
    full_candidate_counts: Counter[str] = Counter()
    version_counts: Counter[str] = Counter()
    depth_multiple_values: list[float] = []
    total_maker_edge_eval_rows = 0
    mid_window_raw_row_count = 0
    ignored_non_mid_window_row_count = 0
    external_blocked_latent_market_evaluable_count = 0
    external_blocked_latent_market_full_mid_window_candidate_count = 0
    external_blocked_latent_full_examples: list[dict[str, Any]] = []

    for row in rows:
        version = row.get("maker_mid_window_probe_version")
        if version is not None:
            version_counts[str(version)] += 1
        population_counts[str(row.get("population_class") or "unknown")] += 1
        lifecycle_phase_counts[_probe_lifecycle_phase(row)] += 1
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
        maker_phase_allowed_counts[
            "allowed" if _probe_maker_phase_allowed(row) else "disallowed"
        ] += 1
        probe_visible_depth_fail_closed_zero_counts[
            str(row.get("probe_visible_depth_fail_closed_zero") or "unknown")
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
        if bool(row.get("full_mid_window_candidate", False)):
            full_candidate_counts["full"] += 1
        if bool(row.get("latent_market_candidate", False)):
            if bool(row.get("latent_market_full_mid_window_candidate", False)):
                full_candidate_counts["latent_full"] += 1
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
                            "lifecycle_phase": _probe_lifecycle_phase(row),
                            "financial_posture_class": str(
                                row.get("financial_posture_class") or ""
                            ),
                            "lifecycle_residue_active": _row_has_lifecycle_residue_truth(row),
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
                if bool(row.get("latent_market_full_mid_window_candidate", False)):
                    external_blocked_latent_market_full_mid_window_candidate_count += 1
        for reason in list(row.get("reject_reasons") or []):
            reject_reason_counts[str(reason or "unknown")] += 1
        if isinstance(row.get("total_maker_edge_eval_rows"), (int, float)):
            total_maker_edge_eval_rows = max(total_maker_edge_eval_rows, int(float(row["total_maker_edge_eval_rows"])))
        if isinstance(row.get("mid_window_raw_row_count"), (int, float)):
            mid_window_raw_row_count = max(mid_window_raw_row_count, int(float(row["mid_window_raw_row_count"])))
        if isinstance(row.get("ignored_non_mid_window_row_count"), (int, float)):
            ignored_non_mid_window_row_count = max(
                ignored_non_mid_window_row_count,
                int(float(row["ignored_non_mid_window_row_count"])),
            )

    bundle_version: str | int | None = None
    if version_counts:
        if len(version_counts) == 1:
            only_key = next(iter(version_counts))
            bundle_version = int(only_key) if only_key.isdigit() else only_key
        else:
            bundle_version = "mixed"

    return {
        "maker_mid_window_probe_version": bundle_version,
        "maker_mid_window_probe_version_distribution": {
            key: int(value) for key, value in sorted(version_counts.items())
        },
        "row_count": int(len(rows)),
        "population_class_counts": {
            key: int(population_counts[key]) for key in sorted(population_counts)
        },
        "full_mid_window_candidate_count": int(full_candidate_counts["full"]),
        "latent_market_truth_class_counts": {
            key: int(latent_market_truth_class_counts[key])
            for key in sorted(latent_market_truth_class_counts)
        },
        "latent_market_full_mid_window_candidate_count": int(full_candidate_counts["latent_full"]),
        "latent_market_full_candidate_population_class_distribution": {
            key: int(latent_market_full_candidate_population_counts[key])
            for key in sorted(latent_market_full_candidate_population_counts)
        },
        "reject_reason_distribution": {
            key: int(reject_reason_counts[key]) for key in sorted(reject_reason_counts)
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
            external_blocked_latent_market_full_mid_window_candidate_count
        ),
        "external_blocked_latent_market_reject_reason_distribution": {
            key: int(external_blocked_latent_market_reject_reason_counts[key])
            for key in sorted(external_blocked_latent_market_reject_reason_counts)
        },
        "lifecycle_phase_distribution": {
            key: int(lifecycle_phase_counts[key]) for key in sorted(lifecycle_phase_counts)
        },
        "market_reference_class_distribution": {
            key: int(market_reference_class_counts[key]) for key in sorted(market_reference_class_counts)
        },
        "market_reference_mode_distribution": {
            key: int(market_reference_mode_counts[key]) for key in sorted(market_reference_mode_counts)
        },
        "market_reference_source_side_distribution": {
            key: int(market_reference_source_side_counts[key])
            for key in sorted(market_reference_source_side_counts)
        },
        "market_probability_band_distribution": {
            key: int(market_probability_band_counts[key]) for key in sorted(market_probability_band_counts)
        },
        "favored_side_depth_class_distribution": {
            key: int(favored_side_depth_class_counts[key])
            for key in sorted(favored_side_depth_class_counts)
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
        "maker_phase_allowed_distribution": {
            key: int(maker_phase_allowed_counts[key])
            for key in sorted(maker_phase_allowed_counts)
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
        "depth_multiple_vs_cannon_target_summary": _numeric_summary(depth_multiple_values),
        "external_blocked_latent_full_examples": external_blocked_latent_full_examples,
        "total_maker_edge_eval_rows": int(total_maker_edge_eval_rows),
        "mid_window_raw_row_count": int(mid_window_raw_row_count),
        "ignored_non_mid_window_row_count": int(ignored_non_mid_window_row_count),
    }


def _build_maker_mid_window_probe_session_sweep(
    run_rows: list[dict[str, Any]],
    probe_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    run_summary_by_id: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        run_id = str(row.get("run_id") or pathlib.Path(str(row.get("report_dir") or "")).name).strip()
        if not run_id:
            continue
        run_summary_by_id[run_id] = {
            "run_id": run_id,
            "report_dir": row.get("report_dir"),
            "runtime_classification": row.get("runtime_classification"),
            "duration_minutes": _coerce_float(row.get("duration_minutes")),
            "run_start_ts_utc": row.get("run_start_ts_utc"),
            "run_stop_ts_utc": row.get("run_stop_ts_utc"),
            "run_start_hour_utc": row.get("run_start_hour_utc"),
            "run_start_session_bucket": row.get("run_start_session_bucket") or "unknown",
            "session_type": row.get("run_session_type"),
            "row_count": 0,
            "candidate_count": 0,
            "external_blocked_count": 0,
            "truth_thin_count": 0,
            "full_mid_window_candidate_count": 0,
            "latent_market_evaluable_count": 0,
            "latent_market_full_mid_window_candidate_count": 0,
            "external_blocked_latent_market_evaluable_count": 0,
            "external_blocked_latent_market_full_mid_window_candidate_count": 0,
            "authoritative_reference_count": 0,
            "not_available_reference_count": 0,
            "positive_favored_depth_count": 0,
            "zero_imputed_favored_depth_count": 0,
            "depth_requirement_met_count": 0,
            "geometry_viable_count": 0,
            "secondary_oracle_confirmed_count": 0,
            "reject_reason_counts": Counter(),
            "latent_market_reject_reason_counts": Counter(),
            "depth_multiple_values": [],
        }

    for probe_row in probe_rows:
        run_id = str(probe_row.get("run_id") or "").strip()
        if not run_id:
            continue
        summary = run_summary_by_id.setdefault(
            run_id,
            {
                "run_id": run_id,
                "report_dir": None,
                "runtime_classification": None,
                "duration_minutes": None,
                "run_start_ts_utc": None,
                "run_stop_ts_utc": None,
                "run_start_hour_utc": None,
                "run_start_session_bucket": "unknown",
                "session_type": None,
                "row_count": 0,
                "candidate_count": 0,
                "external_blocked_count": 0,
                "truth_thin_count": 0,
                "full_mid_window_candidate_count": 0,
                "latent_market_evaluable_count": 0,
                "latent_market_full_mid_window_candidate_count": 0,
                "external_blocked_latent_market_evaluable_count": 0,
                "external_blocked_latent_market_full_mid_window_candidate_count": 0,
                "authoritative_reference_count": 0,
                "not_available_reference_count": 0,
                "positive_favored_depth_count": 0,
                "zero_imputed_favored_depth_count": 0,
                "depth_requirement_met_count": 0,
                "geometry_viable_count": 0,
                "secondary_oracle_confirmed_count": 0,
                "reject_reason_counts": Counter(),
                "latent_market_reject_reason_counts": Counter(),
                "depth_multiple_values": [],
            },
        )
        summary["row_count"] += 1
        population_class = str(probe_row.get("population_class") or "unknown")
        if population_class == "candidate":
            summary["candidate_count"] += 1
        elif population_class == "external_blocked":
            summary["external_blocked_count"] += 1
        elif population_class == "truth_thin":
            summary["truth_thin_count"] += 1
        if bool(probe_row.get("full_mid_window_candidate", False)):
            summary["full_mid_window_candidate_count"] += 1
        if bool(probe_row.get("latent_market_candidate", False)):
            summary["latent_market_evaluable_count"] += 1
            if bool(probe_row.get("latent_market_full_mid_window_candidate", False)):
                summary["latent_market_full_mid_window_candidate_count"] += 1
            if population_class == "external_blocked":
                summary["external_blocked_latent_market_evaluable_count"] += 1
                if bool(probe_row.get("latent_market_full_mid_window_candidate", False)):
                    summary["external_blocked_latent_market_full_mid_window_candidate_count"] += 1
        reference_class = str(probe_row.get("market_reference_class") or "unknown")
        if reference_class == "authoritative":
            summary["authoritative_reference_count"] += 1
        elif reference_class == "not_available":
            summary["not_available_reference_count"] += 1
        else:
            summary["not_available_reference_count"] += 1
        depth_class = str(probe_row.get("favored_side_depth_class") or "unknown")
        if depth_class == "positive":
            summary["positive_favored_depth_count"] += 1
        elif depth_class == "zero_imputed":
            summary["zero_imputed_favored_depth_count"] += 1
        if bool(probe_row.get("cannon_depth_requirement_met")):
            summary["depth_requirement_met_count"] += 1
        if probe_row.get("geometry_viable") is True:
            summary["geometry_viable_count"] += 1
        if bool(probe_row.get("secondary_oracle_confirmation", False)):
            summary["secondary_oracle_confirmed_count"] += 1
        if isinstance(probe_row.get("depth_multiple_vs_cannon_target"), (int, float)):
            summary["depth_multiple_values"].append(float(probe_row["depth_multiple_vs_cannon_target"]))
        for reason in list(probe_row.get("reject_reasons") or []):
            summary["reject_reason_counts"][str(reason or "unknown")] += 1
        for reason in list(probe_row.get("latent_market_reject_reasons") or []):
            summary["latent_market_reject_reason_counts"][str(reason or "unknown")] += 1

    session_bucket_summaries: dict[str, dict[str, Any]] = {}
    run_entries: list[dict[str, Any]] = []
    for run_id in sorted(run_summary_by_id):
        summary = run_summary_by_id[run_id]
        bucket = str(summary.get("run_start_session_bucket") or "unknown")
        bucket_summary = session_bucket_summaries.setdefault(
            bucket,
            {
                "run_count": 0,
                "run_with_probe_count": 0,
                "row_count": 0,
                "candidate_count": 0,
                "external_blocked_count": 0,
                "truth_thin_count": 0,
                "full_mid_window_candidate_count": 0,
                "latent_market_evaluable_count": 0,
                "latent_market_full_mid_window_candidate_count": 0,
                "external_blocked_latent_market_evaluable_count": 0,
                "external_blocked_latent_market_full_mid_window_candidate_count": 0,
                "authoritative_reference_count": 0,
                "not_available_reference_count": 0,
                "positive_favored_depth_count": 0,
                "zero_imputed_favored_depth_count": 0,
                "depth_requirement_met_count": 0,
                "geometry_viable_count": 0,
                "secondary_oracle_confirmed_count": 0,
                "reject_reason_counts": Counter(),
                "latent_market_reject_reason_counts": Counter(),
                "depth_multiple_values": [],
                "full_candidate_run_ids": [],
                "latent_full_candidate_run_ids": [],
            },
        )
        bucket_summary["run_count"] += 1
        if int(summary["row_count"]) > 0:
            bucket_summary["run_with_probe_count"] += 1
        for field_name in (
            "row_count",
            "candidate_count",
            "external_blocked_count",
            "truth_thin_count",
            "full_mid_window_candidate_count",
            "latent_market_evaluable_count",
            "latent_market_full_mid_window_candidate_count",
            "external_blocked_latent_market_evaluable_count",
            "external_blocked_latent_market_full_mid_window_candidate_count",
            "authoritative_reference_count",
            "not_available_reference_count",
            "positive_favored_depth_count",
            "zero_imputed_favored_depth_count",
            "depth_requirement_met_count",
            "geometry_viable_count",
            "secondary_oracle_confirmed_count",
        ):
            bucket_summary[field_name] += int(summary[field_name])
        bucket_summary["reject_reason_counts"].update(summary["reject_reason_counts"])
        bucket_summary["latent_market_reject_reason_counts"].update(
            summary["latent_market_reject_reason_counts"]
        )
        bucket_summary["depth_multiple_values"].extend(summary["depth_multiple_values"])
        if int(summary["full_mid_window_candidate_count"]) > 0:
            bucket_summary["full_candidate_run_ids"].append(run_id)
        if int(summary["latent_market_full_mid_window_candidate_count"]) > 0:
            bucket_summary["latent_full_candidate_run_ids"].append(run_id)

        run_entries.append(
            {
                "run_id": run_id,
                "report_dir": summary["report_dir"],
                "runtime_classification": summary["runtime_classification"],
                "duration_minutes": summary["duration_minutes"],
                "run_start_ts_utc": summary["run_start_ts_utc"],
                "run_stop_ts_utc": summary["run_stop_ts_utc"],
                "run_start_hour_utc": summary["run_start_hour_utc"],
                "run_start_session_bucket": bucket,
                "session_type": summary["session_type"],
                "row_count": int(summary["row_count"]),
                "candidate_count": int(summary["candidate_count"]),
                "external_blocked_count": int(summary["external_blocked_count"]),
                "truth_thin_count": int(summary["truth_thin_count"]),
                "full_mid_window_candidate_count": int(summary["full_mid_window_candidate_count"]),
                "latent_market_evaluable_count": int(summary["latent_market_evaluable_count"]),
                "latent_market_full_mid_window_candidate_count": int(
                    summary["latent_market_full_mid_window_candidate_count"]
                ),
                "external_blocked_latent_market_evaluable_count": int(
                    summary["external_blocked_latent_market_evaluable_count"]
                ),
                "external_blocked_latent_market_full_mid_window_candidate_count": int(
                    summary["external_blocked_latent_market_full_mid_window_candidate_count"]
                ),
                "authoritative_reference_count": int(summary["authoritative_reference_count"]),
                "not_available_reference_count": int(summary["not_available_reference_count"]),
                "positive_favored_depth_count": int(summary["positive_favored_depth_count"]),
                "zero_imputed_favored_depth_count": int(summary["zero_imputed_favored_depth_count"]),
                "depth_requirement_met_count": int(summary["depth_requirement_met_count"]),
                "geometry_viable_count": int(summary["geometry_viable_count"]),
                "secondary_oracle_confirmed_count": int(summary["secondary_oracle_confirmed_count"]),
                "reject_reason_counts": dict(summary["reject_reason_counts"].most_common()),
                "latent_market_reject_reason_counts": dict(
                    summary["latent_market_reject_reason_counts"].most_common()
                ),
                "depth_multiple_vs_cannon_target_summary": _numeric_summary(summary["depth_multiple_values"]),
            }
        )

    normalized_bucket_summaries: dict[str, Any] = {}
    for bucket in sorted(session_bucket_summaries):
        bucket_summary = session_bucket_summaries[bucket]
        normalized_bucket_summaries[bucket] = {
            "run_count": int(bucket_summary["run_count"]),
            "run_with_probe_count": int(bucket_summary["run_with_probe_count"]),
            "run_without_probe_count": int(
                bucket_summary["run_count"] - bucket_summary["run_with_probe_count"]
            ),
            "row_count": int(bucket_summary["row_count"]),
            "candidate_count": int(bucket_summary["candidate_count"]),
            "external_blocked_count": int(bucket_summary["external_blocked_count"]),
            "truth_thin_count": int(bucket_summary["truth_thin_count"]),
            "full_mid_window_candidate_count": int(bucket_summary["full_mid_window_candidate_count"]),
            "latent_market_evaluable_count": int(bucket_summary["latent_market_evaluable_count"]),
            "latent_market_full_mid_window_candidate_count": int(
                bucket_summary["latent_market_full_mid_window_candidate_count"]
            ),
            "external_blocked_latent_market_evaluable_count": int(
                bucket_summary["external_blocked_latent_market_evaluable_count"]
            ),
            "external_blocked_latent_market_full_mid_window_candidate_count": int(
                bucket_summary["external_blocked_latent_market_full_mid_window_candidate_count"]
            ),
            "authoritative_reference_count": int(bucket_summary["authoritative_reference_count"]),
            "not_available_reference_count": int(bucket_summary["not_available_reference_count"]),
            "positive_favored_depth_count": int(bucket_summary["positive_favored_depth_count"]),
            "zero_imputed_favored_depth_count": int(
                bucket_summary["zero_imputed_favored_depth_count"]
            ),
            "depth_requirement_met_count": int(bucket_summary["depth_requirement_met_count"]),
            "geometry_viable_count": int(bucket_summary["geometry_viable_count"]),
            "secondary_oracle_confirmed_count": int(
                bucket_summary["secondary_oracle_confirmed_count"]
            ),
            "reject_reason_counts": dict(bucket_summary["reject_reason_counts"].most_common()),
            "latent_market_reject_reason_counts": dict(
                bucket_summary["latent_market_reject_reason_counts"].most_common()
            ),
            "depth_multiple_vs_cannon_target_summary": _numeric_summary(
                bucket_summary["depth_multiple_values"]
            ),
            "full_candidate_run_ids": sorted(bucket_summary["full_candidate_run_ids"]),
            "latent_full_candidate_run_ids": sorted(
                bucket_summary["latent_full_candidate_run_ids"]
            ),
        }

    run_entries.sort(
        key=lambda item: (
            str(item.get("run_start_ts_utc") or ""),
            str(item.get("run_id") or ""),
        )
    )
    return {
        "run_count": int(len(run_summary_by_id)),
        "probe_row_count": int(len(probe_rows)),
        "session_bucket_summary": normalized_bucket_summaries,
        "run_summaries": run_entries,
    }


def _build_bundle_manifest(
    *,
    out_dir: pathlib.Path,
    report_root: pathlib.Path,
    rows: list[dict[str, Any]],
    outputs: dict[str, pathlib.Path],
    run_id: str | None,
    limit: int | None,
    profile: str | None,
    run_id_file: pathlib.Path | None,
    selected_run_ids_filter: list[str] | None,
) -> dict[str, Any]:
    artifact_files: dict[str, dict[str, Any]] = {}
    for key, path in sorted(outputs.items()):
        artifact_files[path.name] = {
            "output_key": key,
            "relative_path": path.relative_to(out_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }

    run_ids = sorted(str(row.get("run_id") or "").strip() for row in rows if str(row.get("run_id") or "").strip())
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "bundle_closed_snapshot": True,
        "tool_id": "FM-1A1",
        "tool_alias": TOOL_ALIAS,
        "tool_name": TOOL_NAME,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "bundle_kind": "fma_export_bundle",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_report_root": str(report_root),
        "output_dir": str(out_dir),
        "selected_run_count": len(rows),
        "selected_run_ids": run_ids,
        "filters": {
            "run_id": run_id,
            "limit": limit,
            "profile": profile,
            "run_id_file": str(run_id_file.resolve()) if run_id_file is not None else None,
            "run_ids": list(selected_run_ids_filter or []),
        },
        "artifact_files": artifact_files,
    }


def harvest_reports(
    report_root: pathlib.Path,
    out_dir: pathlib.Path,
    run_id: str | None = None,
    limit: int | None = None,
    profile: str | None = None,
    run_id_file: pathlib.Path | None = None,
) -> dict[str, pathlib.Path]:
    report_root = report_root.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_run_ids_filter = _load_run_ids_from_file(run_id_file)
    selected_run_dirs = _discover_run_dirs(report_root, run_id, limit, selected_run_ids_filter)
    rows: list[dict[str, Any]] = []
    metric_catalog_accumulator: dict[str, dict[str, dict[str, Any]]] = {}
    maker_admission_shadow_rows: list[dict[str, Any]] = []
    maker_cannon_probe_rows: list[dict[str, Any]] = []
    maker_mid_window_probe_rows: list[dict[str, Any]] = []

    for run_dir in selected_run_dirs:
        row, loaded, _ = normalize_run(run_dir)
        if profile and row.get("profile_name") != profile:
            continue
        rows.append(row)
        for source_name, data in loaded.items():
            _update_metric_catalog(metric_catalog_accumulator, source_name, data)
        _, support_jsonl, _ = _load_support_artifacts(run_dir)
        for shadow_row in support_jsonl.get("maker_fight_admission_shadow.jsonl", []):
            normalized_shadow_row = dict(shadow_row)
            normalized_shadow_row.setdefault("run_id", row.get("run_id"))
            normalized_shadow_row.setdefault("admission_rubric_version", row.get("admission_rubric_version"))
            normalized_shadow_row.setdefault(
                "maker_timing_band_class",
                _maker_shadow_timing_band_class(normalized_shadow_row.get("sec_to_expiry")),
            )
            maker_admission_shadow_rows.append(normalized_shadow_row)
        for probe_row in support_jsonl.get("maker_cannon_late_window_probe.jsonl", []):
            normalized_probe_row = dict(probe_row)
            normalized_probe_row.setdefault("run_id", row.get("run_id"))
            maker_cannon_probe_rows.append(normalized_probe_row)
        for probe_row in support_jsonl.get("maker_mid_window_probe.jsonl", []):
            normalized_probe_row = dict(probe_row)
            normalized_probe_row.setdefault("run_id", row.get("run_id"))
            maker_mid_window_probe_rows.append(normalized_probe_row)

    run_index_path = out_dir / "run_index.jsonl"
    metric_catalog_path = out_dir / "metric_catalog.json"
    summary_csv_path = out_dir / "maker_taker_summary.csv"
    anomaly_summary_path = out_dir / "anomaly_summary.json"
    research_pack_path = out_dir / "maker_research_pack.md"
    maker_admission_shadow_rows_path = out_dir / "maker_fight_admission_shadow_rows.jsonl"
    maker_admission_shadow_summary_path = out_dir / "maker_fight_admission_shadow_summary.json"
    maker_admission_calibration_audit_path = out_dir / "maker_fight_admission_calibration_audit.json"
    maker_admission_target_side_summary_path = out_dir / "maker_admission_target_side_summary.json"
    maker_cannon_probe_rows_path = out_dir / "maker_cannon_late_window_probe_rows.jsonl"
    maker_cannon_probe_summary_path = out_dir / "maker_cannon_late_window_probe_summary.json"
    maker_cannon_probe_session_sweep_path = out_dir / "maker_cannon_probe_session_sweep.json"
    maker_mid_window_probe_rows_path = out_dir / "maker_mid_window_probe_rows.jsonl"
    maker_mid_window_probe_summary_path = out_dir / "maker_mid_window_probe_summary.json"
    maker_mid_window_probe_session_sweep_path = out_dir / "maker_mid_window_probe_session_sweep.json"
    manifest_path = out_dir / "fma_bundle_manifest.json"

    with run_index_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    metric_catalog = build_metric_catalog(metric_catalog_accumulator, len(rows))
    metric_catalog_path.write_text(json.dumps(metric_catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with summary_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _serialize_csv_value(row.get(field)) for field in CSV_FIELDS})

    anomaly_summary = build_anomaly_summary(rows)
    anomaly_summary_path.write_text(json.dumps(anomaly_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    research_pack_path.write_text(build_maker_research_pack(rows, anomaly_summary), encoding="utf-8")
    maker_admission_shadow_rows.sort(
        key=lambda item: (
            str(item.get("run_id") or ""),
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("admission_shadow_id") or ""),
        )
    )
    with maker_admission_shadow_rows_path.open("w", encoding="utf-8") as handle:
        for shadow_row in maker_admission_shadow_rows:
            handle.write(json.dumps(shadow_row, sort_keys=True) + "\n")
    maker_admission_shadow_summary, maker_admission_calibration_audit = _build_maker_admission_bundle_outputs(
        maker_admission_shadow_rows
    )
    maker_admission_shadow_summary_path.write_text(
        json.dumps(maker_admission_shadow_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_admission_calibration_audit_path.write_text(
        json.dumps(maker_admission_calibration_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_admission_target_side_summary = _build_maker_admission_target_side_summary(
        maker_admission_shadow_rows
    )
    maker_admission_target_side_summary_path.write_text(
        json.dumps(maker_admission_target_side_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_cannon_probe_rows.sort(
        key=lambda item: (
            str(item.get("run_id") or ""),
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("token_id") or ""),
        )
    )
    with maker_cannon_probe_rows_path.open("w", encoding="utf-8") as handle:
        for probe_row in maker_cannon_probe_rows:
            handle.write(json.dumps(probe_row, sort_keys=True) + "\n")
    maker_cannon_probe_summary = _build_maker_cannon_probe_bundle_outputs(
        maker_cannon_probe_rows
    )
    maker_cannon_probe_summary_path.write_text(
        json.dumps(maker_cannon_probe_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_cannon_probe_session_sweep = _build_maker_cannon_probe_session_sweep(rows, maker_cannon_probe_rows)
    maker_cannon_probe_session_sweep_path.write_text(
        json.dumps(maker_cannon_probe_session_sweep, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_mid_window_probe_rows.sort(
        key=lambda item: (
            str(item.get("run_id") or ""),
            str(item.get("target_side_ref") or ""),
            str(item.get("ts_decision_utc") or ""),
            str(item.get("token_id") or ""),
        )
    )
    with maker_mid_window_probe_rows_path.open("w", encoding="utf-8") as handle:
        for probe_row in maker_mid_window_probe_rows:
            handle.write(json.dumps(probe_row, sort_keys=True) + "\n")
    maker_mid_window_probe_summary = _build_maker_mid_window_probe_bundle_outputs(
        maker_mid_window_probe_rows
    )
    maker_mid_window_probe_summary_path.write_text(
        json.dumps(maker_mid_window_probe_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    maker_mid_window_probe_session_sweep = _build_maker_mid_window_probe_session_sweep(
        rows,
        maker_mid_window_probe_rows,
    )
    maker_mid_window_probe_session_sweep_path.write_text(
        json.dumps(maker_mid_window_probe_session_sweep, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    outputs = {
        "run_index_jsonl": run_index_path,
        "metric_catalog_json": metric_catalog_path,
        "maker_taker_summary_csv": summary_csv_path,
        "anomaly_summary_json": anomaly_summary_path,
        "maker_research_pack_md": research_pack_path,
        "maker_fight_admission_shadow_rows_jsonl": maker_admission_shadow_rows_path,
        "maker_fight_admission_shadow_summary_json": maker_admission_shadow_summary_path,
        "maker_fight_admission_calibration_audit_json": maker_admission_calibration_audit_path,
        "maker_admission_target_side_summary_json": maker_admission_target_side_summary_path,
        "maker_cannon_late_window_probe_rows_jsonl": maker_cannon_probe_rows_path,
        "maker_cannon_late_window_probe_summary_json": maker_cannon_probe_summary_path,
        "maker_cannon_probe_session_sweep_json": maker_cannon_probe_session_sweep_path,
        "maker_mid_window_probe_rows_jsonl": maker_mid_window_probe_rows_path,
        "maker_mid_window_probe_summary_json": maker_mid_window_probe_summary_path,
        "maker_mid_window_probe_session_sweep_json": maker_mid_window_probe_session_sweep_path,
    }
    manifest = _build_bundle_manifest(
        out_dir=out_dir,
        report_root=report_root,
        rows=rows,
        outputs=outputs,
        run_id=run_id,
        limit=limit,
        profile=profile,
        run_id_file=run_id_file,
        selected_run_ids_filter=selected_run_ids_filter,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["fma_bundle_manifest_json"] = manifest_path

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest BRO report metrics into engineer-first research artifacts.")
    parser.add_argument("--report-root", type=pathlib.Path, default=DEFAULT_REPORT_ROOT, help="Report root containing per-run directories.")
    parser.add_argument("--run-id", default=None, help="Harvest only a single run directory.")
    parser.add_argument(
        "--run-id-file",
        type=pathlib.Path,
        default=None,
        help="Harvest an exact run set from a newline-delimited run id file.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Harvest only the latest N run directories.")
    parser.add_argument("--profile", default=None, help="Filter harvested runs by profile name when available.")
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT_DIR, help="Output directory for harvested artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outputs = harvest_reports(
        report_root=args.report_root,
        out_dir=args.out_dir,
        run_id=args.run_id,
        limit=args.limit,
        profile=args.profile,
        run_id_file=args.run_id_file,
    )
    print(json.dumps({key: str(value) for key, value in outputs.items()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
