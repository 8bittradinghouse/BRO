from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

from .lineage_stage import normalize_lineage_stage

STAGE_OBSERVE = "OBSERVE"
STAGE_EVALUATE = "EVALUATE"
STAGE_MAKER_POSITION = "MAKER_POSITION"
STAGE_MAKER_TAKER_SELECTIVE = "MAKER_TAKER_SELECTIVE"
STAGE_SNIPER_PRIMARY = "SNIPER_PRIMARY"
STAGE_LATE_DIAGNOSTIC = "LATE_DIAGNOSTIC"
STAGE_MAKER_LATE_WINDOW = "MAKER_LATE_WINDOW"
STAGE_TAKER_COMMITMENT = "TAKER_COMMITMENT"
STAGE_EXTREME_ONLY = "EXTREME_ONLY"
STAGE_EXPIRED = "EXPIRED"
STAGE_UNKNOWN = "UNKNOWN"

EDGE_STAGE_EFFECTIVE_FIELD = "effective_stage"
EDGE_STAGE_BUCKET_FIELD = "stage_bucket"

LEGACY_EDGE_LINEAGE_FIELD_NAMES: Tuple[str, ...] = (
    EDGE_STAGE_EFFECTIVE_FIELD,
    EDGE_STAGE_BUCKET_FIELD,
    "raw_stage",
    "stage",
)

LEGACY_EDGE_AUTHORITY_FIELD_NAMES: Tuple[str, ...] = (
    "maker_allowed",
    "taker_allowed",
    "maker_new_risk_allowed",
    "normal_taker_allowed",
    "late_window_authority_class",
)

LEGACY_BLOCK_REASON_ALIASES: Dict[str, str] = {
    "stage_disallow_maker": "phase_disallow_maker",
    "stage_disallow_taker": "phase_disallow_taker",
}


def normalize_stage_name(value: Any) -> str:
    return normalize_lineage_stage(value)


def legacy_stage_to_lifecycle_phase(stage: Any) -> str:
    normalized = normalize_stage_name(stage)
    if normalized in {STAGE_UNKNOWN}:
        return ""
    if normalized in {STAGE_EXPIRED}:
        return "resolve"
    if normalized in {STAGE_TAKER_COMMITMENT}:
        return "taker_window"
    if normalized in {STAGE_MAKER_LATE_WINDOW}:
        return "maker_window"
    if normalized in {
        STAGE_OBSERVE,
        STAGE_EVALUATE,
        STAGE_MAKER_POSITION,
        STAGE_MAKER_TAKER_SELECTIVE,
        STAGE_SNIPER_PRIMARY,
        STAGE_LATE_DIAGNOSTIC,
        STAGE_EXTREME_ONLY,
    }:
        return "prepare"
    return ""


def lineage_stage_from_legacy_payload(payload: Mapping[str, Any]) -> str:
    lineage_hint = payload.get(EDGE_STAGE_BUCKET_FIELD)
    if lineage_hint is None or not str(lineage_hint).strip():
        lineage_hint = payload.get("raw_stage")
    if lineage_hint is None or not str(lineage_hint).strip():
        lineage_hint = payload.get("lineage_stage")
    if lineage_hint is None or not str(lineage_hint).strip():
        lineage_hint = payload.get(EDGE_STAGE_EFFECTIVE_FIELD)
    if lineage_hint is None or not str(lineage_hint).strip():
        lineage_hint = payload.get("stage")
    return normalize_stage_name(lineage_hint)


def lifecycle_phase_from_legacy_payload(payload: Mapping[str, Any]) -> str:
    sec_value = payload.get("time_remaining_sec")
    if not isinstance(sec_value, (int, float)):
        sec_value = payload.get("sec_to_expiry")
    sec = float(sec_value) if isinstance(sec_value, (int, float)) else None

    def _extreme_only_runtime_phase() -> str:
        if sec is not None and sec >= 0.0:
            if sec <= 7.0 + 1e-9:
                return "taker_window"
            if sec <= 15.0 + 1e-9:
                return "maker_window"
        return "prepare"

    lineage_stage = lineage_stage_from_legacy_payload(payload)
    if lineage_stage == STAGE_EXTREME_ONLY:
        return _extreme_only_runtime_phase()
    if lineage_stage != STAGE_UNKNOWN:
        return legacy_stage_to_lifecycle_phase(lineage_stage)
    compat_stage = normalize_stage_name(
        payload.get(EDGE_STAGE_EFFECTIVE_FIELD) or payload.get("stage")
    )
    if compat_stage == STAGE_EXTREME_ONLY:
        return _extreme_only_runtime_phase()
    return legacy_stage_to_lifecycle_phase(compat_stage)
