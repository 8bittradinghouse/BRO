# Quarantine Deletion Policy - 2026-04-25

## Purpose
Classify the BRO quarantine packets by deletion risk before any destructive action.

This packet is policy-only.

No deletions were performed while producing this document.

`VERIFIED`: packet `A`, `C`, `D`, `E`, and `F/archives` are now represented on disk as compacted `.tar.zst` archives in their packet roots. The risk ladder below is unchanged; only the cold-storage representation changed.

## Evidence Anchors
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_A_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_B_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_C_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_D_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_E_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_PACKET_F_2026-04-25.md`
- `/home/odah/bro/base/docs/STORAGE_HYGIENE_STATUS_2026-04-25.md`

## Current Cold Packets

### Packet A
- Path: `/home/odah/bro_cold_storage/storage_hygiene_packet_a_20260425T031541Z/exports_matched_twins.tar.zst`
- Count: `21`
- Logical payload bytes at quarantine: `2,392,973,340`
- Current archive bytes on disk: `1,076,176,438`
- Original claim:
  - unpacked export dirs had same-name zip siblings

### Packet B
- Path: `/home/odah/bro_cold_storage/storage_hygiene_packet_b_20260425T032225Z/exports_review_snapshot_zips`
- Count: `13`
- Current bytes on disk: `816,630,747`
- Original claim:
  - consultant/review/repo-snapshot zips cold-moved out of hot `exports`

### Packet C
- Path: `/home/odah/bro_cold_storage/storage_hygiene_packet_c_20260425T032522Z/paper_session_dirs_backed_by_report_and_zip.tar.zst`
- Count: `105`
- Logical payload bytes at quarantine: `2,198,606,915`
- Current archive bytes on disk: `50,230,164`
- Original claim:
  - old session wrapper dirs had matching hot report dirs and hot `exports/paper_session_<run_id>.zip`

### Packet D
- Path: `/home/odah/bro_cold_storage/storage_hygiene_packet_d_20260425T032752Z/raw_event_logs_older_than_2026-04-20.tar.zst`
- Count: `21`
- Logical payload bytes at quarantine: `2,688,989,982`
- Current archive bytes on disk: `68,016,005`
- Original claim:
  - old raw daily event logs moved cold to reduce hot-tree clutter

### Packet E
- Path: `/home/odah/bro_cold_storage/storage_hygiene_packet_e_20260425T033031Z/report_only_session_dirs_older_than_2026-04-20.tar.zst`
- Count: `132`
- Logical payload bytes at quarantine: `1,308,832,662`
- Current archive bytes on disk: `36,854,470`
- Original claim:
  - report-only wrapper dirs were old and separate from the canonical hot report dirs

### Packet F
- Paths:
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_f_20260425T035250Z/archives.tar.zst`
  - `/home/odah/bro_cold_storage/storage_hygiene_packet_f_20260425T035250Z/backups`
- Counts:
  - archives: `3`
  - backups: `5`
- Bytes:
  - archives logical payload at quarantine: `1,992,373,899`
  - archives current archive bytes on disk: `221,237,940`
  - backups current bytes on disk: `386,479,544`
- Original claim:
  - safety assets moved out of hot repo tree, not deletion candidates by default

## Redundancy Truth State

### Packet C - Safest Local Redundancy Class
- `VERIFIED`: `105/105` cold session-wrapper dirs still map to hot report dirs
- `VERIFIED`: `105/105` still map to hot `exports/paper_session_<run_id>.zip`
- `VERIFIED`: this is the strongest locally-proven delete candidate class

Plain-English:
these are wrappers for old runs where the canonical report evidence is still hot and the session export zip is still hot too. If we later choose destructive deletion, this is the safest first lane.

### Packet A - Mixed Class, Must Be Split
- `VERIFIED`: total cold dirs = `21`
- `VERIFIED`: `16/21` still have hot same-name zip siblings in `exports`
- `VERIFIED`: `5/21` no longer have hot same-name zip siblings because those zips were later cold-moved in Packet B
- `VERIFIED`: Packet A is no longer one clean risk class

Plain-English:
Packet A looked uniform at first, but it is not uniform anymore. Sixteen items still have the original local redundancy proof. Five are now coupled to Packet B and need separate handling.

### Packet E - Moderate Redundancy Class
- `VERIFIED`: `132/132` cold wrapper dirs still map to hot report dirs
- `VERIFIED`: `0/132` have corresponding hot export zips
- `INFERRED`: delete safety here depends on whether hot report dirs alone are accepted as sufficient canonical retention

Plain-English:
these are probably deleteable later, but they are not as clean as Packet C because the extra export-zip redundancy is gone.

### Packet B - Operator-Claim / Content-Class Candidate
- `VERIFIED`: Packet B holds `13` review/snapshot-style zip artifacts
- `UNKNOWN`: off-box redundancy was not re-proven in-tool during this packet
- `INFERRED`: these are likely low operational value for BRO runtime, but that is not the same thing as proven redundancy

Plain-English:
these smell disposable, but from a truth standpoint the safety case is weaker than Packet C because the redundancy proof is mostly operator-stated, not locally re-proven.

### Packet D - High-Risk Evidence Class
- `VERIFIED`: Packet D holds `21` raw event logs
- `VERIFIED`: these are raw execution tapes, not convenience exports
- `UNKNOWN`: whether external copies are complete enough to support deletion

Plain-English:
raw event logs are boring until the day they save your ass. This is not where we start destructive cleanup.

### Packet F - Highest-Risk Safety Asset Class
- `VERIFIED`: Packet F contains repo-local `archives` and `backups`
- `VERIFIED`: these are safety assets by nature
- `UNKNOWN`: whether all useful continuity/recovery value has already been superseded elsewhere

Plain-English:
don’t get cute here. These are not normal clutter.

## Safest-First Deletion Ladder
1. `VERIFIED`: Packet C
2. `VERIFIED`: Packet A subset with still-hot same-name zips (`16` items)
3. `INFERRED`: Packet E
4. `INFERRED`: Packet B
5. `VERIFIED`: Packet D
6. `VERIFIED`: Packet F

## Required Split Before Any Deletion Packet
- `VERIFIED`: Packet A must be split into:
  - `A1`: `16` items still backed by hot same-name zips
  - `A2`: `5` items now coupled to Packet B

## Non-Claims
- `VERIFIED`: no destructive deletion authority has been exercised here
- `VERIFIED`: no off-box redundancy was re-proven by this document
- `VERIFIED`: this is a risk map, not a permission slip

## Recommended Next Move
- `VERIFIED`: if destructive cleanup is approved later, start with Packet C only
- `VERIFIED`: do not blend Packet C with Packet D or Packet F in one delete wave
- `VERIFIED`: split Packet A before touching it

## Residual Risk
- `ORANGE`: operator-stated off-box redundancy remains largely unverified in-tool
- `ORANGE`: report-dir-only redundancy may be sufficient for Packet E, but that is still a policy choice
- `ORANGE`: Packet A now contains two different safety classes
- `RED`: raw logs and backups should not be destructively cleaned on momentum alone
