from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .lineage_stage import STAGE_LINEAGE_ONLY_0_TO_20S, normalize_lineage_stage

EDGE_EVAL_SCOPE_MAKER = "maker"
EDGE_EVAL_SCOPE_TAKER = "taker"
EDGE_EVAL_SCOPES: Tuple[str, ...] = (
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPE_TAKER,
)

EDGE_ACTION_MAKER = "maker"
EDGE_ACTION_TAKER = "taker"
EDGE_ACTION_NONE = "none"
EDGE_ACTIONS: Tuple[str, ...] = (
    EDGE_ACTION_MAKER,
    EDGE_ACTION_TAKER,
    EDGE_ACTION_NONE,
)

EVENT_TAKER_DECISION = "taker_decision"
EVENT_TAKER_SUBMIT = "taker_submit"
EVENT_TAKER_WINDOW_SEMANTIC_CHECK = "taker_window_semantic_check"
TAKER_DECISION_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_DECISION,
)
TAKER_SUBMIT_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_SUBMIT,
)
TAKER_WINDOW_SEMANTIC_CHECK_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_WINDOW_SEMANTIC_CHECK,
)
TAKER_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_DECISION,
    EVENT_TAKER_SUBMIT,
    EVENT_TAKER_WINDOW_SEMANTIC_CHECK,
)

TAKER_CHAINLINK_REASON = "taker_chainlink"

EDGE_INPUT_REASON_CODES: Tuple[str, ...] = (
    "edge_scope_invalid",
    "phase_missing",
    "fair_probability_missing",
    "fair_probability_invalid",
    "market_probability_missing",
    "market_probability_invalid",
    "time_remaining_sec_missing",
    "time_remaining_sec_invalid",
    "oracle_tick_age_sec_missing",
    "oracle_tick_age_sec_invalid",
    "oracle_tick_stale",
    "edge_value_invalid",
)

EDGE_EXECUTION_BLOCK_REASONS: Tuple[str, ...] = (
    "phase_disallow_maker",
    "maker_requires_ws_book_source",
    "maker_no_submission",
    "missing_expiry_metadata",
    "missing_threshold_metadata",
    "missing_side_metadata",
    "oracle_unavailable_or_stale",
    "maker_timing_gate_closed",
    "taker_disabled",
    "taker_budget_disabled",
    "operating_mode_maker_only",
    "operating_mode_safe_stop",
    "operating_mode_non_normal",
    "ramp_taker_disabled",
    "normal_taker_authority_closed",
    "phase_disallow_taker",
    "taker_requires_ws_book_source",
    "edge_below_min",
    "taker_token_cooldown",
    "taker_order_budget_exhausted",
    "taker_outside_final_window",
    "taker_window_already_submitted",
    "taker_hard_min_notional_unachievable",
    "taker_dynamic_size_capped_by_risk",
    "taker_visible_fill_ratio_below_min",
    "taker_submit_price_below_floor",
    "taker_price_unavailable",
    "taker_competitiveness_disabled",
    "secondary_oracle_not_confirmed",
    "normal_taker_same_token_sell_forbidden",
    "window_geometry_hard_pinned",
    "window_geometry_near_pinned",
    "maker_edge_below_min",
    "maker_single_market_expression_pruned",
    "open_order_cleanup_required",
    "settlement_hold_required",
    "taker_submit_rejected",
)

EDGE_BLOCK_REASONS: Tuple[str, ...] = tuple(
    sorted(set(EDGE_INPUT_REASON_CODES).union(set(EDGE_EXECUTION_BLOCK_REASONS)))
)
_EDGE_BLOCK_REASON_SET = {item.lower() for item in EDGE_BLOCK_REASONS}

