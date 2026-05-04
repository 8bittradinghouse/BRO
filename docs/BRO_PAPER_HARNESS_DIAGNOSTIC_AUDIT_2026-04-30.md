# BRO Paper Harness Diagnostic Audit (2026-04-30)

Static cross-surface audit of the canonical paper harness lane against current
BRO doctrine, truth semantics, runtime evidence semantics, and planned
paper-harness hardening concerns.

## 0) Closure Addendum (Post-Connector Proof Refresh)
- `historical_scope_note`: this addendum preserves the `8db2...` harness-to-spinal
  handoff truth from `2026-04-30`; current Packet 2 closure truth now lives in
  `BRO_GFRAME_PACKET_2_SPINAL_CORD_FAILURE_CHAIN_2026-05-01.md`,
  `BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`, and
  `PROJECT_TRUTH_STATE.md`
- `historical_contrast_role`: this file preserves packet-era harness/spinal
  contrast only; it is not a front-of-house BRO truth surface and it does not
  own current board state
- `timestamp_utc`: `2026-04-30T11:46:41.504Z`
- `current_code_runtime_proof_run_id`: `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
- `current_code_runtime_classification`: `NON_PROMOTABLE_NO_PARTICIPATION`
- `current_code_validation_status`: `policy_failed`
- `current_code_report_dir`: `logs_exec/paper_universal/reports/8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
- `VERIFIED`: connector-closure work plus the fresh canonical run closed the previous connector blockers:
  - `H-001` closed: promotion evidence now compares and surfaces `code_fingerprint_sha256`
  - `H-002` closed: nightly soak now emits explicit realism semantics
  - `H-003` closed as a contract seam: harness audit and nightly soak now share one realism-grade contract surface
  - `H-004` closed: run-integrity now requires top-level `code_fingerprint_sha256`
  - `H-005` materially reduced/closed as a connector blocker: promotion evidence now fail-closes on incomplete manifest-backed lineage
- `VERIFIED`: the fresh run did **not** expose a new harness connector failure.
- `VERIFIED`: the newest live-diagnosed canonical run used the restored late-window maker doctrine in runtime, not just in docs:
  - `timing_gate_min_sec_to_expiry=15.0`
  - `timing_gate_max_sec_to_expiry=20.0`
  - `risk.min_sec_to_expiry_for_new_exposure=15.0`
  - `runtime.held_preexpiry_reduce_only_sec=15.0`
  - `runtime.preexpiry_emergency_taker_window_sec=7.0`
  - `runtime.terminal_unwind_halt_new_risk_sec=7.0`
- `VERIFIED`: the newest live-diagnosed canonical run did **not** reopen a harness connector defect. It failed higher in the runtime lane:
  - `highest_passing_stage=none`
  - `blocking_stage=paper`
  - `quote_uptime_ratio=0.0`
  - `maker_submits=0.0`
- `VERIFIED`: live under-the-hood diagnostics on `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204` verified a healthy feed/runtime floor plus an armed global latency verifier before the choke manifested.
- `VERIFIED`: the current maker choke shape is now anchored on doctrine-restored runtime behavior:
  - every observed live `15-20s` maker block used `token_lag_not_verified_for_maker`
  - the active near-expiry pair reached the cannon window with `maker_prereq_ok=false`
  - token-level confidence remained healthy on a different pair in the same run, so the current choke is per-token / per-pair lag-verification continuity rather than whole-system latency failure

## 1) Lineage Lock (Required)
- `timestamp_utc`: `2026-04-30T07:00:49Z`
- `git_commit`: `static_repo_audit_not_run_anchored`
- `branch`: `UNKNOWN`
- `config_path`: `configs/profiles/paper_universal.yaml`
- `profile_name`: `paper_universal`
- `paper_enforce_setup_lock`: `VERIFIED true` on canonical profile
- `paper_expected_profile_name`: `paper_universal`
- `paper_expected_config_fingerprint_sha256`: `852ff08674395b2759983074773d2de700e49518f4d4fcffc5dc8fc1edd460e1`
- `_meta.effective_config_sha256`: `852ff08674395b2759983074773d2de700e49518f4d4fcffc5dc8fc1edd460e1`
- `setup_lock_match`: `VERIFIED true`
- `run_id`: `N/A (static harness diagnostic + targeted fixture proofs)`
- `comparison_anchors`:
  - `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`
  - `BRO_CANONICAL_DOCTRINE.txt`
  - `scripts/paper_harness_audit.py`
  - `scripts/harness_qualify.py`
  - `scripts/run_integrity_audit.py`
  - `scripts/nightly_soak_report.py`
  - `scripts/promotion_evidence_gate.py`
  - `scripts/canonical_paper_validation.sh`

