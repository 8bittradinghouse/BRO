# BRO Metric Atlas

## Purpose
This atlas defines the engineer-first metric map for BRO report harvesting.

Tool identity:
- `Forge Masters Archiver`
- `FMA`

Primary use:
- diagnose weapon behavior,
- understand submit/fill friction,
- inspect window usage,
- inspect money authority and valuation truth,
- preserve a reusable measurement spine for future BRO-derived systems.

The current harvester is:
- `scripts/bro_metric_harvest.py`

Companion diagnostic-tool surfaces:
- `docs/BRO_DIAGNOSTIC_TOOLS.md`
- `docs/SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md`
- `docs/SOLAR_SLUG_MAKER_TRUTH_SEMANTICS_MAP.json`

Its outputs are:
- `run_index.jsonl`
- `metric_catalog.json`
- `maker_taker_summary.csv`
- `anomaly_summary.json`
- `maker_research_pack.md`

## Semantic Reading Rules
- `BRO_CANONICAL_DOCTRINE.txt` is the semantic root.
- Metrics and distributions are consumer/report surfaces unless the underlying
  emitted live contract says otherwise.
- Live contract names should be read first; downstream mirrors and distributions
  must not silently outrank them.
- Examples:
  - `market_reference_class_distribution` consumes live
    `market_reference_class`
  - `market_reference_mode_distribution` consumes live `market_reference_mode`
  - `secondary_oracle_status_distribution` consumes live
    `secondary_oracle_status`
  - `financial_posture_class_distribution` consumes live
    `financial_posture_class`
  - `maker_block_reason_distribution` consumes emitted `block_reason`
  - `runtime_classification` is a consumer container emitted by runtime
    semantics; `runtime_classification_name` and
    `runtime_primary_suppression_cause` are readouts of that container
  - `wallet_authority_status_class` and `wallet_order_submit_eligible` are
    report-consumer names of live wallet authority terms
  - `wallet_authority_status_class=legacy_fallback_non_authoritative` is a
    report-side fallback/readout state, not a live wallet contract emission
  - `opening_wallet_authority_status_class` and
    `ending_wallet_authority_status_class` are report readouts, not live wallet
    contract fields
  - `maker_new_risk_allowed_distribution` is a canonical report-side summary of
    live `maker_new_risk_allowed` row truth, not a second authority layer
  - report-side `market_reference_basis=report_book_top_pair_backfill` is a
    downstream reconstructed basis label, not a live emitted runtime basis
  - `not_available_reference_count` counts emitted
    `market_reference_class=not_available`; it does not invent a new class
  - canonical `harness_realism_grade*` belongs only to
    `paper_harness_audit`; nightly exercised-only realism belongs under
    `exercised_harness_realism`
  - `harness_realism_grade_semantics` and
    `harness_realism_grade_authority` are descriptive audit metadata only
- Distinguish explicitly:
  - emitted live contract name
  - doctrine-only boundary concept
  - downstream mirror
  - descriptive-only metric/report term

New compression surfaces to read first:
- `validation_status`
- `validation_policy_failed`
- `maker_complete_bad_ratio`
- `maker_multifill_complete_incorrect_ratio`
- `maker_fills_per_filled_order`
- `maker_same_target_repeat_cluster_count`
- `maker_complement_pair_cluster_count`
- `maker_quote_quality_skip_total_count`
- `maker_sizing_reject_total_count`
- `risk_reject_total_count`
- `valuation_bruise_state`
- `valuation_dominant_reason_family_run`
- `valuation_dominant_held_unpriceable_cause_run`
- `outcome_truth_attribution_usability_ratio`
- `anomaly_summary.engineer_focus`

