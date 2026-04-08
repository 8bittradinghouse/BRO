from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..common import first_non_none, parse_float, utc_iso
from .wallet_types import (
    AUTHORITY_CLASS_LOCAL,
    PendingTxSnapshot,
    TRUTH_DOMAIN_LOCAL_TX_LIFECYCLE,
)


def pending_tx_snapshot_from_provider(provider_payload: Mapping[str, Any], *, source_default: str) -> PendingTxSnapshot:
    pending_count = int(max(0, parse_float(provider_payload.get("pending_count")) or 0.0))
    raw_ids = provider_payload.get("order_ids", ())
    if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes)):
        order_ids = tuple(str(x) for x in raw_ids if str(x).strip())
    else:
        order_ids = tuple()
    return PendingTxSnapshot(
        pending_count=pending_count,
        order_ids=order_ids,
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
