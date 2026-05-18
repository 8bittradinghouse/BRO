from __future__ import annotations

"""Historical-only recovery/unwind lineage fields for replay and audit callers.

These names are retired active-owner vocabulary. They survive only so
historical artifacts, replay fixtures, and bounded audit/report callers can
recognize archived lineage without re-teaching those labels as live doctrine.
"""

from typing import Tuple

HISTORICAL_RECOVERY_ACTIVE_FIELD = "reduce_only_recovery_active"
HISTORICAL_PREEXPIRY_REDUCE_ONLY_ACTIVE_FIELD = "preexpiry_reduce_only_active"
HISTORICAL_RECOVERY_REASON_FIELD = "reduce_only_recovery_reason"
HISTORICAL_RECOVERY_ALLOWED_FIELD = "reduce_only_recovery_allowed"
HISTORICAL_EMERGENCY_UNWIND_ALLOWED_FIELD = "preexpiry_emergency_taker_allowed"
HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_FIELD = "preexpiry_emergency_taker_window_sec"

HISTORICAL_RECOVERY_LINEAGE_FIELD_NAMES: Tuple[str, ...] = (
    HISTORICAL_RECOVERY_ACTIVE_FIELD,
    HISTORICAL_PREEXPIRY_REDUCE_ONLY_ACTIVE_FIELD,
    HISTORICAL_RECOVERY_REASON_FIELD,
    HISTORICAL_RECOVERY_ALLOWED_FIELD,
    HISTORICAL_EMERGENCY_UNWIND_ALLOWED_FIELD,
    HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_FIELD,
)
