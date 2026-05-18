from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Tuple


@dataclasses.dataclass(frozen=True)
class WalletConfig:
    min_pol_gas_reserve: float
    gas_reserve_target_pol: float
    protected_reserve_usdc: float
    max_notional_per_order_usdc: float
    require_allowance: bool
    nonce_authority: str
    halt_on_reconcile_mismatch: bool
    reconcile_tolerance_usdc: float
    reservation_mismatch_tolerance_usdc: float
    chain: str
    gas_asset_symbol: str
    stable_asset_symbol: str
    approval_spender_targets: Tuple[str, ...]
    active_wallet_address_source: str
    treasury_mode: str
    treasury_wallet_address: str


def load_wallet_config(cfg: Mapping[str, Any]) -> WalletConfig:
    raw = dict(cfg or {})
    targets_raw = raw.get("approval_spender_targets", ())
    targets: list[str] = []
    if isinstance(targets_raw, (list, tuple)):
        for item in targets_raw:
            text = str(item or "").strip()
            if text:
                targets.append(text)
    return WalletConfig(
        min_pol_gas_reserve=max(0.0, float(raw.get("min_pol_gas_reserve", 0.1))),
        gas_reserve_target_pol=max(0.0, float(raw.get("gas_reserve_target_pol", raw.get("min_pol_gas_reserve", 0.1)))),
        protected_reserve_usdc=max(0.0, float(raw.get("protected_usdc_reserve", 0.0))),
        max_notional_per_order_usdc=max(0.0, float(raw.get("max_notional_per_order_usdc", 250.0))),
        require_allowance=bool(raw.get("require_allowance", True)),
        nonce_authority=str(raw.get("nonce_authority", "tx_manager") or "tx_manager").strip().lower(),
        halt_on_reconcile_mismatch=bool(raw.get("halt_on_reconcile_mismatch", True)),
        reconcile_tolerance_usdc=max(1e-9, float(raw.get("reconcile_tolerance_usdc", 1e-6))),
        reservation_mismatch_tolerance_usdc=max(
            1e-9,
            float(raw.get("reservation_mismatch_tolerance_usdc", raw.get("reconcile_tolerance_usdc", 1e-6))),
        ),
        chain=str(raw.get("chain", "polygon") or "polygon").strip().lower(),
        gas_asset_symbol=str(raw.get("gas_asset_symbol", "POL") or "POL").strip(),
        stable_asset_symbol=str(raw.get("stable_asset_symbol", "pUSD") or "pUSD").strip(),
        approval_spender_targets=tuple(targets),
        active_wallet_address_source=str(raw.get("active_wallet_address_source", "auth.funder") or "auth.funder").strip(),
        treasury_mode=str(raw.get("treasury_mode", "logical") or "logical").strip().lower(),
        treasury_wallet_address=str(raw.get("treasury_wallet_address", "") or "").strip(),
    )