## Core Reading Order
Read in this order when orienting on a run:
1. `validation_ok`, `gate_passed`, `highest_passing_stage`, `blocking_stage`
2. `validation_status`, `validation_policy_failed`, `validation_determinism_consistent`
3. `runtime_classification`, `runtime_primary_suppression_cause`, `error_rows`
4. `maker_submits`, `maker_fill_rate`, `maker_quote_quality_skip_total_count`, `maker_sizing_reject_total_count`, maker no-submit and maker block distributions
5. `maker_complete_record_count`, `maker_complete_bad_ratio`, `maker_multifill_complete_incorrect_ratio`, `maker_same_target_repeat_cluster_count`, `maker_complement_pair_cluster_count`
6. `taker_decision_count`, `taker_submits`, `taker_fills`, timing-window distributions, submit-capable conversion
7. `wallet_authority_status_class`, `wallet_deployable_capital`, `wallet_order_submit_eligible`
8. `valuation_bruise_state`, `valuation_degraded_ratio`, `valuation_hard_degraded_ratio`, pre-expiry emergency taker counters
9. `market_data_rest_ratio`, `chainlink_down_ratio`, `book_feed_down_ratio`
10. `outcome_attribution_usability_ratio` / `outcome_truth_attribution_usability_ratio` and claim-boundary surfaces

## Source Files
### `validation_summary.json`
Category:
- gating

What it tells you:
- whether the summary validators passed
- whether determinism held
- whether the outcome-truth usability surface was present

High-value fields:
- `validation_status`
- `validation_ok`
- `validation_overall_exit_code`
- `validation_validator_determinism_ok`
- `validation_edge_truth_determinism_ok`
- `validation_non_edge_determinism_ok`
- `validation_outcome_truth_usability`

### `canonical_paper_validation.json`
Category:
- gating

What it tells you:
- the current validated stage boundary
- whether the run is promotion-eligible
- whether the report set was complete

High-value fields:
- `validation_policy_failed`
- `validation_determinism_consistent`
- `gate_passed`
- `reports_complete`
- `highest_passing_stage`
- `blocking_stage`
- `promotion_eligible`
- `recommended_next_stage`
- `runtime_classification`

### `nightly_soak_report.json`
Category:
- mixed
- diagnostic-heavy
- partially observational

What it tells you:
- runtime behavior
- maker/taker execution conversion
- window usage
- suppression shape
- money posture
- valuation degradation
- market-data source mix
- exercised-only harness realism actually observed on fill tape

High-value fields:
- runtime:
  - `runtime_classification_name`
  - `runtime_primary_suppression_cause`
  - `runtime_decision_events`
  - `runtime_required_book_feed_disconnected_rows`
- maker:
  - `maker_submits`
  - `maker_fills`
  - `maker_fill_rate`
  - `maker_no_submit_total_count`
  - `maker_quote_quality_skip_total_count`
  - `maker_sizing_reject_total_count`
  - `maker_replace_guard_min_rest_count`
  - `maker_timing_gate_blocked_decision`
  - `maker_no_submission_cause_distribution`
  - `maker_block_reason_distribution`
  - `maker_reference_direct_midpoint_activity`
  - `maker_reference_bounded_fallback_activity`
- taker:
  - `taker_decision_count`
  - `taker_submits`
  - `taker_fills`
  - `taker_decision_to_submit_rate`
  - `taker_submit_capable_to_submit_rate`
  - `taker_submit_capable_dynamic_to_submit_rate`
  - `taker_decision_timing_window_distribution`
  - `taker_decision_predicted_reject_reason_distribution`
  - `taker_stage_final_risk_reject_reason_distribution`
- recovery:
  - `recovery_waiting_for_maker_exit_rows`
  - `recovery_nonflat_or_unknown_rows`
  - `recovery_local_size_cap_unavailable_rows`
- money / authority:
  - `wallet_authority_status_class`
  - `wallet_deployable_capital`
  - `wallet_order_submit_eligible`
  - `risk_reject_total_count`
  - `wallet_reservation_mismatch_candidate`
  - `risk_global_exposure_utilization_ratio_max`
- valuation:
  - `valuation_bruise_state`
  - `valuation_dominant_reason_family_run`
  - `valuation_dominant_held_unpriceable_cause_run`
  - `valuation_dominant_source_degraded_rows`
  - `valuation_degraded_reason_family_counts_run`
  - `valuation_source_counts_degraded_rows`
  - `valuation_degraded_ratio`
  - `valuation_hard_degraded_ratio`
  - `held_unpriceable_unrecovered_meaningful_count`
  - `preexpiry_emergency_taker_attempt_count`
  - `preexpiry_emergency_taker_block_count`
  - `preexpiry_emergency_taker_fill_count`
