#!/usr/bin/env bash
set -euo pipefail

# Deterministic paper deploy:
# 1) image rebuild (default; --no-build is non-canonical fast path)
# 2) hard stop
# 3) clear local state/guard artifacts
# 4) start maker+guardian
# 5) print active run manifest identity

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "${BRO_INTERNAL_SESSION_CALL:-0}" != "1" ]]; then
  echo "deploy_paper_clean.sh direct lifecycle execution is disabled." >&2
  echo "use ./scripts/canonical_paper_session.sh for canonical paper runs." >&2
  exit 2
fi

DO_BUILD=1
WAIT_SEC=25
DO_VERIFY=1
VERIFY_MIN_STATUS_ROWS=1
RUN_ID=""
PY_BIN="${PY_BIN:-}"
if [[ -z "${PY_BIN}" ]]; then
  if command -v python >/dev/null 2>&1; then
    PY_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
  else
    echo "python interpreter not found (python/python3)" >&2
    exit 127
  fi
fi

BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST="${BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST:-}"
BRO_CANONICAL_SESSION_TOKEN="${BRO_CANONICAL_SESSION_TOKEN:-}"
BRO_CANONICAL_SESSION_CONTEXT_FILE_CONTAINER="${BRO_CANONICAL_SESSION_CONTEXT_FILE:-/logs/paper_universal/guardian_session_context.json}"
if [[ -z "${BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST}" || -z "${BRO_CANONICAL_SESSION_TOKEN}" ]]; then
  echo "canonical_session_handshake_required" >&2
  echo "missing BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST or BRO_CANONICAL_SESSION_TOKEN" >&2
  exit 2
fi

resolve_compose_var() {
  local key="$1"
  local fallback="$2"
  local current="${!key-}"
  if [[ -n "${current}" ]]; then
    printf '%s\n' "${current}"
    return 0
  fi
  "${PY_BIN}" - <<'PY' "${key}" "${fallback}" "${ROOT_DIR}/.env"
import pathlib
import sys

key = str(sys.argv[1])
fallback = str(sys.argv[2])
env_path = pathlib.Path(str(sys.argv[3]))
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        k, v = text.split("=", 1)
        if k.strip() != key:
            continue
        value = v.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        print(value if value else fallback)
        raise SystemExit(0)
print(fallback)
PY
}

CONFIG_PATH="$(resolve_compose_var BRO_CONFIG_PATH ./configs/profiles/paper_universal.yaml)"
HOST_LOG_ROOT="$(resolve_compose_var BRO_LOG_DIR ./logs_exec)"
HOST_DATA_ROOT="$(resolve_compose_var BRO_DATA_DIR ./data)"
CANONICAL_CONFIG_PATH="${ROOT_DIR}/configs/profiles/paper_universal.yaml"

if [[ "${BRO_ALLOW_NONCANONICAL_PAPER_CONFIG:-0}" != "1" ]]; then
  RESOLVED_CONFIG="$(${PY_BIN} - <<'PY' "${CONFIG_PATH}"
import pathlib
import sys
print(str(pathlib.Path(sys.argv[1]).expanduser().resolve()))
PY
)"
  RESOLVED_CANONICAL="$(${PY_BIN} - <<'PY' "${CANONICAL_CONFIG_PATH}"
import pathlib
import sys
print(str(pathlib.Path(sys.argv[1]).expanduser().resolve()))
PY
)"
  if [[ "${RESOLVED_CONFIG}" != "${RESOLVED_CANONICAL}" ]]; then
    echo "noncanonical_config_path_blocked:${RESOLVED_CONFIG}" >&2
    echo "expected_canonical:${RESOLVED_CANONICAL}" >&2
    echo "set BRO_ALLOW_NONCANONICAL_PAPER_CONFIG=1 only for explicit break-glass use" >&2
    exit 2
  fi
fi

echo "[deploy] validating paper setup lock in ${CONFIG_PATH}..."
BRO_DEPLOY_CONFIG_PATH="${CONFIG_PATH}" "${PY_BIN}" - <<'PY'
import os
import pathlib

from prodesk.config import load_execution_config

