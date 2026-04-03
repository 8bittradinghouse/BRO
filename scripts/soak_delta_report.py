#!/usr/bin/env python3
"""Compare two soak report bundles and flag regressions."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Tuple


from prodesk.error_codes import summarize_error_codes


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:
        return default
    return out


def _load(path: pathlib.Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _extract(bundle_dir: pathlib.Path) -> Dict[str, float]:
    nightly = _load(bundle_dir / "nightly.json")
    websocket = _load(bundle_dir / "websocket_reliability.json")
    soak = _load(bundle_dir / "soak_hardening.json")
    promotion = _load(bundle_dir / "promotion.json")
    ws_metrics = websocket.get("metrics", {}) if isinstance(websocket.get("metrics"), dict) else {}
    lanes = soak.get("lanes", {}) if isinstance(soak.get("lanes"), dict) else {}
    rel = lanes.get("reliability", {}) if isinstance(lanes.get("reliability"), dict) else {}
    util = lanes.get("utilization", {}) if isinstance(lanes.get("utilization"), dict) else {}
    return {
        "duration_minutes": _safe_float(nightly.get("duration_minutes")),
        "quote_uptime_ratio": _safe_float(nightly.get("quote_uptime_ratio")),
        "error_rows": _safe_float(nightly.get("error_rows")),
        "capture_minus_adverse": _safe_float((nightly.get("execution_quality") or {}).get("capture_minus_adverse")),
        "maker_submits": _safe_float((nightly.get("execution_paths") or {}).get("maker_submits")),
        "taker_bonus_submits": _safe_float((nightly.get("execution_paths") or {}).get("taker_bonus_submits")),
        "taker_bonus_fills": _safe_float((nightly.get("execution_paths") or {}).get("taker_bonus_fills")),
        "ws_book_down_ratio": _safe_float(ws_metrics.get("book_feed_down_ratio")),
        "ws_chain_down_ratio": _safe_float(ws_metrics.get("chainlink_down_ratio")),
        "ws_book_age_p95": _safe_float(ws_metrics.get("book_feed_last_msg_age_p95_sec")),
        "ws_chain_age_p95": _safe_float(ws_metrics.get("chainlink_last_tick_age_p95_sec")),
        "reliability_ok": 1.0 if bool(rel.get("ok", False)) else 0.0,
        "utilization_ok": 1.0 if bool(util.get("ok", False)) else 0.0,
        "promotion_ok": 1.0 if bool(promotion.get("ok", False)) else 0.0,
    }


def _stage_metric_key(stage_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(stage_name or "").strip().lower()).strip("_")
    if not normalized:
        normalized = "unknown"
    return f"taker_stage_net_{normalized}"


def _extract_taker_stage_net_breakout(bundle_dir: pathlib.Path) -> Dict[str, float]:
    nightly = _load(bundle_dir / "nightly.json")
    raw = nightly.get("taker_stage_net_breakout", {})
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for stage_name, row in raw.items():
        if not isinstance(row, dict):
            continue
        out[str(stage_name).strip().upper() or "UNKNOWN"] = _safe_float(row.get("net"))
    return out


def run_report(
    *,
    baseline_dir: pathlib.Path,
    candidate_dir: pathlib.Path,
    min_uptime_delta: float,
    max_error_rows_delta: float,
    min_capture_delta: float,
    min_maker_submits_delta: float,
    min_taker_bonus_submits_delta: float,
    min_taker_bonus_fills_delta: float,
    max_ws_book_down_ratio_delta: float,
    max_ws_chain_down_ratio_delta: float,
    max_ws_book_age_p95_delta: float,
    max_ws_chain_age_p95_delta: float,
) -> Dict[str, Any]:
    base = _extract(baseline_dir.resolve())
    cand = _extract(candidate_dir.resolve())
    base_stage_net = _extract_taker_stage_net_breakout(baseline_dir.resolve())
    cand_stage_net = _extract_taker_stage_net_breakout(candidate_dir.resolve())
    all_stage_names = sorted(set(base_stage_net.keys()) | set(cand_stage_net.keys()))
    for stage_name in all_stage_names:
        metric_key = _stage_metric_key(stage_name)
        base[metric_key] = float(base_stage_net.get(stage_name, 0.0))
        cand[metric_key] = float(cand_stage_net.get(stage_name, 0.0))
    findings: List[str] = []
    deltas: Dict[str, float] = {k: cand[k] - base[k] for k in base.keys()}
    taker_stage_net_breakout: Dict[str, Dict[str, float]] = {}
    for stage_name in all_stage_names:
        metric_key = _stage_metric_key(stage_name)
        taker_stage_net_breakout[stage_name] = {
            "baseline_net": float(base.get(metric_key, 0.0)),
            "candidate_net": float(cand.get(metric_key, 0.0)),
            "delta_net": float(deltas.get(metric_key, 0.0)),
        }

    if deltas["quote_uptime_ratio"] < float(min_uptime_delta):
        findings.append(
            f"soak_delta_quote_uptime_ratio_regression:{deltas['quote_uptime_ratio']:.6f}<min:{float(min_uptime_delta):.6f}"
        )
    if deltas["error_rows"] > float(max_error_rows_delta):
        findings.append(
            f"soak_delta_error_rows_regression:{deltas['error_rows']:.6f}>max:{float(max_error_rows_delta):.6f}"
        )
    if deltas["capture_minus_adverse"] < float(min_capture_delta):
        findings.append(
            f"soak_delta_capture_minus_adverse_regression:{deltas['capture_minus_adverse']:.6f}<min:{float(min_capture_delta):.6f}"
        )
    if deltas["maker_submits"] < float(min_maker_submits_delta):
        findings.append(
            f"soak_delta_maker_submits_regression:{deltas['maker_submits']:.6f}<min:{float(min_maker_submits_delta):.6f}"
        )
    if deltas["taker_bonus_submits"] < float(min_taker_bonus_submits_delta):
        findings.append(
            "soak_delta_taker_bonus_submits_regression:"
            + f"{deltas['taker_bonus_submits']:.6f}<min:{float(min_taker_bonus_submits_delta):.6f}"
        )
    if deltas["taker_bonus_fills"] < float(min_taker_bonus_fills_delta):
        findings.append(
            f"soak_delta_taker_bonus_fills_regression:{deltas['taker_bonus_fills']:.6f}<min:{float(min_taker_bonus_fills_delta):.6f}"
        )
    if deltas["ws_book_down_ratio"] > float(max_ws_book_down_ratio_delta):
        findings.append(
            "soak_delta_ws_book_down_ratio_regression:"
            + f"{deltas['ws_book_down_ratio']:.6f}>max:{float(max_ws_book_down_ratio_delta):.6f}"
        )
    if deltas["ws_chain_down_ratio"] > float(max_ws_chain_down_ratio_delta):
        findings.append(
            "soak_delta_ws_chain_down_ratio_regression:"
            + f"{deltas['ws_chain_down_ratio']:.6f}>max:{float(max_ws_chain_down_ratio_delta):.6f}"
        )
    if deltas["ws_book_age_p95"] > float(max_ws_book_age_p95_delta):
        findings.append(
            f"soak_delta_ws_book_age_p95_regression:{deltas['ws_book_age_p95']:.6f}>max:{float(max_ws_book_age_p95_delta):.6f}"
        )
    if deltas["ws_chain_age_p95"] > float(max_ws_chain_age_p95_delta):
        findings.append(
            f"soak_delta_ws_chain_age_p95_regression:{deltas['ws_chain_age_p95']:.6f}>max:{float(max_ws_chain_age_p95_delta):.6f}"
        )

    return {
        "baseline_dir": str(baseline_dir.resolve()),
        "candidate_dir": str(candidate_dir.resolve()),
        "baseline": base,
        "candidate": cand,
        "deltas": deltas,
        "taker_stage_net_breakout": taker_stage_net_breakout,
        "thresholds": {
            "min_uptime_delta": float(min_uptime_delta),
            "max_error_rows_delta": float(max_error_rows_delta),
            "min_capture_delta": float(min_capture_delta),
            "min_maker_submits_delta": float(min_maker_submits_delta),
            "min_taker_bonus_submits_delta": float(min_taker_bonus_submits_delta),
            "min_taker_bonus_fills_delta": float(min_taker_bonus_fills_delta),
            "max_ws_book_down_ratio_delta": float(max_ws_book_down_ratio_delta),
            "max_ws_chain_down_ratio_delta": float(max_ws_chain_down_ratio_delta),
            "max_ws_book_age_p95_delta": float(max_ws_book_age_p95_delta),
            "max_ws_chain_age_p95_delta": float(max_ws_chain_age_p95_delta),
        },
        "finding_count": len(findings),
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro soak delta report")
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--min-uptime-delta", type=float, default=-0.02)
    parser.add_argument("--max-error-rows-delta", type=float, default=0.0)
    parser.add_argument("--min-capture-delta", type=float, default=-10.0)
    parser.add_argument("--min-maker-submits-delta", type=float, default=-10.0)
    parser.add_argument("--min-taker-bonus-submits-delta", type=float, default=-3.0)
    parser.add_argument("--min-taker-bonus-fills-delta", type=float, default=-2.0)
    parser.add_argument("--max-ws-book-down-ratio-delta", type=float, default=0.05)
    parser.add_argument("--max-ws-chain-down-ratio-delta", type=float, default=0.05)
    parser.add_argument("--max-ws-book-age-p95-delta", type=float, default=2.0)
    parser.add_argument("--max-ws-chain-age-p95-delta", type=float, default=2.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    result = run_report(
        baseline_dir=pathlib.Path(args.baseline_dir),
        candidate_dir=pathlib.Path(args.candidate_dir),
        min_uptime_delta=float(args.min_uptime_delta),
        max_error_rows_delta=float(args.max_error_rows_delta),
        min_capture_delta=float(args.min_capture_delta),
        min_maker_submits_delta=float(args.min_maker_submits_delta),
        min_taker_bonus_submits_delta=float(args.min_taker_bonus_submits_delta),
        min_taker_bonus_fills_delta=float(args.min_taker_bonus_fills_delta),
        max_ws_book_down_ratio_delta=float(args.max_ws_book_down_ratio_delta),
        max_ws_chain_down_ratio_delta=float(args.max_ws_chain_down_ratio_delta),
        max_ws_book_age_p95_delta=float(args.max_ws_book_age_p95_delta),
        max_ws_chain_age_p95_delta=float(args.max_ws_chain_age_p95_delta),
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
