# BRO Baseline Run Set: USA PM `2026-04-27`

## Purpose
Preserve the first intentional multi-specimen baseline harvest packet as one
named reusable stock set.

This is **not** the full future data-pack cabinet system.
It is the seed artifact that proves the concept and keeps tonight's stock from
turning into loose logs.

Plain-English:
this is the first marked pile of good shop steel.

## Set Identity
- set id:
  - `bro_baseline_run_set_usa_pm_2026-04-27_a`
- profile:
  - `paper_universal`
- baseline config fingerprint:
  - `bc8873395234bb1aef36b8d3f8d3d07a786ae8cad1a37f3ab1dcf18d48d293e9`
- code fingerprint:
  - `26f54c31fa5e2e93357313ed46eadc7074b77a8cc718ccef15d3bded0883c517`
- git commit:
  - `519f6ed188c7bde92e674512072d34ecc9d0ba1e`

## Accepted Runs
### Clean baseline runs
1. `f79550de-22f2-439a-844a-09fa27d5fc48`
   - canonical result:
     - `status=pass`
     - `runtime_classification=VALID_ACTIVE`
     - `promotion_eligible=true`
     - `gate_passed=true`
   - stock summary:
     - `maker_submits=29`
     - `maker_fills=39`
     - `maker_window_submit_count=27`
     - `maker_window_quote_quality_skip_total_count=27`
     - `maker_window_low_price_viability_floor=0.04375`
     - `maker_window_sizing_reject_count=9`
     - `maker_window_viable_row_count=71`
     - `maker_window_impossible_row_count=9`
     - `maker_min_notional_max_shares_conflict_rows=10`
     - `maker_complete_bad_ratio=0.6842105263157895`
     - `maker_multifill_complete_incorrect_ratio=0.5384615384615384`

2. `8fabbe70-742a-4e74-b791-2f885a59a010`
   - canonical result:
     - `status=pass`
     - `runtime_classification=VALID_ACTIVE`
     - `promotion_eligible=true`
     - `gate_passed=true`
   - stock summary:
     - `maker_submits=21`
     - `maker_fills=13`
     - `maker_window_submit_count=21`
     - `maker_window_quote_quality_skip_total_count=23`
     - `maker_window_low_price_viability_floor=0.04375`
     - `maker_window_sizing_reject_count=0`
     - `maker_window_viable_row_count=80`
     - `maker_window_impossible_row_count=0`
     - `maker_min_notional_max_shares_conflict_rows=0`
     - `maker_complete_bad_ratio=0.5`
     - `maker_multifill_complete_incorrect_ratio=0.0`

### Accepted with warning tag
3. `3f1bc263-e209-4e05-a510-1218d63d70f4`
   - canonical result:
     - `status=pass`
     - `runtime_classification=VALID_ACTIVE`
     - `promotion_eligible=true`
     - `gate_passed=true`
   - warning tag:
     - `soak_hardening_gate.ok=true`
     - `finding_count=1`
     - `soak_maker_submits_too_low:24.000000<min:27.000000`
   - stock summary:
     - `maker_submits=24`
     - `maker_fills=23`
     - `maker_window_submit_count=24`
     - `maker_window_quote_quality_skip_total_count=12`
     - `maker_window_low_price_viability_floor=0.04375`
     - `maker_window_sizing_reject_count=7`
     - `maker_window_viable_row_count=67`
     - `maker_window_impossible_row_count=10`
     - `maker_min_notional_max_shares_conflict_rows=14`
     - `maker_complete_bad_ratio=0.75`
     - `maker_multifill_complete_incorrect_ratio=0.75`

## Excluded From Clean Baseline
4. `0cfbe40a-8a9c-4ff6-b094-bec6c200ef23`
   - runtime truth:
     - `runtime_classification=VALID_ACTIVE`
     - `promotion_eligible=true`
   - exclusion reason:
     - `status=policy_failed`
     - `gate_passed=false`
     - soak findings:
       - `performance_cycle_latency_max_too_high`
       - `performance_cycle_span_residual_too_high`
   - interpretation:
     - useful warning-tag stock
     - not clean-control stock

Plain-English:
this run is valuable, but it belongs in the shop with a warning label on it.

## Runtime-Neutral Viability Shadow Refresh
`VERIFIED`:
- the baseline set has now been replay-refreshed through the current
  `nightly_soak_report` surfaces and recut through both shop tools.
- every baseline run now carries the same geometry floor:
  - `maker_window_low_price_viability_floor=0.04375`
- low-price impossibility is present in only two stock members:
  - `f795...`
    - `maker_window_sizing_reject_count=9`
    - `maker_window_impossible_row_count=9`
    - mixed viability target present at `0.015..0.035`
  - `3f1bc...`
    - `maker_window_sizing_reject_count=7`
    - `maker_window_impossible_row_count=10`
    - one impossible-only target family at `0.01..0.035`
- the other two runs are fully viable under current geometry:
  - `8fab...`
    - `maker_window_sizing_reject_count=0`
    - `maker_window_impossible_row_count=0`
  - `0cfbe...`
    - `maker_window_sizing_reject_count=0`
    - `maker_window_impossible_row_count=0`
