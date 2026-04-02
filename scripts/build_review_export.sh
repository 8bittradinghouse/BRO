#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/build_review_export.sh --run-id <uuid> [--run-contract <path>] [--scenario-root <path>] [--timestamp <YYYYMMDDTHHMMSSZ>]

Description:
  Deterministically builds review export artifacts:
  - BRO_repo_snapshot_<ts>.zip
  - BRO_run_evidence_<runid>_<ts>.zip
  - BRO_foundation_scenarios_<ts>.zip (optional, when --scenario-root is provided)
  - BRO_consultant_artifacts_<ts>.zip
  - BRO_export_manifest_<ts>.txt
  - BRO_checksums_<ts>.txt
  - BRO_payload_checksums_<ts>.txt
  - BRO_export_audit_<ts>.json
EOF
}

RUN_ID=""
RUN_CONTRACT=""
SCENARIO_ROOT=""
TS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --run-contract)
      RUN_CONTRACT="${2:-}"
      shift 2
      ;;
    --scenario-root)
      SCENARIO_ROOT="${2:-}"
      shift 2
      ;;
    --timestamp)
      TS="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$RUN_ID" ]]; then
  echo "--run-id is required" >&2
  usage >&2
  exit 2
fi

if [[ -z "$TS" ]]; then
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
fi

if [[ -z "$RUN_CONTRACT" ]]; then
  RUN_CONTRACT="./logs_exec/paper_universal/run_contract_${RUN_ID}.json"
fi

if [[ ! -f "$RUN_CONTRACT" ]]; then
  echo "run contract missing: $RUN_CONTRACT" >&2
  exit 2
fi

SCENARIO_ROOT_ABS=""
if [[ -n "$SCENARIO_ROOT" ]]; then
  SCENARIO_ROOT_ABS="$(realpath "$SCENARIO_ROOT")"
  if [[ ! -d "$SCENARIO_ROOT_ABS" ]]; then
    echo "scenario root missing: $SCENARIO_ROOT" >&2
    exit 2
  fi
fi

RUN_CONTRACT_ABS="$(realpath "$RUN_CONTRACT")"
SESSION_ID="$(jq -r '.session_id // empty' "$RUN_CONTRACT_ABS")"
MANIFEST_ABS="$(jq -r '.manifest_path // empty' "$RUN_CONTRACT_ABS")"
EVENTS_SLICE_ABS="$(jq -r '.events_slice_path // empty' "$RUN_CONTRACT_ABS")"
STATUS_SLICE_ABS="$(jq -r '.status_slice_path // empty' "$RUN_CONTRACT_ABS")"
ERRORS_SLICE_ABS="$(jq -r '.errors_slice_path // empty' "$RUN_CONTRACT_ABS")"

if [[ -z "$SESSION_ID" || -z "$MANIFEST_ABS" || -z "$EVENTS_SLICE_ABS" || -z "$STATUS_SLICE_ABS" || -z "$ERRORS_SLICE_ABS" ]]; then
  echo "run contract missing required paths/session_id: $RUN_CONTRACT_ABS" >&2
  exit 2
fi

REPORT_DIR_ABS="$ROOT_DIR/logs_exec/paper_universal/reports/${RUN_ID}"
SESSION_STATE_ABS="$ROOT_DIR/logs_exec/paper_universal/sessions/${SESSION_ID}/session_state.json"
VALIDATION_SUMMARY_ABS="$REPORT_DIR_ABS/validation_summary.json"

for required_path in "$MANIFEST_ABS" "$EVENTS_SLICE_ABS" "$STATUS_SLICE_ABS" "$ERRORS_SLICE_ABS" "$REPORT_DIR_ABS" "$SESSION_STATE_ABS" "$VALIDATION_SUMMARY_ABS"; do
  if [[ ! -e "$required_path" ]]; then
    echo "required evidence missing: $required_path" >&2
    exit 2
  fi
done

VALIDATION_EXIT_CODE="$(jq -r '.overall_exit_code // "unknown"' "$VALIDATION_SUMMARY_ABS")"
VALIDATION_STATUS="unknown"
case "$VALIDATION_EXIT_CODE" in
  0) VALIDATION_STATUS="pass" ;;
  2) VALIDATION_STATUS="policy_failed" ;;
  3) VALIDATION_STATUS="execution_error" ;;
esac

EXP_DIR="$ROOT_DIR/exports"
TMP_DIR="$(mktemp -d "/tmp/bro_export_${TS}_XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$EXP_DIR"

