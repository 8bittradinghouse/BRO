# BRO Diagnostic Tools

## Purpose
This file racks the durable shop instruments that help us inspect, map, and
improve `BRO` over long-horizon development.

These are not decorative docs.
They are reusable engineering tools, truth fixtures, and blueprint surfaces for
future packets.

## Maker Timing Doctrine Boundary
`VERIFIED`:
- this doc includes historical maker timing observations gathered under
  different runtime postures, including drift-era `45-60s` / `50-60s` packet
  surfaces.
- those observations are specimen/support truth only; they do not define maker
  doctrine.
- intended maker timing doctrine anchor is:
  - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
  - maker gate opens at `15s`
  - taker handoff opens at `7s`
- canonical paper proving front door for that doctrine is:
  - `broctl paper -- --active-minutes <minutes> --wait-sec 25`

## Current Tools
### `Forge Masters Archiver`
Identity:
- `FMA`
- `scripts/bro_metric_harvest.py`

## Classification
- `support-only`
- this is a diagnostic and forensic tooling surface
- any legacy stage-family language here is diagnostic ancestry only, not live
  runtime owner-law

What it does:
- harvests run/report truth across the archive,
- compresses engineer-first metrics,
- preserves cross-run anomaly and weapon-behavior surfaces,
- supports maker/taker/recovery debugging without fresh manual archaeology.
- now also harvests maker fireability window pressure, per-target active-window
  cadence, low-price viability geometry, target-level viability classes, and
  raw quote-quality severity bins for shadow-pressure analysis.
- now preserves first-class complete-maker decision-quality rates, while
  queue-pressure packet counters remain archive-only lineage from the retired
  queue-fight experiment family.
- now also preserves maker fight-admission shadow truth through report-bridge
  artifacts, calibration audits, and normalized row-level archive export so
  selectivity work can be recut without reopening raw report dirs.
- now also carries `Galaxy Mega Maker Cannon` shadow features on that same
  admission-shadow lane:
  - `cannon_window_class`
  - `maker_timing_band_class`
  - `session_regime_class`
  - `stack_pressure_class`
  - `secondary_oracle_status` / confirmation
  - `depth_multiple_vs_cannon_target` against the current `$100` doctrine shot
    size
- `FMA` now also preserves the bounded `20-45s` sibling observation lane as
  first-class shop artifacts:
  - `maker_mid_window_probe_rows.jsonl`
  - `maker_mid_window_probe_summary.json`
  - `maker_mid_window_probe_session_sweep.json`
- `FMA` now also supports exact curated specimen harvest through
  `--run-id-file`, so a peak-window maker sweep can be exported as one
  deliberate bundle instead of a drifting "latest runs" slice.
- current timing-ladder bundle now also exists for six valid-active maker runs:
  - `logs_exec/paper_universal/forge_masters_archive_maker_timing_ladder_valid_active_2026-04-28`
  - current truth:
    - `candidate_count_by_timing_band={"45_to_60s":111,"unknown":45}`
    - `complete_bad_ratio_by_timing_band={"45_to_60s":0.76,"unknown":0.6111111111111112}`
  - meaning:
    - this historical timing-ladder bundle was dominated by a drift-era
      `45-60s` runtime band; that archival posture does not define current
      maker doctrine
    - the archived `20-45s` seam is no longer a total unknown; it is now
      preserved as posture-blocked / truth-thin observational truth rather than
      a promoted maker lane
