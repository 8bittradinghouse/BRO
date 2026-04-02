from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .common import clamp, round_to_tick
from .models import BookTop, OrderIntent, Position


class MarketMakingStrategy:
    def __init__(self, config: Dict[str, float]):
        self.cfg = config

    def _volatility_adjustments(self, realized_volatility: Optional[float]) -> Tuple[float, float, str]:
        vol_cfg = self.cfg.get("volatility", {})
        if not bool(vol_cfg.get("enabled", False)):
            return 1.0, 1.0, "disabled"
        if realized_volatility is None:
            return 1.0, 1.0, "unknown"

        low_threshold = float(vol_cfg.get("low_vol_threshold", 0.0015))
        high_threshold = float(vol_cfg.get("high_vol_threshold", 0.008))
        if realized_volatility <= low_threshold:
            return (
                float(vol_cfg.get("low_vol_spread_mult", 1.35)),
                float(vol_cfg.get("low_vol_size_mult", 0.85)),
                "low_vol",
            )
        if realized_volatility >= high_threshold:
            return (
                float(vol_cfg.get("high_vol_spread_mult", 0.8)),
                float(vol_cfg.get("high_vol_size_mult", 1.25)),
                "high_vol",
            )
        return 1.0, 1.0, "normal"

    def make_quotes(
        self,
        token_id: str,
        top: BookTop,
        position: Position,
        *,
        fair_probability: Optional[float] = None,
        realized_volatility: Optional[float] = None,
        size_multiplier: float = 1.0,
        spread_multiplier: float = 1.0,
    ) -> List[OrderIntent]:
        midpoint = top.midpoint
        if midpoint is None:
            return []

        observed_spread = top.spread if top.spread is not None else 0.0
        target_spread = clamp(
            max(float(self.cfg["min_spread"]), observed_spread),
            float(self.cfg["min_spread"]),
            float(self.cfg["max_spread"]),
        )
        spread_mult, size_mult, regime = self._volatility_adjustments(realized_volatility)
        target_spread = clamp(
            target_spread * spread_mult * max(0.25, float(spread_multiplier)),
            float(self.cfg["min_spread"]),
            float(self.cfg["max_spread"]),
        )

        inv_skew = position.net_shares * float(self.cfg["inventory_skew_per_share"])
        fair_skew = 0.0
        if fair_probability is not None:
            fair_prob = clamp(float(fair_probability), 0.001, 0.999)
            fair_skew_factor = float(self.cfg.get("fair_skew_factor", 0.5))
            fair_skew = fair_skew_factor * (fair_prob - midpoint)
        half = target_spread / 2.0
        raw_bid = midpoint - half - inv_skew + fair_skew
        raw_ask = midpoint + half - inv_skew + fair_skew

        tick = float(self.cfg["tick_size"])
        bid = round_to_tick(clamp(raw_bid, 0.001, 0.999), tick)
        ask = round_to_tick(clamp(raw_ask, 0.001, 0.999), tick)

        if bid >= ask:
            ask = clamp(bid + tick, 0.001, 0.999)

        size = clamp(
            float(self.cfg["base_order_size"]) * size_mult * max(0.01, float(size_multiplier)),
            float(self.cfg["min_order_size"]),
            float(self.cfg["max_order_size"]),
        )

        return [
            OrderIntent(token_id=token_id, side="BUY", price=bid, size=size, reason=f"mm_quote:{regime}"),
            OrderIntent(token_id=token_id, side="SELL", price=ask, size=size, reason=f"mm_quote:{regime}"),
        ]
