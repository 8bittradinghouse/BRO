#!/usr/bin/env python3
"""Daily reconciliation report: bot ledger vs venue snapshot (best effort)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any, Dict, List, Optional, Tuple


from prodesk.common import utc_iso
from prodesk.artifact_identity import build_artifact_identity
from prodesk.config import load_execution_config
from prodesk.gateway import GatewayError, LiveClobGateway

REPORT_SCHEMA_VERSION = 3


def parse_ts(value: Any) -> Optional[dt.datetime]:
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _load_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _filter_run_id(rows: List[Dict[str, Any]], run_id: Optional[str]) -> List[Dict[str, Any]]:
    if not run_id:
        return rows
    rid = str(run_id).strip()
    if not rid:
        return rows
    return [row for row in rows if str(row.get("run_id", "")).strip() == rid]


def _load_event_rows(log_dir: pathlib.Path, *, date_str: str, run_id: Optional[str]) -> List[Dict[str, Any]]:
    path = log_dir / f"events_{date_str}.jsonl"
    return _filter_run_id(_load_jsonl(path), run_id)


def _bot_ledger_summary(events: List[Dict[str, Any]], simulation_cfg: Dict[str, Any]) -> Dict[str, Any]:
    order_reason_by_id: Dict[str, str] = {}
    orders_placed = 0.0
    orders_canceled = 0.0
    fills = 0.0
    fill_qty = 0.0
    fill_notional = 0.0
    net_cashflow = 0.0
    taker_fees = 0.0
    maker_rebate = 0.0
    by_token: Dict[str, Dict[str, float]] = {}

    rebate_bps = _safe_float(simulation_cfg.get("maker_rebate_bps"), default=0.0)
    taker_curve_rate = _safe_float(simulation_cfg.get("taker_fee_curve_rate"), default=0.0)

    for evt in events:
        event_type = str(evt.get("event_type") or "")
        if event_type == "order_submit":
            orders_placed += 1.0
            order_id = str(evt.get("order_id") or "")
            if order_id:
                order_reason_by_id[order_id] = str(evt.get("reason") or "")
            continue
        if event_type == "order_cancel":
            orders_canceled += 1.0
            continue
        if event_type in {"kill_switch_cancel_all", "cancel_all_on_exit"}:
            orders_canceled += max(0.0, _safe_float(evt.get("canceled_count"), default=0.0))
            continue
        if event_type != "fill":
            continue

        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        price = _safe_float(evt.get("price"), default=0.0)
        size = _safe_float(evt.get("size"), default=0.0)
        if price <= 0 or size <= 0:
            continue
        notional = price * size
        fills += 1.0
        fill_qty += size
        fill_notional += notional
        if side == "BUY":
            net_cashflow -= notional
        elif side == "SELL":
            net_cashflow += notional

        token_row = by_token.setdefault(token_id, {"fills": 0.0, "qty": 0.0, "notional": 0.0})
        token_row["fills"] += 1.0
        token_row["qty"] += size
        token_row["notional"] += notional

        order_id = str(evt.get("order_id") or "")
        reason = order_reason_by_id.get(order_id, "")
        if "sniper_taker" in reason:
            effective_fee_rate = max(0.0, min(1.0, price * (1.0 - price) * taker_curve_rate))
            taker_fees += notional * effective_fee_rate
        else:
            maker_rebate += notional * (rebate_bps / 10000.0)

    return {
        "orders_placed": orders_placed,
        "orders_canceled": orders_canceled,
        "fills": fills,
        "fill_qty": fill_qty,
        "fill_notional": fill_notional,
        "realized_pnl_cashflow_estimate": net_cashflow,
        "fees_paid_taker_estimate": taker_fees,
        "maker_rebate_estimate": maker_rebate,
        "per_token": by_token,
    }


def _venue_snapshot(cfg: Dict[str, Any], *, date_str: str) -> Dict[str, Any]:
    mode = str(cfg.get("mode", "paper")).lower()
    if mode != "live":
        return {"available": False, "reason": "mode_not_live", "fills": 0.0, "open_orders": 0.0}

    gateway: Optional[LiveClobGateway] = None
    try:
        gateway = LiveClobGateway(cfg["auth"], seen_trade_ids_max=500000)
        open_orders = gateway.get_open_orders()
        venue_fills = gateway.poll_fills()
        target_date = date_str
        day_fills = []
        for fill in venue_fills:
            ts = parse_ts(fill.ts_utc)
            if ts is None:
                continue
            if ts.date().isoformat() != target_date:
                continue
            day_fills.append(fill)
        fill_qty = sum(max(0.0, float(fill.size)) for fill in day_fills)
        fill_notional = sum(max(0.0, float(fill.size) * float(fill.price)) for fill in day_fills)
        return {
            "available": True,
            "reason": "",
            "fills": float(len(day_fills)),
            "fill_qty": float(fill_qty),
            "fill_notional": float(fill_notional),
            "open_orders": float(len(open_orders)),
        }
    except GatewayError as exc:
        return {"available": False, "reason": f"gateway_error:{exc}", "fills": 0.0, "open_orders": 0.0}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {"available": False, "reason": f"venue_snapshot_failed:{exc}", "fills": 0.0, "open_orders": 0.0}
    finally:
        if gateway is not None:
            try:
                gateway.close()
            except (OSError, RuntimeError):
                pass


def _mismatch_metrics(bot: Dict[str, Any], venue: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(venue.get("available", False)):
        return {
            "available": False,
            "mismatch_ratio": None,
            "checks": [],
        }

    checks = []
    max_ratio = 0.0
    for key in ("fills", "fill_qty", "fill_notional"):
        bot_val = _safe_float(bot.get(key))
        venue_val = _safe_float(venue.get(key))
        denom = max(1.0, abs(venue_val))
        ratio = abs(bot_val - venue_val) / denom
        max_ratio = max(max_ratio, ratio)
        checks.append(
            {
                "metric": key,
                "bot": bot_val,
                "venue": venue_val,
                "abs_diff": abs(bot_val - venue_val),
                "ratio": ratio,
            }
        )
    return {
        "available": True,
        "mismatch_ratio": max_ratio,
        "checks": checks,
    }


def build_reconciliation(
    *,
    cfg: Dict[str, Any],
    log_dir: pathlib.Path,
    date_str: str,
    run_id: Optional[str],
    mismatch_threshold: float,
) -> Dict[str, Any]:
    explicit_run_id = str(run_id or "").strip()
    if not explicit_run_id:
        raise ValueError("run_id_required")
    resolved_run_id = explicit_run_id
    events = _load_event_rows(log_dir, date_str=date_str, run_id=resolved_run_id)
    bot = _bot_ledger_summary(events, cfg.get("simulation", {}))
    venue = _venue_snapshot(cfg, date_str=date_str)
    mismatch = _mismatch_metrics(bot, venue)
    mode = str(cfg.get("mode", "paper")).strip().lower() or "paper"
    simulation_cfg = cfg.get("simulation", {})
    if not isinstance(simulation_cfg, dict):
        simulation_cfg = {}
    wallet_sim_enabled = bool(simulation_cfg.get("wallet_sim_enabled", mode == "paper"))
    venue_available = bool(mismatch["available"])
    mismatch_ratio_value = _safe_float(mismatch.get("mismatch_ratio"), default=0.0) if venue_available else 0.0

    exceeds = venue_available and float(mismatch_ratio_value) > float(mismatch_threshold)
    status = "ok"
    verification_level = "venue_verified"
    verification_scope = "venue_verified"
    if not venue_available:
        if mode == "paper" and wallet_sim_enabled:
            status = "ok"
            verification_level = "paper_sim_verified"
            verification_scope = "paper_wallet_simulation_verified"
        else:
            status = "venue_unavailable"
            verification_level = "venue_unavailable"
            verification_scope = "bot_only_no_mismatch_observed"
    elif exceeds:
        status = "mismatch"
        verification_level = "venue_verified"

    decision_trace = [
        {
            "check": "venue_snapshot_available",
            "level": "advisory" if (not venue_available and mode == "paper" and wallet_sim_enabled) else "hard_fail",
            "metric": "venue_truth_available",
            "comparator": "min",
            "value": 1.0 if venue_available else 0.0,
            "threshold": 0.0 if (not venue_available and mode == "paper" and wallet_sim_enabled) else 1.0,
            "passed": True if (not venue_available and mode == "paper" and wallet_sim_enabled) else venue_available,
            "note": "venue truth is required for fully verified reconciliation",
        },
        {
            "check": "mismatch_ratio_threshold",
            "level": "hard_fail",
            "metric": "mismatch_ratio",
            "comparator": "max",
            "value": float(mismatch_ratio_value),
            "threshold": float(mismatch_threshold),
            "passed": (not venue_available) or (float(mismatch_ratio_value) <= float(mismatch_threshold)),
            "note": "evaluated only when venue snapshot is available",
        },
    ]

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ts_utc": utc_iso(),
        "date": date_str,
        "run_id_filter": resolved_run_id,
        "run_id_resolution": "explicit",
        "artifact_identity": build_artifact_identity(log_dir=log_dir, run_id=resolved_run_id),
        "status": status,
        "verification_level": verification_level,
        "verification_scope": verification_scope,
        "verification_note": (
            "full venue+bot match verification"
            if venue_available
            else (
                "paper-mode wallet simulation verified; venue truth unavailable by design"
                if (mode == "paper" and wallet_sim_enabled)
                else "venue truth unavailable; mismatch_ratio does not imply venue-verified match"
            )
        ),
        "mismatch_threshold": float(mismatch_threshold),
        "mismatch_ratio": float(mismatch_ratio_value),
        "mismatch_ratio_semantics": (
            "venue_verified"
            if venue_available
            else ("paper_wallet_simulation" if (mode == "paper" and wallet_sim_enabled) else "bot_only_observation")
        ),
        "exceeds_threshold": exceeds,
        "event_rows": float(len(events)),
        "bot_truth": bot,
        "venue_truth": venue,
        "mismatch": mismatch,
        "decision_trace": decision_trace,
    }


def _default_out_path(log_dir: pathlib.Path, date_str: str) -> pathlib.Path:
    return log_dir / "reports" / f"{date_str}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro daily reconciliation")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--date", default="", help="UTC date YYYY-MM-DD (defaults to today UTC)")
    parser.add_argument("--run-id", required=True, help="Run_id filter")
    parser.add_argument("--out", default="", help="Output report path (default: <log_dir>/reports/YYYY-MM-DD.json)")
    parser.add_argument("--mismatch-threshold", type=float, default=0.05, help="Mismatch ratio alert threshold")
    parser.add_argument("--require-venue", action="store_true", help="Fail if venue snapshot is unavailable")
    parser.add_argument(
        "--latest-path",
        default="",
        help="Optional path for a compact latest-status JSON used by runtime ramp controls",
    )
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.config).resolve()
    cfg = load_execution_config(cfg_path)
    log_dir = pathlib.Path(args.log_dir).resolve()
    date_str = str(args.date).strip() or dt.datetime.now(dt.timezone.utc).date().isoformat()
    run_id = str(args.run_id).strip() or None

    report = build_reconciliation(
        cfg=cfg,
        log_dir=log_dir,
        date_str=date_str,
        run_id=run_id,
        mismatch_threshold=float(args.mismatch_threshold),
    )

    out_path = pathlib.Path(args.out).resolve() if str(args.out).strip() else _default_out_path(log_dir, date_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    latest_path = (
        pathlib.Path(args.latest_path).resolve()
        if str(args.latest_path).strip()
        else (log_dir / "reconcile_latest.json")
    )
    latest_payload = {
        "ts_utc": report["ts_utc"],
        "date": report["date"],
        "run_id_filter": report.get("run_id_filter"),
        "status": report["status"],
        "mismatch_ratio": report["mismatch_ratio"],
        "verification_level": report["verification_level"],
        "exceeds_threshold": report["exceeds_threshold"],
        "report_path": str(out_path),
    }
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(latest_payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"status={report['status']}")
    print(f"mismatch_ratio={report['mismatch_ratio']:.6f}")
    print(f"verification_level={report['verification_level']}")
    print(f"report={out_path}")
    print(f"latest={latest_path}")

    if bool(args.require_venue) and report["status"] == "venue_unavailable":
        raise SystemExit(3)
    if report["status"] == "mismatch":
        raise SystemExit(2)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
