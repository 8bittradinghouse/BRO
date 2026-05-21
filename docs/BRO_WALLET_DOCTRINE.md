BRO WALLET DOCTRINE

Here’s the wallet build at the same seriousness as your central-authority doctrine.

What “wallet module” means for BRO

This is not just:

connect MetaMask
hold USDC
send orders

It is a capital-control system.

Its job is to answer, every single cycle:

what money is actually available
what money is reserved
what money is protected
what money is already committed to open risk
whether the bot is healthy enough to place another order
and whether any action would violate doctrine

So the wallet module is really:

capital truth + transaction discipline + pre-trade authority gates

The standard we want

At BRO quality, the wallet module should be:

1. Canonical

There is one source of truth for wallet state used by the bot.

Not:

executor guessing from RPC
order path using a different balance view
some script reading raw wallet balance
another part subtracting reserves differently

One canonical wallet state object.

2. Fail-closed

If wallet truth is unclear, BRO does not place risk.

Examples:

RPC stale
nonce unclear
approval state ambiguous
POL gas below reserve
reconcile mismatch too large
unexpected asset in wallet
pending tx state unresolved

Result:
stand down, don’t wing it.

3. Reconciled

The wallet module must constantly reconcile:

on-chain balances
internal deployable capital
open order commitments
fills/settlements
gas reserve
protected reserve / treasury reserve

If those don’t line up closely enough, BRO does not pretend everything is fine.

4. Segregated

Not all funds in the wallet are equal.

You need explicit buckets.

The actual buckets

For BRO, I’d define these at minimum:

A. Gas reserve

POL held back for chain operations.

Purpose:

never strand the bot without gas
never let deployable capital calculation eat gas budget

Fields:

gas_token_symbol
gas_balance_raw
gas_balance_usd_est
gas_reserve_min
gas_reserve_target
gas_ok

Rule:
If gas falls below minimum, no new trading actions.

B. Protected reserve

Capital that exists in the wallet but is not deployable.

This is your “do not touch” pool.

Could be:

emergency reserve
operator-defined protected capital
starting reserve
future treasury buffer

Fields:

protected_usdc_reserve
protected_usdc_locked

Rule:
This is excluded from deployable capital.

C. Deployable capital

The money BRO is actually allowed to use for new trades.

This is the key number.

Formula, conceptually:

deployable = wallet_stable_balance - protected_reserve - open_risk_reserved - unsettled_commitments - required buffers

Not just:
wallet balance = available

Fields:

stable_balance_total
open_order_reserved
open_position_reserved
unsettled_cash_delta
deployable_capital
deployable_ok
D. Open-risk reserved capital

Capital already spoken for by:

resting orders
partially filled orders
open exposure that still consumes headroom

This is the part many amateur bots mess up.

BRO must not double-spend capital just because the wallet still visually shows the balance.

E. Treasury / non-trading separation

Even if you use one wallet at first, architect for two logical zones:

active trading capital
treasury / reserve capital

Eventually this should probably become two actual wallet addresses or at least one active wallet plus one reserve wallet.

Why:

keeps BRO from accidentally trading money that should be safe
makes reconciliation and human reasoning much cleaner
The core responsibilities of the wallet module
1. Balance truth

Track:

POL balance
pUSD balance
with any `USDC.e` treated as pre-wrap or operator-funding ancestry only
any stray assets
balances by raw units and normalized decimals

This sounds basic, but it must be exact.

Need:

token decimals handling
normalized quantity conversion
canonical token identity mapping
2. Allowance / approval truth

Before live orders, BRO must know:

is token approval already in place?
to which contract/spender?
for how much?
does approval need refresh?
is approval state ambiguous?

Rules:

no blind repeated approvals
no assuming approval succeeded
approval actions logged as first-class wallet events
approval state included in wallet health
3. Nonce / tx discipline

This is big.

The module must own transaction sequencing truth:

current nonce
pending txs
replacement tx handling
stale pending detection
retry policy
timeout/escalation policy

If nonce state is unclear:
fail closed

Otherwise you get:

duplicate sends
stuck txs
accidental replacement chaos
broken reconciliation
4. Pre-trade wallet health gate

Before any live order, the wallet module should answer one binary question:

Is the wallet healthy enough for a new trade right now?

This gate should check:

RPC reachable and fresh
gas above reserve
stable balance known
approval state valid
no unresolved nonce ambiguity
no reconcile mismatch beyond threshold
deployable capital > minimum
no wallet kill-switch active

Output:

wallet_health_ok: true/false
wallet_health_reasons: [...]

This should be as serious as doctrine gating.

5. Order funding / reservation

When BRO decides to place an order, the wallet module should:

estimate required capital
reserve it internally before submit
track whether submit succeeded
release reservation on cancel/failure
convert reservation into exposure/settlement state after fill

This prevents phantom available balance.

6. Reconciliation engine

This is the heart.

