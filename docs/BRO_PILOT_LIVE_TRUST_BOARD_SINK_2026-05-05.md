# BRO Pilot-Live Trust Board Sink

## Authority Role
- This is the lane-local current board sink for the `pilot_live` live-trust
  qualification program.
- `docs/JIN_RELOCK_PACK_2026-05-12.md` is the BRO-wide anti-drift relock front
  door that must be loaded before this board is trusted for active pickup.
- It does not replace the whole-fighter truth owners:
  - `docs/PROJECT_TRUTH_STATE.md` as the broad repo truth screen
  - `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
- It owns the live-trust lane state, drift register, ambiguity register,
  contradiction matrix, gate-legitimacy board, closure matrix, and next packet
  call for this macro lane.

## Evidence Lock
- active branch: `consultant/full-snapshot-public-20260402T055838Z`
- latest Packet 1 closeout packet commit: `7bf765ecd8f83cd08955e9bce80d813bbf77d221`
- current tree cleanliness: `dirty / Packet 2 documentation hardening in progress`
- current broad proof anchor: `7bbde42c-003a-4f57-b59a-7ce138224075`
- current timing closeout anchor: `4b60bf3e-63c9-4fb0-a47d-69cfb76216d0`
- current grip closeout anchor: `33e30bd8-e416-488e-83ce-f99c8665e7fc`
- current watched current-tree systems-health anchor:
  `98d7f6c5-bec9-4768-bb06-941079c2ac72`
- current watched current-tree systems-health read:
  - `runtime_classification=VALID_ACTIVE`
  - websocket/CLOB/oracle/lifecycle/timing health = clean
  - policy closeout still failed on maker-only utilization/economic findings
- runtime lineage tuple expectation for new closure claims:
  - `run_id`
  - `git_commit`
  - `config_fingerprint_sha256`
  - `code_fingerprint_sha256`

## Lane Status
- phase: `pilot_live`
- macro lane: `live trust qualification`
- current lane status: `open`
- current active implementation lane: `Packet 2 maker live trust qualification (active timing-authority collision surgery + rerun remeasurement)`
- active Packet 2 entry artifact:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
  - companion board + hardening stack:
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_*_2026-05-10.md`
  - surgery-entry hardening artifact for runtime family cuts:
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_SURGERY_SELF_HARDENING_PACK_2026-05-11.md`
- core-frame restoration dependency: `complete`
- timing spine hardening dependency: `complete`
- watched peak-hours paper confirmation dependency: `materially achieved`
- bounded live hookup dependency: `not yet started`

## Interpretation Law
- `prelive_gate` and `live_canary` are bounded proving tools inside this lane.
- They are not final authority by themselves.
- Final live trust comes from clean doctrine, clean internals, and observed real
  behavior under bounded live hookup.
- Stronger existing doctrine or validator surfaces must not be replaced by a
  weaker surface just because the weaker one is easier to summarize.

## Pilot-Live Severity Lock
This board exists to keep pilot-live work serious enough for capital trust.

Board law:
- false confidence is a production bug
- a cleaner story is not stronger than stronger owner evidence
- support-tool output may guide diagnosis but may not impersonate runtime law
- historical packet wins may justify where to look, but never outrank current
  doctrine/runtime owner truth
- every packet must leave an explicit anti-drift trail strong enough for a new
  thread to relock without inventing philosophy

## Packet Status Board
- Packet 1 `Taker Live / Economic and Firing Trust Qualification`: `closed / bounded-live-test ready`
- Packet 2 `Maker-Live / Economic Trust Qualification`: `active / timing-authority collision surgery`
- Packet 3 `Grip-Live / Wallet Live Trust Qualification`: `sequenced / inactive`
- Packet 4 `Bounded Live Hookup / Controlled Arming Qualification`: `sequenced / inactive`
- Packet 5 `Immediate-Performance Proof`: `sequenced / inactive`

## Authorization Boundary
- Generic weapon tuning: `not authorized`
- Threshold-loosening by momentum: `not authorized`
- Maker and Taker live-trust qualification: `authorized diagnostic proof work`
- Wallet live-trust qualification: `authorized`
- Bounded live hookup after internals are healthy and Packet 3 closes the live
  wallet blocker: `authorized`

## Jin Self-Hardening Status
- current status:
  `current for Packet 2 post-audit truth-owner / proof-semantics closeout and anti-drift relock`
- BRO-wide anti-drift relock front door:
  - `docs/JIN_RELOCK_PACK_2026-05-12.md`
- active packet-local lock card:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
- required packet-local lock card:
  - mission lock
  - doctrine lock
  - authority lock
  - pathology lock
  - semantic lock
  - timing lock
  - economic-meaning lock
  - no-change list
  - false-closure list
  - contradiction compression pass
  - negative-proof pass
  - `GO / NO-GO`
- packet work is `NO-GO` if the rehardening output is thin, stale, or
  contradictory
- packet-local work is also `NO-GO` if the BRO-wide relock controller has not
  established a clean owner map, scorecard, and anti-drift legality call first
- current packet-local verdict:
  - `GO` for truth-owner demotion, proof-language repair, selection-family
    reclassification, diagnostic-surface hardening, support-tool fencing,
    history-only quarantine, and anti-drift closeout hardening
  - materialized board stack now exists for:
    - authority census
    - gate legitimacy
    - doctrine proposal delta
    - support-tool fence
    - history-only demotion
    - small-loss + scar-tissue tracing
    - current-code maker path forensic semantic audit
    - maker timing owner layer
  - current strongest packet-local provisional reads are:
    - strongest small-loss owner candidate = `complete-outcome truth`
    - recurring small-loss family = `not trusted steel / surgery-pile candidate`
    - strongest foundation trace = `market-truth substrate`
    - historical small-loss specimens stayed bad even under direct-midpoint
      decision references, so substrate remains a foundation trace rather than
      the proximate owner of that historical wound family
    - official WS/CLOB substrate transplant is now materially landed and no
      longer the active maker patient
    - canonical lifecycle surgery is now materially landed and no longer the
      active maker patient
    - loudest present runtime choke = maker selection timing-authority
      collision
    - loudest downstream follow-on frictions = queue-depth / fill-probability /
      replace-guard pain after that choke is cut
    - recovery and dust = quiet in current Packet 2 specimen set, but no
      longer treated as keep-now maker steel
    - current doctrine timing chain = maker opens at `15s`, taker handoff at
      `7s`
    - the older `15-20 / 15` watched split is pre-correction ancestry only and
      no longer defines current doctrine
    - current timing support-surface warning:
      - raw runtime preserves `late_window_authority_class`
      - `maker_cannon_late_window_probe.jsonl` does not
      - some authority-closed rows therefore read like generic
        `stage_disallow_maker` rows unless the raw event tape is checked
    - historical packet-era `50-60 / 50` timing posture = ancestry only
  - current surgery patients are now explicit:
    1. `maker selection timing-authority collision`
       - current packet
       - must land before broader maker reread
    2. `surviving maker blockers after timing cut`
       - immediate rerun and remeasurement lane after the timing cut
    3. `support-shadow / probe family`
       - report-side de-fat after the rerun and surviving-blocker reread
  - `NO-GO` for generic tuning or live arming

## Packet 1 Relock Spine
Authority boundary:
- this section preserves the current `pilot_live` packet-entry relock method
- it does not outrank doctrine roots, packet-local owner maps, or stronger
  current-code runtime proof

Carry-forward law from Packet 1:
- restore first, tune later
- runtime truth outranks wrapper-green and convenience summaries
- history explains ancestry only
- support tools may help diagnosis but may not impersonate runtime authority
- each packet must force one owner map before closure

Required relock order for later packets:
1. mission lock and explicit no-change list
2. doctrine-root and current board-owner reload
3. runtime river map:
   - doctrine roots
   - runtime emitters
   - validators
   - reports
   - operator consumers
   - back into the strongest owner
4. surface classification:
   - owner-law
   - valid interlock
   - compatibility bridge
   - historical-only
   - scar tissue
5. contradiction matrix plus strongest falsifier list
6. watched runtime proof with raw event/status/timing/wallet/lifecycle/outcome
   inspection
7. contradiction compression pass and negative-proof pass before closure

Current board use:
- Packet 1 remains the reference restoration method for `pilot_live`
- Packet 2 and later packets inherit the method, not the taker-specific owner
  answers
- Packet 2 and later packets must also inherit the recurring self-hardening
  cadence from the program, not just the initial preload ritual
- maker runtime surgery packets must also force the dedicated surgery
  self-hardening loop multiple times through the packet, not only once at
  entry

## Drift Register
| ID | Drift | Risk | Status |
| --- | --- | --- | --- |
| LT-001 | treating `prelive_gate` green as final live trust | high | open |
| LT-002 | reopening maker/taker as tuning projects before trust proof | high | open |
| LT-003 | letting wallet hookup semantics blur into weapon semantics | high | open |
| LT-004 | letting good paper PnL overrule weak capital-safety or timing truth | high | open |
| LT-005 | letting weak taker participation be dismissed as “strict doctrine” without economic proof | medium | open |
| LT-006 | losing Jin self-hardening / continuity lock during deep packets | medium | open |
| LT-007 | replacing stronger authority surfaces with weaker convenience summaries | high | open |
| LT-008 | letting maker support tooling, schematic convenience, or historical packet wins drift back into current owner-law during Packet 2 | high | open |
| LT-009 | letting historical run-manifest timing posture drift back into current Packet 2 timing doctrine | high | open |
| LT-010 | letting compressed maker wound metrics impersonate canonical small-loss owner truth | high | open |

## Ambiguity Register
| ID | Ambiguity | Why it matters | Status |
| --- | --- | --- | --- |
| LA-001 | maker low raw hit-rate vs acceptable expectancy | live capital safety depends on loss shape, not only hit rate | open |
| LA-002 | taker semantically coherent vs taker economically trustworthy | semantic cleanliness alone does not justify live arming | open |
| LA-003 | wallet can connect vs wallet can be trusted to arm | hookup success is weaker than authority trust | open |
| LA-004 | bounded live hookup vs whole-fighter live readiness | successful canary does not automatically certify clone/scale | open |
| LA-005 | market-truth substrate looks serviceable vs genuinely clean enough for capital trust | upstream truth pollution can fake healthy economics | open |
| LA-006 | taker reads like one doctrine lane on the board but still carries inherited split-brain relay ancestry in runtime semantics | live fire trust cannot close cleanly if one lane still behaves like multiple competing brains | open |
| LA-007 | taker may carry true owner-law, valid interlocks, and obsolete scar tissue from inherited sniper-era lineage | later surgery must not cut muscle while removing bullshit | open |
| LA-008 | Packet 2 maker support surfaces may be genuinely useful while still being non-authoritative | useful tooling must be fenced instead of silently promoted into runtime truth | open |
| LA-009 | queue-pressure historical lineage can still linger in docs, replay, and compatibility seams even after current/live authority was cut | later relocks get dangerous if archive-only residue is misread as present-tense maker steel | guarded |
| LA-010 | historical packet-era maker timing posture can differ across run manifest and repo-default surfaces from the same commit | timing ancestry can be misread as current doctrine if provenance is not explicit | open |

## Contradiction Matrix
| ID | Contradiction | Why it matters | Status |
| --- | --- | --- | --- |
| LC-001 | active lane must authorize diagnostic proof work while still forbidding generic weapon tuning | current board language must not split into two authority dialects | guarded |
| LC-002 | wallet hookup truth must stay weaker than order-capable live truth until canonical live nonce/pending-wallet-tx truth is present | connection success must not be misread as safe arming | open |
| LC-003 | `prelive_gate` / `live_canary` must remain bounded tools, not final authority | gate-green overclaim would recreate the exact drift this lane exists to stop | guarded |
| LC-004 | doctrine roots had disagreed on whether `SNIPER_PRIMARY` is current live taker authority or diagnostic-only staging | source doctrine is now synchronized; runtime/config/report residue still must not drift back to the old owner story | guarded |
| LC-005 | runtime still has a real two-surface stage model, but the active Packet 1 path now names it explicitly as `effective_stage` vs `stage_bucket` instead of leaving `stage` / `raw_stage` to carry ambiguous owner meaning | current owner verdict is keep-now explicit two-surface contract: `effective_stage` is authority truth, `stage_bucket` is lineage truth; the remaining cleanup mass is alias clutter, not unresolved owner identity | guarded |
| LC-006 | BRO had still been blessing non-`TAKER_COMMITMENT` taker stage knobs in config/default/profile surfaces even though canonical live taker authority is narrower | canonical taker stage contract is now source-collapsed to the explicit `TAKER_COMMITMENT` effective lane; remaining residue is runtime/report language, not stage-key authorization | guarded |
| LC-007 | runtime/controller identity had still been split between canonical taker meaning and legacy `sniper_taker` / `sniper_stage_window` ownership surfaces | current code now source-collapses controller, emitted event ownership, effective enable ownership, active-mode telemetry, timing-audit evidence keys, nightly-soak public taker summary/gate posture, operator snapshot/brief/parity metrics, and readiness/profile lane output onto canonical taker surfaces while preserving only bounded compatibility bridges; remaining residue is config-bridge mismatch risk and helper/config namespace ancestry | guarded |

## Gate Legitimacy Board
| Gate | Owner | Allowed decision | Upstream dependency | Status |
| --- | --- | --- | --- | --- |
| `prestart_gate` | startup safety | may block startup on guard/kill-switch truth | storage + latest status row | open |
| `prelive_gate` | live preflight | may certify bounded prelive readiness only | config, secrets, wallet, manifest, backup, timing, runtime, security | open |
| `wallet_health` / submit-readiness | wallet authority | may certify wallet startup authority only | live nonce, pending-wallet-tx, balances, allowances, funder truth | open |
| `time_discipline_audit` | combat timing doctrine | may certify authoritative timing only | host sync, event domains, decision timing, host artifacts | open |
| `websocket_hardening_audit` | transport substrate | may certify websocket/book-feed transport truth only | status rows + websocket ordering/freshness surfaces | open |
| `edge_truth_audit` | market-truth substrate | may certify emitted edge semantics only | edge rows, stage policy, market-reference truth | open |
| `readiness_gate` | runtime posture | may classify readiness/posture only | runtime artifacts + policy | open |
| `soak_hardening_gate` | reliability policy | may classify reliability/policy proof only | readiness, transport, participation, timing | open |

## Closure Matrix
| Lane | Must be true | Required proof artifact | Currently missing |
| --- | --- | --- | --- |
| Taker Live | taker fires or abstains for correct reasons, shows acceptable trust economics, rests on clean market-truth substrate, and is mapped as one coherent doctrine/runtime/report brain from core out through branch/contact surfaces and back into the true owner | `docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md` plus watched runtime evidence | acceptable taker trust economics and closure-grade accepted-fire proof remain missing; current watched proof now cleanly supports abstain legitimacy |
| Grip-Live | hookup truth and order-capable live truth are both clean; Polygon `137`, `POL`, `pUSD` live collateral truth, spender targets, and funder truth are correct; canonical live nonce and pending-wallet-tx truth are present on the approved live path | live-mode preflight / prelive evidence plus explicit wallet live-trust qualification artifact | explicit live-trust qualification verdict and closure of the canonical live nonce/pending-wallet-tx blocker |
| Maker-Live | maker is economically safe enough to arm, with acceptable loss shape, clean lifecycle behavior, clean market-truth substrate, clean semantics sweeps, and clean gate legitimacy | recent watched runtime set plus lane-specific trust audit | explicit live-trust qualification verdict |
| Bounded Live Hookup | real wallet + Polymarket path can be armed in smallest safe mode without new contradictions | live canary / bounded live artifacts | not started |
| Jin Self-Hardening | every packet starts from current doctrine/truth and keeps heartbeat commentary alive | packet-local lock card and rehardening output | not yet recorded on this lane |

## Current Lane Call
- Whole fighter still `Needs Work`.
- `pilot_live` remains the active phase.
- Packet 2 `Maker-Live` is now active in post-surgery maker runtime reread:
  - strongest current small-loss owner candidate:
    - `complete-outcome truth`
  - strongest foundation trace:
    - `market-truth substrate`
  - major completed body repairs now carried forward:
    - official Polymarket WS/CLOB substrate transplant
    - canonical lifecycle surgery
  - latest watched 20-minute system-health specimen:
    - `98d7f6c5-bec9-4768-bb06-941079c2ac72`
    - transport / oracle / lifecycle / timing = healthy
    - policy closeout failed on maker-only utilization/economic findings
  - dominant current maker runtime patient:
    - maker selection timing-authority collision between market-admission
      timing law and maker submit timing law
  - loudest downstream follow-on frictions after that cut:
    - `quote_quality_skip_queue_depth`
    - `quote_quality_skip_fill_probability`
    - `replace_guard_min_rest`
  - current scar-tissue read:
    - queue pressure = cut from current/live authority, preserved only as
      historical-only lineage
    - shared recovery / unwind spine = cut from the current
      runtime/report/doctrine owner stack; remaining residue is compatibility
      archaeology, ignored dead-key support, and historical lineage/docs
    - cancel-only cleanup = intended fail-closed replacement for open unfilled
      orders
  - current doctrine timing chain:
    - maker gate opens at `15s`
    - taker handoff opens at `7s`
    - effective maker new-risk submit window remains `(7.0, 15.0]`
  - historical packet-era `50-60 / 50` maker timing posture belongs to
    dirty-tree run manifests and is quarantined as ancestry only
  - current legal residual order is:
    1. maker selection timing-authority collision
    2. rerun and surviving-blocker remeasurement
    3. support-shadow / probe family de-fat
    4. accessory competitiveness bundle runtime tribunal
    5. small-loss wound family remeasurement after earlier family cuts
  - optional later archaeology packet:
    - recovery / unwind compatibility archaeology + ignored dead-key support
      extinction
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
- Packet 1 `Taker Live` now closes `bounded-live-test ready`:
  - one-brain owner collapse is materially achieved on current code
  - recovery/handoff owner classification is now explicitly keep-now dead power
  - raw-root taker gate-posture replay now truthfully classifies gate-only
    watched specimens instead of flattening them to
    `no_taker_activity_observed`
  - fresh watched rerun `9cad4c71-a317-40e2-ac87-c54234a06aa7` now proves
    end-to-end live `required_min_edge` carry-through on gate-only taker rows
    and keeps the late-window no-fire stack coherent under current doctrine
  - Packet 1 now explicitly maps the sniper-derived taker subtree into:
    - surfaces presumed non-steel until they earn keep
    - bounded compatibility bridge mass
    - shared-frame safety / tactical add-ons
    - likely weak legacy carryover
  - strongest likely later-surgery targets in that map are:
    - `stage_final_window_sec_by_stage`
    - `aggressive_window_enabled` / `aggressive_window_sec`
    - retired stage-local taker threshold leaves
    - retired stage-local taker price/size aggression overlays
    - `stage_priority_enabled`
    - inherited taker helper/config alias mass
  - only surfaces with actual doctrine, runtime-safety, or compatibility proof
    are allowed a keep-now call in that map
  - Packet 1 now adds a dedicated packet-entry hardening ritual through:
    - `docs/EXTREME_ONLY_SELF_HARDENING_PACK_2026-05-08.md`
  - external `Taker Sword Doctrine Proposal` audit now explicitly says the current lane
    matches best on CLOB/IOC execution, fail-closed posture, and
    observability; the current paper packet is temporarily detuned to a fixed
  - fresh watched closeout specimen `6957087b-488e-4bbb-b8b9-1f215b5e33d0`
    now proves:
    - stored validator bundle `ok=true` with all exits `0`
    - maker `1 submit / 2 fills`
    - taker `1 submit / 1 fill`
    - action-row source purity clean on both lanes
    - repaired valuation summary carries event-level bruise windows truthfully
  - this closeout handed off into active `Packet 2 Maker-Live`
  - active Packet 2 maker entry artifact is:
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`
  - active Packet 2 recovery / unwind closeout artifact is:
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_ROOT_DEATH_SURGERY_PLAN_2026-05-13.md`
  - optional later Packet 2 recovery / unwind archaeology artifact is:
    - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_RECOVERY_UNWIND_HISTORY_COMPAT_EXTINCTION_PACKET_2026-05-14.md`
    `$20` taker shot for proving
  - that same external audit now explicitly keeps the inherited taker
    fire-condition subtree under challenge:
    - hard `<=7s` taker commitment lane
    - canonical top-level `taker.min_edge=0.11`
    - `multi_oracle_edge_threshold_abs` as boost-only logic
    - `min_visible_fill_ratio`
    - lack of a hard taker-only peak-window whitelist
  - Packet 1 now takes the stronger null-hypothesis removal posture:
    - inherited taker subtree is not presumed to survive
    - canonical taker runtime now ignores the first-cut dormant overlays:
      - `stage_final_window_sec_by_stage`
      - `aggressive_window_enabled` / `aggressive_window_sec`
      - `stage_priority_enabled`
      - `per_token_cooldown_sec_by_stage`
    - proven safety/live-parity behaviors must survive only by re-homing into
      simpler canonical owners
  - helper/config/event compatibility mass is purge material after migration
  - canonical internal taker ownership is now live in code through:
    - `build_taker_competitiveness_policy(...)`
    - `TakerCompetitivenessEngine`
  - the current first bounded taker-commitment live-behavior slice is now:
    - canonical top-level `taker.min_edge=0.11`
    - no dynamic-size authority on canonical taker target sizing
    - no conviction-owned candidate ranking authority on the canonical taker
      path
    - taker one-sided ws market reference now fail-closes without midpoint;
      bounded fallback is removed from current doctrine/runtime
    - keep-now `multi_oracle_boost_*` while dual-oracle lock remains under
      active proof
  - legacy sniper-family wrapper/event surfaces are removed from current code;
    remaining residue is doc/test/history cleanup, not runtime ownership
  - Packet 1 now explicitly records that current BRO still does **not** provide
    independent concurrent maker/taker authority:
    - the raw `EXTREME_ONLY` bucket still exists, but it no longer owns
      concurrent maker+taker authority at the stage-policy root; it remains
      lineage only
    - current code no longer lets maker timing steal late `EXTREME_ONLY`
      authority back into `MAKER_TAKER_SELECTIVE`
    - `_taker_context()` no longer lets legacy `execution_cutoff_sec` suppress
      true late-window taker reachability
    - taker activation and `_run_taker()` now share one canonical stage-window
      token set
    - raw `EXTREME_ONLY` stage policy is no longer the live owner; current late
      authority now rides explicit runtime fields and effective-stage labels
    - Packet 1 removed taker-driven shared `OrderManager` soft-rate budget
      mutation; final-window taker cadence remains lane-local instead
    - maker-side `tracked_token_cleanup` / orphan cleanup still runs on the
      shared manager before taker executes
    - the misunderstood market-ownership / wait family has now been removed
      from current Packet 1 runtime; maker and taker no longer wait on each
      other through same-market commitment or sibling-market conflict shells
  - the active packet now separates:
    - likely false-authority shells that must be challenged or deleted
    - real safety interlocks that may survive only as explicit
      cross-lane contracts
    - latent subtree leaves are now explicitly called out too:
      - `taker_min_edge`
      - `taker_extreme_edge_mult`
      - `per_token_cooldown_sec_by_stage`
      - `multi_oracle_boost_window_sec`
      - `multi_oracle_boost_enabled`
      - `multi_oracle_edge_threshold_abs`
      - `multi_oracle_target_usd_cap`
      - `min_visible_fill_ratio`
      - `normal_side_policy`
      - `final_window_enabled` / `final_window_sec`
      - `dynamic_size_enabled`
      - `hard_min_enforcement`
      - `conviction_model`
      - `edge_weight` / `latency_score_weight`
      - `dynamic_preview_enabled`
    - generic active subtree leaves are now called out too:
      - `taker_target_usd` / `hard_min_target_usd`
      - `taker_max_orders_per_cycle`
      - `taker_per_token_cooldown_sec`
      - `dynamic_size_edge_start_abs` / `dynamic_size_edge_full_abs` /
        `dynamic_size_target_usd_cap`
      - `price_aggress_bps_max`
      - `multi_oracle_capital_pct_cap`
      - dormant `taker_order_size` under current notional sizing
    - recommended removal ladder is now the balanced path:
      - cut dormant overlays first
      - re-home proven keep behaviors
      - then collapse threshold/boost/liquidity/scoring residue
    - critical removal-order hazards are now explicit:
      - keep top-level `taker.min_edge` as the sole current taker fire gate
      - deleting pacing caps changes fire-burst behavior
      - deleting multi-oracle size caps changes boosted-size authority
      - deleting shot-size owners changes canonical taker geometry
  - bounded-live-test readiness is not earned yet
