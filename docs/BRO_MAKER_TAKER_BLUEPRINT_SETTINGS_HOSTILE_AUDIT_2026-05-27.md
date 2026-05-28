# BRO Maker/Taker Blueprint Settings Hostile Audit

## Classification
- `VERIFIED`:
  - this is a read-only settings/doctrine audit of the current active
    `paper_universal` profile against:
    - `docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md`
    - `docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md`
    - `docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md`
  - this file does not mutate runtime law by itself
  - this file does not claim current runtime profitability proof
  - this file is meant to separate:
    - aligned steel,
    - explicit experimental drift,
    - temporary proving detunes,
    - and stale/support-only residue

Plain-English:
this is the brutal map of what matches the blueprint, what does not, and what
is currently living as an experiment or proving compromise.

## Audit Basis
- active profile source:
  - `configs/profiles/paper_universal.yaml`
- effective normalized config loaded through:
  - `prodesk.config.load_execution_config(Path("configs/profiles/paper_universal.yaml"))`
- inherited defaults:
  - `configs/profiles/execution_defaults.yaml`

## Status Legend
- `GREEN`:
  - aligned with blueprint intent strongly enough to keep
- `YELLOW`:
  - partial / bounded / support-only / ambiguous
- `ORANGE`:
  - material mismatch, intentional proving detune, or risky extra behavior
- `RED`:
  - direct contradiction against the present blueprint target or a likely
    high-impact divergence

## Top-Line Verdict
- `VERIFIED`: maker is materially closer to the current blueprint target than
  taker is.
- `VERIFIED`: taker remains intentionally detuned far below the sword proposal
  on shot size and regime posture.
- `VERIFIED`: the current `10s / 5s` timing is an explicit bounded experiment,
  not settled doctrine.
- `VERIFIED`: some old maker support settings still exist in config text even
  though Packet 2D removed them from live maker owner-law.
- `INFERRED`: if current economics are ugly, the first default suspicion should
  not be "the whole profile ignores the blueprint." The stronger read is:
  - maker mostly matches the cannon shape,
  - taker remains in a temporary proving-small configuration,
  - and timing / regime / market-level exposure semantics are more likely to
    explain current live-paper weirdness than pure settings neglect.

## Whole-Lifecycle / Shared Settings

| Surface | Current effective setting | Blueprint target | Grade | Hostile call |
| --- | --- | --- | --- | --- |
| Ownership admission max expiry | `90.0s` | `<=90.0s` lifecycle law | `GREEN` | exact match |
| Ownership admission market age | `60.0s` | `>=60.0s` lifecycle law | `GREEN` | exact match |
| Maker depth admission | `1.45` active with band `[1.45, 1.5]` | `1.5x` lifecycle + maker doctrine | `YELLOW` | very close, but the active lower bound is slightly relaxed |
| Taker aggressive fillability gate | `1.5x` | lifecycle full fillability + sword `1.5x` | `GREEN` | aligned |
| Secondary confirmation required | `true` | lifecycle + both lane doctrines want confirmation law | `GREEN` | aligned |
| Market ownership model | `targets.discovery.max_pairs = 1` | one owned market at a time by default | `GREEN` | aligned |
| Current phase timing | maker `10s`, taker `5s` | lifecycle target `maker@15`, `taker@7` | `RED` | explicit live experiment, not blueprint alignment |

Evidence:
- lifecycle law:
  - [BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md:175)
  - [BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md:339)
  - [BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md](/home/odah/bro/base/docs/BRO_MARKET_LIFECYCLE_BLUEPRINT_2026-05-16.md:357)
- current profile:
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:55)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:73)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:217)
- current experiment warning:
  - [OPEN_LIMITATIONS.md](/home/odah/bro/base/docs/OPEN_LIMITATIONS.md:18)

## Maker Lane Audit

