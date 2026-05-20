# BRO Fixed Diagnostic Template (Run-Anchored)

Use this worksheet for all cross-system diagnosis passes. Fill every field. Avoid freeform-only conclusions.

## 1) Lineage Lock (Required)
- `timestamp_utc`:
- `git_commit`:
- `branch`:
- `config_path`:
- `profile_name`:
- `paper_enforce_setup_lock`:
- `paper_expected_profile_name`:
- `paper_expected_config_fingerprint_sha256`:
- `_meta.effective_config_sha256`:
- `setup_lock_match`:
- `run_id`:
- `comparison_anchors`:

## 2) Symptom Intake
- `primary_symptom`:
- `first_seen_at`:
- `scope` (token-specific / lane-specific / system-wide):
- `impact` (execution / valuation / capital / reporting / gate):
- `operator_observation`:

## 2A) Suspected-Issue Triage (Required For Gate / Blocker Claims)
- `lane_alive_checked` (yes / no / unknown):
- `lane_alive_evidence`:
- `valid_action_checked` (yes / no / unknown):
- `valid_action_evidence`:
- `success_signal_checked` (yes / no / unknown):
- `success_signal_evidence`:
- `gate_role_before_mutation` (defect / correctly_filtering_trash / unknown):
- `why_not_reject_volume_only`:

## 3) System Mapping (Vehicle Model)
- `brain` (strategy/executor/risk/order/wallet/tx/data/mode/report/gate/session):
- `harness` (config/decision/lifecycle/data/capital/evidence/safety):
- `connector_box` (decision/evidence/config-lock/capital/data):
- `fuse_or_gate` (exact gate or reject authority):
- `ground_truth_surface` (status/report/artifact source of truth):

## 3A) Semantic Contract Check
- `emitted_live_contract_names_checked`:
- `doctrine_boundary_concepts_checked`:
- `downstream_mirrors_present`:
- `descriptive_only_surfaces_checked`:
- `precedence_owner_if_conflict`:
- `semantic_drift_suspected`:
- `candidate_actionability_status` (actionable / non_actionable_geometry / unknown):
- `ownership_state_class` (scan / provisional_owned / committed_owned / resolve):
- `commitment_state_checked` (uncommitted / maker_live / taker_committed / accepted_exposure / settle_only):
- `challenger_relevance_state` (live / observational_only / none):

## 4) Evidence Bundle
- `commands_run`:
- `key_log_paths`:
- `status_rows_examined`:
- `event_rows_examined`:
- `report_artifacts_examined`:
- `counter_examples_checked`:
- `proof_strength` (VERIFIED / INFERRED / UNKNOWN):

## 5) Casualty Ledger (Ranked)
| Severity | Finding ID | Subsystem | Root Cause Type | Blast Radius | Evidence | Decision |
|---|---|---|---|---|---|---|
| RED/ORANGE/YELLOW/GREEN | | | wiring_defect / data_starvation / gate_correct_expected / contract_mismatch / unknown | token/lane/system | command+artifact refs | fix now / defer |

## 6) Root-Cause Decision
- `primary_cause_class`:
- `secondary_cause_classes`:
- `why_not_downstream_symptom`:
- `why_not_false_positive_gate`:
- `why_not_non_actionable_candidate_reject`:
- `reuse_vs_extinction_call` (reuse_as_refined_steel / extinct / unknown):
- `confidence` (high/medium/low):

## 7) Surgical Fix Queue (Current Scope Only)
| Priority | Fix ID | Target Surface | Change Type | Risk of Drift | Proof Required | Rollback |
|---|---|---|---|---|---|---|
| RED/ORANGE | | | code/config/doc/test | low/med/high | exact tests+gates | explicit switch/revert |

Rules:
1. Include only current-scope RED/ORANGE items.
2. Deferred YELLOW/GREEN must include rationale and non-blocking claim.
3. No broad refactor items in this table.
4. A suspected gate may not be escalated to RED/ORANGE from reject volume
   alone; Section `2A` must first prove lane death, lack of valid action, lack
   of success, or stronger evidence of false authority / mis-tuning.

## 8) Deferred Items Ledger
| Severity | Finding ID | Reason Deferred | Re-entry Trigger |
|---|---|---|---|

## 9) Acceptance / Exit
- `go_no_go`:
- `blocking_findings`:
- `required_next_proof_commands`:
- `residual_known_limitations`:
- `single_next_action`:

## 10) Final Operator Summary (Short)
- What failed:
- What was actually upstream:
- What is fixed now:
- What remains open:
- Why this is safe (or not) for next runtime:
