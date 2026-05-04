# Maker Low-Price Viability / Queue-Depth Forensic and Design

## Purpose
Turn the first three valid post-lathe maker specimens into a bounded engineering
cut plan for the next maker lane.

This packet does **not** change runtime behavior.
It exists to pin which fireability blockers are:
- persistent,
- regime-dependent,
- worth promoting into first-class truth surfaces,
- and dangerous to tune blindly.

Plain-English:
we now have enough healthy maker stock to stop guessing.
This packet says which friction is real, which friction moves around by regime,
and what must be instrumented before the next knob cut.

## Scope
Primary valid specimens:
- `dff97a9a-9953-47e6-939f-f5f1f8814ed4`
- `2349650c-5a83-4d65-8e7a-c2c4578c2cd6`
- `2c26e81e-084f-4b85-854d-161d9d81c2b4`

Control specimen:
- `2f607a7b-d5ec-4882-a521-9f07ca3e9dd5`
  - first cadence experiment with `maker_replace_min_rest_sec: 3.0 -> 2.0`

Primary anchors:
- `docs/MAKER_FIREABILITY_FORENSIC_dff97a9a.md`
- `docs/MAKER_FIREABILITY_HYPOTHESIS_DESIGN_dff97a9a.md`
- `docs/MAKER_CADENCE_EXPERIMENT_RESULT_2f607a7b.md`
- `logs_exec/paper_universal/forge_masters_archive_latest/*`
- `logs_exec/paper_universal/fusion_core_profile_latest/*`

Primary config surfaces:
- `configs/profiles/paper_universal.yaml`
  - `runtime.maker_replace_min_rest_sec = 3.0`
  - `strategy.execution_quality.max_queue_ahead_size = 300.0`
  - `strategy.execution_quality.min_expected_fill_prob = 0.045`
  - `maker_competitive.timing_gate_min_sec_to_expiry = 50.0`
  - `maker_competitive.timing_gate_max_sec_to_expiry = 60.0`
  - `sizing.target_usd = 350.0`
  - `sizing.maker_competitive_min_notional_usd = 350.0`

## Doctrine Status
`VERIFIED`:
- this packet was written under a drift-era runtime posture where maker timing
  was configured at `50-60s`.
- intended maker timing doctrine remains anchored on:
  - `docs/GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md`
  - doctrine window: `15-20s`
  - sweet spot: `10-15s`
- the `50-60s` frame below is retained as historical runtime posture for these
  specimens, not as current maker doctrine.

## Executive Truth
1. `VERIFIED`: the drift-era `50-60s` maker runtime window looked internally
   consistent across these valid specimens.
2. `VERIFIED`: `replace_guard_min_rest` is a real within-window suppressor, but
   it is **not** the first universal limiter once the tape broadens.
3. `VERIFIED`: low-price viability conflict is real at `0.025..0.035`, but it
   is regime-dependent rather than universal.
4. `VERIFIED`: queue-depth friction is now split cleanly into two stories:
   - hard queue-depth wall on bad low-price / bad-depth stock,
   - much lighter queue-depth friction on healthier viable stock.
5. `VERIFIED`: completed multi-fill maker fights remain the main economic wound,
   even when fireability improves.
6. `INFERRED`: the next best maker packet is not another cadence cut.
   It is a low-price viability / queue-depth truth-hardening packet with
   shadow-only surfaces first.

Plain-English:
timing still looks honest, cadence still matters, but the deeper live seam is
now whether maker is spending combat-window rows on impossible low-price stock
or on viable stock with tolerable queue friction.

## Pinned Evidence
### 1. Drift-era runtime timing held across the valid maker set
`VERIFIED`:
- `dff97a9a...`
  - active-window rows: `40`
  - active-window submits: `16`
  - active-window replace-guard: `16`
  - active-window quote-quality suppressions: `8`
- `2349650c...`
  - active-window rows: `78`
  - active-window submits: `19`
  - active-window replace-guard: `12`
  - active-window quote-quality suppressions: `23`
- `2c26e81e...`
  - active-window rows: `82`
  - active-window submits: `23`
  - active-window replace-guard: `12`
  - active-window quote-quality suppressions: `5`

`INFERRED`:
- the window is broad enough to see real maker behavior,
- the baseline `3.0s` cadence still bites,
- but participation now varies more with live friction mix than with window law.

### 2. The cadence experiment exposed the deeper seams cleanly
`VERIFIED` from control specimen `2f607a7b...`:
- active-window rows stayed `40`
- active-window submits fell to `2`
- active-window replace-guard fell to `0`
- active-window queue-depth suppressions rose to `8`
- sizing rejects surfaced at `20`
- sizing conflict price band stayed:
  - `min = 0.025`
  - `p50 = 0.025`
  - `max = 0.035`
