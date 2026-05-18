# Baseline Doc Sync Status

> Doc Class: `Reference`
> Authority: supporting baseline-sync record only; front-of-house repo current
> truth is maintained in `docs/PROJECT_TRUTH_STATE.md`. `docs/CURRENT_BASELINE.md`
> remains a baseline/reference/history surface.

## Sync Result
VERIFIED: `docs/CURRENT_BASELINE.md` has been moved from stale commit `740f61e5d19e1cded7f57668d4e04e7ae4e0ddc9` to current runtime-resource observability commit `519f6ed188c7bde92e674512072d34ecc9d0ba1e`.

VERIFIED: A new baseline tag was created for doctrine audit compatibility:
- `bro-launch-window-continuity-baseline-20260422`
- Target commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`

## Why This Was Needed
`scripts/doctrine_truth_audit.py` requires the declared `Current baseline tag` to resolve to the declared `Current commit`. Updating only the commit field would break the audit. Updating the tag without moving the doc would leave the source-of-truth stale.

## Scope
- Documentation truth sync plus one report-only accounting semantics clarification, one fair-map runtime proof update, one report-only maker sizing feasibility visibility update, one soak-budget maker-submit taxonomy update, and one report-only reduce-only recovery diagnostics update.
- Trading runtime behavior changed only in the bounded fair-probability map scope patch: maker and taker fair maps are built separately.
- `ops/soak_budget.yaml` changed validation/report policy only: maker-scope `fair_probability_missing` is now non-actionable for maker-submit enforcement.
- `scripts/nightly_soak_report.py` now emits lifecycle-residue truth directly and fences `reduce_only_recovery*` labels as historical replay lineage when older artifacts are reread.
- No wallet/risk/live semantics change.
- No live readiness claim.

## Files Added / Updated
- `docs/CURRENT_BASELINE.md`
- `docs/JIN_CONTINUITY_PROFILE.md`
- `docs/PROJECT_TRUTH_STATE.md`
- `docs/LATEST_RUN_AUDIT_9d3c3225.md`
- `docs/LATEST_RUN_AUDIT_7e0a7dcf.md`
- `docs/LATEST_RUN_AUDIT_ec26dedd.md`
- `docs/OPEN_LIMITATIONS.md`
- `docs/RESIDUAL_FLAG_TRIAGE.md`
- `docs/BASELINE_DOC_SYNC_STATUS.md`
- `docs/COMMANDS_AND_PROOFS.md`
- `docs/NEXT_PACKET_PLAN.md`
- `ops/soak_budget.yaml`
- `scripts/nightly_soak_report.py`
- `tests/test_nightly_soak_report.py`
