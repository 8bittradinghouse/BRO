# BRO Handoff — Maker/Taker Runtime Forensic + Next-Step Packet

## Session Snapshot
- Date (UTC): 2026-03-31
- Last canonical exercised run: `8276b008-856f-41a0-b05e-e0bd38957d12`
- Previous exercised comparison run: `5496dbf5-8ba9-46ea-89d2-1b06be1a77a6`
- Earlier reference run with positive net quality: `316ac5c5-f67d-4f0f-a1b3-e38f2dc4fb76`

## What Was Completed This Session
1. Protection-path semantic truth hardening finalized and validated.
2. Added explicit unknown semantics for missing control-authority fields (no false coercion).
3. Added explicit trigger-chain interpretation field (`causal_suppression_chain` vs `observational_timeline_only`).
4. Added explicit suppression primary-cause coverage for `status_rows_missing` and `runtime_state_ambiguous`.
5. Tests passed:
- Targeted: `32 passed`
- Full suite: `552 passed`
6. Canonical run completed with validation pass:
- Run: `8276b008-856f-41a0-b05e-e0bd38957d12`
- Validation summary: `ok=true`, `overall_exit_code=0`, `validator_determinism_ok=true`

## Files Changed in This Session
- `prodesk/runtime_semantics.py`
- `scripts/nightly_soak_report.py`
- `tests/test_runtime_semantics.py`
- `tests/test_nightly_soak_report.py`
- `docs/DOCTRINE_RUNBOOK.md`
- `BRO_CANONICAL_DOCTRINE.txt`

## Fresh Forensic Diagnosis (No Implementation Yet)
### Executive Status
- Maker lane: RED
- Taker quality: RED
- Status/event truth: AMBER
- Capacity/readiness coupling: AMBER

### Verified Highlights
1. Maker instability across exercised runs:
- `5496...`: maker submits/fills = `29/0`
- `8276...`: maker submits/fills = `14/7`
2. Maker cancel churn in `5496...`:
- maker rest median `0.200s`, p90 `0.209s`
- `<250ms`: `96.6%`
- all maker cancels reason: `replace_quote`
3. Taker exercised but poor quality in recent runs:
- `5496...`: `capture_minus_adverse=-356.4963`, adverse ratio `0.8293`
- `8276...`: `capture_minus_adverse=-247.8285`, adverse ratio `0.8929`
4. Taker submit timing is not decision-path-latency broken:
- nearest submit-to-taker-edge-eval median ~`0.001s`
5. Status snapshot mismatch:
- event stream shows real submits/fills/cancels
- status quick gauges appear quiet due to snapshot semantics/cadence
6. Capacity coupling:
- frequent `position_cap` rejects
- no order/cancel rate-limit rejects
7. Readiness remains blocked for substantive quality reasons (not suppression cosmetics).

## Root-Cause Direction (Current Best Evidence)
1. Maker lane weakness likely coupled to fast replacement cadence under sniper-active loop + low replace threshold.
2. `maker_no_submission` bucket is overloaded and currently hides causal subtypes.
3. Taker expectancy weakness likely from rapid repeated re-entry bursts on same targets under adverse microstructure windows.
4. Status quick-read surfaces are snapshot-valid but operator-misleading without window counters.

## Approved Surgical Plan (Next Packet)
### P0 (first on resume)
1. Maker minimum rest guard before replace-cancel
- File focus: `prodesk/order_manager.py` (+ config surface in `execution_config.yaml`)
- Intent: prevent ultra-short rest churn while preserving fail-closed safety behavior
- Proof: maker rest distribution improves, maker fills non-zero on exercised runs, no stale/safety regressions

2. Maker no-submission attribution split
- File focus: `prodesk/order_manager.py`, `executor.py`, edge-eval emission path
- Intent: split generic `maker_no_submission` into explicit machine-readable causes where uniquely known
- Proof: reduced generic bucket, explicit taxonomy in artifacts, no runtime semantic drift

### P1
3. Taker anti-churn guard (same-target rapid re-fire control)
4. Taker quality freshness guard (stricter action-side oracle-age envelope, explicit block reason)

### P1/P2
5. Status window counters for operator truth alignment (`*_since_last_status`)
6. Cap-coupling observability split (by lane/side) without gate weakening

## Hard Scope Guardrails (Do Not Drift)
- No strategy redesign
- No threshold loosening for convenience
- No safety suppression weakening
- No outcome-truth semantic weakening
- No control-plane authority weakening
- Additive-first semantics only

## Resume Checklist (First 10 Minutes)
1. Re-open this handoff and verify no conflicting local edits in touched files.
2. Implement P0.1 (maker min-rest replace guard) only.
3. Add targeted tests for P0.1.
4. Implement P0.2 (maker no-submission attribution split).
5. Add targeted tests for P0.2.
6. Run:
- `PYTHONPATH=. pytest -q tests/test_runtime_semantics.py tests/test_nightly_soak_report.py`
- `PYTHONPATH=. pytest -q`
7. Run one 20-min canonical session.
8. Compare against baselines (`5496...`, `8276...`) on maker rest, maker fill rate, taker quality, and status alignment.

## Key Artifact Paths
- `logs_exec/paper_universal/reports/8276b008-856f-41a0-b05e-e0bd38957d12/nightly_soak_report.json`
- `logs_exec/paper_universal/reports/8276b008-856f-41a0-b05e-e0bd38957d12/validation_summary.json`
- `logs_exec/paper_universal/reports/5496dbf5-8ba9-46ea-89d2-1b06be1a77a6/nightly_soak_report.json`
- `logs_exec/paper_universal/reports/316ac5c5-f67d-4f0f-a1b3-e38f2dc4fb76/nightly_soak_report.json`

## Notes for Next Operator Pass
- This is a runtime quality packet, not suppression packet.
- Keep certainty labeling strict: verified vs inferred vs unknown.
- Do not claim closure without exercised-run proof.
