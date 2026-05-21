# Wallet Guardian Read-Only Mapping - web3.py Cut Plan

> Doc Class: `Reference`
> Scope: read-only Packet 3 architecture comparison
> Authority: planning support only; does not itself authorize implementation
> Basis: new `BRO_WALLET_GUARDIAN_BLUEPRINT_WEB3PY_2026-05-21.md`

## Goal

Map the new wallet guardian blueprint against current BRO code without cutting
runtime behavior yet.

The main question is:

what should stay as BRO guardian steel, what should move to `web3.py`, and what
should be retired as wallet residue?

## High-Level Call

- keep BRO guardian doctrine
- keep `py-clob-client-v2` for Polymarket CLOB-specific order mechanics
- add `web3.py` as canonical on-chain wallet mechanics provider
- replace homemade nonce and pending-wallet canonical gaps
- add first-class redemption lifecycle
- simplify the current wallet package after doctrine-preserving extraction

## Responsibility Map

| Blueprint Responsibility | Current BRO Owner / Evidence | Current State | Call |
| --- | --- | --- | --- |
| Single capital authority vocabulary | `prodesk/wallet/wallet_controller.py`, `prodesk/wallet/wallet_health.py`, `docs/DOCTRINE_RUNBOOK.md` | Present and strong | `KEEP AS STEEL` |
| Fail-closed startup barrier | `wallet_controller.py` startup-authority refresh and veto path | Present and strong | `KEEP AS STEEL` |
| Dual-veto model with risk | `docs/DOCTRINE_RUNBOOK.md` wallet/risk semantic split | Present and strong | `KEEP AS STEEL` |
| Clean single wallet guardian owner surface | `prodesk/wallet/` package split plus compatibility facade | Present but structurally heavy | `SIMPLIFY / RE-SHAPE` |
| Python-native on-chain wallet mechanics | no `web3.py` provider path present | Missing | `ADD / REPLACE MECHANICS` |
| CLOB order creation and posting | `prodesk/gateway.py` uses `py-clob-client-v2` `create_order()` and `post_order()` | Present and valid | `KEEP SEPARATE` |
| CLOB submission lifecycle must stay separate from canonical pending-wallet-tx truth | gateway/open-order state already reflects exchange-plane behavior; canonical pending-wallet-tx still unavailable | Semantically easy to blur | `KEEP SEPARATE / CLARIFY HARD` |
| QuickNode primary + Alchemy failover | no explicit wallet RPC provider pair found in current wallet lane | Missing | `ADD` |
| Fresh on-chain EOA nonce immediately before signing guardian-managed on-chain tx | no `web3.py` mechanics layer present; local tx manager increments `_next_nonce` for local lifecycle only | Weak / incomplete | `REPLACE WITH web3.py` |
| Canonical live order-capable nonce truth on approved provider path | current canonical live nonce remains unavailable; docs/comments indicate relayer or deposit-wallet lane may define the real owner path | Incomplete and semantically separate | `ADD CANONICAL PROVIDER PATH; DO NOT COLLAPSE INTO RAW EOA NONCE` |
| Canonical pending-wallet-tx truth | canonical live pending-wallet-tx unavailable; local tx lifecycle exists | Incomplete | `ADD CANONICAL, KEEP LOCAL SUPPORT` |
| Gas reserve doctrine | `wallet_config.py`, `wallet_controller.py`, `wallet_health.py` | Present in partial form | `KEEP RULES, REWORK MECHANICS` |
| Dynamic gas policy | no first-class wallet gas strategy matching blueprint band | Weak / partial | `ADD / REPLACE` |
| Capital buckets and deployable-capital logic | wallet controller + health contract | Present and valuable | `KEEP AS STEEL` |
| `$2,800` wallet doctrine tuning | current profile values differ (`paper_starting_usdc=4000`, wallet max order around `351`) | Mismatched to new blueprint | `RETUNE LATER` |
| Max total exposure law | currently risk-owned under `risk.global_exposure_guard` | Present in current code but legacy/current-state only under the new blueprint | `PACKET 3 OWNER MIGRATION TO WALLET GUARDIAN` |
| Daily loss hard pause | currently risk-owned under `risk.max_total_loss` | Present in current code but legacy/current-state only under the new blueprint | `PACKET 3 OWNER MIGRATION TO WALLET GUARDIAN` |
| Pre-trade safety gate | `wallet.authorize_intent()` | Present and strong | `KEEP AS STEEL` |
| Reconciliation tripwire | reconcile logic + event emissions | Present and valuable | `KEEP AS STEEL; DO NOT OVER-CLAIM AS FULL LEDGER` |
| Background wallet monitoring | status contract and wallet telemetry events | Present in partial form | `KEEP / SIMPLIFY` |
| Provider ambiguity hardening | `wallet_provider.py` + `wallet_truth_policy.py` required/optional field policy and disagreement tolerances | Present and valuable | `KEEP AS STEEL` |
| Approval-target identity enforcement | config validation + live-mode `approval_spender_targets` checks | Present and valuable | `KEEP AS STEEL` |
| Redemption flow for resolved markets | no first-class redemption implementation found | Missing | `ADD NEW` |
| Emergency shutdown orchestration | halt semantics exist, no dedicated guardian shutdown lifecycle | Partial | `ADD ORCHESTRATION` |
| One dedicated EOA only | current gateway supports broader wallet modes and funder sources | Broader than new doctrine | `NARROW BY DECISION` |

## Strong Current Steel To Preserve

