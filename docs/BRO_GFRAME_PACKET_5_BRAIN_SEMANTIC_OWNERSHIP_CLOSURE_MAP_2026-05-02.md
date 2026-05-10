# BRO G-Frame Packet 5: Brain / Semantic-Ownership Closure Map

Historical-active boundary note:
- this packet is a completed historical forensic and closure record
- it remains valid for defect lineage, contrast evidence, and method
  inheritance
- it does not own the active `pilot_live` packet pickup or Packet 2
  `Maker-Live` sequencing

## Authority Lock
Current pickup point:
- original packet-local anchor:
  `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
- latest live red-team reconfirmation specimen:
  `656c9d42-070c-4f82-84cf-34aa333a9e7f`
- governing question:
  - does BRO still preserve one concept / one term / one owner from doctrine
    through runtime emitters through downstream consumers, or is the brain still
    split by report-side ownership drift?

Authority chain:
1. `docs/PROJECT_TRUTH_STATE.md`
2. `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
3. relevant BRO-local doctrine/runbook surfaces named in `Module Intake`
4. `docs/BRO_GFRAME_CORE_RESTORATION_PACKET_PROGRAM_2026-05-01.md`
5. `docs/CURRENT_BASELINE.md` as reference-only
6. packet-local prior blockers:
   - `docs/BRO_GFRAME_PACKET_1_BONES_RELEASE_TRUTH_LOCK_2026-05-01.md`
   - `docs/BRO_GFRAME_PACKET_2_SPINAL_CORD_FAILURE_CHAIN_2026-05-01.md`
   - `docs/BRO_GFRAME_PACKET_3_RACK_TRUTH_SYNC_CONFIRMATION_2026-05-01.md`
   - `docs/BRO_GFRAME_PACKET_4_GRIP_WALLET_AUTHORITY_CLOSURE_MAP_2026-05-01.md`
7. brain / semantic-ownership surfaces:
   - `BRO_CANONICAL_DOCTRINE.txt`
   - `docs/DOCTRINE_RUNBOOK.md`
   - `docs/EDGE_TRUTH_RUNBOOK.md`
   - `docs/BRO_METRIC_ATLAS.md`
   - `prodesk/order_manager.py`
   - `prodesk/gateway.py`
   - `scripts/edge_truth_audit.py`
   - `scripts/paper_harness_audit.py`
   - `scripts/nightly_soak_report.py`
   - `scripts/bro_metric_harvest.py`

No-change list:
- no runtime behavior mutation
- no report cleanup beyond packet classification and ownership mapping
- no wallet/live-authority mutation
- no weapon tuning
- no new semantic vocabulary unless a real doctrine gap is proven
- no fighter-closure claim beyond what Packet 1, Packet 2, Packet 3, and
  Packet 4 allow

Current blocker being judged:
- semantic ownership drift between doctrine/runtime emitters and downstream
  report/metric/operator consumers

## Red-Team Recheck (2026-05-03)
Fresh live specimen:
- `run_id=bc4bc73b-7dd4-4060-b44b-07dc0228aaa3`
- `session_id=72d00fd5-dfe5-4207-b488-4b5836d508f8`

Historical packet note:
- the original packet below preserves the real packet-era semantic map
- it should now be read alongside the fresh live recheck, not as the sole
  current truth specimen
- current packet body below is therefore a historical pre-cut diagnosis where
  it speaks in present tense about active mutation
- the closure addendum at the end of this file supersedes that older packet
  body where the two disagree

Red-team verdict:
- original Packet 5 diagnosis was real
- the primary live Brain breach on current code remains report-side mutation in
  `scripts/nightly_soak_report.py`
- the fresh specimen narrows that breach to the shared backfill/recompute
  contract rather than proving late-window dominance on every run
- the fresh specimen does **not** reconfirm the stronger prior claim that
  runtime `edge_evaluation` and `maker_fight_admission_shadow` are currently
  split owners on the same maker-scope row

