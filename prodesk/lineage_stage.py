from __future__ import annotations

from typing import Any


def normalize_lineage_stage(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text or "UNKNOWN"
