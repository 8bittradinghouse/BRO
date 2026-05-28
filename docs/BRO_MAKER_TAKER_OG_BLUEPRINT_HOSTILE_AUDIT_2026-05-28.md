# BRO Maker/Taker OG Blueprint Hostile Audit

## Classification
- `VERIFIED`:
  - this is a fresh hostile audit of the **current active** maker and taker
    lanes against the exact OG blueprint replicas:
    - [OG_TAKER_BLUEPRINT_EXACT_2026-05-28.md](/home/odah/bro/base/docs/OG_TAKER_BLUEPRINT_EXACT_2026-05-28.md:1)
    - [OG_MAKER_BLUEPRINT_EXACT_2026-05-28.md](/home/odah/bro/base/docs/OG_MAKER_BLUEPRINT_EXACT_2026-05-28.md:1)
  - this audit is read-only
  - this audit does not mutate runtime law
  - this audit is against the **active machine**, not later blueprint descendants
- `VERIFIED`:
  - active owner surfaces used:
    - [paper_universal.yaml](/home/odah/bro/base/configs/profiles/paper_universal.yaml:1)
    - [executor.py](/home/odah/bro/base/executor.py:3314)
    - [executor.py](/home/odah/bro/base/executor.py:5427)
    - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:614)
    - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:718)
    - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:1919)
    - [order_manager.py](/home/odah/bro/base/prodesk/order_manager.py:3138)
    - [risk.py](/home/odah/bro/base/prodesk/risk.py:447)
    - [risk.py](/home/odah/bro/base/prodesk/risk.py:755)
  - live behavioral specimen used:
    - run `6e514a60-f195-4f4f-8423-b3a777b984b3`
    - [market_1 order-submit snapshot](/home/odah/bro/base/tmp/paper_live_market_audit/20260528T032000Z/market_1_btc-updown-5m-1779938400/snapshot_20260528T032451031142Z/summary.json:1)
    - [market_2 order-submit snapshot](/home/odah/bro/base/tmp/paper_live_market_audit/20260528T032000Z/market_2_btc-updown-5m-1779938700/snapshot_20260528T032951547077Z/summary.json:1)

## Status Legend
- `GREEN`:
  - strongly aligned with the OG blueprint
- `YELLOW`:
  - partial / adjacent / support-aligned but not exact
- `ORANGE`:
  - material drift or incomplete ownership
- `RED`:
  - direct contradiction against the OG blueprint

## Top-Line Verdict
- `VERIFIED`:
  - current active maker is **not** OG-maker conformant
  - current active taker is **not** OG-taker conformant
- `VERIFIED`:
  - the biggest hard drifts from the OG originals are:
    - maker size: `100` active vs `350` OG
    - taker size: `5` active vs `150` OG
    - taker timing: `5s` active vs `8-12s` OG
    - no active hard regime filter on either lane
    - no active enabled daily-loss threshold on either lane
    - no explicit live owner-law seating the `0.20` threshold as the maker
      fire floor, and only partial seating of it for taker
- `INFERRED`:
  - current maker is best described as a later proving-era one-sided late
    maker lane, not the original `Galaxy Mega Maker Cannon`
- `INFERRED`:
  - current taker is best described as a later proving-small direct-only taker
    lane, not the original `$150 Energy Sword`

## Shared Board Truth

| Surface | Active truth | OG expectation | Grade | Hostile call |
| --- | --- | --- | --- | --- |
| Chainlink live oracle | enabled | required | `GREEN` | aligned |
| Pyth secondary oracle | enabled | required | `GREEN` | aligned |
| Secondary confirmation owner | `require_secondary_oracle_confirmation = true` | dual-oracle confirmation required | `GREEN` | aligned |
| One-market ownership | `targets.discovery.max_pairs = 1` | not explicitly stated, but high selectivity is compatible | `GREEN` | aligned with selective doctrine |
| Daily loss hard limit | `risk.max_total_loss = null`, `risk.max_loss_per_token = null` | no daily loss limit breach | `RED` | guard family exists but active thresholds are off |
| Wallet Guardian submit law | real owner surface exists | required | `GREEN` | aligned |
| Hard regime whitelist | none found in active owner law | both OGs explicitly call for regime/time gating | `RED` | absent |
| Late-window timing owner | maker `10s`, taker `5s` | both OGs say final `8-12s` only | `ORANGE` overall | maker partly inside, taker outside |

## OG Taker Audit

### Core Doctrine

