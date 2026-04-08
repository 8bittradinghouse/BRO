# BRO Latest Handoff Policy

Purpose: keep reviewer/operator handoff deterministic, additive, and easy to consume without re-running forensic discovery.

## Required Artifact Set Per Packet
- `before/` and `after/` report directories (or explicit run IDs when comparing exercised runs).
- Canonical report outputs used in claims:
  - `nightly_soak_report.json`
  - `websocket_reliability_gate.json`
  - `soak_hardening_gate.json`
  - `promotion_evidence_gate.json`
- Delta report output when baseline/candidate comparison is part of closure:
  - `soak_delta_report.json`
- Export identity and integrity surfaces:
  - manifest
  - checksum file
  - export audit

## Required Handoff Sections
1. Scope lock
- what packet changed
- what packet explicitly did **not** change

2. Authoritative change map
- file path
- behavior changed
- semantic classification (`authoritative`, `bounded`, `not_modeled`)

3. Before/after proof table
- exact metric names
- baseline value
- candidate value
- delta

4. Residual unknowns
- explicit unknowns only
- why unknown remains
- what evidence would close it

## Runtime Export Discipline
- Never overwrite prior packet exports.
- Use timestamped filenames.
- Keep packet-level summary markdown next to artifacts.
- Keep raw event/status slices referenced by run contract paths.

## Reviewer Fast Path
For external review (18F/Nova/Grok), include at minimum:
- packet summary markdown
- changed config/profile files
- changed execution + reporting scripts
- test outputs for changed behavior
- a single index file mapping every claim to one machine-readable artifact path
