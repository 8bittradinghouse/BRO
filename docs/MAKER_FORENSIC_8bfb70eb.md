# Maker Forensic: `8bfb70eb-c942-48eb-87ff-b9628b3098c7`

## Purpose
Preserve the first analysis-only maker shot-group / economics forensic for run `8bfb70eb-c942-48eb-87ff-b9628b3098c7` without tuning drift.

This note focuses on two questions:
1. what actually hurt the maker lane economically,
2. whether the current maker reporting surfaces are semantically clean enough to support the right next packet.

## Specimen Scope
- Run ID: `8bfb70eb-c942-48eb-87ff-b9628b3098c7`
- Primary artifacts:
  - `logs_exec/paper_universal/reports/8bfb70eb-c942-48eb-87ff-b9628b3098c7/outcome_truth_audit.json`
  - `logs_exec/paper_universal/reports/8bfb70eb-c942-48eb-87ff-b9628b3098c7/outcome_truth_records.jsonl`
  - `logs_exec/paper_universal/reports/8bfb70eb-c942-48eb-87ff-b9628b3098c7/nightly_soak_report.json`
  - `logs_exec/paper_universal/reports/8bfb70eb-c942-48eb-87ff-b9628b3098c7/edge_truth_audit.json`
- Comparison specimens:
  - `e675467e-368a-49db-bc03-f35c96aebba8`
  - `ed184f61-c453-4511-a5e5-3fa24271c191`

## Executive Truth
1. `VERIFIED`: `8bfb...` was a healthy robot with bad maker economics, not a broken robot hiding behind green wrappers.
2. `VERIFIED`: the completed maker subset was dominated by wrong fights:
   - complete records: `35`
   - incorrect complete decisions: `28`
   - correct complete decisions: `2`
   - neutral complete decisions: `5`
3. `VERIFIED`: execution quality on completed fills was favorable while decision quality was mostly bad.
4. `VERIFIED`: this pattern is bilateral across `BUY` and `SELL` and not confined to only one price regime.
5. `INFERRED`: the next best diagnosis lane is completion / fill-selection bias and maker decision-selection structure, not immediate maker threshold tuning.

## Final Truth-Hardening Surfaces
The final maker truth-hardening pass promoted the hidden wound geometry into machine-readable `FMA` fields for this specimen.

Pinned `8bfb...` surfaces:
- `maker_complete_record_count = 35`
- `maker_incomplete_record_count = 26`
- `maker_complete_bad_ratio = 0.8`
- `maker_incomplete_bad_ratio = 0.23076923076923078`
- `maker_multifill_complete_count = 22`
- `maker_multifill_complete_incorrect_ratio = 0.9090909090909091`
- `maker_execution_rescue_overcome_count = 10`
- `maker_fills_per_filled_order = 2.2`
- `maker_same_target_repeat_cluster_count = 17`
- `maker_complement_pair_cluster_count = 8`
- `maker_complement_pair_cluster_decision_debt_sum = -104.36126999999996`
- `maker_outcome_horizon_ms = 5000`
- `maker_eval_basis_requires_reconstructed_midpoint_flag = true`

Lifecycle gap class distribution:
- `complete_multifill = 22`
- `complete_single_fill = 13`
- `no_fill_incomplete = 26`

`VERIFIED`: this specimen had no `partial_fill_incomplete` maker records.

Plain-English:
the final pass proved the wound is not just “bad complete ratio.”
It is also:
- heavy multi-fill concentration,
- repeated target clustering,
- mirrored complement-pair wounds,
- and a truth lens that must be read with basis/horizon discipline.

## Core Wound Profile
### 1. Completed fights were mostly the wrong fights
From `outcome_truth_records.jsonl`:
- total records: `61`
- complete records: `35`
- incomplete lifecycle records: `26`

Decision quality split:
- all records:
  - `correct=18`
  - `incorrect=34`
  - `neutral=9`
