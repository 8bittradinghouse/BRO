# Taker Sword Blueprint

## Classification
`VERIFIED`:
- this is an external strategy/doctrine input provided by `Robb`, sourced from
  `Grok`, for the taker/sniper lane.
- it is not yet runtime-proven BRO law.
- it should be treated as a high-value external blueprint and challenge input,
  not as automatic canonical authority.
- current taker doctrine and Packet 1 truth should be compared against this
  file; this file must not silently outrank active doctrine without proof.

Plain-English:
this is a serious outside blueprint for the taker sword, not a machine-proven
BRO result yet.

## Exact Grok Text
Everything below is copied from the user-provided Grok text without wording
changes.

90%+ Win-Rate Taker/Sniper Strategy Blueprint
For Polymarket 5-Minute Up/Down Windows (BTC/ETH/SOL/etc.)
Core Philosophy
Fire rarely, but when you fire, it’s almost always a winner.
Target 5–20 sniper shots per hour (even in peak liquidity) with 90–95% win rate on filled orders.
Accept fewer total trades for dramatically higher edge per trade and much lower variance.
Required Stack (Non-Negotiable)

Primary/settlement oracle: Chainlink Data Streams
Secondary fast-confirmation oracle: Pyth Network
Execution: Official Polymarket CLOB API (EIP-712 signed orders)
High-precision clock sync (NTP/chrony)

Exact Decision Flow (Pseudocode)
Every new 5-min window:
while (time remaining > 15 seconds):
wait
while (time remaining > 0):
if (time remaining > 12 seconds): continue   // ultra-tight final window
// Dual-oracle confirmation
delta = abs(Chainlink_price - Pyth_price)
direction_agreement = Chainlink and Pyth both point same way
if (delta < 0.20 OR not direction_agreement):
continue
// Book-depth & liquidity check
if (available_liquidity_at_aggressive_price < required_size * 1.5):
continue
// Regime filter
if (current_time NOT in allowed_peak_windows):
continue
// Risk / exposure gates
if any risk gate is red: continue
// FINAL FIRE CONDITION
place_aggressive_taker_order(size = fixed_size, side = direction, type = IOC)
log: "SNIPER FIRED - delta: X.XX, remaining: Y.Ys, regime: XXX"
break
Calibrated Parameters (Start Here, Tune from Your Own Data)

Minimum delta: 0.20 (range 0.18–0.25)
Timing window: final 8–12 seconds (sweet spot is last 5–8 seconds)
Fixed sniper size: $150 flat (for ~$4k–$5k capital; ~3–4% per trade)
Liquidity multiplier: 1.5× your order size
Allowed windows: USA peak (8 AM – 2 PM Central) + your tested strong Asian blocks only

Common Pitfalls, Hidden Challenges & Pro Tips

Firing too early (15–30s left) → Edge decays very fast. Hard lock to final 8–12 seconds max.
Single-oracle reliance → False signals. Require strong agreement between Chainlink Data Streams + Pyth.
Weak delta (<0.18) → Noise trades. Minimum 0.20 threshold.
No / weak book-depth check → Partial fills + slippage. Require 1.5× liquidity; use IOC and accept partials only if still profitable.
Running in dead/shoulder hours → Noisy, low-quality edges. Hard regime filter + auto-shutdown outside allowed windows.
Clock drift / poor timing → Missing the alpha window. Use NTP/chrony + log clock offset every run (<50 ms target).
Ignoring taker fees & slippage → Turns 90% into breakeven. Size must ensure average win > 2× combined fee + slippage.
Rate limiting / disconnects → Missed opportunities. Implement robust reconnection with exponential backoff.
Poor observability → Impossible to tune thresholds. Log every skipped fire with exact reason (delta, timing, depth, risk, etc.).

Additional Pro Tips for Production Stability

Use IOC (Immediate-Or-Cancel) for sniper orders.
Fail-closed on any unknown state (oracle lag, clock drift, risk data missing) — never default to firing.
Observability is king: every decision (fire or skip) must be logged with full context. This is how you tune without guesswork.
