#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

CONFIG_PATH="./configs/profiles/paper_universal.yaml"
LOG_DIR="./logs_exec/paper_universal"
RUN_ID="${1:-}"
MAX_LINES_PER_FILE="${BRO_REPORT_MAX_LINES_PER_FILE:-50000}"
SESSION_PHASE="validate_postrun"
RUN_CONTRACT_PATH=""

if [[ -z "${RUN_ID}" ]]; then
  echo "canonical_paper_validation requires explicit run_id argument" >&2
  exit 2
fi

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-phase)
      SESSION_PHASE="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="${2:-}"
      shift 2
      ;;
    --run-contract)
      RUN_CONTRACT_PATH="${2:-}"
      shift 2
      ;;
    --max-lines-per-file)
      MAX_LINES_PER_FILE="${2:-${MAX_LINES_PER_FILE}}"
      shift 2
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${RUN_CONTRACT_PATH}" ]]; then
  echo "canonical_paper_validation requires explicit --run-contract path" >&2
  exit 2
fi
if [[ ! -f "${RUN_CONTRACT_PATH}" ]]; then
  echo "canonical_paper_validation run_contract missing: ${RUN_CONTRACT_PATH}" >&2
  exit 2
fi

if [[ ! -x "./.venv/bin/python" ]]; then
  echo "missing python interpreter: ./.venv/bin/python" >&2
  exit 127
fi

RUN_CONTRACT_ARGS=(--run-contract "${RUN_CONTRACT_PATH}")

OUT_DIR="${LOG_DIR}/reports/${RUN_ID}"
mkdir -p "${OUT_DIR}"

echo "[canonical] config=${CONFIG_PATH}"
echo "[canonical] log_dir=${LOG_DIR}"
echo "[canonical] run_id=${RUN_ID}"
echo "[canonical] session_phase=${SESSION_PHASE}"
if [[ -n "${RUN_CONTRACT_PATH}" ]]; then
  echo "[canonical] run_contract=${RUN_CONTRACT_PATH}"
fi

run_validator() {
  local name="$1"
  shift
  local rc=0
  if "$@"; then
    rc=0
  else
    rc=$?
  fi
  echo "[canonical] validator_${name}_exit=${rc}"
  return "${rc}"
}

paper_rc=0
paper_replay_rc=0
readiness_rc=0
readiness_replay_rc=0
nightly_rc=0
nightly_replay_rc=0
edge_truth_rc=0
edge_truth_replay_rc=0
soak_rc=0
soak_replay_rc=0
websocket_hardening_rc=0
websocket_hardening_replay_rc=0
time_discipline_rc=0
time_discipline_replay_rc=0
guardian_profile_rc=0
guardian_profile_replay_rc=0
order_lifecycle_rc=0
order_lifecycle_replay_rc=0
outcome_truth_rc=0
outcome_truth_replay_rc=0
had_policy_fail=0
had_execution_error=0

if run_validator "paper_harness_audit" \
  ./.venv/bin/python scripts/paper_harness_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --min-status-rows 1 \
    --max-status-age-sec 3153600000 \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/paper_harness_audit.json"; then
  paper_rc=0
else
  paper_rc=$?
fi

if run_validator "paper_harness_audit_replay" \
  ./.venv/bin/python scripts/paper_harness_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --min-status-rows 1 \
    --max-status-age-sec 3153600000 \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/paper_harness_audit_replay.json"; then
  paper_replay_rc=0
else
  paper_replay_rc=$?
fi

if run_validator "websocket_hardening_audit" \
  ./.venv/bin/python scripts/websocket_hardening_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/websocket_hardening_audit.json"; then
  websocket_hardening_rc=0
else
  websocket_hardening_rc=$?
fi

if run_validator "websocket_hardening_audit_replay" \
  ./.venv/bin/python scripts/websocket_hardening_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/websocket_hardening_audit_replay.json"; then
  websocket_hardening_replay_rc=0
else
  websocket_hardening_replay_rc=$?
fi

