#!/usr/bin/env python3
"""Generate a compact forensic snapshot for one run_id."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any, Dict, Optional

from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.session_phase import enforce_validation_phase


def _parse_ts(value: Any) -> Optional[dt.datetime]:
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


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_run_rows(paths: list[pathlib.Path], run_id: str) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    target = str(run_id or "").strip()
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if target and str(row.get("run_id") or "").strip() != target:
                        continue
                    out.append(row)
        except Exception:
            continue
    return out


def run_snapshot(
    log_dir: pathlib.Path,
    run_id: str,
    *,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(
        validation_name="forensic_snapshot",
        session_phase=session_phase,
    )
    resolved_log_dir = log_dir.resolve()
    selected_run_id = str(run_id or "").strip()
    contract = resolve_run_contract(
        log_dir=resolved_log_dir,
        run_id=selected_run_id or None,
        run_contract_path_override=run_contract_path,
        allow_open=(normalized_phase == "validate_active"),
    )

    status_slice = run_contract_slice_path(contract, stream="status") if isinstance(contract, dict) else None
    events_slice = run_contract_slice_path(contract, stream="events") if isinstance(contract, dict) else None
    status_files = [status_slice] if status_slice is not None else sorted(resolved_log_dir.glob("status_*.jsonl"))
    events_files = [events_slice] if events_slice is not None else sorted(resolved_log_dir.glob("events_*.jsonl"))
    scoped_status_rows = _load_run_rows(status_files, selected_run_id)
    scoped_event_rows = _load_run_rows(events_files, selected_run_id)
    if isinstance(contract, dict):
        scoped_status_rows = apply_contract_bounds(scoped_status_rows, contract)
        scoped_event_rows = apply_contract_bounds(scoped_event_rows, contract)

    first_ts: Optional[dt.datetime] = None
    last_ts: Optional[dt.datetime] = None
    status_rows = 0
    kill_rows = 0
    guard_rows = 0
    chainlink_age_max = 0.0
    book_age_max = 0.0
    chainlink_disconnected_rows = 0
    book_disconnected_rows = 0

    fills = orders = cancels = risk_rejects = 0
    risk_pos = risk_notional = risk_stale = 0
    pnl_first: Optional[float] = None
    pnl_last: Optional[float] = None
    pnl_min: Optional[float] = None
    pnl_max: Optional[float] = None

    sniper_mode_max = 0.0
    sniper_tokens_max = 0.0
    sniper_lag_verified_max = 0.0
    latency_state_max = 0.0
    latency_samples_max = 0.0
    latency_inactive_max = 0.0

    event_counts = {
        "fill": 0,
        "order_submit": 0,
        "order_cancel": 0,
        "risk_reject": 0,
        "sniper_taker_submit": 0,
        "edge_evaluation": 0,
        "kill_switch_cancel_all": 0,
        "alert_policy_auto_stop": 0,
        "alert_policy_page": 0,
        "alert_policy_warn": 0,
        "latency_sampling_inactive": 0,
    }
    edge_breakdown: Dict[str, int] = {}

    for row in scoped_status_rows:
        ts = _parse_ts(row.get("ts_utc"))
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        status_rows += 1

        if bool(row.get("kill_switch", False)):
            kill_rows += 1
        external_guard = row.get("external_guard") if isinstance(row.get("external_guard"), dict) else {}
        if bool(external_guard.get("active", False)) or bool(row.get("guard_stop", False)):
            guard_rows += 1

        chainlink = row.get("chainlink") if isinstance(row.get("chainlink"), dict) else {}
        book_feed = row.get("book_feed") if isinstance(row.get("book_feed"), dict) else {}
        chainlink_age_max = max(chainlink_age_max, _to_float(chainlink.get("last_tick_age_sec")))
        book_age_max = max(book_age_max, _to_float(book_feed.get("last_msg_age_sec")))
        if chainlink.get("connected") is False:
            chainlink_disconnected_rows += 1
        if book_feed.get("connected") is False:
            book_disconnected_rows += 1

        fills = max(fills, int(_to_float(row.get("counter.fills"))))
        orders = max(orders, int(_to_float(row.get("counter.orders_submitted"))))
        cancels = max(cancels, int(_to_float(row.get("counter.orders_canceled"))))
        risk_rejects = max(risk_rejects, int(_to_float(row.get("counter.risk_rejects"))))
        risk_pos = max(risk_pos, int(_to_float(row.get("counter.risk_reject_position_cap"))))
        risk_notional = max(risk_notional, int(_to_float(row.get("counter.risk_reject_notional_cap"))))
        risk_stale = max(risk_stale, int(_to_float(row.get("counter.risk_reject_stale_book"))))

        pnl = _to_float(row.get("gauge.total_pnl"))
        if pnl_first is None:
            pnl_first = pnl
        pnl_last = pnl
        pnl_min = pnl if pnl_min is None else min(pnl_min, pnl)
        pnl_max = pnl if pnl_max is None else max(pnl_max, pnl)

        sniper_mode_max = max(sniper_mode_max, _to_float(row.get("gauge.sniper_mode_active")))
        sniper_tokens_max = max(sniper_tokens_max, _to_float(row.get("gauge.sniper_token_count")))
        sniper_lag_verified_max = max(
            sniper_lag_verified_max, _to_float(row.get("gauge.sniper_lag_verified_token_count"))
        )
        latency_state_max = max(latency_state_max, _to_float(row.get("gauge.latency_verifier_state")))
        latency_samples_max = max(latency_samples_max, _to_float(row.get("gauge.latency_verifier_sample_count")))
        latency_inactive_max = max(
            latency_inactive_max, _to_float(row.get("gauge.latency_sampling_inactive_cycles"))
        )

    for row in scoped_event_rows:
        event_type = str(row.get("event_type") or "")
        if event_type in event_counts:
            event_counts[event_type] += 1
        if event_type == "edge_evaluation":
            action = str(row.get("action_taken") or "").strip().lower()
            if action == "none":
                reason = str(row.get("block_reason") or "").strip() or "missing"
                edge_breakdown[reason] = edge_breakdown.get(reason, 0) + 1

    duration_min = None
    if first_ts is not None and last_ts is not None:
        duration_min = round((last_ts - first_ts).total_seconds() / 60.0, 3)

    return {
        "run_id": selected_run_id,
        "session_phase": normalized_phase,
        "run_contract_path": str(contract.get("_path", "")) if isinstance(contract, dict) else "",
        "window": {
            "first_ts_utc": first_ts.isoformat().replace("+00:00", "Z") if first_ts is not None else "",
            "last_ts_utc": last_ts.isoformat().replace("+00:00", "Z") if last_ts is not None else "",
            "duration_min": duration_min,
            "status_rows": status_rows,
        },
        "safety": {
            "kill_rows": kill_rows,
            "guard_rows": guard_rows,
        },
        "feed_health": {
            "chainlink_age_max_sec": chainlink_age_max,
            "book_age_max_sec": book_age_max,
            "chainlink_disconnected_rows": chainlink_disconnected_rows,
            "book_disconnected_rows": book_disconnected_rows,
        },
        "execution": {
            "fills": fills,
            "orders_submitted": orders,
            "orders_canceled": cancels,
        },
        "risk": {
            "risk_rejects": risk_rejects,
            "risk_reject_position_cap": risk_pos,
            "risk_reject_notional_cap": risk_notional,
            "risk_reject_stale_book": risk_stale,
        },
        "pnl": {
            "first": pnl_first,
            "last": pnl_last,
            "min": pnl_min,
            "max": pnl_max,
        },
        "sniper": {
            "sniper_mode_max": sniper_mode_max,
            "sniper_token_count_max": sniper_tokens_max,
            "sniper_lag_verified_token_count_max": sniper_lag_verified_max,
        },
        "latency": {
            "latency_state_max": latency_state_max,
            "latency_sample_count_max": latency_samples_max,
            "latency_sampling_inactive_cycles_max": latency_inactive_max,
        },
        "events": event_counts,
        "edge_block_breakdown": edge_breakdown,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro forensic snapshot")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Log directory with status/events JSONL")
    parser.add_argument("--run-id", required=True, help="Target run_id")
    parser.add_argument("--run-contract", default="", help="Optional run contract JSON path for deterministic replay")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    report = run_snapshot(
        pathlib.Path(args.log_dir).resolve(),
        str(args.run_id).strip(),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
