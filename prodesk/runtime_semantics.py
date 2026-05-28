from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .common import parse_float, parse_ts
from .edge_truth_contract import is_taker_submit_event_type

LIFECYCLE_PHASE_SCAN = "scan"
LIFECYCLE_PHASE_PREPARE = "prepare"
LIFECYCLE_PHASE_MAKER_WINDOW = "maker_window"
LIFECYCLE_PHASE_TAKER_WINDOW = "taker_window"
LIFECYCLE_PHASE_RESOLVE = "resolve"
LIFECYCLE_PHASE_ACTIVE = LIFECYCLE_PHASE_PREPARE
LIFECYCLE_PHASE_SAFETY_HALT = LIFECYCLE_PHASE_RESOLVE

RUNTIME_CLASS_VALID_ACTIVE = "VALID_ACTIVE"
RUNTIME_CLASS_VALID_SCAN = "VALID_SCAN"
RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION = "NON_PROMOTABLE_NO_PARTICIPATION"
RUNTIME_CLASS_INVALID_DEADLOCK = "INVALID_DEADLOCK"
RUNTIME_CLASS_INVALID_SAFETY = "INVALID_SAFETY"


SUPPRESSION_CAUSE_NONE = "none"
SUPPRESSION_CAUSE_SCAN_ORDER_SUBMISSION_VIOLATION = "scan_order_submission_violation"
SUPPRESSION_CAUSE_SCAN_MARKET_TRUTH_REQUIREMENT_VIOLATION = "scan_market_truth_requirement_violation"
SUPPRESSION_CAUSE_SUSTAINED_SCAN_WITH_GUARD_OR_KILL = "sustained_scan_with_guard_or_kill"
SUPPRESSION_CAUSE_SAFETY_KILL_SWITCH_OR_EXTERNAL_GUARD = "safety_kill_switch_or_external_guard"
SUPPRESSION_CAUSE_SAFETY_REQUIRED_MARKET_TRUTH_DISCONNECTED = "safety_required_market_truth_disconnected"
SUPPRESSION_CAUSE_ACTIVE_TARGET_SAFETY_VIOLATION = "active_target_safety_violation"
SUPPRESSION_CAUSE_ACTIVE_TARGETS_WITHOUT_MEANINGFUL_PARTICIPATION = "active_targets_without_meaningful_participation"
SUPPRESSION_CAUSE_SCAN_DURATION_NON_PROMOTABLE = "scan_duration_non_promotable"
SUPPRESSION_CAUSE_STATUS_ROWS_MISSING = "status_rows_missing"
SUPPRESSION_CAUSE_LIFECYCLE_PHASE_AMBIGUOUS = "lifecycle_phase_ambiguous"


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
        "gauge.actions_last_status_window",
        "gauge.maker_actions_last_cycle",
        "gauge.maker_actions_last_status_window",
        "gauge.maker_fills_last_cycle",
        "gauge.maker_fills_last_status_window",
        "gauge.maker_submitted_token_count_last_cycle",
        "gauge.maker_submitted_token_count_last_status_window",
        "gauge.taker_actions_last_cycle",
        "gauge.taker_actions_last_status_window",
        "gauge.taker_submitted_last_cycle",
        "gauge.taker_submitted_last_status_window",
        "gauge.taker_fills_last_cycle",
        "gauge.taker_fills_last_status_window",
        "gauge.order_submission_attempts_last_cycle",
        "gauge.order_submission_attempts_last_status_window",
    ):
        value = parse_float(status_row.get(key))
        if value is not None and value > 0.0:
            return True
    return False


def _status_lifecycle_phase(status_row: Dict[str, Any]) -> str:
    phase = _as_nonempty_text(status_row.get("lifecycle_phase")).lower()
    if phase:
        return phase
    if _as_bool(status_row.get("kill_switch")):
        return LIFECYCLE_PHASE_SAFETY_HALT
    if _status_active_targets_present(status_row):
        return LIFECYCLE_PHASE_ACTIVE
    return LIFECYCLE_PHASE_SCAN


def _status_scan_phase(status_row: Dict[str, Any]) -> bool:
    lifecycle_phase = _as_nonempty_text(status_row.get("lifecycle_phase")).lower()
    if lifecycle_phase:
        return lifecycle_phase == LIFECYCLE_PHASE_SCAN
    return _status_lifecycle_phase(status_row) == LIFECYCLE_PHASE_SCAN


