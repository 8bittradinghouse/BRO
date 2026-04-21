# BRO Atlas Deep Sweep Report (Diagnostics-Only)

Date: 2026-04-20  
Scope: Money-touching and adjacent control harnesses (`decision`, `capital`, `evidence`, `safety`)  
Mutation policy: **No runtime/code behavior changes in this pass** (inspection + triage only)

## 1) Lineage Lock
- `git_commit`: `1782dfa51087ca8829bfc0c9393df3283b0f66f3`
- `tree_state`: docs-only dirty (`docs/DOCTRINE_RUNBOOK.md`, `docs/PROJECT_CHARTER.md`, and untracked docs artifacts)
- `profile`: `configs/profiles/paper_universal.yaml`
- `paper_enforce_setup_lock`: `true`
- `paper_expected_profile_name`: `paper_universal`
- `paper_expected_config_fingerprint_sha256`: `a4441860bfe5f8da697a222f3130b1885b13025671042706475daf4efc158304`
- `_meta.effective_config_sha256`: `a4441860bfe5f8da697a222f3130b1885b13025671042706475daf4efc158304`
- `setup_lock_match`: `true`

## 2) Evidence Corpus (This Pass)
- Deep pattern scan: `/tmp/deep_pattern_scan.txt` (355 lines)
- Deep semantic scan: `/tmp/deep_semantic_scan.txt` (1595 lines)
- Candidate board: `/tmp/deep_casualty_candidates.json` (330 candidates)
- Money-touching subset from candidate board:
  - total: `212`
  - `ORANGE_CANDIDATE`: `72`
  - `YELLOW`: `140`
  - tags: `broad_exception=95`, `subprocess_without_timeout=6`, `suppressed_exception=6`, `return_true=78`
- Deep triage expansion artifacts:
  - `docs/BRO_MONEY_HARNESS_TRIAGE_V2_20260420.json`
  - `docs/BRO_MONEY_HARNESS_TRIAGE_V2_20260420.md`
  - Heuristic escalation rule for candidate triage only:
    - `ORANGE_CANDIDATE` if tags include `broad_exception` or `suppressed_exception` or `subprocess_without_timeout`
    - `YELLOW` otherwise
  - Resulting triage counts on money-touching subset:
    - `ORANGE_CANDIDATE`: `107`
    - `YELLOW`: `105`

## 3) VERIFIED Findings (Ranked)

### RED
| ID | Subsystem | Finding | Evidence |
|---|---|---|---|
| V-001 | Config/Safety connector | **Prestart gate bypasses setup-lock/config validation path**: `scripts/prestart_gate.py` loads raw config via `_load_raw_with_extends`, not `load_execution_config`. | Source: `scripts/prestart_gate.py:12,36` and `prodesk/config.py:663-680,2240-2267`. Repro: `/tmp/prestart_lock_mismatch.yaml` returned `prestart_rc=0` while `load_execution_config` raised setup-lock fingerprint mismatch. |
| V-002 | Decision connector | **Post-wallet risk revalidation gap**: risk checks happen before wallet size reduction; reduced intent can bypass final size/risk legality checks. | Source path verified in `prodesk/order_manager.py` around `validate_order` before `wallet.authorize_intent` and submit. Repro file: `/tmp/repro_post_wallet_risk_gap.py`. |
| V-003 | Capital connector | **Reservation lock-loss on failed open-order confirm**: lock is popped before strict order_id check, then failure can halt with lock already removed. | Source: `prodesk/wallet/wallet_reservations.py:74-79`. Repro files: `/tmp/repro_wallet_reservation_loss.py`, `/tmp/repro_wallet_lock_non_idempotent_after_failure.py`, `/tmp/repro_wallet_halt_on_missing_order_id.py`. |

