#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
MODE="${1:-paper}"

if [[ "${MODE}" != "paper" ]]; then
  echo "run.sh only supports canonical paper mode; use broctl for other modes" >&2
  exit 2
fi

if [[ $# -gt 1 ]]; then
  echo "run.sh does not accept overrides; use scripts/canonical_paper_session.sh for explicit controls" >&2
  exit 2
fi

SESSION_ARGS=(--active-minutes "${BRO_ACTIVE_MINUTES:-15}" --wait-sec "${BRO_WAIT_SEC:-35}")
if [[ "${BRO_BUILD_IMAGES:-0}" == "1" ]]; then
  SESSION_ARGS+=(--build)
fi
if [[ "${BRO_ARCHIVE_EXPORT:-0}" == "1" ]]; then
  SESSION_ARGS+=(--archive-export)
fi

./scripts/canonical_paper_session.sh "${SESSION_ARGS[@]}"
