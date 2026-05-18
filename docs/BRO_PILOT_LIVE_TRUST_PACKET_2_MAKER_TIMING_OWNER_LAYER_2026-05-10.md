# BRO Pilot-Live Trust Packet 2: Maker Timing Owner Layer (2026-05-10)

## Role Declaration
- `VERIFIED`: bounded board sink

## Purpose
- `VERIFIED`: this document materializes the missing Packet 2 maker timing/clock owner layer.
- `VERIFIED`: it exists to keep maker timing truth in one chain from host/time seed through submit legality and downstream readout.
- `VERIFIED`: it is a read-only timing-owner surface, not a timing-mutation approval.

## Authority Boundary
- `VERIFIED`: whole-fighter timing doctrine still lives first in:
  - `docs/PROJECT_TRUTH_STATE.md` as the broad repo truth screen
  - `docs/BRO_ENGINEERING_KERNEL.md`
  - `docs/TIMING_AND_WS_SOURCE_DIAGNOSTIC_REPORT_2026-05-09.md`
- `VERIFIED`: Packet 2 uses this document to localize maker-path timing truth inside the already-hardened whole-fighter timing spine.
- `VERIFIED`: no timing leaf may be widened, duplicated, or demoted from this document alone.

## Maker Timing Chain
| Timing seam | Strong owner surface | Current state | Surface class | May decide | Must not decide | Packet 2 call |
| --- | --- | --- | --- | --- | --- | --- |
| host sync / combat clock foundation | host timing + timing-spine hardening artifacts + `time_discipline_audit` | whole-fighter timing spine already closed; not the active maker patient | `valid interlock` | whether BRO timing foundation is trustworthy enough to read later timing surfaces honestly | maker selectivity or expectancy by itself | `KEEP` |
| stage-time seed | `executor.py` stage map and `sec_to_expiry` seed | current upstream source of maker `sec_to_expiry` and effective stage | `owner-law` | the raw time-remaining contract carried into later maker surfaces | be replaced by report approximations or stale stage language | `KEEP` |
| maker competitiveness timing gate | `executor.py::_maker_timing_gate_open()` with paper `7-15s` leaves | active current paper late-window eligibility gate | `valid interlock` | whether maker is even timing-eligible after the `15s` open and before the `7s` taker handoff | whole-path economic meaning or submit legality by itself | `KEEP` |
| selection-gate timing fields | `order_manager.py` `selection_gate.min_sec_to_expiry` / `max_sec_to_expiry` | currently present in code shape but unset in the active paper profile | `compatibility seam` | add a second timing filter only if explicitly configured later | silently become duplicate timing authority just because the fields exist | `KEEP DORMANT / FENCE` |
| lifecycle-context propagation | `executor.py::_build_submission_lifecycle_context()` | copies `sec_to_expiry` into submit/risk/lifecycle truth and emits mismatch/missing events | `owner-law` | carry one timing contract through submit legality and later audits | go missing on risk-increasing submits without being treated as a real packet defect | `KEEP` |
| submit legality timing gate | `risk.py` `min_sec_to_expiry_for_new_exposure` plus lane overrides | maker now carries an explicit `7.0s` lane override while the shared global floor remains `15.0s` | `valid interlock` | block maker new risk after taker handoff without widening unrelated lanes | impersonate maker selectivity doctrine or hide as if it were the same gate as the maker timing gate | `KEEP` |
| submit-time expiry reconstruction | `order_manager.py::_resolve_submit_sec_to_expiry()` and related lifecycle fields | reconstructs/propagates timing at order-commit surfaces | `compatibility seam` | keep submit/lifecycle timing coherent for audits and order tracking | create a second semantic clock divorced from the upstream seed | `KEEP` |
| emitted timing readout | emitted decision/lifecycle/shadow events plus validators and reports | downstream timing readout is available and must stay chained to runtime truth | `support-only` | certify what timing semantics were emitted and observed | invent fresh timing doctrine or outrank the runtime owner chain | `KEEP BUT FENCE` |

## Composite Window Truth
- `VERIFIED`: the plain-language current paper maker timing story is not just one leaf.
- `VERIFIED`: the actual active chain is now:
  1. `executor.py` opens maker timing eligibility when `7.0 <= sec_to_expiry <= 15.0`
  2. `risk.py` allows maker new risk until the maker lane's explicit `7.0s` override
- `VERIFIED`: that makes the effective current paper maker new-risk submit window `(7.0, 15.0]`, with taker taking over at `<=7.0s`.
- `VERIFIED`: current paper selection-gate timing fields are unset, so they are not presently a third active timing owner.
- `VERIFIED`: if later packet work ever sets those selection-gate timing leaves, Packet 2 must treat that as new duplicate timing authority requiring explicit doctrine review.

## Pre-Correction Watched Specimen Read
- `VERIFIED`: watched current-tree specimen `6957087b-488e-4bbb-b8b9-1f215b5e33d0` now belongs to the pre-correction `15-20 / 15` lineage and is preserved here as ancestry only.
- `VERIFIED`: the pre-correction active `15-20s` maker band produced `20` rows:
  - `1` row submitted
  - `19` rows stayed inside-band `maker_no_submission`
  - no `stage_disallow_maker` or `maker_timing_gate_closed` rows lived inside `15-20s`
