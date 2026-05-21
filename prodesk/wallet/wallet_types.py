from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Optional, Protocol


TRUTH_DOMAIN_CANONICAL_LIVE_WALLET = "canonical_live_wallet_truth"
TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE = "local_tx_lifecycle_state"
TRUTH_DOMAIN_OPEN_ORDER_STATE = "open_order_state"
TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE = "bootstrap_non_authoritative"
TRUTH_DOMAIN_INTEGRITY_TRIPWIRE_RECONCILE = "integrity_tripwire_reconcile_state"
TRUTH_DOMAIN_PAPER_WALLET = "paper_wallet_truth"

LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX = "on_chain_pending_wallet_tx"
LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX = "exchange_intent_local_tx_lifecycle"

AUTHORITY_CLASS_LIVE = "live"
AUTHORITY_CLASS_LOCAL = "local"
AUTHORITY_CLASS_DERIVED = "derived"
AUTHORITY_CLASS_BOOTSTRAP = "bootstrap"
AUTHORITY_CLASS_DEPRECATED = "deprecated"


@dataclasses.dataclass(frozen=True)
class WalletAuthorization:
    allowed: bool
    action: str
    approved_size: float
    lock_id: str = ""
    authorization_id: str = ""
    reason: str = "ok"
    detail: str = ""
    halt: bool = False


@dataclasses.dataclass(frozen=True)
class WalletSnapshot:
    address: str
    chain_id: int
    pol_balance: float
    usdc_balance: float
    locked_usdc: float
    protected_reserve_usdc: float
    deployable_usdc: float
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""
    truth_domain: str = TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
    authority_class: str = AUTHORITY_CLASS_LIVE


@dataclasses.dataclass(frozen=True)
class AllowanceSnapshot:
    allowance_usdc: float
    ts_utc: str
    source: str
    target_identity_verified: bool = False
    matched_spender_targets: tuple[str, ...] = tuple()
    required_spender_targets: tuple[str, ...] = tuple()
    healthy: bool = True
    detail: str = ""
    truth_domain: str = TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
    authority_class: str = AUTHORITY_CLASS_LIVE


@dataclasses.dataclass(frozen=True)
class NonceSnapshot:
    current_nonce: Optional[int]
    pending_nonces: tuple[int, ...]
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""
    truth_domain: str = TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
    authority_class: str = AUTHORITY_CLASS_LIVE


@dataclasses.dataclass(frozen=True)
class PendingTxSnapshot:
    pending_count: int
    order_ids: tuple[str, ...]
    ts_utc: str
    source: str
    tx_ids: tuple[str, ...] = tuple()
    exchange_order_ids: tuple[str, ...] = tuple()
    exchange_client_order_ids: tuple[str, ...] = tuple()
    healthy: bool = True
    detail: str = ""
    truth_domain: str = TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
    authority_class: str = AUTHORITY_CLASS_LIVE
    lifecycle_plane: str = LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX


@dataclasses.dataclass(frozen=True)
class OpenOrderStateSnapshot:
    open_count: int
    order_ids: tuple[str, ...]
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""
    truth_domain: str = TRUTH_DOMAIN_OPEN_ORDER_STATE
    authority_class: str = AUTHORITY_CLASS_DERIVED


@dataclasses.dataclass(frozen=True)
class ReconciliationResult:
    healthy: bool
    action: str
    reason: str
    detail: str = ""
    halt: bool = False
    ts_utc: str = ""


@dataclasses.dataclass(frozen=True)
class WalletRedemptionRequest:
    market_id: str
    token_id: str
    settlement_side: str
    size_shares: float
    settlement_price: float
    expected_payout_usd: float
    payout_symbol: str
    ts_utc: str
    metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class WalletRedemptionResult:
    successful: bool
    action: str
    reason: str
    detail: str = ""
    tx_hash: str = ""
    receipt_confirmed: bool = False
    payout_usdc: float = 0.0
    settlement_applied: bool = False
    ts_utc: str = ""
    metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)


class LiveWalletTruthSource(Protocol):
    def wallet_snapshot(self) -> WalletSnapshot: ...

    def allowance_snapshot(self) -> AllowanceSnapshot: ...

    def nonce_snapshot(self) -> NonceSnapshot: ...

    def pending_tx_snapshot(self) -> PendingTxSnapshot: ...

    def open_order_state_snapshot(self) -> OpenOrderStateSnapshot: ...


class WalletRedemptionExecutor(Protocol):
    def __call__(self, request: WalletRedemptionRequest) -> WalletRedemptionResult | Mapping[str, Any]: ...
