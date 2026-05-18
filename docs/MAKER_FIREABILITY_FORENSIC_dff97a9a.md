# Maker Fireability Forensic: `dff97a9a-9953-47e6-939f-f5f1f8814ed4`

## Purpose
Pin the first post-lathe, maker-valid specimen tightly enough that the next
maker packet can start from a real fireability map instead of repeating broad
runtime archaeology.

This packet is analysis-first and does **not** change runtime behavior.

## Doctrine Status
`VERIFIED`:
- this packet was written under a drift-era runtime posture where the active
  maker timing gate lived at `50-60s`.
- that posture is preserved here as specimen context only.
- intended maker timing doctrine remains anchored on:
  - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
  - doctrine window: `15-20s`
  - sweet spot: `10-15s`

Plain-English:
references below to `50-60s` describe drift-era runtime history, not current
maker doctrine.

## Specimen Identity
- Run id:
  - `dff97a9a-9953-47e6-939f-f5f1f8814ed4`
- Canonical validation:
  - `status=pass`
  - `runtime_classification=VALID_ACTIVE`
  - `highest_passing_stage=paper`
  - `promotion_eligible=true`
- Core artifacts:
  - `logs_exec/paper_universal/reports/dff97a9a-9953-47e6-939f-f5f1f8814ed4`
  - `logs_exec/paper_universal/forge_masters_archive_run_dff97a9a-9953-47e6-939f-f5f1f8814ed4`
  - `logs_exec/paper_universal/fusion_core_profile_run_dff97a9a-9953-47e6-939f-f5f1f8814ed4`

## Executive Truth
1. `VERIFIED`: this is real maker stock, not an empty or only-structural tape.
2. `VERIFIED`: the main suppressor family is `maker_timing_gate_closed`.
3. `VERIFIED`: inside the live timing window, the next biggest friction is
   `replace_guard_min_rest`, with smaller but real quote-quality rejects.
4. `VERIFIED`: maker can fire in this tape, but completed fights still skew
   worse than they should, especially on multi-fill geometry.
5. `INFERRED`: the next highest-ROI move is not another specimen first. It is
   a bounded maker fireability / friction hypothesis packet using this stock.

## Runtime / Participation Truth
### Canonical verdict
`VERIFIED`:
- `canonical_paper_validation.json`
  - `status=pass`
  - `runtime_classification=VALID_ACTIVE`
- `nightly_soak_report.json`
  - `runtime_classification.primary_suppression_cause=none`
  - `active_targets_seen=1`
  - `meaningful_participation=1`
  - `order_submission_attempt_rows=2`
  - `participation_rows=2`

Plain-English:
the run was healthy and the system really participated. This was not a cold
no-op tape.

### Raw maker participation
`VERIFIED`:
- raw lifecycle events:
  - `order_submit=24`
  - `fill=24`
  - `order_cancel=18`
  - `order_submission_rejected_local=18`
- maker edge-evaluation rows:
  - `364`
- maker submitted edge-evaluation rows:
  - `16`

Important semantic note:
`VERIFIED`: raw maker submit/fill truth in this runtime lives in generic
lifecycle events:
- `order_submit`
- `fill`
- `order_cancel`

not in a maker-prefixed `maker_order_submitted` event name.

Plain-English:
the machine did fire maker rounds here. The raw event vocabulary is just more
generic than the name might suggest.

## Timing Gate Anatomy
### Configured gate
`VERIFIED`:
- `configs/profiles/paper_universal.yaml`
  - `timing_gate_enabled: true`
  - `timing_gate_min_sec_to_expiry: 50.0`
  - `timing_gate_max_sec_to_expiry: 60.0`

### Observed behavior
`VERIFIED`:
- `maker_timing_gate_closed=324`
- blocked rows by stage:
  - `MAKER_POSITION=120`
  - `MAKER_TAKER_SELECTIVE=84`
  - `EXTREME_ONLY=82`
  - `SNIPER_PRIMARY=38`

Submitted maker edge-evaluation rows all lived inside the open window:
- min `50.119878`
- max `59.913614`

Timing-gate-blocked maker rows spanned:
- min `-0.929513`
- max `89.915525`

`INFERRED`: on current evidence, the timing gate is behaving like a real
enforced runtime window in this specimen, not like a hidden malfunction. It is
the biggest fireability shaper, but the current specimen does not prove that
this drift-era runtime posture was the right maker doctrine.

Plain-English:
the cannon is only supposed to shoot in a narrow late window, and this run
mostly matches that contract.

## Within-Window Friction
### Maker no-submission surface
`VERIFIED`:
- `maker_no_submission=24`
- no-submit rows lived inside:
  - min `50.899615`
  - max `58.534108`