time_discipline_max_status_age_sec="3153600000"
if [[ "${SESSION_PHASE}" == "validate_active" ]]; then
  time_discipline_max_status_age_sec="180"
fi

if run_validator "time_discipline_audit" \
  ./.venv/bin/python scripts/time_discipline_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-status-age-sec "${time_discipline_max_status_age_sec}" \
    --min-status-rows 1 \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/time_discipline_audit.json"; then
  time_discipline_rc=0
else
  time_discipline_rc=$?
fi

if run_validator "time_discipline_audit_replay" \
  ./.venv/bin/python scripts/time_discipline_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-status-age-sec "${time_discipline_max_status_age_sec}" \
    --min-status-rows 1 \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/time_discipline_audit_replay.json"; then
  time_discipline_replay_rc=0
else
  time_discipline_replay_rc=$?
fi

if run_validator "guardian_profile_audit" \
  ./.venv/bin/python scripts/guardian_profile_audit.py \
    --compose ./docker-compose.yml \
    --config "${CONFIG_PATH}" \
    --out "${OUT_DIR}/guardian_profile_audit.json"; then
  guardian_profile_rc=0
else
  guardian_profile_rc=$?
fi

if run_validator "guardian_profile_audit_replay" \
  ./.venv/bin/python scripts/guardian_profile_audit.py \
    --compose ./docker-compose.yml \
    --config "${CONFIG_PATH}" \
    --out "${OUT_DIR}/guardian_profile_audit_replay.json"; then
  guardian_profile_replay_rc=0
else
  guardian_profile_replay_rc=$?
fi

if run_validator "readiness_gate" \
  ./.venv/bin/python scripts/readiness_gate.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --policy ./ops/ramp_policy.yaml \
    --report-max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/readiness_gate.json"; then
  readiness_rc=0
else
  readiness_rc=$?
fi

if run_validator "readiness_gate_replay" \
  ./.venv/bin/python scripts/readiness_gate.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --policy ./ops/ramp_policy.yaml \
    --report-max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/readiness_gate_replay.json"; then
  readiness_replay_rc=0
else
  readiness_replay_rc=$?
fi

if run_validator "edge_truth_audit" \
  ./.venv/bin/python scripts/edge_truth_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file 0 \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/edge_truth_audit.json"; then
  edge_truth_rc=0
else
  edge_truth_rc=$?
fi

if run_validator "edge_truth_audit_replay" \
  ./.venv/bin/python scripts/edge_truth_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file 0 \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/edge_truth_audit_replay.json"; then
  edge_truth_replay_rc=0
else
  edge_truth_replay_rc=$?
fi

if run_validator "order_lifecycle_audit" \
  ./.venv/bin/python scripts/order_lifecycle_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/order_lifecycle_audit.json"; then
  order_lifecycle_rc=0
else
  order_lifecycle_rc=$?
fi

if run_validator "order_lifecycle_audit_replay" \
  ./.venv/bin/python scripts/order_lifecycle_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/order_lifecycle_audit_replay.json"; then
  order_lifecycle_replay_rc=0
else
  order_lifecycle_replay_rc=$?
fi

if run_validator "outcome_truth_audit" \
  ./.venv/bin/python scripts/outcome_truth_audit.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --run-contract "${RUN_CONTRACT_PATH}" \
    --policy ./ops/outcome_truth_policy.json \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file 0 \
    --out "${OUT_DIR}/outcome_truth_audit.json" \
    --records-out "${OUT_DIR}/outcome_truth_records.jsonl"; then
  outcome_truth_rc=0
else
  outcome_truth_rc=$?
fi

if run_validator "outcome_truth_audit_replay" \
  ./.venv/bin/python scripts/outcome_truth_audit.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --run-contract "${RUN_CONTRACT_PATH}" \
    --policy ./ops/outcome_truth_policy.json \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file 0 \
    --out "${OUT_DIR}/outcome_truth_audit_replay.json" \
    --records-out "${OUT_DIR}/outcome_truth_records.jsonl"; then
  outcome_truth_replay_rc=0
