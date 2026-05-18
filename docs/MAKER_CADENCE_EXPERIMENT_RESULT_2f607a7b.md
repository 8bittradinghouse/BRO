# Maker Cadence Experiment Result: `2f607a7b-d5ec-4882-a521-9f07ca3e9dd5`

## Purpose
Evaluate the first guarded paper-only maker cadence cut:
- `runtime.maker_replace_min_rest_sec: 3.0 -> 2.0`

Boundary conditions:
- no timing-window widening
- no quote-quality threshold loosening
- no sizing-policy change

This packet exists to decide whether cadence was the real first fireability
limiter or only a visible friction seam.

## Doctrine Status
`VERIFIED`:
- this experiment was run under a drift-era runtime posture where maker timing
  was configured at `50-60s`.
- intended maker timing doctrine remains anchored on:
  - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
  - doctrine window: `15-20s`
  - sweet spot: `10-15s`
- any `50-60s` references below are specimen-bounded runtime history, not
  current maker doctrine.

## Experiment Integrity
`VERIFIED`:
- canonical session runner used:
  - `scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25`
- specimen run:
  - `2f607a7b-d5ec-4882-a521-9f07ca3e9dd5`
- canonical validation result:
  - `status=pass`
  - `runtime_classification=VALID_ACTIVE`
  - `promotion_eligible=true`
- experimental config fingerprint:
  - `4025551666d6f2f54776f252b7cb3bac233e2706720c164f1c06b849520ea5ee`
- canonical paper profile has already been restored to the prior locked baseline
  after the experiment.

Primary artifacts:
- `logs_exec/paper_universal/reports/2f607a7b-d5ec-4882-a521-9f07ca3e9dd5/*`
- `logs_exec/paper_universal/forge_masters_archive_run_2f607a7b-d5ec-4882-a521-9f07ca3e9dd5/*`
- `logs_exec/paper_universal/fusion_core_profile_run_2f607a7b-d5ec-4882-a521-9f07ca3e9dd5/*`

Comparison baseline:
- `dff97a9a-9953-47e6-939f-f5f1f8814ed4`

## Executive Result
1. `VERIFIED`: the cadence cut removed `replace_guard_min_rest` as an active
   within-window suppressor in this specimen.
2. `VERIFIED`: maker fireability did **not** improve.
3. `VERIFIED`: the bottleneck migrated into:
   - sizing rejects on low-price targets
   - queue-depth quote-quality rejects on the one viable active target family
4. `INFERRED`: cadence was a visible seam, but not the first true limiter in
   this regime/sample.

Plain-English:
the `2.0s` cut worked mechanically, but it did not open the cannon.
It just exposed deeper fireability bottlenecks.

## Pinned Comparison Truth
### Baseline specimen `dff97a9a...`
`VERIFIED`:
- maker submits: `24`
- maker fills: `24`
- active-window rows: `40`
- active-window submits: `16`
- active-window replace-guard: `16`
- active-window quote-quality skips: `8`
- active-window submit rate: `0.4`
- active-window replace-guard rate: `0.4`
- active-window quote-quality skip rate: `0.2`
- no-submit causes:
  - `replace_guard_min_rest = 16`
  - `submit_rejected_quote_quality_skip_fill_probability = 6`
  - `submit_rejected_quote_quality_skip_queue_depth = 2`
- maker outcome truth:
  - `complete_record_count = 12`
  - `incomplete_record_count = 12`
  - `complete_bad_ratio = 0.6666666666666666`
  - `multifill_complete_incorrect_ratio = 1.0`

### Cadence specimen `2f607a7b...`
`VERIFIED`:
- maker submits: `2`
- maker fills: `0`
- active-window rows: `40`
- active-window submits: `2`
- active-window replace-guard: `0`
- active-window quote-quality skips: `8`
- active-window submit rate: `0.05`
- active-window replace-guard rate: `0.0`
- active-window quote-quality skip rate: `0.2`
- no-submit causes:
  - `submit_rejected_quote_quality_skip_queue_depth = 8`
  - `submit_rejected_sizing_reject = 10`
