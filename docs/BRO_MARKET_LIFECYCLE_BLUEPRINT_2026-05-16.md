# BRO Market Lifecycle Blueprint

## Classification
`VERIFIED`:
- this is the canonical top-level market lifecycle blueprint for BRO.
- it is the build-law target for the market lifecycle subsystem.
- it is not automatic proof that current runtime already matches it.
- it is meant to replace split timing-family language with one cleaner lifecycle model.
- it sits above current maker and taker lane doctrine proposals as the parent lifecycle contract.
- current runtime, reports, and support tools must be compared against this file; this file must not be silently rewritten downward to preserve drifted implementation residue.
- when runtime, reports, or operator truth drift from this file, that drift is a defect until either:
  - the implementation is brought into alignment, or
  - this blueprint is explicitly superseded by stronger canonical doctrine

Plain-English:
this is the canonical clean lifecycle system for how BRO should choose, own, act inside, and leave markets.

## Direct-Open Role
- canonical subsystem blueprint
- canonical design/build authority for market lifecycle behavior
- not runtime evidence by itself
- not a validator result
- not a report surface
- runtime proof is still required to claim implementation conformance
- implementation cutover companion:
  - [BRO_MARKET_LIFECYCLE_CUTOVER_PLAN_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_CUTOVER_PLAN_2026-05-16.md)

## Blueprint Objective
BRO should run one market lifecycle system, not several overlapping timing and authority systems.

The lifecycle should:
- select real windows worth owning
- keep ownership continuous once a market is selected
- separate market ownership from lane permission
- let maker and taker operate as independent execution lanes inside one shared lifecycle
- keep one shared safety spine for capital, lifecycle, truth quality, and closeout discipline

## Single-Language Lifecycle Law
One market gets:
- one ownership model
- one lifecycle vocabulary
- one execution-state model
- one report vocabulary

Maker and taker are not separate timing sovereigns.
They are subordinate execution lanes inside the market lifecycle.

Old split-owner timing language should not survive as live authority.
If migration requires compatibility labels temporarily, those labels must stay bounded to explicit compatibility seams only.

## Lifecycle Control Planes
### 1. Market Selection / Ownership
This plane answers:
- is this market worth admitting?
- once admitted, do we keep it?
- when do we drop it?

### 2. In-Market Execution Phase
This plane answers:
- what phase is the owned market in right now?
- which lane permissions are legal in this phase?

### 3. Shared Safety Spine
This spine is shared by maker and taker and may block either lane when real safety or truth problems exist.

Examples:
- oracle freshness / confirmation truth
- wallet / capital truth
- held exposure and open-order lifecycle truth
- market-truth degradation
- fail-closed closeout / cleanup obligations

## Single-Market Arbitration Law
BRO owns one market at a time by default.

Arbitration rules:
- only admitted markets may compete for ownership
- the highest-ranked admitted market wins
- the currently owned market keeps ownership by default unless a challenger wins by explicit replacement law
- replacement must be hysteresis-protected; no churn on near-ties or one-cycle noise
- once a market is in `maker_window`, `taker_window`, or `resolve`, replacement is forbidden unless shared safety requires abandonment

Ranking inputs must be explicit and machine-owned:
- maker depth quality
- taker fillability quality
- oracle / truth quality
- lifecycle cleanliness
- expected side / edge quality
- replacement margin over the current owner

## Canonical Lifecycle State Machine
### Operating States
#### `scan`
BRO is not yet owning a market.

Purpose:
- observe candidate markets
- collect book truth
- collect oracle truth
- collect launch-age / maturity truth
- collect maker-depth and taker-fillability truth
- rank candidates for admission

#### `prepare`
BRO owns one market continuously, but no lane is currently taking action.

Purpose:
- hold market ownership
- keep feeds hot
- keep oracle truth hot
- keep maker/taker readiness current
- track which side has the better edge
- keep ranking challengers for explicit replacement law without yielding current ownership
- wait for the next legal lane window

Important rule:
- `prepare` is not standdown
- ownership remains live during `prepare`