config_path = pathlib.Path(str(os.environ.get("BRO_DEPLOY_CONFIG_PATH", "./execution_config.yaml"))).resolve()
cfg = load_execution_config(config_path)
mode = str(cfg.get("mode", "")).strip().lower()
if mode != "paper":
    print(f"setup_lock_invalid_mode:{mode or 'missing'}")
    raise SystemExit(2)
runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
profile_name = str((cfg.get("profile") or {}).get("name") or "").strip()
expected_profile = str(runtime.get("paper_expected_profile_name", "")).strip()
expected_fp = str(runtime.get("paper_expected_config_fingerprint_sha256", "")).strip().lower()
observed_fp = str((cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
if not bool(runtime.get("paper_enforce_setup_lock", False)):
    print("setup_lock_disabled")
    raise SystemExit(2)
if not expected_profile or expected_profile != profile_name:
    print(
        "setup_lock_profile_mismatch:"
        + f"observed={profile_name or 'missing'}:expected={expected_profile or 'missing'}"
    )
    raise SystemExit(2)
if not expected_fp or expected_fp != observed_fp:
    print(
        "setup_lock_fingerprint_mismatch:"
        + f"observed={observed_fp or 'missing'}:expected={expected_fp or 'missing'}"
    )
    raise SystemExit(2)
print("setup_lock_verified", profile_name, observed_fp)
PY

readarray -t DEPLOY_PATHS < <(
BRO_DOCKER_MODE=1 \
BRO_DEPLOY_CONFIG_PATH="${CONFIG_PATH}" \
BRO_DEPLOY_HOST_LOG_ROOT="${HOST_LOG_ROOT}" \
BRO_DEPLOY_HOST_DATA_ROOT="${HOST_DATA_ROOT}" \
BRO_DEPLOY_ROOT_DIR="${ROOT_DIR}" \
"${PY_BIN}" - <<'PY'
import os
import pathlib
from pathlib import PurePosixPath

from prodesk.config import load_execution_config

config_path = pathlib.Path(str(os.environ.get("BRO_DEPLOY_CONFIG_PATH", "./execution_config.yaml"))).resolve()
cfg = load_execution_config(config_path)
cfg_dir = config_path.parent
root_dir = pathlib.Path(str(os.environ.get("BRO_DEPLOY_ROOT_DIR", cfg_dir))).resolve()
host_log_root = pathlib.Path(str(os.environ.get("BRO_DEPLOY_HOST_LOG_ROOT", "./logs_exec"))).expanduser()
if not host_log_root.is_absolute():
    host_log_root = (root_dir / host_log_root).resolve()
host_data_root = pathlib.Path(str(os.environ.get("BRO_DEPLOY_HOST_DATA_ROOT", "./data"))).expanduser()
if not host_data_root.is_absolute():
    host_data_root = (root_dir / host_data_root).resolve()
storage = cfg.get("storage", {}) if isinstance(cfg.get("storage"), dict) else {}
runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}

def resolve_path(raw: str, fallback: str) -> pathlib.Path:
    text = str(raw or "").strip() or fallback
    # Map container runtime paths to host bind mount roots.
    if text.startswith("/logs"):
        rel = PurePosixPath(text).relative_to("/logs")
        return (host_log_root / pathlib.Path(*rel.parts)).resolve()
    if text.startswith("/data"):
        rel = PurePosixPath(text).relative_to("/data")
        return (host_data_root / pathlib.Path(*rel.parts)).resolve()
    p = pathlib.Path(text)
    if not p.is_absolute():
        p = (cfg_dir / p).resolve()
    return p.resolve()

log_dir = resolve_path(storage.get("log_dir", ""), "./logs_exec")
state_path = resolve_path(storage.get("state_path", ""), "./logs_exec/state.json")
guard_stop = str(runtime.get("guard_stop_file", "")).strip()
guard_stop_path = resolve_path(guard_stop, str(log_dir / "guard_stop.txt"))

print(str(log_dir))
print(str(state_path))
print(str(guard_stop_path))
PY
)
if [[ "${#DEPLOY_PATHS[@]}" -lt 3 ]]; then
  echo "failed to resolve deploy paths from ${CONFIG_PATH}" >&2
  exit 2
fi
LOG_DIR_PATH="${DEPLOY_PATHS[0]}"
STATE_PATH="${DEPLOY_PATHS[1]}"
GUARD_STOP_PATH="${DEPLOY_PATHS[2]}"

echo "[deploy] resolved log_dir=${LOG_DIR_PATH}"
echo "[deploy] resolved state_path=${STATE_PATH}"
echo "[deploy] resolved guard_stop_file=${GUARD_STOP_PATH}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      DO_BUILD=1
      shift
      ;;
    --no-build)
      DO_BUILD=0
      shift
      ;;
    --wait-sec)
      WAIT_SEC="${2:-25}"
      shift 2
      ;;
    --no-verify)
      DO_VERIFY=0
      shift
      ;;
    --verify-min-status-rows)
      VERIFY_MIN_STATUS_ROWS="${2:-1}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${RUN_ID}" ]]; then
  echo "deploy_requires_explicit_run_id" >&2
  echo "use --run-id <uuid> from canonical session control plane" >&2
  exit 2
