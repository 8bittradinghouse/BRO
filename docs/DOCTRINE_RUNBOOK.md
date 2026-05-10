# BRO Doctrine Runbook (Canonical vs Degraded)

## Authority Boundary
- This file is BRO's downstream fighter-specific runtime/runbook policy surface.
- It is not spinal-cord doctrine.
- Higher authority remains:
  - `BRO_CANONICAL_DOCTRINE.txt`
  - `BRO_EDGE_DOCTRINE.txt`
  - `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`
  - `BRO_OUTCOME_TRUTH_DOCTRINE.txt`
  - `BRO_MODULE_ARCHITECTURE.txt`
  - `docs/BRO_WALLET_DOCTRINE.md`
- This file may define current BRO-specific runtime weapon policy, timing
  windows, stage bindings, and recovery posture only inside that higher
  doctrine frame.
- Maker-support surfaces split into three roles:
  - `docs/SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md`
    - pathway / heuristic / support-map surface only; not runtime authority by
      itself
  - `docs/GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md`
    - intended maker-weapon doctrine anchor for the late-window cannon design
    - authoritative for intended timing doctrine:
      - final `15-20s` doctrine window
      - final `10-15s` sweet-spot zone
    - not automatic proof that current runtime already matches that posture
  - `docs/BRO_WEAPON_NOMENCLATURE.md`
    - mnemonic alias surface only

## Single Semantic Language Law
- BRO must run on one semantic language from doctrine through runtime through
  reports.
- Current runtime posture, support probes, report summaries, and operator docs
  must not speak as separate teams.
- Runtime-emitted state is the first authority for what a row meant.
- Report layers may aggregate, classify, or summarize, but they may not
  silently rename runtime truth into a second meaning.
- Descriptive-only surfaces are allowed for research and diagnosis, but they are
  not peer authority to canonical runtime truth.
- If a lane shows duplicate names, duplicate owners, or `runtime_*` and
  non-`runtime_*` mirrors for the same concept, treat that as restoration work,
  not as harmless wording noise.

Restoration rule:
- restore the semantic contract at the highest correct layer before tuning local
  behavior
- do not add new shadow/backfill/report machinery to compensate for a language
  split unless the existing canonical path has first been proven insufficient

## Runtime Vocabulary Mapping
- `BRO_CANONICAL_DOCTRINE.txt` is the semantic root.
- If a live-emitted contract name already exists, this runbook uses that name
  and defines its runtime meaning rather than inventing a cleaner alias.
- Bare `eligible` is doctrine drift. Only domain-qualified forms are allowed.

| Contract family | Live contract names | Runtime meaning |
| --- | --- | --- |
| Runtime posture / readiness | `runtime_state`, `active_targets_present`, `no_target_standdown`, `book_feed_required`, `promotion_eligibility_hint`, `runtime_classification`, `promotion_eligible`, `primary_suppression_cause`, `contributing_suppression_causes`, `ambiguous_suppression_cause` | posture and runtime/readiness classification only; not market actionability or execution realism |
| Runtime transition | `previous_runtime_state`, `previous_book_feed_required`, `transition_reason_code`, `transition_reason_detail` | transition-domain truth only; not steady-state runtime classification |
| Market truth / selection | `maker_allowed`, `taker_allowed`, `maker_new_risk_allowed`, `normal_taker_allowed`, `reduce_only_recovery_allowed`, `preexpiry_emergency_taker_allowed`, `late_window_authority_class`, `secondary_oracle_status`, `secondary_oracle_confirmation`, `market_reference_mode`, `market_reference_basis`, `market_reference_confidence`, `market_reference_fallback_used`, `market_reference_source_side`, `market_reference_class` | emitted lane-authority plus market-truth/reference semantics for the lane; not wallet authority |
| Quote / submit path | `block_reason`, `decision_block_reason`, `decision_result`, `action_taken`, `submitted`, `filled`, `result`, `evaluation_scope`, `financial_posture_class` | local emitted stop/action/submit semantics for the lane or submit path; not cross-system owner by themselves |
| Wallet/startup authority | `canonical_live_wallet_truth`, `local_tx_lifecycle_state`, `open_order_state`, `integrity_tripwire_reconcile_state`, `authority_status_class`, `startup_authority_ready`, `authoritative_refresh_completed`, `order_capable_live`, `order_submit_eligible`, `canonical_live_nonce_available`, `canonical_live_pending_wallet_tx_available`, `canonical_live_nonce_source`, `canonical_live_nonce_detail`, `canonical_live_pending_wallet_tx_source`, `canonical_live_pending_wallet_tx_detail`, `live_truth_gap_reasons` | wallet/startup-domain authority only; not edge validity or quote truth |
| Decision lineage / provenance | `target_ref`, `source_target_ref`, `decision_input_source`, `decision_input_type`, `decision_input_emulated`, `decision_input_data_class` | lineage and decision-input provenance only; not authority class by themselves |
| Paper realism / outcome truth | `execution_realism_class`, `claim_boundary_class`, `record_claim_boundary_class`, `outcome_truth_status` | paper-realism and outcome-layer classification only; not runtime actionability or market authority |

