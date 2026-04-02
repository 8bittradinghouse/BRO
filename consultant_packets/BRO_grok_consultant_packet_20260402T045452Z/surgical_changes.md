# Surgical Changes Applied

## Code changes

1. `prodesk/order_manager.py`
- Added post-only crossing clamp before local cross reject:
  - new method `_maybe_clamp_post_only_intent(...)`
  - if maker post-only quote would cross touch, clamp by `tick_size` to nearest non-crossing price when deterministic and valid.
- Added event/counter surfaces:
  - `pre_submit_cross_guard_adjusted`
  - `pre_submit_cross_guard_adjusted` event payload with original/adjusted price and touch context.

Intent:
- reduce unnecessary local rejections for deterministically salvageable maker intents
- preserve explicit reject semantics when clamp is impossible

2. `configs/profiles/paper_universal.yaml`
- `runtime.maker_replace_min_rest_sec: 3.0`
- `doctrine.oracle_max_tick_age_sec: 5.0`
- `strategy.execution_quality.max_queue_ahead_size: 300.0`
- `strategy.execution_quality.min_expected_fill_prob: 0.045`
- setup-lock fingerprint updated accordingly.

Intent:
- reduce synthetic maker churn
- apply bounded oracle-lag accommodation
- relieve over-restrictive quote-quality gate pressure without disabling it

3. `tests/test_execution_stack.py`
- existing cross-guard reject tests kept by forcing clamp-unavailable setup (`tick_size=1.0`)
- new test added:
  - `test_pre_submit_cross_guard_clamps_crossing_quote_and_submits`

Intent:
- preserve old reject guarantees
- prove new clamp path deterministically

## Validation executed

- targeted tests for cross-guard semantics passed
- full `tests/test_execution_stack.py` passed
- canonical run `885c68e2-...` completed with validation pass and replay determinism consistency