REPO_ZIP="$EXP_DIR/BRO_repo_snapshot_${TS}.zip"
RUN_ZIP="$EXP_DIR/BRO_run_evidence_${RUN_ID}_${TS}.zip"
SCENARIO_ZIP="$EXP_DIR/BRO_foundation_scenarios_${TS}.zip"
CONSULT_ZIP="$EXP_DIR/BRO_consultant_artifacts_${TS}.zip"
MANIFEST_TXT="$EXP_DIR/BRO_export_manifest_${TS}.txt"
CHECKSUMS_TXT="$EXP_DIR/BRO_checksums_${TS}.txt"
PAYLOAD_CHECKSUMS_TXT="$EXP_DIR/BRO_payload_checksums_${TS}.txt"
INVENTORY_TXT="$EXP_DIR/BRO_file_inventory_${TS}.txt"
VALIDATION_CMDS_TXT="$EXP_DIR/BRO_validation_commands_${TS}.txt"
ZIP_INVENTORY_TXT="$EXP_DIR/BRO_zip_inventory_${TS}.txt"
AUDIT_JSON="$EXP_DIR/BRO_export_audit_${TS}.json"

rm -f "$REPO_ZIP" "$RUN_ZIP" "$SCENARIO_ZIP" "$CONSULT_ZIP" "$MANIFEST_TXT" "$CHECKSUMS_TXT" "$PAYLOAD_CHECKSUMS_TXT" "$INVENTORY_TXT" "$VALIDATION_CMDS_TXT" "$ZIP_INVENTORY_TXT" "$AUDIT_JSON"

echo "[export] building repo snapshot zip"
(
  cd "$ROOT_DIR"
  zip -rq "$REPO_ZIP" . \
    -x '.git/*' \
    -x '.venv/*' \
    -x '*/__pycache__/*' \
    -x '__pycache__/*' \
    -x 'logs_exec/*' \
    -x 'data/*' \
    -x 'archives/*' \
    -x 'exports/*' \
    -x '*.zip' \
    -x '.env' \
    -x '*/.env' \
    -x 'node_modules/*' \
    -x '*/node_modules/*'
)

echo "[export] staging run evidence"
RUN_STAGE="$TMP_DIR/run_evidence"
mkdir -p "$RUN_STAGE"

