# BRO Pilot-Live Packet 2 Recovery/Unwind Root-Death Surgery Plan (2026-05-13)

## Authority Role
- `VERIFIED`: this is the packet-local surgery artifact for killing the
  pre-expiry recovery / unwind authority family and tracking its closeout
  progress.
- `VERIFIED`: it no longer functions as planning/prep only. Current-code
  runtime/report/doctrine owner cuts have materially landed; this artifact now
  tracks what remains before full repo extinction can be claimed.
- `VERIFIED`: the optional later archaeology/extinction packet is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- `VERIFIED`: it must be loaded after:
  - `docs/JIN_RELOCK_PACK_2026-05-12.md`
  - `docs/BRO_PILOT_LIVE_TRUST_BOARD_SINK_2026-05-05.md`
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SURGERY_SELF_HARDENING_PACK_2026-05-11.md`

## Current Status
- `VERIFIED`: current active runtime/report/doctrine owner surfaces now route
  lifecycle residue through:
  - `open_order_cleanup_required`
  - `settlement_hold_required`
  - `unresolved_lifecycle_obligation`
  - `cancel_fail_closed`
- `VERIFIED`: current consumer/output and validator readers now prefer those
  lifecycle-residue surfaces first and fence old recovery/unwind names as
  historical compatibility lineage only.
- `VERIFIED`: removed recovery timing/config leaves now survive only as ignored
  dead-key compatibility support in config and report readers.
- `INFERRED`: the remaining packet tail is compatibility archaeology,
  ignored dead-key support, and optional historical artifact/doc extinction,
  not a live contradictory recovery authority spine.

## Doctrine-Root Verdict
- `VERIFIED`: canonical accepted taker is a commitment hold to settlement, not
  an enter-then-unwind lane.
- `VERIFIED`: open unfilled orders are a cancel / cleanup problem, not a reason
  to preserve a recovery trading lane.
- `VERIFIED`: unresolved inventory, open orders, and lifecycle residue are
  lane-local lifecycle patients; they do not create general same-market
  recovery authority.
- `VERIFIED`: the shared recovery / unwind family was contradictory live owner
  mass and has now been cut from the active runtime/report/doctrine owner
  stack.
- `INFERRED`: the remaining tail is compatibility archaeology and historical
  lineage, not a present-tense keep-now safety spine.

## Keep-Now Steel
- ordinary cancel cleanup for open unfilled orders
- explicit unresolved lifecycle residue when cancel cannot complete cleanly
- hold-to-settlement for real accepted exposure
- wallet / risk / timing / market-truth fail-closed behavior
- no new risk inside the final window

## Kill Scope
The runtime family to remove is:

1. config and timing knobs
   - `runtime.held_preexpiry_reduce_only_sec`
   - `runtime.terminal_unwind_halt_new_risk_sec`
   - `runtime.preexpiry_emergency_taker_window_sec`
2. lifecycle payload and posture promotion
   - `_reduce_only_recovery_payload(...)`
   - `reduce_only_recovery_active`
   - `reduce_only_side`
   - `reduce_only_size_cap_shares`
   - posture promotion into `PREEXPIRY_REDUCE_ONLY` / `HALT_NEW_RISK`
3. runtime authority leaves
   - `reduce_only_recovery_allowed`
   - historical pre-expiry emergency taker allow field
   - maker-to-taker recovery handoff
   - emergency taker unwind authority
4. local order-manager recovery behavior
   - recovery cap / fallback / terminal unwind submit behavior
5. semantic ABI and operator language
   - `reduce_only_recovery_*`
   - `preexpiry_emergency_taker_*`
   - `reduce_only_recovery_size_cap_unavailable`
   - any current-owner doc, report, or test that still teaches in-cycle
     recovery trading as keep-now law

## Replacement Owner Model
- `open_order_cleanup_required`
  - open unfilled order exists
  - cancel it or fail closed and surface residue explicitly
- `settlement_hold_required`
  - real accepted exposure exists
  - hold to settlement / outcome truth
- `unresolved_lifecycle_obligation`
  - cancel or cleanup could not complete cleanly
  - surface it explicitly instead of hiding it behind recovery trading
- `cancel_fail_closed`
  - if cancel cannot complete in time or with clean authority, stand down and
    preserve the contradiction explicitly

## Surgery Order
1. authority census and doc/root cleanup
   - restate that no in-cycle unwind is canonical law
   - demote recovery / unwind wording from active-owner docs
2. runtime owner excision
   - remove the lifecycle payload, timing knobs, and posture promotion leaves
3. risk and order-manager collapse
   - delete recovery-only branches
   - preserve ordinary cancel behavior and ordinary fail-closed semantics
4. report / validator / doc ABI cleanup
   - remove recovery-era counters and current-owner language
5. watched proof
   - prove cancel-only fail-close remains available
   - prove accepted exposure rides to settlement
   - prove no in-cycle unwind authority survived under another name

## Abort Criteria
- any cut that weakens ordinary cancel cleanup
- any cut that silently widens new-risk authority
- any cut that leaves held exposure with no explicit owner
- any cut that preserves recovery trading under a renamed branch
- any watched proof that shows recovery / unwind was still protecting real
  current doctrine steel

## Acceptance Standard
- no active-owner surface teaches recovery / unwind as current law
- cancel remains the only fail-close cleanup path for open unfilled orders
- accepted exposure is explicitly hold-to-settlement
- no runtime, report, or test surface still relies on `reduce_only_recovery`
  or `preexpiry_emergency_taker_unwind` as current authority
- any later need for cleanup beyond cancel-only must re-earn authority from
  scratch on a cleaner body
- full repo extinction is a separate optional pass:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
  - ignored dead-key compatibility handling
  - historical artifact/doc string purge
  - deeper archaeology cleanup where no active-owner influence remains
