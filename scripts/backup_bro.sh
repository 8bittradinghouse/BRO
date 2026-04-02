#!/usr/bin/env bash
set -euo pipefail

INCLUDE_LOGS=0
SRC_ROOT="${BRO_SRC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${BRO_BACKUP_DIR:-${SRC_ROOT}/backups}"
if [[ "${1:-}" == "--include-logs" ]]; then INCLUDE_LOGS=1; fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
MAIN_TAR="${OUT_DIR}/bro_backup_${TS}.tar.gz"
LOG_TAR="${OUT_DIR}/bro_backup_logs_${TS}.tar.gz"

mkdir -p "${OUT_DIR}"

# Exclude env/secrets and VCS metadata by default.
tar -czf "${MAIN_TAR}" \
  --warning=no-file-changed \
  --ignore-failed-read \
  --exclude='*/.env' \
  --exclude='*/.git' \
  --exclude='*/__pycache__' \
  --exclude='*/.pytest_cache' \
  --exclude='*/.venv' \
  --exclude='*/secrets*' \
  --exclude='*/wallet*' \
  --exclude='*/logs_exec' \
  --exclude='*/logs_exec/*' \
  --exclude='*/data' \
  --exclude='*/data/*' \
  -C "${SRC_ROOT}" .

echo "repo_backup=${MAIN_TAR}"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${MAIN_TAR}" > "${MAIN_TAR}.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "${MAIN_TAR}" > "${MAIN_TAR}.sha256"
else
  echo "warning=no_sha256_tool_found:${MAIN_TAR}" >&2
fi

if [[ "${INCLUDE_LOGS}" -eq 1 ]]; then
  LOGS_REL="${BRO_LOGS_REL:-logs_exec}"
  DATA_REL="${BRO_DATA_REL:-data}"
  tar -czf "${LOG_TAR}" \
    --warning=no-file-changed \
    --ignore-failed-read \
    -C "${SRC_ROOT}" "${LOGS_REL}" "${DATA_REL}"
  echo "logs_backup=${LOG_TAR}"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${LOG_TAR}" > "${LOG_TAR}.sha256"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${LOG_TAR}" > "${LOG_TAR}.sha256"
  else
    echo "warning=no_sha256_tool_found:${LOG_TAR}" >&2
  fi
fi
