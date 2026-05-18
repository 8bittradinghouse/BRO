# Solar Slug Maker Circuit Schematic

## Purpose
This document preserves a bounded system-pathway and truth-semantics map for the
`Solar Slug Maker Cannon`.

It exists to make future maker work safer by clarifying:
- where the maker lane actually gets its truth,
- which pathway produces which artifact,
- which metrics belong to which population,
- where current hotspot families live,
- and how future tuning packets should avoid semantic self-injury.

Plain-English:
this is the wiring diagram and legend for the maker weapon, not a strategy
tuning memo.

## Authority Boundary
- Canonical runtime/doctrine authority remains in code, validators, and
  established BRO doctrine files.
- Canonical paper proving front door is:
  - `broctl paper -- --active-minutes <minutes> --wait-sec 25`
- Binary market semantics remain:
  - `YES` / `NO` at the token/market identity layer
  - `BUY` / `SELL` at the execution-direction layer after token-side
    resolution
- This schematic is a truth-and-pathway map.
- It does not authorize strategy drift, threshold changes, or runtime behavior
  changes by itself.
- Heuristic surfaces remain explicitly heuristic.
- Intended maker timing doctrine defers to:
  - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
  - maker gate opens at `15s`
  - taker handoff opens at `7s`
- Any other timing-band language in this schematic is observational/runtime
  truth only and must not be read as independent maker doctrine.

## Current Scope
This schematic is based on the current maker forensic and harvest work completed
for:
- `8bfb70eb-c942-48eb-87ff-b9628b3098c7`
- `e675467e-368a-49db-bc03-f35c96aebba8`
- `ed184f61-c453-4511-a5e5-3fa24271c191`

Primary surfaces:
- `edge_truth_audit.json`
- `nightly_soak_report.json`
- `order_lifecycle_audit.json`
- `outcome_truth_audit.json`
- `outcome_truth_records.jsonl`
- `FMA` outputs from `scripts/bro_metric_harvest.py`

