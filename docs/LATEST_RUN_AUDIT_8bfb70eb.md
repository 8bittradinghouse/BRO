# Latest Run Audit: `8bfb70eb-c942-48eb-87ff-b9628b3098c7`

## Run Identity
- Run ID: `8bfb70eb-c942-48eb-87ff-b9628b3098c7`
- Session ID: `f0f48e69-712f-429a-ac06-0d702acf9812`
- Profile: `paper_universal`
- Start: `2026-04-26T08:48:37.821Z`
- Stop: `2026-04-26T09:48:57.016Z`
- Duration from status slice: `60.3199` minutes
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Packet state: uncommitted FMA/continuity packet proof; not a clean release anchor.
- Config fingerprint: `bc8873395234bb1aef36b8d3f8d3d07a786ae8cad1a37f3ab1dcf18d48d293e9`
- Code fingerprint: `2b7053b8c65d2e92ff67897ca94c9dc2c7b6f71742a956a0c5bed1ad2ffcc613`

## Artifact Paths
- Run manifest: `logs_exec/paper_universal/run_manifest_8bfb70eb-c942-48eb-87ff-b9628b3098c7.json`
- Run contract: `logs_exec/paper_universal/run_contract_8bfb70eb-c942-48eb-87ff-b9628b3098c7.json`
- Report directory: `logs_exec/paper_universal/reports/8bfb70eb-c942-48eb-87ff-b9628b3098c7`
- Status slice: `logs_exec/paper_universal/sessions/f0f48e69-712f-429a-ac06-0d702acf9812/slices/status_slice.jsonl`
- Events slice: `logs_exec/paper_universal/sessions/f0f48e69-712f-429a-ac06-0d702acf9812/slices/events_slice.jsonl`
- Errors slice: `logs_exec/paper_universal/sessions/f0f48e69-712f-429a-ac06-0d702acf9812/slices/errors_slice.jsonl`
- FMA run pack: `logs_exec/paper_universal/forge_masters_archive_run_8bfb70eb-c942-48eb-87ff-b9628b3098c7`

## Gate Results
- `canonical_paper_validation.json`: `status=pass`, `runtime_classification=VALID_ACTIVE`
- `canonical_paper_validation.json`: `policy_failed=false`, `reports_complete=true`, `determinism_consistent=true`
- `canonical_paper_validation.json`: `highest_passing_stage=paper`, `blocking_stage=pilot_live`, `promotion_eligible=true`
- Validator exit codes: all `0`
- `soak_hardening_gate.json`: `ok=true`, `finding_count=0`
- `paper_harness_audit.json`: `ok=true`, `finding_count=0`
- `edge_truth_audit.json`: `ok=true`, `finding_count=0`
- `order_lifecycle_audit.json`: `ok=true`, `finding_count=0`
- `outcome_truth_audit.json`: `ok=true`, `finding_count=0`
- `guardian_profile_audit.json`: `ok=true`, `finding_count=0`

## Mechanical Health Truth
- Runtime classification: `VALID_ACTIVE`
- Status rows: `120`
- Errors slice rows: `0`
- Wallet authority class: `authoritative`
- Wallet health: `wallet_health_ok=true`
- Reservation mismatch delta: `0.0`
- Same-market lane collisions: `0`
- `waiting_for_maker_exit_count=0`
- Recovery taker submits/fills: `0/0`
- Startup/book-feed truth stayed clean after bootstrap; no persistent feed disconnect pattern was observed

## Execution And Weapon-Lane Truth
- Maker submits: `61`
- Maker fills: `77`
- Maker fill rate: `0.5737704918032787`
- Maker timing-gate blocked count: `1677`
- Maker quote-quality skip total: `53`
- Maker sizing reject total: `27`
- Risk reject total: `27`
- Taker submits/fills: `0/0`
- Normal taker candidate count from suppression report: `0` submits despite `13108` taker edge eval rows
- Recovery taker block count: `828`
- Recovery taker block reason distribution: `maker_to_taker_recovery_handoff_disabled=828`

## Valuation And Economic Truth
- `valuation_hard_degraded_enter_count=10`
- `valuation_hard_degraded_clear_count=10`
- `held_unpriceable_started_count=17`
- `held_unpriceable_recovered_count=17`
- `held_unpriceable_unrecovered_meaningful_count=0`
- `held_unpriceable_defect_candidate_rows=0`
- `held_unpriceable_escalation_rows=0`
- Final status row:
  - `held_unpriceable_token_count=0`
  - `valuation_degraded=false`
  - `valuation_hard_degraded=false`
  - `open_orders=0`
  - `wallet_open_reserved=0.0`
  - `wallet_stable_balance_total=3030.529105`
  - `gauge.total_pnl=-969.4708949999999`
- Truth classification:
  - VERIFIED: the final negative paper PnL is not explained by unresolved held-unpriceable state or end-of-run valuation degradation.
  - VERIFIED: final paper cash drop from `4000.0` to `3030.529105` matches final `gauge.total_pnl` exactly.
  - INFERRED: this specimen is a healthy robot with poor paper economics, not a mechanically unhealthy robot hiding behind a green wrapper.

