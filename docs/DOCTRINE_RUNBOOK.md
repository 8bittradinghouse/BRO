# BRO Doctrine Runbook (Canonical vs Degraded)

## Modes
- `doctrine.mode=canonical`:
  - fail-closed on missing expiry/threshold/side/fair/oracle freshness
  - `SNIPER_PRIMARY (30-20s)` is taker-only
  - maker market-reference uses:
    - `direct_midpoint` when both ws sides are present
    - `bounded_single_side_touch` only when midpoint is unavailable and exactly one ws side is present
  - bounded single-side maker reference is explicitly labeled `bounded_approximation` (never midpoint-authoritative)
  - `sniper.allow_without_expiry_metadata` must be `false`
- `doctrine.mode=degraded`:
  - allows explicit degraded fallback paths (for paper/soak only)
  - degraded path activity is stamped in telemetry/events

## Oracle Freshness Rule
- Canonical doctrine uses `doctrine.oracle_max_tick_age_sec`.
- In canonical mode, setting both `doctrine.oracle_max_tick_age_sec` and legacy
  `sniper.max_chainlink_tick_age_sec` is rejected by config validation.

## Stage Policy (Canonical)
- `OBSERVE`, `EVALUATE`, `UNKNOWN`, `EXPIRED`: maker/taker forbidden
- `MAKER_POSITION`: maker only
- `MAKER_TAKER_SELECTIVE`: maker + taker
- `SNIPER_PRIMARY`: taker only
- `EXTREME_ONLY`: taker only, stricter edge threshold

## Taker Competitiveness Semantics (Canonical)
- hard floor:
  - configured by `sniper.taker.competitiveness.hard_min_target_usd`
  - enforced with `hard_min_enforcement=skip_if_unachievable`
  - infeasible floor emits `taker_hard_min_notional_unachievable` (no under-floor submit)
- timing windows:
  - `final_window_enabled/final_window_sec` controls final-15 gate
  - optional `aggressive_window_enabled/aggressive_window_sec` controls final-10 path
  - outside-window decision emits `taker_outside_final_window`
- multi-oracle:
  - optional Pyth secondary oracle (`secondary_oracle.pyth`)
  - confirmation/boost only when enabled + directional agreement + threshold + timing-window pass
  - unknown secondary-oracle state is fail-closed for boost (no inferred confirmation)

Required observability surfaces:
- `sniper_taker_decision` event:
  - `conviction_score`, `edge_abs`, `required_min_edge`
  - `timing_window_class`, `aggressiveness_level`, `price_aggress_bps_applied`
  - `target_usd_requested`, `target_usd_resolved`
  - `hard_min_floor_applied`, `hard_min_unachievable`
  - `dynamic_size_capped_by_risk`
  - `multi_oracle_status`, `multi_oracle_confirmation`, `multi_oracle_boost_applied`
- `order_submit.taker_competitiveness` payload for accepted taker submits.
- report surfaces:
  - `taker_competitiveness.*` bucket distributions/counters
  - `taker_stage_net_breakout`

## New-Market Observe-First
- Market identity is propagated via `market_key`.
- On market key change, runtime emits `market_epoch_transition` and enforces
  observe hold until both are satisfied:
  - `doctrine.min_observe_cycles_on_entry`
  - `doctrine.min_observe_seconds_on_entry`
- Arrival conditions are stamped (`normal_on_arrival`, `sniper_primary_on_arrival`,
  `extreme_only_on_arrival`, `expired_on_arrival`, `unknown_on_arrival`).

## Verification Signals
- `doctrine_decision` event includes:
  - `market_key`
  - `stage` and `raw_stage`
  - `doctrine_gate_verdict` (`pass|fail`)
  - `reason`
- `doctrine_prereq_failure` event explicitly marks unknown-stage prerequisite failures.
- `stage_transition` emits stage changes with market key.
- `degraded_path_status` emits degraded fallback activation/deactivation.
- maker reference observability surfaces (run/report):
  - `maker_market_reference_fallback_count`
  - `maker_market_reference_fallback_bid_count`
  - `maker_market_reference_fallback_ask_count`
  - `maker_reference_direct_midpoint_activity`
  - `maker_reference_bounded_fallback_activity`
- readiness/reporting semantics:
  - direct-midpoint maker activity and bounded-fallback maker activity remain separate observational categories
  - categories must not be silently blended into a single undifferentiated maker-activity claim

## Local Validation
```bash
python -m pytest -q tests/test_doctrine_gating.py
python -m pytest -q tests/test_executor_hardening.py tests/test_execution_stack.py
python -m pytest -q tests/test_cli.py tests/test_market_discovery.py
```

## Outcome Truth Doctrine Boundary
- Outcome interpretation and attribution are governed by:
  - `BRO_OUTCOME_TRUTH_DOCTRINE.txt`
- Scope lock for that doctrine layer:
  - measurement + attribution + classification only
  - no control-plane mutation
  - no runtime decision mutation
  - no execution mutation

## Alert Transport vs Control Authority
- `alerts.enabled` controls notification transport only.
- Auto-stop / kill-switch control authority is independent of alert transport.
- Canonical semantics when `alerts.enabled=false`:
  - `transport_disabled_control_authority_unchanged`
  - transport is disabled for notifications
  - control authority remains active via runtime safety policy
- Canonical semantics when required status fields are absent:
  - `unknown_status_fields_missing`
  - report truth remains explicit unknown (never coerced to false)
- Machine-readable surfaces:
  - status rows: `alert_transport_enabled`, `auto_stop_control_authority_enabled`, `transport_disable_control_authority_unchanged`
  - soak/report artifacts: `control_authority_clarity` + `control_authority_observation_status`

## Suppression Cause Semantics
- Protection/suppression reporting emits:
  - `primary_suppression_cause`
  - `contributing_suppression_causes`
  - `ambiguous_suppression_cause`
- If precedence is uniquely determinable, exactly one primary cause is emitted.
- If precedence is not uniquely determinable, `ambiguous_suppression_cause=true` and no synthetic primary cause is invented.
- Run-level starvation observability surfaces:
  - `suppression_dominated_run`
  - `execution_starvation_mode`
  - `protected_no_trade_explanation`
  - `protection_path_trigger_chain`
- Trigger-chain interpretation surface:
  - `trigger_chain_interpretation=causal_suppression_chain` only when suppression dominates
  - `trigger_chain_interpretation=observational_timeline_only` otherwise