Required distinctions:
- `maker_allowed` / `taker_allowed` are emitted lane-action booleans for the current row, not raw stage-bucket authority by themselves
- `maker_new_risk_allowed`, `normal_taker_allowed`, `reduce_only_recovery_allowed`, and `preexpiry_emergency_taker_allowed` are the Packet 1 late-window authority contract
- `runtime_state`, `runtime_classification`, `promotion_eligibility_hint`, and `promotion_eligible` are runtime posture/readiness terms only
- `promotion_eligibility_hint` is a live posture hint only; it is not the final `promotion_eligible` classification verdict
- `secondary_oracle_status` is emitted selection/oracle status only; it does not replace confirmation
- `market_reference_class` may legitimately be `authoritative`, `bounded_approximation`, or `not_available`
- `authority_status_class` and `order_submit_eligible` are wallet/startup
  authority terms only
- `startup_authority_ready` and `authoritative_refresh_completed` are wallet/startup readiness facts only; they do not create market actionability by themselves
- `decision_block_reason` is submit-path local stop semantics only
- `financial_posture_class` is lifecycle/risk posture only; it is not `runtime_state`
- `submitted` is not `filled`
- `result` stays reserved/null in the current edge-truth packet
- `execution_realism_class`, `claim_boundary_class`, and `outcome_truth_status`
  are paper/outcome-layer terms only
- `target_ref` is decision lineage only
- downstream `runtime_*` mirrors in reports are not runtime doctrine terms

Runtime-classification value vocabulary:
- `runtime_state`: `active`, `no_target_standdown`, `safety_halt`
- `active_targets_present`: `true|false`
- `no_target_standdown`: `true|false`
- `book_feed_required`: `true|false`
- `previous_book_feed_required`: `true|false`
- `promotion_eligibility_hint`: `true|false`
- `VALID_ACTIVE`: active targets plus meaningful participation
- `VALID_STANDDOWN`: healthy doctrinal standdown
- `NON_PROMOTABLE_NO_PARTICIPATION`: not invalid, but not promotable
- `INVALID_DEADLOCK`: fail-closed runtime deadlock/participation violation
- `INVALID_SAFETY`: fail-closed runtime safety violation
- `transition_reason_code`: `runtime_state_changed`, `book_requirement_changed`, `kill_switch_engaged`, `targets_activated`, `targets_absent`
- `secondary_oracle_status`: `confirmed`, `direction_mismatch`, `disabled`, `unknown`
  - compatibility input `available` must normalize to `unknown` before emission
- `market_reference_basis`: `direct_book_midpoint`, `ws_recent_paired_touch`, `ws_single_side_touch`, `missing`
  - report-only `report_book_top_pair_backfill` is a downstream reconstructed basis label, not a live emitted runtime basis
