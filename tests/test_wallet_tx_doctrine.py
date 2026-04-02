import unittest

from prodesk.gateway import BaseGateway, GatewayError
from prodesk.models import FillEvent, LiveOrder, OrderIntent
from prodesk.tx_manager import TransactionManager
from prodesk.wallet_doctrine import (
    AllowanceSnapshot,
    LiveWalletDoctrine,
    NonceSnapshot,
    PaperWalletDoctrine,
    PendingTxSnapshot,
    WalletAuthorization,
    WalletSnapshot,
    create_wallet_doctrine,
)


class _DummyGateway(BaseGateway):
    def __init__(self) -> None:
        self._seq = 0
        self._open: dict[str, LiveOrder] = {}

    def place_order(self, intent: OrderIntent, client_order_id: str) -> LiveOrder:
        self._seq += 1
        order = LiveOrder(
            order_id=f"ord-{self._seq}",
            token_id=intent.token_id,
            side=intent.side,
            price=float(intent.price),
            size=float(intent.size),
            remaining_size=float(intent.size),
            status="OPEN",
            client_order_id=client_order_id,
        )
        self._open[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        return self._open.pop(order_id, None) is not None

    def cancel_all(self) -> int:
        count = len(self._open)
        self._open.clear()
        return count

    def get_open_orders(self) -> list[LiveOrder]:
        return list(self._open.values())

    def poll_fills(self) -> list[FillEvent]:
        return []


class _StaticTruthSource:
    def __init__(self, *, chain_id: int = 137, address: str = "0x1111111111111111111111111111111111111111") -> None:
        self.chain_id = chain_id
        self.address = address

    def wallet_snapshot(self) -> WalletSnapshot:
        return WalletSnapshot(
            address=self.address,
            chain_id=self.chain_id,
            pol_balance=10.0,
            usdc_balance=1000.0,
            locked_usdc=0.0,
            protected_reserve_usdc=0.0,
            deployable_usdc=1000.0,
            ts_utc="2026-03-14T00:00:00.000Z",
            source="test",
            healthy=True,
            detail="",
        )

    def allowance_snapshot(self) -> AllowanceSnapshot:
        return AllowanceSnapshot(
            allowance_usdc=1000.0,
            ts_utc="2026-03-14T00:00:00.000Z",
            source="test",
            healthy=True,
            detail="",
        )

    def nonce_snapshot(self) -> NonceSnapshot:
        return NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="test",
            healthy=False,
            detail="unavailable",
        )

    def pending_tx_snapshot(self) -> PendingTxSnapshot:
        return PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="test",
            healthy=True,
            detail="",
        )


class _FallbackPolTruthSource(_StaticTruthSource):
    def wallet_snapshot(self) -> WalletSnapshot:
        snap = super().wallet_snapshot()
        return WalletSnapshot(
            address=snap.address,
            chain_id=snap.chain_id,
            pol_balance=snap.pol_balance,
            usdc_balance=snap.usdc_balance,
            locked_usdc=snap.locked_usdc,
            protected_reserve_usdc=snap.protected_reserve_usdc,
            deployable_usdc=snap.deployable_usdc,
            ts_utc=snap.ts_utc,
            source=snap.source,
            healthy=False,
            detail="live_pol_balance_fallback",
        )


class WalletDoctrineBoundaryTests(unittest.TestCase):
    def test_paper_wallet_reduces_by_notional_cap(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "max_notional_per_order_usdc": 5.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        intent = OrderIntent(token_id="tok", side="BUY", price=1.0, size=20.0)
        auth = wallet.authorize_intent(intent)
        self.assertTrue(auth.allowed)
        self.assertEqual(auth.action, "reduce")
        self.assertAlmostEqual(auth.approved_size, 5.0)

    def test_live_wallet_fails_closed_on_chain_identity_mismatch(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
            },
            truth_source=_StaticTruthSource(chain_id=80001),
            mode="live",
        )
        wallet.register_nonce_authority("tx_manager")
        intent = OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0)
        auth = wallet.authorize_intent(intent)
        self.assertFalse(auth.allowed)
        self.assertTrue(auth.halt)
        self.assertIn("wallet_chain_id_mismatch", wallet.halt_reason())

    def test_create_wallet_doctrine_live_requires_live_gateway(self) -> None:
        with self.assertRaises(ValueError):
            create_wallet_doctrine({}, mode="live", gateway=_DummyGateway())

    def test_live_wallet_uses_tx_provider_nonce_snapshot_when_required(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
            },
            truth_source=_StaticTruthSource(chain_id=137),
            mode="live",
        )
        wallet.register_nonce_authority("tx_manager")
        wallet.register_pending_tx_provider(
            lambda: {
                "pending_count": 1,
                "order_ids": ["ord-1"],
                "current_nonce": 7,
                "pending_nonces": [7],
                "healthy": True,
                "source": "tx_manager_lifecycle",
                "detail": "",
            }
        )
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertTrue(auth.allowed)
        nonce_snapshot = wallet.status()["nonce_snapshot"]
        self.assertTrue(bool(nonce_snapshot.get("healthy")))
        self.assertEqual(int(nonce_snapshot.get("current_nonce")), 7)

    def test_live_wallet_rejects_fallback_pol_snapshot_when_required(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_pol_balance_snapshot": True,
            },
            truth_source=_FallbackPolTruthSource(chain_id=137),
            mode="live",
        )
        wallet.register_nonce_authority("tx_manager")
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_snapshot_unhealthy")

    def test_live_wallet_requires_nonce_value_when_configured(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
            },
            truth_source=_StaticTruthSource(chain_id=137),
            mode="live",
        )
        wallet.register_nonce_authority("tx_manager")
        wallet.register_pending_tx_provider(
            lambda: {
                "pending_count": 0,
                "order_ids": [],
                "pending_nonces": [],
                "current_nonce": None,
                "healthy": True,
                "source": "tx_manager_lifecycle",
                "detail": "",
            }
        )
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_nonce_value_missing")


