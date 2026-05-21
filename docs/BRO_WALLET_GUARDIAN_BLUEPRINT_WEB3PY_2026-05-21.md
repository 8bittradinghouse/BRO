# BRO Wallet Guardian Blueprint - web3.py Edition

> Doc Class: `Current Design Owner Candidate`
> Scope: Packet 3 wallet live-trust architecture and implementation target
> Override Rule: this blueprint overrides prior wallet blueprints and wallet-fortress drafts
> Non-Goal: this document does not claim the current repo already implements this shape

## Purpose

This blueprint defines the intended wallet architecture for BRO after the
execution-lane hardening work. It replaces older wallet-blueprint language with
a Python-native `web3.py` mechanics model while preserving BRO-owned guardian
doctrine.

The wallet is not a convenience helper. It is the capital-protection artery for
the entire ODAH loop.

## Big Picture

Every phase of the BRO lifecycle flows through wallet truth:

1. Initialization
2. Pre-trade safety
3. Execution readiness
4. Post-trade reconciliation
5. Background health monitoring
6. Market resolution and redemption
7. Shutdown and recycle

## Core Doctrine

Non-negotiable rules:

- single source of truth for capital movement
- fail-closed on uncertainty
- one dedicated EOA only
- capital safety over speed or opportunity
- all actions logged and auditable

BRO canonical vocabulary stays in force:

- `canonical_live_wallet_truth`
- `order_submit_eligible`
- `canonical_live_nonce_available`
- `canonical_live_pending_wallet_tx_available`
- `wallet_health_ok`
- `wallet_health_reasons`

The mechanics layer may feed these surfaces. It may not replace them as the
capital authority vocabulary.

## Architecture Boundary

The target split is:

- `web3.py` owns on-chain wallet mechanics
- `py-clob-client-v2` continues owning Polymarket CLOB-specific signed order
  creation, order posting, and exchange heartbeat mechanics
- BRO Wallet Guardian owns capital doctrine, guardian truth, fail-closed
  decisions, reconciliation policy, and redemption orchestration

This means BRO does not replace the exchange client with `web3.py`.
`web3.py` becomes the canonical on-chain wallet mechanics provider.

Lifecycle-plane rule:

- CLOB order lifecycle and on-chain wallet transaction lifecycle are separate
  planes
- `submitted to CLOB` through `py-clob-client-v2` means exchange intent
  acknowledged only
- exchange intent acknowledged does not satisfy canonical
  pending-wallet-tx truth
- canonical pending-wallet-tx truth comes only from the approved on-chain
  provider path through `web3.py`
- wallet guardian may record exchange intent, but it must not upgrade internal
  truth to `pending_wallet_tx` until the on-chain provider confirms it

Important authority caution:

- on-chain EOA nonce for approvals / redemption / direct guardian transactions is
  not automatically the same thing as canonical live order-capable nonce truth
- if the active live lane depends on deposit-wallet or relayer-defined truth,
  canonical live nonce and canonical live pending-wallet-tx readiness must still
  come from the approved provider path for that lane
- the wallet guardian must preserve that split instead of collapsing all nonce
  questions into one simpler label

## Library Responsibility Split

`web3.py` owns:

- RPC client creation
- primary and secondary provider transport
- fresh on-chain EOA nonce reads for guardian-managed on-chain transactions
- pending-transaction inspection for guardian-managed on-chain transactions
- gas estimation and pricing
- EOA transaction signing
- transaction broadcast
- receipt waiting
- approval transactions
- redemption transactions
- low-level contract interaction

BRO Wallet Guardian owns:

- capital safety doctrine
- min balance law
- max order notional law
- max total exposure law
- daily loss hard pause
- POL reserve law
- pre-trade safety authority
- fail-closed truth decisions
- canonical wallet health contract
- provider ambiguity policy
- approval-target identity policy
- reconciliation policy
- redemption orchestration policy
- emergency shutdown policy

## RPC Strategy

Primary:

- QuickNode

Secondary / failover:

- Alchemy

Rules:

- both providers configured explicitly
- health tracked separately
- automatic failover with jittered backoff
- latency and error-rate monitoring
- fail closed if no provider path is trustworthy

## Nonce And Gas Doctrine

Nonce:

- fetch fresh on-chain EOA nonce immediately before signing any guardian-managed
  on-chain transaction
- treat local pending state as supportive, not final canonical truth
- do not pretend fresh on-chain EOA nonce alone proves canonical live
  order-capable nonce truth for relayer/deposit-wallet lanes
- fail closed on nonce ambiguity

Gas:

- dynamic gas pricing using `web3.py`
- normal operating multiplier band: `1.2x` to `1.5x`
- bounded spike ceiling: `2.0x`
- gas paid in `POL`

