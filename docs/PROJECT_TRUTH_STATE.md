# BRO Project Truth State

## Authority Role
- This is the repo-level current truth screen for BRO.
- It is the front-of-house current-truth surface for active operator use.
- It must be backed by explicit run artifacts, fingerprints, and report paths.
- `docs/CURRENT_BASELINE.md` remains a baseline/reference/history surface and
  does not replace this file as the current truth screen.

## Evidence Lock
- Current code-bearing repo commit: `53121bc3641822283ba3543d7eebb42c810eb687`
- Current baseline tag: `bro-launch-window-continuity-baseline-20260422`
- Latest clean-tree validation run: `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
- Current broad current-code canonical runtime proof: `7bbde42c-003a-4f57-b59a-7ce138224075`
- Current broad current-code report dir: `/tmp/bro_bones_clean_anchor_20260504T063100Z/logs_exec/paper_universal/reports/7bbde42c-003a-4f57-b59a-7ce138224075`
- Current broad current-code session dir: `/tmp/bro_bones_clean_anchor_20260504T063100Z/logs_exec/paper_universal/sessions/df1c41b7-b921-4e02-982b-a6db24c79f2d`
- Current broad current-code validation status: `pass`
- Current broad current-code runtime classification: `VALID_ACTIVE`
- Current broad current-code promotion eligibility: `true`
- Current broad current-code recommended next stage: `pilot_live`
- Current broad current-code proof git commit: `24e8dcaa471f8651a5e9231fdf3564026d4294b0`
- Latest current-code lane-specific closeout proof: `33e30bd8-e416-488e-83ce-f99c8665e7fc`
- Latest current-code lane-specific closeout report dir: `logs_exec/paper_universal/reports/33e30bd8-e416-488e-83ce-f99c8665e7fc`
- Latest current-code lane-specific closeout session dir: `logs_exec/paper_universal/sessions/1da6753e-efe4-4ca4-b878-5797b17adebb`
- Current G-frame restoration status: `complete`
- Current whole-fighter completion status: `still open`
- Latest completed post-restoration hardening lane: `timing spine hardening`
- Current next proof frontier: `pilot_live` authority proof
- Latest timing-spine closeout proof: `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
- Latest timing-spine closeout report dir: `logs_exec/paper_universal/reports/4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
- Latest timing-spine closeout session dir: `logs_exec/paper_universal/sessions/d8360bec-8e02-46dd-88aa-3c599f0d784f`
- Latest packet-1 smoke validation: `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d`
- Profile: `paper_universal`
- Broad current-code proof config fingerprint: `6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
- Latest clean-tree baseline code fingerprint: `3d49a999b9a5d1e748b527152cbf08711cdf4147b1339b376f298faefb26bea5`
- Current broad current-code runtime proof code fingerprint: `492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`
- Latest lane-specific closeout proof code fingerprint: `492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`
- Latest timing-spine closeout proof config fingerprint: `bff661de876116b6c96315aa29280bc7432cab9a2d53b2a004af897fa516c8bd`
- Latest timing-spine closeout proof code fingerprint: `d546a054cf2b77f6335b9c5461e6c95ecf87737be6366f14d975311878487be7`
- Current pushed workspace runtime code fingerprint exact-match to timing closeout proof: `d546a054cf2b77f6335b9c5461e6c95ecf87737be6366f14d975311878487be7`
- Current pushed branch: `consultant/full-snapshot-public-20260402T055838Z`
- Current packet state: committed-and-pushed post-packet G-frame restoration
  retightening complete; working tree clean; peak-hours paper confirmation is
  materially achieved on current proof; next move is bounded `pilot_live`
  authority prep through clean-tree prelive/live-canary gating without default
  maker timing mutation.

## VERIFIED_CLOSED
- Clean current-code release-anchor proving seam was diagnosed to
  canonical-session overlap/self-sabotage in
  `scripts/canonical_paper_session.py`; the current wrapper now fail-closes on
  concurrent open sessions and active-phase stack death.
- Canonical session open-state hygiene is now materially closed on current
  code:
  - `phase_start()` now checks concurrent open canonical sessions before
    entering `start`
  - uninitialized `run_manifest_path` / `run_contract_path` no longer
    serialize as `.` into session state
  - stale orphan session-state backlog was reconciled in place only after
    confirming no live canonical containers or wrapper processes were running
