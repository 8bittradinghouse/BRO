from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass
class BookTop:
    token_id: str
    ts_utc: str
    source: str
    best_bid_price: Optional[float] = None
    best_bid_size: Optional[float] = None
    best_ask_price: Optional[float] = None
    best_ask_size: Optional[float] = None

    @property
    def midpoint(self) -> Optional[float]:
        if self.best_bid_price is None or self.best_ask_price is None:
            return None
        return (self.best_bid_price + self.best_ask_price) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid_price is None or self.best_ask_price is None:
            return None
        return self.best_ask_price - self.best_bid_price


@dataclasses.dataclass
class OrderIntent:
    token_id: str
    side: str  # BUY or SELL
    price: float
    size: float
    tif: str = "GTC"
    post_only: Optional[bool] = None
    reason: Optional[str] = None
    market_id: Optional[str] = None
    window_id: Optional[str] = None
    stage: Optional[str] = None
    reason_code: Optional[str] = None
    timestamp_utc: Optional[str] = None
    execution_preference: Optional[str] = None
    target_ref: Optional[str] = None
    decision_reference_midpoint: Optional[float] = None
    decision_reference_source: Optional[str] = None
    decision_reference_lookup_key: Optional[str] = None
    decision_reference_ts_utc: Optional[str] = None
    token_median_lag_ms: Optional[float] = None
    oracle_tick_age_sec: Optional[float] = None
    submission_lane: Optional[str] = None
    commitment_hold_active: Optional[bool] = None
    commitment_hold_reason: Optional[str] = None
    commitment_expiry_ts_utc: Optional[str] = None


@dataclasses.dataclass
class LiveOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    remaining_size: float
    status: str
    client_order_id: Optional[str] = None
    created_ts_utc: Optional[str] = None
    submission_lane: Optional[str] = None
    commitment_hold_active: bool = False
    commitment_hold_reason: Optional[str] = None
    commitment_expiry_ts_utc: Optional[str] = None


@dataclasses.dataclass
class FillEvent:
    trade_id: str
    token_id: str
    side: str
    price: float
    size: float
    ts_utc: str
    order_id: Optional[str] = None
    source: str = "unknown"
    fill_policy_basis: Optional[str] = None
    execution_realism_class: Optional[str] = None
    decision_input_type: Optional[str] = None
    target_ref: Optional[str] = None
    paper_liquidity_depth_multiplier: Optional[float] = None
    paper_queue_position_mode: Optional[str] = None
    paper_queue_fill_multiplier: Optional[float] = None
    paper_maker_depth_consumption_ratio: Optional[float] = None
    paper_maker_eligible_depth: Optional[float] = None
    paper_chainlink_lag_class: Optional[str] = None
    paper_chainlink_lag_sec_effective: Optional[float] = None
    paper_chainlink_lag_penalty_bps: Optional[float] = None


@dataclasses.dataclass
class Position:
    token_id: str
    net_shares: float = 0.0
    buy_shares: float = 0.0
    sell_shares: float = 0.0
    bought_notional: float = 0.0
    sold_notional: float = 0.0

    @property
    def avg_buy_price(self) -> Optional[float]:
        if self.buy_shares <= 0:
            return None
        return self.bought_notional / self.buy_shares

    @property
    def avg_sell_price(self) -> Optional[float]:
        if self.sell_shares <= 0:
            return None
        return self.sold_notional / self.sell_shares


@dataclasses.dataclass
class RiskDecision:
    allowed: bool
    reason: str
    detail: Optional[str] = None
    basis: Optional[Dict[str, Any]] = None
