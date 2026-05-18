# BRO Canonical Validation Path

BRO paper runtime and validation are doctrine-locked to one lifecycle path for
this remediation phase.

Public operator path:
- `broctl paper -- --active-minutes <minutes> --wait-sec 25`

Backend authoritative engine path:
- `scripts/canonical_paper_session.sh`
- `scripts/canonical_paper_validation.sh`

## Canonical inputs
- Profile: `configs/profiles/paper_universal.yaml`
- Compose entrypoint: `docker compose` from repo root
- Env source: repo `.env`
- Canonical paper log root: `./logs_exec/paper_universal`
- Canonical paper state root: `./data/paper_universal/state.json`
- Backend session runner path: `scripts/canonical_paper_session.sh`
- Backend validation script (postrun phase): `scripts/canonical_paper_validation.sh`

## Public canonical paper command
```bash
broctl paper -- --active-minutes <minutes> --wait-sec 25
```

Supported front-of-house evidence-run window:
- `10` to `180` minutes

`broctl paper` is the sole public front door for canonical paper.
Only canonical paper surfaces participate in this validation path.

## Backend canonical session engine
```bash
./scripts/canonical_paper_session.sh --active-minutes <minutes> --wait-sec 25
```

The session runner remains the authoritative lifecycle engine and enforces explicit phases:
- `preflight`
- `start`
- `active`
- `validate_active`
- `stop`
- `validate_postrun`
- `archive_export`
- `complete`

## Backend postrun replay/forensics validation command
```bash
./scripts/canonical_paper_validation.sh <run_id> --session-phase validate_postrun --run-contract <path>
```

`<run_id>` is mandatory to preserve deterministic audit targeting.
`<path>` should point to `run_contract_<run_id>.json` in the canonical log root.
Every canonical rerun through this command now rewrites both:
- `validation_summary.json`
- `canonical_paper_validation.json`

Truth precedence after replay:
- `validation_summary.json` remains the validator exit/determinism owner
- `nightly_soak_report.json` remains the runtime-classification owner
- `readiness_gate.json` remains the stage-boundary owner
- `canonical_paper_validation.json` is the synchronized derived summary tying those surfaces together for downstream gating/harvest

Canonical postrun validators in this path:
- `paper_harness_audit`
- `paper_harness_audit_replay`
- `websocket_hardening_audit`
- `websocket_hardening_audit_replay`
- `time_discipline_audit` (phase-aware status freshness + timestamp-domain/time-policy semantics)
- `time_discipline_audit_replay`
- `guardian_profile_audit` (guardian launch-profile hardening invariants)
- `guardian_profile_audit_replay`
- `readiness_gate`
- `readiness_gate_replay`
- `nightly_soak_report`
- `nightly_soak_report_replay`
- `edge_truth_audit`
- `edge_truth_audit_replay` (same-run replay determinism proof)
- `order_lifecycle_audit`
- `order_lifecycle_audit_replay`
- `outcome_truth_audit`
- `outcome_truth_audit_replay`
- `soak_hardening_gate`
- `soak_hardening_gate_replay`

Semantic boundary and transport rule:
- `BRO_CANONICAL_DOCTRINE.txt` is the semantic root for validator-facing
  contract language.
- `paper_harness_audit` consumes `execution_realism_class`,
  `decision_input_*`, and `target_ref` as paper-realism-domain truth.
- `harness_realism_grade_semantics=descriptive_non_gating` and
  `harness_realism_grade_authority=non_authoritative` are descriptive audit
  metadata only for the canonical `paper_harness_audit` grade.
- `paper_claim_boundary` now carries only the current split:
  `decision_source_truth` for all decision rows and `action_source_truth` for
  action rows. No legacy alias remains in the current contract.
- transport/session identity surfaces such as `BRO_CANONICAL_SESSION_*`,
  `BRO_RUN_ID`, `BRO_GIT_COMMIT`, and `BRO_DOCKER_IMAGE_HASH` are control or
  lineage carriers only; they must not redefine row-level runtime semantics.

`soak_hardening_gate` runs in `gate_mode: reliability` from `ops/soak_budget.yaml` for canonical postrun hard-fail semantics.
Utilization-lane findings remain reported for operator diagnosis but do not block canonical reliability proof.

Paper-harness realism checks enforced in this path:
- post-only parity in paper gateway (crossed post-only maker orders reject)
- size-required paper fills (no implicit infinite-liquidity fallback)
- market-data liveliness floors (`book_updates_ws_delta`, `book_updates_total_delta`)
- action-row source purity in `paper_harness_audit`:
  clear `edge_evaluation` rows with `action_taken in {maker,taker}` must stay
  `book_source=ws`; whole-stream pair-truth health now rides explicit
  `pair_truth_missing_pair_row_ratio` / `pair_truth_missing_pair_count_max`
  support truth rather than REST-mix grading
- active target health semantics are authoritative-active-pair only:
  pending/prewarm candidates may be transport-watched for warm-up, and
  lifecycle watch tokens may remain for cleanup/settlement, but neither class
  may contribute active pair-truth failure, ws-slo degradation, or outage
  health findings in the canonical validation path
- maker submit opportunity semantics are lifecycle-aware:
  `phase_disallow_maker` plus lifecycle residue / settlement / cleanup rows are
  non-actionable opportunity loss only and must not inflate maker-submit
  minimums in the canonical validation path