else
  outcome_truth_replay_rc=$?
fi

if run_validator "nightly_soak_report" \
  ./.venv/bin/python scripts/nightly_soak_report.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/nightly_soak_report.json"; then
  nightly_rc=0
else
  nightly_rc=$?
fi

if run_validator "nightly_soak_report_replay" \
  ./.venv/bin/python scripts/nightly_soak_report.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --max-lines-per-file "${MAX_LINES_PER_FILE}" \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/nightly_soak_report_replay.json"; then
  nightly_replay_rc=0
else
  nightly_replay_rc=$?
fi

if run_validator "soak_hardening_gate" \
  ./.venv/bin/python scripts/soak_hardening_gate.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --budget ./ops/soak_budget.yaml \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/soak_hardening_gate.json"; then
  soak_rc=0
else
  soak_rc=$?
fi

if run_validator "soak_hardening_gate_replay" \
  ./.venv/bin/python scripts/soak_hardening_gate.py \
    --log-dir "${LOG_DIR}" \
    --run-id "${RUN_ID}" \
    --session-phase "${SESSION_PHASE}" \
    --budget ./ops/soak_budget.yaml \
    "${RUN_CONTRACT_ARGS[@]}" \
    --out "${OUT_DIR}/soak_hardening_gate_replay.json"; then
  soak_replay_rc=0
else
  soak_replay_rc=$?
fi

for rc in "${paper_rc}" "${paper_replay_rc}" "${websocket_hardening_rc}" "${websocket_hardening_replay_rc}" "${time_discipline_rc}" "${time_discipline_replay_rc}" "${guardian_profile_rc}" "${guardian_profile_replay_rc}" "${readiness_rc}" "${readiness_replay_rc}" "${nightly_rc}" "${nightly_replay_rc}" "${edge_truth_rc}" "${edge_truth_replay_rc}" "${order_lifecycle_rc}" "${order_lifecycle_replay_rc}" "${outcome_truth_rc}" "${outcome_truth_replay_rc}" "${soak_rc}" "${soak_replay_rc}"; do
  if [[ "${rc}" -eq 2 ]]; then
    had_policy_fail=1
  elif [[ "${rc}" -ne 0 ]]; then
    had_execution_error=1
  fi
done

summary_path="${OUT_DIR}/validation_summary.json"
edge_truth_determinism_json='{"determinism_ok":false,"edge_records_sha256":"","replay_edge_records_sha256":"","replay_match":false,"structural_consistency":{"required_fields_sha256":"","block_reason_taxonomy_sha256":"","stage_policy_sha256":"","audit_rule_set_sha256":"","replay_required_fields_match":false,"replay_block_reason_taxonomy_match":false,"replay_stage_policy_match":false,"replay_audit_rule_set_match":false}}'
if [[ -f "${OUT_DIR}/edge_truth_audit.json" && -f "${OUT_DIR}/edge_truth_audit_replay.json" ]]; then
  edge_truth_determinism_json="$(./.venv/bin/python - "${OUT_DIR}/edge_truth_audit.json" "${OUT_DIR}/edge_truth_audit_replay.json" <<'PY'
import json
import pathlib
import sys

primary = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
replay = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))

comparison_keys = (
    "ok",
    "finding_count",
    "findings",
    "edge_records_sha256",
    "required_fields_sha256",
    "block_reason_taxonomy_sha256",
    "stage_policy_sha256",
    "audit_rule_set_sha256",
)
primary_cmp = {key: primary.get(key) for key in comparison_keys}
replay_cmp = {key: replay.get(key) for key in comparison_keys}

