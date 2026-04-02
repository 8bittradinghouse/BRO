#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper: canonical 12h soak must use the same lifecycle path as all paper runs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

DURATION_MIN="${BRO_SOAK_DURATION_MIN:-720}"
WAIT_SEC="${BRO_WAIT_SEC:-25}"

ARGS=(--active-minutes "${DURATION_MIN}" --wait-sec "${WAIT_SEC}")
if [[ "${BRO_BUILD_IMAGES:-0}" == "1" ]]; then
  ARGS+=(--build)
fi
if [[ "${BRO_ARCHIVE_EXPORT:-0}" == "1" ]]; then
  ARGS+=(--archive-export)
fi

./scripts/canonical_paper_session.sh "${ARGS[@]}"

