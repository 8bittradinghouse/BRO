import json
import unittest
from pathlib import Path

from prodesk.gateway import GatewayError
from prodesk.models import LiveOrder
from prodesk.wallet.wallet_config import load_wallet_config
from prodesk.wallet.wallet_provider import GatewayLiveWalletTruthSource
from prodesk.wallet.web3_adapter import create_wallet_web3_adapter
from prodesk.wallet.wallet_truth_policy import (
    PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
    PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
)
from prodesk.wallet.wallet_types import (
    AUTHORITY_CLASS_DERIVED,
    AUTHORITY_CLASS_LIVE,
    LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
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


class _StubWeb3Eth:
    def __init__(self, latest_nonce: int, pending_nonce: int) -> None:
        self._latest_nonce = latest_nonce
        self._pending_nonce = pending_nonce

    def get_transaction_count(self, _address: str, block_identifier: str) -> int:
        return int(self._pending_nonce if str(block_identifier) == "pending" else self._latest_nonce)


class _StubWeb3Manager:
    def __init__(self, txpool_payload: dict | None = None, *, fail_txpool: bool = False) -> None:
        self._txpool_payload = dict(txpool_payload or {})
        self._fail_txpool = bool(fail_txpool)

    def request_blocking(self, method: str, _params: list) -> dict:
        if method != "txpool_content":
            raise RuntimeError(f"unexpected_method:{method}")
        if self._fail_txpool:
            raise RuntimeError("txpool disabled")
        return dict(self._txpool_payload)


class _StubWeb3Client:
    def __init__(
        self,
        *,
        latest_nonce: int,
        pending_nonce: int,
        txpool_payload: dict | None = None,
        fail_txpool: bool = False,
    ) -> None:
        self.eth = _StubWeb3Eth(latest_nonce, pending_nonce)
        self.manager = _StubWeb3Manager(txpool_payload, fail_txpool=fail_txpool)


def _fixture_payload(name: str) -> dict:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "wallet_provider" / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class WalletProviderTests(unittest.TestCase):
    def test_web3_adapter_switches_to_failover_after_three_high_latency_primary_calls(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
                "web3_failover_max_latency_ms": 800.0,
                "web3_failover_consecutive_high_latency": 3,
                "web3_failover_sticky_seconds": 300.0,
            }
        )
        adapter = create_wallet_web3_adapter(cfg)
        adapter.record_rpc_result(latency_ms=900.0, ok=True, provider_name="primary", now_monotonic=10.0)
        adapter.record_rpc_result(latency_ms=901.0, ok=True, provider_name="primary", now_monotonic=11.0)
        health = adapter.record_rpc_result(latency_ms=902.0, ok=True, provider_name="primary", now_monotonic=12.0)

        self.assertEqual(health.active_provider, "failover")
        self.assertTrue(health.failover_active)
        self.assertEqual(health.last_switch_reason, "primary_high_latency_threshold")
        self.assertTrue(health.sticky_failover_active)

    def test_web3_adapter_switches_to_failover_on_primary_error(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
            }
        )
        adapter = create_wallet_web3_adapter(cfg)
        health = adapter.record_rpc_result(
            latency_ms=None,
            ok=False,
            error="connection_reset",
            provider_name="primary",
            now_monotonic=20.0,
        )

        self.assertEqual(health.active_provider, "failover")
        self.assertEqual(health.last_switch_reason, "primary_error:connection_reset")

    def test_web3_adapter_sticky_failover_blocks_early_primary_restore(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
                "web3_failover_sticky_seconds": 300.0,
            }
        )
        adapter = create_wallet_web3_adapter(cfg)
        adapter.record_rpc_result(latency_ms=None, ok=False, error="boom", provider_name="primary", now_monotonic=5.0)

        self.assertFalse(adapter.attempt_primary_restore(now_monotonic=250.0))
        self.assertTrue(adapter.attempt_primary_restore(now_monotonic=306.0))
        self.assertEqual(adapter.health_contract(now_monotonic=306.0).active_provider, "primary")

    def test_web3_adapter_builds_active_client_through_injected_factory(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
            }
        )
        seen: list[str] = []

        def _factory(rpc_url: str) -> dict:
            seen.append(str(rpc_url))
            return {"rpc_url": rpc_url}

        adapter = create_wallet_web3_adapter(cfg, web3_factory=_factory)
        client = adapter.build_active_client()

        self.assertEqual(client["rpc_url"], "https://quicknode.example")
        self.assertEqual(seen, ["https://quicknode.example"])

    def test_web3_adapter_dynamic_gas_band_interpolates_normal_range(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_gas_normal_min_multiplier": 1.2,
                "web3_gas_normal_max_multiplier": 1.5,
                "web3_gas_spike_max_multiplier": 2.0,
            }
        )
        adapter = create_wallet_web3_adapter(cfg)
        band = adapter.dynamic_gas_band(
            base_fee_wei=100,
            priority_fee_wei=10,
            congestion_ratio=0.5,
        )

        self.assertEqual(band["multiplier_class"], "normal")
        self.assertAlmostEqual(float(band["applied_multiplier"]), 1.35, places=6)
        self.assertEqual(int(band["max_priority_fee_per_gas_wei"]), 14)
        self.assertEqual(int(band["max_fee_per_gas_wei"]), 145)

    def test_web3_adapter_dynamic_gas_band_uses_spike_cap(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_gas_normal_min_multiplier": 1.2,
                "web3_gas_normal_max_multiplier": 1.5,
                "web3_gas_spike_max_multiplier": 2.0,
            }
        )
        adapter = create_wallet_web3_adapter(cfg)
        band = adapter.dynamic_gas_band(
            base_fee_wei=100,
            priority_fee_wei=10,
            spike=True,
        )

        self.assertEqual(band["multiplier_class"], "spike")
        self.assertAlmostEqual(float(band["applied_multiplier"]), 2.0, places=6)
        self.assertEqual(int(band["max_priority_fee_per_gas_wei"]), 20)
        self.assertEqual(int(band["max_fee_per_gas_wei"]), 210)

    def test_web3_adapter_normalizes_redemption_receipt(self) -> None:
        cfg = load_wallet_config({})
        adapter = create_wallet_web3_adapter(cfg)
        receipt = adapter.normalize_redemption_receipt(
            {
                "transactionHash": "0xabc",
                "status": 1,
                "payout_amount_usdc": 12.5,
                "detail": "receipt_ok",
            }
        )

        self.assertTrue(bool(receipt["receipt_confirmed"]))
        self.assertEqual(receipt["tx_hash"], "0xabc")
        self.assertAlmostEqual(float(receipt["payout_usdc"]), 12.5, places=9)
        self.assertEqual(receipt["detail"], "receipt_ok")

    def test_web3_adapter_builds_canonical_nonce_snapshot_from_transaction_counts(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
            }
        )
        adapter = create_wallet_web3_adapter(
            cfg,
            web3_factory=lambda _rpc_url: _StubWeb3Client(latest_nonce=7, pending_nonce=9, fail_txpool=True),
        )
        snapshot = adapter.canonical_nonce_snapshot("0x1111111111111111111111111111111111111111")

        self.assertTrue(snapshot.healthy)
        self.assertEqual(snapshot.current_nonce, 9)
        self.assertEqual(snapshot.pending_nonces, (7, 8))
        self.assertIn("transaction_count_delta_only", snapshot.detail)

    def test_web3_adapter_builds_canonical_pending_snapshot_from_txpool(self) -> None:
        cfg = load_wallet_config(
            {
                "web3_primary_rpc_url": "https://quicknode.example",
                "web3_failover_rpc_url": "https://alchemy.example",
            }
        )
        txpool_payload = {
            "pending": {
                "0x1111111111111111111111111111111111111111": {
                    "0x7": {"hash": "0xaaa", "nonce": "0x7"},
                    "0x8": {"hash": "0xbbb", "nonce": "0x8"},
                }
            }
        }
        adapter = create_wallet_web3_adapter(
            cfg,
            web3_factory=lambda _rpc_url: _StubWeb3Client(
                latest_nonce=7,
                pending_nonce=9,
                txpool_payload=txpool_payload,
            ),
        )
        snapshot = adapter.canonical_pending_tx_snapshot("0x1111111111111111111111111111111111111111")

        self.assertTrue(snapshot.healthy)
        self.assertEqual(snapshot.pending_count, 2)
        self.assertEqual(snapshot.tx_ids, ("0xaaa", "0xbbb"))
        self.assertEqual(snapshot.exchange_order_ids, tuple())
        self.assertEqual(snapshot.order_ids, tuple())
        self.assertIn("web3_pending_state_ok", snapshot.detail)

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

    def test_allowance_snapshot_requires_configured_spender_target_identity(self) -> None:
        gateway = _StubLiveGateway(_fixture_payload("live_balance_allowance_primary.json"))
        source = GatewayLiveWalletTruthSource(
            gateway,
            {"approval_spender_targets": ["0x2222222222222222222222222222222222222222"]},
        )  # type: ignore[arg-type]

        allowance_snapshot = source.allowance_snapshot()

        self.assertFalse(allowance_snapshot.healthy)
        self.assertFalse(allowance_snapshot.target_identity_verified)
        self.assertIn("live_allowance_target_identity_unverified", allowance_snapshot.detail)

    def test_allowance_snapshot_verifies_bounded_spender_target_mapping(self) -> None:
        gateway = _StubLiveGateway(
            {
                "balance": 100.25,
                "allowances": {
                    "0x2222222222222222222222222222222222222222": {
                        "allowance": "80.5",
                    }
                },
                "polBalance": "5.0",
            }
        )
        source = GatewayLiveWalletTruthSource(
            gateway,
            {"approval_spender_targets": ["0x2222222222222222222222222222222222222222"]},
        )  # type: ignore[arg-type]

        allowance_snapshot = source.allowance_snapshot()

        self.assertTrue(allowance_snapshot.healthy)
        self.assertTrue(allowance_snapshot.target_identity_verified)
        self.assertEqual(
            allowance_snapshot.matched_spender_targets,
            ("0x2222222222222222222222222222222222222222",),
        )
        self.assertAlmostEqual(allowance_snapshot.allowance_usdc, 80.5, places=9)

    def test_live_truth_source_uses_web3_adapter_for_nonce_and_pending(self) -> None:
        gateway = _StubLiveGateway(_fixture_payload("live_balance_allowance_primary.json"))
        cfg = {
            "web3_primary_rpc_url": "https://quicknode.example",
            "web3_failover_rpc_url": "https://alchemy.example",
            "expected_wallet_address": "0x1111111111111111111111111111111111111111",
        }
        adapter = create_wallet_web3_adapter(
            load_wallet_config(cfg),
            web3_factory=lambda _rpc_url: _StubWeb3Client(latest_nonce=5, pending_nonce=6, fail_txpool=True),
        )
        source = GatewayLiveWalletTruthSource(gateway, cfg, web3_adapter=adapter)  # type: ignore[arg-type]

        nonce_snapshot = source.nonce_snapshot()
        pending_snapshot = source.pending_tx_snapshot()
        provider_health = source.web3_provider_health_status()

        self.assertTrue(nonce_snapshot.healthy)
        self.assertEqual(nonce_snapshot.current_nonce, 6)
        self.assertTrue(pending_snapshot.healthy)
        self.assertEqual(pending_snapshot.pending_count, 1)
        self.assertEqual(pending_snapshot.tx_ids, tuple())
        self.assertTrue(bool(provider_health))

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
        self.assertEqual(pending_snapshot.tx_ids, tuple())
        self.assertEqual(pending_snapshot.exchange_order_ids, tuple())
        self.assertEqual(pending_snapshot.lifecycle_plane, LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX)
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
