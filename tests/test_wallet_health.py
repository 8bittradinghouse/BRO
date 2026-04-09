import unittest

from prodesk.wallet.wallet_health import build_wallet_health_contract
from prodesk.wallet.wallet_types import (
    AUTHORITY_CLASS_LIVE,
    AUTHORITY_CLASS_LOCAL,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
)


class WalletHealthContractTests(unittest.TestCase):
    def test_health_contract_ignores_deprecated_top_level_surfaces(self) -> None:
        status = {
            "mode": "live",
            "halted": False,
            "authority_status_class": "authoritative",
            "authoritative_refresh_completed": True,
            "startup_authority_ready": True,
            "order_capable_live": True,
            "locked_usdc": 10.0,
            "deployable_usdc": 90.0,
            "min_pol_gas_reserve": 0.1,
            "integrity_tripwire_reconcile_state": {"healthy": True, "scope": "integrity_tripwire"},
            "canonical_live_wallet_truth": {
                "wallet_snapshot": {
                    "pol_balance": 2.0,
                    "usdc_balance": 100.0,
                    "protected_reserve_usdc": 0.0,
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
                "allowance_snapshot": {
                    "healthy": True,
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
                "nonce_snapshot": {
                    "healthy": True,
                    "current_nonce": 7,
                    "source": "canonical_live_wallet_truth",
                    "detail": "",
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
                "pending_wallet_tx_snapshot": {
                    "healthy": True,
                    "source": "canonical_live_wallet_truth",
                    "detail": "",
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
            },
            # Deprecated compatibility aliases intentionally conflict and must not be consumed.
            "wallet_snapshot": {"pol_balance": 0.0, "usdc_balance": 0.0},
            "allowance_snapshot": {"healthy": False},
            "nonce_snapshot": {"healthy": False, "current_nonce": None},
            "pending_tx_snapshot": {"healthy": False},
        }
        contract = build_wallet_health_contract(status=status)
        self.assertTrue(bool(contract.get("wallet_health_ok")))
        self.assertTrue(bool(contract.get("canonical_live_nonce_available")))
        self.assertTrue(bool(contract.get("canonical_live_pending_wallet_tx_available")))

    def test_canonical_live_availability_requires_truth_domain_and_authority_class(self) -> None:
        status = {
            "mode": "live",
            "halted": False,
            "authority_status_class": "authoritative",
            "authoritative_refresh_completed": True,
            "startup_authority_ready": True,
            "order_capable_live": True,
            "locked_usdc": 0.0,
            "deployable_usdc": 100.0,
            "min_pol_gas_reserve": 0.1,
            "integrity_tripwire_reconcile_state": {"healthy": True, "scope": "integrity_tripwire"},
            "canonical_live_wallet_truth": {
                "wallet_snapshot": {
                    "pol_balance": 2.0,
                    "usdc_balance": 100.0,
                    "protected_reserve_usdc": 0.0,
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
                "allowance_snapshot": {
                    "healthy": True,
                    "truth_domain": TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                    "authority_class": AUTHORITY_CLASS_LIVE,
                },
                "nonce_snapshot": {
                    "healthy": True,
                    "current_nonce": 9,
                    "source": "canonical_live_wallet_truth",
                    "detail": "",
                    "truth_domain": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    "authority_class": AUTHORITY_CLASS_LOCAL,
                },
                "pending_wallet_tx_snapshot": {
                    "healthy": True,
                    "source": "canonical_live_wallet_truth",
                    "detail": "",
                    "truth_domain": TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    "authority_class": AUTHORITY_CLASS_LOCAL,
                },
            },
        }
        contract = build_wallet_health_contract(status=status)
        self.assertFalse(bool(contract.get("canonical_live_nonce_available")))
        self.assertFalse(bool(contract.get("canonical_live_pending_wallet_tx_available")))
        reasons = list(contract.get("wallet_health_reasons") or [])
        self.assertIn("canonical_live_nonce_unavailable", reasons)
        self.assertIn("canonical_live_pending_wallet_tx_unavailable", reasons)


if __name__ == "__main__":
    unittest.main()
