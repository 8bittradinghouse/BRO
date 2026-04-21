# Bro Project Charter

## Mission
Harden and complete the Bro trading system to near pro-desk engineering quality before strategy optimization.

## Build Order
1. Engineering reliability, correctness, and operational safety.
2. Validation via deterministic tests and evidence-backed paper soak.
3. Strategy parameter tuning only after hardening and test confidence are established.

## Quality Standard
- Muscle-not-fat changes only: minimal surface area, measurable impact.
- Surgical edits with explicit pass/fail criteria.
- No speculative rewrites or broad refactors without clear ROI.
- Stable error taxonomy, reproducible workflows, and auditable outputs.

## Working Principles
- High rigor on every change.
- Test-backed implementation as work progresses.
- Deterministic gates over ad hoc judgment.
- Cross-system triage must use `docs/BRO_SYSTEM_COMPARISON_TABLE.md` + `docs/BRO_DIAGNOSTIC_TEMPLATE.md` before mutating fixes.
- Keep operator visibility high with concise, periodic progress updates.
- Teach-through-reporting: explain what changed, why, and what evidence passed/failed.

## Operational Priorities
- Paper soak must be meaningful and close to live-operational stress.
- Websocket, runtime, docker, and environment hardening are first-class scope.
- Reconciliation, readiness, and promotion checks are required controls.
- Promotion decisions must be evidence-driven (not subjective).

## Communication Contract
- Continue work in sequence unless blocked by a hard dependency.
- Provide short, steady status updates with current task and reason.
- Surface blockers immediately with concrete remediation options.

## Current Checkpoint (2026-03-08 UTC)
- Targeted tests and CI gate were passing at last checkpoint.
- Latest recompute artifacts were written under:
  - `exports/soak45_20260308T080644Z_repeat`
- Status snapshot:
  - `websocket_reliability.json`: pass
  - `promotion.json`: pass
  - `soak_hardening.json`: fail (utilization lane; order capacity breach rows over threshold)
  - `evidence_window.json`: fail
- Immediate focus: resolve soak utilization/evidence-window gate failures while preserving existing passing reliability and CI results.
