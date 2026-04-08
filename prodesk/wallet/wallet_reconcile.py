from __future__ import annotations

from typing import Dict

from .wallet_types import ReconciliationResult


def reconcile_severity(result: ReconciliationResult) -> str:
    if result.halt or str(result.action).strip().lower() == "halt":
        return "fail_closed"
    if not result.healthy:
        return "warning"
    return "ok"


def reconcile_surface(result: ReconciliationResult) -> Dict[str, str]:
    return {
        "healthy": str(bool(result.healthy)).lower(),
        "action": str(result.action),
        "reason": str(result.reason),
        "severity": reconcile_severity(result),
        "scope": "integrity_tripwire",
    }
