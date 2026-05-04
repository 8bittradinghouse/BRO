## Storage Hygiene Manifest - 2026-04-25

Purpose: macro-level storage hygiene only. This packet does **not** touch source code, config logic, runtime doctrine, or line-level cleanup. It exists to reduce VPS artifact bloat without losing continuity, evidence, or active engineering truth.

### Safety Rules

1. No deletion before inventory.
2. No deletion before redundancy is proven.
3. No touching current-run artifacts, current pickup-point evidence, or continuity anchors.
4. Prefer archive or reversible move over destructive deletion.
5. One artifact class at a time.
6. Measure reclaimed space after each cleanup packet.
7. Keep a manifest of every move, compression, or removal.

### Current Size Snapshot

- Repo root: `16G` - `/home/odah/bro/base`
- Runtime logs: `8.9G` - `/home/odah/bro/base/logs_exec`
- Exports: `4.2G` - `/home/odah/bro/base/exports`
- Archives: `1.9G` - `/home/odah/bro/base/archives`
- Jin pack: `128K` - `/home/odah/.codex/jin-pack`

### Top Storage Drivers

1. `logs_exec/paper_universal/events_*.jsonl`
   - largest single file: `events_2026-04-05.jsonl` ~= `896M`
   - other heavy files:
     - `events_2026-04-22.jsonl` ~= `664M`
     - `events_2026-04-21.jsonl` ~= `577M`
     - `events_2026-04-20.jsonl` ~= `461M`
     - `events_2026-04-24.jsonl` ~= `321M`
2. `logs_exec/paper_universal/sessions/`
   - total ~= `3.9G`
   - reports are comparatively small at ~= `117M`
3. `exports/`
   - `.zip` payloads ~= `2.05G`
   - unpacked directories ~= `2.39G`
4. `archives/hygiene_20260320T005429Z`
   - ~= `1.9G`
5. `backups/bro_backup_20260321T052918Z.tar.gz`
   - ~= `368M`

### Do-Not-Touch Set

These stay out of cleanup packets unless a later packet proves a replacement path:

- `/home/odah/.codex/jin-pack`
- `/home/odah/.codex/sessions/...` continuity anchors
- current active repo worktree and source files
- `docs/JIN_*`
- `docs/BRO_IDENTITY_PLATE.md`
- latest current pickup-point artifacts tied to taker/maker seam work
- recent raw runtime artifacts from the current working window until explicitly superseded

### Classification

#### Bucket A - Highest-Confidence Archive Candidates

These are unpacked export directories that already have a same-basename `.zip` sibling in `exports/`.
They are the cleanest first cleanup target because local redundancy is already visible.

Estimated reclaim if unpacked directories are moved off the hot repo tree after verification:
- ~= `2,392,973,340` bytes (`~2.23 GiB`)

Local zip integrity check:
- `21 / 21` matched `.zip` siblings tested clean with `python3 zipfile` integrity checks on `2026-04-25`
- this proves the local zip twins are structurally readable; it does **not** yet prove external backup policy or deletion readiness

Examples of the largest matches:

- `BRO_nova_taker_validation_packet_20260406T080154Z` ~= `717M`
- `BRO_nova_wallet_execution_packet_20260407T054539Z` ~= `644M`
- `BRO_nova_60m_forensic_packet_20260407T031631Z` ~= `226M`
- `BRO_nova_60m_forensic_packet_20260407T031631Z_repaired_20260407T035330Z` ~= `226M`
- `BRO_nova_full_repo_snapshot_20260416T035005Z` ~= `87M`
- `BRO_nova_reanchor_consultant_packet_20260416T035005Z` ~= `83M`
- `BRO_nova_sniper_surgical_packet_20260406T045428Z` ~= `73M`

Recommended action:
- verify each zip opens and has expected top-level contents
- move matching unpacked directories to a cold-storage quarantine path outside the hot repo tree
- only delete after the quarantine pass is verified and external copies are confirmed

#### Bucket B - Review Before Archive

These are large raw event logs. They are probably the next biggest savings lever, but they need a retention rule before action.

Potential savings by retention cutoff:

- older than `2026-04-20`: `2,688,989,982` bytes across `21` files
- older than `2026-04-21`: `3,172,076,335` bytes across `22` files
- older than `2026-04-22`: `3,776,481,994` bytes across `23` files
- older than `2026-04-23`: `4,471,847,904` bytes across `24` files

Recommended action:
- do **not** delete yet
- define retention first:
  - keep recent raw event logs hot
  - compress or move older logs to cold storage
  - preserve associated report directories

#### Bucket C - Needs Mapping Before Touch

Session directories are large, but less safe to move blindly than zipped exports.

Potential savings by age cutoff:

- older than `2026-04-21`: `2,289,696,508` bytes across `234` directories
- older than `2026-04-22`: `2,931,519,392` bytes across `287` directories
- older than `2026-04-23`: `3,599,637,142` bytes across `303` directories
- older than `2026-04-24`: `3,775,955,321` bytes across `312` directories

Recommended action:
- map which sessions already have:
  - report directories
  - exported `paper_session_<id>.zip` artifacts
  - current engineering references
- only then move stale session dirs

#### Bucket D - Low-Priority Review

- `/home/odah/bro/base/archives/hygiene_20260320T005429Z` ~= `1.9G`
- `/home/odah/bro/base/backups/bro_backup_20260321T052918Z.tar.gz` ~= `368M`

These may be redundant with later export packs and off-box copies, but they are not the first target because they look like intentional safety assets.

Recommended action:
- inventory provenance and external copy status first
- keep until duplication is explicitly proven

#### Bucket E - Tiny / Not Worth Risking the Mission

- Jin pack docs
- continuity docs
- handoff markdown
- repo docs in general

These are not the cause of the VPS weight problem.

### First Surgical Cleanup Packet Recommendation

If we want the highest-value, lowest-risk first pass:

1. `exports/` matched directory + zip duplicates
   - target reclaim: `~2.23 GiB`
   - move unpacked twins to quarantine/cold storage outside the hot repo tree
2. leave raw event logs and session dirs alone until retention/mapping is defined
3. leave archives/backups alone until provenance and duplication are verified

### What This Packet Explicitly Does Not Do

- no source-code cleanup
- no dead-code hunting
- no config pruning
- no doc rewrite beyond this manifest
- no destructive deletion

### Current Recommendation

Proceed with a dedicated **Packet A: matched export directory quarantine** before any broader cleanup.