- `market_reference_confidence`: `authoritative`, `bounded_low`, `none`
- `decision_input_source`: `ws`, `rest`, `chainlink`, `replay`, `replayed`, `paper`, `simulated`, `synthetic`, `emulated`, `unknown`
- `decision_input_type`: `observed_live`, `replayed`, `bounded_derived`, `emulated`, `unknown`
- `decision_input_data_class`: `observed_live`, `observed_other`, `emulated`, `unknown`
- `authority_status_class`: `authoritative`, `bootstrap_non_authoritative`
  - `legacy_fallback_non_authoritative` is report/readout fallback only, not a live wallet contract value
- `startup_authority_ready`: `true|false`
- `authoritative_refresh_completed`: `true|false`
- `canonical_live_nonce_available`: `true|false`
- `canonical_live_pending_wallet_tx_available`: `true|false`
- `financial_posture_class`: `NORMAL`, `PREEXPIRY_REDUCE_ONLY`, `HARD_DEGRADED_REDUCE_ONLY`, `HALT_NEW_RISK`
- `decision_result`: `submitted`, `submit_rejected`, `selection_rejected`, `replace_guard_blocked`, `action_budget_exhausted`, `quote_unchanged`

## Maker Timing Authority Split
- Intended maker-weapon timing doctrine is anchored on:
  - `docs/GALAXY_MEGA_MAKER_CANNON_BLUEPRINT_2026-04-28.md`
- Current runtime configs, packet notes, and support artifacts may drift from
  that intended posture.
- When they do, describe them as runtime posture / implementation history, not
  as maker doctrine.
- Drifted runtime windows must not silently outrank the intended late-window
  cannon doctrine in support or control docs.

## Canonical System Mapping + Diagnostic Kernel (Required Reading)
- Doctrine-critical operator references:
  - [`BRO System Comparison Table`](BRO_SYSTEM_COMPARISON_TABLE.md)
  - [`BRO Fixed Diagnostic Template`](BRO_DIAGNOSTIC_TEMPLATE.md)
  - [`BRO Weapon Nomenclature`](BRO_WEAPON_NOMENCLATURE.md)
- Usage rule:
  - use the comparison table to map symptom -> subsystem -> harness -> connector box before any fix.
  - use the diagnostic template for every cross-system triage pass.
  - use the weapon nomenclature sheet as a memory/mapping aid only; do not let
    alias language silently outrank canonical maker/taker doctrine.
  - keep diagnosis run-anchored with lineage tuple (`run_id`, `git_commit`, `config_fingerprint`, `code_fingerprint`).
- Drift rule:
  - these docs are operator/engineering control aids; they do not change runtime behavior by themselves.
  - this runbook must not silently outrank the core doctrine stack.

## Modes
- `doctrine.mode=canonical`:
  - fail-closed on missing expiry/threshold/side/fair/oracle freshness
  - normal taker submit authority is `EXTREME_ONLY (<=7s)` only
  - earlier taker stages are diagnostic/observability only unless explicitly opened in a non-canonical investigation mode
  - maker market-reference uses:
    - `direct_midpoint` when both ws sides are present
    - `bounded_single_side_touch` only when midpoint is unavailable and exactly one ws side is present
  - bounded single-side maker reference is explicitly labeled `bounded_approximation` (never midpoint-authoritative)
  - taker market-reference uses:
    - `direct_midpoint` when ws midpoint is present
    - `bounded_single_side_touch` when midpoint is unavailable and exactly one ws side is present
    - fully missing ws market reference remains explicit fail-closed `market_probability_missing`
  - `taker.allow_without_expiry_metadata` must be `false`
- `doctrine.mode=degraded`:
  - allows explicit degraded fallback paths (for paper/soak only)
  - degraded path activity is stamped in telemetry/events