- feed/data:
  - `market_data_rest_ratio`
  - `market_data_ws_delta`
  - `market_data_total_delta`
- exercised realism:
  - `exercised_harness_realism.grade`
  - `exercised_harness_realism.breakdown`
- timing/latency:
  - `duration_minutes`
  - `latency_median_ms`
  - `latency_p90_ms`
  - `latency_p95_ms`

### `edge_truth_audit.json`
Category:
- diagnostic

What it tells you:
- how many actionable vs blocked rows were recorded
- whether opportunity identity and stage-policy accounting look coherent

High-value use:
- use it to cross-check whether maker/taker friction is coming from the edge surface or later stages

### `order_lifecycle_audit.json`
Category:
- diagnostic
- integrity

What it tells you:
- whether submits, fills, and cancels are coherently linked
- whether ghost lifecycle seams exist

High-value fields:
- `lifecycle_order_submit_decision_missing_count`
- `lifecycle_edge_decision_ingest_missing_count`
- `lifecycle_edge_decision_submit_missing_count`
- `lifecycle_fill_without_submit_count`
- `lifecycle_cancel_without_submit_count`
- `lifecycle_duplicate_fill_trade_id_count`
- `lifecycle_counts`

### `outcome_truth_audit.json`
Category:
- observational
- diagnostic

What it tells you:
- bounded execution-quality and directional-quality measurement
- whether attribution is usable
- what claim boundary the run actually earned

High-value fields:
- `outcome_attribution_usability_ratio`
- `outcome_filled_complete_ratio`
- `outcome_complete_classification_ratio`
- `outcome_record_claim_boundary_distribution`
- `outcome_status_distribution`
- `slippage_summary`
- `adverse_selection_summary`

### `outcome_truth_records.jsonl`
Category:
- diagnostic
- maker-forensic
- research-support

What it tells you:
- the per-submit maker outcome population
- lifecycle geometry
- fill-count geometry
- basis provenance
- repeated-target and complement-pair wound structure

High-value harvested fields derived from it:
- `maker_complete_record_count`
- `maker_incomplete_record_count`
- `maker_complete_bad_ratio`
- `maker_incomplete_bad_ratio`
- `maker_multifill_complete_count`
- `maker_multifill_complete_incorrect_ratio`
- `maker_fill_count_quality_distribution`
- `maker_execution_rescue_overcome_count`
- `maker_execution_rescue_ratio_summary`
- `maker_same_target_repeat_cluster_count`
- `maker_target_cluster_summary`
- `maker_complement_pair_cluster_count`
- `maker_complement_pair_cluster_decision_debt_sum`
- `maker_lifecycle_gap_class_distribution`
- `maker_reference_basis_summary`
- `maker_outcome_horizon_ms`
- `maker_eval_basis_requires_reconstructed_midpoint_flag`

### `soak_hardening_gate.json`
Category:
- gating
- integrity
- feed-hardening

What it tells you:
- whether readiness and websocket integrity passed
- whether the run had explicit hard-fail budget breaches
- which decision-trace checks mattered

High-value fields:
- `soak_gate_ok`
- `soak_gate_readiness_highest_passing_stage`
- `soak_gate_readiness_blocking_stage`
- `soak_gate_websocket_ok`
- `soak_gate_integrity_ok`
- `chainlink_down_ratio`
- `book_feed_down_ratio`
- `integrity_finding_count`
- `integrity_duplicate_fill_trade_id_count`

## Metric Classes
### Gating
Treat these as authority surfaces:
- validation pass/fail
- stage pass/fail
- readiness / websocket / integrity hard-fail surfaces

### Observational
Treat these as bounded measurement, not strategy proof:
- outcome-truth directional correctness
- execution-quality slippage/adverse-selection summaries
- maker fill rate by itself

