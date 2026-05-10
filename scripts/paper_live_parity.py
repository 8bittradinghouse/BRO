#!/usr/bin/env python3
"""Paper/live parity diagnostics from nightly soak reports."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List


from prodesk.error_codes import summarize_error_codes


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _extract_metrics(payload: Dict[str, Any]) -> Dict[str, float]:
    execution_quality = payload.get("execution_quality") or {}
    taker = payload.get("taker") or {}
    latency = payload.get("latency_distribution_ms") or {}
    return {
        "quote_uptime_ratio": _safe_float(payload.get("quote_uptime_ratio")),
        "error_rows": _safe_float(payload.get("error_rows")),
        "capture_minus_adverse": _safe_float(execution_quality.get("capture_minus_adverse")),
        "taker_fill_rate": _safe_float(taker.get("fill_rate")),
        "latency_p90_ms": _safe_float(latency.get("p90_ms")),
    }


def _abs_gap(a: float, b: float) -> float:
    return abs(float(a) - float(b))


def run_parity(
    *,
    paper_report_path: pathlib.Path,
    live_report_path: pathlib.Path,
    max_uptime_gap: float,
    max_error_rows_gap: float,
    max_capture_gap: float,
    max_taker_fill_rate_gap: float,
    max_latency_p90_gap_ms: float,
) -> Dict[str, Any]:
    paper = json.loads(paper_report_path.read_text(encoding="utf-8"))
    live = json.loads(live_report_path.read_text(encoding="utf-8"))
    paper_m = _extract_metrics(paper)
    live_m = _extract_metrics(live)

    findings: List[str] = []
    gaps = {
        "quote_uptime_ratio_gap": _abs_gap(paper_m["quote_uptime_ratio"], live_m["quote_uptime_ratio"]),
        "error_rows_gap": _abs_gap(paper_m["error_rows"], live_m["error_rows"]),
        "capture_minus_adverse_gap": _abs_gap(paper_m["capture_minus_adverse"], live_m["capture_minus_adverse"]),
        "taker_fill_rate_gap": _abs_gap(paper_m["taker_fill_rate"], live_m["taker_fill_rate"]),
        "latency_p90_ms_gap": _abs_gap(paper_m["latency_p90_ms"], live_m["latency_p90_ms"]),
    }

    if gaps["quote_uptime_ratio_gap"] > float(max_uptime_gap):
        findings.append(f"parity_gap_quote_uptime:{gaps['quote_uptime_ratio_gap']:.6f}>max:{float(max_uptime_gap):.6f}")
    if gaps["error_rows_gap"] > float(max_error_rows_gap):
        findings.append(f"parity_gap_error_rows:{gaps['error_rows_gap']:.6f}>max:{float(max_error_rows_gap):.6f}")
    if gaps["capture_minus_adverse_gap"] > float(max_capture_gap):
        findings.append(
            f"parity_gap_capture_minus_adverse:{gaps['capture_minus_adverse_gap']:.6f}>max:{float(max_capture_gap):.6f}"
        )
    if gaps["taker_fill_rate_gap"] > float(max_taker_fill_rate_gap):
        findings.append(
            f"parity_gap_taker_fill_rate:{gaps['taker_fill_rate_gap']:.6f}>max:{float(max_taker_fill_rate_gap):.6f}"
        )
    if gaps["latency_p90_ms_gap"] > float(max_latency_p90_gap_ms):
        findings.append(f"parity_gap_latency_p90_ms:{gaps['latency_p90_ms_gap']:.6f}>max:{float(max_latency_p90_gap_ms):.6f}")

    return {
        "paper_report_path": str(paper_report_path.resolve()),
        "live_report_path": str(live_report_path.resolve()),
        "paper_metrics": paper_m,
        "live_metrics": live_m,
        "gaps": gaps,
        "thresholds": {
            "max_uptime_gap": float(max_uptime_gap),
            "max_error_rows_gap": float(max_error_rows_gap),
            "max_capture_gap": float(max_capture_gap),
            "max_taker_fill_rate_gap": float(max_taker_fill_rate_gap),
            "max_latency_p90_gap_ms": float(max_latency_p90_gap_ms),
        },
        "finding_count": len(findings),
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro paper/live parity diagnostics")
    parser.add_argument("--paper-report", required=True, help="Paper nightly soak report JSON path")
    parser.add_argument("--live-report", required=True, help="Live nightly soak report JSON path")
    parser.add_argument("--max-uptime-gap", type=float, default=0.25, help="Maximum absolute quote_uptime_ratio gap")
    parser.add_argument("--max-error-rows-gap", type=float, default=10.0, help="Maximum absolute error_rows gap")
    parser.add_argument("--max-capture-gap", type=float, default=10.0, help="Maximum absolute capture_minus_adverse gap")
    parser.add_argument("--max-taker-fill-rate-gap", type=float, default=0.50, help="Maximum absolute taker fill rate gap")
    parser.add_argument("--max-latency-p90-gap-ms", type=float, default=500.0, help="Maximum absolute latency p90 gap")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_parity(
        paper_report_path=pathlib.Path(args.paper_report),
        live_report_path=pathlib.Path(args.live_report),
        max_uptime_gap=max(0.0, float(args.max_uptime_gap)),
        max_error_rows_gap=max(0.0, float(args.max_error_rows_gap)),
        max_capture_gap=max(0.0, float(args.max_capture_gap)),
        max_taker_fill_rate_gap=max(0.0, float(args.max_taker_fill_rate_gap)),
        max_latency_p90_gap_ms=max(0.0, float(args.max_latency_p90_gap_ms)),
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
