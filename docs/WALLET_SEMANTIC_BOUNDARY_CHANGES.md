# Wallet Semantic Boundary Changes

This file records naming and authority-boundary hardening applied in the wallet/execution authority packet.

## Before/After Name Map

| Old Surface Name | New Truthful Surface Name | Authority Class |
| --- | --- | --- |
| `wallet_snapshot` (flat top-level status) | `canonical_live_wallet_truth.wallet_snapshot` | `live` |
| `allowance_snapshot` (flat top-level status) | `canonical_live_wallet_truth.allowance_snapshot` | `live` |
| `nonce_snapshot` (ambiguous legacy use) | `canonical_live_wallet_truth.nonce_snapshot` | `live` |
| `pending_tx_snapshot` (ambiguous pending/open/local conflation) | `canonical_live_wallet_truth.pending_wallet_tx_snapshot` | `live` |
| tx-manager `pending_tx_snapshot` consumed as live nonce/pending truth | `local_tx_lifecycle_state.pending_tx_snapshot` | `local` |
| tx-manager `nonce_snapshot` consumed as live nonce truth | `local_tx_lifecycle_state.nonce_snapshot` | `local` |
| open orders inferred via pending wallet tx fields | `open_order_state` | `derived` |
| reconcile status interpreted as full accounting truth | `integrity_tripwire_reconcile_state` | `derived` |
| startup status implied by first refresh attempt | `authority_status_class=bootstrap_non_authoritative` until authoritative refresh succeeds | `bootstrap` |
| legacy flat status compatibility fields on authority path | compatibility-only legacy keys (deprecated), excluded from authoritative decision path | `deprecated` |
| implicit live order capability in `mode=live` | explicit opt-in `auth.live_order_submission_enabled` + `order_capable_live` + `order_submit_eligible` | `bootstrap` / `live` |
| source-string-based live truth assumptions | truth-domain + authority-class classified availability fields (`canonical_live_*_available`) | `live` |

## Semantic Scope and Precedence

- `BRO_CANONICAL_DOCTRINE.txt` is the semantic root.
- Wallet-domain live names are authoritative inside wallet/startup authority
  only.
- They must not be reused as market-actionability terms.
- `authority_status_class`, `startup_authority_ready`,
  `authoritative_refresh_completed`, `order_capable_live`, and
  `order_submit_eligible` are wallet/startup-domain terms only.
- `canonical_live_wallet_truth` outranks `local_tx_lifecycle_state`,
  `open_order_state`, and `integrity_tripwire_reconcile_state` for live wallet
  authority.
- Local and derived wallet surfaces may explain or protect, but they may not
  masquerade as canonical live wallet truth.

## Notes

- `authority_status_class` live wallet/startup vocabulary is:
  - `authoritative`
  - `bootstrap_non_authoritative`
- `startup_authority_ready` and `authoritative_refresh_completed` are
  wallet/startup readiness facts only; they do not create live submit
  capability, market actionability, or weapon permission by themselves.
- `legacy_fallback_non_authoritative` is a report/readout fallback for older or
  incomplete wallet artifacts; it is not a live wallet authority contract term.
- `order_submit_eligible` is a wallet/startup-domain submit-readiness term only.

- Deprecated legacy surfaces are retained only at compatibility boundaries.
- Authoritative decisions consume canonical domain surfaces, not deprecated aliases.
- Strict live gates (`require_live_nonce_snapshot`, `require_live_pending_tx_snapshot`) only accept canonical live truth.
- Local tx-manager lifecycle state and open-order state are never canonical live nonce/pending-wallet-tx truth.
