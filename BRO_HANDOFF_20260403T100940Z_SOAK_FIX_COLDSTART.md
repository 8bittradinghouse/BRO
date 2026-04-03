# BRO Handoff — Soak Fix + Cold-Start Verification

## Timestamp
2026-04-03 UTC

## Branch / Git
- Branch: `consultant/full-snapshot-public-20260402T055838Z`
- Latest pushed commit: `6647476`
- Remote: `origin git@github-bro:8bittradinghouse/BRO.git`

## What Was Completed
### 1) Surgical soak semantics fix set
Files changed in commit `6647476`:
- `ops/soak_budget.yaml`
- `scripts/nightly_soak_report.py`
- `scripts/soak_hardening_gate.py`
- `tests/test_nightly_soak_report.py`
- `tests/test_soak_hardening_gate.py`

### 2) Concrete fixes implemented
- Maker fill-rate semantics now use **unique filled orders** (`maker_filled_orders / maker_submits`) rather than raw fill-event count.
- Maker submit enforcement now properly supports **opportunity-aware required submits**:
  - `required_submits = min(min_maker_submits, maker_actionable_opportunity_rows)`
- `maker_timing_gate_closed` included in default non-actionable maker reasons for opportunity-aware submit enforcement.
- Maker fill-rate hard-fail now has **low-sample guard**:
  - `maker_fill_rate_enforcement_min_submits` (set to `5` in soak budget).
- Quote uptime comparator epsilon adjusted to explicit configured tolerance:
  - `quote_uptime_ratio.min_eps = 0.0020`.

### 3) Test proof
- Targeted tests (nightly + soak gate): pass.
- Full regression: `601 passed`.

### 4) Runtime proof
#### 8-minute diagnostic run
- Run: `5b0504f2-88a8-460e-876f-7c118e94e8ef`
- Status: policy failed due non-promotable short-window/no participation conditions.
- Use: validates no hidden accounting drift at zero-submit conditions.

#### Cold-start 10-minute canonical run (fresh stack)
- Cold reset executed with `docker compose down` before run.
- Run: `ce2cd2c4-e24c-4954-8dab-8c6bed00dd2c`
- Validation summary: **PASS** (`gate_passed: true`, all validators exit code `0`).
- Key metrics:
  - `duration_minutes=10.4623`
  - `quote_uptime_ratio=0.09457`
  - `maker_submits=1`
  - `maker_filled_orders=1`
  - `maker_fill_rate=1.0`
  - `taker_bonus_submits=0` (valid by policy)
- Soak decision trace confirms:
  - runtime promotion eligibility pass
  - meaningful participation pass
  - maker submit enforcement pass with `required_submits=1.0`
  - maker fill-rate enforcement skipped as low sample (`min_submits=5`, applied=`false`)

## Artifacts to inspect first
- `/home/odah/bro/base/logs_exec/paper_universal/reports/ce2cd2c4-e24c-4954-8dab-8c6bed00dd2c/validation_summary.json`
- `/home/odah/bro/base/logs_exec/paper_universal/reports/ce2cd2c4-e24c-4954-8dab-8c6bed00dd2c/nightly_soak_report.json`
- `/home/odah/bro/base/logs_exec/paper_universal/reports/ce2cd2c4-e24c-4954-8dab-8c6bed00dd2c/soak_hardening_gate.json`
- `/home/odah/bro/base/exports/BRO_soak_fix_verification_20260403T095333Z/README.md`

## Important Worktree Note
- There are additional pre-existing modified/untracked files on this branch outside commit `6647476`.
- This commit is intentionally scoped only to soak semantic hardening.

## Resume Plan (first 3 actions)
1. Re-open latest cold-start report trio (validation summary + nightly + soak gate) and reconfirm no drift.
2. If handing to external reviewer (Grok/Nova), share commit `6647476` + run id `ce2cd2c4-e24c-4954-8dab-8c6bed00dd2c`.
3. Continue next packet from current branch without rebasing until reviewer pass is acknowledged.
