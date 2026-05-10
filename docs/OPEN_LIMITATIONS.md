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
- no generic weapon tuning or blueprint tuning is authorized while Packet 1
  taker authority repair remains open.
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
  - working tree clean after Packet 1 closeout stack
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
  - current specimens can still show mixed `15-20s` maker-allowed rows on some
    target refs
  - on `c6b3...`, those residual rows are truth-thin through
    `maker_requires_ws_book_source` /
    `market_reference_not_authoritative`, not re-proven as the old lag owner
- The earlier current-code pass run `c6019d01-3d4c-45c2-aea8-1c1312b870eb` remains useful as a pre-restoration comparison specimen only. It is no longer the active runtime truth anchor.
- Packet 1 closes the `soak_maker_submits_too_low` report-policy false-positive path. It does not prove broader maker profitability or justify strategy-aggression changes.
- Clean-anchor core-fighter re-audit on `7bbde...` demotes the old maker-core
  choke reading on that specimen:
  - `readiness_gate.runtime_findings=[]`
  - `execution_starvation_mode=none`
  - maker selection/readout surfaces now show explicit bounded no-submit
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
- Low-price maker floor/cap adaptation remains unresolved policy scope: current behavior fails closed when maker hard floor `100.0` USDC cannot fit inside maker hard max `800.0` shares.
- A literal 5-minute canonical session remains below the current canonical soak budget (`10` minutes and `20` status rows). Use it as smoke evidence only unless a separate smoke-budget lane is explicitly created.
- Current artifacts classify `reduce_only_recovery_size_cap_unavailable` as flat/wrong-side no-op local rejection, but future non-flat/unknown recovery-cap rows would reopen that lane.
- Current artifacts classify the single required-book-feed disconnected row as startup/bootstrap telemetry, but recurring post-bootstrap disconnects would reopen that lane.
- Current VPS resource telemetry is now visible, but capacity planning remains evidence-driven and unresolved for future multi-BRO operation.
- Packet 2 `Maker-Live / Economic Trust Qualification` remains unopened and
  unresolved even though Packet 1 has now closed `bounded-live-test ready`.
- the active Packet 2 recovery/entry artifact is:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
- A clean current-code release anchor now exists on
  `7bbde42c-003a-4f57-b59a-7ce138224075`, but that does not remove the
  `pilot_live` frontier or promote whole-fighter closure by itself.
- Pilot-live readiness still remains unproven, but paper-stage readiness is no
  longer blocked on the latest current-code doctrinal proof set.
- Maker/taker remain diagnostic-only for tuning/aggression work, but
  maker and taker live-trust qualification are now active diagnostic
  proof work inside the `pilot_live` lane.

## Truth Handling Rule
If a limitation is unresolved, it must remain visible in docs, reports, and operator handoff. It must not be softened into a success claim.
