# BRO Doctrine/Truth Closure Diagnosis — 2026-04-08

> Doc Class: `Reference`
> Authority: supporting diagnosis record; canonical operational truth is maintained in `docs/CURRENT_BASELINE.md`, `docs/DOCTRINE_RUNBOOK.md`, and `docs/BASELINE_LOCK_20260408.md`.

## Executive Summary
- `VERIFIED`: Canonical live nonce truth is unavailable in current live provider path.
- `VERIFIED`: Canonical live pending-wallet-tx truth is unavailable in current live provider path.
- `VERIFIED`: Strict order-capable live remains fail-closed when those canonical truths are unavailable.
- `VERIFIED`: Authority boundary split (canonical live vs local lifecycle vs open-order vs reconcile tripwire) is present and enforced in current code paths.
- `INFERRED`: Provider payload robustness is solid but still under-covered for representative schema drift scenarios.
- `INFERRED`: Doctrine/report wording is mostly aligned but still vulnerable to phrase drift without a single canonical phrase source + bounded audit guard.

Red-stop check:
- `VERIFIED`: No new doctrine red issue discovered that requires halting this pass.

## A) Canonical Live Truth Assessment
- `VERIFIED` (`prodesk/wallet/wallet_provider.py`):
  - `nonce_snapshot()` returns unhealthy with `detail="nonce_snapshot_unavailable"`.
  - `pending_tx_snapshot()` returns unhealthy with `detail="pending_wallet_tx_snapshot_unavailable"`.
- `VERIFIED` (`prodesk/wallet/wallet_controller.py`):
  - Canonical availability is domain/authority-class gated via `_canonical_live_nonce_available()` and `_canonical_live_pending_wallet_tx_available()`.
  - Strict live checks reject when canonical surfaces are unhealthy or not canonical-live.
- `VERIFIED` (`prodesk/wallet/wallet_health.py`):
  - Status contract exposes canonical-live availability/detail + `live_truth_gap_reasons`.
  - `order_capable_live` and `order_submit_eligible` are explicit and fail closed under missing canonical truth.
- Readiness implication:
  - `VERIFIED`: strict order-capable live is non-ready and non-submittable without canonical live nonce/pending truth.

## B) Provider/Payload Truth Assessment
- `VERIFIED` (`prodesk/wallet/wallet_provider.py`):
  - Required field resolution (`_resolve_required_field`) hard-fails on ambiguity/missing.
  - Optional field resolution (`_resolve_optional_field`) returns explicit ambiguity detail.
  - Ambiguity threshold logic exists but tolerances are currently configured inline per provider instance.
- `VERIFIED` (`tests/test_wallet_provider.py`):
  - Covers required ambiguity fail and optional POL ambiguity unhealthy behavior.
  - Covers "pending-wallet-tx truth is not open-order surrogate."
- `INFERRED`:
  - Coverage lacks richer representative payload fixture matrix; schema drift protection is present but not yet deeply fixture-driven.

## C) Authority-Boundary Guard Assessment
- `VERIFIED`:
  - Canonical truth domains and authority classes are explicit (`prodesk/wallet/wallet_types.py`).
  - Local tx lifecycle snapshots come from tx manager and are labeled local (`prodesk/tx_manager.py`, wallet controller status surfaces).
  - Open-order state is separate derived surface (`wallet_provider.open_order_state_snapshot()`).
- `VERIFIED`:
  - Deprecated compatibility surfaces are emitted in status for boundary compatibility (`wallet_controller.status`) and classified as deprecated.
- `INFERRED`:
  - Additional targeted safeguards would reduce future contributor risk of accidentally consuming deprecated surfaces in authority logic.

## D) Doctrine/Report Alignment Assessment
- `VERIFIED`:
  - Runbook states fail-closed startup and reconcile tripwire scope (`docs/DOCTRINE_RUNBOOK.md`).
  - Nightly wallet authority fallback is now explicitly legacy non-authoritative (`scripts/nightly_soak_report.py`).
- `INFERRED`:
  - Wording is semantically close but not centralized to a single canonical phrase source, which leaves future drift risk.
- `UNKNOWN`:
  - Whether future non-canonical docs/handoffs could reintroduce contradictory wording without a bounded doctrine audit guard in CI.

## Verified Open Limitations
- Canonical live nonce truth unavailable.
- Canonical live pending-wallet-tx truth unavailable.
- Strict order-capable live remains fail-closed accordingly.
- Reconciliation remains integrity-tripwire scoped, not full accounting ledger truth.
