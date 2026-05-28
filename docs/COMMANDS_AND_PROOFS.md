# Commands And Proofs

> Doc Class: `Reference`
> Authority: supporting packet proof ledger only; front-of-house repo current
> truth is maintained in `docs/PROJECT_TRUTH_STATE.md`, and runtime policy is
> maintained in `docs/DOCTRINE_RUNBOOK.md`.
> Current public canonical paper start path is
> `broctl paper -- --active-minutes <minutes> --wait-sec 25`.
> Historical packet proof commands below may invoke backend engine surfaces
> directly; they are not the public happy-path start instructions.

## Historical Baseline Re-Lock Commands
- `git status --short --branch`
- `git rev-parse HEAD`
- `git log -1 --oneline --decorate`
- `sed -n '1,240p' docs/CURRENT_BASELINE.md`
- `find logs_exec/paper_universal -maxdepth 3 -type f ...`
- `./.venv/bin/python scripts/doctrine_truth_audit.py`

## Historical Baseline Re-Lock Results
- Branch: `consultant/full-snapshot-public-20260402T055838Z`
- Ahead of origin: `48`
- Initial working tree state before this packet: clean
- HEAD: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Previous `docs/CURRENT_BASELINE.md` declared commit: `740f61e5d19e1cded7f57668d4e04e7ae4e0ddc9`
- Doctrine truth audit before doc update: `ok=true`

## Latest Run Evidence Commands
- JSON inspection of:
  - `logs_exec/paper_universal/run_manifest_9d3c3225-13b6-4a12-8dd4-fb51a6d666e6.json`
  - `logs_exec/paper_universal/run_contract_9d3c3225-13b6-4a12-8dd4-fb51a6d666e6.json`
  - `logs_exec/paper_universal/reports/9d3c3225-13b6-4a12-8dd4-fb51a6d666e6/validation_summary.json`
  - `logs_exec/paper_universal/reports/9d3c3225-13b6-4a12-8dd4-fb51a6d666e6/readiness_gate.json`
  - `logs_exec/paper_universal/reports/9d3c3225-13b6-4a12-8dd4-fb51a6d666e6/soak_hardening_gate.json`
  - `logs_exec/paper_universal/reports/9d3c3225-13b6-4a12-8dd4-fb51a6d666e6/nightly_soak_report.json`