### Diagnostic
Treat these as the main engineering weapon-building surfaces:
- maker no-submit causes
- maker quote-quality skip totals
- maker sizing-reject totals
- taker window distributions
- wallet authority status
- deployable capital
- valuation degradation
- lifecycle coherence
- feed mix and feed-health ratios

## Engineer Focus Surface
`anomaly_summary.json` now carries a machine-readable `engineer_focus` block.

Use it for:
- fast maker-friction totals
- fast risk-reject totals
- quick coverage thin-spot detection
- deciding what deserves the next hardening pass without re-reading the whole corpus first

## Maker Research Shortlist
Read these first for maker design work:
- `maker_submits`
- `maker_fill_rate`
- `maker_fills_per_filled_order`
- `maker_no_submit_total_count`
- `maker_quote_quality_skip_total_count`
- `maker_sizing_reject_total_count`
- `maker_replace_guard_min_rest_count`
- `maker_timing_gate_blocked_decision`
- `maker_complete_record_count`
- `maker_complete_bad_ratio`
- `maker_multifill_complete_incorrect_ratio`
- `maker_same_target_repeat_cluster_count`
- `maker_complement_pair_cluster_count`
- `maker_no_submission_cause_distribution`
- `maker_block_reason_distribution`
- `maker_reference_direct_midpoint_activity`
- `maker_reference_bounded_fallback_activity`

Why:
- they tell you whether maker is not seeing windows, seeing them but getting blocked, or firing into bad queue/fill-prob geometry

### Maker Fireability / Window Surfaces
Read these first when the question is:
- is maker failing to see the combat window,
- failing to fire inside it,
- or getting choked by cadence / quote-quality friction after it gets there?

High-value fields:
- `maker_window_active_row_count`
- `maker_window_submit_count`
- `maker_window_replace_guard_count`
- `maker_window_quote_quality_skip_total_count`
- `maker_window_submit_rate`
- `maker_window_replace_guard_rate`
- `maker_window_quote_quality_skip_rate`
- `maker_window_target_summary`
- `maker_quote_quality_skip_fill_probability_severity_bins`
- `maker_quote_quality_skip_queue_depth_severity_bins`

Claim boundary:
- `VERIFIED`: `maker_window_*` counts/rates are derived from maker `edge_evaluation`
  rows inside the manifest-configured maker timing gate.
- `VERIFIED`: quote-quality severity bins are built from raw non-recovery
  `quote_quality_skip` events.
- `VERIFIED`: those raw reject sparks are not the same population as maker
  no-submit assignments, so do not compare the totals as though they must match
  one-for-one.

Practical read:
- use the active-window rates to judge within-window fireability
- use the target summary to spot repeat-target cadence pressure
- use the severity bins to separate near-threshold quote friction from clearly
  bad stock
- do not widen the whole timing window just because `maker_timing_gate_closed`
  is large outside the live combat box

### Maker Fight-Admission Shadow Surfaces
Read these when the question is:
- which maker fights look `clean`, `borderline`, or `trash` before submit?
- which driver families are pushing maker into bad pits?
- is the selector teaching us real fight quality or only restating current
  block/no-block behavior?

Primary surfaces:
- `maker_fight_admission_shadow_summary.json`
- `maker_fight_admission_calibration_audit.json`
- `maker_fight_admission_shadow_rows.jsonl`
- `maker_admission_target_side_summary.json`

High-value fields:
- `population_class_counts`
- `admission_class_counts`
- `complete_joined_count_by_class`
- `complete_bad_ratio_by_class`
- `multifill_incorrect_ratio_by_class`
- `dominant_driver_distribution`
- `cannon_window_class_distribution`
- `maker_timing_band_class_distribution`
- `candidate_count_by_timing_band`
- `admission_class_distribution_by_timing_band`
- `submitted_count_by_timing_band`
- `complete_joined_count_by_timing_band`
- `complete_bad_ratio_by_timing_band`
- `multifill_incorrect_ratio_by_timing_band`
- `session_regime_class_distribution`
- `stack_pressure_class_distribution`
- `secondary_oracle_status_distribution`
- `secondary_oracle_confirmation_distribution`
- `maker_new_risk_allowed_distribution`
- `probe_visible_depth_fail_closed_zero_distribution`
- `market_reference_class_distribution`
- `market_reference_mode_distribution`
- `market_reference_source_side_distribution`
- `market_probability_band_distribution`
- `favored_side_depth_class_distribution`
- `cannon_depth_requirement_counts`
- `depth_multiple_vs_cannon_target_summary`
- `clean_but_bad_examples`
- `trash_but_okay_examples`
- `target_side_ref`
- `dominant_driver`
- `component_scores`
- `admission_rubric_version`

