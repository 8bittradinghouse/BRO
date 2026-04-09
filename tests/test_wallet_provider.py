import json
import unittest
from pathlib import Path

from prodesk.gateway import GatewayError
from prodesk.models import LiveOrder
from prodesk.wallet.wallet_provider import GatewayLiveWalletTruthSource
from prodesk.wallet.wallet_truth_policy import (
    PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
    PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
)
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


def _fixture_payload(name: str) -> dict:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "wallet_provider" / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class WalletProviderTests(unittest.TestCase):
    def test_wallet_snapshot_required_field_missing_fails_closed(self) -> None:
        gateway = _StubLiveGateway({"allowance": 10.0, "polBalance": 1.0})
        source = GatewayLiveWalletTruthSource(gateway, {})  # type: ignore[arg-type]
        with self.assertRaises(GatewayError):
            source.wallet_snapshot()

    def test_wallet_snapshot_required_field_ambiguity_fails_closed(self) -> None:
        gateway = _StubLiveGateway(_fixture_payload("live_balance_required_conflict.json"))
        source = GatewayLiveWalletTruthSource(gateway, {})  # type: ignore[arg-type]
        with self.assertRaises(GatewayError):
            source.wallet_snapshot()

    def test_wallet_snapshot_optional_pol_ambiguity_marks_unhealthy(self) -> None:
        gateway = _StubLiveGateway(_fixture_payload("live_pol_optional_conflict.json"))
        source = GatewayLiveWalletTruthSource(
            gateway,  # type: ignore[arg-type]
            {
                "require_live_pol_balance_snapshot": True,
                "live_pol_balance_fallback": 1.0,
            },
        )
        snapshot = source.wallet_snapshot()
        self.assertFalse(snapshot.healthy)
        self.assertIn("live_pol_balance_ambiguous", snapshot.detail)

    def test_wallet_snapshot_representative_payload_is_canonical_live_truth(self) -> None:
        gateway = _StubLiveGateway(_fixture_payload("live_balance_allowance_primary.json"))
        source = GatewayLiveWalletTruthSource(gateway, {})  # type: ignore[arg-type]
        wallet_snapshot = source.wallet_snapshot()
        allowance_snapshot = source.allowance_snapshot()
        self.assertTrue(wallet_snapshot.healthy)
        self.assertEqual(wallet_snapshot.truth_domain, TRUTH_DOMAIN_CANONICAL_LIVE_WALLET)
        self.assertEqual(wallet_snapshot.authority_class, AUTHORITY_CLASS_LIVE)
        self.assertTrue(allowance_snapshot.healthy)
        self.assertEqual(allowance_snapshot.truth_domain, TRUTH_DOMAIN_CANONICAL_LIVE_WALLET)
        self.assertEqual(allowance_snapshot.authority_class, AUTHORITY_CLASS_LIVE)

    def test_wallet_snapshot_multi_path_consistency_within_centralized_tolerances(self) -> None:
        payload = _fixture_payload("live_balance_allowance_multi_path_consistent.json")
        cfg = {
            "provider_ambiguity_abs_tolerance": PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
            "provider_ambiguity_rel_tolerance": PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
            "require_live_pol_balance_snapshot": True,
        }
        gateway = _StubLiveGateway(payload)
        source = GatewayLiveWalletTruthSource(gateway, cfg)  # type: ignore[arg-type]
        snapshot = source.wallet_snapshot()
        allowance = source.allowance_snapshot()
        self.assertTrue(snapshot.healthy)
        self.assertEqual(snapshot.detail, "")
        self.assertTrue(allowance.healthy)
        self.assertEqual(allowance.detail, "")

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