## Oracle Freshness Rule
- Canonical doctrine uses `doctrine.oracle_max_tick_age_sec`.
- In canonical mode, setting both `doctrine.oracle_max_tick_age_sec` and
  `taker.max_chainlink_tick_age_sec` is rejected by config validation.

## Timing Doctrine (Canonical)
- One coherent time base is mandatory:
  - host clock must be NTP-synchronized before any live or paper evidence run is trusted
  - wall-clock truth is UTC only
  - elapsed-time guards must use monotonic time, not wall clock
- Time-domain responsibilities are explicit:
  - UTC wall clock is authoritative for cross-system timestamps, artifact lineage, and decision anchors
  - monotonic clock is authoritative for cooldowns, age checks, rate limits, and elapsed runtime intervals
  - source timestamps from external feeds are evidence inputs, not permission to ignore local receive-time ordering
- Diagnose clock drift separately from runtime latency:
  - `clock drift` means host time is wrong relative to synchronized UTC
  - `latency/jitter` means host time is correct but BRO reacts late because of transport, CPU, websocket, signing, or order-path delay
  - do not treat a latency-chain problem as a clock problem
- Timing hardening is additive-first and fail-closed:
  - improve observability before tightening behavior
  - if host sync truth is unavailable, label it explicitly; do not infer “clock is fine”
  - if decision-to-submit latency is unobserved, do not claim taker timing is healthy
- Canonical authoritative timing thresholds are combat-tight:
  - `preflight.max_clock_skew_sec=0.25` is coarse fallback-only and must not be treated as primary host-clock proof
  - authoritative host-clock truth requires:
    - `system_clock_synchronized=true`
    - `ntp_service_active=true`
    - `stratum <= 3`
    - `abs(offset_ms) <= 10`
    - `jitter_ms <= 10`
    - `root_distance_ms <= 100`
  - authoritative event-domain skew truth is separate from host-clock truth:
    - `time_policy.skew_tolerance_ms=120.0`
    - do not infer event skew tolerance from coarse host-clock fallback tolerance
- Required observability surfaces:
  - host time-sync snapshot (`system_clock_synchronized`, NTP service state, server, offset/jitter/root-distance when available)
  - in containerized/runtime contexts, host sync authority may be unavailable inside the runtime even when the host clock is healthy
  - when runtime/container visibility is partial, canonical host-side session artifacts are authoritative:
    - `host_time_sync_active_start.json`
    - `host_time_sync_active_samples.jsonl`
    - `host_time_sync_active_stop.json`
  - `decision_reference_ts_utc` on order submissions
  - `decision_to_submit_latency_ms` on accepted submits
  - existing lag-chain surfaces remain required: `latency_sample`, `leadlag_book_move`, oracle tick age, and websocket freshness
- Validation rule:
  - every timing change packet must be proven with a monitored short canonical run before it is trusted
  - green tests or wrapper completion alone do not close timing work

## Stage Policy (Canonical)
- `OBSERVE`, `EVALUATE`, `UNKNOWN`, `EXPIRED`: maker/taker forbidden
- `MAKER_POSITION`: maker only
- `MAKER_TAKER_SELECTIVE`: maker only
- `SNIPER_PRIMARY`: maker/taker forbidden in canonical live doctrine; reserved for diagnostic staging only
- `EXTREME_ONLY`: raw late-window lineage bucket only; raw stage policy itself grants neither maker nor taker live authority
  - normal taker live authority is the explicit `<=7s` commitment lane surfaced through `normal_taker_allowed`
  - maker new-risk late authority is the explicit `15-20s` lane surfaced through `maker_new_risk_allowed`
  - reduce-only recovery remains a separate explicit authority lane surfaced through `reduce_only_recovery_allowed`

## Taker Competitiveness Semantics (Canonical)
- hard floor:
  - configured by `taker.competitiveness.hard_min_target_usd`
  - enforced with `hard_min_enforcement=skip_if_unachievable`
  - infeasible floor emits `taker_hard_min_notional_unachievable` (no under-floor submit)
