from __future__ import annotations

import dataclasses
import math
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from ..common import first_non_none, parse_float, utc_iso
from ..gateway import BaseGateway, GatewayError, LiveClobGateway
from ..models import FillEvent, OrderIntent
from .wallet_config import load_wallet_config
from .wallet_health import build_wallet_health_contract
from .wallet_provider import GatewayLiveWalletTruthSource
from .wallet_reservations import WalletReservations
from .wallet_tx_state import nonce_snapshot_from_provider, pending_tx_snapshot_from_provider
from .wallet_types import (
    AUTHORITY_CLASS_BOOTSTRAP,
    AUTHORITY_CLASS_DERIVED,
    AUTHORITY_CLASS_LIVE,
    AUTHORITY_CLASS_LOCAL,
    AllowanceSnapshot,
    LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
    LiveWalletTruthSource,
    NonceSnapshot,
    OpenOrderStateSnapshot,
    PendingTxSnapshot,
    ReconciliationResult,
    TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
    TRUTH_DOMAIN_INTEGRITY_TRIPWIRE_RECONCILE,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
    TRUTH_DOMAIN_OPEN_ORDER_STATE,
    TRUTH_DOMAIN_PAPER_WALLET,
    WalletAuthorization,
    WalletRedemptionExecutor,
    WalletRedemptionRequest,
    WalletRedemptionResult,
    WalletSnapshot,
)
from .web3_adapter import WalletWeb3Adapter


WALLET_TRUTH_EXCEPTIONS = (
    OSError,
    TimeoutError,
    ConnectionError,
    RuntimeError,
    TypeError,
    ValueError,
    KeyError,
    AttributeError,
)


