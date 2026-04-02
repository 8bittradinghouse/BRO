# BRO Handoff — Maker Packet Continuation (Post-Cutoff Reorientation)

## 0) Session Freeze Facts (authoritative)
- Freeze timestamp (UTC): `2026-04-01T07:18:00Z`
- Repo root: `/home/odah/bro/base`
- Branch: `run-freeze-20260309-paper`
- HEAD: `410d4aa4e08ec1a7460064e2dbf905b6bc64484b`
- Container runtime state: **no docker containers running** (`docker ps` empty)
- Collaboration mode at cutoff: diagnostic-heavy maker packet, no new runtime strategy changes requested
- Active doctrine constraints still in force:
  - one harness / one environment
  - deterministic inputs
  - fail-closed semantics
  - no semantic lying
  - additive-first
  - single operational pathway per task

## 1) What happened in the cutoff session
This session focused on **maker-lane forensic diagnosis**, not broad implementation.

### 1.1 Diagnostic profile prepared
- File added: `configs/profiles/paper_maker_isolation.yaml`
- Intent: disable taker lane to isolate maker viability without changing core strategy math.
- Profile content:
  - `sniper.taker.enabled: false`
  - `auth.allow_taker: false`
  - `runtime.paper_enforce_setup_lock: false` (diagnostic profile unlock)

### 1.2 State-contamination prevention and reset
- First isolation attempt used run `d533aeff-6c3f-4607-b095-377c46f5bc8b` (context captured).
- Prior state had carried exposure; backup was taken:
  - `exports/state_backup_before_maker_iso_20260401T060638Z.json`
  - Contains non-zero positions (notably +311.11 and -61.55 net shares on two tokens).
- State was reset before clean isolation run (`state_reset: true` in context).

### 1.3 Clean maker-isolation diagnostic run (anchor for current diagnosis)
- Run id: `a84f8daa-9548-4dff-b605-be60ddf359be`
- Contract: `logs_exec/paper_universal/run_contract_a84f8daa-9548-4dff-b605-be60ddf359be.json`
- Manifest: `logs_exec/paper_universal/run_manifest_a84f8daa-9548-4dff-b605-be60ddf359be.json`
- Evidence window: ~20m46s (`1246.612s`)
- Forensic artifact generated:
  - `exports/BRO_maker_isolation_forensic_a84f8daa-9548-4dff-b605-be60ddf359be_20260401T062855Z.json`
- Delta vs mixed-lane anchor generated:
  - `exports/BRO_maker_lane_delta_1e020ca3_vs_a84f8daa_20260401T063427Z.json`
- Regression map generated:
  - `exports/BRO_maker_regression_map_20260401T063632Z.md`

## 2) Current verified findings (code/evidence grounded)

### 2.1 Maker remained transport-inactive even in taker-isolation
From run `a84f8daa...`:
- `submit_counts.total=0`
- `submit_counts.maker=0`
- `submit_counts.taker=0`
- `fill_count=0`

This is not a taker-headroom-only explanation.

### 2.2 Maker was actively evaluated but blocked upstream of transport
From same forensic artifact:
- `edge_evaluation_scope_counts.maker=784`
- `edge_evaluation_scope_counts.taker=5390` (taker evaluations still logged even though execution disabled)
- `diagnostic_judgment.maker_lane_transport_activity_present=false`
- `diagnostic_judgment.maker_lane_local_reject_activity_present=true`

### 2.3 Dominant maker blocker mix in isolation run
- `market_probability_missing=300`
- `maker_no_submission=278`
- `token_lag_not_verified_for_maker=202`
- `maker_requires_ws_book_source=4`

### 2.4 Local reject subcauses in isolation run
- `pre_submit_cross_guarded=278`
- `quote_quality_skip_queue_depth=144`
- `quote_quality_skip_fill_probability=134`

### 2.5 Maker no-submission decomposition (isolation)
From delta/regression artifacts:
- `submit_rejected_pre_submit_cross_guarded=141`
- `submit_rejected_quote_quality_skip_queue_depth=72`
- `submit_rejected_quote_quality_skip_fill_probability=65`

### 2.6 Soft-throttle/post-only transport failure no longer dominate maker-no-submission chain
Compared to prior pathological run (`05dc35b5...`):
- previous heavy causes included:
  - `submit_rejected_order_soft_throttle=269`
  - `submit_rejected_post_only_reject=158`
- current isolation chain does **not** show those as dominant transport-path causes.

### 2.7 Cross-guard and quote-quality constraints are materially active
From regression map:
- cross-guard deltas are large (`~0.313` to `~0.477`), not rounding noise
- quote-quality skips carry low viability signatures (`expected_fill_prob=0.0`, deep queue context)

## 3) Certainty separation

### Verified
- Maker transport inactivity persisted under taker-isolation.
- Maker blockers are now concentrated in local deterministic guards + prerequisites + reference availability.
- Prior accounting/soft-throttle poison pattern is no longer the visible dominant cause chain.

### Inferred (high-confidence, not yet closure-proof)
- Current maker failure is now primarily a **viability/reference path issue**, not a raw suppression-only issue.
- Hardening-era prerequisite/quality gates likely surfaced existing low-viability maker intents rather than fabricating them.

### Unknown (requires targeted proof)
- Whether `market_probability_missing` in maker stages is primarily due to one-sided book midpoint absence vs mapping/availability defects.
- Whether fair/strike/side mapping is directionally correct for all blocked cross-guard cohorts in this window.
- Whether token-lag gating is legitimately protective vs over-conservative for maker in this specific regime.
- Whether minimal doctrine-safe maker policy adjustment is needed after reference-path confirmation.

