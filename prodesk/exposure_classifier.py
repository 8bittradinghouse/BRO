from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


EXPOSURE_CLASS_MEANINGFUL = "MEANINGFUL"
EXPOSURE_CLASS_DUST_ELIGIBLE = "DUST_ELIGIBLE"
EXPOSURE_CLASS_DUST_QUARANTINED = "DUST_QUARANTINED"
EXPOSURE_CLASS_VALUES = {
    EXPOSURE_CLASS_MEANINGFUL,
    EXPOSURE_CLASS_DUST_ELIGIBLE,
    EXPOSURE_CLASS_DUST_QUARANTINED,
}


@dataclass(frozen=True)
class ExposureClassifierConfig:
    dust_shares_epsilon: float
    dust_notional_usd_epsilon: float
    dust_total_notional_usd_cap: float
    dust_token_count_cap: int
    dust_max_age_sec: float
    dust_enter_consecutive_cycles: int
    dust_clear_consecutive_cycles: int


@dataclass(frozen=True)
class ExposureClassification:
    exposure_class: str
    dust_share_eligible: bool
    dust_notional_eligible: bool
    dust_gate_eligible: bool
    dust_notional_upper_bound_usd: float
    dust_reason: str


def is_flat_position(net_shares: float, *, position_epsilon: float = 1e-9) -> bool:
    return abs(float(net_shares or 0.0)) <= float(position_epsilon)


def classify_exposure(
    *,
    net_shares: float,
    cfg: ExposureClassifierConfig,
    conservative_mark_price: float = 1.0,
    open_order_present: bool = False,
    unresolved_lifecycle_obligation: bool = False,
    dust_age_sec: float = 0.0,
    aggregate_dust_notional_upper_bound_usd: float = 0.0,
    aggregate_dust_token_count: int = 0,
) -> ExposureClassification:
    net = float(net_shares or 0.0)
    abs_shares = abs(net)
    if is_flat_position(net):
        return ExposureClassification(
            exposure_class=EXPOSURE_CLASS_DUST_ELIGIBLE,
            dust_share_eligible=True,
            dust_notional_eligible=True,
            dust_gate_eligible=not bool(open_order_present or unresolved_lifecycle_obligation),
            dust_notional_upper_bound_usd=0.0,
            dust_reason="flat",
        )

    mark = max(0.0, float(conservative_mark_price or 0.0))
    dust_notional_upper_bound_usd = float(abs_shares * mark)
    dust_share_eligible = bool(abs_shares <= (float(cfg.dust_shares_epsilon) + 1e-9))
    dust_notional_eligible = bool(
        dust_notional_upper_bound_usd <= (float(cfg.dust_notional_usd_epsilon) + 1e-9)
    )
    dust_gate_eligible = bool(
        dust_share_eligible
        and dust_notional_eligible
        and (not bool(open_order_present))
        and (not bool(unresolved_lifecycle_obligation))
        and float(dust_age_sec) <= (float(cfg.dust_max_age_sec) + 1e-9)
        and float(aggregate_dust_notional_upper_bound_usd) <= (float(cfg.dust_total_notional_usd_cap) + 1e-9)
        and int(aggregate_dust_token_count) <= int(cfg.dust_token_count_cap)
    )
    if dust_gate_eligible:
        exposure_class = EXPOSURE_CLASS_DUST_ELIGIBLE
        dust_reason = "eligible"
    elif dust_share_eligible and dust_notional_eligible:
        exposure_class = EXPOSURE_CLASS_DUST_QUARANTINED
        dust_reason = "quarantined"
    else:
        exposure_class = EXPOSURE_CLASS_MEANINGFUL
        dust_reason = "meaningful"
    return ExposureClassification(
        exposure_class=exposure_class,
        dust_share_eligible=dust_share_eligible,
        dust_notional_eligible=dust_notional_eligible,
        dust_gate_eligible=dust_gate_eligible,
        dust_notional_upper_bound_usd=dust_notional_upper_bound_usd,
        dust_reason=dust_reason,
    )


def classify_exposure_fail_closed(
    *,
    net_shares: float,
    cfg: ExposureClassifierConfig,
    conservative_mark_price: float = 1.0,
    open_order_present: bool = False,
    unresolved_lifecycle_obligation: bool = False,
    dust_age_sec: float = 0.0,
    aggregate_dust_notional_upper_bound_usd: float = 0.0,
    aggregate_dust_token_count: int = 0,
) -> ExposureClassification:
    try:
        out = classify_exposure(
            net_shares=net_shares,
            cfg=cfg,
            conservative_mark_price=conservative_mark_price,
            open_order_present=open_order_present,
            unresolved_lifecycle_obligation=unresolved_lifecycle_obligation,
            dust_age_sec=dust_age_sec,
            aggregate_dust_notional_upper_bound_usd=aggregate_dust_notional_upper_bound_usd,
            aggregate_dust_token_count=aggregate_dust_token_count,
        )
        if out.exposure_class in EXPOSURE_CLASS_VALUES:
            return out
    except Exception:
        pass
    # Fail closed: anything uncertain remains meaningful exposure.
    return ExposureClassification(
        exposure_class=EXPOSURE_CLASS_MEANINGFUL,
        dust_share_eligible=False,
        dust_notional_eligible=False,
        dust_gate_eligible=False,
        dust_notional_upper_bound_usd=max(0.0, abs(float(net_shares or 0.0)) * max(0.0, float(conservative_mark_price or 0.0))),
        dust_reason="fail_closed",
    )


def exposure_class_to_dict(exposure: ExposureClassification) -> Dict[str, object]:
    return {
        "exposure_class": str(exposure.exposure_class),
        "dust_share_eligible": bool(exposure.dust_share_eligible),
        "dust_notional_eligible": bool(exposure.dust_notional_eligible),
        "dust_gate_eligible": bool(exposure.dust_gate_eligible),
        "dust_notional_upper_bound_usd": float(exposure.dust_notional_upper_bound_usd),
        "dust_reason": str(exposure.dust_reason),
    }