- max-shares notional on rejected rows:
  - `p50 = 200.0`
  - `max = 280.0`

`VERIFIED`: removing the rest-guard seam did not open the cannon.
It exposed low-price viability conflict and hard queue-depth friction instead.

### 3. Low-price viability conflict is real, but not present in every valid run
`VERIFIED`:
- `dff97a9a...`
  - `maker_min_notional_max_shares_conflict_rows = 0`
- `2349650c...`
  - `maker_min_notional_max_shares_conflict_rows = 6`
  - `maker_sizing_reject_rows = 6`
  - reject price band:
    - `min = 0.025`
    - `p50 = 0.025`
    - `max = 0.035`
  - reject max-shares notional:
    - `p50 = 200.0`
    - `max = 280.0`
  - reject reasons:
    - `maker_hard_max_shares_cap = 6`
    - `maker_hard_min_notional_failed_after_rounding = 6`
    - `maker_hard_min_notional_floor = 6`
- `2c26e81e...`
  - `maker_min_notional_max_shares_conflict_rows = 0`

`INFERRED`:
- low-price viability conflict is not a constant background nuisance,
- it is a real specimen family that can dominate a run when the tape leans into
  `0.025..0.035` targets under the current `350.0` maker notional geometry.

Plain-English:
some tapes give us viable maker stock; some spend real combat-window rows on
targets that cannot satisfy the current notional/share geometry cleanly.

### 4. Queue-depth friction now has a healthier vs uglier split
`VERIFIED` from raw non-recovery queue-depth skip severity:
- `dff97a9a...`
  - active-window queue-depth no-submit assignments: `2`
  - raw queue bins:
    - `within_25 = 2`
    - `25_to_50 = 2`
    - `gt_50 = 4`
- `2349650c...`
  - active-window queue-depth no-submit assignments: `9`
  - raw queue bins:
    - `within_25 = 4`
    - `25_to_50 = 2`
    - `gt_50 = 5`
- `2c26e81e...`
  - active-window queue-depth no-submit assignments: `1`
  - raw queue bins:
    - `25_to_50 = 2`
    - `gt_50 = 2`
- `2f607a7b...`
  - active-window queue-depth no-submit assignments: `8`
  - raw queue bins:
    - `within_25 = 1`
    - `gt_50 = 14`

`VERIFIED` from per-target active-window summaries:
- `2349650c...` had one low-price family where queue-depth dominated:
  - `a4f1d49a1a838d7f`
    - submitted `2`
    - queue-depth skips `5`
    - replace-guard `3`
  - `b69a586ee47c2d61`
    - submitted `1`
    - replace-guard `3`
- `2c26e81e...` had healthier viable families:
  - `0f65c76ec91ffcf5`
    - submitted `7`
    - queue-depth skips `0`
    - replace-guard `2`
  - `11c3d5b9b43f5216`
    - submitted `6`
    - queue-depth skips `0`
    - replace-guard `2`
  - `9b5ba0a795a87803`
    - submitted `5`
    - queue-depth skips `1`
    - replace-guard `4`

`INFERRED`:
- queue depth is not one generic suppressor,
- it separates into:
  - hard bad-depth walls on ugly stock,
  - lighter, survivable friction on viable stock.

### 5. Better fireability did not solve the economic wound
`VERIFIED`:
- `dff97a9a...`
  - `maker_complete_record_count = 12`
  - `maker_complete_bad_ratio = 0.6666666666666666`
  - `maker_multifill_complete_incorrect_ratio = 1.0`
- `2349650c...`
  - `maker_complete_record_count = 13`
  - `maker_complete_bad_ratio = 1.0`
  - `maker_multifill_complete_incorrect_ratio = 1.0`
- `2c26e81e...`
  - `maker_complete_record_count = 18`
  - `maker_complete_bad_ratio = 0.6666666666666666`
  - `maker_multifill_complete_incorrect_ratio = 0.5555555555555556`

`VERIFIED`: `2c26e81e...` is materially healthier than `2349650c...`.
`VERIFIED`: even the healthier run still carries real multi-fill wound.

Plain-English:
more maker fire is only useful if the tape is viable enough that we are not
just feeding the bad fight geometry.

### 6. `FM-2A1` still corroborates the lane-level story
`VERIFIED` from refreshed corpus lathe output:
- candidate blanks still include:
  - `friction_burden` (`strong`)
  - `multifill_wound` (`strong`)
  - `repeat_target_cluster` (`bounded`)
  - `complement_pair_cluster` (`bounded`)
- current corpus maker `friction_burden` headline:
  - `quote_quality_skip_per_submit = 2.081530185295876`
