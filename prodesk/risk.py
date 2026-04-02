from __future__ import annotations

import collections
import datetime as dt
import math
import time
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .common import parse_ts, utc_now
from .models import BookTop, FillEvent, OrderIntent, Position, RiskDecision


class RiskEngine:
    def __init__(
        self,
        config: Dict[str, float],
        positions: Dict[str, Position],
        *,
        monotonic_fn: Optional[Callable[[], float]] = None,
        utc_now_fn: Optional[Callable[[], dt.datetime]] = None,
    ):
        self.cfg = config
        self.positions = positions
        self.kill_switch = False
        self.kill_reason = ""
        self._monotonic = monotonic_fn or time.monotonic
        self._utc_now = utc_now_fn or utc_now
        # Use monotonic time for rate limits so wall-clock shifts do not break guards.
        self.order_timestamps: Deque[float] = collections.deque()
        self.cancel_timestamps: Deque[float] = collections.deque()
        self.order_submission_transport_attempt_timestamps: Deque[float] = collections.deque()
        self._order_submission_reserved_outstanding: int = 0

    def set_kill_switch(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_reason = reason

    def clear_kill_switch(self) -> None:
        self.kill_switch = False
        self.kill_reason = ""

    @staticmethod
    def _order_side(order: object) -> str:
        return str(getattr(order, "side", "")).strip().upper()

    @staticmethod
    def _order_remaining_size(order: object) -> float:
        for attr in ("remaining_size", "size"):
            raw = getattr(order, attr, None)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0.0

    @staticmethod
    def _order_price(order: object, *, fallback: float) -> float:
        raw = getattr(order, "price", None)
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            return fallback
        if parsed <= 0:
            return fallback
        return parsed

    def _pending_same_side_exposure(
        self,
        *,
        side: str,
        open_orders_for_token: List[object],
        fallback_price: float,
    ) -> Tuple[float, float]:
        pending_shares = 0.0
        pending_notional = 0.0
        for order in open_orders_for_token:
            if self._order_side(order) != side:
                continue
            remaining = self._order_remaining_size(order)
            if remaining <= 0.0:
                continue
            pending_shares += remaining
            pending_notional += remaining * self._order_price(order, fallback=fallback_price)
        return pending_shares, pending_notional

    def _prune(self, window_sec: float = 60.0) -> None:
        now = self._monotonic()
        while self.order_timestamps and now - self.order_timestamps[0] > window_sec:
            self.order_timestamps.popleft()
        while self.cancel_timestamps and now - self.cancel_timestamps[0] > window_sec:
            self.cancel_timestamps.popleft()
        while (
            self.order_submission_transport_attempt_timestamps
            and now - self.order_submission_transport_attempt_timestamps[0] > window_sec
        ):
            self.order_submission_transport_attempt_timestamps.popleft()

    def reserve_order_submission(self) -> None:
        self._order_submission_reserved_outstanding = max(0, int(self._order_submission_reserved_outstanding)) + 1
        self._prune()

    def mark_order_submission_transport_attempted(self) -> None:
        self.order_submission_transport_attempt_timestamps.append(self._monotonic())
        self._prune()

    def release_order_submission_reservation(self) -> bool:
        if int(self._order_submission_reserved_outstanding) <= 0:
            self._prune()
            return False
        self._order_submission_reserved_outstanding = max(0, int(self._order_submission_reserved_outstanding) - 1)
        self._prune()
        return True

    def on_order_submitted(self) -> None:
        if int(self._order_submission_reserved_outstanding) > 0:
            self._order_submission_reserved_outstanding = max(0, int(self._order_submission_reserved_outstanding) - 1)
        self.order_timestamps.append(self._monotonic())
        self._prune()

    def on_order_canceled(self) -> None:
        self.cancel_timestamps.append(self._monotonic())
        self._prune()

    def can_cancel(self) -> RiskDecision:
        self._prune()
        if len(self.cancel_timestamps) >= int(self.cfg["max_cancels_per_min"]):
            return RiskDecision(False, "cancel_rate_limit", "max cancels/min reached")
        return RiskDecision(True, "ok")

    def remaining_order_capacity(self, soft_limit_pct: float = 1.0) -> int:
        self._prune()
        limit = max(1, int(self.cfg["max_orders_per_min"]))
        soft = max(1, int(math.floor(limit * float(soft_limit_pct))))
        used = len(self.order_timestamps) + int(self._order_submission_reserved_outstanding)
        return max(0, soft - used)

    def order_capacity_state(self, soft_limit_pct: float = 1.0) -> Dict[str, int]:
        self._prune()
        limit = max(1, int(self.cfg["max_orders_per_min"]))
        soft_limit = max(1, int(math.floor(limit * float(soft_limit_pct))))
        accepted_used = int(len(self.order_timestamps))
        reserved_outstanding = max(0, int(self._order_submission_reserved_outstanding))
        transport_attempted_recent = int(len(self.order_submission_transport_attempt_timestamps))
        effective_used = accepted_used + reserved_outstanding
        return {
            "orders_limit": int(limit),
            "orders_soft_limit": int(soft_limit),
            "orders_used_accepted": int(accepted_used),
            "orders_reserved_outstanding": int(reserved_outstanding),
            "orders_transport_attempted_recent": int(transport_attempted_recent),
            "orders_soft_effective_used": int(effective_used),
            "orders_soft_remaining": int(max(0, soft_limit - effective_used)),
        }

    def remaining_cancel_capacity(self, soft_limit_pct: float = 1.0) -> int:
        self._prune()
        limit = max(1, int(self.cfg["max_cancels_per_min"]))
        soft = max(1, int(math.floor(limit * float(soft_limit_pct))))
        used = len(self.cancel_timestamps)
        return max(0, soft - used)

    def rate_limit_snapshot(self) -> Dict[str, int]:
        self._prune()
        order_state = self.order_capacity_state(soft_limit_pct=1.0)
        return {
            "orders_used": len(self.order_timestamps),
            "orders_limit": int(self.cfg["max_orders_per_min"]),
            "cancels_used": len(self.cancel_timestamps),
            "cancels_limit": int(self.cfg["max_cancels_per_min"]),
            "orders_reserved_outstanding": int(order_state["orders_reserved_outstanding"]),
            "orders_transport_attempted_recent": int(order_state["orders_transport_attempted_recent"]),
        }

    def on_fill(self, fill: FillEvent) -> None:
        pos = self.positions.setdefault(fill.token_id, Position(token_id=fill.token_id))
        if fill.side.upper() == "BUY":
            pos.net_shares += fill.size
            pos.buy_shares += fill.size
            pos.bought_notional += fill.size * fill.price
        else:
            pos.net_shares -= fill.size
            pos.sell_shares += fill.size
            pos.sold_notional += fill.size * fill.price

    def validate_order(
        self,
        intent: OrderIntent,
        top: BookTop,
        open_orders_for_token: List[object],
        open_orders_total: int,
    ) -> RiskDecision:
        if self.kill_switch:
            return RiskDecision(False, "kill_switch", self.kill_reason)

        self._prune()
        if len(self.order_timestamps) >= int(self.cfg["max_orders_per_min"]):
            return RiskDecision(False, "order_rate_limit", "max orders/min reached")

        is_aggressive = bool(intent.post_only is False) or intent.tif.upper() in {"IOC", "FOK"}
        if not is_aggressive:
            if len(open_orders_for_token) >= int(self.cfg["max_open_orders_per_token"]):
                return RiskDecision(False, "open_orders_token_cap", "too many open orders for token")
            if open_orders_total >= int(self.cfg["max_total_open_orders"]):
                return RiskDecision(False, "open_orders_global_cap", "too many global open orders")

        if intent.size < float(self.cfg["min_order_size"]):
            return RiskDecision(False, "size_too_small", f"size={intent.size}")
        if intent.size > float(self.cfg["max_order_size"]):
            return RiskDecision(False, "size_too_large", f"size={intent.size}")

        if not (0.0 < intent.price < 1.0):
            return RiskDecision(False, "invalid_price", f"price={intent.price}")

        ts = parse_ts(top.ts_utc)
        if ts is None:
            return RiskDecision(False, "bad_book_timestamp", top.ts_utc)
        age = (self._utc_now() - ts).total_seconds()
        max_future_skew = float(self.cfg.get("max_book_future_skew_sec", 2.0))
        if age < -max_future_skew:
            return RiskDecision(False, "future_book_timestamp", f"age_sec={age:.3f}")
        if age > float(self.cfg["max_book_age_sec"]):
            return RiskDecision(False, "stale_book", f"age_sec={age:.3f}")

        if top.best_bid_price is not None and top.best_ask_price is not None:
            if top.best_bid_price > top.best_ask_price and not bool(self.cfg.get("allow_crossed_quotes", False)):
                return RiskDecision(False, "crossed_market", "bid > ask")

        pos = self.positions.setdefault(intent.token_id, Position(token_id=intent.token_id))
        pending_same_side_shares, pending_same_side_notional = self._pending_same_side_exposure(
            side=intent.side,
            open_orders_for_token=open_orders_for_token,
            fallback_price=float(intent.price),
        )
        pending_signed = pending_same_side_shares if intent.side == "BUY" else -pending_same_side_shares
        projected = pos.net_shares + pending_signed + (intent.size if intent.side == "BUY" else -intent.size)
        if abs(projected) > float(self.cfg["max_abs_position_shares"]):
            return RiskDecision(
                False,
                "position_cap",
                f"projected={projected:.2f},pending_same_side={pending_same_side_shares:.2f}",
            )

        max_notional = float(self.cfg["max_notional_per_token"])
        exposure_cap_mode = str(self.cfg.get("exposure_cap_mode", "per_market_total")).strip().lower()
        projected_notional = abs(projected * intent.price)
        if exposure_cap_mode == "per_side":
            projected_long = max(0.0, projected) * intent.price
            projected_short = max(0.0, -projected) * intent.price
            if intent.side == "BUY" and projected_long > max_notional:
                return RiskDecision(
                    False,
                    "notional_cap_long",
                    f"projected_long_notional={projected_long:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
                )
            if intent.side == "SELL" and projected_short > max_notional:
                return RiskDecision(
                    False,
                    "notional_cap_short",
                    f"projected_short_notional={projected_short:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
                )
        elif projected_notional > max_notional:
            return RiskDecision(
                False,
                "notional_cap",
                f"projected_notional={projected_notional:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
            )

        return RiskDecision(True, "ok")

    def mark_to_market(self, mid_by_token: Dict[str, Optional[float]]) -> Tuple[float, Dict[str, float]]:
        pnl_by_token: Dict[str, float] = {}
        total = 0.0
        for token_id, pos in self.positions.items():
            mid = mid_by_token.get(token_id)
            if mid is None:
                continue
            # Cash-flow convention:
            # - buys decrease cash (negative)
            # - sells increase cash (positive)
            # PnL = realized cashflow + mark of current inventory.
            pnl = (pos.sold_notional - pos.bought_notional) + (pos.net_shares * mid)
            pnl_by_token[token_id] = pnl
            total += pnl
        return total, pnl_by_token

    def evaluate_loss_limits(self, mid_by_token: Dict[str, Optional[float]]) -> RiskDecision:
        total_pnl, pnl_by_token = self.mark_to_market(mid_by_token)
        max_total_loss = self.cfg.get("max_total_loss")
        if max_total_loss is not None and total_pnl <= -float(max_total_loss):
            return RiskDecision(False, "max_total_loss", f"total_pnl={total_pnl:.4f}")

        max_loss_per_token = self.cfg.get("max_loss_per_token")
        if max_loss_per_token is not None:
            threshold = -float(max_loss_per_token)
            worst = min(pnl_by_token.values()) if pnl_by_token else None
            if worst is not None and worst <= threshold:
                return RiskDecision(False, "max_loss_per_token", f"worst_token_pnl={worst:.4f}")
        return RiskDecision(True, "ok")
