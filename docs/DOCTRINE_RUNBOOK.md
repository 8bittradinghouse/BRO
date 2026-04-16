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

## Risk Competitiveness Semantics (Canonical)
- dynamic scaling (`risk.dynamic_scaling`):
  - bounded cap scaling from volatility + TOD + edge
  - applies to exposure caps only (position/notional/global), never kill-switch/readiness authority
  - `unknown_input_policy=no_aggressive_uplift` prevents inferred aggressive uplift on missing inputs
- global exposure guard (`risk.global_exposure_guard`):
  - projected exposure combines active positions + resting orders + incoming intent
  - reject reason is explicit: `global_exposure_cap`

Required observability surfaces:
- `risk_reject.risk_decision_basis` and `order_submit.risk_decision_basis`
- nightly report `risk_competitiveness`:
  - `decision_count_by_lane`, `reject_count_by_lane`
  - `scaling_class_distribution`
  - `reject_reason_distribution` (including `global_exposure_cap`)
  - global exposure utilization ratios + near-cap counters

## PnL / Loss-Guard Degraded Valuation Semantics (Canonical)
- Last-known midpoint reuse is freshness-bounded by:
  - `risk.last_known_mid_max_age_sec` (default `6.0`)
- One-sided conservative quote reuse is freshness-bounded by:
  - `risk.one_sided_quote_max_age_sec` (default `6.0`)
- Direct midpoint reuse is freshness-bounded by:
  - `risk.max_book_age_sec`
- Valuation source classes:
  - `fresh_live_mid`
  - `fresh_live_side_conservative_quote`
  - `fresh_last_known_mid`
  - `conservative_bound_hard_degraded`
- `valuation_degraded=true` when any non-flat position token is valued from:
  - `fresh_last_known_mid`
  - `conservative_bound_hard_degraded`
- `valuation_hard_degraded=true` when any non-flat position token is valued from:
  - `conservative_bound_hard_degraded`
- In hard-degraded mode:
  - risk engine enters risk-reduction-only posture
  - new risk-increasing submissions are rejected
  - management/exit submissions are allowed only when they reduce exposure without crossing through flat
- No implied auto-flatten behavior is introduced by degraded valuation mode.
- Required truth surfaces:
  - `valuation_degraded`
  - `valuation_hard_degraded`
  - `pnl_degraded`
  - `loss_guard_degraded`
  - `valuation_degraded_reasons`
- Held-token market-data starvation remains explicitly classified, not hidden:
  - `valuation_degraded_reasons` may include `held_book_not_found_404_age_sec=...` when a held token repeatedly returns `/book` 404.
  - held-token 404 recovery refresh is bounded and rate-limited by:
    - `runtime.held_book_not_found_force_refresh_interval_sec`
    - `runtime.held_book_not_found_force_refresh_min_unpriceable_age_sec`

## Execution-Quality Semantic Split (Canonical Reporting)
- Immediate midpoint-relative fill quality is reported under:
  - `execution_quality_immediate_midpoint.*`
- Horizon outcome quality is reported under:
  - `execution_quality_horizon_outcome.*`
- Legacy aliases (`execution_quality.*` and `pickoff_indicator.*`) remain compatibility-only.
- Operators must not conflate immediate capture/adverse metrics with horizon outcome metrics.

## Wallet Authority Semantics (Canonical)
- Wallet controller is canonical authority for capital buckets, approvals, nonce truth, reservations, and reconciliation.
- Executor/strategy may request authorization; they may not infer deployable capital independently for final authorization.
- Dual-veto semantics are mandatory:
  - wallet authority = canonical capital truth + non-overridable capital veto
  - risk engine = final admissibility authority + non-overridable risk veto
  - final permission requires both allow
  - no path may convert either veto into reduce-success
- Wallet preview/health signals are advisory for orchestration and vetoing unsafe states; they must not replace final risk admissibility authority.
- Live unknowns are fail-closed:
  - wallet identity ambiguity
  - approval target ambiguity
  - nonce ambiguity
  - reconciliation mismatch beyond thresholds
- Startup authority barrier is fail-closed:
  - before post-registration authoritative refresh succeeds, authority class is `bootstrap_non_authoritative`
  - bootstrap surfaces are non-authoritative and may not be consumed as final readiness truth
- Reconciliation scope is explicitly `integrity_tripwire`, not full accounting ledger truth.

Canonical open-limitation phrase block (source-locked):
- canonical live nonce truth unavailable
- canonical live pending-wallet-tx truth unavailable
- strict order-capable live remains fail-closed
- reconcile is integrity tripwire, not full ledger accounting

Provider payload field criticality (canonical):
- required authority fields:
  - `live_wallet_balance`: missing/ambiguous => fail-closed
- required authority health fields:
  - `live_allowance`: missing/ambiguous => unhealthy surface (fail-closed when allowance is required)
- optional/supporting fields:
  - `live_pol_balance`: missing allowed by policy; material ambiguity => unhealthy surface
- material disagreement rule:
  - disagreement when `span > max(abs_tolerance, rel_tolerance * max(1, |low|, |high|))`
- tolerance constants are centralized and shared by provider logic + tests:
  - `PROVIDER_AMBIGUITY_ABS_TOLERANCE_DEFAULT`
  - `PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT`

Truth-domain naming is explicit and non-interchangeable:
- `canonical_live_wallet_truth`
- `local_tx_lifecycle_state`
- `open_order_state`
- `integrity_tripwire_reconcile_state`
- `bootstrap_non_authoritative` (status class only)

Required wallet status contract fields:
- `gas_balance`, `gas_reserve_min`, `gas_ok`
- `stable_balance_total`, `protected_reserve`, `open_reserved`, `deployable_capital`
- `approval_ok`, `nonce_ok`, `reconcile_ok`, `wallet_health_ok`, `wallet_health_reasons`
- `reservation_mismatch_candidate`, `reservation_mismatch_delta_usdc`, `reservation_mismatch_detail`
- `authority_status_class`, `order_capable_live`, `order_submit_eligible`
- `canonical_live_nonce_available`, `canonical_live_pending_wallet_tx_available`
- `canonical_live_nonce_source`, `canonical_live_nonce_detail`
- `canonical_live_pending_wallet_tx_source`, `canonical_live_pending_wallet_tx_detail`
- `live_truth_gap_reasons`

Required wallet events:
- `wallet_state_refresh`
- `wallet_health_gate`
- `wallet_reservation_created`
- `wallet_reservation_released`
- `wallet_reservation_settled`
- `wallet_approval_check`
- `wallet_approval_submitted`
- `wallet_nonce_state`
- `wallet_reconcile_result`
- `wallet_integrity_warning`
- `wallet_integrity_fail_closed`
- `wallet_startup_authority_refresh`
- `wallet_health_gate_veto`
- `wallet_local_tx_lifecycle_state`
- `wallet_open_order_state`

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
