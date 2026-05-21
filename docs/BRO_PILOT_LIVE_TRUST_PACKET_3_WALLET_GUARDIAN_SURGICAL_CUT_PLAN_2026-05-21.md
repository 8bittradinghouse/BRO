# BRO Pilot-Live Trust Packet 3: Wallet Guardian Surgical Cut Plan

> Doc Class: `Current Planning Owner`
> Scope: Packet 3 `Grip-Live / Wallet Live Trust Qualification`
> Authority: implementation planning only; this doc does not itself claim
> Packet 3 closure

## Purpose

Turn the finalized Packet 3 wallet doctrine lock into one canonical
implementation lane.

This is the point after the three-way pass where planning stops branching.
From here forward, Packet 3 should build from one settled story:

- `web3.py` is the canonical on-chain wallet mechanics provider
- `py-clob-client-v2` remains the Polymarket CLOB exchange client
- BRO Wallet Guardian remains the owner of capital doctrine, fail-closed truth,
  and wallet live-trust decisions

## Canonical Lock Inputs

This cut plan is downstream of these locked planning surfaces:

- `docs/BRO_WALLET_GUARDIAN_BLUEPRINT_WEB3PY_2026-05-21.md`
- `docs/WALLET_GUARDIAN_READONLY_MAPPING_2026-05-21.md`
- `docs/BRO_PILOT_LIVE_TRUST_QUALIFICATION_PACKET_PROGRAM_2026-05-05.md`
- `docs/OPEN_LIMITATIONS.md`

## Packet 3 Non-Negotiables

The implementation must preserve these truths:

- CLOB order lifecycle and on-chain wallet transaction lifecycle are separate
  planes
- `submitted to CLOB` is exchange intent acknowledged only
- exchange intent acknowledged does not satisfy canonical
  pending-wallet-tx truth
- canonical live nonce truth is owned by fresh on-chain EOA reads through
  `web3.py`
- canonical live pending-wallet-tx truth is owned by on-chain pending / mempool
  state through `web3.py`
- Wallet Guardian owns:
  - max total exposure law
  - daily loss hard pause
  - capital safety veto
  - wallet health truth
  - approval-target identity
  - reconciliation tripwire semantics
- Risk Engine may keep a transition mirror / veto during migration, but Wallet
  Guardian becomes the primary owner for those two laws in Packet 3

## Recommended Target Shape

To minimize blast radius while removing Nova-era sprawl, Packet 3 should land
in the existing wallet package instead of inventing a new top-level family.

Recommended physical shape:

- `prodesk/wallet/guardian.py`
  - one obvious owner surface for wallet authority
- `prodesk/wallet/web3_adapter.py`
  - one obvious mechanics surface for on-chain provider, nonce, gas, broadcast,
    receipt, approval, and redemption operations
- keep and simplify current helper modules where they already carry steel:
  - `wallet_health.py`
  - `wallet_reconcile.py`
  - `wallet_reservations.py`
  - `wallet_truth_policy.py`
  - `wallet_types.py`
- preserve `prodesk/wallet_doctrine.py` as compatibility facade until residue
  deletion is safe

Why this shape:

- preserves current import stability
- avoids new top-level scatter
- keeps one obvious owner and one obvious mechanics plane
- gives residue somewhere clear to collapse toward

## Explicit Non-Goals

- no maker / taker / sniper strategy retuning
- no generic aggression or threshold loosening
- no substitution of CLOB exchange submission for on-chain wallet truth
- no live secret hookup yet
- no Packet 4 bounded live arming work inside Packet 3
- no weakening of fail-closed semantics in order to simplify the code shape

## Packet 3 Slice Plan

### Slice 0. Doctrine lock and routing sync

Goal:

- freeze the architecture story before code migration starts

Required output:

- blueprint locked
- mapping locked
- cut plan locked
- top pickup surfaces point to Packet 3 cut-plan readiness

Proof ring:

- `tests/test_operator_docs_canonical.py`
- `git diff --check`

### Slice 1. Introduce the new owner surface without changing behavior

Goal:

- create `prodesk/wallet/guardian.py` as the obvious owner surface
- keep behavior delegated to existing helpers initially

Required work:

- define the owner interface and constructor boundary
- preserve canonical wallet status vocabulary
- preserve startup-authority and fail-closed semantics
- keep compatibility facade stable

Target files:

- `prodesk/wallet/guardian.py` new
- `prodesk/wallet/__init__.py`
- `prodesk/wallet_doctrine.py`
- existing helper modules only as import rewiring demands

Proof ring:

- `tests/test_wallet_tx_doctrine.py`
- `tests/test_wallet_health.py`
- targeted `tests/test_execution_stack.py`

### Slice 2. Add `web3.py` mechanics adapter and provider health model

Goal:

- introduce one clear mechanics plane without arming live trading

Required work:

- create `prodesk/wallet/web3_adapter.py`
- define QuickNode primary / Alchemy failover contract
- implement provider health, latency tracking, and sticky failover rules
- surface provider problems into canonical wallet health reasons

Target files:

- `prodesk/wallet/web3_adapter.py` new
- `prodesk/wallet/wallet_health.py`
- config surfaces for provider URLs and thresholds

Proof ring:

- `tests/test_wallet_provider.py`
- `tests/test_wallet_health.py`
- config validation tests in `tests/test_execution_stack.py`

### Slice 3. Hard-separate exchange intent from on-chain wallet pending state

Goal:

- make the two lifecycle planes impossible to blur in code, comments, and
  status

Required work:

