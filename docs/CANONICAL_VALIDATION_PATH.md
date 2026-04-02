# BRO Canonical Validation Path

BRO paper runtime and validation are doctrine-locked to one lifecycle path for this remediation phase.

## Canonical inputs
- Profile: `configs/profiles/paper_universal.yaml`
- Compose entrypoint: `docker compose` from repo root
- Env source: repo `.env`
- Canonical paper log root: `./logs_exec/paper_universal`
- Canonical paper state root: `./data/paper_universal/state.json`
- Session runner path: `scripts/canonical_paper_session.sh`
- Validation script (postrun phase): `scripts/canonical_paper_validation.sh`

## Canonical session command
```bash
./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25
```

The session runner is the only canonical lifecycle path and enforces explicit phases:
- `preflight`
- `start`
- `active`
- `validate_active`
- `stop`
- `validate_postrun`
- `archive_export`
- `complete`

## Postrun validation command
```bash
./scripts/canonical_paper_validation.sh <run_id> --session-phase validate_postrun --run-contract <path>
```

`<run_id>` is mandatory to preserve deterministic audit targeting.
`<path>` should point to `run_contract_<run_id>.json` in the canonical log root.

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

`soak_hardening_gate` runs in `gate_mode: reliability` from `ops/soak_budget.yaml` for canonical postrun hard-fail semantics.
Utilization-lane findings remain reported for operator diagnosis but do not block canonical reliability proof.

Paper-harness realism checks enforced in this path:
- post-only parity in paper gateway (crossed post-only maker orders reject)
- size-required paper fills (no implicit infinite-liquidity fallback)
- market-data source realism (`book_updates_ws_delta`, `book_updates_rest_ratio`)
- fill realism envelopes (`max_maker_fill_rate`, `max_taker_bonus_fill_rate`)

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