| OG taker doctrine item | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Highly selective, high-conviction taker only | `max_orders_per_cycle = 1`, fail-closed posture, one-market ownership | `YELLOW` | mechanically selective, but shot size and regime looseness mean it is not the full OG sword shape |
| Never spray-and-pray | one order per cycle, direct-only taker path, same-token sell blocked | `GREEN` | aligned |
| Dual-oracle confirmation required | active owner law requires secondary confirmation | `GREEN` | aligned |
| Fail-closed on uncertainty | active taker blocks on stale or missing oracle/book truth and on forbidden same-token short route | `GREEN` | aligned |

### Timing

| OG taker timing item | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Final `8-12` seconds only | active taker handoff opens at `5s` | `RED` | direct contradiction |

### Decision Rules

| OG taker rule | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Chainlink + Pyth must agree on direction | yes | `GREEN` | aligned |
| Delta threshold `>= 0.20` between oracles and current mid-price | live taker owner floor is `taker.min_edge = 0.18`; `0.20` exists in competitiveness as `multi_oracle_edge_threshold_abs` | `ORANGE` | partial only; `0.20` is not the sole live fire authority |
| IOC orders only | taker intents are `IOC`, `post_only = false` | `GREEN` | aligned |
| Flat order size `$150` | `taker.target_usd = 5.0` | `RED` | direct contradiction |

### Gates

| OG taker gate | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Correct regime / time window | no hard regime whitelist; taker time window is `5s` | `RED` | absent / contradictory |
| Strong oracle agreement | confirmation yes, but hard `0.20` owner law not fully seated | `YELLOW` | partial |
| Sufficient book depth on target side | `taker_min_fill_ratio = 1.5`, `min_visible_fill_ratio = 1.5` | `GREEN` | aligned |
| No daily loss limit breach | active threshold disabled | `RED` | absent in live authority |
| Wallet Guardian approval | active guardian submit law exists | `GREEN` | aligned |

### Pro Tips / Pitfall Fixes

| OG taker advisory | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Use both oracles for confirmation | yes | `GREEN` | aligned |
| Tighten delta in quieter / overnight hours | no explicit overnight taker delta tightening found | `RED` | absent |
| Log every decision + regime for later analysis | decision logging strong; regime-specific logging not seated as explicit owner law | `YELLOW` | partial |
| Account for taker fees when sizing small edges | recent money-law surgery makes taker fee truth stronger, but active size is still `$5`, not OG `$150` | `ORANGE` | economics cleaner, doctrine still violated |
| Tighten delta in low-volume times | not found | `RED` | absent |
| Add depth gate to stop slippage | present | `GREEN` | aligned |
| Size at `$150` minimum to survive fees | active size is `$5` | `RED` | direct contradiction |
| Over-firing in dead zones → regime filter | not found | `RED` | absent |

### Pseudocode Fit

| OG taker pseudocode element | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| `time_remaining in [8,12]` | `5s` | `RED` | contradiction |
| `dual_oracle_agree(delta >= 0.20)` | confirmation yes, hard floor partly `0.18` | `ORANGE` | partial |
| `book_depth_sufficient` | yes | `GREEN` | aligned |
| `gates_pass` | mixed because regime and daily-loss gates are not seated | `ORANGE` | partial |
| `place_ioc_order(size=150, side=predicted_direction)` | `IOC` yes, `size=5` no | `RED` | direct contradiction |

## OG Maker Audit

### Core Doctrine

| OG maker doctrine item | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| One-side maker only | `one_sided_enabled = true` and live specimen showed one actual submit only | `GREEN` | aligned |
| Extremely selective, high-quality maker | late-window, one-market, depth-gated structure exists | `YELLOW` | selective scaffolding exists, but live specimen still posted into fully pinned `0.99 / 0.01` books |
| `95%+` win-rate target | no settings surface can prove this | `UNKNOWN` | target only, not a settings truth |
| PostOnly orders only | maker path is post-only and non-aggressive | `GREEN` | aligned |

### Timing

| OG maker timing item | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Final `8-12` seconds only | active maker window opens at `10s`; live submits happened around `9.7s` | `GREEN` | aligned on actual entry timing |

### Decision Rules

| OG maker rule | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Dual-oracle confirmation | yes | `GREEN` | aligned |
| Delta threshold `>= 0.20` in our favor | no explicit active maker `0.20` fire floor found; `one_sided_edge_threshold_abs = 0.15` is not the same contract | `RED` | direct doctrine drift |
| Book-depth safety check `>= 1.5x` | active band `[1.45, 1.5]`, floor `1.45` | `YELLOW` | near-aligned but relaxed |
| Stack limit `4-6` open maker orders | non-aggressive per-token open-order cap is `4` | `YELLOW` | effect is close, but it is a generic risk cap, not a maker-front-door stack contract |