## 2) Symptom Intake
- `primary_symptom`: harness truth/runtimeliness/lineage seams should be mapped in one broad pass instead of one-by-one patching
- `first_seen_at`: doctrine restoration lane, paper harness return segment
- `scope`: system-wide across canonical paper harness doctrine, audit, reporting, and promotion connectors
- `impact`: evidence / reporting / gate / operator trust
- `operator_observation`: confused nervous-system surfaces create instability and later debugging pain even when runtime is technically okay

## 3) System Mapping (Vehicle Model)
- `brain`: canonical paper harness doctrine + audit + validation + promotion chain
- `harness`: config / decision / lifecycle / data / evidence / safety
- `connector_box`: evidence + lineage + reporting + promotion consistency
- `fuse_or_gate`: `paper_harness_audit`, `run_integrity_audit`, `validator_determinism_ok`, `promotion_evidence_gate`
- `ground_truth_surface`: canonical paper artifacts under `logs_exec/paper_universal`, especially run manifest, run contract, validator outputs, and report bundle

## 4) Evidence Bundle
- `commands_run`:
  - `python3 -m pytest -q tests/test_paper_harness_audit.py tests/test_operator_docs_canonical.py`
  - `python3 scripts/paper_harness_audit.py --config configs/profiles/paper_universal.yaml --skip-run-integrity`
  - targeted `rg` and `sed` passes over doctrine, audit, reporting, integrity, promotion, validation, and artifact-identity surfaces
- `key_log_paths`:
  - `logs_exec/paper_universal`
- `status_rows_examined`:
  - static code/doctrine pass
  - targeted fixture-backed audit coverage in `tests/test_paper_harness_audit.py`
- `event_rows_examined`:
  - targeted fixture-backed audit coverage in `tests/test_paper_harness_audit.py`
- `report_artifacts_examined`:
  - `paper_harness_audit`
  - `nightly_soak_report`
  - `promotion_evidence_gate`
  - `validation_summary.json` contract in `canonical_paper_validation.sh`
- `counter_examples_checked`:
  - hidden emulation hard-fail
  - missing disclosure hard-fail
  - short-window REST warning downgrade
  - low realism grade with zero findings
  - run-manifest lineage surface extraction
- `proof_strength`: `VERIFIED`

## 5) Strength Locks
1. `VERIFIED`: canonical paper harness is now explicitly treated as the emulation/proving lane, not a generic simulator.
2. `VERIFIED`: `paper_harness_audit` fail-closes on missing decision/fill truth disclosures and on emulated action rows unless explicitly allowed.
3. `VERIFIED`: simulator has been removed from the active harness/control nervous system.
4. `VERIFIED`: `paper_harness_audit` now surfaces explicit claim-boundary truth, explicit proving lineage, and explicit non-authority semantics for `harness_realism_grade`.
5. `VERIFIED`: canonical validation still fail-closes on validator replay mismatch through `validator_determinism_ok`.

