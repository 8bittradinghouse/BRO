# Bro Live Drillbook

## Scope

Operational drillbook for unattended Bro deployments on ODAH:
- startup,
- kill-switch response,
- outage handling,
- restart/recovery,
- secret leak checks,
- mode interpretation.

Default production assumption: one process per asset (`BTC`, `SOL`, `XRP`), isolated config, wallet, logs, data.

## Startup Procedure

1. Preflight host:
   - system clock synced (NTP active).
   - firewall policy applied (deny inbound except controlled SSH).
   - run user is non-root.

2. Validate repo and config:
```bash
python scripts/ci_gate.py --skip-pip-audit
python scripts/security_audit.py --config configs/btc_live.yaml --mode live
```

3. Export live env:
```bash
export POLYMARKET_PRIVATE_KEY=...
export POLYMARKET_FUNDER=...
export SECURITY_ACK=YES
```

4. Pre-live go/no-go (must pass):
```bash
python scripts/prelive_gate.py \
  --config configs/btc_live.yaml \
  --required-stage pilot_live
```

5. Start one bot (controlled orchestrator path only):
- direct `executor.py` operator launch is non-canonical and blocked for canonical workflows.
- use your managed service/orchestrator runbook for live start after step 4 passes.

6. Verify status stream:
   - `kill_switch=false`
   - `chainlink.connected=true`
   - `book_feed.connected=true`
   - `gauge.operating_mode_state` stable near `0`.

## Kill-Switch Event Drill

Trigger conditions include:
- hard risk violation,
- auto-safe-stop alert policy breach,
- external guard-stop file.

Operator actions:
0. Resolve target run id from an explicit manifest path before post-run gates:
```bash
MANIFEST_PATH="./logs_exec/btc_live/run_manifest_<run_id>.json"
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
```
1. Confirm cancel-all event in `events_*.jsonl` (`kill_switch_cancel_all`).
2. Confirm no residual open orders at venue and in status.
3. Record cause from `kill_reason`.
4. Do not clear kill until root cause is identified.
5. Run integrity checks before any restart:
```bash
RUN_ID="<run_id>"
python scripts/run_integrity_audit.py --log-dir ./logs_exec/btc_live --run-id "${RUN_ID}" --min-status-rows 5
python scripts/forensic_snapshot.py --log-dir ./logs_exec/btc_live --run-id <run_id>
```
6. Restart only after config/risk correction and preflight rerun.

## Network Outage Drill

Symptoms:
- book feed disconnects/reconnect loops,
- chainlink disconnect or stale tick age spikes.

Actions:
1. Confirm transition in status and events.
2. Expect mode degradation (`normal -> cautious -> maker_only`).
3. If persistent, external guardian should arm guard file; bot should safe-stop.
4. Restore network path, then restart process cleanly.
5. Review `errors_*.jsonl` and reconnect counters before re-arming.

## Restart / Recovery Drill

1. Stop process.
2. Preserve logs/state and create backup:
```bash
python scripts/backup_daily.py --log-dir ./logs_exec/btc_live --state-path ./data/btc_live/state.json --out-dir ./backups --require-files-min 5
python scripts/rollback_drill.py --backup-dir ./backups --require-state --require-manifest
```
3. Run:
```bash
RUN_ID="<run_id>"
python scripts/reconcile_daily.py --config configs/btc_live.yaml --log-dir ./logs_btc --run-id "${RUN_ID}" --date $(date -u +%F)
```
4. Check `mismatch_ratio` and `status`.
5. Re-run pre-live gate:
```bash
python scripts/prelive_gate.py --config configs/btc_live.yaml --required-stage pilot_live
```
6. Restart only if reconciliation and security checks are acceptable.

## Secret Leakage Verification

1. Ensure only `.env.example` exists in repo payloads.
2. Confirm `.env` is not packaged.
3. Run security scan:
```bash
python scripts/security_audit.py --config configs/btc_live.yaml --mode live
```
4. Grep logs for key fragments:
```bash
rg -n "PRIVATE_KEY|POLYMARKET_PRIVATE_KEY|api_key|secret|funder" logs_* logs_exec* || true
```
5. Any hit containing actual secret value is incident-level.

## Mode Interpretation

`normal`:
- full maker behavior; sniper allowed when lag-gated and configured.

`cautious`:
- reduced size, wider spread; sniper constrained by edge/risk gates.

`maker_only`:
- tighter safety posture, reduced exposure, sniper disabled.

`safe_stop`:
- terminal degraded state; kill-switch expected; cancel-all path engaged.

Typical trigger drivers:
- stale book reject ratio,
- outage ratio,
- disarmed latency-verifier ratio,
- error ratio,
- transition thrashing.

## Fast Kill

1. CLAWD operator action: issue immediate stop command to service/process manager.
2. External guard file:
```bash
echo "manual_critical_stop" > /logs/guard_stop.txt
```
3. Verify `external_guard.active=true` and kill-switch status row.

## FINAL CHECKLIST

1. Run PAPER safely:
   - `./scripts/deploy_paper_clean.sh --wait-sec 25 --verify-min-status-rows 1`
   - `python scripts/run_integrity_audit.py --log-dir ./logs_exec/paper_universal --run-id <run_id> --min-status-rows 5`
2. Arm LIVE safely:
   - set env (`POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER`, `SECURITY_ACK=YES`)
   - pass `prelive_gate`
   - run with `--confirm-live`
3. Run 3 bots independently:
   - separate compose project names
   - separate config/wallet/data/log paths
   - separate localhost metrics ports
4. Kill fast path verified:
   - CLAWD stop command works
   - guard file kill path works (`/logs/guard_stop.txt`)
   - cancel-all observed in event log
