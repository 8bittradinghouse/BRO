# Galaxy Mega Maker Cannon Doctrine Proposal

## Classification
`VERIFIED`:
- this is an external strategy/doctrine input provided by `Robb`, shaped with
  `Grok`, for the future maker lane.
- it is not yet runtime-proven BRO law.
- it should be treated as a high-value design target and selectivity hypothesis
  source, not as instant production authority.
- for maker doctrine cleanup, this file is the intended maker-lane doctrine
  proposal
  inside the parent market lifecycle blueprint:
  - `docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md`
- current runtime posture must be compared to this file; this file must not be
  rewritten downward to match drifted implementation residue.

Plain-English:
this is a serious doctrine proposal from the house, not a proven machine result
yet.

## Operator Front Door
`VERIFIED`:
- canonical paper proving runs should be launched from the public operator front
  door:
  - `broctl paper -- --active-minutes <minutes> --wait-sec 25`
- backend session wrappers remain implementation machinery, not the doctrine
  surface operators should memorize first.

Plain-English:
if we are proving this proposal on live paper, the front door is `broctl
paper`.

## Stated Strategic Intent
- maker should be the cannon, not the machine gun
- trade less often
- push for dramatically higher fight quality
- aim for `95%+` win rate on filled maker orders
- accept lower activity in exchange for cleaner fills, better inventory
  posture, and stronger edge concentration

## Parent Lifecycle Boundary
`VERIFIED`:
- maker is not its own separate timing sovereign
- maker is a subordinate execution lane inside:
  - `docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md`
- market ownership, market continuity, and parent lifecycle vocabulary belong
  to the market lifecycle blueprint
- this file owns intended maker-lane proposal behavior only

## Operator-Locked Proposal Inputs
### Core posture
- pure maker only for this lane
- one maker side only; this is not a bilateral spread-harvest lane
- `postOnly: true` always
- fail closed on degraded oracle / clock / risk state

### Timing
- maker gate opens at `15s` to expiry
- no maker order before `15s` to expiry
- maker keeps authority until taker's `7s` handoff unless a commitment already owns the token
- once a maker order is placed inside that final window, it should ride the
  rest of the commitment window instead of flipping or routine-canceling

### Confirmation
- primary oracle: `Chainlink Data Streams`
- secondary oracle: `Pyth`
- require directional agreement
- require minimum oracle delta of about `0.20`
- skip if delta is below threshold or confirmation is weak
- market-side semantics must stay source-closed:
  - market identity is `YES` / `NO`
  - execution verbs can still be `BUY` / `SELL`
  - the runtime must not confuse those layers

### Sizing / capital
- fixed maker order size: `$100` per side
- hard stacked-open-order cap: `4-6`
- intended bankroll context: roughly `$4k-$5k`

### Book / liquidity
- require at least `1.5x` order-size resting liquidity at or near the quote
  price
- with `$100` order size, the stated safety threshold is `$150` equivalent
  depth

### Observability
- every skipped maker opportunity should be logged with exact reason
- reasons include timing, delta, depth, stack cap, and risk gates
- anti-churn doctrine should be visible too:
  - one maker order per token/window
  - one taker order per market/window

## Direct Alignment With Current Maker Truth
`VERIFIED`:
- the new maker fight-admission shadow packet already proved that
  `size_liquidity_pressure` is the strongest current driver family in the
  baseline set:
  - `size_liquidity_pressure=52`
- the shadow packet also proved `repeat_target_side_pressure` is a major
  current wound family:
  - `repeat_target_side_pressure=40`
- this proposal's emphasis on deep enough books, strict timing, and low stacked
  concurrency is directionally aligned with that truth.

`VERIFIED`:
- current maker tooling already exposes the raw ingredients needed to test much
  of this proposal:
  - `visible_depth_shares`
  - `size_to_visible_depth_ratio`
  - `queue_ahead_size`
  - `expected_fill_prob`
  - `financial_posture_class`
  - `reduce_only_recovery_active`
  - `same_target_side_shadow_count_prior`

Plain-English:
the proposal is not coming out of nowhere. It hits the same pain families the
shop just found.

## Open Tensions / Non-Automatic Imports
### Fixed `$100` side size vs low-price geometry
`VERIFIED`:
- current baseline truth still carries a maker low-price geometry floor of
  `0.0125` under the present floor/cap policy.
- that means a rigid `$100` maker doctrine is not universally compatible with
  every cheap market in the current machine.

Implication:
- the proposal's fixed size can be preserved as the desired cannon shot, but
  the lane still needs explicit doctrine for what to do when the market is too
  cheap for current geometry.

### `95%+` win-rate target
`VERIFIED`:
- current baseline evidence is nowhere near that level yet.
- current admission shadow recut did not separate submitted `clean` fights from
  bad submitted fights strongly enough to earn runtime gating.

Implication:
- `95%+` is a design target, not a claim supported by current artifacts.

### Dual-oracle delta semantics
`UNKNOWN`:
- the proposal's `0.20` delta needs precise machine semantics:
  - underlying price units
  - symbol normalization
  - freshness / ordering policy
  - exact interaction with the final-seconds timing window

Implication:
- this is promising, but it still needs a formal BRO-side definition before it
  becomes doctrine code.

### Final `15s` maker open / `7s` taker handoff
`INFERRED`:
- this is a plausible high-selectivity doctrine shift, but it has not yet been
  proven against BRO's current maker evidence.

Implication:
- it belongs in the next shadow/rubric packet first, not as an immediate
  runtime narrowing.

## Recommended Engineering Use
`VERIFIED`:
- the best immediate use is to treat this proposal as input for the first
  stricter `skip-trash-windows` maker-selection packet.

Recommended first translation into BRO-selectivity tooling:
- add a `final_window_class` or equivalent late-window feature
- add a dual-oracle agreement / disagreement flag
- add a normalized oracle-delta feature
- add a depth-multiple-of-order-size feature based on the `1.5x` doctrine
- add a stacked-open-order pressure feature
- preserve all of those as report-only / shadow surfaces first

Recommended non-action:
- do **not** convert this whole proposal directly into runtime maker law in
  one swing
- do **not** treat it as disproving the currently observed geometry floor
- do **not** assume the `95%+` target is already close

Plain-English:
use this proposal as the aiming doctrine for the next selectivity packet, then
make BRO earn it through shadow truth before letting it touch the trigger.