class WalletDoctrineBase(ABC):
    """Capital authority contract for BRO wallet doctrine.

    Strategy may only emit intents. Wallet doctrine is the only layer that can
    approve, reduce, reject, or halt based on capital and wallet truth.
    """

    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        mode: str,
        auth_cfg: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.mode = str(mode or "paper").strip().lower() or "paper"
        self._cfg = dict(cfg or {})
        self._auth_cfg = dict(auth_cfg or {})
        self._wallet_cfg = load_wallet_config(self._cfg)
        self._min_pol_gas_reserve = float(self._wallet_cfg.min_pol_gas_reserve)
        self._gas_reserve_target_pol = float(self._wallet_cfg.gas_reserve_target_pol)
        self._gas_reserve_fail_floor_usd = float(self._wallet_cfg.gas_reserve_fail_floor_usd)
        self._gas_reserve_target_usd = float(self._wallet_cfg.gas_reserve_target_usd)
        self._gas_asset_price_usd_hint = float(self._wallet_cfg.gas_asset_price_usd_hint)
        self._protected_reserve_usdc = float(self._wallet_cfg.protected_reserve_usdc)
        self._max_notional_per_order_usdc = float(self._wallet_cfg.max_notional_per_order_usdc)
        self._require_allowance = bool(self._wallet_cfg.require_allowance)
        self._expected_nonce_authority = str(self._wallet_cfg.nonce_authority)
        self._halt_on_reconcile_mismatch = bool(self._wallet_cfg.halt_on_reconcile_mismatch)
        self._reconcile_tolerance_usdc = float(self._wallet_cfg.reconcile_tolerance_usdc)
        self._reservation_mismatch_tolerance_usdc = float(self._wallet_cfg.reservation_mismatch_tolerance_usdc)
        self._approval_spender_targets = tuple(self._wallet_cfg.approval_spender_targets)
        self._wallet_chain = str(self._wallet_cfg.chain)
        self._gas_asset_symbol = str(self._wallet_cfg.gas_asset_symbol)
        self._stable_asset_symbol = str(self._wallet_cfg.stable_asset_symbol)
        self._treasury_mode = str(self._wallet_cfg.treasury_mode)
        self._treasury_wallet_address = str(self._wallet_cfg.treasury_wallet_address)
        self._active_wallet_address_source = str(self._wallet_cfg.active_wallet_address_source)
        self._live_order_submission_enabled = bool(self._auth_cfg.get("live_order_submission_enabled", False))
        self._order_capable_live = bool(self.mode == "live" and self._live_order_submission_enabled)

        self._nonce_authority_registered = ""
        self._pending_tx_provider: Optional[Callable[[], Mapping[str, Any]]] = None
        self._redemption_executor: Optional[WalletRedemptionExecutor] = None
        self._event_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._halted = False
        self._halt_reason = ""
        self._reservations = WalletReservations()
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
            truth_domain=TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE,
            authority_class=AUTHORITY_CLASS_BOOTSTRAP,
        )
        self._allowance_snapshot = AllowanceSnapshot(
            allowance_usdc=0.0,
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=not self._require_allowance,
            detail="allowance_snapshot_uninitialized",
            truth_domain=TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE,
            authority_class=AUTHORITY_CLASS_BOOTSTRAP,
        )
        self._nonce_snapshot = NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=False,
            detail="nonce_snapshot_uninitialized",
            truth_domain=TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE,
            authority_class=AUTHORITY_CLASS_BOOTSTRAP,
        )
        self._pending_tx_snapshot = PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc=now,
            source=f"{self.mode}_init",
            healthy=False,
            detail="pending_tx_snapshot_uninitialized",
            truth_domain=TRUTH_DOMAIN_BOOTSTRAP_NON_AUTHORITATIVE,
            authority_class=AUTHORITY_CLASS_BOOTSTRAP,
        )
        self._local_nonce_lifecycle_snapshot = NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=now,
            source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            healthy=False,
            detail="local_nonce_lifecycle_uninitialized",
            truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            authority_class=AUTHORITY_CLASS_LOCAL,
        )
        self._local_pending_tx_lifecycle_snapshot = PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc=now,
            source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            healthy=False,
            detail="local_pending_tx_lifecycle_uninitialized",
            truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            authority_class=AUTHORITY_CLASS_LOCAL,
            lifecycle_plane=LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
        )
        self._open_order_state_snapshot = OpenOrderStateSnapshot(
            open_count=0,
            order_ids=tuple(),
            ts_utc=now,
            source=TRUTH_DOMAIN_OPEN_ORDER_STATE,
            healthy=False,
            detail="open_order_state_uninitialized",
            truth_domain=TRUTH_DOMAIN_OPEN_ORDER_STATE,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
        self._last_reconcile_ts_mono = time.monotonic()
        self._last_reconcile_result = ReconciliationResult(
            healthy=False,
            action="reject",
            reason="wallet_reconcile_not_run",
            detail="wallet doctrine initialized before first reconciliation",
            ts_utc=now,
        )
        self._reconcile_scope = "integrity_tripwire"
        self._startup_authority_ready = False
        self._authoritative_refresh_completed = False
        self._authority_status_class = "bootstrap_non_authoritative"
        self._reservation_mismatch_candidate = False
        self._reservation_mismatch_delta_usdc = 0.0
        self._reservation_mismatch_detail = ""
        self._last_emitted_reservation_mismatch_delta_usdc = 0.0
        self._event_emit_failure_count = 0
        self._event_emit_last_error = ""
        self._event_emit_last_error_ts_utc = ""
        self._web3_provider_health: Dict[str, Any] = {}
        self._last_guardian_order_law_state: Dict[str, Any] = {
            "primary_owner": "wallet_guardian",
            "mirror_owner": "risk_engine_transition",
            "law_domain": "order_submission",
            "enabled": False,
            "detail": "wallet_guardian_order_law_state_uninitialized",
            "global_exposure_guard": {"enabled": False, "within_cap": True},
        }
        self._last_guardian_drawdown_state: Dict[str, Any] = {
            "primary_owner": "wallet_guardian",
            "mirror_owner": "risk_engine_transition",
            "law_domain": "drawdown_pause",
            "law_name": "daily_loss_hard_pause",
            "legacy_reason": "max_total_loss",
            "enabled": False,
            "detail": "wallet_guardian_drawdown_state_uninitialized",
            "within_limit": True,
        }
        self._last_redemption_state: Dict[str, Any] = {
            "successful": False,
            "action": "idle",
            "reason": "wallet_redemption_not_run",
            "detail": "wallet_redemption_not_run",
            "tx_hash": "",
            "receipt_confirmed": False,
            "payout_usdc": 0.0,
            "settlement_applied": False,
            "market_id": "",
            "token_id": "",
            "ts_utc": now,
        }

    def register_nonce_authority(self, authority_tag: str) -> None:
        self._nonce_authority_registered = str(authority_tag or "").strip().lower()
        self._attempt_startup_authoritative_refresh()

    def register_pending_tx_provider(self, provider: Callable[[], Mapping[str, Any]]) -> None:
        self._pending_tx_provider = provider
        self._attempt_startup_authoritative_refresh()

    def register_redemption_executor(self, executor: WalletRedemptionExecutor) -> None:
        self._redemption_executor = executor

    def register_event_logger(self, logger: Callable[[str, Dict[str, Any]], None]) -> None:
        self._event_logger = logger

    def is_halted(self) -> bool:
        return bool(self._halted)

    def halt_reason(self) -> str:
        return str(self._halt_reason)

    def _attempt_startup_authoritative_refresh(self) -> None:
        nonce_ready = bool(self._nonce_authority_registered)
        provider_ready = self._pending_tx_provider is not None
        if not (nonce_ready and provider_ready):
            self._startup_authority_ready = False
            self._authoritative_refresh_completed = False
            self._authority_status_class = "bootstrap_non_authoritative"
            return
        result = self.reconcile(pre_execution=True)
        health_contract = self.status_contract(enforce_startup_barrier=False)
        refresh_ok = bool(result.healthy) and (not self._halted) and bool(health_contract.get("wallet_health_ok", False))
        self._authoritative_refresh_completed = bool(refresh_ok)
        order_submit_eligible = bool(refresh_ok if self.mode != "live" else (refresh_ok and self._order_capable_live))
        self._startup_authority_ready = bool(order_submit_eligible)
        self._authority_status_class = "authoritative" if order_submit_eligible else "bootstrap_non_authoritative"
        self._emit(
            "wallet_startup_authority_refresh",
            {
                "ts_utc": utc_iso(),
                "ready": bool(order_submit_eligible),
                "authoritative_refresh_completed": bool(self._authoritative_refresh_completed),
                "order_capable_live": bool(self._order_capable_live),
                "order_submit_eligible": bool(order_submit_eligible),
                "authority_status_class": str(self._authority_status_class),
                "reconcile_reason": str(result.reason),
                "reconcile_action": str(result.action),
                "wallet_health_ok": bool(health_contract.get("wallet_health_ok", False)),
                "wallet_health_reasons": list(health_contract.get("wallet_health_reasons", [])),
            },
        )

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_logger is None:
            return
        try:
            self._event_logger(str(event_type), dict(payload))
        except Exception as exc:
            # Wallet authority must remain functional even if telemetry emission fails.
            self._event_emit_failure_count = int(self._event_emit_failure_count) + 1
            self._event_emit_last_error = f"{exc.__class__.__name__}:{exc}"
            self._event_emit_last_error_ts_utc = utc_iso()
            return

    def _evaluate_reservation_mismatch(self, *, context: str) -> None:
        canonical_locked = float(self._locked_usdc_total())
        exposed_locked = float(getattr(self._wallet_snapshot, "locked_usdc", 0.0) or 0.0)
        delta = float(exposed_locked - canonical_locked)
        mismatch = bool(abs(delta) > self._reservation_mismatch_tolerance_usdc)
        self._reservation_mismatch_candidate = mismatch
        self._reservation_mismatch_delta_usdc = delta
        self._reservation_mismatch_detail = (
            f"exposed_locked_usdc={exposed_locked:.9f}:canonical_locked_usdc={canonical_locked:.9f}:"
            f"delta={delta:.9f}:tolerance={self._reservation_mismatch_tolerance_usdc:.9f}"
        )
        if not mismatch:
            return
        should_emit = (
            abs(delta - self._last_emitted_reservation_mismatch_delta_usdc) > self._reservation_mismatch_tolerance_usdc
        )
        if should_emit:
            self._last_emitted_reservation_mismatch_delta_usdc = delta
            self._emit(
                "wallet_reservation_mismatch_candidate",
                {
                    "ts_utc": utc_iso(),
                    "context": str(context or "unknown"),
                    "defect_candidate": True,
                    "exposed_locked_usdc": float(exposed_locked),
                    "canonical_locked_usdc": float(canonical_locked),
                    "mismatch_delta_usdc": float(delta),
                    "mismatch_tolerance_usdc": float(self._reservation_mismatch_tolerance_usdc),
                    "detail": str(self._reservation_mismatch_detail),
                },
            )

    def reconcile(self, *, pre_execution: bool = False) -> ReconciliationResult:
        result = self._refresh_truth(pre_execution=pre_execution)
        self._evaluate_reservation_mismatch(context="pre_execution" if pre_execution else "post_execution")
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
        self._emit(
            "wallet_reconcile_result",
            {
                "ts_utc": utc_iso(),
                "mode": self.mode,
                "pre_execution": bool(pre_execution),
                "reconcile_scope": str(self._reconcile_scope),
                "healthy": bool(result.healthy),
                "action": str(result.action),
                "reason": str(result.reason),
                "detail": str(result.detail),
                "halt": bool(result.halt),
                "authority_status_class": str(self._authority_status_class),
            },
        )
        self._emit(
            "wallet_nonce_state",
            {
                "ts_utc": utc_iso(),
                "healthy": bool(self._nonce_snapshot.healthy),
                "source": str(self._nonce_snapshot.source),
                "detail": str(self._nonce_snapshot.detail),
                "current_nonce": self._nonce_snapshot.current_nonce,
                "pending_nonces": list(self._nonce_snapshot.pending_nonces),
                "truth_domain": str(self._nonce_snapshot.truth_domain),
                "authority_class": str(self._nonce_snapshot.authority_class),
            },
        )
        self._emit(
            "wallet_local_tx_lifecycle_state",
            {
                "ts_utc": utc_iso(),
                "local_nonce_snapshot": dataclasses.asdict(self._local_nonce_lifecycle_snapshot),
                "exchange_intent_snapshot": self._local_exchange_intent_snapshot(),
            },
        )
        self._emit(
            "wallet_open_order_state",
            {
                "ts_utc": utc_iso(),
                "open_order_state": dataclasses.asdict(self._open_order_state_snapshot),
            },
        )
        self._emit(
            "wallet_state_refresh",
            {
                "ts_utc": utc_iso(),
                "wallet_chain": self._wallet_chain,
                "wallet_address": str(self._wallet_snapshot.address),
                "gas_asset_symbol": self._gas_asset_symbol,
                "stable_asset_symbol": self._stable_asset_symbol,
                "authority_status_class": str(self._authority_status_class),
                **self.status_contract(),
            },
        )
        if not result.healthy:
            self._emit(
                "wallet_integrity_warning",
                {
                    "ts_utc": utc_iso(),
                    "reason": str(result.reason),
                    "detail": str(result.detail),
                    "halt": bool(result.halt),
                },
            )
        if self._halted:
            self._emit(
                "wallet_integrity_fail_closed",
                {
                    "ts_utc": utc_iso(),
                    "halt_reason": str(self._halt_reason),
                },
            )
        return result

    def status(self) -> Dict[str, Any]:
        reserve_snapshot = self._reservations.snapshot()
        canonical_live_wallet_truth = {
            "wallet_snapshot": dataclasses.asdict(self._wallet_snapshot),
            "allowance_snapshot": dataclasses.asdict(self._allowance_snapshot),
            "nonce_snapshot": dataclasses.asdict(self._nonce_snapshot),
            "pending_wallet_tx_snapshot": dataclasses.asdict(self._pending_tx_snapshot),
        }
        local_exchange_intent_snapshot = self._local_exchange_intent_snapshot()
        local_tx_lifecycle_state = {
            "nonce_snapshot": dataclasses.asdict(self._local_nonce_lifecycle_snapshot),
            "exchange_intent_snapshot": local_exchange_intent_snapshot,
        }
        open_order_state = dataclasses.asdict(self._open_order_state_snapshot)
        integrity_tripwire_reconcile_state = {
            **dataclasses.asdict(self._last_reconcile_result),
            "scope": str(self._reconcile_scope),
            "truth_domain": TRUTH_DOMAIN_INTEGRITY_TRIPWIRE_RECONCILE,
            "authority_class": AUTHORITY_CLASS_DERIVED,
        }
        return {
            "mode": self.mode,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "authority_status_class": str(self._authority_status_class),
            "live_order_submission_enabled": bool(self._live_order_submission_enabled),
            "order_capable_live": bool(self._order_capable_live),
            "order_submit_eligible": bool(self._startup_authority_ready),
            "startup_authority_ready": bool(self._startup_authority_ready),
            "authoritative_refresh_completed": bool(self._authoritative_refresh_completed),
            "pending_lock_usdc": reserve_snapshot["pending_lock_usdc"],
            "order_lock_usdc": reserve_snapshot["order_lock_usdc"],
            "locked_usdc": reserve_snapshot["locked_usdc"],
            "reservation_mismatch_candidate": bool(self._reservation_mismatch_candidate),
            "reservation_mismatch_delta_usdc": float(self._reservation_mismatch_delta_usdc),
            "reservation_mismatch_detail": str(self._reservation_mismatch_detail),
            "event_emit_failure_count": int(self._event_emit_failure_count),
            "event_emit_last_error": str(self._event_emit_last_error),
            "event_emit_last_error_ts_utc": str(self._event_emit_last_error_ts_utc),
            "net_usdc_outflow": self._net_usdc_outflow,
            "deployable_usdc": self._deployable_usdc(),
            "min_pol_gas_reserve": self._min_pol_gas_reserve,
            "gas_reserve_target_pol": self._gas_reserve_target_pol,
            "gas_reserve_fail_floor_usd": self._gas_reserve_fail_floor_usd,
            "gas_reserve_target_usd": self._gas_reserve_target_usd,
            "gas_asset_price_usd_hint": self._gas_asset_price_usd_hint,
            "gas_reserve_policy": self._gas_reserve_policy_state(),
            "wallet_chain": self._wallet_chain,
            "gas_asset_symbol": self._gas_asset_symbol,
            "stable_asset_symbol": self._stable_asset_symbol,
            "active_wallet_address_source": self._active_wallet_address_source,
            "approval_spender_targets": list(self._approval_spender_targets),
            "treasury_mode": self._treasury_mode,
            "treasury_wallet_address": self._treasury_wallet_address,
            "nonce_authority_expected": self._expected_nonce_authority,
            "nonce_authority_registered": self._nonce_authority_registered,
            "last_reconcile_ts_mono": self._last_reconcile_ts_mono,
            "last_reconcile_result": dataclasses.asdict(self._last_reconcile_result),
            "web3_provider_health": dict(self._web3_provider_health),
            "wallet_guardian_law_state": {
                "order_submission": dict(self._last_guardian_order_law_state),
                "drawdown_pause": dict(self._last_guardian_drawdown_state),
            },
            "wallet_redemption_state": dict(self._last_redemption_state),
            "integrity_tripwire_reconcile_state": integrity_tripwire_reconcile_state,
            "canonical_live_wallet_truth": canonical_live_wallet_truth,
            "local_tx_lifecycle_state": local_tx_lifecycle_state,
            "open_order_state": open_order_state,
        }

    def status_contract(self, *, enforce_startup_barrier: bool = True) -> Dict[str, Any]:
        return build_wallet_health_contract(status=self.status(), enforce_startup_barrier=enforce_startup_barrier)

    def authorize_intent(
        self,
        intent: OrderIntent,
        *,
        guardian_context: Optional[Mapping[str, Any]] = None,
    ) -> WalletAuthorization:
        if self.mode == "live" and not self._order_capable_live:
            health_contract = self.status_contract()
            self._emit(
                "wallet_health_gate",
                {
                    "ts_utc": utc_iso(),
                    "allowed": False,
                    "enforced": True,
                    "authority_status_class": str(self._authority_status_class),
                    "reasons": list(health_contract.get("wallet_health_reasons", [])),
                    **health_contract,
                },
            )
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_live_order_submission_disabled",
                detail="auth.live_order_submission_enabled=false",
                halt=False,
            )
        if not self._startup_authority_ready:
            health_contract = self.status_contract()
            self._emit(
                "wallet_health_gate",
                {
                    "ts_utc": utc_iso(),
                    "allowed": False,
                    "enforced": True,
                    "authority_status_class": str(self._authority_status_class),
                    "reasons": list(health_contract.get("wallet_health_reasons", [])),
                    **health_contract,
                },
            )
            self._emit(
                "wallet_health_gate_veto",
                {
                    "ts_utc": utc_iso(),
                    "veto_reason": "wallet_startup_authority_not_ready",
                    "veto_action": "reject",
                    "authority_status_class": str(self._authority_status_class),
                },
            )
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_startup_authority_not_ready",
                detail="post_registration_authoritative_refresh_not_completed",
                halt=False,
            )
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
        health_contract = self.status_contract()
        health_gate_allowed = bool(health_contract.get("wallet_health_ok", False))
        self._emit(
            "wallet_health_gate",
            {
                "ts_utc": utc_iso(),
                "allowed": bool(health_gate_allowed),
                "enforced": True,
                "authority_status_class": str(self._authority_status_class),
                "reconcile_reason": str(reconcile.reason),
                "reconcile_action": str(reconcile.action),
                "reasons": list(health_contract.get("wallet_health_reasons", [])),
                **health_contract,
            },
        )
        if self._halted:
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_halted",
                detail=self._halt_reason,
                halt=True,
            )
        if not health_gate_allowed:
            veto_action = "halt" if (bool(reconcile.halt) or str(reconcile.action).strip().lower() == "halt") else "reject"
            veto_reason = str(reconcile.reason or "wallet_health_gate_failed")
            veto_detail = str(reconcile.detail or "wallet_health_ok_false")
            self._emit(
                "wallet_health_gate_veto",
                {
                    "ts_utc": utc_iso(),
                    "veto_reason": veto_reason,
                    "veto_action": veto_action,
                    "veto_detail": veto_detail,
                },
            )
            return WalletAuthorization(
                allowed=False,
                action=veto_action,
                approved_size=0.0,
                reason=veto_reason,
                detail=veto_detail,
                halt=(veto_action == "halt"),
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

        guardian_order_law_state = self._guardian_order_law_state(guardian_context=guardian_context)
        self._last_guardian_order_law_state = dict(guardian_order_law_state)
        global_exposure_guard = dict(guardian_order_law_state.get("global_exposure_guard") or {})
        if bool(global_exposure_guard.get("enabled", False)) and not bool(global_exposure_guard.get("within_cap", True)):
            detail = (
                "projected_global_notional="
                + f"{float(global_exposure_guard.get('projected_total_notional', 0.0) or 0.0):.2f},"
                + "effective_global_cap="
                + f"{float(global_exposure_guard.get('effective_cap_usd', 0.0) or 0.0):.2f}"
            )
            self._emit(
                "wallet_guardian_order_law_veto",
                {
                    "ts_utc": utc_iso(),
                    "reason": "global_exposure_cap",
                    "detail": detail,
                    "law_domain": "order_submission",
                    "primary_owner": "wallet_guardian",
                    "mirror_owner": "risk_engine_transition",
                    "global_exposure_guard": dict(global_exposure_guard),
                },
            )
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="global_exposure_cap",
                detail=detail,
                halt=False,
            )

        gas_reserve_policy = self._gas_reserve_policy_state()
        if not bool(gas_reserve_policy.get("conservative_fail_floor_ok", False)):
            self._halt("wallet_gas_reserve_insufficient")
            return WalletAuthorization(
                allowed=False,
                action="halt",
                approved_size=0.0,
                reason="wallet_gas_reserve_insufficient",
                detail=str(gas_reserve_policy.get("detail") or "gas_reserve_fail_floor_breached"),
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
            self._emit(
                "wallet_approval_check",
                {
                    "ts_utc": utc_iso(),
                    "allowance_usdc": float(self._allowance_snapshot.allowance_usdc),
                    "allowance_healthy": bool(self._allowance_snapshot.healthy),
                    "approval_spender_targets": list(self._approval_spender_targets),
                },
            )
            if self.mode == "live" and not self._approval_spender_targets:
                self._emit(
                    "wallet_approval_alert",
                    {
                        "ts_utc": utc_iso(),
                        "result": "halt",
                        "reason": "wallet_approval_target_unknown",
                        "approval_spender_targets": [],
                    },
                )
                self._halt("wallet_approval_target_unknown")
                return WalletAuthorization(
                    allowed=False,
                    action="halt",
                    approved_size=0.0,
                    reason="wallet_approval_target_unknown",
                    detail="approval_spender_targets_missing",
                    halt=True,
                )
            approved_notional = min(approved_notional, max(0.0, float(self._allowance_snapshot.allowance_usdc)))
            if approved_notional <= self._reconcile_tolerance_usdc:
                self._emit(
                    "wallet_approval_alert",
                    {
                        "ts_utc": utc_iso(),
                        "result": "halt" if self.mode == "live" else "reject",
                        "reason": "wallet_allowance_insufficient",
                        "allowance_usdc": float(self._allowance_snapshot.allowance_usdc),
                    },
                )
                if self.mode == "live":
                    self._halt("wallet_allowance_insufficient")
                return WalletAuthorization(
                    allowed=False,
                    action="halt" if self.mode == "live" else "reject",
                    approved_size=0.0,
                    reason="wallet_allowance_insufficient",
                    detail=f"allowance={self._allowance_snapshot.allowance_usdc:.6f}",
                    halt=bool(self.mode == "live"),
                )

        approved_size = approved_notional / price if price > 0 else 0.0
        approved_size_tolerance = self._reconcile_tolerance_usdc / price if price > 0 else self._reconcile_tolerance_usdc
        if approved_size <= approved_size_tolerance:
            return WalletAuthorization(
                allowed=False,
                action="reject",
                approved_size=0.0,
                reason="wallet_approved_size_zero",
                detail=f"approved_notional={approved_notional:.6f}",
            )

        lock_notional = approved_size * price
        lock_id = self._reservations.create_pending(lock_notional)
        self._emit(
            "wallet_reservation_created",
            {
                "ts_utc": utc_iso(),
                "lock_id": str(lock_id),
                "notional_usd": float(lock_notional),
                "submission_lineage_stage": str(intent.lineage_stage or ""),
                "token_id": str(intent.token_id or ""),
                "side": str(intent.side or ""),
            },
        )
        post_lock_reconcile = self.reconcile(pre_execution=False)
        if self._halted or not bool(post_lock_reconcile.healthy):
            self.release_pending_lock(lock_id)
            action = (
                "halt"
                if self._halted or bool(post_lock_reconcile.halt) or str(post_lock_reconcile.action).strip().lower() == "halt"
                else "reject"
            )
            reason = str(post_lock_reconcile.reason or "wallet_post_lock_reconcile_unhealthy")
            detail = str(post_lock_reconcile.detail or "post_lock_reconcile_failed")
            return WalletAuthorization(
                allowed=False,
                action=action,
                approved_size=0.0,
                reason=reason,
                detail=detail,
                halt=(action == "halt"),
            )
        reduced = approved_size + approved_size_tolerance < requested_size
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
        ok, reason = self._reservations.confirm_submission(lock_id=lock_id, order_id=order_id, order_open=order_open)
        if not ok:
            self._halt(reason)
            return False
        self.reconcile(pre_execution=False)
        self._emit(
            "wallet_reservation_settled",
            {
                "ts_utc": utc_iso(),
                "lock_id": str(lock_id or ""),
                "order_id": str(order_id or ""),
                "order_open": bool(order_open),
            },
        )
        return True

    def release_pending_lock(self, lock_id: str) -> None:
        key = str(lock_id or "").strip()
        if not key:
            return
        self._reservations.release_pending(key)
        self.reconcile(pre_execution=False)
        self._emit(
            "wallet_reservation_released",
            {
                "ts_utc": utc_iso(),
                "lock_id": key,
                "release_kind": "pending",
            },
        )

    def release_order_lock(self, order_id: str) -> None:
        key = str(order_id or "").strip()
        if not key:
            return
        self._reservations.release_order(key)
        self.reconcile(pre_execution=False)
        self._emit(
            "wallet_reservation_released",
            {
                "ts_utc": utc_iso(),
                "order_id": key,
                "release_kind": "order",
            },
        )

    def on_fill(self, fill: FillEvent) -> None:
        notional = abs(float(fill.price) * float(fill.size))
        side = str(fill.side or "").strip().upper()
        if side == "BUY":
            self._net_usdc_outflow += notional
        elif side == "SELL":
            self._net_usdc_outflow -= notional

        self._reservations.settle_fill(
            order_id=(str(fill.order_id or "").strip() or None),
            notional_usd=notional,
            tolerance=self._reconcile_tolerance_usdc,
        )
        self.reconcile(pre_execution=False)

    @staticmethod
    def _normalize_redemption_result(
        result: WalletRedemptionResult | Mapping[str, Any],
        *,
        request: WalletRedemptionRequest,
    ) -> WalletRedemptionResult:
        if isinstance(result, WalletRedemptionResult):
            return result
        raw = dict(result or {})
        receipt_contract = WalletWeb3Adapter.normalize_redemption_receipt(raw)
        successful = bool(raw.get("successful", receipt_contract.get("receipt_confirmed", False)))
        action = str(raw.get("action") or ("redeem_positions" if successful else "reject")).strip() or "reject"
        reason = str(raw.get("reason") or ("wallet_redemption_ok" if successful else "wallet_redemption_failed")).strip()
        detail = str(raw.get("detail") or receipt_contract.get("detail") or "").strip()
        payout_usdc = float(raw.get("payout_usdc", receipt_contract.get("payout_usdc", request.expected_payout_usd)) or 0.0)
        tx_hash = str(raw.get("tx_hash") or receipt_contract.get("tx_hash") or "").strip()
        receipt_confirmed = bool(raw.get("receipt_confirmed", receipt_contract.get("receipt_confirmed", False)))
        ts_utc = str(raw.get("ts_utc") or request.ts_utc or utc_iso())
        settlement_applied = bool(raw.get("settlement_applied", False))
        metadata = raw.get("metadata")
        return WalletRedemptionResult(
            successful=bool(successful),
            action=action,
            reason=reason,
            detail=detail,
            tx_hash=tx_hash,
            receipt_confirmed=bool(receipt_confirmed),
            payout_usdc=max(0.0, float(payout_usdc)),
            settlement_applied=bool(settlement_applied),
            ts_utc=ts_utc,
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )

    def _record_redemption_state(
        self,
        *,
        request: WalletRedemptionRequest,
        result: WalletRedemptionResult,
    ) -> None:
        self._last_redemption_state = {
            "successful": bool(result.successful),
            "action": str(result.action),
            "reason": str(result.reason),
            "detail": str(result.detail),
            "tx_hash": str(result.tx_hash),
            "receipt_confirmed": bool(result.receipt_confirmed),
            "payout_usdc": float(result.payout_usdc),
            "settlement_applied": bool(result.settlement_applied),
            "market_id": str(request.market_id),
            "token_id": str(request.token_id),
            "ts_utc": str(result.ts_utc or request.ts_utc),
            "payout_symbol": str(request.payout_symbol),
            "metadata": dict(result.metadata),
        }

    def redeem_winnings(
        self,
        *,
        market_id: str,
        token_id: str,
        settlement_side: str,
        size_shares: float,
        settlement_price: float = 1.0,
        payout_symbol: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        ts_utc: Optional[str] = None,
    ) -> WalletRedemptionResult:
        market = str(market_id or "").strip()
        token = str(token_id or "").strip()
        side = str(settlement_side or "").strip().upper()
        size = max(0.0, float(size_shares))
        price = max(0.0, min(1.0, float(settlement_price)))
        expected_payout_usd = float(size * price)
        request = WalletRedemptionRequest(
            market_id=market,
            token_id=token,
            settlement_side=side,
            size_shares=float(size),
            settlement_price=float(price),
            expected_payout_usd=float(expected_payout_usd),
            payout_symbol=str(payout_symbol or self._stable_asset_symbol),
            ts_utc=str(ts_utc or utc_iso()),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )
        self._emit(
            "wallet_redemption_requested",
            {
                "ts_utc": request.ts_utc,
                "market_id": str(request.market_id),
                "token_id": str(request.token_id),
                "settlement_side": str(request.settlement_side),
                "settlement_size_shares": float(request.size_shares),
                "settlement_price": float(request.settlement_price),
                "expected_payout_usd": float(request.expected_payout_usd),
                "payout_symbol": str(request.payout_symbol),
                "metadata": dict(request.metadata),
            },
        )
        if not market or not token or not side or size <= 0.0 or price <= 0.0:
            result = WalletRedemptionResult(
                successful=False,
                action="reject",
                reason="wallet_redemption_invalid_request",
                detail="market_id/token_id/settlement_side/size_shares/settlement_price required",
                ts_utc=request.ts_utc,
            )
            self._record_redemption_state(request=request, result=result)
            self._emit(
                "wallet_redemption_failed",
                {
                    "ts_utc": request.ts_utc,
                    "market_id": str(request.market_id),
                    "token_id": str(request.token_id),
                    "reason": str(result.reason),
                    "detail": str(result.detail),
                },
            )
            return result

        if self.mode == "live" and self._redemption_executor is None:
            result = WalletRedemptionResult(
                successful=False,
                action="reject",
                reason="wallet_redemption_executor_unavailable",
                detail="live_redemption_executor_not_registered",
                ts_utc=request.ts_utc,
            )
            self._record_redemption_state(request=request, result=result)
            self._emit(
                "wallet_redemption_failed",
                {
                    "ts_utc": request.ts_utc,
                    "market_id": str(request.market_id),
                    "token_id": str(request.token_id),
                    "reason": str(result.reason),
                    "detail": str(result.detail),
                },
            )
            return result

        if self._redemption_executor is None:
            result = WalletRedemptionResult(
                successful=True,
                action="redeem_positions",
                reason="paper_redemption_emulated",
                detail="paper_wallet_redemption_emulated",
                tx_hash="",
                receipt_confirmed=False,
                payout_usdc=float(expected_payout_usd),
                settlement_applied=False,
                ts_utc=request.ts_utc,
            )
        else:
            try:
                raw_result = self._redemption_executor(request)
            except Exception as exc:
                raw_result = {
                    "successful": False,
                    "action": "reject",
                    "reason": "wallet_redemption_executor_error",
                    "detail": f"{exc.__class__.__name__}:{exc}",
                    "ts_utc": request.ts_utc,
                }
            result = self._normalize_redemption_result(raw_result, request=request)

        if not result.successful:
            self._record_redemption_state(request=request, result=result)
            self._emit(
                "wallet_redemption_failed",
                {
                    "ts_utc": str(result.ts_utc or request.ts_utc),
                    "market_id": str(request.market_id),
                    "token_id": str(request.token_id),
                    "reason": str(result.reason),
                    "detail": str(result.detail),
                    "tx_hash": str(result.tx_hash),
                },
            )
            return result

        settlement_payload = self.settle_binary_position(
            token_id=request.token_id,
            settlement_side=request.settlement_side,
            size_shares=request.size_shares,
            settlement_price=request.settlement_price,
            ts_utc=request.ts_utc,
        )
        finalized = dataclasses.replace(result, settlement_applied=True, payout_usdc=max(0.0, float(result.payout_usdc or expected_payout_usd)))
        self._record_redemption_state(request=request, result=finalized)
        self._emit(
            "wallet_redemption_completed",
            {
                "ts_utc": str(finalized.ts_utc or request.ts_utc),
                "market_id": str(request.market_id),
                "token_id": str(request.token_id),
                "reason": str(finalized.reason),
                "detail": str(finalized.detail),
                "tx_hash": str(finalized.tx_hash),
                "receipt_confirmed": bool(finalized.receipt_confirmed),
                "payout_usdc": float(finalized.payout_usdc),
                "payout_symbol": str(request.payout_symbol),
                "settlement_applied": bool(finalized.settlement_applied),
                "wallet_settlement": dict(settlement_payload),
                "metadata": dict(finalized.metadata),
            },
        )
        return finalized

    def settle_binary_position(
        self,
        *,
        token_id: str,
        settlement_side: str,
        size_shares: float,
        settlement_price: float,
        ts_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = str(token_id or "").strip()
        side = str(settlement_side or "").strip().upper()
        size = max(0.0, float(size_shares))
        price = max(0.0, min(1.0, float(settlement_price)))
        notional = float(size * price)
        if side == "BUY":
            self._net_usdc_outflow += notional
        elif side == "SELL":
            self._net_usdc_outflow -= notional
        self.reconcile(pre_execution=False)
        payload = {
            "ts_utc": str(ts_utc or utc_iso()),
            "token_id": token,
            "settlement_side": side,
            "settlement_size_shares": float(size),
            "settlement_price": float(price),
            "settlement_notional_usd": float(notional),
        }
        self._emit("wallet_position_settled", payload)
        return payload

    def _locked_usdc_total(self) -> float:
        return float(self._reservations.locked_total())

    def _deployable_usdc(self) -> float:
        free = max(0.0, float(self._wallet_snapshot.usdc_balance) - float(self._protected_reserve_usdc))
        return free - self._locked_usdc_total()

    def _gas_reserve_policy_state(self) -> Dict[str, Any]:
        raw_balance_pol = max(0.0, float(self._wallet_snapshot.pol_balance or 0.0))
        raw_fail_floor_pol = max(0.0, float(self._min_pol_gas_reserve))
        raw_target_pol = max(raw_fail_floor_pol, float(self._gas_reserve_target_pol))
        usd_fail_floor = max(0.0, float(self._gas_reserve_fail_floor_usd))
        usd_target = max(usd_fail_floor, float(self._gas_reserve_target_usd))
        price_hint_usd = max(0.0, float(self._gas_asset_price_usd_hint))
        usd_policy_enabled = bool(usd_fail_floor > 0.0 or usd_target > 0.0)
        price_hint_available = bool(price_hint_usd > 0.0)
        usd_balance_estimate = raw_balance_pol * price_hint_usd if price_hint_available else None
        raw_fail_floor_ok = bool(raw_balance_pol + 1e-9 >= raw_fail_floor_pol)
        raw_target_ok = bool(raw_balance_pol + 1e-9 >= raw_target_pol)
        usd_fail_floor_ok = (
            None
            if not usd_policy_enabled
            else bool(usd_balance_estimate is not None and usd_balance_estimate + 1e-9 >= usd_fail_floor)
        )
        usd_target_ok = (
            None
            if not usd_policy_enabled
            else bool(usd_balance_estimate is not None and usd_balance_estimate + 1e-9 >= usd_target)
        )
        conservative_fail_floor_ok = bool(
            raw_fail_floor_ok and ((not usd_policy_enabled) or bool(usd_fail_floor_ok))
        )
        conservative_target_ok = bool(
            raw_target_ok and ((not usd_policy_enabled) or bool(usd_target_ok))
        )
        detail = "gas_reserve_policy_ok"
        if usd_policy_enabled and not price_hint_available:
            detail = "gas_asset_price_usd_hint_missing"
        elif not conservative_fail_floor_ok:
            detail = "gas_reserve_fail_floor_breached"
        elif not conservative_target_ok:
            detail = "gas_reserve_target_not_met"
        return {
            "raw_balance_pol": float(raw_balance_pol),
            "raw_fail_floor_pol": float(raw_fail_floor_pol),
            "raw_target_pol": float(raw_target_pol),
            "usd_balance_estimate": usd_balance_estimate,
            "usd_fail_floor": float(usd_fail_floor),
            "usd_target": float(usd_target),
            "price_hint_usd": float(price_hint_usd),
            "price_hint_available": bool(price_hint_available),
            "usd_policy_enabled": bool(usd_policy_enabled),
            "raw_fail_floor_ok": bool(raw_fail_floor_ok),
            "raw_target_ok": bool(raw_target_ok),
            "usd_fail_floor_ok": usd_fail_floor_ok,
            "usd_target_ok": usd_target_ok,
            "conservative_fail_floor_ok": bool(conservative_fail_floor_ok),
            "conservative_target_ok": bool(conservative_target_ok),
            "detail": detail,
        }

    @staticmethod
    def _is_canonical_live_surface(*, truth_domain: str, authority_class: str) -> bool:
        return (
            str(truth_domain or "").strip() == TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
            and str(authority_class or "").strip() == AUTHORITY_CLASS_LIVE
        )

    def _canonical_live_nonce_available(self) -> bool:
        return bool(
            self.mode == "live"
            and self._is_canonical_live_surface(
                truth_domain=self._nonce_snapshot.truth_domain,
                authority_class=self._nonce_snapshot.authority_class,
            )
            and bool(self._nonce_snapshot.healthy)
            and (self._nonce_snapshot.current_nonce is not None)
        )

    def _canonical_live_pending_wallet_tx_available(self) -> bool:
        return bool(
            self.mode == "live"
            and self._is_canonical_live_surface(
                truth_domain=self._pending_tx_snapshot.truth_domain,
                authority_class=self._pending_tx_snapshot.authority_class,
            )
            and bool(self._pending_tx_snapshot.healthy)
        )

    def _local_exchange_intent_snapshot(self) -> Dict[str, Any]:
        snapshot = dataclasses.asdict(self._local_pending_tx_lifecycle_snapshot)
        exchange_order_ids = tuple(
            str(x)
            for x in (
                self._local_pending_tx_lifecycle_snapshot.exchange_order_ids
                or self._local_pending_tx_lifecycle_snapshot.order_ids
            )
            if str(x).strip()
        )
        exchange_client_order_ids = tuple(
            str(x)
            for x in self._local_pending_tx_lifecycle_snapshot.exchange_client_order_ids
            if str(x).strip()
        )
        snapshot["exchange_order_ids"] = list(exchange_order_ids)
        snapshot["exchange_client_order_ids"] = list(exchange_client_order_ids)
        snapshot["tx_ids"] = list(self._local_pending_tx_lifecycle_snapshot.tx_ids)
        snapshot["order_ids"] = list(exchange_order_ids)
        snapshot["lifecycle_plane"] = (
            str(snapshot.get("lifecycle_plane") or "").strip()
            or LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX
        )
        snapshot["canonical_pending_wallet_tx"] = False
        return snapshot

    def _guardian_order_law_state(self, *, guardian_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        context = guardian_context if isinstance(guardian_context, Mapping) else {}
        snapshot = context.get("order_law_snapshot")
        raw = snapshot if isinstance(snapshot, Mapping) else {}
        global_exposure_guard = raw.get("global_exposure_guard")
        global_guard_raw = global_exposure_guard if isinstance(global_exposure_guard, Mapping) else {}
        enabled = bool(global_guard_raw.get("enabled", False))
        return {
            "primary_owner": "wallet_guardian",
            "mirror_owner": "risk_engine_transition",
            "law_domain": "order_submission",
            "enabled": bool(enabled),
            "detail": str(raw.get("detail") or ""),
            "dynamic_scaling": dict(raw.get("dynamic_scaling") or {}) if isinstance(raw.get("dynamic_scaling"), Mapping) else {},
            "global_exposure_guard": {
                "enabled": bool(enabled),
                "base_cap_usd": float(global_guard_raw.get("base_cap_usd", 0.0) or 0.0),
                "effective_cap_usd": float(global_guard_raw.get("effective_cap_usd", 0.0) or 0.0),
                "taker_reserved_notional_usd": float(global_guard_raw.get("taker_reserved_notional_usd", 0.0) or 0.0),
                "taker_reserve_applied": bool(global_guard_raw.get("taker_reserve_applied", False)),
                "near_cap_ratio": float(global_guard_raw.get("near_cap_ratio", 0.0) or 0.0),
                "projected_total_notional": float(global_guard_raw.get("projected_total_notional", 0.0) or 0.0),
                "projected_to_cap_ratio": float(global_guard_raw.get("projected_to_cap_ratio", 0.0) or 0.0),
                "position_notional": float(global_guard_raw.get("position_notional", 0.0) or 0.0),
                "resting_open_order_notional": float(global_guard_raw.get("resting_open_order_notional", 0.0) or 0.0),
                "incoming_intent_notional": float(global_guard_raw.get("incoming_intent_notional", 0.0) or 0.0),
                "unknown_position_tokens": list(global_guard_raw.get("unknown_position_tokens", []) or []),
                "within_cap": bool(global_guard_raw.get("within_cap", True)),
                "near_cap": bool(global_guard_raw.get("near_cap", False)),
            },
        }

    def evaluate_drawdown_guard(
        self,
        *,
        guardian_context: Optional[Mapping[str, Any]] = None,
    ) -> ReconciliationResult:
        context = guardian_context if isinstance(guardian_context, Mapping) else {}
        snapshot = context.get("drawdown_snapshot")
        raw = snapshot if isinstance(snapshot, Mapping) else {}
        enabled = bool(raw.get("enabled", False))
        threshold = raw.get("threshold_usd")
        threshold_usd = float(threshold) if isinstance(threshold, (int, float)) else None
        total_pnl = float(raw.get("total_pnl", 0.0) or 0.0)
        within_limit = bool(raw.get("within_limit", True))
        self._last_guardian_drawdown_state = {
            "primary_owner": "wallet_guardian",
            "mirror_owner": "risk_engine_transition",
            "law_domain": "drawdown_pause",
            "law_name": str(raw.get("law_name") or "daily_loss_hard_pause"),
            "legacy_reason": str(raw.get("legacy_reason") or "max_total_loss"),
            "enabled": bool(enabled),
            "threshold_usd": threshold_usd,
            "total_pnl": float(total_pnl),
            "within_limit": bool(within_limit),
            "detail": f"total_pnl={total_pnl:.4f}",
        }
        self._emit(
            "wallet_guardian_drawdown_guard",
            {
                "ts_utc": utc_iso(),
                "enabled": bool(enabled),
                "threshold_usd": threshold_usd,
                "total_pnl": float(total_pnl),
                "within_limit": bool(within_limit),
                "primary_owner": "wallet_guardian",
                "mirror_owner": "risk_engine_transition",
                "legacy_reason": str(raw.get("legacy_reason") or "max_total_loss"),
            },
        )
        if enabled and not within_limit:
            detail = f"total_pnl={total_pnl:.4f}"
            self._halt("max_total_loss")
            return ReconciliationResult(
                healthy=False,
                action="halt",
                reason="max_total_loss",
                detail=detail,
                halt=True,
                ts_utc=utc_iso(),
            )
        return ReconciliationResult(
            healthy=True,
            action="ok",
            reason="ok",
            detail=f"total_pnl={total_pnl:.4f}",
            halt=False,
            ts_utc=utc_iso(),
        )

    def _nonce_snapshot_from_provider(self) -> Optional[NonceSnapshot]:
        provider = self._pending_tx_provider
        if provider is None:
            return None
        try:
            payload = provider() or {}
        except WALLET_TRUTH_EXCEPTIONS as exc:
            return NonceSnapshot(
                current_nonce=None,
                pending_nonces=tuple(),
                ts_utc=utc_iso(),
                source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                healthy=False,
                detail=f"nonce_provider_error:{exc}",
                truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                authority_class=AUTHORITY_CLASS_LOCAL,
            )
        return nonce_snapshot_from_provider(payload, source_default=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE)

    def _pending_snapshot_from_provider(self) -> Optional[PendingTxSnapshot]:
        provider = self._pending_tx_provider
        if provider is None:
            return None
        try:
            payload = provider() or {}
        except WALLET_TRUTH_EXCEPTIONS as exc:
            return PendingTxSnapshot(
                pending_count=0,
                order_ids=tuple(),
                ts_utc=utc_iso(),
                source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                healthy=False,
                detail=f"pending_tx_provider_error:{exc}",
                truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                authority_class=AUTHORITY_CLASS_LOCAL,
                lifecycle_plane=LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
            )
        return pending_tx_snapshot_from_provider(payload, source_default=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE)

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = str(reason or "wallet_halt")
        self._startup_authority_ready = False
        self._authoritative_refresh_completed = False
        self._authority_status_class = "bootstrap_non_authoritative"
        self._emit(
            "wallet_integrity_fail_closed",
            {
                "ts_utc": utc_iso(),
                "halt_reason": self._halt_reason,
            },
        )

    @abstractmethod
    def _refresh_truth(self, *, pre_execution: bool) -> ReconciliationResult:
        raise NotImplementedError


class PaperWalletDoctrine(WalletDoctrineBase):
    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        mode: str = "paper",
        auth_cfg: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(cfg, mode=mode, auth_cfg=auth_cfg)
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
            truth_domain=TRUTH_DOMAIN_PAPER_WALLET,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
        self._allowance_snapshot = AllowanceSnapshot(
            allowance_usdc=self._paper_allowance_usdc,
            ts_utc=now,
            source="paper_internal",
            healthy=(not self._require_allowance) or self._paper_allowance_usdc > self._reconcile_tolerance_usdc,
            detail="",
            truth_domain=TRUTH_DOMAIN_PAPER_WALLET,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
        self._nonce_snapshot = NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=now,
            source=TRUTH_DOMAIN_PAPER_WALLET,
            healthy=bool(self._nonce_authority_registered),
            detail="" if self._nonce_authority_registered else "nonce_authority_unregistered",
            truth_domain=TRUTH_DOMAIN_PAPER_WALLET,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
        self._pending_tx_snapshot = PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc=now,
            source=TRUTH_DOMAIN_PAPER_WALLET,
            healthy=True,
            detail="paper_pending_wallet_tx_not_modeled",
            truth_domain=TRUTH_DOMAIN_PAPER_WALLET,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
        provider_nonce = self._nonce_snapshot_from_provider()
        if provider_nonce is not None:
            self._local_nonce_lifecycle_snapshot = provider_nonce
        else:
            self._local_nonce_lifecycle_snapshot = NonceSnapshot(
                current_nonce=None,
                pending_nonces=tuple(),
                ts_utc=now,
                source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                healthy=False,
                detail="local_nonce_lifecycle_unavailable",
                truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                authority_class=AUTHORITY_CLASS_LOCAL,
            )
        provider_pending = self._pending_snapshot_from_provider()
        if provider_pending is not None:
            self._local_pending_tx_lifecycle_snapshot = provider_pending
        else:
            self._local_pending_tx_lifecycle_snapshot = PendingTxSnapshot(
                pending_count=len(self._reservations.order_locks),
                order_ids=tuple(sorted(self._reservations.order_locks.keys())),
                exchange_order_ids=tuple(sorted(self._reservations.order_locks.keys())),
                ts_utc=now,
                source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                healthy=True,
                detail="exchange_intent_derived_from_order_locks",
                truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                authority_class=AUTHORITY_CLASS_LOCAL,
                lifecycle_plane=LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
            )
        self._open_order_state_snapshot = OpenOrderStateSnapshot(
            open_count=int(self._local_pending_tx_lifecycle_snapshot.pending_count),
            order_ids=tuple(
                self._local_pending_tx_lifecycle_snapshot.exchange_order_ids
                or self._local_pending_tx_lifecycle_snapshot.order_ids
            ),
            ts_utc=now,
            source=TRUTH_DOMAIN_OPEN_ORDER_STATE,
            healthy=True,
            detail="paper_open_order_state",
            truth_domain=TRUTH_DOMAIN_OPEN_ORDER_STATE,
            authority_class=AUTHORITY_CLASS_DERIVED,
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


class LiveWalletDoctrine(WalletDoctrineBase):
    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        truth_source: LiveWalletTruthSource,
        mode: str = "live",
        auth_cfg: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(cfg, mode=mode, auth_cfg=auth_cfg)
        self._truth_source = truth_source
        self._expected_chain_id = int(self._cfg.get("expected_chain_id", 137))
        self._expected_wallet_address = str(self._cfg.get("expected_wallet_address", "")).strip().lower()
        self._require_live_pol_balance_snapshot = bool(self._cfg.get("require_live_pol_balance_snapshot", False))
        self._require_live_nonce_snapshot = bool(self._cfg.get("require_live_nonce_snapshot", False))
        self._require_live_nonce_value = bool(self._cfg.get("require_live_nonce_value", False))
        self._require_live_pending_tx_snapshot = bool(self._cfg.get("require_live_pending_tx_snapshot", False))
        if self._order_capable_live and (
            (not self._require_live_nonce_snapshot)
            or (not self._require_live_nonce_value)
            or (not self._require_live_pending_tx_snapshot)
        ):
            raise ValueError(
                "order-capable live mode requires wallet.require_live_nonce_snapshot=true, "
                "wallet.require_live_nonce_value=true, and wallet.require_live_pending_tx_snapshot=true"
            )
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
            if hasattr(self._truth_source, "web3_provider_health_status"):
                self._web3_provider_health = dict(self._truth_source.web3_provider_health_status())  # type: ignore[attr-defined]
            else:
                self._web3_provider_health = {}
            if hasattr(self._truth_source, "open_order_state_snapshot"):
                self._open_order_state_snapshot = self._truth_source.open_order_state_snapshot()  # type: ignore[assignment]
            else:
                self._open_order_state_snapshot = OpenOrderStateSnapshot(
                    open_count=int(self._pending_tx_snapshot.pending_count),
                    order_ids=tuple(
                        self._pending_tx_snapshot.exchange_order_ids
                        or self._pending_tx_snapshot.order_ids
                    ),
                    ts_utc=now,
                    source=TRUTH_DOMAIN_OPEN_ORDER_STATE,
                    healthy=False,
                    detail="open_order_state_unavailable_from_truth_source",
                    truth_domain=TRUTH_DOMAIN_OPEN_ORDER_STATE,
                    authority_class=AUTHORITY_CLASS_DERIVED,
                )
            self._local_pending_tx_lifecycle_snapshot = (
                self._pending_snapshot_from_provider()
                or PendingTxSnapshot(
                    pending_count=0,
                    order_ids=tuple(),
                    ts_utc=now,
                    source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    healthy=False,
                    detail="local_pending_tx_lifecycle_unavailable",
                    truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    authority_class=AUTHORITY_CLASS_LOCAL,
                    lifecycle_plane=LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
                )
            )
            self._local_nonce_lifecycle_snapshot = (
                self._nonce_snapshot_from_provider()
                or NonceSnapshot(
                    current_nonce=None,
                    pending_nonces=tuple(),
                    ts_utc=now,
                    source=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    healthy=False,
                    detail="local_nonce_lifecycle_unavailable",
                    truth_domain=TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
                    authority_class=AUTHORITY_CLASS_LOCAL,
                )
            )
        except WALLET_TRUTH_EXCEPTIONS as exc:
            self._web3_provider_health = {}
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
                action="halt" if self.mode == "live" else "reject",
                reason="wallet_allowance_snapshot_unhealthy",
                detail=self._allowance_snapshot.detail,
                halt=bool(self.mode == "live"),
                ts_utc=now,
            )

        nonce_surface_is_canonical_live = self._is_canonical_live_surface(
            truth_domain=self._nonce_snapshot.truth_domain,
            authority_class=self._nonce_snapshot.authority_class,
        )
        if self._require_live_nonce_snapshot and (
            (not nonce_surface_is_canonical_live) or (not self._nonce_snapshot.healthy)
        ):
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_nonce_snapshot_unhealthy",
                detail=(
                    self._nonce_snapshot.detail
                    or f"nonce_surface_not_canonical_live:{self._nonce_snapshot.truth_domain}:{self._nonce_snapshot.authority_class}"
                ),
                ts_utc=now,
            )
        if self._require_live_nonce_snapshot and self._require_live_nonce_value and not self._canonical_live_nonce_available():
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_nonce_value_missing",
                detail=self._nonce_snapshot.detail or "live_nonce_value_missing",
                ts_utc=now,
            )

        pending_surface_is_canonical_live = self._is_canonical_live_surface(
            truth_domain=self._pending_tx_snapshot.truth_domain,
            authority_class=self._pending_tx_snapshot.authority_class,
        )
        if self._require_live_pending_tx_snapshot and (
            (not pending_surface_is_canonical_live) or (not self._pending_tx_snapshot.healthy)
        ):
            return ReconciliationResult(
                healthy=False,
                action="reject",
                reason="wallet_pending_tx_snapshot_unhealthy",
                detail=(
                    self._pending_tx_snapshot.detail
                    or "pending_wallet_tx_surface_not_canonical_live"
                ),
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
    event_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    auth_cfg: Optional[Mapping[str, Any]] = None,
) -> WalletDoctrineBase:
    selected_mode = str(mode or "paper").strip().lower() or "paper"
    if selected_mode == "live":
        if not isinstance(gateway, LiveClobGateway):
            raise ValueError("live wallet doctrine requires LiveClobGateway")
        truth_source = GatewayLiveWalletTruthSource(gateway, cfg)
        wallet = LiveWalletDoctrine(cfg, truth_source=truth_source, mode="live", auth_cfg=auth_cfg)
    else:
        wallet = PaperWalletDoctrine(cfg, mode="paper", auth_cfg=auth_cfg)
    if event_logger is not None:
        wallet.register_event_logger(event_logger)
    return wallet


def create_wallet_controller(
    cfg: Mapping[str, Any],
    *,
    mode: str,
    gateway: Optional[BaseGateway] = None,
    event_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    auth_cfg: Optional[Mapping[str, Any]] = None,
) -> WalletDoctrineBase:
    return create_wallet_doctrine(
        cfg,
        mode=mode,
        gateway=gateway,
        event_logger=event_logger,
        auth_cfg=auth_cfg,
    )


class WalletDoctrine(PaperWalletDoctrine):
    """Backwards-compatible paper doctrine alias."""

    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        mode: str,
        auth_cfg: Optional[Mapping[str, Any]] = None,
    ) -> None:
        selected_mode = str(mode or "paper").strip().lower() or "paper"
        if selected_mode != "paper":
            raise ValueError("WalletDoctrine alias supports paper mode only; use create_wallet_doctrine for live")
        super().__init__(cfg, mode="paper", auth_cfg=auth_cfg)
