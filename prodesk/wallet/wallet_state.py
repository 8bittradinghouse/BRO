from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .wallet_types import AllowanceSnapshot, NonceSnapshot, PendingTxSnapshot, ReconciliationResult, WalletSnapshot


@dataclass
class WalletRuntimeState:
    wallet_snapshot: WalletSnapshot
    allowance_snapshot: AllowanceSnapshot
    nonce_snapshot: NonceSnapshot
    pending_tx_snapshot: PendingTxSnapshot
    last_reconcile_result: ReconciliationResult
    halted: bool
    halt_reason: str
    net_usdc_outflow: float
    pending_locks: Dict[str, float]
    order_locks: Dict[str, float]
