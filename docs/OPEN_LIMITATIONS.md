# BRO Open Limitations

## Canonical Live Truth Limitations
- canonical live nonce truth unavailable
- canonical live pending-wallet-tx truth unavailable
- strict order-capable live remains fail-closed
- reconcile is integrity tripwire, not full ledger accounting

## Current Operational Meaning
- Paper-mode wallet truth can be authoritative for paper harness operation.
- Paper-mode success does not prove live order capability.
- Wallet hookup truth is not the same thing as order-capable live truth.
- Live order-capable paths remain blocked unless canonical live nonce and pending-wallet-tx truth requirements are satisfied by an approved provider path.
- `prelive_gate` and `live_canary` are bounded tools inside the live-trust
  lane; they are not final live authority by themselves.
- no generic weapon tuning or blueprint tuning is authorized while Packet 2
  maker live-trust qualification remains unresolved.
- Reconcile output is an integrity tripwire and must not be represented as full ledger accounting.

## Launch-Window Limitations
- Current broad current-code canonical runtime proof
  `7bbde42c-003a-4f57-b59a-7ce138224075` is the active repo-level runtime
  truth anchor:
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - clean snapshot commit:
    `24e8dcaa471f8651a5e9231fdf3564026d4294b0`
  - current live repo runtime code fingerprint matches the clean anchor
    fingerprint exactly:
    `492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`
  - this exact match is fingerprint-scoped runtime identity, not a blanket
    proving-path identity claim
  - the referenced report/session path is not currently present on disk in this
    workspace, so this anchor is currently doc-backed and fingerprint-backed
    rather than directly artifact-readable locally
- Latest lane-specific closeout proof
  `33e30bd8-e416-488e-83ce-f99c8665e7fc` is supporting current-code closure
  truth for its lane, not the broad front-door runtime anchor.
- Current G-frame restoration status remains:
  - `complete`
- Current whole-fighter completion status remains:
  - `still open`
- Latest completed post-restoration hardening lane remains:
  - `timing spine hardening`
- Current next proof frontier remains:
  - `pilot_live` authority proof
