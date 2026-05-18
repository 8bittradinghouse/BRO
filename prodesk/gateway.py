from __future__ import annotations

from collections import deque
import json
import re
import threading
import time
import uuid
from typing import Any, Deque, Dict, List, Optional

from .common import first_non_none, parse_float, parse_ts, utc_iso
from .edge_truth_contract import is_taker_reason
from .models import (
    BookTop,
    FillEvent,
    LiveOrder,
    OrderIntent,
    decision_input_type_from_book_source,
)
from .secrets import SecretLoadError, load_auth_secrets


class GatewayError(RuntimeError):
    pass


class PostOnlyRejectError(GatewayError):
    pass


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_PAPER_TRADE_ID_RE = re.compile(r"^paper-trade-[0-9a-f]{12}-[1-9][0-9]*$")

_WALLET_MODE_TO_SIGNATURE_TYPE = {
    "eoa": 0,
    "poly_proxy": 1,
    "gnosis_safe": 2,
    "poly_1271": 3,
}
_SIGNATURE_TYPE_TO_WALLET_MODE = {value: key for key, value in _WALLET_MODE_TO_SIGNATURE_TYPE.items()}


def _normalize_private_key(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise GatewayError("private key is empty")
    if text.startswith("0x") or text.startswith("0X"):
        body = text[2:]
    else:
        body = text
    if len(body) != 64 or not _HEX_RE.fullmatch(body):
        raise GatewayError("private key must be 32-byte hex (64 chars, optional 0x prefix)")
    return "0x" + body.lower()


def _normalize_evm_address(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise GatewayError("funder address is empty")
    if not text.startswith(("0x", "0X")):
        raise GatewayError("funder address must start with 0x")
    body = text[2:]
    if len(body) != 40 or not _HEX_RE.fullmatch(body):
        raise GatewayError("funder address must be 20-byte hex (40 chars after 0x)")
    return "0x" + body.lower()


def _normalize_wallet_mode(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return "poly_proxy"
    if text not in _WALLET_MODE_TO_SIGNATURE_TYPE:
        raise GatewayError(
            "wallet_mode must be one of eoa|poly_proxy|gnosis_safe|poly_1271"
        )
    return text


def _resolve_signature_type(auth_cfg: Dict[str, Any]) -> tuple[str, int]:
    wallet_mode = _normalize_wallet_mode(auth_cfg.get("wallet_mode", "poly_proxy"))
    resolved = int(_WALLET_MODE_TO_SIGNATURE_TYPE[wallet_mode])
    compat_raw = auth_cfg.get("signature_type")
    if compat_raw is not None:
        compat = int(compat_raw)
        if compat != resolved:
            raise GatewayError(
                f"auth.signature_type={compat} conflicts with auth.wallet_mode={wallet_mode}"
            )
    return wallet_mode, resolved


def _poly_status_code(exc: BaseException) -> Optional[int]:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    resp = getattr(exc, "resp", None)
    if resp is not None:
        status_code = getattr(resp, "status_code", None)
        if isinstance(status_code, int):
            return status_code
    return None


def _poly_error_payload(exc: BaseException) -> Any:
    if hasattr(exc, "error_msg"):
        return getattr(exc, "error_msg")
    resp = getattr(exc, "resp", None)
    if resp is None:
        return None
    try:
        return resp.json()
    except Exception:
        return getattr(resp, "text", None)


def _extract_heartbeat_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("heartbeat_id", "heartbeatID"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    elif isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return ""
            return _extract_heartbeat_id(parsed)
    return ""


class BaseGateway:
    def place_order(self, intent: OrderIntent, client_order_id: str) -> LiveOrder:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def cancel_all(self) -> int:
        raise NotImplementedError

    def get_open_orders(self) -> List[LiveOrder]:
        raise NotImplementedError

    def poll_fills(self) -> List[FillEvent]:
        raise NotImplementedError

    def on_book(self, top: BookTop) -> None:
        # Paper gateways use this hook for fill simulation.
        return None

    def status(self) -> Dict[str, Any]:
        return {}

    def close(self) -> None:
        return None

    def seed_fill_cursor(self, last_fill_ts_utc: Optional[str]) -> None:
        return None

    def preview_visible_immediate_fill(self, *, top: Optional[BookTop], side: str) -> Optional[Dict[str, Any]]:
        return None


class PaperGateway(BaseGateway):
    def __init__(self, runtime_cfg: Optional[Dict[str, Any]] = None) -> None:
        cfg = runtime_cfg or {}
        self._seq = 0
        self._trade_seq = 0
        self._trade_session = uuid.uuid4().hex[:12]
        self._open_orders: Dict[str, LiveOrder] = {}
        self._fill_queue: List[FillEvent] = []
        self._latest_top_by_token: Dict[str, BookTop] = {}
        self._passive_touch_fill_enabled = bool(cfg.get("paper_passive_touch_fill_enabled", False))
        self._passive_touch_fill_ratio = max(0.0, min(1.0, float(cfg.get("paper_passive_touch_fill_ratio", 0.15))))
        self._passive_min_rest_sec = max(0.0, float(cfg.get("paper_passive_min_rest_sec", 1.0)))
        self._passive_min_fill_size = max(1e-9, float(cfg.get("paper_passive_min_fill_size", 0.01)))
        self._passive_near_touch_band = max(0.0, float(cfg.get("paper_passive_near_touch_band", 0.02)))
        self._passive_near_touch_fill_ratio = max(
            0.0, min(1.0, float(cfg.get("paper_passive_near_touch_fill_ratio", 0.08)))
        )
        self._paper_background_fill_ratio = max(0.0, min(1.0, float(cfg.get("paper_background_fill_ratio", 0.0))))
        self._paper_liquidity_tod_scaler_enabled = bool(cfg.get("paper_liquidity_tod_scaler_enabled", False))
        self._paper_liquidity_tod_start_hour_utc = int(float(cfg.get("paper_liquidity_tod_start_hour_utc", 2)))
        self._paper_liquidity_tod_end_hour_utc = int(float(cfg.get("paper_liquidity_tod_end_hour_utc", 6)))
        self._paper_liquidity_tod_depth_multiplier = max(
            0.0,
            float(cfg.get("paper_liquidity_tod_depth_multiplier", 1.0)),
        )
        queue_mode = str(cfg.get("paper_queue_position_mode", "not_modeled")).strip().lower()
        self._paper_queue_position_mode = "not_modeled" if queue_mode != "not_modeled" else queue_mode
        self._paper_queue_position_ahead_ratio = max(
            0.0,
            min(1.0, float(cfg.get("paper_queue_position_ahead_ratio", 0.0))),
        )
        self._paper_chainlink_lag_emulation_enabled = bool(
            cfg.get("paper_chainlink_lag_emulation_enabled", False)
        )
        self._paper_chainlink_lag_window_low_sec = max(
            0.0,
            float(cfg.get("paper_chainlink_lag_window_low_sec", 2.0)),
        )
        self._paper_chainlink_lag_window_high_sec = max(
            self._paper_chainlink_lag_window_low_sec,
            float(cfg.get("paper_chainlink_lag_window_high_sec", 15.0)),
        )
        self._paper_chainlink_lag_penalty_bps_below_window = max(
            0.0,
            float(cfg.get("paper_chainlink_lag_penalty_bps_below_window", 0.0)),
        )
        self._paper_chainlink_lag_penalty_bps_within_window = max(
            0.0,
            float(cfg.get("paper_chainlink_lag_penalty_bps_within_window", 0.0)),
        )
        self._paper_chainlink_lag_penalty_bps_above_window = max(
            0.0,
            float(cfg.get("paper_chainlink_lag_penalty_bps_above_window", 0.0)),
        )

    @staticmethod
    def _hour_in_window(*, hour_utc: int, start_hour: int, end_hour: int) -> bool:
        start = max(0, min(23, int(start_hour)))
        end = max(0, min(23, int(end_hour)))
        if start == end:
            return True
        if start < end:
            return start <= hour_utc < end
        return hour_utc >= start or hour_utc < end

    def _paper_liquidity_depth_scale(self, top: BookTop) -> float:
        if not self._paper_liquidity_tod_scaler_enabled:
            return 1.0
        ts = parse_ts(top.ts_utc)
        if ts is None:
            return 1.0
        if self._hour_in_window(
            hour_utc=int(ts.hour),
            start_hour=self._paper_liquidity_tod_start_hour_utc,
            end_hour=self._paper_liquidity_tod_end_hour_utc,
        ):
            return float(self._paper_liquidity_tod_depth_multiplier)
        return 1.0

    def _paper_queue_fill_multiplier(self) -> float:
        return 1.0

    def _maker_depth_fill_plan(self, *, side_liq: float, remaining: float) -> Dict[str, float]:
        queue_mult = self._paper_queue_fill_multiplier()
        eligible_depth = max(0.0, float(side_liq) * float(queue_mult))
        fill_size = max(0.0, min(float(remaining), eligible_depth))
        depth_ratio = (fill_size / float(side_liq)) if float(side_liq) > 0.0 else 0.0
        return {
            "queue_fill_multiplier": float(queue_mult),
            "eligible_depth": float(eligible_depth),
            "fill_size": float(fill_size),
            "depth_consumption_ratio": float(depth_ratio),
        }

    def _classify_chainlink_lag(self, *, intent: OrderIntent) -> tuple[str, Optional[float]]:
        token_median_lag_ms = (
            float(intent.token_median_lag_ms)
            if isinstance(intent.token_median_lag_ms, (int, float))
            else None
        )
        oracle_tick_age_sec = (
            float(intent.oracle_tick_age_sec)
            if isinstance(intent.oracle_tick_age_sec, (int, float))
            else None
        )
        if (
            token_median_lag_ms is None
            or token_median_lag_ms < 0.0
            or oracle_tick_age_sec is None
            or oracle_tick_age_sec < 0.0
        ):
            return "unknown", None
        effective_lag_sec = max(token_median_lag_ms / 1000.0, oracle_tick_age_sec)
        if effective_lag_sec < float(self._paper_chainlink_lag_window_low_sec):
            return "below_window", float(effective_lag_sec)
        if effective_lag_sec > float(self._paper_chainlink_lag_window_high_sec):
            return "above_window", float(effective_lag_sec)
        return "within_window", float(effective_lag_sec)

    def _chainlink_lag_penalty_bps(self, *, lag_class: str) -> float:
        if not self._paper_chainlink_lag_emulation_enabled:
            return 0.0
        normalized = str(lag_class or "").strip().lower()
        if normalized == "below_window":
            return float(self._paper_chainlink_lag_penalty_bps_below_window)
        if normalized == "within_window":
            return float(self._paper_chainlink_lag_penalty_bps_within_window)
        if normalized == "above_window":
            return float(self._paper_chainlink_lag_penalty_bps_above_window)
        # Fail closed for unknown lag class: do not infer penalty.
        return 0.0

    @staticmethod
    def _decision_input_type_from_book_source(source: Any) -> str:
        return decision_input_type_from_book_source(source)

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"paper-order-{self._seq}"

    def _next_trade_id(self) -> str:
        self._trade_seq += 1
        trade_id = f"paper-trade-{self._trade_session}-{self._trade_seq}"
        if not _PAPER_TRADE_ID_RE.fullmatch(trade_id):
            raise GatewayError(f"invalid_paper_trade_id_generated:{trade_id}")
        return trade_id

    def preview_visible_immediate_fill(self, *, top: Optional[BookTop], side: str) -> Optional[Dict[str, Any]]:
        if top is None:
            return None
        side_norm = str(side or "").strip().upper()
        if side_norm not in {"BUY", "SELL"}:
            return None
        liquidity_depth_multiplier = self._paper_liquidity_depth_scale(top)
        if side_norm == "BUY":
            visible_size = (
                (float(top.best_ask_size) if top.best_ask_size is not None else 0.0)
                * float(liquidity_depth_multiplier)
            )
            touch_price = float(top.best_ask_price) if top.best_ask_price is not None else None
        else:
            visible_size = (
                (float(top.best_bid_size) if top.best_bid_size is not None else 0.0)
                * float(liquidity_depth_multiplier)
            )
            touch_price = float(top.best_bid_price) if top.best_bid_price is not None else None
        visible_notional_usd = (
            float(visible_size) * float(touch_price)
            if touch_price is not None and visible_size > 0.0
            else 0.0
        )
        return {
            "available": bool(touch_price is not None),
            "side": side_norm,
            "touch_price": touch_price,
            "visible_size": float(max(0.0, visible_size)),
            "visible_notional_usd": float(max(0.0, visible_notional_usd)),
            "paper_liquidity_depth_multiplier": float(liquidity_depth_multiplier),
            "fill_policy_basis": "visible_liquidity_top_of_book",
        }

    def place_order(self, intent: OrderIntent, client_order_id: str) -> LiveOrder:
        tif = str(intent.tif or "GTC").upper()
        post_only = True if intent.post_only is None else bool(intent.post_only)
        if tif in {"IOC", "FOK"} or post_only is False:
            return self._place_immediate_order(intent, client_order_id=client_order_id)

        top = self._latest_top_by_token.get(intent.token_id)
        if top is not None and self._would_cross_touch(intent=intent, top=top):
            raise PostOnlyRejectError("paper_post_only_would_cross")

        order_id = self._next_order_id()
        order = LiveOrder(
            order_id=order_id,
            token_id=intent.token_id,
            side=intent.side,
            price=float(intent.price),
            size=float(intent.size),
            remaining_size=float(intent.size),
            status="OPEN",
            client_order_id=client_order_id,
            created_ts_utc=utc_iso(),
            submission_lane=(
                str(intent.submission_lane).strip().lower()
                if str(intent.submission_lane or "").strip()
                else None
            ),
            commitment_hold_active=bool(intent.commitment_hold_active),
            commitment_hold_reason=(
                str(intent.commitment_hold_reason).strip()
                if str(intent.commitment_hold_reason or "").strip()
                else None
            ),
            commitment_expiry_ts_utc=(
                str(intent.commitment_expiry_ts_utc).strip()
                if str(intent.commitment_expiry_ts_utc or "").strip()
                else None
            ),
        )
        self._open_orders[order_id] = order
        return order

    @staticmethod
    def _would_cross_touch(*, intent: OrderIntent, top: BookTop) -> bool:
        if intent.side == "BUY" and top.best_ask_price is not None:
            return float(intent.price) >= float(top.best_ask_price)
        if intent.side == "SELL" and top.best_bid_price is not None:
            return float(intent.price) <= float(top.best_bid_price)
        return False

    def _place_immediate_order(self, intent: OrderIntent, client_order_id: str) -> LiveOrder:
        order_id = self._next_order_id()
        remaining = float(intent.size)
        status = "CANCELED"
        top = self._latest_top_by_token.get(intent.token_id)
        liquidity_depth_multiplier = self._paper_liquidity_depth_scale(top) if top is not None else 1.0
        is_taker_lane = is_taker_reason(intent.reason)
        lag_class: Optional[str] = None
        lag_sec_effective: Optional[float] = None
        lag_penalty_bps = 0.0
        if is_taker_lane:
            lag_class, lag_sec_effective = self._classify_chainlink_lag(intent=intent)
            if lag_class == "unknown":
                lag_penalty_bps = 0.0
                lag_sec_effective = None
            else:
                lag_penalty_bps = self._chainlink_lag_penalty_bps(lag_class=lag_class)
        if top is not None:
            if intent.side == "BUY" and top.best_ask_price is not None and intent.price >= top.best_ask_price:
                ask_liq = (
                    (float(top.best_ask_size) if top.best_ask_size is not None else 0.0)
                    * float(liquidity_depth_multiplier)
                )
                fill_size = max(0.0, min(remaining, ask_liq))
                if fill_size > 0:
                    remaining -= fill_size
                    fill_price = float(top.best_ask_price)
                    if is_taker_lane and lag_penalty_bps > 0.0:
                        fill_price *= 1.0 + (lag_penalty_bps / 10000.0)
                    self._fill_queue.append(
                        FillEvent(
                            trade_id=self._next_trade_id(),
                            token_id=intent.token_id,
                            side="BUY",
                            price=float(fill_price),
                            size=fill_size,
                            ts_utc=utc_iso(),
                            order_id=order_id,
                            source="paper",
                            fill_policy_basis="visible_liquidity_top_of_book",
                            execution_realism_class="not_modeled",
                            decision_input_type=self._decision_input_type_from_book_source(top.source),
                            paper_liquidity_depth_multiplier=float(liquidity_depth_multiplier),
                            paper_queue_position_mode="not_applicable",
                            paper_queue_fill_multiplier=1.0,
                            paper_chainlink_lag_class=lag_class,
                            paper_chainlink_lag_sec_effective=lag_sec_effective,
                            paper_chainlink_lag_penalty_bps=float(lag_penalty_bps),
                        )
                    )
            elif intent.side == "SELL" and top.best_bid_price is not None and intent.price <= top.best_bid_price:
                bid_liq = (
                    (float(top.best_bid_size) if top.best_bid_size is not None else 0.0)
                    * float(liquidity_depth_multiplier)
                )
                fill_size = max(0.0, min(remaining, bid_liq))
                if fill_size > 0:
                    remaining -= fill_size
                    fill_price = float(top.best_bid_price)
                    if is_taker_lane and lag_penalty_bps > 0.0:
                        fill_price *= 1.0 - (lag_penalty_bps / 10000.0)
                    self._fill_queue.append(
                        FillEvent(
                            trade_id=self._next_trade_id(),
                            token_id=intent.token_id,
                            side="SELL",
                            price=float(fill_price),
                            size=fill_size,
                            ts_utc=utc_iso(),
                            order_id=order_id,
                            source="paper",
                            fill_policy_basis="visible_liquidity_top_of_book",
                            execution_realism_class="not_modeled",
                            decision_input_type=self._decision_input_type_from_book_source(top.source),
                            paper_liquidity_depth_multiplier=float(liquidity_depth_multiplier),
                            paper_queue_position_mode="not_applicable",
                            paper_queue_fill_multiplier=1.0,
                            paper_chainlink_lag_class=lag_class,
                            paper_chainlink_lag_sec_effective=lag_sec_effective,
                            paper_chainlink_lag_penalty_bps=float(lag_penalty_bps),
                        )
                    )

        if remaining <= 1e-9:
            remaining = 0.0
            status = "FILLED"
        elif remaining < float(intent.size):
            status = "PARTIAL"

        return LiveOrder(
            order_id=order_id,
            token_id=intent.token_id,
            side=intent.side,
            price=float(intent.price),
            size=float(intent.size),
            remaining_size=remaining,
            status=status,
            client_order_id=client_order_id,
            created_ts_utc=utc_iso(),
            submission_lane=(
                str(intent.submission_lane).strip().lower()
                if str(intent.submission_lane or "").strip()
                else None
            ),
            commitment_hold_active=bool(intent.commitment_hold_active),
            commitment_hold_reason=(
                str(intent.commitment_hold_reason).strip()
                if str(intent.commitment_hold_reason or "").strip()
                else None
            ),
            commitment_expiry_ts_utc=(
                str(intent.commitment_expiry_ts_utc).strip()
                if str(intent.commitment_expiry_ts_utc or "").strip()
                else None
            ),
        )

    def cancel_order(self, order_id: str) -> bool:
        order = self._open_orders.get(order_id)
        if order is None:
            return False
        order.status = "CANCELED"
        del self._open_orders[order_id]
        return True

    def cancel_all(self) -> int:
        ids = list(self._open_orders.keys())
        for order_id in ids:
            self.cancel_order(order_id)
        return len(ids)

    def get_open_orders(self) -> List[LiveOrder]:
        return list(self._open_orders.values())

    def poll_fills(self) -> List[FillEvent]:
        fills = list(self._fill_queue)
        self._fill_queue.clear()
        return fills

    def on_book(self, top: BookTop) -> None:
        self._latest_top_by_token[top.token_id] = top
        liquidity_depth_multiplier = self._paper_liquidity_depth_scale(top)
        ask_liq = (
            (float(top.best_ask_size) if top.best_ask_size is not None else 0.0)
            * float(liquidity_depth_multiplier)
        )
        bid_liq = (
            (float(top.best_bid_size) if top.best_bid_size is not None else 0.0)
            * float(liquidity_depth_multiplier)
        )
        queue_fill_multiplier = self._paper_queue_fill_multiplier()
        now_ts = parse_ts(utc_iso())

        to_remove: List[str] = []
        for order_id, order in list(self._open_orders.items()):
            if order.token_id != top.token_id:
                continue
            crossed = False
            fill_price: Optional[float] = None
            touched = False
            near_touched = False
            background_touched = False
            near_touch_factor = 0.0
            if order.side == "BUY" and top.best_ask_price is not None and order.price >= top.best_ask_price:
                crossed = True
                fill_price = top.best_ask_price
            if order.side == "SELL" and top.best_bid_price is not None and order.price <= top.best_bid_price:
                crossed = True
                fill_price = top.best_bid_price
            if not crossed and self._passive_touch_fill_enabled:
                created_ts = parse_ts(order.created_ts_utc)
                rest_sec = None
                if created_ts is not None and now_ts is not None:
                    rest_sec = max(0.0, (now_ts - created_ts).total_seconds())
                if rest_sec is None or rest_sec >= self._passive_min_rest_sec:
                    if order.side == "BUY" and top.best_bid_price is not None and order.price >= top.best_bid_price:
                        touched = True
                        fill_price = min(order.price, float(top.best_bid_price))
                    elif order.side == "SELL" and top.best_ask_price is not None and order.price <= top.best_ask_price:
                        touched = True
                        fill_price = max(order.price, float(top.best_ask_price))
                    elif self._passive_near_touch_band > 0:
                        if order.side == "BUY" and top.best_bid_price is not None and order.price < top.best_bid_price:
                            distance = float(top.best_bid_price) - float(order.price)
                            if distance <= self._passive_near_touch_band:
                                near_touched = True
                                near_touch_factor = max(0.0, 1.0 - (distance / self._passive_near_touch_band))
                                fill_price = float(order.price)
                        elif order.side == "SELL" and top.best_ask_price is not None and order.price > top.best_ask_price:
                            distance = float(order.price) - float(top.best_ask_price)
                            if distance <= self._passive_near_touch_band:
                                near_touched = True
                                near_touch_factor = max(0.0, 1.0 - (distance / self._passive_near_touch_band))
                                fill_price = float(order.price)
            if (
                not crossed
                and not touched
                and not near_touched
                and self._paper_background_fill_ratio > 0
                and self._passive_touch_fill_enabled
            ):
                background_touched = True
                fill_price = float(order.price)
            if not crossed and not touched and not near_touched and not background_touched:
                continue
            if fill_price is None:
                continue

            consume_bid_liquidity = False
            order_queue_fill_multiplier = float(queue_fill_multiplier)
            maker_depth_consumption_ratio: Optional[float] = None
            maker_eligible_depth: Optional[float] = None
            if crossed and order.side == "BUY":
                depth_plan = self._maker_depth_fill_plan(side_liq=ask_liq, remaining=order.remaining_size)
                fill_size = float(depth_plan["fill_size"])
                order_queue_fill_multiplier = float(depth_plan["queue_fill_multiplier"])
                maker_eligible_depth = float(depth_plan["eligible_depth"])
                maker_depth_consumption_ratio = float(depth_plan["depth_consumption_ratio"])
            elif crossed and order.side == "SELL":
                depth_plan = self._maker_depth_fill_plan(side_liq=bid_liq, remaining=order.remaining_size)
                fill_size = float(depth_plan["fill_size"])
                order_queue_fill_multiplier = float(depth_plan["queue_fill_multiplier"])
                maker_eligible_depth = float(depth_plan["eligible_depth"])
                maker_depth_consumption_ratio = float(depth_plan["depth_consumption_ratio"])
                consume_bid_liquidity = True
            elif touched and order.side == "BUY":
                candidate = bid_liq * self._passive_touch_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                consume_bid_liquidity = True
            elif touched:
                candidate = ask_liq * self._passive_touch_fill_ratio
                fill_size = min(order.remaining_size, candidate)
            elif near_touched and order.side == "BUY":
                candidate = bid_liq * self._passive_near_touch_fill_ratio * near_touch_factor
                fill_size = min(order.remaining_size, candidate)
                consume_bid_liquidity = True
            elif near_touched:
                candidate = ask_liq * self._passive_near_touch_fill_ratio * near_touch_factor
                fill_size = min(order.remaining_size, candidate)
            elif background_touched and order.side == "BUY":
                candidate = bid_liq * self._paper_background_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                consume_bid_liquidity = True
            else:
                candidate = ask_liq * self._paper_background_fill_ratio
                fill_size = min(order.remaining_size, candidate)
            if fill_size < self._passive_min_fill_size and touched:
                continue
            if fill_size < self._passive_min_fill_size and near_touched:
                continue
            if fill_size < self._passive_min_fill_size and background_touched:
                continue
            if fill_size <= 0:
                continue
            if consume_bid_liquidity:
                bid_liq = max(0.0, bid_liq - fill_size)
            else:
                ask_liq = max(0.0, ask_liq - fill_size)

            fill_policy_basis = "visible_liquidity_top_of_book"
            execution_realism_class = "not_modeled"
            if touched:
                fill_policy_basis = "synthetic_touch_fill"
                execution_realism_class = "not_modeled"
            elif near_touched:
                fill_policy_basis = "synthetic_near_touch_fill"
                execution_realism_class = "not_modeled"
            elif background_touched:
                fill_policy_basis = "synthetic_background_fill"
                execution_realism_class = "not_modeled"

            order.remaining_size -= fill_size
            if order.remaining_size <= 1e-9:
                order.remaining_size = 0.0
                order.status = "FILLED"
                to_remove.append(order_id)
            else:
                order.status = "PARTIAL"

            self._fill_queue.append(
                FillEvent(
                    trade_id=self._next_trade_id(),
                    token_id=order.token_id,
                    side=order.side,
                    price=fill_price,
                    size=fill_size,
                    ts_utc=utc_iso(),
                    order_id=order.order_id,
                    source="paper",
                    fill_policy_basis=fill_policy_basis,
                    execution_realism_class=execution_realism_class,
                    decision_input_type=self._decision_input_type_from_book_source(top.source),
                    paper_liquidity_depth_multiplier=float(liquidity_depth_multiplier),
                    paper_queue_position_mode=str(self._paper_queue_position_mode),
                    paper_queue_fill_multiplier=(
                        float(order_queue_fill_multiplier)
                        if self._paper_queue_position_mode != "not_modeled"
                        else 1.0
                    ),
                    paper_maker_depth_consumption_ratio=maker_depth_consumption_ratio,
                    paper_maker_eligible_depth=maker_eligible_depth,
                )
            )

        for order_id in to_remove:
            self._open_orders.pop(order_id, None)


def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "orders", "trades", "items", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _normalize_order_side(side_raw: Any) -> Optional[str]:
    if side_raw is None:
        return None
    text = str(side_raw).strip().upper()
    if text in {"BUY", "BID", "B"}:
        return "BUY"
    if text in {"SELL", "ASK", "S"}:
        return "SELL"
    return None


class LiveClobGateway(BaseGateway):
    def __init__(self, auth_cfg: Dict[str, Any], seen_trade_ids_max: int = 200000):
        self._seen_trade_ids: set[str] = set()
        self._seen_trade_ids_queue: Deque[str] = deque()
        self._seen_trade_ids_max = max(1, int(seen_trade_ids_max))
        self._last_trade_ts_epoch: Optional[float] = None
        self._enforce_post_only = bool(auth_cfg.get("enforce_post_only", True))
        self._allow_taker = bool(auth_cfg.get("allow_taker", False))
        self._open_orders_cache_ttl_sec = max(0.0, float(auth_cfg.get("open_orders_cache_ttl_sec", 0.25)))
        self._open_orders_cache_expires_mono = 0.0
        self._open_orders_cache: Optional[List[LiveOrder]] = None
        self._market_info_cache_ttl_sec = max(0.0, float(auth_cfg.get("market_info_cache_ttl_sec", 30.0)))
        self._market_info_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._market_info_cache_lock = threading.Lock()
        self._matching_engine_retry_attempts = max(1, int(auth_cfg.get("matching_engine_retry_attempts", 4)))
        self._matching_engine_retry_initial_sec = max(
            0.1, float(auth_cfg.get("matching_engine_retry_initial_sec", 0.5))
        )
        self._matching_engine_retry_max_sec = max(
            self._matching_engine_retry_initial_sec,
            float(auth_cfg.get("matching_engine_retry_max_sec", 4.0)),
        )
        self._matching_engine_last_status = "unknown"
        self._matching_engine_last_error: Optional[str] = None
        self._matching_engine_restart_windows = 0
        self._matching_engine_last_restart_monotonic: Optional[float] = None
        self._heartbeat_enabled = bool(auth_cfg.get("heartbeat_enabled", True))
        self._heartbeat_interval_sec = max(1.0, float(auth_cfg.get("heartbeat_interval_sec", 8.0)))
        self._heartbeat_retry_sec = max(0.25, float(auth_cfg.get("heartbeat_retry_sec", 2.0)))
        self._heartbeat_id = ""
        self._heartbeat_last_success_monotonic: Optional[float] = None
        self._heartbeat_last_error: Optional[str] = None
        self._heartbeat_failures = 0
        self._resting_orders_present = False
        self._heartbeat_lock = threading.Lock()
        self._heartbeat_stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None

        try:
            from py_builder_relayer_client.client import RelayClient
            from py_clob_client_v2 import ClobClient, SignatureTypeV2
            from py_clob_client_v2.clob_types import (
                AssetType,
                BalanceAllowanceParams,
                OpenOrderParams,
                OrderArgsV2,
                OrderPayload,
                OrderType,
                TradeParams,
            )
            from py_clob_client_v2.exceptions import PolyApiException
        except ImportError as exc:
            raise GatewayError(
                "py-clob-client-v2 and py-builder-relayer-client are required for live mode."
            ) from exc
        self._PolyApiException = PolyApiException

        private_key_env = str(auth_cfg["private_key_env"])
        funder_env = str(auth_cfg["funder_env"])
        wallet_mode, signature_type = _resolve_signature_type(auth_cfg)
        requires_funder = wallet_mode != "eoa"
        try:
            private_key, funder, source_meta = load_auth_secrets(auth_cfg, require_funder=requires_funder)
        except SecretLoadError as exc:
            raise GatewayError(str(exc)) from exc
        try:
            private_key = _normalize_private_key(private_key)
        except GatewayError as exc:
            src = str(source_meta.get("private_key_source", private_key_env))
            raise GatewayError(f"invalid private key from {src}: {exc}") from exc
        funder_address: Optional[str] = None
        if requires_funder:
            try:
                funder_address = _normalize_evm_address(funder)
            except GatewayError as exc:
                src = str(source_meta.get("funder_source", funder_env))
                raise GatewayError(f"invalid funder address from {src}: {exc}") from exc

        self._OrderArgsV2 = OrderArgsV2
        self._OrderType = OrderType
        self._OpenOrderParams = OpenOrderParams
        self._AssetType = AssetType
        self._BalanceAllowanceParams = BalanceAllowanceParams
        self._TradeParams = TradeParams
        self._OrderPayload = OrderPayload
        self._SignatureTypeV2 = SignatureTypeV2
        self._RelayClient = RelayClient

        host = str(auth_cfg["host"])
        chain_id = int(auth_cfg["chain_id"])
        self._wallet_mode = wallet_mode
        self._wallet_address = funder_address or ""
        self._chain_id = chain_id
        self._host = host

        self.client = ClobClient(
            host=host,
            key=private_key,
            chain_id=chain_id,
            signature_type=signature_type,
            funder=funder_address,
            retry_on_error=False,
        )
        api_creds = self.client.create_or_derive_api_key()
        self.client.set_api_creds(api_creds)
        self._relayer_client = self._build_relayer_client(
            auth_cfg=auth_cfg,
            private_key=private_key,
            wallet_mode=wallet_mode,
        )
        if self._heartbeat_enabled:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="live-clob-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def wallet_address(self) -> str:
        return str(self._wallet_address)

    def chain_id(self) -> int:
        return int(self._chain_id)

    def host(self) -> str:
        return str(self._host)

    def get_collateral_balance_allowance(self) -> Dict[str, Any]:
        params = self._BalanceAllowanceParams(asset_type=self._AssetType.COLLATERAL, signature_type=-1)
        payload = self._call_with_retry("get_balance_allowance", self.client.get_balance_allowance, params)
        if not isinstance(payload, dict):
            raise GatewayError(f"unexpected balance/allowance payload type: {type(payload).__name__}")
        return payload

    def _invalidate_open_orders_cache(self) -> None:
        self._open_orders_cache = None
        self._open_orders_cache_expires_mono = 0.0

    def seed_fill_cursor(self, last_fill_ts_utc: Optional[str]) -> None:
        ts = parse_ts(last_fill_ts_utc)
        if ts is None:
            return
        epoch = ts.timestamp()
        if self._last_trade_ts_epoch is None:
            self._last_trade_ts_epoch = epoch
            return
        self._last_trade_ts_epoch = max(self._last_trade_ts_epoch, epoch)

    def _remember_trade_id(self, trade_id: str) -> None:
        self._seen_trade_ids.add(trade_id)
        self._seen_trade_ids_queue.append(trade_id)
        while len(self._seen_trade_ids_queue) > self._seen_trade_ids_max:
            old = self._seen_trade_ids_queue.popleft()
            self._seen_trade_ids.discard(old)

    def place_order(self, intent: OrderIntent, client_order_id: str) -> LiveOrder:
        side = str(intent.side or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            raise GatewayError(f"unsupported order side: {intent.side!r}")
        order_args = self._OrderArgsV2(
            token_id=intent.token_id,
            price=float(intent.price),
            size=float(intent.size),
            side=side,
        )
        signed_order = self.client.create_order(order_args)

        tif = intent.tif.upper()
        post_only = self._enforce_post_only if intent.post_only is None else bool(intent.post_only)
        if self._enforce_post_only and not post_only and not self._allow_taker:
            raise GatewayError("post-only enforcement active; set auth.allow_taker=true to allow taker overrides")
        if post_only and tif not in {"GTC", "GTD"}:
            raise GatewayError(f"post-only requires GTC or GTD tif, got {tif!r}")
        order_type = self._OrderType.GTC
        if hasattr(self._OrderType, tif):
            order_type = getattr(self._OrderType, tif)

        try:
            response = self._call_with_retry(
                "post_order",
                self.client.post_order,
                signed_order,
                order_type,
                post_only=post_only,
            )
        except TypeError as exc:
            if post_only:
                raise GatewayError(
                    "py-clob-client-v2 post_order post_only contract mismatch"
                ) from exc
            response = self._call_with_retry("post_order", self.client.post_order, signed_order, order_type)
        if not isinstance(response, dict):
            raise GatewayError(f"unexpected post_order response type: {type(response).__name__}")
        status = str(first_non_none(response.get("status"), response.get("state"), "")).strip().lower()
        err_text = str(first_non_none(response.get("error"), response.get("message"), response.get("msg"), "")).strip()
        if status in {"rejected", "failed", "error"}:
            detail = err_text or status
            if self._enforce_post_only:
                raise PostOnlyRejectError(detail)
            raise GatewayError(detail)
        order_id = first_non_none(response.get("orderID"), response.get("order_id"), response.get("id"))
        if not isinstance(order_id, str) or not order_id:
            if self._enforce_post_only and err_text:
                raise PostOnlyRejectError(err_text)
            raise GatewayError(f"missing order id in response: {response}")

        self._invalidate_open_orders_cache()
        if tif in {"GTC", "GTD"}:
            self._set_resting_orders_present(True)
        return LiveOrder(
            order_id=order_id,
            token_id=intent.token_id,
            side=intent.side,
            price=float(intent.price),
            size=float(intent.size),
            remaining_size=float(intent.size),
            status=str(first_non_none(response.get("status"), "OPEN")),
            client_order_id=client_order_id,
            created_ts_utc=utc_iso(),
            submission_lane=(
                str(intent.submission_lane).strip().lower()
                if str(intent.submission_lane or "").strip()
                else None
            ),
            commitment_hold_active=bool(intent.commitment_hold_active),
            commitment_hold_reason=(
                str(intent.commitment_hold_reason).strip()
                if str(intent.commitment_hold_reason or "").strip()
                else None
            ),
            commitment_expiry_ts_utc=(
                str(intent.commitment_expiry_ts_utc).strip()
                if str(intent.commitment_expiry_ts_utc or "").strip()
                else None
            ),
        )

    def cancel_order(self, order_id: str) -> bool:
        if hasattr(self.client, "cancel_order"):
            response = self._call_with_retry(
                "cancel_order",
                self.client.cancel_order,
                self._OrderPayload(orderID=order_id),
            )
        else:  # pragma: no cover - compatibility with older fakes only
            response = self._call_with_retry("cancel_order", self.client.cancel, order_id)
        self._invalidate_open_orders_cache()
        if isinstance(response, dict):
            canceled = first_non_none(response.get("canceled"), response.get("cancelled"), response.get("success"))
            if isinstance(canceled, bool):
                if canceled:
                    self._refresh_resting_orders_presence()
                return canceled
            state = str(first_non_none(response.get("status"), response.get("state"), "")).strip().lower()
            if state in {"canceled", "cancelled", "ok", "success"}:
                self._refresh_resting_orders_presence()
                return True
            if state in {"not_found", "missing", "failed", "error", "rejected"}:
                return False
            detail = str(first_non_none(response.get("error"), response.get("message"), response.get("msg"), "")).strip()
            if detail:
                if "not found" in detail.lower() or "unknown order" in detail.lower():
                    return False
                raise GatewayError(f"cancel_order_unconfirmed:{detail}")
            raise GatewayError(f"cancel_order_unconfirmed_response:{response!r}")
        raise GatewayError(f"cancel_order_unconfirmed_response_type:{type(response).__name__}")

    def cancel_all(self) -> int:
        response = self._call_with_retry("cancel_all", self.client.cancel_all)
        self._invalidate_open_orders_cache()
        self._set_resting_orders_present(False)
        if isinstance(response, dict):
            count = first_non_none(response.get("count"), response.get("canceled_count"))
            if isinstance(count, int):
                return count
        if isinstance(response, list):
            return len(response)
        return 0

    def get_open_orders(self) -> List[LiveOrder]:
        ttl_sec = max(0.0, float(getattr(self, "_open_orders_cache_ttl_sec", 0.0)))
        if ttl_sec > 0:
            cache = getattr(self, "_open_orders_cache", None)
            expires = float(getattr(self, "_open_orders_cache_expires_mono", 0.0))
            now = time.monotonic()
            if cache is not None and now < expires:
                return list(cache)
        try:
            open_order_params = getattr(self, "_OpenOrderParams", lambda: None)
            if hasattr(self.client, "get_open_orders"):
                raw = self._call_with_retry("get_open_orders", self.client.get_open_orders, open_order_params())
            else:  # pragma: no cover - compatibility with older fakes only
                raw = self._call_with_retry("get_open_orders", self.client.get_orders, open_order_params())
        except TypeError:
            if hasattr(self.client, "get_open_orders"):
                raw = self._call_with_retry("get_open_orders", self.client.get_open_orders)
            else:  # pragma: no cover - compatibility with older fakes only
                raw = self._call_with_retry("get_open_orders", self.client.get_orders)
        rows = _extract_rows(raw)
        out: List[LiveOrder] = []
        for row in rows:
            order_id = first_non_none(row.get("id"), row.get("order_id"), row.get("orderID"))
            token_id = first_non_none(row.get("asset_id"), row.get("token_id"))
            side_raw = first_non_none(row.get("side"), row.get("type"), row.get("order_side"))
            side = _normalize_order_side(side_raw)

            if not isinstance(order_id, str) or not isinstance(token_id, str) or side is None:
                continue
            size = parse_float(first_non_none(row.get("size"), row.get("original_size"), row.get("amount"))) or 0.0
            remaining = parse_float(first_non_none(row.get("remaining"), row.get("remaining_size"))) or size
            price = parse_float(row.get("price")) or 0.0
            status = str(first_non_none(row.get("status"), "OPEN"))
            status_norm = status.strip().lower()
            if status_norm in {"canceled", "cancelled", "filled", "rejected", "expired", "closed", "executed"}:
                continue
            created_dt = parse_ts(first_non_none(row.get("created_at"), row.get("createdAt"), row.get("timestamp"), row.get("ts"), row.get("time")))
            created_ts_utc = utc_iso(created_dt) if created_dt is not None else None
            out.append(
                LiveOrder(
                    order_id=order_id,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    remaining_size=remaining,
                    status=status,
                    client_order_id=str(first_non_none(row.get("client_order_id"), "")) or None,
                    created_ts_utc=created_ts_utc,
                )
            )
        if ttl_sec > 0:
            self._open_orders_cache = list(out)
            self._open_orders_cache_expires_mono = time.monotonic() + ttl_sec
        self._set_resting_orders_present(bool(out))
        return out

    def poll_fills(self) -> List[FillEvent]:
        if hasattr(self.client, "get_trades"):
            raw = self._call_with_retry("get_trades", self.client.get_trades)
        else:  # pragma: no cover - compatibility with older fakes only
            raw = []
        rows = _extract_rows(raw)
        fills: List[FillEvent] = []
        newest_trade_ts_epoch = self._last_trade_ts_epoch
        for row in rows:
            trade_id = first_non_none(row.get("id"), row.get("tradeID"), row.get("trade_id"))
            token_id = first_non_none(row.get("asset_id"), row.get("token_id"))
            if not isinstance(trade_id, str) or not trade_id:
                continue
            if not isinstance(token_id, str) or not token_id:
                continue
            if trade_id in self._seen_trade_ids:
                continue

            raw_ts = first_non_none(row.get("timestamp"), row.get("ts"), row.get("time"))
            parsed_ts = parse_ts(raw_ts)
            ts_epoch = parsed_ts.timestamp() if parsed_ts is not None else None
            # Ignore historical backfill rows once we have moved forward in time.
            if (
                ts_epoch is not None
                and self._last_trade_ts_epoch is not None
                and ts_epoch < self._last_trade_ts_epoch
            ):
                continue

            side_raw = first_non_none(row.get("side"), row.get("taker_side"), row.get("order_side"))
            side = _normalize_order_side(side_raw)
            if side is None:
                continue
            price = parse_float(first_non_none(row.get("price"), row.get("last_trade_price")))
            size = parse_float(first_non_none(row.get("size"), row.get("amount"), row.get("quantity")))
            if price is None or size is None:
                continue
            if size <= 0:
                continue
            ts = utc_iso(parsed_ts) if parsed_ts is not None else str(first_non_none(raw_ts, utc_iso()))
            fills.append(
                FillEvent(
                    trade_id=trade_id,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    ts_utc=ts,
                    order_id=str(first_non_none(row.get("order_id"), row.get("maker_order_id"), "")) or None,
                    source="live",
                )
            )
            self._remember_trade_id(trade_id)
            if ts_epoch is not None:
                newest_trade_ts_epoch = ts_epoch if newest_trade_ts_epoch is None else max(newest_trade_ts_epoch, ts_epoch)
        self._last_trade_ts_epoch = newest_trade_ts_epoch
        if fills:
            self._invalidate_open_orders_cache()
            self._refresh_resting_orders_presence()
        return fills

    def status(self) -> Dict[str, Any]:
        with self._heartbeat_lock:
            heartbeat_last_success = self._heartbeat_last_success_monotonic
            heartbeat_last_error = self._heartbeat_last_error
            heartbeat_id = self._heartbeat_id
            heartbeat_failures = self._heartbeat_failures
            resting_orders_present = self._resting_orders_present
        heartbeat_age_sec = (
            time.monotonic() - heartbeat_last_success
            if heartbeat_last_success is not None
            else None
        )
        restart_window_age_sec = (
            time.monotonic() - self._matching_engine_last_restart_monotonic
            if self._matching_engine_last_restart_monotonic is not None
            else None
        )
        return {
            "host": self._host,
            "wallet_mode": self._wallet_mode,
            "matching_engine_status": self._matching_engine_last_status,
            "matching_engine_last_error": self._matching_engine_last_error,
            "matching_engine_restart_windows": self._matching_engine_restart_windows,
            "matching_engine_restart_window_age_sec": restart_window_age_sec,
            "heartbeat_enabled": self._heartbeat_enabled,
            "heartbeat_required": resting_orders_present,
            "heartbeat_id": heartbeat_id or None,
            "heartbeat_last_success_age_sec": heartbeat_age_sec,
            "heartbeat_last_error": heartbeat_last_error,
            "heartbeat_failures": heartbeat_failures,
            "resting_orders_present": resting_orders_present,
        }

    def get_clob_market_info(self, condition_id: str) -> Dict[str, Any]:
        key = str(condition_id or "").strip()
        if not key:
            raise GatewayError("condition_id is required for get_clob_market_info")
        if self._market_info_cache_ttl_sec > 0.0:
            with self._market_info_cache_lock:
                cached = self._market_info_cache.get(key)
                if cached is not None:
                    expires_mono, payload = cached
                    if time.monotonic() < expires_mono:
                        return dict(payload)
        payload = self._call_with_retry("get_clob_market_info", self.client.get_clob_market_info, condition_id)
        if not isinstance(payload, dict):
            raise GatewayError(f"unexpected get_clob_market_info payload type: {type(payload).__name__}")
        if self._market_info_cache_ttl_sec > 0.0:
            with self._market_info_cache_lock:
                self._market_info_cache[key] = (
                    time.monotonic() + self._market_info_cache_ttl_sec,
                    dict(payload),
                )
        return dict(payload)

    def close(self) -> None:
        self._heartbeat_stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

    def _call_with_retry(self, op_name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        delay = max(0.1, float(getattr(self, "_matching_engine_retry_initial_sec", 0.5)))
        attempts = max(1, int(getattr(self, "_matching_engine_retry_attempts", 1)))
        delay_max = max(delay, float(getattr(self, "_matching_engine_retry_max_sec", delay)))
        poly_api_exc = getattr(self, "_PolyApiException", None)
        last_error: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                result = func(*args, **kwargs)
                self._matching_engine_last_status = "ok"
                self._matching_engine_last_error = None
                return result
            except Exception as exc:
                last_error = exc
                status_code = _poly_status_code(exc)
                if poly_api_exc is not None and isinstance(exc, poly_api_exc) and status_code == 425:
                    self._matching_engine_last_status = "restart_window"
                    self._matching_engine_last_restart_monotonic = time.monotonic()
                    restart_windows = int(getattr(self, "_matching_engine_restart_windows", 0)) + 1
                    self._matching_engine_restart_windows = restart_windows
                    self._matching_engine_last_error = str(exc)
                    if attempt < attempts:
                        time.sleep(delay)
                        delay = min(delay_max, delay * 2.0)
                        continue
                    raise GatewayError(f"{op_name}_restart_window_exhausted:{exc}") from exc
                self._matching_engine_last_status = "error"
                self._matching_engine_last_error = str(exc)
                raise GatewayError(f"{op_name}_failed:{exc}") from exc
        raise GatewayError(f"{op_name}_failed:{last_error}")

    def _build_relayer_client(
        self,
        *,
        auth_cfg: Dict[str, Any],
        private_key: str,
        wallet_mode: str,
    ) -> Any:
        if wallet_mode != "poly_1271":
            return None
        relayer_url = str(auth_cfg.get("relayer_url", "")).strip()
        if not relayer_url:
            return None
        try:
            return self._RelayClient(
                relayer_url=relayer_url,
                chain_id=self._chain_id,
                private_key=private_key,
            )
        except Exception as exc:  # pragma: no cover - defensive setup guard
            raise GatewayError(f"relayer_client_init_failed:{exc}") from exc

    def _set_resting_orders_present(self, value: bool) -> None:
        heartbeat_lock = getattr(self, "_heartbeat_lock", None)
        if heartbeat_lock is None:
            self._resting_orders_present = bool(value)
            return
        with heartbeat_lock:
            self._resting_orders_present = bool(value)

    def _refresh_resting_orders_presence(self) -> None:
        try:
            open_orders = self.get_open_orders()
        except GatewayError:
            return
        self._set_resting_orders_present(bool(open_orders))

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop_event.is_set():
            with self._heartbeat_lock:
                enabled = self._heartbeat_enabled
                resting_orders_present = self._resting_orders_present
                heartbeat_id = self._heartbeat_id
            wait_for = self._heartbeat_retry_sec
            if enabled and resting_orders_present:
                try:
                    response = self._post_heartbeat_once(heartbeat_id)
                    new_heartbeat_id = _extract_heartbeat_id(response)
                    with self._heartbeat_lock:
                        if new_heartbeat_id:
                            self._heartbeat_id = new_heartbeat_id
                        self._heartbeat_last_success_monotonic = time.monotonic()
                        self._heartbeat_last_error = None
                        self._heartbeat_failures = 0
                    wait_for = self._heartbeat_interval_sec
                except GatewayError as exc:
                    with self._heartbeat_lock:
                        self._heartbeat_last_error = str(exc)
                        self._heartbeat_failures += 1
                    wait_for = self._heartbeat_retry_sec
            self._heartbeat_stop_event.wait(wait_for)

    def _post_heartbeat_once(self, heartbeat_id: str) -> Dict[str, Any]:
        poly_api_exc = getattr(self, "_PolyApiException", None)
        delay = max(0.1, float(getattr(self, "_matching_engine_retry_initial_sec", 0.5)))
        delay_max = max(delay, float(getattr(self, "_matching_engine_retry_max_sec", delay)))
        attempts = max(1, int(getattr(self, "_matching_engine_retry_attempts", 1)))
        current_heartbeat_id = str(heartbeat_id or "")
        corrected_once = False
        last_error: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.post_heartbeat(current_heartbeat_id)
                self._matching_engine_last_status = "ok"
                self._matching_engine_last_error = None
                if not isinstance(response, dict):
                    raise GatewayError(f"post_heartbeat_unexpected_response_type:{type(response).__name__}")
                if not _extract_heartbeat_id(response) and current_heartbeat_id:
                    response = dict(response)
                    response["heartbeat_id"] = current_heartbeat_id
                return response
            except Exception as exc:
                last_error = exc
                status_code = _poly_status_code(exc)
                if poly_api_exc is not None and isinstance(exc, poly_api_exc) and status_code == 400 and not corrected_once:
                    corrected_heartbeat_id = _extract_heartbeat_id(_poly_error_payload(exc))
                    if corrected_heartbeat_id:
                        current_heartbeat_id = corrected_heartbeat_id
                        corrected_once = True
                        continue
                if poly_api_exc is not None and isinstance(exc, poly_api_exc) and status_code == 425:
                    self._matching_engine_last_status = "restart_window"
                    self._matching_engine_last_restart_monotonic = time.monotonic()
                    self._matching_engine_restart_windows = int(self._matching_engine_restart_windows) + 1
                    self._matching_engine_last_error = str(exc)
                    if attempt < attempts:
                        time.sleep(delay)
                        delay = min(delay_max, delay * 2.0)
                        continue
                    raise GatewayError(f"post_heartbeat_restart_window_exhausted:{exc}") from exc
                self._matching_engine_last_status = "error"
                self._matching_engine_last_error = str(exc)
                raise GatewayError(f"post_heartbeat_failed:{exc}") from exc
        raise GatewayError(f"post_heartbeat_failed:{last_error}")