- preserve open-order / CLOB submission state as exchange-plane truth only
- preserve on-chain pending-wallet-tx as wallet-plane truth only
- ensure comments, event names, and status fields do not treat them as
  interchangeable
- keep `order_submit_eligible` semantics honest

Target files:

- `prodesk/wallet/guardian.py`
- `prodesk/wallet/wallet_tx_state.py`
- `prodesk/wallet/wallet_types.py`
- `executor.py`
- reporting surfaces only where naming needs correction

Proof ring:

- `tests/test_wallet_tx_doctrine.py`
- `tests/test_execution_stack.py`
- `tests/test_nightly_soak_report.py`

### Slice 4. Replace homemade canonical nonce and pending gaps

Goal:

- remove homemade local-counter authority from canonical wallet truth

Required work:

- fetch fresh on-chain EOA nonce through `web3.py` immediately before
  guardian-managed on-chain transactions
- implement canonical on-chain pending-wallet-tx provider path
- keep local tx lifecycle state supportive only
- ensure strict live readiness stays fail-closed until canonical fields are
  truly available

Target files:

- `prodesk/wallet/web3_adapter.py`
- `prodesk/wallet/guardian.py`
- `prodesk/wallet/wallet_provider.py`
- `prodesk/tx_manager.py`

Proof ring:

- `tests/test_wallet_provider.py`
- `tests/test_wallet_tx_doctrine.py`
- `tests/test_wallet_health.py`

### Slice 5. Migrate exposure and drawdown law into Wallet Guardian

Goal:

- move blueprint-owned laws to the correct owner without losing current risk
  protection

Required work:

- migrate max total exposure law into wallet guardian authority
- migrate daily loss hard pause into wallet guardian authority
- keep Risk Engine mirror / veto during transition
- preserve clear failure reasons and report semantics

Target files:

- `prodesk/wallet/guardian.py`
- `prodesk/risk.py`
- config surfaces and doctrine docs

Proof ring:

- `tests/test_preflight_and_risk.py`
- `tests/test_wallet_tx_doctrine.py`
- targeted `tests/test_execution_stack.py`

### Slice 6. Harden approval doctrine and POL reserve law

Goal:

- make approval / gas / reserve law first-class under the new guardian

Required work:

- bounded approval targets only
- re-check allowance before every trade window
- ambiguous or insufficient allowance = halt + alert
- POL target/fail-floor logic with conservative interpretation
- dynamic gas band implementation

Target files:

- `prodesk/wallet/guardian.py`
- `prodesk/wallet/web3_adapter.py`
- `prodesk/wallet/wallet_health.py`
- config validation surfaces

Proof ring:

- `tests/test_wallet_health.py`
- `tests/test_wallet_provider.py`
- targeted config tests in `tests/test_execution_stack.py`

### Slice 7. Add first-class redemption lifecycle

Goal:

- make winnings redemption a real Packet 3 wallet lifecycle surface

Required work:

- detect resolved markets
- identify winning inventory
- call `redeemPositions()`
- confirm receipt
- reconcile payout into canonical wallet truth
- emit first-class wallet event trail

Target files:

- `prodesk/wallet/guardian.py`
- `prodesk/wallet/web3_adapter.py`
- possibly a small helper for CTF contract interaction
- report/event surfaces as needed

Proof ring:

- new redemption-focused wallet tests
- `tests/test_wallet_health.py` where status contract changes
- targeted reporting tests if new wallet event surfaces land

### Slice 8. Simplify residue after the new path is proven

Goal:

- collapse the old structural sprawl only after the new owner plane is proven

Required work:

- retire replaced nonce/pending mechanics
- collapse redundant provider assumptions
- preserve compatibility facade only as long as it still carries real callers

Guard:

- do not delete steel during the same slice that proves the replacement for the
  first time

Proof ring:

- wallet and execution targeted rings
- `git diff --check`
- import-surface sanity checks

## Target Test Rings

Minimum recurring ring while Packet 3 is in flight:

- `tests/test_wallet_health.py`
- `tests/test_wallet_provider.py`
- `tests/test_wallet_tx_doctrine.py`
- `tests/test_preflight_and_risk.py`
- targeted wallet / live-readiness cases in `tests/test_execution_stack.py`
- `tests/test_operator_docs_canonical.py` whenever routing/docs move

Secondary ring when report/status semantics move:

- `tests/test_nightly_soak_report.py`

## Top Risks To Avoid

1. Blurring exchange-plane and on-chain wallet-plane truth
2. Letting `web3.py` become the authority brain instead of the mechanics layer
3. Migrating exposure / loss law in a way that silently weakens current risk
   veto protection
4. Over-claiming reconciliation as full ledger truth
5. Accidentally opening Packet 4 live-hookup behavior while Packet 3 is still
   fail-closed

## Packet 3 Closure Standard

Packet 3 is not honestly closed until:

- wallet live-trust doctrine and runtime surfaces match
- canonical live nonce truth is real on the approved path
- canonical live pending-wallet-tx truth is real on the approved path
- CLOB submission and on-chain pending state remain explicitly separated
- Wallet Guardian owns max total exposure and daily loss hard pause
- approval-target identity and provider ambiguity hardening remain fail-closed
- redemption exists as a first-class lifecycle path
- strict order-capable live can be stated cleanly for the approved tested path

## Immediate Next Move

Open with Slice 1, not with live hookup:

1. create the new owner surface
2. preserve current steel through compatibility
3. add the `web3.py` mechanics plane
4. then migrate the real live-truth gaps under proof

That keeps Packet 3 surgical, doctrine-clean, and honest.
