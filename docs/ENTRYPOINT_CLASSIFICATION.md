# BRO Entrypoint Classification (Packet C Closeout)

This classification defines which operational surfaces are authoritative.
Anything outside canonical entrypoints must be wrapper-only, internal-only, or fail-fast.

## CANONICAL_ENTRYPOINT
- `scripts/canonical_paper_session.sh`
  - Canonical paper lifecycle: preflight -> start -> active -> validate_active -> stop -> validate_postrun -> archive_export -> complete.
- `scripts/canonical_paper_validation.sh`
  - Canonical postrun validation path (explicit `run_id` + run contract).

## WRAPPER_ONLY
- `run.sh`
  - Thin wrapper to canonical paper session only.
- `scripts/paper_12h_soak.sh`
  - Thin duration wrapper to canonical paper session only.

## INTERNAL_ONLY
- `scripts/deploy_paper_clean.sh`
  - Internal lifecycle helper; exits unless canonical session handshake is present:
    - `BRO_INTERNAL_SESSION_CALL=1`
    - `BRO_CANONICAL_SESSION_TOKEN`
    - `BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST` with matching `session_token` and `session_phase=start`
- `docker-compose.yml` service command for `bro-maker`
  - Internal runtime surface launched by orchestrator.
- `docker-compose.yml` service command for `bro-guardian`
  - Internal guardian surface; authoritative mode requires:
    - explicit session context file
    - explicit session token match
    - explicit startup fail-closed authority requirement (`--require-authoritative-startup`)
    - authoritative phase membership

## FAIL_FAST_NONCANONICAL
- Direct `python executor.py ...` in paper mode
  - Blocked unless canonical session handshake verifies:
    - `BRO_CANONICAL_SESSION_CALL=1`
    - `BRO_CANONICAL_SESSION_TOKEN`
    - `BRO_CANONICAL_SESSION_CONTEXT_FILE` with matching `session_token` and authoritative `session_phase`

## LEGACY_ARCHIVE_CANDIDATE
- `ops/systemd/polymarket-executor.service`
- `ops/systemd/polymarket-guardian.service`
  - Historical host-runtime templates; not canonical paper lifecycle entrypoints.

## Authoritative Run-ID Rules
- Authoritative validation/evidence workflows require explicit `run_id`.
- Latest-run convenience must not be used to determine canonical truth outcomes.
