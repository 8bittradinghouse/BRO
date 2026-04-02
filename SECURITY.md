# Bro Security Guide

## Runtime Security Posture

- LIVE mode is fail-closed:
  - requires `--confirm-live`
  - requires `SECURITY_ACK=YES`
  - preflight cannot be skipped
  - rejects root user in live mode
  - enforces localhost-only metrics bind in live mode
- URL allowlist + TLS-only checks enforced by `prodesk/security.py`.
- Storage path root checks and symlink/path checks enforced.
- Structured log redaction is applied to sensitive key fragments.

## Host Hardening (ODAH)

- Use a dedicated non-root service user.
- Keep inbound firewall denied by default.
- Allow SSH only if needed, from trusted source ranges.
- Keep OS patches and Python patched.
- Keep bot directories with least-privilege permissions.

## Clock / NTP Guidance

- LIVE preflight performs best-effort clock skew validation.
- Keep NTP active and monitor drift continuously.
- If skew exceeds configured threshold, treat as hard stop for live arming.

## Container Hardening

- Non-root container user.
- `cap_drop: [ALL]`
- `no-new-privileges:true`
- read-only root filesystem
- `tmpfs` for `/tmp`
- localhost metrics binding only

## Secrets Handling

- Only `.env.example` should be in repo payloads.
- `.env` is ignored and should not be committed or packaged.
- Use environment variables at runtime for private key/funder.
- Rotate secrets if any accidental exposure is detected.

## Dependency Audit Procedure

1. Install pinned dependencies:
```bash
pip install -r requirements.txt
```

2. Run CI gate:
```bash
python scripts/ci_gate.py
```

3. Optional vulnerability audit:
```bash
pip install pip-audit
python -m pip_audit
```

4. If vulnerabilities are reported:
   - review severity/reachability,
   - pin upgraded safe versions,
   - rerun tests and `scripts/ci_gate.py`.

## Verification Commands

- Resolve explicit run id first:
```bash
RUN_ID="$(python - <<'PY'
import glob, json, os
paths = sorted(glob.glob("./logs_btc/run_manifest_*.json"), key=os.path.getmtime)
if not paths:
    raise SystemExit("no run_manifest_*.json found")
payload = json.load(open(paths[-1], "r", encoding="utf-8"))
run_id = str(payload.get("run_id") or "").strip()
if not run_id:
    raise SystemExit("run_id missing in manifest")
print(run_id)
PY
)"
```

- Security audit:
```bash
python scripts/security_audit.py --config configs/btc_live.yaml --mode live
```

- Readiness gate:
```bash
RUN_ID="<run_id>"
python scripts/readiness_gate.py --log-dir ./logs_btc --run-id "${RUN_ID}" --policy ./ops/ramp_policy.yaml
```

- Reconciliation:
```bash
RUN_ID="<run_id>"
python scripts/reconcile_daily.py --config configs/btc_live.yaml --log-dir ./logs_btc --run-id "${RUN_ID}" --date 2026-03-03
```

## Backup / Retention Policy

- Create daily bundles:
```bash
python scripts/backup_daily.py --log-dir ./logs_btc --state-path ./logs_btc/state.json --out-dir ./backups --keep-days 14
```
- Bundle includes logs, state, manifests, checksum sidecar.
- Recommended:
  - keep at least 14 days local,
  - copy checksum-verified bundles off-host daily,
  - optionally encrypt bundle before off-host transfer.
