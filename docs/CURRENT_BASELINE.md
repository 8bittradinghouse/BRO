# BRO Current Baseline

## Current Status
- Status call: `NEAR-CLOSEOUT`
- Current commit: `740f61e5d19e1cded7f57668d4e04e7ae4e0ddc9`
- Current baseline tag: `bro-wallet-exec-near-closeout-baseline-20260409`
- Docs streamlining: complete.

## Canonical Laws / Docs (Authoritative)
- `docs/CURRENT_BASELINE.md` (this file, top-level current-truth entrypoint)
- `docs/DOCTRINE_RUNBOOK.md`
- `docs/BASELINE_LOCK_20260408.md`
- `docs/DOCTRINE_LIMITATION_PHRASES.json`

## Open Limitations (Canonical Phrase Set)
- canonical live nonce truth unavailable
- canonical live pending-wallet-tx truth unavailable
- strict order-capable live remains fail-closed
- reconcile is integrity tripwire, not full ledger accounting

## Current Phase
- Wallet/execution doctrine-truth closure and baseline lock are complete for this lane.
- Edge strategy lanes remain frozen.

## Next Phase
- Repository/file-tree cleanup pass with no strategy/runtime behavior drift.

## What Is Authoritative
- Wallet authority is canonical for capital truth and capital veto.
- Risk engine is canonical for admissibility veto.
- Final order permission requires both wallet and risk allow.
- Canonical live truth gaps above remain fail-closed in strict order-capable live paths.

## Documentation Classes
- `Authoritative`: active doctrine/lock surfaces used for current operational truth.
- `Reference`: supporting context that may explain implementation decisions but is not primary authority.
- `Archive`: historical snapshots retained for traceability only; not current authority.
