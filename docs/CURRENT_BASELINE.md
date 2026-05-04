# BRO Current Baseline

## Authority Role
- Baseline/reference/history surface only.
- Front-of-house repo current truth lives in `docs/PROJECT_TRUTH_STATE.md`.
- This file preserves the active baseline bundle and supporting baseline anchors;
  it is not the top-level current-truth entrypoint.

## Current Status
- Status call: `LAUNCH-WINDOW_NEAR-CLOSEOUT`
- Current commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Current baseline tag: `bro-launch-window-continuity-baseline-20260422`
- Latest clean-tree validation anchor: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- Latest clean current-code release anchor: `7bbde42c-003a-4f57-b59a-7ce138224075`
- Latest post-patch runtime proof: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Latest packet-1 smoke validation: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Baseline truth timestamp: `2026-04-22T00:00:00Z`
- Current packet state: uncommitted continuity/doc/report-truth/fair-map/soak-budget-taxonomy worktree changes after clean baseline commit.
- Docs streamlining: complete.
- Current active truth and pickup have moved beyond this baseline surface; use
  `docs/PROJECT_TRUTH_STATE.md` for the clean current-code release anchor and
  current father-frame lane.

## Baseline Bundle / Supporting Surfaces
- `docs/PROJECT_TRUTH_STATE.md` (front-of-house repo current truth screen)
- `docs/CURRENT_BASELINE.md` (this baseline/reference surface)
- `docs/DOCTRINE_RUNBOOK.md`
- `docs/BASELINE_LOCK_20260408.md`
- `docs/DOCTRINE_LIMITATION_PHRASES.json`
- `docs/LATEST_RUN_AUDIT_9d3c3225.md`
- `docs/LATEST_RUN_AUDIT_7e0a7dcf.md`
- `docs/LATEST_RUN_AUDIT_ec26dedd.md`
- `docs/RESIDUAL_FLAG_TRIAGE.md`

## Open Limitations (Canonical Phrase Set)
- canonical live nonce truth unavailable
- canonical live pending-wallet-tx truth unavailable
- strict order-capable live remains fail-closed
- reconcile is integrity tripwire, not full ledger accounting

## Latest Clean-Tree Evidence Anchor
- Run ID: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- Profile: `paper_universal`
- Run window: `2026-04-21T20:20:58.496Z` through `2026-04-21T20:41:29.394Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Git dirty: `false`
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `3d49a999b9a5d1e748b527152cbf08711cdf4147b1339b376f298faefb26bea5`
- Canonical validation: `status=pass`, all validator exit codes `0`
- Runtime classification: `VALID_ACTIVE`
- Readiness gate: highest passing stage `paper`; blocking stage `pilot_live`
- Soak hardening gate: `ok=true`, `finding_count=0`

## Latest Post-Patch Runtime Proof
- Run ID: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Profile: `paper_universal`
- Run window: `2026-04-22T01:44:26.275Z` through `2026-04-22T01:55:02.361Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Packet state: uncommitted post-clean-baseline patch proof; not a clean release anchor.
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `b901be301201c1cb58a6486bc28c1366e4bccba4d6f047bf545bc34a50905e78`
- Canonical validation: `status=pass`, all validator exit codes `0`
- Runtime classification: `VALID_ACTIVE`
- Readiness gate: highest passing stage `paper`; blocking stage `pilot_live`
- Soak hardening gate: `ok=true`, `finding_count=1`
- Soak hardening finding: `soak_maker_submits_too_low:16.000000<min:29.000000`
- `fair_probability_missing` scope proof: `13` maker rows, `0` taker rows.