#### `maker_window`
The owned market is inside the maker action window.

Purpose:
- maker lane may quote if its gates are open
- maker lane may continue managing its resting order or held commitment

#### `taker_window`
The owned market is inside the taker action window.

Purpose:
- taker lane may fire if its gates are open
- maker semantics do not erase taker authority by language drift

#### `resolve`
The owned market has entered closeout / settlement / hold-to-resolution behavior.

Purpose:
- complete real market lifecycle obligations
- settle accepted exposure
- cleanly finish closeout before ownership is released

### Transition Verbs
#### `admit`
Transition:
- `scan -> prepare`

A market may be admitted only if it passes the admission law.

Admission law:
- `sec_to_expiry >= 90.0`
- `market_age_sec >= 60.0`
  - preferred source: venue launch/open truth
  - fallback source when venue launch truth is unavailable: first-observed age
- visible maker depth notional at the intended maker side/price supports at least `1.5x` the canonical maker order notional
- visible taker aggressive fillability supports the full canonical taker order requirement at the intended entry price
  - partial fillability counts only if higher canonical doctrine explicitly ratifies it
- primary oracle is fresh and healthy
- secondary oracle is available enough to support lane confirmation law
- shared safety spine is green
- the market wins arbitration against all other admitted candidates

#### `advance`
Transition:
- owned lifecycle may advance among:
  - `prepare`
  - `maker_window`
  - `taker_window`
  - `resolve`

Chronology:
- `prepare -> maker_window` when the maker window opens
- `prepare -> taker_window` is legal when the taker window opens, even if maker never placed
- `maker_window -> taker_window` is legal when the taker window opens, whether or not maker actually placed
- `prepare`, `maker_window`, or `taker_window` may move directly to `resolve` when accepted exposure, expiry, or explicit closeout law requires it

Important rule:
- lifecycle phases are legal operating windows, not mandatory submit steps
- challenger scoring and replacement evaluation continue while the market is owned, but they do not erase ownership unless explicit replacement law fires

#### `drop`
Transition:
- owned state -> `scan`

Drop law:
- market resolved or expired and no remaining lifecycle obligation exists
- shared safety spine is red and abandonment is the explicit legal response
- truth quality fails the hard ownership floor for longer than the explicit ownership-fail dwell threshold
- a challenger wins replacement law before the market enters protected live execution / resolve states

Mandatory runtime constants:
- `ownership_fail_dwell_sec` and/or `ownership_fail_dwell_cycles`
- `replacement_margin`
- `replacement_dwell_sec` and/or `replacement_dwell_cycles`

#### `recycle`
Transition:
- `resolve -> scan`

Re-entry rule:
- BRO may recycle only after lifecycle, cleanup, and settlement obligations are clear

## Ownership, Stay, And Drop Law
### Ownership Law
Once admitted, BRO owns the market continuously.

Important rule:
- ownership does not disappear just because no lane is currently allowed to fire

This is the key separation:
- market ownership is one truth
- lane permission is a different truth

### Stay Law
Once owned, a market stays owned while all of these remain true:
- the market is unresolved
- the hard ownership floor remains intact
- no superior challenger wins replacement law
- no explicit abandonment condition is active

Hard ownership floor:
- market identity is still valid
- oracle truth is not red
- market-truth substrate is not red
- shared safety spine is not red

Soft bruises that do **not** force drop by themselves:
- temporary lane-local gate closure
- temporary no-edge / side flip
- temporary depth dip
- temporary fillability dip
- temporary maker or taker disallow

Soft bruises close lane permission.
They do not erase ownership.

### Revalidation Law
- the hard ownership floor is rechecked every runtime decision cycle
- selection-quality inputs are rechecked continuously while owned
- maker and taker lane gates are re-evaluated independently every cycle
- a one-cycle bruise may close lane permission, but it may not erase ownership unless drop law is satisfied

### Shared-Safety Precedence
Shared safety may:
- block maker
- block taker
- force `resolve`
- force `drop` only when explicit drop law says abandonment is legal

