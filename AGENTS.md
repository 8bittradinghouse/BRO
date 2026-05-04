# BRO Repository Operating Contract

## Mission Context
BRO is a doctrine-locked trading execution system. The operating expectation is professional rigor under pressure, with capital safety and truth integrity ahead of speed or narrative.

## Canonical Principles
1. Execution-seeking, not execution-forcing.
2. Each short market window is treated as an independent decision context.
3. Three-gate behavior is mandatory:
   - strategy gate,
   - wallet/capital authority gate,
   - market-quality gate.
4. Fail-closed posture is required when truth quality degrades.
5. No strategy or architecture drift unless explicitly requested.

## Required Engineering Behavior
1. Diagnose before patch.
2. Keep changes surgical and lane-bounded.
3. Keep behavior and docs synchronized.
4. Separate `VERIFIED`, `INFERRED`, and `UNKNOWN` conclusions.
5. Do not claim full closure from narrow test subsets.
6. Keep unresolved limits explicit in final summaries.
7. Treat the first build of any new tool, module, plugin, or major surgery packet as a rough draft by default.
8. Require at least one explicit second-pass hardening / red-team pass before a new build is treated as turned in.

## High-Risk Guardrails
1. Do not bypass wallet safety semantics.
2. Do not blur valuation truth with reporting language.
3. Do not merge unrelated reliability lanes into one patch.
4. Do not silently relax gating behavior.
5. Do not hide or relabel runtime anomalies without evidence.

## Change Control
1. Keep commit scope single-lane when possible.
2. Use targeted tests for lane proof, then broader checks proportional to blast radius.
3. Preserve deterministic artifact evidence for major packets.
4. If evidence conflicts with plan assumptions, stop and re-anchor truth state.

## Communication Contract
1. Keep progress updates frequent during long packets.
2. Sidebars do not derail main-lane execution unless explicitly redirected.
3. Report risk directly and without softening.
4. Distinguish facts from hypotheses in every major update.

## Continuity Documents
For BRO continuity-sensitive work, load these first:
1. `/home/odah/bro/base/docs/JIN_OPERATING_AGREEMENT.md`
2. `/home/odah/bro/base/docs/JIN_EVIDENCE_LEDGER_2026-04-24.md`
3. `/home/odah/bro/base/docs/JIN_THREAD_RECOVERY_RUNBOOK.md`
4. `/home/odah/bro/base/docs/JIN_BOOTSTRAP_PROMPT.md`
5. `/home/odah/bro/base/docs/BRO_ENGINEERING_KERNEL.md`