- current runtime truth boundary:
  - the bounded late-window probe now widens the observational population into
    `<=20s` rows even when maker is stage-disallowed there
  - report-layer fail-closed zero-depth logic now treats missing favored-side
    size as `0.0` available depth when the row still has enough market context
    to be judged honestly
  - report-layer secondary-oracle recompute now corrects midpoint-blind runtime
    `unknown` rows when bounded market reference plus dual fair values are
    present
  - current live truth from run `310f728b-dd59-4216-8d4b-259e66ce0f7b`:
    - `late_window_raw_row_count=68`
    - `candidate=36`
    - `truth_thin=32`
    - `full_cannon_candidate_count=0`
    - dominant candidate blockers:
      - `market_reference_not_authoritative`
      - `insufficient_depth_multiple`
  - current archive truth boundary:
    - the probe summary now also carries a decontaminated `latent_market_*`
      layer
    - this lets us ask:
      - if recovery posture were removed, was the underlying late-window market
        actually Galaxy-cannon quality?
    - healthier maker archive rereads now show:
      - transition sample `f79550de...`: `111` latent evaluable rows, `0`
        latent full cannon candidates
      - asia sample `dff97a9a...`: `58` latent evaluable rows, `0` latent
        full cannon candidates
      - peak sample `6c3f1332...`: still too truth-thin to judge honestly
    - dominant latent-market blocker families are:
      - `insufficient_depth_multiple`
      - `secondary_oracle_unknown`
      - narrower `non_viable_geometry`
      - `non_viable_geometry`
    - topology signature:
      - mostly one-sided WS with no midpoint-backed market reference
      - mostly `0.01 / 0.99` extreme-band rows
      - favored-side depth currently resolves to `zero_imputed` on all candidate rows
  - fresh native transition specimen:
    - run: `ba5bfde7-01f1-4e34-9695-975b56661ad9`
    - preserved in:
      - `logs_exec/paper_universal/forge_masters_archive_run_ba5bfde7-01f1-4e34-9695-975b56661ad9`
    - truth:
      - late-window probe:
        - `full_cannon_candidate_count=27`
        - `latent_market_full_cannon_candidate_count=27`
      - mid-window probe:
        - `full_mid_window_candidate_count=1`
        - `latent_market_full_mid_window_candidate_count=1`
    - meaning:
      - current native probe truth now points much more strongly at the
        `<=20s` Galaxy-cannon lane than at `30-45s`

Primary outputs:
- `run_index.jsonl`
- `metric_catalog.json`
- `maker_taker_summary.csv`
- `anomaly_summary.json`
- `maker_research_pack.md`
- `fma_bundle_manifest.json`

Shop role:
- foundational harvest chassis for the diagnostic tool set
- first instrument to use when a money-touching lane needs serious mapping
- supports later blueprint work by turning raw artifacts into repeatable truth

### `Maker Peak Session Harvest Sweep`
Identity:
- `scripts/maker_peak_session_harvest.py`

What it does:
- runs a bounded sequence of canonical paper sessions using explicit
  `run_id` / `session_id` values,
- preserves a sweep manifest, `run_ids.txt`, and a per-run ledger,
- gives `FMA` an exact-set specimen list for later harvest.

Current intended use:
- collect `20m` canonical maker specimens during the heavy `8am-2pm` working
  window,
- then pass the resulting `run_ids.txt` into:
  `python3 scripts/bro_metric_harvest.py --report-root logs_exec/paper_universal/reports --run-id-file <sweep_dir>/run_ids.txt --out-dir <bundle_dir>`
- optional wall-clock guard for that collection:
  `python3 scripts/maker_peak_session_harvest.py --session-count 30 --active-minutes 20 --wait-sec 25 --stop-before-local <YYYY-MM-DD>T14:00:00 --local-timezone America/Chicago`

Plain-English:
this is the orderly specimen rack for the next maker collection phase.

### `Maker Cannon ROI Setup Analyzer`
Identity:
- `scripts/maker_cannon_roi_setup.py`

What it does:
- reads a harvested `FMA` keeper bundle,
- scores the `Grok` / `Galaxy Mega Maker Cannon` doctrine inputs against the
  real keeper rows,
- separates:
  - high-ROI cannon setup pieces,
  - low-ROI safety guards,
  - and doctrine pieces that still need stronger proof or clearer semantics.

