# Galaxy Mega Maker Cannon Blueprint

## Classification
`VERIFIED`:
- this is an external strategy/doctrine input provided by `Robb`, shaped with
  `Grok`, for the future maker lane.
- it is not yet runtime-proven BRO law.
- it should be treated as a high-value design target and selectivity hypothesis
  source, not as instant production authority.
- for maker doctrine cleanup, this file is the intended maker-weapon timing
  anchor against which drifted runtime/support surfaces should be judged.
- current runtime posture must be compared to this file; this file must not be
  rewritten downward to match drifted implementation residue.

Plain-English:
this is a serious blueprint from the house, not a proven machine result yet.

## Stated Strategic Intent
- maker should be the cannon, not the machine gun
- trade less often
- push for dramatically higher fight quality
- aim for `95%+` win rate on filled maker orders
- accept lower activity in exchange for cleaner fills, better inventory
  posture, and stronger edge concentration

## Operator-Locked Blueprint Inputs
### Core posture
- pure maker only for this lane
- `postOnly: true` always
- fail closed on degraded oracle / clock / risk state

### Timing
- maker fire window should live only in the final `15-20s`
- sweet spot stated as final `10-15s`

### Confirmation
- primary oracle: `Chainlink Data Streams`
- secondary oracle: `Pyth`
- require directional agreement
- require minimum oracle delta of about `0.20`
- skip if delta is below threshold or confirmation is weak

### Sizing / capital
- fixed maker order size: `$350` per side
- hard stacked-open-order cap: `4-6`
- intended bankroll context: roughly `$4k-$5k`

### Book / liquidity
- require at least `1.5x` order-size resting liquidity at or near the quote
  price
- with `$350` order size, the stated safety threshold is `$525` equivalent
  depth

### Observability
- every skipped maker opportunity should be logged with exact reason
- reasons include timing, delta, depth, stack cap, and risk gates

## Direct Alignment With Current Maker Truth
`VERIFIED`:
- the new maker fight-admission shadow packet already proved that
  `size_liquidity_pressure` is the strongest current driver family in the
  baseline set:
  - `size_liquidity_pressure=52`
- the shadow packet also proved `repeat_target_side_pressure` is a major
  current wound family:
  - `repeat_target_side_pressure=40`
- this blueprint's emphasis on deep enough books, strict timing, and low stacked
  concurrency is directionally aligned with that truth.

`VERIFIED`:
- current maker tooling already exposes the raw ingredients needed to test much
  of this blueprint:
  - `visible_depth_shares`
  - `size_to_visible_depth_ratio`
  - `queue_ahead_size`
  - `expected_fill_prob`
  - `financial_posture_class`
  - `reduce_only_recovery_active`
  - `same_target_side_shadow_count_prior`

Plain-English:
the blueprint is not coming out of nowhere. It hits the same pain families the
shop just found.

## Open Tensions / Non-Automatic Imports
### Fixed `$350` side size vs low-price geometry
`VERIFIED`:
- current baseline truth still carries a maker low-price geometry floor of
  `0.04375` under the present floor/cap policy.
- that means a rigid `$350` maker doctrine is not universally compatible with
  every cheap market in the current machine.

Implication:
- the blueprint's fixed size can be preserved as the desired cannon shot, but
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
- the blueprint's `0.20` delta needs precise machine semantics:
  - underlying price units
  - symbol normalization
  - freshness / ordering policy
  - exact interaction with the final-seconds timing window

Implication:
- this is promising, but it still needs a formal BRO-side definition before it
  becomes doctrine code.

### Final `15-20s` maker window
`INFERRED`:
- this is a plausible high-selectivity doctrine shift, but it has not yet been
  proven against BRO's current maker evidence.

Implication:
- it belongs in the next shadow/rubric packet first, not as an immediate
  runtime narrowing.

## Recommended Engineering Use
`VERIFIED`:
- the best immediate use is to treat this blueprint as input for the first
  stricter `skip-trash-windows` maker-selection packet.

Recommended first translation into BRO-selectivity tooling:
- add a `final_window_class` or equivalent late-window feature
- add a dual-oracle agreement / disagreement flag
- add a normalized oracle-delta feature
- add a depth-multiple-of-order-size feature based on the `1.5x` doctrine
- add a stacked-open-order pressure feature
- preserve all of those as report-only / shadow surfaces first

Recommended non-action:
- do **not** convert this whole blueprint directly into runtime maker law in
  one swing
- do **not** treat it as disproving the currently observed geometry floor
- do **not** assume the `95%+` target is already close

Plain-English:
use this blueprint as the aiming doctrine for the next selectivity packet, then
make BRO earn it through shadow truth before letting it touch the trigger.
