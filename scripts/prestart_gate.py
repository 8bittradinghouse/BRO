#!/usr/bin/env python3
"""Prestart safety gate for Bro runtime launches."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, Optional

from prodesk.config import _load_raw_with_extends


def _latest_status_row(log_dir: pathlib.Path) -> Optional[Dict[str, Any]]:
    files = sorted(log_dir.glob("status_*.jsonl"))
    if not files:
        return None
    for path in reversed(files[-3:]):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                lines = [line.strip() for line in fh if line.strip()]
        except Exception:
            continue
        for text in reversed(lines[-500:]):
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                return row
    return None


def run_gate(*, config_path: pathlib.Path, allow_kill_switch: bool, allow_guard_file: bool) -> Dict[str, Any]:
    cfg, _ = _load_raw_with_extends(config_path.resolve())
    if not isinstance(cfg, dict):
        raise ValueError("config root must be a mapping")
    cfg_dir = config_path.resolve().parent
    storage = cfg.get("storage", {}) if isinstance(cfg.get("storage", {}), dict) else {}
    runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime", {}), dict) else {}

    log_raw = str(storage.get("log_dir", "./logs_exec")).strip()
    log_path = pathlib.Path(log_raw) if log_raw else pathlib.Path("./logs_exec")
    if not log_path.is_absolute():
        log_path = (cfg_dir / log_path).resolve()
    log_dir = log_path.resolve()

    guard_file_raw = str(runtime.get("guard_stop_file", "")).strip()
    if guard_file_raw:
        guard_path = pathlib.Path(guard_file_raw)
        if not guard_path.is_absolute():
            guard_path = (cfg_dir / guard_path).resolve()
        guard_file = guard_path.resolve()
    else:
        guard_file = None

    findings: list[str] = []
    guard_candidates: list[pathlib.Path] = []
    if guard_file is not None:
        guard_candidates.append(guard_file)
    discovered_guard = (log_dir / "guard_stop.txt").resolve()
    if discovered_guard not in guard_candidates:
        guard_candidates.append(discovered_guard)
    if not allow_guard_file:
        for candidate in guard_candidates:
            if candidate.exists():
                findings.append(f"guard_stop_file_present:{candidate}")
                break

    row = _latest_status_row(log_dir)
    if row is not None and bool(row.get("kill_switch", False)) and not allow_kill_switch:
        reason = str(row.get("kill_reason") or "")
        findings.append(f"latest_status_kill_switch_true:{reason}")

    return {
        "config_path": str(config_path.resolve()),
        "log_dir": str(log_dir),
        "guard_stop_file": str(guard_file) if guard_file is not None else "",
        "finding_count": len(findings),
        "findings": findings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro prestart safety gate")
    parser.add_argument("--config", default="execution_config.yaml", help="execution config path")
    parser.add_argument("--allow-kill-switch", action="store_true", help="allow starting while last status has kill_switch=true")
    parser.add_argument("--allow-guard-file", action="store_true", help="allow starting with existing guard stop file")
    args = parser.parse_args()

    result = run_gate(
        config_path=pathlib.Path(args.config),
        allow_kill_switch=bool(args.allow_kill_switch),
        allow_guard_file=bool(args.allow_guard_file),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