Current first result on the `2026-04-28` `13`-run keeper packet:
- bundle:
  - `logs_exec/paper_universal/forge_masters_archive_maker_peak_session_keeper_set_2026-04-28`
- output artifacts:
  - `solar_slug_maker_cannon_roi_setup.json`
  - `solar_slug_maker_cannon_roi_setup.md`
- verdict:
  - `partial_adopt_high_roi_components_only`
- strongest promote-now candidates:
  - `depth_requirement_1p5x`
  - `secondary_oracle_confirmation`
  - `repeat_target_side_calm`

### `Maker Zero-Submit Reconciliation Audit`
Identity:
- report-layer support artifacts emitted by `scripts/nightly_soak_report.py`

What it does:
- reconciles the maker funnel when a run produces `0` submits / `0` fills,
- keeps `maker_fight_admission_shadow` semantics narrow,
- adds a sibling pre-shadow audit so quote starvation is visible without
  redefining the shadow contract,
- freezes the `$350 Packet B` and `$250` runs as the canonical zero-submit
  specimen pair,
- separates:
  - upstream prereq blockers,
  - pre-shadow quote starvation,
  - matched shadow selection rejection,
  - and off-band full-cannon opportunities.

Primary artifacts:
- `maker_participation_waterfall.json`
- `maker_quote_starvation_audit.jsonl`
- `maker_quote_starvation_summary.json`
- `maker_truth_reference_starvation_audit.jsonl`
- `maker_truth_reference_starvation_summary.json`
- `maker_quote_construction_audit.jsonl`
- `maker_quote_construction_summary.json`
- `maker_timing_band_diagnostic_matrix.json`
- `maker_timing_band_decision.json`
- `maker_zero_submit_root_cause_audit.json`
- `maker_zero_submit_specimen_manifest.json`
- `maker_zero_submit_specimen_comparison.json`

Current bedrock truths from the specimen pair:
- `$350 Packet B`:
  - `10` active-band quote-starvation rows
  - `0` shadow rows
  - truth readiness is one-sided WS missing midpoint context
  - dominant deprivation reason:
    one-sided WS with zero favored depth
- `$250` caliber:
  - `20` active-band quote-starvation rows
  - `12` authoritative-complete active-band rows
  - `12` matched shadow selection rejects
  - `16` total shadow selection rejects
  - `4` off-band full-cannon opportunities
  - dominant one-sided deprivation reason:
    one-sided WS with zero favored depth

Repaired substrate follow-on truth:
- repaired `Packet B` specimen:
  - active-band quote starvation was materially reduced
  - observed active-band path shifted toward:
    `launch_safe_selection_insufficient_depth_multiple`
  - maker still produced `0` submits / `0` fills
- repaired `$250` specimen:
  - first real `10-15s` maker window (`2026-04-29T06:59Z` bucket):
    - `10` maker rows blocked by `token_lag_not_verified_for_maker`
    - `market_reference_mode = missing`
    - no shadow rows
  - second real `10-15s` maker window (`2026-04-29T07:04Z` bucket):
    - market truth was `direct_midpoint`, not bounded/missing
    - `9` maker no-submit rows:
      - `6` `launch_safe_selection_insufficient_depth_multiple`
      - `3` `one_sided_mode_disallow_side`
    - `11` shadow rejects with
      `selection_gate_primary_reject_reason = insufficient_depth_multiple`
    - still `0` submits / `0` fills
- interpretation:
  - paired-touch repair is directionally real, but it is not the whole answer
  - active maker windows can now split into two distinct failure families:
    - upstream truth/lag starvation
    - or honest selection-depth rejection once truth is good

Current decision-readiness meaning:
- `ready_for_truth_packet`

### `Maker Quote Integrity Audit`
Identity:
- report-layer support artifacts emitted by `scripts/nightly_soak_report.py`

What it does:
- isolates the maker submit path into three clean planes:
  - `model`
  - `quote`
  - `survival`
