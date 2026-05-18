# Latest Run Audit: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`

## Run Identity
- Run ID: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Session ID: `5f8bc852-0df2-4a3f-94f8-ab01e1b2084e`
- Profile: `paper_universal`
- Start: `2026-04-22T01:44:26.275Z`
- Stop: `2026-04-22T01:55:02.361Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Packet state: uncommitted post-clean-baseline patch proof; not a clean release anchor.
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `b901be301201c1cb58a6486bc28c1366e4bccba4d6f047bf545bc34a50905e78`

## Artifact Paths
- Run manifest: `logs_exec/paper_universal/run_manifest_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.json`
- Run contract: `logs_exec/paper_universal/run_contract_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.json`
- Report directory: `logs_exec/paper_universal/reports/7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Status slice: `logs_exec/paper_universal/sessions/5f8bc852-0df2-4a3f-94f8-ab01e1b2084e/slices/status_slice.jsonl`
- Events slice: `logs_exec/paper_universal/sessions/5f8bc852-0df2-4a3f-94f8-ab01e1b2084e/slices/events_slice.jsonl`
- Errors slice: `logs_exec/paper_universal/sessions/5f8bc852-0df2-4a3f-94f8-ab01e1b2084e/slices/errors_slice.jsonl`
- Export archive: `exports/paper_session_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.zip`

## Gate Results
- `canonical_paper_validation.json`: `status=pass`, `script_exit_code=0`
- `validation_summary.json`: `ok=true`, `overall_exit_code=0`
- Validator exit codes: all `0`
- `readiness_gate.json`: highest passing stage `paper`; blocking stage `pilot_live`
- `soak_hardening_gate.json`: `ok=true`, `finding_count=1`
- Soak hardening finding: `soak_maker_submits_too_low:16.000000<min:29.000000`
- `edge_truth_audit.json`: `ok=true`, `finding_count=0`
- `order_lifecycle_audit.json`: `ok=true`, `finding_count=0`
- `outcome_truth_audit.json`: `ok=true`, `finding_count=0`
- `websocket_hardening_audit.json`: `ok=true`, `finding_count=0`
- `paper_harness_audit.json`: `ok=true`, `finding_count=0`

## Runtime Truth
- Runtime classification: `VALID_ACTIVE`
- Canonical validation `promotion_eligible`: `true`
- Duration from nightly soak report: `10.529` minutes
- Status rows: `21`
- Events slice rows: `11525`
- Errors slice rows: `0`
- Primary suppression cause: `none`
- Reports complete: `true`
- Missing reports: `[]`
- Parse error reports: `[]`
- Determinism consistent: `true`

## Fair-Probability Proof
- Clean-tree anchor run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6` had `233` taker `fair_probability_missing` block rows before the patch.
- Post-patch run `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` had `0` taker `fair_probability_missing` rows.
- Post-patch run had `13` maker `fair_probability_missing` rows.
- Last status row gauges: `fair_probability_token_count=2.0`, `taker_fair_probability_token_count=4.0`, `secondary_fair_probability_token_count=4.0`.
- Truth classification: VERIFIED_CLOSED_TAKER_SCOPE. The previous taker map-scope bug is runtime-closed by this artifact. Maker-scoped fair missing remains classified as maker-gated protective behavior from this evidence.

## Execution Summary
- Edge actions: maker `16`, taker `55`, none `3087`
- Maker submits: `16`
- Maker fills: `2`
- Maker fill rate: `0.125`
- Taker submits: `55`
- Taker fills: `55`
- Taker fill rate: `1.0`
- Taker block reasons did not include `fair_probability_missing`.

## Remaining Signals
- Archived report signal: `soak_maker_submits_too_low:16.000000<min:29.000000`
- Current-code packet-1 replay closes this signal: maker submits `16`, required submits `16`, `soak_hardening_gate.ok=true`, `finding_count=0`.
- `required_market_truth_disconnected_rows=1`
- `reduce_only_recovery_waiting_for_maker_exit=115`
- `taker_submit_rejected=95`
- `maker_no_submission=190`
- `sizing_reject=2` maker no-submission category rows
- Last status `gauge.total_pnl=-17.265999999999963`

## Current-Code Sizing Feasibility Probe
- `maker_sizing_reject_rows=4`
- `maker_min_notional_max_shares_conflict_rows=4`
- Reject price range: `0.11499999999999999` to `0.12`
- Max-share notional max: `96.0`
- Maker hard floor: `100.0` USDC
- Maker hard max shares: `800.0`
- Minimum feasible midpoint from current config: `0.125`
- Truth classification: VERIFIED fail-closed maker floor/cap feasibility constraint. No behavior/config change was made in this packet.

## Current-Code Reduce-Only Recovery Probe
- `reduce_only_recovery.edge_waiting_for_maker_exit_rows=115`
- `reduce_only_recovery.local_size_cap_unavailable_rows=2`
- `reduce_only_recovery.local_size_cap_flat_or_wrong_side_rows=2`
- `reduce_only_recovery.local_size_cap_nonflat_or_unknown_rows=0`
- `reduce_only_recovery.local_size_cap_classification=flat_or_wrong_side_noop_only`
- Truth classification: VERIFIED flat/wrong-side no-op local rejection. This is not evidence of a non-flat recovery-path weakness in this artifact.

## Current-Code Book-Feed Bootstrap Probe
- Required-book-feed disconnected row: first status row only.
- First row had `ws_slo_bootstrap_active=1`, `order_submission_attempts_last_cycle=0`, `actions_last_cycle=0`, and `taker_actions_last_cycle=0`.
- Book feed was connected by the next status row.
- `websocket_hardening_audit.json`: `ok=true`, `finding_count=0`
- Truth classification: VERIFIED startup/bootstrap telemetry, not a recurring market-data defect in this artifact.

## Final Truth Label
VERIFIED: post-patch paper-stage runtime proof for taker-scope fair-probability closure with validator pass.

VERIFIED_CLOSED_BY_PACKET1: current-code soak hardening replay closes the archived `soak_maker_submits_too_low` finding as a maker-submit enforcement taxonomy gap. No trading behavior changed for this closure.

VERIFIED_OPEN: this run is not pilot/live clearance, not a clean release anchor, and not a profitability proof.
