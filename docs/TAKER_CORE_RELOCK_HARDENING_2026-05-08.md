# Taker Core Relock Hardening (2026-05-08)

## Purpose
- `VERIFIED`: this is a relock / hardening aid for future taker-core reloads.
- `VERIFIED`: it preserves the blueprint-first lens that should govern future taker analysis, surgery, and paper-live proving.
- `VERIFIED`: it is not a new doctrine root.

## Authority Boundary
- Active doctrine/runtime owners still are:
  - `docs/DOCTRINE_RUNBOOK.md`
  - `docs/EDGE_TRUTH_RUNBOOK.md`
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md`
  - `docs/BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md`
  - `docs/PROJECT_TRUTH_STATE.md`
  - current runtime/config/report code
- This file is a relock lens, not an overruling source.

## Core Lock
- `VERIFIED`: taker is supposed to be a real weapon, not a permanently sheathed museum piece.
- `VERIFIED`: a gate doing *a* job is not enough; it must be doing the *right blueprint job*.
- `VERIFIED`: if something keeps taker from executing and it is not doctrine-true and blueprint-appropriate, it is wrong even if it looks protective.

Plain English:
- right gates stay
- wrong gates die
- "careful but non-viable" is still failure

## Right vs Wrong
### Right
- lane-local timing authority
- oracle truth / freshness
- market reference truth
- book / liquidity truth
- wallet / risk fail-closed
- strong observability

### Wrong
- legacy sniper-era separation logic that keeps the weapon from firing
- fake authority or reporter-law pretending to own runtime
- accessory scoring shells that outrank direct truth
- bug-chase compensator mass
- "safety" that makes the taker lane non-viable as an actual trading weapon

## Blueprint Lock
- `VERIFIED`: blueprint requires:
  - hard final fire window
  - strong dual-oracle confirmation
  - book-depth / liquidity check
  - fixed shot geometry
  - fail-closed unknown-state behavior
  - strong observability
- `VERIFIED`: blueprint does not justify broad accessory gating or cross-lane wait mechanics.

## Current Keep-Now Gates
- `normal_taker_authority_closed`
- fully missing ws market reference remains fail-closed as
  `market_probability_missing`
- `taker_requires_ws_book_source`
- oracle freshness / unknown-state fail-closed
- current dual-oracle keep-now lock path (`multi_oracle_boost_*`) while it
  remains a confirmation aid instead of a fake owner

These are still presumed real sword geometry unless disproven.

## Current Overbuild Watchlist
- `sniper.taker.min_edge_by_stage.EXTREME_ONLY`
- dynamic-size accessory shell when shot is already fixed
- stage-priority accessory mass
- conviction / weighted scoring accessory housing
- visible-fill-ratio accessory gating
- stale doctrine/report wording that teaches old sniper-era owner stories

## Testing / Detune Order
If taker still looks too sheathed, use this order:
1. keep the real fail-closed truth gates
2. simplify dead accessory housing first
3. first detune candidate: lower `EXTREME_ONLY` taker threshold
4. second detune candidate: relax liquidity accessory gating if it proves too tight
5. do not widen into broad earlier windows first
6. do not weaken missing-midpoint / stale-oracle fail-closed first

## Reload Use
When asked to "reharden to core taker docs" or equivalent:
1. load this file
2. load the taker blueprint
3. load current canonical doctrine
4. load the active Packet 1 artifact and board sink
5. judge every surviving taker gate by one binary question:
   - does this help the taker weapon fire correctly when the blueprint says it should?