- freezes the repaired `$250` overnight specimen as a logic-only truth
  specimen,
- shows whether BRO:
  - certified one quote,
  - launched a materially different quote,

### `Canonical Maker Selection Authority Audit`
Identity:
- report-layer support artifacts emitted by `scripts/nightly_soak_report.py`

What it does:
- freezes one authoritative per-run canonical maker selection truth surface
  using the run session slice, not day-level event logs,
- counterfactually replays canonical maker submits through the intended minimal
  selector without inventing a second runtime control plane,
- makes zero-submit-era artifacts explicitly non-authoritative on runs that
  actually submitted maker orders.

Primary artifacts:
- `maker_selection_authority_audit.json`
- `maker_selection_authority_counterfactual.json`

Current canonical selector law in `paper_universal`:
- reuse the lifecycle-owned canonical selector under:
  - `lifecycle.selection`
  - `lifecycle.lane_gates.maker`
- `enabled = true`
- lifecycle-first admission and maker-lane gating now own timing semantics;
  the old stage allowlist is retired
- `require_secondary_oracle_confirmation = true`
- `cannon_target_notional_usd = 100.0`
- `max_same_target_submit_count_prior = 1`
- `max_same_target_side_submit_count_prior = 1`
- `min_depth_multiple = 1.5`

Current bedrock proof:
- certified canonical run:
  - `9563888b-bca3-4073-b7ec-71752928ec67`
- historical runtime truth for that run:
  - selector was off
  - `runtime_selector_enabled = false`
- counterfactual replay truth for that same run:
  - keep:
    - `paper-order-1`
    - `paper-order-2`
    - `paper-order-5`
    - `paper-order-6`
    - `paper-order-7`
- interpretation:
  - the old selector-owned one-sided branch has been retired from canonical
    maker selection authority
  - one-sided posture remains observational maker side-policy context, not a
    selector-owned reject family

Reporting quarantine law on submit runs:
- these artifacts must not be mistaken for canonical selection authority once
  maker actually submitted:
  - `maker_participation_waterfall.json`
  - `maker_quote_construction_summary.json`
  - `maker_truth_reference_starvation_summary.json`
  - `maker_fight_admission_calibration_audit.json`
- required markers:
  - `authoritative_for_canonical_selection = false`
  - applicability:
    - `zero_submit_only`
    - or `descriptive_only`
  - and/or canceled the resting order under a different survival reference.

Primary artifacts:
- `maker_quote_integrity_manifest.json`
- `maker_quote_integrity_trace.jsonl`
- `maker_execution_quality_semantics.json`
- `maker_quote_mutation_summary.json`
- `maker_resting_order_survival_audit.json`
- `maker_quote_integrity_summary.json`

Current bedrock truth from repaired `$250` specimen
`484e533d-c9a1-4ac4-bc0d-ce379c624e09`:
- quote plane:
  - certified `SELL` quote: `0.874`
  - cross-guard clamped launch quote: `0.961`
  - mutation class:
    `material_cross_guard_only`
  - the certified quote was crossing the touch; the launched quote was
    touch-adjacent post-only safe
- model plane:
  - fill-probability and queue-ahead remained identical across that quote jump
  - synthetic `BUY` and `SELL` truth tables both show the current
    `ExecutionQualityModel` is blind to materially different inside-spread
    prices
  - current verdict:
    `inside_spread_blind_spot_present`
- survival plane:
  - the resting order was canceled under next-cycle
    `launch_safe_selection_reject`
  - survival was evaluated against a re-derived desired quote, not the actual
    resting submitted quote
  - even on the actual resting price, depth still fell below `1.5x`
  - counterfactual verdict:
    `cancel_only_due_to_aggressive_survival_policy`

