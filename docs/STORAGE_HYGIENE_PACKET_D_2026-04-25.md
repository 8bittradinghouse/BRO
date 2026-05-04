# Storage Hygiene Packet D - 2026-04-25

## Scope
- Packet: raw event-log quarantine
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_d_20260425T032752Z`
- Quarantine payload path: `/home/odah/bro_cold_storage/storage_hygiene_packet_d_20260425T032752Z/raw_event_logs_older_than_2026-04-20`
- Raw event logs moved out of hot `logs_exec/paper_universal`: `21`
- Raw event-log bytes moved out of hot tree: `2,688,989,982`
- Retention cutoff used: keep `2026-04-20` and newer hot
- Remaining hot raw event logs: `6`

## Remaining Hot Event Files
- `events_2026-04-20.jsonl`
- `events_2026-04-21.jsonl`
- `events_2026-04-22.jsonl`
- `events_2026-04-23.jsonl`
- `events_2026-04-24.jsonl`
- `events_2026-04-25.jsonl`

## Before / After
- Hot raw event-log count before Packet D: `27`
- Hot raw event-log count after Packet D: `6`
- Hot raw event-log bytes after Packet D: `2,312,162,883`
- Hot `logs_exec` after Packet D: `4.4G`
- Packet D quarantine payload size: `2.6G`

## Why This Was a Good Candidate
- Raw event logs were the biggest remaining BRO-local storage driver.
- Older logs are outside the current active working window.
- This pass preserved the recent hot window for current BRO debugging while staying reversible.

## Residual Risk
- This packet improved hot-tree organization but did not reclaim VPS bytes because the logs still exist locally in quarantine.
- If future diagnostics need one of the cold event files, it must be restored or read from the quarantine path.
- Report-only session dirs and `archives/` remain unmapped later storage candidates.

## Next Recommended Move
- Pause and review whether the current workshop state is good enough before touching report-only session dirs or `archives/`.
- If hygiene continues later, handle report-only session dirs with a fresh evidence policy instead of reusing the Packet C rule.
