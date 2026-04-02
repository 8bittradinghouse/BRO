# PAPER_STRESS

## Goal
Run a 45-minute paper soak with the canonical universal paper profile and capture evidence artifacts.

## Commands
```bash
SESSION_JSON="$(./scripts/canonical_paper_session.sh --active-minutes 45 --wait-sec 25)"
RUN_ID="$(printf '%s\n' "${SESSION_JSON}" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')"
RUN_CONTRACT="./logs_exec/paper_universal/run_contract_${RUN_ID}.json"
./scripts/canonical_paper_validation.sh "${RUN_ID}" --session-phase validate_postrun --run-contract "${RUN_CONTRACT}"
python scripts/nightly_soak_report.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --out ./exports/paper_universal_nightly.json
python scripts/reconcile_daily.py --config configs/profiles/paper_universal.yaml --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --out ./exports/paper_universal_reconcile.json
python scripts/soak_hardening_gate.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --budget ops/soak_budget.yaml --out ./exports/paper_universal_soak_gate.json
```

For a deterministic long soak on the locked universal profile:
```bash
./scripts/paper_12h_soak.sh
```

## Verify Active Profile
- Check `run_manifest_${RUN_ID}.json` under `./logs_exec/paper_universal`:
  - `runtime_identity.profile_name` must be `paper_universal`.
  - `runtime_identity.effective_config_sha256` must be non-empty.
- Realism guardrail: synthetic passive fill shortcuts must remain disabled (`paper_passive_touch_fill_enabled=false`, fill ratios = `0.0`).
- Setup lock must be enabled and internally consistent:
```bash
python - <<'PY'
from pathlib import Path
from prodesk.config import load_execution_config
cfg = load_execution_config(Path("configs/profiles/paper_universal.yaml"))
rt = cfg["runtime"]
print("lock_enabled", rt["paper_enforce_setup_lock"])
print("profile", cfg["profile"]["name"], "expected_profile", rt["paper_expected_profile_name"])
print("observed_fp", cfg["_meta"]["effective_config_sha256"])
print("expected_fp", rt["paper_expected_config_fingerprint_sha256"])
PY
```

## If Guardian Trips
1. `docker compose logs --tail=200 bro-guardian`
2. Check `guard_stop.txt` and latest `errors_*.jsonl`.
3. Build forensic bundle:
```bash
python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --config configs/profiles/paper_universal.yaml --out-dir ./exports
```