fi
RUN_CONTRACT_PATH="${LOG_DIR_PATH}/run_contract_${RUN_ID}.json"
if [[ ! -f "${RUN_CONTRACT_PATH}" ]]; then
  echo "run_contract_missing:${RUN_CONTRACT_PATH}" >&2
  echo "canonical session must pre-write run_contract before deploy start" >&2
  exit 2
fi

"${PY_BIN}" - <<'PY' "${BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST}" "${BRO_CANONICAL_SESSION_TOKEN}" "${RUN_ID}" "${LOG_DIR_PATH}"
import pathlib
import sys
import uuid

from prodesk.canonical_authority import (
    ACTOR_DEPLOY_PAPER_CLEAN,
    CAPABILITY_DEPLOY_START,
    AuthorityRequest,
    render_authority_denial,
    resolve_authority_decision,
)

context_path = pathlib.Path(str(sys.argv[1])).expanduser().resolve()
session_token = str(sys.argv[2]).strip()
run_id = str(sys.argv[3]).strip()
log_dir = pathlib.Path(str(sys.argv[4])).expanduser().resolve()
try:
    uuid.UUID(run_id)
except ValueError:
    print(f"run_id_invalid:{run_id!r}")
    raise SystemExit(2)
decision = resolve_authority_decision(
    AuthorityRequest(
        actor=ACTOR_DEPLOY_PAPER_CLEAN,
        action=CAPABILITY_DEPLOY_START,
        log_dir=log_dir,
        session_context_file=context_path,
        session_token=session_token,
        run_id=run_id,
        require_authoritative=True,
        allow_open_contract=True,
    )
)
if not decision.authorized:
    print(render_authority_denial(decision, prefix="canonical_deploy_authority_denied"))
    raise SystemExit(2)
print("canonical_deploy_authority_verified", decision.session_id, decision.session_phase, decision.run_id)
PY

if [[ -z "${BRO_GIT_COMMIT:-}" ]]; then
  if GIT_COMMIT="$(${PY_BIN} - <<'PY' "${ROOT_DIR}"
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1]).resolve()
try:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=2.0,
    ).strip()
except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
    out = ""
print(out)
PY
)"; then
    BRO_GIT_COMMIT="${GIT_COMMIT}"
  fi
fi

if [[ -z "${BRO_GIT_DIRTY:-}" ]]; then
  if GIT_DIRTY="$(${PY_BIN} - <<'PY' "${ROOT_DIR}"
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1]).resolve()
try:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain"],
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=2.0,
    )
    print("1" if out.strip() else "0")
except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
    print("0")
PY
)"; then
    BRO_GIT_DIRTY="${GIT_DIRTY}"
  fi
fi

export BRO_GIT_COMMIT="${BRO_GIT_COMMIT:-}"
export BRO_GIT_DIRTY="${BRO_GIT_DIRTY:-0}"
if [[ -n "${BRO_GIT_COMMIT}" ]]; then
  echo "[deploy] git identity commit=${BRO_GIT_COMMIT} dirty=${BRO_GIT_DIRTY}"
