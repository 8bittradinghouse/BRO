# Storage Hygiene Packet B - 2026-04-25

## Scope
- Packet: review/snapshot export zip quarantine
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_b_20260425T032225Z`
- Quarantine payload path: `/home/odah/bro_cold_storage/storage_hygiene_packet_b_20260425T032225Z/exports_review_snapshot_zips`
- Review/snapshot zips moved out of hot `exports/`: `13`
- Review/snapshot zip bytes moved out of hot `exports/`: `816,630,747`
- All moved zips are absent from `/home/odah/bro/base/exports`.
- Remaining hot zip count after Packet B: `142`

## Before / After
- Hot repo `exports/` before Packet B: `2.0G`
- Hot repo `exports/` after Packet B: `1.2G`
- Packet B quarantine payload size: `779M`

## Why These Were Good Candidates
- They were consultant/review/repo-snapshot style artifacts, not current pickup-point evidence.
- They were user-identified as redundant off-box handoff material.
- They are still preserved locally in quarantine, so this pass stays reversible.

## Moved Zip Set
- `BRO_consultant_artifacts_20260331T092011Z.zip`
- `BRO_nova_full_repo_snapshot_20260416T035005Z.zip`
- `BRO_nova_full_review_20260405T053016Z.zip`
- `BRO_nova_reanchor_consultant_packet_20260416T035005Z.zip`
- `BRO_nova_repo_code_snapshot_20260405T074700Z.zip`
- `BRO_nova_repo_snapshot_20260405T074125Z.zip`
- `BRO_nova_repo_snapshot_20260405T074321Z.zip`
- `BRO_nova_repo_snapshot_20260405T074500Z.zip`
- `BRO_nova_wallet_execution_hardening_review_packet_20260408T050232Z.zip`
- `BRO_repo_snapshot_20260331T092011Z.zip`
- `BRO_review_pack_92609236-10d5-4f91-9866-2472229b8e0b_20260402T025332Z.zip`
- `BRO_rocky_consultant_packet_20260405T221003Z.zip`
- `BRO_run_evidence_5496dbf5-8ba9-46ea-89d2-1b06be1a77a6_20260331T092011Z.zip`

## Residual Risk
- This packet improved hot-tree organization but did not reclaim VPS bytes because the zips still exist locally in quarantine.
- The moved zips still contain BRO-related docs and evidence, so any future destructive pass should keep the manifest and the operator's off-box redundancy statement attached to the deletion decision.
- Paper-session zips remain hot for now; they are smaller and lower priority than raw logs.

## Next Recommended Move
- Keep Packet B as-is unless an operator needs one of the quarantined zips restored.
- Next storage target should be raw event-log retention/compression planning, because `logs_exec` remains the dominant BRO storage driver.
