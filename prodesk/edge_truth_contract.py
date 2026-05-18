from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .lineage_stage import normalize_lineage_stage

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
    "latency_state_missing",
    "latency_state_invalid",
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
    "latency_not_armed_for_maker",
    "token_lag_not_verified_for_maker",
    "token_score_below_maker_min",
    "fair_probability_unavailable",
    "maker_timing_gate_closed",
    "taker_disabled",
    "taker_budget_disabled",
    "operating_mode_maker_only",
    "operating_mode_safe_stop",
    "operating_mode_non_normal",
    "latency_not_armed",
    "ramp_taker_disabled",
    "token_lag_not_verified",
    "normal_taker_authority_closed",
    "phase_disallow_taker",
    "taker_requires_ws_book_source",
    "edge_below_min",
    "taker_token_cooldown",
    "token_score_below_taker_min",
    "taker_order_budget_exhausted",
    "taker_outside_final_window",
    "taker_window_already_submitted",
    "taker_hard_min_notional_unachievable",
    "taker_dynamic_size_capped_by_risk",
    "taker_visible_fill_ratio_below_min",
    "taker_price_unavailable",
    "taker_competitiveness_disabled",
    "normal_taker_same_token_sell_forbidden",
    "complement_route_disabled_pending_validation",
    "complement_token_mapping_unavailable",
    "complement_token_fair_probability_unavailable",
    "complement_token_price_unavailable",
    "open_order_cleanup_required",
    "settlement_hold_required",
    "taker_submit_rejected",
)

EDGE_BLOCK_REASONS: Tuple[str, ...] = tuple(
    sorted(set(EDGE_INPUT_REASON_CODES).union(set(EDGE_EXECUTION_BLOCK_REASONS)))
)
_EDGE_BLOCK_REASON_SET = {item.lower() for item in EDGE_BLOCK_REASONS}

LATENCY_STATE_DISARMED = "disarmed"
LATENCY_STATE_PROBATION = "probation"
LATENCY_STATE_ARMED = "armed"
LATENCY_STATES: Tuple[str, ...] = (
    LATENCY_STATE_DISARMED,
    LATENCY_STATE_PROBATION,
    LATENCY_STATE_ARMED,
)

STAGE_OBSERVE = "OBSERVE"
STAGE_EVALUATE = "EVALUATE"
STAGE_MAKER_POSITION = "MAKER_POSITION"
STAGE_MAKER_TAKER_SELECTIVE = "MAKER_TAKER_SELECTIVE"
STAGE_SNIPER_PRIMARY = "SNIPER_PRIMARY"
STAGE_LATE_DIAGNOSTIC = "LATE_DIAGNOSTIC"
STAGE_MAKER_LATE_WINDOW = "MAKER_LATE_WINDOW"
STAGE_TAKER_COMMITMENT = "TAKER_COMMITMENT"
STAGE_EXTREME_ONLY = "EXTREME_ONLY"
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
EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD = "open_order_cleanup_required"
EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD = "settlement_hold_required"
EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD = "unresolved_lifecycle_obligation"
EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD = "cancel_fail_closed"
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


def normalize_latency_state(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_edge_action(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_block_reason(value: Any) -> str:
    return str(value or "").strip().lower()


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
    latency_state: Optional[str] = None
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
    require_latency_state: bool,
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

    latency_state = normalize_latency_state(snapshot.latency_state)
    if require_latency_state and not latency_state:
        return _invalid("latency_state_missing")
    if latency_state and latency_state not in LATENCY_STATES:
        return _invalid("latency_state_invalid", f"latency_state={latency_state}")

    edge_value = compute_edge_value(fair_probability=fair, market_probability=market)
    if edge_value is None:
        return _invalid("edge_value_invalid")

    return _valid()
