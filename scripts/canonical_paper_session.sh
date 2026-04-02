#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -x "./.venv/bin/python" ]]; then
  echo "missing python interpreter: ./.venv/bin/python" >&2
  exit 127
fi

exec ./.venv/bin/python scripts/canonical_paper_session.py "$@"

