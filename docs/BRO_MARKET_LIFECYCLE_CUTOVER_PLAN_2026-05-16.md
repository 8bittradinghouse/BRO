# BRO Market Lifecycle Cutover Plan

## Classification
`support-only`:
- this file is the implementation-planning companion for the lifecycle blueprint
- it is not a live runtime owner
- legacy stage-family terms appear here only as cut-list targets or bounded
  compatibility references

`VERIFIED`:
- this is the implementation-planning companion to [BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md).
- it does not create a second doctrine system.
- it exists to map the currently-wired timing/authority family into:
  - `KEEP AND REPURPOSE`
  - `COMPATIBILITY SEAM ONLY`
  - `CUT COMPLETELY`
- it is explicitly biased toward `no build` unless reuse is honestly insufficient.

Plain-English:
this is the cut list and reuse map for turning the current timing-family stack into the one clean market lifecycle system.

## Canonical Parent
Higher authority for the target system is:
- [BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md)

This file answers a different question:
- not "what should the final lifecycle mean?"
- but "how do we get there from the code we already have without building extra fat?"

## Current Five-Layer Family
### Layer 1. Raw expiry clock and raw bucket vocabulary
Current root surfaces:
- [executor.py](/home/odah/bro/base/executor.py:3217)
- [edge_truth_contract.py](/home/odah/bro/base/prodesk/edge_truth_contract.py:119)

Current behavior:
- runtime computes `sec_to_expiry`
- raw stage names are assigned from that clock:
  - `OBSERVE`
  - `EVALUATE`
  - `MAKER_POSITION`
  - `MAKER_TAKER_SELECTIVE`
  - `SNIPER_PRIMARY`
  - `EXTREME_ONLY`

Classification:
- `KEEP AND REPURPOSE` for the raw expiry clock
- `CUT COMPLETELY` for the raw bucket vocabulary

Why:
- the raw expiry clock itself is real, cheap, and already heavily wired
- the clock should remain a first-class input
- the bucket language is not earning its keep under the new lifecycle contract

Replacement shape:
- keep `sec_to_expiry`
- cut raw stage names as live runtime truth
- derive lifecycle truth directly from the clock plus ownership / resolve conditions:
  - `scan`
  - `prepare`
  - `maker_window`
  - `taker_window`
  - `resolve`

### Layer 2. Effective-stage overlay
Current root surfaces:
- [executor.py](/home/odah/bro/base/executor.py:3235)
- [edge_truth_contract.py](/home/odah/bro/base/prodesk/edge_truth_contract.py:130)

Current behavior:
- raw `EXTREME_ONLY` is reinterpreted into:
  - `LATE_DIAGNOSTIC`
  - `MAKER_LATE_WINDOW`
  - `TAKER_COMMITMENT`
- runtime carries:
  - `effective_stage`
  - `stage_bucket`
  - `lineage_stage`
  - `raw_stage`

Classification:
- `CUT COMPLETELY`

Why:
- this is the clearest second-language owner in the system
- it solves timing semantics by layering another timing vocabulary on top of the first one
- it is doctrine-breaking residue under the new lifecycle contract

Replacement shape:
- no second timing language
- one lifecycle phase only
- if historical replay needs these names temporarily, emit them only through a bounded compatibility adapter derived from lifecycle truth

### Layer 3. Canonical stage-authority map
Current root surfaces:
- [edge_truth_contract.py](/home/odah/bro/base/prodesk/edge_truth_contract.py:141)
- [executor.py](/home/odah/bro/base/executor.py:3182)

Current behavior:
- `CANONICAL_EDGE_STAGE_POLICY` grants maker/taker authority from stage names
- late-window authority then overrides that map with a second authority pass

Classification:
- `CUT COMPLETELY`

Why:
- this is duplicated authority
- it keeps stage names as runtime owner-law
- it is why the same reality has to pass through:
  - raw stage
  - effective stage
  - stage policy
  - late-window override

Replacement shape:
- replace stage-authority with lifecycle phase + lane gate evaluation
- lane permission should flow from:
  - current lifecycle phase
  - shared safety
  - lane-local readiness
- not from a stage lookup table