### ORANGE
| ID | Subsystem | Finding | Evidence |
|---|---|---|---|
| V-004 | Evidence connector | `canonical_paper_session` subprocess calls have no timeout on critical paths; session can stall/hang without deterministic timeout reason. | Source: `scripts/canonical_paper_session.py:290,611,1050,1230`. Repro scan: `/tmp/repro_subprocess_timeout_scan.py`. |
| V-005 | Decision/Safety connector | Cancel capacity drift: `risk.on_order_canceled()` is called before cancel success is known; failed/exception cancel attempts still consume cancel budget. | Source: `prodesk/order_manager.py:346-388`. Repros: `/tmp/repro_cancel_capacity_drift.py`, `/tmp/repro_cancel_capacity_drift_exception.py`. |
| V-006 | Decision/Safety connector | False cancel rate-limit lockout can trigger from repeated failed cancels (not only confirmed cancels). | Repro: `/tmp/repro_false_cancel_rate_limit.py`. |
| V-007 | Evidence/Safety connector | New valuation/lifecycle counters are emitted but not consumed by readiness/soak/ci gates. | Absence proof from script scan: `preexpiry_404_anomaly_count`, `lifecycle_context_mismatch_count`, `preexpiry_emergency_taker_*`, `held_unpriceable_*_count`, `valuation_hard_degraded_*_count` have `NO_GATE_CONSUMER` across `readiness_gate.py`, `soak_hardening_gate.py`, `ci_gate.py`. |
| V-008 | Capital connector | Wallet authorization mismatch in reduction semantics: `reduced` action path compares share-size flow using USDC tolerance semantics. | Source/repro from prior diagnostics: `/tmp/repro_wallet_units.py`. |
| V-009 | Evidence connector | `run_integrity_audit` silently drops malformed JSON lines; high log corruption can still pass integrity if enough valid rows remain. | Source: `scripts/run_integrity_audit.py` parse loops suppress parse errors. Repro: `/tmp/run_integrity_corrupt` produced `ok=true` with many malformed status rows. |
| V-010 | Runtime/Capital truth | Transaction lifecycle snapshots always report `healthy=true` even after submit failure states; can overstate runtime health surface. | Source: `prodesk/tx_manager.py:221-252`. Repro: submit-failed state still returns healthy snapshots. |
| V-011 | Identity/Evidence connector | Manifest parse fallback is silent (`{}` on decode failure), removing explicit reason surfaces for artifact identity failures. | Source: `prodesk/artifact_identity.py:15-19`. |
| V-012 | CI operability | `ci_gate.py` subprocess steps are unbounded (no timeout), including package install path; CI can hang without bounded failure classification. | Source: `scripts/ci_gate.py:19,56`. |

## 4) Additional High-Confidence Candidates (Not Yet Promoted to VERIFIED)

These are line-verified risk candidates, but not yet repro-promoted in this pass:

| Severity | Candidate Class | Count | Primary Surfaces |
|---|---|---:|---|
| ORANGE_CANDIDATE | broad exception masking in high-authority connectors | 60 | `executor.py`, `order_manager.py`, `wallet_controller.py`, `canonical_paper_session.py`, `run_integrity_audit.py`, gate scripts |
| ORANGE_CANDIDATE | suppressed exceptions (`with suppress`) in order lifecycle manipulation | 6 | `prodesk/order_manager.py` |
| ORANGE_CANDIDATE | subprocess-without-timeout in session/CI gates | 6 | `scripts/canonical_paper_session.py`, `scripts/ci_gate.py` |

## 5) YELLOW Candidate Pool (Money-Touching Harnesses)

- Total YELLOW in money-touching subset: `140`
- Dominant classes:
  - permissive truthy guards / fallback returns in control flows
  - broad exception blocks in non-fatal parse/telemetry routines
  - non-failing parse fallbacks in diagnostics/report paths

Representative hot spots by candidate density:
- `executor.py` (29)
- `scripts/canonical_paper_session.py` (18)
- `scripts/nightly_soak_report.py` (17)
- `prodesk/order_manager.py` (16)
- `prodesk/preflight.py` (14)
- `scripts/guardian_watchdog.py` (12)
- `prodesk/market_discovery.py` (10)
- `prodesk/chainlink_feed.py` (10)

## 6) Triage Output

### Immediate Surgical Queue (Current Scope RED/ORANGE only)
1. Close V-001 (prestart setup-lock bypass) with aligned config-load authority.
2. Close V-002 (post-wallet risk revalidation gap).
3. Close V-003 (reservation lock-loss + halt chain).
4. Add bounded timeout/error-class surfaces for V-004/V-012.
5. Correct cancel accounting semantics for V-005/V-006.
6. Wire key valuation/lifecycle counters into gate decisions for V-007.
7. Tighten evidence integrity policy for V-009/V-011.

### Deferred for follow-on packet
- Remaining ORANGE_CANDIDATE/YELLOW candidate board (212-item money subset) after RED/ORANGE closure.

## 7) Truth Boundary
- `VERIFIED`: V-001..V-012 listed above (source and/or repro evidence present).
- `INFERRED`: Remaining ORANGE_CANDIDATE and YELLOW pools from static inspection.
- `UNKNOWN`: Runtime materiality of non-promoted candidates until exercised in replay/runtime proofs.
