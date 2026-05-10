from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

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
EVENT_TAKER_STAGE_WINDOW_SEMANTIC_CHECK = "taker_stage_window_semantic_check"
TAKER_DECISION_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_DECISION,
)
TAKER_SUBMIT_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_SUBMIT,
)
TAKER_STAGE_WINDOW_SEMANTIC_CHECK_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_STAGE_WINDOW_SEMANTIC_CHECK,
)
TAKER_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TAKER_DECISION,
    EVENT_TAKER_SUBMIT,
    EVENT_TAKER_STAGE_WINDOW_SEMANTIC_CHECK,
)

TAKER_CHAINLINK_REASON = "taker_chainlink"

EDGE_INPUT_REASON_CODES: Tuple[str, ...] = (
    "edge_scope_invalid",
    "stage_missing",
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
    "stage_disallow_maker",
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
    "stage_disallow_taker",
    "taker_requires_ws_book_source",
    "edge_below_min",
    "taker_token_cooldown",
    "token_score_below_taker_min",
    "taker_order_budget_exhausted",
    "taker_outside_final_window",
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
    "maker_to_taker_recovery_handoff_disabled",
    "taker_recovery_disabled_in_taker_scope",
    "reduce_only_recovery_size_cap_below_min_order_size",
    "reduce_only_recovery_no_reducing_side",
    "reduce_only_recovery_waiting_for_maker_exit",
    "reduce_only_recovery_size_cap_unavailable",
    "reduce_only_recovery_touch_price_unavailable",
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
STAGE_EXTREME_ONLY = "EXTREME_ONLY"
STAGE_EXPIRED = "EXPIRED"
STAGE_UNKNOWN = "UNKNOWN"
EDGE_STAGE_EFFECTIVE_FIELD = "effective_stage"
EDGE_STAGE_BUCKET_FIELD = "stage_bucket"
EDGE_AUTH_MAKER_NEW_RISK_FIELD = "maker_new_risk_allowed"
EDGE_AUTH_NORMAL_TAKER_FIELD = "normal_taker_allowed"
EDGE_AUTH_REDUCE_ONLY_RECOVERY_FIELD = "reduce_only_recovery_allowed"
EDGE_AUTH_PREEXPIRY_EMERGENCY_TAKER_FIELD = "preexpiry_emergency_taker_allowed"
EDGE_LATE_WINDOW_AUTHORITY_CLASS_FIELD = "late_window_authority_class"

CANONICAL_EDGE_STAGE_POLICY: Dict[str, Tuple[bool, bool]] = {
    STAGE_OBSERVE: (False, False),
    STAGE_EVALUATE: (False, False),
    STAGE_MAKER_POSITION: (True, False),
    STAGE_MAKER_TAKER_SELECTIVE: (True, False),
    STAGE_SNIPER_PRIMARY: (False, False),
    STAGE_EXTREME_ONLY: (False, False),
    STAGE_EXPIRED: (False, False),
    STAGE_UNKNOWN: (False, False),
}


def normalize_stage_name(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized or STAGE_UNKNOWN


def stage_surface_fields(*, effective_stage: Any, stage_bucket: Any) -> Dict[str, str]:
    normalized_effective = normalize_stage_name(effective_stage)
    normalized_bucket = normalize_stage_name(stage_bucket)
    return {
        EDGE_STAGE_EFFECTIVE_FIELD: normalized_effective,
        EDGE_STAGE_BUCKET_FIELD: normalized_bucket,
        "stage": normalized_effective,
        "raw_stage": normalized_bucket,
    }


def authority_surface_fields(
    *,
    maker_new_risk_allowed: Any,
    normal_taker_allowed: Any,
    reduce_only_recovery_allowed: Any,
    preexpiry_emergency_taker_allowed: Any,
    late_window_authority_class: Any,
) -> Dict[str, Any]:
    return {
        EDGE_AUTH_MAKER_NEW_RISK_FIELD: bool(maker_new_risk_allowed),
        EDGE_AUTH_NORMAL_TAKER_FIELD: bool(normal_taker_allowed),
        EDGE_AUTH_REDUCE_ONLY_RECOVERY_FIELD: bool(reduce_only_recovery_allowed),
        EDGE_AUTH_PREEXPIRY_EMERGENCY_TAKER_FIELD: bool(preexpiry_emergency_taker_allowed),
        EDGE_LATE_WINDOW_AUTHORITY_CLASS_FIELD: str(late_window_authority_class or "").strip().lower() or "unknown",
    }


def effective_stage_from_payload(payload: Mapping[str, Any]) -> str:
    return normalize_stage_name(payload.get(EDGE_STAGE_EFFECTIVE_FIELD) or payload.get("stage"))


def stage_bucket_from_payload(payload: Mapping[str, Any]) -> str:
    bucket = payload.get(EDGE_STAGE_BUCKET_FIELD)
    if bucket is None or not str(bucket).strip():
        bucket = payload.get("raw_stage")
    return normalize_stage_name(bucket)


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


def is_taker_stage_window_semantic_check_event_type(value: Any) -> bool:
    return normalize_event_type(value) in TAKER_STAGE_WINDOW_SEMANTIC_CHECK_EVENT_TYPES


def canonicalize_taker_event_type(value: Any) -> str:
    normalized = normalize_event_type(value)
    if normalized in TAKER_DECISION_EVENT_TYPES:
        return EVENT_TAKER_DECISION
    if normalized in TAKER_SUBMIT_EVENT_TYPES:
        return EVENT_TAKER_SUBMIT
    if normalized in TAKER_STAGE_WINDOW_SEMANTIC_CHECK_EVENT_TYPES:
        return EVENT_TAKER_STAGE_WINDOW_SEMANTIC_CHECK
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
    fair_probability: Optional[float]
    market_probability: Optional[float]
    time_remaining_sec: Optional[float]
    oracle_tick_age_sec: Optional[float]
    latency_state: Optional[str]
    stage: Optional[str]
    evaluation_scope: str


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


def stage_policy(stage: Any) -> Tuple[bool, bool]:
    key = str(stage or "").strip().upper()
    return CANONICAL_EDGE_STAGE_POLICY.get(key, (False, False))


def stage_allows_action(stage: Any, action: Any) -> bool:
    normalized_action = normalize_edge_action(action)
    if normalized_action == EDGE_ACTION_NONE:
        return True
    allow_maker, allow_taker = stage_policy(stage)
    if normalized_action == EDGE_ACTION_MAKER:
        return bool(allow_maker)
    if normalized_action == EDGE_ACTION_TAKER:
        return bool(allow_taker)
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

    stage = str(snapshot.stage or "").strip().upper()
    if not stage:
        return _invalid("stage_missing")

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