Current recheck evidence:
- `canonical_paper_validation.json` on `bc4...` says:
  - `status=pass`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `reports_complete=true`
- `readiness_gate.json` on `bc4...` says:
  - `blocking_stage=pilot_live`
  - `quote_uptime_ratio=0.09424910777625768`
  - `runtime_meaningful_participation=1.0`
- `soak_hardening_gate.json` on `bc4...` remains non-blocking for this lane:
  - `ok=true`
  - sole finding:
    - `soak_maker_submits_too_low:1.000000<min:50.000000`
- report-side mutation is still live, but it presented differently on this
  specimen:
  - `maker_cannon_late_window_probe.jsonl`:
    - `row_count=140`
    - `market_reference_backfill_applied=0`
    - `runtime-vs-report secondary-oracle deltas=0`
  - `maker_mid_window_probe.jsonl`:
    - `row_count=60`
    - `market_reference_backfill_applied=6`
    - `runtime-vs-report secondary-oracle deltas=6`
    - all `6` rewritten rows are:
      - `stage=MAKER_TAKER_SELECTIVE`
      - `market_reference_mode=backfilled_paired_touch`
      - `market_reference_basis=report_book_top_pair_backfill`
      - runtime `secondary_oracle_status=unknown`
      - runtime `secondary_oracle_confirmation=false`
      - report `secondary_oracle_status=confirmed`
      - report `secondary_oracle_confirmation=true`
- fresh live maker-band tape on the later pair tightens the runtime story:
  - maker-scope `edge_evaluation` rows in `MAKER_TAKER_SELECTIVE` carried:
    - `market_reference_class=authoritative`
    - `market_reference_mode=direct_midpoint`
    - `secondary_oracle_status=confirmed`
    - `secondary_oracle_confirmation=true`
    - non-null `secondary_fair_probability`
  - same-time null-oracle duplicates were taker-scope rows with
    `block_reason=stage_disallow_taker`
  - `maker_fight_admission_shadow` matched the maker-scope direct-midpoint /
    confirmed-oracle rows on this specimen instead of contradicting them

## Rehardening Gate
Mission frame:
- preserve one semantic language across BRO so the fighter can be judged and
  repaired on truth instead of report-side reinterpretation

Doctrine frame:
- one concept, one term, one owner
- reports consume truth; reports do not invent a second dialect
- fix the highest broken authority layer before local tuning

Authority frame:
- doctrine root and emitted runtime contract names own meaning
- validators may check or normalize fail-closed within their own scope
- reports and metrics may summarize, but may not silently re-own emitted truth

Pathology frame:
- disease: split ownership and downstream meaning mutation
- symptoms:
  - `runtime_*` mirrors alongside canonical fields
  - report-side backfill labels
  - report-side recomputation of oracle truth
  - best-effort inference helpers that make rows look more semantically complete
    than emitted truth alone

Semantic frame:
- this is not a generic naming-polish packet
- this is not Packet 6 consumer-noise cleanup
- this is a brain packet about who owns meaning on the active path

Intervention frame:
- smallest correct-layer move is:
  - prove the emitter owners
  - classify every competing consumer surface
  - rank the real blockers without mutating behavior in this packet

Drift frame:
- high risk if report or metric consumers are treated like peer owners
- high risk if Packet 5 drifts into Packet 6 nervous-system cleanup
- high risk if new vocabulary is introduced instead of repairing the contract

Proof frame:
- closure requires:
  - explicit emitted owner for every in-scope concept
  - explicit classification of every connected downstream consumer
  - no unclassified report-side mutation left in the active path

Failure-signature frame:
- mirrored canonical and `runtime_*` fields coexist downstream
- `scripts/nightly_soak_report.py` upgrades some market-reference meaning
- `scripts/nightly_soak_report.py` recomputes some oracle truth
- `scripts/bro_metric_harvest.py` propagates both canonical and mirrored
  downstream distributions

