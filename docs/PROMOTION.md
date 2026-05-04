# PROMOTION

## Preconditions
- Soak hardening gate green.
- Promotion evidence gate green.
- Reconciliation status understood (`verification_level` inspected).
- In paper mode, `paper_sim_verified` /
  `paper_wallet_simulation_verified` mean paper-mode wallet/reconcile semantics,
  not proof from non-canonical shop tooling.

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
- Verify promotion-grade identity fields before trusting a pass:
  - proving-lineage tuple:
    - `run_id`
    - `git_commit`
    - `config_fingerprint_sha256`
    - `code_fingerprint_sha256`
  - visible fighter identity:
    - `profile_name`
  - manifest-backed artifact identity:
    - `manifest_present=true`
    - `manifest_load_error=""`
- `profile_name` is not part of the four-field proving-lineage tuple, but it
  remains mandatory for one-fighter identity and profile-policy selection.
- Promotion-grade evidence must be manifest-backed and lineage-complete.
- Config-only/backstage diagnostics are not sufficient for promotion proof.
