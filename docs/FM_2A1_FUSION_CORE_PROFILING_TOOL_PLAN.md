# FM-2A1 Fusion Core Profiling Tool

## Purpose
`FM-2A1` is the BRO shop `lathe`.

It is a standalone, read-only profiling tool that sits downstream of
`FM-1A1 FMA` and shapes harvested truth into:
- profile families,
- wound families,
- strength families,
- stability grades,
- and candidate design blanks.

Plain-English:
`FMA` harvests the metal.
`FM-2A1` turns the metal into shaped stock we can design from without touching
runtime behavior.

## Final Architecture Lock
`VERIFIED`: the tool is now built as a **hard-decoupled machine**.

That means:
- separate entrypoint,
- separate tests,
- no import of `FMA` business logic,
- communication only through exported artifacts and manifest/schema contracts.

Current chain:

```text
BRO artifacts -> FM-1A1 FMA -> FM-2A1 Fusion Core Profiling Tool -> future decision mill
```

## Implemented Foundation
### Contract surface
- preferred upstream contract:
  - `fma_bundle_manifest.json`
- legacy compatibility:
  - if the manifest is missing, `FM-2A1` derives a bounded compatibility
    manifest and downgrades trust
- required inputs:
  - `run_index.jsonl`
  - `anomaly_summary.json`
  - `metric_catalog.json`
- optional deep inputs:
  - per-run `outcome_truth_records.jsonl`
  - per-run `outcome_truth_audit.json`
  - per-run `nightly_soak_report.json`

### Mode model
- `specimen`
  - single-run or single-specimen profiling
  - can never emit `strong` stability grades
- `corpus`
  - cross-run profiling
  - can emit `strong` grades when gates are met

### Lane depth model
- `maker`
  - `full_depth` when deep outcome coverage crosses the current coverage gate
  - `mixed_depth_partial_deep` when deep outcome records exist but only cover part
    of the bundle
  - otherwise `bounded_depth`
- `taker`
  - `bounded_depth` only
- `sniper`
  - `bounded_depth` only

### Current outputs
- `fusion_core_input_contract_audit.json`
- `fusion_core_lane_readiness.json`
- `fusion_core_profile_catalog.json`
- `fusion_core_profile_cards.md`
- `fusion_core_stability_matrix.json`
- `fusion_core_candidate_blanks.json`
- `fusion_core_cohort_comparison.csv`
- `fusion_core_profile_diff.json`

## Math Placement
### In scope for the lathe
- expected value / conditional expectation
- stochastic or survival-style fill and maturity summaries
- microstructure / friction / inventory-cost summaries

### Explicitly out of scope
- Bayesian updating
- optimization / control
- risk sizing / utility
- live action recommendation
- runtime/config mutation

Plain-English:
the lathe shapes profile truth.
It does not cut live decision policy.

## Current Profile Families
### Maker
- `outcome_balance`
- `multifill_wound`
- `singlefill_strength`
- `execution_rescue_geometry`
- `repeat_target_cluster`
- `complement_pair_cluster`
- `friction_burden`
- `valuation_pressure`

### Taker
- `window_conversion_overview`

### Sniper
- chassis slot exists, but current output remains bounded and dormant until
  sniper truth mapping is earned

## Guardrails
- fail-closed stability grading
- explicit population/provenance tagging on every profile
- deterministic `profile_id` and `cohort_signature`
- deterministic output ordering for diffability
- candidate blanks stop below live policy recommendations
- bounded heuristics stay labeled as heuristics

## Implemented Pass 2 Hardening
`VERIFIED`: the semantic red-team hardening pass is now implemented.

That pass added:
- explicit contract derivation and snapshot-integrity truth
- deep artifact coverage reporting
- explicit downgrade and suppression reason codes
- lifecycle-basis provenance
- friction population-accounting notes
- valuation-pressure profile hooks
- diff semantics for specimen-vs-corpus comparisons

## Implemented Pass 3 Promotion
`VERIFIED`: the profile-quality promotion pass is now implemented.

