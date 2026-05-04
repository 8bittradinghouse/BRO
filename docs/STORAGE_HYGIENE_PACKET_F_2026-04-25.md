# Storage Hygiene Packet F - 2026-04-25

## Scope
- Packet: repo-local archive and backup cold-storage move
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_f_20260425T035250Z`
- Moved out of hot repo tree:
  - `/home/odah/bro/base/archives`
  - `/home/odah/bro/base/backups`
- Cold payload size after move: about `2.3G`
- `/home/odah/backups` was deliberately left untouched.

## Why This Was Safe
- These were repo-local safety assets, not current pickup-point runtime evidence.
- The move was reversible.
- This cleaned the BRO workshop without pretending deletion authority or off-box re-proof existed.
- The higher-value continuity / thread-recovery material in `/home/odah/backups` stayed in place.

## Payload Summary
- `archives/hygiene_20260320T005429Z`
  - old archived logs/sim/exports bundle
  - about `1.9G`
- `archives/packet_b_handoff_archive_20260324T023202Z`
  - old handoff markdown set
- `archives/legacy_paths/data_data_20260321T053213Z`
  - old legacy-path stub
- `backups/bro_backup_20260321T052918Z.tar.gz`
  - about `368M`
- `backups/bro_backup_2026-03-17.tar.gz`
  - about `1.3M`

## Residual Risk
- This packet did not reclaim VPS bytes; it only moved cold assets out of the hot repo tree.
- No deletion authority was exercised on these archive/backup assets.
- `/home/odah/backups` remains a separate protected continuity/backups area and was not part of this packet.

## Next Recommended Move
- Stop the BRO storage hygiene wave here unless a new explicit objective is chosen.
- If a later operator wants deeper reduction, the next honest conversation is about destructive deletion policy, not more blind moving.
