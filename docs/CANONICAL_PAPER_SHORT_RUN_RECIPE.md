# CANONICAL_PAPER_SHORT_RUN_RECIPE

## Goal
Canonical paper discipline recipe for short controlled runs.

## Surface Class
- Workflow/backroom recipe doc, not a separate runtime mode.
- Public canonical paper start remains:
  `broctl paper -- --active-minutes <minutes> --wait-sec 25`
- Raw validation commands below remain replay/forensics/control surfaces.

## Realism Doctrine Anchor
- Canonical realism doctrine for this harness is defined in:
  - `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`
- Canonical paper harness is the emulation/proving lane for paper evidence.
- Auxiliary shop tooling is outside canonical paper proof, promotion claims,
  and front-of-house harness truth.
- Required claim classes:
  - `authoritative`
  - `not_modeled`

## Scientific Proving Discipline
- one canonical proving path per evidence claim
- one controlled variable at a time unless the packet explicitly says otherwise
- compare runs only when the proving lane is the same
- required lineage tuple for evidence claims:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`

## Commands
```bash
broctl paper -- --active-minutes 10 --wait-sec 25
# Use the run_id printed at completion to point MANIFEST_PATH at the matching run manifest.
MANIFEST_PATH="./logs_exec/paper_universal/run_manifest_<run_id>.json"
RUN_ID="$(python - "${MANIFEST_PATH}" <<'PY'
import json, sys
manifest_path = str(sys.argv[1]).strip()
if not manifest_path:
    raise SystemExit("MANIFEST_PATH is required")
payload = json.load(open(manifest_path, "r", encoding="utf-8"))
run_id = str(payload.get("run_id") or "").strip()
if not run_id:
    raise SystemExit("run_id missing in manifest")
print(run_id)
PY
)"
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
- Canonical validation enforces market-data liveliness and action-lane source purity:
  - minimum websocket book-update continuity
  - minimum total book-update continuity
  - action-row `edge_evaluation` records with `action_taken in {maker,taker}`
    must stay `book_source=ws`
  - whole-stream REST ratio remains a watch/descriptive metric, not the hard
    source-purity blocker
- Canonical soak gating enforces fill realism envelopes:
  - `max_maker_fill_rate`
  - `max_taker_bonus_fill_rate`

Policy source for these thresholds:
- `ops/soak_budget.yaml`

## Decision-Input Truth Disclosure
- Decision-path evidence (`edge_evaluation`) must declare decision-input provenance:
  - `decision_input_source`
  - `decision_input_type` (`observed_live|observed_other|replayed|emulated|unknown`)
  - `decision_input_emulated` (bool)
  - `decision_input_data_class`
  - `execution_realism_class` (`not_modeled`)
- Semantic distinction:
  - `decision_input_source`, `decision_input_type`, `decision_input_emulated`,
    and `decision_input_data_class` are live contract terms
  - `execution_realism_class` is an audit/harness classification term, not a
    replacement for the emitted decision-input fields
  - harness claim classes may still use `authoritative`, but current emitted
    `execution_realism_class` value is `not_modeled`
- Paper harness realism is fail-closed for hidden emulation:
  - if emulated decision input is used, it must be explicitly disclosed
  - undisclosed decision-input emulation is a harness-audit failure
  - action on emulated decision input is forbidden unless explicitly enabled for a controlled scenario;
    canonical paper profile keeps this disabled.
- Harness audit surfaces explicit counts:
  - decision count by input class (`observed_live|observed_other|replayed|emulated|unknown`)
  - action count by input class
  - emulated-action rows (hard-fail by default)
  - fill-policy basis counts
  - claim-boundary summary (`paper_claim_boundary`)
  - proving-lineage tuple (`run_id`, `git_commit`, `config_fingerprint_sha256`, `code_fingerprint_sha256`)
  - claim-boundary source truth layers:
    - `decision_source_truth` (all decision rows)
    - `action_source_truth` (action rows only)
  - `harness_realism_grade` is descriptive only, not authority and not a pass/fail substitute

## Maker/Taker Realism Policy Surface
- Harness audit emits machine-readable policy objects:
  - `maker_policy`
  - `taker_policy`
- Canonical interpretation:
  - maker expectancy is `not_modeled` (queue position is not explicitly modeled)
  - taker expectancy is `not_modeled`, but current taker action truth is still bounded explicitly by visible aggressive-side top-of-book liquidity plus the emitted lag-penalty disclosures
  - that means paper taker can prove fire law, sizing truth, and bounded visible-spend behavior, but it does **not** by itself prove full live slippage / queue realism

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
- In paper mode, `verification_level=paper_sim_verified` and
  `verification_scope=paper_wallet_simulation_verified` refer to paper-mode
  wallet/reconcile semantics only.
- They do **not** mean non-canonical shop tooling proved anything about the run.