- Isolated clean current-code release anchor
  `7bbde42c-003a-4f57-b59a-7ce138224075` on clean snapshot commit
  `24e8dcaa471f8651a5e9231fdf3564026d4294b0` passed canonical paper validation
  with `code_fingerprint_sha256=492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`,
  `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`,
  `highest_passing_stage=paper`, `blocking_stage=pilot_live`, and
  `runtime_classification=VALID_ACTIVE`.
- Clean-anchor core-fighter re-audit on
  `7bbde42c-003a-4f57-b59a-7ce138224075` now proves there is no active
  unexplained maker-core choke on the clean release anchor; that proof moved
  the open work first to timing spine hardening and now, after timing closeout,
  to `pilot_live` authority proof above paper:
  - `canonical_paper_validation.json` says:
    - `promotion_eligible=true`
    - `recommended_next_stage=pilot_live`
  - `readiness_gate.json` says:
    - `runtime_findings=[]`
    - `execution_starvation_mode=none`
    - `runtime_meaningful_participation=1.0`
  - `maker_selection_authority_audit.json` says:
    - `current_decision_distribution={"selection_rejected":8,"submit_rejected":16,"submitted":2}`
    - `blocked_count_by_canonical_reject_reason={"secondary_oracle_not_confirmed":8}`
  - `maker_quote_integrity_summary.json` says:
    - logic findings from quote certification, launch mutation,
      execution-quality semantics, and submit-to-cancel survival remain
      authoritative
    - its `next_repair_lane="D. Peak-hours confirmation specimen"` and
      `peak_hours_economic_conclusion_allowed=false` outputs are now historical
      specimen-local conclusions, not the active board call after later watched
      current-code peak-hours evidence
- Post-restoration timing spine hardening is now closed on current proof on
  watched authoritative run `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`:
  - `canonical_paper_validation.json` says:
    - `status=pass`
    - `runtime_classification=VALID_ACTIVE`
    - `highest_passing_stage=paper`
    - `blocking_stage=pilot_live`
    - `promotion_eligible=true`
    - `recommended_next_stage=pilot_live`
  - `time_discipline_audit.json` says:
    - `contract_authority_level=authoritative`
    - `finding_count=0`
    - `sample_count=11`
  - current pushed workspace runtime code fingerprint exactly matches that
    timing-closeout proof:
    - `d546a054cf2b77f6335b9c5461e6c95ecf87737be6366f14d975311878487be7`
  - canonical paper setup-lock config fingerprint on that proof is:
    - `bff661de876116b6c96315aa29280bc7432cab9a2d53b2a004af897fa516c8bd`
  - participation on that watched specimen was:
    - `maker_submits=2`
    - `maker_fills=10`
    - `normal_taker_submit_count=0`
    - `normal_taker_fill_count=0`
    - `recovery_taker_submit_count=0`
    - `recovery_taker_fill_count=0`
- Current committed-and-pushed packetized retightening stack is now preserved
  on branch `consultant/full-snapshot-public-20260402T055838Z` with clean tree
  state:
  - `4128422` `chore: promote BRO authority and archive surfaces into the tracked tree`
  - `6eed0d0` `feat: enforce single-authority paper pathway and timing spine`
  - `f5791be` `feat: harden audit and report truth semantics`
  - `53121bc` `feat: harden runtime execution and lifecycle steel`
- Fresh watched post-restoration contrast specimen
  `6e2826a6-d1bf-4cd5-8d18-2846e86b8db1` proves the current open frontier is
  specimen-grade `pilot_live` authority evidence debt, not runtime integrity
  failure:
  - `canonical_paper_validation.json` says:
    - `runtime_classification=VALID_ACTIVE`
    - `promotion_eligible=true`
    - `highest_passing_stage=none`
    - `blocking_stage=paper`
  - `readiness_gate.json` says:
    - `runtime_findings=[]`
    - `execution_starvation_mode=none`
  - `soak_hardening_gate.json` says:
    - `soak_quote_uptime_too_low:0.047531<min:0.050000`
    - `soak_maker_submits_too_low:2.000000<min:50.000000`
    - `soak_readiness_below_required_stage:required=paper:highest=none:causes=min_quote_uptime_ratio`
  - `maker_selection_authority_audit.json` says:
    - `current_decision_distribution={"submit_rejected":2,"submitted":2}`
    - `blocked_count_by_canonical_reject_reason={}`
  - `maker_zero_submit_root_cause_audit.json` says:
    - `zero_submit_classification=upstream_starvation`
  - `maker_quote_integrity_summary.json` says:
    - `specimen_regime_class=overnight_logic_specimen`
    - `next_repair_lane="D. Peak-hours confirmation specimen"`
    - `peak_hours_economic_conclusion_allowed=false`
