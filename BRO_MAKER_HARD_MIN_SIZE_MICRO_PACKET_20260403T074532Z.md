# Maker Hard Minimum Size Micro-Packet

## 1) Diagnosis (current vs desired maker competitiveness)

Current state (verified):
- Maker hard floors and bounded depth scaling are already implemented in `OrderManager` sizing resolution.
- `paper_universal` already runs with maker floors/depth targets enabled (`$100` min notional, `200` min shares, depth target window `15-30%`).
- Remaining gap was not core logic, but enforcement clarity and observability:
  - impossible floor/depth configs could still be set in theory and only fail indirectly at runtime,
  - run-level reporting did not isolate hard-floor and depth-target application counts.

Desired state for this micro-packet:
- hard floor semantics become explicitly fail-closed at config validation time,
- depth/floor sizing decisions become first-class in soak reporting,
- doctrine language explicitly captures hard-floor + depth-target semantics.

## 2) Proposed Changes (exact files)

### `prodesk/config.py`
- Added fail-closed validation guards:
  - maker competitive sizing overlays require `sizing.mode=notional` when configured,
  - `sizing.max_usd` must be `>= maker_competitive_min_notional_usd` when hard min notional floor is enabled,
  - `sizing.min_usd` must be `<= maker_competitive_max_notional_usd` when hard max notional cap is enabled,
  - `strategy.max_order_size` and `risk.max_order_size` must each be `>= maker_competitive_min_shares` when hard min share floor is enabled.

### `prodesk/order_manager.py`
- Added explicit maker sizing truth fields into `size_resolution` payload:
  - `maker_hard_floor_active`
  - `maker_hard_notional_range_usd`
  - `maker_hard_share_range`
  - `maker_depth_target_ratio_window`
  - `maker_depth_scaling_active`

### `scripts/nightly_soak_report.py`
- Added new run-level section: `maker_sizing_competitiveness`:
  - maker submit rows / size-resolution rows
  - hard-min floor application counts (notional + shares)
  - depth-target floor application count
  - hard-max cap application counts
  - floor-active rows / depth-scaling-active rows
  - resolved notional percentiles and depth/ratio summaries
- Added human summary line:
  - `maker_sizing_competitiveness=...`

### Tests
- `tests/test_execution_stack.py`
  - added config validation tests for:
    - maker hard floor disallowed in `shares` sizing mode,
    - unachievable notional floor (`max_usd < maker_min_notional`),
    - unachievable share floor (`risk.max_order_size < maker_min_shares`).
- `tests/test_nightly_soak_report.py`
  - added report test for `maker_sizing_competitiveness` counts and percentile surfaces.

### Doctrine
- `BRO_CANONICAL_DOCTRINE.txt`
  - expanded `7.2.1 Maker Competitiveness Gate` to explicitly include:
    - non-negotiable hard floor semantics,
    - config fail-closed requirement for impossible floor/depth setups,
    - bounded depth-target semantics and required truth surfaces.

## 3) Expected Before/After Behavior

Behavioral impact:
- No strategy redesign and no gate weakening.
- Existing maker hard floor + depth scaling behavior remains intact.
- Invalid/contradictory maker sizing configs now fail at config validation instead of failing later indirectly.

Operational observability impact:
- maker sizing decisions become auditable at run level without log forensics.
- explicit counts now separate floor/cap/depth-target application from other maker blockers.

## 4) Updated Doctrine Snippet

Added/clarified under `7.2.1 Maker Competitiveness Gate`:
- hard floor and hard cap enforcement are explicit and non-bypassable when configured,
- impossible hard-floor/depth configurations are rejected during config validation,
- depth-target floor is bounded and uses visible top-of-book depth plus configured TOD multiplier,
- size-resolution truth surfaces must carry floor/depth rationale fields.

## 5) Handoff Note for 18F Review

Status:
- micro-packet implemented as surgical hardening + observability closure,
- full test suite passed after changes.

Verification run:
- tests: `590 passed` (`python -m pytest -q`)

Estimated maker-lane grade after this micro-packet:
- `95-96/100` (strong bounded competitiveness semantics, improved fail-closed config discipline, and improved run-level auditability).

Recommended next proof step (behavioral confirmation):
1. run canonical 20-minute window,
2. compare new `maker_sizing_competitiveness` block before/after,
3. verify maker floor/depth application counts align with actual maker submit/fill outcomes.