- Latest timing-spine closeout proof remains:
  - `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
  - `time_discipline_audit.json` says:
    - `contract_authority_level=authoritative`
    - `finding_count=0`
    - `sample_count=11`
- Current pushed tree remains:
  - branch `consultant/full-snapshot-public-20260402T055838Z`
  - pushed
  - clean-tree Packet 1 closeout snapshot had been achieved before later
    continuity hardening packets; verify live cleanliness via `git status`
- Current clean closeout stack is:
  - runtime / config / report closeout packet `7bf765e...`
  - closeout-anchor sync packet `c3c5086...`
  - clean-state truth lock packet `907afa9...`
- Latest clean-tree validation run `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6`
  remains a clean-tree wiring reference, not the active front-door runtime
  anchor.
- Latest post-patch runtime proof `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69`
  remains a narrower contrast proof for taker-scope
  `fair_probability_missing`, not the active front-door runtime anchor.
- Older current-code specimens remain supporting contrast only and must not be
  treated as the active front-door anchor:
  - earlier packet specimen `4494f47e-9c0d-4ab0-80a3-141588388446`
  - clean reconfirmation specimen `d30b1c7c-ab05-494e-bfd1-3c5ac1205051`
  - deeper contrast specimen `c6b3bba3-a268-4e3e-9f53-3ae134689ca1`
- Packet 1 host-time semantics are explicit on the current truth chain:
  - host-side start/stop artifacts are authoritative and synced
  - runtime status rows remain non-authoritative `clock_state=partial_visibility`
    when `timedatectl` is unavailable in runtime context
- Current Packet 2 truth boundary is sharper than “old lag choke gone”:
  - the old `token_lag_not_verified_for_maker` seam is no longer the stable
    blocker owner on current code
  - historical pre-correction specimens can still show mixed `15-20s`
    maker-allowed rows on some target refs
  - on `c6b3...`, those residual rows are truth-thin through
    `maker_requires_ws_book_source` /
    `market_reference_not_authoritative`, not re-proven as the old lag owner
- Latest truth-reference starvation packet now has one current-code before/after
  pair on May 12, 2026:
  - pre-cut specimen `9a9cddf4-3738-4f66-82e0-b0fbc1d679fc`:
    - `book_feed_ws_books_missing_all_targets` fired `2` times
    - maker active-band truth was `0` authoritative rows / `0` depth-met /
      `0` full-cannon candidates
    - maker late-window starvation stayed entirely one-sided / non-authoritative
  - post-cut specimen `c56545a7-2183-4d3f-8405-9ad072dc7188`:
    - current code now triggers a one-shot existing-path self-heal on
      `book_feed_ws_books_missing_all_targets`
    - the same degraded WS cycle fired `1` time, not `2`
    - maker active-band truth improved to `16` authoritative rows / `3`
      depth-met / `3` full-cannon candidates
    - the current watched proof does **not** prove the self-heal caused those
      `16` authoritative rows because they were pre-reset and the reset armed
      bootstrap grace in that specimen
    - run still remained `NON_PROMOTABLE_NO_PARTICIPATION`, so this packet
      moved the disease but did not close the whole lane
  - strongest remaining post-cut blockers are now:
    - `secondary_oracle_not_confirmed`
    - `insufficient_depth_multiple`
    - residual `maker_requires_ws_book_source` /
      `taker_requires_ws_book_source`
- Latest one-sided selection packet now has a follow-on runtime specimen on
  May 12, 2026:
  - post-selection-cut specimen `03cfeff3-ad41-4b65-9172-89e78d472b52`:
    - runtime direct-midpoint maker rows now promoted into real
      `BUY_ONLY` / `SELL_ONLY` posture instead of staying `TWO_SIDED`
    - `maker_selection_authority_audit.blocked_count_by_canonical_reject_reason={}`
    - the narrow authority-blind one-sided activation bug is therefore cut
    - the old selector-owned low-edge one-sided reject family is now retired
      from canonical maker selection authority
    - run still remained `NON_PROMOTABLE_NO_PARTICIPATION`
    - active-band truth regressed on this specimen to `0` authoritative rows /
      `1` depth-met / `0` full-cannon candidates, so the live lane still
      failed on truth-thin one-sided / non-authoritative rows rather than selection drift
  - strongest current blockers after the one-sided cut are now:
    - `secondary_oracle_not_confirmed`
    - one-sided WS pair-truth with no authoritative maker reference
    - fail-closed maker reference missing / non-authoritative truth
    - residual `maker_requires_ws_book_source` /
      `taker_requires_ws_book_source`
- The earlier current-code pass run `c6019d01-3d4c-45c2-aea8-1c1312b870eb` remains useful as a pre-restoration comparison specimen only. It is no longer the active runtime truth anchor.
- Packet 1 closes the `soak_maker_submits_too_low` report-policy false-positive path. It does not prove broader maker profitability or justify strategy-aggression changes.
- Clean-anchor core-fighter re-audit on `7bbde...` demotes the old maker-core
  choke reading on that specimen:
  - `readiness_gate.runtime_findings=[]`
  - `execution_starvation_mode=none`
  - maker selection/readout surfaces now show explicit one-sided / missing-truth no-submit
    reasons rather than an unexplained paper-stage choke
  - peak-hours economic conclusions remain deferred because the specimen is an
    overnight logic specimen
- Fresh watched post-restoration contrast specimen `6e2826...` remained
  `VALID_ACTIVE` but did not earn paper-stage soak closure:
  - `quote_uptime_ratio=0.04753138846848339 < 0.05`
  - `maker_submits=2 < 50` under opportunity-aware enforcement
  - `maker_selection_authority_audit.current_decision_distribution={"submit_rejected":2,"submitted":2}`
  - `maker_selection_authority_audit.blocked_count_by_canonical_reject_reason={}`
  - `maker_zero_submit_root_cause_audit.zero_submit_classification=upstream_starvation`
  - `maker_quote_integrity_summary.next_repair_lane="D. Peak-hours confirmation specimen"`
  - do not treat this overnight specimen as runtime failure or as authorization
    to mutate doctrine, gates, or strategy
- Current accessible packet-era artifacts (`4b60...`, `33e3...`, `6e2826...`,
  `ec26...`) still carry earlier taker config semantics in their run manifests:
  - `min_edge_by_stage` still includes `MAKER_TAKER_SELECTIVE` and
    `SNIPER_PRIMARY`
  - `execution_cutoff_sec=10.0`
  - `arming_horizon_sec=86400.0`
  - some runs still carry stage-local final-window entries
  - they remain useful contrast/runtime truth, but they do not by themselves
    certify the newest uncommitted live-trust semantics now present in the
    working tree
- Historical Packet 2 maker specimens such as `8bfb...`, `ed184...`, and
  `e675...` carry their own dirty-tree run-manifest timing contract:
  - maker timing posture `50.0-60.0`
  - risk new-exposure floor `50.0`
  - they are valid runtime ancestry for that moment, but they do not certify
    current Packet 2 timing doctrine
- Current Packet 2 timing doctrine is now:
  - maker gate opens at `15.0s`
  - maker risk-increasing authority lives in `(7.0, 15.0]`
  - taker authority opens at `<=7.0s`
- Current Packet 2 timing collision is explicit in pre-fix Packet 2 lineage:
  - `lifecycle.selection.min_sec_to_expiry=90.0`
  - `lifecycle.phase.maker_window_open_sec=15.0`
  - pre-fix `prodesk/order_manager.py` reused those as the maker selection
    gate min/max timing window
  - that can reject authoritative `maker_window` rows as
    `launch_safe_selection_timing_window_out_of_band`
- Current live code now derives maker selection timing from lifecycle phase
  windows:
  - min = `lifecycle.phase.taker_window_open_sec`
  - max = `lifecycle.phase.maker_window_open_sec`
  - the active Packet 2 move is therefore runtime reproval and surviving
    blocker remeasurement, not more speculative timing surgery
- Current watched Packet 2 maker timing specimen
  `6957087b-488e-4bbb-b8b9-1f215b5e33d0` does not currently emit a distinct
  maker `new_exposure_expiry_gate_blocked` family.
  - the old exact `15.0s` split is now retired doctrine ancestry
  - this watched specimen does not yet show it as a separate runtime blocker
- Current Packet 2 timing support surfaces still carry a semantic compression
  seam:
  - raw `edge_evaluation` rows preserve historical recovery lineage flags
  - `maker_cannon_late_window_probe.jsonl` does not
  - some normal `10-15s` authority closures therefore compress into
    `phase_disallow_maker` on the support surface even though packet-era raw
    runtime truth still carried historical recovery lineage from the pre-cut
    recovery family
  - treat that historical recovery lineage as ancestry only, not as current
    owner-law
- Current watched evidence does not yet prove that normal-maker economics are
  being wrongly choked inside `10-15s`.
  - no normal `10-15s` rows in the watched specimen were simultaneously
    authoritative, geometry-viable, and non-recovery
  - the only authoritative + geometry-viable `10-15s` rows were recovery-only
- Low-price maker floor/cap adaptation remains unresolved policy scope: current behavior fails closed when maker hard floor `100.0` USDC cannot fit inside maker hard max `800.0` shares.
- A literal 5-minute canonical session remains below the current canonical soak budget (`10` minutes and `20` status rows). Use it as smoke evidence only unless a separate smoke-budget lane is explicitly created.
- Current artifacts classify `reduce_only_recovery_size_cap_unavailable` as
  flat/wrong-side no-op local rejection, but the broader recovery / unwind
  family has been cut out of the current active owner stack; remaining mentions
  are historical artifact lineage / compatibility archaeology only.
- Current artifacts classify the single required-book-feed disconnected row as startup/bootstrap telemetry, but recurring post-bootstrap disconnects would reopen that lane.
- Current VPS resource telemetry is now visible, but capacity planning remains evidence-driven and unresolved for future multi-BRO operation.
- Packet 2 `Maker-Live / Economic Trust Qualification` is now active in
  active post-surgery maker reread and remains unresolved even though Packet 1
  has now closed `bounded-live-test ready`.
- Major completed Packet 2 body repairs that changed the lane:
  - official Polymarket WS/CLOB substrate transplant is materially landed
  - canonical lifecycle surgery is materially landed
  - both moved the current maker patient away from generic body-breakage and
    toward a narrower maker-local authority wound
- Latest watched 20-minute current-tree systems-health specimen
  `98d7f6c5-bec9-4768-bb06-941079c2ac72` now says:
  - `runtime_classification=VALID_ACTIVE`
  - websocket/CLOB/oracle/lifecycle/timing health was clean
  - policy closeout still failed on maker-only findings:
    - `soak_maker_submits_too_low`
    - `soak_execution_quality_capture_minus_adverse_too_low`
- the active Packet 2 maker entry artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
- the active Packet 2 recovery / unwind closeout artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md`
- the optional later recovery / unwind history/compat extinction artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- Current strongest Packet 2 small-loss owner is still provisional:
  - strongest current owner candidate = `complete-outcome truth`
  - strongest foundation trace = `market-truth substrate`
  - current disposition = `not trusted steel / surgery-pile candidate`
  - final authoritative-only dollar-owner verdict remains open