At regular intervals and after meaningful events:

compare internal view vs chain/account view
compare reserved capital vs actual open orders
compare expected stable balance vs actual stable balance
detect drift

Need thresholds:

tiny drift: log only
moderate drift: warning / stand down
severe drift: kill switch / operator attention
7. Kill-switch integration

Wallet module should be able to trigger or honor:

no-new-orders
cancel-resting-orders
flatten-risk mode
freeze-trading mode

If wallet integrity breaks, it must be able to escalate.

The architecture I would build
A. wallet_types.py

Pure data models.

Examples:

TokenBalance
WalletState
WalletHealth
CapitalBuckets
ApprovalState
NonceState
ReservationRecord
ReconcileResult

Keep these clean and explicit.

B. wallet_config.py

Schema and validation for:

gas reserve levels
protected reserve
token addresses
decimals
spender/approval targets
reconcile thresholds
wallet health thresholds

No magic constants scattered around.

C. wallet_provider.py

Thin chain/RPC access layer.

Responsibilities:

fetch balances
fetch allowances
fetch nonce
query tx receipts/status
normalize chain reads

No doctrine here. Just data access.

D. guardian.py / wallet_controller.py

Build and enforce canonical wallet state.

Responsibilities:

combine provider reads
normalize assets
compute capital buckets
expose one canonical wallet health/truth contract
enforce fail-closed guardian law

This is the “source of truth” layer.

E. wallet_reservations.py

Order/exposure reservation logic.

Responsibilities:

reserve capital before submit
update reservations after submit/fill/cancel
release stale reservations safely
prevent double counting
F. wallet_health.py

Pre-trade health gate.

Responsibilities:

evaluate wallet readiness
emit reasons
produce a binary go/no-go state
G. wallet_reconcile.py

Reconciliation engine.

Responsibilities:

compare expected vs actual
classify mismatch severity
produce reconcile events
optionally trigger stand-down
H. wallet_controller.py

Top-level orchestrator.

Responsibilities:

refresh wallet state
expose canonical wallet snapshot
answer “can we trade?”
provide deployable capital to executor/order path
integrate with kill switches and telemetry

This is the central authority layer for wallet truth.

The exact outputs I’d want every cycle

Every cycle, the wallet module should be able to emit or expose:

gas_balance
gas_reserve_min
gas_ok
stable_balance_total
protected_reserve
open_reserved
deployable_capital
approval_ok
approval_target_identity_verified
approval_spender_targets_matched
approval_spender_targets_required
nonce_ok
reconcile_ok
wallet_health_ok
wallet_health_reasons

That way the rest of BRO never has to guess.

What events/logs I’d want

At BRO quality, wallet events should be first-class.

Examples:

wallet_state_refresh
wallet_health_gate
wallet_reservation_created
wallet_reservation_released
wallet_reservation_settled
wallet_approval_check
wallet_approval_alert
wallet_nonce_state
wallet_reconcile_result
wallet_integrity_warning
wallet_integrity_fail_closed
wallet_redemption_requested
wallet_redemption_failed
wallet_redemption_completed

These should be boring, explicit, and easy to audit.

Redemption must also stay fail closed:

- live redemption is not complete just because an executor says "successful"
- settlement only applies after a confirmed receipt
- `wallet_redemption_receipt_unconfirmed` must fail without mutating wallet settlement

The biggest mistakes to avoid
1. Treating wallet balance as deployable capital

Amateur mistake.
Balance is not deployable.

2. Letting executor infer wallet truth on its own

No.
Executor asks wallet controller, wallet answers.

3. Mixing approval logic into trade logic

Keep it separate and explicit.

4. Shadow nonce handling

One owner of nonce truth.

5. Not reserving capital before submit

That leads to double-counting and messy failures.

6. Weak reconcile discipline

If reconcile mismatch appears, do not shrug.

7. Too much cleverness

The wallet module should be boring and brutally explicit.

The phased build I’d use
Phase 1 — Canonical wallet truth

Build:

balances
gas reserve
protected reserve
deployable capital
wallet health snapshot

No live orders yet.

Phase 2 — Reservations and approvals

Build:

approval truth
reservation lifecycle
pre-submit capital locking

Still mostly controlled testing.

Phase 3 — Nonce / tx handling

Build:

pending tx tracking
replacement discipline
receipt/timeout handling
Phase 4 — Reconciliation + fail-closed behaviors

Build:

reconcile engine
severity thresholds
stand-down behavior
Phase 5 — Full integration into BRO order path

Now let executor use wallet controller as real authority.

What “good enough for live” would mean to me

Before live, I’d want to be able to say:

wallet state is canonical
deployable capital is not guessed
approval state is explicit
gas reserve is protected
nonce state is owned and traceable
reservations prevent double use of capital
reconciliation catches drift
wallet health can veto trading
failures fail closed
all of the above are logged and testable

That is the wallet build at BRO doctrine level.