- Current live repo runtime code fingerprint exactly matches the clean release
  anchor fingerprint:
  `492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`.
- This exact match is fingerprint-scoped runtime identity, not a blanket claim
  that every proving-path file is byte-identical.
- Latest clean-tree 20-minute wiring run passed canonical paper validation.
- All validator exit codes for the latest clean run were `0`.
- Runtime classification for the latest clean run was `VALID_ACTIVE`.
- Soak hardening gate passed with no findings.
- Runtime resource telemetry was present in status/report/readiness evidence.
- Wallet authority reported authoritative paper wallet contract state.
- Reservation mismatch delta was `0.0`.
- Valuation hard-degraded ratio was `0.0`.
- Held book-not-found 404 ratio was `0.0`.
- Error rows were `0`.
- Stage-reduction delta accounting semantics were clarified in `scripts/nightly_soak_report.py`: primary reduction counters may count event rows and can exceed net decision-to-submit delta.
- `fair_probability_missing` was diagnosed as a maker/taker fair-map scope bug and received a targeted code patch after the latest clean run.
- Post-patch canonical 10-minute paper run `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` passed canonical validation with all validator exit codes `0`.
- Post-patch run `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` proved taker-scope `fair_probability_missing=0`; remaining `fair_probability_missing=13` rows were maker-scoped.
- Maker `size_notional_bounds` / `sizing_reject` was diagnosed as a deterministic floor/cap feasibility constraint: maker hard floor `100.0` USDC, maker hard max `800.0` shares, infeasible below midpoint `0.125`.
- Current-code report replay exposes maker sizing reject rows and min-notional/max-shares conflict rows.
- `soak_maker_submits_too_low` was diagnosed as a maker-submit enforcement taxonomy gap, not a proven maker-execution defect. `ops/soak_budget.yaml` now counts maker-scope `fair_probability_missing` as non-actionable for this enforcement path.
- Current-code `soak_hardening_gate.py` replays of `9d3c3225-13b6-4a12-8dd4-fb51a6d666e6` and `7e0a7dcf-947a-4d88-9f0c-9a6790ed6b69` both passed with `ok=true` and `finding_count=0`.
- Requested 5-minute smoke run `ec26dedd-84ee-4cc9-9f5f-d448ea834f9d` verified packet-1 behavior in runtime: maker submit enforcement required `1` submit and observed `1` submit.
- `reduce_only_recovery_size_cap_unavailable` was diagnosed across the clean anchor, 10-minute proof, and 5-minute smoke artifacts. All local size-cap-unavailable rows were flat/wrong-side no-op local rejects: clean anchor `16/16`, 10-minute proof `2/2`, smoke `1/1`; non-flat/unknown rows `0`.
- `required_book_feed_disconnected_rows=1` was diagnosed across the same three artifacts as startup/bootstrap telemetry: first status row only, `ws_slo_bootstrap_active=1`, no order attempts/actions, connected by the next status row, websocket audit `ok=true`.
- Current-code nightly soak report now emits `reduce_only_recovery` diagnostics so future artifacts can separate flat/wrong-side no-op churn from true non-flat recovery weakness.
- Current-code canonical 10-minute run `7fd90a69-2be3-4aff-9e3d-88c85cf3df77` completed full lifecycle with complete proving lineage:
  - `run_id=7fd90a69-2be3-4aff-9e3d-88c85cf3df77`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=15802c9f8c1aff24a3f21023118ba2b5cbbc741d9b4bd7a6ae9341037ade5912`
  - `code_fingerprint_sha256=e47555513c2fceb215d0e54a04fada1f62d3bef682dd3b441f51f571d12f517b`
  - `profile_name=paper_universal`
- Current-code run `7fd90a69-2be3-4aff-9e3d-88c85cf3df77` proved the repaired harness connector chain:
  - `validator_determinism_ok=true`
  - `edge_truth_determinism_ok=true`
  - `non_edge_determinism_ok=true`
  - `paper_harness_audit.ok=true`
  - `websocket_hardening_audit.ok=true`
  - `edge_truth_audit.ok=true`
  - `order_lifecycle_audit.ok=true`
  - `outcome_truth_audit.ok=true`
- Current-code reconcile for `7fd90a69-2be3-4aff-9e3d-88c85cf3df77` completed with:
  - `status=ok`
  - `mismatch_ratio=0.0`
  - `verification_level=paper_sim_verified`
- Current-code promotion evidence gate for `7fd90a69-2be3-4aff-9e3d-88c85cf3df77` fail-closed only on:
  - `quote_uptime_ratio_too_low:0.047888<min:0.050000`
  - no lineage, manifest, or cross-artifact identity failure was observed.
- Selector-stabilized canonical run `91e232fc-2d6a-46c7-9eed-6027b1a49bc8` passed paper-stage canonical validation with:
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `quote_uptime_ratio=0.19141723517112907`
  - `maker_submits=3`
  - `soak_hardening_gate.ok=true`
- Current canonical selector choke is materially reduced on `91e232fc-2d6a-46c7-9eed-6027b1a49bc8`:
  - `maker_selection_authority_audit.current_decision_distribution={'submit_rejected': 15, 'submitted': 3}`
  - `blocked_count_by_canonical_reject_reason={}`
- Latest current-code canonical 10-minute run `c6019d01-3d4c-45c2-aea8-1c1312b870eb` completed with full proving lineage and clean paper-stage validation:
  - `run_id=c6019d01-3d4c-45c2-aea8-1c1312b870eb`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=852ff08674395b2759983074773d2de700e49518f4d4fcffc5dc8fc1edd460e1`
  - `code_fingerprint_sha256=e47555513c2fceb215d0e54a04fada1f62d3bef682dd3b441f51f571d12f517b`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