copy_to_stage() {
  local src_abs="$1"
  local rel_path
  rel_path="$(realpath --relative-to="$ROOT_DIR" "$src_abs")"
  if [[ "$rel_path" == ../* ]]; then
    echo "path escapes repo root: $src_abs" >&2
    exit 2
  fi
  mkdir -p "$RUN_STAGE/$(dirname "$rel_path")"
  if [[ -d "$src_abs" ]]; then
    cp -r "$src_abs/." "$RUN_STAGE/$rel_path/"
  else
    cp "$src_abs" "$RUN_STAGE/$rel_path"
  fi
}

copy_to_stage "$RUN_CONTRACT_ABS"
copy_to_stage "$MANIFEST_ABS"
copy_to_stage "$EVENTS_SLICE_ABS"
copy_to_stage "$STATUS_SLICE_ABS"
copy_to_stage "$ERRORS_SLICE_ABS"
copy_to_stage "$REPORT_DIR_ABS"
copy_to_stage "$SESSION_STATE_ABS"

(
  cd "$RUN_STAGE"
  zip -rq "$RUN_ZIP" .
)

SCENARIO_SIZE="0"
if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
  echo "[export] staging foundation scenario evidence"
  SCENARIO_STAGE="$TMP_DIR/foundation_scenarios"
  mkdir -p "$SCENARIO_STAGE"
  cp -r "$SCENARIO_ROOT_ABS/." "$SCENARIO_STAGE/"
  (
    cd "$SCENARIO_STAGE"
    zip -rq "$SCENARIO_ZIP" .
  )
  SCENARIO_SIZE="$(stat -c '%s' "$SCENARIO_ZIP")"
fi

cat > "$VALIDATION_CMDS_TXT" <<EOF
Validation Commands (UTC packet closeout)
- PYTHONPATH=. pytest -q
- bash -n scripts/canonical_paper_validation.sh scripts/canonical_paper_session.sh scripts/deploy_paper_clean.sh
- ./.venv/bin/python -m py_compile scripts/order_lifecycle_audit.py scripts/outcome_truth_audit.py scripts/export_truth_audit.py scripts/validator_replay_fingerprint.py scripts/foundation_scenario_proof.py
- ./scripts/foundation_closeout_gate.sh --run-id ${RUN_ID} --run-contract ${RUN_CONTRACT}
- ./scripts/foundation_scenario_proof.py --timestamp ${TS}
- ./scripts/build_review_export.sh --run-id ${RUN_ID} --run-contract ${RUN_CONTRACT} --timestamp ${TS}$( [[ -n "$SCENARIO_ROOT_ABS" ]] && printf ' --scenario-root %q' "$SCENARIO_ROOT_ABS" )
EOF

{
  echo "BRO Zip Inventory"
  echo "timestamp_utc=${TS}"
  stat -c '%n %s bytes' "$REPO_ZIP"
  stat -c '%n %s bytes' "$RUN_ZIP"
  if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
    stat -c '%n %s bytes' "$SCENARIO_ZIP"
  fi
} > "$ZIP_INVENTORY_TXT"

{
  echo "# repo snapshot zip inventory"
  unzip -l "$REPO_ZIP" | sed -n '1,120p'
  echo
  echo "# run evidence zip inventory"
  unzip -l "$RUN_ZIP" | sed -n '1,220p'
  if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
    echo
    echo "# foundation scenarios zip inventory"
    unzip -l "$SCENARIO_ZIP" | sed -n '1,260p'
  fi
} > "$INVENTORY_TXT"

CONSULT_STAGE="$TMP_DIR/consultant"
mkdir -p "$CONSULT_STAGE"
cp "$INVENTORY_TXT" "$CONSULT_STAGE/"
cp "$VALIDATION_CMDS_TXT" "$CONSULT_STAGE/"
cp "$ZIP_INVENTORY_TXT" "$CONSULT_STAGE/"
(
  cd "$CONSULT_STAGE"
  zip -rq "$CONSULT_ZIP" .
)
stat -c '%n %s bytes' "$CONSULT_ZIP" >> "$ZIP_INVENTORY_TXT"

REPO_SIZE="$(stat -c '%s' "$REPO_ZIP")"
RUN_SIZE="$(stat -c '%s' "$RUN_ZIP")"
CONS_SIZE="$(stat -c '%s' "$CONSULT_ZIP")"

cat > "$MANIFEST_TXT" <<EOF
BRO Review Export Manifest
Timestamp (UTC): ${TS}
Run ID: ${RUN_ID}
Session ID: ${SESSION_ID}

ZIP Artifacts:
- ${REPO_ZIP} (${REPO_SIZE} bytes)
  Contents: source/docs/tests/configs/scripts for doctrine/canonical review.
  Exclusions: .git, .venv, __pycache__, logs_exec, data, archives, prior exports, .env, node_modules, zip artifacts.

- ${RUN_ZIP} (${RUN_SIZE} bytes)
  Contents: minimal real runtime evidence for canonical run ${RUN_ID} (run_contract, run_manifest, validator report suite, session_state, run slices).

EOF

if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
  cat >> "$MANIFEST_TXT" <<EOF
- ${SCENARIO_ZIP} (${SCENARIO_SIZE} bytes)
  Contents: deterministic foundation scenario evidence bundle (clean, reconnect, disorder, degraded-source, thin-liquidity, poor-truth standdown) with websocket/time/paper/lifecycle audits.

EOF
fi

cat >> "$MANIFEST_TXT" <<EOF
- ${CONSULT_ZIP} (${CONS_SIZE} bytes)
  Contents: file inventory, validation commands log, zip inventory.

Validation Status:
- full regression gate: external (see BRO_validation_commands_<timestamp>.txt)
- shell syntax gate: external (see BRO_validation_commands_<timestamp>.txt)
- canonical run + validation status: ${VALIDATION_STATUS} (overall_exit_code=${VALIDATION_EXIT_CODE}, source=$(basename "${VALIDATION_SUMMARY_ABS}"))

Known Limitations:
- Repo snapshot intentionally excludes runtime log roots, archives, and prior export artifacts.
- No secrets were included (.env excluded).

Completeness Statement:
- Export is complete for doctrine-level source review, one real canonical run evidence set, and scenario proof artifacts when included.
EOF

if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
  sha256sum "$REPO_ZIP" "$RUN_ZIP" "$SCENARIO_ZIP" "$CONSULT_ZIP" > "$CHECKSUMS_TXT"
else
  sha256sum "$REPO_ZIP" "$RUN_ZIP" "$CONSULT_ZIP" > "$CHECKSUMS_TXT"
fi

if [[ -n "$SCENARIO_ROOT_ABS" ]]; then
  ./.venv/bin/python scripts/export_truth_audit.py \
    --manifest "$MANIFEST_TXT" \
    --payload "$REPO_ZIP" \
    --payload "$RUN_ZIP" \
    --payload "$SCENARIO_ZIP" \
    --payload "$CONSULT_ZIP" \
    --out "$AUDIT_JSON" >/dev/null
else
  ./.venv/bin/python scripts/export_truth_audit.py \
    --manifest "$MANIFEST_TXT" \
    --payload "$REPO_ZIP" \
    --payload "$RUN_ZIP" \
    --payload "$CONSULT_ZIP" \
    --out "$AUDIT_JSON" >/dev/null
fi

sha256sum "$MANIFEST_TXT" "$CHECKSUMS_TXT" "$INVENTORY_TXT" "$VALIDATION_CMDS_TXT" "$ZIP_INVENTORY_TXT" "$AUDIT_JSON" > "$PAYLOAD_CHECKSUMS_TXT"

cat <<EOF
[export] status=pass
[export] run_id=${RUN_ID}
[export] timestamp_utc=${TS}
[export] repo_zip=${REPO_ZIP}
[export] run_zip=${RUN_ZIP}
[export] scenario_zip=$( [[ -n "$SCENARIO_ROOT_ABS" ]] && printf '%s' "$SCENARIO_ZIP" || printf '%s' "none" )
[export] consultant_zip=${CONSULT_ZIP}
[export] manifest=${MANIFEST_TXT}
[export] checksums=${CHECKSUMS_TXT}
[export] payload_checksums=${PAYLOAD_CHECKSUMS_TXT}
[export] audit=${AUDIT_JSON}
EOF
