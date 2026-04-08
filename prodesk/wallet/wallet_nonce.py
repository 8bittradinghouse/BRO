from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..common import first_non_none, parse_float, utc_iso
from .wallet_types import (
    AUTHORITY_CLASS_LOCAL,
    NonceSnapshot,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
)


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
