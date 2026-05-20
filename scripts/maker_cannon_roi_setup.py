#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Callable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_DIR = (
    REPO_ROOT
    / "logs_exec"
    / "paper_universal"
    / "forge_masters_archive_maker_peak_session_keeper_set_2026-04-28"
)
DEFAULT_OUTPUT_STEM = "solar_slug_maker_cannon_roi_setup"


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _count_distribution(values: list[str]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for value in values:
        distribution[value] = distribution.get(value, 0) + 1
    return dict(sorted(distribution.items(), key=lambda item: (item[0], item[1])))


def _filtering_strength(pass_rate: float | None) -> str:
    if pass_rate is None:
        return "unknown"
    if pass_rate >= 0.95:
        return "very_low"
    if pass_rate >= 0.80:
        return "low"
    if pass_rate >= 0.50:
        return "moderate"
    if pass_rate >= 0.20:
        return "high"
    return "very_high"


def _summarize_rule(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool], note: str) -> dict[str, Any]:
    total = len(rows)
    pass_rows = [row for row in rows if predicate(row)]
    pass_count = len(pass_rows)
    pass_rate = (pass_count / total) if total else None
    submitted_pass_count = sum(1 for row in pass_rows if row.get("order_submit_id"))
    return {
        "total": total,
        "pass_count": pass_count,
        "pass_rate": pass_rate,
        "submitted_pass_count": submitted_pass_count,
        "filtering_strength": _filtering_strength(pass_rate),
        "note": note,
    }