- The current next macro move is no longer “another generic peak-hours paper
  run.”
- The current next macro move is to finish Packet 2 on the current dirty tree
  by cutting the maker timing-authority collision and then rerunning watched
  proof:
  - keep active pickup routing honest
  - keep the major body-surgery carry-forward honest
  - cut the impossible maker selection timing window
  - then remeasure the real surviving maker blockers before opening smaller
    downstream families
- Packet 1 remains closed `bounded-live-test ready`.
- Packet 3 through Packet 5 remain sequenced and inactive behind Packet 2.
- Every packet in this lane must include:
  - forward semantics sweep
  - reverse semantics sweep
  - contradiction compression pass
  - negative-proof pass
  - combat timing doctrine verification
  - gate legitimacy sweep

## Current Packet Recommendation
Current packet:
- Packet 2 `Maker-Live / Economic Trust Qualification`
- active artifact:
  - `docs/BRO_PILOT_LIVE_TRUST_PACKET_2_MAKER_QUALIFICATION_2026-05-10.md`

Why this is first:
- it is already the active packet and owns the current post-audit truth-owner /
  proof-semantics closeout
- stale pickup and packet-order drift still existed in current-owner docs and
  had to be hardened before broader runtime surgery
- the current next runtime patient still sits inside Packet 2:
  - maker selection timing-authority collision
  - then rerun and surviving-blocker remeasurement
- Packet 1 is already closed `bounded-live-test ready` and now belongs to
  historical closeout truth plus bounded ancestry only for current pickup

Follow-on order:
1. Packet 2 `Maker-Live`
2. Packet 3 `Grip-Live`
3. Packet 4 `Bounded Live Hookup`
4. Packet 5 `Immediate-Performance Proof`