- timing windows:
  - `final_window_enabled/final_window_sec` controls the default final-window gate; canonical current lock is `7.0`
  - `stage_final_window_sec_by_stage` is reserved for explicit diagnostic/non-production investigations, not canonical live taker authority
  - a taker stage window must remain semantically live against the stage interval; dead-by-construction stage windows are doctrine violations
  - `aggressive_window_enabled/aggressive_window_sec` must not silently broaden canonical live taker beyond the hard `<=7s` window
  - outside-window decision emits `taker_outside_final_window`
- multi-oracle:
  - optional Pyth secondary oracle (`secondary_oracle.pyth`)
  - confirmation/boost only when enabled + directional agreement + threshold + timing-window pass
  - unknown secondary-oracle state is fail-closed for boost (no inferred confirmation)
- canonical paper Packet 1 posture:
  - taker shot geometry is temporarily detuned to a fixed configured `$20`
    on the current paper packet
  - dynamic-size and preview bridge knobs may remain parseable for compatibility, but they must not own canonical taker fireability, submit ranking, or target sizing
  - `conviction_score` may remain emitted for diagnosis, but weighted conviction housing is not canonical taker owner-law

## Taker Sniper Commitment Doctrine (Canonical)
- Normal taker sniper is a commitment trade, not an enter-then-exit trade:
  - the expected normal lifecycle is entry by taker, hold through market resolution, then settle by outcome truth
  - canonical operator intent is a terminal last-seconds sniper commitment with the configured taker shot size, not a taker lane that should create cleanup work for itself after accepted entry
  - canonical normal-taker window is now hard `<=7s`; broader taker windows are diagnostic-only and are not canonical live doctrine
  - in paper/runtime, post-expiry settlement must use the first authoritative Chainlink tick whose `source_ts_utc` is at or after token expiry; later drifted ticks are not an acceptable substitute
  - settlement must flatten the binary position and realize `$1` for the winning token or `$0` for the losing token, rather than leaving expired inventory to decay through stale midpoint fallbacks
  - if authoritative post-expiry oracle truth is unavailable, runtime must fail closed and preserve the unresolved lifecycle explicitly instead of inventing settlement
  - pre-expiry maker/taker recovery is not part of normal taker expectancy
  - while financial posture remains `NORMAL`, an intentional normal taker commitment hold is exempt from pre-expiry reduce-only and emergency recovery; runtime must not "rescue" the very commitment it just chose to hold
  - any repeated or routine taker-side recovery after accepted normal taker entry is a doctrine breach signal, not acceptable steady-state behavior
  - pre-expiry emergency taker recovery must not machine-gun below-min dust residue; if the remaining reduce-only cap is below the minimum executable order size, runtime must surface the residue explicitly and stand down instead of repeatedly attempting doomed taker submits
  - any run that requires ordinary taker profit to come from pre-expiry unwind is not doctrine-clean
  - maker and taker must each remain fireable inside their own doctrine timing windows when their own direct truth gates are clean
  - doctrine does not authorize a cross-lane "wait for the other lane" shell as a substitute for proper lane-local timing, market truth, oracle truth, or wallet/risk gates
  - unresolved inventory, open orders, or lifecycle residue remain lane-local lifecycle/settlement patients; they do not create a general same-market ownership law that forces the other lane to stand down
- Normal taker side expression is live-parity constrained:
  - normal taker may only open exposure by buying the outcome token expected to resolve to `$1`
  - same-token normal taker `SELL` from flat or risk-increasing inventory is forbidden
  - `SELL` is allowed only when it is pure reduce-only against an owned positive position, or when a separate live-compatible complement-token buy path is explicitly implemented and audited
  - paper-only synthetic short behavior is not canonical live-compatible evidence