## Latest Run Evidence Results
- Run ID: `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- Manifest and contract agree on run start/stop.
- Artifact identity complete: `git_commit`, `config_fingerprint_sha256`, and `code_fingerprint_sha256` present.
- `validation_summary.ok=true`
- All validator exit codes `0`
- `canonical_paper_validation.status=pass`
- `readiness_gate.highest_passing_stage=paper`
- `readiness_gate.blocking_stage=pilot_live`
- `soak_hardening_gate.ok=true`

## Verification Commands Run In This Packet
- `./.venv/bin/python scripts/doctrine_truth_audit.py`
- `./.venv/bin/python -m pytest -q tests/test_nightly_soak_report.py tests/test_doctrine_truth_audit.py`
- `./.venv/bin/python -m pytest -q tests/test_soak_hardening_gate.py tests/test_readiness_gate.py tests/test_paper_harness_audit.py tests/test_validator_replay_fingerprint.py tests/test_canonical_paper_session.py`
- Current-code `build_report(...)` probe against run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
- `PYTHONPATH=. pytest -q tests/test_execution_stack.py -k "fair_probability_map or fair_probability_math or chainlink_fair_probability"`
- `PYTHONPATH=. pytest -q tests/test_execution_stack.py tests/test_doctrine_gating.py tests/test_edge_truth_contract.py`
- `PYTHONPATH=. python3 -m py_compile executor.py`
- `PYTHONPATH=. python3 scripts/doctrine_truth_audit.py`

## Verification Results
- Doctrine truth audit: `ok=true`
- Targeted tests: `36 passed in 0.32s`
- Downstream report-consumer tests: `60 passed in 1.90s`
- Current-code stage accounting probe:
  - `decision_to_submit_delta=49.0`
  - `primary_reduction_cause_total=92.0`
  - `primary_reduction_cause_total_delta_difference=43.0`
  - `primary_reduction_cause_total_exceeds_delta=true`
  - `primary_reduction_cause_overlap_possible=true`
- Fair-probability targeted slice: `4 passed, 175 deselected`
- Adjacent execution/doctrine/edge contract tests: `208 passed`
- `executor.py` py-compile: passed
- Doctrine truth audit after fair-map patch: `ok=true`

## Historical Backend Runtime Proof Command
- `./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25 --archive-export`

## Historical Backend Runtime Proof Results
- Run ID: `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Session ID: `5f8bc852-0df2-4a3f-94f8-ab01e1b2084e`
- Run contract: `logs_exec/paper_universal/run_contract_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.json`
- Run manifest: `logs_exec/paper_universal/run_manifest_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.json`
- Report directory: `logs_exec/paper_universal/reports/7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Export archive: `exports/paper_session_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.zip`
- Canonical paper validation: `status=pass`, `script_exit_code=0`
- Runtime classification: `VALID_ACTIVE`
- Validation summary: `ok=true`, `overall_exit_code=0`, all validator exit codes `0`
- Reports complete: `true`; missing reports `[]`; parse error reports `[]`
- Readiness gate: highest passing stage `paper`; blocking stage `pilot_live`; recommended next stage `pilot_live`
- Soak hardening gate: `ok=true`, `finding_count=1`, finding `soak_maker_submits_too_low:16.000000<min:29.000000`
- Edge truth proof: taker `fair_probability_missing=0`; maker `fair_probability_missing=13`
- Execution activity: maker submits `16`, maker fills `2`; taker submits `55`, taker fills `55`
- Errors slice rows: `0`
- Last status `gauge.total_pnl`: `-17.265999999999963`

## Packet 1 Maker-Submit Taxonomy Commands
- `PYTHONPATH=. pytest -q tests/test_soak_hardening_gate.py`
- `PYTHONPATH=. python3 scripts/doctrine_truth_audit.py`
- `./.venv/bin/python scripts/soak_hardening_gate.py --log-dir logs_exec/paper_universal --run-id 7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69 --budget ops/soak_budget.yaml --run-contract logs_exec/paper_universal/run_contract_7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69.json --out /tmp/soak_7e0a_after_packet1.json`
- `./.venv/bin/python scripts/soak_hardening_gate.py --log-dir logs_exec/paper_universal --run-id 9d3c3225-13b6-4a12-8dd4-fb51a6d666e6 --budget ops/soak_budget.yaml --run-contract logs_exec/paper_universal/run_contract_9d3c3225-13b6-4a12-8dd4-fb51a6d666e6.json --out /tmp/soak_9d3c_after_packet1.json`

## Packet 1 Maker-Submit Taxonomy Results
- Scope: `ops/soak_budget.yaml` report/validation policy only.
- Change: `fair_probability_missing` added to `soak.maker_submit_enforcement.non_actionable_block_reasons`.
- Trading behavior changed: no.
- Wallet/risk/live semantics changed: no.
- Soak hardening tests: `14 passed in 0.42s`
- Doctrine truth audit: `ok=true`
- Current-code replay for `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`:
  - `ok=true`
  - `finding_count=0`
  - `maker_actionable_opportunity_rows=16.0`
  - `required_submits=16.0`
  - `maker_submits=16.0`
- Current-code replay for `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`:
  - `ok=true`
  - `finding_count=0`
  - `maker_actionable_opportunity_rows=5.0`
  - `required_submits=5.0`
  - `maker_submits=6.0`

## Historical Packet 1 Five-Minute Smoke Runtime Command
- `./scripts/canonical_paper_session.sh --active-minutes 5 --wait-sec 25 --archive-export`

## Historical Packet 1 Five-Minute Smoke Runtime Results
- Run ID: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Session ID: `059215d1-deaf-40f4-88a8-45e076d8fef2`
- Run contract: `logs_exec/paper_universal/run_contract_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.json`
- Run manifest: `logs_exec/paper_universal/run_manifest_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.json`
- Report directory: `logs_exec/paper_universal/reports/ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Export archive: `exports/paper_session_ec26dedd-84ee-4cc9-9f5f-d448ea834f9d.zip`
- Runtime classification: `VALID_ACTIVE`
- Canonical paper validation: `status=policy_failed`, `script_exit_code=2`
- Policy-fail cause: requested 5-minute run was shorter than canonical soak budget (`5.4464<10.0` minutes; `11<20` status rows).
- Validator exit codes: all `0` except `soak_hardening_gate=2` and `soak_hardening_gate_replay=2`.
- Soak hardening findings:
  - `performance_status_rows_too_few:11<min:20`
  - `soak_duration_too_short:5.446400<min:10.000000`
  - `soak_readiness_below_required_stage:required=paper:highest=none:causes=min_status_rows`
  - `status_rows_below_min:11<min:20`
  - `websocket_slo_status_rows_too_few:11<min:20`
