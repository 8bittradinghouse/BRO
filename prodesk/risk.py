from __future__ import annotations

import collections
import datetime as dt
import math
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from .common import parse_ts, utc_now
from .exposure_classifier import EXPOSURE_CLASS_MEANINGFUL, is_flat_position
from .models import BookTop, FillEvent, OrderIntent, Position, RiskDecision


class RiskEngine:
    _POSITION_EPSILON = 1e-9

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
        self._valuation_hard_degraded: bool = False
        self._valuation_degraded_reasons: List[str] = []
        self._exposure_class_by_token: Dict[str, str] = {}
        self._last_mark_to_market_skipped_nonflat_by_class: Dict[str, int] = {}

    def set_kill_switch(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_reason = reason

    def clear_kill_switch(self) -> None:
        self.kill_switch = False
        self.kill_reason = ""

    def set_valuation_degraded_state(self, *, hard_degraded: bool, reasons: Optional[List[str]] = None) -> None:
        self._valuation_hard_degraded = bool(hard_degraded)
        self._valuation_degraded_reasons = [str(r).strip() for r in list(reasons or []) if str(r).strip()]

    def valuation_degraded_state(self) -> Dict[str, Any]:
        return {
            "hard_degraded": bool(self._valuation_hard_degraded),
            "reasons": list(self._valuation_degraded_reasons),
        }

    def set_exposure_classification_state(self, *, exposure_class_by_token: Optional[Dict[str, Any]] = None) -> None:
        raw = exposure_class_by_token if isinstance(exposure_class_by_token, dict) else {}
        normalized: Dict[str, str] = {}
        for token_id, exposure_class in raw.items():
            token = str(token_id or "").strip()
            if not token:
                continue
            klass = str(exposure_class or "").strip().upper() or EXPOSURE_CLASS_MEANINGFUL
            normalized[token] = klass
        self._exposure_class_by_token = normalized

    @classmethod
    def _is_flat_position(cls, net_shares: float) -> bool:
        return is_flat_position(float(net_shares), position_epsilon=cls._POSITION_EPSILON)

    @classmethod
    def _is_pure_risk_reducing_intent(cls, *, net_shares: float, side: str, size: float) -> bool:
        size_abs = abs(float(size))
        if size_abs <= cls._POSITION_EPSILON:
            return False
        net = float(net_shares)
        normalized_side = str(side or "").strip().upper()
        if cls._is_flat_position(net):
            return False
        if net > 0.0:
            if normalized_side != "SELL":
                return False
            return bool(size_abs <= (net + cls._POSITION_EPSILON))
        if normalized_side != "BUY":
            return False
        return bool(size_abs <= ((-net) + cls._POSITION_EPSILON))

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

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(out):
            return None
        return float(out)

    @staticmethod
    def _hour_in_window(hour: int, start_hour: int, end_hour: int) -> bool:
        if start_hour == end_hour:
            return True
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        return hour >= start_hour or hour < end_hour

    def _resolve_dynamic_scaling(
        self,
        *,
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        cfg = self.cfg.get("dynamic_scaling", {})
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return 1.0, {
                "enabled": False,
                "effective_multiplier": 1.0,
                "scaling_class": "neutral",
                "unknown_inputs": [],
            }
        ctx = risk_context if isinstance(risk_context, dict) else {}
        unknown_inputs: List[str] = []

        vol_mult = 1.0
        vol_component = "disabled"
        if bool(cfg.get("volatility_enabled", True)):
            vol = self._safe_float(ctx.get("realized_volatility"))
            if vol is None:
                vol_component = "unknown_input"
                unknown_inputs.append("realized_volatility")
            else:
                low = max(0.0, float(cfg.get("volatility_low_threshold", 0.0015)))
                high = max(low, float(cfg.get("volatility_high_threshold", 0.008)))
                low_mult = max(1e-6, float(cfg.get("volatility_low_mult", 1.05)))
                high_mult = max(1e-6, float(cfg.get("volatility_high_mult", 0.85)))
                if vol <= low:
                    vol_mult = low_mult
                    vol_component = "low_vol_aggressive"
                elif vol >= high:
                    vol_mult = high_mult
                    vol_component = "high_vol_conservative"
                else:
                    vol_component = "neutral_band"

        tod_mult = 1.0
        tod_component = "disabled"
        used_hour_utc = int(self._utc_now().hour)
        if bool(cfg.get("tod_enabled", True)):
            hour = self._safe_float(ctx.get("tod_hour_utc"))
            if hour is None:
                hour = float(self._utc_now().hour)
            used_hour_utc = int(hour) % 24
            start = int(float(cfg.get("tod_start_hour_utc", 2)))
            end = int(float(cfg.get("tod_end_hour_utc", 6)))
            in_window = self._hour_in_window(used_hour_utc, start, end)
            if in_window:
                tod_mult = max(1e-6, float(cfg.get("tod_thin_liquidity_mult", 0.9)))
                tod_component = "thin_liquidity_window"
            else:
                tod_component = "regular_liquidity_window"

        edge_mult = 1.0
        edge_component = "disabled"
        if bool(cfg.get("edge_enabled", True)):
            edge_abs = self._safe_float(ctx.get("edge_abs"))
            if edge_abs is None:
                edge_component = "unknown_input"
                unknown_inputs.append("edge_abs")
            else:
                start = max(0.0, float(cfg.get("edge_start_abs", 0.10)))
                full = max(start, float(cfg.get("edge_full_abs", 0.30)))
                edge_mult_max = max(1.0, float(cfg.get("edge_mult_max", 1.15)))
                if edge_abs <= start:
                    edge_component = "below_start"
                elif edge_abs >= full:
                    edge_mult = edge_mult_max
                    edge_component = "at_or_above_full"
                else:
                    span = max(1e-9, full - start)
                    frac = max(0.0, min(1.0, (edge_abs - start) / span))
                    edge_mult = 1.0 + ((edge_mult_max - 1.0) * frac)
                    edge_component = "between_start_full"

        raw_multiplier = float(vol_mult) * float(tod_mult) * float(edge_mult)
        min_mult = max(1e-6, float(cfg.get("min_effective_mult", 0.75)))
        max_mult = max(min_mult, float(cfg.get("max_effective_mult", 1.25)))
        effective_multiplier = max(min_mult, min(max_mult, raw_multiplier))

        unknown_policy = str(cfg.get("unknown_input_policy", "no_aggressive_uplift")).strip().lower()
        if unknown_inputs and unknown_policy == "no_aggressive_uplift" and effective_multiplier > 1.0:
            effective_multiplier = 1.0

        scaling_class = "neutral"
        if unknown_inputs and abs(effective_multiplier - 1.0) <= 1e-9:
            scaling_class = "unknown_input"
        elif effective_multiplier < 1.0 - 1e-9:
            scaling_class = "conservative"
        elif effective_multiplier > 1.0 + 1e-9:
            scaling_class = "aggressive"

        return float(effective_multiplier), {
            "enabled": True,
            "effective_multiplier": float(effective_multiplier),
            "raw_multiplier": float(raw_multiplier),
            "min_effective_mult": float(min_mult),
            "max_effective_mult": float(max_mult),
            "scaling_class": scaling_class,
            "unknown_inputs": sorted(set(unknown_inputs)),
            "unknown_input_policy": unknown_policy,
            "components": {
                "volatility": {
                    "component_class": vol_component,
                    "multiplier": float(vol_mult),
                    "realized_volatility": self._safe_float(ctx.get("realized_volatility")),
                },
                "tod": {
                    "component_class": tod_component,
                    "multiplier": float(tod_mult),
                    "hour_utc": int(used_hour_utc),
                },
                "edge": {
                    "component_class": edge_component,
                    "multiplier": float(edge_mult),
                    "edge_abs": self._safe_float(ctx.get("edge_abs")),
                },
            },
        }

    def _global_exposure_snapshot(
        self,
        *,
        intent: OrderIntent,
        open_orders_all: List[object],
        reference_mid_by_token: Dict[str, Optional[float]],
        effective_multiplier: float,
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cfg = self.cfg.get("global_exposure_guard", {})
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return {
                "enabled": False,
                "projected_total_notional": 0.0,
                "projected_to_cap_ratio": 0.0,
                "within_cap": True,
            }

        base_cap = max(0.0, float(cfg.get("max_global_notional_usd", 0.0)))
        effective_cap = float(base_cap * max(0.0, float(effective_multiplier)))
        context = risk_context if isinstance(risk_context, dict) else {}
        submission_lane = str(context.get("submission_lane") or "unknown").strip().lower()
        stage = str(context.get("stage") or "unknown").strip().upper()
        sniper_reserved_notional_usd = max(0.0, float(cfg.get("sniper_reserved_notional_usd", 0.0) or 0.0))
        reserve_applied = False
        if sniper_reserved_notional_usd > 0.0:
            is_taker_non_sniper = (submission_lane == "taker") and (stage != "SNIPER_PRIMARY")
            if is_taker_non_sniper:
                effective_cap = max(0.0, float(effective_cap - sniper_reserved_notional_usd))
                reserve_applied = True
        near_cap_ratio = max(0.0, float(cfg.get("near_cap_ratio", 0.85)))

        position_notional = 0.0
        unknown_position_tokens: List[str] = []
        for token_id, pos in self.positions.items():
            px = self._safe_float(reference_mid_by_token.get(token_id))
            if px is None or px <= 0.0:
                if pos.net_shares > 0 and isinstance(pos.avg_buy_price, (int, float)):
                    px = float(pos.avg_buy_price)
                elif pos.net_shares < 0 and isinstance(pos.avg_sell_price, (int, float)):
                    px = float(pos.avg_sell_price)
                elif token_id == intent.token_id:
                    px = float(intent.price)
            if px is None or px <= 0.0:
                unknown_position_tokens.append(str(token_id))
                continue
            position_notional += abs(float(pos.net_shares)) * float(px)

        resting_notional = 0.0
        for order in open_orders_all:
            remaining = self._order_remaining_size(order)
            if remaining <= 0.0:
                continue
            order_token = str(getattr(order, "token_id", "") or "")
            fallback_price = self._safe_float(reference_mid_by_token.get(order_token))
            if fallback_price is None or fallback_price <= 0.0:
                fallback_price = float(intent.price if order_token == intent.token_id else 0.0)
            resting_notional += remaining * self._order_price(order, fallback=float(fallback_price))

        incoming_notional = abs(float(intent.size) * float(intent.price))
        projected_total = float(position_notional + resting_notional + incoming_notional)
        ratio = (projected_total / effective_cap) if effective_cap > 0.0 else math.inf
        within_cap = bool(effective_cap > 0.0 and projected_total <= effective_cap + 1e-9)
        return {
            "enabled": True,
            "base_cap_usd": float(base_cap),
            "effective_cap_usd": float(effective_cap),
            "sniper_reserved_notional_usd": float(sniper_reserved_notional_usd),
            "sniper_reserve_applied": bool(reserve_applied),
            "sniper_reserve_scope": "taker_non_sniper_only",
            "near_cap_ratio": float(near_cap_ratio),
            "projected_total_notional": float(projected_total),
            "projected_to_cap_ratio": float(ratio if math.isfinite(ratio) else 0.0),
            "position_notional": float(position_notional),
            "resting_open_order_notional": float(resting_notional),
            "incoming_intent_notional": float(incoming_notional),
            "unknown_position_tokens": sorted(set(unknown_position_tokens)),
            "within_cap": within_cap,
            "near_cap": bool(effective_cap > 0.0 and ratio >= near_cap_ratio),
        }

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

    def _order_rate_recovery_reserved_slots(self, *, limit: int) -> int:
        raw = self.cfg.get("order_rate_recovery_reserved_slots", 0)
        try:
            reserved = int(float(raw or 0.0))
        except (TypeError, ValueError):
            reserved = 0
        return max(0, min(int(reserved), max(0, int(limit) - 1)))

    def order_capacity_state(self, soft_limit_pct: float = 1.0) -> Dict[str, int]:
        self._prune()
        limit = max(1, int(self.cfg["max_orders_per_min"]))
        soft_limit = max(1, int(math.floor(limit * float(soft_limit_pct))))
        reserved_recovery_slots = self._order_rate_recovery_reserved_slots(limit=limit)
        non_recovery_hard_limit = max(1, int(limit - reserved_recovery_slots))
        non_recovery_soft_limit = max(0, int(soft_limit - reserved_recovery_slots))
        accepted_used = int(len(self.order_timestamps))
        reserved_outstanding = max(0, int(self._order_submission_reserved_outstanding))
        transport_attempted_recent = int(len(self.order_submission_transport_attempt_timestamps))
        effective_used = accepted_used + reserved_outstanding
        return {
            "orders_limit": int(limit),
            "orders_soft_limit": int(soft_limit),
            "orders_recovery_reserved_slots": int(reserved_recovery_slots),
            "orders_hard_limit_non_recovery": int(non_recovery_hard_limit),
            "orders_soft_limit_non_recovery": int(non_recovery_soft_limit),
            "orders_used_accepted": int(accepted_used),
            "orders_reserved_outstanding": int(reserved_outstanding),
            "orders_transport_attempted_recent": int(transport_attempted_recent),
            "orders_soft_effective_used": int(effective_used),
            "orders_soft_remaining": int(max(0, soft_limit - effective_used)),
            "orders_soft_remaining_non_recovery": int(max(0, non_recovery_soft_limit - effective_used)),
            "orders_hard_remaining_recovery": int(max(0, limit - accepted_used)),
            "orders_hard_remaining_non_recovery": int(max(0, non_recovery_hard_limit - accepted_used)),
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
        open_orders_all: Optional[List[object]] = None,
        reference_mid_by_token: Optional[Dict[str, Optional[float]]] = None,
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> RiskDecision:
        if self.kill_switch:
            return RiskDecision(False, "kill_switch", self.kill_reason, basis={"risk_authority": "kill_switch"})

        self._prune()

        context = risk_context if isinstance(risk_context, dict) else {}
        pos = self.positions.setdefault(intent.token_id, Position(token_id=intent.token_id))
        pure_risk_reducing_intent = self._is_pure_risk_reducing_intent(
            net_shares=float(pos.net_shares),
            side=str(intent.side or ""),
            size=float(intent.size),
        )
        financial_posture_class = str(context.get("financial_posture_class") or "UNKNOWN").strip().upper()
        reduce_only_recovery_active = bool(context.get("reduce_only_recovery_active", False))
        require_lifecycle_context_for_decisions = bool(
            context.get("require_lifecycle_context_for_decisions", False)
        )
        sec_to_expiry = self._safe_float(context.get("sec_to_expiry"))
        lifecycle_context_present = bool(sec_to_expiry is not None)
        lifecycle_context_missing_reason = str(context.get("lifecycle_context_missing_reason") or "").strip()
        lifecycle_context_mismatch = bool(context.get("lifecycle_context_mismatch", False))
        if reduce_only_recovery_active and financial_posture_class == "NORMAL":
            lifecycle_context_mismatch = True
            if not lifecycle_context_missing_reason:
                lifecycle_context_missing_reason = "reduce_only_recovery_active_with_normal_financial_posture"
        reduce_only_recovery_priority = bool(reduce_only_recovery_active and pure_risk_reducing_intent)
        context_stage = str(context.get("stage") or "").strip().upper() or "UNKNOWN"
        context_submission_lane = str(context.get("submission_lane") or "").strip().lower() or "unknown"
        early_basis_base: Dict[str, Any] = {
            "submission_lane": str(context_submission_lane),
            "stage": str(context_stage),
            "financial_posture_class": str(financial_posture_class),
            "sec_to_expiry": sec_to_expiry,
            "reduce_only_recovery_active": bool(reduce_only_recovery_active),
            "reduce_only_recovery_priority": bool(reduce_only_recovery_priority),
            "lifecycle_context_present": bool(lifecycle_context_present),
            "lifecycle_context_missing_reason": str(lifecycle_context_missing_reason),
            "lifecycle_context_mismatch": bool(lifecycle_context_mismatch),
            "require_lifecycle_context_for_decisions": bool(require_lifecycle_context_for_decisions),
        }
        if lifecycle_context_mismatch and (not pure_risk_reducing_intent):
            return RiskDecision(
                False,
                "lifecycle_context_posture_mismatch_blocked",
                (
                    "risk-increasing intent blocked because "
                    "reduce_only_recovery_active is incompatible with NORMAL posture"
                ),
                basis={
                    **early_basis_base,
                    "risk_authority": "lifecycle_context",
                    "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                },
            )
        if financial_posture_class == "HALT_NEW_RISK" and (not pure_risk_reducing_intent):
            return RiskDecision(
                False,
                "terminal_unwind_halt_new_risk_blocked",
                (
                    f"net_shares={float(pos.net_shares):.9f}:size={float(intent.size):.9f}:"
                    f"side={str(intent.side or '').upper()}"
                ),
                basis={
                    **early_basis_base,
                    "risk_authority": "terminal_unwind_halt_new_risk",
                    "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                    "terminal_unwind_halt_new_risk_active": True,
                },
            )
        order_capacity = self.order_capacity_state(soft_limit_pct=1.0)
        hard_limit = (
            int(order_capacity.get("orders_limit") or 0)
            if reduce_only_recovery_priority
            else int(order_capacity.get("orders_hard_limit_non_recovery") or 0)
        )
        if int(order_capacity.get("orders_used_accepted") or 0) >= int(hard_limit):
            return RiskDecision(
                False,
                "order_rate_limit",
                (
                    f"used={int(order_capacity.get('orders_used_accepted') or 0)}"
                    f">=limit={int(hard_limit)}"
                ),
                basis={
                    **early_basis_base,
                    "risk_authority": "order_rate",
                    "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                    "order_rate_limit_basis": {
                        "orders_limit": int(order_capacity.get("orders_limit") or 0),
                        "orders_hard_limit_non_recovery": int(
                            order_capacity.get("orders_hard_limit_non_recovery") or 0
                        ),
                        "orders_recovery_reserved_slots": int(
                            order_capacity.get("orders_recovery_reserved_slots") or 0
                        ),
                        "orders_used_accepted": int(order_capacity.get("orders_used_accepted") or 0),
                    },
                },
            )

        is_aggressive = bool(intent.post_only is False) or intent.tif.upper() in {"IOC", "FOK"}
        if not is_aggressive:
            if len(open_orders_for_token) >= int(self.cfg["max_open_orders_per_token"]):
                return RiskDecision(
                    False,
                    "open_orders_token_cap",
                    "too many open orders for token",
                    basis={**early_basis_base, "risk_authority": "open_orders_token_cap"},
                )
            if open_orders_total >= int(self.cfg["max_total_open_orders"]):
                return RiskDecision(
                    False,
                    "open_orders_global_cap",
                    "too many global open orders",
                    basis={**early_basis_base, "risk_authority": "open_orders_global_cap"},
                )

        min_order_size = float(self.cfg["min_order_size"])
        reduce_only_terminal_min_notional_usd = float(
            self.cfg.get("reduce_only_terminal_min_notional_usd", 0.0) or 0.0
        )
        terminal_reduce_only_posture = financial_posture_class in {
            "PREEXPIRY_REDUCE_ONLY",
            "HARD_DEGRADED_REDUCE_ONLY",
            "HALT_NEW_RISK",
        }
        terminal_reduce_only_notional_exemption = bool(
            reduce_only_recovery_priority
            and terminal_reduce_only_posture
            and float(intent.size) + 1e-9 < min_order_size
            and reduce_only_terminal_min_notional_usd > 0.0
            and float(intent.price) > 0.0
            and (float(intent.size) * float(intent.price) + 1e-9) >= reduce_only_terminal_min_notional_usd
        )
        if float(intent.size) < min_order_size and (not terminal_reduce_only_notional_exemption):
            return RiskDecision(
                False,
                "size_too_small",
                f"size={intent.size}",
                basis={**early_basis_base, "risk_authority": "size_bounds"},
            )
        if intent.size > float(self.cfg["max_order_size"]):
            return RiskDecision(
                False,
                "size_too_large",
                f"size={intent.size}",
                basis={**early_basis_base, "risk_authority": "size_bounds"},
            )

        if not (0.0 < intent.price < 1.0):
            return RiskDecision(
                False,
                "invalid_price",
                f"price={intent.price}",
                basis={**early_basis_base, "risk_authority": "price_bounds"},
            )

        ts = parse_ts(top.ts_utc)
        if ts is None:
            return RiskDecision(
                False,
                "bad_book_timestamp",
                top.ts_utc,
                basis={**early_basis_base, "risk_authority": "book_freshness"},
            )
        age = (self._utc_now() - ts).total_seconds()
        max_future_skew = float(self.cfg.get("max_book_future_skew_sec", 2.0))
        if age < -max_future_skew:
            return RiskDecision(
                False,
                "future_book_timestamp",
                f"age_sec={age:.3f}",
                basis={**early_basis_base, "risk_authority": "book_freshness"},
            )
        if age > float(self.cfg["max_book_age_sec"]):
            return RiskDecision(
                False,
                "stale_book",
                f"age_sec={age:.3f}",
                basis={**early_basis_base, "risk_authority": "book_freshness"},
            )

        if top.best_bid_price is not None and top.best_ask_price is not None:
            if top.best_bid_price > top.best_ask_price and not bool(self.cfg.get("allow_crossed_quotes", False)):
                return RiskDecision(
                    False,
                    "crossed_market",
                    "bid > ask",
                    basis={**early_basis_base, "risk_authority": "market_sanity"},
                )

        effective_multiplier, dynamic_scaling_basis = self._resolve_dynamic_scaling(risk_context=context)
        max_abs_position_base = float(self.cfg["max_abs_position_shares"])
        max_notional_base = float(self.cfg["max_notional_per_token"])
        max_abs_position_effective = max(1e-9, max_abs_position_base * float(effective_multiplier))
        max_notional_effective = max(1e-9, max_notional_base * float(effective_multiplier))
        basis_base: Dict[str, Any] = {
            "risk_authority": "risk_engine_v2",
            "submission_lane": str(context_submission_lane),
            "stage": str(context_stage),
            "financial_posture_class": str(financial_posture_class),
            "sec_to_expiry": sec_to_expiry,
            "min_sec_to_expiry_for_new_exposure": self._safe_float(
                self.cfg.get("min_sec_to_expiry_for_new_exposure")
            ),
            "edge_abs": self._safe_float(context.get("edge_abs")),
            "realized_volatility": self._safe_float(context.get("realized_volatility")),
            "dynamic_scaling": dynamic_scaling_basis,
            "valuation_hard_degraded": bool(self._valuation_hard_degraded),
            "valuation_degraded_reasons": list(self._valuation_degraded_reasons),
            "reduce_only_recovery_active": bool(reduce_only_recovery_active),
            "reduce_only_recovery_priority": bool(reduce_only_recovery_priority),
            "lifecycle_context_present": bool(lifecycle_context_present),
            "lifecycle_context_missing_reason": str(lifecycle_context_missing_reason),
            "lifecycle_context_mismatch": bool(lifecycle_context_mismatch),
            "require_lifecycle_context_for_decisions": bool(require_lifecycle_context_for_decisions),
            "reduce_only_terminal_min_notional_usd": float(reduce_only_terminal_min_notional_usd),
            "terminal_reduce_only_notional_exemption": bool(terminal_reduce_only_notional_exemption),
            "effective_caps": {
                "max_abs_position_shares_base": float(max_abs_position_base),
                "max_abs_position_shares_effective": float(max_abs_position_effective),
                "max_notional_per_token_base": float(max_notional_base),
                "max_notional_per_token_effective": float(max_notional_effective),
                "effective_multiplier": float(effective_multiplier),
            },
            "order_rate_limit_basis": {
                "orders_limit": int(order_capacity.get("orders_limit") or 0),
                "orders_hard_limit_non_recovery": int(order_capacity.get("orders_hard_limit_non_recovery") or 0),
                "orders_recovery_reserved_slots": int(order_capacity.get("orders_recovery_reserved_slots") or 0),
                "orders_used_accepted": int(order_capacity.get("orders_used_accepted") or 0),
            },
            "intent_exposure_class": str(
                self._exposure_class_by_token.get(str(intent.token_id), EXPOSURE_CLASS_MEANINGFUL)
            ),
        }

        min_sec_to_expiry_for_new_exposure = float(self.cfg.get("min_sec_to_expiry_for_new_exposure", 0.0) or 0.0)
        if require_lifecycle_context_for_decisions and (not pure_risk_reducing_intent) and sec_to_expiry is None:
            return RiskDecision(
                False,
                "new_exposure_sec_to_expiry_unknown_blocked",
                "risk-increasing intent requires sec_to_expiry lifecycle context",
                basis={
                    **basis_base,
                    "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                    "sec_to_expiry": None,
                },
            )
        if min_sec_to_expiry_for_new_exposure > 0.0 and (not pure_risk_reducing_intent):
            if sec_to_expiry is None:
                return RiskDecision(
                    False,
                    "new_exposure_sec_to_expiry_unknown_blocked",
                    (
                        "risk-increasing intent requires sec_to_expiry context when "
                        f"min_sec_to_expiry_for_new_exposure={min_sec_to_expiry_for_new_exposure:.3f}"
                    ),
                    basis={
                        **basis_base,
                        "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                        "sec_to_expiry": None,
                    },
                )
            if float(sec_to_expiry) <= (min_sec_to_expiry_for_new_exposure + 1e-9):
                return RiskDecision(
                    False,
                    "new_exposure_expiry_gate_blocked",
                    (
                        f"sec_to_expiry={float(sec_to_expiry):.6f}"
                        f"<=min_sec_to_expiry_for_new_exposure={min_sec_to_expiry_for_new_exposure:.6f}"
                    ),
                    basis={
                        **basis_base,
                        "risk_reduction_only_intent": bool(pure_risk_reducing_intent),
                        "sec_to_expiry": float(sec_to_expiry),
                    },
                )
        if self._valuation_hard_degraded:
            if not pure_risk_reducing_intent:
                return RiskDecision(
                    False,
                    "valuation_hard_degraded_risk_increase_blocked",
                    f"net_shares={float(pos.net_shares):.9f}:size={float(intent.size):.9f}:side={str(intent.side or '').upper()}",
                    basis={
                        **basis_base,
                        "valuation_hard_degraded": True,
                        "valuation_degraded_reasons": list(self._valuation_degraded_reasons),
                        "risk_reduction_only_mode": True,
                    },
                )
        pending_same_side_shares, pending_same_side_notional = self._pending_same_side_exposure(
            side=intent.side,
            open_orders_for_token=open_orders_for_token,
            fallback_price=float(intent.price),
        )
        pending_signed = pending_same_side_shares if intent.side == "BUY" else -pending_same_side_shares
        projected = pos.net_shares + pending_signed + (intent.size if intent.side == "BUY" else -intent.size)
        if abs(projected) > float(max_abs_position_effective):
            return RiskDecision(
                False,
                "position_cap",
                f"projected={projected:.2f},pending_same_side={pending_same_side_shares:.2f}",
                basis={
                    **basis_base,
                    "projected_position_shares": float(projected),
                    "pending_same_side_shares": float(pending_same_side_shares),
                },
            )

        exposure_cap_mode = str(self.cfg.get("exposure_cap_mode", "per_market_total")).strip().lower()
        projected_notional = abs(projected * intent.price)
        if exposure_cap_mode == "per_side":
            projected_long = max(0.0, projected) * intent.price
            projected_short = max(0.0, -projected) * intent.price
            if intent.side == "BUY" and projected_long > max_notional_effective:
                return RiskDecision(
                    False,
                    "notional_cap_long",
                    f"projected_long_notional={projected_long:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
                    basis={
                        **basis_base,
                        "projected_long_notional": float(projected_long),
                        "pending_same_side_notional": float(pending_same_side_notional),
                    },
                )
            if intent.side == "SELL" and projected_short > max_notional_effective:
                return RiskDecision(
                    False,
                    "notional_cap_short",
                    f"projected_short_notional={projected_short:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
                    basis={
                        **basis_base,
                        "projected_short_notional": float(projected_short),
                        "pending_same_side_notional": float(pending_same_side_notional),
                    },
                )
        elif projected_notional > max_notional_effective:
            return RiskDecision(
                False,
                "notional_cap",
                f"projected_notional={projected_notional:.2f},pending_same_side_notional={pending_same_side_notional:.2f}",
                basis={
                    **basis_base,
                    "projected_notional": float(projected_notional),
                    "pending_same_side_notional": float(pending_same_side_notional),
                },
            )

        global_snapshot = self._global_exposure_snapshot(
            intent=intent,
            open_orders_all=list(open_orders_all or open_orders_for_token),
            reference_mid_by_token=(
                dict(reference_mid_by_token) if isinstance(reference_mid_by_token, dict) else {intent.token_id: intent.price}
            ),
            effective_multiplier=float(effective_multiplier),
            risk_context=context,
        )
        if bool(global_snapshot.get("enabled", False)) and not bool(global_snapshot.get("within_cap", True)):
            return RiskDecision(
                False,
                "global_exposure_cap",
                (
                    "projected_global_notional="
                    + f"{float(global_snapshot.get('projected_total_notional', 0.0)):.2f},"
                    + "effective_global_cap="
                    + f"{float(global_snapshot.get('effective_cap_usd', 0.0)):.2f}"
                ),
                basis={**basis_base, "global_exposure_guard": global_snapshot},
            )

        return RiskDecision(True, "ok", basis={**basis_base, "global_exposure_guard": global_snapshot})

    def preview_order_feasibility(
        self,
        intent: OrderIntent,
        top: BookTop,
        open_orders_for_token: List[object],
        open_orders_total: int,
        open_orders_all: Optional[List[object]] = None,
        reference_mid_by_token: Optional[Dict[str, Optional[float]]] = None,
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> RiskDecision:
        """Advisory-only preview for order feasibility.

        This method is explicitly non-authoritative and non-reserving:
        - does not reserve submission capacity
        - does not mutate position/exposure state
        - does not replace validate_order authority on final submit path
        """
        decision = self.validate_order(
            intent,
            top,
            open_orders_for_token,
            open_orders_total,
            open_orders_all=open_orders_all,
            reference_mid_by_token=reference_mid_by_token,
            risk_context=risk_context,
        )
        basis = dict(decision.basis) if isinstance(decision.basis, dict) else {}
        basis["preview_authority"] = "advisory_read_only"
        basis["preview_non_authoritative"] = True
        return RiskDecision(
            allowed=bool(decision.allowed),
            reason=str(decision.reason or "unknown"),
            detail=decision.detail,
            basis=basis,
        )

    def mark_to_market(self, mid_by_token: Dict[str, Optional[float]]) -> Tuple[float, Dict[str, float]]:
        pnl_by_token: Dict[str, float] = {}
        skipped_nonflat_by_class: Dict[str, int] = {}
        total = 0.0
        for token_id, pos in self.positions.items():
            mid = mid_by_token.get(token_id)
            realized_cashflow = float(pos.sold_notional - pos.bought_notional)
            if mid is None:
                if not self._is_flat_position(float(pos.net_shares)):
                    exposure_class = str(
                        self._exposure_class_by_token.get(str(token_id), EXPOSURE_CLASS_MEANINGFUL)
                    ).strip().upper() or EXPOSURE_CLASS_MEANINGFUL
                    skipped_nonflat_by_class[exposure_class] = (
                        int(skipped_nonflat_by_class.get(exposure_class, 0)) + 1
                    )
                    continue
                pnl = realized_cashflow
                pnl_by_token[token_id] = pnl
                total += pnl
                continue
            # Cash-flow convention:
            # - buys decrease cash (negative)
            # - sells increase cash (positive)
            # PnL = realized cashflow + mark of current inventory.
            pnl = realized_cashflow + (float(pos.net_shares) * float(mid))
            pnl_by_token[token_id] = pnl
            total += pnl
        self._last_mark_to_market_skipped_nonflat_by_class = dict(skipped_nonflat_by_class)
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
