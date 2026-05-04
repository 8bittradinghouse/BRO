# Latest Run Audit: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`

## Run Identity
- Run ID: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- Profile: `paper_universal`
- Start: `2026-04-21T20:20:58.496Z`
- Stop: `2026-04-21T20:41:29.394Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Git dirty: `false`
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `3d49a999b9a5d1e748b527152cbf08711cdf4147b1339b376f298faefb26bea5`

## Artifact Paths
- Run manifest: `logs_exec/paper_universal/run_manifest_9d3c3225-13b6-4a12-8dd4-fb51a6d666e6.json`
- Run contract: `logs_exec/paper_universal/run_contract_9d3c3225-13b6-4a12-8dd4-fb51a6d666e6.json`
- Report directory: `logs_exec/paper_universal/reports/9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- Status slice: `logs_exec/paper_universal/sessions/6e4157f6-59d3-427b-b281-1c4ee38f1aae/slices/status_slice.jsonl`
- Events slice: `logs_exec/paper_universal/sessions/6e4157f6-59d3-427b-b281-1c4ee38f1aae/slices/events_slice.jsonl`

## Gate Results
- `canonical_paper_validation.json`: `status=pass`, `script_exit_code=0`
- `validation_summary.json`: `ok=true`
- Validator exit codes: all `0`
- `readiness_gate.json`: highest passing stage `paper`; blocking stage `pilot_live`
- `soak_hardening_gate.json`: `ok=true`, `finding_count=0`
- `outcome_truth_audit.json`: `ok=true`, `finding_count=0`
- `order_lifecycle_audit.json`: `ok=true`, `finding_count=0`
- `edge_truth_audit.json`: `ok=true`, `finding_count=0`
- `time_discipline_audit.json`: `ok=true`, `finding_count=0`
- `paper_harness_audit.json`: `ok=true`, `finding_count=0`

## Runtime Truth
- Runtime classification: `VALID_ACTIVE`
- Promotion eligible inside paper validation: `true`
- Duration: approximately `20.49` minutes
- Status rows: `41`
- Error rows: `0`
- Kill-switch events: `0`
- Safe-stop transitions: `0`
- Execution starvation mode: `none`
- Primary suppression cause: `none`

## Execution Summary
- Maker submits: `6`
- Maker fills: `1`
- Taker submits: `47`
- Taker fills: `47`
- Total fills from integrity gate: `48`
- Top rejects:
  - `new_exposure_expiry_gate_blocked`: `243`
  - `size_notional_bounds`: `30`
  - `terminal_unwind_halt_new_risk_blocked`: `5`

## Valuation / Lifecycle Truth
- `valuation_hard_degraded_ratio`: `0.0`
- `held_book_not_found_404_ratio`: `0.0`
- `held_unpriceable_escalation_ratio`: `0.0`
- `preexpiry_404_anomaly_count`: `0.0`
- `lifecycle_context_mismatch_count`: `0.0`
- `lifecycle_context_missing_sec_to_expiry_count`: `0.0`
- `preexpiry_emergency_taker_attempt_count`: `67`
- `preexpiry_emergency_taker_fill_count`: `43`
- `preexpiry_emergency_taker_block_count`: `24`

## Wallet / Capital Truth
- Wallet authority status class: `authoritative`
- Wallet contract surface source: `wallet_contract`
- Reservation mismatch candidate: `false`
- Reservation mismatch delta: `0.0`
- Order-capable live: `false`
- Order-submit eligible: `false`
- Canonical live nonce available: `false`
- Canonical live pending-wallet-tx available: `false`

## Runtime Resource Envelope
- Resource status rows: `41`
- Process CPU p95: `29.671057765666102`
- Process CPU max: `35.00540585386164`
- Process RSS max MB: `93.60546875`
- System load1 p95: `0.9208984375`
- System memory available min MB: `1991.4921875`
- System swap used max MB: `2878.8671875`

## Current-Code Stage Accounting Probe
- Original artifact field: `stage_reduction_delta_accounting.MAKER_TAKER_SELECTIVE.primary_reduction_cause_total_matches_delta=false`
- Current-code replay/probe on the same run:
  - `decision_to_submit_delta=49.0`
  - `primary_reduction_cause_total=92.0`
  - `primary_reduction_cause_total_delta_difference=43.0`
  - `primary_reduction_cause_total_exceeds_delta=true`
  - `primary_reduction_cause_overlap_possible=true`
- Truth classification: VERIFIED report-accounting overlap. Primary reduction counters can count event-row reductions and may exceed the net decision-to-submit delta. This is not, by itself, evidence of a runtime trading-path defect.

## Current-Code Sizing Feasibility Probe
- `maker_sizing_reject_rows=30`
- `maker_min_notional_max_shares_conflict_rows=30`
- Reject price range: `0.015` to `0.035`
- Max-share notional max: `28.000000000000004`
- Maker hard floor: `100.0` USDC
- Maker hard max shares: `800.0`
- Minimum feasible midpoint from current config: `0.125`
- Truth classification: VERIFIED fail-closed maker floor/cap feasibility constraint. This is not a wallet/risk loosening target.

## Current-Code Reduce-Only Recovery Probe
- `reduce_only_recovery.edge_waiting_for_maker_exit_rows=2`
- `reduce_only_recovery.local_size_cap_unavailable_rows=16`
- `reduce_only_recovery.local_size_cap_flat_or_wrong_side_rows=16`
- `reduce_only_recovery.local_size_cap_nonflat_or_unknown_rows=0`
- `reduce_only_recovery.local_size_cap_classification=flat_or_wrong_side_noop_only`
- Truth classification: VERIFIED flat/wrong-side no-op local rejection. This is not evidence of a non-flat recovery-path weakness in this artifact.

## Current-Code Book-Feed Bootstrap Probe
- Required-book-feed disconnected row: first status row only.
- First row had `ws_slo_bootstrap_active=1`, `order_submission_attempts_last_cycle=0`, `actions_last_cycle=0`, and `taker_actions_last_cycle=0`.
- Book feed was connected by the next status row.
- `websocket_hardening_audit.json`: `ok=true`, `finding_count=0`
- Truth classification: VERIFIED startup/bootstrap telemetry, not a recurring market-data defect in this artifact.

## Post-Run Fair-Probability Patch
- Original artifact signal: `fair_probability_missing=233` taker block rows.
- Current code now separates maker-scoped and taker-scoped fair-probability maps so taker evaluation does not inherit maker-only per-token lag/score filters.
- Follow-up runtime proof: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`.
- Truth classification: VERIFIED_SUPERSEDED_BY_POST_PATCH_PROOF. This run remains the pre-patch defect anchor; the follow-up run closes the taker-scope residual.

## Final Truth Label
VERIFIED: clean paper-stage wiring validation anchor with residual candidates still requiring closeout. This run is not pilot/live clearance.