Harvested cause mix:
- `replace_guard_min_rest=16`
- `submit_rejected_quote_quality_skip_fill_probability=6`
- `submit_rejected_quote_quality_skip_queue_depth=2`

### Replace-guard truth
`VERIFIED`:
- `maker_replace_min_rest_sec=3.0`
- actual maker submit bursts occurred around:
  - `59.91s`
  - `56.90s`
  - `52.89s`
  - and again later around:
  - `59.13s`
  - `56.12s`
  - `53.11s`

`INFERRED`: that cadence is consistent with the `3.0s` min-rest guard doing
real work inside the drift-era `50-60s` runtime window sampled by this packet.

Plain-English:
once maker gets inside the allowed time box, the replace guard is one of the
main reasons it still does not keep firing every cycle.

### Quote-quality truth
`VERIFIED`:
- execution-quality thresholds:
  - `max_queue_ahead_size=300.0`
  - `min_expected_fill_prob=0.045`
- raw `quote_quality_skip` events:
  - `18` total
  - examples:
    - queue-depth skips at `306.59`, `327.36`, `354.06`
    - fill-probability skips at `0.0142`, `0.0205`, `0.0391`, `0.0424`

`INFERRED`: these are not fuzzy “felt bad” suppressions. They are concrete
numeric rejects against explicit configured thresholds.

### Population warning
`VERIFIED`: raw local reject sparks and harvested no-submit causes are
different populations.
- raw local reject events: `18`
- harvested maker no-submit causes: `24`

Reason:
- reject events are per-attempt event sparks,
- no-submit causes are token-side no-fire assignments emitted through edge
  evaluation / soak compression.

Plain-English:
do not compare `18` and `24` like they are the same thing.

## Outcome Truth
`VERIFIED`:
- `maker_submits=24`
- `maker_fills=24`
- `maker_fill_rate=0.5`
- `maker_complete_record_count=12`
- `maker_incomplete_record_count=12`
- decision-quality counts across all outcome records:
  - `correct=14`
  - `incorrect=10`

Pinned lathe/specimen truth:
- `complete_bad_ratio=0.6666666666666666`
- `multifill_complete_count=6`
- `multifill_incorrect_ratio=1.0`
- `singlefill_correct_ratio=0.6666666666666666`
- `execution_rescue_overcome_rate=0.3333333333333333`

`VERIFIED`: this specimen proves maker can fire and participate. It does **not**
prove the completed-fight economics are good enough yet.

Plain-English:
the cannon is live, but some of the shells that fully connect are still the
wrong fights.

## Lathe Read
`VERIFIED`:
- specimen `FM-2A1` run entered `full_depth` for maker
- candidate blanks promoted at `bounded`:
  - `friction_burden`
  - `multifill_wound`
  - `outcome_balance`
  - `singlefill_strength`
  - `valuation_pressure`

`INFERRED`: this is exactly the kind of specimen that justifies stopping runtime
collection and opening a tighter maker hypothesis packet.

## What This Specimen Does Not Prove
1. `UNKNOWN`: whether the drift-era `50-60s` runtime window was ever globally
   optimal.
2. `UNKNOWN`: whether `3.0s` replace-guard min-rest is too conservative for
   current maker doctrine.
3. `UNKNOWN`: whether the quote-quality thresholds should be loosened,
   tightened, or split by subfamily.
4. `VERIFIED`: this specimen alone does not justify live-behavior tuning.

## Recommended Next Engineering Cuts
1. `VERIFIED_RECOMMENDATION`: open a bounded maker fireability / friction packet
   on this specimen before running more tape.
   - inspect the drift-era `50-60s` runtime window as the specimen's active
     runtime band, not as current maker doctrine
   - separate:
     - runtime timing closures,
     - replace-guard friction,
     - quote-quality suppressions
2. `VERIFIED_RECOMMENDATION`: keep the first design cuts analysis-first.
   - no threshold loosening first
   - no timing-window widening first
3. `INFERRED_RECOMMENDATION`: if tuning opens after the forensic pass,
   preserve:
   - `singlefill_strength`
   - bounded valuation calm
   and avoid blind churn that worsens:
   - `multifill_wound`
   - completed-fight debt
4. `VERIFIED_RECOMMENDATION`: do not run another 10-20 minute specimen yet.
   - this run already gave us real maker fireability stock
   - use it before collecting more tape

## Bottom Line
`VERIFIED`: `dff97a9a...` is the first strong post-lathe maker specimen for the
current lane.

`VERIFIED`: the biggest maker no-fire story here is:
- doctrinal timing gate first,
- replace-guard min-rest second,
- quote-quality friction third.

`INFERRED`: the next elite move is to shape a maker fireability hypothesis from
this exact anatomy, not to rush into another runtime or a blind threshold
change.
