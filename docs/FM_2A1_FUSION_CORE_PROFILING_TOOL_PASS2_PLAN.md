# FM-2A1 Fusion Core Profiling Tool — Skunkworks Pass 2 Plan

## Purpose
`FM-2A1` foundation metal is good enough to use.
Pass 2 is about making it harder to misread, harder to overclaim from, and
harder to break with ugly stock.

Plain-English:
the first build works.
This pass makes it more honest, more industrial, and more custom-shop tight.

## Evidence Anchors
Fresh specimen used for this plan:
- run id: `22daa81f-a3c6-45ee-9086-897a953dde10`
- validation:
  - `logs_exec/paper_universal/reports/22daa81f-a3c6-45ee-9086-897a953dde10/canonical_paper_validation.json`
- fresh single-run `FMA` bundle:
  - `logs_exec/paper_universal/forge_masters_archive_run_22daa81f-a3c6-45ee-9086-897a953dde10`
- fresh specimen lathe outputs:
  - `logs_exec/paper_universal/fusion_core_profile_run_22daa81f-a3c6-45ee-9086-897a953dde10`
- corpus baseline lathe outputs:
  - `logs_exec/paper_universal/fusion_core_profile_latest`

## Verified Foundation Truth
- `VERIFIED`: the fresh 10-minute specimen passed clean at the paper stage.
- `VERIFIED`: `FMA` harvested the fresh single-run bundle cleanly and emitted a
  valid manifest.
- `VERIFIED`: `FM-2A1` resolved that bundle as `specimen` mode automatically.
- `VERIFIED`: specimen mode emitted no `strong` grades.
- `VERIFIED`: maker remained `full_depth`, taker stayed `bounded_depth`, and
  sniper stayed bounded and dormant.
- `VERIFIED`: profile diff correctly showed specimen-vs-corpus downgrades rather
  than silently pretending one-run truth was corpus-grade truth.

## Verified Seams Worth Hardening
### 1. Downgrade honesty is weaker than grade honesty
Evidence:
- specimen stability matrix shows only `bounded`, `thin`, and `suppressed`
  grades
- several thin/suppressed profiles carry empty:
  - `downgrade_reasons`
  - `suppression_flags`

Why it matters:
- the grade is honest
- the explanation under the grade is thinner than it should be

Plain-English:
the lathe is refusing to overclaim, but it is not yet telling us sharply enough
why it refused.

### 2. Specimen-vs-corpus diff works, but it needs semantic coaching
Evidence:
- specimen diff against corpus shows widespread expected grade downgrades
  because one-run truth is naturally weaker than corpus truth

Why it matters:
- the diff is technically correct
- future operators could still overread the downgrade list as a regression
  instead of a mode mismatch effect

Plain-English:
the dial indicator works, but it needs a better label on what kind of metal it
is comparing.

### 3. Friction accounting is honest but not obvious enough
Evidence from the fresh run:
- live event stream produced `18` local submit-reject events
- those were paired per-side rejects:
  - `6` `sizing_reject` pairs
  - `3` `quote_quality_skip_queue_depth` pairs
  - `4` `quote_quality_skip_fill_probability` pairs
- harvested specimen surfaces summarize:
  - `maker_submits=9`
  - `maker_quote_quality_skip_total_count=6`
  - `maker_sizing_reject_total_count=3`

Why it matters:
- this appears to be a population-accounting difference:
  - event stream = per-side local rejects
  - harvested friction = decision-row / submit-row populations
- the truth is probably honest
- the semantic boundary is not explicit enough

Plain-English:
the numbers are likely counting different things on purpose, but future-us
should not have to reverse-engineer that every time.

### 4. Lifecycle labeling is stronger than the raw field support underneath it
Evidence:
- fresh specimen `outcome_truth_records.jsonl` had `9` records
- all `lifecycle_completeness` fields were `null`
- lathe still produced meaningful complete/incomplete geometry by using:
  - fill presence
  - decision/execution components

Why it matters:
- the derived geometry is useful
- the current profile wording can still sound more canonical than it really is

Plain-English:
the lathe is inferring lifecycle shape from the metal, not reading a perfect
factory stamp.
That should be made explicit.

### 5. Runtime pressure is underrepresented in the current profile family set
Evidence from the fresh run:
- `valuation_degraded=112`
- `held_valuation_rest_fallback_attempted=50`
- `held_valuation_rest_fallback_applied=50`
- final `valuation_bruise_state=recovered_clean`

Why it matters:
- the final bruise state looks clean
- the live run still experienced meaningful valuation-pressure churn
- current candidate blanks do not surface that pressure as a first-class
  profile family

Plain-English:
the bruise healed by the end, but the bloodstream still told us the system was
working hard to stay clean.

### 6. Zero-record suppressions need explicit cause tags
Evidence:
- specimen taker profile suppressed with `sample_count=0`
- specimen multifill maker profile suppressed with `sample_count=0`
- current explainability is too generic

Why it matters:
- `suppressed because no eligible records existed`
  is different from
- `suppressed because records existed but semantics were incompatible`

Plain-English:
“nothing there” and “unsafe to trust” are not the same failure class.

## Pass 2 Objectives
1. Make downgrade and suppression reasons first-class machine-readable truth.
2. Make specimen-vs-corpus interpretation safer and more explicit.
3. Promote friction population accounting into explicit semantics.
4. Make derived lifecycle geometry provenance visible.
5. Add runtime-pressure profile hooks for recovered-clean but noisy runs.
6. Abuse-test bundle/manifest handling harder.

