# Residual Flag Triage

## Scope
Clean-tree anchor run: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`

Post-patch runtime proof run: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`

Packet-1 smoke validation run: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`

This document classifies the residual flags from the latest clean-tree 20-minute wiring run. It is a report-only triage surface unless a packet promotes a candidate into a proven code/doc/test change.

## Summary
VERIFIED: The run passed canonical paper validation and all validator exit codes were `0`.

VERIFIED: The run still produced residual signals. These are not all defects. They are classified below as expected protective behavior, neutral telemetry, true candidates, or `UNKNOWN`.

VERIFIED: The post-patch proof run passed canonical paper validation and all validator exit codes were `0`.

VERIFIED: The post-patch proof run closed the taker-scope `fair_probability_missing` residual. The proof run had `0` taker `fair_probability_missing` rows and `13` maker `fair_probability_missing` rows.

VERIFIED: Packet 1 closed the `soak_maker_submits_too_low` false-positive path by adding maker-scope `fair_probability_missing` to the non-actionable maker-submit enforcement taxonomy in `ops/soak_budget.yaml`.

VERIFIED: Current-code replays of the clean anchor run and post-patch runtime proof now pass `soak_hardening_gate.py` with `ok=true` and `finding_count=0`.

VERIFIED: Requested 5-minute smoke validation run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d` produced `VALID_ACTIVE` runtime behavior and verified packet-1 maker-submit enforcement, but correctly failed canonical postrun validation because the current canonical soak budget requires `10` minutes and `20` status rows.

## Flag Board
| Signal | Count / Evidence | Classification | Truth Label | Next Action |
|---|---:|---|---|---|
| `new_exposure_expiry_gate_blocked` | `243` top reject count | Expected protective behavior | VERIFIED | No code change from this evidence alone |
| `stage_disallow_taker` | `4540` edge block rows | Expected stage-policy behavior | VERIFIED | No code change from this evidence alone |
| `maker_timing_gate_closed` | `217` maker block rows | Expected timing-gate behavior | VERIFIED | No code change from this evidence alone |
| `token_lag_not_verified_for_maker` | `283` maker block rows | Expected protective behavior unless over-applied | INFERRED | Revisit only if tied to a stronger suppression proof |
| `fair_probability_missing` | Clean run: `233` taker block rows. Post-patch run: `0` taker rows, `13` maker rows | Taker map-scope bug runtime-closed; maker rows remain maker-gated protective behavior from this evidence | VERIFIED_CLOSED_TAKER_SCOPE | No further taker patch from this evidence; keep maker scope on watch only if tied to suppression proof |
| `market_probability_missing` | `2` taker block rows | Low-count telemetry candidate | VERIFIED_OPEN | Keep on watch; no immediate patch |
| `taker_requires_ws_book_source` | `4` taker block rows | Fail-closed source guard | INFERRED | Keep on watch unless recurrence grows |
| `required_book_feed_disconnected_rows` | `1` row in each inspected run; first status row only; `ws_slo_bootstrap_active=1`; no order attempts/actions; connected by next status row | Startup/bootstrap telemetry | VERIFIED_BOOTSTRAP_TRANSIENT | No code change; keep websocket audits authoritative |
| `size_notional_bounds` | Clean run current-code replay: `30` maker rejects, `30` min-notional/max-shares conflicts. Post-patch replay: `4` maker rejects, `4` conflicts | Fail-closed maker floor/cap feasibility constraint | VERIFIED_CLASSIFIED_REPORT_PATCHED | No behavior/config change in this packet; future policy decision if maker should quote low-priced markets |
| `sizing_reject` | Clean run report: `15` maker no-submission category rows. Post-patch report: `2` rows | Same floor/cap feasibility constraint, surfaced through maker no-submission aggregation | VERIFIED_CLASSIFIED_REPORT_PATCHED | Preserve as protective unless strategy owners approve floor/cap adaptation |
| `submit_rejected_reduce_only_recovery_size_cap_unavailable` | Current-code report replay: clean anchor `16/16`, post-patch `2/2`, smoke `1/1` flat/wrong-side local rejects; non-flat/unknown `0` | Flat/wrong-side no-op local rejection | VERIFIED_CLOSED_REPORT_VISIBILITY | No trading behavior change; keep watching for future non-flat/unknown cases |
| `stage_reduction_delta_accounting.MAKER_TAKER_SELECTIVE.primary_reduction_cause_total_matches_delta=false` | `decision_to_submit_delta=49`, `primary_reduction_cause_total=92` | Report-accounting overlap now explicit in current code | VERIFIED_CLOSED_REPORT_ONLY | No runtime patch from this signal alone |
| `soak_maker_submits_too_low` | Archived post-patch report: `16.000000<min:29.000000`; current-code replay: `16>=16`; smoke run: `1>=1` | Maker-submit enforcement taxonomy gap, closed by treating maker-scope `fair_probability_missing` as non-actionable | VERIFIED_CLOSED_REPORT_POLICY | No trading behavior change; preserve canonical duration/status-row gate |

