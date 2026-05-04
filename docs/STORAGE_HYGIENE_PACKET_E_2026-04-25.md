# Storage Hygiene Packet E - 2026-04-25

## Scope
- Packet: stale report-only session directory quarantine
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_e_20260425T033031Z`
- Quarantine payload path: `/home/odah/bro_cold_storage/storage_hygiene_packet_e_20260425T033031Z/report_only_session_dirs_older_than_2026-04-20`
- Report-only session directories moved out of hot `logs_exec/paper_universal/sessions`: `132`
- Report-only session-dir bytes moved out of hot tree: `1,308,832,662`
- Selection rule:
  - session dir had `session_state.json`
  - session `run_id` existed
  - session timestamp was older than `2026-04-20`
  - matching report dir existed in `logs_exec/paper_universal/reports/<run_id>`
  - matching `paper_session_<run_id>.zip` did **not** exist in hot `exports/`
- Remaining hot report-only session dirs: `26`
- Remaining hot report-only bytes: `325,135,683`

## Why These Were Good Candidates
- Their canonical report dirs remain hot.
- The hot session dirs mostly added `session_state.json` plus raw slices.
- The moved subset was outside the recent working window, while newer report-only material stayed hot.

## Before / After
- Hot `logs_exec/paper_universal/sessions` after Packet E: `594M`
- Hot `logs_exec` after Packet E: `2.6G`
- Packet E quarantine payload size: `1.3G`

## Remaining Hot Report-Only Window
- Mostly `2026-04-24` through `2026-04-25`
- Includes current active run wrapper `6dc1b428-eb77-4881-ade7-204935b2b1a1`

## Residual Risk
- This packet improved hot-tree organization but did not reclaim VPS bytes because the session dirs still exist locally in quarantine.
- If deeper old-slice forensics are needed, a quarantined session dir may need to be restored or read from cold storage.
- `archives/` remains a separate later packet and still needs provenance mapping before action.

## Next Recommended Move
- Stop this hygiene wave here unless a new storage objective is explicitly chosen.
- The workshop is now materially cleaner; the next packet should be deliberate, not cleanup-for-cleanup's-sake.
