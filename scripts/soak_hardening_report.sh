#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${1:-./logs_exec/paper_universal}"
BUDGET="${2:-ops/soak_budget.yaml}"
RUN_ID="${3:-}"
OUT_DIR="${4:-./exports}"
TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${OUT_DIR}/soak_hardening_report_${TS_UTC}.json"

if [[ -z "${RUN_ID}" ]]; then
  echo "ERROR: explicit run_id is required (arg3)" >&2
  exit 2
fi

mkdir -p "${OUT_DIR}"
set +e
python3 scripts/soak_hardening_gate.py \
  --log-dir "${LOG_DIR}" \
  --run-id "${RUN_ID}" \
  --budget "${BUDGET}" \
  --out "${OUT_FILE}"
rc=$?
set -e

echo "run_id=${RUN_ID}"
echo "report=${OUT_FILE}"
exit "${rc}"
