#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "" ]]; then
  echo "usage: $0 <run_id> [out_json]"
  exit 2
fi

RUN_ID="$1"
OUT_JSON="${2:-./exports/prelive_${RUN_ID}.json}"
CONFIG_PATH="${BRO_LIVE_CONFIG_PATH:-./configs/btc_live.yaml}"
ACK_ENV_NAME="${BRO_LIVE_ACK_ENV_NAME:-SECURITY_ACK}"
ACK_EXPECTED="${BRO_LIVE_ACK_EXPECTED:-YES}"

ACK_VALUE="$(printenv "$ACK_ENV_NAME" || true)"
if [[ "$ACK_VALUE" != "$ACK_EXPECTED" ]]; then
  echo "prelive_fail: ${ACK_ENV_NAME} must equal ${ACK_EXPECTED}"
  exit 2
fi

for secret_path in /run/secrets/polymarket_private_key /run/secrets/polymarket_funder; do
  if [[ ! -s "$secret_path" ]]; then
    echo "prelive_fail: missing_or_empty_secret_file:$secret_path"
    exit 2
  fi
done

mkdir -p "$(dirname "$OUT_JSON")"
SECURITY_ACK="$ACK_VALUE" ./.venv/bin/python scripts/prelive_gate.py \
  --config "$CONFIG_PATH" \
  --run-id "$RUN_ID" \
  --out "$OUT_JSON"

echo "prelive_ok: out_json=$OUT_JSON"