| Surface | Current effective setting | Maker doctrine target | Grade | Hostile call |
| --- | --- | --- | --- | --- |
| Core order size | `100-101 USD` | fixed `$100` | `GREEN` | aligned |
| Bankroll context | `4000 USD` paper start | `$4k-$5k` | `GREEN` | aligned |
| One-sided posture | `one_sided_enabled = true` | one maker side only | `GREEN` | aligned |
| Post-only | enforced globally | `postOnly: true` always | `GREEN` | aligned |
| Timing open | `10s` | `15s` | `RED` | explicit experiment, not blueprint-conformant |
| Taker handoff | `5s` | maker authority until taker `7s` | `RED` | explicit experiment, not blueprint-conformant |
| Visible depth threshold | `1.45-1.5x` with active floor `1.45` | `1.5x` | `YELLOW` | near-aligned but slightly relaxed |
| Secondary oracle requirement | `true` | required | `GREEN` | aligned |
| Hard maker delta threshold | no explicit `0.20` maker fire gate in active settings | require about `0.20` + direction agreement | `ORANGE` | partial; confirmation exists, but the explicit delta doctrine is not current active owner-law |
| Anti-churn per target | same-target and same-target-side prior counts capped at `1` | one maker order per token/window | `GREEN` | aligned enough in current profile |
| Stacked-open-order cap | no explicit `4-6` maker stack cap surface | hard `4-6` | `ORANGE` | not explicitly implemented as a doctrine-facing setting |
| Ride-the-window posture | `maker_replace_min_rest_sec = 3.0` plus live management still possible | once placed, ride the rest of commitment window | `YELLOW` | directionally improved, not a hard full-window ride contract |
| Skip-reason observability | explicit viability / reject logging | exact reason logging | `GREEN` | aligned / stronger |

Evidence:
- maker doctrine target:
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:50)
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:57)
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:64)
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:75)
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:80)
  - [GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md](/home/odah/bro/base/docs/GALAXY_MEGA_MAKER_CANNON_DOCTRINE_PROPOSAL_2026-04-28.md:86)
- current maker settings:
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:34)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:60)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:73)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:84)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:97)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:111)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:163)
- post-only owner:
  - [execution_defaults.yaml](/home/odah/bro/base/configs/profiles/execution_defaults.yaml:474)
- skip / viability observability:
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:65)
  - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:1255)

## Taker Lane Audit

| Surface | Current effective setting | Taker doctrine target | Grade | Hostile call |
| --- | --- | --- | --- | --- |
| IOC execution | `tif = IOC`, `post_only = false` | IOC | `GREEN` | aligned |
| Primary + secondary oracle stack | Chainlink + Pyth enabled | required | `GREEN` | aligned |
| Hard minimum fill ratio | `1.5x` | `1.5x` | `GREEN` | aligned |
| Fixed shot size | `5 USD` | fixed `$150` | `RED` | major proving detune; not blueprint-aligned |
| Taker fire window | lifecycle handoff at `5s` | final `8-12s`, sweet `5-8s`, lifecycle target `7s` | `ORANGE` | inside the sweet-spot floor but materially narrower/later than blueprint |
| Minimum delta | `min_edge = 0.18`, multi-oracle threshold `0.20` | minimum `0.20` hard floor | `YELLOW` | close, but hard fire ownership is not cleanly seated on `0.20` alone |
| Regime whitelist | no hard allowed-window gate | peak + strong Asia only | `ORANGE` | intentionally not adopted; real divergence |
| Fail closed on unknown state | false expiry metadata disallow + secondary confirmation + shared gates | fail closed | `GREEN` | aligned / stronger |
| Clock discipline target | latency verifier `120ms` threshold, no explicit `<50ms` doctrine setting | `<50ms` target | `YELLOW` | timing discipline exists, but not at the blueprint's harder explicit target |
| Observability | explicit taker decision / edge events | log every fire or skip | `GREEN` | aligned / stronger |
| Complement route | extinct in active runtime; direct-only taker path required | not present in blueprint | `RED` | non-acceptable doctrine drift; same-thesis alternate path removed from current code and current-owner truth |

Evidence:
- taker doctrine target:
  - [TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md](/home/odah/bro/base/docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md:41)
  - [TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md](/home/odah/bro/base/docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md:46)
  - [TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md](/home/odah/bro/base/docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md:69)
  - [TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md](/home/odah/bro/base/docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md:77)
  - [TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md](/home/odah/bro/base/docs/TAKER_SWORD_DOCTRINE_PROPOSAL_2026-05-07.md:89)