- `VERIFIED`: the same pre-correction specimen shows a distinct normal `10-15s` authority-closed population:
  - `15` rows emitted `block_reason=stage_disallow_maker`
  - raw `edge_evaluation` rows for those same rows preserved
    `late_window_authority_class=reduce_only_recovery_only`
  - those rows were not recovery-active; they were normal rows living below the
    current `15.0s` maker new-risk floor
- `VERIFIED`: the same pre-correction specimen shows a separate recovery-active `10-15s`
  population:
  - `5` rows emitted `block_reason=maker_timing_gate_closed`
  - raw `edge_evaluation` rows for those same rows still preserved
    `late_window_authority_class=reduce_only_recovery_only`
  - the changed label came from `reduce_only_recovery_active=true`, which kept
    `maker_allowed=true` even while `maker_new_risk_allowed=false`
- `VERIFIED`: no current watched maker row emitted
  `new_exposure_expiry_gate_blocked`.
  - the exact `15.0s` split remains a code/doctrine seam
  - this watched specimen did not surface it as a distinct downstream runtime
    blocker family
- `VERIFIED`: the current cannon-probe support surface omits
  `late_window_authority_class` even though the raw runtime events preserve it.
  - Packet 2 must treat that as a support-surface semantic gap
  - it is not proof that the authority class was absent

## Historical Specimen Provenance Rule
- `VERIFIED`: current Packet 2 doctrine is the current-code layered story above:
  - maker gate opens at `15s`
  - taker handoff opens at `7s`
  - effective maker new-risk submit window remains `(7.0, 15.0]`
- `VERIFIED`: accessible packet-era maker specimens such as `8bfb...`, `ed184...`, and `e675...` were generated from historical runtime commit `519f6ed...`, not the current working-tree code.
- `VERIFIED`: those historical runs carried live run manifests with:
  - maker timing posture `50.0-60.0`
  - risk `min_sec_to_expiry_for_new_exposure=50.0`
- `VERIFIED`: the checked-in repo profile at commit `519f6ed...` showed a different default/file posture:
  - maker timing posture `45.0-60.0`
  - risk `min_sec_to_expiry_for_new_exposure=45.0`
- `VERIFIED`: because those specimens were dirty-tree historical runs, the interpretation order for that era is:
  1. run manifest / emitted runtime artifacts from the run
  2. report surfaces derived from that run
  3. repo file at the pinned git commit
  4. later human summary language
- `VERIFIED`: this means the old `50-60s` / `45-60s` maker timing packet story is valid historical runtime ancestry only. It is not present-tense Packet 2 doctrine.

## Timing Break Register
| Break or seam | Why it matters | Current call |
| --- | --- | --- |
| exact `15.0s` left-edge split | maker timing gate says open, risk gate says blocked for new risk | `VERIFIED`, code-level seam; no distinct watched runtime blocker hit yet |
| blueprint sweet-spot `10-15s` remains fully authority-closed in current normal paper posture | current blueprint intent and current paper authority shape are not the same thing | `VERIFIED` |
| dormant duplicate timing carrier in selection gate | code supports second timing filter even though current paper does not use it | `VERIFIED` |
| lane-override asymmetry | taker has an explicit `0.0` lane override while maker inherits the global `15.0` new-exposure rule | `VERIFIED` |
| lifecycle-context missing timing path | missing `sec_to_expiry` is a real fail-closed outcome on new-risk intent | `VERIFIED` |
| probe and shadow timing bands sounding doctrinal | late-window probes are useful research, not present-tense runtime authority | `VERIFIED` |
| `stage_disallow_maker` can flatten a later-timing authority closure into a stage-looking label on support surfaces | later surgery gets dangerous if report/support semantics hide the real owner class | `VERIFIED` |
| historical run-manifest vs repo-default timing posture | historical packet-era runtime may have lived a dirty-tree timing contract different from the repo file at the same commit | `VERIFIED` |

## Packet 2 Timing Calls
- `VERIFIED`: host timing is not the active maker patient right now; local timing-authority layering is.
- `VERIFIED`: maker timing truth currently has one real doctrinal chain, but it is layered:
  - upstream stage-time seed
  - maker late-window eligibility
  - lifecycle propagation
  - risk new-exposure legality
  - downstream emitted and validator readout
- `VERIFIED`: any future timing mutation must answer this exact layered chain, not one convenient leaf.
- `VERIFIED`: the larger current timing story is not only the exact `15.0s`
  seam.
  - `(7.0, 15.0]` is the current active maker new-risk window
  - `10-15s` is authority-closed in current paper posture
- `VERIFIED`: the watched specimen does not yet prove a clean economic
  normal-maker choke inside `10-15s`.
  - no normal `10-15s` rows were simultaneously authoritative,
    geometry-viable, and non-recovery
  - the only authoritative + geometry-viable `10-15s` rows in the watched
    specimen were recovery-active
- `VERIFIED`: historical packet-era maker timing results must be read under
  their own run-manifest authority and then quarantined as ancestry, not
  silently blended into the current maker-at-`15s` / taker-at-`7s` doctrine.
- `INFERRED`: the first likely later timing surgery question is not “widen the
  window.”
  - it is whether current paper should keep `10-15s` fully authority-closed
  - and whether support/report surfaces should describe that closure more
    honestly than `stage_disallow_maker`
- `INFERRED`: the exact `15.0s` left-edge split remains a later surgery
  candidate only if future watched proof shows it matters economically, not
  just semantically.
