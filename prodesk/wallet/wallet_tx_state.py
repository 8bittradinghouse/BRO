from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..common import first_non_none, parse_float, utc_iso
from .wallet_types import (
    AUTHORITY_CLASS_LOCAL,
    LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX,
    LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
    NonceSnapshot,
    PendingTxSnapshot,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(x) for x in value if str(x).strip())
    return tuple()


def nonce_snapshot_from_provider(provider_payload: Mapping[str, Any], *, source_default: str) -> NonceSnapshot:
    current_nonce_raw = parse_float(provider_payload.get("current_nonce"))
    current_nonce = int(current_nonce_raw) if current_nonce_raw is not None and current_nonce_raw >= 0 else None

    raw_pending = provider_payload.get("pending_nonces", ())
    pending_nonces: list[int] = []
    if isinstance(raw_pending, Sequence) and not isinstance(raw_pending, (str, bytes)):
        for item in raw_pending:
            parsed = parse_float(item)
            if parsed is None or parsed < 0:
                continue
            pending_nonces.append(int(parsed))
    pending_nonces = sorted(set(pending_nonces))

    return NonceSnapshot(
        current_nonce=current_nonce,
        pending_nonces=tuple(pending_nonces),
        ts_utc=utc_iso(),
        source=str(first_non_none(provider_payload.get("source"), source_default)),
        healthy=bool(first_non_none(provider_payload.get("healthy"), True)),
        detail=str(first_non_none(provider_payload.get("detail"), "")),
        truth_domain=str(
            first_non_none(
                provider_payload.get("truth_domain"),
                TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            )
        ).strip()
        or TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
        authority_class=str(
            first_non_none(
                provider_payload.get("authority_class"),
                AUTHORITY_CLASS_LOCAL,
            )
        ).strip()
        or AUTHORITY_CLASS_LOCAL,
    )


def pending_tx_snapshot_from_provider(provider_payload: Mapping[str, Any], *, source_default: str) -> PendingTxSnapshot:
    pending_count = int(max(0, parse_float(provider_payload.get("pending_count")) or 0.0))
    order_ids = _string_tuple(provider_payload.get("order_ids", ()))
    tx_ids = _string_tuple(provider_payload.get("tx_ids", ()))
    exchange_order_ids = _string_tuple(provider_payload.get("exchange_order_ids", order_ids))
    exchange_client_order_ids = _string_tuple(
        provider_payload.get("exchange_client_order_ids", provider_payload.get("client_order_ids", ()))
    )
    lifecycle_plane = str(
        first_non_none(
            provider_payload.get("lifecycle_plane"),
            LIFECYCLE_PLANE_EXCHANGE_INTENT_LOCAL_TX if exchange_order_ids or exchange_client_order_ids else LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX,
        )
    ).strip() or LIFECYCLE_PLANE_ON_CHAIN_PENDING_WALLET_TX
    return PendingTxSnapshot(
        pending_count=pending_count,
        order_ids=order_ids,
        tx_ids=tx_ids,
        exchange_order_ids=exchange_order_ids,
        exchange_client_order_ids=exchange_client_order_ids,
        ts_utc=utc_iso(),
        source=str(first_non_none(provider_payload.get("source"), source_default)),
        healthy=bool(first_non_none(provider_payload.get("healthy"), True)),
        detail=str(first_non_none(provider_payload.get("detail"), "")),
        truth_domain=str(
            first_non_none(
                provider_payload.get("truth_domain"),
                TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
            )
        ).strip()
        or TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
        authority_class=str(
            first_non_none(
                provider_payload.get("authority_class"),
                AUTHORITY_CLASS_LOCAL,
            )
        ).strip()
        or AUTHORITY_CLASS_LOCAL,
        lifecycle_plane=lifecycle_plane,
    )