else
  echo "[deploy] git identity unavailable (BRO_GIT_COMMIT empty)"
fi

if [[ "${DO_BUILD}" -eq 1 ]]; then
  echo "[deploy] building images..."
  docker compose build bro-maker bro-guardian
else
  echo "[deploy] skipping image build via --no-build (non-canonical fast path)"
fi

# Resolve image identities with bounded retry so transient compose failures do
# not surface as silent shell exit 255 from command substitution.
COMPOSE_IMAGES_OUTPUT=""
COMPOSE_IMAGES_RC=0
for attempt in 1 2 3; do
  set +e
  COMPOSE_IMAGES_OUTPUT="$(docker compose config --images 2>&1)"
  COMPOSE_IMAGES_RC=$?
  set -e
  if [[ "${COMPOSE_IMAGES_RC}" -eq 0 ]]; then
    break
  fi
  echo "[deploy] docker compose config --images failed attempt ${attempt}/3 rc=${COMPOSE_IMAGES_RC}" >&2
  echo "${COMPOSE_IMAGES_OUTPUT}" >&2
  sleep 1
done
if [[ "${COMPOSE_IMAGES_RC}" -ne 0 ]]; then
  echo "deploy_image_identity_lookup_failed:docker_compose_config_images_exit_${COMPOSE_IMAGES_RC}" >&2
  exit 2
fi
BRO_MAKER_IMAGE_REF="$(printf '%s\n' "${COMPOSE_IMAGES_OUTPUT}" | awk '/-bro-maker$/ {print; exit}')"
if [[ -z "${BRO_MAKER_IMAGE_REF}" ]]; then
  echo "deploy_image_identity_missing:bro-maker-image-ref" >&2
  exit 2
fi
BRO_DOCKER_IMAGE_HASH="${BRO_DOCKER_IMAGE_HASH:-}"
if [[ -z "${BRO_DOCKER_IMAGE_HASH}" ]]; then
  BRO_DOCKER_IMAGE_HASH="$(docker image inspect --format '{{.Id}}' "${BRO_MAKER_IMAGE_REF}" 2>/dev/null || true)"
fi
if [[ -z "${BRO_DOCKER_IMAGE_HASH}" ]]; then
  echo "deploy_image_identity_missing:bro-maker-image-hash:${BRO_MAKER_IMAGE_REF}" >&2
  exit 2
fi
export BRO_DOCKER_IMAGE_HASH
echo "[deploy] docker image identity maker_ref=${BRO_MAKER_IMAGE_REF} hash=${BRO_DOCKER_IMAGE_HASH}"

echo "[deploy] stopping stack..."
docker compose down

echo "[deploy] clearing state and guard artifacts..."
rm -f "${STATE_PATH}" "${GUARD_STOP_PATH}"

echo "[deploy] starting stack..."
BRO_CANONICAL_SESSION_CALL=1 \
BRO_CANONICAL_SESSION_TOKEN="${BRO_CANONICAL_SESSION_TOKEN}" \
BRO_CANONICAL_SESSION_CONTEXT_FILE="${BRO_CANONICAL_SESSION_CONTEXT_FILE_CONTAINER}" \
BRO_RUN_ID="${RUN_ID}" \
BRO_DOCKER_IMAGE_HASH="${BRO_DOCKER_IMAGE_HASH}" \
docker compose up -d bro-maker bro-guardian

echo "[deploy] waiting ${WAIT_SEC}s for health..."
sleep "${WAIT_SEC}"

echo "[deploy] service status:"
docker compose ps

echo "[deploy] manifest identity:"
MANIFEST_WAIT_SEC="$("${PY_BIN}" - <<'PY' "${WAIT_SEC}"
import sys
try:
    wait = float(sys.argv[1])
except (TypeError, ValueError):
    wait = 25.0
print(int(max(15.0, wait + 20.0)))
PY
)"
BRO_DEPLOY_LOG_DIR="${LOG_DIR_PATH}" BRO_DEPLOY_RUN_ID="${RUN_ID}" BRO_DEPLOY_MANIFEST_WAIT_SEC="${MANIFEST_WAIT_SEC}" "${PY_BIN}" - <<'PY'
import json
import os
import sys
import time

