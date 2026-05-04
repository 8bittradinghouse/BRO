# Storage Hygiene Packet A - 2026-04-25

## Scope
- Packet: matched export directory quarantine
- Mode: reversible move-only
- Source cleanup: out of scope
- Destructive deletion: not performed

## VERIFIED
- Quarantine root: `/home/odah/bro_cold_storage/storage_hygiene_packet_a_20260425T031541Z`
- Quarantine payload path: `/home/odah/bro_cold_storage/storage_hygiene_packet_a_20260425T031541Z/exports_matched_twins`
- Matched export directories moved: `21`
- Matched directory bytes moved out of hot repo tree: `2,392,973,340`
- All `21 / 21` matched `.zip` siblings were present, opened cleanly, and passed `zipfile.testzip()`.
- All moved directories are absent from `/home/odah/bro/base/exports`.
- Remaining hot `exports/` directories after Packet A: `4`
- Remaining hot directories are unmatched/non-packet twins:
  - `BRO_20m_coldstart_forensic_capture_35ba6f27-542e-4e4c-9787-3aa9961be0e2_20260409T081813Z`
  - `BRO_grok_consultant_packet_20260402T045452Z`
  - `BRO_paper_harness_revision_proof_4dee0545-3a42-4cde-9b2d-30e16e0764fd_20260403T053050Z`
  - `BRO_soak_fix_verification_20260403T095333Z`

## Before / After
- Hot repo `exports/` before move: `4.2G`
- Hot repo `exports/` after move: `2.0G`
- Quarantine payload size: `2.3G`

## Zip-Root Notes
- Most matched zips used the same-name top-level directory as their unpacked twin.
- Three structurally readable zips used a top-level `exports/` wrapper instead of the exact directory name:
  - `BRO_nova_full_review_20260405T053016Z.zip`
  - `BRO_nova_sniper_surgical_packet_20260406T045428Z.zip`
  - `BRO_sniper_taker_closeout_packet_20260405T051002Z.zip`
- This does not block Packet A quarantine.
- This does mean any future delete-readiness packet should keep an explicit eye on those three wrapper-shaped archives instead of assuming same-name roots everywhere.

## Moved Directory Set
- `BRO_20m_coldstart_forensic_capture_35ba6f27-542e-4e4c-9787-3aa9961be0e2_20260409T081906Z`
- `BRO_nova_60m_forensic_packet_20260407T031631Z`
- `BRO_nova_60m_forensic_packet_20260407T031631Z_repaired_20260407T035330Z`
- `BRO_nova_60m_validation_packet_20260407T014628Z`
- `BRO_nova_current_baseline_followup_packet_20260409T072550Z`
- `BRO_nova_doctrine_truth_streamline_review_fileset_20260409T060427Z`
- `BRO_nova_full_repo_snapshot_20260416T035005Z`
- `BRO_nova_full_review_20260405T053016Z`
- `BRO_nova_packet_20260403T101214Z`
- `BRO_nova_reanchor_consultant_packet_20260416T035005Z`
- `BRO_nova_runtime_packet_20260403T101355Z`
- `BRO_nova_sniper_surgical_packet_20260406T045428Z`
- `BRO_nova_taker_diagnosis_packet_20260406T020921Z`
- `BRO_nova_taker_validation_packet_20260406T080154Z`
- `BRO_nova_wallet_doctrine_truth_closure_packet_20260408T110154Z`
- `BRO_nova_wallet_execution_final_hardening_packet_20260408T065157Z`
- `BRO_nova_wallet_execution_hardening_review_packet_20260408T050232Z`
- `BRO_nova_wallet_execution_packet_20260407T054539Z`
- `BRO_nova_wallet_source_complete_packet_20260407T095855Z`
- `BRO_rocky_consultant_packet_20260405T221003Z`
- `BRO_sniper_taker_closeout_packet_20260405T051002Z`

## Residual Risk
- This packet reduced hot repo tree weight; it did not reclaim VPS disk bytes yet because the payload still exists locally in quarantine.
- No external backup or off-box duplication proof was established in this packet.
- Event-log retention and session-dir retention remain separate later packets.

## Next Recommended Move
- Keep Packet A as-is unless an operator needs one of the quarantined directories restored.
- If broader hygiene resumes later, do retention/mapping work for raw event logs and session directories before any destructive action.
