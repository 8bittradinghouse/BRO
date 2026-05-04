# BRO Wallet / Execution Packet Gap Map

> Doc Class: `Reference`
> Authority: supporting packet context only; front-of-house repo current truth
> is maintained in `docs/PROJECT_TRUTH_STATE.md`, and downstream fighter/runtime
> policy remains in `docs/DOCTRINE_RUNBOOK.md`.

## Authority Scope
- Packet scope is wallet/capital/tx discipline only.
- Strategy lanes (maker/taker/sniper) are unchanged by intent.
- Risk engine remains final order-admissibility authority.

## Already Present Before Packet
- Wallet doctrine class and authorization flow existed in `prodesk/wallet_doctrine.py`.
- Transaction lifecycle authority existed in `prodesk/tx_manager.py`.
- Wallet authorization was already wired into order submission.
- Reconciliation and halt/reject semantics already existed in doctrine implementation.

## Missing Before Packet
- Clean module split for wallet authority responsibilities.
- Canonical wallet status contract surfaced into status rows/reports.
- First-class wallet telemetry event surface.
- Explicit wallet constants and validation coverage for live identity/spender/treasury posture.
- Compatibility-first migration path to preserve existing imports while splitting internals.

## Built In This Packet
- New package split under `prodesk/wallet/` with explicit responsibility modules.
- Compatibility facade retained at `prodesk/wallet_doctrine.py`.
- Event logger registration + wallet event emissions on health/approval/reservation/reconcile paths.
- Wallet status contract surfaced in executor status rows and nightly reporting.
- Wallet constants and fail-closed validation added to runtime config schema + profile/yaml surfaces.
- Expanded tests for wallet authorization fail-closed behavior, reservation lifecycle, status contract, and report surfaces.
- Authority hardening:
  - authoritative startup barrier with explicit `bootstrap_non_authoritative` class
  - order-capable live explicit opt-in (`auth.live_order_submission_enabled`) with default-off posture
  - strict live nonce/pending gates bound only to canonical live wallet truth
  - canonical truth availability surfaced via authority-classified fields (`canonical_live_*_available`)
  - truth-domain split: canonical live truth vs local tx lifecycle vs open-order state vs reconcile tripwire state
  - post-lock reconcile rollback (no success return after failed post-lock reconcile)
- Reservation leak hardening:
  - idempotent release/settle behavior
  - non-negative lock totals and orphan-lock invariants
- Provider ambiguity hardening:
  - payload collision rule for candidate paths (material disagreement => unhealthy/fail-closed)

## Explicit Non-Goals
- No maker/taker/sniper strategy retuning.
- No risk-cap loosening.
- No replacement of `RiskEngine.validate_order` authority by wallet preview logic.