Stop-the-line status:
- `TRIGGERED` against downstream report-side meaning mutation

Go / no-go:
- `GO` for semantic ownership mapping and blocker ranking
- `NO-GO` for runtime/report code mutation in this packet

Real problem:
- determine whether emitted runtime truth still owns meaning end to end, or
  whether downstream consumers are competing for ownership

Authoritative surface:
- doctrine root plus live emitted runtime contract names

Surface purpose:
- classify which surfaces emit truth, which merely consume it, and which still
  mutate or compete with it

Disease vs symptom:
- disease: ownership drift and second-dialect growth
- symptom: mirrors, backfills, recomputation, best-effort helper synthesis

Authority owner:
- doctrine + runtime emitters for the concept families in scope
- never the report or metric consumers

Smallest correct-layer move:
- classify and rank, do not mutate

What proves closure:
- every in-scope concept has one explicit authoritative owner
- every connected consumer is classified
- any remaining mirrors are explicitly bounded and non-authoritative
- any remaining reconstructed labels are explicitly demoted and cannot upgrade
  meaning

What this packet must not change:
- runtime behavior
- weapon doctrine
- wallet/live-authority posture
- packet ordering
- semantic vocabulary unless a doctrine gap is proven

## Module Intake
Authoritative sources:
- `BRO_CANONICAL_DOCTRINE.txt`
- `docs/DOCTRINE_RUNBOOK.md`
- `docs/EDGE_TRUTH_RUNBOOK.md`
- `docs/BRO_METRIC_ATLAS.md`
- `prodesk/order_manager.py`
- `prodesk/gateway.py`
- `scripts/edge_truth_audit.py`
- `scripts/paper_harness_audit.py`
- `scripts/nightly_soak_report.py`
- `scripts/bro_metric_harvest.py`

Current evidence anchors:
- `BRO_CANONICAL_DOCTRINE.txt`
  - defines one-language law, semantic precedence, and the live contract
    registry for:
    - `maker_allowed`
    - `secondary_oracle_status`
    - `secondary_oracle_confirmation`
    - `market_reference_*`
    - `decision_input_*`
- `docs/DOCTRINE_RUNBOOK.md`
  - explicitly says downstream `runtime_*` report mirrors are not runtime
    doctrine terms
  - explicitly demotes `report_book_top_pair_backfill` to report-only
    reconstructed basis label
  - explicitly demotes `legacy_fallback_non_authoritative` to report/readout
    fallback only
- `docs/EDGE_TRUTH_RUNBOOK.md`
  - defines edge truth as canonical measurement-only runtime artifact
  - forbids backfilling inferred values to make records pass
- `docs/BRO_METRIC_ATLAS.md`
  - explicitly marks `runtime_secondary_oracle_*` and
    `runtime_maker_stage_allowed_distribution` as downstream mirrors
- `prodesk/gateway.py`
  - emits `decision_input_type` from source semantics at the live contract layer
- `prodesk/order_manager.py`
  - emits `market_reference_class` and `secondary_oracle_*` from runtime
    competitiveness context into maker admission shadow surfaces
- `scripts/edge_truth_audit.py`
  - consumes emitted `maker_allowed` and related contract fields without report
    backfill
- `scripts/paper_harness_audit.py`
  - normalizes `decision_input_type` fail-closed for consumer audit purposes
    without claiming emitter ownership
- `scripts/nightly_soak_report.py`
  - duplicates:
    - `runtime_maker_stage_allowed`
    - `runtime_secondary_oracle_status`
    - `runtime_secondary_oracle_confirmation`
  - upgrades:
    - `market_reference_class` from `not_available` / empty to `authoritative`
  - reconstructs:
    - `market_reference_basis=report_book_top_pair_backfill`
  - recomputes:
    - `secondary_oracle_confirmation`
    - `secondary_oracle_status`
  - synthesizes:
    - `_best_effort_market_reference_mode`
    - `_best_effort_market_reference_source_side`
