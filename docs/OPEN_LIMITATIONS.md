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
- no further generic weapon tuning is authorized while Packet 2 maker
  live-trust qualification remains unresolved beyond the bounded OG-tight
  alignment packet now active by explicit operator decision.
- current active paper OG-tight alignment packet is now:
  - maker gate band `8-12s`
  - taker gate band `8-12s`
  - hard regime filter active on both lanes:
    `usa_europe_peak_heuristic`, `asia_dominant_heuristic`
  - pinned and near-pinned market windows are now fail-closed for both maker
    and taker on `window_geometry_hard_pinned` /
    `window_geometry_near_pinned`
  - maker must be one-sided and above the `0.20` conviction floor to submit;
    below-threshold maker candidates now fail-closed on
    `maker_edge_below_min`
  - maker same-target repeat tolerance is now `0` prior submits
  - maker same-market expression is now single-op only; weaker complementary
    token candidates are pruned on
    `maker_single_market_expression_pruned`
  - daily loss hard pause active at `$280`
  - deliberate size exceptions remain active:
    maker `~$100`, taker `$25`
  - this is the current runtime truth for blueprint-aligned paper proving,
    not final economic closure
- taker complement-route behavior is extinct in current code:
  - direct-path expression only
  - forbidden same-token short thesis now blocks on
    `normal_taker_same_token_sell_forbidden`
  - older packet language that treated complement expression as valid is
    ancestry only and does not own current truth
- active paper money truth is now hardened around cash semantics:
  - canonical runtime / report pnl uses fill cashflow, exact cash adjustments,
    and `wallet_position_settled`
  - taker fee modeling is share-based `size * p * (1-p) * fee_rate`, not the
    old price-squared drift
  - binary short deployable-capital truth now holds gross `$1/share` short
    liability instead of `price * size`
  - slippage and adverse-selection remain visible as attribution, not wallet
    cash
  - maker rebates fail closed to `0` in canonical runtime money truth until an
    exact payout surface exists
- current remaining money-model limitation:
  - active paper fee truth now resolves via explicit fee authority precedence,
    but live per-market fee / reward ingestion is still not first-class
  - exact maker rebate payout truth is still unavailable, so canonical runtime
    rebate cash remains fail-closed to `0`
- maker is no longer blocked mainly by old semantics/authority slag; remaining
  maker unresolved work is product-fit / launch-engineering hardening:
  lifecycle cleanliness, actionable opportunity quality, and resting-order
  economics proof.
- first live-aligned overnight watcher proof now supports a stronger nuance:
  - fully pinned overnight books can already exist by maker open
  - honest maker abstention is therefore a real possibility, not automatic lane
    failure
  - taker may still find cheap-side late-window exploitation in the same
    regime
- discovery-only watcher runs may hint at complementary extreme-edge behavior,
  but that remains support context until repeated final-standard watcher
  specimens confirm it.
- Reconcile output is an integrity tripwire and must not be represented as full ledger accounting.
- schoolhouse/toolbox authority must remain zero:
  - schoolhouse means external VPS-level study/tool material, not BRO
  - active board sinks, packet owners, doctrine roots, and live proof steel
    stay with BRO
  - a later `schoolhouse clearing / deauth` lane is expected, but it is
    architecture/authority hygiene debt rather than the active runtime blocker