- complete records:
  - `correct=2`
  - `incorrect=28`
  - `neutral=5`
- incomplete lifecycle records:
  - `correct=16`
  - `incorrect=6`
  - `neutral=4`

`VERIFIED`: many correct or neutral decisions remained incomplete, while many incorrect decisions matured into completed scored fills.

`INFERRED`: the economic wound is not only "signal bad." It also looks like a completion bias where the wrong engagements are the ones most likely to mature into scoreable completed maker outcomes.

### 2. Execution was favorable but could not save the decision layer
Complete-record aggregate sums:
- `decision_component_x_size_sum = -196.1662825`
- `execution_component_x_size_sum = +137.1666`
- `edge_realized_x_size_sum = -58.9996825`

From `outcome_truth_audit.json`:
- `edge_expected_mean = -0.014473514295803715`
- `edge_realized_mean = -0.005494882263510608`
- `slippage_mean = -0.02693368916506081`
- `adverse_selection_mean = 0.005494882263510608`

`VERIFIED`: the maker lane earned favorable execution on completed fills but still lost because the decision layer dug a deeper hole than execution could fill.

### 3. The worst completed damage was mirrored, not isolated
The heaviest completed losses showed up as mirrored `BUY` / `SELL` fights around paired regimes rather than one obviously broken one-sided blade.

Examples already pinned during the packet:
- `paper-order-14`
- `paper-order-15`
- `paper-order-46`
- `paper-order-58`
- `paper-order-59`

`VERIFIED`: the wound is not obviously "BUY bad" or "SELL bad."

`INFERRED`: this looks more like a broad maker fight-selection problem than a single broken side or one isolated market shard.

## Bucket And Regime Truth
### Completed edge buckets
For completed records:
- `0p05_0p10`
  - count `8`
  - mean decision component `-0.06`
  - mean execution component `+0.066764`
  - mean realized `+0.006764`
- `gt_0p20`
  - count `25`
  - decision quality `{incorrect:22, neutral:3}`
  - mean decision component `-0.0222`
  - mean execution component `+0.013057`
  - mean realized `-0.009143`
- `le_0p05`
  - count `2`
  - decision quality `{incorrect:2}`
  - mean decision component `-0.05`
  - mean execution component `+0.041069`
  - mean realized `-0.008931`

`VERIFIED`: the strongest completed edge bucket by absolute threshold (`gt_0p20`) was also the ugliest on decision quality in this specimen.

### Price regimes
Complete-record price grouping:
- low `<0.15`
  - `9` records
  - `8` incorrect
- middle `0.15..0.85`
  - `14` records
  - `10` incorrect
- high `>0.85`
  - `12` records
  - `10` incorrect

`VERIFIED`: the wound was not confined to tail pricing only.

## Cross-Run Comparator Truth
### `8bfb...`
- complete records: `35`
- bad complete ratio: `0.8000`
- maker submits: `61`
- maker filled orders: `35`
- maker fills: `77`
- fills per filled order: `2.2`
- sizing rejects: `27`
- replace-guard watch count: `91`

### `e675...`
- complete records: `8`
- bad complete ratio: `1.0000`
- maker submits: `14`
- maker filled orders: `8`
- maker fills: `12`
- fills per filled order: `1.5`
- sizing rejects: `0`
- replace-guard watch count: `16`

### `ed184...`
- complete records: `5`
- bad complete ratio: `0.6000`
- maker submits: `7`
- maker filled orders: `5`
- maker fills: `9`
- fills per filled order: `1.8`
- sizing rejects: `17`
- replace-guard watch count: `4`

`VERIFIED`: `8bfb...` is not an isolated alien pattern. `e675...` also showed `incorrect_decision_good_execution` dominance, though with a much smaller specimen and shallower loss surface.

`INFERRED`: the current maker issue likely has a broader structural component, with `8bfb...` acting as a larger, uglier specimen rather than a one-off mutation.