def _dominant_failures(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for row in rows:
        if predicate(row):
            continue
        reasons: list[str] = []
        if not _coerce_bool(row.get("secondary_oracle_confirmation")):
            reasons.append("secondary_oracle_not_confirmed")
        if not _coerce_bool(row.get("cannon_depth_requirement_met")):
            reasons.append("insufficient_depth_multiple")
        if not _geometry_ok(row):
            reasons.append("non_viable_geometry_or_sizing_conflict")
        if not _stack_hard_cap_ok(row):
            reasons.append("stack_hard_cap_exceeded")
        fill_prob_margin = _coerce_float(row.get("fill_prob_margin"))
        if fill_prob_margin is not None and fill_prob_margin < 0.0:
            reasons.append("fill_prob_margin_negative")
        repeat_count = _coerce_float(row.get("same_target_side_submit_count_prior"))
        if repeat_count is not None and repeat_count > 1.0:
            reasons.append("repeat_target_side_pressure")
        if not reasons:
            reasons.append("other")
        for reason in reasons:
            distribution[reason] = distribution.get(reason, 0) + 1
    return dict(sorted(distribution.items(), key=lambda item: (-item[1], item[0])))


def _geometry_ok(row: dict[str, Any]) -> bool:
    return row.get("viability_class") == "viable_only" and not bool(row.get("sizing_conflict"))


def _stack_hard_cap_ok(row: dict[str, Any]) -> bool:
    return row.get("stack_pressure_class") in {"below_soft_cap", "within_hard_cap"}


def _stack_soft_cap_ok(row: dict[str, Any]) -> bool:
    return row.get("stack_pressure_class") == "below_soft_cap"


def _delta_threshold_ok(row: dict[str, Any]) -> bool:
    value = _coerce_float(row.get("secondary_oracle_price_delta_abs"))
    if value is None:
        return False
    return value >= 0.20


def _fill_prob_ok(row: dict[str, Any]) -> bool:
    value = _coerce_float(row.get("fill_prob_margin"))
    return value is not None and value >= 0.0


def _repeat_calm(row: dict[str, Any]) -> bool:
    value = _coerce_float(row.get("same_target_side_submit_count_prior"))
    return value is not None and value <= 1.0


def _grok_core_active(row: dict[str, Any]) -> bool:
    return (
        _coerce_bool(row.get("secondary_oracle_confirmation")) is True
        and _delta_threshold_ok(row)
        and _stack_hard_cap_ok(row)
        and _coerce_bool(row.get("cannon_depth_requirement_met")) is True
        and _geometry_ok(row)
    )


def _grok_core_plus_bro_safety(row: dict[str, Any]) -> bool:
    return _grok_core_active(row) and _fill_prob_ok(row) and _repeat_calm(row)


def analyze_active_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_rows = [row for row in rows if row.get("population_class") == "candidate"]
    rule_roi = {
        "secondary_oracle_confirmation": _summarize_rule(
            candidate_rows,
            lambda row: _coerce_bool(row.get("secondary_oracle_confirmation")) is True,
            "Dual-oracle agreement is directionally useful, but it is not the main bottleneck in this keeper set.",
        ),
        "secondary_oracle_delta_abs_ge_0p20": _summarize_rule(
            candidate_rows,
            _delta_threshold_ok,
            "Current recorded absolute delta is almost always above 0.20, so this threshold is not discriminating enough in the present machine surface.",
        ),
        "stack_hard_cap_ok": _summarize_rule(
            candidate_rows,
            _stack_hard_cap_ok,
            "The current keeper set is already staying inside the hard stack cap; this looks like a safety guard, not a primary selector.",
        ),
        "stack_soft_cap_preferred": _summarize_rule(
            candidate_rows,
            _stack_soft_cap_ok,
            "Soft-cap preference has some filtering value, but it is not the main wound family either.",
        ),
        "depth_requirement_1p5x": _summarize_rule(
            candidate_rows,
            lambda row: _coerce_bool(row.get("cannon_depth_requirement_met")) is True,
            "Depth relative to the $100 cannon shot is the strongest current cannon-doctrine filter.",
        ),
        "geometry_viable_only": _summarize_rule(
            candidate_rows,
            _geometry_ok,
            "Current geometry still blocks a real subset of fights, so fixed $100 cannot be treated as universal without doctrine exceptions.",
        ),
        "fill_prob_margin_nonnegative": _summarize_rule(
            candidate_rows,
            _fill_prob_ok,
            "Negative fill-probability margin is a live pressure family and a strong candidate for skip-trash discipline.",
        ),
        "repeat_target_side_calm": _summarize_rule(
            candidate_rows,
            _repeat_calm,
            "Repeat-target-side churn is a major active wound and a strong BRO-specific add-on to the Grok cannon doctrine.",
        ),
    }

    combined_profiles = {
        "grok_core": _summarize_rule(
            candidate_rows,
            _grok_core_active,
            "Blueprint core only: confirmation + 0.20 delta + stack hard-cap + depth 1.5x + viable geometry.",
        ),
        "grok_core_plus_bro_safety": _summarize_rule(
            candidate_rows,
            _grok_core_plus_bro_safety,
            "Blueprint core plus BRO-specific safety overlays for fill-probability and repeat-target churn.",
        ),
    }

    return {
        "candidate_row_count": len(candidate_rows),
        "submitted_candidate_row_count": sum(1 for row in candidate_rows if row.get("order_submit_id")),
        "rule_roi": rule_roi,
        "combined_profiles": combined_profiles,
        "dominant_failures_vs_grok_core_plus_bro_safety": _dominant_failures(candidate_rows, _grok_core_plus_bro_safety),
    }


def analyze_late_probe_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    late_rows = rows
    candidate_rows = [row for row in late_rows if row.get("population_class") == "candidate"]
    latent_evaluable_rows = [row for row in late_rows if row.get("latent_market_truth_class") == "evaluable"]
    full_candidates = [row for row in late_rows if bool(row.get("full_cannon_candidate"))]
    latent_full_candidates = [row for row in late_rows if bool(row.get("latent_market_full_cannon_candidate"))]
    return {
        "row_count": len(late_rows),
        "candidate_row_count": len(candidate_rows),
        "latent_evaluable_row_count": len(latent_evaluable_rows),
        "full_cannon_candidate_count": len(full_candidates),
        "latent_market_full_cannon_candidate_count": len(latent_full_candidates),
        "full_cannon_window_distribution": _count_distribution([str(row.get("cannon_window_class") or "unknown") for row in full_candidates]),
        "latent_full_cannon_window_distribution": _count_distribution(
            [str(row.get("cannon_window_class") or "unknown") for row in latent_full_candidates]
        ),
        "candidate_secondary_confirmation_distribution": _count_distribution(
            [
                "confirmed" if _coerce_bool(row.get("secondary_oracle_confirmation")) is True else "not_confirmed"
                for row in candidate_rows
            ]
        ),
        "candidate_depth_requirement_distribution": _count_distribution(
            [
                "met" if _coerce_bool(row.get("cannon_depth_requirement_met")) is True else "not_met_or_unknown"
                for row in candidate_rows
            ]
        ),
        "latent_market_reject_reason_distribution": _count_distribution(
            [
                str(reason)
                for row in late_rows
                for reason in (row.get("latent_market_reject_reasons") or [])
                if isinstance(reason, str)
            ]
        ),
    }


def build_recommendation(active_analysis: dict[str, Any], late_analysis: dict[str, Any]) -> dict[str, Any]:
    active_rule_roi = active_analysis["rule_roi"]
    promote_now: list[str] = []
    keep_report_only: list[str] = []
    needs_formalization: list[str] = []

    if active_rule_roi["depth_requirement_1p5x"]["filtering_strength"] in {"moderate", "high", "very_high"}:
        promote_now.append("depth_requirement_1p5x")
    if active_rule_roi["secondary_oracle_confirmation"]["pass_rate"] and active_rule_roi["secondary_oracle_confirmation"]["pass_rate"] < 0.95:
        promote_now.append("secondary_oracle_confirmation")
    if active_rule_roi["fill_prob_margin_nonnegative"]["filtering_strength"] in {"high", "very_high"}:
        promote_now.append("fill_prob_margin_nonnegative")
    if active_rule_roi["repeat_target_side_calm"]["filtering_strength"] in {"high", "very_high", "moderate"}:
        promote_now.append("repeat_target_side_calm")
    if active_rule_roi["geometry_viable_only"]["filtering_strength"] in {"moderate", "high", "very_high"}:
        promote_now.append("geometry_viable_only")

    if active_rule_roi["stack_hard_cap_ok"]["pass_rate"] == 1.0:
        keep_report_only.append("stack_hard_cap_ok")
    if late_analysis["full_cannon_candidate_count"] == 0 and late_analysis["latent_market_full_cannon_candidate_count"] == 0:
        keep_report_only.append("late_window_shift")
    elif late_analysis["full_cannon_candidate_count"] > 0:
        keep_report_only.append("late_window_shift_needs_native_runtime_proof")

    if active_rule_roi["secondary_oracle_delta_abs_ge_0p20"]["pass_rate"] and active_rule_roi["secondary_oracle_delta_abs_ge_0p20"]["pass_rate"] >= 0.95:
        needs_formalization.append("secondary_oracle_delta_abs_ge_0p20")

    verdict = "partial_adopt_high_roi_components_only"
    if late_analysis["full_cannon_candidate_count"] <= 0 and late_analysis["latent_market_full_cannon_candidate_count"] <= 0:
        verdict = "do_not_promote_late_window_shift"

    return {
        "verdict": verdict,
        "promote_now_candidates": promote_now,
        "keep_report_only": keep_report_only,
        "needs_formalization": needs_formalization,
        "launch_bias": "stable_conservative_minimal_pnl_first",
    }


def analyze_bundle(bundle_dir: pathlib.Path) -> dict[str, Any]:
    active_rows = _load_jsonl(bundle_dir / "maker_market_snapshot_rows.jsonl")
    late_rows = _load_jsonl(bundle_dir / "maker_cannon_late_window_probe_rows.jsonl")
    active_analysis = analyze_active_rows(active_rows)
    late_analysis = analyze_late_probe_rows(late_rows)
    recommendation = build_recommendation(active_analysis, late_analysis)
    return {
        "tool": "maker_cannon_roi_setup",
        "bundle_dir": str(bundle_dir),
        "claim_boundary": {
            "verified": [
                "This tool measures cannon-doctrine fit and filtering ROI from existing FMA bundle artifacts.",
                "It does not prove realized maker PnL or final live-launch readiness by itself.",
                "Active maker shadow rows in the current keeper set are still the older 45-60s lane.",
                "Late-window cannon rows in the same keeper set are observational probe truth, not yet runtime participation proof.",
            ],
            "unknown": [
                "Whether the current secondary_oracle_price_delta_abs field exactly matches the Grok 0.20 doctrine semantics.",
                "Whether a full late-window runtime timing shift is already earned without more native proof.",
            ],
        },
        "active_keeper_lane": active_analysis,
        "late_window_probe_lane": late_analysis,
        "recommendation": recommendation,
    }


def render_markdown(report: dict[str, Any]) -> str:
    active = report["active_keeper_lane"]
    late = report["late_window_probe_lane"]
    rec = report["recommendation"]

    lines = [
        "# Solar Slug Maker Cannon ROI Setup",
        "",
        "## Verdict",
        f"- `VERIFIED`: `{rec['verdict']}`",
        f"- `INFERRED`: current launch bias should stay `{rec['launch_bias']}`",
        "",
        "## Active Keeper Lane",
        f"- Candidate rows: `{active['candidate_row_count']}`",
        f"- Submitted candidate rows: `{active['submitted_candidate_row_count']}`",
        "",
        "### Rule ROI",
    ]
    for name, summary in active["rule_roi"].items():
        pass_rate = summary["pass_rate"]
        pass_rate_text = "unknown" if pass_rate is None else f"{pass_rate:.4f}"
        lines.extend(
            [
                f"- `{name}`",
                f"  pass: `{summary['pass_count']}/{summary['total']}` (`{pass_rate_text}`), filtering strength: `{summary['filtering_strength']}`",
                f"  note: {summary['note']}",
            ]
        )
    lines.extend(
        [
            "",
            "### Combined Profiles",
        ]
    )
    for name, summary in active["combined_profiles"].items():
        pass_rate = summary["pass_rate"]
        pass_rate_text = "unknown" if pass_rate is None else f"{pass_rate:.4f}"
        lines.extend(
            [
                f"- `{name}`",
                f"  pass: `{summary['pass_count']}/{summary['total']}` (`{pass_rate_text}`), submitted passes: `{summary['submitted_pass_count']}`",
                f"  note: {summary['note']}",
            ]
        )

    lines.extend(
        [
            "",
            "### Dominant Failures Vs Grok Core Plus BRO Safety",
            f"- `{json.dumps(active['dominant_failures_vs_grok_core_plus_bro_safety'], sort_keys=True)}`",
            "",
            "## Late-Window Probe Lane",
            f"- Candidate rows: `{late['candidate_row_count']}`",
            f"- Latent evaluable rows: `{late['latent_evaluable_row_count']}`",
            f"- Runtime full cannon candidates: `{late['full_cannon_candidate_count']}`",
            f"- Latent market full cannon candidates: `{late['latent_market_full_cannon_candidate_count']}`",
            f"- Full-candidate window distribution: `{json.dumps(late['full_cannon_window_distribution'], sort_keys=True)}`",
            f"- Latent full-candidate window distribution: `{json.dumps(late['latent_full_cannon_window_distribution'], sort_keys=True)}`",
            f"- Candidate secondary confirmation: `{json.dumps(late['candidate_secondary_confirmation_distribution'], sort_keys=True)}`",
            f"- Candidate depth requirement: `{json.dumps(late['candidate_depth_requirement_distribution'], sort_keys=True)}`",
            f"- Latent market reject reasons: `{json.dumps(late['latent_market_reject_reason_distribution'], sort_keys=True)}`",
            "",
            "## Recommendation",
            f"- Promote-now candidates: `{json.dumps(rec['promote_now_candidates'])}`",
            f"- Keep report-only: `{json.dumps(rec['keep_report_only'])}`",
            f"- Needs formalization: `{json.dumps(rec['needs_formalization'])}`",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze the maker cannon blueprint ROI against an FMA keeper bundle.")
    parser.add_argument("--bundle-dir", type=pathlib.Path, default=DEFAULT_BUNDLE_DIR, help="FMA export bundle directory.")
    parser.add_argument(
        "--output-stem",
        default=DEFAULT_OUTPUT_STEM,
        help="Output filename stem written under the bundle directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle_dir = args.bundle_dir.resolve()
    report = analyze_bundle(bundle_dir)

    json_path = bundle_dir / f"{args.output_stem}.json"
    md_path = bundle_dir / f"{args.output_stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "verdict": report["recommendation"]["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
