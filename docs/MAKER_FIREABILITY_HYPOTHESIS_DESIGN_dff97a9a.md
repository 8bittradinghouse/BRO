# Maker Fireability Hypothesis Design: `dff97a9a-9953-47e6-939f-f5f1f8814ed4`

## Purpose
Turn the first strong post-lathe maker-valid specimen into a bounded
engineering-cut plan.

This packet does **not** change runtime behavior.
It exists to define the cleanest next maker cuts so we can:
- improve maker fireability inside the real combat window,
- preserve current honest strengths,
- avoid blind threshold loosening,
- and reduce the chance of reopening the same archaeology later.

## Scope
Primary specimen:
- `dff97a9a-9953-47e6-939f-f5f1f8814ed4`

Primary anchors:
- `docs/MAKER_FIREABILITY_FORENSIC_dff97a9a.md`
- `docs/SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md`
- `docs/MAKER_FORENSIC_8bfb70eb.md`
- `docs/MAKER_COMPARATOR_8bfb_e675_ed184.md`

Primary config surfaces:
- `configs/profiles/paper_universal.yaml`
- `prodesk/order_manager.py`
- `executor.py`

## Doctrine Status
`VERIFIED`:
- this packet was written under a drift-era runtime posture where the active
  maker timing gate lived at `50-60s`.
- intended maker timing doctrine remains anchored on:
  - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
  - doctrine window: `15-20s`
  - sweet spot: `10-15s`
- the `50-60s` frame discussed below is preserved as runtime history for this
  specimen packet, not as current maker doctrine.

## Executive Truth
1. `VERIFIED`: this specimen packet was cut under a drift-era `50-60s` runtime
   posture that looked mechanically consistent in its own frame, not obviously
   broken.
2. `VERIFIED`: inside that live combat window, the biggest no-fire suppressor is
   `replace_guard_min_rest`, not quote quality.
3. `VERIFIED`: quote-quality suppressions are real, but they are split between
   near-threshold and clearly-bad rejects.
4. `VERIFIED`: completed multi-fill fights remain the ugliest maker wound and
   must be protected against while improving fireability.
5. `INFERRED`: the best next engineering move is not broad threshold loosening.
   It is a guarded cut ladder:
   - shadow truth first,
   - then a minimal rest-cadence experiment,
   - then quote-quality split experiments if still justified.

Plain-English:
maker is not failing because it cannot reach the trigger.
It is failing because the real combat window is narrow, the replace guard is
heavy inside that window, and the wrong completed fight geometry is still
dangerous enough that we cannot just “let it rip.”

## Pinned Evidence
### 1. Real maker combat-zone anatomy
`VERIFIED` from raw `edge_evaluation` maker rows:
- total maker rows: `364`
- `maker_timing_gate_closed = 324`
- active window rows (`50 <= sec_to_expiry <= 60`): `40`

Inside the active window:
- `submitted = 16`
- `replace_guard_min_rest = 16`
- `quote_quality_skip_fill_probability = 6`
- `quote_quality_skip_queue_depth = 2`

Window split:
- submit share: `16 / 40 = 0.40`
- replace-guard share: `16 / 40 = 0.40`
- quote-quality share: `8 / 40 = 0.20`

`VERIFIED`: once maker gets into the real fire zone, min-rest suppression is as
large as actual firing.

### 2. Submit cadence matches the configured rest guard
`VERIFIED`:
- `maker_replace_min_rest_sec = 3.0`
- submitted decision rows clustered at:
  - target pair `808f...` / `b2a0...`
    - `59.9136`
    - `56.9018`
    - `52.8932`
  - target pair `83f3...` / `d93f...`
    - `59.1287`
    - `56.1167`
    - `52.8648`
    - `51.1174`
    - `50.1199`

Approximate observed gaps:
- around `3.0s`
- one late tighter burst around `1.75s`
- one final tighter burst around `1.0s`

`INFERRED`: the min-rest guard is not theoretical. It is one of the main
shapers of actual within-window cadence.

### 3. Quote-quality rejects are split, not uniform
`VERIFIED` from raw `quote_quality_skip` events:
- fill-probability rejects: `10`
- queue-depth rejects: `8`

