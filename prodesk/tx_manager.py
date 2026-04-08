from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from .common import utc_iso
from .gateway import BaseGateway, GatewayError, PostOnlyRejectError
from .models import BookTop, FillEvent, LiveOrder, OrderIntent
from .wallet.wallet_types import AUTHORITY_CLASS_LOCAL, TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE
from .wallet_doctrine import WalletAuthorization


@dataclasses.dataclass
class TxLifecycleRecord:
    client_order_id: str
    authorization_id: str
    token_id: str
    side: str
    price: float
    size: float
    tif: str
    post_only: Optional[bool]
    state: str
    created_ts_utc: str
    last_update_ts_utc: str
    reserved_nonce: int
    order_id: Optional[str] = None
    target_ref: Optional[str] = None
    failure_class: str = ""
    failure_detail: str = ""
    filled_size: float = 0.0
    canceled: bool = False


class TransactionManager:
    """Submission mechanics authority.

    The execution controller and strategy layers must route submission/cancel
    operations through this component instead of touching gateway mechanics.
    """

    def __init__(self, gateway: BaseGateway) -> None:
        self._gateway = gateway
        self._nonce_authority = "tx_manager"
        self._next_nonce = 0
        self._records_by_client: Dict[str, TxLifecycleRecord] = {}
        self._client_by_order_id: Dict[str, str] = {}
        self._client_by_authorization_id: Dict[str, str] = {}

    def nonce_authority(self) -> str:
        return self._nonce_authority

    def submit_order(
        self,
        intent: OrderIntent,
        *,
        client_order_id: str,
        wallet_authorization: WalletAuthorization,
    ) -> LiveOrder:
        cid = str(client_order_id or "").strip()
        if not cid:
            raise GatewayError("client_order_id required")
        auth = wallet_authorization
        if not auth.allowed:
            raise GatewayError("wallet_authorization_required")
        approved_size = float(auth.approved_size)
        if approved_size <= 0:
            raise GatewayError("wallet_authorization_size_invalid")
        intent_size = float(intent.size)
        if intent_size - approved_size > 1e-9:
            raise GatewayError("wallet_authorization_size_exceeded")
        authorization_id = str(auth.authorization_id or auth.lock_id or "").strip()
        if not authorization_id:
            raise GatewayError("wallet_authorization_id_missing")
        existing_for_auth = self._client_by_authorization_id.get(authorization_id)
        if existing_for_auth and existing_for_auth != cid:
            raise GatewayError("wallet_authorization_reuse_blocked")
        if cid in self._records_by_client:
            raise GatewayError("duplicate_client_order_id")

        now = utc_iso()
        reserved_nonce = self._reserve_nonce()
        record = TxLifecycleRecord(
            client_order_id=cid,
            authorization_id=authorization_id,
            token_id=intent.token_id,
            side=intent.side,
            price=float(intent.price),
            size=intent_size,
            tif=str(intent.tif or "GTC").upper(),
            post_only=intent.post_only,
            state="authorized",
            created_ts_utc=now,
            last_update_ts_utc=now,
            reserved_nonce=reserved_nonce,
            target_ref=(str(intent.target_ref).strip() if str(intent.target_ref or "").strip() else None),
        )
        self._records_by_client[cid] = record
        self._client_by_authorization_id[authorization_id] = cid
        self._set_state(record, "submitting")

        try:
            order = self._gateway.place_order(intent, client_order_id=cid)
        except Exception as exc:
            record.failure_class = self._classify_submit_error(exc)
            record.failure_detail = str(exc)
            self._set_state(record, "submit_failed")
            raise

        record.order_id = str(order.order_id or "").strip() or None
        if record.order_id:
            self._client_by_order_id[record.order_id] = cid
        self._set_state(record, self._normalize_order_state(order.status))
        return order

    def cancel_order(self, order_id: str) -> bool:
        client_id = self._client_by_order_id.get(str(order_id or "").strip())
        try:
            ok = self._gateway.cancel_order(order_id)
        except Exception as exc:
            if client_id:
                record = self._records_by_client.get(client_id)
                if record is not None:
                    record.failure_class = self._classify_cancel_error(exc)
                    record.failure_detail = str(exc)
                    self._set_state(record, "cancel_failed")
            raise
        if client_id:
            record = self._records_by_client.get(client_id)
            if record is not None:
                if ok:
                    record.canceled = True
                    self._set_state(record, "canceled")
                else:
                    record.failure_class = "cancel_not_found"
                    record.failure_detail = "cancel_order returned false"
                    self._set_state(record, "cancel_failed")
        return ok

    def cancel_all(self) -> int:
        summary = self.cancel_all_with_summary()
        return int(summary["confirmed_canceled_count"])

    def cancel_all_with_summary(self) -> Dict[str, Any]:
        open_before = self.get_open_orders()
        open_before_ids = self._order_ids_from_orders(open_before)
        gateway_reported_canceled = self._gateway.cancel_all()
        open_after = self.get_open_orders()
        open_after_ids = self._order_ids_from_orders(open_after)

        confirmed_canceled_order_ids = sorted(open_before_ids - open_after_ids)
        unconfirmed_order_ids = sorted(open_before_ids & open_after_ids)
        for order_id in confirmed_canceled_order_ids:
            client_id = self._client_by_order_id.get(order_id)
            if not client_id:
                continue
            record = self._records_by_client.get(client_id)
            if record is None:
                continue
            record.canceled = True
            self._set_state(record, "canceled")

        try:
            gateway_reported = int(gateway_reported_canceled)
        except (TypeError, ValueError):
            gateway_reported = 0

        return {
            "gateway_reported_canceled_count": max(0, gateway_reported),
            "open_before_count": len(open_before_ids),
            "open_after_count": len(open_after_ids),
            "confirmed_canceled_count": len(confirmed_canceled_order_ids),
            "confirmed_canceled_order_ids": confirmed_canceled_order_ids,
            "unconfirmed_order_ids": unconfirmed_order_ids,
        }

    def get_open_orders(self) -> List[LiveOrder]:
        open_orders = self._gateway.get_open_orders()
        for order in open_orders:
            client_id = str(order.client_order_id or "").strip()
            if not client_id:
                known = self._client_by_order_id.get(str(order.order_id or "").strip())
                if known:
                    client_id = known
            if not client_id:
                continue
            record = self._records_by_client.get(client_id)
            if record is None:
                continue
            record.order_id = str(order.order_id or "").strip() or record.order_id
            if record.order_id:
                self._client_by_order_id[record.order_id] = client_id
            self._set_state(record, self._normalize_order_state(order.status))
        return open_orders

    def poll_fills(self) -> List[FillEvent]:
        fills = self._gateway.poll_fills()
        for fill in fills:
            oid = str(fill.order_id or "").strip()
            client_id = self._client_by_order_id.get(oid)
            if not client_id:
                continue
            record = self._records_by_client.get(client_id)
            if record is None:
                continue
            if not str(fill.target_ref or "").strip() and str(record.target_ref or "").strip():
                fill.target_ref = str(record.target_ref)
            record.filled_size += max(0.0, float(fill.size))
            if record.filled_size + 1e-9 >= record.size:
                self._set_state(record, "filled")
            else:
                self._set_state(record, "partial")
        return fills

    def on_book(self, top: BookTop) -> None:
        self._gateway.on_book(top)

    def seed_fill_cursor(self, last_fill_ts_utc: Optional[str]) -> None:
        self._gateway.seed_fill_cursor(last_fill_ts_utc)

    def pending_tx_snapshot(self) -> Dict[str, Any]:
        pending = [
            record
            for record in self._records_by_client.values()
            if record.state in {"authorized", "submitting", "submitted", "open", "partial"}
        ]
        pending_nonces = sorted({int(record.reserved_nonce) for record in pending if int(record.reserved_nonce) > 0})
        current_nonce = int(self._next_nonce) if self._next_nonce > 0 else None
        return {
            "pending_count": len(pending),
            "order_ids": [str(record.order_id) for record in pending if record.order_id],
            "client_order_ids": [record.client_order_id for record in pending],
            "pending_nonces": pending_nonces,
            "current_nonce": current_nonce,
            "healthy": True,
            "source": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            "truth_domain": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            "authority_class": AUTHORITY_CLASS_LOCAL,
            "detail": "",
        }

    def nonce_snapshot(self) -> Dict[str, Any]:
        pending = self.pending_tx_snapshot()
        return {
            "current_nonce": pending.get("current_nonce"),
            "pending_nonces": pending.get("pending_nonces", []),
            "healthy": True,
            "source": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            "truth_domain": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            "authority_class": AUTHORITY_CLASS_LOCAL,
            "detail": "",
        }

    def lifecycle_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {cid: dataclasses.asdict(record) for cid, record in self._records_by_client.items()}

    def close(self) -> None:
        self._gateway.close()

    def _reserve_nonce(self) -> int:
        self._next_nonce += 1
        return self._next_nonce

    @staticmethod
    def _normalize_order_state(status: Any) -> str:
        text = str(status or "").strip().lower()
        if text in {"open", "submitted"}:
            return "open"
        if text in {"partial", "partially_filled"}:
            return "partial"
        if text in {"filled", "executed"}:
            return "filled"
        if text in {"canceled", "cancelled"}:
            return "canceled"
        if text in {"rejected", "failed", "error"}:
            return "rejected"
        if not text:
            return "submitted"
        return text

    @staticmethod
    def _classify_submit_error(exc: Exception) -> str:
        if isinstance(exc, PostOnlyRejectError):
            return "post_only_reject"
        if isinstance(exc, GatewayError):
            return "gateway_error"
        return "submit_exception"

    @staticmethod
    def _classify_cancel_error(exc: Exception) -> str:
        if isinstance(exc, GatewayError):
            return "cancel_gateway_error"
        return "cancel_exception"

    @staticmethod
    def _set_state(record: TxLifecycleRecord, state: str) -> None:
        record.state = str(state or "unknown")
        record.last_update_ts_utc = utc_iso()

    @staticmethod
    def _order_ids_from_orders(orders: List[LiveOrder]) -> set[str]:
        out: set[str] = set()
        for order in orders:
            oid = str(order.order_id or "").strip()
            if oid:
                out.add(oid)
        return out
