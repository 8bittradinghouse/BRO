# PAPER_DISCIPLINE

## Goal
Backward-compatible alias for canonical paper mode.

## Realism Doctrine Anchor
- Canonical realism doctrine for this harness is defined in:
  - `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`
- Required claim classes:
  - `authoritative`
  - `bounded_approximation`
  - `not_modeled`

## Commands
```bash
SESSION_JSON="$(./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25)"
RUN_ID="$(printf '%s\n' "${SESSION_JSON}" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')"
RUN_CONTRACT="./logs_exec/paper_universal/run_contract_${RUN_ID}.json"
./scripts/canonical_paper_validation.sh "${RUN_ID}" --session-phase validate_postrun --run-contract "${RUN_CONTRACT}"
python scripts/readiness_gate.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --policy ops/ramp_policy.yaml --out ./exports/paper_universal_readiness.json
python scripts/performance_budget_gate.py --log-dir ./logs_exec/paper_universal --run-id "${RUN_ID}" --out ./exports/paper_universal_perf.json
```

## Verify Active Profile
- `run_manifest_*.json` `runtime_identity.profile_name = paper_universal`.
- Setup lock must be enabled and internally consistent:
```bash
python - <<'PY'
from pathlib import Path
from prodesk.config import load_execution_config
cfg = load_execution_config(Path("configs/profiles/paper_universal.yaml"))
rt = cfg["runtime"]
print("lock_enabled", rt["paper_enforce_setup_lock"])
print("profile", cfg["profile"]["name"], "expected_profile", rt["paper_expected_profile_name"])
print("observed_fp", cfg["_meta"]["effective_config_sha256"])
print("expected_fp", rt["paper_expected_config_fingerprint_sha256"])
PY
```

## Reality-Emulation Guardrails
- Paper maker orders enforce post-only semantics: crossed post-only GTC orders are rejected (`post_only_reject`) instead of silently resting.
- Paper fills require explicit touch size. Missing `best_bid_size` / `best_ask_size` is treated as zero available liquidity (fail-closed), not optimistic infinite depth.
- Canonical soak gating enforces market-data source realism:
  - minimum websocket book-update continuity
  - bounded REST fallback ratio
- Canonical soak gating enforces fill realism envelopes:
  - `max_maker_fill_rate`
  - `max_taker_bonus_fill_rate`

Policy source for these thresholds:
- `ops/soak_budget.yaml`

## Decision-Input Truth Disclosure
- Decision-path evidence (`edge_evaluation`) must declare decision-input provenance:
  - `decision_input_source`
  - `decision_input_type` (`observed_live|replayed|emulated|bounded_derived|unknown`)
  - `decision_input_emulated` (bool)
  - `decision_input_data_class`
  - `execution_realism_class` (`authoritative|bounded_approximation|not_modeled`)
- Paper harness realism is fail-closed for hidden emulation:
  - if emulated decision input is used, it must be explicitly disclosed
  - undisclosed decision-input emulation is a harness-audit failure
  - action on emulated decision input is forbidden unless explicitly enabled for a controlled scenario;
    canonical paper profile keeps this disabled.
- Harness audit surfaces explicit counts:
  - decision count by input class (`observed_live|replayed|emulated|bounded_derived|unknown`)
  - action count by input class
  - emulated-action rows (hard-fail by default)
  - fill-policy basis counts
  - claim-boundary summary (`paper_claim_boundary`)
  - claim-boundary source truth layers:
    - `decision_source_truth` (all decision rows)
    - `action_source_truth` (action rows only)
    - `source_truth` is kept as a legacy alias of `action_source_truth` for backward-safe parsing

## Maker/Taker Realism Policy Surface
- Harness audit emits machine-readable policy objects:
  - `maker_policy`
  - `taker_policy`
- Canonical interpretation:
  - maker expectancy is `not_modeled` (queue position is not explicitly modeled)
  - taker expectancy is `bounded_approximation` (best-touch price + visible top-size bounds)

## Timestamp Domain Semantics
- Event-time fields are domain-labeled:
  - `ts_event_utc` (event emission time)
  - `ts_receive_utc` (ingress receive time, when available)
  - `ts_source_utc` (upstream/source timestamp, when available)
  - `ts_decision_utc` (decision timestamp for action/decision surfaces)
- Legacy `ts_utc` remains for compatibility, but domain aliases are canonical for audit clarity.
- Status rows surface `time_policy` with required keys:
  - `source_of_truth`
  - `fallback_logic`
  - `skew_tolerance_ms`
  - `monotonicity_rule`

## Websocket Ordering Semantics
- Chainlink ingest exposes an explicit ordering policy object:
  - `primary=source_timestamp`
  - `fallback=receive_monotonic`
  - `tolerance_ms`
  - `tie_breaker`
- Ingest classification is explicit and auditable:
  - `ordered`
  - `out_of_order`
  - `duplicate`
  - `revision`
  - `missing_source_time`

## Reconcile Partial Truth
If reconcile output has `verification_level != venue_verified`, treat it as partial evidence and do not claim venue-verified parity.
