# Bro Workflow Validation Checklist

> Legacy checklist snapshot retained for historical context.
> Current public canonical paper start path is
> `broctl paper -- --active-minutes <minutes> --wait-sec 25`.
> Backend canonical validation truth is still carried by
> `./scripts/canonical_paper_session.sh` +
> `./scripts/canonical_paper_validation.sh <run_id> --run-contract <path>`,
> with edge evidence sourced from `event_type=edge_evaluation` rows.

## Status Legend
- `PASS`: validated with concrete evidence artifact and/or gate output.
- `PARTIAL`: partially covered; requires additional scenario-specific validation.
- `PENDING`: not yet validated in this cycle.

## Current Validation Scope
- Canonical profile: `configs/profiles/paper_universal.yaml`
- Canonical log root: `logs_exec/paper_universal`
- Canonical run evidence: `logs_exec/paper_universal/reports/<run_id>/`
- Canonical edge evidence stream: `events_*.jsonl` rows with `event_type=edge_evaluation`

## Workflow Matrix
| ID | Workflow | Status | Validation Source / Evidence |
|---|---|---|---|
| 1 | Boot/Config Load | PASS | `scripts/prestart_gate.py`, `scripts/config_consistency_audit.py` |
| 2 | Prestart Safety Gate | PASS | `scripts/prestart_gate.py` |
| 3 | Market Discovery | PASS | `events_*`: `targets_updated`, `targets_refreshed` |
| 4 | Token Metadata Sync | PASS | `events_*`: `token_expiry_map_update`, `token_side_map_update`, `token_strike_map_update` |
| 5 | Chainlink Feed Ingest | PASS | `websocket_reliability.json` (`chainlink_*` metrics) |
| 6 | Orderbook Feed Ingest | PASS | `websocket_reliability.json` (`book_feed_*` metrics) |
| 7 | Latency/Lag Verification | PASS | `nightly.json` latency distribution + `leadlag_book_move` events |
| 8 | Operating Mode Engine | PASS | status `gauge.operating_mode_state` stable; no unsafe transitions |
| 9 | Maker Quote Engine | PASS | `nightly.json` maker submits/fills |
| 10 | Quote Quality Filter | PASS | `events_*`: `quote_quality_skip` |
| 11 | Risk Gate Pre-Order | PASS | `events_*`: `risk_reject` reason distribution |
| 12 | Taker Sniper Arming | PASS | `events_*`: `taker_mode_transition`, `edge_evaluation` (`action_taken=none`, taxonomy `block_reason`) |
| 13 | Taker Sniper Execution | PASS | `nightly.json` taker submits/fills |
| 14 | Order Lifecycle Tracking | PASS | event counts: submit/cancel/fill consistency; no orphan fills |
| 15 | Position and Exposure State | PASS | status positions + risk reject behavior |
| 16 | PnL and Execution Quality | PASS | `nightly.json` + run-filtered `desk_trade_report` |
| 17 | Telemetry Emission | PASS | non-empty status/events logs for run |
| 18 | Integrity Manifests | PASS | `integrity.json`, manifest present and monotonic |
| 19 | Guardian Watchdog | PASS | `guardian_watchdog.py` coverage in CI + profile audit |
| 20 | Kill Switch Handling | PASS | controlled guard-stop drill produced `kill_switch` risk rejects (`external_guard_stop:triggered`) |
| 21 | Readiness Assessment | PASS | `soak_hardening.json` readiness section |
| 22 | Reconciliation | PASS | `reconcile.json` mismatch ratio `0.0` |
| 23 | Websocket Reliability Gate | PASS | `websocket_reliability.json` |
| 24 | Performance Budget Gate | PASS | `performance_budget.json` |
| 25 | Promotion Evidence Gate | PASS | `promotion.json` |
| 26 | Soak Hardening Gate | PASS | pass in active evidence cycle; post-stop staleness is expected and non-defect |
| 27 | Evidence Window Gate | PASS | `evidence_window.json` (`3/3` pass) |
| 28 | Paper/Live Parity Diagnostics | PASS | `scripts/paper_live_parity.py` exercised in CI gate |
| 29 | Forensic Snapshot/Bundle | PASS | `forensics_bundle.json` + incident tarball |
| 30 | Backup and Rollback Drill | PASS | `backup_daily.py` + `rollback_drill.py` in CI gate |
| 31 | CI and Audit Workflow | PASS | `scripts/ci_gate.py` all steps passed |
| 32 | Deployment Workflow | PARTIAL | paper docker deployment/start-stop validated; strict live prelive path still environment/live-profile dependent |

## Notes
- `Soak Hardening Gate` can report stale-status failure when bot is intentionally stopped; treat as expected post-stop condition.
- `Prelive/live deployment` remains partially validated in this cycle because strict live profile checks require live-profile consistency and fresh live-path evidence.
