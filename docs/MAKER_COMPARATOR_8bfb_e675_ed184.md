# Maker Comparator: `8bfb...` vs `e675...` vs `ed184...`

## Purpose
Preserve the no-drift cross-run maker comparator that follows the `8bfb...` forensic.

This packet is analysis-only.

Goal:
- determine whether the `8bfb...` maker wound is isolated or structurally recurring,
- separate recurring maker weakness from specimen-specific ugliness,
- keep report semantics clean enough that later tuning packets do not optimize against the wrong scoreboards.

## Specimens
- `8bfb70eb-c942-48eb-87ff-b9628b3098c7`
- `e675467e-368a-49db-bc03-f35c96aebba8`
- `ed184f61-c453-4511-a5e5-3fa24271c191`

## Comparator Table
### Core anatomy
- `8bfb...`
  - records total `61`
  - complete `35`
  - incomplete `26`
  - complete ratio `0.5738`
  - maker submits `61`
  - maker filled orders `35`
  - maker fills `77`
  - fills per filled order `2.2`
  - maker fill rate `0.5737704918032787`
  - attribution usability ratio `0.5737704918032787`
- `e675...`
  - records total `14`
  - complete `8`
  - incomplete `6`
  - complete ratio `0.5714`
  - maker submits `14`
  - maker filled orders `8`
  - maker fills `12`
  - fills per filled order `1.5`
  - maker fill rate `0.5714285714285714`
  - attribution usability ratio `0.5714285714285714`
- `ed184...`
  - records total `7`
  - complete `5`
  - incomplete `2`
  - complete ratio `0.7143`
  - maker submits `7`
  - maker filled orders `5`
  - maker fills `9`
  - fills per filled order `1.8`
  - maker fill rate `0.7142857142857143`
  - attribution usability ratio `0.7142857142857143`

### Complete decision-quality split
- `8bfb...`
  - `correct=2`
  - `incorrect=28`
  - `neutral=5`
  - bad complete ratio `0.8000`
- `e675...`
  - `correct=0`
  - `incorrect=8`
  - `neutral=0`
  - bad complete ratio `1.0000`
- `ed184...`
  - `correct=2`
  - `incorrect=3`
  - `neutral=0`
  - bad complete ratio `0.6000`

### Incomplete decision-quality split
- `8bfb...`
  - `correct=16`
  - `incorrect=6`
  - `neutral=4`
- `e675...`
  - `correct=4`
  - `incorrect=0`
  - `neutral=2`
- `ed184...`
  - `correct=0`
  - `incorrect=2`
  - `neutral=0`

### Final hardening anatomy
- `8bfb...`
  - `maker_multifill_complete_count = 22`
  - `maker_multifill_complete_incorrect_ratio = 0.9091`
  - `maker_same_target_repeat_cluster_count = 17`
  - `maker_complement_pair_cluster_count = 8`
  - `maker_fills_per_filled_order = 2.2`
- `e675...`
  - `maker_multifill_complete_count = 4`
  - `maker_multifill_complete_incorrect_ratio = 1.0`
  - `maker_same_target_repeat_cluster_count = 3`
  - `maker_complement_pair_cluster_count = 0`
  - `maker_fills_per_filled_order = 1.5`
- `ed184...`
  - `maker_multifill_complete_count = 3`
  - `maker_multifill_complete_incorrect_ratio = 1.0`
  - `maker_same_target_repeat_cluster_count = 1`
  - `maker_complement_pair_cluster_count = 0`
  - `maker_fills_per_filled_order = 1.8`

## Recurring Structures
### 1. Completed subsets skew worse than incomplete subsets
`VERIFIED`:
- `8bfb...` complete records were mostly incorrect, while incomplete records skewed much more correct.
- `e675...` complete records were entirely incorrect, while incomplete records were all `correct` or `neutral`.
- `ed184...` was the mildest of the three, but still had more incorrect than correct complete records.

`INFERRED`: this is the strongest recurring structure in the current maker specimens.

Plain-English:
the fights that mature into completed scored maker outcomes are often worse than the fights that die incomplete.

### 1b. Multi-fill completed fights skew even worse
`VERIFIED`:
- `8bfb...` complete multi-fill outcomes were `20/22` incorrect
- `e675...` complete multi-fill outcomes were `4/4` incorrect
- `ed184...` complete multi-fill outcomes were `3/3` incorrect

`INFERRED`: multi-fill geometry is not just a side note. It looks like one of the clearest reusable maker wound families across the audited specimens.

### 2. Execution helps, but decision debt is still larger
Complete-record aggregate sums:
- `8bfb...`
  - decision debt `-196.1662825`
  - execution rescue `+137.1666`
  - realized net `-58.9996825`