- Packet-1 maker-submit proof in runtime:
  - `maker_actionable_opportunity_rows=1.0`
  - `required_submits=1.0`
  - `maker_submits=1.0`
  - `maker_non_actionable_block_rows=155.0`
- Other artifact checks:
  - `paper_harness_audit.json`: `ok=true`, `finding_count=0`
  - `edge_truth_audit.json`: `ok=true`, `finding_count=0`
  - `order_lifecycle_audit.json`: `ok=true`, `finding_count=0`
  - `outcome_truth_audit.json`: `ok=true`, `finding_count=0`
  - `guardian_profile_audit.json`: `ok=true`, `finding_count=0`
  - `time_discipline_audit.json`: `ok=true`, `finding_count=0`
  - `websocket_hardening_audit.json`: `ok=true`, `finding_count=0`
- Classification: healthy 5-minute runtime behavior with expected canonical-budget policy fail. Do not weaken canonical budget to make a 5-minute run all-green.

## Sizing Feasibility Diagnostic Commands
- Current-code `build_report(...)` replay against:
  - `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
  - `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
- Raw `risk_reject` event inspection for `reason=size_notional_bounds`
- `PYTHONPATH=. pytest -q tests/test_nightly_soak_report.py -k maker_sizing_competitiveness`
- `PYTHONPATH=. python3 -m py_compile scripts/nightly_soak_report.py`
- `PYTHONPATH=. pytest -q tests/test_nightly_soak_report.py`
- `PYTHONPATH=. pytest -q tests/test_soak_hardening_gate.py tests/test_readiness_gate.py tests/test_paper_harness_audit.py tests/test_validator_replay_fingerprint.py tests/test_canonical_paper_session.py tests/test_nightly_soak_report.py`
- `PYTHONPATH=. python3 scripts/doctrine_truth_audit.py`

## Sizing Feasibility Diagnostic Results
- Clean anchor run replay:
  - `maker_sizing_reject_rows=30`
  - `maker_min_notional_max_shares_conflict_rows=30`
  - reject price range: `0.015` to `0.035`
  - max-share notional max: `28.000000000000004`
- Post-patch run replay:
  - `maker_sizing_reject_rows=4`
  - `maker_min_notional_max_shares_conflict_rows=4`
  - reject price range: `0.11499999999999999` to `0.12`
  - max-share notional max: `96.0`
- Causal constraint:
  - maker hard floor `100.0` USDC
  - maker hard max shares `800.0`
  - minimum feasible midpoint `0.125`