STAGE_OBSERVE = "OBSERVE"
STAGE_EVALUATE = "EVALUATE"
STAGE_MAKER_POSITION = "MAKER_POSITION"
STAGE_MAKER_TAKER_SELECTIVE = "MAKER_TAKER_SELECTIVE"
STAGE_SNIPER_PRIMARY = "SNIPER_PRIMARY"
STAGE_LATE_DIAGNOSTIC = "LATE_DIAGNOSTIC"
STAGE_MAKER_LATE_WINDOW = "MAKER_LATE_WINDOW"
STAGE_TAKER_COMMITMENT = "TAKER_COMMITMENT"
STAGE_EXTREME_ONLY = STAGE_LINEAGE_ONLY_0_TO_20S
STAGE_EXPIRED = "EXPIRED"
STAGE_UNKNOWN = "UNKNOWN"
EDGE_STAGE_LINEAGE_FIELD = "lineage_stage"
LIFECYCLE_PHASE_SCAN = "scan"
LIFECYCLE_PHASE_PREPARE = "prepare"
LIFECYCLE_PHASE_MAKER_WINDOW = "maker_window"
LIFECYCLE_PHASE_TAKER_WINDOW = "taker_window"
LIFECYCLE_PHASE_RESOLVE = "resolve"
LIFECYCLE_PHASES: Tuple[str, ...] = (
    LIFECYCLE_PHASE_SCAN,
    LIFECYCLE_PHASE_PREPARE,
    LIFECYCLE_PHASE_MAKER_WINDOW,
    LIFECYCLE_PHASE_TAKER_WINDOW,
    LIFECYCLE_PHASE_RESOLVE,
)
EDGE_LIFECYCLE_PHASE_FIELD = "lifecycle_phase"
EDGE_OWNED_MARKET_REF_FIELD = "owned_market_ref"
EDGE_CHALLENGER_MARKET_REF_FIELD = "challenger_market_ref"
EDGE_OWNERSHIP_DROP_REASON_FIELD = "ownership_drop_reason"
EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD = "ownership_replacement_reason"
EDGE_MARKET_TRUTH_REQUIRED_FIELD = "market_truth_required"
EDGE_MAKER_PHASE_ALLOWED_FIELD = "maker_phase_allowed"
EDGE_TAKER_PHASE_ALLOWED_FIELD = "taker_phase_allowed"
EDGE_MAKER_GATE_OPEN_FIELD = "maker_gate_open"
EDGE_TAKER_GATE_OPEN_FIELD = "taker_gate_open"
EDGE_MAKER_GATE_STAGE_FIELD = "maker_gate_stage"
EDGE_MAKER_GATE_REASON_FIELD = "maker_gate_reason"
EDGE_MAKER_GATE_OWNER_FAMILY_FIELD = "maker_gate_owner_family"
EDGE_MAKER_GATE_TERMINAL_FIELD = "maker_gate_terminal"
EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD = "open_order_cleanup_required"
EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD = "settlement_hold_required"
EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD = "unresolved_lifecycle_obligation"
EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD = "cancel_fail_closed"
MAKER_GATE_STAGE_PHASE = "phase_gate"
MAKER_GATE_STAGE_TRUTH_REFERENCE = "truth_reference_gate"
MAKER_GATE_STAGE_LANE_READINESS = "lane_readiness_gate"
MAKER_GATE_STAGE_SELECTION = "selection_gate"
MAKER_GATE_STAGE_FEASIBILITY = "feasibility_gate"
MAKER_GATE_STAGE_SHARED_SAFETY = "shared_safety_lifecycle_gate"
MAKER_GATE_STAGE_QUOTE_QUALITY = "quote_quality_gate"
MAKER_GATE_STAGE_SUBMIT_PATH = "submit_path_gate"
MAKER_GATE_STAGE_SUBMITTED = "submitted"
MAKER_GATE_STAGE_FILLED = "filled"
MAKER_GATE_STAGES: Tuple[str, ...] = (
    MAKER_GATE_STAGE_PHASE,
    MAKER_GATE_STAGE_TRUTH_REFERENCE,
    MAKER_GATE_STAGE_LANE_READINESS,
    MAKER_GATE_STAGE_SELECTION,
    MAKER_GATE_STAGE_FEASIBILITY,
    MAKER_GATE_STAGE_SHARED_SAFETY,
    MAKER_GATE_STAGE_QUOTE_QUALITY,
    MAKER_GATE_STAGE_SUBMIT_PATH,
    MAKER_GATE_STAGE_SUBMITTED,
    MAKER_GATE_STAGE_FILLED,
)
MAKER_GATE_OWNER_PHASE = "lifecycle_phase"
MAKER_GATE_OWNER_TRUTH_REFERENCE = "executor_truth_reference"
MAKER_GATE_OWNER_LANE_READINESS = "executor_lane_readiness"
MAKER_GATE_OWNER_SELECTION = "order_manager_selection"
MAKER_GATE_OWNER_FEASIBILITY = "order_manager_feasibility"
MAKER_GATE_OWNER_SHARED_SAFETY = "shared_lifecycle_safety"
MAKER_GATE_OWNER_QUOTE_QUALITY = "order_manager_quote_quality"
MAKER_GATE_OWNER_SUBMIT_PATH = "submit_path"
MAKER_GATE_OWNER_SUBMITTED = "submitted_population"
MAKER_GATE_OWNER_FILLED = "filled_population"
MAKER_GATE_OWNER_FAMILIES: Tuple[str, ...] = (
    MAKER_GATE_OWNER_PHASE,
    MAKER_GATE_OWNER_TRUTH_REFERENCE,
    MAKER_GATE_OWNER_LANE_READINESS,
    MAKER_GATE_OWNER_SELECTION,
    MAKER_GATE_OWNER_FEASIBILITY,
    MAKER_GATE_OWNER_SHARED_SAFETY,
    MAKER_GATE_OWNER_QUOTE_QUALITY,
    MAKER_GATE_OWNER_SUBMIT_PATH,
    MAKER_GATE_OWNER_SUBMITTED,
    MAKER_GATE_OWNER_FILLED,
)
_MAKER_GATE_STAGE_SET = set(MAKER_GATE_STAGES)
_MAKER_GATE_OWNER_SET = set(MAKER_GATE_OWNER_FAMILIES)
_MAKER_GATE_STAGE_OWNER_BY_STAGE: Dict[str, str] = {
    MAKER_GATE_STAGE_PHASE: MAKER_GATE_OWNER_PHASE,
    MAKER_GATE_STAGE_TRUTH_REFERENCE: MAKER_GATE_OWNER_TRUTH_REFERENCE,
    MAKER_GATE_STAGE_LANE_READINESS: MAKER_GATE_OWNER_LANE_READINESS,
    MAKER_GATE_STAGE_SELECTION: MAKER_GATE_OWNER_SELECTION,
    MAKER_GATE_STAGE_FEASIBILITY: MAKER_GATE_OWNER_FEASIBILITY,
    MAKER_GATE_STAGE_SHARED_SAFETY: MAKER_GATE_OWNER_SHARED_SAFETY,
    MAKER_GATE_STAGE_QUOTE_QUALITY: MAKER_GATE_OWNER_QUOTE_QUALITY,
    MAKER_GATE_STAGE_SUBMIT_PATH: MAKER_GATE_OWNER_SUBMIT_PATH,
    MAKER_GATE_STAGE_SUBMITTED: MAKER_GATE_OWNER_SUBMITTED,
    MAKER_GATE_STAGE_FILLED: MAKER_GATE_OWNER_FILLED,
}
_MAKER_GATE_PHASE_REASONS = frozenset({"phase_disallow_maker"})
_MAKER_GATE_TRUTH_REFERENCE_REASONS = frozenset(
    {
        "maker_requires_ws_book_source",
        "market_reference_not_authoritative",
        "oracle_unavailable_or_stale",
        "missing_expiry_metadata",
        "missing_threshold_metadata",
        "missing_side_metadata",
    }
)
_MAKER_GATE_LANE_READINESS_REASONS = frozenset(
    {
        "maker_timing_gate_closed",
    }
)
_MAKER_GATE_SELECTION_REASONS = frozenset(
    {
        "secondary_oracle_not_confirmed",
        "launch_safe_selection_insufficient_depth_multiple",
        "selection_prior_target_submit",
        "selection_prior_same_side_submit",
        "maker_edge_below_min",
        "maker_single_market_expression_pruned",
        "maker_market_viability_reject",
    }
)
_MAKER_GATE_FEASIBILITY_REASONS = frozenset(
    {
        "window_geometry_hard_pinned",
        "window_geometry_near_pinned",
        "non_actionable_geometry",
        "sizing_reject",
        "maker_hard_min_notional_failed_after_rounding",
        "maker_hard_max_notional_failed_after_rounding",
        "global_notional_bounds_after_rounding",
        "rounded_shares_nonpositive",
        "price_unavailable",
        "wallet_reject",
    }
)
_MAKER_GATE_SHARED_SAFETY_REASONS = frozenset(
    {
        "settlement_hold_required",
        "open_order_cleanup_required",
        "maker_commitment_hold_active",
        "unresolved_lifecycle_obligation",
        "cancel_fail_closed",
        "maker_commitment_context_missing",
    }
)
_MAKER_GATE_QUOTE_QUALITY_REASONS = frozenset(
    {
        "no_desired_quote",
    }
)
_MAKER_GATE_SUBMIT_PATH_REASONS = frozenset(
    {
        "action_budget_exhausted",
        "replace_guard_min_rest",
        "replace_cancel_unavailable",
        "quote_unchanged",
        "post_only_reject",
        "order_submit_exception",
        "submit_rejected",
        "taker_submit_rejected",
        "pre_submit_cross_guarded",
        "soft_throttle",
        "maker_viability_context_missing",
    }
)
CANONICAL_LIFECYCLE_PHASE_POLICY: Dict[str, Tuple[bool, bool]] = {
    LIFECYCLE_PHASE_SCAN: (False, False),
    LIFECYCLE_PHASE_PREPARE: (False, False),
    LIFECYCLE_PHASE_MAKER_WINDOW: (True, False),
    LIFECYCLE_PHASE_TAKER_WINDOW: (False, True),
    LIFECYCLE_PHASE_RESOLVE: (False, False),
}

