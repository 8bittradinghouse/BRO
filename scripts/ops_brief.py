#!/usr/bin/env python3
"""Compact operator brief derived from ops snapshot."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict


from scripts.ops_snapshot import run_snapshot


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _severity(payload: Dict[str, Any]) -> str:
    integrity_ok = bool(((payload.get("integrity") or {}).get("ok")))
    fin = payload.get("financial_summary", {}) or {}
    error_rows = _safe_float(fin.get("error_rows"))
    if not integrity_ok:
        return "PAGE"
    if error_rows >= 10:
        return "WARN"
    return "OK"


def build_brief(payload: Dict[str, Any]) -> Dict[str, Any]:
    fin = payload.get("financial_summary", {}) or {}
    brief = {
        "severity": _severity(payload),
        "run_id": str(payload.get("run_id") or ""),
        "status_rows": int(((payload.get("integrity") or {}).get("status_row_count")) or 0),
        "fills": _safe_float(fin.get("fill_count")),
        "orders_submitted": _safe_float(fin.get("orders_submitted")),
        "capture_minus_adverse": _safe_float(fin.get("execution_capture_minus_adverse")),
        "sniper_submits": _safe_float(fin.get("sniper_submits")),
        "sniper_fills": _safe_float(fin.get("sniper_fills")),
        "sniper_fill_rate": _safe_float(fin.get("sniper_fill_rate")),
        "sniper_midpoint_win_rate_proxy": _safe_float(fin.get("sniper_midpoint_win_rate_proxy")),
        "quote_uptime_ratio": _safe_float(fin.get("quote_uptime_ratio")),
        "error_rows": _safe_float(fin.get("error_rows")),
        "integrity_ok": bool(((payload.get("integrity") or {}).get("ok"))),
    }
    return brief


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro compact ops brief")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument("--run-contract", default="", help="Optional run contract JSON path for deterministic replay")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument("--compose-project-name", default="", help="Optional compose project name")
    parser.add_argument("--min-status-rows", type=int, default=5, help="Minimum status rows for integrity checks")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Max latest status age")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    snap = run_snapshot(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        compose_project_name=str(args.compose_project_name),
        min_status_rows=int(args.min_status_rows),
        max_status_age_sec=float(args.max_status_age_sec),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )
    brief = build_brief(snap)
    if args.json:
        print(json.dumps(brief, indent=2, sort_keys=True))
    else:
        print(
            " | ".join(
                [
                    f"sev={brief['severity']}",
                    f"run={brief['run_id'] or 'none'}",
                    f"integrity_ok={brief['integrity_ok']}",
                    f"fills={brief['fills']:.0f}",
                    f"orders={brief['orders_submitted']:.0f}",
                    f"capture={brief['capture_minus_adverse']:.4f}",
                    f"sniper_fill={brief['sniper_fill_rate']:.2%}",
                    f"sniper_midpoint_win_proxy={brief['sniper_midpoint_win_rate_proxy']:.2%}",
                    f"uptime={brief['quote_uptime_ratio']:.2%}",
                    f"errors={brief['error_rows']:.0f}",
                ]
            )
        )
    raise SystemExit(0 if brief["severity"] != "PAGE" else 2)


if __name__ == "__main__":
    main()