- current corpus maker `multifill_wound` headline:
  - `multifill_incorrect_ratio = 0.7948717948717948`

`INFERRED`: the two new 20-minute runs did not break the old map.
They sharpened where the pre-fire friction family splits internally.

## Strong Things To Preserve
1. `VERIFIED`: under this packet's drift-era `50-60s` runtime posture, timing
   was not the cleanest next lever.
   Plain-English: this packet did not earn widening that old runtime window.
2. `VERIFIED`: keep `maker_replace_min_rest_sec = 3.0` as the canonical
   baseline until a deeper viability/queue packet is complete.
   Plain-English: the `2.0s` cadence cut taught us something, but it did not
   earn adoption.
3. `VERIFIED`: keep quote-quality work split between near-threshold and hard-bad
   stock.
   Plain-English: no blanket loosening.
4. `VERIFIED`: protect against worsening multi-fill wound while improving
   fireability.
   Plain-English: more shots is not the same thing as better shots.

## Main Hypotheses
### H1. The next strongest truth gap is viability classification, not cadence
`VERIFIED` support:
- cadence removal did not rescue participation in `2f607a7b...`
- low-price conflict was `0` in `dff97a9a...`, `6` in `2349650c...`, and `0`
  in `2c26e81e...`
- the low-price conflict band is already numerically pinned at `0.025..0.035`

`INFERRED` engineering read:
- we need first-class truth for "viable stock" vs "impossible stock inside the
  maker window" before another knob cut means anything.

### H2. Queue depth should be modeled as a viability sub-family, not as a generic skip
`VERIFIED` support:
- `2349650c...` and `2f607a7b...` show hard queue-depth pressure
- `2c26e81e...` shows much lighter queue-depth friction on healthier stock
- viable target families in `2c26e81e...` still fired repeatedly without
  queue-depth collapse

`INFERRED` engineering read:
- the next queue-depth packet should distinguish:
  - near-threshold viable depth misses,
  - hard bad-depth misses,
  - and queue-depth friction on already-impossible low-price stock.

### H3. The first next maker mutation should not be cadence, timing, or broad quality
`VERIFIED` support:
- cadence mutation already failed to earn adoption
- the drift-era runtime timing posture still looked internally honest in these
  specimens
- quote-quality friction is now clearly mixed by severity and specimen family

`INFERRED` engineering read:
- the next real-maker mutation, if earned later, should come from:
  - low-price viability geometry, or
  - tiered queue-depth tolerance on viable near-threshold stock,
  - but only after shadow-only truth promotion.

## Recommended Next Packet
### Phase A. Low-price viability / queue-depth truth hardening
Goal:
- make the next maker knob cut cheap, attributable, and honest

Recommended surfaces to promote:
1. `maker_window_sizing_reject_count`
2. `maker_window_sizing_reject_rate`
3. `maker_window_min_notional_max_shares_conflict_count`
4. `maker_window_low_price_conflict_price_band`
5. `maker_window_viable_target_count`
6. `maker_window_impossible_target_count`
7. `maker_window_queue_depth_near_threshold_count`
8. `maker_window_queue_depth_hard_miss_count`
9. `maker_window_queue_depth_target_summary`
10. `maker_window_viability_target_summary`

Claim-boundary rule:
- viable/impossible classification must be geometry-only
- do not invent causal language not earned by artifacts

### Phase B. Shadow-only low-price viability classifier
Goal:
- mark combat-window rows that are impossible under the current maker notional /
  max-shares geometry before we mutate runtime policy

Desired outputs:
- percentage of active-window rows that are impossible under current geometry
- per-target impossible-row burden
- price-band distribution of impossible rows
- correlation to no-submit causes and later completed outcomes

### Phase C. Shadow-only queue-depth split
Goal:
- separate queue-depth friction on viable targets from queue-depth friction on
  already-impossible stock

Desired outputs:
- near-threshold vs hard-miss queue-depth counts inside the active window
- per-target queue-depth burden on submitted vs non-submitted viable families
- queue-depth burden on low-price impossible families vs healthy viable families

### Phase D. Only then decide the next paper-only mutation
Allowed candidate families after Phases A-C:
1. low-price viability geometry experiment
2. tiered queue-depth tolerance on viable near-threshold stock

Not allowed next:
- another cadence cut first
- timing-window widening
- blanket quote-quality loosening
- multi-knob maker mutation

## Bottom Line
`VERIFIED`: the cannon is telling us more truth now.
`VERIFIED`: two healthy 20-minute runs were enough to prove that low-price
viability conflict and queue-depth friction are real but not identical seams.
`INFERRED`: the highest-ROI next move is a shadow-only instrumentation packet
that promotes those seams into first-class truth before the next maker mutation.
