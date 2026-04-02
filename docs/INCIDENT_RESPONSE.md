# INCIDENT_RESPONSE

## Immediate Actions
1. Stop execution safely (guardian stop-file or orchestrator stop).
2. Preserve evidence (no log cleanup until bundle complete).

## Triage Commands
```bash
RUN_ID="<run_id>"
python scripts/ops_snapshot.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --out ./exports/ops_snapshot.json
python scripts/forensic_snapshot.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --out ./exports/forensic_snapshot.json
python scripts/forensics_bundle.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --config configs/profiles/paper_universal.yaml --out-dir ./exports
```

## What To Confirm
- Active profile and config hash from manifest/runtime identity.
- Gate `decision_trace` for all failed checks.
- Reconcile `verification_level` (venue_verified vs partial truth).
- Guardian trigger reason and timestamp alignment.

## Post-Incident
- Record rollback/promotion decision.
- Attach bundle tarball + hashes to incident ticket.