Fill-probability severity:
- within `0.005` of threshold: `4`
- `0.005..0.015` below threshold: `2`
- more than `0.015` below threshold: `4`

Queue-depth severity:
- within `25` shares of threshold: `2`
- `25..50` shares above threshold: `2`
- more than `50` shares above threshold: `4`

`VERIFIED`: roughly half of the quote-quality rejects are near-threshold.
`VERIFIED`: the other half are clearly low-quality and should not be lumped into
the same tuning story.

### 4. Multi-fill wound remains the main economic danger
`VERIFIED` from harvested specimen truth:
- `maker_complete_record_count = 12`
- `maker_incomplete_record_count = 12`
- `maker_complete_bad_ratio = 0.6666666666666666`
- `maker_multifill_complete_count = 6`
- `maker_multifill_complete_incorrect_ratio = 1.0`
- `maker_singlefill_correct_ratio = 0.6666666666666666`

`VERIFIED`: completed multi-fill geometry is still toxic in this specimen.

### 5. Same-target repetition is not an abstract risk
`VERIFIED`:
- the same four targets account for the whole specimen's maker outcome surface
- two target families repeatedly re-engaged inside the same drift-era
  `50-60s` runtime window
- each target family saw repeated submit / no-submit cycling before expiry

`INFERRED`: if we relax cadence blindly, we risk increasing repeat-target churn
and worsening the same multifill wound that is already the worst completed
family.

## Strong Things To Preserve
1. `VERIFIED`: the drift-era `50-60s` runtime gate was mechanically honest in
   this specimen.
   Plain-English: under that old runtime frame, the packet did not earn
   widening the whole combat zone first.
2. `VERIFIED`: single-fill geometry is materially healthier than multi-fill
   geometry.
   Plain-English: keep what little clean bite the cannon already has.
3. `VERIFIED`: quote-quality filters are numerically explicit and observable.
   Plain-English: we can tune them surgically later if needed instead of using
   vibes.

## Main Hypotheses
### H1. The next best fireability gain is cadence-related, not window-related
`VERIFIED` support:
- all submits already live inside the drift-era `50-60s` runtime box sampled by
  this packet
- `324` timing-gate closures mostly happen outside that box
- inside the box, `replace_guard_min_rest` matches submit count exactly

`INFERRED` engineering read:
- the first useful fireability cut is probably not to widen the time box
- it is to inspect whether the current `3.0s` rest guard is too conservative
  for current maker competitiveness doctrine

### H2. Any cadence relaxation can worsen the wrong-fight geometry
`VERIFIED` support:
- `multifill_complete_incorrect_ratio = 1.0`
- repeated target families already dominate this specimen

`INFERRED` engineering read:
- more frequent re-entry can raise participation and still make economics worse
- cadence work must be paired with repeat-target / fill-geometry protection

### H3. Quote-quality tuning should be split into near-threshold and hard-bad lanes
`VERIFIED` support:
- `4/10` fill-probability rejects are within `0.005`
- `4/8` queue-depth rejects are within `50` shares
- the rest are clearly bad

`INFERRED` engineering read:
- if quote-quality tuning opens later, it should be tiered
- broad lowering of `min_expected_fill_prob` or broad raising of
  `max_queue_ahead_size` is below house standard

### H4. First cut should be truth and shadow pressure, not blind live mutation
`VERIFIED` support:
- this is one valid specimen, not a sacred universal law
- current maker economics are still fragile on completed fights

`INFERRED` engineering read:
- first promote instrumentation and shadow counters
- then test one knob at a time under paper-only, watched conditions

## Guarded Experiment Ladder
### Phase A. Truth hardening first
Goal:
- make future cadence and quality experiments cheaper and more honest

`VERIFIED_CLOSED`:
- this phase is now implemented in:
  - `scripts/nightly_soak_report.py`
  - `scripts/bro_metric_harvest.py`
- the live specimen and current archive now emit:
  - explicit active-window maker counts and rates
  - per-target active-window summaries
  - raw quote-quality severity bins with claim-boundary language

Recommended additions:
1. add explicit within-window maker surfaces
   - `maker_window_active_row_count`
   - `maker_window_submit_count`
   - `maker_window_replace_guard_count`
   - `maker_window_quote_quality_skip_fill_probability_count`
   - `maker_window_quote_quality_skip_queue_depth_count`
