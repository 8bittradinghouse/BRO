# Maintenance Work Pack - 2026-04-25

## Purpose
Lock the remaining BRO/VPS cleanup choices into one systematic maintenance pack.

This document is for:
- workshop cleanliness,
- storage discipline,
- VPS housekeeping,
- explicit risk separation,
- no code-lane drift.

This document is not for:
- source/code cleanup,
- strategy changes,
- taker/maker engineering changes,
- blind deletion.

## Current Operating Rule
- `VERIFIED`: each maintenance choice is its own work group.
- `VERIFIED`: the whole set is one work pack.
- `VERIFIED`: least-risk work group goes first.
- `VERIFIED`: risky items get separated and discussed before execution.

## Completed Work Groups
1. `VERIFIED`: BRO storage hygiene quarantine wave completed.
   - Packets: `A` through `F`
   - Status board: `/home/odah/bro/base/docs/STORAGE_HYGIENE_STATUS_2026-04-25.md`
2. `VERIFIED`: Docker builder-cache cleanup completed.
   - Result doc: `/home/odah/bro/base/docs/STORAGE_HYGIENE_DOCKER_SWEEP_2026-04-25.md`
3. `VERIFIED`: VPS safe housekeeping sweep completed.
   - Result doc: `/home/odah/bro/base/docs/VPS_MAINTENANCE_SWEEP_2026-04-25.md`
   - Risk backlog: `/home/odah/bro/base/docs/VPS_MAINTENANCE_RISK_BACKLOG_2026-04-25.md`
4. `VERIFIED`: cold-storage compaction completed.
   - Result doc: `/home/odah/bro/base/docs/COLD_STORAGE_COMPACTION_PACKET_2026-04-25.md`

## Remaining Work Groups

### Group 1 - Quarantined Artifact Deletion Policy
- Status: active
- Risk: lowest remaining
- Mode: analysis / doctrine only
- Destructive action: not allowed in this group
- Output:
  - `/home/odah/bro/base/docs/QUARANTINE_DELETION_POLICY_2026-04-25.md`

Why first:
- it reduces uncertainty without changing runtime state
- it tells us what is actually safe to delete later
- it separates locally proven redundancy from operator-stated redundancy

### Group 2 - Destructive Deletion Packet(s)
- Status: pending
- Risk: medium to high depending on artifact class
- Prerequisite:
  - Group 1 completed
  - explicit approval per risk class

Candidate sub-packets:
- `C-first`: session wrappers backed by hot reports and hot export zips
- `A-split`: only the subset still backed by hot zips
- `B`: consultant/review/snapshot zips only if off-box redundancy is accepted as sufficient
- `E`: report-only session wrappers only if report-dir-only redundancy is accepted

### Group 3 - Root-Owned APT Cache / Lists Cleanup
- Status: pending
- Risk: low to moderate
- Blocker: requires `sudo` / root
- Source:
  - `/var/cache/apt/pkgcache.bin`
  - `/var/cache/apt/srcpkgcache.bin`
  - optional `/var/lib/apt/lists/*`

### Group 4 - VS Code Installed Extension Version Pruning
- Status: pending
- Risk: moderate
- Why not yet:
  - active extension version is not simply "latest"
  - careless pruning would be sloppy

### Group 5 - System / Docker Package Upgrade Window
- Status: pending
- Risk: highest VPS-maintenance lane
- Why not yet:
  - can restart daemons
  - can change runtime state
  - needs deliberate maintenance window

## Execution Order
1. `VERIFIED`: Group 1 - quarantine deletion policy
2. `INFERRED`: Group 2 - limited destructive deletion, safest-first
3. `VERIFIED`: Group 3 - root-owned apt cleanup when root is available
4. `INFERRED`: Group 4 - VS Code extension pruning after active-version check
5. `VERIFIED`: Group 5 - package upgrades in a maintenance window

## Current Shop State
- `VERIFIED`: hot BRO tree is about `4.4G`
- `VERIFIED`: hot `logs_exec` is about `3.1G`
- `VERIFIED`: hot `exports` is about `1.2G`
- `VERIFIED`: BRO cold-storage root is about `2.5G`
- `VERIFIED`: VPS cold-storage root is about `818M`
- `VERIFIED`: filesystem is `29G` used / `86G` available on `/dev/vda2`

## What Will Not Change In This Work Pack
- no source cleanup
- no architecture drift
- no taker/maker engineering changes
- no blind deletion
- no package upgrades without a maintenance window
- no root-required cleanup without root

## Engineer Call
- `VERIFIED`: Group 1 is the correct least-risk next move.
- `VERIFIED`: destructive cleanup should be split by evidence class, not treated as one giant trash packet.
- `VERIFIED`: after compaction, the workshop is in a healthy state and further reduction is now more about deletion policy than obvious maintenance.
