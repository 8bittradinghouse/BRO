from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

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
    "sniper_disabled",
    "sniper_taker_disabled",
    "taker_budget_disabled",
    "operating_mode_maker_only",
    "operating_mode_safe_stop",
    "operating_mode_non_normal",
    "latency_not_armed",
    "ramp_sniper_disabled",
    "token_lag_not_verified",
    "stage_disallow_taker",
    "taker_requires_ws_book_source",
    "edge_below_min",
    "taker_token_cooldown",
    "token_score_below_taker_min",
    "taker_order_budget_exhausted",
    "taker_price_unavailable",
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

CANONICAL_EDGE_STAGE_POLICY: Dict[str, Tuple[bool, bool]] = {
    STAGE_OBSERVE: (False, False),
    STAGE_EVALUATE: (False, False),
    STAGE_MAKER_POSITION: (True, False),
    STAGE_MAKER_TAKER_SELECTIVE: (True, True),
    STAGE_SNIPER_PRIMARY: (False, True),
    STAGE_EXTREME_ONLY: (False, True),
    STAGE_EXPIRED: (False, False),
    STAGE_UNKNOWN: (False, False),
}


def normalize_edge_scope(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_latency_state(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_edge_action(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_block_reason(value: Any) -> str:
    return str(value or "").strip().lower()


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
    except Exception:
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
    except Exception:
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
