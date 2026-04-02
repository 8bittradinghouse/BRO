# Bro: Polymarket 5m Maker + Chainlink Lag Verifier + Near-Expiry Sniper

`Bro` is a production-oriented Polymarket bot with:
- maker core (post-only quote engine),
- Chainlink lead/lag verification,
- optional near-expiry taker sniper (lag-gated),
- paper and live runtime paths through the same execution stack.

Default posture is safe: `mode: paper`.

Project context and operating standards are documented in
[`docs/PROJECT_CHARTER.md`](docs/PROJECT_CHARTER.md).
Operator runbooks:
`docs/PAPER_STRESS.md`, `docs/PAPER_DISCIPLINE.md`, `docs/PROMOTION.md`, `docs/LIVE_CANARY.md`, `docs/INCIDENT_RESPONSE.md`.

## What Bro does

- Discovers and rotates 5-minute Up/Down crypto markets (BTC/SOL/XRP config profiles included).
- Maintains two-sided maker quotes with risk limits and mode controls.
- Monitors Chainlink feed vs book reaction to arm/disarm edge usage.
- Optionally fires IOC taker entries near expiry when edge and verification constraints pass.
- Logs structured events/status/errors for soak, readiness, and reconciliation.

## Safety model

- PAPER mode by default.
- LIVE requires:
  - `--confirm-live`
  - preflight pass (cannot be skipped in live)
  - non-root runtime (enforced by security checks)
  - localhost-only metrics bind in live
  - explicit live arming env acknowledgment: `SECURITY_ACK=YES`

## Repository layout

- `executor.py`: internal execution engine (not an operator entrypoint)
- `observer.py`: read-only market observer (no trading path)
- `simulator.py`: stress harness for execution stack
- `prodesk/`: modular execution/risk/feeds/security code
- `configs/`: BTC/SOL/XRP paper/live config examples
- `scripts/`: readiness/security/reconciliation/backup/CI tooling
- `DRILLBOOK.md`: live operation procedures
- `SECURITY.md`: deployment and dependency audit guidance

## Quickstart (Ubuntu / Vultr VPS)

