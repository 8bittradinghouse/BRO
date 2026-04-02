from __future__ import annotations

import dataclasses
import math
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence

from .common import first_non_none, parse_float, utc_iso
from .gateway import BaseGateway, GatewayError, LiveClobGateway
from .models import FillEvent, OrderIntent


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


@dataclasses.dataclass(frozen=True)
class AllowanceSnapshot:
    allowance_usdc: float
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class NonceSnapshot:
    current_nonce: Optional[int]
    pending_nonces: tuple[int, ...]
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class PendingTxSnapshot:
    pending_count: int
    order_ids: tuple[str, ...]
    ts_utc: str
    source: str
    healthy: bool = True
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class ReconciliationResult:
    healthy: bool
    action: str
    reason: str
    detail: str = ""
    halt: bool = False
    ts_utc: str = ""


class LiveWalletTruthSource(Protocol):
    def wallet_snapshot(self) -> WalletSnapshot: ...

    def allowance_snapshot(self) -> AllowanceSnapshot: ...

    def nonce_snapshot(self) -> NonceSnapshot: ...

    def pending_tx_snapshot(self) -> PendingTxSnapshot: ...


class WalletDoctrineBase(ABC):
    """Capital authority contract for BRO wallet doctrine.

    Strategy may only emit intents. Wallet doctrine is the only layer that can
    approve, reduce, reject, or halt based on capital and wallet truth.
    """

    def __init__(self, cfg: Mapping[str, Any], *, mode: str) -> None:
        self.mode = str(mode or "paper").strip().lower() or "paper"
        self._cfg = dict(cfg or {})
        self._min_pol_gas_reserve = max(0.0, float(self._cfg.get("min_pol_gas_reserve", 0.1)))
        self._protected_reserve_usdc = max(0.0, float(self._cfg.get("protected_usdc_reserve", 0.0)))
        self._max_notional_per_order_usdc = max(0.0, float(self._cfg.get("max_notional_per_order_usdc", 250.0)))
        self._require_allowance = bool(self._cfg.get("require_allowance", True))
        self._expected_nonce_authority = str(self._cfg.get("nonce_authority", "tx_manager")).strip().lower() or "tx_manager"
        self._halt_on_reconcile_mismatch = bool(self._cfg.get("halt_on_reconcile_mismatch", True))
        self._reconcile_tolerance_usdc = max(1e-9, float(self._cfg.get("reconcile_tolerance_usdc", 1e-6)))

        self._nonce_authority_registered = ""
        self._pending_tx_provider: Optional[Callable[[], Mapping[str, Any]]] = None
        self._halted = False
        self._halt_reason = ""
        self._lock_seq = 0
        self._pending_locks: Dict[str, float] = {}
        self._order_locks: Dict[str, float] = {}
        self._net_usdc_outflow = 0.0
        now = utc_iso()
        self._wallet_snapshot = WalletSnapshot(
            address="",
            chain_id=0,
            pol_balance=0.0,
            usdc_balance=0.0,
            locked_usdc=0.0,
            protected_reserve_usdc=self._protected_reserve_usdc,
            deployable_usdc=0.0,
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=False,
            detail="wallet_snapshot_uninitialized",
        )
        self._allowance_snapshot = AllowanceSnapshot(
            allowance_usdc=0.0,
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=not self._require_allowance,
            detail="allowance_snapshot_uninitialized",
        )
        self._nonce_snapshot = NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=False,
            detail="nonce_snapshot_uninitialized",
        )
        self._pending_tx_snapshot = PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=True,
            detail="pending_tx_snapshot_uninitialized",
        )
        self._last_reconcile_ts_mono = time.monotonic()
        self._last_reconcile_result = ReconciliationResult(
            healthy=False,
            action="reject",
            reason="wallet_reconcile_not_run",
            detail="wallet doctrine initialized before first reconciliation",
            ts_utc=now,
        )

    def register_nonce_authority(self, authority_tag: str) -> None:
        self._nonce_authority_registered = str(authority_tag or "").strip().lower()

    def register_pending_tx_provider(self, provider: Callable[[], Mapping[str, Any]]) -> None:
        self._pending_tx_provider = provider

    def is_halted(self) -> bool:
        return bool(self._halted)

    def halt_reason(self) -> str:
        return str(self._halt_reason)

    def reconcile(self, *, pre_execution: bool = False) -> ReconciliationResult:
        result = self._refresh_truth(pre_execution=pre_execution)
        self._last_reconcile_ts_mono = time.monotonic()
        self._wallet_snapshot = dataclasses.replace(
            self._wallet_snapshot,
            locked_usdc=self._locked_usdc_total(),
            protected_reserve_usdc=self._protected_reserve_usdc,
            deployable_usdc=self._deployable_usdc(),
        )
        if result.halt and self._halt_on_reconcile_mismatch:
            self._halt(result.reason)
        elif self._deployable_usdc() < -self._reconcile_tolerance_usdc:
            reason = f"wallet_reconcile_negative_deployable:{self._deployable_usdc():.6f}"
            result = ReconciliationResult(
                healthy=False,
                action="halt" if self._halt_on_reconcile_mismatch else "reject",
                reason=reason,
                detail="locked capital exceeds deployable capital",
                halt=self._halt_on_reconcile_mismatch,
                ts_utc=utc_iso(),
            )
            if self._halt_on_reconcile_mismatch:
                self._halt(reason)
        self._last_reconcile_result = result
        return result

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "pending_lock_usdc": sum(self._pending_locks.values()),
            "order_lock_usdc": sum(self._order_locks.values()),
            "locked_usdc": self._locked_usdc_total(),
            "net_usdc_outflow": self._net_usdc_outflow,
            "deployable_usdc": self._deployable_usdc(),
            "min_pol_gas_reserve": self._min_pol_gas_reserve,
            "nonce_authority_expected": self._expected_nonce_authority,
            "nonce_authority_registered": self._nonce_authority_registered,
            "last_reconcile_ts_mono": self._last_reconcile_ts_mono,
            "last_reconcile_result": dataclasses.asdict(self._last_reconcile_result),
            "wallet_snapshot": dataclasses.asdict(self._wallet_snapshot),
            "allowance_snapshot": dataclasses.asdict(self._allowance_snapshot),
            "nonce_snapshot": dataclasses.asdict(self._nonce_snapshot),
            "pending_tx_snapshot": dataclasses.asdict(self._pending_tx_snapshot),
        }

    def authorize_intent(self, intent: OrderIntent) -> WalletAuthorization:
        if self._halted:
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_halted",
                detail=self._halt_reason,
                halt=True,
            )
        if not self._nonce_authority_registered:
            self._halt("nonce_authority_unregistered")
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_nonce_authority_missing",
                detail="transaction manager nonce authority not registered",
                halt=True,
            )
        if self._nonce_authority_registered != self._expected_nonce_authority:
            self._halt("nonce_authority_mismatch")
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_nonce_authority_mismatch",
                detail=f"expected={self._expected_nonce_authority}:registered={self._nonce_authority_registered}",
                halt=True,
            )

        reconcile = self.reconcile(pre_execution=True)
        if self._halted:
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_halted",
                detail=self._halt_reason,
                halt=True,
            )
        if not reconcile.healthy:
            return WalletAuthorization(
                allowed=False,
                action=reconcile.action if reconcile.action in {"reject", "halt"} else "reject",
                approved_size=0.0,
                reason=reconcile.reason,
                detail=reconcile.detail,
                halt=bool(reconcile.halt),
            )

        if self._wallet_snapshot.pol_balance < self._min_pol_gas_reserve:
            self._halt("insufficient_pol_gas_reserve")
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_gas_reserve_insufficient",
                detail=f"balance={self._wallet_snapshot.pol_balance:.6f}<min={self._min_pol_gas_reserve:.6f}",
                halt=True,
            )

        price = abs(float(intent.price))
        requested_size = max(0.0, float(intent.size))
        if requested_size <= 0.0 or price <= 0.0:
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_invalid_intent_size_or_price",
                detail=f"size={requested_size:.9f}:price={price:.9f}",
            )

        requested_notional = requested_size * price
        deployable = self._deployable_usdc()
        if deployable <= self._reconcile_tolerance_usdc:
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_deployable_insufficient",
                detail=f"deployable={deployable:.6f}",
            )

        approved_notional = requested_notional
        if self._max_notional_per_order_usdc > 0.0:
            approved_notional = min(approved_notional, self._max_notional_per_order_usdc)
        approved_notional = min(approved_notional, deployable)

        if self._require_allowance:
            approved_notional = min(approved_notional, max(0.0, float(self._allowance_snapshot.allowance_usdc)))
            if approved_notional <= self._reconcile_tolerance_usdc:
                return WalletAuthorization(
                    allowed=False,
                    action="reject",
                    approved_size=0.0,
                    reason="wallet_allowance_insufficient",
                    detail=f"allowance={self._allowance_snapshot.allowance_usdc:.6f}",
                )

        approved_size = approved_notional / price if price > 0 else 0.0
        if approved_size <= self._reconcile_tolerance_usdc:
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_approved_size_zero",
                detail=f"approved_notional={approved_notional:.6f}",
            )

        lock_notional = approved_size * price
        lock_id = self._next_lock_id()
        self._pending_locks[lock_id] = lock_notional
        self.reconcile(pre_execution=False)
        reduced = approved_size + self._reconcile_tolerance_usdc < requested_size
        return WalletAuthorization(
            allowed=True,
            action="reduce" if reduced else "approve",
            approved_size=approved_size,
            lock_id=lock_id,
            authorization_id=lock_id,
            reason="wallet_capital_authorized",
            detail=f"approved_notional={lock_notional:.6f}",
        )

    def confirm_submission(self, *, lock_id: str, order_id: str, order_open: bool) -> bool:
        lock_key = str(lock_id or "").strip()
        if not lock_key:
            self._halt("wallet_lock_id_missing_on_submit")
            return False
        if lock_key not in self._pending_locks:
            self._halt(f"wallet_lock_id_unknown:{lock_key}")
            return False

        lock_notional = float(self._pending_locks.pop(lock_key))
        if order_open:
            oid = str(order_id or "").strip()
            if not oid:
                self._halt("wallet_order_id_missing_for_open_lock")
                return False
            self._order_locks[oid] = self._order_locks.get(oid, 0.0) + lock_notional
        self.reconcile(pre_execution=False)
        return True

    def release_pending_lock(self, lock_id: str) -> None:
        key = str(lock_id or "").strip()
        if not key:
            return
        self._pending_locks.pop(key, None)
        self.reconcile(pre_execution=False)

    def release_order_lock(self, order_id: str) -> None:
        key = str(order_id or "").strip()
        if not key:
            return
        self._order_locks.pop(key, None)
        self.reconcile(pre_execution=False)

    def on_fill(self, fill: FillEvent) -> None:
        notional = abs(float(fill.price) * float(fill.size))
        side = str(fill.side or "").strip().upper()
        if side == "BUY":
            self._net_usdc_outflow += notional
        elif side == "SELL":
            self._net_usdc_outflow -= notional

        oid = str(fill.order_id or "").strip()
        if oid and oid in self._order_locks:
            current = self._order_locks.get(oid, 0.0)
            remaining = current - notional
            if remaining <= self._reconcile_tolerance_usdc:
                self._order_locks.pop(oid, None)
            else:
                self._order_locks[oid] = remaining
        self.reconcile(pre_execution=False)

    def _next_lock_id(self) -> str:
        self._lock_seq += 1
        return f"wallet-lock-{self._lock_seq}"

    def _locked_usdc_total(self) -> float:
        return sum(self._pending_locks.values()) + sum(self._order_locks.values())

    def _deployable_usdc(self) -> float:
        free = max(0.0, float(self._wallet_snapshot.usdc_balance) - float(self._protected_reserve_usdc))
        return free - self._locked_usdc_total()

    def _nonce_snapshot_from_provider(self) -> Optional[NonceSnapshot]:
        provider = self._pending_tx_provider
        if provider is None:
            return None
        try:
            payload = provider() or {}
        except Exception as exc:
            return NonceSnapshot(
                current_nonce=None,
                pending_nonces=tuple(),
                ts_utc=utc_iso(),
                source=f"{self.mode}_tx_manager",
                healthy=False,
                detail=f"nonce_provider_error:{exc}",
            )
        current_nonce_raw = parse_float(payload.get("current_nonce"))
        current_nonce = int(current_nonce_raw) if current_nonce_raw is not None and current_nonce_raw >= 0 else None
        raw_pending_nonces = payload.get("pending_nonces", ())
        pending_nonces: list[int] = []
        if isinstance(raw_pending_nonces, Sequence) and not isinstance(raw_pending_nonces, (str, bytes)):
            for value in raw_pending_nonces:
                parsed = parse_float(value)
                if parsed is None or parsed < 0:
                    continue
                pending_nonces.append(int(parsed))
        pending_nonces = sorted(set(pending_nonces))
        return NonceSnapshot(
            current_nonce=current_nonce,
            pending_nonces=tuple(pending_nonces),
            ts_utc=utc_iso(),
            source=str(first_non_none(payload.get("source"), f"{self.mode}_tx_manager")),
            healthy=bool(first_non_none(payload.get("healthy"), True)),
            detail=str(first_non_none(payload.get("detail"), "")),
        )

    def _pending_snapshot_from_provider(self) -> Optional[PendingTxSnapshot]:
        provider = self._pending_tx_provider
        if provider is None:
            return None
        try:
            payload = provider() or {}
        except Exception as exc:
            return PendingTxSnapshot(
                pending_count=0,
                order_ids=tuple(),
                ts_utc=utc_iso(),
                source=f"{self.mode}_tx_manager",
                healthy=False,
                detail=f"pending_tx_provider_error:{exc}",
            )
        pending_count = int(max(0, parse_float(payload.get("pending_count")) or 0.0))
        raw_ids = payload.get("order_ids", ())
        order_ids: tuple[str, ...]
        if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes)):
            order_ids = tuple(str(x) for x in raw_ids if str(x).strip())
        else:
            order_ids = tuple()
        return PendingTxSnapshot(
            pending_count=pending_count,
            order_ids=order_ids,
            ts_utc=utc_iso(),
            source=str(first_non_none(payload.get("source"), f"{self.mode}_tx_manager")),
            healthy=bool(first_non_none(payload.get("healthy"), True)),
            detail=str(first_non_none(payload.get("detail"), "")),
        )

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = str(reason or "wallet_halt")

    @abstractmethod
    def _refresh_truth(self, *, pre_execution: bool) -> ReconciliationResult:
        raise NotImplementedError