payload = {
    "determinism_ok": bool(
        bool(str(primary.get("edge_records_sha256") or "").strip())
        and bool(str(replay.get("edge_records_sha256") or "").strip())
        and bool(primary_cmp == replay_cmp)
        and str(primary.get("required_fields_sha256") or "") == str(replay.get("required_fields_sha256") or "")
        and str(primary.get("block_reason_taxonomy_sha256") or "") == str(replay.get("block_reason_taxonomy_sha256") or "")
        and str(primary.get("stage_policy_sha256") or "") == str(replay.get("stage_policy_sha256") or "")
        and str(primary.get("audit_rule_set_sha256") or "") == str(replay.get("audit_rule_set_sha256") or "")
    ),
    "edge_records_sha256": str(primary.get("edge_records_sha256") or ""),
    "replay_edge_records_sha256": str(replay.get("edge_records_sha256") or ""),
    "replay_match": bool(primary_cmp == replay_cmp),
    "structural_consistency": {
        "required_fields_sha256": str(primary.get("required_fields_sha256") or ""),
        "block_reason_taxonomy_sha256": str(primary.get("block_reason_taxonomy_sha256") or ""),
        "stage_policy_sha256": str(primary.get("stage_policy_sha256") or ""),
        "audit_rule_set_sha256": str(primary.get("audit_rule_set_sha256") or ""),
        "replay_required_fields_match": str(primary.get("required_fields_sha256") or "") == str(replay.get("required_fields_sha256") or ""),
        "replay_block_reason_taxonomy_match": str(primary.get("block_reason_taxonomy_sha256") or "") == str(replay.get("block_reason_taxonomy_sha256") or ""),
        "replay_stage_policy_match": str(primary.get("stage_policy_sha256") or "") == str(replay.get("stage_policy_sha256") or ""),
        "replay_audit_rule_set_match": str(primary.get("audit_rule_set_sha256") or "") == str(replay.get("audit_rule_set_sha256") or ""),
    },
}
print(json.dumps(payload, sort_keys=True))
PY
)"
fi
edge_truth_determinism_ok="$(
  EDGE_TRUTH_DETERMINISM_JSON="${edge_truth_determinism_json}" ./.venv/bin/python - <<'PY'
import json
import os

raw = os.environ.get("EDGE_TRUTH_DETERMINISM_JSON", "")
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print("false")
    raise SystemExit(0)
print("true" if bool(payload.get("determinism_ok", False)) else "false")
PY
)"
if [[ "${edge_truth_determinism_ok}" != "true" ]]; then
  had_execution_error=1
  echo "[canonical] edge_truth_determinism_ok=false"
fi
non_edge_determinism_json='{"determinism_ok":false,"validators":{}}'
if non_edge_determinism_json="$(./.venv/bin/python scripts/validator_replay_fingerprint.py \
  --pair paper_harness_audit "${OUT_DIR}/paper_harness_audit.json" "${OUT_DIR}/paper_harness_audit_replay.json" \
  --pair websocket_hardening_audit "${OUT_DIR}/websocket_hardening_audit.json" "${OUT_DIR}/websocket_hardening_audit_replay.json" \
  --pair time_discipline_audit "${OUT_DIR}/time_discipline_audit.json" "${OUT_DIR}/time_discipline_audit_replay.json" \
  --pair guardian_profile_audit "${OUT_DIR}/guardian_profile_audit.json" "${OUT_DIR}/guardian_profile_audit_replay.json" \
  --pair readiness_gate "${OUT_DIR}/readiness_gate.json" "${OUT_DIR}/readiness_gate_replay.json" \
  --pair nightly_soak_report "${OUT_DIR}/nightly_soak_report.json" "${OUT_DIR}/nightly_soak_report_replay.json" \
  --pair order_lifecycle_audit "${OUT_DIR}/order_lifecycle_audit.json" "${OUT_DIR}/order_lifecycle_audit_replay.json" \
  --pair outcome_truth_audit "${OUT_DIR}/outcome_truth_audit.json" "${OUT_DIR}/outcome_truth_audit_replay.json" \
  --pair soak_hardening_gate "${OUT_DIR}/soak_hardening_gate.json" "${OUT_DIR}/soak_hardening_gate_replay.json")"; then
  :
else
  had_execution_error=1
  echo "[canonical] non_edge_determinism_compute_error=true"
fi
non_edge_determinism_ok="$(
  NON_EDGE_DETERMINISM_JSON="${non_edge_determinism_json}" ./.venv/bin/python - <<'PY'