def normalize_lifecycle_phase(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in LIFECYCLE_PHASES else ""


def lineage_stage_surface_fields(*, lineage_stage: Any) -> Dict[str, str]:
    return {
        EDGE_STAGE_LINEAGE_FIELD: normalize_lineage_stage(lineage_stage),
    }


def lifecycle_phase_surface_fields(*, lifecycle_phase: Any) -> Dict[str, str]:
    normalized_phase = normalize_lifecycle_phase(lifecycle_phase)
    return {
        EDGE_LIFECYCLE_PHASE_FIELD: normalized_phase,
    }


def lifecycle_phase_from_payload(payload: Mapping[str, Any]) -> str:
    return normalize_lifecycle_phase(payload.get(EDGE_LIFECYCLE_PHASE_FIELD))


def ownership_surface_fields(
    *,
    owned_market_ref: Any,
    challenger_market_ref: Any,
    ownership_drop_reason: Any,
    ownership_replacement_reason: Any,
) -> Dict[str, Optional[str]]:
    def _norm_optional_text(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    return {
        EDGE_OWNED_MARKET_REF_FIELD: _norm_optional_text(owned_market_ref),
        EDGE_CHALLENGER_MARKET_REF_FIELD: _norm_optional_text(challenger_market_ref),
        EDGE_OWNERSHIP_DROP_REASON_FIELD: _norm_optional_text(ownership_drop_reason),
        EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD: _norm_optional_text(ownership_replacement_reason),
    }


def market_truth_surface_fields(*, market_truth_required: Any) -> Dict[str, bool]:
    return {
        EDGE_MARKET_TRUTH_REQUIRED_FIELD: bool(market_truth_required),
    }


def market_truth_required_from_payload(payload: Mapping[str, Any]) -> bool:
    if EDGE_MARKET_TRUTH_REQUIRED_FIELD in payload:
        return bool(payload.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD))
    return False


def lane_permission_surface_fields(
    *,
    maker_phase_allowed: Any,
    taker_phase_allowed: Any,
    maker_gate_open: Any,
    taker_gate_open: Any,
) -> Dict[str, bool]:
    return {
        EDGE_MAKER_PHASE_ALLOWED_FIELD: bool(maker_phase_allowed),
        EDGE_TAKER_PHASE_ALLOWED_FIELD: bool(taker_phase_allowed),
        EDGE_MAKER_GATE_OPEN_FIELD: bool(maker_gate_open),
        EDGE_TAKER_GATE_OPEN_FIELD: bool(taker_gate_open),
    }


def maker_phase_allowed_from_payload(payload: Mapping[str, Any]) -> bool:
    if EDGE_MAKER_PHASE_ALLOWED_FIELD in payload:
        return bool(payload.get(EDGE_MAKER_PHASE_ALLOWED_FIELD))
    lifecycle_phase = normalize_lifecycle_phase(payload.get(EDGE_LIFECYCLE_PHASE_FIELD))
    if not lifecycle_phase:
        lifecycle_phase = lifecycle_phase_from_payload(payload)
    if lifecycle_phase:
        return phase_allows_action(lifecycle_phase, EDGE_ACTION_MAKER)
    return False


def taker_phase_allowed_from_payload(payload: Mapping[str, Any]) -> bool:
    if EDGE_TAKER_PHASE_ALLOWED_FIELD in payload:
        return bool(payload.get(EDGE_TAKER_PHASE_ALLOWED_FIELD))
    lifecycle_phase = normalize_lifecycle_phase(payload.get(EDGE_LIFECYCLE_PHASE_FIELD))
    if not lifecycle_phase:
        lifecycle_phase = lifecycle_phase_from_payload(payload)
    if lifecycle_phase:
        return phase_allows_action(lifecycle_phase, EDGE_ACTION_TAKER)
    return False


def lifecycle_surface_fields(
    *,
    open_order_cleanup_required: Any,
    settlement_hold_required: Any,
    unresolved_lifecycle_obligation: Any,
    cancel_fail_closed: Any,
) -> Dict[str, Any]:
    return {
        EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD: bool(open_order_cleanup_required),
        EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD: bool(settlement_hold_required),
        EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD: bool(unresolved_lifecycle_obligation),
        EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD: bool(cancel_fail_closed),
    }


def lineage_stage_from_payload(payload: Mapping[str, Any]) -> str:
    lineage_hint = payload.get(EDGE_STAGE_LINEAGE_FIELD)
    return normalize_lineage_stage(lineage_hint)


def normalize_edge_scope(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_edge_action(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_block_reason(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_maker_gate_stage(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _MAKER_GATE_STAGE_SET else ""


def normalize_maker_gate_owner_family(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _MAKER_GATE_OWNER_SET else ""


def normalize_event_type(value: Any) -> str:
    return str(value or "").strip().lower()


def is_taker_decision_event_type(value: Any) -> bool:
    return normalize_event_type(value) in TAKER_DECISION_EVENT_TYPES


def is_taker_submit_event_type(value: Any) -> bool:
    return normalize_event_type(value) in TAKER_SUBMIT_EVENT_TYPES


def is_taker_event_type(value: Any) -> bool:
    return normalize_event_type(value) in TAKER_EVENT_TYPES


def is_taker_window_semantic_check_event_type(value: Any) -> bool:
    return normalize_event_type(value) in TAKER_WINDOW_SEMANTIC_CHECK_EVENT_TYPES


def canonicalize_taker_event_type(value: Any) -> str:
    normalized = normalize_event_type(value)
    if normalized in TAKER_DECISION_EVENT_TYPES:
        return EVENT_TAKER_DECISION
    if normalized in TAKER_SUBMIT_EVENT_TYPES:
        return EVENT_TAKER_SUBMIT
    if normalized in TAKER_WINDOW_SEMANTIC_CHECK_EVENT_TYPES:
        return EVENT_TAKER_WINDOW_SEMANTIC_CHECK
    return normalized


def normalize_submission_reason(value: Any) -> str:
    return str(value or "").strip().lower()


def is_taker_reason(value: Any) -> bool:
    reason = normalize_submission_reason(value)
    if not reason:
        return False
    return (
        reason == TAKER_CHAINLINK_REASON
        or reason.startswith("taker_bonus")
    )


def is_canonical_block_reason(value: Any) -> bool:
    reason = normalize_block_reason(value)
    if not reason:
        return False
    return reason in _EDGE_BLOCK_REASON_SET


def maker_gate_owner_family_for_stage(stage: Any) -> str:
    normalized_stage = normalize_maker_gate_stage(stage)
    if not normalized_stage:
        return ""
    return _MAKER_GATE_STAGE_OWNER_BY_STAGE.get(normalized_stage, "")


def _canonicalize_maker_gate_reason(value: Any) -> str:
    normalized = normalize_block_reason(value)
    if normalized == "insufficient_depth_multiple":
        return "launch_safe_selection_insufficient_depth_multiple"
    if normalized.startswith("submit_rejected_"):
        return normalized
    return normalized


def _maker_gate_reason_from_payload(payload: Mapping[str, Any]) -> str:
    placeholder_reasons = {"", "unknown", "unspecified", "none", "null"}
    saw_maker_no_submission = False
    for field_name in (
        EDGE_MAKER_GATE_REASON_FIELD,
        "runtime_decision_block_reason",
        "decision_block_reason",
        "primary_reject_reason",
        "selection_gate_primary_reject_reason",
        "block_reason",
    ):
        reason = _canonicalize_maker_gate_reason(payload.get(field_name))
        if reason and reason != "maker_no_submission":
            return reason
        if reason == "maker_no_submission":
            saw_maker_no_submission = True
            break
    for field_name in ("maker_no_submission_cause", "maker_no_submission_category"):
        reason = _canonicalize_maker_gate_reason(payload.get(field_name))
        if reason and reason not in placeholder_reasons:
            return reason
    if saw_maker_no_submission:
        return "maker_no_submission"
    return ""


def maker_gate_contract_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    explicit_stage = normalize_maker_gate_stage(payload.get(EDGE_MAKER_GATE_STAGE_FIELD))
    explicit_owner = normalize_maker_gate_owner_family(
        payload.get(EDGE_MAKER_GATE_OWNER_FAMILY_FIELD)
    )
    explicit_reason = _canonicalize_maker_gate_reason(payload.get(EDGE_MAKER_GATE_REASON_FIELD))
    if explicit_stage:
        owner_family = explicit_owner or maker_gate_owner_family_for_stage(explicit_stage)
        terminal_value = payload.get(EDGE_MAKER_GATE_TERMINAL_FIELD)
        terminal = bool(terminal_value) if terminal_value is not None else True
        return {
            EDGE_MAKER_GATE_STAGE_FIELD: explicit_stage,
            EDGE_MAKER_GATE_REASON_FIELD: explicit_reason or explicit_stage,
            EDGE_MAKER_GATE_OWNER_FAMILY_FIELD: owner_family or explicit_stage,
            EDGE_MAKER_GATE_TERMINAL_FIELD: bool(terminal),
        }

    event_type = normalize_event_type(payload.get("event_type"))
    submission_lane = str(payload.get("submission_lane") or "").strip().lower()
    filled_flag = bool(payload.get("filled"))
    if event_type == "fill" and submission_lane == EDGE_ACTION_MAKER:
        filled_flag = True
    if filled_flag:
        return {
            EDGE_MAKER_GATE_STAGE_FIELD: MAKER_GATE_STAGE_FILLED,
            EDGE_MAKER_GATE_REASON_FIELD: "filled",
            EDGE_MAKER_GATE_OWNER_FAMILY_FIELD: MAKER_GATE_OWNER_FILLED,
            EDGE_MAKER_GATE_TERMINAL_FIELD: True,
        }

    submitted_flag = bool(payload.get("submitted"))
    action_taken = normalize_edge_action(payload.get("action_taken"))
    decision_result = str(payload.get("decision_result") or "").strip().lower()
    if submission_lane == EDGE_ACTION_MAKER and event_type == "order_submit":
        submitted_flag = True
    if str(payload.get("order_submit_id") or "").strip():
        submitted_flag = True
    if decision_result == "submitted":
        submitted_flag = True
    if action_taken == EDGE_ACTION_MAKER and str(payload.get("order_id") or "").strip():
        submitted_flag = True
    if submitted_flag:
        return {
            EDGE_MAKER_GATE_STAGE_FIELD: MAKER_GATE_STAGE_SUBMITTED,
            EDGE_MAKER_GATE_REASON_FIELD: "submitted",
            EDGE_MAKER_GATE_OWNER_FAMILY_FIELD: MAKER_GATE_OWNER_SUBMITTED,
            EDGE_MAKER_GATE_TERMINAL_FIELD: True,
        }

    reason = _maker_gate_reason_from_payload(payload)
    if reason in _MAKER_GATE_PHASE_REASONS:
        stage = MAKER_GATE_STAGE_PHASE
    elif reason in _MAKER_GATE_TRUTH_REFERENCE_REASONS:
        stage = MAKER_GATE_STAGE_TRUTH_REFERENCE
    elif reason in _MAKER_GATE_LANE_READINESS_REASONS:
        stage = MAKER_GATE_STAGE_LANE_READINESS
    elif reason in _MAKER_GATE_SELECTION_REASONS:
        stage = MAKER_GATE_STAGE_SELECTION
    elif reason in _MAKER_GATE_FEASIBILITY_REASONS:
        stage = MAKER_GATE_STAGE_FEASIBILITY
    elif reason in _MAKER_GATE_SHARED_SAFETY_REASONS:
        stage = MAKER_GATE_STAGE_SHARED_SAFETY
    elif reason in _MAKER_GATE_QUOTE_QUALITY_REASONS:
        stage = MAKER_GATE_STAGE_QUOTE_QUALITY
    elif reason in _MAKER_GATE_SUBMIT_PATH_REASONS:
        stage = MAKER_GATE_STAGE_SUBMIT_PATH
    else:
        stage = MAKER_GATE_STAGE_SUBMIT_PATH if reason else ""
    owner_family = maker_gate_owner_family_for_stage(stage)
    return {
        EDGE_MAKER_GATE_STAGE_FIELD: stage or None,
        EDGE_MAKER_GATE_REASON_FIELD: reason or None,
        EDGE_MAKER_GATE_OWNER_FAMILY_FIELD: owner_family or None,
        EDGE_MAKER_GATE_TERMINAL_FIELD: bool(stage),
    }


def _parse_prob(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    if out < 0.0 or out > 1.0:
        return None
    return out


def _parse_nonnegative(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    if out < 0.0:
        return None
    return out


@dataclass(frozen=True)
class EdgeInputSnapshot:
    fair_probability: Optional[float] = None
    market_probability: Optional[float] = None
    time_remaining_sec: Optional[float] = None
    oracle_tick_age_sec: Optional[float] = None
    lifecycle_phase: Optional[str] = None
    lineage_stage: Optional[str] = None
    evaluation_scope: str = ""


@dataclass(frozen=True)
class EdgeInputValidation:
    valid: bool
    reason_code: str
    reason_detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valid": bool(self.valid),
            "reason_code": str(self.reason_code or ""),
            "reason_detail": str(self.reason_detail or ""),
        }


def _invalid(reason_code: str, reason_detail: str = "") -> EdgeInputValidation:
    return EdgeInputValidation(valid=False, reason_code=reason_code, reason_detail=reason_detail)


def _valid() -> EdgeInputValidation:
    return EdgeInputValidation(valid=True, reason_code="ok", reason_detail="")


def phase_policy(lifecycle_phase: Any) -> Tuple[bool, bool]:
    normalized_phase = normalize_lifecycle_phase(lifecycle_phase)
    return CANONICAL_LIFECYCLE_PHASE_POLICY.get(normalized_phase, (False, False))


def phase_allows_action(lifecycle_phase: Any, action: Any) -> bool:
    normalized_action = normalize_edge_action(action)
    if normalized_action == EDGE_ACTION_NONE:
        return True
    maker_phase_allowed, taker_phase_allowed = phase_policy(lifecycle_phase)
    if normalized_action == EDGE_ACTION_MAKER:
        return bool(maker_phase_allowed)
    if normalized_action == EDGE_ACTION_TAKER:
        return bool(taker_phase_allowed)
    return False


def compute_edge_value(*, fair_probability: Any, market_probability: Any) -> Optional[float]:
    fair = _parse_prob(fair_probability)
    market = _parse_prob(market_probability)
    if fair is None or market is None:
        return None
    out = fair - market
    if not math.isfinite(out):
        return None
    return float(out)


def validate_edge_inputs(
    snapshot: EdgeInputSnapshot,
    *,
    oracle_max_tick_age_sec: float,
) -> EdgeInputValidation:
    scope = normalize_edge_scope(snapshot.evaluation_scope)
    if scope not in EDGE_EVAL_SCOPES:
        return _invalid("edge_scope_invalid", f"scope={scope or 'missing'}")

    lifecycle_phase = normalize_lifecycle_phase(snapshot.lifecycle_phase)
    if not lifecycle_phase:
        return _invalid("phase_missing")

    fair = _parse_prob(snapshot.fair_probability)
    if snapshot.fair_probability is None:
        return _invalid("fair_probability_missing")
    if fair is None:
        return _invalid("fair_probability_invalid")

    market = _parse_prob(snapshot.market_probability)
    if snapshot.market_probability is None:
        return _invalid("market_probability_missing")
    if market is None:
        return _invalid("market_probability_invalid")

    time_remaining = _parse_nonnegative(snapshot.time_remaining_sec)
    if snapshot.time_remaining_sec is None:
        return _invalid("time_remaining_sec_missing")
    if time_remaining is None:
        return _invalid("time_remaining_sec_invalid")

    oracle_tick_age = _parse_nonnegative(snapshot.oracle_tick_age_sec)
    if snapshot.oracle_tick_age_sec is None:
        return _invalid("oracle_tick_age_sec_missing")
    if oracle_tick_age is None:
        return _invalid("oracle_tick_age_sec_invalid")
    if oracle_tick_age > float(max(0.0, oracle_max_tick_age_sec)):
        return _invalid(
            "oracle_tick_stale",
            f"tick_age_sec={oracle_tick_age:.6f}>max={float(max(0.0, oracle_max_tick_age_sec)):.6f}",
        )

    edge_value = compute_edge_value(fair_probability=fair, market_probability=market)
    if edge_value is None:
        return _invalid("edge_value_invalid")

    return _valid()