- `scripts/bro_metric_harvest.py`
  - propagates both canonical and mirrored downstream distributions harvested
    from nightly/report summary surfaces

Downstream consumers:
- `scripts/edge_truth_audit.py`
- `scripts/paper_harness_audit.py`
- `scripts/nightly_soak_report.py`
- `scripts/bro_metric_harvest.py`
- `docs/BRO_METRIC_ATLAS.md`
- `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
- `docs/NEXT_PACKET_PLAN.md`

Stale surfaces that must not dominate judgment:
- `docs/CURRENT_BASELINE.md`
- older packet-history surfaces preserved for continuity only
- downstream `runtime_*` mirrors if treated like owners
- report-side reconstructed labels if treated like emitted truth

## Contradiction Matrix
- doctrine and emitters already define `market_reference_class` ownership
- `scripts/nightly_soak_report.py` upgrades empty / `not_available` rows to
  `authoritative`

Resolution:
- this is a real ownership contradiction
- report-side code is competing with emitted runtime reference truth

- doctrine and emitters already define `secondary_oracle_status` and
  `secondary_oracle_confirmation`
- `scripts/nightly_soak_report.py` recomputes them in late-window probe rows

Resolution:
- this is a real ownership contradiction
- report-side code is competing with emitted selection/oracle truth

- doctrine and runbook explicitly demote `runtime_*` report mirrors
- downstream report and metric surfaces still carry both canonical and
  `runtime_*` parallel distributions

Resolution:
- this is not automatically fatal by itself
- it remains bounded only if those mirrors never feel like the real owner and
  never outrank the canonical term in downstream interpretation

- `decision_input_type` is emitted from gateway/source semantics
- `scripts/paper_harness_audit.py` normalizes it fail-closed from related
  provenance inputs when explicit value is absent

Resolution:
- current evidence supports this as a bounded consumer normalization, not an
  emitter-owner conflict
- it stays acceptable only while clearly consumer-side, fail-closed, and
  non-authoritative

## Semantic Ownership Ledger
| Concept | Doctrine Owner | Runtime Emitter | Validator Consumers | Report Consumers | Metric Consumers | Operator-Facing Readouts | Current Risk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `maker_allowed` | runtime stage policy | emitted edge/runtime rows | `edge_truth_audit.py` | `nightly_soak_report.py` plus `runtime_maker_stage_allowed` mirror | `bro_metric_harvest.py` via nightly summary propagation | audit board Brain section consumes packet-level stage-permission truth; `NEXT_PACKET_PLAN.md` is pickup-only and non-authoritative | medium |
| `secondary_oracle_status` | selection/oracle gate | emitted edge/runtime rows and order-manager shadow rows | `edge_truth_audit.py` | `nightly_soak_report.py` canonical field plus `runtime_*` mirror and downstream recomputation | `bro_metric_harvest.py` propagates canonical and mirrored distributions | audit board Brain section carries contradiction summary only; `NEXT_PACKET_PLAN.md` does not own oracle meaning | high |
| `secondary_oracle_confirmation` | selection/oracle gate | emitted edge/runtime rows and order-manager shadow rows | `edge_truth_audit.py` | `nightly_soak_report.py` canonical field plus `runtime_*` mirror and downstream recomputation | `bro_metric_harvest.py` propagates canonical and mirrored distributions | audit board Brain section carries contradiction summary only; `NEXT_PACKET_PLAN.md` does not own oracle meaning | high |
| `market_reference_class` | runtime reference resolution | emitted edge/runtime rows and order-manager shadow rows | `edge_truth_audit.py` | `nightly_soak_report.py` upgrades missing / `not_available` rows | `bro_metric_harvest.py` propagates downstream class distributions | audit board Brain section calls out the ownership breach; `NEXT_PACKET_PLAN.md` remains pickup-only | high |
| `market_reference_basis` | runtime reference resolution | emitted edge/runtime rows | `edge_truth_audit.py` | `nightly_soak_report.py` writes `report_book_top_pair_backfill` | downstream report/metric surfaces can read reconstructed basis labels | audit board Brain section demotes report-only basis language; `NEXT_PACKET_PLAN.md` remains pickup-only | high |
| `market_reference_mode` | runtime reference resolution | emitted edge/runtime rows and order-manager shadow rows when present | none primary | `nightly_soak_report.py` synthesizes best-effort mode when absent | `bro_metric_harvest.py` propagates downstream mode distributions | no direct operator-facing owner in current board/pickup surfaces; only indirect through report/metric consumers | high |
| `market_reference_source_side` | runtime reference resolution | emitted edge/runtime rows and order-manager shadow rows when present | none primary | `nightly_soak_report.py` synthesizes best-effort source-side when absent | `bro_metric_harvest.py` propagates downstream source-side distributions | no direct operator-facing owner in current board/pickup surfaces; only indirect through report/metric consumers | high |
| `market_reference_confidence` | runtime reference resolution | emitted edge/runtime rows | `edge_truth_audit.py` | current Packet 5 sweep found no loud downstream ownership override | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through report/metric consumers | low |
| `market_reference_fallback_used` | runtime reference resolution | emitted edge/runtime rows | `edge_truth_audit.py` | current Packet 5 sweep found no loud downstream ownership override | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through report/metric consumers | low |
| `decision_input_source` | emitting contract owner | emitted edge/runtime rows | `paper_harness_audit.py` | no loud report-side ownership conflict found in Packet 5 | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through harness/report consumers | low |
| `decision_input_type` | emitting contract owner | emitted edge/runtime rows and gateway source semantics | `paper_harness_audit.py` fail-closed normalization | no loud report-side ownership conflict found in Packet 5 | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through harness/report consumers | low |
| `decision_input_emulated` | emitting contract owner | emitted edge/runtime rows | `paper_harness_audit.py` | no loud report-side ownership conflict found in Packet 5 | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through harness/report consumers | low |
| `decision_input_data_class` | emitting contract owner | emitted edge/runtime rows | `paper_harness_audit.py` | no loud report-side ownership conflict found in Packet 5 | downstream use not currently the loud seam | no direct operator-facing owner in current board/pickup surfaces; only indirect through harness/report consumers | low |

## Mirror vs Mutation Ledger
| Surface | Current Packet Class | Why |
| --- | --- | --- |
| `runtime_maker_stage_allowed` | bounded mirror | direct duplicate of `maker_allowed`; allowed only if explicitly non-authoritative |
| `runtime_secondary_oracle_status` | bounded mirror with high drift risk | direct duplicate of emitted oracle status; still creates second-dialect propagation downstream |
| `runtime_secondary_oracle_confirmation` | bounded mirror with high drift risk | direct duplicate of emitted oracle confirmation; still creates second-dialect propagation downstream |
| `report_book_top_pair_backfill` | descriptive reconstructed label | explicitly report-only in doctrine, but becomes dangerous when paired with stronger authority upgrades |
| `market_reference_class` rewrite in `nightly_soak_report.py` | active truth mutation | downstream report code upgrades emitted reference authority |
| report-side `secondary_oracle_confirmation` recomputation | active truth mutation | downstream report code replaces emitted oracle truth |
| report-side `secondary_oracle_status` recomputation | active truth mutation | downstream report code replaces emitted oracle truth |
| `_best_effort_market_reference_mode` | active truth mutation | downstream helper synthesizes missing reference-mode meaning and writes it back into consumer logic |
| `_best_effort_market_reference_source_side` | active truth mutation | downstream helper synthesizes missing source-side meaning and writes it back into consumer logic |
| `paper_harness_audit._decision_input_type_from_row` | bounded mirror / fail-closed consumer normalization | consumer derives missing type for audit classification without claiming emitter ownership |
| `bro_metric_harvest.py` runtime/canonical parallel distributions | bounded mirror propagation with drift risk | harvester does not originate meaning, but amplifies second-dialect consumers downstream |

## Brain Still Split Here Register
1. report-side upgrade of `market_reference_class`
   - strongest current ownership breach
2. report-side reconstructed `market_reference_basis=report_book_top_pair_backfill`
   - safe only as demoted report-only label; unsafe when paired with authority
     upgrade
3. report-side recomputation of `secondary_oracle_confirmation`
4. report-side recomputation of `secondary_oracle_status`
5. best-effort synthesis of:
   - `market_reference_mode`
   - `market_reference_source_side`
6. downstream propagation of canonical plus `runtime_*` mirror distributions
   through report and metric consumers

## Highest-Authority Fix Order
1. stop report-side `market_reference_class` upgrade semantics from competing
   with emitted reference truth
2. stop report-side oracle recomputation from competing with emitted oracle
   truth
3. demote or remove best-effort market-reference helper synthesis when it is
   acting like contract truth
4. re-evaluate whether surviving `runtime_*` mirrors remain worth carrying once
   ownership is clean

## Pass 1
Top-down authority preservation findings:
- stage-permission family:
  - `maker_allowed` ownership is explicit in doctrine and edge/runtime truth
- oracle/selection family:
  - `secondary_oracle_status` and `secondary_oracle_confirmation` ownership is
    explicit in doctrine and emitted runtime/order-manager surfaces
- market-reference family:
  - `market_reference_class`, `basis`, `mode`, `source_side`, `confidence`, and
    `fallback_used` ownership is explicit in doctrine and emitted runtime
    surfaces
- decision-provenance family:
  - `decision_input_*` ownership is explicit in doctrine and emitted gateway /
    edge/runtime surfaces
- validators:
  - `edge_truth_audit.py` behaves like a bounded consumer
  - `paper_harness_audit.py` behaves like a bounded fail-closed consumer for
    `decision_input_*`

Pass 1 verdict:
- doctrine and emitter ownership for the in-scope families is mostly explicit
- no real doctrine-gap was proven on the active path
- the open brain problem is downstream consumer competition, not missing
  top-level vocabulary

## Pass 2
Bottom-up reinterpretation findings:
- `scripts/nightly_soak_report.py` is not only summarizing:
  - it mirrors
  - it reconstructs
  - it upgrades
  - it recomputes
  - it synthesizes helper meaning and writes it into downstream logic
- `scripts/bro_metric_harvest.py` is not the primary mutation source, but it
  propagates both canonical and mirrored downstream distributions deeper into
  the operator-facing measurement spine
- `scripts/paper_harness_audit.py` currently stays inside bounded consumer
  normalization rather than competing with emitted owner truth
- `scripts/edge_truth_audit.py` currently stays inside emitted-truth consumer
  behavior rather than backfilling rows
- `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md` currently behaves like a
  bounded operator-facing board sink:
  - it carries Packet 5 truth and ambiguity classifications
  - it does not originate competing runtime semantic owners
  - it is still downstream of report/metric contamination until Packet 6 closes
    the broader consumer spine
- `docs/NEXT_PACKET_PLAN.md` currently behaves like a non-authoritative pickup
  surface:
  - it points to the packet artifacts and board sink
  - it does not independently own Brain semantics
  - it must remain fenced from becoming a competing semantic summary

Pass 2 verdict:
- the loudest Brain blocker is downstream report-side ownership drift in
  `scripts/nightly_soak_report.py`
- the second-loudest seam is propagation/amplification through
  `scripts/bro_metric_harvest.py`
- operator-facing board/pickup surfaces currently look subordinate rather than
  ownership-competitive, but they are only as clean as the upstream report and
  metric consumers feeding them
- not every downstream consumer is sick, but the active path still contains
  real competing owners

## Dependency Review
Upstream dependency:
- Packet 1 still limits closure-certifying claims on the active dirty anchor
- Packet 2 still says the fighter fails above the semantic lane on the
  canonical path
- Packet 4 already proved a clean example of stage-domain semantics staying in
  their proper owner

Downstream blast radius:
- high
- Packet 6 nervous-system work depends on Packet 5 drawing the ownership line
  correctly first
- future maker forensic work can drift badly if report-owned terms keep
  masquerading as runtime truth

False-closure risk:
- treating bounded mirrors as harmless without checking downstream amplification
- treating `report_book_top_pair_backfill` like emitted basis truth
- treating best-effort helpers as acceptable because they are convenient
- mistaking “the docs already mention the drift” for “the drift is already
  closed”

No-shortcut zones:
- do not invent new semantic registry layers
- do not skip emitter ownership and jump straight to report cleanup
- do not absorb Packet 6 nervous-system cleanup into Packet 5
- do not let packet-local classification language become a new parallel runtime
  dialect

## Drift Register Delta
- `D-024 | Report/runtime drift`
  - report-side `market_reference_class` upgrade and paired reconstructed basis
    labeling compete with emitted reference truth
- `D-025 | Ownership drift`
  - report-side oracle recomputation competes with emitted
    `secondary_oracle_status` / `secondary_oracle_confirmation`
- `D-026 | Semantic drift`
  - best-effort market-reference helper synthesis creates downstream meaning
    that can look like emitted contract truth
- `D-027 | Report/runtime drift`
  - downstream metric/report propagation amplifies canonical plus `runtime_*`
    parallel distributions into a longer-lived second dialect

## Ambiguity Register Delta
- `bounded mirror`
  - exact definition:
    - downstream duplicate/readout of an emitted concept that is explicitly
      non-authoritative and does not compete for ownership
  - forbidden misread:
    - harmless forever, even when it amplifies into a second dialect
- `active truth mutation`
  - exact definition:
    - downstream consumer rewrites, upgrades, or synthesizes meaning that
      belongs to an emitted owner
  - forbidden misread:
    - acceptable summary convenience
- `runtime_* mirror`
  - exact definition:
    - downstream report/readout duplicate of a canonical emitted term, not a new
      doctrine root
  - forbidden misread:
    - the real owner because it appears in summary distributions
- `report_book_top_pair_backfill`
  - exact definition:
    - report-only reconstructed basis label
  - forbidden misread:
    - live emitted runtime basis
- `best_effort market-reference helper`
  - exact definition:
    - downstream consumer-side inference of missing market-reference mode or
      source-side meaning
  - forbidden misread:
    - canonical emitted reference truth

## Closure Addendum (2026-05-03)
Fresh live closure specimen:
- `run_id=656c9d42-070c-4f82-84cf-34aa333a9e7f`
- `session_id=21e60a54-c7e9-4c44-a471-5e8543408d26`

Implementation truth:
- the bounded source cut landed in `scripts/nightly_soak_report.py`
- probe builders no longer overwrite:
  - `market_probability`
  - `desired_quote_price`
  - `market_reference_*`
  - `secondary_oracle_*`
- existing descriptive-only backfill visibility remains:
  - `market_reference_backfill_applied`
  - `market_reference_backfill_pair_delta_sec`
- best-effort market-reference helpers no longer write synthesized
  owner-looking values back into persisted probe or actionability rows

Fresh closure proof:
- `canonical_paper_validation.json` on `656c...` says:
  - `status=pass`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
- `maker_cannon_late_window_probe.jsonl` on `656c...` says:
  - `row_count=78`
  - backfill rows=`3`
  - runtime-vs-report oracle deltas=`0`
- those `3` late-window backfill rows preserve runtime owner truth exactly:
  - `market_reference_class=not_available`
  - `market_reference_mode=missing`
  - `market_reference_basis=missing`
  - `market_reference_source_side=none`
  - `secondary_oracle_status=unknown`
  - `secondary_oracle_confirmation=false`
  - `market_probability=null`
- `maker_mid_window_probe.jsonl` on `656c...` says:
  - `row_count=62`
  - runtime-vs-report oracle deltas=`0`
- raw event-to-probe replay on the same run confirms no row upgrades runtime
  `missing/not_available/unknown` owner truth into report
  `authoritative/confirmed`
- downstream harvest on the same `656c...` specimen does not resurrect the old
  second dialect

Current interpretation:
- historical pre-cut anchors `8a389...`, `7b4c...`, and `bc4...` remain the
  proof that the defect was real
- the fresh `656c...` specimen is now the proof that the Brain owner seam is
  source-closed on current code
- remaining broader consumer/readout cleanup belongs to Packet 6 / Nervous
  System, not to an active Brain owner rewrite seam

## Binary Verdict
- `Completed` for current-code source-layer semantic-ownership closure
- whole-fighter closure remains subordinate to the board sink and the
  post-restoration `pilot_live` authority proof frontier

Reason:
- doctrine and emitter ownership were already stronger than the consumer layers
- the emitted runtime language was mostly not the problem
- the bounded source cut now removes the active report-side owner rewrite seam
  on current code

## Closure Matrix Update
What must become true:
- one-language ownership must survive from doctrine to emitters to consumers
- report-side `market_reference_class` / `basis` handling must no longer upgrade
  emitted truth
- report-side oracle handling must no longer recompute emitted truth
- best-effort market-reference helpers must be either removed, demoted, or
  explicitly bounded so they cannot masquerade as emitted truth
- surviving mirrors must be explicitly bounded and non-authoritative
- downstream metric propagation must not turn bounded mirrors into longer-lived
  competing readouts

Required proof artifact:
- a current-code Packet 5 semantic-ownership ledger spanning:
  - doctrine root
  - runtime emitters
  - validators
  - reports
  - metrics
  - operator-facing board truth

Currently missing proof:
- no Brain-source missing proof remains on the current specimen
- broader consumer-chain cleanliness remains a Nervous-system lane question,
  not an open Brain owner-seam question

## Packet 5 Call
`VERIFIED`:
- doctrine already contains the live contract vocabulary needed for the active
  brain lane
- runtime emitter ownership for the in-scope concept families is mostly clear
- the historical loudest semantic mutation surface was
  `scripts/nightly_soak_report.py`
- the bounded source cut in `scripts/nightly_soak_report.py` now removes:
  - report-side `market_reference_*` owner rewrites
  - report-side `secondary_oracle_*` recomputation
  - helper-layer best-effort resurrection of owner-looking market-reference
    values
- `scripts/paper_harness_audit.py` currently reads like bounded consumer
  normalization rather than competing ownership
- `scripts/edge_truth_audit.py` currently reads like an emitted-truth consumer
  rather than a semantic owner
- fresh live closure specimen `656c9d42-070c-4f82-84cf-34aa333a9e7f` proves:
  - `0` late-window runtime-vs-report oracle deltas
  - `0` mid-window runtime-vs-report oracle deltas
  - late-window backfill rows preserve runtime:
    - `market_reference_class=not_available`
    - `market_reference_mode=missing`
    - `market_reference_basis=missing`
    - `market_reference_source_side=none`
    - `secondary_oracle_status=unknown`
    - `secondary_oracle_confirmation=false`
  - downstream harvest on the same run does not resurrect the old second dialect

`INFERRED`:
- the next highest-ROI semantic lane is now Packet 6 / Nervous-system
  consumer-truth closure, not another Brain source cut
- the Packet 5 ledgers already provide usable seed material for current
  semantic-registry, lineage, mirror/fat, and audit-coverage work inside the
  existing packet and board surfaces; no new standalone artifact is justified
  yet

`VERIFIED`:
- Packet 5 does not loosen the weapon gate
- Brain source-layer mutation closure is now achieved on current code
- `Maker`, `Taker`, and `Sniper` remain diagnostic-only