## Latest Packet-1 Smoke Validation
- Run ID: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Profile: `paper_universal`
- Run window: `2026-04-22T02:25:25.748Z` through `2026-04-22T02:30:56.560Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Packet state: uncommitted post-clean-baseline packet-1 proof; not a clean release anchor.
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `b901be301201c1cb58a6486bc28c1366e4bccba4d6f047bf545bc34a50905e78`
- Canonical validation: `status=policy_failed`, `script_exit_code=2`
- Runtime classification: `VALID_ACTIVE`
- Policy-fail cause: requested 5-minute run was shorter than canonical soak budget (`5.4464<10.0` minutes; `11<20` status rows).
- Validator exit codes: all `0` except `soak_hardening_gate=2` and `soak_hardening_gate_replay=2`.
- Packet-1 proof: maker submit enforcement saw `1` actionable maker row and `1` maker submit; `fair_probability_missing` maker rows were counted non-actionable for this enforcement path.

## Current Phase
- Launch-window near-closeout remains paper-stage only.
- Current active BRO-local truth and pickup have since moved into post-packet
  G-frame restoration; use `docs/PROJECT_TRUTH_STATE.md`,
  `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`, and
  `docs/NEXT_PACKET_PLAN.md` for live truth and lane order.
- The active lane is truth-maintenance and residual diagnostic closeout after the latest clean-tree 20-minute wiring run and the post-patch 10-minute runtime proof.
- Edge strategy lanes remain frozen unless explicitly reopened under a separate plan.
- No pilot/live deployment claim is authorized by this baseline.

## Verified Closed Since Prior Baseline
- Setup-lock/fingerprint drift was repeatedly found and current prestart authority uses canonical config loading.
- Post-wallet risk revalidation exists after wallet authorization and size adjustment.
- Missing `order_id` on wallet submission confirmation does not consume a pending reservation lock.
- Cancel accounting is hardened around confirmed cancels.
- Canonical active/postrun validation subprocesses are timeout-bounded.
- Runtime resource observability is wired through status/report/readiness surfaces.
- Latest clean-tree 20-minute wiring run passed the validator chain.
- Stage-reduction delta accounting now exposes event-row overlap fields in `scripts/nightly_soak_report.py`; the latest run's `MAKER_TAKER_SELECTIVE` mismatch is classified as report-accounting overlap, not a proven runtime defect.
- `fair_probability_missing` taker-scope closure is runtime-proved by post-patch run `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`: taker rows `0`; remaining maker rows `13` are maker-gated protective behavior from this evidence.
- `size_notional_bounds` / `sizing_reject` is classified as a fail-closed maker floor/cap feasibility constraint: maker hard floor `100.0` USDC with maker hard max `800.0` shares is infeasible below midpoint `0.125`.
- Current-code report replay now surfaces maker sizing reject counts and min-notional/max-shares conflict rows.
- `soak_maker_submits_too_low` was diagnosed as a maker-submit enforcement taxonomy gap. `ops/soak_budget.yaml` now treats maker-scope `fair_probability_missing` as non-actionable for this enforcement path. Current-code replays of `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6` and `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` pass with `finding_count=0`.
- Requested 5-minute smoke run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d` verified packet-1 behavior in runtime: maker submit enforcement required `1` submit and observed `1` submit.
- `reduce_only_recovery_size_cap_unavailable` is classified in current artifacts as flat/wrong-side no-op local rejection, not a proven non-flat recovery weakness: clean anchor `16/16`, post-patch run `2/2`, and smoke run `1/1` local size-cap rejects were `flat_or_wrong_side_noop_only`.
- `required_book_feed_disconnected_rows=1` is classified in current artifacts as startup/bootstrap telemetry: first status row only, `ws_slo_bootstrap_active=1`, no order attempts/actions, connected by the next status row, websocket audits passed.
- Current-code nightly report now surfaces `reduce_only_recovery` diagnostics, including flat/wrong-side vs non-flat/unknown size-cap reject classification.

## Verified Open / Residual Candidates
- A literal 5-minute canonical session does not satisfy the current canonical 10-minute soak budget. This is a validation-duration constraint, not a proven bot behavior defect.
- Whether maker floor/cap policy should adapt for low-priced markets remains an explicit future policy/strategy decision, not a current defect fix.

## What Is Authoritative
- Wallet authority is canonical for capital truth and capital veto.
- Risk engine is canonical for admissibility veto.
- Final order permission requires both wallet and risk allow.
- Canonical live truth gaps above remain fail-closed in strict order-capable live paths.
- Passing paper validators is not equivalent to pilot/live readiness.
- Residual flags must be classified as expected protective behavior, neutral telemetry, true candidate, or `UNKNOWN`; they must not be silently collapsed into "all clear."

## Documentation Classes
- `Authoritative`: active doctrine/lock surfaces used for current operational truth.
- `Continuity`: operator/team handoff surfaces used to preserve launch-window working context.
- `Reference`: supporting context that may explain implementation decisions but is not primary authority.
- `Archive`: historical snapshots retained for traceability only; not current authority.
- `Baseline`: baseline/reference bundles kept to preserve anchor runs and their evidence context.