## Outcome-Truth Compression
- Outcome-truth attribution usability ratio: `0.5737704918032787`
- Complete classification ratio: `0.5737704918032787`
- Complete maker outcome records: `35`
- Quality split on complete records:
  - `incorrect + favorable execution`: `28`
  - `neutral + favorable execution`: `5`
  - `correct + favorable execution`: `2`
- Means across complete records:
  - `edge_expected_mean=-0.02522526777268647`
  - `edge_realized_mean=-0.005494882263510608`
  - `decision_component_mean=-0.03242857142857142`
  - `execution_component_mean=0.02693368916506081`
- Truth classification:
  - VERIFIED: when fills completed and classified, execution quality was favorable.
  - VERIFIED: decision quality skewed negative.
  - INFERRED: this run's economic ugliness came more from bad shot selection than from poor fill mechanics.

## Tagged Seams
1. `maker_to_taker_recovery_handoff_disabled` chatter
   - VERIFIED: `828` repeated `preexpiry_emergency_taker_unwind` blocked events were emitted with the same canonical block reason.
   - VERIFIED: no same-market collision or recovery submit/fill accompanied the chatter.
   - Classification: healthy doctrine enforcement with noisy repeated telemetry, not evidence of lane-crossing failure.

2. Valuation degraded bruise
   - VERIFIED: valuation degraded and hard-degraded transitions occurred, then fully cleared.
   - VERIFIED: repeated reasons included stale quote / last-known-mid fallback / hard-degraded clear hysteresis pending.
   - VERIFIED: regenerated valuation instrumentation now classifies the bruise as `recovered_clean`.
   - VERIFIED: dominant degraded reason family was `degraded_using_last_known_mid`, with a smaller `hard_degraded` burst.
   - VERIFIED: degraded-row valuation source mix was `last_known_mid=4`, `conservative_bound_hard_degraded=2`, `hard_degraded=2`.
   - VERIFIED: dominant held-unpriceable cause family for the run was `unknown_data_gap`.
   - Classification: bounded valuation-truth bruise worth tracing, not an unresolved held-exposure defect in this artifact.

3. Paper PnL collapse
   - VERIFIED: final paper PnL settled at `-969.4708949999999` with flat end-state positions/reservations.
   - VERIFIED: outcome truth shows favorable execution on completed fills but mostly incorrect decisions.
   - Classification: real paper-economics problem requiring analysis, not a wrapper-only illusion.

4. Maker forensic and reporting-semantics follow-through
   - VERIFIED: a dedicated maker analysis-first forensic is now preserved in `docs/MAKER_FORENSIC_8bfb70eb.md`.
   - VERIFIED: the final truth-hardening pass is now reflected in `FMA` for this specimen:
     - multi-fill wound geometry,
     - lifecycle gap classes,
     - same-target repeat clusters,
     - complement-pair cluster counts/examples,
     - basis provenance and canonical horizon context.
   - VERIFIED: the maker wound currently reads as bad completed fight selection with favorable execution unable to rescue it.
   - VERIFIED: the forensic also pins the semantic split between:
     - order-completion rate,
     - fill-event execution economics,
     - and order-level observational outcome truth.
   - Classification: active truth rack is now materially harder and more machine-readable; next lane should use these flags rather than reopen raw discovery first.

## Surgical Follow-Up Rack
1. Low-risk telemetry cleanup packet
   - Target: `preexpiry_emergency_taker_unwind` spam when the only outcome is repeated `maker_to_taker_recovery_handoff_disabled`
   - Intent: preserve doctrine truth while compressing repeated same-reason chatter
   - Shape: emit transition-first detail plus aggregate counters, or rate-limit repeated identical blocked rows per token/window
   - Guardrail: no change to taker authority, recovery doctrine, or same-market protection semantics

2. Valuation bruise instrumentation packet
   - Target: stale-mid / one-sided / last-known-mid degradation sequences
   - Intent: compress token-level recurrence into a clearer diagnostic surface without weakening fail-closed behavior
   - Shape: add a compact per-token valuation degradation summary artifact or end-of-run rollup keyed by token, reason family, and max age
   - Guardrail: no loosening of valuation age thresholds in this packet

3. Economic shot-group packet
   - Target: maker decision-quality weakness shown by negative `decision_component` despite favorable execution
   - Intent: inspect where the maker blade is entering low-quality fights
   - Shape: use FMA plus outcome-truth records to compare edge buckets, stage windows, side policy, and sizing overlays for the losing specimens
   - Guardrail: analysis first; no maker-strategy tuning without a separately approved packet

## Final Truth Label
VERIFIED: this was a healthy 60-minute canonical paper run at the control-plane and validator level.

VERIFIED: the taker lane did not fire, but it also did not cross lanes illegally. Recovery chatter was blocked cleanly by current doctrine.

VERIFIED: the final paper loss was real in the artifact surfaces and matched final paper cash, not just an unresolved valuation artifact.

VERIFIED_OPEN: this is a strong health-and-stability proof for BRO, but it is not an economics proof, not a taker-live specimen, and not a reason to skip deeper diagnosis of the noisy blocked-handoff telemetry or valuation bruising.