- Negative edge on a token is not a license to same-token short:
  - if the current token is overpriced, canonical expression is to buy the opposite/complementary outcome when token pairing and side mapping are authoritative
  - if the complementary token cannot be identified with authoritative `YES/NO`, strike, expiry, and market pairing metadata, skip
  - if the complementary book is unavailable, stale, one-sided in the required direction, or fails liquidity/price checks, skip
- Taker timing is intentionally narrow:
  - normal taker should fire only inside the canonical `EXTREME_ONLY <=7s` sniper window where oracle freshness and market-delay edge are strongest
  - broader diagnostic taker windows may be used for paper investigation only and must be labeled as diagnostic, not production doctrine
  - `MAKER_TAKER_SELECTIVE` and `SNIPER_PRIMARY` taker activity are not canonical live authority
- Entry requires expected-value discipline:
  - Chainlink/Pyth direction alone is insufficient
  - submit requires `fair_probability - entry_price` margin for buys of the expected winner, after all configured edge, confidence, multi-oracle, liquidity, and wallet/risk gates
  - buying a high-priced likely winner is allowed only when confidence clears the price-implied break-even probability plus the configured edge margin
  - if the market has already caught up to the oracle signal, skip
- Recovery is an emergency guardrail only:
  - recovery exists to reduce or flatten unintended or stuck exposure, preserve risk posture, and keep valuation/lifecycle gates honest
  - recovery never authorizes new risk, never upgrades a marginal normal taker setup into an acceptable one, and never counts as normal taker strategy success
  - taker-side recovery is admissible only for unintended, non-doctrine baggage; it is not part of the canonical planned lifecycle for an accepted normal taker sniper shot
  - maker-originated handoff into taker recovery is not valid authority for canonical taker
  - for accepted normal taker, taker-side recovery is dead-power doctrine: present in shared code, unplugged in canonical authority
  - emergency taker unwind should be evaluated as a safety outcome, not as alpha
- Promotion rule:
  - no paper run is live-promotable if it contains normal taker same-token short-style entry, unrecovered meaningful held exposure, `pnl_degraded=true`, unrecovered hard-degraded valuation, or unresolved lifecycle obligation caused by normal taker entry
  - clean taker proof requires accepted normal taker entries to be buy-side expected-winner or audited complement-token buys, with no unrecovered meaningful exposure at report close

Required observability surfaces:
- `taker_decision` event:
  - `conviction_score`, `edge_abs`, `required_min_edge`
  - `conviction_score` is diagnostic/readback truth only on the canonical taker path; it is not a second owner of submit ranking or shot sizing
  - `timing_window_class`, `aggressiveness_level`, `price_aggress_bps_applied`
  - `target_usd_requested`, `target_usd_resolved`
  - `hard_min_floor_applied`, `hard_min_unachievable`
  - `dynamic_size_capped_by_risk`
  - `multi_oracle_status`, `multi_oracle_confirmation`, `multi_oracle_boost_applied`
- `order_submit.taker_competitiveness` payload for accepted taker submits.
- report surfaces:
  - `taker_competitiveness.*` bucket distributions/counters
  - `taker_stage_net_breakout`
- commitment-doctrine report surfaces:
  - normal taker side-class distribution (`buy_expected_winner`, `same_token_sell_blocked`, `complement_buy`, `unknown`)
  - normal taker same-token risk-increasing `SELL` count
  - complement-token mapping authority/failure counts
  - unrecovered meaningful exposure caused by normal taker entry
  - recovery usage split between last-resort safety and normal path
  - doctrine-breach counters for:
    - taker submit outside hard `<=7s`
    - maker-to-taker recovery handoff attempts
    - taker-side recovery activity in taker scope
    - same-market lane-collision attempts while market ownership is still active

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
- New-exposure expiry gate is explicit and fail-closed:
  - `risk.min_sec_to_expiry_for_new_exposure` (default `15.0`)
  - risk-increasing submissions are rejected when `sec_to_expiry <= threshold`
  - risk-increasing submissions are rejected when `sec_to_expiry` is missing/unknown
  - pure risk-reducing/flatten submissions remain admissible under this gate
