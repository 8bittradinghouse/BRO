"""Shared contract constants for canonical paper harness realism reporting."""

from __future__ import annotations

from typing import Dict, Tuple

HARNESS_REALISM_GRADE_SEMANTICS = "descriptive_non_gating"
HARNESS_REALISM_GRADE_AUTHORITY = "non_authoritative"
HARNESS_REALISM_BREAKDOWN_KEYS: Tuple[str, ...] = (
    "tod_liquidity_scaling",
    "maker_queue_proxy_depth_model",
    "taker_depth_slippage_model",
    "taker_lag_emulation_with_unknown_guard",
    "truth_surface_completeness",
)


def empty_harness_realism_breakdown() -> Dict[str, int]:
    return {key: 0 for key in HARNESS_REALISM_BREAKDOWN_KEYS}
