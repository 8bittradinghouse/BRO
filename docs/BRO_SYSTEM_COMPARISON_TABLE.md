# BRO System Comparison Table (Vehicle Model)

Purpose: give operators and engineers a shared mental model that maps automotive system concepts to BRO runtime architecture for faster diagnosis and cleaner handoffs.

## Core Comparison Table
| Vehicle Part | BRO System | Primary Repo Surface | Diagnostic Meaning |
|---|---|---|---|
| Mission spec | Doctrine + operating rules | `BRO_*DOCTRINE*.txt`, `docs/` | Defines truth constraints and non-negotiables. |
| Chassis/frame | Architecture boundaries | `BRO_MODULE_ARCHITECTURE.txt` | Prevents hidden cross-domain coupling. |
| Driver controls | CLI + launch entrypoints | `prodesk/cli.py`, `run.sh`, `scripts/canonical_paper_session.*` | Ensures operators start through canonical paths. |
| Main ECU | Execution orchestrator | `executor.py` | Cycle control, posture, lane routing, status emission. |
| TCU | Risk authority | `prodesk/risk.py` | Legal vs illegal action decisions; fail-closed controls. |
| Transmission actuator | Order policy/execution | `prodesk/order_manager.py` | Converts intent into bounded submit/cancel behavior. |
| Fuel system | Wallet capital authority | `prodesk/wallet/*`, `prodesk/wallet_doctrine.py` | Deployable/reserved capital truth and reservation integrity. |
| Starter/ignition | Tx lifecycle + nonce | `prodesk/tx_manager.py`, `prodesk/wallet/wallet_nonce.py` | Sequencing and transaction-state truth. |
| Air + sensors | Market/oracle feeds | `prodesk/book_feed.py`, `prodesk/chainlink_feed.py`, `prodesk/pyth_feed.py`, `prodesk/market_data.py` | Priceability and freshness signal integrity. |
| Cooling system | Mode + ramp controls | `prodesk/operating_mode.py`, `prodesk/ramp_controller.py` | Stabilizes runtime under stress and reject waves. |
| Brake system | Hard stop controls | `prodesk/risk.py`, `prodesk/alerts.py`, `scripts/guardian_*` | Safety halts and automatic containment. |
| Dashboard cluster | Status/report/metrics | `executor.py`, `scripts/nightly_soak_report.py`, `prodesk/prometheus_exporter.py` | Operator visibility and truth surfaces. |
| Black box recorder | Artifact identity + run contract | `prodesk/artifact_identity.py`, `prodesk/run_contract.py`, `logs_exec/`, `exports/` | Forensic replay and lineage evidence. |
| Inspection station | Gates and audits | `scripts/prestart_gate.py`, `scripts/profile_matrix_audit.py`, `scripts/readiness_gate.py`, `scripts/soak_hardening_gate.py`, `scripts/outcome_truth_audit.py`, `scripts/ci_gate.py` | Promotion safety checks and release controls. |
| Test dyno | Test harness | `tests/` | Controlled proof before runtime exposure. |

## Multi-Brain View
1. Strategy brain: `prodesk/strategy.py`
2. Execution brain: `executor.py`
3. Risk brain: `prodesk/risk.py`
4. Order-routing brain: `prodesk/order_manager.py`
5. Wallet brain: `prodesk/wallet/*`
6. Transaction brain: `prodesk/tx_manager.py`
7. Data brain: feeds + discovery (`book_feed`, `chainlink_feed`, `pyth_feed`, `market_discovery`)
8. Mode-control brain: `prodesk/operating_mode.py`, `prodesk/ramp_controller.py`
9. Reporting brain: `scripts/nightly_soak_report.py`, `prodesk/reporting.py`
10. Gate/audit brain: `scripts/*gate.py`, `scripts/*audit.py`
11. Session brain: `scripts/canonical_paper_session.py`, `scripts/canonical_paper_validation.sh`

## Harness Families
1. Config harness: profile/config + setup-lock/fingerprint (`configs/`, `execution_config.yaml`, `prodesk/config.py`)
2. Decision harness: executor -> risk -> order manager
3. Lifecycle harness: posture/expiry/reduce-only context continuity
4. Data harness: book/oracle freshness to valuation ladder
5. Capital harness: wallet authority -> tx -> gateway -> reconcile
6. Evidence harness: events/status -> reports -> gates
7. Safety harness: kill-switch/guardian/prestart/readiness/soak controls

## Primary Connector Boxes (Frequent Fault Domains)
1. Decision connector box: lifecycle context mismatch across executor/risk/order manager.
2. Evidence connector box: runtime succeeds but validation/report path mis-signals or stalls.
3. Config-lock connector box: setup-lock/fingerprint drift across profiles/config.
4. Capital connector box: reservation/nonce/tx state inconsistency.
5. Data connector box: feed starvation and book-availability transitions near expiry.

## Diagnosis Workflow (Use With Template)
1. Identify symptom from dashboard/report.
2. Map symptom to one subsystem family (data, decision, capital, safety, evidence).
3. Trace one harness upstream to first contradiction.
4. Determine if fuse/gate tripped correctly or falsely.
5. Classify root cause: expected protective behavior, wiring defect, data starvation, contract mismatch, or unknown.
6. Patch only after classification and bounded proof plan.