- maker outcome truth:
  - `complete_record_count = 0`
  - `incomplete_record_count = 2`
  - `filled_complete_ratio = 0.0`
  - `complete_classification_ratio = 0.0`

## Deep Anatomy
### 1. Cadence seam was real but not sufficient
`VERIFIED`:
- `replace_guard_min_rest` went from `16` inside the active window to `0`
- submitted rows in the cadence specimen still occurred with roughly
  `~3s` spacing:
  - `53.883263`
  - `50.909173`

`INFERRED`:
- the old `3.0s` guard was suppressing some rows in the baseline specimen
- but removing that local suppressor did not unlock broad participation

### 2. Low-price sizing conflict became the dominant hard wall
`VERIFIED` from `maker_sizing_competitiveness`:
- `maker_min_notional_max_shares_conflict_rows = 20`
- `maker_sizing_reject_rows = 20`
- sizing reject posture:
  - `NORMAL = 20`
- sizing reject reasons:
  - `maker_hard_max_shares_cap = 20`
  - `maker_hard_min_notional_failed_after_rounding = 20`
  - `maker_hard_min_notional_floor = 20`
- reject price band:
  - `min = 0.025`
  - `p50 = 0.025`
  - `max = 0.035`
- reject max-shares notional:
  - `p50 = 200.0`
  - `max = 280.0`

Plain-English:
half the window rows were on targets that could not satisfy the current maker
minimum notional / share geometry at those prices.

### 3. Queue depth became the dominant live-target friction
`VERIFIED`:
- one target family (`b96689f1a9e1c55b`) carried the only submits
- that same family also carried:
  - `8` active-window queue-depth quote-quality suppressions
- raw non-recovery queue-depth severity bins:
  - `gt_50 = 14`
  - `within_25 = 1`

`INFERRED`:
- once cadence stopped blocking, the viable target family still hit a real
  queue-depth wall
- this is not a near-threshold gentle friction story in this specimen

### 4. This run did not worsen multi-fill wound because it never reached that layer
`VERIFIED`:
- `maker_fills = 0`
- `complete_record_count = 0`
- `multifill_complete_count = 0`

Plain-English:
the experiment did not make maker economics worse here because it mostly failed
before execution, not because it solved the decision problem.

## Lathe Read
`VERIFIED` from `FM-2A1` specimen cut:
- no candidate blanks promoted
- `friction_burden` stayed `thin`
- `execution_rescue_geometry`, `multifill_wound`, and `singlefill_strength`
  were suppressed by `zero_eligible_records`
- specimen diff vs `dff97a9a...` flagged:
  - suppressed execution-rescue geometry
  - suppressed multifill wound
  - suppressed singlefill strength
  - metric drift in repeat-cluster count

`INFERRED`:
- this is consistent with a specimen that became less active and less
  economically informative, not more decisive

## Judgment
`VERIFIED`:
- the `2.0s` cadence cut is **not** earned for adoption
- canonical paper profile should remain at `3.0s` for now

`INFERRED`:
- the next highest-ROI lane is **not** another blind cadence cut
- the next lane is a bounded maker viability / queue-depth forensic and design
  pass:
  - low-price min-notional / max-shares conflict
  - active-target queue-depth friction
  - preserve timing-window doctrine
  - preserve quote-quality split discipline

## Recommended Next Packet
1. `VERIFIED_RECOMMENDATION`: keep `paper_universal` back at `3.0s`.
2. `VERIFIED_RECOMMENDATION`: under this experiment's drift-era `50-60s`
   runtime posture, the packet did **not** earn widening that old runtime
   window.
3. `VERIFIED_RECOMMENDATION`: do **not** broadly loosen quote-quality gates.
4. `INFERRED_RECOMMENDATION`: open a bounded maker low-price viability /
   queue-depth packet:
   - machine-readable price-band viability counts
   - `maker_min_notional_max_shares_conflict` promotion into first-class
     experiment surfaces
   - queue-depth friction anatomy on viable targets
5. `INFERRED_RECOMMENDATION`: only after that truth pass, decide whether the
   next paper-only cut belongs in:
   - maker competitive notional geometry
   - max-shares / low-price viability handling
   - or tiered queue-depth tolerance on near-threshold cases