## Packet 3 Owner Map And Current Build State (2026-05-21)
- locked planning surfaces for this owner map now include:
  - `docs/BRO_WALLET_GUARDIAN_BLUEPRINT_WEB3PY_2026-05-21.md`
  - `docs/WALLET_GUARDIAN_READONLY_MAPPING_2026-05-21.md`
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_3_WALLET_GUARDIAN_SURGICAL_CUT_PLAN_2026-05-21.md`
- wallet/startup owner surfaces on current code are:
  - `prodesk/wallet/wallet_health.py`
  - `prodesk/wallet/wallet_controller.py`
- canonical live wallet authority lives under:
  - `canonical_live_wallet_truth`
- local and derived surfaces stay subordinate:
  - `local_tx_lifecycle_state`
  - `open_order_state`
  - `integrity_tripwire_reconcile_state`
- in live mode, submit readiness requires all of:
  - `auth.live_order_submission_enabled=true`
  - authoritative startup refresh
  - `canonical_live_nonce_available=true`
  - `canonical_live_pending_wallet_tx_available=true`
- current `live_pilot` posture remains fail-closed by default:
  - `auth.live_order_submission_enabled` is not armed by default
  - strict live nonce / pending-wallet-tx truth flags become mandatory if live
    order submission is enabled
- current paper posture may honestly show:
  - `order_submit_eligible=true`
  - `order_capable_live=false`
  - `canonical_live_nonce_available=false`
  - `canonical_live_pending_wallet_tx_available=false`
  - that is paper-authoritative only and must not be misread as live readiness
- deprecated top-level wallet / allowance / nonce / pending surfaces are
  retired from the live authority status contract; historical artifacts may
  still contain them, but they must not outrank `canonical_live_wallet_truth`

## Current Packet Boundary Addendum (2026-05-21)
- Current Packet 2 maker/taker selector families are not presently proven broken by reject volume alone.
- Strong health anchor:
  - `b6336854-b2f6-44a7-862a-71b41b6ac60f`
  - `runtime_classification=VALID_ACTIVE`
  - maker `15 submits / 25 fills`
  - taker `7 submits / 7 fills`
- Quiet-time contrast anchor:
  - `c519e785-598c-4cd1-83af-51f0c37592b5`
  - `runtime_classification=VALID_ACTIVE`
  - taker `1 submit / 1 fill`
  - maker `0 submits`
  - lifecycle clean
  - read this as low-opportunity shoulder-band structural truth, not as default maker choke evidence
- Current doctrine call:
  - high reject volume alone is not authorization for selector retuning
  - no generic maker/taker gate mutation is authorized without blueprint mismatch proof or a proved near-miss opportunity band
- Current immediate move:
  - the older straight-line move back into Packet 3 implementation is now
    temporarily overridden by:
    1. Packet 2 maker runtime-truth tribunal
    2. Packet 1 taker support-authority tribunal
    3. then return to Packet 3 wallet guardian implementation body
  - owner/mechanics/residue slices through compatibility retirement remain in
    place
  - strict live wallet truth remains fail-closed until an approved live hookup
    path is configured and proven
  - blueprint-alignment audit remains downstream
  - not emergency blocker surgery

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
- Current full-system completion status remains:
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
- Clean-anchor core-system re-audit on `7bbde...` demotes the old maker-core
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
  - lifecycle ownership-entry ceiling is `lifecycle.selection.max_sec_to_expiry=90.0`
  - maker gate band is `8.0-12.0s`
  - taker gate band is `8.0-12.0s`
  - hard regime filter is active on both lanes:
    `usa_europe_peak_heuristic`, `asia_dominant_heuristic`
  - maker risk-increasing authority lives only inside the shared
    blueprint-aligned `8-12s` band
  - daily loss hard pause is active at `$280`
- Current Packet 2 timing collision is explicit in pre-fix Packet 2 lineage:
  - the legacy profile carried the `90s` ownership-entry rule as
    `lifecycle.selection.min_sec_to_expiry=90.0`
  - `lifecycle.phase.maker_window_open_sec=10.0`
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
- Latest canon timing / queue-audit specimen
  `4f843da4-16ec-4616-948c-c7b19e7f5aea` now says:
  - `runtime_classification=VALID_ACTIVE`
  - owned markets stayed inside the final `90s`
  - maker and taker both fired on the live lane
  - current-code replay closes the archived:
    - `websocket_hardening_audit` denominator false fail
    - `soak_maker_submits_too_low` opportunity-accounting false fail
  - current-code replay still hard-fails on a real remaining economics signal:
    - `soak_execution_quality_capture_minus_adverse_too_low`
- the active Packet 2 maker entry artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
- the active Packet 2 recovery / unwind closeout artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md`
- the optional later recovery / unwind history/compat extinction artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- Historical repeated tiny-bleed recovery lineage is no longer an active Packet
  2 patient on current proof:
  - current canon losses are discrete order outcomes under the deliberately
    detuned test profile
  - `outcome_truth_audit.lane_outcome_truth.maker.lifecycle_residue_records=0`
  - `outcome_truth_audit.lane_outcome_truth.normal_taker.lifecycle_residue_records=0`
  - reopen only if the old repeated tiny recovery-style bleed pattern returns
