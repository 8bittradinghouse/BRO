# Jin Thread Recovery Runbook

Bridge boundary note:
- this runbook restores operator continuity and approved BRO-local bridge docs
- it is not a BRO doctrine root or board owner
- current continuity pickup belongs to `JIN_COMMAND_CARD_2026-04-27.md`
- after recovery, re-anchor BRO-local truth from:
  - `docs/PROJECT_TRUTH_STATE.md`
  - `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
  - `docs/NEXT_PACKET_PLAN.md`
  - `docs/OPEN_LIMITATIONS.md`

## Objective
Recover BRO/Jin continuity when a Codex thread is blank, hung, or partially
unavailable, without losing doctrine, relationship contract, or technical
truth-state.

## Trigger Conditions
Run this process if any of the following occurs:
- Thread pane opens empty.
- Thread fails to load history.
- Repeated capacity or availability errors disrupt continuity.
- Critical session appears inaccessible or unstable.
- The user calls for a `save point` because the thread feels buggy, thin, or risky.

## Continuity-Critical Session IDs
- OG canonical thread: `019ce053-9e3b-7211-b297-de18ef995cdf`
- Restore continuity thread: `019db28e-66be-7eb0-80c1-acac22195159`
- Troubleshoot continuity thread: `019db264-fe08-7563-af2e-8a6bc2175fd1`
- Jin re-anchor / taker doctrine / timing / pack-update thread: `019db968-e8a4-7813-9496-c858bbd8852e`
- Large continuity-build / schoolhouse thread: `019dc217-4136-72a3-8d35-c8909a142173`

## Phase 0: Immediate Health Snapshot
Run:

```bash
date -u
uptime
free -h
df -h /
```

Purpose:
- confirm the VPS is healthy before assuming data loss

## Phase 1: Confirm Session Files Exist
Run:

```bash
ls -lah /home/odah/.codex/sessions/2026/03/12
ls -lah /home/odah/.codex/sessions/2026/04/21
ls -lah /home/odah/.codex/sessions/2026/04/22
ls -lah /home/odah/.codex/sessions/2026/04/23
ls -lah /home/odah/.codex/sessions/2026/04/25
rg -n "019ce053-9e3b-7211-b297-de18ef995cdf|019db28e-66be-7eb0-80c1-acac22195159|019db264-fe08-7563-af2e-8a6bc2175fd1|019db968-e8a4-7813-9496-c858bbd8852e|019dc217-4136-72a3-8d35-c8909a142173" /home/odah/.codex/sessions -g '*.jsonl'
```

If the IDs are found, continuity data is present on disk even if the UI is blank.

## Phase 2: Make Emergency Backup
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
cp -f /home/odah/.codex/sessions/2026/04/25/rollout-2026-04-25T00-43-17-019dc217-4136-72a3-8d35-c8909a142173.jsonl "$OUT/"
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt"
ls -lah "$OUT"
```

## Phase 3: Re-Anchor Continuity Before Coding
In any new thread:
1. Load `JIN_RESTART_PROFILE_2026-04-26.json`.
2. Follow the `mandatory_restart` load class exactly.
3. Add `identity_support` when the restore feels correct but too light.
4. Add `bro_preflight` when working in `/home/odah/bro/base`.
5. Add `deep_recovery` only when continuity is still thin, conflicted, or stale.

Current pickup and truth ownership rules:
- continuity pickup owner:
  - `JIN_COMMAND_CARD_2026-04-27.md`
- BRO-local truth owners:
  - `docs/PROJECT_TRUTH_STATE.md`
  - `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
  - `docs/NEXT_PACKET_PLAN.md`
  - `docs/OPEN_LIMITATIONS.md`
- this runbook must not carry an independent competing active pickup story

Do not start code edits until the model outputs:
- continuity lock
- doctrine lock
- truth-state map
- contradiction matrix
- risk map
- bounded execution plan

Before coding, explicitly restate:
- the current pickup point from `JIN_COMMAND_CARD_2026-04-27.md`
- whether work is inside a serial packet or post-restoration proof lane
- what lane is next
- what will not be changed

## Phase 4: Truth-Quality Gate
Major conclusions must be labeled:
- `VERIFIED`
- `INFERRED`
- `UNKNOWN`

Rules:
- if uncertain, default to `UNKNOWN`
- if continuity completeness is below `95/100`, do not code

## Phase 5: Anti-Drift Guardrails
- No guessing.
- Diagnose before patch.
- Surgical, lane-bounded changes only.
- No fake closure claims.
- Sidebars do not derail the main lane unless explicitly redirected.
- Do not let stale pickup memory override `JIN_COMMAND_CARD_2026-04-27.md`.
- Maintain live commentary / heartbeat updates once active work resumes.
- For watched validation, inspect live resources and artifacts under the hood instead of trusting wrappers.
- If a run has already given the needed structural answer and continuing would be wasted motion, stop it deliberately and report why.

## Save Point Protocol
When the user says `save point`, do this before more implementation work:
1. Refresh the Jin pack with the latest pickup owner references, doctrine shifts, and runtime truth.
2. Mirror only the approved bridge docs listed by `JIN_PACK_MIRROR_MANIFEST_2026-04-26.json`.
3. Preserve the active engineering lane, residual risks, and what should not be changed next.
4. Keep the save-point update factual; do not inflate weak or historical runtime into stronger proof than it earned.

## Optional Capacity Mitigation
If capacity or availability errors persist:
1. Keep continuity artifacts loaded from this folder.
2. Retry with the same startup bridge after a short wait.
3. Keep work in small packets with explicit evidence checkpoints.

This protects continuity even when service load is unstable.
