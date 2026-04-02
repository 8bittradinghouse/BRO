# Bro Master List Status (1-27)

> Legacy status snapshot retained for historical reference.
> For current canonical truth, use:
> `docs/CANONICAL_VALIDATION_PATH.md`, `docs/ENTRYPOINT_CLASSIFICATION.md`,
> and `docs/EDGE_TRUTH_RUNBOOK.md` with run-id-scoped evidence under
> `logs_exec/paper_universal/reports/<run_id>/`.

Last updated: 2026-03-08 (UTC)

Status legend:
- `Enforced`: implemented with code + tests and/or CI gate path.
- `Partial`: implemented in part, but not yet fully hard-gated to original spec.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Live GO/NO-GO Gate | Enforced | `scripts/prelive_gate.py`, `tests/test_prelive_gate.py`, `scripts/ci_gate.py` |
| 2 | Canary Live Mode + Auto Rollback | Enforced | `README.md` (canary ramp), `prodesk/ramp_controller.py`, `tests/test_ramp_controller.py` |
| 3 | Secrets Handling Upgrade | Enforced | `prodesk/secrets.py` (`env`/`file`/`manager`), gateway + prelive integration, `tests/test_secrets.py` |
| 4 | Stable Error Code Taxonomy | Enforced | `prodesk/error_codes.py` + error code emission in prelive/integrity/time/drill/promotion/forensics outputs |
| 5 | Workflow SLOs | Enforced | `scripts/readiness_gate.py`, `ops/ramp_policy.yaml`, `tests/test_readiness_gate.py` |
| 6 | Stuck-Function Watchdogs | Enforced | `scripts/guardian_watchdog.py`, `tests/test_guardian_watchdog.py` |
| 7 | Deterministic Replay Harness | Enforced | `scripts/sim_harness_audit.py`, `tests/test_sim_harness_audit.py`, `tests/test_simulator.py` |
| 8 | API Contract Drift Tests | Enforced | `scripts/api_contract_drift_audit.py`, `tests/test_api_contract_drift_audit.py`, CI contract step + gateway/feed hardening tests |
| 9 | Reconciliation Escalation Ladder | Enforced | `prodesk/ramp_controller.py`, `scripts/reconcile_daily.py`, `scripts/alert_profile_audit.py` |
| 10 | Paper/Live Parity Diagnostics | Enforced | `scripts/paper_live_parity.py`, `tests/test_paper_live_parity.py`, CI parity step |
| 11 | Wallet Safety Controls | Enforced | `scripts/prelive_gate.py` wallet checks, `prodesk/gateway.py` key/address normalization, `tests/test_gateway_hardening.py` |
| 12 | Performance Budgets | Enforced | `scripts/performance_budget_gate.py`, executor `gauge.process_rss_mb`, `tests/test_performance_budget_gate.py`, CI performance step |
| 13 | Trader Attribution Reporting v2 | Enforced | `scripts/desk_trade_report.py`, `tests/test_desk_trade_report.py` |
| 14 | Failure Runbooks | Enforced | `DRILLBOOK.md` + script command paths in `README.md` |
| 15 | Config Profile System (Paper/Canary/Live) | Enforced | `configs/*`, `scripts/profile_matrix_audit.py`, `tests/test_profile_matrix_audit.py` |
| 16 | Idempotent Recovery/Restart Protocol | Enforced | `scripts/deploy_paper_clean.sh`, `scripts/backup_daily.py`, `scripts/rollback_drill.py`, tests |
| 17 | Change-Risk Scoring in CI | Enforced | `scripts/change_risk_score.py`, `scripts/critical_path_gate.py`, `.github/workflows/ci.yml`, `tests/test_change_risk_score.py`, `tests/test_critical_path_gate.py` |
| 18 | Dependency Lock + Build Reproducibility | Enforced | `scripts/dependency_repro_audit.py`, `ops/dependency_lock.json`, CI dependency reproducibility step, `tests/test_dependency_repro_audit.py` |
| 19 | WebSocket Reliability Suite | Enforced | `scripts/websocket_hardening_audit.py` + `scripts/websocket_reliability_gate.py`, `tests/test_websocket_hardening_audit.py`, `tests/test_websocket_reliability_gate.py`, CI websocket evidence/SLO steps |
| 20 | Runtime Infra Hardening Pack | Enforced | `scripts/runtime_hardening_audit.py`, `tests/test_runtime_hardening_audit.py` |
| 21 | Dual-Source Data Consistency Check | Enforced | latency/consistency logic in `prodesk/latency_verifier.py` + execution integration/tests |
| 22 | Execution Quality Degradation Controller | Enforced | `prodesk/operating_mode.py`, `prodesk/ramp_controller.py`, `tests/test_operating_mode.py` |
| 23 | State Integrity Guardrails | Enforced | `scripts/run_integrity_audit.py`, `scripts/prelive_gate.py` manifest/run checks, tests |
| 24 | Clock/Time Discipline Hardening | Enforced | `scripts/time_discipline_audit.py`, `tests/test_time_discipline_audit.py`, CI gate |
| 25 | Network Fault Injection Drills | Enforced | `scripts/network_fault_drill.py`, `tests/test_network_fault_drill.py`, CI gate |
| 26 | Operational Forensics Bundle | Enforced | `scripts/forensics_bundle.py`, `scripts/forensic_snapshot.py`, `tests/test_forensics_bundle.py` |
| 27 | Promotion Gate by Evidence | Enforced | `scripts/promotion_evidence_gate.py` + `scripts/soak_evidence_window_gate.py` (repeatability window), tests, CI gate |

## Gap Audit Outcome (This Pass)

- Closed now:
  - Item `17` (change-risk scoring + extra strict gate on high-risk paths)
  - Item `26` (one-command incident forensics bundle)
  - Item `18` (dependency lock + build reproducibility gate)
- Remaining partials for future hardening wave:
  - none
