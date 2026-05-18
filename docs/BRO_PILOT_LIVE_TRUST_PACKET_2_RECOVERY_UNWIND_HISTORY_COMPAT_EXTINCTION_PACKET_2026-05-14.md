# BRO Pilot-Live Packet 2 Recovery/Unwind History and Compat Extinction Packet (2026-05-14)

## Authority Role
- `VERIFIED`: this is an optional later packet for historical lineage,
  compatibility-reader cleanup, ignored dead-key retirement, and repo-history
  extinction work after the recovery / unwind current-owner cut.
- `VERIFIED`: it is **not** a current live-owner surgery gate.
- `VERIFIED`: it must not outrank:
  - `docs/JIN_RELOCK_PACK_2026-05-12.md`
  - `docs/BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md`
  - `docs/NEXT_PACKET_PLAN.md`
  - `docs/OPEN_LIMITATIONS.md`
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md`

## Packet Trigger
- open this packet only if we explicitly choose:
  - full repo extinction of old recovery / unwind vocabulary, or
  - retirement of remaining compatibility-reader support, or
  - removal of ignored dead-key config handling
- do **not** open it by accident just because historical packet/docs still
  preserve ancestry strings

## Current Residual Classes
1. compatibility-reader tail
   - old recovery lineage still exists as bounded historical fallback in:
     - `scripts/nightly_soak_report.py`
     - `scripts/outcome_truth_audit.py`
     - `scripts/foundation_scenario_proof.py`
     - `scripts/edge_truth_audit.py`
     - `scripts/bro_metric_harvest.py`
2. ignored dead-key support
   - removed recovery/unwind knobs are still accepted-and-ignored in:
     - `prodesk/config.py`
3. test archaeology
   - historical fixtures and ignored-input proofs still preserve old names in:
     - `tests/test_execution_stack.py`
     - `tests/test_nightly_soak_report.py`
     - `tests/test_edge_truth_audit.py`
     - `tests/test_preflight_and_risk.py`
4. historical docs and artifacts
   - packet-history docs, old run audits, ledgers, and proofs still preserve
     old recovery / unwind ancestry where that history is still useful

## No-Change List
- do not reopen pre-expiry recovery / unwind as live doctrine
- do not weaken cancel-only fail-close for open unfilled orders
- do not weaken hold-to-settlement for accepted exposure
- do not convert historical cleanup into runtime behavior tuning
- do not purge history that still carries legitimate ancestry value unless the
  packet scope explicitly says to do so

## Packet Objectives
1. fence or remove remaining compatibility-reader recovery lineage where it no
   longer earns keep
2. decide whether ignored dead-key support should remain graceful or be
   hard-removed
3. centralize or delete leftover raw historical strings in tests where safe
4. optionally reduce historical doc/artifact residue without confusing current
   pickup truth

## Acceptance Standard
- no active pickup doc treats this packet as required before the next live
  maker packet
- current-owner recovery/unwind closure remains intact
- remaining old names are either:
  - intentionally historical, or
  - intentionally compatibility-bound, or
  - removed
- if full repo extinction is claimed later, it must be backed by a dedicated
  repo-wide proof pass rather than implied from current-owner closure
