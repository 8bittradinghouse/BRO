# CANONICAL_PAPER_SOAK_RECIPE

## Goal
Run a 45-minute canonical-paper soak recipe and capture evidence artifacts.

## Surface Class
- Workflow/backroom recipe doc, not a separate runtime mode.
- Public canonical paper start remains:
  `broctl paper -- --active-minutes <minutes> --wait-sec 25`
- Raw validation/gate commands below remain replay/forensics/control surfaces.

## Realism Doctrine Anchor
- Canonical paper harness is the emulation/proving lane for paper evidence.
- Realism semantics for this lane are defined in:
  - `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`

## Scientific Proving Discipline
- one canonical proving path per evidence claim
- one controlled variable at a time unless the packet explicitly says otherwise
- compare runs only when the proving lane is the same
- required lineage tuple for evidence claims:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`

## Commands
```bash
broctl paper -- --active-minutes 45 --wait-sec 25
# Use the run_id printed at completion to point MANIFEST_PATH at the matching run manifest.
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
- Check `paper_harness_audit` output carries the proving-lineage tuple:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`
- `harness_realism_grade` is descriptive only, not a pass/fail substitute.
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
