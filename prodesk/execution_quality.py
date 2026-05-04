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
        size = max(0.0, float(intent.size))
        bid = top.best_bid_price
        ask = top.best_ask_price
        bid_size = float(top.best_bid_size) if top.best_bid_size is not None else 0.0
        ask_size = float(top.best_ask_size) if top.best_ask_size is not None else 0.0

        crosses_touch = False
        queue_ahead = 0.0
        distance_to_touch = 0.0
        visible_touch_depth = 0.0

        if side == "BUY":
            visible_touch_depth = bid_size
            if ask is not None and price >= ask:
                crosses_touch = True
            elif bid is not None and ask is not None and bid < price < ask:
                # Inside-spread passive BUY quotes become the new best bid.
                queue_ahead = 0.0
                distance_to_touch = max(0.0, float(ask) - price)
            elif bid is not None:
                queue_ahead = bid_size
                distance_to_touch = max(0.0, float(bid) - price)
            elif ask is not None:
                distance_to_touch = max(0.0, float(ask) - price)
        else:
            visible_touch_depth = ask_size
            if bid is not None and price <= bid:
                crosses_touch = True
            elif bid is not None and ask is not None and bid < price < ask:
                # Inside-spread passive SELL quotes become the new best ask.
                queue_ahead = 0.0
                distance_to_touch = max(0.0, price - float(bid))
            elif ask is not None:
                queue_ahead = ask_size
                distance_to_touch = max(0.0, price - float(ask))
            elif bid is not None:
                distance_to_touch = max(0.0, price - float(bid))

        queue_factor = math.exp(-queue_ahead / self.queue_depth_scale)
        distance_factor = math.exp(-distance_to_touch / self.distance_scale)
        spread = top.spread if top.spread is not None else 0.0
        spread_factor = clamp(spread / 0.03, 0.25, 1.0)
        size_fill_factor = 1.0
        if size > 0.0:
            if visible_touch_depth <= 0.0:
                size_fill_factor = 0.0
            else:
                size_fill_factor = clamp(visible_touch_depth / size, 0.0, 1.0)
        cross_penalty = 1.0
        tif = str(intent.tif or "GTC").upper()
        if crosses_touch and bool(intent.post_only is not False) and tif not in {"IOC", "FOK"}:
            cross_penalty = 0.05
        expected_fill_prob = clamp(
            queue_factor * distance_factor * spread_factor * size_fill_factor * cross_penalty,
            0.0,
            1.0,
        )
        adverse_risk = clamp((1.0 - distance_factor) * (1.0 - spread_factor * 0.5), 0.0, 1.0)
        quality = clamp(expected_fill_prob - (adverse_risk * self.adverse_selection_penalty), 0.0, 1.0)
        return QuoteQuality(
            expected_fill_prob=expected_fill_prob,
            adverse_selection_risk=adverse_risk,
            expected_quality_score=quality,
            queue_ahead_size=queue_ahead,
            distance_to_touch=distance_to_touch,
        )