## Semantic Runtime Reporting Truth
### 1. `maker_fill_rate` is not a fill-event rate
In `nightly_soak_report.py`, `execution_paths.maker_fill_rate` is:
- `maker_filled_orders / maker_submits`
- not `maker_fills / maker_submits`

For `8bfb...`:
- `maker_submits = 61`
- `maker_filled_orders = 35`
- `maker_fills = 77`
- reported `maker_fill_rate = 35 / 61 = 0.5737704918032787`

`VERIFIED`: this surface is order-completion rate, not fill-event success rate.

### 2. Decision-reference lane attribution is fill-event economics, not outcome-truth
`execution_quality_decision_reference_lane_attribution` in `nightly_soak_report.py`:
- classifies submits by lane,
- scores each fill event against the submit-side `decision_reference_midpoint`,
- aggregates notional and immediate capture/adverse selection,
- declares its own claim boundary as report-only.

For `8bfb...` maker:
- `submit_count = 61`
- `fill_event_count = 77`
- `filled_order_count = 35`
- `notional = 3876.7180650000014`
- `immediate_capture_minus_adverse = 137.16659999999996`
- `immediate_net_to_notional_ratio = 0.03538214481944792`

`VERIFIED`: this is a useful fill-event execution surface, but it is not the same truth layer as the order-level outcome audit.

### 3. Outcome truth is order-submit observational truth with incomplete records kept in the pool
`outcome_truth_audit.py` emits one record per `order_submit` and leaves no-fill records as:
- `outcome_truth_status = unknown_incomplete_lifecycle`
- `execution_quality = unknown`

For `8bfb...`:
- total outcome records: `61`
- complete outcome records: `35`
- filled total: `35`
- attribution usability ratio: `0.5737704918032787`

`VERIFIED`: outcome truth distributions are not silently limited to only complete filled orders. They include incomplete lifecycle submits unless the consumer explicitly filters.

### 4. Maker action rows are decision-cycle truth, not submit-count truth
In `edge_truth`:
- maker action rows: `57`
- maker blocked rows: `2018`
- maker rows total: `2075`

But in `execution_paths`:
- maker submits: `61`

`VERIFIED`: these are not one-to-one metrics.

`INFERRED`: some maker decision cycles can lead to multiple submit events, so `action_rows` must not be treated as a submit-count proxy.

### 5. Outcome record identity field is `order_submit_id`, not `order_id`
The normalized outcome records use:
- `order_submit_id`

They do **not** surface a plain `order_id` key.

`VERIFIED`: this is a naming seam only, not a truth-integrity failure.

`INFERRED`: any future tooling that expects `order_id` in `outcome_truth_records.jsonl` will silently thin itself unless it maps `order_submit_id` explicitly.

### 6. Current maker outcome basis is uniform but more reconstructed than it first appears
For `8bfb...`:
- `decision_reference_basis_distribution`
  - `direct_book_midpoint = 61`
- `eval_reference_basis_distribution`
  - `edge_market_midpoint_series = 61`

`VERIFIED`: every current maker outcome record in this specimen uses:
- direct midpoint at decision time,
- reconstructed target-ref midpoint series at eval time.

`VERIFIED`: this is deterministic and disclosed by claim boundary, but it is not a raw-book-to-raw-book comparison.

`INFERRED`: anyone skimming only the high-level policy names could read this as more direct than it really is.

### 7. The fixed `5000ms` horizon is not a harmless detail
Red-team sensitivity probe on complete `8bfb...` records:
- `1000ms`
  - complete decision split: `correct=2`, `incorrect=14`, `neutral=19`
  - decision debt sum: `-83.418535`
- `3000ms`
  - `correct=0`, `incorrect=29`, `neutral=6`
  - decision debt sum: `-178.185395`
- `5000ms`
  - `correct=2`, `incorrect=28`, `neutral=5`
  - decision debt sum: `-196.1662825`
