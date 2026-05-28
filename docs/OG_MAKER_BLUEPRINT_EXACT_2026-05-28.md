2. Full Maker Blueprint (Galaxy Mega Maker Cannon – 95%+ Selective)
Galaxy Mega Maker Cannon Blueprint
Primary and Only Maker Strategy
Core Doctrine

One-side maker only (no both-sides spread chasing).
Extremely selective, high-quality maker.
95%+ win-rate target (maker losses hurt more).
PostOnly orders only.

Timing

Final 8–12 seconds of the 5-minute window only.

Decision Rules

Dual-oracle confirmation (Chainlink + Pyth).
Delta threshold ≥ 0.20 in our favor.
Book-depth safety check: at least 1.5× order size resting liquidity on the target side.
Stack limit: maximum 4–6 open maker orders.

Order Size

Fixed $350 per side (one side only).

Gates (All Must Pass)

Approved market window.
Strong oracle agreement in our favor.
Sufficient resting liquidity (1.5× order size).
No exposure or daily loss breach.
Wallet Guardian approval.

Pro Tips

Use book-depth check to avoid being picked off.
One-side only keeps risk lower and win-rate higher.
Stack limit protects capital in thin books.
Log every decision + fill rate per regime.

Common Pitfalls & Fixes

Placing both sides → adverse selection → enforce one-side only.
Ignoring book depth → low fill rate or bad fills → enforce 1.5× liquidity gate.
Over-stacking in thin books → enforce stack limit.
Not tightening gates in dead zones → add regime filter.

Pseudocode (High Level)
Pythonif time_remaining in [8,12] and dual_oracle_agree(delta >= 0.20) and book_depth >= 1.5 * order_size and gates_pass:
    place_postonly_maker_order(size=350, side=predicted_direction)
else:
    log_skip_reason()