Claim boundary:
- `VERIFIED`: runtime emits raw maker-side fight rows; the canonical selector
  score/class is computed in the report bridge, not in runtime.
- `VERIFIED`: historical specimen recuts may be sourced from
  `legacy_quote_or_submit_backfill_v1` rather than native runtime shadow events.
- `VERIFIED`: current `v1` selector separates submit/no-submit pressure much
  better than it separates good submitted fights from bad submitted fights.
- `VERIFIED`: the bounded late-window probe now observes real `<=20s` cannon
  rows even when runtime maker stage policy disallows them there.
- `VERIFIED`: report-layer fail-closed zero-depth logic is part of the current
  cannon probe contract, so missing favored-side size can legitimately mean
  `0.0` visible depth instead of `UNKNOWN`.
- `VERIFIED`: report-layer secondary-oracle recompute can promote runtime
  `unknown` rows into `confirmed` or `direction_mismatch` when bounded market
  reference plus dual fair values are present.
- `VERIFIED`: the cannon probe now also carries a decontaminated
  `latent_market_*` layer.
  - use it to separate:
    - `runtime posture blocked this row`
    - from
    - `the underlying late-window market was still trash anyway`
- `VERIFIED`: in the healthier archive rereads, transition and asia samples had
  non-zero latent evaluable late-window rows but still `0` latent full cannon
  candidates, with dominant blocker families:
  - `insufficient_depth_multiple`
  - `secondary_oracle_unknown`
  - narrower `non_viable_geometry`
- do **not** treat this surface as runtime-ready gating authority unless a later
  packet proves class separation on mature outcome truth.

Practical read:
- use it to identify driver families and redesign selectivity doctrine
- use the cannon-specific distributions to answer:
  - are we even observing the late-window doctrine yet?
  - is depth actually thin relative to the `$350` cannon shot?
  - are stack pressure or secondary-oracle disagreement even present?
- use `clean_but_bad_examples` to learn what the current rubric still misses
- if `trash` rows never mature into submitted outcomes, that is evidence about
  block discipline, not proof that the selector can already find 90% fights
- do not promote this straight into live gating just because the class names
  sound strong

### Maker Semantic Reading Note
Do not read the maker surfaces as though they all describe the same population.

- `maker_fill_rate`
  - `VERIFIED`: this is order-completion rate:
    - `maker_filled_orders / maker_submits`
  - it is **not** fill-event rate
- `execution_quality_decision_reference_lane_attribution`
  - `VERIFIED`: this is fill-event execution economics scored against submit-side decision-reference midpoint
  - claim boundary is report-only and lane-attribution specific
- `outcome_truth_audit`
  - `VERIFIED`: this is order-submit observational outcome truth
  - incomplete lifecycle records remain in the population unless explicitly filtered out
- `edge_truth.action_rows`
  - `VERIFIED`: this is decision-cycle action truth, not submit-count truth

Practical rule:
- use `maker_fill_rate` for completion conversion
- use `maker_fills_per_filled_order` for multi-fill churn geometry, not for “maker quality” by itself
- use `execution_quality_decision_reference_lane_attribution` for fill-event execution read
- use `outcome_truth_audit` for order-level decision/execution classification
- never collapse those three into one implicit “maker performance” number

