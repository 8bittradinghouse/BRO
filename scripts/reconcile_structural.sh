#!/usr/bin/env bash
set -euo pipefail

ASSET_MODE="${1:-paper_universal}"
RUN_ID="${2:-}"
LOG_DIR="./logs_exec/${ASSET_MODE}"
STATE_PATH="./data/${ASSET_MODE}/state.json"

ok=1

echo "=== Structural Reconcile Check (${ASSET_MODE}) ==="

if [[ -d "${LOG_DIR}" ]]; then
  echo "[OK] log dir exists: ${LOG_DIR}"
else
  echo "[FAIL] missing log dir: ${LOG_DIR}"
  ok=0
fi

if [[ -f "${STATE_PATH}" ]]; then
  if python3 -m json.tool "${STATE_PATH}" >/dev/null 2>&1; then
    echo "[OK] state.json is parseable: ${STATE_PATH}"
  else
    echo "[FAIL] state.json is not valid JSON: ${STATE_PATH}"
    ok=0
  fi
else
  echo "[FAIL] missing state file: ${STATE_PATH}"
  ok=0
fi

if [[ -n "${RUN_ID}" ]]; then
  manifest_path="${LOG_DIR}/run_manifest_${RUN_ID}.json"
  if [[ ! -f "${manifest_path}" ]]; then
    echo "[FAIL] explicit run manifest missing: ${manifest_path}"
    ok=0
  elif python3 - << 'PY' "${manifest_path}" "${RUN_ID}" >/dev/null 2>&1
import json,sys
p=sys.argv[1]
expected=sys.argv[2]
with open(p,'r',encoding='utf-8') as f:
    d=json.load(f)
assert isinstance(d,dict)
rid=str(d.get('run_id') or d.get('runId') or '').strip()
assert rid == expected
PY
  then
    echo "[OK] explicit run manifest bound: ${manifest_path}"
  else
    echo "[FAIL] explicit run manifest invalid/mismatched: ${manifest_path}"
    ok=0
  fi
else
  echo "[INFO] manifest check skipped (explicit run_id not provided)"
fi

if [[ "${ok}" -eq 1 ]]; then
  echo "RESULT=PASS"
  exit 0
fi

echo "RESULT=FAIL"
exit 1
