# VPS Maintenance Sweep - 2026-04-25

## Purpose
Broaden shop maintenance beyond BRO-local artifact hygiene and handle safe VPS/workstation housekeeping without drifting into source-level cleanup or risky system changes.

## VERIFIED System Read
- Host: `8bit-BRO`
- OS: Ubuntu `24.04.4 LTS`
- Kernel: `6.8.0-101-generic`
- Root filesystem: `ext4` on `/dev/vda2`
- Uptime at audit: about `50` days
- Memory posture at audit:
  - RAM available ~= `2.5Gi`
  - swap used ~= `578Mi`
- Failed systemd units: `0`
- Maintenance timers already active:
  - `fstrim.timer`
  - `logrotate.timer`
  - `systemd-tmpfiles-clean.timer`
  - `apt-daily.timer`
  - `apt-daily-upgrade.timer`

## Doctrine Read
- No “CPU maintenance” action was needed.
- No filesystem defragmentation action was needed.
- On this host, `ext4` plus active `fstrim.timer` is the relevant normal disk-maintenance posture, not manual defrag theater.
- The VPS was not unhealthy; it was mostly carrying clutter and cache weight.

## Safe Maintenance Executed

### Disposable Cache / Temp Cleanup
- Deleted VS Code cached extension payloads from `/home/odah/.vscode-server/data/CachedExtensionVSIXs`
  - deleted bytes: `514,404,481`
- Deleted stale VS Code log directories, keeping the active/current one hot
  - deleted bytes: `282,594,212`
- Purged user pip cache
  - `python3 -m pip cache purge`
  - files removed: `290`
- Swept stale `/tmp` junk older than `24h`
  - removed old BRO analysis outputs, stale MCP scratch dirs, stale snapshot dirs, and stale temp work dirs
  - `/tmp` dropped from about `703M` to `13M`

### Reversible VPS Runtime Quarantine
- Quarantine root: `/home/odah/vps_cold_storage/vps_maintenance_packet_a_20260425T034158Z`
- Reversibly moved stale VS Code CLI server builds out of the hot path:
  - `Stable-41dd792b5e652393e7787322889ed5fdc58bd75b`
  - `Stable-e7fb5e96c0730b9deb70b33781f98e2f35975036`
  - `Stable-07ff9d6178ede9a1bd12ad3399074d726ebe6e43`
- Bytes moved out of the hot path: `824,946,835`
- Remaining hot VS Code CLI servers:
  - `Stable-10c8e557c8b9f9ed0a87f61f1c9a44bde731c409`
  - `Stable-560a9dba96f961efea7b1612916f89e5d5d4d679`

## Before / After Highlights
- `.vscode-server` dropped from about `3.7G` to `2.2G`
- `.cache/pip` dropped from about `29M` to `2.2M`
- `/tmp` dropped from about `703M` to `13M`
- Filesystem moved from about `39G used / 76G available` to about `38G used / 77G available`

## Safe But Not Executed
- Root-owned APT binary caches still present:
  - `/var/cache/apt/pkgcache.bin`
  - `/var/cache/apt/srcpkgcache.bin`
- APT package lists still present:
  - `/var/lib/apt/lists` ~= `239M`
- These are safe-ish housekeeping targets, but they need root and were not touched in this packet.

## Current Non-Urgent Findings
- Upgradable packages pending: `43`
- This includes Docker and core system packages, so it is not a blind-background-cleanup move.
- Journal effective disk usage is modest (`47.4M` by `journalctl --disk-usage`), so journald is not a priority issue.

## Residual Risk
- The VS Code CLI server move was reversible, not destructive.
- Package upgrades remain deliberately deferred because they change runtime/software state.
- Root-owned apt cache cleanup was blocked by missing sudo credentials.

## Engineer Recommendation
- Call this VPS maintenance section a clean checkpoint.
- If continuing later, next non-trivial VPS task should be a planned package-update window, not more random sweeping.
