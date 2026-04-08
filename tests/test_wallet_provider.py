import unittest

from prodesk.gateway import GatewayError
from prodesk.models import LiveOrder
from prodesk.wallet.wallet_provider import GatewayLiveWalletTruthSource
from prodesk.wallet.wallet_types import (
    AUTHORITY_CLASS_DERIVED,
    AUTHORITY_CLASS_LIVE,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
    TRUTH_DOMAIN_OPEN_ORDER_STATE,
)


class _StubLiveGateway:
    def __init__(self, payload: dict, *, open_orders: list[LiveOrder] | None = None) -> None:
        self._payload = payload
        self._open_orders = list(open_orders or [])

    def get_collateral_balance_allowance(self) -> dict:
        return dict(self._payload)

    def wallet_address(self) -> str:
        return "0x1111111111111111111111111111111111111111"

    def chain_id(self) -> int:
        return 137

    def get_open_orders(self) -> list[LiveOrder]:
        return list(self._open_orders)


class WalletProviderTests(unittest.TestCase):
    def test_wallet_snapshot_required_field_ambiguity_fails_closed(self) -> None:
        gateway = _StubLiveGateway({"balance": 100.0, "available": 90.0})
        source = GatewayLiveWalletTruthSource(
            gateway,  # type: ignore[arg-type]
            {"provider_ambiguity_abs_tolerance": 1e-9, "provider_ambiguity_rel_tolerance": 0.0},
        )
        with self.assertRaises(GatewayError):
            source.wallet_snapshot()

    def test_wallet_snapshot_optional_pol_ambiguity_marks_unhealthy(self) -> None:
        gateway = _StubLiveGateway({"balance": 100.0, "polBalance": 10.0, "gasBalance": 7.5})
        source = GatewayLiveWalletTruthSource(
            gateway,  # type: ignore[arg-type]
            {
                "provider_ambiguity_abs_tolerance": 1e-9,
                "provider_ambiguity_rel_tolerance": 0.0,
                "require_live_pol_balance_snapshot": True,
                "live_pol_balance_fallback": 1.0,
            },
        )
        snapshot = source.wallet_snapshot()
        self.assertFalse(snapshot.healthy)
        self.assertIn("live_pol_balance_ambiguous", snapshot.detail)

    def test_pending_wallet_tx_truth_is_not_open_order_surrogate(self) -> None:
        open_orders = [
            LiveOrder(
                order_id="ord-1",
                token_id="tok",
                side="BUY",
                price=0.5,
                size=10.0,
                remaining_size=10.0,
                status="OPEN",
                client_order_id="cid-1",
            )
        ]
        gateway = _StubLiveGateway({"balance": 100.0}, open_orders=open_orders)
        source = GatewayLiveWalletTruthSource(gateway, {})  # type: ignore[arg-type]
        pending_snapshot = source.pending_tx_snapshot()
        open_order_snapshot = source.open_order_state_snapshot()

        self.assertFalse(pending_snapshot.healthy)
        self.assertEqual(pending_snapshot.pending_count, 0)
        self.assertIn("unavailable", pending_snapshot.detail)

        self.assertTrue(open_order_snapshot.healthy)
        self.assertEqual(open_order_snapshot.open_count, 1)
        self.assertIn("ord-1", open_order_snapshot.order_ids)
        self.assertEqual(pending_snapshot.truth_domain, TRUTH_DOMAIN_CANONICAL_LIVE_WALLET)
        self.assertEqual(pending_snapshot.authority_class, AUTHORITY_CLASS_LIVE)
        self.assertEqual(open_order_snapshot.truth_domain, TRUTH_DOMAIN_OPEN_ORDER_STATE)
        self.assertEqual(open_order_snapshot.authority_class, AUTHORITY_CLASS_DERIVED)


if __name__ == "__main__":
    unittest.main()