These are the parts of the existing wallet stack that should not be discarded
blindly:

- `canonical_live_wallet_truth` vocabulary
- `order_submit_eligible` and startup-authority readiness semantics
- fail-closed health gate
- capital reservation lifecycle
- reconcile tripwire semantics
- dual-veto relationship with risk engine
- provider ambiguity hardening
- approval-target identity enforcement
- explicit authority-domain split:
  - `canonical_live_wallet_truth`
  - `local_tx_lifecycle_state`
  - `open_order_state`
  - `integrity_tripwire_reconcile_state`

## Mechanics That Should Move To web3.py

These are the strongest candidates to move out of custom BRO plumbing and into
the new `web3.py` mechanics layer:

- RPC provider creation and failover
- fresh on-chain EOA nonce fetch for guardian-managed on-chain transactions
- pending-transaction inspection for guardian-managed on-chain transactions
- gas estimation and dynamic gas pricing
- allowance / approval transactions
- on-chain receipt waiting
- redemption contract calls
- EOA transaction signing and broadcast

## Things That Should Stay Out Of web3.py

`web3.py` should not become the owner of:

- deployable-capital doctrine
- daily loss pause
- max exposure law
- order admissibility authority
- final fail-closed judgment language
- BRO report/status vocabulary

Those remain BRO guardian responsibilities.

## Critical Current Gaps

### 1. Canonical live order-capable nonce truth

Current repo truth:

- `wallet_provider.py` returns `nonce_snapshot_unavailable`
- `tx_manager.py` reserves nonce by incrementing a local counter
- current wallet-provider comments explicitly warn that deposit-wallet /
  relayer lanes may require a different approved truth source

Read:

- current local tx lifecycle support is not the same thing as canonical live
  order-capable nonce truth
- fresh on-chain EOA nonce may be necessary for guardian-managed on-chain
  transactions without being sufficient for the exchange-lane live readiness
  contract

Call:

- replace the on-chain guardian transaction nonce mechanics with `web3.py`
- add an explicit approved canonical live nonce provider path for live
  order-capable readiness when the active lane requires it
- keep local tx lifecycle state only as supportive local context

### 2. Canonical live pending-wallet-tx truth

Current repo truth:

- canonical live pending-wallet-tx remains unavailable
- open orders are explicitly not treated as canonical pending-wallet-tx truth
- `py-clob-client-v2` exchange submissions may acknowledge order intent, but
  they are not canonical on-chain pending-wallet-tx truth

Call:

- add a real canonical pending-wallet-tx provider path
- use `web3.py` for guardian-managed on-chain pending transaction mechanics
- preserve open-order state as separate derived truth
- preserve exchange-intent acknowledgement as separate from on-chain pending
  truth

### 3. Redemption lifecycle

Current repo truth:

- no first-class winnings-redemption path was found

Call:

- add it as a required Packet 3 lifecycle surface

### 4. Wallet RPC strategy

Current repo truth:

- current wallet lane does not express the explicit QuickNode / Alchemy primary
  and failover strategy required by the new blueprint

Call:

- add provider orchestration under the `web3.py` mechanics layer

## Things That Look Old But Are Not Automatically Trash

The current `prodesk/wallet/` folder is structurally heavy, but not all of it
is residue.

Most reusable pieces:

- health-contract logic
- authorization flow
- reservation lifecycle
- reconcile and status surfaces
- truth-domain separation
- provider ambiguity policy
- approval-target identity checks

Most likely to be replaced or collapsed:

- provider layer assumptions tied to gateway balance/allowance only
- homemade canonical nonce handling
- homemade pending-wallet-tx canonical gap
- broader structural sprawl once the new guardian owner and `web3.py` adapter
  exist

## Preliminary Keep / Replace / Delete Split

Keep:

- guardian doctrine semantics
- health gate semantics
- reservation and reconciliation steel
- canonical wallet vocabulary
- risk boundary
- provider ambiguity hardening
- approval-target identity enforcement
- `py-clob-client-v2` for Polymarket exchange mechanics

Replace:

- on-chain guardian transaction nonce provider
- wallet RPC/provider mechanics
- gas strategy mechanics
- approval / redemption transaction mechanics

Add Canonical Provider Paths:

- canonical live order-capable nonce truth
- canonical live pending-wallet-tx truth

Delete Later:

- compatibility-heavy wallet residue that remains only because current
  mechanics are spread across too many modules
- old wallet plumbing once the new guardian and `web3.py` adapter fully cover
  its responsibilities

## Open Design Decisions Before Surgery

1. Should the target runtime shape be:
   - one `wallet_guardian.py` owner plus one `web3_adapter.py`
   - or a very small package with one obvious owner module and one obvious
     mechanics module?
2. How strictly do we narrow to one dedicated EOA in config and gateway paths?
3. Which exact contract surfaces are needed first for redemption and approval?
4. How should `web3.py` provider failover health be surfaced into
   `wallet_health_reasons`?
5. Do `max total exposure` and `daily loss hard pause` remain risk-owned, or do
   we migrate them into wallet guardian authority without breaking existing risk
   veto semantics during the cut?

## Read-Only Conclusion

The new blueprint is compatible with BRO doctrine if we treat it as:

- `web3.py` for wallet mechanics
- BRO guardian for capital truth and fail-closed authority
- `py-clob-client-v2` retained for Polymarket CLOB-specific exchange behavior

This is not a “delete everything and start over” call.
It is a doctrine-preserving mechanics replacement plan with explicit new gaps to
close in Packet 3.
