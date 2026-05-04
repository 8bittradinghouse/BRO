# Storage Hygiene Packet C - 2026-04-25

## Scope
- Packet: stale paper-session directory quarantine
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_c_20260425T032522Z`
- Quarantine payload path: `/home/odah/bro_cold_storage/storage_hygiene_packet_c_20260425T032522Z/paper_session_dirs_backed_by_report_and_zip`
- Session directories moved out of hot `logs_exec/paper_universal/sessions`: `105`
- Session-dir bytes moved out of hot tree: `2,198,606,915`
- Selection rule:
  - session dir had `session_state.json`
  - session `run_id` existed
  - session timestamp was older than `2026-04-23`
  - matching report dir existed in `logs_exec/paper_universal/reports/<run_id>`
  - matching export zip existed in `exports/paper_session_<run_id>.zip`
- Explicit keep-hot exclusions:
  - `6dc1b428-eb77-4881-ade7-204935b2b1a1`
  - `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
  - `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
  - `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- All four exclusion runs remain hot in `logs_exec/paper_universal/sessions`.

## Before / After
- Hot `logs_exec/paper_universal/sessions` before Packet C: about `4.1G`
- Hot `logs_exec/paper_universal/sessions` after Packet C: `1.9G`
- Packet C quarantine payload size: `2.1G`
- Hot `logs_exec` after Packet C: `5.1G`

## Why These Were Good Candidates
- They were old enough to be outside the current working window.
- They were locally redundant on the VPS because both:
  - a hot report directory existed, and
  - a hot `paper_session_<run_id>.zip` existed.
- The move stays reversible while reducing hot-tree clutter.

## Residual Risk
- This packet improved hot-tree organization but did not reclaim VPS bytes because the session dirs still exist locally in quarantine.
- Report-only session dirs remain hot and need separate mapping before any move.
- Raw daily `events_*.jsonl` files remain the biggest BRO storage driver inside `logs_exec`.

## Next Recommended Move
- If continuing hygiene, focus next on raw event-log retention/compression planning.
- Do not move report-only session dirs blindly; they need a different evidence policy than the redundant session dirs handled here.