- `e675...`
  - decision debt `-52.54587`
  - execution rescue `+41.583425`
  - realized net `-10.962445`
- `ed184...`
  - decision debt `-7.907055`
  - execution rescue `+4.475835`
  - realized net `-3.43122`

`VERIFIED`: all three specimens show the same broad shape:
- execution on completed fills is useful,
- but it is not large enough to offset wrong fight selection.

### 3. The wound is bilateral, not one-sided
`VERIFIED`:
- `8bfb...` complete losses split across `BUY` and `SELL` almost symmetrically.
- `e675...` was exactly symmetric in the completed subset.
- `ed184...` was also mixed across both sides.

`INFERRED`: there is no strong evidence yet that one side alone is the culprit.

### 4. The wound is not one single price regime
`VERIFIED`:
- `8bfb...` carried incorrect complete records in low, middle, and high price regimes.
- `e675...` also spread incorrect complete records across low, middle, and high.
- `ed184...` was smaller and mostly mid/high.

`INFERRED`: this does not look like a single tail-only pathology.

## Edge-Bucket Comparison
### `8bfb...`
- complete buckets:
  - `0p05_0p10`: `8` records, mixed but net small positive realized
  - `gt_0p20`: `25` records, `22` incorrect, main negative driver
  - `le_0p05`: `2` records, both incorrect, materially negative
- incomplete buckets:
  - `0p05_0p10`: mostly correct
  - `gt_0p20`: mixed
  - `le_0p05`: both correct

### `e675...`
- complete buckets:
  - `0p10_0p20`: `6` records, all incorrect
  - `le_0p05`: `2` records, all incorrect
- incomplete buckets:
  - `gt_0p20`: `2` correct, `2` neutral
  - `le_0p05`: `2` correct

### `ed184...`
- complete buckets:
  - `0p10_0p20`: `5` records, `3` incorrect, `2` correct
- incomplete buckets:
  - `0p10_0p20`: `2` incorrect

## Structural Read
`VERIFIED`:
- `8bfb...` and `e675...` both show a strong pattern where the completed population is worse than the incomplete population.
- `e675...` especially suggests that stronger-looking maker opportunities can remain incomplete while weaker/wronger ones mature into completed outcomes.
- `8bfb...` is larger and uglier, but it still fits the same broad family.

`INFERRED`:
- the most useful current hypothesis is not "maker cannot see edges."
- the stronger hypothesis is "maker completion / fill-selection structure is favoring the wrong engagements too often."

## Overlay Factors
### No-submit / guard pressure
- `8bfb...`
  - `replace_guard_min_rest=91`
  - `quote_quality_skip_fill_probability=18`
  - `quote_quality_skip_queue_depth=35`
- `e675...`
  - `replace_guard_min_rest=16`
  - `quote_quality_skip_fill_probability=2`
  - `quote_quality_skip_queue_depth=0`
- `ed184...`
  - `replace_guard_min_rest=4`
  - `quote_quality_skip_fill_probability=11`
  - `quote_quality_skip_queue_depth=8`

### Sizing-reject pressure
- `8bfb...`
  - `maker_sizing_rejects=27`
  - `maker_min_notional_max_shares_conflicts=27`
- `e675...`
  - `maker_sizing_rejects=0`
  - `maker_min_notional_max_shares_conflicts=0`
- `ed184...`
  - `maker_sizing_rejects=17`
  - `maker_min_notional_max_shares_conflicts=17`

`VERIFIED`: reject and guard overlays vary by specimen, but the broad completed-wrong-fight pattern still appears in both high-reject and zero-reject examples.

`INFERRED`: this means reject pressure alone is not the whole wound.

## Reporting-Semantic Guardrails
This packet also re-confirms the maker semantic split already pinned in the `8bfb...` forensic:

1. `maker_fill_rate` is order-completion rate.
2. `execution_quality_decision_reference_lane_attribution` is fill-event execution economics with a report-only claim boundary.
3. `outcome_truth_audit` is order-submit observational truth and includes incomplete lifecycle records unless filtered.
4. `maker action rows` are decision-cycle truth, not submit-count truth.

`VERIFIED`: comparing those surfaces as though they represent the same population will misread the weapon.

## Red-Team Findings
### 1. The canonical maker lens is horizon-sensitive
Exploratory probe using the audit engine under controlled monkeypatched canonical horizons:

