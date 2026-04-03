# BRO Paper Harness Realism Revision Proof

- run_id: `4dee0545-3a42-4cde-9b2d-30e16e0764fd`
- session_id: `9c1ee9b1-8e19-43ef-93b7-bd0b0a426759`
- report_dir: `logs_exec/paper_universal/reports/4dee0545-3a42-4cde-9b2d-30e16e0764fd`
- local archive: `exports/paper_session_4dee0545-3a42-4cde-9b2d-30e16e0764fd.zip`

## Canonical Validation
- overall_exit_code: `0`
- validator_determinism_ok: `True`
- gate status: `PASS`

## Key Runtime Metrics
- maker_submits: `31.0`
- maker_fills: `4.0`
- maker_fill_rate: `0.12903225806451613`
- taker_bonus_submits: `59.0`
- taker_bonus_fills: `59.0`
- taker_bonus_fill_rate: `1.0`
- capture_minus_adverse: `-220.22994999999983`

## Harness Realism Surfaces
- harness_realism_grade: `100`
- harness_realism_grade_breakdown: `{"maker_queue_proxy_depth_model": 20, "taker_depth_slippage_model": 20, "taker_lag_emulation_with_unknown_guard": 20, "tod_liquidity_scaling": 20, "truth_surface_completeness": 20}`
- maker_realism_class: `bounded_approximation`
- taker_latency_model: `bounded_lag_emulation`
- lag_unknown_handling: `fail_closed_no_penalty`

## Included Artifacts
- `validation_summary.json`
- `paper_harness_audit.json`
- `nightly_soak_report.json`
- `readiness_gate.json`
- `soak_hardening_gate.json`
- `run_contract_4dee0545-3a42-4cde-9b2d-30e16e0764fd.json`
- `proof_summary.json` (sha256 `be440eeb215b8436ddd6ab440d993e169041af9e9d1f92253f4f2373ab250c2b`)