log_dir = str(os.environ.get("BRO_DEPLOY_LOG_DIR", "")).strip()
run_id = str(os.environ.get("BRO_DEPLOY_RUN_ID", "")).strip()
timeout_sec = float(os.environ.get("BRO_DEPLOY_MANIFEST_WAIT_SEC", "45") or 45.0)
if not run_id:
    print("run_manifest_run_id_required")
    raise SystemExit(1)
manifest = os.path.join(log_dir, f"run_manifest_{run_id}.json")
deadline = time.monotonic() + max(1.0, timeout_sec)
while not os.path.exists(manifest) and time.monotonic() <= deadline:
    time.sleep(0.5)
if not os.path.exists(manifest):
    print("run_manifest_missing", manifest)
    raise SystemExit(1)
payload = json.load(open(manifest, "r", encoding="utf-8"))
runtime_identity = payload.get("runtime_identity") if isinstance(payload.get("runtime_identity"), dict) else {}
print("manifest_path", manifest)
print("run_id", payload.get("run_id"))
print("config_fingerprint_sha256", payload.get("config_fingerprint_sha256"))
print("config_source_path", payload.get("config_source_path"))
print("config_source_sha256", payload.get("config_source_sha256"))
print("code_fingerprint_sha256", payload.get("code_fingerprint_sha256"))
print("code_fingerprint_file_count", payload.get("code_fingerprint_file_count"))
print("runtime_identity.effective_config_sha256", runtime_identity.get("effective_config_sha256"))
print("runtime_identity.dependency_lock_sha256", runtime_identity.get("dependency_lock_sha256"))
print("runtime_identity.docker_image_hash", runtime_identity.get("docker_image_hash"))
manifest_run_id = str(payload.get("run_id") or "").strip()
if manifest_run_id != run_id:
    print("run_manifest_run_id_mismatch", f"{manifest_run_id or 'missing'}!={run_id}")
    raise SystemExit(2)
required = (
    "config_fingerprint_sha256",
    "config_source_sha256",
    "code_fingerprint_sha256",
)
missing = [k for k in required if not str(payload.get(k) or "").strip()]
runtime_required = (
    "effective_config_sha256",
    "dependency_lock_sha256",
    "docker_image_hash",
)
runtime_missing = [
    f"runtime_identity.{k}" for k in runtime_required if not str(runtime_identity.get(k) or "").strip()
]
if missing or runtime_missing:
    print("manifest_missing_required_fields", ",".join(missing + runtime_missing))
    print("hint", "re-run with --build to ensure latest image includes hardened manifest fields")
    raise SystemExit(2)
if str(runtime_identity.get("effective_config_sha256") or "").strip() != str(payload.get("config_fingerprint_sha256") or "").strip():
    print("manifest_runtime_identity_mismatch", "effective_config_sha256!=config_fingerprint_sha256")
    raise SystemExit(2)
PY

if [[ "${DO_VERIFY}" -eq 1 ]]; then
  echo "[deploy] running post-start audits..."
  "${PY_BIN}" scripts/prestart_gate.py --config "${CONFIG_PATH}" --allow-kill-switch --allow-guard-file
  "${PY_BIN}" scripts/websocket_hardening_audit.py \
    --config "${CONFIG_PATH}" \
    --log-dir "${LOG_DIR_PATH}" \
    --run-id "${RUN_ID}" \
    --run-contract "${RUN_CONTRACT_PATH}" \
    --session-phase validate_active \
    --max-lines-per-file 0
  "${PY_BIN}" scripts/guardian_profile_audit.py --compose ./docker-compose.yml --config "${CONFIG_PATH}"
  "${PY_BIN}" scripts/run_integrity_audit.py \
    --log-dir "${LOG_DIR_PATH}" \
    --run-id "${RUN_ID}" \
    --session-phase validate_active \
    --min-status-rows "${VERIFY_MIN_STATUS_ROWS}" \
    --max-status-age-sec 600
fi