class PaperWalletDoctrine(WalletDoctrineBase):
    def __init__(self, cfg: Mapping[str, Any], *, mode: str = "paper") -> None:
        super().__init__(cfg, mode=mode)
        self._initial_usdc = max(0.0, float(self._cfg.get("paper_starting_usdc", 1000.0)))
        self._paper_pol_balance = max(0.0, float(self._cfg.get("paper_pol_balance", 10.0)))
        self._paper_allowance_usdc = max(0.0, float(self._cfg.get("paper_allowance_usdc", 1_000_000.0)))
        self.reconcile(pre_execution=False)

    def _refresh_truth(self, *, pre_execution: bool) -> ReconciliationResult:
        now = utc_iso()
        usdc_balance = self._initial_usdc - self._net_usdc_outflow
        self._wallet_snapshot = WalletSnapshot(
            address="paper-wallet",
            chain_id=137,
            pol_balance=self._paper_pol_balance,
            usdc_balance=max(0.0, usdc_balance),
            locked_usdc=self._locked_usdc_total(),
            protected_reserve_usdc=self._protected_reserve_usdc,
            deployable_usdc=0.0,
            ts_utc=now,
            source="paper_internal",
            healthy=usdc_balance >= -self._reconcile_tolerance_usdc,
            detail="" if usdc_balance >= -self._reconcile_tolerance_usdc else "paper_usdc_balance_negative",
        )
        self._allowance_snapshot = AllowanceSnapshot(
            allowance_usdc=self._paper_allowance_usdc,
            ts_utc=now,
            source="paper_internal",
            healthy=(not self._require_allowance) or self._paper_allowance_usdc > self._reconcile_tolerance_usdc,
            detail="",
        )
        self._nonce_snapshot = NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=now,
            source="paper_internal",
            healthy=bool(self._nonce_authority_registered),
            detail="" if self._nonce_authority_registered else "nonce_authority_unregistered",
        )
        provider_nonce = self._nonce_snapshot_from_provider()
        if provider_nonce is not None:
            self._nonce_snapshot = provider_nonce
        self._pending_tx_snapshot = (
            self._pending_snapshot_from_provider()
            or PendingTxSnapshot(
                pending_count=len(self._order_locks),
                order_ids=tuple(sorted(self._order_locks.keys())),
                ts_utc=now,
                source="paper_internal",
                healthy=True,
                detail="",
            )
        )
        if usdc_balance < -self._reconcile_tolerance_usdc:
            reason = f"wallet_reconcile_negative_balance:{usdc_balance:.6f}"
            return ReconciliationResult(
                healthy=False,
                action="halt" if self._halt_on_reconcile_mismatch else "reject",
                reason=reason,
                detail="paper balance dropped below zero",
                halt=self._halt_on_reconcile_mismatch,
                ts_utc=now,
            )
        return ReconciliationResult(
            healthy=True,
            action="continue",
            reason="wallet_reconcile_ok",
            ts_utc=now,
        )


