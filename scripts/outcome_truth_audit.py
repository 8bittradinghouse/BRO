#!/usr/bin/env python3
"""Deterministic observational audit for BRO outcome-truth / edge-realization records."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
from bisect import bisect_right
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import DEFAULT_MAX_LINES_PER_FILE, load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.session_phase import enforce_validation_phase

EDGE_EVALUATION = "edge_evaluation"
ORDER_SUBMIT = "order_submit"
FILL = "fill"
BOOK_TOP = "book_top"

CANONICAL_EVALUATION_HORIZON_MS = 5000
REDACTED_TOKEN_ID = "[REDACTED]"

OUTCOME_STATUS_COMPLETE = "complete"
OUTCOME_STATUS_UNKNOWN_MISSING_DATA = "unknown_missing_data"
OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE = "unknown_incomplete_lifecycle"
OUTCOME_STATUS_UNKNOWN_MISSING_DECISION_REFERENCE = "unknown_missing_decision_reference"
OUTCOME_STATUS_UNKNOWN_MISSING_EVAL_REFERENCE = "unknown_missing_eval_reference"
OUTCOME_STATUS_UNKNOWN_MISSING_LINKAGE = "unknown_missing_linkage"

OUTCOME_STATUSES = {
    OUTCOME_STATUS_COMPLETE,
    OUTCOME_STATUS_UNKNOWN_MISSING_DATA,
    OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE,
    OUTCOME_STATUS_UNKNOWN_MISSING_DECISION_REFERENCE,
    OUTCOME_STATUS_UNKNOWN_MISSING_EVAL_REFERENCE,
    OUTCOME_STATUS_UNKNOWN_MISSING_LINKAGE,
}

DECISION_QUALITY_CORRECT = "correct"
DECISION_QUALITY_INCORRECT = "incorrect"
DECISION_QUALITY_NEUTRAL = "neutral"
DECISION_QUALITY_UNKNOWN = "unknown"
DECISION_QUALITIES = {
    DECISION_QUALITY_CORRECT,
    DECISION_QUALITY_INCORRECT,
    DECISION_QUALITY_NEUTRAL,
    DECISION_QUALITY_UNKNOWN,
}

EXECUTION_QUALITY_FAVORABLE = "favorable"
EXECUTION_QUALITY_UNFAVORABLE = "unfavorable"
EXECUTION_QUALITY_NEUTRAL = "neutral"
EXECUTION_QUALITY_UNKNOWN = "unknown"
EXECUTION_QUALITIES = {
    EXECUTION_QUALITY_FAVORABLE,
    EXECUTION_QUALITY_UNFAVORABLE,
    EXECUTION_QUALITY_NEUTRAL,
    EXECUTION_QUALITY_UNKNOWN,
}

COMBINED_CLASS_CORRECT_GOOD = "correct_decision_good_execution"
COMBINED_CLASS_CORRECT_POOR = "correct_decision_poor_execution"
COMBINED_CLASS_INCORRECT_GOOD = "incorrect_decision_good_execution"
COMBINED_CLASS_INCORRECT_POOR = "incorrect_decision_poor_execution"
COMBINED_CLASS_NEUTRAL_NEUTRAL = "neutral_decision_neutral_execution"
COMBINED_CLASS_UNKNOWN = "unknown"
COMBINED_CLASSES = {
    COMBINED_CLASS_CORRECT_GOOD,
    COMBINED_CLASS_CORRECT_POOR,
    COMBINED_CLASS_INCORRECT_GOOD,
    COMBINED_CLASS_INCORRECT_POOR,
    COMBINED_CLASS_NEUTRAL_NEUTRAL,
    COMBINED_CLASS_UNKNOWN,
}

CLAIM_BOUNDARY_COMPLETE = "bounded_approximation"
CLAIM_BOUNDARY_UNKNOWN = "not_provable_missing_inputs"
CLAIM_BOUNDARY_INCOMPLETE = "not_modeled_incomplete_lifecycle"

RECORD_CLAIM_BOUNDARY_COMPLETE = "complete"
RECORD_CLAIM_BOUNDARY_PARTIAL_MISSING_DATA = "partial_missing_data"
RECORD_CLAIM_BOUNDARY_INCOMPLETE_LIFECYCLE = "incomplete_lifecycle"
RECORD_CLAIM_BOUNDARY_MISSING_EVAL_REFERENCE = "missing_eval_reference"
RECORD_CLAIM_BOUNDARY_MISSING_DECISION_REFERENCE = "missing_decision_reference"
RECORD_CLAIM_BOUNDARY_UNKNOWN = "unknown"
RECORD_CLAIM_BOUNDARY_CLASSES = {
    RECORD_CLAIM_BOUNDARY_COMPLETE,
    RECORD_CLAIM_BOUNDARY_PARTIAL_MISSING_DATA,
    RECORD_CLAIM_BOUNDARY_INCOMPLETE_LIFECYCLE,
    RECORD_CLAIM_BOUNDARY_MISSING_DECISION_REFERENCE,
    RECORD_CLAIM_BOUNDARY_MISSING_EVAL_REFERENCE,
    RECORD_CLAIM_BOUNDARY_UNKNOWN,
}

REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT = "direct_book_midpoint"
REFERENCE_BASIS_EDGE_MARKET_MIDPOINT = "edge_market_midpoint"
REFERENCE_BASIS_EDGE_MARKET_MIDPOINT_SERIES = "edge_market_midpoint_series"
REFERENCE_BASIS_OTHER_EXPLICIT = "other_explicit"
REFERENCE_BASIS_UNKNOWN = "unknown"
REFERENCE_BASES = {
    REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT,
    REFERENCE_BASIS_EDGE_MARKET_MIDPOINT,
    REFERENCE_BASIS_EDGE_MARKET_MIDPOINT_SERIES,
    REFERENCE_BASIS_OTHER_EXPLICIT,
    REFERENCE_BASIS_UNKNOWN,
}

REQUIRED_POLICY_FIELDS: Sequence[str] = (
    "policy_version",
    "evaluation_horizon_ms",
    "decision_reference",
    "eval_reference",
    "slippage_basis",
    "adverse_selection_basis",
    "edge_realized_basis",
    "decision_direction_source",
    "decision_reference_selection",
    "eval_reference_selection",
    "multi_fill_aggregation",
    "neutral_tolerance",
    "reference_recovery_priority",
    "eval_reference_timestamp_domain",
    "eval_reference_selection_rule",
    "eval_reference_tolerance_ms",
    "claim_boundary",
)

REQUIRED_RECORD_FIELDS: Sequence[str] = (
    "decision_id",
    "order_submit_id",
    "fill_trade_id",
    "ts_decision_utc",
    "ts_fill_utc",
    "ts_eval_utc",
    "mid_price_decision",
    "fill_price",
    "mid_price_eval",
    "edge_expected",
    "edge_expected_known",
    "slippage",
    "adverse_selection",
    "edge_realized",
    "decision_quality",
    "execution_quality",
    "combined_outcome_class",
    "evaluation_horizon_ms",
    "outcome_truth_status",
    "missing_fields",
    "claim_boundary_class",
    "record_claim_boundary_class",
    "decision_reference_status",
    "decision_reference_source",
    "decision_reference_basis",
    "decision_reference_lookup_key",
    "decision_reference_recoverable",
    "eval_reference_status",
    "eval_reference_source",
    "eval_reference_basis",
    "eval_reference_lookup_key",
    "eval_reference_recoverable",
    "decision_reference_link_status",
    "eval_reference_link_status",
    "reference_linkage_mode",
    "reference_linkage_complete",
    "maker_edge_linkage_attempted",
    "maker_edge_linkage_resolved",
    "maker_edge_linkage_ambiguous",
    "maker_edge_linkage_missing",
    "decision_anchor_ts_utc",
    "decision_anchor_source",
)

AUDIT_RULE_SET: Sequence[str] = (
    "policy_fields_present",
    "canonical_horizon_and_tolerances",
    "required_record_fields_present",
    "record_enum_values_valid",
    "classification_uses_only_directional_midpoint_bases",
    "complete_records_have_required_linkage_and_references",
    "unknown_paths_explicitly_counted",
    "claim_boundary_emitted_per_record_and_run",
)


def _sha256_json_payload(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _parse_ts(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
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


def _format_ts(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _safe_positive_float(value: Any) -> Optional[float]:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    if parsed <= 0.0:
        return None
    return parsed


def _non_empty_text(value: Any) -> str:
    return str(value or "").strip()


def _is_redacted_token(token_id: str) -> bool:
    return token_id == REDACTED_TOKEN_ID


def _submit_scope_hint(row: Dict[str, Any]) -> str:
    reason = str(row.get("reason") or "").strip().lower()
    execution_preference = str(row.get("execution_preference") or "").strip().lower()
    if reason.startswith("mm_quote:"):
        return "maker"
    if "sniper_taker" in reason:
        return "taker"
    if execution_preference == "taker_only":
        return "taker"
    if execution_preference == "maker_preferred":
        return "maker"
    return ""


def _decision_reference_basis(source: str, status: str) -> str:
    normalized_source = str(source or "").strip().lower()
    normalized_status = str(status or "").strip().lower()
    if not normalized_status or normalized_status == "missing":
        return REFERENCE_BASIS_UNKNOWN
    if normalized_status == "recovered_token_lookup":
        return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
    if normalized_status == "recovered_target_ref_linkage":
        return REFERENCE_BASIS_EDGE_MARKET_MIDPOINT
    if normalized_status == "recovered_timestamp_bound_artifact_lookup":
        if "book_top" in normalized_source:
            return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
        return REFERENCE_BASIS_EDGE_MARKET_MIDPOINT_SERIES
    if normalized_status == "recovered_explicit_decision_reference":
        if normalized_source in {"edge_decision_market_midpoint", "order_submit_top_midpoint"}:
            return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
        if "book" in normalized_source or "midpoint" in normalized_source:
            return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
        return REFERENCE_BASIS_OTHER_EXPLICIT
    return REFERENCE_BASIS_OTHER_EXPLICIT


def _eval_reference_basis(source: str, status: str) -> str:
    normalized_source = str(source or "").strip().lower()
    normalized_status = str(status or "").strip().lower()
    if not normalized_status or normalized_status == "missing":
        return REFERENCE_BASIS_UNKNOWN
    if normalized_status == "recovered_token_lookup":
        return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
    if normalized_status == "recovered_timestamp_bound_artifact_lookup":
        if "book_top" in normalized_source:
            return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
        return REFERENCE_BASIS_EDGE_MARKET_MIDPOINT_SERIES
    if normalized_status == "recovered_explicit_eval_reference":
        if "book" in normalized_source or "midpoint" in normalized_source:
            return REFERENCE_BASIS_DIRECT_BOOK_MIDPOINT
        return REFERENCE_BASIS_OTHER_EXPLICIT
    return REFERENCE_BASIS_OTHER_EXPLICIT


def _load_policy(*, path: pathlib.Path) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"outcome_truth_policy_load_failed:{exc}"]
    if not isinstance(payload, dict):
        return {}, ["outcome_truth_policy_invalid_root"]

    for key in REQUIRED_POLICY_FIELDS:
        if key not in payload:
            findings.append(f"outcome_truth_policy_missing_field:{key}")

    horizon_ms = payload.get("evaluation_horizon_ms")
    if not isinstance(horizon_ms, int):
        findings.append("outcome_truth_policy_horizon_not_int")
    elif int(horizon_ms) != int(CANONICAL_EVALUATION_HORIZON_MS):
        findings.append(
            f"outcome_truth_policy_horizon_must_equal:{CANONICAL_EVALUATION_HORIZON_MS}:got:{int(horizon_ms)}"
        )

    tolerance = payload.get("neutral_tolerance")
    if not isinstance(tolerance, dict):
        findings.append("outcome_truth_policy_neutral_tolerance_missing_or_invalid")
    else:
        decision_tol = _safe_float(tolerance.get("decision_quality"))
        execution_tol = _safe_float(tolerance.get("execution_quality"))
        if decision_tol is None:
            findings.append("outcome_truth_policy_decision_tolerance_invalid")
        elif abs(float(decision_tol)) > 0.0:
            findings.append("outcome_truth_policy_decision_tolerance_must_be_zero")
        if execution_tol is None:
            findings.append("outcome_truth_policy_execution_tolerance_invalid")
        elif abs(float(execution_tol)) > 0.0:
            findings.append("outcome_truth_policy_execution_tolerance_must_be_zero")

    recovery_priority = payload.get("reference_recovery_priority")
    expected_priority = [
        "explicit_decision_reference",
        "target_ref_linkage",
        "timestamp_bound_artifact_lookup",
        "token_lookup_non_redacted",
        "unknown",
    ]
    if not isinstance(recovery_priority, list) or not all(str(x or "").strip() for x in recovery_priority):
        findings.append("outcome_truth_policy_reference_recovery_priority_invalid")
    else:
        observed = [str(x).strip() for x in recovery_priority]
        if observed != expected_priority:
            findings.append("outcome_truth_policy_reference_recovery_priority_mismatch")

    eval_ts_domain = str(payload.get("eval_reference_timestamp_domain") or "").strip().lower()
    if eval_ts_domain not in {"event_time"}:
        findings.append("outcome_truth_policy_eval_reference_timestamp_domain_invalid")
    eval_selection_rule = str(payload.get("eval_reference_selection_rule") or "").strip().lower()
    if eval_selection_rule not in {"latest_at_or_before_target"}:
        findings.append("outcome_truth_policy_eval_reference_selection_rule_invalid")
    eval_tolerance_ms = payload.get("eval_reference_tolerance_ms")
    if not isinstance(eval_tolerance_ms, int):
        findings.append("outcome_truth_policy_eval_reference_tolerance_ms_not_int")
    elif int(eval_tolerance_ms) != 0:
        findings.append("outcome_truth_policy_eval_reference_tolerance_ms_must_be_zero")

    claim_boundary = payload.get("claim_boundary")
    if not isinstance(claim_boundary, dict):
        findings.append("outcome_truth_policy_claim_boundary_missing_or_invalid")
    else:
        for key in ("proves", "does_not_prove", "non_goals"):
            value = claim_boundary.get(key)
            if not isinstance(value, list) or not value:
                findings.append(f"outcome_truth_policy_claim_boundary_{key}_invalid")
            elif any(not str(item or "").strip() for item in value):
                findings.append(f"outcome_truth_policy_claim_boundary_{key}_empty_item")

        non_goals = {str(item or "").strip() for item in claim_boundary.get("non_goals", [])}
        for required in (
            "no_strategy_optimality_proof",
            "no_long_term_profitability_proof",
            "no_live_venue_equivalence_proof",
            "no_runtime_feedback",
        ):
            if required not in non_goals:
                findings.append(f"outcome_truth_policy_missing_non_goal:{required}")

    normalized = {
        "policy_version": str(payload.get("policy_version") or ""),
        "evaluation_horizon_ms": int(payload.get("evaluation_horizon_ms") or 0),
        "decision_reference": str(payload.get("decision_reference") or ""),
        "eval_reference": str(payload.get("eval_reference") or ""),
        "slippage_basis": str(payload.get("slippage_basis") or ""),
        "adverse_selection_basis": str(payload.get("adverse_selection_basis") or ""),
        "edge_realized_basis": str(payload.get("edge_realized_basis") or ""),
        "decision_direction_source": str(payload.get("decision_direction_source") or ""),
        "decision_reference_selection": str(payload.get("decision_reference_selection") or ""),
        "eval_reference_selection": str(payload.get("eval_reference_selection") or ""),
        "reference_recovery_priority": (
            [str(x).strip() for x in payload.get("reference_recovery_priority", [])]
            if isinstance(payload.get("reference_recovery_priority"), list)
            else []
        ),
        "eval_reference_timestamp_domain": str(payload.get("eval_reference_timestamp_domain") or ""),
        "eval_reference_selection_rule": str(payload.get("eval_reference_selection_rule") or ""),
        "eval_reference_tolerance_ms": int(payload.get("eval_reference_tolerance_ms") or 0),
        "multi_fill_aggregation": payload.get("multi_fill_aggregation") if isinstance(payload.get("multi_fill_aggregation"), dict) else {},
        "neutral_tolerance": {
            "decision_quality": float(_safe_float((payload.get("neutral_tolerance") or {}).get("decision_quality")) or 0.0),
            "execution_quality": float(_safe_float((payload.get("neutral_tolerance") or {}).get("execution_quality")) or 0.0),
        },
        "claim_boundary": payload.get("claim_boundary") if isinstance(payload.get("claim_boundary"), dict) else {},
    }
    return normalized, findings


def _load_event_rows(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    run_contract_path: Optional[pathlib.Path],
    max_lines_per_file: int,
) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any], List[pathlib.Path]]:
    if run_contract_path is None:
        raise ValueError("outcome_truth_run_contract_required")
    contract = resolve_run_contract(
        log_dir=log_dir,
        run_id=run_id,
        run_contract_path_override=run_contract_path,
        allow_open=False,
    )
    if contract is None:
        raise ValueError("outcome_truth_run_contract_missing")
    slice_path = run_contract_slice_path(contract, stream="events")
    if slice_path is None:
        raise ValueError("outcome_truth_events_slice_missing")
    event_paths = [slice_path]
    rows = load_jsonl(event_paths, max_lines_per_file=max(0, int(max_lines_per_file)))
    bounded = apply_contract_bounds(rows, contract)
    return bounded, str(contract.get("_path") or ""), contract, event_paths


def _event_ts(row: Dict[str, Any]) -> Optional[dt.datetime]:
    return (
        _parse_ts(row.get("ts_event_utc"))
        or _parse_ts(row.get("ts_decision_utc"))
        or _parse_ts(row.get("ts_utc"))
    )


def _compute_book_mid(row: Dict[str, Any]) -> Optional[float]:
    bid = _safe_positive_float(row.get("best_bid_price"))
    ask = _safe_positive_float(row.get("best_ask_price"))
    if bid is None or ask is None:
        return None
    if ask < bid:
        return None
    return (float(bid) + float(ask)) / 2.0


def _direction_sign(side: str) -> Optional[float]:
    normalized = str(side or "").strip().upper()
    if normalized == "BUY":
        return 1.0
    if normalized == "SELL":
        return -1.0
    return None


def _classify_signed_delta(*, value: Optional[float], tolerance: float, positive: str, negative: str, neutral: str) -> str:
    if value is None:
        return DECISION_QUALITY_UNKNOWN if positive == DECISION_QUALITY_CORRECT else EXECUTION_QUALITY_UNKNOWN
    if value > float(tolerance):
        return positive
    if value < -float(tolerance):
        return negative
    return neutral


def _combine_outcome_class(decision_quality: str, execution_quality: str) -> str:
    if decision_quality == DECISION_QUALITY_CORRECT and execution_quality == EXECUTION_QUALITY_FAVORABLE:
        return COMBINED_CLASS_CORRECT_GOOD
    if decision_quality == DECISION_QUALITY_CORRECT and execution_quality == EXECUTION_QUALITY_UNFAVORABLE:
        return COMBINED_CLASS_CORRECT_POOR
    if decision_quality == DECISION_QUALITY_INCORRECT and execution_quality == EXECUTION_QUALITY_FAVORABLE:
        return COMBINED_CLASS_INCORRECT_GOOD
    if decision_quality == DECISION_QUALITY_INCORRECT and execution_quality == EXECUTION_QUALITY_UNFAVORABLE:
        return COMBINED_CLASS_INCORRECT_POOR
    if decision_quality == DECISION_QUALITY_NEUTRAL and execution_quality == EXECUTION_QUALITY_NEUTRAL:
        return COMBINED_CLASS_NEUTRAL_NEUTRAL
    return COMBINED_CLASS_UNKNOWN


def _numeric_summary(values: Iterable[Optional[float]]) -> Dict[str, Optional[float]]:
    series = [float(v) for v in values if isinstance(v, (int, float))]
    if not series:
        return {"count": 0.0, "min": None, "max": None, "mean": None, "p50": None}
    ordered = sorted(series)
    count = float(len(ordered))
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        p50 = (ordered[mid - 1] + ordered[mid]) / 2.0
    else:
        p50 = ordered[mid]
    return {
        "count": count,
        "min": float(ordered[0]),
        "max": float(ordered[-1]),
        "mean": float(sum(ordered) / len(ordered)),
        "p50": float(p50),
    }


def _record_sort_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("order_submit_id") or ""),
        str(row.get("decision_id") or ""),
        str(row.get("fill_trade_id") or ""),
        str(row.get("ts_decision_utc") or ""),
    )


def _normalize_record(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: row.get(key) for key in sorted(row.keys())}


def _record_claim_boundary_from_status(status: str) -> str:
    normalized = str(status or "").strip()
    if normalized == OUTCOME_STATUS_COMPLETE:
        return RECORD_CLAIM_BOUNDARY_COMPLETE
    if normalized == OUTCOME_STATUS_UNKNOWN_MISSING_DECISION_REFERENCE:
        return RECORD_CLAIM_BOUNDARY_MISSING_DECISION_REFERENCE
    if normalized == OUTCOME_STATUS_UNKNOWN_MISSING_DATA:
        return RECORD_CLAIM_BOUNDARY_PARTIAL_MISSING_DATA
    if normalized == OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE:
        return RECORD_CLAIM_BOUNDARY_INCOMPLETE_LIFECYCLE
    if normalized == OUTCOME_STATUS_UNKNOWN_MISSING_EVAL_REFERENCE:
        return RECORD_CLAIM_BOUNDARY_MISSING_EVAL_REFERENCE
    return RECORD_CLAIM_BOUNDARY_UNKNOWN


def _validate_records(*, records: List[Dict[str, Any]], policy: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    horizon = int(policy.get("evaluation_horizon_ms") or 0)
    decision_tol = float((policy.get("neutral_tolerance") or {}).get("decision_quality") or 0.0)
    execution_tol = float((policy.get("neutral_tolerance") or {}).get("execution_quality") or 0.0)

    horizon_values = set()
    missing_horizon_detected = False
    for idx, row in enumerate(records):
        prefix = f"outcome_record[{idx}]"
        for field in REQUIRED_RECORD_FIELDS:
            if field not in row:
                findings.append(f"{prefix}:missing_required_field:{field}")

        row_horizon = row.get("evaluation_horizon_ms")
        if row_horizon is None:
            missing_horizon_detected = True
            findings.append(f"{prefix}:missing_evaluation_horizon")
        elif not isinstance(row_horizon, int):
            findings.append(f"{prefix}:evaluation_horizon_ms_not_int")
        else:
            horizon_values.add(int(row_horizon))
            if int(row_horizon) != horizon:
                findings.append(f"{prefix}:evaluation_horizon_ms_mismatch:{row_horizon}:{horizon}")

        status = str(row.get("outcome_truth_status") or "")
        if status not in OUTCOME_STATUSES:
            findings.append(f"{prefix}:outcome_truth_status_invalid:{status or '<empty>'}")

        decision_quality = str(row.get("decision_quality") or "")
        execution_quality = str(row.get("execution_quality") or "")
        combined = str(row.get("combined_outcome_class") or "")
        if decision_quality not in DECISION_QUALITIES:
            findings.append(f"{prefix}:decision_quality_invalid:{decision_quality or '<empty>'}")
        if execution_quality not in EXECUTION_QUALITIES:
            findings.append(f"{prefix}:execution_quality_invalid:{execution_quality or '<empty>'}")
        if combined not in COMBINED_CLASSES:
            findings.append(f"{prefix}:combined_outcome_class_invalid:{combined or '<empty>'}")

        missing_fields = row.get("missing_fields")
        if not isinstance(missing_fields, list):
            findings.append(f"{prefix}:missing_fields_not_list")
            missing_fields = []

        if status != OUTCOME_STATUS_COMPLETE and len(missing_fields) == 0:
            findings.append(f"{prefix}:unknown_status_requires_missing_fields")

        edge_expected_known = row.get("edge_expected_known")
        if not isinstance(edge_expected_known, bool):
            findings.append(f"{prefix}:edge_expected_known_not_bool")
        elif edge_expected_known and row.get("edge_expected") is None:
            findings.append(f"{prefix}:edge_expected_known_true_but_edge_expected_null")

        claim_boundary_class = str(row.get("claim_boundary_class") or "")
        if not claim_boundary_class:
            findings.append(f"{prefix}:claim_boundary_class_missing")

        record_claim_boundary_class = str(row.get("record_claim_boundary_class") or "")
        if record_claim_boundary_class not in RECORD_CLAIM_BOUNDARY_CLASSES:
            findings.append(
                f"{prefix}:record_claim_boundary_class_invalid:{record_claim_boundary_class or '<empty>'}"
            )
        else:
            expected_record_claim_boundary_class = _record_claim_boundary_from_status(status)
            if record_claim_boundary_class != expected_record_claim_boundary_class:
                findings.append(
                    f"{prefix}:record_claim_boundary_class_mismatch:{record_claim_boundary_class}:{expected_record_claim_boundary_class}"
                )

        decision_reference_status = str(row.get("decision_reference_status") or "").strip()
        decision_reference_source = str(row.get("decision_reference_source") or "").strip()
        decision_reference_basis = str(row.get("decision_reference_basis") or "").strip()
        eval_reference_status = str(row.get("eval_reference_status") or "").strip()
        eval_reference_source = str(row.get("eval_reference_source") or "").strip()
        eval_reference_basis = str(row.get("eval_reference_basis") or "").strip()
        if not decision_reference_status:
            findings.append(f"{prefix}:decision_reference_status_missing")
        if not decision_reference_source:
            findings.append(f"{prefix}:decision_reference_source_missing")
        if decision_reference_basis not in REFERENCE_BASES:
            findings.append(f"{prefix}:decision_reference_basis_invalid:{decision_reference_basis or '<empty>'}")
        if not eval_reference_status:
            findings.append(f"{prefix}:eval_reference_status_missing")
        if not eval_reference_source:
            findings.append(f"{prefix}:eval_reference_source_missing")
        if eval_reference_basis not in REFERENCE_BASES:
            findings.append(f"{prefix}:eval_reference_basis_invalid:{eval_reference_basis or '<empty>'}")

        decision_reference_recoverable = row.get("decision_reference_recoverable")
        eval_reference_recoverable = row.get("eval_reference_recoverable")
        reference_linkage_complete = row.get("reference_linkage_complete")
        if not isinstance(decision_reference_recoverable, bool):
            findings.append(f"{prefix}:decision_reference_recoverable_not_bool")
        if not isinstance(eval_reference_recoverable, bool):
            findings.append(f"{prefix}:eval_reference_recoverable_not_bool")
        if not isinstance(reference_linkage_complete, bool):
            findings.append(f"{prefix}:reference_linkage_complete_not_bool")
        for maker_link_field in (
            "maker_edge_linkage_attempted",
            "maker_edge_linkage_resolved",
            "maker_edge_linkage_ambiguous",
            "maker_edge_linkage_missing",
        ):
            if not isinstance(row.get(maker_link_field), bool):
                findings.append(f"{prefix}:{maker_link_field}_not_bool")
        decision_anchor_source = str(row.get("decision_anchor_source") or "").strip()
        if decision_anchor_source not in {"decision_reference_ts_utc", "ts_decision_utc", "edge_ts_decision_utc", "missing"}:
            findings.append(f"{prefix}:decision_anchor_source_invalid:{decision_anchor_source or '<empty>'}")
        if decision_anchor_source != "missing" and _parse_ts(row.get("decision_anchor_ts_utc")) is None:
            findings.append(f"{prefix}:decision_anchor_ts_invalid_for_source")

        side = str(row.get("order_side") or "").upper()
        sign = _direction_sign(side)

        decision_mid = _safe_float(row.get("mid_price_decision"))
        eval_mid = _safe_float(row.get("mid_price_eval"))
        fill_price = _safe_float(row.get("fill_price"))

        decision_delta = None
        if sign is not None and decision_mid is not None and eval_mid is not None:
            decision_delta = float(sign) * (float(eval_mid) - float(decision_mid))
        expected_decision_quality = _classify_signed_delta(
            value=decision_delta,
            tolerance=decision_tol,
            positive=DECISION_QUALITY_CORRECT,
            negative=DECISION_QUALITY_INCORRECT,
            neutral=DECISION_QUALITY_NEUTRAL,
        )
        if decision_quality != expected_decision_quality:
            findings.append(f"{prefix}:decision_quality_basis_mismatch")

        execution_delta = None
        if sign is not None and decision_mid is not None and fill_price is not None:
            execution_delta = float(sign) * (float(decision_mid) - float(fill_price))
        expected_execution_quality = _classify_signed_delta(
            value=execution_delta,
            tolerance=execution_tol,
            positive=EXECUTION_QUALITY_FAVORABLE,
            negative=EXECUTION_QUALITY_UNFAVORABLE,
            neutral=EXECUTION_QUALITY_NEUTRAL,
        )
        if execution_quality != expected_execution_quality:
            findings.append(f"{prefix}:execution_quality_basis_mismatch")

        expected_combined = _combine_outcome_class(decision_quality, execution_quality)
        if combined != expected_combined:
            findings.append(f"{prefix}:combined_outcome_class_mismatch")

        if status == OUTCOME_STATUS_UNKNOWN_MISSING_DECISION_REFERENCE and decision_mid is not None:
            findings.append(f"{prefix}:missing_decision_reference_status_without_missing_mid")
        if status == OUTCOME_STATUS_UNKNOWN_MISSING_EVAL_REFERENCE and eval_mid is not None:
            findings.append(f"{prefix}:missing_eval_reference_status_without_missing_mid")

        if status == OUTCOME_STATUS_COMPLETE:
            for critical_field in (
                "decision_id",
                "order_submit_id",
                "fill_trade_id",
                "ts_decision_utc",
                "ts_fill_utc",
                "ts_eval_utc",
                "mid_price_decision",
                "fill_price",
                "mid_price_eval",
            ):
                value = row.get(critical_field)
                if value is None or (isinstance(value, str) and not str(value).strip()):
                    findings.append(f"{prefix}:complete_record_missing:{critical_field}")
            if claim_boundary_class != CLAIM_BOUNDARY_COMPLETE:
                findings.append(f"{prefix}:complete_record_claim_boundary_invalid:{claim_boundary_class}")

            ts_decision = _parse_ts(row.get("ts_decision_utc"))
            ts_eval = _parse_ts(row.get("ts_eval_utc"))
            if ts_decision is None or ts_eval is None:
                findings.append(f"{prefix}:complete_record_timestamp_invalid")
            else:
                expected_eval = ts_decision + dt.timedelta(milliseconds=horizon)
                if ts_eval != expected_eval:
                    findings.append(f"{prefix}:ts_eval_not_fixed_horizon")
            if decision_mid is None:
                findings.append(f"{prefix}:complete_record_missing_decision_reference")
            if eval_mid is None:
                findings.append(f"{prefix}:complete_record_missing_eval_reference")

    if len(horizon_values) > 1:
        findings.append("mixed_evaluation_horizon_detected")
    if missing_horizon_detected:
        findings.append("missing_evaluation_horizon")

    return sorted(set(findings))


def run_audit(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    run_contract_path: Optional[pathlib.Path],
    policy_path: pathlib.Path,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    records_out_path: Optional[pathlib.Path] = None,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="outcome_truth_audit", session_phase=session_phase)

    selected_run_id = str(run_id or "").strip()
    findings: List[str] = []
    warnings: List[str] = []

    if not selected_run_id:
        findings.append("outcome_truth_run_id_required")
    if run_contract_path is None:
        findings.append("outcome_truth_run_contract_required")
    if int(max_lines_per_file) > 0:
        findings.append("outcome_truth_max_lines_must_be_zero_for_authoritative_scan")

    policy, policy_findings = _load_policy(path=policy_path.resolve())
    findings.extend(policy_findings)

    rows: List[Dict[str, Any]] = []
    contract: Dict[str, Any] = {}
    resolved_contract_path = ""
    event_paths: List[pathlib.Path] = []
    if not findings:
        try:
            rows, resolved_contract_path, contract, event_paths = _load_event_rows(
                log_dir=log_dir.resolve(),
                run_id=selected_run_id,
                run_contract_path=run_contract_path.resolve() if run_contract_path else None,
                max_lines_per_file=max_lines_per_file,
            )
        except Exception as exc:
            findings.append(str(exc))

    events = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("run_id") or "").strip() == selected_run_id
        and str(row.get("event_type") or "").strip() in {EDGE_EVALUATION, ORDER_SUBMIT, FILL, BOOK_TOP}
    ]

    order_submit_rows = [row for row in events if str(row.get("event_type") or "").strip() == ORDER_SUBMIT]
    fill_rows = [row for row in events if str(row.get("event_type") or "").strip() == FILL]
    edge_all_rows = [row for row in events if str(row.get("event_type") or "").strip() == EDGE_EVALUATION]
    edge_action_rows = [
        row
        for row in edge_all_rows
        if str(row.get("action_taken") or "").strip().lower() in {"maker", "taker"}
    ]
    book_rows = [row for row in events if str(row.get("event_type") or "").strip() == BOOK_TOP]

    if not order_submit_rows:
        warnings.append("outcome_truth_order_submit_rows_missing")

    fills_by_order_id: Dict[str, List[Dict[str, Any]]] = {}
    for row in fill_rows:
        order_id = _non_empty_text(row.get("order_id"))
        if not order_id:
            findings.append("fill_row_missing_order_id")
            continue
        fills_by_order_id.setdefault(order_id, []).append(row)

    edge_by_order_id: Dict[str, List[Dict[str, Any]]] = {}
    for row in edge_action_rows:
        order_ids: List[str] = []
        order_id = _non_empty_text(row.get("order_id"))
        if order_id:
            order_ids.append(order_id)
        submitted_order_ids = row.get("submitted_order_ids")
        if isinstance(submitted_order_ids, list):
            for submitted_order_id in submitted_order_ids:
                normalized_submitted_order_id = _non_empty_text(submitted_order_id)
                if normalized_submitted_order_id:
                    order_ids.append(normalized_submitted_order_id)
        for normalized_order_id in sorted(set(order_ids)):
            edge_by_order_id.setdefault(normalized_order_id, []).append(row)

    for order_id in list(edge_by_order_id.keys()):
        edge_by_order_id[order_id] = sorted(
            edge_by_order_id[order_id],
            key=lambda row: (
                str(row.get("ts_decision_utc") or ""),
                str(row.get("timestamp_utc") or ""),
                str(row.get("evaluation_scope") or ""),
            ),
        )

    edge_by_target_ref_scope: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in edge_action_rows:
        target_ref = _non_empty_text(row.get("target_ref"))
        scope = _non_empty_text(row.get("evaluation_scope")).lower()
        if not target_ref or scope not in {"maker", "taker"}:
            continue
        edge_by_target_ref_scope.setdefault((target_ref, scope), []).append(row)
    for key in list(edge_by_target_ref_scope.keys()):
        edge_by_target_ref_scope[key] = sorted(
            edge_by_target_ref_scope[key],
            key=lambda row: (
                str(row.get("ts_decision_utc") or ""),
                str(row.get("timestamp_utc") or ""),
                str(row.get("order_id") or ""),
            ),
        )

    eval_timestamp_domain = str(policy.get("eval_reference_timestamp_domain") or "event_time").strip().lower()
    eval_selection_rule = str(policy.get("eval_reference_selection_rule") or "latest_at_or_before_target").strip().lower()
    eval_tolerance_ms = int(policy.get("eval_reference_tolerance_ms") or 0)

    def reference_ts(row: Dict[str, Any]) -> Optional[dt.datetime]:
        if eval_timestamp_domain == "receive_time":
            return _parse_ts(row.get("ts_receive_utc")) or _event_ts(row)
        return _event_ts(row)

    book_index: Dict[str, List[Tuple[dt.datetime, float]]] = {}
    for row in book_rows:
        token_id = _non_empty_text(row.get("token_id"))
        if not token_id:
            continue
        row_ts = reference_ts(row)
        if row_ts is None:
            continue
        midpoint = _compute_book_mid(row)
        if midpoint is None:
            continue
        book_index.setdefault(token_id, []).append((row_ts, float(midpoint)))

    for token_id in list(book_index.keys()):
        book_index[token_id] = sorted(book_index[token_id], key=lambda item: (item[0], item[1]))

    edge_series_by_target_ref: Dict[str, List[Tuple[dt.datetime, float]]] = {}
    for row in edge_all_rows:
        target_ref = _non_empty_text(row.get("target_ref"))
        if not target_ref:
            continue
        row_ts = reference_ts(row)
        if row_ts is None:
            continue
        market_probability = _safe_float(row.get("market_probability"))
        if market_probability is None:
            continue
        edge_series_by_target_ref.setdefault(target_ref, []).append((row_ts, float(market_probability)))
    for target_ref in list(edge_series_by_target_ref.keys()):
        edge_series_by_target_ref[target_ref] = sorted(
            edge_series_by_target_ref[target_ref], key=lambda item: (item[0], item[1])
        )

    def lookup_mid_by_token(*, token_id: str, target_ts: Optional[dt.datetime]) -> Tuple[Optional[float], Optional[str]]:
        if target_ts is None:
            return None, "target_ts_missing"
        if eval_selection_rule != "latest_at_or_before_target":
            return None, f"selection_rule_unsupported:{eval_selection_rule}"
        token = str(token_id or "").strip()
        if not token:
            return None, "token_missing"
        if _is_redacted_token(token):
            return None, "token_redacted"
        series = book_index.get(token)
        if not series:
            return None, "book_series_missing"
        ts_values = [item[0] for item in series]
        pos = bisect_right(ts_values, target_ts) - 1
        if pos < 0:
            return None, "book_reference_before_target_missing"
        return float(series[pos][1]), None

    def lookup_mid_by_target_ref(
        *,
        target_ref: str,
        target_ts: Optional[dt.datetime],
        tolerance_ms: int,
    ) -> Tuple[Optional[float], Optional[str]]:
        if target_ts is None:
            return None, "target_ts_missing"
        if eval_selection_rule != "latest_at_or_before_target":
            return None, f"selection_rule_unsupported:{eval_selection_rule}"
        normalized_ref = str(target_ref or "").strip()
        if not normalized_ref:
            return None, "target_ref_missing"
        series = edge_series_by_target_ref.get(normalized_ref)
        if not series:
            return None, "edge_target_ref_series_missing"
        upper_bound = target_ts + dt.timedelta(milliseconds=max(0, int(tolerance_ms)))
        ts_values = [item[0] for item in series]
        pos = bisect_right(ts_values, upper_bound) - 1
        if pos < 0:
            return None, "edge_target_ref_reference_before_target_missing"
        return float(series[pos][1]), None

    def _resolve_edge_candidate_by_target_ref(
        *,
        target_ref: str,
        desired_scope: str,
        anchor_ts: Optional[dt.datetime],
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        if not target_ref:
            return None, "missing_target_ref"
        if anchor_ts is None:
            return None, "missing_anchor_ts"
        if desired_scope not in {"maker", "taker"}:
            return None, "scope_not_inferred"
        candidates = list(edge_by_target_ref_scope.get((target_ref, desired_scope), []))
        if not candidates:
            return None, "no_scope_candidates"
        eligible: List[Dict[str, Any]] = []
        for candidate in candidates:
            candidate_ts = _parse_ts(candidate.get("ts_decision_utc")) or _event_ts(candidate)
            if candidate_ts is None:
                continue
            if candidate_ts <= anchor_ts:
                eligible.append(candidate)
        if not eligible:
            return None, "no_candidates_at_or_before_anchor"
        anchor_candidates: List[Tuple[dt.datetime, Dict[str, Any]]] = []
        max_ts: Optional[dt.datetime] = None
        for candidate in eligible:
            candidate_ts = _parse_ts(candidate.get("ts_decision_utc")) or _event_ts(candidate)
            if candidate_ts is None:
                continue
            if max_ts is None or candidate_ts > max_ts:
                max_ts = candidate_ts
                anchor_candidates = [(candidate_ts, candidate)]
            elif candidate_ts == max_ts:
                anchor_candidates.append((candidate_ts, candidate))
        if len(anchor_candidates) != 1:
            return None, "ambiguous_scope_candidates"
        return anchor_candidates[0][1], "resolved_unique_scope_candidate"

    def target_ref_candidate_exists(*, target_ref: str, target_ts: Optional[dt.datetime], tolerance_ms: int) -> bool:
        normalized_ref = str(target_ref or "").strip()
        if not normalized_ref or target_ts is None:
            return False
        series = edge_series_by_target_ref.get(normalized_ref)
        if not series:
            return False
        upper_bound = target_ts + dt.timedelta(milliseconds=max(0, int(tolerance_ms)))
        ts_values = [item[0] for item in series]
        return (bisect_right(ts_values, upper_bound) - 1) >= 0

    def token_candidate_exists(*, token_id: str, target_ts: Optional[dt.datetime]) -> bool:
        token = str(token_id or "").strip()
        if not token or target_ts is None or _is_redacted_token(token):
            return False
        series = book_index.get(token)
        if not series:
            return False
        ts_values = [item[0] for item in series]
        return (bisect_right(ts_values, target_ts) - 1) >= 0

    decision_tol = float((policy.get("neutral_tolerance") or {}).get("decision_quality") or 0.0)
    execution_tol = float((policy.get("neutral_tolerance") or {}).get("execution_quality") or 0.0)
    horizon_ms = int(policy.get("evaluation_horizon_ms") or 0)

    records: List[Dict[str, Any]] = []
    missing_linkage_count = 0
    maker_edge_linkage_attempted_count = 0
    maker_edge_linkage_resolved_count = 0
    maker_edge_linkage_ambiguous_count = 0
    maker_edge_linkage_missing_count = 0

    sorted_order_submits = sorted(
        order_submit_rows,
        key=lambda row: (
            str(row.get("ts_utc") or ""),
            str(row.get("order_id") or ""),
        ),
    )

    for submit in sorted_order_submits:
        order_id = _non_empty_text(submit.get("order_id"))
        if not order_id:
            findings.append("order_submit_missing_order_id")
            continue

        side = _non_empty_text(submit.get("side")).upper()
        direction_sign = _direction_sign(side)
        token_id = _non_empty_text(submit.get("token_id"))

        submit_scope_hint = _submit_scope_hint(submit)
        decision_ts = _parse_ts(submit.get("decision_reference_ts_utc"))
        decision_anchor_source = "decision_reference_ts_utc" if decision_ts is not None else "missing"
        if decision_ts is None:
            decision_ts = _parse_ts(submit.get("ts_decision_utc"))
            if decision_ts is not None:
                decision_anchor_source = "ts_decision_utc"

        edge_candidate = (edge_by_order_id.get(order_id) or [None])[0]
        target_ref = _non_empty_text(submit.get("target_ref"))
        if not target_ref and isinstance(edge_candidate, dict):
            target_ref = _non_empty_text(edge_candidate.get("target_ref"))

        maker_edge_linkage_attempted = False
        maker_edge_linkage_resolved = False
        maker_edge_linkage_ambiguous = False
        maker_edge_linkage_missing = False
        if edge_candidate is None and submit_scope_hint == "maker":
            maker_edge_linkage_attempted = True
            maker_edge_linkage_attempted_count += 1
            fallback_candidate, fallback_reason = _resolve_edge_candidate_by_target_ref(
                target_ref=target_ref,
                desired_scope="maker",
                anchor_ts=decision_ts,
            )
            if isinstance(fallback_candidate, dict):
                edge_candidate = fallback_candidate
                maker_edge_linkage_resolved = True
                maker_edge_linkage_resolved_count += 1
            elif fallback_reason == "ambiguous_scope_candidates":
                maker_edge_linkage_ambiguous = True
                maker_edge_linkage_ambiguous_count += 1
            else:
                maker_edge_linkage_missing = True
                maker_edge_linkage_missing_count += 1

        if edge_candidate is None:
            missing_linkage_count += 1
        elif decision_ts is None:
            decision_ts = _parse_ts(edge_candidate.get("ts_decision_utc"))
            if decision_ts is not None:
                decision_anchor_source = "edge_ts_decision_utc"

        if not target_ref and isinstance(edge_candidate, dict):
            target_ref = _non_empty_text(edge_candidate.get("target_ref"))

        edge_expected = _safe_float(edge_candidate.get("edge_value")) if isinstance(edge_candidate, dict) else None
        edge_expected_known = edge_expected is not None

        ts_eval = decision_ts + dt.timedelta(milliseconds=horizon_ms) if decision_ts is not None else None

        reference_linkage_mode = "explicit_decision_reference>target_ref_linkage>timestamp_bound_artifact_lookup>token_lookup_non_redacted>unknown"
        decision_reference_link_status = "missing"
        if maker_edge_linkage_resolved:
            decision_reference_link_status = "linked_via_target_ref_decision_ts_unique"
        elif maker_edge_linkage_ambiguous:
            decision_reference_link_status = "maker_target_ref_decision_ts_ambiguous"
        elif maker_edge_linkage_missing:
            decision_reference_link_status = "maker_target_ref_decision_ts_missing"
        elif isinstance(edge_candidate, dict) and target_ref:
            decision_reference_link_status = "linked_via_order_id_to_edge_target_ref"
        elif isinstance(edge_candidate, dict):
            decision_reference_link_status = "linked_via_order_id_without_target_ref"
        elif target_ref:
            decision_reference_link_status = "linked_via_submit_target_ref"
        eval_reference_link_status = "linked_via_target_ref" if target_ref else "missing_target_ref"
        reference_linkage_complete = bool(target_ref and decision_ts is not None)

        # Strict reference recovery priority (no skipping):
        # 1) explicit decision reference fields
        # 2) target_ref linkage
        # 3) timestamp-bound artifact lookup
        # 4) token lookup (non-redacted only)
        # 5) unknown
        decision_reference_status = "missing"
        decision_reference_source = "none"
        decision_reference_lookup_key: Optional[str] = None
        decision_reference_reason: Optional[str] = None
        mid_decision: Optional[float] = None
        explicit_decision_mid = _safe_float(submit.get("decision_reference_midpoint"))
        if explicit_decision_mid is not None:
            mid_decision = float(explicit_decision_mid)
            decision_reference_status = "recovered_explicit_decision_reference"
            decision_reference_source = str(submit.get("decision_reference_source") or "order_submit.decision_reference_midpoint")
            decision_reference_lookup_key = str(submit.get("decision_reference_lookup_key") or f"order_submit:{order_id}")
        else:
            decision_reference_reason = "explicit_decision_reference_missing"

        if mid_decision is None:
            if target_ref and isinstance(edge_candidate, dict):
                edge_decision_mid = _safe_float(edge_candidate.get("market_probability"))
                if edge_decision_mid is not None:
                    mid_decision = float(edge_decision_mid)
                    decision_reference_status = "recovered_target_ref_linkage"
                    decision_reference_source = "edge_evaluation.market_probability"
                    decision_reference_lookup_key = f"target_ref:{target_ref}"
                else:
                    decision_reference_reason = "target_ref_linkage_missing_market_probability"
            elif target_ref:
                decision_reference_reason = "target_ref_linkage_missing_edge_order_binding"

        if mid_decision is None:
            if target_ref:
                decision_mid_candidate, decision_ref_reason = lookup_mid_by_target_ref(
                    target_ref=target_ref,
                    target_ts=decision_ts,
                    tolerance_ms=eval_tolerance_ms,
                )
                if decision_mid_candidate is not None:
                    mid_decision = float(decision_mid_candidate)
                    decision_reference_status = "recovered_timestamp_bound_artifact_lookup"
                    decision_reference_source = "edge_evaluation.target_ref_series"
                    decision_reference_lookup_key = f"target_ref:{target_ref}"
                else:
                    decision_reference_reason = decision_ref_reason
            else:
                decision_reference_reason = "target_ref_missing_for_artifact_lookup"

        if mid_decision is None:
            decision_mid_candidate, decision_ref_reason = lookup_mid_by_token(token_id=token_id, target_ts=decision_ts)
            if decision_mid_candidate is not None:
                mid_decision = float(decision_mid_candidate)
                decision_reference_status = "recovered_token_lookup"
                decision_reference_source = "book_top.token_lookup"
                decision_reference_lookup_key = f"token_id:{token_id}"
            else:
                decision_reference_reason = decision_ref_reason

        decision_reference_recoverable = bool(mid_decision is not None)
        decision_reference_basis = _decision_reference_basis(
            source=decision_reference_source,
            status=decision_reference_status,
        )
        if decision_reference_lookup_key is None and target_ref:
            decision_reference_lookup_key = f"target_ref:{target_ref}"

        eval_reference_status = "missing"
        eval_reference_source = "none"
        eval_reference_lookup_key: Optional[str] = None
        eval_reference_reason: Optional[str] = None
        mid_eval: Optional[float] = None

        explicit_eval_mid = _safe_float(submit.get("eval_reference_midpoint"))
        if explicit_eval_mid is not None:
            mid_eval = float(explicit_eval_mid)
            eval_reference_status = "recovered_explicit_eval_reference"
            eval_reference_source = str(submit.get("eval_reference_source") or "order_submit.eval_reference_midpoint")
            eval_reference_lookup_key = str(submit.get("eval_reference_lookup_key") or f"order_submit:{order_id}")
        else:
            eval_reference_reason = "explicit_eval_reference_missing"

        if mid_eval is None:
            if target_ref:
                eval_mid_candidate, eval_ref_reason = lookup_mid_by_target_ref(
                    target_ref=target_ref,
                    target_ts=ts_eval,
                    tolerance_ms=eval_tolerance_ms,
                )
                if eval_mid_candidate is not None:
                    mid_eval = float(eval_mid_candidate)
                    eval_reference_status = "recovered_timestamp_bound_artifact_lookup"
                    eval_reference_source = "edge_evaluation.target_ref_series"
                    eval_reference_lookup_key = f"target_ref:{target_ref}"
                else:
                    eval_reference_reason = eval_ref_reason
            else:
                eval_reference_reason = "target_ref_missing_for_artifact_lookup"

        if mid_eval is None:
            eval_mid_candidate, eval_ref_reason = lookup_mid_by_token(token_id=token_id, target_ts=ts_eval)
            if eval_mid_candidate is not None:
                mid_eval = float(eval_mid_candidate)
                eval_reference_status = "recovered_token_lookup"
                eval_reference_source = "book_top.token_lookup"
                eval_reference_lookup_key = f"token_id:{token_id}"
            else:
                eval_reference_reason = eval_ref_reason

        eval_reference_recoverable = bool(mid_eval is not None)
        eval_reference_basis = _eval_reference_basis(
            source=eval_reference_source,
            status=eval_reference_status,
        )
        if eval_reference_lookup_key is None and target_ref:
            eval_reference_lookup_key = f"target_ref:{target_ref}"

        decision_reference_exists_in_artifacts = bool(
            explicit_decision_mid is not None
            or target_ref_candidate_exists(
                target_ref=target_ref,
                target_ts=decision_ts,
                tolerance_ms=eval_tolerance_ms,
            )
            or token_candidate_exists(token_id=token_id, target_ts=decision_ts)
        )
        eval_reference_exists_in_artifacts = bool(
            explicit_eval_mid is not None
            or target_ref_candidate_exists(
                target_ref=target_ref,
                target_ts=ts_eval,
                tolerance_ms=eval_tolerance_ms,
            )
            or token_candidate_exists(token_id=token_id, target_ts=ts_eval)
        )

        order_fills = sorted(
            fills_by_order_id.get(order_id, []),
            key=lambda row: (
                str(row.get("ts_utc") or ""),
                str(row.get("trade_id") or ""),
            ),
        )

        fill_trade_ids: List[str] = []
        fill_total_size = 0.0
        fill_notional = 0.0
        fill_value_invalid = False
        ts_fill: Optional[dt.datetime] = None

        for fill in order_fills:
            trade_id = _non_empty_text(fill.get("trade_id"))
            if trade_id:
                fill_trade_ids.append(trade_id)
            fill_ts = _parse_ts(fill.get("ts_utc"))
            if fill_ts is not None:
                ts_fill = fill_ts if ts_fill is None else max(ts_fill, fill_ts)
            fill_price = _safe_positive_float(fill.get("price"))
            fill_size = _safe_positive_float(fill.get("size"))
            if fill_price is None or fill_size is None:
                fill_value_invalid = True
                continue
            fill_total_size += float(fill_size)
            fill_notional += float(fill_price) * float(fill_size)

        aggregated_fill_price: Optional[float] = None
        if fill_total_size > 0.0:
            aggregated_fill_price = float(fill_notional / fill_total_size)

        unique_trade_ids = sorted(set(fill_trade_ids))
        fill_trade_id: Optional[str]
        if len(unique_trade_ids) == 1:
            fill_trade_id = unique_trade_ids[0]
        elif len(unique_trade_ids) > 1:
            digest = hashlib.sha256("|".join(unique_trade_ids).encode("utf-8")).hexdigest()[:16]
            fill_trade_id = f"multi:{digest}"
        else:
            fill_trade_id = None

        slippage = None
        adverse_selection = None
        edge_realized = None
        decision_component = None
        execution_component = None
        market_component = None

        if direction_sign is not None and aggregated_fill_price is not None and mid_decision is not None:
            slippage = float(direction_sign) * (float(aggregated_fill_price) - float(mid_decision))
            execution_component = float(direction_sign) * (float(mid_decision) - float(aggregated_fill_price))
        if direction_sign is not None and aggregated_fill_price is not None and mid_eval is not None:
            adverse_selection = float(direction_sign) * (float(aggregated_fill_price) - float(mid_eval))
            edge_realized = float(direction_sign) * (float(mid_eval) - float(aggregated_fill_price))
        if direction_sign is not None and mid_decision is not None and mid_eval is not None:
            decision_component = float(direction_sign) * (float(mid_eval) - float(mid_decision))
        if decision_component is not None and execution_component is not None and edge_realized is not None:
            market_component = float(edge_realized - decision_component - execution_component)

        decision_delta = None
        if direction_sign is not None and mid_decision is not None and mid_eval is not None:
            decision_delta = float(direction_sign) * (float(mid_eval) - float(mid_decision))
        decision_quality = _classify_signed_delta(
            value=decision_delta,
            tolerance=decision_tol,
            positive=DECISION_QUALITY_CORRECT,
            negative=DECISION_QUALITY_INCORRECT,
            neutral=DECISION_QUALITY_NEUTRAL,
        )

        execution_delta = None
        if direction_sign is not None and aggregated_fill_price is not None and mid_decision is not None:
            execution_delta = float(direction_sign) * (float(mid_decision) - float(aggregated_fill_price))
        execution_quality = _classify_signed_delta(
            value=execution_delta,
            tolerance=execution_tol,
            positive=EXECUTION_QUALITY_FAVORABLE,
            negative=EXECUTION_QUALITY_UNFAVORABLE,
            neutral=EXECUTION_QUALITY_NEUTRAL,
        )

        combined_outcome_class = _combine_outcome_class(decision_quality, execution_quality)

        missing_fields: List[str] = []
        if direction_sign is None:
            missing_fields.append("order_side")
        if decision_ts is None:
            missing_fields.append("ts_decision_utc")
        if ts_eval is None:
            missing_fields.append("ts_eval_utc")
        if mid_decision is None:
            missing_fields.append("mid_price_decision")
        if mid_eval is None:
            missing_fields.append("mid_price_eval")
        if aggregated_fill_price is None:
            missing_fields.append("fill_price")
        if ts_fill is None:
            missing_fields.append("ts_fill_utc")
        if fill_trade_id is None:
            missing_fields.append("fill_trade_id")
        if edge_candidate is None:
            missing_fields.append("edge_linkage")
            if maker_edge_linkage_ambiguous:
                missing_fields.append("edge_linkage:maker_target_ref_decision_ts_ambiguous")
            elif maker_edge_linkage_missing:
                missing_fields.append("edge_linkage:maker_target_ref_decision_ts_missing")
        if not edge_expected_known:
            missing_fields.append("edge_expected")
        if fill_value_invalid:
            missing_fields.append("fill_value_invalid")
        if decision_reference_reason is not None:
            missing_fields.append(f"decision_reference:{decision_reference_reason}")
        if eval_reference_reason is not None:
            missing_fields.append(f"eval_reference:{eval_reference_reason}")

        has_fills = len(order_fills) > 0
        outcome_truth_status = OUTCOME_STATUS_COMPLETE
        if not has_fills:
            outcome_truth_status = OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE
        elif mid_decision is None:
            outcome_truth_status = OUTCOME_STATUS_UNKNOWN_MISSING_DECISION_REFERENCE
        elif mid_eval is None:
            outcome_truth_status = OUTCOME_STATUS_UNKNOWN_MISSING_EVAL_REFERENCE
        elif edge_candidate is None:
            outcome_truth_status = OUTCOME_STATUS_UNKNOWN_MISSING_LINKAGE
        elif (
            decision_ts is None
            or ts_eval is None
            or direction_sign is None
            or mid_decision is None
            or aggregated_fill_price is None
            or ts_fill is None
            or fill_trade_id is None
        ):
            outcome_truth_status = OUTCOME_STATUS_UNKNOWN_MISSING_DATA

        claim_boundary_class = CLAIM_BOUNDARY_COMPLETE
        if outcome_truth_status == OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE:
            claim_boundary_class = CLAIM_BOUNDARY_INCOMPLETE
        elif outcome_truth_status != OUTCOME_STATUS_COMPLETE:
            claim_boundary_class = CLAIM_BOUNDARY_UNKNOWN
        record_claim_boundary_class = _record_claim_boundary_from_status(outcome_truth_status)

        record = {
            "decision_id": f"decision:{selected_run_id}:{order_id}",
            "order_submit_id": order_id,
            "fill_trade_id": fill_trade_id,
            "ts_decision_utc": _format_ts(decision_ts),
            "ts_fill_utc": _format_ts(ts_fill),
            "ts_eval_utc": _format_ts(ts_eval),
            "mid_price_decision": mid_decision,
            "fill_price": aggregated_fill_price,
            "mid_price_eval": mid_eval,
            "edge_expected": edge_expected,
            "edge_expected_known": bool(edge_expected_known),
            "slippage": slippage,
            "adverse_selection": adverse_selection,
            "edge_realized": edge_realized,
            "decision_quality": decision_quality,
            "execution_quality": execution_quality,
            "combined_outcome_class": combined_outcome_class,
            "evaluation_horizon_ms": int(horizon_ms),
            "outcome_truth_status": outcome_truth_status,
            "missing_fields": sorted(set(missing_fields)),
            "claim_boundary_class": claim_boundary_class,
            "record_claim_boundary_class": record_claim_boundary_class,
            "decision_reference_status": decision_reference_status,
            "decision_reference_source": decision_reference_source,
            "decision_reference_basis": decision_reference_basis,
            "decision_reference_lookup_key": decision_reference_lookup_key,
            "decision_reference_recoverable": bool(decision_reference_recoverable),
            "eval_reference_status": eval_reference_status,
            "eval_reference_source": eval_reference_source,
            "eval_reference_basis": eval_reference_basis,
            "eval_reference_lookup_key": eval_reference_lookup_key,
            "eval_reference_recoverable": bool(eval_reference_recoverable),
            "decision_reference_link_status": decision_reference_link_status,
            "eval_reference_link_status": eval_reference_link_status,
            "reference_linkage_mode": reference_linkage_mode,
            "reference_linkage_complete": bool(reference_linkage_complete),
            "maker_edge_linkage_attempted": bool(maker_edge_linkage_attempted),
            "maker_edge_linkage_resolved": bool(maker_edge_linkage_resolved),
            "maker_edge_linkage_ambiguous": bool(maker_edge_linkage_ambiguous),
            "maker_edge_linkage_missing": bool(maker_edge_linkage_missing),
            "decision_anchor_ts_utc": _format_ts(decision_ts),
            "decision_anchor_source": decision_anchor_source,
            "decision_reference_exists_in_artifacts": bool(decision_reference_exists_in_artifacts),
            "eval_reference_exists_in_artifacts": bool(eval_reference_exists_in_artifacts),
            "order_side": side,
            "token_id": token_id,
            "target_ref": target_ref or None,
            "decision_component": decision_component,
            "execution_component": execution_component,
            "market_component": market_component,
            "fill_count": int(len(order_fills)),
            "fill_total_size": (float(fill_total_size) if fill_total_size > 0.0 else None),
            "decision_quality_basis": "directional_mid_eval_vs_mid_decision",
            "execution_quality_basis": "directional_mid_decision_vs_fill",
            "combined_outcome_basis": "decision_quality_plus_execution_quality",
            "claim_boundary": {
                "layer": "outcome_truth_observational",
                "record_class": claim_boundary_class,
                "record_claim_boundary_class": record_claim_boundary_class,
            },
        }
        records.append(record)

    normalized_records = [_normalize_record(row) for row in records]
    normalized_records.sort(key=_record_sort_key)

    findings.extend(_validate_records(records=normalized_records, policy=policy))
    findings = sorted(set(str(item) for item in findings if str(item).strip()))
    warnings = sorted(set(str(item) for item in warnings if str(item).strip()))

    records_json = json.dumps(normalized_records, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    outcome_records_sha256 = hashlib.sha256(records_json.encode("utf-8")).hexdigest()

    decision_quality_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("decision_quality") or "") == key))
        for key in sorted(DECISION_QUALITIES)
    }
    execution_quality_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("execution_quality") or "") == key))
        for key in sorted(EXECUTION_QUALITIES)
    }
    combined_outcome_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("combined_outcome_class") or "") == key))
        for key in sorted(COMBINED_CLASSES)
    }
    outcome_status_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("outcome_truth_status") or "") == key))
        for key in sorted(OUTCOME_STATUSES)
    }

    complete_count = int(outcome_status_distribution.get(OUTCOME_STATUS_COMPLETE, 0))
    partial_count = int(len(normalized_records) - complete_count)
    unknown_count = int(len(normalized_records) - complete_count)
    incomplete_count = int(outcome_status_distribution.get(OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE, 0))
    record_claim_boundary_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("record_claim_boundary_class") or "") == key))
        for key in sorted(RECORD_CLAIM_BOUNDARY_CLASSES)
    }
    decision_reference_basis_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("decision_reference_basis") or "") == key))
        for key in sorted(REFERENCE_BASES)
    }
    eval_reference_basis_distribution = {
        key: int(sum(1 for row in normalized_records if str(row.get("eval_reference_basis") or "") == key))
        for key in sorted(REFERENCE_BASES)
    }
    filled_total = int(sum(1 for row in normalized_records if int(row.get("fill_count") or 0) > 0))
    filled_complete = int(
        sum(
            1
            for row in normalized_records
            if int(row.get("fill_count") or 0) > 0 and str(row.get("outcome_truth_status") or "") == OUTCOME_STATUS_COMPLETE
        )
    )
    filled_unknown = int(max(0, filled_total - filled_complete))
    filled_complete_ratio = float(filled_complete) / float(filled_total) if filled_total > 0 else 0.0
    maker_edge_linkage_attempted_records = int(
        sum(1 for row in normalized_records if bool(row.get("maker_edge_linkage_attempted", False)))
    )
    maker_edge_linkage_resolved_records = int(
        sum(1 for row in normalized_records if bool(row.get("maker_edge_linkage_resolved", False)))
    )
    maker_edge_linkage_ambiguous_records = int(
        sum(1 for row in normalized_records if bool(row.get("maker_edge_linkage_ambiguous", False)))
    )
    maker_edge_linkage_missing_records = int(
        sum(1 for row in normalized_records if bool(row.get("maker_edge_linkage_missing", False)))
    )
    run_claim_boundary = {
        "complete_records": int(record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_COMPLETE, 0)),
        "partial_records": int(
            record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_PARTIAL_MISSING_DATA, 0)
            + record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_MISSING_DECISION_REFERENCE, 0)
            + record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_MISSING_EVAL_REFERENCE, 0)
        ),
        "unknown_records": int(
            record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_INCOMPLETE_LIFECYCLE, 0)
            + record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_UNKNOWN, 0)
        ),
        "completeness_ratio": (
            float(record_claim_boundary_distribution.get(RECORD_CLAIM_BOUNDARY_COMPLETE, 0)) / float(len(normalized_records))
            if len(normalized_records) > 0
            else 0.0
        ),
        "claim_scope": "observational_only_fixed_horizon_outcome_truth",
    }
    horizon_findings = [
        finding
        for finding in findings
        if finding == "mixed_evaluation_horizon_detected"
        or finding == "missing_evaluation_horizon"
        or "evaluation_horizon_ms" in finding
        or str(finding).startswith("outcome_truth_policy_horizon")
    ]
    horizon_consistency_check = "pass" if len(horizon_findings) == 0 else "fail"

    missing_decision_midpoint_count = int(sum(1 for row in normalized_records if row.get("mid_price_decision") is None))
    missing_eval_midpoint_count = int(sum(1 for row in normalized_records if row.get("mid_price_eval") is None))
    missing_edge_expected_count = int(sum(1 for row in normalized_records if not bool(row.get("edge_expected_known", False))))
    decision_reference_recovered_count = int(
        sum(1 for row in normalized_records if bool(row.get("decision_reference_recoverable", False)))
    )
    eval_reference_recovered_count = int(
        sum(1 for row in normalized_records if bool(row.get("eval_reference_recoverable", False)))
    )
    decision_reference_missing_count = int(len(normalized_records) - decision_reference_recovered_count)
    eval_reference_missing_count = int(len(normalized_records) - eval_reference_recovered_count)
    recoverable_but_missing_count = int(
        sum(
            1
            for row in normalized_records
            if (
                (
                    bool(row.get("decision_reference_exists_in_artifacts", False))
                    and not bool(row.get("decision_reference_recoverable", False))
                )
                or (
                    bool(int(row.get("fill_count") or 0) > 0)
                    and bool(row.get("eval_reference_exists_in_artifacts", False))
                    and not bool(row.get("eval_reference_recoverable", False))
                )
            )
        )
    )
    attribution_usability_ratio = (
        float(complete_count) / float(len(normalized_records)) if len(normalized_records) > 0 else 0.0
    )

    expected_vs_realized_values: List[Optional[float]] = []
    for row in normalized_records:
        expected = _safe_float(row.get("edge_expected"))
        realized = _safe_float(row.get("edge_realized"))
        if expected is None or realized is None:
            continue
        expected_vs_realized_values.append(float(realized - expected))

    records_out_resolved = ""
    if records_out_path is not None:
        records_target = records_out_path.resolve()
        records_target.parent.mkdir(parents=True, exist_ok=True)
        with records_target.open("w", encoding="utf-8") as fh:
            for row in normalized_records:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
        records_out_resolved = str(records_target)

    claim_boundary = policy.get("claim_boundary") if isinstance(policy.get("claim_boundary"), dict) else {}

    return {
        "ok": len(findings) == 0,
        "finding_count": len(findings),
        "findings": findings,
        "warning_count": len(warnings),
        "warnings": warnings,
        "run_id": selected_run_id,
        "log_dir": str(log_dir.resolve()),
        "session_phase": normalized_phase,
        "run_contract_path": (
            str(run_contract_path.resolve()) if isinstance(run_contract_path, pathlib.Path) else str(resolved_contract_path)
        ),
        "event_source_paths": [str(path.resolve()) for path in event_paths],
        "events_considered": int(len(events)),
        "order_submit_events_considered": int(len(order_submit_rows)),
        "fill_events_considered": int(len(fill_rows)),
        "edge_action_events_considered": int(len(edge_action_rows)),
        "book_top_events_considered": int(len(book_rows)),
        "outcome_truth_policy_path": str(policy_path.resolve()),
        "outcome_truth_policy": policy,
        "eval_reference_timestamp_domain": eval_timestamp_domain,
        "eval_reference_selection_rule": eval_selection_rule,
        "eval_reference_tolerance_ms": int(eval_tolerance_ms),
        "reference_recovery_priority": list(policy.get("reference_recovery_priority") or []),
        "classification_tolerances": {
            "decision_quality": float(decision_tol),
            "execution_quality": float(execution_tol),
        },
        "evaluation_horizon_ms": int(horizon_ms),
        "horizon_enforced": True,
        "horizon_consistency_check": horizon_consistency_check,
        "record_scope_policy": {
            "emit_for_order_submit": True,
            "emit_for_no_action_edge_rows": False,
            "incomplete_lifecycle_record_status": OUTCOME_STATUS_UNKNOWN_INCOMPLETE_LIFECYCLE,
        },
        "claim_boundary": claim_boundary,
        "run_claim_boundary": run_claim_boundary,
        "total_outcome_records": int(len(normalized_records)),
        "complete_outcome_records": int(complete_count),
        "partial_outcome_records": int(partial_count),
        "unknown_outcome_records": int(unknown_count),
        "incomplete_lifecycle_records": int(incomplete_count),
        "filled_total": int(filled_total),
        "filled_complete": int(filled_complete),
        "filled_unknown": int(filled_unknown),
        "filled_complete_ratio": float(filled_complete_ratio),
        "decision_reference_recovered_count": int(decision_reference_recovered_count),
        "eval_reference_recovered_count": int(eval_reference_recovered_count),
        "decision_reference_missing_count": int(decision_reference_missing_count),
        "eval_reference_missing_count": int(eval_reference_missing_count),
        "maker_edge_linkage_attempted_count": int(maker_edge_linkage_attempted_records),
        "maker_edge_linkage_resolved_count": int(maker_edge_linkage_resolved_records),
        "maker_edge_linkage_ambiguous_count": int(maker_edge_linkage_ambiguous_records),
        "maker_edge_linkage_missing_count": int(maker_edge_linkage_missing_records),
        "recoverable_but_missing_count": int(recoverable_but_missing_count),
        "attribution_usability_ratio": float(attribution_usability_ratio),
        "complete_classification_ratio": float(attribution_usability_ratio),
        "outcome_truth_status_distribution": outcome_status_distribution,
        "record_claim_boundary_distribution": record_claim_boundary_distribution,
        "decision_reference_basis_distribution": decision_reference_basis_distribution,
        "eval_reference_basis_distribution": eval_reference_basis_distribution,
        "decision_quality_distribution": decision_quality_distribution,
        "execution_quality_distribution": execution_quality_distribution,
        "combined_outcome_distribution": combined_outcome_distribution,
        "slippage_summary": _numeric_summary(row.get("slippage") for row in normalized_records),
        "adverse_selection_summary": _numeric_summary(row.get("adverse_selection") for row in normalized_records),
        "edge_expected_summary": _numeric_summary(row.get("edge_expected") for row in normalized_records),
        "edge_realized_summary": _numeric_summary(row.get("edge_realized") for row in normalized_records),
        "expected_vs_realized_summary": _numeric_summary(expected_vs_realized_values),
        "missingness": {
            "missing_decision_midpoint_count": int(missing_decision_midpoint_count),
            "missing_eval_midpoint_count": int(missing_eval_midpoint_count),
            "missing_edge_expected_count": int(missing_edge_expected_count),
            "missing_linkage_count": int(missing_linkage_count),
            "decision_reference_missing_count": int(decision_reference_missing_count),
            "eval_reference_missing_count": int(eval_reference_missing_count),
            "recoverable_but_missing_count": int(recoverable_but_missing_count),
        },
        "outcome_records_sha256": outcome_records_sha256,
        "policy_sha256": _sha256_json_payload(policy),
        "claim_boundary_sha256": _sha256_json_payload(claim_boundary),
        "audit_rule_set_sha256": _sha256_json_payload(list(AUDIT_RULE_SET)),
        "outcome_records_path": records_out_resolved,
        "error_codes": summarize_error_codes(findings),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO outcome-truth / edge-realization audit")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument("--run-contract", required=True, help="Run contract JSON path")
    parser.add_argument("--policy", default="ops/outcome_truth_policy.json", help="Outcome-truth policy JSON")
    parser.add_argument("--session-phase", default="validate_postrun", help="Validation lifecycle phase")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file; set 0 for full-file scans",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument("--records-out", default="", help="Optional output JSONL path for per-record outcomes")
    args = parser.parse_args()

    result = run_audit(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        policy_path=pathlib.Path(args.policy),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
        records_out_path=(pathlib.Path(args.records_out) if str(args.records_out).strip() else None),
    )

    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)

    out_path_raw = str(args.out).strip()
    if out_path_raw:
        out_path = pathlib.Path(out_path_raw).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")

    raise SystemExit(0 if bool(result.get("ok", False)) else 2)


if __name__ == "__main__":
    main()