### Layer 4. Lane timing gates and timing thresholds
Current root surfaces:
- [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:67)
- [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:82)
- [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:123)
- [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:219)
- [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:546)
- [risk.py](/home/odah/bro/base/prodesk/risk.py:156)
- [taker_competitiveness.py](/home/odah/bro/base/prodesk/taker_competitiveness.py:507)

Current behavior:
- maker has:
  - timing gates
  - selection gates
  - stage allowlists
  - depth multiple gates
- taker has:
  - final-window logic
  - lane-local timing logic
- risk has:
  - global and lane-specific min-sec-to-expiry exposure rules

Classification:
- `KEEP AND REPURPOSE`

Why:
- this layer contains real high-ROI steel:
  - the `1.5x` depth gate
  - oracle confirmation gates
  - maker `15s` timing intent
  - taker `7s` timing intent
  - risk/exposure timing constraints
- these are useful controls, but they are currently spread across too many semantic owners

Replacement shape:
- keep the logic
- move it under one lifecycle config family
- delete stage-based gating from it

Expected survivors:
- admission gates
- maker-window open time
- taker-window open time
- lane-local oracle/depth/fillability gates
- risk timing floors

Expected removals from this layer:
- `allowed_stages`
- stage-dependent timing semantics
- duplicated timing minima/maxima that say the same thing in different places

### Layer 5. Actionability-owned market ownership
Current root surfaces:
- [executor.py](/home/odah/bro/base/executor.py:1060)
- [executor.py](/home/odah/bro/base/executor.py:1129)
- [executor.py](/home/odah/bro/base/executor.py:1146)
- [executor.py](/home/odah/bro/base/executor.py:6553)
- [runtime_semantics.py](/home/odah/bro/base/prodesk/runtime_semantics.py:612)

Current behavior:
- a pair is authoritative-active only if some token is currently actionable
- otherwise it can be demoted to `pending_prewarm`
- runtime can fall into `no_target_standdown`
- then the same pair can be promoted back later

Classification:
- `CUT COMPLETELY`

Why:
- this is the deadband bounce owner
- it fuses:
  - "is this still our market?"
  - with
  - "is a lane allowed to fire right now?"
- under the lifecycle blueprint, those are separate truths

Replacement shape:
- keep the current owner containers if they are useful:
  - authoritative owner
  - challenger / pending candidate
  - lifecycle watch set
- remove actionability as the rule for losing ownership
- ownership should persist through `prepare`
- lane closure should close lanes, not erase the market

## Keep / Repurpose / Cut Summary
### Keep and repurpose
- `sec_to_expiry` as the canonical lifecycle clock
- current runtime decision loop
- current single-owned-market data path as the base ownership rail
- maker selection gate depth/oracle/fillability steel
- taker final-window steel
- risk timing floors
- pending candidate and lifecycle-watch containers, but with new semantics

### Compatibility seam only
- old stage names for replay/report backfill only during migration
- `effective_stage`, `lineage_stage`, `stage_bucket`, `raw_stage` only if required for a bounded transition
- old report fields derived from lifecycle truth, never owning it

### Cut completely
- raw stage / bucket vocabulary as live owner-law
- effective-stage overlay as live runtime language
- `CANONICAL_EDGE_STAGE_POLICY`
- stage-based maker gate allowlists
- actionability-owned market loss / standdown semantics

## Minimal New System Shape
### Control plane A. Market ownership
Canonical truths:
- `owned_market_ref`
- `challenger_market_ref`
- `ownership_drop_reason`
- `ownership_replacement_reason`

### Control plane B. Lifecycle phase
Canonical truths:
- `lifecycle_phase`
  - `scan`
  - `prepare`
  - `maker_window`
  - `taker_window`
  - `resolve`

### Control plane C. Lane permissions
Canonical truths:
- `maker_phase_allowed`
- `taker_phase_allowed`
- `maker_gate_open`
- `taker_gate_open`

Important rule:
- maker/taker are independent lanes inside the same owned market
- they do not create separate timing systems

## Proposed Config Compression
The target is not more config. The target is cleaner config.

