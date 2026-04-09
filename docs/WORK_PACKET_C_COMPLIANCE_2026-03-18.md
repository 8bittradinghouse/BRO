# WORK PACKET C — CANONICAL COMPLIANCE MAP (2026-03-18 UTC)

> Doc Class: `Archive`
> Authority: historical compliance snapshot (non-authoritative for current operations).
> Current truth entrypoint: `docs/CURRENT_BASELINE.md`.

## Canonical Truth Lock
- Canonical paper profile: `configs/profiles/paper_universal.yaml`
- Canonical runtime lifecycle entrypoint: `scripts/canonical_paper_session.sh`
- Canonical runtime lifecycle engine: `scripts/canonical_paper_session.py`
- Canonical deployment path: `scripts/deploy_paper_clean.sh` (internal-only via `BRO_INTERNAL_SESSION_CALL=1`)
- Canonical validation path: `scripts/canonical_paper_validation.sh <run_id>`
- Canonical active log root (current enforced): `logs_exec/paper_universal`
- Canonical active state root (current enforced): `data/paper_universal/state.json`

## Compliance Classification

### Session Lifecycle Compliance
- `CANONICAL_COMPLIANT`: `canonical_paper_session.py` phase machine enforces `preflight -> start -> active -> validate_active -> stop -> validate_postrun -> archive_export -> complete`.
- `CANONICAL_COMPLIANT`: explicit phase transitions are validated; invalid transitions fail closed.
- `CANONICAL_COMPLIANT`: postrun policy-fail (`exit 2`) is separated from execution failure (`exit 3`).

### Entrypoint Compliance
- `WRAPPER_OK`: `run.sh` routes to canonical session and rejects non-paper / override args.
- `WRAPPER_OK`: `scripts/paper_12h_soak.sh` routes to canonical session.
- `CANONICAL_COMPLIANT`: `prodesk/cli.py` `paper|paper-stress|paper-discipline` route to canonical session and forbid config/log-dir/mode overrides.
- `CLOSED_ALREADY`: direct paper-mode `python executor.py ...` now fail-fast unless internal orchestrator context is present.

### Config / Env Compliance
- `CANONICAL_COMPLIANT`: deploy path blocks non-canonical config unless explicit break-glass (`BRO_ALLOW_NONCANONICAL_PAPER_CONFIG=1`).
- `CANONICAL_COMPLIANT`: setup-lock fingerprint/profile checks enforced at deploy.
- `PHASE_CONFUSION` (resolved): prior mixed host/container root behavior is now constrained by canonical session + deploy lock checks.

### State / Evidence Compliance
- `CANONICAL_COMPLIANT`: run contract is written and closed per run; validation consumes explicit run_id.
- `CANONICAL_COMPLIANT`: canonical validation refuses missing run_id.
- `EVIDENCE_DRIFT` (resolved this pass): `scripts/soak_report.sh` now requires explicit `--run-id` and resolves status from run contract (no latest-run shortcut).
- `STALE_STATE_RISK` (remaining): legacy artifacts still exist under `logs_exec/paper_universal` from older non-canonical sessions.

### Phase-Aware Validation Compliance
- `CANONICAL_COMPLIANT`: validators receive explicit `--session-phase`.
- `CANONICAL_COMPLIANT`: readiness/websocket gates enforce phase legality.

### Large-Log / Determinism Compliance
- `CANONICAL_COMPLIANT`: websocket gate now avoids eager deep candidate-dir scans when rows are present.
- `CANONICAL_COMPLIANT`: websocket gate prefers contract-bound status path when available.
- `CANONICAL_COMPLIANT`: bounded scan behavior is test-covered.

## Surgical Fixes Applied In This Packet Segment

1. `scripts/websocket_reliability_gate.py`
- Removed eager diagnostic `candidate_run_log_dirs` scan from happy path.
- Candidate discovery now runs only when status rows are absent.
- Contract status path is preferred when available.

2. `tests/test_websocket_reliability_gate.py`
- Added lazy candidate-search tests.
- Added missing-row candidate-search test.

3. `scripts/soak_report.sh`
- Removed latest-status shortcut behavior.
- Added explicit `--run-id` requirement.
- Added run-contract-anchored status source resolution (`status_slice_path` then `status_path`).

## Determinism Evidence

### Pre-fix 3-run sequence
- Evidence: `exports/packet_c_20260318/session_run_1.json..session_run_3.json`
- `validate_active` durations: `70.182s`, `70.619s`, `69.777s` (avg `70.193s`)

### Post-fix 3-run sequence
- Evidence: `exports/packet_c_20260318_postfix/session_run_1.json..session_run_3.json`
- `validate_active` durations: `23.683s`, `24.049s`, `24.350s` (avg `24.027s`)

### Post-fix run characteristics
- All three runs completed canonical lifecycle without manual debugging.
- All three ended with `postrun_validation.status=policy_failed` (expected gate policy outcome), not execution failure.

## Residual Risks
- Legacy non-canonical directories still exist (`logs_exec/logs_exec/paper_universal` / `data/data/paper_universal` historical artifacts from pre-normalization runs).
- Direct executor invocation remains possible outside canonical wrappers.

## Explicit Non-Changes
- No strategy logic/threshold tuning.
- No doctrine stage policy changes.
- No wallet doctrine boundary weakening.
- No execution permissiveness increase.
