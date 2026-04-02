#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-./logs_exec}"
COMPRESS_AFTER_DAYS="${COMPRESS_AFTER_DAYS:-2}"
DELETE_AFTER_DAYS="${DELETE_AFTER_DAYS:-14}"

mkdir -p "${ROOT}/_ops"

echo "Rotating logs under ${ROOT}"
echo "compress_after_days=${COMPRESS_AFTER_DAYS} delete_after_days=${DELETE_AFTER_DAYS}"

find "${ROOT}" -type f -name '*.jsonl' -mtime +"${COMPRESS_AFTER_DAYS}" ! -name '*.gz' -print -exec gzip -f {} \;
find "${ROOT}" -type f -name '*.jsonl.gz' -mtime +"${DELETE_AFTER_DAYS}" -print -delete
find "${ROOT}" -type f -name '*.txt' -path '*/_ops/*' -mtime +"${DELETE_AFTER_DAYS}" -print -delete

echo "Rotation complete"
