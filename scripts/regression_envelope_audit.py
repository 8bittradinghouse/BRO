#!/usr/bin/env python3
"""Check report metrics against baseline regression envelopes."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional


from prodesk.error_codes import summarize_error_codes


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:
        return default
    return out


def _metric_value(payload: Dict[str, Any], dotted: str) -> Optional[float]:
    node: Any = payload
    for part in str(dotted).split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if not isinstance(node, (int, float)):
        return None
    return float(node)


def _load_json(path: pathlib.Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected object json at {path}")
    return data


def _evaluate_group(
    *,
    group_name: str,
    source_payload: Dict[str, Any],
    rule_payload: Dict[str, Any],
    findings: List[str],
) -> None:
    for metric, raw_rules in sorted(rule_payload.items()):
        rules = raw_rules if isinstance(raw_rules, dict) else {}
        value = _metric_value(source_payload, metric)
        if value is None:
            findings.append(f"regression_envelope_missing_metric:{group_name}:{metric}")
            continue
        if "min" in rules and value < _safe_float(rules.get("min"), default=value):
            findings.append(
                f"regression_envelope_breach:{group_name}:{metric}:value={value:.6f}<min={_safe_float(rules['min']):.6f}"
            )
        if "max" in rules and value > _safe_float(rules.get("max"), default=value):
            findings.append(
                f"regression_envelope_breach:{group_name}:{metric}:value={value:.6f}>max={_safe_float(rules['max']):.6f}"
            )


def run_audit(
    *,
    baseline_path: pathlib.Path,
    nightly_report_path: pathlib.Path,
    performance_report_path: pathlib.Path,
) -> Dict[str, Any]:
    findings: List[str] = []
    baseline = _load_json(baseline_path.resolve())
    nightly = _load_json(nightly_report_path.resolve())
    performance = _load_json(performance_report_path.resolve())

    nightly_rules = baseline.get("nightly", {})
    if isinstance(nightly_rules, dict):
        _evaluate_group(group_name="nightly", source_payload=nightly, rule_payload=nightly_rules, findings=findings)
    perf_rules = baseline.get("performance", {})
    if isinstance(perf_rules, dict):
        _evaluate_group(group_name="performance", source_payload=performance, rule_payload=perf_rules, findings=findings)

    return {
        "ok": len(findings) == 0,
        "finding_count": len(findings),
        "findings": sorted(set(findings)),
        "error_codes": summarize_error_codes(findings),
        "baseline_path": str(baseline_path.resolve()),
        "nightly_report_path": str(nightly_report_path.resolve()),
        "performance_report_path": str(performance_report_path.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro regression envelope audit")
    parser.add_argument("--baseline", default="ops/regression_envelope_ci.json")
    parser.add_argument("--nightly-report", required=True)
    parser.add_argument("--performance-report", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    result = run_audit(
        baseline_path=pathlib.Path(args.baseline),
        nightly_report_path=pathlib.Path(args.nightly_report),
        performance_report_path=pathlib.Path(args.performance_report),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
