#!/usr/bin/env python3
"""Produce a single operational snapshot: runtime health + trading metrics."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, Optional


from scripts.nightly_soak_report import build_report
from scripts.run_integrity_audit import run_audit as run_run_integrity_audit


def _safe_run(cmd: list[str]) -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": int(proc.returncode),
        "stdout": str(proc.stdout or "").strip(),
        "stderr": str(proc.stderr or "").strip(),
    }


def run_snapshot(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    compose_project_name: str,
    min_status_rows: int,
    max_status_age_sec: float,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    resolved = log_dir.resolve()
    selected_run_id = str(run_id or "").strip()

    report = build_report(
        resolved,
        run_id=(selected_run_id or None),
        auto_resolve_run_id=False,
        run_contract_path=run_contract_path,
        session_phase=session_phase,
    )
    integrity = run_run_integrity_audit(
        log_dir=resolved,
        run_id=selected_run_id,
        min_status_rows=max(1, int(min_status_rows)),
        status_tail_lines=800,
        event_tail_lines=800,
        max_status_age_sec=max(1.0, float(max_status_age_sec)),
        run_contract_path=run_contract_path,
        session_phase=session_phase,
    )

    env_prefix: list[str] = []
    if str(compose_project_name).strip():
        env_prefix = [f"COMPOSE_PROJECT_NAME={compose_project_name.strip()}"]

    compose_ps_cmd = ["bash", "-lc", " ".join(env_prefix + ["docker compose ps"]).strip()]
    compose_logs_cmd = ["bash", "-lc", " ".join(env_prefix + ["docker compose logs --tail=40 bro-maker"]).strip()]
    compose_ps = _safe_run(compose_ps_cmd)
    compose_logs = _safe_run(compose_logs_cmd)

    financial = {
        "run_duration_minutes": float(report.get("run_duration_minutes", 0.0)),
        "fill_count": float(report.get("fill_count", 0.0)),
        "orders_submitted": float(report.get("orders_submitted", 0.0)),
        "orders_canceled": float(report.get("orders_canceled", 0.0)),
        "execution_capture_minus_adverse": float(
            (report.get("execution_quality", {}) or {}).get("capture_minus_adverse", 0.0)
        ),
        "taker_submits": float((report.get("taker", report.get("taker", {})) or {}).get("submits", 0.0)),
        "taker_fills": float((report.get("taker", report.get("taker", {})) or {}).get("fills", 0.0)),
        "taker_fill_rate": float((report.get("taker", report.get("taker", {})) or {}).get("fill_rate", 0.0)),
        "taker_midpoint_win_rate_proxy": float(
            (report.get("taker", report.get("taker", {})) or {}).get("midpoint_win_rate_proxy", 0.0)
        ),
        "quote_uptime_ratio": float(report.get("quote_uptime_ratio", 0.0)),
        "error_rows": float(report.get("error_rows", 0.0)),
    }

    return {
        "log_dir": str(resolved),
        "run_id": selected_run_id,
        "run_contract_path": str(run_contract_path.resolve()) if isinstance(run_contract_path, pathlib.Path) else "",
        "session_phase": str(session_phase or "validate_postrun"),
        "integrity": integrity,
        "financial_summary": financial,
        "report": report,
        "docker": {
            "compose_ps": compose_ps,
            "maker_logs_tail": compose_logs,
        },
        "ok": bool(integrity.get("ok", False)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro one-command ops snapshot")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument("--run-contract", default="", help="Optional run contract JSON path for deterministic replay")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument("--compose-project-name", default="", help="Optional compose project name")
    parser.add_argument("--min-status-rows", type=int, default=5, help="Minimum status rows required for integrity")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Max latest status age for integrity")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    snapshot = run_snapshot(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        compose_project_name=str(args.compose_project_name),
        min_status_rows=int(args.min_status_rows),
        max_status_age_sec=float(args.max_status_age_sec),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
    )
    rendered = json.dumps(snapshot, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if snapshot["ok"] else 2)


if __name__ == "__main__":
    main()