- `10000ms`
  - `correct=4`, `incorrect=29`, `neutral=2`
  - decision debt sum: `-197.008865`

`VERIFIED`: the specimen still looks bad under the canonical horizon, but the severity is horizon-sensitive.

`INFERRED`: the `5000ms` lens is revealing something real, but it is also materially shaping how wrong the maker looks.

### 8. Some of the ugliest wounds are mirrored complement-pair hits
Heuristic mirrored-pair detector found complete incorrect pairs where:
- `mid_price_decision` sums to `~1.0`
- `mid_price_eval` sums to `~1.0`
- `edge_expected` sums to `~0.0`
- fill sizes match
- decision debt magnitudes match closely

Examples:
- `paper-order-14` + `paper-order-15`
- `paper-order-21` + `paper-order-22`
- `paper-order-25` + `paper-order-26`
- `paper-order-36` + `paper-order-37`

Final hardening pass result:
- complement-pair cluster count `8`
- combined selected-cluster decision debt `-104.36126999999996`

Largest pinned live example:
- `paper-order-14` + `paper-order-15`
  - `combined_decision_debt_sum = -61.850749999999955`
  - `decision_mid_sum = 1.0`
  - `eval_mid_sum = 1.0`
  - `fill_count_a = 4`
  - `fill_count_b = 4`

`VERIFIED`: at least part of the maker wound arrives as mirrored pair hits, not just isolated single-order misses.

`INFERRED`: some one-move market shocks are being expressed as twin outcome wounds across complement-looking targets.

### 9. Multi-fill completed fights are materially worse
Final hardening pass on complete `8bfb...` records:
- fill-count `1`
  - `13` complete records
  - `8` incorrect
- fill-count `2`
  - `8` complete records
  - `6` incorrect
- fill-count `3`
  - `8` complete records
  - `8` incorrect
- fill-count `4`
  - `6` complete records
  - `6` incorrect

`VERIFIED`: every `3-fill` and `4-fill` completed maker order in this specimen was incorrect.

`INFERRED`: fill multiplication is not neutral geometry here; it looks like a real wound family that future tuning must account for.

## What This Means
1. `VERIFIED`: the current maker reporting circuit is mostly truthful, but several important surfaces live at different semantic layers.
2. `VERIFIED`: if we compare:
   - fill-event execution economics,
   - order-completion rate,
   - and order-level outcome truth
   as though they were the same population,
   we will misread the weapon.
3. `VERIFIED`: the new `FMA` surfaces now make completion-bias, multi-fill wound geometry, lifecycle classes, target clustering, and complement-pair structure machine-readable.
4. `INFERRED`: the highest-ROI next analysis is no longer “find hidden structure.” It is “use the new structure to form bounded tuning hypotheses.”

## Recommended No-Drift Next Moves
1. `VERIFIED_RECOMMENDATION`: preserve semantic claim-boundary discipline in `FMA`.
   - Do not collapse fill-event economics into order-level outcome truth.
   - Do not label `maker_fill_rate` as though it were fill-event rate.
2. `VERIFIED_RECOMMENDATION`: if a tuning-design packet opens next, use the new `FMA` maker forensics first:
   - complete vs incomplete outcome proportions,
   - multi-fill wound geometry,
   - repeated-target clustering,
   - complement-pair clustering,
   - execution-rescue coverage,
   - no-submit and sizing-reject overlays.
3. `INFERRED_RECOMMENDATION`: tuning should still open with bounded hypothesis design, not threshold edits from one field alone.

## Final Forensic Label
`VERIFIED_OPEN`: `8bfb...` is a mechanically healthy maker specimen with a real decision-selection wound and a meaningful completion-bias signature.

`VERIFIED_OPEN`: the maker reporting circuit is largely honest, but it contains multiple semantic layers that must stay separated if future diagnostics are going to stay sharp.