- current taker settings:
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:73)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:177)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:182)
  - [execution_defaults.yaml](/home/odah/bro/base/configs/profiles/execution_defaults.yaml:158)
  - [execution_defaults.yaml](/home/odah/bro/base/configs/profiles/execution_defaults.yaml:175)
- IOC implementation:
  - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:3129)
  - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:3288)
- prior explicit audit on the regime-whitelist divergence:
  - [BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md](/home/odah/bro/base/docs/BRO_PILOT_LIVE_TRUST_PACKET_1_TAKER_QUALIFICATION_2026-05-06.md:804)

## Support-Only / Residue Audit

| Surface | Current setting | Current owner truth | Grade | Hostile call |
| --- | --- | --- | --- | --- |
| `strategy.execution_quality.min_expected_fill_prob` | `0.045` | Packet 2D removed it from live maker owner-law | `YELLOW` | support/residue only; do not tune as if it still owns maker truth |
| `strategy.execution_quality.max_queue_ahead_size` | `300.0` | Packet 2D removed it from live maker owner-law | `YELLOW` | support/residue only |
| `maker_depth_target_*` | all `0.0` | intentionally neutralized | `GREEN` | good; accessory scaler family is zero-authority now |
| `maker_liquidity_tod_*` | scaler disabled, multiplier `1.0` | intentionally neutralized | `GREEN` | good; no stealth maker TOD scaler currently owns truth |
| `runtime.paper_liquidity_tod_*` | enabled with `0.6` from `02-06 UTC` | proving-harness realism only | `YELLOW` | useful harness realism, not strategy doctrine |

Evidence:
- current profile:
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:20)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:51)
  - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:102)
- support-only / zero-authority ruling:
  - [OPEN_LIMITATIONS.md](/home/odah/bro/base/docs/OPEN_LIMITATIONS.md:370)
  - [PROJECT_TRUTH_STATE.md](/home/odah/bro/base/docs/PROJECT_TRUTH_STATE.md:83)

## Hostile Conclusions
### Green steel
- `VERIFIED`: current market admission law is broadly aligned:
  - `<=90s`
  - `>=60s`
  - secondary confirmation required
  - one-market default ownership
- `VERIFIED`: maker core shape is broadly aligned:
  - one-sided
  - post-only
  - `$100`
  - `1.5x`-ish depth requirement
- `VERIFIED`: taker execution mechanics are aligned:
  - IOC
  - fail-closed posture
  - `1.5x` visible fillability
  - strong observability

### Orange / red wounds
- `VERIFIED`: the active `10s / 5s` timing is live experiment drift versus the
  lifecycle and lane doctrine targets.
- `VERIFIED`: taker size is still radically below the sword doctrine:
  - `$5` current
  - `$150` proposed
- `VERIFIED`: hard taker regime whitelisting is still absent by explicit
  doctrine choice.
- `VERIFIED`: maker does not yet express the external `0.20` oracle-delta law
  as an explicit current settings owner.
- `VERIFIED`: complement-route taker behavior is now seated as extinct current
  behavior; older pro-complement packet language is ancestry only and does not
  own present doctrine.

### Interpretation guard
- `VERIFIED`: this audit does **not** prove the blueprint is correct in final
  economic terms.
- `VERIFIED`: this audit does prove where the current active profile is:
  - already aligned,
  - temporarily detuned,
  - experimentally drifted,
  - or still carrying old/helper residue.
- `INFERRED`: if we retune next, the highest-ROI surfaces are probably:
  1. maker/taker timing return path versus experiment hold,
  2. taker size normalization upward toward doctrine,
  3. regime gating posture,
  4. complement-route / market-level exposure semantics,
  5. explicit oracle-delta ownership.

## No-Change Reminder
- do not treat support-only maker quote-quality settings as live owner-law
- do not talk about `10s / 5s` as if it is already canonical closure
- do not call taker blueprint-conformant while `target_usd = 5.0`
- do not rewrite the blueprint downward just because the current proving
  profile is smaller or later
