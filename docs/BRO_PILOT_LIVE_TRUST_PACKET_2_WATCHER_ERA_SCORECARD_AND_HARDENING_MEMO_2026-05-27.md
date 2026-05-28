# BRO Pilot Live Trust Packet 2 Watcher Era Scorecard And Hardening Memo (2026-05-27)

Bridge boundary note:
- `current owner` for watcher-era synthesis and the small watcher/session hardening packet
- not the maker/taker doctrine root
- not the broad repo truth screen

## Purpose

This memo closes the first real watcher era where `BRO` paper runtime was
audited against the same live Polymarket BTC `5m` markets across multiple
final-standard specimens.

It also records the small hardening packet that followed directly from those
specimens:

- canonical session closeout cleanup
- per-slug target discovery fallback/noise reduction

## Final-Standard Scope

The current owner scorecard uses only the two hardened `20-minute` watcher
runs:

1. `f7fc082f-4d7b-4a18-9fa1-2692ecaee15d`
   - bundle: `tmp/paper_live_market_audit/20260527T070304Z`
   - `conditions_seen = 4`
   - matching counts:
     - `events = 4386`
     - `status = 40`
     - `errors = 1`
   - financial result:
     - overall `+584.4994`
     - maker `+99.4994`
     - taker `+485.0000`

2. `2cdcb966-0311-4547-adb5-20e142d2dfe9`
   - bundle: `tmp/paper_live_market_audit/20260527T073010Z`
   - `conditions_seen = 4`
   - matching counts:
     - `events = 4001`
     - `status = 33`
     - `errors = 2`
   - financial result:
     - overall `-11.0153`
     - maker `-1.0153`
     - taker `-10.0000`

Combined hardened watcher-era baseline:

- `8` final-standard market specimens
- combined overall financial result: `+573.4841`

## Eight-Specimen Scorecard

### Run `f7fc082f-4d7b-4a18-9fa1-2692ecaee15d`

1. `btc-updown-5m-1779865200`
   - maker submitted on the rich side
   - taker later bought the cheap side
   - paired-lane active specimen

2. `btc-updown-5m-1779865500`
   - maker abstained
   - taker abstained on `edge_below_min`
   - honest no-shot specimen

3. `btc-updown-5m-1779865800`
   - maker abstained
   - taker-only submit fired on the cheap side
   - clean taker-only exploitation specimen

4. `btc-updown-5m-1779866100`
   - maker submitted on the rich side
   - taker later bought the cheap side
   - paired-lane active specimen

### Run `2cdcb966-0311-4547-adb5-20e142d2dfe9`

1. `btc-updown-5m-1779867000`
   - maker submitted
   - no fill
   - clean commitment-end cancel
   - taker abstained

2. `btc-updown-5m-1779867300`
   - maker submitted
   - no fill
   - clean commitment-end cancel
   - taker abstained

3. `btc-updown-5m-1779867600`
   - maker sold the rich side and later filled
   - taker later bought the cheap side and filled
   - strongest watcher-era paired-lane specimen

4. `btc-updown-5m-1779867900`
   - no maker submit
   - taker-only cheap-side submit fired and filled
   - clean taker-only specimen

## What The Eight-Specimen Baseline Says

### 1. The watcher is now real owner evidence

`VERIFIED`: the watcher can now align:

- live public books
- `BRO` runtime events
- maker actions
- taker actions
- and closeout truth

on the same market and same window.

### 2. Overnight / Asia-regime structure is harsh but not uniform

`VERIFIED`: some specimens were already pinned by maker open.

`VERIFIED`: some specimens still showed a healthier interior ladder earlier in
the lifecycle and only collapsed later.

`INFERRED`: the overnight working band is not one flat `dead-hours` bucket. It
contains:

- fully pinned hopeless structure
- temporarily quoteable structure that degrades fast
- and occasional complementary extreme-edge opportunities

### 3. Both lanes are showing sane behavior

Current hardened spread:

- paired-lane active specimens: `3`
- taker-only active specimens: `2`
- maker-only clean-post/no-fill specimens: `2`
- full abstention specimen: `1`

`VERIFIED`: `BRO` is not forcing activity just to stay busy.

`VERIFIED`: maker can abstain honestly, post without forcing fills, and cancel
cleanly when the commitment window ends.

`VERIFIED`: taker can still exploit cheap-side extremes in fully pinned or
late-collapsing structure.

`INFERRED`: the machine is showing more intelligent lane separation than the
old dark-room view suggested.

### 4. The strongest current live-aligned read

`VERIFIED`: the current surviving maker blockers are more likely to be honest
selection / feasibility / actionability truth than fake slag.

`VERIFIED`: the watcher-era baseline does not support the story that maker is
blindly forcing garbage in hostile overnight structure.

`INFERRED`: some complementary rich-side maker plus cheap-side taker sequences
may be genuinely intelligent edge expression rather than doctrine drift.

`UNKNOWN`: whether every smart-looking complementary extreme sequence is
durable steel or just promising early behavior.

## Hardening Packet Landed

### 1. Canonical session closeout cleanup

Code:

- `scripts/canonical_paper_session.py`

Change:

- session state now writes `stop_ts`
- `phase=complete` now clears `runner_pid`

Why:

- the stale-open launch blocker class had already been cut enough to allow the
  hardened runs
- but closed session state still retained dirty `runner_pid` / missing
  `stop_ts` semantics

Proof:

- `tests/test_canonical_paper_session.py`

### 2. Per-slug discovery fallback / noise reduction

Code:

- `prodesk/market_discovery.py`

Change:

- when slug probe against Gamma `/markets?slug=...` errors or returns no usable
  list, discovery now falls back per slug to Gamma `/events?slug=...`
- the per-slug fallback keeps discovery moving without treating that single
  seam as a whole-discovery failure

Proof:

- `tests/test_market_discovery.py`

### Focused proof ring

- `53 passed`
  - `tests/test_market_discovery.py`
  - `tests/test_canonical_paper_session.py`

Boundary:

- `VERIFIED`: the hardening packet is landed and unit-proved
- `UNKNOWN`: we have not yet re-proved the hardening packet through a fresh
  watched runtime after these exact code edits

## Timing Experiment Boundary

`VERIFIED`: the eight-specimen watcher-era scorecard above is the pre-shift
baseline.

Current active paper timing experiment now moves to:

- maker gate at `10s`
- taker handoff at `5s`
- effective maker risk-increasing submit band `(5.0, 10.0]`

This memo does **not** claim post-change runtime proof yet.

## Current Best Read

`VERIFIED`: the watcher era materially reduced blindness.

`VERIFIED`: the overnight / Asia-regime proving band is harsh enough to act as
real training ground rather than cosmetic paper comfort.

`VERIFIED`: both lanes are now showing a credible spread of:

- honest abstention
- clean maker posting without forced fills
- clean maker cancellation
- taker-only cheap-side exploitation
- and paired-lane complementary behavior

`INFERRED`: this is strong evidence that the machine is now closer to real
product-fit / launch-engineering hardening than to old fake-authority cleanup.

`UNKNOWN`: whether the new `10s / 5s` timing experiment improves, harms, or
simply reshapes that behavior spread.

## Immediate Next Use

1. run the next watcher specimens on the new `10s / 5s` timing experiment
2. compare them directly against this eight-specimen pre-shift baseline
3. only then decide whether timing, selection/feasibility, or regime-aware
   posture is the next real mover
