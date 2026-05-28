# BRO Pilot-Live Packet 1: Taker Architecture Surgery

## Classification
- `active packet owner`
- `pickup bridge`
- current owner for taker surgery after Packet 1 reopen
- historical taker closeout language elsewhere is ancestry only unless it is
  explicitly mirrored here

## Authority Lock
Current pickup point:
- active phase:
  - `pilot_live`
- active macro lane:
  - `live trust qualification`
- reopen dependency:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md`
- governing question:
  - can taker be rebuilt into one coherent live-trust lane before wallet
    hookup, or do deeper owner and economic defects still make it unsafe?

Authority chain:
1. `docs/JIN_RELOCK_PACK_2026-05-12.md`
2. `docs/PROJECT_TRUTH_STATE.md` as broad repo truth only
3. `docs/NEXT_PACKET_PLAN.md`
4. `docs/BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md`
5. Packet 1 reopen truth:
   - `docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md`
6. doctrine and design roots:
   - `docs/DOCTRINE_RUNBOOK.md`
   - `BRO_EDGE_DOCTRINE.txt`
   - `BRO_OUTCOME_TRUTH_DOCTRINE.txt`
   - `docs/EDGE_TRUTH_RUNBOOK.md`
7. runtime owners:
   - `executor.py`
   - `prodesk/taker_competitiveness.py`
   - `prodesk/order_manager.py`
   - `prodesk/gateway.py`
   - `prodesk/risk.py`
   - `prodesk/config.py`
   - `configs/profiles/paper_universal.yaml`
8. consumer/report surfaces:
   - `scripts/nightly_soak_report.py`
   - `scripts/outcome_truth_audit.py`
   - `scripts/edge_truth_audit.py`

## Pilot-Live Severity Covenant
Taker surgery is capital-trust work, not a settings cleanup packet.

Severity rules:
- false confidence is a production bug
- a seated knob is not proof that the lower truth model is clean
- wrapper-green is weaker than raw runtime, ledger, and report-owner truth
- support/report convenience may not impersonate runtime owner-law
- wallet hookup pressure may not distort taker surgery order

## Packet Disease Statement
- `VERIFIED`: the historical Packet 1 closeout fixed real false-authority
  seams around stage contradiction, lane naming, and some maker/taker
  interference.
- `VERIFIED`: the current taker patient is deeper:
  - global/ramp target ownership can still shadow taker-local shot geometry
  - taker notional sizing still uses midpoint semantics while actual spend uses
    executable touch price
  - decision lineage is not yet guaranteed to survive intact into submit/report
    truth
  - executor orchestration and taker competitiveness still split active taker
    decision law
  - lifecycle semantics are still copied across layers instead of owned once
  - route/source/submit token truth is still not one first-class contract
- `INFERRED`: this is architecture surgery, not just tuning repair

## Historical Runtime Pathology Cohort
- current hostile specimen:
  - `5f050669-2235-43e0-9a45-0d781fee70e2`
- current watched restoration proof specimen:
  - `9eca7bdf-defb-4db3-a2f0-f4d728092228`
- historical supporting cohort:
  - `c519e785-598c-4cd1-83af-51f0c37592b5`
  - `fbf0fff0-4452-40e6-83dc-fd64f62f0a72`
  - `aedce755-2a5b-42d9-8d36-5bf50758b71f`

What these prove:
- intended taker shot and actual runtime spend have diverged before
- the `-40` style losses were real inside BRO accounting once the oversized
  order was placed
- accepted submit/fill lineage has historically degraded beneath the cleaner
  decision lineage

## No-Change List
- no wallet hookup
- no generic weapon tuning
- no threshold loosening by momentum
- no blueprint-pleasing retune before owner truth is clean
- no report-only fix pretending to solve runtime owner disease
- no broad frame surgery
- no helper/shim growth unless the current path is proven insufficient

## Keep-Now Steel
- hard `<=7s` taker window
- top-level `taker.min_edge` as the current fire-threshold owner
- lag-verification requirement
- per-token cooldown
- max-orders-per-cycle
- visible-fill gate as real gate steel, while acknowledging its denominator
  still depends on wrong notional owner if target truth is dirty

## Surgery Families
1. target owner collapse
   - taker shot geometry must become taker-owned, not global/ramp shadow-owned
2. spend model collapse
   - target USD, share sizing, submit price, fill notional, and settlement
     must speak one economic language
3. lineage preservation collapse
   - decision lineage must remain final authority into submit/report surfaces
4. decision owner collapse
   - taker decision law must stop being split across executor and
     competitiveness as if they were co-equal brains
5. lifecycle owner collapse
   - lifecycle semantics must stop being copied through config, executor, and
     competitiveness layers
6. route/source/submit token contract closure
   - source token, submit token, and complement-route truth must be owned once

## Current Dirty-Tree Truth
- `VERIFIED`: the current dirty tree already contains partial taker hardening:
  - taker target is no longer force-overwritten from `_active_target_usd`
  - taker runtime now uses the canonical stage-window token set instead of the
    broader near-token shell
  - metrics bind/shutdown hardening is in place
- `VERIFIED`: the current dirty tree now also contains the first core surgery
  families in code:
  - taker notional sizing uses executable submit price instead of midpoint
    fantasy
  - canonical lifecycle / lineage / route fields survive through
    submit -> fill -> report surfaces
  - executor now owns explicit final-window and min-edge fire law for normal
    taker, while competitiveness is narrowed to bounded price/size feasibility
    work
- `VERIFIED`: the remaining legal blocker is no longer the old visible
  midpoint-to-touch overspend or submit-lineage downgrade class on normal
  taker.
- `VERIFIED`: the remaining packet gate is watched current-code runtime proof
  plus final doc/routing sync, not another abstract owner census.

## Current Cut Status
1. target owner collapse
   - `VERIFIED`: materially landed in code
2. spend model collapse
   - `VERIFIED`: materially landed in code
3. lineage preservation collapse
   - `VERIFIED`: materially landed through submit/fill/report surfaces
4. decision owner collapse
   - `VERIFIED`: materially landed for normal taker fire law
5. lifecycle owner collapse
   - `VERIFIED`: materially tightened; executor now owns the explicit
     final-window legality call for live `taker_window` records
6. route/source/submit token contract closure
   - `VERIFIED`: materially landed through canonical event fields
7. watched runtime proof
   - `VERIFIED`: fresh current-code watched specimen
     `9eca7bdf-defb-4db3-a2f0-f4d728092228` proved the restored normal taker
     lane end to end:
     - `target_usd_requested=5.0`
     - `target_usd_resolved=5.0`
     - executable-price sizing held with `price_source=taker_executable_price`
     - actual filled spend reconciled at `4.9992`
     - submit and fill both preserved `TAKER_COMMITMENT`
     - report lineage distribution stayed `TAKER_COMMITMENT: 1`
     - taker financial fill-notional reconciliation stayed `true`

## Current-Code Restoration Verdict
- `VERIFIED`: Packet 1 taker architecture restoration is now materially
  reclosed on current code for the pre-wallet gate.
- `VERIFIED`: the historical midpoint-to-touch overspend class is cut on the
  normal taker lane.
- `VERIFIED`: the normal taker submit/fill lineage downgrade class is cut on
  the canonical event path and current report preference path.
- `VERIFIED`: this earns a Packet 1 taker pre-wallet `GO` gate.
- `VERIFIED`: this does **not** authorize wallet hookup by itself; Packet 3
  wallet live-truth / approved-hookup proof remains the next downstream owner.

## First-Cut Surgery Order
1. routing truth sync
2. target owner collapse
3. spend model collapse
4. decision-to-submit lineage preservation
5. decision owner and lifecycle owner consolidation
6. route/source/submit token contract audit and repair
7. watched runtime + raw artifact proof

## First-Cut Falsifiers
- `$5` still fails to survive runtime as the taker-owned requested/resolved
  target
- actual executable spend still exceeds intended taker spend because midpoint
  share math remains upstream
- `TAKER_COMMITMENT` still degrades into weaker raw lineage buckets on submit
  or fill
- a supposed fix only changes reporting while raw order economics remain wrong
- route/source/submit token surfaces still disagree on what the shot was

## Current Legality Call
- `GO` for Packet 1 closeout sync, broader packet reroute, and downstream
  Packet 3 wallet live-truth work
- `NO-GO` for wallet hookup itself until Packet 3 live-wallet truth closes its
  own blocker family
- `NO-GO` for generic tuning or any performance claim beyond the restored
  architecture proof that was actually earned
