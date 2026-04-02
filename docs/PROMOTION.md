# PROMOTION

## Preconditions
- Soak hardening gate green.
- Promotion evidence gate green.
- Reconciliation status understood (`verification_level` inspected).

## Commands
```bash
python scripts/promotion_evidence_gate.py \
  --policy ops/promotion_policy.yaml \
  --soak-report ./exports/paper_universal_nightly.json \
  --reconcile-report ./exports/paper_universal_reconcile.json \
  --websocket-report ./exports/websocket_reliability.json \
  --out ./exports/promotion_gate.json
```

## Decision Transparency
- Inspect `decision_trace` in `promotion_gate.json`.
- Verify `artifact_identity` fields (run_id/config hash/git/dependency lock hash).