### New canonical family
`lifecycle.selection`
- `min_sec_to_expiry`
- `min_market_age_sec`
- `maker_min_depth_multiple`
- `taker_min_fill_ratio` or equivalent full-fill requirement
- `require_secondary_oracle_confirmation`
- `replacement_margin`
- `replacement_dwell_sec`
- `ownership_fail_dwell_sec`

`lifecycle.phase`
- `maker_window_open_sec`
- `taker_window_open_sec`

`lifecycle.lane_gates.maker`
- maker-local readiness gates only

`lifecycle.lane_gates.taker`
- taker-local readiness gates only

### Current config that should be retired or folded
- `strategy.maker_competitiveness.selection_gate.allowed_stages`
- stage-local one-sided allowlists
- duplicated stage-local timing vocabulary
- any config whose only reason to exist is old stage ownership

## Migration-Time Constant Ownership
Until the lifecycle config family becomes the sole owner, timing constants must
have one explicit active owner.

Current migration rule:
- lifecycle blueprint target constants are:
  - admit no earlier than `90s`
  - maker window opens at `15s`
  - taker window opens at `7s`
- current implementation owners remain:
  - maker timing-band numbers in the active profile config
  - taker final-window numbers in the active taker competitiveness config
  - exposure-floor numbers in the active risk config
- the `90s` admission minimum is currently blueprint target only and does not yet
  have a dedicated runtime config owner

Packet rule:
- Packet 3 must introduce one canonical lifecycle config owner for these
  numbers and retire duplicate timing owners immediately after migration proof

## No-Build Rules
- do not build a second scheduler
- do not build a second market selector
- do not build a second runtime state machine beside the current loop
- do not build report-only aliases as substitutes for cutting owner drift
- do not keep stage language alive just to avoid touching consumers

Preferred reuse:
- keep the current cycle loop
- keep current owner containers where semantics can be corrected
- keep current gate implementations where they can be rebound to lifecycle truth

## Implementation Packets
### Packet 1. Introduce lifecycle truth without changing behavior yet
Goal:
- create the new canonical lifecycle fields in runtime surfaces
- derive them from current truth while still emitting legacy stage fields

Expected work:
- add `lifecycle_phase`
- add ownership-state fields
- mark legacy stage fields as compatibility-only in docs and reports

### Packet 2. Cut actionability-owned ownership
Goal:
- make ownership persist through `prepare`
- stop same-pair active/standdown/reactivate churn

Expected work:
- replace `_pair_tokens_actionable()` as the authority-loss trigger
- keep current market owner while hard ownership floor remains valid
- repurpose `pending_prewarm` into challenger semantics or replace it with a cleaner candidate name
- rebind runtime posture/report semantics that currently equate ownership with
  `has_targets`, including `runtime_state`, `no_target_standdown`, and
  `book_feed_required`

### Packet 3. Rehome lane gates under lifecycle phase
Goal:
- make maker/taker permissions derive from lifecycle phase plus lane gates

Expected work:
- remove stage allowlists
- bind maker `15s` and taker `7s` to lifecycle phase
- bind maker selection depth/oracle logic to:
  - admission law
  - maker-window lane gates

### Packet 4. Remove stage-authority ownership
Goal:
- remove `CANONICAL_EDGE_STAGE_POLICY`
- remove effective-stage runtime ownership

Expected work:
- replace stage-authority reads with lifecycle permission reads
- convert report/audit consumers onto lifecycle fields

### Packet 5. Kill compatibility residue
Goal:
- remove stage-family live language from runtime, reports, and operator truth

Expected work:
- cut compatibility fields once consumers are migrated
- update report/docs/audits
- verify one semantic language remains

## Proof Order
1. ring tests on lifecycle field derivation
2. ring tests on ownership persistence through `prepare`
3. watched paper run proving same-pair deadband churn is gone
4. report/audit truth reconciliation
5. final semantic grep for old stage-owner residue

## Honest Call
`VERIFIED`:
- the easiest steel to keep is the raw expiry clock, the current loop, and the real gate logic
- the highest-ROI cuts are the effective-stage overlay, stage-authority map, and actionability-owned ownership

`INFERRED`:
- the cleanest implementation path is a root replacement that reuses current rails instead of inventing a new engine

`NO-GO`:
- do not start with maker/taker tuning
- do not start by renaming everything
- do not add new helper systems before the old authority stack is cut down