def _status_market_truth_required(
    status_row: Dict[str, Any],
    *,
    configured_default_required: bool,
) -> bool:
    explicit_market_truth = _as_bool(status_row.get("market_truth_required"))
    if explicit_market_truth is not None:
        return bool(explicit_market_truth)
    if not configured_default_required:
        return False
    if _status_scan_phase(status_row):
        return False
    return _status_active_targets_present(status_row)


def lifecycle_phase_from_cycle(*, has_targets: bool, kill_switch: bool) -> str:
    if bool(kill_switch) and bool(has_targets):
        return LIFECYCLE_PHASE_RESOLVE
    if bool(has_targets):
        return LIFECYCLE_PHASE_PREPARE
    return LIFECYCLE_PHASE_SCAN


def lifecycle_phase_to_gauge(lifecycle_phase: str) -> float:
    mapping = {
        LIFECYCLE_PHASE_SCAN: 1.0,
        LIFECYCLE_PHASE_PREPARE: 2.0,
        LIFECYCLE_PHASE_MAKER_WINDOW: 3.0,
        LIFECYCLE_PHASE_TAKER_WINDOW: 4.0,
        LIFECYCLE_PHASE_RESOLVE: 5.0,
    }
    return float(mapping.get(str(lifecycle_phase).strip().lower(), 0.0))