- Queue-pressure current/live authority has now been cut. Only legacy-config
  ignored compatibility and historical replay lineage remain; it no longer
  exists as a live maker behavior/report family.
- Dust authority is not trusted steel. The shared recovery / unwind spine is
  now cut from the active runtime/report/doctrine owner stack; the remaining
  residue is compatibility archaeology, ignored dead-key support, and
  historical lineage/docs. The intended replacement is cancel-only fail-close
  for open unfilled orders plus hold-to-settlement for real accepted exposure.
- Current explicit Packet 2 surgery queue is:
  1. `surviving maker blocker family after timing-owner reproval`:
     - watched rerun `ae3bdf9e-6eee-4c99-8e3b-6e021136125c` is now clean on:
       - websocket/CLOB/oracle/lifecycle/timing health
       - canonical maker-window timing legality
       - real maker participation (`2` maker submits)
     - the older selector-owned one-sided branch was the leading blocker on the
       watched rerun and has now been retired from maker selection authority
     - strongest surviving follow-up question is therefore what the next real
       maker blocker becomes once timing and selector-owned one-sided drift are
       both out of the way
  2. `support-shadow / probe family` truth cleanup:
     - `maker_fight_admission_shadow*`
     - late/mid-window probes
     - zero-submit root-cause
     - quote-starvation / quote-integrity / selection-authority counterfactual
     - current disposition after rerun:
       - `maker_zero_submit_root_cause_audit.json` must step aside on submit
         runs instead of hallucinating timing-band mismatch
       - `maker_participation_waterfall.json` remains descriptive-only and must
         redirect to `maker_selection_authority_audit.json` as the canonical
         current owner for selection truth
  3. `accessory competitiveness bundle` runtime tribunal:
     - `edge_scale_*`
     - `size_mult_max`
     - `spread_mult_min`
     - `requote_delta_mult_min`
  4. `accessory maker sizing scaler bundle` runtime tribunal:
     - `maker_depth_target_*`
     - `maker_liquidity_tod_*`
  5. `small-loss wound family` remeasurement after earlier family cuts
  6. optional historical extinction packet only if we explicitly choose full
     repo extinction beyond current active-owner surfaces:
     - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- A clean current-code release anchor now exists on
  `7bbde42c-003a-4f57-b59a-7ce138224075`, but that does not remove the
  `pilot_live` frontier or promote whole-fighter closure by itself.
- Pilot-live readiness still remains unproven, but paper-stage readiness is no
  longer blocked on the latest current-code doctrinal proof set.
- Maker/taker remain diagnostic-only for tuning/aggression work, but
  maker and taker live-trust qualification are now active diagnostic
  proof work inside the `pilot_live` lane.
- Phase 2 WS/CLOB closure hardening is now materially landed for the current
  Packet 2 lane:
  - deterministic container build proof is landed
  - watched 20-minute paper specimen plus under-the-hood review are completed
  - do not reopen websocket/CLOB as the dominant maker patient unless new
    runtime evidence contradicts the current clean systems-health specimen

## Truth Handling Rule
If a limitation is unresolved, it must remain visible in docs, reports, and operator handoff. It must not be softened into a success claim.