Current decision output:
- `VERIFIED_CLOSED`: `A. Quality-model repair`
  - code:
    `prodesk/execution_quality.py`
  - closure truth:
    the model now distinguishes materially different passive inside-spread
    prices and heavily penalizes huge intended size against tiny visible touch
    depth
  - repaired live proof from specimen `0e70ac1b-1eb0-4683-86a0-eb6ab8357ada`:
    absurd tiny-depth rows no longer showed fantasy `0.98` fill probability;
    they collapsed toward near-zero instead
- current next repair lane:
  `B. Quote-consistency repair`
  - code:
    `prodesk/order_manager.py`
  - new repair law:
    maker shadow / selection now certify the submission-facing quote, while
    preserving the raw strategy quote as lineage
  - repaired live truth from specimen `0e70ac1b-1eb0-4683-86a0-eb6ab8357ada`:
    - `2` maker submits
    - `2` partial fills
    - `2` cancel-remainders under `launch_safe_selection_reject`
  - lifecycle truth hardening:
    - canonical tracked-target cancels now preserve explicit maker
      ineligibility reasons such as `maker_timing_gate_closed` and
      `phase_disallow_maker`
    - this replaces the misleading prior pattern where the same deaths could
      surface as generic `orphan_token_order`
    - doctrine is unchanged; this is audit-truth hardening, not a maker-loosen
  - interpretation:
    quality-model blindness is no longer the first seam; quote consistency and
    then resting-order survival are now front-line
- `VERIFIED_CLOSED`: `C. Maker Commitment / No-Cancel Doctrine Repair`
  - code:
    `prodesk/models.py`
    `prodesk/tx_manager.py`
    `prodesk/gateway.py`
    `prodesk/order_manager.py`
    `scripts/order_lifecycle_audit.py`
    `scripts/nightly_soak_report.py`
  - doctrinal closure:
    accepted maker `GTC + post_only` orders now persist commitment metadata in
    the lifecycle record and are treated as held exposure, not routine quote
    candidates
  - active-window truth:
    pre-expiry routine cancel attempts now surface as
    `order_cancel_suppressed` instead of executing
  - terminal truth:
    expiry cleanup now uses canonical reason `commitment_window_ended` with
    terminal class `terminal_window_end`
  - audit truth:
    order lifecycle and maker quote/survival reports now distinguish:
    - suppressed routine cancel pressure
    - terminal window-end cleanup
    - exceptional shutdown/safety cleanup
  - implication:
    the old cancel-after-commit maker contradiction is no longer canonical
- regime warning:
  this specimen occurred around `02:04 AM Central`
  and is authoritative for logic, not peak-hours fillability or PnL

### `Maker Truth/Reference Repair Packet`
Identity:
- runtime repair in `executor.py`

What it does:
- repairs the active maker truth substrate when the live `ws` book is
  one-sided and midpoint-less,
- backfills a maker-only `backfilled_paired_touch` reference when the
  complementary `ws` side was seen recently enough to form a tight paired
  touch,
- feeds one consistent resolved book/reference map into:
  - maker competitiveness profiling,
  - quote construction / manager step,
  - and maker edge-evaluation logging.

Current implementation truths:
- paired-touch repair is maker-only; taker was not changed
- no config/setup-lock fingerprint churn was introduced for this packet
- paired-touch max delta is currently an internal bounded runtime policy:
  `0.10s`
- authoritative repaired rows now report:
  - `market_reference_mode = backfilled_paired_touch`
  - `market_reference_basis = ws_recent_paired_touch`
  - `market_reference_source_side = paired`

### `Run Financial Performance Summary`
Identity:
- standard block inside `reports/*/nightly_soak_report.json`
- rendered in `nightly_soak_report.txt` by `scripts/nightly_soak_report.py`

What it does:
- emits canonical run money stats without inventing a second accounting path,
- uses BRO cashflow truth from:
  - `fill`
  - `wallet_position_settled`
- carries the run-start capital snapshot from the existing single sources of
  truth:
  - `run_manifest.config.wallet`
  - first authoritative `wallet_contract` status row