Shared safety may **not**:
- silently erase ownership just because a lane is blocked
- impersonate lane-timing semantics

## Lane Windows And Lane Permissions
### `maker_window`
At the maker window, the maker lane may place the maker order if its gates are open.

Intended maker-lane proposal shape:
- maker window opens at `15s`
- maker chooses the side with the better edge
- maker requires:
  - healthy shared safety spine
  - oracle confirmation truth
  - sufficient visible depth
  - valid side selection

### `taker_window`
At the taker window, the taker lane may fire independently if its gates are open.

Intended taker-lane proposal shape:
- taker window opens at `7s`
- taker requires:
  - healthy oracle truth
  - valid fillability
  - valid side selection
  - healthy shared safety spine

## Lifecycle Compression
Short form:

`scan -> admit -> prepare -> maker@15 if valid -> taker@7 if valid -> resolve -> recycle`

## Lifecycle Gates
### Selection Gates
These decide whether a market is admitted at all.

Examples:
- maturity / launch-age gate
- maker depth multiple gate
- taker fillability gate
- oracle-health gate
- shared-safety gate

### Phase Gates
These decide what operating state the owned market is in.

Allowed operating-state vocabulary:
- `scan`
- `prepare`
- `maker_window`
- `taker_window`
- `resolve`

Allowed transition-verb vocabulary:
- `admit`
- `advance`
- `drop`
- `recycle`

### Lane Gates
These decide whether maker or taker may act inside the current phase.

Examples:
- oracle confirmation
- side validity
- visible depth / fillability
- lane-local readiness
- shared-safety interlocks

## Maker / Taker Relationship Law
Maker and taker must be:
- independent execution lanes
- inside one shared market lifecycle
- under one shared safety spine

They may not:
- own separate timing languages
- steal market ownership from each other
- create duplicate semantic authorities

They still must share:
- wallet / capital truth
- inventory / held exposure truth
- open-order lifecycle truth
- cleanup / settlement / fail-closed truth

### Exact Coexistence Law
- a live maker order does not by itself block taker eligibility
- a live taker lane does not by itself erase maker ownership or maker commitment
- maker and taker permissions are evaluated independently from the same owned market
- pre-existing maker orders may remain live through `taker_window` unless shared safety or lifecycle cleanup law requires intervention
- if taker submission would create forbidden exposure, duplicate conflict, or lifecycle contradiction, the shared safety / lifecycle interlock decides the response
- conflicting maker open orders must be handled by explicit cleanup law, not by semantic stage suppression
- same-side coexistence is allowed only if capital, inventory, and lifecycle law permit it
- opposite-side conflict must be resolved through explicit cleanup / exposure law once a real commitment exists

## What This Blueprint Explicitly Rejects
- multiple parallel timing sovereigns
- raw lineage timing buckets as live authority
- effective-stage overlays as a second language
- pair ownership that disappears merely because no lane is currently actionable
- live runtime and report surfaces speaking different lifecycle vocabularies
- ownership churn caused by temporary no-action periods
- semantic suppression of one lane by the other

## Relationship To Current Maker And Taker Lane Proposals
- the current maker doctrine proposal should define intended maker-lane behavior inside this lifecycle
- the current taker doctrine proposal should define intended taker-lane behavior inside this lifecycle
- neither proposal may reintroduce a second timing system
- both proposals must inherit market ownership and lifecycle vocabulary from this file
- future ratified maker/taker blueprints, if created, must also inherit this lifecycle contract unless stronger canonical doctrine explicitly replaces it

## Engineering Use
Use this blueprint to:
- redesign the current timing-family stack into one lifecycle owner
- remove duplicate timing and stage owners
- replace deadband standdown semantics with continuous market ownership plus lane-permission changes
- align runtime, reporting, and operator truth onto one lifecycle vocabulary
- define one exact stay law, drop law, and replacement law for the owned market

Do not use this blueprint to:
- claim current runtime already matches the target
- justify threshold loosening by narrative
- preserve old timing vocabulary as equal authority
