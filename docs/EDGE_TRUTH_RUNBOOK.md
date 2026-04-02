# BRO Edge Truth Runbook

## Purpose
`edge_evaluation` is the canonical runtime truth artifact for edge measurement.

Hard rule:
- edge truth is measurement-only
- edge truth is not a control plane
- edge truth must never change runtime decisions

Outcome-truth boundary:
- Outcome interpretation is governed separately by `BRO_OUTCOME_TRUTH_DOCTRINE.txt`.
- `edge_evaluation` remains decision/execution-path evidence.
- `result` remains reserved/null in edge truth until outcome-linkage packet fields are introduced under explicit doctrine versioning.

## Canonical source
- Stream: `events_*.jsonl`
- Event type: `edge_evaluation`
- Scope: run-scoped canonical artifacts only (`run_id` + required closed `run_contract` bounds)

## Canonical schema
Required fields:
- `run_id`
- `token_id`
- `timestamp_utc`
- `stage`
- `time_remaining_sec`
- `fair_probability`
- `market_probability`
- `edge_value`
- `oracle_tick_age_sec`
- `latency_state`
- `maker_allowed` (bool)
- `taker_allowed` (bool)
- `action_taken` (`maker|taker|none`)
- `submitted` (bool)
- `filled` (bool)
- `evaluation_scope` (`maker|taker`)
- `cycle_index` (int >= 0)
- `decision_input_source` (nullable string)
- `decision_input_emulated` (bool)
- `decision_input_data_class` (`observed_live|observed_other|emulated|unknown`)

Conditionally required:
- `block_reason` is required when `action_taken=none`
- One opportunity key (`run_id`,`identity`,`cycle_index`,`evaluation_scope`) must map to exactly one row.

Nullable fields:
- `result`
- `order_id`
- `block_reason` (nullable only when action is not `none`)
- `target_ref` (non-sensitive stable identity for audits when `token_id` is redacted)

Provenance constraints:
- `result` must stay `null` for all rows in this packet.
- Opportunity-key uniqueness is enforced with identity priority:
  `token_id` when visible, otherwise `target_ref`.
- No decision action may silently rely on emulated decision input.
  If emulated decision input exists, it must be explicitly disclosed via
  `decision_input_emulated=true` and is treated as non-promotable for harness realism.

Canonical stage-policy (exact):
- `OBSERVE`: maker no, taker no
- `EVALUATE`: maker no, taker no
- `MAKER_POSITION`: maker yes, taker no
- `MAKER_TAKER_SELECTIVE`: maker yes, taker yes
- `SNIPER_PRIMARY`: maker no, taker yes
- `EXTREME_ONLY`: maker no, taker yes
- `EXPIRED`: maker no, taker no
- `UNKNOWN`: maker no, taker no

Canonical latency state vocabulary:
- `disarmed`
- `probation`
- `armed`

## Audit command
```bash
./.venv/bin/python scripts/edge_truth_audit.py \
  --config configs/profiles/paper_universal.yaml \
  --log-dir ./logs_exec/paper_universal \
  --run-id <run_id> \
  --session-phase validate_postrun \
  --run-contract ./logs_exec/paper_universal/run_contract_<run_id>.json \
  --max-lines-per-file 0
```

Determinism proof in canonical validation:
- `scripts/canonical_paper_validation.sh` runs `edge_truth_audit` twice (`edge_truth_audit` + `edge_truth_audit_replay`)
- canonical validation also replays non-edge validators (`paper_harness_audit`, `websocket_hardening_audit`, `time_discipline_audit`, `guardian_profile_audit`, `readiness_gate`, `nightly_soak_report`, `soak_hardening_gate`)
  plus `order_lifecycle_audit` and `outcome_truth_audit`
- `validation_summary.json` includes:
  - `validator_determinism_ok`
  - `edge_truth_determinism_ok`
  - `non_edge_determinism_ok`
  - `edge_records_sha256`
  - `replay_edge_records_sha256`
  - `replay_match`
  - structural consistency hashes for required fields, block-reason taxonomy, stage policy, and audit-rule set
  - `non_edge_determinism.validators.<validator>.primary_sha256`
  - `non_edge_determinism.validators.<validator>.replay_sha256`
  - `non_edge_determinism.validators.<validator>.replay_match`
- canonical postrun validation fails closed when `validator_determinism_ok=false`

## Failure interpretation
Examples:
- `block_reason_missing_for_no_action`: causality gap, invalid
- `action_with_invalid_edge_inputs:*`: fail-closed violation, invalid
- `stage_action_mismatch:*`: doctrine stage-policy violation, invalid
- `edge_non_deterministic_duplicate_key:*`: duplicate opportunity truth rows, invalid
- `edge_duplicate_opportunity_key:*`: multiple rows for one opportunity key, invalid
- `run_id_mismatch:*`: run-contamination inside run-scoped audit, invalid
- `result_must_be_null`: non-null result is forbidden in this packet

## Non-fabrication rule
- Unknown value -> `null`
- Never backfill inferred values
- Never substitute defaults to make records pass
