from __future__ import annotations

from typing import Any

LEGACY_STAGE_EXTREME_ONLY = "EXTREME_ONLY"
STAGE_LINEAGE_ONLY_0_TO_20S = "LINEAGE_ONLY_0_TO_20S"


def normalize_lineage_stage(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return "UNKNOWN"
    if text == LEGACY_STAGE_EXTREME_ONLY:
        return STAGE_LINEAGE_ONLY_0_TO_20S
    return text
