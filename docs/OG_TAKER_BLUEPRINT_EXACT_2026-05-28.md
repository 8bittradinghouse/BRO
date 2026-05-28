1. Full Taker / Sniper Blueprint (Energy Sword – 90%+ Selective)
Taker/Sniper Blueprint – 90%+ Win Rate
For BRO – 8bit ODA
Core Doctrine

Highly selective, high-conviction taker only.
Never spray-and-pray.
Dual-oracle confirmation required.
Fail-closed on any uncertainty.

Timing

Final 8–12 seconds of the 5-minute window only.

Decision Rules

Chainlink + Pyth oracles must both agree on direction.
Delta threshold ≥ 0.20 between oracles and current mid-price.
IOC (Immediate-Or-Cancel) orders only.
Flat order size: $150.

Gates (All Must Pass)

Correct regime / time window.
Strong oracle agreement.
Sufficient book depth on the target side.
No daily loss limit breach.
Wallet Guardian approval.

Pro Tips

Use both oracles for confirmation to avoid false signals.
Tighten delta in quieter / overnight hours.
Log every decision + regime for later analysis.
Account for taker fees when sizing small edges.

Common Pitfalls & Fixes

Taking weak oracle signals in low-volume times → tighten delta.
Ignoring book depth → slippage on fill → add depth gate.
Not accounting for taker fees eating small edges → size at $150 minimum.
Over-firing in dead zones → regime filter.

Pseudocode (High Level)
Pythonif time_remaining in [8,12] and dual_oracle_agree(delta >= 0.20) and book_depth_sufficient and gates_pass:
    place_ioc_order(size=150, side=predicted_direction)
else:
    log_skip_reason()
