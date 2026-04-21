#!/usr/bin/env python3
"""Gate over a window of soak artifacts to require repeatable evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml


from prodesk.error_codes import summarize_error_codes


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _load_json(path: pathlib.Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_policy(path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("policy root must be a mapping")
    return payload


def _report_key(path: pathlib.Path) -> Tuple[float, str]:
    try:
        mt = path.stat().st_mtime
    except OSError:
        mt = 0.0
    return (mt, str(path))


def _discover_reports(root: pathlib.Path) -> List[Dict[str, pathlib.Path]]:
    out: List[Dict[str, pathlib.Path]] = []
    for d in sorted([p for p in root.glob("soak45_*") if p.is_dir()], key=lambda p: _report_key(p)):
        soak = d / "soak_hardening.json"
        promo = d / "promotion.json"
        ws = d / "websocket_reliability.json"
        nightly = d / "nightly.json"
        if soak.exists() and promo.exists() and ws.exists() and nightly.exists():
            out.append({"root": d, "soak": soak, "promotion": promo, "websocket": ws, "nightly": nightly})
    return out


def run_gate(*, reports_root: pathlib.Path, policy_path: pathlib.Path) -> Dict[str, Any]:
    policy = _load_policy(policy_path.resolve())
    findings: List[str] = []

    required_runs = max(1, int(policy.get("required_runs", 3)))
    min_reliability_passes = max(1, int(policy.get("min_reliability_passes", required_runs)))
    min_utilization_passes = max(1, int(policy.get("min_utilization_passes", required_runs)))
    min_promotion_passes = max(1, int(policy.get("min_promotion_passes", required_runs)))
    min_total_duration_minutes = max(0.0, float(policy.get("min_total_duration_minutes", float(required_runs * 30))))
    require_monotonic_recency = bool(policy.get("require_monotonic_recency", True))

    discovered = _discover_reports(reports_root.resolve())
    recent = discovered[-required_runs:] if len(discovered) >= required_runs else discovered
    if len(recent) < required_runs:
        findings.append(f"soak_evidence_insufficient_runs:{len(recent)}<required:{required_runs}")

    reliability_passes = 0
    utilization_passes = 0
    promotion_passes = 0
    duration_total = 0.0
    recency_ts: List[float] = []
    details: List[Dict[str, Any]] = []
    for item in recent:
        soak = _load_json(item["soak"])
        promo = _load_json(item["promotion"])
        nightly = _load_json(item["nightly"])
        lanes = soak.get("lanes", {}) if isinstance(soak.get("lanes"), dict) else {}
        rel = lanes.get("reliability", {}) if isinstance(lanes.get("reliability"), dict) else {}
        util = lanes.get("utilization", {}) if isinstance(lanes.get("utilization"), dict) else {}
        rel_ok = bool(rel.get("ok", False))
        util_ok = bool(util.get("ok", False))
        promo_ok = bool(promo.get("ok", False))
        if rel_ok:
            reliability_passes += 1
        if util_ok:
            utilization_passes += 1
        if promo_ok:
            promotion_passes += 1
        dur = _safe_float(nightly.get("duration_minutes"))
        duration_total += dur
        try:
            recency_ts.append(item["root"].stat().st_mtime)
        except OSError:
            pass
        details.append(
            {
                "report_dir": str(item["root"]),
                "reliability_ok": rel_ok,
                "utilization_ok": util_ok,
                "promotion_ok": promo_ok,
                "duration_minutes": dur,
            }
        )

    if reliability_passes < min_reliability_passes:
        findings.append(
            f"soak_evidence_reliability_passes_too_few:{reliability_passes}<min:{min_reliability_passes}"
        )
    if utilization_passes < min_utilization_passes:
        findings.append(
            f"soak_evidence_utilization_passes_too_few:{utilization_passes}<min:{min_utilization_passes}"
        )
    if promotion_passes < min_promotion_passes:
        findings.append(f"soak_evidence_promotion_passes_too_few:{promotion_passes}<min:{min_promotion_passes}")
    if duration_total < min_total_duration_minutes:
        findings.append(
            f"soak_evidence_duration_total_too_low:{duration_total:.6f}<min:{min_total_duration_minutes:.6f}"
        )
    if require_monotonic_recency and len(recency_ts) >= 2:
        for i in range(1, len(recency_ts)):
            if recency_ts[i] < recency_ts[i - 1]:
                findings.append("soak_evidence_recency_order_invalid")
                break

    return {
        "reports_root": str(reports_root.resolve()),
        "policy_path": str(policy_path.resolve()),
        "required_runs": required_runs,
        "selected_runs": len(recent),
        "metrics": {
            "reliability_passes": reliability_passes,
            "utilization_passes": utilization_passes,
            "promotion_passes": promotion_passes,
            "duration_total_minutes": duration_total,
        },
        "thresholds": {
            "min_reliability_passes": min_reliability_passes,
            "min_utilization_passes": min_utilization_passes,
            "min_promotion_passes": min_promotion_passes,
            "min_total_duration_minutes": min_total_duration_minutes,
            "require_monotonic_recency": require_monotonic_recency,
        },
        "runs": details,
        "finding_count": len(findings),
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro soak evidence window gate")
    parser.add_argument("--reports-root", default="exports", help="Root directory containing soak report folders")
    parser.add_argument("--policy", default="ops/soak_evidence_policy.yaml", help="Soak evidence policy YAML")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_gate(reports_root=pathlib.Path(args.reports_root), policy_path=pathlib.Path(args.policy))
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