- Live under-the-hood diagnostics on `c6019d01-3d4c-45c2-aea8-1c1312b870eb` verified runtime health rather than wrapper-only closure:
  - healthy guardian/maker containers
  - host time-sync surface was unavailable on-host
    (`timedatectl` missing); do not overread this as verified host clock health
  - `0` error-like events in the run event stream
  - authoritative wallet status and fresh book/oracle feeds in live status rows
  - real maker submits/fills observed during the live inspection window
- Current maker submission path on `c6019d01-3d4c-45c2-aea8-1c1312b870eb` is no longer a selector-authority unknown:
  - `maker_selection_authority_audit.current_decision_distribution={'submit_rejected': 3, 'submitted': 3}`
  - `maker_selection_authority_audit.blocked_count_by_canonical_reject_reason={}`
- Historical contrast specimen `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204` proved that the live runtime matched the intended maker-weapon timing ladder on that packet-era run:
  - `run_id=8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=7673ddbf3aa8ebc5feab3f8949bdd0ce8c8fa325245a7dbca29b777f8af61511`
  - `profile_name=paper_universal`
  - `strategy.maker_competitiveness.timing_gate_min_sec_to_expiry=15.0`
  - `strategy.maker_competitiveness.timing_gate_max_sec_to_expiry=20.0`
  - `risk.min_sec_to_expiry_for_new_exposure=15.0`
  - `runtime.held_preexpiry_reduce_only_sec=15.0`
  - `runtime.preexpiry_emergency_taker_window_sec=7.0`
  - `runtime.terminal_unwind_halt_new_risk_sec=7.0`
- Live-under-the-hood canonical 10-minute run `8a389b34-5df7-4d78-a750-3e2b909f17c8` closed the active spinal-cord paper-stage blocker on current code:
  - `run_id=8a389b34-5df7-4d78-a750-3e2b909f17c8`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=604d2932e00f52874a6472760b314f02b43c482385d3a0692f62e1c975d5f861`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `quote_uptime_ratio=0.18873916784343087`
  - `maker_submits=3`
  - `meaningful_participation=1.0`
  - `fill_rows=13`
  - live event/status inspection on the same run showed:
    - `0` runtime occurrences of `token_lag_not_verified_for_maker`
    - `gauge.doctrine_maker_prereq_failure_count=0.0`
    - `leadlag_verified=1.0` on the active trading tokens
    - real runtime fills before the postrun validators landed
- Live-under-the-hood Packet 1 closeout specimen
  `4494f47e-9c0d-4ab0-80a3-141588388446` verifies the host-time semantic fix
  while preserving the release-truth boundary:
  - `run_id=4494f47e-9c0d-4ab0-80a3-141588388446`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=e1f29b2441d9bb6dc5bf3a86ce9773155e3f29b2bc4a0afc41e2570bc0ef5cf6`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - start-phase run contract is intentionally open and authoritative with blank
    fingerprint fields by design
  - postrun run contract closes correctly as observational with manifest-linked
    commit/config/code lineage
  - `start_manifest_observation.json` confirms manifest observation on the same
    run
  - `paper_harness_audit.json` says:
    - `ok=true`
    - `finding_count=0`
  - `soak_hardening_gate.json` says:
    - `ok=true`
    - one residual finding:
      - `soak_maker_submits_too_low:3.000000<min:50.000000`
  - `time_discipline_audit.json` passes on internal timestamp-domain
    consistency, and the host-time authority split is now semantically clean:
    - session-start / session-stop host-time artifacts report synced via
      `timedatectl`
    - rolling status rows stay `available=false` but now report
      `clock_state=partial_visibility`
    - runtime `FileNotFoundError` evidence remains visible without competing
      with host-side authority
