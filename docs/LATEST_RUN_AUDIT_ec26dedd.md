# Latest Run Audit: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`

## Run Identity
- Run ID: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Session ID: `059215d1-deaf-40f4-88a8-45e076d8fef2`
- Profile: `paper_universal`
- Start: `2026-04-22T02:25:25.748Z`
- Stop: `2026-04-22T02:30:56.560Z`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Packet state: uncommitted post-clean-baseline packet-1 proof; not a clean release anchor.
- Config fingerprint: `a46c8c7c15cb7de36331abedc4f0e444e8c6c63e311cb2aa223b26fc383a54de`
- Code fingerprint: `b901be301201c1cb58a6486bc28c1366e4bccba4d6f047bf545bc34a50905e78`

## Artifact Paths
- Run manifest: `logs_exec/paper_universal/run_manifest_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.json`
- Run contract: `logs_exec/paper_universal/run_contract_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.json`
- Report directory: `logs_exec/paper_universal/reports/ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Status slice: `logs_exec/paper_universal/sessions/059215d1-deaf-40f4-88a8-45e076d8fef2/slices/status_slice.jsonl`
- Events slice: `logs_exec/paper_universal/sessions/059215d1-deaf-40f4-88a8-45e076d8fef2/slices/events_slice.jsonl`
- Errors slice: `logs_exec/paper_universal/sessions/059215d1-deaf-40f4-88a8-45e076d8fef2/slices/errors_slice.jsonl`
- Export archive: `exports/paper_session_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.zip`

## Gate Results
- `canonical_paper_validation.json`: `status=policy_failed`, `script_exit_code=2`
- `canonical_paper_validation.json`: `runtime_classification=VALID_ACTIVE`, `promotion_eligible=true`
- `canonical_paper_validation.json`: `execution_error=false`, `determinism_consistent=true`, `reports_complete=true`
- `validation_summary.json`: `ok=false`, `overall_exit_code=2`
- Validator exit codes: all `0` except `soak_hardening_gate=2` and `soak_hardening_gate_replay=2`
- `soak_hardening_gate.json`: `ok=false`, `finding_count=5`
- Soak hardening findings:
  - `performance_status_rows_too_few:11<min:20`
  - `soak_duration_too_short:5.446400<min:10.000000`
  - `soak_readiness_below_required_stage:required=paper:highest=none:causes=min_status_rows`
  - `status_rows_below_min:11<min:20`
  - `websocket_slo_status_rows_too_few:11<min:20`
- `paper_harness_audit.json`: `ok=true`, `finding_count=0`
- `edge_truth_audit.json`: `ok=true`, `finding_count=0`
- `order_lifecycle_audit.json`: `ok=true`, `finding_count=0`
- `outcome_truth_audit.json`: `ok=true`, `finding_count=0`
- `guardian_profile_audit.json`: `ok=true`, `finding_count=0`
- `time_discipline_audit.json`: `ok=true`, `finding_count=0`
- `websocket_hardening_audit.json`: `ok=true`, `finding_count=0`

## Runtime Truth
- Runtime classification: `VALID_ACTIVE`
- Duration from soak gate: `5.4464` minutes
- Status rows: `11`
- Event rows from run-integrity evidence: `5620`
- Fill events from run-integrity evidence: `20`
- Errors slice rows: `0`
- Primary suppression cause: `none`
- Suppression dominated run: `false`
- Execution starvation mode: `none`
- Kill switch events: `0`
- Safe-stop transitions: `0`
- Reports complete: `true`
- Missing reports: `[]`
- Parse error reports: `[]`
- Determinism consistent: `true`

## Packet 1 Proof
- Packet 1 changed `ops/soak_budget.yaml` report policy only.
- `fair_probability_missing` is now listed under `soak.maker_submit_enforcement.non_actionable_block_reasons`.
- No trading behavior, wallet authority, risk authority, live-readiness gate, or strategy threshold was changed.
- In this run, maker submit enforcement applied with:
  - `maker_actionable_opportunity_rows=1.0`
  - `required_submits=1.0`
  - `maker_submits=1.0`
  - `maker_non_actionable_block_rows=155.0`
  - `maker_rows_total=156.0`
- Maker block distribution included `fair_probability_missing=5`, now treated as non-actionable for maker-submit enforcement.
- Truth classification: VERIFIED_CLOSED. The prior `soak_maker_submits_too_low` false-positive path is closed for current-code gate evaluation.

## Execution Summary
- Edge rows: `1722`
- Edge action rows: `19`
- Maker rows: `156`
- Taker rows: `1566`
- Maker submits: `1`
- Maker fills: `2`
- Taker bonus submits: `18`
- Taker bonus fills: `18`
- Taker bonus fill rate: `1.0`
- Quote uptime ratio: `0.18254896360985437`
- Market data source: `ws_delta=1196`, `rest_delta=276`, `rest_ratio=0.1875`
- Runtime resources:
  - `process_cpu_percent_max=28.97387078475467`
  - `process_rss_mb_max=79.23828125`
  - `system_load1_max=0.54736328125`
  - `system_mem_available_ratio_min=0.5924673819656736`
  - `system_swap_used_ratio_max=0.013929850544858239`

## Valuation And Wallet Truth
- `error_rows=0`
- `valuation_degraded_ratio=0.0`
- `valuation_hard_degraded_ratio=0.0`
- `valuation_hard_degraded_enter_count=0.0`
- `held_unpriceable_started_count=0.0`
- `held_unpriceable_recovered_count=0.0`
- `preexpiry_404_anomaly_count=0.0`
- `lifecycle_context_mismatch_count=0.0`
- `lifecycle_context_missing_sec_to_expiry_count=0.0`
- Wallet authority class: `authoritative`
- Wallet health: `wallet_health_ok=true`
- Reservation mismatch delta: `0.0`
- Order-capable live: `false`
- Canonical live nonce available: `false`
- Canonical live pending-wallet-tx available: `false`

## Current-Code Reduce-Only Recovery Probe
- `reduce_only_recovery.edge_waiting_for_maker_exit_rows=4`
- `reduce_only_recovery.local_size_cap_unavailable_rows=1`
- `reduce_only_recovery.local_size_cap_flat_or_wrong_side_rows=1`
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
VERIFIED: packet-1 behavior is healthy on a requested 5-minute paper run. The maker-submit taxonomy fix works, runtime classification is `VALID_ACTIVE`, no execution error occurred, and all non-soak validators passed.

VERIFIED: this is not a canonical all-green paper validation because the current canonical soak budget requires at least `10` minutes and `20` status rows. The `policy_failed` result is expected by design for a 5-minute run and should not be patched away by weakening canonical evidence requirements.

VERIFIED_OPEN: this run is not pilot/live clearance, not a clean release anchor, and not a profitability proof.
