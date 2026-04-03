#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

REMOTE="origin"
BRANCH="$(git branch --show-current)"
COMMIT_REF="HEAD"
RUN_ID=""
PROOF_FILE=""
OUTPUT_PATH=""
APPEND_INDEX=1

usage() {
  cat <<'EOF'
Usage:
  ./scripts/grok_sync_packet.sh [options]

Options:
  --remote <name>         Git remote name (default: origin)
  --branch <name>         Branch for blob/raw links (default: current branch)
  --commit <ref>          Commit ref for packet links (default: HEAD)
  --run-id <id>           Optional canonical run_id to include local artifact path
  --proof-file <path>     Optional repo-relative or absolute path to proof note
  --output <path>         Optional packet output path (default: exports/GROK_SYNC_PACKET_<UTC>.md)
  --no-index-append       Do not append pointer section to GROK_BLOB_INDEX.md
  --help                  Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      REMOTE="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:-}"
      shift 2
      ;;
    --commit)
      COMMIT_REF="${2:-}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --proof-file)
      PROOF_FILE="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    --no-index-append)
      APPEND_INDEX=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

REMOTE_URL="$(git remote get-url "${REMOTE}")"
if [[ -z "${REMOTE_URL}" ]]; then
  echo "remote not found: ${REMOTE}" >&2
  exit 2
fi

OWNER=""
REPO=""
REMOTE_NO_GIT="${REMOTE_URL%.git}"
if [[ "${REMOTE_NO_GIT}" =~ ^git@[^:]+:([^/]+)/(.+)$ ]]; then
  OWNER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
elif [[ "${REMOTE_NO_GIT}" =~ ^https?://[^/]+/([^/]+)/(.+)$ ]]; then
  OWNER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
elif [[ "${REMOTE_NO_GIT}" =~ ^ssh://git@[^/]+/([^/]+)/(.+)$ ]]; then
  OWNER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
else
  echo "unable to parse owner/repo from remote url: ${REMOTE_URL}" >&2
  exit 2
fi
REPO="${REPO##*/}"

COMMIT_FULL="$(git rev-parse "${COMMIT_REF}")"
COMMIT_SHORT="$(git rev-parse --short "${COMMIT_FULL}")"

TIMESTAMP_Z="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "${OUTPUT_PATH}" ]]; then
  OUTPUT_PATH="exports/GROK_SYNC_PACKET_${STAMP}.md"
fi

PROOF_REL=""
if [[ -n "${PROOF_FILE}" ]]; then
  if [[ -f "${PROOF_FILE}" ]]; then
    PROOF_REL="$(realpath --relative-to="${ROOT_DIR}" "${PROOF_FILE}")"
  elif [[ -f "${ROOT_DIR}/${PROOF_FILE}" ]]; then
    PROOF_REL="$(realpath --relative-to="${ROOT_DIR}" "${ROOT_DIR}/${PROOF_FILE}")"
  else
    echo "proof file not found: ${PROOF_FILE}" >&2
    exit 2
  fi
fi

mapfile -t CHANGED_FILES < <(git show --name-only --pretty="" "${COMMIT_FULL}" | rg -v '^\s*$')

REPO_WEB="https://github.com/${OWNER}/${REPO}"
INDEX_BLOB_URL="${REPO_WEB}/blob/${BRANCH}/GROK_BLOB_INDEX.md"

mkdir -p "$(dirname "${OUTPUT_PATH}")"
{
  echo "BRO sync packet for Grok"
  echo
  echo "Generated: ${TIMESTAMP_Z}"
  echo "Repo: ${REPO_WEB}"
  echo "Branch: ${BRANCH}"
  echo "Commit: ${COMMIT_SHORT} (${REPO_WEB}/commit/${COMMIT_FULL})"
  echo "Blob index: ${INDEX_BLOB_URL}"
  echo
  if [[ -n "${RUN_ID}" ]]; then
    echo "Run ID: ${RUN_ID}"
    echo "Local validation summary:"
    echo "${ROOT_DIR}/logs_exec/paper_universal/reports/${RUN_ID}/validation_summary.json"
    echo
  fi
  if [[ -n "${PROOF_REL}" ]]; then
    echo "Proof note:"
    echo "${REPO_WEB}/blob/${BRANCH}/${PROOF_REL}"
    echo "https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/${PROOF_REL}"
    echo
  fi
  if [[ ${#CHANGED_FILES[@]} -gt 0 ]]; then
    echo "Changed files in commit:"
    for file_path in "${CHANGED_FILES[@]}"; do
      echo "- ${file_path}"
      echo "  ${REPO_WEB}/blob/${BRANCH}/${file_path}"
    done
  else
    echo "Changed files in commit: none detected"
  fi
} > "${OUTPUT_PATH}"

if [[ ${APPEND_INDEX} -eq 1 ]]; then
  INDEX_FILE="GROK_BLOB_INDEX.md"
  if [[ ! -f "${INDEX_FILE}" ]]; then
    echo "missing ${INDEX_FILE}, skipping append" >&2
  else
    sed -i "s|^Branch: .*|Branch: \`${BRANCH}\`|" "${INDEX_FILE}"
    if rg -q "${COMMIT_FULL}" "${INDEX_FILE}"; then
      echo "index already contains commit ${COMMIT_SHORT}; skipping duplicate pointer append"
    else
      {
        echo
        echo "## Sync Pointer (${TIMESTAMP_Z})"
        echo
        echo "- Branch: \`${BRANCH}\`"
        echo "- Commit: [\`${COMMIT_SHORT}\`](${REPO_WEB}/commit/${COMMIT_FULL})"
        if [[ -n "${RUN_ID}" ]]; then
          echo "- Run ID: \`${RUN_ID}\`"
          echo "- Local validation summary: \`${ROOT_DIR}/logs_exec/paper_universal/reports/${RUN_ID}/validation_summary.json\`"
        fi
        if [[ -n "${PROOF_REL}" ]]; then
          echo "- Proof note:"
          echo "  - blob: ${REPO_WEB}/blob/${BRANCH}/${PROOF_REL}"
          echo "  - raw: https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/${PROOF_REL}"
        fi
        if [[ ${#CHANGED_FILES[@]} -gt 0 ]]; then
          echo
          echo "### Changed Files"
          for file_path in "${CHANGED_FILES[@]}"; do
            echo "- \`${file_path}\`"
            echo "  - ${REPO_WEB}/blob/${BRANCH}/${file_path}"
          done
        fi
      } >> "${INDEX_FILE}"
    fi
  fi
fi

cat <<EOF
grok sync packet generated
- output: ${OUTPUT_PATH}
- repo: ${REPO_WEB}
- branch: ${BRANCH}
- commit: ${COMMIT_SHORT}
- blob index: ${INDEX_BLOB_URL}
EOF
