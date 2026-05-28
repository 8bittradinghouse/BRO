from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..common import parse_float, utc_iso
from ..gateway import GatewayError, LiveClobGateway
from .web3_adapter import WalletWeb3Adapter
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
    LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
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

    def __init__(
        self,
        gateway: LiveClobGateway,
        cfg: Mapping[str, Any],
        *,
        web3_adapter: Optional[WalletWeb3Adapter] = None,
    ) -> None:
        self._gateway = gateway
        self._cfg = dict(cfg or {})
        self._web3_adapter: Optional[WalletWeb3Adapter] = web3_adapter
        if self._web3_adapter is None:
            try:
                from .wallet_config import load_wallet_config

                wallet_cfg = load_wallet_config(self._cfg)
                adapter = WalletWeb3Adapter(wallet_cfg)
                self._web3_adapter = adapter if adapter.is_configured() else None
            except Exception:
                self._web3_adapter = None
        self._wallet_address_override = str(self._cfg.get("expected_wallet_address", "") or "").strip()
        self._pol_balance_fallback = max(0.0, float(self._cfg.get("live_pol_balance_fallback", 1.0)))
        self._require_live_pol_balance_snapshot = bool(self._cfg.get("require_live_pol_balance_snapshot", False))
        self._approval_spender_targets = tuple(
            sorted(
                {
                    self._normalize_target(item)
                    for item in self._cfg.get("approval_spender_targets", ())
                    if self._normalize_target(item)
                }
            )
        )
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
    def _normalize_target(value: Any) -> str:
        return str(value or "").strip().lower()

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

    def _resolve_target_allowance(
        self,
        payload: Mapping[str, Any],
        *,
        target: str,
    ) -> tuple[Optional[float], Optional[str], bool]:
        target_key = self._normalize_target(target)
        mapping_roots: list[Mapping[str, Any]] = []
        for path in (
            ("allowances",),
            ("allowancesBySpender",),
            ("allowanceBySpender",),
            ("spenderAllowances",),
            ("data", "allowances"),
            ("data", "allowancesBySpender"),
            ("data", "allowanceBySpender"),
        ):
            node: Any = payload
            ok = True
            for part in path:
                if not isinstance(node, Mapping) or part not in node:
                    ok = False
                    break
                node = node.get(part)
            if ok and isinstance(node, Mapping):
                mapping_roots.append(node)

        found_target_mapping = False
        target_candidates: list[tuple[tuple[str, ...], float]] = []
        for mapping in mapping_roots:
            for raw_key, raw_value in mapping.items():
                if self._normalize_target(raw_key) != target_key:
                    continue
                found_target_mapping = True
                if isinstance(raw_value, Mapping):
                    candidates = self._extract_float_candidates(
                        raw_value,
                        [
                            ("allowance",),
                            ("allowanceDecimal",),
                            ("approved",),
                            ("available",),
                            ("data", "allowance"),
                        ],
                    )
                else:
                    parsed = parse_float(raw_value)
                    candidates = [(("value",), float(parsed))] if parsed is not None else []
                target_candidates.extend(candidates)

        if target_candidates:
            values = [value for _, value in target_candidates]
            if provider_has_material_disagreement(
                values,
                abs_tolerance=self._ambiguity_abs_tolerance,
                rel_tolerance=self._ambiguity_rel_tolerance,
            ):
                path_values = ",".join(".".join(path) + f"={value:.12f}" for path, value in target_candidates)
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
                    "live_allowance_target_ambiguous:"
                    f"{target_key}:allowed_span={allowed_span:.12f}:span={(high - low):.12f}:{path_values}",
                    True,
                )
            return float(target_candidates[0][1]), None, True

        payload_data = payload.get("data")
        explicit_target = self._normalize_target(
            payload.get("spender")
            or payload.get("approvalTarget")
            or payload.get("allowanceTarget")
            or (payload_data.get("spender") if isinstance(payload_data, Mapping) else None)
            or (payload_data.get("approvalTarget") if isinstance(payload_data, Mapping) else None)
            or (payload_data.get("allowanceTarget") if isinstance(payload_data, Mapping) else None)
        )
        if explicit_target and explicit_target == target_key:
            allowance, allowance_ambiguity = self._resolve_optional_field(
                payload=payload,
                policy=ALLOWANCE_FIELD_POLICY,
                paths=[
                    ("allowance",),
                    ("allowanceDecimal",),
                    ("approved",),
                    ("allowance", "available"),
                    ("data", "allowance"),
                ],
            )
            if allowance_ambiguity:
                return None, allowance_ambiguity, True
            if allowance is None:
                return None, f"live_allowance_target_missing:{target_key}", True
            return float(allowance), None, True

        return None, None, found_target_mapping or bool(explicit_target)

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
            provider_locked_usdc_semantics="unknown",
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
        if self._approval_spender_targets:
            matched_targets: list[str] = []
            per_target_allowances: list[float] = []
            saw_identity_surface = False
            for target in self._approval_spender_targets:
                allowance, detail, saw_target_identity = self._resolve_target_allowance(payload, target=target)
                saw_identity_surface = saw_identity_surface or saw_target_identity
                if detail:
                    return AllowanceSnapshot(
                        allowance_usdc=0.0,
                        ts_utc=utc_iso(),
                        source="canonical_live_wallet_truth",
                        target_identity_verified=False,
                        matched_spender_targets=tuple(matched_targets),
                        required_spender_targets=self._approval_spender_targets,
                        healthy=False,
                        detail=detail,
                        truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                        authority_class=AUTHORITY_CLASS_LIVE,
                    )
                if allowance is None:
                    detail = (
                        f"live_allowance_target_missing:{target}"
                        if saw_identity_surface
                        else f"live_allowance_target_identity_unverified:{target}"
                    )
                    return AllowanceSnapshot(
                        allowance_usdc=0.0,
                        ts_utc=utc_iso(),
                        source="canonical_live_wallet_truth",
                        target_identity_verified=False,
                        matched_spender_targets=tuple(matched_targets),
                        required_spender_targets=self._approval_spender_targets,
                        healthy=False,
                        detail=detail,
                        truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                        authority_class=AUTHORITY_CLASS_LIVE,
                    )
                matched_targets.append(target)
                per_target_allowances.append(max(0.0, float(allowance)))

            return AllowanceSnapshot(
                allowance_usdc=min(per_target_allowances) if per_target_allowances else 0.0,
                ts_utc=utc_iso(),
                source="canonical_live_wallet_truth",
                target_identity_verified=True,
                matched_spender_targets=tuple(matched_targets),
                required_spender_targets=self._approval_spender_targets,
                healthy=True,
                detail="",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )

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
                target_identity_verified=False,
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
                target_identity_verified=False,
                healthy=False,
                detail="live_allowance_missing",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )
        return AllowanceSnapshot(
            allowance_usdc=max(0.0, allowance),
            ts_utc=utc_iso(),
            source="canonical_live_wallet_truth",
            target_identity_verified=False,
            healthy=True,
            detail="",
            truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
            authority_class=AUTHORITY_CLASS_LIVE,
        )

    def nonce_snapshot(self) -> NonceSnapshot:
        adapter = self._web3_adapter
        if adapter is None:
            return NonceSnapshot(
                current_nonce=None,
                pending_nonces=tuple(),
                ts_utc=utc_iso(),
                source="canonical_live_wallet_truth",
                healthy=False,
                detail="web3_nonce_snapshot_unavailable",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
            )
        return adapter.canonical_nonce_snapshot(self._canonical_wallet_address())

    def pending_tx_snapshot(self) -> PendingTxSnapshot:
        adapter = self._web3_adapter
        if adapter is None:
            return PendingTxSnapshot(
                pending_count=0,
                order_ids=tuple(),
                tx_ids=tuple(),
                exchange_order_ids=tuple(),
                exchange_client_order_ids=tuple(),
                ts_utc=utc_iso(),
                source="canonical_live_wallet_truth",
                healthy=False,
                detail="web3_pending_wallet_tx_snapshot_unavailable",
                truth_domain=TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
                authority_class=AUTHORITY_CLASS_LIVE,
                lifecycle_plane=LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
            )
        return adapter.canonical_pending_tx_snapshot(self._canonical_wallet_address())

    def web3_provider_health_status(self) -> Mapping[str, Any]:
        adapter = self._web3_adapter
        if adapter is None:
            return {}
        return adapter.health_status_mapping()

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

    def _canonical_wallet_address(self) -> str:
        preferred = str(self._wallet_address_override or "").strip()
        if preferred:
            return preferred
        return str(self._gateway.wallet_address() or "").strip()