- No implied auto-flatten behavior is introduced by degraded valuation mode.
- Required truth surfaces:
  - `valuation_degraded`
  - `valuation_hard_degraded`
  - `pnl_degraded`
  - `loss_guard_degraded`
  - `valuation_degraded_reasons`
- Dust residual handling is explicit and two-phase (`shadow -> enforce`), not implicit:
  - one canonical classifier emits per-token exposure class:
    - `MEANINGFUL`
    - `DUST_ELIGIBLE`
    - `DUST_QUARANTINED`
  - classifier uncertainty is fail-closed (`MEANINGFUL`)
  - aggregate containment is bounded by:
    - `risk.position_dust_total_notional_usd_cap`
    - `risk.position_dust_token_count_cap`
    - `risk.position_dust_max_age_sec`
  - shadow posture transitions are hysteresis-bounded by:
    - `risk.position_dust_enter_consecutive_cycles`
    - `risk.position_dust_clear_consecutive_cycles`
  - enforcement is opt-in and rollback-ready:
    - `runtime.dust_classifier_enforce_enabled=false` keeps pure shadow mode
    - enabling enforcement allows dust-only hard-degraded exemption only when:
      - all raw hard-degraded held tokens are `DUST_ELIGIBLE`
      - no open-order/lifecycle obligation blocks remain
      - aggregate containment caps are not breached
    - any uncertainty or cap breach stays fail-closed (`MEANINGFUL` behavior)
