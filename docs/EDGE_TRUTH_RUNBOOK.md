# BRO Edge Truth Runbook

## Purpose
`edge_evaluation` is the canonical runtime truth artifact for edge measurement.

Hard rule:
- edge truth is measurement-only
- edge truth is not a control plane
- edge truth must never change runtime decisions

Authority boundary:
- this file defines the edge-evaluation measurement/audit contract only
- it does not define fighter-specific runtime lifecycle or weapon policy
- current BRO fighter-specific runtime/lifecycle authority lives in
  `docs/DOCTRINE_RUNBOOK.md`
- active edge truth must follow the lifecycle-owned contract in
  `prodesk.edge_truth_contract`; legacy stage aliases may exist only as bounded
  compatibility readouts during migration

Outcome-truth boundary:
- Outcome interpretation is governed separately by `BRO_OUTCOME_TRUTH_DOCTRINE.txt`.
- `edge_evaluation` remains decision/execution-path evidence.
- `result` remains reserved/null in edge truth until outcome-linkage packet fields are introduced under explicit doctrine versioning.

Semantic contract alignment:
- `BRO_CANONICAL_DOCTRINE.txt` is the semantic root for emitted edge field names.
- `maker_phase_allowed` and `taker_phase_allowed` are lifecycle-phase permission facts for the row.
- `maker_gate_open` and `taker_gate_open` carry the lane-gate verdict beneath lifecycle phase.
- `open_order_cleanup_required`, `settlement_hold_required`, `unresolved_lifecycle_obligation`, and `cancel_fail_closed` are explicit lifecycle residue truth only; they must not backfill retired lane-permission aliases.
- `action_taken` is the emitted action choice for the row.
- `block_reason` is the emitted local stop reason for the row; later surfaces may
  map it into blocker lanes but may not rewrite its owner.
- `secondary_oracle_status` and `secondary_oracle_confirmation` are selection
  terms; they do not by themselves grant submit authority.
- `market_reference_basis`, `market_reference_confidence`,
  `market_reference_fallback_used`, and `market_reference_source_side` explain
  how reference truth was formed; they do not replace `market_reference_class`.
- `financial_posture_class` is lifecycle/risk posture only.
- `target_ref` is deterministic lineage identity only.
- `source_target_ref` is source/complement lineage identity only.
- `decision_input_source`, `decision_input_type`, `decision_input_emulated`, and
  `decision_input_data_class` are decision-input provenance terms, not global
  authority classes.

## Canonical source
- Stream: `events_*.jsonl`
- Event type: `edge_evaluation`
- Scope: run-scoped canonical artifacts only (`run_id` + required closed `run_contract` bounds)

## Canonical schema
Required fields:
- `run_id`
- `token_id`
- `timestamp_utc`
- `lifecycle_phase`
- `time_remaining_sec`
- `fair_probability`
- `market_probability`
- `edge_value`
- `oracle_tick_age_sec`
- `latency_state`
- `market_reference_mode`
- `market_reference_basis`
- `market_reference_confidence`
- `market_reference_fallback_used`
- `market_reference_source_side`
- `market_reference_class`
- `secondary_oracle_status`
- `secondary_oracle_confirmation`
- `financial_posture_class`
- `maker_phase_allowed` (bool)
- `taker_phase_allowed` (bool)
- `maker_gate_open` (bool)
- `taker_gate_open` (bool)
- `open_order_cleanup_required` (bool)
- `settlement_hold_required` (bool)
- `unresolved_lifecycle_obligation` (bool)
- `cancel_fail_closed` (bool)
- `action_taken` (`maker|taker|none`)
- `submitted` (bool)
- `filled` (bool)
- `evaluation_scope` (`maker|taker`)
- `cycle_index` (int >= 0)
- `decision_input_source` (nullable string)
- `decision_input_type` (`observed_live|observed_other|replayed|emulated|unknown`)
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
- `source_target_ref` (non-sensitive stable identity for source/complement linkage when present)

Provenance constraints:
- `result` must stay `null` for all rows in this packet.
- `market_reference_class` may legitimately be `authoritative` or `not_available`
  depending on emitted reference truth.
- `market_reference_confidence` current emitted values are
  `authoritative` or `none`.
- `market_reference_basis` current emitted values are
  `direct_book_midpoint`, `ws_recent_paired_touch`, or `missing`.
  Report-only `report_book_top_pair_backfill` remains a downstream reconstructed
  basis label, not a live emitted edge value.
- `secondary_oracle_status` current emitted values are
  `confirmed`, `direction_mismatch`, `disabled`, or `unknown`.
  Compatibility input `available` must normalize to `unknown` before emission.
- `financial_posture_class` current emitted values are
  `NORMAL`, `PREEXPIRY_REDUCE_ONLY`, `HARD_DEGRADED_REDUCE_ONLY`, or
  `HALT_NEW_RISK`.
- Historical artifact lineage may still contain `reduce_only_recovery_*` or
  `preexpiry_emergency_taker_*` fields, but those are no longer part of the
  current emitted edge-truth contract.
- Opportunity-key uniqueness is enforced with identity priority:
  `token_id` when visible, otherwise `target_ref`.
- No decision action may silently rely on emulated decision input.
  If emulated decision input exists, it must be explicitly disclosed via
  `decision_input_emulated=true` and is treated as non-promotable for harness realism.

Canonical audit lifecycle-phase policy (exact):
- `scan`: maker no, taker no
- `prepare`: maker no, taker no
- `maker_window`: maker yes, taker no
- `taker_window`: maker no, taker yes
- `resolve`: maker no, taker no

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