## 4) Relevant code surfaces already identified
- Maker prereq and block reason emission:
  - `executor.py:1276` (`token_lag_not_verified_for_maker`)
  - `executor.py:1340` (`maker_requires_ws_book_source`)
  - `executor.py:1537-1542` (maker no-submission cause/category attachment)
- Maker local reject and quality guard pathways:
  - `prodesk/order_manager.py:384-399` (`pre_submit_cross_guard`)
  - `prodesk/order_manager.py:438-460` (`quote_quality_skip_*`)
  - `prodesk/order_manager.py:990-1131` (maker no-submission reason recording)
- Submission accounting and reservation lifecycle (already hardened earlier):
  - `prodesk/order_manager.py:528-567,584`
  - `prodesk/risk.py:100-118,134-151`
- Report/audit surfaces:
  - `scripts/nightly_soak_report.py:663-704`

## 5) Evidence index (resume-ready)
- Prior handoff (pre-this-session):
  - `BRO_HANDOFF_20260331T105800Z_MAKER_TAKER_RUNTIME_FORENSIC.md`
- This session contexts/artifacts:
  - `exports/BRO_maker_isolation_context_20260401T060559Z.json`
  - `exports/BRO_maker_isolation_context_20260401T060658Z.json`
  - `exports/state_backup_before_maker_iso_20260401T060638Z.json`
  - `exports/BRO_maker_isolation_forensic_a84f8daa-9548-4dff-b605-be60ddf359be_20260401T062855Z.json`
  - `exports/BRO_maker_lane_delta_1e020ca3_vs_a84f8daa_20260401T063427Z.json`
  - `exports/BRO_maker_regression_map_20260401T063632Z.md`
- Historical comparison run reports:
  - `logs_exec/paper_universal/reports/05dc35b5-9056-448c-b3bc-1a16e75d433a/nightly_soak_report.json`
  - `logs_exec/paper_universal/reports/1e020ca3-d3bf-4f2b-a12d-4b9256fa429e/nightly_soak_report.json`
  - `logs_exec/paper_universal/reports/8276b008-856f-41a0-b05e-e0bd38957d12/nightly_soak_report.json`
  - `logs_exec/paper_universal/reports/5496dbf5-8ba9-46ea-89d2-1b06be1a77a6/nightly_soak_report.json`

## 6) Critical constraints for next resume
1. Do **not** treat maker-isolation run as closure proof for mixed-lane production behavior.
2. Do **not** weaken safety gates/thresholds just to force maker activity.
3. Keep unknown explicit; no inference from pnl/future-only info.
4. Any behavior change must remain surgical and be backed by before/after artifacts.
5. Preserve single operational pathway semantics per task.

## 7) Immediate resume plan (first packet after restart)

### Step A — Reorientation (no edits)
Run exactly:
```bash
cd /home/odah/bro/base
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --porcelain=v1 | sed -n '1,220p'
```
Confirm handoff artifacts exist:
```bash
ls -lt exports/BRO_maker_isolation_* exports/BRO_maker_lane_delta_* exports/BRO_maker_regression_map_* | sed -n '1,120p'
```

### Step B — Finish maker root-cause decomposition (still diagnosis-first)
- Decompose `market_probability_missing` by stage and midpoint availability.
- Validate cross-guard cohort mapping correctness (fair/strike/side).
- Split token-lag maker blocks by per-token lag quality and stage context.
- Produce one consolidated forensic note with:
  - verified / inferred / unknown split
  - single primary root-cause chain + downstream amplifiers

### Step C — Only then propose minimal fix packet
Expected priority order (subject to Step B proof):
1. Reference availability/midpoint handling clarification (if proven dominant)
2. Narrow maker viability policy/quote-side correction (only if proven)
3. Status/event semantic alignment surfaces (operator truth)

### Step D — Proof standard before claiming improvement
- targeted tests for touched code
- full regression
- at least 2 exercised mixed-lane canonical runs (different windows)
- before/after deltas for:
  - maker submits/fills/fill%
  - maker no-submission taxonomy
  - market_probability_missing counts
  - token_lag_not_verified_for_maker counts
  - status/event alignment counters

## 8) Token-constrained mode (if low budget again)
If session budget is tight, execute only:
1. reorientation checks (Step A)
2. one artifact-rich diagnostic extraction pass
3. write/update forensic note

Defer implementation until token budget is sufficient for full test + run proof.

## 9) Final status at pause
- Packet state: **open** (maker lane still unresolved)
- No broad refactor authorized.
- No closure claims made.
- Next action on resume: complete Step B decomposition and finalize minimal corrective design.

---

## Update — 2026-04-02 Resume Pass (No Runtime Behavior Changes)

### New artifacts created
- `exports/BRO_maker_stepB_decomposition_a84f8daa-9548-4dff-b605-be60ddf359be_20260402T_ot_resume.json`
- `exports/BRO_maker_stepB_findings_20260402T012200Z.md`

### Additional verified decomposition
1. `target_ref` -> token mapping is now fully resolved (deterministically via `sha256(token_id)[:16]`), allowing per-target forensic splits even with redacted `token_id` in event rows.
2. `market_probability_missing` is concentrated on exactly two target refs (a BUY/SELL pair) and mostly in `MAKER_TAKER_SELECTIVE` stage.
3. `token_lag_not_verified_for_maker` is persistent on a subset of target refs and transient on others.
4. `pre_submit_cross_guard` remains balanced by side (BUY/SELL near-equal), indicating no obvious one-sided sign inversion.

### Next immediate action retained
- Stay diagnosis-first: decide whether `market_probability_missing` behavior on one-sided books is doctrinally intended strictness or an avoidable recoverability gap.
- Do not change runtime behavior until this is resolved with explicit proof.