import json
import os

raw = os.environ.get("NON_EDGE_DETERMINISM_JSON", "")
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print("false")
    raise SystemExit(0)
print("true" if bool(payload.get("determinism_ok", False)) else "false")
PY
)"
if [[ "${non_edge_determinism_ok}" != "true" ]]; then
  had_execution_error=1
  echo "[canonical] non_edge_determinism_ok=false"
fi
validator_determinism_ok="false"
if [[ "${edge_truth_determinism_ok}" == "true" && "${non_edge_determinism_ok}" == "true" ]]; then
  validator_determinism_ok="true"
else
  had_execution_error=1
  echo "[canonical] validator_determinism_ok=false"
fi
outcome_truth_usability_json='{"available":false,"total_outcome_records":0,"complete_outcome_records":0,"partial_outcome_records":0,"unknown_outcome_records":0,"filled_total":0,"filled_complete":0,"filled_unknown":0,"filled_complete_ratio":0.0,"decision_reference_recovered_count":0,"eval_reference_recovered_count":0,"decision_reference_missing_count":0,"eval_reference_missing_count":0,"maker_edge_linkage_attempted_count":0,"maker_edge_linkage_resolved_count":0,"maker_edge_linkage_ambiguous_count":0,"maker_edge_linkage_missing_count":0,"recoverable_but_missing_count":0,"attribution_usability_ratio":0.0,"complete_classification_ratio":0.0,"observational_only":true,"non_gating":true}'
if [[ -f "${OUT_DIR}/outcome_truth_audit.json" ]]; then
  if outcome_truth_usability_json="$(./.venv/bin/python - "${OUT_DIR}/outcome_truth_audit.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)

def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)

out = {
    "available": True,
    "total_outcome_records": _as_int(payload.get("total_outcome_records", 0)),
    "complete_outcome_records": _as_int(payload.get("complete_outcome_records", 0)),
    "partial_outcome_records": _as_int(payload.get("partial_outcome_records", 0)),
    "unknown_outcome_records": _as_int(payload.get("unknown_outcome_records", 0)),
    "filled_total": _as_int(payload.get("filled_total", 0)),
    "filled_complete": _as_int(payload.get("filled_complete", 0)),
    "filled_unknown": _as_int(payload.get("filled_unknown", 0)),
    "filled_complete_ratio": _as_float(payload.get("filled_complete_ratio", 0.0)),
    "decision_reference_recovered_count": _as_int(payload.get("decision_reference_recovered_count", 0)),
    "eval_reference_recovered_count": _as_int(payload.get("eval_reference_recovered_count", 0)),
    "decision_reference_missing_count": _as_int(payload.get("decision_reference_missing_count", 0)),
    "eval_reference_missing_count": _as_int(payload.get("eval_reference_missing_count", 0)),
    "maker_edge_linkage_attempted_count": _as_int(payload.get("maker_edge_linkage_attempted_count", 0)),
    "maker_edge_linkage_resolved_count": _as_int(payload.get("maker_edge_linkage_resolved_count", 0)),
    "maker_edge_linkage_ambiguous_count": _as_int(payload.get("maker_edge_linkage_ambiguous_count", 0)),
    "maker_edge_linkage_missing_count": _as_int(payload.get("maker_edge_linkage_missing_count", 0)),
    "recoverable_but_missing_count": _as_int(payload.get("recoverable_but_missing_count", 0)),
    "attribution_usability_ratio": _as_float(payload.get("attribution_usability_ratio", 0.0)),
    "complete_classification_ratio": _as_float(payload.get("complete_classification_ratio", 0.0)),
    "observational_only": True,
    "non_gating": True,
}
print(json.dumps(out, sort_keys=True))
PY
)"; then
    :
  else
    outcome_truth_usability_json='{"available":false,"total_outcome_records":0,"complete_outcome_records":0,"partial_outcome_records":0,"unknown_outcome_records":0,"filled_total":0,"filled_complete":0,"filled_unknown":0,"filled_complete_ratio":0.0,"decision_reference_recovered_count":0,"eval_reference_recovered_count":0,"decision_reference_missing_count":0,"eval_reference_missing_count":0,"maker_edge_linkage_attempted_count":0,"maker_edge_linkage_resolved_count":0,"maker_edge_linkage_ambiguous_count":0,"maker_edge_linkage_missing_count":0,"recoverable_but_missing_count":0,"attribution_usability_ratio":0.0,"complete_classification_ratio":0.0,"observational_only":true,"non_gating":true}'
  fi