## Highest-Value Diagnostic Order
1. Low-price maker floor/cap policy: revisit only under explicit strategy/config scope.
2. Literal 5-minute validation lane: add a separate smoke budget only if explicitly needed; do not weaken canonical 10-minute soak criteria.

## Current Packet Decision
The stage-reduction mismatch received a report-only code clarification in `scripts/nightly_soak_report.py`.

`fair_probability_missing` was diagnosed as a map-scope bug: taker evaluation was consuming a fair-probability map filtered by maker-only per-token lag/score gates. Current code now builds maker-scoped and taker-scoped fair maps separately. Maker semantics retain the existing maker gates; taker fair probability still fails closed on missing/stale Chainlink or missing target metadata.

No wallet semantics, risk semantics, live-readiness gates, or strategy thresholds were changed.

Targeted tests passed, and post-patch run `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` provides runtime closure for the taker-scope residual. This does not close sizing, recovery, maker-participation, or live-readiness limitations.

`size_notional_bounds` / `sizing_reject` was diagnosed next. Current-code replay of the clean anchor run showed `30` maker sizing rejects and `30` min-notional/max-shares conflicts. Current-code replay of the post-patch run showed `4` maker sizing rejects and `4` min-notional/max-shares conflicts. The causal constraint is deterministic: maker hard floor `100.0` USDC cannot be satisfied with maker hard max `800.0` shares when midpoint is below `0.125`.

Current packet decision: report visibility was improved; trading behavior and config thresholds were not changed.

`soak_maker_submits_too_low` was diagnosed next. The defect was not maker execution. The hardening gate was counting maker rows blocked by maker-scope `fair_probability_missing` as actionable opportunities, even though missing fair probability is a fail-closed no-submit condition for the maker path. Packet 1 updated `ops/soak_budget.yaml` so `fair_probability_missing` is non-actionable for maker-submit enforcement.

Current-code replay proof:
- Clean anchor run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`: `ok=true`, `finding_count=0`, maker submits `6`, required submits `5`.
- Post-patch runtime proof `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`: `ok=true`, `finding_count=0`, maker submits `16`, required submits `16`.
- Requested 5-minute smoke run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`: maker submits `1`, required submits `1`; canonical validation still returned `policy_failed` because `5.4464` minutes and `11` status rows do not meet the canonical `10` minute / `20` status-row budget.

Packet 1 changed validation/report policy only. No wallet semantics, risk semantics, live-readiness gates, trading behavior, or strategy thresholds were changed.

`reduce_only_recovery_size_cap_unavailable` was diagnosed next. The code already clamps recovery size caps to live position and locally rejects flat/wrong-side recovery attempts before they can reach risk as false reduce-only orders. Current artifact replay now confirms the observed rows are that expected case:
- Clean anchor run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`: `local_size_cap_unavailable=16`, `flat_or_wrong_side=16`, `nonflat_or_unknown=0`.
- Post-patch runtime proof `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`: `local_size_cap_unavailable=2`, `flat_or_wrong_side=2`, `nonflat_or_unknown=0`.
- Smoke run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`: `local_size_cap_unavailable=1`, `flat_or_wrong_side=1`, `nonflat_or_unknown=0`.

Current packet decision: `scripts/nightly_soak_report.py` now emits a `reduce_only_recovery` report section so future artifacts classify this condition directly. Trading behavior, wallet semantics, risk semantics, and live-readiness gates were not changed.

`required_book_feed_disconnected_rows=1` was rechecked across the same three artifacts. In each run, the disconnected row was the first status row only, with `ws_slo_bootstrap_active=1`, zero order attempts/actions, and a connected book feed by the next status row. Websocket audits passed with `ok=true` and `finding_count=0`.

Current packet decision: classify as VERIFIED bootstrap telemetry, not a recurring market-data defect. No code change.