POL reserve doctrine:

- target reserve equivalent: `$20`
- hard fail-closed floor equivalent: `$15`

## Capital Safety Rules

Starting-capital tuning for this blueprint:

- starting capital target: `$2,800` USDC
- minimum USDC balance before trading: `$700`
- max single maker order: `$350`
- max single taker / sniper order: `$150`
- max total exposure at any time: `$1,400`
- daily loss hard pause: `$280`

These are guardian laws, not library settings.

Owner note:

- by this blueprint, `max total exposure` and `daily loss hard pause` are
  wallet guardian laws
- if current BRO still carries those surfaces under the risk owner, that is
  legacy/current-state ownership only
- Packet 3 must migrate those laws explicitly into wallet guardian authority
  instead of leaving them split or implied

## Lifecycle Responsibilities

### 1. Initialization

Primary entry:

- `initialize_wallet_guardian()`

Responsibilities:

- load key material
- instantiate `web3.py` provider set
- verify expected chain and active address
- perform initial authoritative truth sync
- confirm approval-target configuration
- establish canonical wallet health baseline

### 2. Health And Safety

Primary surfaces:

- `get_wallet_health()`
- `pre_trade_safety_check(order)`

Responsibilities:

- balances
- exposure
- daily PnL drawdown
- POL reserve
- approval state
- approval-target identity
- provider health
- provider ambiguity handling
- canonical live nonce availability
- canonical live pending-wallet-tx availability
- startup-authority readiness

### 3. Execution Readiness

Primary surfaces:

- `get_fresh_onchain_nonce()`
- `get_gas_config()`
- `authorize_order_intent(order)`

For BRO, execution readiness is split:

- the wallet guardian authorizes capital and wallet truth readiness
- the Polymarket gateway still performs CLOB order creation and posting
- on-chain transactions such as approvals or redemption use `web3.py`
- a CLOB submission does not upgrade wallet truth to canonical pending-wallet-tx
  state

### 4. Post-Trade Reconciliation

Primary surface:

- `post_trade_reconciliation(receipt_or_fill_event)`

Responsibilities:

- update capital buckets
- update pending / settled state
- verify reservations released or settled correctly
- surface reconcile mismatches as fail-closed defects when material

Boundary:

- reconciliation remains an integrity-tripwire authority, not a full accounting
  ledger truth claim

### 5. Background Monitoring

Primary surface:

- `monitor_wallet_health()`

Cadence:

- every `30s` to `60s`

Responsibilities:

- provider health
- POL reserve
- allowance drift
- approval-target drift
- provider ambiguity drift
- live nonce truth readiness
- pending-wallet-tx truth readiness
- reconciliation tripwire state

### 6. Critical Closeout - Redemption

Primary surface:

- `redeem_winnings(market_id)`

Responsibilities:

- detect resolved market
- identify winning token inventory
- call `redeemPositions()` through `web3.py`
- confirm receipt
- reconcile payout into canonical wallet truth
- log payout as first-class wallet event

Redemption is not optional add-on logic.
It is part of canonical BRO wallet lifecycle closure.

### 7. Emergency

Primary surface:

- `emergency_shutdown(reason)`

Responsibilities:

- pause new trading authority
- perform final reconciliation attempt
- emit final guardian status
- preserve operator-readable reason trail

## Canonical Health Questions

The wallet guardian must answer these every cycle:

- what capital is actually available
- what capital is reserved
- what capital is protected
- what capital is already committed
- whether the wallet is healthy enough for new action
- whether any action would violate doctrine

## Target Runtime Shape

The desired code shape is intentionally simpler than the Nova-era structure:

- one clear wallet guardian owner module
- one `web3.py` mechanics adapter/provider layer
- thin truth translation into BRO canonical wallet surfaces
- no duplicate authority planes
- no wallet mechanics spread casually across unrelated runtime modules

This blueprint does not require all logic to live in exactly one physical file,
but it does require one clear owner surface for wallet guardian authority.

## Explicit Non-Goals

- do not let `web3.py` become the authority brain
- do not let the wallet guardian replace final risk admissibility authority
- do not broaden strategy authority under the label of wallet cleanup
- do not weaken fail-closed semantics in order to simplify the implementation

## Implementation Success Criteria

This blueprint is only honestly landed when all of the following are true:

- `web3.py` is the canonical on-chain wallet mechanics provider
- BRO guardian doctrine remains the owner of wallet truth and capital veto
- canonical live nonce truth is available on live-capable paths
- canonical live pending-wallet-tx truth is available on live-capable paths
- redemption exists as first-class lifecycle logic
- provider failover exists and is monitored
- startup authority and live readiness remain fail-closed
- docs, runtime surfaces, and reports all speak the same wallet truth language
