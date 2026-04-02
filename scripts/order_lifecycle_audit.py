#!/usr/bin/env python3
"""Audit order lifecycle observability and execution-substrate evidence semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from prodesk.config import load_execution_config
from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import DEFAULT_MAX_LINES_PER_FILE, load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.runtime_semantics import RUNTIME_STATE_SAFETY_HALT
from prodesk.session_phase import enforce_validation_phase

ORDER_SUBMIT = "order_submit"
ORDER_CANCEL = "order_cancel"
FILL = "fill"
KILL_SWITCH_CANCEL_ALL = "kill_switch_cancel_all"
CANCEL_ALL_ON_EXIT = "cancel_all_on_exit"
RUNTIME_STATE_TRANSITION = "runtime_state_transition"
OPERATING_MODE_TRANSITION = "operating_mode_transition"
WS_SLO_STATE = "ws_slo_state"
KILL_SWITCH_EXPECTED_RUNTIME_STATE = str(RUNTIME_STATE_SAFETY_HALT)
EDGE_EVALUATION = "edge_evaluation"
CHAINLINK_TICK = "chainlink_tick"
ORDER_DECISION_LINK_WINDOW_SEC = 30.0
INGEST_DECISION_LINK_WINDOW_SEC = 300.0

LIFECYCLE_EVENT_TYPES = frozenset(
    {
        ORDER_SUBMIT,
        ORDER_CANCEL,
        FILL,
        KILL_SWITCH_CANCEL_ALL,
        CANCEL_ALL_ON_EXIT,
        RUNTIME_STATE_TRANSITION,
        OPERATING_MODE_TRANSITION,
        WS_SLO_STATE,
    }
)

RUNTIME_TRANSITION_REASON_CODES = frozenset(
    {
        "runtime_state_changed",
        "book_requirement_changed",
        "kill_switch_engaged",
        "targets_activated",
        "targets_absent",
    }
)

REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    ORDER_SUBMIT: (
        "ts_utc",
        "order_id",
        "token_id",
        "side",
        "price",
        "size",
        "reason_code",
        "execution_preference",
        "market_id",
        "window_id",
        "stage",
    ),
    ORDER_CANCEL: ("ts_utc", "order_id", "reason"),
    FILL: ("ts_utc", "trade_id", "order_id", "token_id", "side", "price", "size", "source"),
    KILL_SWITCH_CANCEL_ALL: ("ts_utc", "reason", "canceled_count", "released_lock_count"),
    CANCEL_ALL_ON_EXIT: ("ts_utc", "reason", "canceled_count", "released_lock_count"),
    RUNTIME_STATE_TRANSITION: (
        "ts_utc",
        "previous_runtime_state",
        "runtime_state",
        "active_targets_present",
        "no_target_standdown",
        "previous_book_feed_required",
        "book_feed_required",
        "kill_switch",
        "transition_reason_code",
        "transition_reason_detail",
    ),
    OPERATING_MODE_TRANSITION: ("ts_utc", "state", "previous_state", "reason"),
    WS_SLO_STATE: ("ts_utc", "degraded", "reasons"),
}

OPTIONAL_CANCEL_ALL_COUNT_FIELDS = (
    "gateway_reported_canceled_count",
    "open_before_count",
    "open_after_count",
    "unconfirmed_open_count",
)

EXECUTION_PREFERENCES = frozenset({"maker_preferred", "taker_only"})
SIDES = frozenset({"BUY", "SELL"})
BOOLEAN_FIELDS = frozenset(
    {
        "active_targets_present",
        "no_target_standdown",
        "previous_book_feed_required",
        "book_feed_required",
        "kill_switch",
        "degraded",
    }
)


def _sha256_payload(payload: Any) -> str:
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


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _non_empty_text(value: Any) -> str:
    return str(value or "").strip()


def _row_timestamp(row: Dict[str, Any]) -> Optional[dt.datetime]:
    ts_event = _parse_ts(row.get("ts_event_utc"))
    if ts_event is not None:
        return ts_event
    return _parse_ts(row.get("ts_utc"))


def _has_time_link(
    *,
    source_ts: Optional[dt.datetime],
    candidate_ts: Optional[dt.datetime],
    window_sec: float,
) -> bool:
    if source_ts is None or candidate_ts is None:
        return False
    return abs((source_ts - candidate_ts).total_seconds()) <= max(0.0, float(window_sec))


def _row_value_present(row: Dict[str, Any], field: str) -> bool:
    return field in row


def _event_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        str(row.get("ts_utc") or ""),
        str(row.get("event_type") or ""),
        str(row.get("order_id") or ""),
        str(row.get("trade_id") or ""),
        str(row.get("token_id") or ""),
        str(row.get("reason_code") or ""),
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
    )


def _normalize_lifecycle_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        normalized.append({key: row.get(key) for key in sorted(row.keys())})
    normalized.sort(key=_event_sort_key)
    return normalized


def _load_run_scoped_events(
    *,
    event_paths: List[pathlib.Path],
    run_id: str,
    contract: Optional[Dict[str, Any]],
    max_lines_per_file: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in load_jsonl(event_paths, max_lines_per_file=max(0, int(max_lines_per_file))):
        if str(row.get("run_id") or "").strip() != run_id:
            continue
        rows.append(row)
    return apply_contract_bounds(rows, contract)


def run_audit(
    *,
    config_path: pathlib.Path,
    log_dir: Optional[pathlib.Path] = None,
    run_id: Optional[str] = None,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="order_lifecycle_audit", session_phase=session_phase)
    cfg = load_execution_config(config_path.resolve())

    findings: List[str] = []
    warnings: List[str] = []

    configured_log_dir = pathlib.Path(str(cfg.get("storage", {}).get("log_dir", "./logs_exec"))).resolve()
    effective_log_dir = log_dir.resolve() if isinstance(log_dir, pathlib.Path) else configured_log_dir
    selected_run_id = str(run_id or "").strip() or None
    run_id_resolution = "explicit" if selected_run_id else "missing"
    resolved_contract_path = ""
    contract: Optional[Dict[str, Any]] = None

    if run_contract_path is not None:
        resolve_log_dir = effective_log_dir if effective_log_dir is not None else pathlib.Path(".").resolve()
        try:
            contract = resolve_run_contract(
                log_dir=resolve_log_dir,
                run_id=selected_run_id,
                run_contract_path_override=run_contract_path,
                allow_open=(normalized_phase == "validate_active"),
            )
        except Exception as exc:
            findings.append(str(exc))
            contract = None
        if isinstance(contract, dict):
            resolved_contract_path = str(contract.get("_path") or "")
            if not selected_run_id:
                selected_run_id = str(contract.get("run_id") or "").strip() or None
                if selected_run_id:
                    run_id_resolution = "contract"
            log_root = str(contract.get("log_root") or "").strip()
            if log_root:
                effective_log_dir = pathlib.Path(log_root).expanduser().resolve()

    if not selected_run_id:
        findings.append("run_id_required")
        return {
            "ok": False,
            "session_phase": normalized_phase,
            "run_id_filter": "",
            "run_id_resolution": run_id_resolution,
            "config_path": str(config_path.resolve()),
            "configured_log_dir": str(configured_log_dir),
            "log_dir": str(effective_log_dir),
            "run_contract_path": resolved_contract_path,
            "finding_count": len(findings),
            "warning_count": len(warnings),
            "findings": findings,
            "warnings": warnings,
            "error_codes": summarize_error_codes(findings),
        }

    event_paths = sorted(effective_log_dir.glob("events_*.jsonl"))
    if isinstance(contract, dict):
        events_slice = run_contract_slice_path(contract, stream="events")
        if events_slice is not None:
            event_paths = [events_slice]
    if not event_paths:
        findings.append("events_files_missing")

    events = _load_run_scoped_events(
        event_paths=event_paths,
        run_id=selected_run_id,
        contract=contract,
        max_lines_per_file=max_lines_per_file,
    )
    lifecycle_rows = [row for row in events if str(row.get("event_type") or "").strip() in LIFECYCLE_EVENT_TYPES]
    if not lifecycle_rows:
        warnings.append("lifecycle_events_missing")

    counts: Dict[str, int] = {name: 0 for name in sorted(LIFECYCLE_EVENT_TYPES)}
    submit_order_ids: Set[str] = set()
    duplicate_submit_order_ids: Set[str] = set()
    fill_trade_ids: Set[str] = set()
    duplicate_trade_ids: Set[str] = set()
    fill_without_submit_order_ids: Set[str] = set()
    cancel_without_submit_order_ids: Set[str] = set()
    order_submit_rows_for_linkage: List[Dict[str, Any]] = []

    for row in lifecycle_rows:
        event_type = str(row.get("event_type") or "").strip()
        counts[event_type] = counts.get(event_type, 0) + 1

        required = REQUIRED_FIELDS.get(event_type, ())
        for field in required:
            if not _row_value_present(row, field):
                findings.append(f"{event_type}:missing_required_field:{field}")

        ts = _parse_ts(row.get("ts_utc"))
        if ts is None:
            findings.append(f"{event_type}:invalid_ts_utc")
        event_ts = _parse_ts(row.get("ts_event_utc"))
        if event_ts is None:
            findings.append(f"{event_type}:invalid_or_missing_ts_event_utc")
        elif ts is not None and event_ts != ts:
            findings.append(f"{event_type}:ts_event_utc_mismatch")

        if event_type == ORDER_SUBMIT:
            order_id = _non_empty_text(row.get("order_id"))
            token_id = _non_empty_text(row.get("token_id"))
            side = _non_empty_text(row.get("side")).upper()
            reason_code = _non_empty_text(row.get("reason_code"))
            execution_preference = _non_empty_text(row.get("execution_preference"))
            market_id = _non_empty_text(row.get("market_id"))
            window_id = _non_empty_text(row.get("window_id"))
            if not order_id:
                findings.append("order_submit:order_id_missing_or_blank")
            if not token_id:
                findings.append("order_submit:token_id_missing_or_blank")
            if side not in SIDES:
                findings.append(f"order_submit:side_invalid:{side or '<empty>'}")
            if not reason_code:
                findings.append("order_submit:reason_code_missing_or_blank")
            if execution_preference not in EXECUTION_PREFERENCES:
                findings.append(f"order_submit:execution_preference_invalid:{execution_preference or '<empty>'}")
            if not market_id:
                findings.append("order_submit:market_id_missing_or_blank")
            if not window_id:
                findings.append("order_submit:window_id_missing_or_blank")
            price = _safe_float(row.get("price"))
            size = _safe_float(row.get("size"))
            if price is None or price <= 0:
                findings.append(f"order_submit:price_invalid:{row.get('price')!r}")
            if size is None or size <= 0:
                findings.append(f"order_submit:size_invalid:{row.get('size')!r}")
            if order_id:
                if order_id in submit_order_ids:
                    duplicate_submit_order_ids.add(order_id)
                submit_order_ids.add(order_id)
                order_submit_rows_for_linkage.append(
                    {
                        "order_id": order_id,
                        "token_id": token_id,
                        "execution_preference": execution_preference,
                        "ts": _row_timestamp(row),
                    }
                )

        elif event_type == ORDER_CANCEL:
            order_id = _non_empty_text(row.get("order_id"))
            reason = _non_empty_text(row.get("reason"))
            if not order_id:
                findings.append("order_cancel:order_id_missing_or_blank")
            if not reason:
                findings.append("order_cancel:reason_missing_or_blank")
            if order_id and order_id not in submit_order_ids:
                cancel_without_submit_order_ids.add(order_id)

        elif event_type == FILL:
            trade_id = _non_empty_text(row.get("trade_id"))
            order_id = _non_empty_text(row.get("order_id"))
            token_id = _non_empty_text(row.get("token_id"))
            side = _non_empty_text(row.get("side")).upper()
            source = _non_empty_text(row.get("source"))
            if not trade_id:
                findings.append("fill:trade_id_missing_or_blank")
            if not order_id:
                findings.append("fill:order_id_missing_or_blank")
            if not token_id:
                findings.append("fill:token_id_missing_or_blank")
            if side not in SIDES:
                findings.append(f"fill:side_invalid:{side or '<empty>'}")
            if not source:
                findings.append("fill:source_missing_or_blank")
            price = _safe_float(row.get("price"))
            size = _safe_float(row.get("size"))
            if price is None or price <= 0:
                findings.append(f"fill:price_invalid:{row.get('price')!r}")
            if size is None or size <= 0:
                findings.append(f"fill:size_invalid:{row.get('size')!r}")
            if trade_id:
                if trade_id in fill_trade_ids:
                    duplicate_trade_ids.add(trade_id)
                fill_trade_ids.add(trade_id)
            if order_id and order_id not in submit_order_ids:
                fill_without_submit_order_ids.add(order_id)

        elif event_type in {KILL_SWITCH_CANCEL_ALL, CANCEL_ALL_ON_EXIT}:
            reason = _non_empty_text(row.get("reason"))
            if not reason:
                findings.append(f"{event_type}:reason_missing_or_blank")
            for field in ("canceled_count", "released_lock_count"):
                value = _safe_float(row.get(field))
                if value is None or value < 0:
                    findings.append(f"{event_type}:{field}_invalid:{row.get(field)!r}")
            for field in OPTIONAL_CANCEL_ALL_COUNT_FIELDS:
                if field not in row:
                    warnings.append(f"{event_type}:missing_optional_count_field:{field}")
                    continue
                value = _safe_float(row.get(field))
                if value is None or value < 0:
                    findings.append(f"{event_type}:{field}_invalid:{row.get(field)!r}")
            open_after = _safe_float(row.get("open_after_count"))
            if open_after is not None and open_after > 0:
                warnings.append(f"{event_type}:open_after_nonzero:{int(open_after)}")

        elif event_type == RUNTIME_STATE_TRANSITION:
            runtime_state = _non_empty_text(row.get("runtime_state"))
            previous_runtime_state = _non_empty_text(row.get("previous_runtime_state"))
            reason_code = _non_empty_text(row.get("transition_reason_code"))
            reason_detail = _non_empty_text(row.get("transition_reason_detail"))
            if not previous_runtime_state:
                findings.append("runtime_state_transition:previous_runtime_state_missing_or_blank")
            if not runtime_state:
                findings.append("runtime_state_transition:runtime_state_missing_or_blank")
            if reason_code not in RUNTIME_TRANSITION_REASON_CODES:
                findings.append(f"runtime_state_transition:transition_reason_code_invalid:{reason_code or '<empty>'}")
            if not reason_detail:
                findings.append("runtime_state_transition:transition_reason_detail_missing_or_blank")
            for field in BOOLEAN_FIELDS:
                if field in row and not isinstance(row.get(field), bool):
                    findings.append(f"runtime_state_transition:boolean_field_not_bool:{field}")
            if bool(row.get("kill_switch")) and runtime_state != KILL_SWITCH_EXPECTED_RUNTIME_STATE:
                findings.append(
                    f"runtime_state_transition:kill_switch_runtime_state_mismatch:{runtime_state or '<empty>'}"
                )

        elif event_type == OPERATING_MODE_TRANSITION:
            state = _non_empty_text(row.get("state"))
            previous_state = _non_empty_text(row.get("previous_state"))
            reason = _non_empty_text(row.get("reason"))
            if not state:
                findings.append("operating_mode_transition:state_missing_or_blank")
            if not previous_state:
                findings.append("operating_mode_transition:previous_state_missing_or_blank")
            if not reason:
                findings.append("operating_mode_transition:reason_missing_or_blank")

        elif event_type == WS_SLO_STATE:
            reasons = row.get("reasons")
            if not isinstance(row.get("degraded"), bool):
                findings.append("ws_slo_state:degraded_not_bool")
            if not isinstance(reasons, list):
                findings.append("ws_slo_state:reasons_not_list")
                reasons = []
            if bool(row.get("degraded")) and len(reasons) == 0:
                findings.append("ws_slo_state:degraded_without_reason")

    if duplicate_submit_order_ids:
        findings.append(
            "order_submit:duplicate_order_id:"
            + ",".join(sorted(str(order_id) for order_id in duplicate_submit_order_ids))
        )
    if duplicate_trade_ids:
        findings.append(
            "fill:duplicate_trade_id:" + ",".join(sorted(str(trade_id) for trade_id in duplicate_trade_ids))
        )
    if fill_without_submit_order_ids:
        findings.append(
            "fill:order_id_without_submit:"
            + ",".join(sorted(str(order_id) for order_id in fill_without_submit_order_ids))
        )
    if cancel_without_submit_order_ids:
        warnings.append(
            "order_cancel:order_id_without_submit:"
            + ",".join(sorted(str(order_id) for order_id in cancel_without_submit_order_ids))
        )

    chainlink_tick_rows = [row for row in events if str(row.get("event_type") or "").strip() == CHAINLINK_TICK]
    edge_action_rows = [
        row
        for row in events
        if str(row.get("event_type") or "").strip() == EDGE_EVALUATION
        and str(row.get("action_taken") or "").strip().lower() in {"maker", "taker"}
    ]
    chainlink_tick_ts = [_row_timestamp(row) for row in chainlink_tick_rows]
    edge_action_for_linkage: List[Dict[str, Any]] = [
        {
            "token_id": _non_empty_text(row.get("token_id")),
            "action_taken": str(row.get("action_taken") or "").strip().lower(),
            "ts": _row_timestamp(row),
        }
        for row in edge_action_rows
    ]

    missing_order_decision_links: Set[str] = set()
    linked_order_submit_ids: Set[str] = set()
    linked_edge_action_indices: Set[int] = set()
    if edge_action_for_linkage:
        for submit in order_submit_rows_for_linkage:
            order_id = str(submit.get("order_id") or "")
            token_id = str(submit.get("token_id") or "")
            submit_ts = submit.get("ts")
            execution_preference = str(submit.get("execution_preference") or "")
            expected_action = "taker" if execution_preference == "taker_only" else "maker"
            matched = False
            for idx, edge in enumerate(edge_action_for_linkage):
                if token_id and token_id != str(edge.get("token_id") or ""):
                    continue
                if expected_action != str(edge.get("action_taken") or ""):
                    continue
                if not _has_time_link(
                    source_ts=submit_ts,
                    candidate_ts=edge.get("ts"),
                    window_sec=ORDER_DECISION_LINK_WINDOW_SEC,
                ):
                    continue
                linked_order_submit_ids.add(order_id)
                linked_edge_action_indices.add(idx)
                matched = True
                break
            if not matched and order_id:
                missing_order_decision_links.add(order_id)

    if edge_action_for_linkage and missing_order_decision_links:
        findings.append(
            "order_submit:missing_edge_decision_link:"
            + ",".join(sorted(str(order_id) for order_id in missing_order_decision_links))
        )

    missing_edge_submit_links: List[str] = []
    if order_submit_rows_for_linkage and edge_action_for_linkage:
        for idx, edge in enumerate(edge_action_for_linkage):
            if idx in linked_edge_action_indices:
                continue
            token_id = str(edge.get("token_id") or "")
            edge_ts = edge.get("ts")
            action_taken = str(edge.get("action_taken") or "")
            expected_pref = "taker_only" if action_taken == "taker" else "maker_preferred"
            matched = False
            for submit in order_submit_rows_for_linkage:
                if token_id and str(submit.get("token_id") or "") != token_id:
                    continue
                if str(submit.get("execution_preference") or "") != expected_pref:
                    continue
                if not _has_time_link(
                    source_ts=edge_ts,
                    candidate_ts=submit.get("ts"),
                    window_sec=ORDER_DECISION_LINK_WINDOW_SEC,
                ):
                    continue
                matched = True
                break
            if not matched:
                missing_edge_submit_links.append(f"{token_id or '<missing_token>'}:{action_taken or '<missing_action>'}")
    if order_submit_rows_for_linkage and edge_action_for_linkage and missing_edge_submit_links:
        findings.append("edge_decision:missing_order_submit_link:" + ",".join(sorted(missing_edge_submit_links)))

    missing_edge_ingest_links: List[str] = []
    if edge_action_for_linkage and chainlink_tick_rows:
        for edge in edge_action_for_linkage:
            edge_ts = edge.get("ts")
            matched = False
            for ingest_ts in chainlink_tick_ts:
                if _has_time_link(
                    source_ts=edge_ts,
                    candidate_ts=ingest_ts,
                    window_sec=INGEST_DECISION_LINK_WINDOW_SEC,
                ):
                    matched = True
                    break
            if not matched:
                missing_edge_ingest_links.append(
                    f"{str(edge.get('token_id') or '<missing_token>')}:{str(edge.get('action_taken') or '<missing_action>')}"
                )
    if edge_action_for_linkage and chainlink_tick_rows and missing_edge_ingest_links:
        findings.append("edge_decision:missing_chainlink_ingest_link:" + ",".join(sorted(missing_edge_ingest_links)))
    if not chainlink_tick_rows:
        warnings.append("chainlink_tick_events_missing_for_lifecycle_linkage")
    if not edge_action_for_linkage:
        warnings.append("edge_action_events_missing_for_lifecycle_linkage")

    normalized_rows = _normalize_lifecycle_rows(lifecycle_rows)
    lifecycle_records_sha256 = _sha256_payload(normalized_rows)
    required_fields_sha256 = _sha256_payload({k: list(v) for k, v in sorted(REQUIRED_FIELDS.items())})
    audit_rule_set_sha256 = _sha256_payload(
        {
            "lifecycle_event_types": sorted(LIFECYCLE_EVENT_TYPES),
            "runtime_transition_reason_codes": sorted(RUNTIME_TRANSITION_REASON_CODES),
            "execution_preferences": sorted(EXECUTION_PREFERENCES),
            "sides": sorted(SIDES),
            "boolean_fields": sorted(BOOLEAN_FIELDS),
            "optional_cancel_all_count_fields": sorted(OPTIONAL_CANCEL_ALL_COUNT_FIELDS),
            "kill_switch_expected_runtime_state": KILL_SWITCH_EXPECTED_RUNTIME_STATE,
        }
    )

    return {
        "ok": len(findings) == 0,
        "config_path": str(config_path.resolve()),
        "session_phase": normalized_phase,
        "configured_log_dir": str(configured_log_dir),
        "log_dir": str(effective_log_dir),
        "run_contract_path": resolved_contract_path,
        "run_id_filter": str(selected_run_id),
        "run_id_resolution": run_id_resolution,
        "event_source_paths": [str(path.resolve()) for path in event_paths],
        "events_considered": int(len(events)),
        "lifecycle_events_considered": int(len(lifecycle_rows)),
        "lifecycle_counts": counts,
        "duplicate_order_submit_ids": sorted(duplicate_submit_order_ids),
        "duplicate_fill_trade_ids": sorted(duplicate_trade_ids),
        "fill_without_submit_order_ids": sorted(fill_without_submit_order_ids),
        "cancel_without_submit_order_ids": sorted(cancel_without_submit_order_ids),
        "chainlink_tick_events_considered": int(len(chainlink_tick_rows)),
        "edge_action_events_considered": int(len(edge_action_for_linkage)),
        "order_submit_decision_linked_count": int(len(linked_order_submit_ids)),
        "order_submit_decision_missing_count": int(len(missing_order_decision_links)),
        "edge_decision_submit_missing_count": int(len(missing_edge_submit_links)),
        "edge_decision_ingest_missing_count": int(len(missing_edge_ingest_links)),
        "lifecycle_records_sha256": lifecycle_records_sha256,
        "required_fields_sha256": required_fields_sha256,
        "audit_rule_set_sha256": audit_rule_set_sha256,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "error_codes": summarize_error_codes(findings),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO order lifecycle observability audit")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--log-dir", default="", help="Optional explicit log dir override")
    parser.add_argument("--run-id", default="", help="Explicit run_id filter")
    parser.add_argument("--run-contract", default="", help="Explicit run contract path")
    parser.add_argument("--session-phase", default="validate_postrun", help="Validation phase context")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Max lines per jsonl file (0 means full file)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        config_path=pathlib.Path(args.config),
        log_dir=(pathlib.Path(args.log_dir).resolve() if str(args.log_dir).strip() else None),
        run_id=(str(args.run_id).strip() or None),
        run_contract_path=(pathlib.Path(args.run_contract).resolve() if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if bool(result.get("ok", False)) else 2)


if __name__ == "__main__":
    main()
