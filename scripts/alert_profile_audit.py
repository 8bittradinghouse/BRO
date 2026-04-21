#!/usr/bin/env python3
"""Audit alert threshold profile sanity for warning/page/auto-stop ladders."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional


from prodesk.config import load_execution_config


def _f(v: Any) -> Optional[float]:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _check_metric_ladder(
    *,
    name: str,
    warn: Any,
    page: Any,
    auto: Any,
    findings: List[str],
) -> None:
    w = _f(warn)
    p = _f(page)
    a = _f(auto)
    if w is None or p is None or a is None:
        findings.append(f"alerts:{name}:threshold_non_numeric")
        return
    if not (0.0 <= w <= 1.0 and 0.0 <= p <= 1.0 and 0.0 <= a <= 1.0):
        findings.append(f"alerts:{name}:threshold_out_of_range")
        return
    if not (w <= p <= a):
        findings.append(f"alerts:{name}:threshold_order_invalid:warn={w}:page={p}:auto={a}")


def run_audit(*, config_path: pathlib.Path) -> Dict[str, Any]:
    cfg = load_execution_config(config_path.resolve())
    findings: List[str] = []
    warnings: List[str] = []

    alerts = cfg.get("alerts", {}) if isinstance(cfg.get("alerts"), dict) else {}
    warn = alerts.get("warn_thresholds", {}) if isinstance(alerts.get("warn_thresholds"), dict) else {}
    page = alerts.get("page_thresholds", {}) if isinstance(alerts.get("page_thresholds"), dict) else {}
    auto = alerts.get("auto_stop_thresholds", {}) if isinstance(alerts.get("auto_stop_thresholds"), dict) else {}

    for metric in ("stale_reject_ratio", "disarmed_ratio", "error_ratio", "reconcile_mismatch_ratio"):
        _check_metric_ladder(
            name=metric,
            warn=warn.get(metric),
            page=page.get(metric),
            auto=auto.get(metric),
            findings=findings,
        )

    mt_warn = _f(warn.get("mode_transitions_window"))
    mt_page = _f(page.get("mode_transitions_window"))
    mt_auto = _f(auto.get("mode_transitions_window"))
    if mt_warn is None or mt_page is None or mt_auto is None:
        findings.append("alerts:mode_transitions_window:non_numeric")
    elif not (mt_warn <= mt_page <= mt_auto):
        findings.append(
            f"alerts:mode_transitions_window:order_invalid:warn={mt_warn}:page={mt_page}:auto={mt_auto}"
        )

    min_samples = _f(alerts.get("auto_stop_min_samples"))
    min_stale = _f(alerts.get("auto_stop_min_stale_rejects"))
    min_risk = _f(alerts.get("auto_stop_min_risk_rejects"))
    if min_samples is None or min_samples < 1:
        findings.append("alerts:auto_stop_min_samples_invalid")
    if min_stale is None or min_stale < 1:
        findings.append("alerts:auto_stop_min_stale_rejects_invalid")
    if min_risk is None or min_risk < 1:
        findings.append("alerts:auto_stop_min_risk_rejects_invalid")
    if min_samples is not None and min_stale is not None and min_stale > min_samples * 5:
        warnings.append("alerts:auto_stop_min_stale_rejects_high_vs_min_samples")
    if min_samples is not None and min_risk is not None and min_risk > min_samples * 5:
        warnings.append("alerts:auto_stop_min_risk_rejects_high_vs_min_samples")

    return {
        "config_path": str(config_path.resolve()),
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro alert profile audit")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()
    result = run_audit(config_path=pathlib.Path(args.config))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
