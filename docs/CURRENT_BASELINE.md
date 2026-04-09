# BRO Current Baseline

## Current Status
- Status call: `NEAR-CLOSEOUT`
- Current commit (docs streamlining in progress): `8b0d9e2bf0c2aacca72ac36535ff13a64711da68`
- Current baseline tag: `bro-wallet-exec-near-closeout-baseline` (points to prior locked baseline commit)
- Tag policy: new tag is deferred until doc-streamlining validation is complete.

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
- Wallet/execution doctrine-truth closeout and documentation streamlining.
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

