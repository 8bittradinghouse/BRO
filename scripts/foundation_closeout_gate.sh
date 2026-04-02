#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/foundation_closeout_gate.sh [--run-id <uuid>] [--run-contract <path>]

Description:
  Runs foundation closeout validation gates with deterministic command order:
  1) full regression: PYTHONPATH=. pytest -q
  2) shell syntax checks for canonical scripts
  3) Python compile checks for closeout validators
  4) optional canonical validation for explicit run_id/run_contract
EOF
}

RUN_ID=""
RUN_CONTRACT=""

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

echo "[closeout] step=pytest_full"
PYTHONPATH=. pytest -q

echo "[closeout] step=shell_syntax"
bash -n scripts/canonical_paper_validation.sh scripts/canonical_paper_session.sh scripts/deploy_paper_clean.sh

echo "[closeout] step=py_compile"
./.venv/bin/python -m py_compile \
  scripts/order_lifecycle_audit.py \
  scripts/outcome_truth_audit.py \
  scripts/export_truth_audit.py \
  scripts/validator_replay_fingerprint.py

if [[ -n "$RUN_ID" ]]; then
  if [[ -z "$RUN_CONTRACT" ]]; then
    RUN_CONTRACT="./logs_exec/paper_universal/run_contract_${RUN_ID}.json"
  fi
  if [[ ! -f "$RUN_CONTRACT" ]]; then
    echo "run_contract not found: $RUN_CONTRACT" >&2
    exit 2
  fi
  echo "[closeout] step=canonical_validation run_id=${RUN_ID}"
  ./scripts/canonical_paper_validation.sh "$RUN_ID" --session-phase validate_postrun --run-contract "$RUN_CONTRACT"
fi

echo "[closeout] status=pass"
