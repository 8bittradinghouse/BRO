from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..common import parse_float, utc_iso
from ..gateway import GatewayError, LiveClobGateway
from .wallet_truth_policy import (
    ALLOWANCE_FIELD_POLICY,
    POL_BALANCE_FIELD_POLICY,
    PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
    PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
    WALLET_BALANCE_FIELD_POLICY,
    ProviderFieldPolicy,
    provider_allowed_disagreement_span,
    provider_has_material_disagreement,
)
from .wallet_types import (
    AUTHORITY_CLASS_DERIVED,
    AUTHORITY_CLASS_LIVE,
    AllowanceSnapshot,
    LiveWalletTruthSource,
    NonceSnapshot,
    OpenOrderStateSnapshot,
    PendingTxSnapshot,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
    TRUTH_DOMAIN_OPEN_ORDER_STATE,
    WalletSnapshot,
)


class GatewayLiveWalletTruthSource:
    """Live wallet truth source backed by the authenticated CLOB gateway client."""

    def __init__(self, gateway: LiveClobGateway, cfg: Mapping[str, Any]) -> None:
        self._gateway = gateway
        self._cfg = dict(cfg or {})
        self._pol_balance_fallback = max(0.0, float(self._cfg.get("live_pol_balance_fallback", 1.0)))
        self._require_live_pol_balance_snapshot = bool(self._cfg.get("require_live_pol_balance_snapshot", False))
        self._ambiguity_abs_tolerance = max(
            1e-9,
            float(
                self._cfg.get(
                    "provider_ambiguity_abs_tolerance",
                    PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT,
                )
            ),
        )
        self._ambiguity_rel_tolerance = max(
            0.0,
            float(
                self._cfg.get(
                    "provider_ambiguity_rel_tolerance",
                    PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT,
                )
            ),
        )

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

    @staticmethod
    def _extract_float_candidates(
        payload: Mapping[str, Any],
        paths: Sequence[Sequence[str]],
    ) -> list[tuple[tuple[str, ...], float]]:
        candidates: list[tuple[tuple[str, ...], float]] = []
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
            if parsed is None:
                continue
            candidates.append((tuple(path), float(parsed)))
        return candidates

    def _resolve_required_field(
        self,
        *,
        payload: Mapping[str, Any],
        policy: ProviderFieldPolicy,
        paths: Sequence[Sequence[str]],
    ) -> float:
        candidates = self._extract_float_candidates(payload, paths)
        if not candidates:
            raise GatewayError(f"{policy.label}_missing:{policy.missing_behavior}")
        values = [value for _, value in candidates]
        if provider_has_material_disagreement(
            values,
            abs_tolerance=self._ambiguity_abs_tolerance,
            rel_tolerance=self._ambiguity_rel_tolerance,
        ):
            path_values = ",".join(".".join(path) + f"={value:.12f}" for path, value in candidates)
            low = min(values)
            high = max(values)
            allowed_span = provider_allowed_disagreement_span(
                low=low,
                high=high,
                abs_tolerance=self._ambiguity_abs_tolerance,
                rel_tolerance=self._ambiguity_rel_tolerance,
            )
            raise GatewayError(
                f"{policy.label}_ambiguous:{policy.ambiguity_behavior}:"
                f"allowed_span={allowed_span:.12f}:span={(high - low):.12f}:{path_values}"
            )
        return float(candidates[0][1])

    def _resolve_optional_field(
        self,
        *,
        payload: Mapping[str, Any],
        policy: ProviderFieldPolicy,
        paths: Sequence[Sequence[str]],
    ) -> tuple[Optional[float], Optional[str]]:
        candidates = self._extract_float_candidates(payload, paths)
        if not candidates:
            return None, None
        values = [value for _, value in candidates]
        if provider_has_material_disagreement(
            values,
            abs_tolerance=self._ambiguity_abs_tolerance,
            rel_tolerance=self._ambiguity_rel_tolerance,
        ):
            path_values = ",".join(".".join(path) + f"={value:.12f}" for path, value in candidates)
            low = min(values)
            high = max(values)
            allowed_span = provider_allowed_disagreement_span(
                low=low,
                high=high,
                abs_tolerance=self._ambiguity_abs_tolerance,
                rel_tolerance=self._ambiguity_rel_tolerance,
            )
            return (
                None,
                f"{policy.label}_ambiguous:{policy.ambiguity_behavior}:"
                f"allowed_span={allowed_span:.12f}:span={(high - low):.12f}:{path_values}",
            )
        return float(candidates[0][1]), None

    def _balance_allowance_payload(self) -> Mapping[str, Any]:
        payload = self._gateway.get_collateral_balance_allowance()
        if not isinstance(payload, Mapping):
            raise GatewayError(f"invalid balance/allowance payload type: {type(payload).__name__}")
        return payload

    def wallet_snapshot(self) -> WalletSnapshot:
        payload = self._balance_allowance_payload()
        usdc = self._resolve_required_field(
            payload=payload,
            policy=WALLET_BALANCE_FIELD_POLICY,
            paths=[
                ("balance",),
                ("balanceDecimal",),
                ("available",),
                ("availableBalance",),
                ("balance", "available"),
                ("balances", "balance"),
                ("data", "balance"),
            ],
        )
        pol, pol_ambiguity = self._resolve_optional_field(
            payload=payload,
            policy=POL_BALANCE_FIELD_POLICY,
            paths=[
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
        if pol_ambiguity:
            pol_healthy = False
            pol_detail = pol_ambiguity
        if pol is not None:
            pol_balance = max(0.0, float(pol))
            if not pol_ambiguity:
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
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def allowance_snapshot(self) -> AllowanceSnapshot:
        payload = self._balance_allowance_payload()
        allowance, allowance_ambiguity = self._resolve_optional_field(
            payload=payload,
            policy=ALLOWANCE_FIELD_POLICY,
            paths=[
                ("allowance",),
                ("allowanceDecimal",),
                ("approved",),
                ("allowance", "available"),
                ("allowances", "allowance"),
                ("data", "allowance"),
            ],
        )
        if allowance_ambiguity:
            return AllowanceSnapshot(
                allowance_usdc=0.0,
                ts_utc=utc_iso(),
                source="canonical_live_wallet_truth",
                healthy=False,
                detail=allowance_ambiguity,
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )
        if allowance is None:
            return AllowanceSnapshot(
                allowance_usdc=0.0,
                ts_utc=utc_iso(),
                source="canonical_live_wallet_truth",
                healthy=False,
                detail="live_allowance_missing",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )
        return AllowanceSnapshot(
            allowance_usdc=max(0.0, allowance),
            ts_utc=utc_iso(),
            source="canonical_live_wallet_truth",
            healthy=True,
            detail="",
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def nonce_snapshot(self) -> NonceSnapshot:
        # The official CLOB clients are not the canonical owner of on-chain nonce truth.
        # Deposit-wallet / relayer lanes must source nonce truth from the relayer surface instead.
        return NonceSnapshot(
            current_nonce=None,
            pending_nonces=tuple(),
            ts_utc=utc_iso(),
            source="canonical_live_wallet_truth",
            healthy=False,
            detail="nonce_snapshot_unavailable",
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def pending_tx_snapshot(self) -> PendingTxSnapshot:
        # Open orders are not canonical pending-wallet-transaction truth.
        # Keep this surface explicit fail-closed for strict live pending-tx truth.
        return PendingTxSnapshot(
            pending_count=0,
            order_ids=tuple(),
            ts_utc=utc_iso(),
            source="canonical_live_wallet_truth",
            healthy=False,
            detail="pending_wallet_tx_snapshot_unavailable",
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def open_order_state_snapshot(self) -> OpenOrderStateSnapshot:
        open_orders = self._gateway.get_open_orders()
        order_ids = tuple(sorted({str(o.order_id) for o in open_orders if str(o.order_id).strip()}))
        return OpenOrderStateSnapshot(
            open_count=len(order_ids),
            order_ids=order_ids,
            ts_utc=utc_iso(),
            source="open_order_state",
            healthy=True,
            detail="",
            truth_domain=TRUTH_DOMAIN_OPEN_ORDER_STATE,
            authority_class=AUTHORITY_CLASS_DERIVED,
        )
