import unittest
from unittest import mock

from prodesk.gateway import BaseGateway, GatewayError
from prodesk.models import FillEvent, LiveOrder, OrderIntent
from prodesk.tx_manager import TransactionManager
from prodesk.wallet.wallet_reservations import WalletReservations
from prodesk.wallet_doctrine import (
    AUTHORITY_CLASS_LOCAL,
    AllowanceSnapshot,
    LiveWalletDoctrine,
    NonceSnapshot,
    OpenOrderStateSnapshot,
    PaperWalletDoctrine,
    PendingTxSnapshot,
    ReconciliationResult,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
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

    def open_order_state_snapshot(self) -> OpenOrderStateSnapshot:
        return OpenOrderStateSnapshot(
            open_count=0,
            order_ids=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="test_open_orders",
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


class _LiveNonceTruthSource(_StaticTruthSource):
    def __init__(
        self,
        *,
        chain_id: int = 137,
        address: str = "0x1111111111111111111111111111111111111111",
        current_nonce: int | None = None,
        nonce_healthy: bool = True,
        nonce_detail: str = "",
    ) -> None:
        super().__init__(chain_id=chain_id, address=address)
        self._current_nonce = current_nonce
        self._nonce_healthy = bool(nonce_healthy)
        self._nonce_detail = str(nonce_detail)

    def nonce_snapshot(self) -> NonceSnapshot:
        return NonceSnapshot(
            current_nonce=self._current_nonce,
            pending_nonces=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="canonical_live_wallet_truth",
            healthy=self._nonce_healthy,
            detail=self._nonce_detail,
        )


class _UnavailablePendingTxTruthSource(_StaticTruthSource):
    def nonce_snapshot(self) -> NonceSnapshot:
        return NonceSnapshot(
            current_nonce=9,
            pending_nonces=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="canonical_live_wallet_truth",
            healthy=True,
            detail="",
        )

    def pending_tx_snapshot(self) -> PendingTxSnapshot:
        return PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="canonical_live_wallet_truth",
            healthy=False,
            detail="pending_wallet_tx_snapshot_unavailable",
        )


class _SpoofedLocalNonceTruthSource(_StaticTruthSource):
    def nonce_snapshot(self) -> NonceSnapshot:
        return NonceSnapshot(
            current_nonce=42,
            pending_nonces=tuple(),
            ts_utc="2026-03-14T00:00:00.000Z",
            source="canonical_live_wallet_truth",
            healthy=True,
            detail="",
            truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            authority_class=AUTHORITY_CLASS_LOCAL,
        )


class WalletDoctrineBoundaryTests(unittest.TestCase):
    @staticmethod
    def _register_local_lifecycle_provider(wallet: PaperWalletDoctrine | LiveWalletDoctrine, *, nonce: int = 7) -> None:
        wallet.register_pending_tx_provider(
            lambda: {
                "pending_count": 0,
                "order_ids": [],
                "current_nonce": nonce,
                "pending_nonces": [nonce] if nonce >= 0 else [],
                "healthy": True,
                "source": "local_tx_lifecycle_state",
                "detail": "",
            }
        )

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
        self._register_local_lifecycle_provider(wallet)
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
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
            },
            truth_source=_StaticTruthSource(chain_id=80001),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        intent = OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0)
        auth = wallet.authorize_intent(intent)
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        self.assertFalse(auth.halt)
        self.assertEqual(wallet.status().get("authority_status_class"), "bootstrap_non_authoritative")
        reconcile = wallet.status().get("last_reconcile_result", {})
        self.assertEqual(reconcile.get("reason"), "wallet_chain_id_mismatch")

    def test_create_wallet_doctrine_live_requires_live_gateway(self) -> None:
        with self.assertRaises(ValueError):
            create_wallet_doctrine({}, mode="live", gateway=_DummyGateway())

    def test_live_wallet_strict_nonce_gate_rejects_local_lifecycle_substitute(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
                "approval_spender_targets": ["0x2222222222222222222222222222222222222222"],
            },
            truth_source=_StaticTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        status = wallet.status()
        canonical_nonce = (
            status.get("canonical_live_wallet_truth", {})
            .get("nonce_snapshot", {})
        )
        local_nonce = (
            status.get("local_tx_lifecycle_state", {})
            .get("nonce_snapshot", {})
        )
        self.assertFalse(bool(canonical_nonce.get("healthy")))
        self.assertTrue(bool(local_nonce.get("healthy")))
        self.assertEqual(
            status.get("last_reconcile_result", {}).get("reason"),
            "wallet_nonce_snapshot_unhealthy",
        )

    def test_live_wallet_strict_nonce_gate_rejects_domain_spoofed_source_label(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
                "approval_spender_targets": ["0x2222222222222222222222222222222222222222"],
            },
            truth_source=_SpoofedLocalNonceTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        status = wallet.status()
        self.assertEqual(status.get("last_reconcile_result", {}).get("reason"), "wallet_nonce_snapshot_unhealthy")

    def test_live_wallet_fails_closed_when_approval_target_unknown(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_allowance": True,
                "approval_spender_targets": [],
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
            },
            truth_source=_StaticTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        self.assertIn("bootstrap_non_authoritative", wallet.status_contract().get("wallet_health_reasons", []))

    def test_live_wallet_rejects_fallback_pol_snapshot_when_required(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_pol_balance_snapshot": True,
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
            },
            truth_source=_FallbackPolTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        self.assertEqual(wallet.status().get("last_reconcile_result", {}).get("reason"), "wallet_snapshot_unhealthy")

    def test_live_wallet_requires_nonce_value_when_configured(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
            },
            truth_source=_LiveNonceTruthSource(chain_id=137, current_nonce=None, nonce_healthy=True),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        self.assertEqual(wallet.status().get("last_reconcile_result", {}).get("reason"), "wallet_nonce_value_missing")

    def test_wallet_status_contract_exposes_required_fields(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        contract = wallet.status_contract()
        expected_keys = {
            "gas_balance",
            "gas_reserve_min",
            "gas_ok",
            "stable_balance_total",
            "protected_reserve",
            "open_reserved",
            "deployable_capital",
            "approval_ok",
            "nonce_ok",
            "reconcile_ok",
            "wallet_health_ok",
            "wallet_health_reasons",
        }
        self.assertTrue(expected_keys.issubset(set(contract.keys())))

    def test_wallet_emits_reservation_lifecycle_events(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        emitted: list[str] = []
        wallet.register_event_logger(lambda event_type, payload: emitted.append(str(event_type)))

        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=5.0))
        self.assertTrue(auth.allowed)
        self.assertTrue(auth.lock_id)
        wallet.release_pending_lock(auth.lock_id)
        self.assertIn("wallet_reservation_created", emitted)
        self.assertIn("wallet_reservation_released", emitted)

    def test_wallet_confirm_submission_moves_lock_pending_to_order(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=5.0))
        self.assertTrue(auth.allowed)
        before = wallet.status()
        self.assertGreater(float(before["pending_lock_usdc"]), 0.0)
        self.assertEqual(float(before["order_lock_usdc"]), 0.0)

        ok = wallet.confirm_submission(lock_id=auth.lock_id, order_id="ord-1", order_open=True)
        self.assertTrue(ok)
        after = wallet.status()
        self.assertEqual(float(after["pending_lock_usdc"]), 0.0)
        self.assertGreater(float(after["order_lock_usdc"]), 0.0)

    def test_wallet_startup_barrier_blocks_authorization_until_authoritative_refresh_ready(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        auth_before = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth_before.allowed)
        self.assertEqual(auth_before.reason, "wallet_startup_authority_not_ready")
        contract_before = wallet.status_contract()
        self.assertEqual(contract_before.get("authority_status_class"), "bootstrap_non_authoritative")
        self.assertIn("bootstrap_non_authoritative", contract_before.get("wallet_health_reasons", []))

        self._register_local_lifecycle_provider(wallet)
        auth_after = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertTrue(auth_after.allowed)
        self.assertIn(auth_after.action, {"approve", "reduce"})

    def test_live_wallet_strict_pending_tx_gate_rejects_local_lifecycle_substitute(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_pending_tx_snapshot": True,
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
            },
            truth_source=_UnavailablePendingTxTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_startup_authority_not_ready")
        status = wallet.status()
        canonical_pending = (
            status.get("canonical_live_wallet_truth", {})
            .get("pending_wallet_tx_snapshot", {})
        )
        local_pending = (
            status.get("local_tx_lifecycle_state", {})
            .get("pending_tx_snapshot", {})
        )
        self.assertFalse(bool(canonical_pending.get("healthy")))
        self.assertTrue(bool(local_pending.get("healthy")))
        self.assertEqual(
            status.get("last_reconcile_result", {}).get("reason"),
            "wallet_pending_tx_snapshot_unhealthy",
        )

    def test_live_wallet_non_order_capable_mode_explicitly_blocks_authorization(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": False,
                "require_live_nonce_value": False,
                "require_live_pending_tx_snapshot": False,
            },
            truth_source=_StaticTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": False},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=1.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "wallet_live_order_submission_disabled")
        contract = wallet.status_contract()
        self.assertFalse(bool(contract.get("order_capable_live")))
        self.assertFalse(bool(contract.get("order_submit_eligible")))
        self.assertIn("order_capable_live_disabled", list(contract.get("wallet_health_reasons", [])))

    def test_live_wallet_truth_availability_fields_are_semantically_strict(self) -> None:
        wallet = LiveWalletDoctrine(
            {
                "expected_chain_id": 137,
                "expected_wallet_address": "0x1111111111111111111111111111111111111111",
                "nonce_authority": "tx_manager",
                "require_live_nonce_snapshot": True,
                "require_live_nonce_value": True,
                "require_live_pending_tx_snapshot": True,
            },
            truth_source=_UnavailablePendingTxTruthSource(chain_id=137),
            mode="live",
            auth_cfg={"live_order_submission_enabled": True},
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)
        contract = wallet.status_contract()
        self.assertTrue(bool(contract.get("canonical_live_nonce_available")))
        self.assertFalse(bool(contract.get("canonical_live_pending_wallet_tx_available")))
        self.assertIsInstance(contract.get("live_truth_gap_reasons"), list)
        self.assertTrue(
            any("canonical_live_pending_wallet_tx_unavailable" in str(x) for x in (contract.get("live_truth_gap_reasons") or []))
        )

    def test_wallet_post_lock_reconcile_failure_rolls_back_pending_lock(self) -> None:
        wallet = PaperWalletDoctrine(
            {
                "paper_starting_usdc": 100.0,
                "paper_allowance_usdc": 100.0,
                "require_allowance": True,
                "nonce_authority": "tx_manager",
            },
            mode="paper",
        )
        wallet.register_nonce_authority("tx_manager")
        self._register_local_lifecycle_provider(wallet)

        real_reconcile = wallet.reconcile
        fail_once = {"pending": True}

        def _patched_reconcile(*, pre_execution: bool = False) -> ReconciliationResult:
            if (
                not pre_execution
                and fail_once["pending"]
                and float(wallet.status().get("pending_lock_usdc", 0.0) or 0.0) > 0.0
            ):
                fail_once["pending"] = False
                return ReconciliationResult(
                    healthy=False,
                    action="reject",
                    reason="forced_post_lock_reconcile_failure",
                    detail="test_injected_failure",
                    halt=False,
                    ts_utc="2026-04-08T00:00:00.000Z",
                )
            return real_reconcile(pre_execution=pre_execution)

        with mock.patch.object(wallet, "reconcile", side_effect=_patched_reconcile):
            auth = wallet.authorize_intent(OrderIntent(token_id="tok", side="BUY", price=1.0, size=5.0))
        self.assertFalse(auth.allowed)
        self.assertEqual(auth.reason, "forced_post_lock_reconcile_failure")
        status = wallet.status()
        self.assertEqual(float(status.get("pending_lock_usdc", 0.0) or 0.0), 0.0)
        self.assertEqual(float(status.get("order_lock_usdc", 0.0) or 0.0), 0.0)
        self.assertEqual(float(status.get("locked_usdc", 0.0) or 0.0), 0.0)

    def test_wallet_reservations_orphan_lock_invariant_idempotent_and_non_negative(self) -> None:
        reservations = WalletReservations()
        lock_id = reservations.create_pending(5.0)
        reservations.release_pending(lock_id)
        reservations.release_pending(lock_id)
        self.assertEqual(reservations.locked_total(), 0.0)

        lock_id_2 = reservations.create_pending(7.0)
        ok_first, _ = reservations.confirm_submission(lock_id=lock_id_2, order_id="ord-1", order_open=True)
        ok_second, reason_second = reservations.confirm_submission(lock_id=lock_id_2, order_id="ord-1", order_open=True)
        self.assertTrue(ok_first)
        self.assertTrue(ok_second)
        self.assertIn(reason_second, {"wallet_lock_id_idempotent_completed", "wallet_lock_id_idempotent_order_exists"})

        reservations.settle_fill(order_id="ord-1", notional_usd=4.0, tolerance=1e-9)
        reservations.settle_fill(order_id="ord-1", notional_usd=4.0, tolerance=1e-9)
        reservations.release_order("ord-1")
        reservations.release_order("ord-1")
        snap = reservations.snapshot()
        self.assertEqual(float(snap.get("pending_lock_usdc", 0.0) or 0.0), 0.0)
        self.assertEqual(float(snap.get("order_lock_usdc", 0.0) or 0.0), 0.0)
        self.assertGreaterEqual(float(snap.get("locked_usdc", 0.0) or 0.0), 0.0)


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
