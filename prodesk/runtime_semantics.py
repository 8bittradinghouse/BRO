from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .common import parse_float, parse_ts

RUNTIME_STATE_ACTIVE = "active"
RUNTIME_STATE_NO_TARGET_STANDDOWN = "no_target_standdown"
RUNTIME_STATE_SAFETY_HALT = "safety_halt"

RUNTIME_CLASS_VALID_ACTIVE = "VALID_ACTIVE"
RUNTIME_CLASS_VALID_STANDDOWN = "VALID_STANDDOWN"
RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION = "NON_PROMOTABLE_NO_PARTICIPATION"
RUNTIME_CLASS_INVALID_DEADLOCK = "INVALID_DEADLOCK"
RUNTIME_CLASS_INVALID_SAFETY = "INVALID_SAFETY"


SUPPRESSION_CAUSE_NONE = "none"
SUPPRESSION_CAUSE_STANDDOWN_ORDER_SUBMISSION_VIOLATION = "standdown_order_submission_violation"
SUPPRESSION_CAUSE_STANDDOWN_BOOK_FEED_REQUIREMENT_VIOLATION = "standdown_book_feed_requirement_violation"
SUPPRESSION_CAUSE_SUSTAINED_NO_TARGET_WITH_GUARD_OR_KILL = "sustained_no_target_with_guard_or_kill"
SUPPRESSION_CAUSE_SAFETY_KILL_SWITCH_OR_EXTERNAL_GUARD = "safety_kill_switch_or_external_guard"
SUPPRESSION_CAUSE_SAFETY_REQUIRED_BOOK_FEED_DISCONNECTED = "safety_required_book_feed_disconnected"
SUPPRESSION_CAUSE_ACTIVE_TARGET_SAFETY_VIOLATION = "active_target_safety_violation"
SUPPRESSION_CAUSE_ACTIVE_TARGETS_WITHOUT_MEANINGFUL_PARTICIPATION = "active_targets_without_meaningful_participation"
SUPPRESSION_CAUSE_STANDDOWN_DURATION_NON_PROMOTABLE = "standdown_duration_non_promotable"
SUPPRESSION_CAUSE_STATUS_ROWS_MISSING = "status_rows_missing"
SUPPRESSION_CAUSE_RUNTIME_STATE_AMBIGUOUS = "runtime_state_ambiguous"