### Order Size

| OG maker size item | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Fixed `$350` per side | active maker target is `100`, cap `101` | `RED` | direct contradiction |

### Gates

| OG maker gate | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Approved market window | max expiry `90s`, market age `60s`, maker timing gate `10s`, but no hard regime approval filter | `YELLOW` | partial |
| Strong oracle agreement in our favor | confirmation yes, but explicit `0.20` in-favor owner law absent | `ORANGE` | partial |
| Sufficient resting liquidity `1.5x` | active `1.45-1.5x` band | `YELLOW` | near-aligned but relaxed |
| No exposure or daily loss breach | exposure guard yes, daily-loss threshold no | `ORANGE` | partial |
| Wallet Guardian approval | active guardian submit law exists | `GREEN` | aligned |

### Pro Tips / Pitfall Fixes

| OG maker advisory | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| Use book-depth check to avoid being picked off | depth gate exists | `YELLOW` | present, but live specimen still posted in brutally pinned books |
| One-side only keeps risk lower and win-rate higher | enforced | `GREEN` | aligned |
| Stack limit protects capital in thin books | generic open-order cap of `4` exists | `YELLOW` | partially aligned |
| Log every decision + fill rate per regime | decision logging strong; explicit regime-facing maker fill-rate doctrine not seated as owner law | `YELLOW` | partial |
| Enforce one-side only to stop adverse selection | yes | `GREEN` | aligned |
| Enforce `1.5x` liquidity gate | active floor is `1.45` | `YELLOW` | slightly relaxed |
| Enforce stack limit in thin books | partial through generic risk cap | `YELLOW` | partial |
| Not tightening gates in dead zones → add regime filter | no hard regime filter found | `RED` | absent |

### Pseudocode Fit

| OG maker pseudocode element | Active truth | Grade | Hostile call |
| --- | --- | --- | --- |
| `time_remaining in [8,12]` | active maker at `10s` | `GREEN` | aligned |
| `dual_oracle_agree(delta >= 0.20)` | confirmation yes, hard `0.20` owner law no | `RED` | contradiction |
| `book_depth >= 1.5 * order_size` | active `1.45-1.5x` | `YELLOW` | near-aligned but relaxed |
| `gates_pass` | mixed because regime and daily-loss gates are incomplete | `ORANGE` | partial |
| `place_postonly_maker_order(size=350, side=predicted_direction)` | post-only yes, one-side yes, size no | `RED` | direct contradiction |

## Live Behavioral Specimen Notes
- `VERIFIED`:
  - the fresh Asia-regime watcher run `6e514a60-f195-4f4f-8423-b3a777b984b3`
    produced two maker submits and zero taker submits
  - both maker submits were:
    - `BUY`
    - `size = 100.51`
    - `price = 0.984`
  - both markets were already pinned:
    - one token bid-only around `0.99`
    - complement ask-only around `0.01`
- `INFERRED`:
  - this does **not** prove the current maker is broken
  - it **does** show that the live machine is not behaving like a classic OG
    `$350` mega-cannon with an explicit `0.20` in-favor front door

## Bottom-Line Map

### Green steel
- `VERIFIED`:
  - maker one-side only
  - maker post-only
  - taker IOC-only
  - dual-oracle confirmation family
  - wallet guardian submit-law family
  - taker depth gate

### Major OG drifts
- `VERIFIED`:
  - maker size is radically below OG
  - taker size is radically below OG
  - taker timing is outside the OG window
  - no hard regime filter is active on either lane
  - no enabled daily-loss threshold is active on either lane
  - maker lacks an explicit active `0.20` front-door fire threshold
  - taker only partially seats `0.20` as active owner-law

### Powwow-ready decisions
- `INFERRED`:
  - the cleanest discussion points after this audit are:
    1. whether to move maker back toward OG `$350`
    2. whether to move taker back toward OG `$150`
    3. whether taker timing should be restored to the OG `8-12s` band
    4. whether both lanes should get an explicit hard regime filter now
    5. whether the `0.20` threshold should be seated as first-class owner-law
       for maker and taker instead of living partly in side surfaces
    6. whether daily-loss thresholds should be activated before more serious
       trust qualification

## No-Change Reminder
- do not treat later modified blueprints as if they outrank these OG files in
  this specific audit
- do not call current maker OG-conformant while it is still a `$100` lane
- do not call current taker OG-conformant while it is still a `$5` lane
- do not blur support-adjacent code leaves into active doctrine just because
  the words exist somewhere in the repo