class GatewayLiveWalletTruthSource:
    """Live wallet truth source backed by the authenticated CLOB gateway client."""

    def __init__(self, gateway: LiveClobGateway, cfg: Mapping[str, Any]) -> None:
        self._gateway = gateway
        self._cfg = dict(cfg or {})
        self._pol_balance_fallback = max(0.0, float(self._cfg.get("live_pol_balance_fallback", 1.0)))
        self._require_live_pol_balance_snapshot = bool(self._cfg.get("require_live_pol_balance_snapshot", False))

    @staticmethod
    def _extract_float(payload: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> Optional[float]:
        for path in paths:
            node: Any = payload
            ok = True
            for part in path:
                if not isinstance(node, Mapping) or part not in node:
                    ok = False
                    break
                node = node.get(part)
            if not ok:
                continue
            parsed = parse_float(node)
            if parsed is not None:
                return parsed
        return None

    def _balance_allowance_payload(self) -> Mapping[str, Any]:
        payload = self._gateway.get_collateral_balance_allowance()
        if not isinstance(payload, Mapping):
            raise GatewayError(f"invalid balance/allowance payload type: {type(payload).__name__}")
        return payload

    def wallet_snapshot(self) -> WalletSnapshot:
        payload = self._balance_allowance_payload()
        usdc = self._extract_float(
            payload,
            [
                ("balance",),
                ("balanceDecimal",),
                ("available",),
                ("availableBalance",),
                ("balance", "available"),
                ("balances", "balance"),
                ("data", "balance"),
            ],
        )
        if usdc is None:
            raise GatewayError("live_wallet_balance_missing")
        pol = self._extract_float(
            payload,
            [
                ("polBalance",),
                ("pol_balance",),
                ("gasBalance",),
                ("gas_balance",),
                ("balances", "pol"),
                ("balances", "gas"),
                ("data", "polBalance"),
                ("data", "gasBalance"),
            ],
        )
        pol_balance = self._pol_balance_fallback
        pol_healthy = not self._require_live_pol_balance_snapshot
        pol_detail = "live_pol_balance_fallback"
        if pol is not None:
            pol_balance = max(0.0, float(pol))
            pol_healthy = True
            pol_detail = ""
        return WalletSnapshot(
            address=self._gateway.wallet_address(),
            chain_id=self._gateway.chain_id(),
            pol_balance=pol_balance,
            usdc_balance=max(0.0, usdc),
            locked_usdc=0.0,
            protected_reserve_usdc=0.0,
            deployable_usdc=0.0,
            ts_utc=utc_iso(),
            source="live_gateway_balance_allowance",
            healthy=pol_healthy,
            detail=pol_detail,
        )

    def allowance_snapshot(self) -> AllowanceSnapshot:
        payload = self._balance_allowance_payload()
        allowance = self._extract_float(
            payload,
            [
                ("allowance",),
                ("allowanceDecimal",),
                ("approved",),
                ("allowance", "available"),
                ("allowances", "allowance"),
                ("data", "allowance"),
            ],
        )
        if allowance is None:
            return AllowanceSnapshot(
                allowance_usdc=0.0,
                ts_utc=utc_iso(),
                source="live_gateway_balance_allowance",
                healthy=False,
                detail="live_allowance_missing",
            )
        return AllowanceSnapshot(
            allowance_usdc=max(0.0, allowance),
            ts_utc=utc_iso(),
            source="live_gateway_balance_allowance",
            healthy=True,
            detail="",
        )

    def nonce_snapshot(self) -> NonceSnapshot:
        # py-clob-client does not currently expose a nonce read endpoint.
        return NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=utc_iso(),
            source="live_gateway_unavailable",
            healthy=False,
            detail="nonce_snapshot_unavailable",
        )

    def pending_tx_snapshot(self) -> PendingTxSnapshot:
        open_orders = self._gateway.get_open_orders()
        order_ids = tuple(sorted({str(o.order_id) for o in open_orders if str(o.order_id).strip()}))
        return PendingTxSnapshot(
            pending_count=len(order_ids),
            order_ids=order_ids,
            ts_utc=utc_iso(),
            source="live_gateway_open_orders",
            healthy=True,
            detail="",
        )