- `8bfb...`
  - `1000ms`: complete split `2 correct / 14 incorrect / 19 neutral`, decision debt `-83.418535`
  - `3000ms`: `0 / 29 / 6`, decision debt `-178.185395`
  - `5000ms`: `2 / 28 / 5`, decision debt `-196.1662825`
  - `10000ms`: `4 / 29 / 2`, decision debt `-197.008865`
- `e675...`
  - `1000ms`: complete split `0 / 1 / 7`, decision debt `-0.123435`
  - `3000ms`: `0 / 8 / 0`, decision debt `-49.640175`
  - `5000ms`: `0 / 8 / 0`, decision debt `-52.54587`
  - `10000ms`: `0 / 8 / 0`, decision debt `-66.07471`
- `ed184...`
  - `1000ms`: complete split `2 / 1 / 2`, decision debt `+15.305355`
  - `3000ms`: `0 / 3 / 2`, decision debt `-5.730595`
  - `5000ms`: `2 / 3 / 0`, decision debt `-7.907055`
  - `10000ms`: `0 / 5 / 0`, decision debt `-16.029975`

`VERIFIED`: the current outcome-truth story is materially shaped by the fixed-horizon lens.

`VERIFIED`: `8bfb...` remains bad under the canonical lens, but the exact severity is not horizon-invariant.

`INFERRED`: any future maker tuning packet that pretends the `5000ms` result is the only possible truth would be below house standard.

### 2. The audit engine hard-locks the horizon by doctrine
`outcome_truth_audit.py` rejects policy horizons that differ from `CANONICAL_EVALUATION_HORIZON_MS`.

`VERIFIED`: naïve sensitivity testing through the stock CLI is impossible by design.

`INFERRED`: this is good canonical discipline, but it also means deeper red-team analysis has to go underneath the wrapper on purpose.

### 3. Current maker eval references are all reconstructed target-ref series
Across all three comparator specimens:
- decision basis: `direct_book_midpoint`
- eval basis: `edge_market_midpoint_series`

`VERIFIED`: the current maker outcome classifier is deterministic and basis-disclosed.

`VERIFIED`: it is not a direct book-to-book eval path in these specimens.

`INFERRED`: the policy names `eval_reference_selection = latest_book_top_at_or_before_eval_ts` and `decision_reference_selection = latest_book_top_at_or_before_decision_ts` can read cleaner than the actual recovered maker path if someone ignores `reference_recovery_priority` and basis distributions.

### 4. Mirrored complement-pair losses are a real structural family in `8bfb...`
The worst `8bfb...` wounds include mirrored pairs whose decision mids sum to `1.0` and whose eval mids sum to `1.0`.

`VERIFIED`: one move in the market can show up as twin decision wounds across complement-looking targets.

`INFERRED`: some of the maker loss surface should be analyzed as pair-cluster behavior, not only as independent single-order errors.

### 5. The final pass turned the hidden structures into reusable flags
`VERIFIED`:
- the new `FMA` harvest now carries:
  - lifecycle gap classes,
  - fill-count quality distributions,
  - execution-rescue coverage,
  - same-target repeat clusters,
  - complement-pair cluster counts/examples,
  - basis provenance and horizon context.

`INFERRED`: the next lane no longer needs to spend elite effort rediscovering structure. It can open directly on bounded tuning hypotheses built from these flags.

## Best Current Hypotheses
1. `INFERRED`: the maker lane has a recurring completion-bias problem where the wrong fights disproportionately mature into completed outcomes.
2. `INFERRED`: multi-fill completed fights are one of the clearest recurring wound families.
3. `INFERRED`: the wound is broader than one side, one tail, or one reject family.
4. `INFERRED`: `8bfb...` is a scaled-up bad specimen of a recurring family rather than an isolated freak event.

## Recommended No-Drift Next Moves
1. `VERIFIED_RECOMMENDATION`: preserve the new semantic reading surfaces in the atlas and `FMA` outputs so future analysis cannot confuse:
   - order-completion truth,
   - fill-event execution truth,
   - order-level outcome truth.
2. `VERIFIED_RECOMMENDATION`: if a tuning-design packet opens next, center it on the new reusable maker flags:
   - complete vs incomplete outcome geometry,
   - multi-fill wound geometry,
   - same-target repeat clustering,
   - complement-pair clustering,
   - execution-rescue coverage.
3. `INFERRED_RECOMMENDATION`: do not tune maker thresholds from one metric alone; use the new machine-readable structure as the starting hypothesis rack.

## Final Comparator Label
`VERIFIED_OPEN`: the current maker issue is structurally recurring across multiple specimens.

`VERIFIED_OPEN`: `8bfb...` is worse in scale, but the family resemblance to `e675...` and `ed184...` is real enough that a blind one-run patch would be below house standard.
