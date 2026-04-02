#!/usr/bin/env python3
"""Generate desk-style trade performance report from execution logs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from collections import defaultdict
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


def _parse_ts(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = dt.datetime.fromisoformat(text)
    except Exception:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc)


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return 0.0
    if out != out:
        return 0.0
    return out


def _load_jsonl(paths: List[pathlib.Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for text in lines:
            text = text.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _filter_rows(rows: List[Dict[str, Any]], *, run_id: Optional[str], date_str: str) -> List[Dict[str, Any]]:
    target_run = str(run_id or "").strip()
    target_date = str(date_str or "").strip()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if target_run and str(row.get("run_id") or "").strip() != target_run:
            continue
        if target_date:
            ts = _parse_ts(row.get("ts_utc"))
            if ts is None or ts.date().isoformat() != target_date:
                continue
        out.append(row)
    return out


def build_report(*, log_dir: pathlib.Path, run_id: Optional[str], date_str: str) -> Dict[str, Any]:
    event_files = sorted(log_dir.glob("events_*.jsonl"))
    events = _filter_rows(_load_jsonl(event_files), run_id=run_id, date_str=date_str)

    orders_submitted = 0.0
    orders_canceled = 0.0
    fills = 0.0
    buy_count = 0.0
    sell_count = 0.0
    buy_notional = 0.0
    sell_notional = 0.0
    buy_qty = 0.0
    sell_qty = 0.0
    token_book_mid: Dict[str, float] = {}
    token_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for evt in events:
        typ = str(evt.get("event_type") or "")
        if typ == "order_submit":
            orders_submitted += 1.0
            continue
        if typ in {"order_cancel", "cancel_all_on_exit", "kill_switch_cancel_all"}:
            if typ == "order_cancel":
                orders_canceled += 1.0
            else:
                orders_canceled += max(0.0, _safe_float(evt.get("canceled_count")))
            continue
        if typ == "book_top":
            token_id = str(evt.get("token_id") or "")
            mid = evt.get("midpoint")
            if token_id and isinstance(mid, (int, float)):
                token_book_mid[token_id] = float(mid)
            continue
        if typ != "fill":
            continue
        token_id = str(evt.get("token_id") or "")
        side = str(evt.get("side") or "").upper()
        size = _safe_float(evt.get("size"))
        price = _safe_float(evt.get("price"))
        if size <= 0 or price <= 0:
            continue
        notional = size * price
        fills += 1.0
        t = token_stats[token_id]
        t["fills"] += 1.0
        if side == "BUY":
            buy_count += 1.0
            buy_qty += size
            buy_notional += notional
            t["buy_qty"] += size
            t["buy_notional"] += notional
        elif side == "SELL":
            sell_count += 1.0
            sell_qty += size
            sell_notional += notional
            t["sell_qty"] += size
            t["sell_notional"] += notional

    net_cashflow = sell_notional - buy_notional
    net_position_qty = buy_qty - sell_qty
    mtm_mid = 0.0
    for token_id, t in token_stats.items():
        pos = _safe_float(t.get("buy_qty")) - _safe_float(t.get("sell_qty"))
        mid = token_book_mid.get(token_id)
        if mid is not None:
            mtm_mid += pos * float(mid)
        t["avg_buy_price"] = (_safe_float(t.get("buy_notional")) / _safe_float(t.get("buy_qty"))) if _safe_float(t.get("buy_qty")) > 0 else 0.0
        t["avg_sell_price"] = (_safe_float(t.get("sell_notional")) / _safe_float(t.get("sell_qty"))) if _safe_float(t.get("sell_qty")) > 0 else 0.0
        t["net_qty"] = pos
        t["realized_cashflow"] = _safe_float(t.get("sell_notional")) - _safe_float(t.get("buy_notional"))

    pnl_mark_to_mid = net_cashflow + mtm_mid
    cancel_ratio = (orders_canceled / orders_submitted) if orders_submitted > 0 else 0.0
    avg_entry = (buy_notional / buy_qty) if buy_qty > 0 else 0.0
    avg_exit = (sell_notional / sell_qty) if sell_qty > 0 else 0.0

    return {
        "schema_version": SCHEMA_VERSION,
        "log_dir": str(log_dir.resolve()),
        "run_id_filter": (str(run_id).strip() if run_id else None),
        "date_filter": date_str,
        "orders_submitted": orders_submitted,
        "orders_canceled": orders_canceled,
        "cancel_ratio": cancel_ratio,
        "fills": fills,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "avg_entry_price": avg_entry,
        "avg_exit_price": avg_exit,
        "net_position_qty": net_position_qty,
        "realized_cashflow": net_cashflow,
        "mark_to_mid_value": mtm_mid,
        "pnl_mark_to_mid": pnl_mark_to_mid,
        "per_token": {k: dict(v) for k, v in sorted(token_stats.items(), key=lambda x: x[0])},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro desk trade report")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Execution log directory")
    parser.add_argument("--run-id", default="", help="Optional run_id filter")
    parser.add_argument("--date", default="", help="Optional UTC date filter YYYY-MM-DD")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    date_str = str(args.date).strip()
    report = build_report(
        log_dir=pathlib.Path(args.log_dir),
        run_id=(str(args.run_id).strip() or None),
        date_str=date_str,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