## Planned Workstreams
### Workstream A — Contract and Snapshot Hardening
Add stronger contract surfaces:
- `bundle_contract_findings`
- `manifest_derivation_reason`
- `snapshot_integrity_class`
- `deep_artifact_coverage_summary`

Add uglier bundle tests:
- manifest missing
- manifest/file hash mismatch
- partial copied bundle
- deep artifact missing while run index claims depth

Plain-English:
before the lathe shapes stock, it should be better at telling us whether the
crate of stock is complete, legacy, or sketchy.

### Workstream B — Grade, Suppression, and Explainability Hardening
Require every non-strong profile to emit explicit machine-readable causes:
- `mode_cap_specimen_only`
- `sample_count_below_bounded_floor`
- `zero_eligible_records`
- `heuristic_only_population`
- `basis_incompatibility`
- `horizon_incompatibility`
- `lane_depth_cap`

Add:
- `downgrade_reason_codes`
- `suppression_reason_codes`
- `grade_rationale_summary`

Plain-English:
no more quiet shrugging.
If the lathe downgrades a profile, it should say why in steel, not vibes.

### Workstream C — Population Accounting Hardening
Add explicit dual-surface friction accounting:
- `event_side_reject_count`
- `decision_row_reject_count`
- `submit_row_reject_count`
- `population_accounting_note`

Add a compact population explainer for friction profiles:
- what was counted
- at what level
- why counts can differ from raw reject events

Plain-English:
we need the tool to say whether it counted raw sparks, paired tool passes, or
 finished cut attempts.

### Workstream D — Lifecycle Geometry Provenance Hardening
Split lifecycle meaning into:
- canonical lifecycle field support
- derived lifecycle geometry support

Add explicit flags:
- `lifecycle_basis=canonical_field`
- `lifecycle_basis=derived_fill_geometry`
- `lifecycle_basis=mixed`

Add warnings when derived geometry is used:
- especially for complete vs incomplete wound families

Plain-English:
if the lathe inferred the part shape from the cut marks, it should say that.

### Workstream E — Runtime Pressure Profile Hooks
Add a bounded new profile family class for recovered-clean pressure:
- `valuation_pressure`
- optional later sibling:
  - `fallback_recovery_pressure`

Initial signals:
- valuation degraded event count
- fallback attempt/apply counts
- recovered-clean final state
- held-unpriceable escalation absence/presence

Important limit:
- these stay profile/diagnostic surfaces only
- no runtime behavior changes

Plain-English:
we want the lathe to notice when the system stayed healthy by working hard under
the hood.

### Workstream F — Diff Semantics Hardening
Improve `profile_diff` so it knows what kind of comparison it is doing:
- specimen vs corpus
- corpus vs corpus
- specimen vs specimen

Add:
- `comparison_class`
- `expected_mode_cap_downgrades`
- `regression_candidate_changes`

Plain-English:
not every downgrade is a regression.
Some are just different classes of stock being compared.

## Low-Cost Support Tooling Worth Adding
- `fusion_core_bundle_doctor`
  - read-only contract probe for ugly bundles
- `fusion_core_grade_audit`
  - checks that every non-strong profile has explicit rationale codes
- `fusion_core_population_accounting_note`
  - compact JSON or markdown explainer emitted alongside friction profiles

## Test Plan
### Contract abuse tests
- manifest missing
- manifest schema mismatch
- manifest hash mismatch
- run-count mismatch across bundle files
- missing deep artifact with maker full-depth request

### Grade honesty tests
- specimen mode never emits `strong`
- suppressed profiles must have suppression reason codes
- thin profiles must have downgrade reason codes
- zero-record suppressions must distinguish:
  - zero eligible records
  - incompatible semantics

### Population accounting tests
- paired per-side reject events collapse into explicit decision-row accounting
- friction profile explains why raw event count differs from summarized count

### Lifecycle provenance tests
- derived lifecycle basis is explicitly tagged when canonical completeness fields
  are absent
- complete/incomplete families remain stable on current specimen fixtures

### Runtime pressure tests
- recovered-clean run with high valuation-pressure churn surfaces pressure
  profile
- clean calm run does not overclaim pressure

### Diff semantics tests
- specimen-vs-corpus diff labels expected mode-cap downgrades
- real unexpected grade loss still shows as regression candidate

## Acceptance Criteria
- every downgraded or suppressed profile explains itself clearly in machine
  readable form
- friction counts can be reconciled across raw event and summarized populations
- lifecycle geometry provenance is explicit
- runtime pressure can surface even when final bruise state is recovered-clean
- profile diff becomes safer for specimen-vs-corpus comparisons
- no runtime, strategy, wallet-authority, or live execution behavior changes

## Non-Goals
- no live policy tuning
- no config threshold changes
- no strategy redesign
- no coupling `FM-2A1` back into `FMA`
- no expansion of taker/sniper beyond evidence-safe bounded depth in this pass

## Strong Recommendation
Run Pass 2 exactly as a semantic and contract hardening packet, not as a
feature-party.

Best order:
1. contract abuse and snapshot truth
2. downgrade/suppression honesty
3. friction population accounting
4. lifecycle provenance
5. runtime pressure profile hook
6. diff semantics

Plain-English:
make the lathe harder to fool, harder to misread, and better at explaining its
own caution before we make it fancier.