- Held-token market-data starvation remains explicitly classified, not hidden:
  - `valuation_degraded_reasons` may include `held_book_not_found_404_age_sec=...` when a held token repeatedly returns `/book` 404.
  - held-token 404 recovery refresh is bounded and rate-limited by:
    - `runtime.held_book_not_found_force_refresh_interval_sec`
    - `runtime.held_book_not_found_force_refresh_min_unpriceable_age_sec`
  - pre-expiry reduce-only recovery activation is explicit and bounded by:
    - `runtime.held_preexpiry_reduce_only_sec` (default `15.0`)
    - activation requires held exposure (`non-flat` or `open-order`) and `sec_to_expiry <= held_preexpiry_reduce_only_sec`
    - terminal unwind priority can elevate posture to `HALT_NEW_RISK` when:
      - `runtime.terminal_unwind_halt_new_risk_sec` (default `7.0`) is reached
      - at least one held recovery token remains meaningful (`|net_shares| >= risk.min_order_size`) or has open-order lifecycle obligations
      - in this posture, risk-increasing intents are blocked globally while pure reduction/flattening remains allowed
    - expired-grace and pre-expiry recovery share the same canonical payload:
      - `reduce_only_recovery_active`
      - `reduce_only_side`
      - `reduce_only_size_cap_shares`
      - `financial_posture_class` + `sec_to_expiry` lifecycle context (required when `runtime.require_lifecycle_context_for_decisions=true`)
      - invalid payload combinations are surfaced explicitly (`lifecycle_context_mismatch`, `lifecycle_context_missing`)
    - maker and taker both consume this payload; recovery mode never authorizes risk increase
    - if recovery-side touch price is unavailable, submit is explicitly blocked (`reduce_only_recovery_touch_price_unavailable`)
    - pre-expiry emergency taker unwind is bounded and explicit:
      - `runtime.preexpiry_emergency_taker_window_sec` (default `7.0`)
      - emits explicit outcomes (`preexpiry_emergency_taker_unwind`) with `attempted`, `filled`, and `blocked_*` reason codes
      - current runtime has two explicit emit paths:
        - true emergency-window path when `sec_to_expiry <= preexpiry_emergency_taker_window_sec` and maker reduce-only exit is blocked/ineffective in-cycle
        - timing-gate handoff override path when maker reduce-only exit is blocked by `maker_timing_gate_closed` before that emergency window
      - the timing-gate handoff override path is diagnostic / safety-only and remains dead-power on the taker side; it does not restore normal taker live authority
      - repeated identical blocked rows may be compressed into repeat-summary events carrying:
        - `compression_mode`
        - `repeat_count_delta`
        - `repeat_count_total`
        - `repeat_distinct_token_count`
        - `repeat_token_ids_sample`
    - terminal tiny-notional reduce-only fallback is bounded and non-bypass:
      - `risk.reduce_only_terminal_min_notional_usd` (default `2.0`)
      - only for pure risk-reducing recovery intents in `PREEXPIRY_REDUCE_ONLY`, `HARD_DEGRADED_REDUCE_ONLY`, or `HALT_NEW_RISK`
      - never authorizes risk increase, never bypasses wallet/risk authority, and remains fail-closed on missing lifecycle context
  - recovery-only maker quote-quality relaxation is bounded and non-bypass:
    - `strategy.execution_quality.reduce_only_recovery_min_expected_fill_prob_floor` (default `0.02`)
    - `strategy.execution_quality.reduce_only_recovery_max_queue_ahead_size_multiplier` (default `2.0`)
    - applies only for true risk-reducing recovery intents
    - does not bypass post-only cross checks, stale-book checks, wallet authority, or risk authority
  - recovery-only lag/fair prereq relaxation is explicit and bounded:
    - when `reduce_only_recovery_active=true`, maker/taker lag-verification prereq gates and maker fair-probability prereq may be relaxed for de-risking continuity
    - this does not authorize risk increase and does not bypass WS-source, touch-price, stale-book, wallet, or risk authority checks
  - canonical paper profile currently uses:
    - `runtime.held_book_not_found_backoff_sec = 5.0`
    - `runtime.held_book_not_found_force_refresh_min_unpriceable_age_sec = 20.0`
    - `runtime.held_book_not_found_force_refresh_interval_sec = 45.0`
    - `runtime.held_preexpiry_reduce_only_sec = 15.0`
    - `runtime.preexpiry_emergency_taker_window_sec = 7.0`
    - `runtime.terminal_unwind_halt_new_risk_sec = 7.0`
  - transition/anomaly truth surfaces are explicit:
    - `valuation_hard_degraded_enter_count`
    - `valuation_hard_degraded_clear_count`
    - `held_unpriceable_started_count`
    - `held_unpriceable_recovered_count`
    - `preexpiry_404_anomaly_count`
    - `preexpiry_404_anomaly_active`
    - `lifecycle_context_mismatch_count`
    - `lifecycle_context_missing_sec_to_expiry_count`
    - `preexpiry_emergency_taker_attempt_count`
    - `preexpiry_emergency_taker_fill_count`
    - `preexpiry_emergency_taker_block_count`
    - `preexpiry_emergency_taker_block_reasons`
    - `held_unpriceable_cause_counts`
  - additive dust truth surfaces are explicit:
    - `held_exposure_class_by_token`
    - `held_exposure_detail_by_token`
    - `held_dust_token_ids`, `held_dust_count`
    - `held_dust_quarantined_token_ids`, `held_dust_quarantined_count`
    - `held_dust_total_notional_upper_bound_usd`
    - `held_dust_shadow_candidate_active`
    - `held_dust_shadow_active`
    - `held_dust_enforced_this_cycle`
    - `held_dust_hard_degraded_exempt_count`
    - `raw_valuation_degraded`, `raw_valuation_hard_degraded`

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
- Arrival conditions are stamped (`normal_on_arrival`,
  `extreme_only_on_arrival`, `expired_on_arrival`, `unknown_on_arrival`).

## Verification Signals
- `doctrine_decision` event includes:
  - `market_key`
  - `effective_stage` and `stage_bucket`
  - legacy `stage` and `raw_stage` as compatibility aliases only
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
