#!/usr/bin/env bash
set -euo pipefail

TAIL_LINES=120
RUN_ID=""
LOG_DIR="./logs_exec/paper_universal"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tail-lines)
      TAIL_LINES="${2:-120}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="${2:-${LOG_DIR}}"
      shift 2
      ;;
    *)
      echo "unknown arg: $1" >&2
      echo "usage: $0 --run-id <run_id> [--tail-lines <n>] [--log-dir <path>]" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${RUN_ID}" ]]; then
  echo "soak_report requires explicit --run-id" >&2
  exit 2
fi

TS_UTC="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT_DIR="./logs_exec/_ops"
OUT_FILE="${OUT_DIR}/soak_report_${TS_UTC}.txt"
PROJECT="${COMPOSE_PROJECT_NAME:-bro_paper_universal}"

mkdir -p "${OUT_DIR}"

maker_cid="$(docker compose ps -q bro-maker 2>/dev/null || true)"
guardian_cid="$(docker compose ps -q bro-guardian 2>/dev/null || true)"

maker_logs="$(docker compose logs --tail="${TAIL_LINES}" bro-maker 2>&1 || true)"
guardian_logs="$(docker compose logs --tail="${TAIL_LINES}" bro-guardian 2>&1 || true)"

maker_errs="$(printf '%s\n' "${maker_logs}" | grep -Eic 'error|exception|traceback' || true)"
maker_warns="$(printf '%s\n' "${maker_logs}" | grep -Eic 'warn|warning' || true)"
guardian_errs="$(printf '%s\n' "${guardian_logs}" | grep -Eic 'error|exception|traceback' || true)"
guardian_warns="$(printf '%s\n' "${guardian_logs}" | grep -Eic 'warn|warning' || true)"

maker_restarts="n/a"
maker_health="n/a"
if [[ -n "${maker_cid}" ]]; then
  maker_restarts="$(docker inspect -f '{{.RestartCount}}' "${maker_cid}" 2>/dev/null || echo n/a)"
  maker_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${maker_cid}" 2>/dev/null || echo n/a)"
fi

guardian_restarts="n/a"
guardian_health="n/a"
if [[ -n "${guardian_cid}" ]]; then
  guardian_restarts="$(docker inspect -f '{{.RestartCount}}' "${guardian_cid}" 2>/dev/null || echo n/a)"
  guardian_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${guardian_cid}" 2>/dev/null || echo n/a)"
fi

last_guard_state="none"
status_source="none"
readarray -t STATUS_TARGET < <(
python3 - <<'PY' "${LOG_DIR}" "${RUN_ID}"
import json
import pathlib
import sys

log_dir = pathlib.Path(sys.argv[1]).expanduser().resolve()
run_id = str(sys.argv[2] or "").strip()
contract_path = log_dir / f"run_contract_{run_id}.json"
if contract_path.exists():
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    slice_path = str(payload.get("status_slice_path") or "").strip()
    status_path = str(payload.get("status_path") or "").strip()
    if slice_path:
        p = pathlib.Path(slice_path).expanduser().resolve()
        if p.exists():
            print(str(p))
            print("status_slice_path")
            raise SystemExit(0)
    if status_path:
        p = pathlib.Path(status_path).expanduser().resolve()
        if p.exists():
            print(str(p))
            print("status_path")
            raise SystemExit(0)
print("")
print("none")
PY
)
status_path="${STATUS_TARGET[0]:-}"
status_source="${STATUS_TARGET[1]:-none}"
if [[ -n "${status_path}" ]]; then
  last_guard_state="$(tail -n 1 "${status_path}" 2>/dev/null || echo none)"
fi

{
  echo "=== Bro Soak Report (${TS_UTC}) ==="
  echo "project=${PROJECT}"
  echo "run_id=${RUN_ID}"
  echo "log_dir=${LOG_DIR}"
  echo
  echo "[compose ps]"
  docker compose ps || true
  echo
  echo "[docker ps]"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | (grep -E 'NAMES|bro_' || true)
  echo
  echo "[health/restarts]"
  echo "bro-maker: health=${maker_health} restarts=${maker_restarts}"
  echo "bro-guardian: health=${guardian_health} restarts=${guardian_restarts}"
  echo
  echo "[log summary last ${TAIL_LINES} lines]"
  echo "bro-maker: errors=${maker_errs} warnings=${maker_warns}"
  echo "bro-guardian: errors=${guardian_errs} warnings=${guardian_warns}"
  echo
  echo "[disk usage]"
  df -h . || true
  du -sh ./logs_exec ./data 2>/dev/null || true
  echo
  echo "[last guardian state snapshot line]"
  echo "status_source=${status_source}"
  echo "${last_guard_state}"
} | tee "${OUT_FILE}"

echo "report_file=${OUT_FILE}"