- fill realism envelopes (`max_maker_fill_rate`, `max_taker_bonus_fill_rate`)
- proving-lineage tuple surfaced directly in `paper_harness_audit` output:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`

`harness_realism_grade` is descriptive only:
- non-gating
- non-authoritative
- not a substitute for findings, run integrity, determinism, or promotion proof
- `paper_harness_audit` is the only current top-level owner of
  `harness_realism_grade*`
- `nightly_soak_report` carries exercised-only realism separately under
  `exercised_harness_realism` with the same descriptive semantics and
  non-authoritative boundary
- canonical harness grade and nightly exercised realism are both descriptive,
  but they are not the same metric and must not share the same contract name

`validation_summary.json` includes determinism fields:
- `edge_truth_determinism_ok`
- `non_edge_determinism_ok`
- `validator_determinism_ok`
- `outcome_truth_usability` (observational-only; non-gating usability surface)

`edge_truth_determinism` contains:
- `edge_records_sha256`
- `replay_match`
- structural consistency hashes (`required_fields`, block-reason taxonomy, stage policy, audit-rule set)
- `edge_truth_determinism_ok` is fail-closed for edge replay.

`non_edge_determinism` contains normalized replay hashes per validator pair.
This includes `order_lifecycle_audit` replay parity for execution-substrate evidence consistency.
This also includes `outcome_truth_audit` replay parity for fixed-horizon outcome semantics.
`outcome_truth_usability` does not alter validator pass/fail gates.
It reports attribution usability and missing-reference recovery counts only.
It now includes both submit-scope and filled-cohort usability surfaces:
- submit-scope: `total_outcome_records`, `complete_outcome_records`, `attribution_usability_ratio`
- filled-cohort: `filled_total`, `filled_complete`, `filled_unknown`, `filled_complete_ratio`
- maker-linkage observability: `maker_edge_linkage_attempted_count|resolved_count|ambiguous_count|missing_count`

`outcome_truth_audit` now emits two observational lenses for taker analysis:
- fixed-horizon lens: existing `lane_outcome_truth` remains the canonical 5000ms directional observation surface
- commitment-window lens: `commitment_lane_outcome_truth.normal_taker` measures the last observed midpoint at or before `decision_ts + submit.sec_to_expiry`

Claim-boundary rule for the new commitment lens:
- it is still observational-only
- it does not prove settlement truth, ledger PnL, or live-venue equivalence
- it exists to keep commitment-style normal taker doctrine from being misread by the narrower 5000ms lens alone

## Reconciliation Reporting Boundary
- In paper mode, reconciliation may emit:
  - `verification_level=paper_sim_verified`
  - `verification_scope=paper_wallet_simulation_verified`
- `mismatch_ratio_semantics=paper_wallet_simulation`
- Those fields refer to paper-mode wallet/reconcile semantics only.
- They do **not** elevate any non-canonical shop tooling into the canonical
  proof lane.

## Promotion Evidence Boundary
- Promotion-grade evidence must be manifest-backed and lineage-complete.
- Required proving-lineage tuple:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`
- Required visible fighter identity:
  - `profile_name`
- Promotion connectors fail closed when artifact identity is incomplete,
  malformed, inconsistent across evidence artifacts, or not manifest-backed.
- Config-only / backstage harness audits remain useful for diagnosis, but they
  are not sufficient for promotion-grade claims.

`websocket_hardening_audit` evidence now includes:
- explicit `ordering_policy` surface validation
- `ordering_classification_totals` for `ordered|out_of_order|duplicate|revision|missing_source_time`

`time_discipline_audit` evidence now includes:
- required `time_policy` validation from status rows
- required event timestamp-domain surface validation:
  `ts_event_utc`, `ts_receive_utc`, `ts_source_utc`, `ts_decision_utc`
- cross-domain skew checks bounded by declared `skew_tolerance_ms`

`order_lifecycle_audit` evidence includes linkage counters for:
- `chainlink_tick -> edge_evaluation(action) -> order_submit -> fill`
- missing decision/order/ingest links (hard-fail findings)

Fail-closed rule:
- canonical validation treats any replay mismatch (edge or non-edge) as an execution error.
- canonical postrun determinism is authoritative from `validator_determinism_ok=true`.

## Data Root Resolution (Stage 0 Truth Lock)
- Runtime truth root is currently `./data/paper_universal/state.json`.
- `./data/data/paper_universal/state.json` is legacy/non-canonical host path from pre-normalization runs.
- Hygiene operations must not delete either path until reconciliation confirms no active runtime writes to legacy roots.

## Guardrails
- Non-canonical paper lifecycle entrypoints must be thin wrappers to `scripts/canonical_paper_session.sh` or fail fast.
- `scripts/deploy_paper_clean.sh` is internal-only for canonical session execution and requires canonical handshake context:
  - `BRO_INTERNAL_SESSION_CALL=1`
  - `BRO_CANONICAL_SESSION_TOKEN`
  - `BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST` with matching `session_token` and `session_phase=start`
- Direct operator launch of `executor.py` in paper mode is fail-fast unless canonical handshake verifies:
  - `BRO_CANONICAL_SESSION_CALL=1`
  - `BRO_CANONICAL_SESSION_TOKEN`
  - `BRO_CANONICAL_SESSION_CONTEXT_FILE` with matching `session_token` and authoritative phase
- Guardian authoritative control is context-bound and token-bound:
  - `--session-context-file` must resolve inside canonical log root
  - `--session-token` (or `BRO_CANONICAL_SESSION_TOKEN`) must match context `session_token`
  - `--require-authoritative-startup` fail-closes guardian startup when authoritative context is missing
  - guard-stop arming is blocked outside authoritative phases
- Validation tools consume explicit `session_phase` and may consume explicit `run_contract` for deterministic replay.