- baseline-only corpus truth:
  - `maker_window_viable_row_count_total=298`
  - `maker_window_impossible_row_count_total=19`
  - `maker_window_sizing_reject_count_total=16`
  - `maker_min_notional_max_shares_conflict_rows_total=24`
  - `maker_window_queue_depth_on_viable_targets_count_total=23`
  - `maker_window_queue_depth_on_impossible_targets_count_total=0`
  - `viable_target_total=30`
  - `impossible_target_total=1`
  - `mixed_target_total=1`

Current interpretation:
- low-price geometry conflict is real, but it is not universal across the
  baseline stock
- queue-depth friction in this baseline set is landing on viable targets, not
  on impossible ones
- the queue packet built from this stock was a valid question, but it fast-failed
  cleanly at runtime because the candidate family barely existed in live proof
- the next useful use of this stock is selectivity driver discovery, not more
  queue sanding
- cadence retesting is not earned as the next move
- broad quote-quality loosening is not earned as the next move

## Report-Layer Admission Shadow Recut
`VERIFIED`:
- this baseline set has now been recut through the maker fight-admission shadow
  pipeline.
- current bundle:
  - `logs_exec/paper_universal/forge_masters_archive_baseline_set_usa_pm_2026-04-27_admission_shadow`
- row truth:
  - `row_count=204`
  - all rows currently come from `legacy_quote_or_submit_backfill_v1`
  - `candidate=118`
  - `truth_thin=86`
  - `clean=64`
  - `borderline=28`
  - `trash=26`
- mature-outcome join truth:
  - `clean_complete_joined=34`
  - `borderline_complete_joined=15`
  - `trash_complete_joined=0`
  - `clean_complete_bad_ratio=0.7352941176470589`
  - `borderline_complete_bad_ratio=0.7333333333333333`
  - `clean_multifill_incorrect_ratio=0.6190476190476191`
  - `borderline_multifill_incorrect_ratio=0.8`
- dominant driver families:
  - `size_liquidity_pressure=52`
  - `repeat_target_side_pressure=40`
  - `fill_prob_margin_lt_neg_0p015=19`
  - `queue_delta_gt_50=7`

Current interpretation:
- this recut is useful for selectivity-driver discovery
- it does **not** yet prove that the selector can separate good submitted fights
  from bad submitted fights
- `trash` is mostly a no-submit / blocked-fight population in this stock
- the strongest next engineering question is how to tighten selection around
  `size_liquidity_pressure` and `repeat_target_side_pressure`, not how to keep
  sanding queue knobs

## Lane Usefulness
### Maker
`VERIFIED`:
- all four runs produced usable maker evidence
- the set spans:
  - healthier viable stock
  - quote-friction-heavy stock
  - low-price-conflict stock
  - one warning-tag latency specimen

### Taker
`VERIFIED`:
- no normal taker submit specimen emerged in this set
- this set is still useful for taker non-fire / lane-presence truth where
  harvested surfaces exist

### Sniper
`VERIFIED`:
- no sniper-specific execution specimen was earned here
- this set does not replace a future sniper mapping packet

### Wallet / Pathways / Runtime health
`VERIFIED`:
- the accepted runs are strong whole-machine health stock
- watched under-the-hood checks showed:
  - live feeds connected cleanly after startup
  - no reconnect churn in the watched windows
  - no execution error
  - no sustained open-order pileup in the accepted runs

## Shop Outputs
Per-run `FMA` bundles:
- `logs_exec/paper_universal/forge_masters_archive_run_f79550de-22f2-439a-844a-09fa27d5fc48`
- `logs_exec/paper_universal/forge_masters_archive_run_0cfbe40a-8a9c-4ff6-b094-bec6c200ef23`
- `logs_exec/paper_universal/forge_masters_archive_run_8fabbe70-742a-4e74-b791-2f885a59a010`
- `logs_exec/paper_universal/forge_masters_archive_run_3f1bc263-e209-4e05-a510-1218d63d70f4`

Per-run lathe cuts:
- `logs_exec/paper_universal/fusion_core_profile_run_f79550de-22f2-439a-844a-09fa27d5fc48`
- `logs_exec/paper_universal/fusion_core_profile_run_0cfbe40a-8a9c-4ff6-b094-bec6c200ef23`
- `logs_exec/paper_universal/fusion_core_profile_run_8fabbe70-742a-4e74-b791-2f885a59a010`
- `logs_exec/paper_universal/fusion_core_profile_run_3f1bc263-e209-4e05-a510-1218d63d70f4`

Refreshed shared shop stock:
- `logs_exec/paper_universal/forge_masters_archive_latest`
- `logs_exec/paper_universal/fusion_core_profile_latest`

Baseline-only corpus cuts:
- `logs_exec/paper_universal/forge_masters_archive_baseline_set_usa_pm_2026-04-27`
- `logs_exec/paper_universal/fusion_core_profile_baseline_set_usa_pm_2026-04-27`

## Best Uses
1. baseline control stock for future paper-only maker experiments
2. dead-zone analysis fuel for `FMA` and `FM-2A1`
3. future mill input once the decision-cutting tool exists
4. reference stock for later:
   - low-price viability work
   - fight-admission / selectivity rubric work
   - multi-fill wound work
   - taker/sniper mapping comparisons when those lanes are ready

## Boundary
`VERIFIED`:
- this is not a sacred universal regime set
- this is one good USA-PM baseline pack under one locked profile/config/code

`INFERRED`:
- later we should add sibling packs such as:
  - nightmare runs
  - thin-liquidity runs
  - transition-regime runs
  - experiment-control packs