## 6) Casualty Ledger (Ranked)
| Severity | Finding ID | Subsystem | Root Cause Type | Blast Radius | Evidence | Decision |
|---|---|---|---|---|---|---|
| CLOSED | H-001 | `scripts/promotion_evidence_gate.py` | contract_mismatch | promotion/evidence lane | promotion now requires and compares `code_fingerprint_sha256` across soak/reconcile/websocket identity and the fresh run proved cross-artifact agreement | keep closed |
| CLOSED | H-002 | `scripts/nightly_soak_report.py` | contract_mismatch | reporting/operator interpretation | nightly soak now emits `descriptive_non_gating` / `non_authoritative` semantics and current-code proof carried them in the artifact | keep closed |
| CLOSED | H-003 | `scripts/nightly_soak_report.py` + `scripts/paper_harness_audit.py` | wiring_defect | reporting drift risk | both surfaces now consume one shared realism-grade contract without forcing a risky grading-math refactor | keep closed |
| CLOSED | H-004 | `scripts/run_integrity_audit.py` | contract_mismatch | evidence integrity lane | manifest integrity now requires top-level `code_fingerprint_sha256`; strict and legacy paths are covered by tests | keep closed |
| CLOSED | H-005 | connector claim surfaces | gate_correct_expected | config-only vs evidence-claim distinction | promotion-grade evidence now fail-closes on incomplete or inconsistent manifest-backed lineage; current-code proof exercised the strict path cleanly | keep closed |
| YELLOW | H-006 | `scripts/harness_qualify.py` | gate_correct_expected | backstage operator interpretation | backstage qualifier can still run without `run_id`, so it is config/run-integrity advisory, not a full evidence-claim surface | document/keep scoped |
| GREEN | H-007 | doctrine + active docs | gate_correct_expected | operator trust | proving-lane law, controlled-variable rule, lineage tuple, and descriptive-only grade semantics are now loud in active harness docs | keep |
| GREEN | H-008 | simulator demotion | gate_correct_expected | whole harness nervous system | active commands, policy, CI, and working docs no longer let simulator compete with canonical paper harness | keep |

## 7) Root-Cause Decision
- `primary_cause_class`: cross-surface doctrine enforcement lag
- `secondary_cause_classes`:
  - reporting semantics drift
  - promotion lineage under-enforcement
  - duplicated realism-grade logic
- `why_not_downstream_symptom`:
  - the core harness itself is mostly strong; the open seams are in connectors above it
- `why_not_false_positive_gate`:
  - these are not fake alarms; they are real mismatches against the now-explicit lineage and proving-lane doctrine
- `confidence`: `high`

## 8) Surgical Fix Queue (Current Scope Only)
| Priority | Fix ID | Target Surface | Change Type | Risk of Drift | Proof Required | Rollback |
|---|---|---|---|---|---|---|
| ORANGE | P-001 | maker lag-verification continuity lane | forensic + live runtime truth + doc | medium | explain why doctrine-restored canonical run `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204` earned `0` maker submits by reaching the real `15-20s` maker band with `token_lag_not_verified_for_maker`, with special focus on per-token lag confidence, discovery-refresh continuity, and active-pair verification persistence | rollback not applicable; diagnosis-first packet |

## 9) Deferred Items Ledger
| Severity | Finding ID | Reason Deferred | Re-entry Trigger |
|---|---|---|---|
| YELLOW | H-006 | backstage `harness_qualify` scope is currently honest enough after simulator demotion | if operators start using it as primary proof instead of backstage qualification |
| GREEN | H-008 | simulator already parked off-path | only if future deletion/archive packet is requested |

## 10) Acceptance / Exit
- `go_no_go`: `GO` for leaving the harness connector lane and moving into maker participation / submit-scarcity forensics; `NO-GO` for claiming pilot-live readiness or maker-utilization all-clear
- `blocking_findings`:
  - none remaining in the harness connector box
- `required_next_proof_commands`:
  - artifact-backed per-token lag-verification / discovery-refresh forensics on `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
- `residual_known_limitations`:
  - config-only `paper_harness_audit` runs remain honest but lineage-incomplete
  - current-code runtime now matches the late-window maker doctrine, but maker participation is currently blocked before paper-stage pass
  - maker zero-submit reporting surfaces with empty/null payloads should not outrank the live event stream when participation collapses completely
- `single_next_action`:
  - maker lag-verification continuity forensic packet anchored on `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`

## 11) Final Operator Summary (Short)
- What failed:
  - not the core harness; the doctrine-restored current-code run failed above the healthy harness floor because the active near-expiry pair hit the real maker band with `token_lag_not_verified_for_maker`
- What was actually upstream:
  - connector doctrine hardened successfully; the remaining blocker is runtime behavior/policy at the per-token lag-verification continuity layer
- What is fixed now:
  - core harness semantics, simulator demotion, explicit proving lineage in `paper_harness_audit`, explicit descriptive-only realism-grade semantics in doctrine/active docs, promotion/report lineage connector closure
- What remains open:
  - maker submits are `0` on the doctrine-restored canonical run
  - paper-stage readiness is currently blocked, and pilot-live remains farther out
- Why this is safe (or not) for next runtime:
  - safe for continued canonical paper use
  - paper-stage all-clear is materially stronger than before
  - not yet clean enough to call pilot-live or maker-utilization all-clear
