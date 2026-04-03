# BRO Maker Competitiveness Packet Proof

## Scope
- Packet: Maker Sizing & Competitiveness Gate (timing gate + edge scaling + optional one-sided mode + observability)
- Candidate run: `23e1187c-4aee-4a58-bc83-0719f55624b5`
- Baseline run: `4dee0545-3a42-4cde-9b2d-30e16e0764fd`
- Profile: `paper_universal`
- Session phase: `validate_postrun`

## Validation
- Canonical validation executed for candidate run:
  - Command: `./scripts/canonical_paper_validation.sh 23e1187c-4aee-4a58-bc83-0719f55624b5 --run-contract logs_exec/paper_universal/run_contract_23e1187c-4aee-4a58-bc83-0719f55624b5.json`
  - Result: validators completed, outputs written to:
    - `logs_exec/paper_universal/reports/23e1187c-4aee-4a58-bc83-0719f55624b5/validation_summary.json`

## Before / After Delta

### Execution path metrics
- Baseline (`4dee...`):
  - maker_submits: `31`
  - maker_fills: `4`
  - maker_fill_rate (event-based): `0.1290`
  - taker_submits: `59`
  - taker_fills: `59`
- Candidate (`23e...`):
  - maker_submits: `4`
  - maker_fills: `7`
  - maker_fill_rate (event-based): `1.75`
  - taker_submits: `68`
  - taker_fills: `68`

Note: `maker_fill_rate` here is fill-events / submits; partial fills can drive values above 1.0.

### Maker competitiveness surfaces (candidate)
- timing_gate_blocked_count_decision: `326`
- one_sided_activation_submit_buy_count: `1`
- one_sided_activation_submit_sell_count: `3`
- aggressiveness application counts:
  - size_scaled: `4`
  - spread_tightened: `4`
  - requote_tightened: `4`
- maker submit edge-bucket distribution:
  - `gt_0p20`: `4`
- maker fill edge-bucket distribution:
  - `gt_0p20`: `7`

### Maker no-submission taxonomy
- Baseline causes:
  - submit_rejected_sizing_reject: `307`
  - submit_rejected_risk_reject: `212`
  - submit_rejected_quote_quality_skip_fill_probability: `27`
  - submit_rejected_quote_quality_skip_queue_depth: `27`
  - replace_guard_min_rest: `11`
  - no_desired_quote: `40`
- Candidate causes:
  - submit_rejected_sizing_reject: `71`
  - submit_rejected_risk_reject: `35`
  - replace_guard_min_rest: `32`

### Quality / PnL realism
- Baseline execution quality:
  - capture: `0.000000`
  - adverse: `220.229950`
  - net: `-220.229950`
- Candidate execution quality:
  - capture: `462.648785`
  - adverse: `45.584640`
  - net: `+417.064145`

### Taker stage net (UNKNOWN bucket)
- Baseline:
  - net: `-157.472320`
  - capture: `0.000000`
  - adverse: `157.472320`
- Candidate:
  - net: `+359.245950`
  - capture: `381.953650`
  - adverse: `22.707700`

## Interpretation (bounded, no over-claim)
- The packet surfaces are active and auditable in candidate run (`timing gate`, `one-sided mode`, `edge-strength scaling`).
- Candidate run shifted to fewer but much higher-quality maker submissions (all in `gt_0p20` edge bucket), with maker fills occurring via bounded queue-depth policy.
- Quote-quality skip rejects dropped to zero in candidate run; dominant remaining maker no-submission causes are now sizing/risk and min-rest replace guard.
- Taker lane remained active (`68/68`), and run-level execution quality materially improved.
- This proof demonstrates functional activation and measurable behavior shift; it does not claim final maker optimization closure.

## Artifact paths
- Candidate run contract:
  - `logs_exec/paper_universal/run_contract_23e1187c-4aee-4a58-bc83-0719f55624b5.json`
- Candidate report dir:
  - `logs_exec/paper_universal/reports/23e1187c-4aee-4a58-bc83-0719f55624b5/`
- Candidate soak outputs generated in this pass:
  - `/tmp/soak_23e1187c.json`
  - `/tmp/soak_23e1187c.txt`
- Baseline soak outputs used for delta:
  - `/tmp/soak_4dee0545.json`
  - `/tmp/soak_4dee0545.txt`
