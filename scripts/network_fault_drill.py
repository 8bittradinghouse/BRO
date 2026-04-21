#!/usr/bin/env python3
"""Validate scheduled network fault-injection drill evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional


from prodesk.error_codes import summarize_error_codes

REQUIRED_FAULTS = ("dns_failure", "packet_loss", "latency_spike", "endpoint_flap")


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


def run_audit(*, drills_dir: pathlib.Path, max_age_days: float = 7.0) -> Dict[str, Any]:
    findings: List[str] = []
    rows: List[Dict[str, Any]] = []
    files = sorted(drills_dir.glob("drill_*.json"))
    now = dt.datetime.now(dt.timezone.utc)
    min_ts = now - dt.timedelta(days=max(0.1, float(max_age_days)))

    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append(f"invalid_json:{path.name}")
            continue
        if not isinstance(payload, dict):
            findings.append(f"invalid_payload:{path.name}")
            continue
        payload["_file"] = path.name
        rows.append(payload)

    latest_ok_by_fault: Dict[str, dt.datetime] = {}
    for row in rows:
        fault = str(row.get("fault_type") or "").strip().lower()
        ts = _parse_ts(row.get("ts_utc"))
        ok = bool(row.get("ok", False))
        if fault not in REQUIRED_FAULTS:
            continue
        if ts is None:
            findings.append(f"missing_or_invalid_ts:{row.get('_file')}")
            continue
        if not ok:
            findings.append(f"drill_not_ok:{fault}:{row.get('_file')}")
            continue
        if ts < min_ts:
            continue
        prev = latest_ok_by_fault.get(fault)
        if prev is None or ts > prev:
            latest_ok_by_fault[fault] = ts

    for fault in REQUIRED_FAULTS:
        if fault not in latest_ok_by_fault:
            findings.append(f"missing_recent_ok_drill:{fault}")

    return {
        "drills_dir": str(drills_dir.resolve()),
        "required_faults": list(REQUIRED_FAULTS),
        "max_age_days": float(max_age_days),
        "source_files": len(files),
        "parsed_rows": len(rows),
        "latest_ok_by_fault": {k: v.isoformat().replace("+00:00", "Z") for k, v in sorted(latest_ok_by_fault.items())},
        "finding_count": len(findings),
        "findings": sorted(set(findings)),
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro network fault drill evidence audit")
    parser.add_argument("--drills-dir", default="./ops/drills", help="Directory containing drill_*.json evidence files")
    parser.add_argument("--max-age-days", type=float, default=7.0, help="Maximum accepted age for successful drill evidence")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        drills_dir=pathlib.Path(args.drills_dir),
        max_age_days=max(0.1, float(args.max_age_days)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
