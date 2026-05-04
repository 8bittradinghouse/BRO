# Storage Hygiene Status - 2026-04-25

## VERIFIED Completed Packets

### Packet A
- Type: matched export directory quarantine
- Hot-tree bytes moved: `2,392,973,340`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_A_2026-04-25.md`

### Packet B
- Type: consultant/review/repo-snapshot zip quarantine
- Hot-tree bytes moved: `816,630,747`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_B_2026-04-25.md`

### Packet C
- Type: stale paper-session directory quarantine
- Hot-tree bytes moved: `2,198,606,915`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_C_2026-04-25.md`

### Packet D
- Type: raw event-log quarantine
- Hot-tree bytes moved: `2,688,989,982`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_D_2026-04-25.md`

### Packet E
- Type: stale report-only session-dir quarantine
- Hot-tree bytes moved: `1,308,832,662`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_E_2026-04-25.md`

### Packet F
- Type: repo-local archive and backup cold-storage move
- Hot-tree payload moved: about `2.3G`
- Result doc: `docs/STORAGE_HYGIENE_PACKET_F_2026-04-25.md`

### Docker Sweep
- Type: builder cache prune
- Real VPS bytes reclaimed: `4.279GB`
- Result doc: `docs/STORAGE_HYGIENE_DOCKER_SWEEP_2026-04-25.md`

### Cold Compaction Packet
- Type: preserve-data cold archive compaction
- Real VPS bytes reclaimed: `9,129,261,781`
- Result doc: `docs/COLD_STORAGE_COMPACTION_PACKET_2026-04-25.md`

## Current State
- Hot repo tree size now: about `4.4G`
- Hot `logs_exec` now: `3.1G`
- Hot `exports` now: `1.2G`
- Cold-storage quarantine root now: about `2.5G`
- VPS filesystem state: `29G` used / `86G` available on `/dev/vda2`

## What Changed
- The BRO working tree is materially cleaner and lighter for day-to-day engineering.
- Active maker/guardian Docker images remain intact.
- Current anchor runs remain hot.
- Old consultant/review clutter and redundant session payloads are no longer sitting in the main workshop path.

## Biggest Remaining Storage Drivers
1. Raw event logs still kept hot:
   - `6` files
   - current total ~= `2,312,162,883` bytes
2. Archives:
   - now cold-moved out of the hot repo tree
3. Recent report-only session dirs still kept hot:
   - `26` dirs
   - current total ~= `325,135,683` bytes

## Safe Next Packet
- stop and reassess before any destructive phase

Why:
- the workshop is already materially cleaner
- further reduction would start leaning toward deletion-policy decisions rather than obvious low-risk organization work

## Explicit Non-Claims
- No destructive deletion authority has been exercised in this hygiene wave except Docker build-cache prune.
- No off-box redundancy was re-proven during these packets; operator statement and local reversibility are carrying the safety posture for quarantined artifacts.
- Cold packets `A`, `C`, `D`, `E`, and `F/archives` are now stored as verified `.tar.zst` archives rather than loose cold directories.