That pass added:
- bounded-depth lane grade capping so `taker` and other bounded lanes cannot
  silently promote to `strong`
- strength-signal honesty for `singlefill_strength`
  - weak single-fill quality is explicitly capped and tagged with
    `family_signal_not_positive`
- rescue-ratio denominator hardening
  - near-zero decision-debt records are excluded from rescue-ratio summaries and
    counted explicitly
- compact candidate-blank evidence for cluster families
  - large raw cluster payloads are compressed into sampleable, diffable evidence
- stronger headline metric selection
  - higher-signal metrics such as `valuation_degraded_ratio` and
    `execution_rescue_overcome_rate` surface first
- profile-card honesty upgrades
  - cards now surface `signal_posture` and effective downgrade/suppression
    reasons directly

## Implemented Pass 4 Truth Hardening
`VERIFIED`: the lane-maturity, valuation-semantics, and contract-trust pass is
now implemented.

That pass added:
- partial-deep maker truth
  - maker lane readiness no longer overclaims `full_depth` from partial deep
    coverage
  - the lathe now surfaces `mixed_depth_partial_deep` with explicit deep-coverage
    ratio reporting
- corpus valuation bruise-state honesty
  - mixed corpus bruise populations are surfaced as `mixed_bruise_states`
  - bruise-state distributions are now emitted machine-readably
- contract trust signaling
  - audit output now distinguishes clean contracts from warning-bearing contracts
  - `ok_with_warnings` and `contract_health` make derived-manifest stock easier
    to read correctly

## Implemented Pass 5 Diff Hardening
`VERIFIED`: the metric-drift diff pass is now implemented.

That pass added:
- numeric headline-metric delta tracking in profile diffs
- `metric_value_changes` for changed numeric headline surfaces
- `metric_drift_candidates` for meaningful drift where grades hold steady
- fixture coverage for unchanged-grade / changed-metric diff cases

## Implemented Pass 6 Calibration Hardening
`VERIFIED`: the calibration and promotion-audit pass is now implemented.

That pass added:
- explicit stability policy surfaces
  - bounded and strong floors are now machine-readable instead of living only
    inside grade logic
- profile-level promotion readiness
  - each profile now carries:
    - bounded readiness
    - strong readiness
    - blocker lists
    - sample/run gaps to stronger promotion
- lane-level promotion requirements
  - maker now states why it is still `mixed_depth_partial_deep`
  - taker and sniper now state why they remain bounded-depth lanes
- calibration audit output
  - `fusion_core_calibration_audit.json` summarizes:
    - lane blocker counts
    - near-strong profiles
    - promotion pressure
    - global and per-lane policy surfaces
- profile-card honesty upgrades
  - cards now surface lane blockers and profile strong-blockers directly

## Implemented Pass 7 Threshold Pressure Hardening
`VERIFIED`: the threshold-pressure pass is now implemented.

That pass added:
- multi-policy grade projection
  - `current`, `tighter`, and `looser` policy projections now exist without
    mutating the canonical grading policy
- threshold pressure matrix output
  - `fusion_core_threshold_pressure_matrix.json` summarizes:
    - per-preset lane grade counts
    - per-profile grade projections
    - pressure-sensitive profiles
    - threshold-invariant profiles
    - robust strong profiles
- structural-blocker visibility
  - the matrix now distinguishes:
    - profiles that would move under different thresholds
    - profiles that stay put because structural blockers still dominate

Real-stock lesson from this pass:
- current corpus profiles are largely threshold-invariant
- active blockers are structural:
  - partial deep maker coverage
  - heuristic-only maker families
  - bounded-depth taker lane truth
- that is useful because it tells future calibration packets not to waste time
  pretending threshold twiddling is the main issue

## Remaining Hardening Passes
This is still not sacred final metal.

Still pending:
1. wider-lane promotion only after earned truth mapping, especially for taker
   and sniper
2. future-corpus calibration review as new stock arrives
3. additional low-cost shop attachments where they earn their keep

Current pass-2 skunkworks packet:
- `docs/FM_2A1_FUSION_CORE_PROFILING_TOOL_PASS2_PLAN.md`