- Historical contrast specimen `b0f164e7-5db5-4807-9c2b-1b2f8fa1af3c` remains
  the strongest proof that a latest current-code specimen can fail `paper`
  honestly while lineage still holds end to end.
- Fresh live-under-the-hood Packet 2 red-team canonical 10-minute run
  `d30b1c7c-ab05-494e-bfd1-3c5ac1205051` reconfirmed that the old spinal-cord
  no-participation choke no longer owns current code:
  - `run_id=d30b1c7c-ab05-494e-bfd1-3c5ac1205051`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=604d2932e00f52874a6472760b314f02b43c482385d3a0692f62e1c975d5f861`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `quote_uptime_ratio=0.09598189269320323`
  - `runtime_meaningful_participation=1.0`
  - live doctrine rows in the `15-20s` band carried:
    - `maker_allowed=true`
    - `maker_prereq_ok=true`
  - live event tape contains `0` occurrences of
    `token_lag_not_verified_for_maker`
  - `maker_selection_authority_audit.json` says:
    - `row_count=6`
    - `current_decision_distribution={"submit_rejected":5,"submitted":1}`
    - `blocked_count_by_canonical_reject_reason={}`
  - live status/event counters on the same run showed:
    - `counter.order_submission_accepted_maker=1.0`
    - `counter.order_submission_rejected_local_sizing_reject=5.0`
    - `counter.fills=5.0`
  - `soak_hardening_gate.json` remains `ok=true` with one non-blocking submit
    scarcity finding:
    - `soak_maker_submits_too_low:1.000000<min:50.000000`
- Deeper live-under-the-hood Packet 2 red-team canonical 10-minute run
  `c6b3bba3-a268-4e3e-9f53-3ae134689ca1` hardened the same closure with a more
  exact truth boundary:
  - `run_id=c6b3bba3-a268-4e3e-9f53-3ae134689ca1`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=604d2932e00f52874a6472760b314f02b43c482385d3a0692f62e1c975d5f861`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `quote_uptime_ratio=0.09519777607847149`
  - `runtime_meaningful_participation=1.0`
  - `order_submission_accepted_maker=1`
  - `fill_rows=4`
  - current truth boundary on that same specimen:
    - the old lag blocker is not re-proven as the active owner
    - live status tape can still show intermittent
      `gauge.doctrine_maker_prereq_failure_count=2.0`
    - those spikes coexist with:
      - `gauge.latency_verifier_state=2.0`
      - visible tracked `leadlag_verified=1.0`
    - active `15-20s` maker-allowed rows are mixed:
      - one path reaches authoritative truth and real submit/fill
      - other rows remain truth-thin with:
        - `market_reference_class=not_available`
        - `market_reference_mode=missing`
        - `secondary_oracle_status=unknown`
        - `block_reason=maker_requires_ws_book_source`
      - bounded fallback rows also persist with
        `market_reference_not_authoritative`
  - meaning:
    - Packet 2 closure is real as stable-owner demotion
    - Packet 2 closure is not a license to claim every current maker-allowed
      row is uniformly prereq-clean