class LiveWalletDoctrine(WalletDoctrineBase):
    def __init__(self, cfg: Mapping[str, Any], *, truth_source: LiveWalletTruthSource, mode: str = "live") -> None:
        super().__init__(cfg, mode=mode)
        self._truth_source = truth_source
        self._expected_chain_id = int(self._cfg.get("expected_chain_id", 137))
        self._expected_wallet_address = str(self._cfg.get("expected_wallet_address", "")).strip().lower()
        self._require_live_pol_balance_snapshot = bool(self._cfg.get("require_live_pol_balance_snapshot", False))
        self._require_live_nonce_snapshot = bool(self._cfg.get("require_live_nonce_snapshot", False))
        self._require_live_nonce_value = bool(self._cfg.get("require_live_nonce_value", False))
        self._require_live_pending_tx_snapshot = bool(self._cfg.get("require_live_pending_tx_snapshot", False))
        self._max_live_reconcile_mismatch_count = max(
            1,
            int(self._cfg.get("max_live_reconcile_mismatch_count", 2)),
        )
        self._live_balance_baseline_usdc: Optional[float] = None
        self._live_mismatch_count = 0
        self.reconcile(pre_execution=False)

    def _refresh_truth(self, *, pre_execution: bool) -> ReconciliationResult:
        now = utc_iso()
        try:
            self._wallet_snapshot = self._truth_source.wallet_snapshot()
            self._allowance_snapshot = self._truth_source.allowance_snapshot()
            self._nonce_snapshot = self._truth_source.nonce_snapshot()
            self._pending_tx_snapshot = self._truth_source.pending_tx_snapshot()
            provider_pending = self._pending_snapshot_from_provider()
            if provider_pending is not None:
                self._pending_tx_snapshot = provider_pending
            provider_nonce = self._nonce_snapshot_from_provider()
            if provider_nonce is not None and (not self._nonce_snapshot.healthy or self._nonce_snapshot.current_nonce is None):
                self._nonce_snapshot = provider_nonce
        except Exception as exc:
            return ReconciliationResult(
                healthy=False,
                action="halt",
                reason="live_wallet_truth_unavailable",
                detail=str(exc),
                halt=True,
                ts_utc=now,
            )

        address = str(self._wallet_snapshot.address or "").strip().lower()
        if self._expected_wallet_address and address != self._expected_wallet_address:
            return ReconciliationResult(
                healthy=False,
                action="halt",
                reason="wallet_address_mismatch",
                detail=f"expected={self._expected_wallet_address}:observed={address}",
                halt=True,
                ts_utc=now,
            )

        if self._expected_chain_id > 0 and int(self._wallet_snapshot.chain_id) != self._expected_chain_id:
            return ReconciliationResult(
                healthy=False,
                action="halt",
                reason="wallet_chain_id_mismatch",
                detail=f"expected={self._expected_chain_id}:observed={self._wallet_snapshot.chain_id}",
                halt=True,
                ts_utc=now,
            )

        if self._require_live_pol_balance_snapshot and not self._wallet_snapshot.healthy:
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_snapshot_unhealthy",
                detail=self._wallet_snapshot.detail or "live_wallet_snapshot_unhealthy",
                ts_utc=now,
            )

        if self._require_allowance and not self._allowance_snapshot.healthy:
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_allowance_snapshot_unhealthy",
                detail=self._allowance_snapshot.detail,
                ts_utc=now,
            )

        if self._require_live_nonce_snapshot and not self._nonce_snapshot.healthy:
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_nonce_snapshot_unhealthy",
                detail=self._nonce_snapshot.detail,
                ts_utc=now,
            )
        if self._require_live_nonce_snapshot and self._require_live_nonce_value and self._nonce_snapshot.current_nonce is None:
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_nonce_value_missing",
                detail=self._nonce_snapshot.detail or "live_nonce_value_missing",
                ts_utc=now,
            )

        if self._require_live_pending_tx_snapshot and not self._pending_tx_snapshot.healthy:
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_pending_tx_snapshot_unhealthy",
                detail=self._pending_tx_snapshot.detail,
                ts_utc=now,
            )

        observed = float(self._wallet_snapshot.usdc_balance)
        if not math.isfinite(observed):
            return ReconciliationResult(
                healthy=False,
                action="halt",
                reason="wallet_live_balance_invalid",
                detail=f"observed={self._wallet_snapshot.usdc_balance!r}",
                halt=True,
                ts_utc=now,
            )
        if self._live_balance_baseline_usdc is None:
            self._live_balance_baseline_usdc = observed
        expected = float(self._live_balance_baseline_usdc - self._net_usdc_outflow)
        delta = observed - expected
        if abs(delta) > self._reconcile_tolerance_usdc:
            self._live_mismatch_count += 1
            if self._live_mismatch_count >= self._max_live_reconcile_mismatch_count:
                return ReconciliationResult(
                    healthy=False,
                    action="halt" if self._halt_on_reconcile_mismatch else "reject",
                    reason="wallet_reconcile_mismatch",
                    detail=(
                        f"observed={observed:.6f}:expected={expected:.6f}:"
                        f"delta={delta:.6f}:count={self._live_mismatch_count}"
                    ),
                    halt=self._halt_on_reconcile_mismatch,
                    ts_utc=now,
                )
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_reconcile_mismatch_pending",
                detail=(
                    f"observed={observed:.6f}:expected={expected:.6f}:"
                    f"delta={delta:.6f}:count={self._live_mismatch_count}"
                ),
                ts_utc=now,
            )
        self._live_mismatch_count = 0
        return ReconciliationResult(
            healthy=True,
            action="continue",
            reason="wallet_reconcile_ok",
            ts_utc=now,
        )


def create_wallet_doctrine(
    cfg: Mapping[str, Any],
    *,
    mode: str,
    gateway: Optional[BaseGateway] = None,
) -> WalletDoctrineBase:
    selected_mode = str(mode or "paper").strip().lower() or "paper"
    if selected_mode == "live":
        if not isinstance(gateway, LiveClobGateway):
            raise ValueError("live wallet doctrine requires LiveClobGateway")
        truth_source = GatewayLiveWalletTruthSource(gateway, cfg)
        return LiveWalletDoctrine(cfg, truth_source=truth_source, mode="live")
    return PaperWalletDoctrine(cfg, mode="paper")


class WalletDoctrine(PaperWalletDoctrine):
    """Backwards-compatible paper doctrine alias."""

    def __init__(self, cfg: Mapping[str, Any], *, mode: str) -> None:
        selected_mode = str(mode or "paper").strip().lower() or "paper"
        if selected_mode != "paper":
            raise ValueError("WalletDoctrine alias supports paper mode only; use create_wallet_doctrine for live")
        super().__init__(cfg, mode="paper")
