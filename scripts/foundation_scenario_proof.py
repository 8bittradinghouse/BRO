#!/usr/bin/env python3
"""Generate deterministic C/D/E/F foundation closeout scenario proof artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any, Dict, Iterable, List, Tuple

from prodesk.chainlink_feed import ChainlinkFeed
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.order_lifecycle_audit import run_audit as run_lifecycle_audit
from scripts.paper_harness_audit import run_audit as run_paper_harness_audit
from scripts.time_discipline_audit import run_audit as run_time_discipline_audit
from scripts.websocket_hardening_audit import run_audit as run_websocket_hardening_audit

AUDIT_SURFACE_NAMES = (
    "websocket_hardening_audit",
    "time_discipline_audit",
    "paper_harness_audit",
    "order_lifecycle_audit",
)


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: pathlib.Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
    if rendered:
        rendered += "\n"
    path.write_text(rendered, encoding="utf-8")


def _time_policy() -> Dict[str, Any]:
    return {
        "source_of_truth": "utc_wall_clock",
        "fallback_logic": "source_ts_utc_then_ts_receive_utc_then_ts_event_utc",
        "skew_tolerance_ms": 120.0,
        "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
    }


def _ordering_policy() -> Dict[str, Any]:
    return {
        "primary": "source_timestamp",
        "fallback": "receive_monotonic",
        "tolerance_ms": 0,
        "tie_breaker": "same_timestamp_price_revision",
    }


def _base_status_row(
    *,
    run_id: str,
    ts_utc: str,
    cycle: int,
    book_connected: bool,
    chain_connected: bool,
    book_reconnects: int,
    chain_reconnects: int,
    book_age_sec: float,
    chain_age_sec: float,
    ordering_counts: Dict[str, int],
    ws_updates_total: float,
    ws_updates_ws: float,
    ws_updates_rest: float,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "ts_utc": ts_utc,
        "ts_event_utc": ts_utc,
        "ts_receive_utc": ts_utc,
        "ts_source_utc": None,
        "ts_decision_utc": ts_utc,
        "time_policy": _time_policy(),
        "runtime_state": "active",
        "active_targets_present": True,
        "no_target_standdown": False,
        "book_feed_required": True,
        "kill_switch": False,
        "counter.cycles": float(cycle),
        "gauge.open_orders": 1.0,
        "gauge.quote_active": 1.0,
        "gauge.actions_last_cycle": 1.0,
        "counter.book_updates": float(ws_updates_total),
        "counter.book_updates_ws": float(ws_updates_ws),
        "counter.book_updates_rest": float(ws_updates_rest),
        "book_feed": {
            "connected": bool(book_connected),
            "reconnects": int(book_reconnects),
            "reconnects_steady": int(book_reconnects),
            "last_msg_age_sec": float(book_age_sec),
            "token_count": 1,
            "cached_books": 1,
            "primed": True,
        },
        "chainlink": {
            "connected": bool(chain_connected),
            "reconnects": int(chain_reconnects),
            "reconnects_steady": int(chain_reconnects),
            "last_tick_age_sec": float(chain_age_sec),
            "queue_size": 0,
            "dropped_ticks": 0,
            "ordering_policy": _ordering_policy(),
            "ordering_classification_counts": dict(ordering_counts),
            "ordering_decision_counts": {},
        },
    }


def _event(
    *,
    run_id: str,
    event_type: str,
    ts_utc: str,
    extra: Dict[str, Any],
) -> Dict[str, Any]:
    row = {
        "run_id": run_id,
        "event_type": event_type,
        "ts_utc": ts_utc,
        "ts_event_utc": ts_utc,
        "ts_receive_utc": ts_utc,
        "ts_source_utc": None,
        "ts_decision_utc": ts_utc,
    }
    row.update(extra)
    if str(event_type).strip() == "edge_evaluation":
        evaluation_scope = str(row.get("evaluation_scope") or "").strip().lower()
        maker_allowed = bool(row.get("maker_allowed", False))
        taker_allowed = bool(row.get("taker_allowed", False))
        recovery_active = bool(row.get("reduce_only_recovery_active", False))
        if "maker_new_risk_allowed" not in row:
            row["maker_new_risk_allowed"] = bool(
                evaluation_scope == "maker" and maker_allowed and (not recovery_active)
            )
        if "normal_taker_allowed" not in row:
            row["normal_taker_allowed"] = bool(
                evaluation_scope == "taker" and taker_allowed and (not recovery_active)
            )
        row.setdefault("reduce_only_recovery_allowed", bool(recovery_active))
        row.setdefault("preexpiry_emergency_taker_allowed", False)
        if "late_window_authority_class" not in row:
            if bool(row.get("preexpiry_emergency_taker_allowed", False)):
                row["late_window_authority_class"] = "preexpiry_emergency_recovery_only"
            elif bool(row.get("reduce_only_recovery_allowed", False)):
                row["late_window_authority_class"] = "reduce_only_recovery_only"
            elif bool(row.get("normal_taker_allowed", False)):
                row["late_window_authority_class"] = "normal_taker_only"
            elif bool(row.get("maker_new_risk_allowed", False)):
                row["late_window_authority_class"] = "maker_new_risk_only"
            else:
                row["late_window_authority_class"] = "authority_closed"
    return row


def _clean_scenario_rows(run_id: str, base_ts: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            cycle=1,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.2,
            chain_age_sec=0.2,
            ordering_counts={"ordered": 2, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=10.0,
            ws_updates_ws=9.0,
            ws_updates_rest=1.0,
        ),
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=3601)),
            cycle=2,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.2,
            chain_age_sec=0.2,
            ordering_counts={"ordered": 4, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=20.0,
            ws_updates_ws=18.0,
            ws_updates_rest=2.0,
        ),
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "no_target_standdown",
                "runtime_state": "active",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": False,
                "book_feed_required": True,
                "kill_switch": False,
                "transition_reason_code": "targets_activated",
                "transition_reason_detail": "scenario_clean",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": False, "reasons": []},
        ),
        _event(
            run_id=run_id,
            event_type="chainlink_tick",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "symbol": "btc/usd",
                "price": 65000.0,
                "ts_source_utc": _iso(base_ts + dt.timedelta(seconds=1)),
                "ts_receive_utc": _iso(base_ts + dt.timedelta(seconds=1)),
            },
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-clean",
                "action_taken": "maker",
                "evaluation_scope": "maker",
                "maker_allowed": True,
                "taker_allowed": False,
                "submitted": True,
                "filled": True,
                "result": None,
                "decision_input_source": "ws",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_live",
                "decision_input_type": "observed_live",
                "execution_realism_class": "not_modeled",
            },
        ),
        _event(
            run_id=run_id,
            event_type="order_submit",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "order_id": "ord-clean-1",
                "token_id": "tok-clean",
                "side": "BUY",
                "price": 0.5,
                "size": 10.0,
                "reason_code": "mm_quote",
                "execution_preference": "maker_preferred",
                "market_id": "m-clean",
                "window_id": "2026-03-30T00:00",
                "stage": "MAKER_POSITION",
            },
        ),
        _event(
            run_id=run_id,
            event_type="fill",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=3)),
            extra={
                "trade_id": "paper-trade-cleanabc123-1",
                "order_id": "ord-clean-1",
                "token_id": "tok-clean",
                "side": "BUY",
                "price": 0.5,
                "size": 10.0,
                "source": "paper",
                "fill_policy_basis": "bounded_visible_liquidity_top_of_book",
                "execution_realism_class": "bounded_approximation",
                "decision_input_type": "observed_live",
            },
        ),
    ]
    return status_rows, events


def _degraded_scenario_rows(run_id: str, base_ts: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            cycle=1,
            book_connected=False,
            chain_connected=False,
            book_reconnects=3,
            chain_reconnects=4,
            book_age_sec=50.0,
            chain_age_sec=50.0,
            ordering_counts={"ordered": 1, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=5.0,
            ws_updates_ws=0.0,
            ws_updates_rest=5.0,
        ),
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            cycle=2,
            book_connected=False,
            chain_connected=False,
            book_reconnects=6,
            chain_reconnects=8,
            book_age_sec=60.0,
            chain_age_sec=60.0,
            ordering_counts={"ordered": 1, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=8.0,
            ws_updates_ws=0.0,
            ws_updates_rest=8.0,
        ),
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "active",
                "runtime_state": "active",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": True,
                "book_feed_required": True,
                "kill_switch": False,
                "transition_reason_code": "book_requirement_changed",
                "transition_reason_detail": "scenario_degraded",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": True, "reasons": ["book_feed_disconnected", "chainlink_disconnected"]},
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-degraded",
                "action_taken": "none",
                "evaluation_scope": "maker",
                "maker_allowed": True,
                "taker_allowed": False,
                "block_reason": "stale_book",
                "submitted": False,
                "filled": False,
                "result": None,
                "decision_input_source": "rest",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_live",
                "decision_input_type": "bounded_derived",
                "execution_realism_class": "not_modeled",
            },
        ),
    ]
    return status_rows, events


def _reconnect_scenario_rows(run_id: str, base_ts: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            cycle=1,
            book_connected=True,
            chain_connected=True,
            book_reconnects=1,
            chain_reconnects=1,
            book_age_sec=0.5,
            chain_age_sec=0.5,
            ordering_counts={"ordered": 2, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=12.0,
            ws_updates_ws=10.0,
            ws_updates_rest=2.0,
        ),
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=3601)),
            cycle=2,
            book_connected=True,
            chain_connected=True,
            book_reconnects=2,
            chain_reconnects=2,
            book_age_sec=0.4,
            chain_age_sec=0.3,
            ordering_counts={"ordered": 4, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=24.0,
            ws_updates_ws=20.0,
            ws_updates_rest=4.0,
        ),
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "active",
                "runtime_state": "active",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": True,
                "book_feed_required": True,
                "kill_switch": False,
                "transition_reason_code": "runtime_state_changed",
                "transition_reason_detail": "scenario_reconnect",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": False, "reasons": []},
        ),
        _event(
            run_id=run_id,
            event_type="chainlink_tick",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "symbol": "btc/usd",
                "price": 65001.0,
                "ts_source_utc": _iso(base_ts + dt.timedelta(seconds=1)),
                "ts_receive_utc": _iso(base_ts + dt.timedelta(seconds=1)),
            },
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-reconnect",
                "action_taken": "taker",
                "evaluation_scope": "taker",
                "maker_allowed": False,
                "taker_allowed": True,
                "submitted": True,
                "filled": True,
                "result": None,
                "decision_input_source": "ws",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_live",
                "decision_input_type": "observed_live",
                "execution_realism_class": "bounded_approximation",
            },
        ),
        _event(
            run_id=run_id,
            event_type="order_submit",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "order_id": "ord-reconnect-1",
                "token_id": "tok-reconnect",
                "side": "BUY",
                "price": 0.5,
                "size": 6.0,
                "reason_code": "taker_chainlink",
                "execution_preference": "taker_only",
                "market_id": "m-reconnect",
                "window_id": "2026-03-30T00:01",
                "stage": "SNIPER_PRIMARY",
            },
        ),
        _event(
            run_id=run_id,
            event_type="fill",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=3)),
            extra={
                "trade_id": "paper-trade-reconabc123-1",
                "order_id": "ord-reconnect-1",
                "token_id": "tok-reconnect",
                "side": "BUY",
                "price": 0.5,
                "size": 6.0,
                "source": "paper",
                "fill_policy_basis": "bounded_visible_liquidity_top_of_book",
                "execution_realism_class": "bounded_approximation",
                "decision_input_type": "observed_live",
            },
        ),
    ]
    return status_rows, events


def _disorder_scenario_rows(run_id: str, base_ts: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            cycle=1,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.4,
            chain_age_sec=0.4,
            ordering_counts={"ordered": 2, "out_of_order": 1, "duplicate": 1, "revision": 1, "missing_source_time": 1},
            ws_updates_total=14.0,
            ws_updates_ws=12.0,
            ws_updates_rest=2.0,
        ),
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            cycle=2,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.3,
            chain_age_sec=0.3,
            ordering_counts={"ordered": 4, "out_of_order": 2, "duplicate": 2, "revision": 2, "missing_source_time": 2},
            ws_updates_total=28.0,
            ws_updates_ws=24.0,
            ws_updates_rest=4.0,
        ),
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "active",
                "runtime_state": "active",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": True,
                "book_feed_required": True,
                "kill_switch": False,
                "transition_reason_code": "runtime_state_changed",
                "transition_reason_detail": "scenario_disorder",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": False, "reasons": []},
        ),
        _event(
            run_id=run_id,
            event_type="chainlink_tick",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "symbol": "btc/usd",
                "price": 65002.0,
                "ts_source_utc": _iso(base_ts + dt.timedelta(seconds=1)),
                "ts_receive_utc": _iso(base_ts + dt.timedelta(seconds=1)),
            },
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-disorder",
                "action_taken": "none",
                "evaluation_scope": "maker",
                "maker_allowed": True,
                "taker_allowed": False,
                "block_reason": "oracle_unavailable_or_stale",
                "submitted": False,
                "filled": False,
                "result": None,
                "decision_input_source": "ws",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_live",
                "decision_input_type": "observed_live",
                "execution_realism_class": "not_modeled",
            },
        ),
    ]
    return status_rows, events


def _thin_liquidity_partial_fill_scenario_rows(
    run_id: str, base_ts: dt.datetime
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            cycle=1,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.2,
            chain_age_sec=0.2,
            ordering_counts={"ordered": 2, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=9.0,
            ws_updates_ws=8.0,
            ws_updates_rest=1.0,
        ),
        _base_status_row(
            run_id=run_id,
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            cycle=2,
            book_connected=True,
            chain_connected=True,
            book_reconnects=0,
            chain_reconnects=0,
            book_age_sec=0.2,
            chain_age_sec=0.2,
            ordering_counts={"ordered": 4, "out_of_order": 0, "duplicate": 0, "revision": 0, "missing_source_time": 0},
            ws_updates_total=18.0,
            ws_updates_ws=16.0,
            ws_updates_rest=2.0,
        ),
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "no_target_standdown",
                "runtime_state": "active",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": False,
                "book_feed_required": True,
                "kill_switch": False,
                "transition_reason_code": "targets_activated",
                "transition_reason_detail": "scenario_thin_liquidity_partial_fill",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": False, "reasons": []},
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-thin",
                "action_taken": "taker",
                "evaluation_scope": "taker",
                "maker_allowed": False,
                "taker_allowed": True,
                "submitted": True,
                "filled": True,
                "result": None,
                "decision_input_source": "ws",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_live",
                "decision_input_type": "observed_live",
                "execution_realism_class": "bounded_approximation",
            },
        ),
        _event(
            run_id=run_id,
            event_type="order_submit",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "order_id": "ord-thin-1",
                "token_id": "tok-thin",
                "side": "BUY",
                "price": 0.5,
                "size": 10.0,
                "reason_code": "taker_chainlink",
                "execution_preference": "taker_only",
                "market_id": "m-thin",
                "window_id": "2026-03-30T00:04",
                "stage": "SNIPER_PRIMARY",
            },
        ),
        _event(
            run_id=run_id,
            event_type="fill",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=3)),
            extra={
                "trade_id": "paper-trade-thinliqabc1-1",
                "order_id": "ord-thin-1",
                "token_id": "tok-thin",
                "side": "BUY",
                "price": 0.5,
                "size": 2.5,
                "source": "paper",
                "fill_policy_basis": "bounded_visible_liquidity_top_of_book",
                "execution_realism_class": "bounded_approximation",
                "decision_input_type": "observed_live",
            },
        ),
    ]
    return status_rows, events


def _poor_truth_standdown_scenario_rows(
    run_id: str, base_ts: dt.datetime
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    status_rows = [
        {
            **_base_status_row(
                run_id=run_id,
                ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
                cycle=1,
                book_connected=False,
                chain_connected=False,
                book_reconnects=8,
                chain_reconnects=9,
                book_age_sec=80.0,
                chain_age_sec=80.0,
                ordering_counts={"ordered": 0, "out_of_order": 1, "duplicate": 0, "revision": 0, "missing_source_time": 1},
                ws_updates_total=2.0,
                ws_updates_ws=0.0,
                ws_updates_rest=2.0,
            ),
            "runtime_state": "safety_halt",
            "kill_switch": True,
            "active_targets_present": True,
            "no_target_standdown": False,
            "book_feed_required": True,
        },
        {
            **_base_status_row(
                run_id=run_id,
                ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
                cycle=2,
                book_connected=False,
                chain_connected=False,
                book_reconnects=10,
                chain_reconnects=11,
                book_age_sec=90.0,
                chain_age_sec=90.0,
                ordering_counts={"ordered": 0, "out_of_order": 2, "duplicate": 0, "revision": 0, "missing_source_time": 2},
                ws_updates_total=3.0,
                ws_updates_ws=0.0,
                ws_updates_rest=3.0,
            ),
            "runtime_state": "safety_halt",
            "kill_switch": True,
            "active_targets_present": True,
            "no_target_standdown": False,
            "book_feed_required": True,
        },
    ]
    events = [
        _event(
            run_id=run_id,
            event_type="runtime_state_transition",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={
                "previous_runtime_state": "active",
                "runtime_state": "safety_halt",
                "active_targets_present": True,
                "no_target_standdown": False,
                "previous_book_feed_required": True,
                "book_feed_required": True,
                "kill_switch": True,
                "transition_reason_code": "kill_switch_engaged",
                "transition_reason_detail": "scenario_poor_truth_standdown",
            },
        ),
        _event(
            run_id=run_id,
            event_type="ws_slo_state",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=1)),
            extra={"degraded": True, "reasons": ["book_feed_disconnected", "chainlink_disconnected", "stale_oracle"]},
        ),
        _event(
            run_id=run_id,
            event_type="edge_evaluation",
            ts_utc=_iso(base_ts + dt.timedelta(seconds=2)),
            extra={
                "token_id": "tok-poor-truth",
                "action_taken": "none",
                "evaluation_scope": "taker",
                "maker_allowed": False,
                "taker_allowed": True,
                "block_reason": "oracle_unavailable_or_stale",
                "submitted": False,
                "filled": False,
                "result": None,
                "decision_input_source": "rest",
                "decision_input_emulated": False,
                "decision_input_data_class": "observed_other",
                "decision_input_type": "bounded_derived",
                "execution_realism_class": "bounded_approximation",
            },
        ),
    ]
    return status_rows, events


def _write_scenario(
    *,
    scenario_root: pathlib.Path,
    run_id: str,
    status_rows: List[Dict[str, Any]],
    event_rows: List[Dict[str, Any]],
) -> Dict[str, pathlib.Path]:
    scenario_root.mkdir(parents=True, exist_ok=True)
    date_tag = "2026-03-30"
    status_path = scenario_root / f"status_{date_tag}.jsonl"
    events_path = scenario_root / f"events_{date_tag}.jsonl"
    errors_path = scenario_root / f"errors_{date_tag}.jsonl"
    manifest_path = scenario_root / f"run_manifest_{run_id}.json"
    contract_path = scenario_root / f"run_contract_{run_id}.json"
    status_slice_path = scenario_root / f"status_slice_{run_id}.jsonl"
    events_slice_path = scenario_root / f"events_slice_{run_id}.jsonl"
    errors_slice_path = scenario_root / f"errors_slice_{run_id}.jsonl"

    _write_jsonl(status_path, status_rows)
    _write_jsonl(events_path, event_rows)
    _write_jsonl(errors_path, [])
    _write_jsonl(status_slice_path, status_rows)
    _write_jsonl(events_slice_path, event_rows)
    _write_jsonl(errors_slice_path, [])

    start_ts = status_rows[0]["ts_utc"] if status_rows else event_rows[0]["ts_utc"]
    stop_ts = status_rows[-1]["ts_utc"] if status_rows else event_rows[-1]["ts_utc"]
    manifest_payload = {
        "run_id": run_id,
        "manifest_schema_version": 2,
        "profile_name": "paper_universal",
        "mode": "paper",
        "start_ts": start_ts,
        "stop_ts": stop_ts,
        "status_path": str(status_path.resolve()),
        "events_path": str(events_path.resolve()),
        "errors_path": str(errors_path.resolve()),
        "config_fingerprint_sha256": "",
        "code_fingerprint_sha256": "",
    }
    _write_json(manifest_path, manifest_payload)

    contract_payload = build_run_contract(
        session_id=f"scenario-session-{run_id}",
        run_id=run_id,
        phase="validate_postrun",
        session_type="paper_canonical",
        authority_level="observational",
        allowed_actions=["validate_postrun"],
        manifest_path=manifest_path,
        log_root=scenario_root,
        state_root=scenario_root,
        start_ts=start_ts,
        stop_ts=stop_ts,
        evidence_slice_start_ts=start_ts,
        evidence_slice_end_ts=stop_ts,
        status_path=str(status_path.resolve()),
        events_path=str(events_path.resolve()),
        errors_path=str(errors_path.resolve()),
        status_slice_path=str(status_slice_path.resolve()),
        events_slice_path=str(events_slice_path.resolve()),
        errors_slice_path=str(errors_slice_path.resolve()),
    )
    write_run_contract(contract_path, contract_payload, allow_open=False)
    return {
        "status_path": status_path,
        "events_path": events_path,
        "errors_path": errors_path,
        "manifest_path": manifest_path,
        "run_contract_path": contract_path,
    }


def _chainlink_injection_proof() -> Dict[str, Any]:
    scenarios: Dict[str, Dict[str, Any]] = {}

    def _status_after_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        for idx, msg in enumerate(messages):
            feed._handle_message_obj(msg, received_monotonic=100.0 + float(idx))
        return feed.status()

    base_tick = {
        "topic": "crypto_prices_chainlink",
        "payload": {"symbol": "btc/usd", "value": "65000", "timestamp": 2000},
    }
    scenarios["normal_feed"] = _status_after_messages(
        [
            base_tick,
            {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "value": "65001", "timestamp": 2001},
            },
        ]
    )
    scenarios["out_of_order_injection"] = _status_after_messages(
        [
            base_tick,
            {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "value": "64000", "timestamp": 1999},
            },
        ]
    )
    scenarios["duplicate_injection"] = _status_after_messages([base_tick, base_tick])
    scenarios["missing_source_timestamp"] = _status_after_messages(
        [
            base_tick,
            {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "value": "65010"},
            },
        ]
    )
    scenarios["revision_injection"] = _status_after_messages(
        [
            base_tick,
            {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "value": "65005", "timestamp": 2000},
            },
        ]
    )

    checks = {
        "normal_feed_ordered_positive": int(scenarios["normal_feed"].get("ordering_classification_counts", {}).get("ordered", 0))
        > 0,
        "out_of_order_classified": int(
            scenarios["out_of_order_injection"].get("ordering_classification_counts", {}).get("out_of_order", 0)
        )
        > 0,
        "duplicate_classified": int(
            scenarios["duplicate_injection"].get("ordering_classification_counts", {}).get("duplicate", 0)
        )
        > 0,
        "missing_source_classified": int(
            scenarios["missing_source_timestamp"].get("ordering_classification_counts", {}).get("missing_source_time", 0)
        )
        > 0,
        "revision_classified": int(
            scenarios["revision_injection"].get("ordering_classification_counts", {}).get("revision", 0)
        )
        > 0,
    }
    return {
        "scenarios": scenarios,
        "checks": checks,
        "ok": all(bool(value) for value in checks.values()),
    }


def _scenario_definitions() -> Dict[str, Dict[str, Any]]:
    return {
        "clean_canonical": {
            "builder": _clean_scenario_rows,
            "scenario_fixture_type": "bounded_approximation_fixture",
            "scenario_execution_purpose": "canonical_behavior_fixture",
            "scenario_realism_interpretation": (
                "maker_preferred_path_with_bounded_fill_fixture_not_venue_queue_realism"
            ),
            "expected_audits": {
                "websocket_hardening_audit": True,
                "time_discipline_audit": True,
                "paper_harness_audit": True,
                "order_lifecycle_audit": True,
            },
        },
        "reconnect_transport": {
            "builder": _reconnect_scenario_rows,
            "scenario_fixture_type": "degraded_behavior_fixture",
            "scenario_execution_purpose": "transport_reconnect_resilience_fixture",
            "scenario_realism_interpretation": "bounded_approximation_fixture_with_reconnect_recovery",
            "expected_audits": {
                "websocket_hardening_audit": True,
                "time_discipline_audit": True,
                "paper_harness_audit": True,
                "order_lifecycle_audit": True,
            },
        },
        "disorder_injected": {
            "builder": _disorder_scenario_rows,
            "scenario_fixture_type": "degraded_behavior_fixture",
            "scenario_execution_purpose": "ordering_disorder_classification_fixture",
            "scenario_realism_interpretation": "ingest_disorder_handling_demonstration_without_runtime_overclaim",
            "expected_audits": {
                "websocket_hardening_audit": True,
                "time_discipline_audit": True,
                "paper_harness_audit": True,
                "order_lifecycle_audit": True,
            },
        },
        "degraded_source_fallback_pressure": {
            "builder": _degraded_scenario_rows,
            "scenario_fixture_type": "degraded_behavior_fixture",
            "scenario_execution_purpose": "source_degradation_blocking_fixture",
            "scenario_realism_interpretation": "expected_guardrail_failure_fixture_not_success_path",
            "expected_audits": {
                "websocket_hardening_audit": False,
                "time_discipline_audit": True,
                "paper_harness_audit": False,
                "order_lifecycle_audit": True,
            },
        },
        "thin_liquidity_partial_fill": {
            "builder": _thin_liquidity_partial_fill_scenario_rows,
            "scenario_fixture_type": "bounded_approximation_fixture",
            "scenario_execution_purpose": "partial_fill_visibility_fixture",
            "scenario_realism_interpretation": "bounded_visible_liquidity_fill_fixture_not_full_venue_microstructure",
            "expected_audits": {
                "websocket_hardening_audit": True,
                "time_discipline_audit": True,
                "paper_harness_audit": True,
                "order_lifecycle_audit": True,
            },
        },
        "poor_truth_no_action_standdown": {
            "builder": _poor_truth_standdown_scenario_rows,
            "scenario_fixture_type": "degraded_behavior_fixture",
            "scenario_execution_purpose": "no_action_under_poor_truth_fixture",
            "scenario_realism_interpretation": "expected_standdown_failure_fixture_not_success_path",
            "expected_audits": {
                "websocket_hardening_audit": False,
                "time_discipline_audit": True,
                "paper_harness_audit": False,
                "order_lifecycle_audit": True,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic foundation scenario proof artifacts")
    parser.add_argument("--config", default="configs/profiles/paper_universal.yaml", help="Execution config path")
    parser.add_argument(
        "--out-root",
        default="logs_exec/paper_universal/foundation_scenarios",
        help="Scenario artifact root",
    )
    parser.add_argument("--timestamp", default="", help="Optional UTC timestamp tag (YYYYMMDDTHHMMSSZ)")
    args = parser.parse_args()

    timestamp = str(args.timestamp).strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scenario_root = pathlib.Path(args.out_root).resolve() / timestamp
    scenario_root.mkdir(parents=True, exist_ok=True)
    config_path = pathlib.Path(args.config).resolve()

    base_ts = dt.datetime(2026, 3, 30, 12, 0, 0, tzinfo=dt.timezone.utc)
    scenario_definitions = _scenario_definitions()
    scenario_results: Dict[str, Any] = {}

    for offset, (name, definition) in enumerate(scenario_definitions.items()):
        builder = definition["builder"]
        run_id = f"foundation-{name}-{timestamp.lower()}"
        scenario_dir = scenario_root / name
        status_rows, event_rows = builder(run_id, base_ts + dt.timedelta(minutes=offset))
        paths = _write_scenario(
            scenario_root=scenario_dir,
            run_id=run_id,
            status_rows=status_rows,
            event_rows=event_rows,
        )
        report_dir = scenario_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ws = run_websocket_hardening_audit(
            config_path=config_path,
            log_dir=scenario_dir,
            run_id=run_id,
            run_contract_path=paths["run_contract_path"],
            session_phase="validate_postrun",
            max_lines_per_file=0,
        )
        tm = run_time_discipline_audit(
            config_path=config_path,
            max_allowed_skew_sec=0.25,
            max_status_age_sec=3153600000.0,
            min_status_rows=1,
            log_dir=scenario_dir,
            run_id=run_id,
            run_contract_path=paths["run_contract_path"],
            session_phase="validate_postrun",
            max_lines_per_file=0,
        )
        ph = run_paper_harness_audit(
            config_path=config_path,
            log_dir=scenario_dir,
            run_id=run_id,
            skip_run_integrity=True,
            min_status_rows=1,
            max_status_age_sec=3153600000.0,
            max_lines_per_file=0,
            run_contract_path=paths["run_contract_path"],
            session_phase="validate_postrun",
        )
        lc = run_lifecycle_audit(
            config_path=config_path,
            log_dir=scenario_dir,
            run_id=run_id,
            run_contract_path=paths["run_contract_path"],
            session_phase="validate_postrun",
            max_lines_per_file=0,
        )
        audit_paths = {
            "websocket_hardening_audit": report_dir / "websocket_hardening_audit.json",
            "time_discipline_audit": report_dir / "time_discipline_audit.json",
            "paper_harness_audit": report_dir / "paper_harness_audit.json",
            "order_lifecycle_audit": report_dir / "order_lifecycle_audit.json",
        }
        _write_json(audit_paths["websocket_hardening_audit"], ws)
        _write_json(audit_paths["time_discipline_audit"], tm)
        _write_json(audit_paths["paper_harness_audit"], ph)
        _write_json(audit_paths["order_lifecycle_audit"], lc)
        observed_audits = {
            "websocket_hardening_audit": {"ok": bool(ws.get("ok", False)), "finding_count": int(ws.get("finding_count", 0))},
            "time_discipline_audit": {"ok": bool(tm.get("ok", False)), "finding_count": int(tm.get("finding_count", 0))},
            "paper_harness_audit": {"ok": bool(ph.get("ok", False)), "finding_count": int(ph.get("finding_count", 0))},
            "order_lifecycle_audit": {"ok": bool(lc.get("ok", False)), "finding_count": int(lc.get("finding_count", 0))},
        }
        expected_audits = {name: bool(value) for name, value in dict(definition.get("expected_audits", {})).items()}
        expectation_mismatches: List[str] = []
        for audit_name in AUDIT_SURFACE_NAMES:
            expected_ok = bool(expected_audits.get(audit_name, False))
            observed_ok = bool(observed_audits.get(audit_name, {}).get("ok", False))
            if observed_ok != expected_ok:
                expectation_mismatches.append(
                    f"{audit_name}:expected_ok={str(expected_ok).lower()}:observed_ok={str(observed_ok).lower()}"
                )
        expectation_match = len(expectation_mismatches) == 0
        scenario_results[name] = {
            "run_id": run_id,
            "scenario_fixture_type": str(definition.get("scenario_fixture_type") or ""),
            "scenario_execution_purpose": str(definition.get("scenario_execution_purpose") or ""),
            "scenario_realism_interpretation": str(definition.get("scenario_realism_interpretation") or ""),
            "paths": {key: str(path.resolve()) for key, path in paths.items()},
            "audits": observed_audits,
            "expected_audits": expected_audits,
            "scenario_expectation_match": bool(expectation_match),
            "scenario_expectation_mismatches": list(expectation_mismatches),
            "audit_paths": {key: str(path.resolve()) for key, path in audit_paths.items()},
        }

    injection_proof = _chainlink_injection_proof()
    required_scenarios = tuple(scenario_definitions.keys())
    required_present = set(required_scenarios).issubset(set(scenario_results.keys()))
    expected_success_scenarios = sorted(
        [
            name
            for name, definition in scenario_definitions.items()
            if all(bool(value) for value in dict(definition.get("expected_audits", {})).values())
        ]
    )
    expected_failure_scenarios = sorted(
        [name for name in scenario_definitions.keys() if name not in set(expected_success_scenarios)]
    )
    expectation_match_by_scenario = {
        name: bool((scenario_results.get(name) or {}).get("scenario_expectation_match", False))
        for name in required_scenarios
    }
    scenario_expectations_matched = required_present and all(bool(x) for x in expectation_match_by_scenario.values())
    proof_success_criteria = {
        "required_scenarios_present": bool(required_present),
        "scenario_audit_expectations_matched": bool(scenario_expectations_matched),
        "ingress_injection_checks_all_true": bool(injection_proof.get("ok", False)),
        "overall_success_rule": (
            "required_scenarios_present && scenario_audit_expectations_matched && ingress_injection_checks_all_true"
        ),
        "degraded_scenarios_are_expected_to_fail_some_audits": True,
    }
    summary = {
        "timestamp": timestamp,
        "config_path": str(config_path),
        "scenario_root": str(scenario_root),
        "scenario_expectation_mode": "expected_outcome_matrix",
        "expected_success_scenarios": list(expected_success_scenarios),
        "expected_failure_scenarios": list(expected_failure_scenarios),
        "proof_success_criteria": proof_success_criteria,
        "scenario_expectation_match_by_scenario": expectation_match_by_scenario,
        "scenarios": scenario_results,
        "ingress_injection_proof": injection_proof,
        "ok": bool(
            bool(proof_success_criteria["required_scenarios_present"])
            and bool(proof_success_criteria["scenario_audit_expectations_matched"])
            and bool(proof_success_criteria["ingress_injection_checks_all_true"])
        ),
    }
    summary_path = scenario_root / f"foundation_scenario_proof_{timestamp}.json"
    _write_json(summary_path, summary)
    print(json.dumps({"ok": summary["ok"], "summary_path": str(summary_path.resolve())}, sort_keys=True))


if __name__ == "__main__":
    main()