## Packet 2 Companion Board Stack
This schematic now has explicit companion boards for the classifications it was
previously only hinting at:
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_AUTHORITY_CENSUS_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_GATE_LEGITIMACY_BOARD_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_DOCTRINE_PROPOSAL_DELTA_BOARD_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SUPPORT_TOOL_FENCE_BOARD_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_HISTORY_ONLY_DEMOTION_BOARD_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SMALL_LOSS_SCAR_TISSUE_BOARD_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_PATH_FORENSIC_SEMANTIC_AUDIT_2026-05-10.md`
- `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_TIMING_OWNER_LAYER_2026-05-10.md`

Plain-English:
this schematic still owns the pathway legend, but the companion boards now own
the sharper Packet 2 classification calls.

## Current-Code Packet 2 Hardening Notes
- `VERIFIED`: the live maker river is now explicitly traced through:
  - stage / `sec_to_expiry` seed
  - maker prereq and late-window timing gate
  - WS-source legality
  - authoritative market-reference filter
  - competitiveness profile
  - post-only pre-submit handling
  - selection gate
  - risk submit legality
  - lifecycle propagation
  - emitted truth and downstream validators
- `VERIFIED`: the intended current paper maker doctrine is now:
  - maker gate opens at `15.0s`
  - maker new-risk authority may live in `(7.0, 15.0]`
  - taker takes over at `<=7.0s`
- `VERIFIED`: active paper doctrine now requires one-sided maker posture before
  maker selection can fire.
- `VERIFIED`: active paper doctrine now treats committed maker orders as a
  token-level hold:
  - one maker order owns the token/window
  - no opposite-side flip admission while that commitment is active
- `VERIFIED`: active paper taker doctrine now latches one submit per
  market/window rather than allowing repeated same-window re-entry churn
- `VERIFIED`: selection-gate timing leaves are currently dormant in the active
  paper profile and must stay fenced from live authority until a later packet
  explicitly approves otherwise
- `VERIFIED`: the stronger `1.5x` depth rule is restored as live current-paper
  selector steel for the active cannon shot

## Pathway Map
### 1. Market / Reference Substrate
Engineering meaning:
- This is the upstream reference spine that gives the maker lane book state,
  fair-probability context, and midpoint lineage.
- If this layer is weak, downstream maker truth can still look mechanically
  coherent while being strategically misleading.

Primary surfaces:
- `nightly_soak_report.json`
- `edge_truth_audit.json`
- maker reference counters harvested by `FMA`

High-value fields:
- `maker_reference_direct_midpoint_activity`
- `maker_reference_missing_activity`
- `market_data_rest_ratio`
- `market_data_ws_delta`

Current hotspot family:
- missing-reference activity
- bounded degraded valuation context

Plain-English:
this is where the maker cannon gets the ruler it uses to see the market.

### 2. Decision-Cycle Edge Truth Path
Engineering meaning:
- This layer records decision-cycle truth: what the lane could have done, what
  it was allowed to do, and what action it took on a per-evaluation-row basis.
- It is upstream of submit truth and should not be confused with real order or
  fill populations.

Primary surfaces:
- `edge_truth_audit.json`
- action-row summaries in `nightly_soak_report.json`

High-value fields:
- `maker_submits`
- `maker_no_submit_total_count`
- `maker_no_submission_cause_distribution`
- `maker_block_reason_distribution`
- `maker_timing_gate_blocked_decision`

Current hotspot family:
- quote-quality skips
- sizing rejects
- no-submit overlays

Plain-English:
this is the “what did maker see and choose?” layer, not the “what got filled?”
layer.

### 3. Stage / Lane Eligibility Path
Engineering meaning:
- This layer defines whether the maker lane is allowed to act under the current
  stage and runtime authority.
- It is separate from whether the action would have been profitable.

Primary surfaces:
- stage policy embedded in edge truth rows
- runtime classification and suppression surfaces in `nightly_soak_report.json`

High-value fields:
- `runtime_classification_name`
- `runtime_primary_suppression_cause`
- `maker_block_reason_distribution`

Current hotspot family:
- none currently pinned as a maker-doctrine breach in the active specimens

Plain-English:
this is where we answer “was maker allowed to fire here?” before we answer
“should it have fired?”

### 4. Risk / Sizing / Quote-Quality Friction Path
Engineering meaning:
- This is the pre-submit friction layer where maker intent can fail closed due
  to sizing feasibility, quote-quality gates, replace-guard timing, and risk
  filters.
- It is often where healthy robots still show disappointing participation.

Primary surfaces:
- `nightly_soak_report.json`
- `FMA` harvested compression

High-value fields:
- `maker_quote_quality_skip_total_count`
- `maker_sizing_reject_total_count`
- `maker_replace_guard_min_rest_count`
- `maker_window_active_row_count`
- `maker_window_submit_rate`
- `maker_window_replace_guard_rate`
- `maker_window_quote_quality_skip_rate`
- `maker_quote_quality_skip_fill_probability_severity_bins`
- `maker_quote_quality_skip_queue_depth_severity_bins`
- `risk_reject_total_count`

Current hotspot family:
- low-price floor/cap feasibility seams
- quote-quality suppressions
- replace-guard friction
- within-window cadence pressure
- near-threshold vs hard-bad quote-quality split

Plain-English:
this is the chambering and trigger-discipline layer for the maker cannon.

### 4A. Fight-Admission Shadow Bridge
Engineering meaning:
- This layer emits raw per-side pre-fight rows from runtime, then lets the
  report/shop layer score them into `clean`, `borderline`, and `trash` classes.
- It is a selectivity research bridge, not a runtime authority surface.

Primary surfaces:
- `maker_fight_admission_shadow.jsonl`
- `maker_fight_admission_shadow_summary.json`
- `maker_fight_admission_calibration_audit.json`
- `maker_fight_admission_shadow_rows.jsonl`
- `maker_admission_target_side_summary.json`

High-value fields:
- `population_class_counts`
- `admission_class_counts`
- `complete_joined_count_by_class`
- `complete_bad_ratio_by_class`
- `multifill_incorrect_ratio_by_class`
- `dominant_driver_distribution`
- `maker_timing_band_class_distribution`
- `candidate_count_by_timing_band`
- `complete_bad_ratio_by_timing_band`
- `clean_but_bad_examples`
- `trash_but_okay_examples`

Current hotspot family:
- current `v1` selector is useful for identifying no-submit and weak-fight
  driver families
- it does **not** yet separate submitted `clean` fights from submitted bad
  fights strongly enough to earn runtime gating
- `size_liquidity_pressure` and `repeat_target_side_pressure` are the richest
  current clean-but-bad debt families
- the new cannon-shadow overlays now also tell us whether the sampled fights are
  even inside the intended maker-open `15s` / taker-handoff `7s` doctrine box, whether the book would satisfy
  the blueprint/default-lineage `1.5x` depth test against the `$100` doctrine
  shot, and whether secondary-oracle confirmation is present
- that depth signal is useful Packet 2 delta evidence, but it is not automatic
  current paper runtime authority because the live paper
  `selection_gate.min_depth_multiple` leaf is presently `1.5`
- the bounded late-window probe now does observe true `<=20s` cannon rows
- the fresh native transition specimen `ba5bfde7-01f1-4e34-9695-975b56661ad9`
  now reaches `27` observational full late-window cannon candidates, while the
  sibling native `30-45s` probe only reaches `1`
- meaning:
  - native current-code transition truth now points much more strongly at the
    `<=20s` Galaxy-cannon lane than at `30-45s`
  - this is still observational truth only because live maker runtime did not
    participate in those late-window rows
- the decontaminated archive reread now adds a second question:
  - if recovery posture were removed, was the underlying late-window market
    actually good enough for the Galaxy cannon?
- current answer:
  - transition and asia samples still fail the observational
    `insufficient_depth_multiple` shadow check and the
    `secondary_oracle_unknown` family before reaching any latent full-candidate
    state
  - treat the depth failure as blueprint-delta evidence first, not as proof that
    current paper runtime would reject on that leaf
  - peak-session truth is still too thin to judge honestly

Plain-English:
this is the pre-fight grading table. Right now it is good at telling us why the
weapon hesitated or skipped, but it is not yet good enough at telling us which
submitted fights will finish cleanly.

### 5. Submit / Ownership / Lifecycle Path
Engineering meaning:
- This layer begins once maker intent becomes an order submit.
- It tracks order identity, ownership, and whether the lifecycle stays
  coherent from submit through fill/cancel.

Primary surfaces:
- `order_lifecycle_audit.json`
- `outcome_truth_records.jsonl`

Raw event taxonomy note:
- current runtime submit/fill truth is emitted through generic lifecycle events
  like `order_submit`, `fill`, and `order_cancel`
- do **not** assume there will be a maker-prefixed `maker_order_submitted`
  event name in the raw stream when auditing maker participation

High-value fields:
- `lifecycle_order_submit_decision_missing_count`
- `lifecycle_edge_decision_submit_missing_count`
- `lifecycle_fill_without_submit_count`
- `maker_complete_record_count`
- `maker_incomplete_record_count`
- `maker_lifecycle_gap_class_distribution`

Current hotspot family:
- completion vs incomplete split
- no-fill incomplete accumulation

Plain-English:
this is where we make sure the maker cannon’s shells are real tracked shells,
not ghost rounds.

### 6. Fill-Event Execution Surface
Engineering meaning:
- This layer scores fill-event execution against the submit-side decision
  reference midpoint.
- It is a fill-event economics surface, not an order-level directional truth
  surface.

Primary surfaces:
- `execution_quality_decision_reference_lane_attribution`
- `nightly_soak_report.json`

High-value fields:
- `execution_capture_minus_adverse`
- `execution_realized_capture`
- `execution_adverse_selection`
- `execution_fills_scored`

Current hotspot family:
- execution can be favorable while the completed decision layer is still bad

Plain-English:
this tells us how well fills behaved after the shot was taken, not whether the
shot itself was wise.

### 7. Outcome-Truth Forensic Surface
Engineering meaning:
- This layer is the order-submit observational truth substrate.
- It classifies decision quality, execution quality, fill-count geometry,
  lifecycle geometry, and basis provenance on the maker outcome population.

Primary surfaces:
- `outcome_truth_audit.json`
- `outcome_truth_records.jsonl`
- `FMA` derived maker-forensic fields

High-value fields:
- `maker_complete_bad_ratio`
- `maker_incomplete_bad_ratio`
- `maker_multifill_complete_count`
- `maker_multifill_complete_incorrect_ratio`
- `maker_execution_rescue_overcome_count`
- `maker_same_target_repeat_cluster_count`
- `maker_complement_pair_cluster_count`
- `maker_complement_pair_cluster_decision_debt_sum`
- `maker_outcome_horizon_ms`
- `maker_reference_basis_summary`

Current hotspot family:
- completion bias
- multi-fill wounds
- same-target repeat clusters
- complement-pair clusters
- horizon/basis sensitivity

Plain-English:
this is the main autopsy table for maker pain.

### 8. Harvest / Compression / Shop-Tool Path
Engineering meaning:
- `FMA` is the shop compression layer that turns scattered report truth into a
  reusable engineering instrument.
- It does not replace the runtime or audit truth surfaces; it joins and labels
  them.

Primary surfaces:
- `scripts/bro_metric_harvest.py`
- `run_index.jsonl`
- `metric_catalog.json`
- `maker_taker_summary.csv`
- `anomaly_summary.json`
- `maker_research_pack.md`

High-value fields:
- `maker_truth_population_note`
- `maker_complete_bad_ratio_summary`
- `maker_multifill_complete_incorrect_ratio_summary`
- `maker_fill_count_quality_distribution`
- `maker_reference_basis_summary`

Current hotspot family:
- semantic misuse prevention
- cross-run comparison discipline

Plain-English:
this is the shop gauge cluster that keeps future-us from doing fresh manual
archaeology every time.

## Packet 2 Classification Overlay
| Surface family | Packet 2 class | What it can honestly say | What it must not claim | Current call |
| --- | --- | --- | --- | --- |
| market-reference substrate + required WS book truth | runtime owner-law | whether the maker row is authoritative enough to evaluate | the final economic verdict by itself | `KEEP` |
| decision-cycle and lifecycle truth | emitted/runtime truth and validator evidence | what maker saw, chose, submitted, and tracked | complete loss ownership without reconciliation | `KEEP` |
| current paper timing gate + secondary confirmation + `$100` shot + repeat-target calm | active paper runtime leaves | what current paper maker is currently allowed to do | whole-doctrine closure beyond the paper lane | `KEEP` |
| depth-multiple shadow signal | live runtime plus blueprint/default-lineage evidence | whether fights satisfy the stronger `1.5x` cannon story now enforced in current paper runtime | a full economics verdict by itself | `KEEP` |
| fill-event execution surface | supporting economics surface | how fills behaved after the shot | whether the shot itself was wise or whether losses are owned there alone | `KEEP` |
| `FMA` / harvest compression | support-only shop tool | compress report truth for reuse | act like BRO body steel or runtime authority | `KEEP BUT FENCE` |
| admission shadow + late-window probes | research bridge | classify and compare candidate fights | gate live runtime or certify live trust by themselves | `KEEP BUT FENCE` |
| drift-era `45-60s` / `50-60s` maker timing language | historical lineage | explain ancestry and old protection intent | reclaim present-tense authority | `HISTORICAL ONLY` |
| dust / recovery families + queue-pressure archive lineage | active cleanup families plus historical-only queue-pressure ancestry | show where compensator-fat may still live without reviving a cut branch | remain silently authoritative just because they exist | `KEEP BUT FENCE` |

## Truth Populations That Must Never Be Mixed Casually
1. `decision-cycle truth`
- Unit: evaluation rows / action rows
- Example surfaces: `maker_no_submit_total_count`, edge truth rows

2. `submit truth`
- Unit: order submits
- Example surfaces: `maker_submits`, `outcome_truth_records.jsonl`

3. `filled-order truth`
- Unit: orders with at least one fill
- Example surfaces: `maker_filled_orders`, `maker_fill_rate`

4. `fill-event truth`
- Unit: individual fills
- Example surfaces: `execution_quality_decision_reference_lane_attribution`,
  `maker_fills`

5. `complete-outcome truth`
- Unit: matured outcome records
- Example surfaces: `maker_complete_record_count`,
  `maker_complete_bad_ratio`

Plain-English:
if we mix these populations casually, we can talk ourselves into fixing the
wrong thing.

## Current Hotspot Families
### Completion Bias
- Complete maker outcomes skew materially worse than incomplete maker outcomes
  in the audited specimens.

### Multi-Fill Wound Geometry
- Completed multi-fill fights are disproportionately incorrect in the current
  evidence set.

### Same-Target Repeat Clustering
- Repeated target engagement is a real wound family and should be treated as a
  design seam, not just a report curiosity.

### Complement-Pair Clustering
- Mirrored complement-pair wounds are present in the current evidence.
- This is a bounded heuristic family, not canonical market ontology.

### Execution Rescue Limits
- Favorable execution can partially offset bad decisions but often does not
  fully rescue them.

### Basis / Horizon Sensitivity
- The current canonical maker outcome lens is valid doctrine, but its measured
  severity remains shaped by evaluation horizon and basis lineage.

## Packet 2 Family Notes
- `dust` and maker recovery machinery are not automatically condemned here.
- queue-pressure is already cut from current/live authority and appears here
  only as compatibility/replay ancestry.
- They are also not allowed to stay vague.
- Packet 2 now treats them as explicit family-trace patients that must prove
  whether they are real steel, fenced support, or scar tissue.

## How To Use This Schematic
Use this map when doing:
- maker debugging,
- maker tuning-hypothesis design,
- maker report interpretation,
- FMA extension work,
- future maker modularization,
- future money-touching lane schematics.

Use it together with the Packet 2 companion boards when the question is:
- who owns this surface,
- whether it is support-only,
- whether it is historical-only,
- or whether it is a scar-tissue candidate.

Do not use this map to:
- justify runtime tuning by itself,
- blur observational truth into canonical authority,
- infer new strategy law without a separate reviewed packet.

## Future Extension Rule
If the shop later builds equivalent schematics for:
- `Taker Katana`
- `Sniper Wakazashi`
- shared money-touching pathways

they should be built lane-by-lane after a serious evidence pass, not as one
giant abstract architecture poster.

Plain-English:
earn each blueprint from real steel, not vibes.