- cross-checks the resulting ledger against latest `gauge.total_pnl`,
- exposes:
  - starting base capital
  - starting deployable capital
  - ending stable / deployable capital
  - overall `net_pnl_usd`
  - overall `win_rate`
  - maker/taker lane breakouts
  - submitted / filled notional
  - average submitted / filled order size
  - gross profit / gross loss
  - expectancy
  - profit factor

Claim boundary:
- `net_pnl_usd` is ledger cashflow from fills plus settlement,
- `latest_total_pnl_usd` is the latest status-surface total PnL cross-check,
- start-capital fields are configuration truth plus opening-wallet cross-check,
  not a second wallet doctrine,
- `win_rate_basis = closed_target_ref_campaigns_with_zero_remaining_position`
- trade win/loss stats are campaign-scoped, not naive raw-order counts, so
  same-target multi-order entry/closeout sequences do not get double-counted as
  separate closed trades.

Why it exists:
- the run report must carry first-class money truth, not force manual
  back-calculation,
- base capital and order-size context are part of reading execution quality
  honestly,
- operators need one canonical surface for PnL, win rate, starting capital, and
  lane breakout without trusting wrappers alone.

Next proof step:
- keep backchecking the report against raw logs on certified specimens,
- preserve one accounting path only,
- extend the same canonical finance surface into future watched runs and peak
  batches.

Current intended use:
- first stop when a strict maker packet stays safe but does not participate,
- prove whether the next move belongs to:
  - timing-band shift,
  - quoteability repair,
  - truth/reference hardening,
  - or caliber reduction.
- keep report-only for now:
  - `stack_hard_cap_ok`
  - late-window timing shift itself
- needs formalization:
  - `secondary_oracle_delta_abs_ge_0p20`

Plain-English:
this is the shop attachment that tells us which parts of the maker cannon are
actually buying safety/selectivity ROI right now, instead of forcing us to take
the whole doctrine package as one all-or-nothing bet.

Current runtime bridge from this tool truth:
- the bounded launch-safe maker selector now exists as:
  - runtime gate:
    `lifecycle.selection` plus maker-lane lifecycle gates
- historical experiment-profile lineage has been cut from the live workspace;
  the durable runtime bridge that remains is the canonical selector gate and
  archived report truth rather than separate packet-profile surfaces.
- it currently enforces only the proven high-ROI pieces:
  - `depth_requirement_1p5x`
  - `secondary_oracle_confirmation`
  - `repeat_target_side_calm`
- it does **not** yet promote:
  - hard stack cap as a primary selector
  - strict late-window timing shift as runtime law
  - exact `0.20` delta semantics
- first end-to-end bounded launch-safe maker packet proof was mechanically
  healthy but strategically `NON_PROMOTABLE_NO_PARTICIPATION` at `$350`
- next live maker work should continue through canonical selector/runtime
  surfaces, not by reviving retired packet profiles

### `Fusion Core Profiling Tool`
Identity:
- `FM-2A1`
- `scripts/fusion_core_profile.py`

What it does:
- shapes exported `FMA` truth into deterministic profile families,
- promotes wound and strength families into reusable candidate blanks,
- grades profile stability without touching runtime behavior,
- compares specimen and corpus cuts through deterministic diff semantics,
- now includes a `viability_shadow` family so low-price geometry and
  viable-target queue-depth burden can be cut as first-class lathe truth.
- current follow-on runtime packet uses that lathe truth to justify a bounded
  inside-spread queue-fight experiment rather than a global gate loosening.
- first runtime proof of that historical queue-fight packet produced `0` real
  queue-pressure candidates and `0` gate conversions, so the archived tool
  truth pointed away from more queue sanding and toward a market-selection /
  skip-trash-windows follow-on lane.
