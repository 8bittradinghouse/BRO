# BRO Canonical Doctrine Stage-Machine Archive

## Classification
- `historical-only`
- archive boundary for the pre-lifecycle stage-machine doctrine body

## Purpose
This file marks the retired doctrine family that treated stage/posture aliases as
live runtime owners. The canonical live doctrine has moved to
`BRO_CANONICAL_DOCTRINE.txt` and the lifecycle blueprint.

## Archive boundary
Old stage-family terms such as:

- `maker_allowed`
- `taker_allowed`
- `runtime_state`
- `no_target_standdown`
- `book_feed_required`
- `effective_stage`
- `stage_bucket`
- `raw_stage`

may still appear in historical packet docs, replay fixtures, and explicit
compat/rejector tests. They are not current-owner doctrine.

## Current owner references
- `BRO_CANONICAL_DOCTRINE.txt`
- `docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md`
- `docs/DOCTRINE_RUNBOOK.md`