- Classification: fail-closed maker floor/cap feasibility constraint, with report visibility patched.
- Focused maker sizing report test: `1 passed, 32 deselected`
- `scripts/nightly_soak_report.py` py-compile: passed
- Full nightly soak report tests: `33 passed in 0.22s`
- Adjacent report/gate consumer tests: `93 passed in 2.00s`
- Doctrine truth audit after sizing report patch: `ok=true`

## Reduce-Only / Bootstrap Diagnostic Commands
- Raw event aggregation for `reduce_only_recovery_size_cap_unavailable` across:
  - `logs_exec/paper_universal/sessions/6e4157f6-59d3-427b-b281-1c4ee38f1aae/slices/events_slice.jsonl`
  - `logs_exec/paper_universal/sessions/5f8bc852-0df2-4a3f-94f8-ab01e1b2084e/slices/events_slice.jsonl`
  - `logs_exec/paper_universal/sessions/059215d1-deaf-40f4-88a8-45e076d8fef2/slices/events_slice.jsonl`
- Raw status-row aggregation for required-book-feed disconnected rows across the same three run slices.
- Current-code `nightly_soak_report.py` replays:
  - `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
  - `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
  - `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- `PYTHONPATH=. pytest -q tests/test_nightly_soak_report.py -k "reduce_only_recovery or maker_sizing_competitiveness"`
- `PYTHONPATH=. python3 -m py_compile scripts/nightly_soak_report.py`
- `PYTHONPATH=. pytest -q tests/test_nightly_soak_report.py`
- `PYTHONPATH=. pytest -q tests/test_execution_stack.py -k "reduce_only_size_cap or terminal_unwind_halt_new_risk or reduce_only_recovery"`
- `PYTHONPATH=. pytest -q tests/test_preflight_and_risk.py -k "terminal_unwind_halt_new_risk or reduce_only_recovery"`
- `PYTHONPATH=. python3 scripts/doctrine_truth_audit.py`

## Reduce-Only / Bootstrap Diagnostic Results
- Clean anchor run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`:
  - `local_size_cap_unavailable=16`
  - `flat_or_wrong_side=16`
  - `nonflat_or_unknown=0`
  - `classification=flat_or_wrong_side_noop_only`
- Post-patch runtime proof `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`:
  - `local_size_cap_unavailable=2`
  - `flat_or_wrong_side=2`
  - `nonflat_or_unknown=0`
  - `classification=flat_or_wrong_side_noop_only`
- Smoke run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`:
  - `local_size_cap_unavailable=1`
  - `flat_or_wrong_side=1`
  - `nonflat_or_unknown=0`
  - `classification=flat_or_wrong_side_noop_only`
- Required-book-feed disconnected classification across all three runs:
  - first status row only
  - `ws_slo_bootstrap_active=1`
  - `order_submission_attempts_last_cycle=0`
  - `actions_last_cycle=0`
  - `taker_actions_last_cycle=0`
  - connected by the next status row
  - websocket hardening audits `ok=true`, `finding_count=0`
- Focused report tests: `2 passed, 32 deselected`
- `scripts/nightly_soak_report.py` py-compile: passed
- Full nightly report tests: `34 passed in 0.26s`
- Reduce-only execution stack slice: `6 passed, 173 deselected`
- Reduce-only risk/preflight slice: `1 passed, 31 deselected`
- Doctrine truth audit: `ok=true`
- Classification: current artifacts show flat/wrong-side no-op local rejection and bootstrap market-data telemetry. No trading behavior, wallet semantics, risk semantics, live-readiness gates, or strategy thresholds were changed.

## Pending Verification Commands
- `git status --short --branch`
- Doctrine truth audit after reduce-only/bootstrap doc sync.
- Continuity packet checksum regeneration after reduce-only/bootstrap doc sync.
- Fresh canonical runtime proof decision.
- Full integration validation remains pending; targeted tests are not full closure.