def resolve_guard_connectivity_requirements(
    *,
    status_row: Optional[Dict[str, Any]],
    require_book_feed_connected_config: bool,
) -> Dict[str, Any]:
    if status_row is None:
        return {
            "lifecycle_phase": "",
            "active_targets_present": False,
            "scan_phase": False,
            "market_truth_required": bool(require_book_feed_connected_config),
        }
    lifecycle_phase = _status_lifecycle_phase(status_row)
    active_targets_present = _status_active_targets_present(status_row)
    scan_phase = _status_scan_phase(status_row)
    market_truth_required = _status_market_truth_required(
        status_row,
        configured_default_required=bool(require_book_feed_connected_config),
    )
    return {
        "lifecycle_phase": lifecycle_phase,
        "active_targets_present": bool(active_targets_present),
        "scan_phase": bool(scan_phase),
        "market_truth_required": bool(market_truth_required),
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
    max_value = 0.0
    for key in (
        "order_submission_attempts_last_cycle",
        "gauge.order_submission_attempts_last_cycle",
        "order_submission_attempts_last_status_window",
        "gauge.order_submission_attempts_last_status_window",
    ):
        value = parse_float(row.get(key))
        if value is not None:
            max_value = max(max_value, max(0.0, float(value)))
    return max_value


def _status_order_submit_attempts_current_cycle(row: Dict[str, Any]) -> float:
    max_value = 0.0
    for key in (
        "order_submission_attempts_last_cycle",
        "gauge.order_submission_attempts_last_cycle",
    ):
        value = parse_float(row.get(key))
        if value is not None:
            max_value = max(max_value, max(0.0, float(value)))
    return max_value


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
    scan_non_promotable_min_minutes: float = 15.0,
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
                "scan_rows": 0.0,
                "deadlock_rows": 0.0,
                "safety_rows": 0.0,
                "required_market_truth_rows": 0.0,
                "required_market_truth_disconnected_rows": 0.0,
                "scan_order_submission_violation_rows": 0.0,
                "scan_market_truth_required_violation_rows": 0.0,
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
    scan_rows = 0
    deadlock_rows = 0
    safety_rows = 0
    required_market_truth_rows = 0
    required_market_truth_disconnected_rows = 0
    order_submission_attempt_rows = 0
    scan_order_submission_violation_rows = 0
    scan_market_truth_required_violation_rows = 0
    participation_rows = 0
    max_cycles_counter = 0.0
    active_feed_rows = 0
    unknown_active_feed_rows = 0
    unknown_active_feed_streak = 0
    max_unknown_active_feed_streak = 0
    active_target_guard_or_kill_rows = 0
    active_target_required_feed_disconnected_rows = 0

    for row in rows:
        lifecycle_phase = _status_lifecycle_phase(row)
        has_targets = _status_active_targets_present(row)
        is_scan_phase = _status_scan_phase(row)
        kill_switch = bool(_as_bool(row.get("kill_switch")))
        external_guard = _status_external_guard_active(row)
        market_truth_required = _status_market_truth_required(
            row,
            configured_default_required=True,
        )
        book_connected = _status_book_feed_connected(row)
        book_age = _status_book_feed_age_sec(row)
        order_submit_attempts = _status_order_submit_attempts(row)
        order_submit_attempts_current_cycle = _status_order_submit_attempts_current_cycle(row)
        cycle_counter = _status_cycle_counter(row)
        if cycle_counter > max_cycles_counter:
            max_cycles_counter = cycle_counter

        if has_targets:
            active_target_rows += 1
        if is_scan_phase:
            scan_rows += 1
        if order_submit_attempts > 0:
            order_submission_attempt_rows += 1
        open_orders = parse_float(row.get("gauge.open_orders"))
        quote_active = parse_float(row.get("gauge.quote_active"))
        actions_last_cycle = parse_float(row.get("gauge.actions_last_cycle"))
        actions_last_status_window = parse_float(row.get("gauge.actions_last_status_window"))
        maker_actions_last_cycle = parse_float(row.get("gauge.maker_actions_last_cycle"))
        maker_actions_last_status_window = parse_float(row.get("gauge.maker_actions_last_status_window"))
        maker_fills_last_cycle = parse_float(row.get("gauge.maker_fills_last_cycle"))
        maker_fills_last_status_window = parse_float(row.get("gauge.maker_fills_last_status_window"))
        maker_submitted_token_count_last_cycle = parse_float(
            row.get("gauge.maker_submitted_token_count_last_cycle")
        )
        maker_submitted_token_count_last_status_window = parse_float(
            row.get("gauge.maker_submitted_token_count_last_status_window")
        )
        taker_actions_last_cycle = parse_float(row.get("gauge.taker_actions_last_cycle"))
        taker_actions_last_status_window = parse_float(row.get("gauge.taker_actions_last_status_window"))
        taker_submitted_last_cycle = parse_float(row.get("gauge.taker_submitted_last_cycle"))
        taker_submitted_last_status_window = parse_float(row.get("gauge.taker_submitted_last_status_window"))
        taker_fills_last_cycle = parse_float(row.get("gauge.taker_fills_last_cycle"))
        taker_fills_last_status_window = parse_float(row.get("gauge.taker_fills_last_status_window"))
        if (
            (open_orders is not None and open_orders > 0.0)
            or (quote_active is not None and quote_active > 0.0)
            or (actions_last_cycle is not None and actions_last_cycle > 0.0)
            or (actions_last_status_window is not None and actions_last_status_window > 0.0)
            or (maker_actions_last_cycle is not None and maker_actions_last_cycle > 0.0)
            or (maker_actions_last_status_window is not None and maker_actions_last_status_window > 0.0)
            or (maker_fills_last_cycle is not None and maker_fills_last_cycle > 0.0)
            or (maker_fills_last_status_window is not None and maker_fills_last_status_window > 0.0)
            or (
                maker_submitted_token_count_last_cycle is not None
                and maker_submitted_token_count_last_cycle > 0.0
            )
            or (
                maker_submitted_token_count_last_status_window is not None
                and maker_submitted_token_count_last_status_window > 0.0
            )
            or (taker_actions_last_cycle is not None and taker_actions_last_cycle > 0.0)
            or (taker_actions_last_status_window is not None and taker_actions_last_status_window > 0.0)
            or (taker_submitted_last_cycle is not None and taker_submitted_last_cycle > 0.0)
            or (taker_submitted_last_status_window is not None and taker_submitted_last_status_window > 0.0)
            or (taker_fills_last_cycle is not None and taker_fills_last_cycle > 0.0)
            or (taker_fills_last_status_window is not None and taker_fills_last_status_window > 0.0)
            or order_submit_attempts > 0.0
        ):
            participation_rows += 1

        if is_scan_phase:
            # Status-window counters can legitimately carry forward actions from the
            # preceding active window; only same-cycle submit attempts prove a
            # scan-phase submission defect.
            if order_submit_attempts_current_cycle > 0.0:
                scan_order_submission_violation_rows += 1
            if market_truth_required:
                scan_market_truth_required_violation_rows += 1

        if market_truth_required:
            required_market_truth_rows += 1
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
                required_market_truth_disconnected_rows += 1
                if has_targets:
                    active_feed_rows += 1
                    if unknown_age_disconnect:
                        unknown_active_feed_rows += 1
        if has_targets and market_truth_required and (book_connected is False) and (book_age is None):
            unknown_active_feed_streak += 1
            if unknown_active_feed_streak > max_unknown_active_feed_streak:
                max_unknown_active_feed_streak = unknown_active_feed_streak
        else:
            unknown_active_feed_streak = 0

        if is_scan_phase and (kill_switch or external_guard):
            deadlock_rows += 1
        if has_targets and (kill_switch or external_guard):
            safety_rows += 1
            active_target_guard_or_kill_rows += 1
        if has_targets and market_truth_required and (
            (book_connected is False)
            and (book_age is not None and book_age >= float(max_required_book_feed_age_sec))
        ):
            safety_rows += 1
            active_target_required_feed_disconnected_rows += 1
        if has_targets and market_truth_required and (
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
        if event_type in {"order_submit", "risk_reject", "fill"} or is_taker_submit_event_type(event_type):
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
    if scan_order_submission_violation_rows > 0:
        reasons.append("scan_order_submission_violation")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False
    elif scan_market_truth_required_violation_rows > 0:
        reasons.append("scan_market_truth_requirement_violation")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False
    elif deadlock_rows >= max(1, int(deadlock_min_rows)):
        reasons.append("sustained_scan_with_guard_or_kill")
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
    elif scan_rows == len(rows):
        reasons.append("doctrine_scan_phase")
        if duration_minutes >= float(scan_non_promotable_min_minutes):
            reasons.append("scan_duration_non_promotable")
            classification = RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION
            promotion_eligible = False
        else:
            classification = RUNTIME_CLASS_VALID_SCAN
            promotion_eligible = False
    else:
        reasons.append("lifecycle_phase_ambiguous")
        classification = RUNTIME_CLASS_INVALID_DEADLOCK
        promotion_eligible = False

    suppression_candidates: List[Dict[str, Any]] = []
    if scan_order_submission_violation_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SCAN_ORDER_SUBMISSION_VIOLATION, "priority": 10}
        )
    if scan_market_truth_required_violation_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SCAN_MARKET_TRUTH_REQUIREMENT_VIOLATION, "priority": 15}
        )
    if deadlock_rows >= max(1, int(deadlock_min_rows)):
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SUSTAINED_SCAN_WITH_GUARD_OR_KILL, "priority": 20}
        )
    if active_target_guard_or_kill_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SAFETY_KILL_SWITCH_OR_EXTERNAL_GUARD, "priority": 30}
        )
    if active_target_required_feed_disconnected_rows > 0:
        suppression_candidates.append(
            {"cause": SUPPRESSION_CAUSE_SAFETY_REQUIRED_MARKET_TRUTH_DISCONNECTED, "priority": 30}
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
    if scan_rows == len(rows) and duration_minutes >= float(scan_non_promotable_min_minutes):
        suppression_candidates.append(
            {
                "cause": SUPPRESSION_CAUSE_SCAN_DURATION_NON_PROMOTABLE,
                "priority": 50,
            }
        )
    if "lifecycle_phase_ambiguous" in reasons:
        suppression_candidates.append(
            {
                "cause": SUPPRESSION_CAUSE_LIFECYCLE_PHASE_AMBIGUOUS,
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
            "scan_rows": float(scan_rows),
            "deadlock_rows": float(deadlock_rows),
            "safety_rows": float(safety_rows),
            "required_market_truth_rows": float(required_market_truth_rows),
            "required_market_truth_disconnected_rows": float(required_market_truth_disconnected_rows),
            "required_market_truth_disconnected_active_target_rows": float(active_feed_rows),
            "required_market_truth_disconnected_unknown_age_rows": float(unknown_active_feed_rows),
            "required_market_truth_disconnected_unknown_age_max_streak": float(max_unknown_active_feed_streak),
            "active_target_guard_or_kill_rows": float(active_target_guard_or_kill_rows),
            "active_target_required_feed_disconnected_rows": float(active_target_required_feed_disconnected_rows),
            "scan_order_submission_violation_rows": float(scan_order_submission_violation_rows),
            "scan_market_truth_required_violation_rows": float(scan_market_truth_required_violation_rows),
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
    lifecycle_phase: str
    active_targets_present: bool
    scan_phase: bool
    market_truth_required: bool
    promotion_eligibility_hint: bool


def cycle_semantics(*, has_targets: bool, kill_switch: bool) -> RuntimeCycleSemantics:
    lifecycle_phase = lifecycle_phase_from_cycle(has_targets=has_targets, kill_switch=kill_switch)
    scan_phase = lifecycle_phase == LIFECYCLE_PHASE_SCAN
    active_targets_present = bool(has_targets)
    promotion_eligibility_hint = bool(active_targets_present and not bool(kill_switch))
    return RuntimeCycleSemantics(
        lifecycle_phase=lifecycle_phase,
        active_targets_present=active_targets_present,
        scan_phase=scan_phase,
        market_truth_required=bool(active_targets_present),
        promotion_eligibility_hint=promotion_eligibility_hint,
    )