Additional rule:
- read `decision_reference_basis_distribution` and `eval_reference_basis_distribution` before trusting maker outcome-truth summaries as though they were direct book-to-book measurements
- if eval basis is dominated by `edge_market_midpoint_series`, treat the result as a bounded reconstructed midpoint lens, not a direct raw-book replay lens

### Maker Truth Population Map
Use these as separate populations:

- `decision-cycle truth`
  - source: `edge_truth.action_rows`
  - meaning: decision cycles where maker logic evaluated and possibly acted
  - danger: not a submit count
- `submit truth`
  - source: `outcome_truth_records.jsonl`
  - meaning: one record per maker order submit
  - danger: includes records that never fill or never mature complete
- `filled-order truth`
  - source: `execution_paths.maker_filled_orders`
  - meaning: orders with at least one fill
  - danger: sits between submit truth and fill-event truth
- `fill-event truth`
  - source: `execution_quality_decision_reference_lane_attribution`
  - meaning: economics scored per fill event
  - danger: execution can look favorable while completed decisions are still bad
- `complete-outcome truth`
  - source: complete rows inside `outcome_truth_records.jsonl`
  - meaning: matured completed order outcomes under canonical bounded horizon
  - danger: this is the right decision-debt lane, but it is still shaped by the outcome horizon

Plain-English:
not all maker numbers describe the same set of fights. Read the population before trusting the number.

## Taker / Window Shortlist
Read these first for taker and selective-window work:
- `taker_decision_count`
- `taker_submits`
- `taker_fills`
- `taker_decision_to_submit_rate`
- `taker_submit_capable_to_submit_rate`
- `taker_submit_capable_dynamic_to_submit_rate`
- `taker_decision_timing_window_distribution`
- `taker_decision_predicted_reject_reason_distribution`

Why:
- they separate edge visibility from conversion friction
- they show whether the weapon sees opportunities but dies in preview, risk, sizing, or timing

## Money / Authority Shortlist
Read these first when capital truth or execution eligibility matters:
- `wallet_authority_status_class`
- `wallet_deployable_capital`
- `wallet_order_submit_eligible`
- `wallet_reservation_mismatch_candidate`
- `risk_global_exposure_utilization_ratio_max`
- `valuation_bruise_state`
- `valuation_dominant_reason_family_run`
- `valuation_dominant_held_unpriceable_cause_run`
- `valuation_degraded_ratio`
- `valuation_hard_degraded_ratio`
- `preexpiry_emergency_taker_attempt_count`
- `preexpiry_emergency_taker_block_count`

Why:
- they show whether the money plane itself is constraining the weapon
- they show whether valuation degradation is poisoning evidence quality

## Expected Noise / False Alarms
Do not overreact to these in isolation:
- `outcome_attribution_usability_ratio < 1.0`
  - this can reflect missing bounded attribution, not a broken strategy
- high `maker_fill_rate`
  - this is not automatically good; it can mean under-posting or over-crossing
- high `maker_fills_per_filled_order`
  - this can mean multi-fill churn or repeated exposure within one order, not automatically better maker quality
- `runtime_classification = VALID_ACTIVE`
  - this means the run was alive and meaningful, not that the edge is proven
- high `preexpiry_emergency_taker_*` counts
  - this often reflects valuation/exit pressure, not normal offensive taker behavior
- `valuation_bruise_state = recovered_clean`
  - this means a bruise fully cleared, not that the economics or data plane were good
- elevated `market_data_rest_ratio`
  - not automatically bad without websocket health and fill behavior context

## Claim-Boundary Reminders
- Validation pass does not prove live-venue equivalence.
- Outcome truth does not prove long-range profitability.
- Outcome truth does not prove strategy optimality.
- Observational metrics must not silently override canonical doctrine.
- Engineer-first harvesting should surface friction and evidence-quality problems, not auto-generate trading policy.

## Future Toolkit Intent
This atlas should stay stable enough that future BRO-derived systems can reuse the same measurement language.

That means:
- preserve window metrics
- preserve money and wallet truth metrics
- preserve lifecycle integrity metrics
- preserve valuation degradation metrics
- preserve feed-health metrics

The goal is a house-level diagnostic spine, not a one-off BRO report script.