2. add same-target cadence surfaces
   - per-target submit cadence
   - per-target replace-guard cadence
   - per-target window participation density
3. add quote-skip severity bins to report/harvest surfaces
   - near-threshold
   - medium miss
   - hard miss

`VERIFIED_RECOMMENDATION`: do this before runtime tuning if we want later
experiments to be cheaper and less ambiguous.

### Phase B. Minimal cadence experiment
Goal:
- test whether maker participation improves materially from a modest min-rest
  cut without immediately exploding repeat-target or multi-fill pain

`VERIFIED_TESTED`:
- first cadence specimen:
  - `2f607a7b-d5ec-4882-a521-9f07ca3e9dd5`
- result packet:
  - `docs/MAKER_CADENCE_EXPERIMENT_RESULT_2f607a7b.md`
- top-line outcome:
  - `replace_guard_min_rest` was removed as an active-window suppressor
  - maker fireability still degraded sharply
  - sizing and queue-depth friction became the dominant blockers

Plain-English:
the first cadence cut answered a real question, but it did not earn adoption.

Candidate cut:
- paper-only variant:
  - `maker_replace_min_rest_sec: 3.0 -> 2.0`

Why this cut first:
- it attacks the largest within-window suppressor directly
- it is smaller and safer than widening the full timing gate
- it is easier to attribute than multi-knob changes

Required guardrails:
- no timing-window change in the same experiment
- no quote-quality threshold change in the same experiment
- no sizing policy change in the same experiment

Success signals:
- more maker submits inside the existing drift-era `50-60s` runtime window
- no blowout increase in:
  - `maker_multifill_complete_count`
  - `maker_multifill_complete_incorrect_ratio`
  - same-target repeat cluster burden

Abort signals:
- single-fill strength degrades materially
- repeat-target churn spikes without cleaner completed outcomes
- completed bad ratio worsens without a meaningful fireability gain

### Phase C. Split quote-quality experiment
Goal:
- recover some of the near-threshold suppressions without admitting clearly bad
  stock

Candidate cuts, one at a time:
1. fill-probability split:
   - small bounded relaxation only for near-threshold misses
2. queue-depth split:
   - small bounded relaxation only for shallow threshold overruns

Design rule:
- do **not** run both changes at once
- do **not** use a blanket relaxed quality floor

Why this comes after cadence:
- quote-quality is a smaller suppressor than min-rest in the current specimen
- broad quality loosening has a higher risk of worsening bad completed fights

### Phase D. Timing-window reassessment only if earlier cuts fail
Goal:
- reopen that drift-era `50-60s` runtime box only if cadence and split-quality
  work do not explain
  enough of the suppressed participation

Current posture:
- `VERIFIED`: no evidence yet justifies widening the box first
- `INFERRED`: this should be a later, evidence-earned reconsideration, not the
  opening move

## Non-Goals
1. do not widen the timing gate first
2. do not lower `min_expected_fill_prob` broadly
3. do not raise `max_queue_ahead_size` broadly
4. do not loosen multiple fireability knobs in one packet
5. do not use this specimen to claim maker tuning closure

## Recommended Next Packet
`VERIFIED_RECOMMENDATION`: the best next engineering packet is:
- **maker fireability instrumentation and shadow-pressure packet**

Target surfaces:
- `scripts/nightly_soak_report.py`
- `scripts/bro_metric_harvest.py`
- possibly small runtime observability additions where needed

Intent:
- promote within-window and same-target cadence truth
- promote quote-skip severity truth
- keep runtime behavior unchanged unless an observability hole truly requires
  surgical emission

Plain-English:
before we cut the cannon, add better gauges around the chamber and trigger.

## Bottom Line
`VERIFIED`: the current maker specimen says the real fight in this historical
packet lived inside the existing drift-era `50-60s` runtime box.

`VERIFIED`: the biggest next suppressor inside that box is
`replace_guard_min_rest`.

`VERIFIED`: quote-quality rejects matter, but they are mixed between
near-threshold and obviously-bad stock.

`INFERRED`: the highest-ROI next move is not blind tuning.
It is:
1. truth hardening for within-window fireability,
2. then a modest paper-only cadence experiment,
3. then split quote-quality experiments if still justified.
