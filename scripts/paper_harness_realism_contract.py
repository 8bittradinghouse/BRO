"""Shared contract constants for canonical paper harness realism reporting."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

HARNESS_REALISM_GRADE_SEMANTICS = "descriptive_non_gating"
HARNESS_REALISM_GRADE_AUTHORITY = "non_authoritative"
HARNESS_REALISM_BREAKDOWN_KEYS: Tuple[str, ...] = (
    "tod_liquidity_scaling",
    "maker_queue_proxy_depth_model",
    "taker_depth_slippage_model",
    "taker_lag_emulation_with_unknown_guard",
    "truth_surface_completeness",
)
EXERCISED_HARNESS_REALISM_FIELD = "exercised_harness_realism"


def empty_harness_realism_breakdown() -> Dict[str, int]:
    return {key: 0 for key in HARNESS_REALISM_BREAKDOWN_KEYS}


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def normalize_harness_realism_breakdown(breakdown: Mapping[str, Any] | None) -> Dict[str, int]:
    normalized = empty_harness_realism_breakdown()
    if not isinstance(breakdown, Mapping):
        return normalized
    for key in HARNESS_REALISM_BREAKDOWN_KEYS:
        normalized[key] = _coerce_int(breakdown.get(key, 0))
    return normalized


def build_exercised_harness_realism_surface(
    *,
    grade: Any,
    breakdown: Mapping[str, Any] | None,
    semantics: Any = HARNESS_REALISM_GRADE_SEMANTICS,
    authority: Any = HARNESS_REALISM_GRADE_AUTHORITY,
) -> Dict[str, Any]:
    return {
        "grade": _coerce_int(grade),
        "breakdown": normalize_harness_realism_breakdown(breakdown),
        "semantics": str(semantics or HARNESS_REALISM_GRADE_SEMANTICS),
        "authority": str(authority or HARNESS_REALISM_GRADE_AUTHORITY),
    }


def normalize_nightly_exercised_harness_realism(report: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(report, Mapping):
        return build_exercised_harness_realism_surface(grade=0, breakdown=None)

    nested = report.get(EXERCISED_HARNESS_REALISM_FIELD)
    if isinstance(nested, Mapping):
        return build_exercised_harness_realism_surface(
            grade=nested.get("grade"),
            breakdown=nested.get("breakdown"),
            semantics=nested.get("semantics"),
            authority=nested.get("authority"),
        )

    looks_like_nightly_report = any(
        key in report for key in ("event_files", "status_files", "error_files", "quote_uptime_ratio", "execution_paths")
    )
    if looks_like_nightly_report:
        return build_exercised_harness_realism_surface(
            grade=report.get("harness_realism_grade"),
            breakdown=report.get("harness_realism_grade_breakdown"),
            semantics=report.get("harness_realism_grade_semantics"),
            authority=report.get("harness_realism_grade_authority"),
        )

    return build_exercised_harness_realism_surface(grade=0, breakdown=None)
