from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, Optional

from .common import clamp
from .models import BookTop, OrderIntent


@dataclasses.dataclass(frozen=True)
class QuoteQuality:
    expected_fill_prob: float
    adverse_selection_risk: float
    expected_quality_score: float
    queue_ahead_size: float
    distance_to_touch: float


class ExecutionQualityModel:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.min_expected_fill_prob = clamp(float(cfg.get("min_expected_fill_prob", 0.06)), 0.0, 1.0)
        self.max_queue_ahead_size = max(0.0, float(cfg.get("max_queue_ahead_size", 200.0)))
        self.queue_depth_scale = max(1.0, float(cfg.get("queue_depth_scale", 120.0)))
        self.distance_scale = max(0.0005, float(cfg.get("distance_scale", 0.02)))
        self.adverse_selection_penalty = max(0.0, float(cfg.get("adverse_selection_penalty", 0.3)))

    def assess_quote(self, *, intent: OrderIntent, top: BookTop) -> QuoteQuality:
        side = intent.side.upper()
        price = float(intent.price)
        bid = top.best_bid_price
        ask = top.best_ask_price
        bid_size = float(top.best_bid_size) if top.best_bid_size is not None else 0.0
        ask_size = float(top.best_ask_size) if top.best_ask_size is not None else 0.0

        if side == "BUY":
            touch = bid if bid is not None else price
            queue_ahead = 0.0
            if bid is not None:
                if price <= bid:
                    queue_ahead = bid_size
                else:
                    queue_ahead = max(0.0, bid_size * 0.35)
            distance_to_touch = max(0.0, (touch - price) if touch is not None else 0.0)
        else:
            touch = ask if ask is not None else price
            queue_ahead = 0.0
            if ask is not None:
                if price >= ask:
                    queue_ahead = ask_size
                else:
                    queue_ahead = max(0.0, ask_size * 0.35)
            distance_to_touch = max(0.0, (price - touch) if touch is not None else 0.0)

        queue_factor = math.exp(-queue_ahead / self.queue_depth_scale)
        distance_factor = math.exp(-distance_to_touch / self.distance_scale)
        spread = top.spread if top.spread is not None else 0.0
        spread_factor = clamp(spread / 0.03, 0.25, 1.0)
        expected_fill_prob = clamp(queue_factor * distance_factor * spread_factor, 0.0, 1.0)
        adverse_risk = clamp((1.0 - distance_factor) * (1.0 - spread_factor * 0.5), 0.0, 1.0)
        quality = clamp(expected_fill_prob - (adverse_risk * self.adverse_selection_penalty), 0.0, 1.0)
        return QuoteQuality(
            expected_fill_prob=expected_fill_prob,
            adverse_selection_risk=adverse_risk,
            expected_quality_score=quality,
            queue_ahead_size=queue_ahead,
            distance_to_touch=distance_to_touch,
        )