- the next proposed lathe family, `fight_admission_shadow`, is deliberately not
  promoted yet because the baseline recut did not show meaningful submitted-fight
  class separation. Current selectivity truth is still an `FMA`-level research
  surface, not an earned lathe cut.

Shop role:
- first implemented `lathe` in the BRO diagnostic machine shop
- hard-decoupled from `FM-1A1 FMA`
- current status:
  - implemented foundation
  - implemented semantic red-team hardening
  - implemented profile-quality promotion
  - implemented lane-maturity / contract-trust hardening
  - implemented metric-drift diff hardening
  - implemented calibration / promotion-audit hardening
  - implemented threshold-pressure hardening

Primary outputs:
- `fusion_core_input_contract_audit.json`
- `fusion_core_lane_readiness.json`
- `fusion_core_profile_catalog.json`
- `fusion_core_profile_cards.md`
- `fusion_core_stability_matrix.json`
- `fusion_core_calibration_audit.json`
- `fusion_core_threshold_pressure_matrix.json`
- `fusion_core_candidate_blanks.json`
- `fusion_core_cohort_comparison.csv`
- `fusion_core_profile_diff.json`

Key support docs:
- `docs/FM_2A1_FUSION_CORE_PROFILING_TOOL_PLAN.md`
- `docs/FM_2A1_FUSION_CORE_PROFILING_TOOL_SPEC.json`
- `docs/FM_2A1_FUSION_CORE_PROFILING_TOOL_PASS2_PLAN.md`

### `BRO Metric Atlas`
Path:
- `docs/BRO_METRIC_ATLAS.md`

What it does:
- defines metric meaning,
- defines claim boundaries,
- tells future operators which gauges to read first,
- helps prevent semantic misuse.

### `Solar Slug Maker Circuit Schematic`
Paths:
- `docs/SOLAR_SLUG_MAKER_CIRCUIT_SCHEMATIC.md`
- `docs/SOLAR_SLUG_MAKER_TRUTH_SEMANTICS_MAP.json`

What it does:
- preserves the maker lane pathway order,
- maps truth populations,
- identifies current hotspot families,
- keeps future maker tuning/debug work from re-deriving the whole lane by hand.

### `Reference Data Packs`
Seed paths:
- `docs/BRO_BASELINE_RUN_SET_USA_PM_2026-04-27.md`
- `docs/BRO_BASELINE_RUN_SET_USA_PM_2026-04-27.json`

What they do:
- preserve named reusable specimen batches,
- separate clean baseline stock from warning-tag stock,
- give `FMA`, `FM-2A1`, and future toolheads stable comparison sets,
- support dead-zone analysis work without re-deriving which runs belonged
  together.
- can also be recut into baseline-only corpus bundles when the broad historical
  archive would dilute the signal of the active shop lane.

Plain-English:
these are the first marked reference stock piles for the shop.

## Design Rule
New diagnostic tools should only be added here if they are:
- reusable,
- evidence-derived,
- bounded in purpose,
- and likely to improve future engineering packets materially.

Plain-English:
if a tool makes the shop smarter again later, it belongs here.

## Lane Mapping Workflow
When a money-touching work lane does not yet have a schematic, the default shop
flow is:
1. run the lane through a serious evidence pass using `FMA` and primary
   artifacts
2. do at least three bounded diagnostic passes:
   - first-pass orientation
   - second-pass red-team / semantic pressure test
   - third-pass truth hardening / hotspot promotion
3. once the lane truth is mature enough, create:
   - one human-readable circuit/pathway schematic
   - one machine-readable truth-semantics map
4. rack those maps here as BRO diagnostic tools

Plain-English:
harvest first, then map. Do not draw a blueprint for a lane we have not really
interrogated yet.

## Future Rack
Likely future candidates, only after real evidence passes:
- `Taker Katana` pathway/truth schematic
- `Sniper Wakazashi` pathway/truth schematic
- shared money-touching pathway map
- regime comparator tools
- drift sentinel / semantic regression tools
