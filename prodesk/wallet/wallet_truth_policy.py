from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Canonical provider disagreement tolerances (shared by provider logic + tests).
PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT = 1e-6
PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT = 1e-6


PROVIDER_FIELD_CLASS_REQUIRED_AUTHORITY = "required_authority"
PROVIDER_FIELD_CLASS_OPTIONAL_SUPPORTING = "optional_supporting"

PROVIDER_FAILURE_BEHAVIOR_FAIL_CLOSED = "fail_closed"
PROVIDER_FAILURE_BEHAVIOR_UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class ProviderFieldPolicy:
    label: str
    field_class: str
    missing_behavior: str
    ambiguity_behavior: str


WALLET_BALANCE_FIELD_POLICY = ProviderFieldPolicy(
    label="live_wallet_balance",
    field_class=PROVIDER_FIELD_CLASS_REQUIRED_AUTHORITY,
    missing_behavior=PROVIDER_FAILURE_BEHAVIOR_FAIL_CLOSED,
    ambiguity_behavior=PROVIDER_FAILURE_BEHAVIOR_FAIL_CLOSED,
)

ALLOWANCE_FIELD_POLICY = ProviderFieldPolicy(
    label="live_allowance",
    field_class=PROVIDER_FIELD_CLASS_REQUIRED_AUTHORITY,
    missing_behavior=PROVIDER_FAILURE_BEHAVIOR_UNHEALTHY,
    ambiguity_behavior=PROVIDER_FAILURE_BEHAVIOR_UNHEALTHY,
)

POL_BALANCE_FIELD_POLICY = ProviderFieldPolicy(
    label="live_pol_balance",
    field_class=PROVIDER_FIELD_CLASS_OPTIONAL_SUPPORTING,
    missing_behavior=PROVIDER_FAILURE_BEHAVIOR_UNHEALTHY,
    ambiguity_behavior=PROVIDER_FAILURE_BEHAVIOR_UNHEALTHY,
)


def provider_allowed_disagreement_span(
    *,
    low: float,
    high: float,
    abs_tolerance: float = PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
    rel_tolerance: float = PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
) -> float:
    scale = max(1.0, abs(float(low)), abs(float(high)))
    return float(max(float(abs_tolerance), float(rel_tolerance) * scale))


def provider_has_material_disagreement(
    values: Iterable[float],
    *,
    abs_tolerance: float = PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
    rel_tolerance: float = PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
) -> bool:
    numeric = [float(v) for v in values]
    if not numeric:
        return False
    low = min(numeric)
    high = max(numeric)
    span = high - low
    allowed = provider_allowed_disagreement_span(
        low=low,
        high=high,
        abs_tolerance=abs_tolerance,
        rel_tolerance=rel_tolerance,
    )
    return bool(span > allowed)