1. Create environment and install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m pytest
```

2. Run a canonical paper session:
```bash
./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25
```

3. Run simulator stress:
```bash
python simulator.py --config execution_config.yaml --scenario all --difficulty nightmare --pairs 7 --steps 900 --dt-sec 1 --out-dir ./logs_sim
```
The simulator now includes a MetaMask-style wallet policy emulator (fake account by default) and writes `wallet_sim_summary.json` per run with:
- chain/account session state,
- order/cancel signature counts,
- blocked restricted actions (withdraw/bridge probes),
- policy violation count (must stay `0`).

4. Run CI gate locally:
```bash
python scripts/ci_gate.py
```

## Operator Workflow (5-Minute Map)

1. Installation
```bash
python3 -m pip install --break-system-packages --user -e .
```

2. Running tests
```bash
python3 -m pytest -q
broctl ci
broctl harness
broctl harness-qualify -- --skip-fault-drill
# policy-governed deep qualification (default policy: ops/harness_policy.yaml)
broctl harness-qualify
```

3. Canonical paper mode
```bash
broctl prestart
./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25
```

4. Backward-compatible aliases
- `broctl paper`, `broctl paper-stress`, and `broctl paper-discipline` are compatibility wrappers only.
- Canonical evidence runs must use `./scripts/canonical_paper_session.sh`.

5. Promotion workflow
```bash
broctl promote -- --soak-report ./exports/paper_universal_nightly.json --reconcile-report ./exports/paper_universal_reconcile.json --websocket-report ./exports/websocket_reliability.json
```

6. Live canary deployment
```bash
broctl canary
```
Live execution launch is internal-only and must be invoked by controlled orchestrator paths.

7. Incident response
```bash
broctl incident -- --log-dir ./logs_exec/paper_universal --run-id <run_id> --out-dir ./exports
```

## Live arming requirements

Set env vars:
```bash
export POLYMARKET_PRIVATE_KEY=...
export POLYMARKET_FUNDER=...
export SECURITY_ACK=YES
```

Auth secret sources support:
- `env` (default, backward-compatible)
- `file` (`auth.private_key_source.path`, `auth.funder_source.path`)
- `manager` (`auth.*_source.command` or `auth.*_source.argv`)

Live launch is intentionally not exposed as a direct operator command in this repo.
Use controlled orchestration paths only.

If preflight fails, Bro exits fail-closed.

## Notional sizing and per-window risk ($1-$20)

Bro supports `sizing.mode: notional` with:
- `min_usd`, `target_usd`, `max_usd`
- `rounding: floor|nearest`
- `price_source: mid|best_bid|best_ask`
- `exposure_cap_mode: per_market_total|per_side`

Both maker and sniper taker orders use the same sizing system.

## Canary size ramp

Optional ramp controller (`ramp.enabled: true`) adjusts `target_usd` between configured bounds using rolling health signals:
- reject pressure,
- stale oracle conditions,
- latency disarm frequency,
- reconciliation mismatch ratio.

On breach, Bro downshifts size and can disable sniper automatically.

## Multi-asset cloning (BTC/SOL/XRP)

No code changes required. Use config profiles:
- `configs/btc_live.yaml`
- `configs/sol_live.yaml`
- `configs/xrp_live.yaml`

Each profile sets:
- `asset.symbol`
- `asset.chainlink_symbols`
- `asset.discovery_symbols`

## Profile Hierarchy

BRO supports explicit profile layering via `extends`:
- `configs/profiles/base.yaml`
- `configs/profiles/paper_universal.yaml`
- `configs/profiles/live_canary.yaml`
- `configs/profiles/live_pilot.yaml`

`paper_stress.yaml` and `paper_discipline.yaml` are compatibility aliases to `paper_universal.yaml`.

At runtime, BRO stamps effective config identity in manifests and reports:
- `profile.name`
- config source path chain
- effective config SHA256
- git commit / dirty state
- dependency lock hash (if present)

Paper-mode configs are setup-locked:
- `runtime.paper_enforce_setup_lock=true`
- `runtime.paper_expected_profile_name == profile.name`
- `runtime.paper_expected_config_fingerprint_sha256 == _meta.effective_config_sha256`
- `profile.name` must be `paper_universal` for canonical paper workflows.
- synthetic paper-only fill shortcuts are disabled (`touch/near-touch/background fill ratios = 0`).

This blocks paper launches when experiment variables drift.

### 3 independent bots (Docker)

Run each as a separate compose project with isolated config/wallet/data/logs:

BTC:
```bash
COMPOSE_PROJECT_NAME=bro-btc \
BRO_ASSET=btc \
BRO_DOCKER_MODE=1 \
BRO_MODE=live \
BRO_CONFIG_PATH=./configs/btc_live_docker.yaml \
BRO_DATA_DIR=./data_btc \
BRO_LOG_DIR=./logs_btc \
BRO_METRICS_PORT=9111 \
docker compose up -d --build
```

SOL:
```bash
COMPOSE_PROJECT_NAME=bro-sol \
BRO_ASSET=sol \
BRO_DOCKER_MODE=1 \
BRO_MODE=live \
BRO_CONFIG_PATH=./configs/sol_live_docker.yaml \
BRO_DATA_DIR=./data_sol \
BRO_LOG_DIR=./logs_sol \
BRO_METRICS_PORT=9112 \
docker compose up -d --build
```

XRP:
```bash
COMPOSE_PROJECT_NAME=bro-xrp \
BRO_ASSET=xrp \
BRO_DOCKER_MODE=1 \
BRO_MODE=live \
BRO_CONFIG_PATH=./configs/xrp_live_docker.yaml \
BRO_DATA_DIR=./data_xrp \
BRO_LOG_DIR=./logs_xrp \
BRO_METRICS_PORT=9113 \
docker compose up -d --build
```

For a complete Vultr deployment workflow, see `README_DEPLOY_VULTR.md`.

## Docker hardening

- Non-root runtime user.
- `cap_drop: [ALL]`
- `no-new-privileges:true`
- read-only container FS.
- `tmpfs` for `/tmp`.
- metrics bound localhost only (`127.0.0.1:...:9108`).
- `.env` ignored; only `.env.example` shipped.

## Operations tooling

- Resolve run_id from an explicit manifest path before running gates:
```bash
MANIFEST_PATH="./logs_exec/paper_universal/run_manifest_<run_id>.json"
RUN_ID="$(python - "${MANIFEST_PATH}" <<'PY'
import json, sys
manifest_path = str(sys.argv[1]).strip()
if not manifest_path:
    raise SystemExit("MANIFEST_PATH is required")