def _resolve_suppression_causes(candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [row for row in candidates if isinstance(row, dict) and str(row.get("cause") or "").strip()]
    if not rows:
        return {
            "primary_suppression_cause": SUPPRESSION_CAUSE_NONE,
            "contributing_suppression_causes": [],
            "ambiguous_suppression_cause": False,
            "suppression_cause_candidates": [],
            "suppression_cause_precedence": [],
        }
    normalized = sorted(
        (
            {
                "cause": str(row.get("cause") or "").strip(),
                "priority": int(row.get("priority", 9999)),
            }
            for row in rows
            if str(row.get("cause") or "").strip()
        ),
        key=lambda row: (int(row["priority"]), str(row["cause"])),
    )
    if not normalized:
        return {
            "primary_suppression_cause": SUPPRESSION_CAUSE_NONE,
            "contributing_suppression_causes": [],
            "ambiguous_suppression_cause": False,
            "suppression_cause_candidates": [],
            "suppression_cause_precedence": [],
        }
    min_priority = int(normalized[0]["priority"])
    tied_top = sorted({str(row["cause"]) for row in normalized if int(row["priority"]) == min_priority})
    all_causes = sorted({str(row["cause"]) for row in normalized})
    if len(tied_top) > 1:
        return {
            "primary_suppression_cause": "",
            "contributing_suppression_causes": all_causes,
            "ambiguous_suppression_cause": True,
            "suppression_cause_candidates": all_causes,
            "suppression_cause_precedence": normalized,
        }
    primary = tied_top[0]
    contributing = [cause for cause in all_causes if cause != primary]
    return {
        "primary_suppression_cause": primary,
        "contributing_suppression_causes": contributing,
        "ambiguous_suppression_cause": False,
        "suppression_cause_candidates": all_causes,
        "suppression_cause_precedence": normalized,
    }


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


def _as_nonempty_text(value: Any) -> str:
    text = str(value or "").strip()
    return text


def _status_external_guard_active(status_row: Dict[str, Any]) -> bool:
    explicit = _as_bool(status_row.get("external_guard_active"))
    if explicit is not None:
        return bool(explicit)
    guard_obj = status_row.get("external_guard")
    if isinstance(guard_obj, dict):
        embedded = _as_bool(guard_obj.get("active"))
        if embedded is not None:
            return bool(embedded)
    return False


def _status_target_count(status_row: Dict[str, Any]) -> float:
    for key in ("target_count", "gauge.target_count"):
        value = parse_float(status_row.get(key))
        if value is not None:
            return float(value)
    return 0.0


def _status_active_targets_present(status_row: Dict[str, Any]) -> bool:
    explicit = _as_bool(status_row.get("active_targets_present"))
    if explicit is not None:
        return bool(explicit)
    if _status_target_count(status_row) > 0.0:
        return True
    for key in (
        "gauge.open_orders",
        "gauge.quote_active",
        "gauge.actions_last_cycle",
        "gauge.order_submission_attempts_last_cycle",
    ):
        value = parse_float(status_row.get(key))
        if value is not None and value > 0.0:
            return True
    return False


def _status_runtime_state(status_row: Dict[str, Any]) -> str:
    text = _as_nonempty_text(status_row.get("runtime_state")).lower()
    if text:
        return text
    if _as_bool(status_row.get("kill_switch")):
        return RUNTIME_STATE_SAFETY_HALT
    if _status_active_targets_present(status_row):
        return RUNTIME_STATE_ACTIVE
    return RUNTIME_STATE_NO_TARGET_STANDDOWN


def _status_no_target_standdown(status_row: Dict[str, Any]) -> bool:
    explicit = _as_bool(status_row.get("no_target_standdown"))
    if explicit is not None:
        return bool(explicit)
    return _status_runtime_state(status_row) == RUNTIME_STATE_NO_TARGET_STANDDOWN


def _status_book_feed_required(
    status_row: Dict[str, Any],
    *,
    configured_default_required: bool,
) -> bool:
    explicit = _as_bool(status_row.get("book_feed_required"))
    if explicit is not None:
        return bool(explicit)
    if not configured_default_required:
        return False
    if _status_no_target_standdown(status_row):
        return False
    return _status_active_targets_present(status_row)


def runtime_state_from_cycle(*, has_targets: bool, kill_switch: bool) -> str:
    if bool(kill_switch):
        return RUNTIME_STATE_SAFETY_HALT
    if bool(has_targets):
        return RUNTIME_STATE_ACTIVE
    return RUNTIME_STATE_NO_TARGET_STANDDOWN


def runtime_state_to_gauge(runtime_state: str) -> float:
    mapping = {
        RUNTIME_STATE_ACTIVE: 1.0,
        RUNTIME_STATE_NO_TARGET_STANDDOWN: 2.0,
        RUNTIME_STATE_SAFETY_HALT: 3.0,
    }
    return float(mapping.get(str(runtime_state).strip().lower(), 0.0))


def resolve_guard_connectivity_requirements(
    *,
    status_row: Optional[Dict[str, Any]],
    require_book_feed_connected_config: bool,
) -> Dict[str, Any]:
    if status_row is None:
        return {
            "runtime_state": "",
            "active_targets_present": False,
            "no_target_standdown": False,
            "book_feed_required": bool(require_book_feed_connected_config),
        }
    runtime_state = _status_runtime_state(status_row)
    active_targets_present = _status_active_targets_present(status_row)
    no_target_standdown = _status_no_target_standdown(status_row)
    book_feed_required = _status_book_feed_required(
        status_row,
        configured_default_required=bool(require_book_feed_connected_config),
    )
    return {
        "runtime_state": runtime_state,
        "active_targets_present": bool(active_targets_present),
        "no_target_standdown": bool(no_target_standdown),
        "book_feed_required": bool(book_feed_required),
    }


def _status_book_feed_connected(status_row: Dict[str, Any]) -> Optional[bool]:
    book_feed = status_row.get("book_feed")
    if isinstance(book_feed, dict):
        value = _as_bool(book_feed.get("connected"))
        if value is not None:
            return bool(value)
    return None


def _status_book_feed_age_sec(status_row: Dict[str, Any]) -> Optional[float]:
    book_feed = status_row.get("book_feed")
    if not isinstance(book_feed, dict):
        return None
    value = parse_float(book_feed.get("last_msg_age_sec"))
    if value is None:
        return None
    return float(value)


def _status_cycle_counter(row: Dict[str, Any]) -> float:
    for key in ("counter.cycles", "cycles"):
        value = parse_float(row.get(key))
        if value is not None:
            return float(value)
    return 0.0


def _status_order_submit_attempts(row: Dict[str, Any]) -> float:
    for key in ("order_submission_attempts_last_cycle", "gauge.order_submission_attempts_last_cycle"):
        value = parse_float(row.get(key))
        if value is not None:
            return max(0.0, float(value))
    return 0.0


def _status_duration_minutes(status_rows: Sequence[Dict[str, Any]]) -> float:
    ts_values: List[dt.datetime] = []
    for row in status_rows:
        ts = parse_ts(row.get("ts_utc"))
        if ts is not None:
            ts_values.append(ts)
    if len(ts_values) < 2:
        return 0.0
    return max(0.0, (max(ts_values) - min(ts_values)).total_seconds() / 60.0)


def classify_runtime(
    *,
    status_rows: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    standdown_non_promotable_min_minutes: float = 15.0,
    deadlock_min_rows: int = 3,
    max_required_book_feed_age_sec: float = 12.0,
) -> Dict[str, Any]:
    rows = [row for row in status_rows if isinstance(row, dict)]
    event_rows = [row for row in events if isinstance(row, dict)]
    if not rows:
        return {
            "classification": RUNTIME_CLASS_INVALID_DEADLOCK,
            "promotion_eligible": False,
            "reasons": ["status_rows_missing"],
            "primary_suppression_cause": SUPPRESSION_CAUSE_STATUS_ROWS_MISSING,
            "contributing_suppression_causes": [],
            "ambiguous_suppression_cause": False,
            "suppression_cause_candidates": [SUPPRESSION_CAUSE_STATUS_ROWS_MISSING],
            "suppression_cause_precedence": [
                {"cause": SUPPRESSION_CAUSE_STATUS_ROWS_MISSING, "priority": 5}
            ],
            "metrics": {
                "status_rows": 0.0,
                "active_target_rows": 0.0,
                "standdown_rows": 0.0,
                "deadlock_rows": 0.0,
                "safety_rows": 0.0,
                "required_book_feed_rows": 0.0,
                "required_book_feed_disconnected_rows": 0.0,
                "standdown_order_submission_violation_rows": 0.0,
                "standdown_book_feed_required_violation_rows": 0.0,
                "duration_minutes": 0.0,
                "decision_events": 0.0,
                "max_cycles_counter": 0.0,
                "order_submission_attempt_rows": 0.0,
                "participation_rows": 0.0,
                "active_targets_seen": 0.0,
                "meaningful_participation": 0.0,
            },
        }

    active_target_rows = 0
    standdown_rows = 0
    deadlock_rows = 0
    safety_rows = 0
    required_book_feed_rows = 0
    required_book_feed_disconnected_rows = 0
    order_submission_attempt_rows = 0
    standdown_order_submission_violation_rows = 0
    standdown_book_feed_required_violation_rows = 0
    participation_rows = 0
    max_cycles_counter = 0.0
    active_feed_rows = 0
    unknown_active_feed_rows = 0
    unknown_active_feed_streak = 0
    max_unknown_active_feed_streak = 0
    active_target_guard_or_kill_rows = 0
    active_target_required_feed_disconnected_rows = 0

    for row in rows:
        runtime_state = _status_runtime_state(row)
        has_targets = _status_active_targets_present(row)
        is_standdown = _status_no_target_standdown(row)
        kill_switch = bool(_as_bool(row.get("kill_switch")))
        external_guard = _status_external_guard_active(row)
        book_feed_required = _status_book_feed_required(
            row,
            configured_default_required=True,
        )
        book_connected = _status_book_feed_connected(row)
        book_age = _status_book_feed_age_sec(row)
        order_submit_attempts = _status_order_submit_attempts(row)
        cycle_counter = _status_cycle_counter(row)
        if cycle_counter > max_cycles_counter:
            max_cycles_counter = cycle_counter

        if has_targets:
            active_target_rows += 1
        if is_standdown or runtime_state == RUNTIME_STATE_NO_TARGET_STANDDOWN:
            standdown_rows += 1
        if order_submit_attempts > 0:
            order_submission_attempt_rows += 1
        open_orders = parse_float(row.get("gauge.open_orders"))
        quote_active = parse_float(row.get("gauge.quote_active"))
        actions_last_cycle = parse_float(row.get("gauge.actions_last_cycle"))
        if (
            (open_orders is not None and open_orders > 0.0)
            or (quote_active is not None and quote_active > 0.0)
            or (actions_last_cycle is not None and actions_last_cycle > 0.0)
            or order_submit_attempts > 0.0
        ):
            participation_rows += 1

        if is_standdown:
            if order_submit_attempts > 0.0:
                standdown_order_submission_violation_rows += 1
            if book_feed_required:
                standdown_book_feed_required_violation_rows += 1

        if book_feed_required:
            required_book_feed_rows += 1
            unknown_age_disconnect = False
            disconnected = False
            if book_connected is False:
                if book_age is None:
                    disconnected = True
                    unknown_age_disconnect = True
                elif book_age >= float(max_required_book_feed_age_sec):
                    disconnected = True
            elif book_age is not None and book_age > float(max_required_book_feed_age_sec):
                disconnected = True
            if disconnected:
                required_book_feed_disconnected_rows += 1
                if has_targets:
                    active_feed_rows += 1
                    if unknown_age_disconnect:
                        unknown_active_feed_rows += 1
        if has_targets and book_feed_required and (book_connected is False) and (book_age is None):
            unknown_active_feed_streak += 1
            if unknown_active_feed_streak > max_unknown_active_feed_streak:
                max_unknown_active_feed_streak = unknown_active_feed_streak
        else:
            unknown_active_feed_streak = 0

        if is_standdown and (kill_switch or external_guard):
            deadlock_rows += 1
        if has_targets and (kill_switch or external_guard):
            safety_rows += 1
            active_target_guard_or_kill_rows += 1
        if has_targets and book_feed_required and (
            (book_connected is False)
            and (book_age is not None and book_age >= float(max_required_book_feed_age_sec))
        ):
            safety_rows += 1
            active_target_required_feed_disconnected_rows += 1
        if has_targets and book_feed_required and (
            (book_connected is None)
            and (book_age is not None and book_age > float(max_required_book_feed_age_sec))
        ):
            safety_rows += 1
            active_target_required_feed_disconnected_rows += 1

    if max_unknown_active_feed_streak >= max(1, int(deadlock_min_rows)):
        safety_rows += max_unknown_active_feed_streak
        active_target_required_feed_disconnected_rows += max_unknown_active_feed_streak

    decision_events = 0
    for evt in event_rows:
        event_type = _as_nonempty_text(evt.get("event_type"))
        if event_type in {
            "order_submit",
            "risk_reject",
            "fill",
            "sniper_taker_submit",
        }:
            decision_events += 1

    duration_minutes = _status_duration_minutes(rows)
    active_targets_seen = active_target_rows > 0
    meaningful_participation = bool(
        active_targets_seen
        and (
            decision_events > 0
            or participation_rows > 0
            or order_submission_attempt_rows > 0
        )
    )

    reasons: List[str] = []
    if standdown_order_submission_violation_rows > 0:
        reasons.append("standdown_order_submission_violation")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False
    elif standdown_book_feed_required_violation_rows > 0:
        reasons.append("standdown_book_feed_requirement_violation")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False
    elif deadlock_rows >= max(1, int(deadlock_min_rows)):
        reasons.append("sustained_no_target_with_guard_or_kill")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False
    elif safety_rows > 0:
        reasons.append("active_target_safety_violation")
        classification = RUNTIME_CLASS_INVALID_SAFETY
        promotion_eligible = False
    elif active_targets_seen:
        if meaningful_participation:
            reasons.append("active_targets_with_participation")
            classification = RUNTIME_CLASS_VALID_ACTIVE
            promotion_eligible = True
        else:
            reasons.append("active_targets_without_meaningful_participation")
            classification = RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION
            promotion_eligible = False
    elif standdown_rows == len(rows):
        reasons.append("doctrine_no_target_standdown")
        if duration_minutes >= float(standdown_non_promotable_min_minutes):
            reasons.append("standdown_duration_non_promotable")
            classification = RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION
            promotion_eligible = False
        else:
            classification = RUNTIME_CLASS_VALID_STANDDOWN
            promotion_eligible = False
    else:
        reasons.append("runtime_state_ambiguous")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False

    suppression_candidates: List[Dict[str, Any]] = []
    if standdown_order_submission_violation_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_STANDDOWN_ORDER_SUBMISSION_VIOLATION, "priority": 10}
        )
    if standdown_book_feed_required_violation_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_STANDDOWN_BOOK_FEED_REQUIREMENT_VIOLATION, "priority": 15}
        )
    if deadlock_rows >= max(1, int(deadlock_min_rows)):
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SUSTAINED_NO_TARGET_WITH_GUARD_OR_KILL, "priority": 20}
        )
    if active_target_guard_or_kill_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SAFETY_KILL_SWITCH_OR_EXTERNAL_GUARD, "priority": 30}
        )
    if active_target_required_feed_disconnected_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SAFETY_REQUIRED_BOOK_FEED_DISCONNECTED, "priority": 30}
        )
    if safety_rows > 0:
        suppression_candidates.append({"cause": SUPPRESSION_CAUSE_ACTIVE_TARGET_SAFETY_VIOLATION, "priority": 35})
    if active_targets_seen and not meaningful_participation:
        suppression_candidates.append(
            {
                "cause": SUPPRESSION_CAUSE_ACTIVE_TARGETS_WITHOUT_MEANINGFUL_PARTICIPATION,
                "priority": 40,
            }
        )
    if standdown_rows == len(rows) and duration_minutes >= float(standdown_non_promotable_min_minutes):
        suppression_candidates.append(
            {
                "cause": SUPPRESSION_CAUSE_STANDDOWN_DURATION_NON_PROMOTABLE,
                "priority": 50,
            }
        )
    if "runtime_state_ambiguous" in reasons:
        suppression_candidates.append(
            {
                "cause": SUPPRESSION_CAUSE_RUNTIME_STATE_AMBIGUOUS,
                "priority": 60,
            }
        )
    suppression_surface = _resolve_suppression_causes(suppression_candidates)

    return {
        "classification": classification,
        "promotion_eligible": bool(promotion_eligible),
        "reasons": reasons,
        "primary_suppression_cause": str(suppression_surface.get("primary_suppression_cause", SUPPRESSION_CAUSE_NONE)),
        "contributing_suppression_causes": list(suppression_surface.get("contributing_suppression_causes") or []),
        "ambiguous_suppression_cause": bool(suppression_surface.get("ambiguous_suppression_cause", False)),
        "suppression_cause_candidates": list(suppression_surface.get("suppression_cause_candidates") or []),
        "suppression_cause_precedence": list(suppression_surface.get("suppression_cause_precedence") or []),
        "metrics": {
            "status_rows": float(len(rows)),
            "active_target_rows": float(active_target_rows),
            "standdown_rows": float(standdown_rows),
            "deadlock_rows": float(deadlock_rows),
            "safety_rows": float(safety_rows),
            "required_book_feed_rows": float(required_book_feed_rows),
            "required_book_feed_disconnected_rows": float(required_book_feed_disconnected_rows),
            "required_book_feed_disconnected_active_target_rows": float(active_feed_rows),
            "required_book_feed_disconnected_unknown_age_rows": float(unknown_active_feed_rows),
            "required_book_feed_disconnected_unknown_age_max_streak": float(max_unknown_active_feed_streak),
            "active_target_guard_or_kill_rows": float(active_target_guard_or_kill_rows),
            "active_target_required_feed_disconnected_rows": float(active_target_required_feed_disconnected_rows),
            "standdown_order_submission_violation_rows": float(standdown_order_submission_violation_rows),
            "standdown_book_feed_required_violation_rows": float(standdown_book_feed_required_violation_rows),
            "duration_minutes": float(duration_minutes),
            "decision_events": float(decision_events),
            "max_cycles_counter": float(max_cycles_counter),
            "order_submission_attempt_rows": float(order_submission_attempt_rows),
            "participation_rows": float(participation_rows),
            "active_targets_seen": 1.0 if active_targets_seen else 0.0,
            "meaningful_participation": 1.0 if meaningful_participation else 0.0,
        },
    }


@dataclass(frozen=True)
class RuntimeCycleSemantics:
    runtime_state: str
    active_targets_present: bool
    no_target_standdown: bool
    book_feed_required: bool
    promotion_eligibility_hint: bool


def cycle_semantics(*, has_targets: bool, kill_switch: bool) -> RuntimeCycleSemantics:
    runtime_state = runtime_state_from_cycle(has_targets=has_targets, kill_switch=kill_switch)
    no_target_standdown = runtime_state == RUNTIME_STATE_NO_TARGET_STANDDOWN
    active_targets_present = bool(has_targets)
    promotion_eligibility_hint = bool(active_targets_present and not bool(kill_switch))
    return RuntimeCycleSemantics(
        runtime_state=runtime_state,
        active_targets_present=active_targets_present,
        no_target_standdown=no_target_standdown,
        book_feed_required=bool(active_targets_present),
        promotion_eligibility_hint=promotion_eligibility_hint,
    )
