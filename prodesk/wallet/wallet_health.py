from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .wallet_types import (
    AUTHORITY_CLASS_LIVE,
    TRUTH_DOMAIN_CANONICAL_LIVE_WALLET,
)


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _is_canonical_live_truth_surface(snapshot: Mapping[str, Any]) -> bool:
    return (
        str(snapshot.get("truth_domain") or "").strip() == TRUTH_DOMAIN_CANONICAL_LIVE_WALLET
        and str(snapshot.get("authority_class") or "").strip() == AUTHORITY_CLASS_LIVE
    )


def build_wallet_health_contract(*, status: Mapping[str, Any], enforce_startup_barrier: bool = True) -> Dict[str, Any]:
    mode = str(status.get("mode") or "").strip().lower()
    canonical = _as_mapping(status.get("canonical_live_wallet_truth"))
    wallet_snapshot = _as_mapping(canonical.get("wallet_snapshot"))
    allowance_snapshot = _as_mapping(canonical.get("allowance_snapshot"))
    nonce_snapshot = _as_mapping(canonical.get("nonce_snapshot"))
    pending_wallet_tx_snapshot = _as_mapping(canonical.get("pending_wallet_tx_snapshot"))
    web3_provider_health = _as_mapping(status.get("web3_provider_health"))
    gas_reserve_policy = _as_mapping(status.get("gas_reserve_policy"))
    reconcile_state = _as_mapping(status.get("integrity_tripwire_reconcile_state"))
    reconcile = reconcile_state or _as_mapping(status.get("last_reconcile_result"))

    gas_balance = float(wallet_snapshot.get("pol_balance", 0.0) or 0.0)
    gas_reserve_min = float(status.get("min_pol_gas_reserve", 0.0) or 0.0)
    gas_ok = bool(gas_reserve_policy.get("conservative_fail_floor_ok", gas_balance >= gas_reserve_min))
    gas_target_ok = bool(gas_reserve_policy.get("conservative_target_ok", gas_ok))

    stable_total = float(wallet_snapshot.get("usdc_balance", 0.0) or 0.0)
    protected = float(wallet_snapshot.get("protected_reserve_usdc", 0.0) or 0.0)
    open_reserved = float(status.get("locked_usdc", 0.0) or 0.0)
    deployable = float(status.get("deployable_usdc", 0.0) or 0.0)

    approval_target_identity_verified = bool(allowance_snapshot.get("target_identity_verified", False))
    approval_spender_targets_matched = list(allowance_snapshot.get("matched_spender_targets") or [])
    approval_spender_targets_required = list(allowance_snapshot.get("required_spender_targets") or [])
    approval_ok = bool(
        allowance_snapshot.get("healthy", True)
        and (
            not approval_spender_targets_required
            or approval_target_identity_verified
        )
    )
    nonce_ok = bool(nonce_snapshot.get("healthy", False))
    reconcile_ok = bool(reconcile.get("healthy", False))
    halted = bool(status.get("halted", False))
    authority_status_class = str(status.get("authority_status_class") or "bootstrap_non_authoritative").strip()
    authoritative_refresh_completed = bool(status.get("authoritative_refresh_completed", False))
    startup_authority_ready = bool(status.get("startup_authority_ready", False))
    order_capable_live = bool(status.get("order_capable_live", False))
    startup_ready = bool(startup_authority_ready and authoritative_refresh_completed and authority_status_class == "authoritative")
    # Submit readiness is stage-relative. In paper mode, authoritative startup
    # readiness is sufficient for paper submission. In live mode, the separate
    # live-order capability gate must also be true.
    order_submit_eligible = bool(startup_ready if mode != "live" else (order_capable_live and startup_ready))
    reconcile_scope = str(reconcile.get("scope") or "integrity_tripwire")
    reservation_mismatch_candidate = bool(status.get("reservation_mismatch_candidate", False))
    reservation_mismatch_delta_usdc = float(status.get("reservation_mismatch_delta_usdc", 0.0) or 0.0)
    reservation_mismatch_detail = str(status.get("reservation_mismatch_detail") or "")

    canonical_live_nonce_source = str(nonce_snapshot.get("source") or "")
    canonical_live_nonce_detail = str(nonce_snapshot.get("detail") or "")
    canonical_live_pending_wallet_tx_source = str(pending_wallet_tx_snapshot.get("source") or "")
    canonical_live_pending_wallet_tx_detail = str(pending_wallet_tx_snapshot.get("detail") or "")

    canonical_live_nonce_available = bool(
        mode == "live"
        and _is_canonical_live_truth_surface(nonce_snapshot)
        and bool(nonce_snapshot.get("healthy", False))
        and (nonce_snapshot.get("current_nonce") is not None)
    )
    canonical_live_pending_wallet_tx_available = bool(
        mode == "live"
        and _is_canonical_live_truth_surface(pending_wallet_tx_snapshot)
        and bool(pending_wallet_tx_snapshot.get("healthy", False))
    )
    web3_provider_trustworthy = bool(
        web3_provider_health.get("provider_trustworthy", True)
        if web3_provider_health
        else True
    )
    web3_provider_reason = str(
        web3_provider_health.get("health_reasons", ["web3_provider_unhealthy"])[0]
        if web3_provider_health
        else ""
    )
    live_truth_gap_reasons: List[str] = []
    if mode == "live" and not canonical_live_nonce_available:
        detail = canonical_live_nonce_detail or "canonical_live_nonce_unavailable"
        live_truth_gap_reasons.append(f"canonical_live_nonce_unavailable:{detail}")
    if mode == "live" and not canonical_live_pending_wallet_tx_available:
        detail = canonical_live_pending_wallet_tx_detail or "canonical_live_pending_wallet_tx_unavailable"
        live_truth_gap_reasons.append(f"canonical_live_pending_wallet_tx_unavailable:{detail}")

    reasons: List[str] = []
    if not wallet_snapshot:
        reasons.append("canonical_wallet_snapshot_missing")
    if not allowance_snapshot:
        reasons.append("canonical_allowance_snapshot_missing")
    if not nonce_snapshot:
        reasons.append("canonical_nonce_snapshot_missing")
    if not pending_wallet_tx_snapshot:
        reasons.append("canonical_pending_wallet_tx_snapshot_missing")
    if halted:
        reasons.append(str(status.get("halt_reason") or "wallet_halted"))
    if enforce_startup_barrier and not startup_ready:
        reasons.append("bootstrap_non_authoritative")
    if mode == "live" and not order_capable_live:
        reasons.append("order_capable_live_disabled")
    if mode == "live" and order_capable_live and not canonical_live_nonce_available:
        reasons.append("canonical_live_nonce_unavailable")
    if mode == "live" and order_capable_live and not canonical_live_pending_wallet_tx_available:
        reasons.append("canonical_live_pending_wallet_tx_unavailable")
    if mode == "live" and web3_provider_health and not web3_provider_trustworthy:
        reasons.append(web3_provider_reason or "web3_provider_unhealthy")
    if not gas_ok:
        reasons.append("gas_reserve_insufficient")
    if not approval_ok:
        reasons.append(str(allowance_snapshot.get("detail") or "approval_unhealthy"))
    if not nonce_ok:
        reasons.append(str(nonce_snapshot.get("detail") or "nonce_unhealthy"))
    if not reconcile_ok:
        reasons.append(str(reconcile.get("reason") or "reconcile_unhealthy"))

    wallet_health_ok = not reasons

    return {
        "gas_balance": gas_balance,
        "gas_reserve_min": gas_reserve_min,
        "gas_ok": bool(gas_ok),
        "gas_target_ok": bool(gas_target_ok),
        "gas_reserve_policy": gas_reserve_policy,
        "gas_balance_usd_estimate": gas_reserve_policy.get("usd_balance_estimate"),
        "stable_balance_total": stable_total,
        "protected_reserve": protected,
        "open_reserved": open_reserved,
        "deployable_capital": deployable,
        "approval_ok": bool(approval_ok),
        "approval_target_identity_verified": bool(approval_target_identity_verified),
        "approval_spender_targets_matched": approval_spender_targets_matched,
        "approval_spender_targets_required": approval_spender_targets_required,
        "nonce_ok": bool(nonce_ok),
        "reconcile_ok": bool(reconcile_ok),
        "wallet_health_ok": bool(wallet_health_ok),
        "wallet_health_reasons": reasons,
        "authority_status_class": authority_status_class,
        "order_capable_live": bool(order_capable_live),
        "order_submit_eligible": bool(order_submit_eligible),
        "startup_authority_ready": bool(startup_authority_ready),
        "authoritative_refresh_completed": bool(authoritative_refresh_completed),
        "pending_wallet_tx_ok": bool(pending_wallet_tx_snapshot.get("healthy", False)),
        "pending_wallet_tx_detail": str(pending_wallet_tx_snapshot.get("detail") or ""),
        "canonical_live_nonce_available": bool(canonical_live_nonce_available),
        "canonical_live_pending_wallet_tx_available": bool(canonical_live_pending_wallet_tx_available),
        "canonical_live_nonce_source": canonical_live_nonce_source,
        "canonical_live_nonce_detail": canonical_live_nonce_detail,
        "canonical_live_pending_wallet_tx_source": canonical_live_pending_wallet_tx_source,
        "canonical_live_pending_wallet_tx_detail": canonical_live_pending_wallet_tx_detail,
        "web3_provider_health": web3_provider_health,
        "web3_provider_trustworthy": bool(web3_provider_trustworthy),
        "live_truth_gap_reasons": live_truth_gap_reasons,
        "reconcile_scope": reconcile_scope,
        "reservation_mismatch_candidate": bool(reservation_mismatch_candidate),
        "reservation_mismatch_delta_usdc": float(reservation_mismatch_delta_usdc),
        "reservation_mismatch_detail": reservation_mismatch_detail,
    }
