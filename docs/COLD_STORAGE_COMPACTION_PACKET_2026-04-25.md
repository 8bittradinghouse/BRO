# Cold Storage Compaction Packet - 2026-04-25

## Scope
- Packet: cold-storage compaction
- Mode: preserve-data archive transform
- Goal:
  - keep cold evidence/resources,
  - reduce VPS disk footprint,
  - avoid destructive evidence loss
- Source/code cleanup: out of scope
- Live runtime state changes: none

## Save Point
- `VERIFIED`: pre-compaction save point:
  - `/home/odah/backups/maintenance_savepoint_pre_cold_compaction_20260425T041722Z`

## Method
For each selected cold payload:
1. create `.tar.zst` archive in the same packet root
2. verify archive readability with `tar -I zstd -tf`
3. write archive checksum sidecar
4. remove only the bulky uncompressed cold payload after verification

## Why These Were Safe Candidates
- `VERIFIED`: packets `A`, `C`, `D`, `E`, and `F/archives` were dominated by text/log/json evidence and old archival material
- `VERIFIED`: packet `B` was already zip-compressed and not worth fake work
- `VERIFIED`: `F/backups` was already tar.gz-compressed safety gear and was left untouched

## Results

### Packet A
- Source:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_a_20260425T031541Z/exports_matched_twins`
- Archive:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_a_20260425T031541Z/exports_matched_twins.tar.zst`
- Before: `2,392,973,340`
- After: `1,076,176,438`
- Saved: `1,316,796,902`

### Packet C
- Source:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_c_20260425T032522Z/paper_session_dirs_backed_by_report_and_zip`
- Archive:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_c_20260425T032522Z/paper_session_dirs_backed_by_report_and_zip.tar.zst`
- Before: `2,198,606,915`
- After: `50,230,164`
- Saved: `2,148,376,751`

### Packet D
- Source:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_d_20260425T032752Z/raw_event_logs_older_than_2026-04-20`
- Archive:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_d_20260425T032752Z/raw_event_logs_older_than_2026-04-20.tar.zst`
- Before: `2,688,989,982`
- After: `68,016,005`
- Saved: `2,620,973,977`

### Packet E
- Source:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_e_20260425T033031Z/report_only_session_dirs_older_than_2026-04-20`
- Archive:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_e_20260425T033031Z/report_only_session_dirs_older_than_2026-04-20.tar.zst`
- Before: `1,308,832,662`
- After: `36,854,470`
- Saved: `1,271,978,192`

### Packet F Archives
- Source:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_f_20260425T035250Z/archives`
- Archive:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_f_20260425T035250Z/archives.tar.zst`
- Before: `1,992,373,899`
- After: `221,237,940`
- Saved: `1,771,135,959`

## Totals
- `VERIFIED`: total selected cold payload before compaction: `10,581,776,798`
- `VERIFIED`: total archive payload after compaction: `1,452,515,017`
- `VERIFIED`: total bytes saved by compaction: `9,129,261,781`

## Audit Artifacts
- Compaction manifest:
  - `/home/odah/bro_cold_storage/cold_compaction_manifest_20260425T041722Z.tsv`
- Per-archive checksum sidecars:
  - `*.tar.zst.sha256` next to each compacted archive

## Current State After Compaction
- `VERIFIED`: hot BRO tree remains about `4.4G`
- `VERIFIED`: hot `logs_exec` remains about `3.1G`
- `VERIFIED`: hot `exports` remains about `1.2G`
- `VERIFIED`: BRO cold-storage root is now about `2.5G`
- `VERIFIED`: filesystem is now `29G` used / `86G` available on `/dev/vda2`

## Residual Risk
- `VERIFIED`: payload shape changed from directories/files to `.tar.zst` archives
- `VERIFIED`: any future cold read now requires archive listing/extract instead of direct directory browse
- `VERIFIED`: packet `B` and `F/backups` remain as-is
- `UNKNOWN`: whether future operator preference will favor per-file compression over per-packet archives

## Engineer Call
- `VERIFIED`: this was the right move for logs/archives we wanted to keep but slim down
- `VERIFIED`: this reduced real VPS disk use without deleting the underlying evidence/resource set
