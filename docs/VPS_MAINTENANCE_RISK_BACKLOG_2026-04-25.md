# VPS Maintenance Risk Backlog - 2026-04-25

## Talk-First Items

### 1. System / Docker Package Upgrades
- Pending upgradable packages: `43`
- Includes:
  - `docker-ce`
  - `docker-ce-cli`
  - `docker-compose-plugin`
  - `containerd.io`
  - multiple `systemd` packages
  - `snapd`
  - other base-system libraries
- Why this is not a blind cleanup move:
  - it changes runtime state
  - it may require daemon restarts
  - it can affect Docker/BRO execution windows

### 2. Root-Owned APT Cache / Lists Cleanup
- Blocked by lack of sudo password in this session.
- Remaining targets:
  - `/var/cache/apt/pkgcache.bin`
  - `/var/cache/apt/srcpkgcache.bin`
  - optionally `/var/lib/apt/lists/*`
- Risk level: low to moderate
- Why deferred:
  - requires root
  - list cleanup is safe but means apt metadata must be regenerated later

### 3. Installed VS Code Extension Version Pruning
- Multiple installed OpenAI ChatGPT extension versions still exist side-by-side.
- The active process was using:
  - `openai.chatgpt-26.417.40842-linux-x64`
- Newer installed versions also exist:
  - `openai.chatgpt-26.422.21459-linux-x64`
  - `openai.chatgpt-26.422.30944-linux-x64`
- Why deferred:
  - the active extension version is not simply “latest”
  - deleting the wrong installed version would be sloppy

### 4. BRO Artifact Deletion
- BRO artifacts were quarantined aggressively in the hygiene wave, but not destructively deleted.
- Why deferred:
  - continuity / evidence sensitivity
  - off-box redundancy was user-stated, not re-proven in-tool

## Real-World Help That Would Actually Matter
- If you want me to finish the root-owned apt housekeeping, I need a session with sudo/root available.
- If you want a package-upgrade packet, give me an okay for a maintenance window where Docker/system packages are allowed to change and possibly restart services.