- Queue-pressure current/live authority has now been cut. Only legacy-config
  ignored compatibility and historical replay lineage remain; it no longer
  exists as a live maker behavior/report family.
- Dust authority is not trusted steel. The shared recovery / unwind spine is
  now cut from the active runtime/report/doctrine owner stack; the remaining
  residue is compatibility archaeology, ignored dead-key support, and
  historical lineage/docs. The intended replacement is cancel-only fail-close
  for open unfilled orders plus hold-to-settlement for real accepted exposure.
- Current explicit Packet 2 surgery queue is:
  - current-code replay on canon specimen
    `4f843da4-16ec-4616-948c-c7b19e7f5aea` proved the first blocker-ledger cut:
    - `launch_safe_selection_insufficient_depth_multiple` = `keep-now steel`
    - `quote_quality_skip_queue_depth` = historical specimen-local call only;
      later hostile tribunal re-opened this family as suspicious live authority
    - `non_actionable_geometry` = clean upstream expression of the old low-price
      floor/cap infeasibility on that replay specimen, pending leaf-specific
      reread if fixed-shot doctrine changes
    - `maker_blocker_ledger.json` = current owner artifact for blocker truth
  - later hostile tribunal on the watched Packet 2C closeout `4ee0cf47...`
    now outranks the earlier specimen-local call:
    - selection depth multiple remains provisional blueprint steel
    - queue-depth quote quality is reopened as the first suspicious live
      downstream authority
    - geometry remains blueprint-aligned actionability truth pending
      leaf-specific reread
  1. `support-shadow / probe family` truth cleanup:
     - `maker_market_snapshot*`
     - late/mid-window probes
     - zero-submit root-cause
     - quote-starvation / quote-integrity / selection-authority counterfactual
     - current disposition after rerun:
       - current snapshot non-submit owner contract is
         `decision_result=viability_rejected` plus `decision_block_reason`
       - `selection_gate_*` fields are selector-only mirrors and may not be
         repopulated from geometry or quote-quality viability reasons
       - `maker_zero_submit_root_cause_audit.json` must step aside on submit
         runs instead of hallucinating timing-band mismatch
       - `maker_participation_waterfall.json` remains descriptive-only and must
         redirect to `maker_blocker_ledger.json` as the canonical current
         owner for blocker truth
  2. `accessory maker sizing scaler bundle` runtime tribunal:
     - `maker_depth_target_*`
     - `maker_liquidity_tod_*`
     - current `paper_universal` hardening now gives these leaves zero live
       authority:
       - `maker_depth_target_* = 0`
       - `maker_liquidity_tod_scaler_enabled = false`
       - `maker_liquidity_tod_depth_multiplier = 1.0`
     - current live owner still enforces the hard scalar
       `lifecycle.selection.maker_min_depth_multiple=1.5`
  3. `accessory competitiveness bundle` runtime tribunal:
     - `edge_scale_*`
     - `size_mult_max`
     - `spread_mult_min`
     - `requote_delta_mult_min`
     - current `paper_universal` hardening now gives these leaves zero live
       authority:
       - `edge_scale_enabled = false`
       - `size_mult_max = 1.0`
       - `spread_mult_min = 1.0`
       - `requote_delta_mult_min = 1.0`
  4. `final trust / falsifier proof` after the blocker queue is honest
  5. optional historical extinction packet only if we explicitly choose full
     repo extinction beyond current active-owner surfaces:
     - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- A clean current-code release anchor now exists on
  `7bbde42c-003a-4f57-b59a-7ce138224075`, but that does not remove the
  `pilot_live` frontier or promote full-system closure by itself.
- Pilot-live readiness still remains unproven.
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