class TransactionManagerBoundaryTests(unittest.TestCase):
    def test_submit_requires_wallet_authorization(self) -> None:
        tx = TransactionManager(_DummyGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        with self.assertRaises(GatewayError):
            tx.submit_order(
                intent,
                client_order_id="cid-1",
                wallet_authorization=WalletAuthorization(
                    allowed=False,
                    action="reject",
                    approved_size=0.0,
                    reason="test",
                ),
            )

    def test_submit_blocks_authorization_reuse(self) -> None:
        tx = TransactionManager(_DummyGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        auth = WalletAuthorization(
            allowed=True,
            action="approve",
            approved_size=10.0,
            lock_id="lock-1",
            authorization_id="lock-1",
            reason="ok",
        )
        tx.submit_order(intent, client_order_id="cid-1", wallet_authorization=auth)
        with self.assertRaises(GatewayError):
            tx.submit_order(intent, client_order_id="cid-2", wallet_authorization=auth)

    def test_submit_blocks_size_above_wallet_authorization(self) -> None:
        tx = TransactionManager(_DummyGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        auth = WalletAuthorization(
            allowed=True,
            action="reduce",
            approved_size=5.0,
            lock_id="lock-1",
            authorization_id="lock-1",
            reason="cap",
        )
        with self.assertRaises(GatewayError):
            tx.submit_order(intent, client_order_id="cid-1", wallet_authorization=auth)

    def test_pending_snapshot_tracks_open_orders(self) -> None:
        tx = TransactionManager(_DummyGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        auth = WalletAuthorization(
            allowed=True,
            action="approve",
            approved_size=10.0,
            lock_id="lock-1",
            authorization_id="lock-1",
            reason="ok",
        )
        order = tx.submit_order(intent, client_order_id="cid-1", wallet_authorization=auth)
        snap = tx.pending_tx_snapshot()
        self.assertEqual(snap["pending_count"], 1)
        self.assertIn(order.order_id, snap["order_ids"])
        self.assertEqual(snap["current_nonce"], 1)
        self.assertIn(1, snap["pending_nonces"])

    def test_cancel_order_records_gateway_error(self) -> None:
        class _CancelErrorGateway(_DummyGateway):
            def cancel_order(self, order_id: str) -> bool:
                raise GatewayError("cancel boom")

        tx = TransactionManager(_CancelErrorGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        auth = WalletAuthorization(
            allowed=True,
            action="approve",
            approved_size=10.0,
            lock_id="lock-1",
            authorization_id="lock-1",
            reason="ok",
        )
        order = tx.submit_order(intent, client_order_id="cid-1", wallet_authorization=auth)
        with self.assertRaises(GatewayError):
            tx.cancel_order(order.order_id)
        snap = tx.lifecycle_snapshot()["cid-1"]
        self.assertEqual(snap["state"], "cancel_failed")
        self.assertEqual(snap["failure_class"], "cancel_gateway_error")

    def test_cancel_all_only_marks_confirmed_closed_orders(self) -> None:
        class _PartialCancelAllGateway(_DummyGateway):
            def cancel_all(self) -> int:
                ids = sorted(self._open.keys())
                if ids:
                    self._open.pop(ids[0], None)
                return 2

        tx = TransactionManager(_PartialCancelAllGateway())
        intent = OrderIntent(token_id="tok", side="BUY", price=0.5, size=10.0)
        auth_1 = WalletAuthorization(
            allowed=True,
            action="approve",
            approved_size=10.0,
            lock_id="lock-1",
            authorization_id="lock-1",
            reason="ok",
        )
        auth_2 = WalletAuthorization(
            allowed=True,
            action="approve",
            approved_size=10.0,
            lock_id="lock-2",
            authorization_id="lock-2",
            reason="ok",
        )
        tx.submit_order(intent, client_order_id="cid-1", wallet_authorization=auth_1)
        tx.submit_order(intent, client_order_id="cid-2", wallet_authorization=auth_2)

        summary = tx.cancel_all_with_summary()
        self.assertEqual(summary["gateway_reported_canceled_count"], 2)
        self.assertEqual(summary["open_before_count"], 2)
        self.assertEqual(summary["open_after_count"], 1)
        self.assertEqual(summary["confirmed_canceled_count"], 1)
        self.assertEqual(len(summary["unconfirmed_order_ids"]), 1)

        snap = tx.lifecycle_snapshot()
        self.assertEqual(snap["cid-1"]["state"], "canceled")
        self.assertEqual(snap["cid-2"]["state"], "open")


if __name__ == "__main__":
    unittest.main()