payload = json.load(open(manifest_path, "r", encoding="utf-8"))
run_id = str(payload.get("run_id") or "").strip()
if not run_id:
    raise SystemExit("run_id missing in manifest")
print(run_id)
PY
)"
echo "RUN_ID=${RUN_ID}"
```

- Nightly soak report:
```bash
RUN_ID="<run_id>"
python scripts/nightly_soak_report.py --log-dir ./logs_exec --run-id "${RUN_ID}" --out ./logs_exec/nightly_report.json
```

- Daily reconciliation:
```bash
RUN_ID="<run_id>"
python scripts/reconcile_daily.py --config configs/btc_live.yaml --log-dir ./logs_btc --run-id "${RUN_ID}" --date 2026-03-03
```

- Security audit:
```bash
python scripts/security_audit.py --config configs/btc_live.yaml --mode live
```

- Dependency lock + build reproducibility audit:
```bash
python scripts/dependency_repro_audit.py --lock-manifest ops/dependency_lock.json
```

- Readiness gate:
```bash
RUN_ID="<run_id>"
python scripts/readiness_gate.py --log-dir ./logs_btc --run-id "${RUN_ID}" --policy ./ops/ramp_policy.yaml
```

- Runtime hardening audit:
```bash
python scripts/runtime_hardening_audit.py --compose ./docker-compose.yml
```

- Performance budget gate (latency/memory/capacity ratios):
```bash
python scripts/performance_budget_gate.py --log-dir ./logs_exec/paper_universal --run-id <run_id>
```

- Websocket/feed hardening audit:
```bash
python scripts/websocket_hardening_audit.py --config execution_config.yaml
```
```bash
python scripts/websocket_hardening_audit.py --config execution_config.yaml --log-dir ./logs_exec/paper_universal --run-id <run_id>
```
- Websocket reliability SLO gate (policy-driven):
```bash
python scripts/websocket_reliability_gate.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --budget ops/websocket_slo_budget.yaml
```

- API contract drift audit:
```bash
python scripts/api_contract_drift_audit.py --samples ./ops/api_contract_samples.json
```

- Clock/time discipline audit:
```bash
python scripts/time_discipline_audit.py --config execution_config.yaml
```

- Guardian launch profile audit:
```bash
python scripts/guardian_profile_audit.py --compose docker-compose.yml
```

- Alert threshold profile audit:
```bash
python scripts/alert_profile_audit.py --config execution_config.yaml
```

- Multi-profile matrix audit (BTC/SOL/XRP/docker isolation checks):
```bash
python scripts/profile_matrix_audit.py
```

- Prestart safety gate:
```bash
python scripts/prestart_gate.py --config configs/btc_paper_docker.yaml
```

- Config consistency audit (canonical vs active docker profile):
```bash
python scripts/config_consistency_audit.py --primary execution_config.yaml --secondary config.yaml
```

- Canonical paper session start (single authoritative lifecycle path):
```bash
./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25
```
`scripts/deploy_paper_clean.sh` is internal-only and invoked by the canonical session runner.

- Forensic snapshot for one run:
```bash
python scripts/forensic_snapshot.py --log-dir ./logs_exec/paper_universal --run-id <run_id>
```

- One-command forensics bundle (summary + tails + snapshots + config fingerprint):
```bash
python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --config execution_config.yaml --out-dir ./exports
```

- Simulation harness audit:
```bash
python scripts/sim_harness_audit.py --config configs/profiles/paper_universal.yaml
```

- Network fault drill evidence audit:
```bash
python scripts/network_fault_drill.py --drills-dir ./ops/drills
```

- Pre-live go/no-go gate (canonical wrapper, explicit run_id):
```bash
SECURITY_ACK=YES ./scripts/live_prelive_validation.sh <live_run_id> ./exports/prelive_<live_run_id>.json
```

- Promotion gate by evidence (soak + reconcile):
```bash
python scripts/promotion_evidence_gate.py --policy ops/promotion_policy.yaml --soak-report ./logs_exec/nightly_report.json --reconcile-report ./logs_exec/reconcile_daily.json --websocket-report ./logs_exec/websocket_reliability.json
```
`ops/promotion_policy.yaml` supports `profiles.<profile_name>` overrides for paper/live-specific thresholds while preserving strict global defaults.

- Unified soak hardening gate (integrity + performance + readiness + soak thresholds):
```bash
python scripts/soak_hardening_gate.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --budget ops/soak_budget.yaml
```

- One-command soak hardening report (explicit `run_id` required):
```bash
./scripts/soak_hardening_report.sh ./logs_exec/paper_universal ops/soak_budget.yaml <run_id>
```

- Deterministic 12-hour paper soak (locked universal profile):
```bash
./scripts/paper_12h_soak.sh
```

- Soak evidence window gate (repeatability over multiple runs):
```bash
python scripts/soak_evidence_window_gate.py --reports-root ./exports --policy ops/soak_evidence_policy.yaml
```

- Soak delta report (candidate vs baseline regression guard):
```bash
python scripts/soak_delta_report.py --baseline-dir ./exports/soak45_prev --candidate-dir ./exports/soak45_latest
```

- Paper/live parity diagnostics:
```bash
python scripts/paper_live_parity.py --paper-report ./logs_exec/nightly_paper.json --live-report ./logs_exec/nightly_live.json
```

- Backup bundle:
```bash
python scripts/backup_daily.py --log-dir ./logs_btc --state-path ./logs_btc/state.json --out-dir ./backups --exclude-glob "events_*" --require-files-min 5
```

- Rollback drill (prove latest backup is restorable):
```bash
python scripts/rollback_drill.py --backup-dir ./backups --require-state --require-manifest
```

- Run integrity audit (manifest/status/events continuity):
```bash
python scripts/run_integrity_audit.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --min-status-rows 5
```

- Unified ops snapshot (runtime + financial summary):
```bash
python scripts/ops_snapshot.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --out ./logs_exec/_ops/ops_snapshot.json
```

- Compact desk brief:
```bash
python scripts/ops_brief.py --log-dir ./logs_exec/paper_universal --run-id <run_id>
```

- Desk trade report (PnL/entry/exit/cancel/fill metrics):
```bash
python scripts/desk_trade_report.py --log-dir ./logs_exec/paper_universal --date $(date -u +%F)
```

## Observer and analysis tools

- Read-only observer:
```bash
python observer.py --config config.yaml
```

- Microstructure analysis:
```bash
python analyze.py --log-dir ./logs
```

- Lead/lag analysis:
```bash
python analyze_leadlag.py --log-dir ./logs_exec
```

## Notes

- This repo contains trading execution code paths. It is not observer-only.
- Keep defaults in paper until preflight, soak, and reconciliation checks are clean.
- Use `DRILLBOOK.md` as the run procedure for unattended live operation.