fi
overall_rc=0
if [[ "${had_execution_error}" -eq 1 ]]; then
  overall_rc=3
elif [[ "${had_policy_fail}" -eq 1 ]]; then
  overall_rc=2
fi

overall_ok=true
if [[ "${overall_rc}" -ne 0 ]]; then
  overall_ok=false
fi
cat > "${summary_path}" <<EOF
{
  "run_id": "${RUN_ID}",
  "session_phase": "${SESSION_PHASE}",
  "log_dir": "${LOG_DIR}",
  "run_contract_path": "${RUN_CONTRACT_PATH}",
  "overall_exit_code": ${overall_rc},
  "ok": ${overall_ok},
  "validator_exit_codes": {
    "paper_harness_audit": ${paper_rc},
    "paper_harness_audit_replay": ${paper_replay_rc},
    "websocket_hardening_audit": ${websocket_hardening_rc},
    "websocket_hardening_audit_replay": ${websocket_hardening_replay_rc},
    "time_discipline_audit": ${time_discipline_rc},
    "time_discipline_audit_replay": ${time_discipline_replay_rc},
    "guardian_profile_audit": ${guardian_profile_rc},
    "guardian_profile_audit_replay": ${guardian_profile_replay_rc},
    "readiness_gate": ${readiness_rc},
    "readiness_gate_replay": ${readiness_replay_rc},
    "nightly_soak_report": ${nightly_rc},
    "nightly_soak_report_replay": ${nightly_replay_rc},
    "edge_truth_audit": ${edge_truth_rc},
    "edge_truth_audit_replay": ${edge_truth_replay_rc},
    "order_lifecycle_audit": ${order_lifecycle_rc},
    "order_lifecycle_audit_replay": ${order_lifecycle_replay_rc},
    "outcome_truth_audit": ${outcome_truth_rc},
    "outcome_truth_audit_replay": ${outcome_truth_replay_rc},
    "soak_hardening_gate": ${soak_rc},
    "soak_hardening_gate_replay": ${soak_replay_rc}
  },
  "edge_truth_determinism_ok": ${edge_truth_determinism_ok},
  "non_edge_determinism_ok": ${non_edge_determinism_ok},
  "validator_determinism_ok": ${validator_determinism_ok},
  "outcome_truth_usability": ${outcome_truth_usability_json},
  "edge_truth_determinism": ${edge_truth_determinism_json},
  "non_edge_determinism": ${non_edge_determinism_json}
}
EOF

./.venv/bin/python - "${RUN_ID}" "${OUT_DIR}" "${overall_rc}" "${OUT_DIR}/canonical_paper_validation.json" "${SESSION_PHASE}" <<'PY'
import pathlib
import sys

from scripts.canonical_paper_session import write_postrun_validation_artifact

run_id = str(sys.argv[1] or "").strip()
report_dir = pathlib.Path(sys.argv[2]).resolve()
script_exit_code = int(str(sys.argv[3] or "0").strip() or "0")
artifact_path = pathlib.Path(sys.argv[4]).resolve()
session_phase = str(sys.argv[5] or "validate_postrun").strip() or "validate_postrun"
write_postrun_validation_artifact(
    run_id=run_id,
    report_dir=report_dir,
    script_exit_code=script_exit_code,
    artifact_path=artifact_path,
    session_phase=session_phase,
)
PY

echo "[canonical] validation_summary=${summary_path}"
echo "[canonical] report_dir=${OUT_DIR}"
exit "${overall_rc}"
