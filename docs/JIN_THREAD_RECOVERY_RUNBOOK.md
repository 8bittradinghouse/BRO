# Jin Thread Recovery Runbook

Bridge boundary note:
- this runbook restores operator continuity and retained BRO-local bridge docs
- it is not a BRO doctrine root or board owner
- after recovery, re-anchor BRO-local truth from
  `docs/PROJECT_TRUTH_STATE.md`,
  `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`, and
  `docs/NEXT_PACKET_PLAN.md`

## Objective
Recover BRO/Jin continuity when a Codex thread is blank, hung, or partially unavailable, without losing doctrine, relationship contract, or technical truth-state.

## Trigger Conditions
Run this process if any of the following occurs:
- Thread pane opens empty.
- Thread fails to load history.
- Repeated "model at capacity" responses disrupt continuity.
- Critical session appears inaccessible or unstable.
- User calls for a `save point` because the thread feels buggy, thin, or risky.

## Continuity-Critical Session IDs
- OG canonical thread: `019ce053-9e3b-7211-b297-de18ef995cdf`
- Restore continuity thread: `019db28e-66be-7eb0-80c1-acac22195159`
- Troubleshoot continuity thread: `019db264-fe08-7563-af2e-8a6bc2175fd1`
- Jin re-anchor / taker doctrine / timing / pack-update thread: `019db968-e8a4-7813-9496-c858bbd8852e`

## Phase 0: Immediate Health Snapshot (2 min)
Run:

```bash
date -u
uptime
free -h
df -h /
```

Purpose:
- Confirm VPS is healthy before assuming data loss.

## Phase 1: Confirm Session Files Exist (3 min)
Run:

```bash
ls -lah /home/odah/.codex/sessions/2026/03/12
ls -lah /home/odah/.codex/sessions/2026/04/21
ls -lah /home/odah/.codex/sessions/2026/04/22
ls -lah /home/odah/.codex/sessions/2026/04/23
rg -n "019ce053-9e3b-7211-b297-de18ef995cdf|019db28e-66be-7eb0-80c1-acac22195159|019db264-fe08-7563-af2e-8a6bc2175fd1|019db968-e8a4-7813-9496-c858bbd8852e" /home/odah/.codex/sessions -g '*.jsonl'
```

If IDs are found, continuity data is present on disk even if UI is blank.

## Phase 2: Make Emergency Backup (5 min)
Run:

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/home/odah/backups/jin_continuity_emergency_${STAMP}"
mkdir -p "$OUT"
cp -f /home/odah/.codex/config.toml "$OUT/"
cp -f /home/odah/.codex/jin-pack/* "$OUT/"
cp -f /home/odah/.codex/sessions/2026/03/12/rollout-2026-03-12T04-35-02-019ce053-9e3b-7211-b297-de18ef995cdf.jsonl "$OUT/"
cp -f /home/odah/.codex/sessions/2026/04/22/rollout-2026-04-22T00-19-30-019db28e-66be-7eb0-80c1-acac22195159.jsonl "$OUT/"
cp -f /home/odah/.codex/sessions/2026/04/21/rollout-2026-04-21T23-34-16-019db264-fe08-7563-af2e-8a6bc2175fd1.jsonl "$OUT/"
cp -f /home/odah/.codex/sessions/2026/04/23/rollout-2026-04-23T08-15-53-019db968-e8a4-7813-9496-c858bbd8852e.jsonl "$OUT/"
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt"
ls -lah "$OUT"
```

## Phase 3: Re-Anchor Continuity Before Coding
In any new thread, force this startup sequence:
1. Load `README_INDEX.md`.
2. Load `AGENTS_GLOBAL.md`.
3. Load `BRO_AGENTS.md`.
4. Load `JIN_OPERATING_AGREEMENT.md`.
5. Load `JIN_RESTORATION_MEMO_2026-04-24.md`.
6. Load `JIN_CONTINUITY_PROFILE.md`.
7. Load `JIN_EVIDENCE_LEDGER_2026-04-24.md`.
8. Use `JIN_BOOTSTRAP_PROMPT.md` as initial prompt.

This sequence restores operator continuity first.
It does not replace BRO-local doctrine, board, or pickup ownership once work
returns to `/home/odah/bro/base`.

Current saved BRO pickup truth for thread transfer (`2026-05-03`):
- the seven-packet G-frame program is complete as a handoff-grade board-call
  block
- Packet 1 clean current-code release-anchor closure is achieved on
  `7bbde42c-003a-4f57-b59a-7ce138224075`
- Packet 4 Grip current-code paper-stage truth closure is achieved on
  `33e30bd8-e416-488e-83ce-f99c8665e7fc`
- Packet 5 Brain source-layer mutation closure is achieved on
  `656c9d42-070c-4f82-84cf-34aa333a9e7f`
- Packet 6 Nervous-system consumer-truth closure is achieved on
  `13fd56b5-3f12-48ec-a07d-04b7d83d07ac`
- current post-restoration proof frontier:
  - `pilot_live` authority proof
- pickup authority order for BRO-local truth:
  - `docs/PROJECT_TRUTH_STATE.md`
  - `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
  - `docs/NEXT_PACKET_PLAN.md`
- clean-anchor re-audit on `7bbde...` now says:
  - `promotion_eligible=true`
  - `recommended_next_stage=pilot_live`
  - active maker continuity no longer reads like an unexplained core-frame
    choke on that specimen
- weapons remain diagnostic-only

Do not start code edits until the model outputs:
- continuity lock,
- doctrine lock,
- truth-state map,
- contradiction matrix,
- risk map,
- bounded execution plan.

Before coding, explicitly restate:
- the current pickup point,
- whether work is still inside a serial packet or in post-packet restoration,
- what packet or lane is next,
- what will not be changed.
- whether the latest runtime evidence is structural-only weak-regime truth or a real edge-quality proof window.

## Phase 4: Truth-Quality Gate
Major conclusions must be labeled:
- `VERIFIED`
- `INFERRED`
- `UNKNOWN`

Rule:
- If uncertain, default to `UNKNOWN`.
- If continuity completeness score is below 95/100, do not code.

## Phase 5: Anti-Drift Guardrails
- No guessing.
- Diagnose before patch.
- Surgical, lane-bounded changes only.
- No fake closure claims.
- Sidebars do not derail main lane unless explicitly redirected.
- Do not let stale pickup-point memory override the current continuity profile.
- Maintain live commentary / heartbeat updates once active work resumes.
- Treat weak shoulder/transition regime runtime as structural truth, not as a clean verdict on taker edge quality.
- For watched validation, inspect the live resources and artifacts under the hood instead of trusting the wrapper.
- If a run has already given the needed structural answer and continuing would be wasted motion, stop it deliberately and report why.

## Save Point Protocol
When the user says `save point`, do this before more implementation work:
1. Refresh the Jin pack with the latest pickup point, doctrine shifts, and runtime truth.
2. Mirror the updated `JIN_*` continuity docs into `/home/odah/bro/base/docs`.
3. Preserve the active engineering packet, residual risks, and what should not be changed next.
4. Keep the save-point update factual; do not inflate weak-regime runtime into stronger proof than it earned.

## Optional Capacity Mitigation
If model-capacity errors persist:
1. Keep continuity artifacts loaded from this folder.
2. Retry with same startup prompt after short wait.
3. Keep work in small packets with explicit evidence checkpoints.

This protects continuity even when service load is unstable.