- Fresh live-under-the-hood Packet 3 deep red-team canonical 10-minute run
  `13fd56b5-3f12-48ec-a07d-04b7d83d07ac` hardened the rack truth boundary on
  current code:
  - `run_id=13fd56b5-3f12-48ec-a07d-04b7d83d07ac`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=e1f29b2441d9bb6dc5bf3a86ce9773155e3f29b2bc4a0afc41e2570bc0ef5cf6`
  - `profile_name=paper_universal`
  - `status=policy_failed`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `paper_harness_audit` keeps proving lineage honest while surfacing one
    bounded realism-pressure finding:
    - `ok=false`
    - `finding_count=1`
    - `findings=['paper_harness_book_updates_rest_ratio_high:0.393103>max:0.350000']`
    - `proving_lineage_complete=true`
    - `manifest_present=true`
  - descriptive realism remains explicitly bounded:
    - harness audit `harness_realism_grade=100`
    - nightly report `harness_realism_grade=60`
    - both surfaces label the grade `descriptive_non_gating` and
      `non_authoritative`
  - readiness and soak remain aligned with the same paper/pilot frontier while
    surfacing bounded realism pressure:
    - `readiness_gate.metrics.quote_uptime_ratio=0.18949293894558247`
    - `readiness_gate.metrics.runtime_meaningful_participation=1.0`
    - `soak_hardening_gate.findings=['soak_book_updates_rest_ratio_too_high:0.393103>max:0.350000','soak_maker_fill_rate_too_high:1.000000>max:0.850000','soak_maker_submits_too_low:6.000000<min:21.000000']`
  - live runtime tape on the same specimen showed:
    - sustained `cl_connected=true` / `ws_book_connected=true`
    - real maker submits and fills before the postrun validators landed
    - `fills=16`
    - `total_pnl=1024.5362` by the last active status row
- Earlier clean reconfirmation specimen `8666da8e-a76c-45c0-9013-f7542008a4cd`
  remains the lower-pressure comparison anchor:
  - `paper_harness_audit.ok=true`
  - `paper_harness_audit.finding_count=0`
  - `soak_hardening_gate.ok=true`
- Fresh live-under-the-hood Packet 4 contradiction specimen
  `ed44e26c-c52d-4003-9a41-464d5d528ff9` proved the remaining current-code grip
  seam:
  - `wallet_startup_authority_refresh` emitted `order_submit_eligible=true`
  - `wallet_health_gate.allowed=true`
  - `wallet_authorization=3`
  - `order_submit=3`
  - downstream `wallet_state_refresh` / nightly wallet contract still carried
    `order_submit_eligible=false`
- Fresh live-under-the-hood Packet 4 closeout specimen
  `33e30bd8-e416-488e-83ce-f99c8665e7fc` now proves that paper-stage wallet
  authority remains honest while live authority stays fail-closed without the
  old submit-readiness contradiction:
  - `run_id=33e30bd8-e416-488e-83ce-f99c8665e7fc`
  - `git_commit=519f6ed188c7bde92e674512072d34ecc9d0ba1e`
  - `config_fingerprint_sha256=6d4b4950bc89856f619325fbb8ec07fc5cc43ab78eaaf92671f3bd7be19356bc`
  - `code_fingerprint_sha256=492ea0b757623c0a9dade4a333c0bf743dcd05ffbaa8a3c34b24c16a22313ede`
  - `profile_name=paper_universal`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `nightly_soak_report.json` plus live wallet status/event tape keep:
    - `authority_status_class=authoritative`
    - `startup_authority_ready=true`
    - `authoritative_refresh_completed=true`
    - `wallet_health_ok=true`
    - `gas_ok=true`
    - `approval_ok=true`
    - `reconcile_ok=true`
    - `reconcile_scope=integrity_tripwire`
    - `reservation_mismatch_candidate=false`
    - `order_capable_live=false`
    - `order_submit_eligible=true`
    - `canonical_live_nonce_available=false`
    - `canonical_live_pending_wallet_tx_available=false`
  - live status rows keep that corrected contract through the run:
    - `wallet_order_submit_eligible=true`
    - `wallet_open_reserved` returned to `0.0`
    - `wallet_deployable_capital=3996.4366`
    - reservation mismatch remained false throughout
  - live wallet events keep local/derived surfaces fenced:
    - `wallet_health_gate` emitted `allowed=true` with
      `order_submit_eligible=true`
    - `wallet_authorization=1`
    - `wallet_reservation_created=1`
    - `order_submit=1`
    - `fill=3`
    - `wallet_reservation_released=1`
    - `wallet_local_tx_lifecycle_state` stayed local
    - `wallet_open_order_state` stayed derived
    - `wallet_reconcile_result` stayed healthy with
      `reconcile_scope=integrity_tripwire`

## VERIFIED_OPEN
- G-frame restoration is complete on current truth.
- Whole-fighter completion remains open on the post-restoration `pilot_live`
  authority frontier.
- BRO remains paper-stage only.
- Additional watched peak-hours canonical paper specimens are no longer the
  highest-ROI next proving move unless a fresh contradiction appears.
- Current next proving move is bounded `pilot_live` authority prep through the
  clean-tree `prelive_gate` / `live_canary` pathway.
- Latest clean-tree readiness highest passing stage is `paper`; blocking stage is `pilot_live`.
- Current broad current-code specimen `7bbde42c-003a-4f57-b59a-7ce138224075`
  preserves full proving lineage on a clean current-code release anchor while
  still ending at the `pilot_live` frontier:
  - `status=pass`
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `reports_complete=true`
- Historical broad dirty-worktree specimen
  `13fd56b5-3f12-48ec-a07d-04b7d83d07ac` remains important because it closed the
  Packet 6 downstream consumer-truth lane on the older runtime fingerprint
  while still ending `policy_failed` above the rack floor.
  - `determinism_consistent=true`
- Latest current-code passing Brain-closure specimen remains `656c9d42-070c-4f82-84cf-34aa333a9e7f`:
  - `highest_passing_stage=paper`
  - `blocking_stage=pilot_live`
  - `runtime_classification=VALID_ACTIVE`
  - `soak_hardening_gate.ok=true`
  - non-blocking residual finding:
    - `soak_maker_submits_too_low:3.000000<min:32.000000`
- Fresh live 10-minute Brain closure specimen `656c9d42-070c-4f82-84cf-34aa333a9e7f` now proves the source-layer report mutation seam is closed on current code:
  - `maker_cannon_late_window_probe.jsonl`:
    - `row_count=78`
    - `backfill_rows=3`
    - `runtime-vs-report oracle deltas=0`
  - those `3` late-window backfill rows preserve runtime owner truth exactly:
    - `market_reference_class=not_available`
    - `market_reference_mode=missing`
    - `market_reference_basis=missing`
    - `market_reference_source_side=none`
    - `secondary_oracle_status=unknown`
    - `secondary_oracle_confirmation=false`
    - `market_probability=null`
  - `maker_mid_window_probe.jsonl`:
    - `row_count=62`
    - `runtime-vs-report oracle deltas=0`
  - raw event-to-probe replay on the same run confirms no row upgrades runtime
    `missing/not_available/unknown` truth into report
    `authoritative/confirmed`
  - downstream harvest on the same run does not resurrect the old second
    dialect
- Fresh live 10-minute Nervous-system closure specimen
  `13fd56b5-3f12-48ec-a07d-04b7d83d07ac` now proves the downstream consumer
  chain stays one-language on current code:
  - `maker_cannon_late_window_probe_summary.json` preserves identical
    report/runtime secondary-oracle distributions:
    - `{"confirmed":54,"direction_mismatch":5,"unknown":21}`
    - `{"confirmed":54,"not_confirmed":26}`
  - `maker_mid_window_probe_summary.json` preserves identical report/runtime
    secondary-oracle distributions:
    - `{"confirmed":50,"direction_mismatch":10}`
    - `{"confirmed":50,"not_confirmed":10}`
  - `readiness_gate.json` preserves:
    - `highest_passing_stage=paper`
    - `blocking_stage=pilot_live`
    - `runtime_findings=[]`
    - `metrics.runtime_meaningful_participation=1.0`
  - `soak_hardening_gate.json` preserves the same readiness frontier and adds
    only bounded realism / policy findings
  - `paper_harness_audit.json` stays lineage-complete and aligned, with only
    `paper_harness_book_updates_rest_ratio_high:0.393103>max:0.350000`
  - fresh harvest replay bundle
    `logs_exec/paper_universal/metric_harvest/packet6_13fd56b5` does not
    resurrect the old second dialect
- The old lag-verification choke on historical contrast specimen
  `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204` is no longer the active blocker owner
  after `8a389b34-5df7-4d78-a750-3e2b909f17c8`:
  - current runtime event tape for `8a389...` contains `0` occurrences of `token_lag_not_verified_for_maker`
  - current status tape for `8a389...` shows `gauge.doctrine_maker_prereq_failure_count=0.0`
  - current runtime produced real paper participation and fills on the canonical path
- canonical live nonce truth unavailable
- canonical live pending-wallet-tx truth unavailable
- strict order-capable live remains fail-closed
- reconcile is integrity tripwire, not full ledger accounting
- A literal 5-minute canonical session does not satisfy the current canonical 10-minute soak budget; this remains a validation-duration constraint, not a proven bot behavior defect.
- The latest current-code proof is a dirty worktree/code-fingerprint proof, not a clean release anchor.
- The current release-truth model is two-phase:
  - start-phase run contract = authoritative open control surface
  - postrun run contract = observational closure artifact
- Host time-discipline remains internally consistent on the canonical path, and
  the latest Packet 1 specimen now expresses the host/runtime authority split
  cleanly:
  - session-start / session-stop artifacts remain authoritative host truth
  - rolling status rows stay non-authoritative with
    `clock_state=partial_visibility` when runtime `timedatectl` visibility is
    unavailable
- Maker low-price floor/cap adaptation remains a future policy/strategy decision, not a current safety defect.

## INFERRED
- The spinal-cord current-code paper-stage blocker is now materially closed on `8a389b34-5df7-4d78-a750-3e2b909f17c8` and reconfirmed on `1966bf8a-b0e1-401d-8d1e-913e5260f60f` and `d30b1c7c-ab05-494e-bfd1-3c5ac1205051`.
- Brain mutation closure is now materially achieved on current code at the
  source layer, and Nervous-system consumer-truth closure is now materially
  achieved on fresh downstream specimen
  `13fd56b5-3f12-48ec-a07d-04b7d83d07ac`.
- Grip current-code truth closure is now materially achieved on
  `33e30bd8-e416-488e-83ce-f99c8665e7fc`; clean-anchor core-fighter re-audit is
  now complete and the highest-ROI next macro frontier is post-restoration
  `pilot_live` authority proof.
- The latest watched post-restoration contrast specimen
  `6e2826a6-d1bf-4cd5-8d18-2846e86b8db1` keeps that frontier pointed at
  peak-hours confirmation rather than runtime mutation.
- The historical Brain anchors remain important because they prove the defect
  that was actually closed:
  - `8a389...` proved `3` late-window report rewrites from runtime
    `unknown/not_confirmed` into report `authoritative/confirmed`
  - fresh live specimen `7b4cabf6-fa6b-4d97-8ffb-3c7885547822` proves the
    same mutation also hits the duplicated mid-window branch:
    - late-window rewrites: `2`
    - mid-window rewrites: `4`
    - all `6` rewritten rows are labeled
      `market_reference_basis=report_book_top_pair_backfill`
  - fresh live specimen `bc4bc73b-7dd4-4060-b44b-07dc0228aaa3` proved the
    mutation is still live on current code even when the active branch shifts:
    - late-window rewrites: `0`
    - mid-window rewrites: `6`
    - all `6` rewritten rows are labeled
      `market_reference_basis=report_book_top_pair_backfill`
- The bounded source cut in `scripts/nightly_soak_report.py` now removes the
  shared owner-rewrite contract and helper-layer resurrection path without
  introducing a new dialect:
  - probe builders no longer overwrite:
    - `market_probability`
    - `desired_quote_price`
    - `market_reference_*`
    - `secondary_oracle_*`
  - existing descriptive-only backfill visibility remains:
    - `market_reference_backfill_applied`
    - `market_reference_backfill_pair_delta_sec`
  - best-effort market-reference helpers no longer write synthesized
    owner-looking values back into persisted probe or actionability rows
- The stronger executor-vs-shadow split remains unproven on the latest
  evidence:
  - on `bc4...`, maker-scope `edge_evaluation` rows in
    `MAKER_TAKER_SELECTIVE` already carry:
    - `market_reference_class=authoritative`
    - `market_reference_mode=direct_midpoint`
    - `secondary_oracle_status=confirmed`
    - `secondary_oracle_confirmation=true`
    - non-null `secondary_fair_probability`
  - same-time null-oracle duplicates are taker-scope
    `stage_disallow_taker` rows, not a proven maker-scope owner split
  - `maker_fight_admission_shadow` matched the maker-scope rows on this
    specimen
  - the broader executor/shadow owner split therefore remains unproven

## UNKNOWN
- No current artifact-backed UNKNOWN remains in this diagnostic packet. Future runs can still surface new non-flat recovery-cap evidence.

## No-Go Claims
- No live-readiness claim.
- No pilot-readiness claim.
- No strategy-aggression claim.
- No claim that validator pass equals full integration closure.
