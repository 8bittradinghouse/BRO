from __future__ import annotations

from collections import deque
import re
import time
import uuid
from typing import Any, Deque, Dict, List, Optional

from .common import first_non_none, parse_float, parse_ts, utc_iso
from .models import BookTop, FillEvent, LiveOrder, OrderIntent
from .secrets import SecretLoadError, load_auth_secrets


class GatewayError(RuntimeError):
    pass


class PostOnlyRejectError(GatewayError):
    pass


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_PAPER_TRADE_ID_RE = re.compile(r"^paper-trade-[0-9a-f]{12}-[1-9][0-9]*$")


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

    def close(self) -> None:
        return None

    def seed_fill_cursor(self, last_fill_ts_utc: Optional[str]) -> None:
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

    @staticmethod
    def _decision_input_type_from_book_source(source: Any) -> str:
        normalized = str(source or "").strip().lower()
        if normalized in {"ws", "chainlink"}:
            return "observed_live"
        if normalized == "rest":
            return "bounded_derived"
        if normalized in {"paper", "simulated", "synthetic", "emulated"}:
            return "emulated"
        if normalized in {"replay", "replayed"}:
            return "replayed"
        return "unknown"

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"paper-order-{self._seq}"

    def _next_trade_id(self) -> str:
        self._trade_seq += 1
        trade_id = f"paper-trade-{self._trade_session}-{self._trade_seq}"
        if not _PAPER_TRADE_ID_RE.fullmatch(trade_id):
            raise GatewayError(f"invalid_paper_trade_id_generated:{trade_id}")
        return trade_id

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
        if top is not None:
            if intent.side == "BUY" and top.best_ask_price is not None and intent.price >= top.best_ask_price:
                ask_liq = float(top.best_ask_size) if top.best_ask_size is not None else 0.0
                fill_size = max(0.0, min(remaining, ask_liq))
                if fill_size > 0:
                    remaining -= fill_size
                    self._fill_queue.append(
                        FillEvent(
                            trade_id=self._next_trade_id(),
                            token_id=intent.token_id,
                            side="BUY",
                            price=float(top.best_ask_price),
                            size=fill_size,
                            ts_utc=utc_iso(),
                            order_id=order_id,
                            source="paper",
                            fill_policy_basis="bounded_visible_liquidity_top_of_book",
                            execution_realism_class="bounded_approximation",
                            decision_input_type=self._decision_input_type_from_book_source(top.source),
                        )
                    )
            elif intent.side == "SELL" and top.best_bid_price is not None and intent.price <= top.best_bid_price:
                bid_liq = float(top.best_bid_size) if top.best_bid_size is not None else 0.0
                fill_size = max(0.0, min(remaining, bid_liq))
                if fill_size > 0:
                    remaining -= fill_size
                    self._fill_queue.append(
                        FillEvent(
                            trade_id=self._next_trade_id(),
                            token_id=intent.token_id,
                            side="SELL",
                            price=float(top.best_bid_price),
                            size=fill_size,
                            ts_utc=utc_iso(),
                            order_id=order_id,
                            source="paper",
                            fill_policy_basis="bounded_visible_liquidity_top_of_book",
                            execution_realism_class="bounded_approximation",
                            decision_input_type=self._decision_input_type_from_book_source(top.source),
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
        ask_liq = float(top.best_ask_size) if top.best_ask_size is not None else 0.0
        bid_liq = float(top.best_bid_size) if top.best_bid_size is not None else 0.0
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

            if crossed and order.side == "BUY":
                fill_size = min(order.remaining_size, ask_liq)
                ask_liq -= fill_size
            elif crossed and order.side == "SELL":
                fill_size = min(order.remaining_size, bid_liq)
                bid_liq -= fill_size
            elif touched and order.side == "BUY":
                candidate = bid_liq * self._passive_touch_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                bid_liq -= fill_size
            elif touched:
                candidate = ask_liq * self._passive_touch_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                ask_liq -= fill_size
            elif near_touched and order.side == "BUY":
                candidate = bid_liq * self._passive_near_touch_fill_ratio * near_touch_factor
                fill_size = min(order.remaining_size, candidate)
                bid_liq -= fill_size
            elif near_touched:
                candidate = ask_liq * self._passive_near_touch_fill_ratio * near_touch_factor
                fill_size = min(order.remaining_size, candidate)
                ask_liq -= fill_size
            elif background_touched and order.side == "BUY":
                candidate = bid_liq * self._paper_background_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                bid_liq -= fill_size
            else:
                candidate = ask_liq * self._paper_background_fill_ratio
                fill_size = min(order.remaining_size, candidate)
                ask_liq -= fill_size
            if fill_size < self._passive_min_fill_size and touched:
                continue
            if fill_size < self._passive_min_fill_size and near_touched:
                continue
            if fill_size < self._passive_min_fill_size and background_touched:
                continue
            if fill_size <= 0:
                continue

            fill_policy_basis = "bounded_visible_liquidity_top_of_book"
            execution_realism_class = "bounded_approximation"
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

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OpenOrderParams, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise GatewayError(
                "py-clob-client is required for live mode. Install it with `pip install py-clob-client`."
            ) from exc

        private_key_env = str(auth_cfg["private_key_env"])
        funder_env = str(auth_cfg["funder_env"])
        try:
            private_key, funder, source_meta = load_auth_secrets(auth_cfg)
        except SecretLoadError as exc:
            raise GatewayError(str(exc)) from exc
        try:
            private_key = _normalize_private_key(private_key)
        except GatewayError as exc:
            src = str(source_meta.get("private_key_source", private_key_env))
            raise GatewayError(f"invalid private key from {src}: {exc}") from exc
        try:
            funder = _normalize_evm_address(funder)
        except GatewayError as exc:
            src = str(source_meta.get("funder_source", funder_env))
            raise GatewayError(f"invalid funder address from {src}: {exc}") from exc

        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._OpenOrderParams = OpenOrderParams
        self._BUY = BUY
        self._SELL = SELL
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        except ImportError as exc:
            raise GatewayError("py-clob-client missing clob_types for live wallet truth") from exc
        self._AssetType = AssetType
        self._BalanceAllowanceParams = BalanceAllowanceParams

        host = str(auth_cfg["host"])
        chain_id = int(auth_cfg["chain_id"])
        signature_type = int(auth_cfg["signature_type"])
        self._wallet_address = funder
        self._chain_id = chain_id
        self._host = host

        self.client = ClobClient(
            host=host,
            key=private_key,
            chain_id=chain_id,
            signature_type=signature_type,
            funder=funder,
        )
        api_creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(api_creds)

    def wallet_address(self) -> str:
        return str(self._wallet_address)

    def chain_id(self) -> int:
        return int(self._chain_id)

    def host(self) -> str:
        return str(self._host)

    def get_collateral_balance_allowance(self) -> Dict[str, Any]:
        params = self._BalanceAllowanceParams(asset_type=self._AssetType.COLLATERAL, signature_type=-1)
        payload = self.client.get_balance_allowance(params)
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
        side = self._BUY if intent.side == "BUY" else self._SELL
        order_args = self._OrderArgs(token_id=intent.token_id, price=float(intent.price), size=float(intent.size), side=side)
        signed_order = self.client.create_order(order_args)

        tif = intent.tif.upper()
        post_only = self._enforce_post_only if intent.post_only is None else bool(intent.post_only)
        if self._enforce_post_only and not post_only and not self._allow_taker:
            raise GatewayError("post-only enforcement active; set auth.allow_taker=true to allow taker overrides")
        if post_only and tif != "GTC":
            raise GatewayError(f"post-only requires GTC tif, got {tif!r}")
        order_type = self._OrderType.GTC
        if hasattr(self._OrderType, tif):
            order_type = getattr(self._OrderType, tif)

        try:
            response = self.client.post_order(signed_order, order_type, post_only=post_only)
        except TypeError as exc:
            if post_only:
                raise GatewayError(
                    "py-clob-client post_order does not support post_only. Upgrade py-clob-client for maker-safe operation."
                ) from exc
            response = self.client.post_order(signed_order, order_type)
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
        )

    def cancel_order(self, order_id: str) -> bool:
        response = self.client.cancel(order_id)
        self._invalidate_open_orders_cache()
        if isinstance(response, dict):
            canceled = first_non_none(response.get("canceled"), response.get("cancelled"), response.get("success"))
            if isinstance(canceled, bool):
                return canceled
            state = str(first_non_none(response.get("status"), response.get("state"), "")).strip().lower()
            if state in {"canceled", "cancelled", "ok", "success"}:
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
        response = self.client.cancel_all()
        self._invalidate_open_orders_cache()
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
            raw = self.client.get_orders(self._OpenOrderParams())
        except TypeError:
            raw = self.client.get_orders()
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
        return out

    def poll_fills(self) -> List[FillEvent]:
        raw = self.client.get_trades()
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
        return fills
