# LIVE_CANARY

## Goal
Launch smallest live profile with full guardrails.

## Commands
```bash
export POLYMARKET_PRIVATE_KEY=...
export POLYMARKET_FUNDER=...
export SECURITY_ACK=YES
python scripts/prelive_gate.py --config configs/profiles/live_canary.yaml --policy ops/ramp_policy.yaml --out ./exports/prelive_canary.json
```
Live start is internal-only; do not launch `executor.py` directly from operator docs.

## Verify Active Profile
- `run_manifest_*.json` must show `profile_name=live_canary`.

## Auth Failure Response
1. Run `python scripts/prelive_gate.py --config configs/profiles/live_canary.yaml`.
2. Resolve `secret_load_failed` / `invalid_private_key` / `invalid_funder` findings before retry.

## Controlled Shutdown
```bash
touch ./logs_exec/live_canary/guard_stop.txt
```
Then run after-action reports (`nightly_soak_report`, `reconcile_daily`, `forensics_bundle`).
