# BRO Pilot Live Trust Packet 2 Overnight Watcher Audit Memo (2026-05-27)

## Purpose

This memo records the first bounded overnight audit packet where `BRO` paper
runtime was watched against the same live Polymarket BTC `5m` market in real
time.

The core question was not “did the paper harness run.”

The core question was:

- can we now watch `BRO` against the live battlefield closely enough to tell
  the difference between:
  - fake blocker stories,
  - honest abstention,
  - and genuinely intelligent edge expression?

`VERIFIED`: after the watcher hardening landed, the answer is yes.

## Watcher Standard

### Discovery-only specimens

These runs were useful for harness shaping and runtime intuition, but they are
not current owner-truth:

- `8d87570c-220c-4ad5-943b-7b6b964a9724`
- `ae2430ee-6992-4e31-8150-4e8977e5a55a`
- `1eb34754-dbbe-4cdf-b5be-ca48cdfb710b`

Reason:

- watcher hardening was still in motion across:
  - null `owned_market_ref` recovery
  - unique snapshot dir precision
  - best bid / best ask summarization
  - terminal-state stand-down behavior

These runs may still be used as supporting discovery context, but not as sole
owner-law.

### Final watcher-standard specimens

These runs happened after the watcher was hardened enough to count:

1. Clean no-shot / no-condition closeout
   - run: `e99f96ea-f213-482f-a6c0-90d9f43d9320`
   - bundle: `tmp/paper_live_market_audit/20260527T054002Z`
   - result:
     - `conditions_seen = 0`
     - watcher reached `completed` closeout
     - one target-discovery Gamma `500` was recorded without breaking watcher
       shutdown truth

2. Clean attached overnight market audit
   - run: `fe093fad-8cb9-483c-a014-22dd054ad2a7`
   - bundle: `tmp/paper_live_market_audit/20260527T054338Z`
   - result:
     - attached to a real BTC `5m` live market
     - captured public books through maker/taker/resolve/scan
     - clean `completed` closeout

## Final Specimen Findings

### Specimen A: clean no-shot closure

Run:

- `e99f96ea-f213-482f-a6c0-90d9f43d9320`

What happened:

- no `owned_market_ref` was ever acquired
- watcher still reached terminal `completed` state
- final summary recorded `conditions_seen = 0`
- one target-discovery Gamma `500` was recorded during the run

Why it matters:

- `VERIFIED`: the watcher can now tell the truth when no valid market is owned
- `VERIFIED`: we are no longer biased toward only “interesting” trade
  specimens
- `VERIFIED`: a discovery-side Gamma seam can now fail without collapsing the
  watcher closeout story
- `INFERRED`: this gives us a clean starvation / no-shot anchor for overnight
  logic

### Specimen B: final attached overnight market

Run:

- `fe093fad-8cb9-483c-a014-22dd054ad2a7`

Attached market:

- condition:
  `0x0236bf4abf5a60fede908306de0c1e95835262a1b1e5e1330b21b8a229bd1f78`
- slug:
  `btc-updown-5m-1779860400`

#### Live market structure at maker open

At `transition_maker_window`, the public book was already pinned:

- rich side:
  - best bid `0.99`
  - no visible ask
- cheap side:
  - best ask `0.01`
  - no visible bid

This was not a soft interior book.
It was already fully collapsed.

#### BRO behavior

At maker window:

- `VERIFIED`: maker did not submit

At taker window:

- taker decision fired
- fair probability was about `0.50885`
- selected price was `0.01`
- taker submitted `BUY 0.01 size=500`
- taker filled at `0.01`

Then:

- `resolve`
- `scan`
- clean watcher completion

#### Reading

`VERIFIED`: in a fully pinned `0.99 / 0.01` overnight book, `BRO` did not force
a maker shot.

`VERIFIED`: taker then exploited the cheap-side extreme and filled.

`INFERRED`: this is healthy lane separation:

- maker abstains when quoteable structure is gone
- taker remains willing to exploit an extreme late-window edge if the machine
  still believes the edge is real

This specimen does **not** support the story that maker is blindly firing into
dead overnight books.

## Discovery Support

The strongest discovery specimen was:

- run: `1eb34754-dbbe-4cdf-b5be-ca48cdfb710b`
- bundle: `tmp/paper_live_market_audit/20260527T053406Z`

What happened there:

- market was already severely compressed
- maker submitted a rich-side `SELL 0.981`
- maker later filled at `0.99`
- taker later bought the cheap side at `0.01`
- taker filled too

`INFERRED`: this looked less like random confusion and more like complementary
extreme-edge exploitation inside a pinned overnight structure.

Because this happened before the final watcher stop logic was finished, keep it
as discovery support only, not as sole authority.

## What This Changes

### 1. The watcher is now a real owner tool

`VERIFIED`: we can now audit:

- what `BRO` saw
- what the live book looked like
- what each lane did
- whether the move was smart, stupid, or honest abstention

This is a real capability jump.

### 2. Overnight regime is a legitimate proving ground

`VERIFIED`: overnight BTC `5m` structure can already be fully pinned before
maker open.

`INFERRED`: that makes overnight a valid hard-training environment:

- bad selection should be punished
- fake courage should be punished
- good lane separation should be visible

### 3. The lanes may be behaving more intelligently than the old simplified picture

Current live-aligned read:

- `VERIFIED`: maker can abstain in fully collapsed books
- `VERIFIED`: taker can still exploit a late cheap-side extreme
- `INFERRED`: earlier discovery specimens suggest `BRO` may sometimes express
  edge in smarter ways than a naive “one obvious move only” doctrine picture

This does **not** yet authorize doctrine rewrite.
It **does** justify more careful observation before killing unconventional
profitable behavior.

## Guardrails

- do not treat discovery-only watcher specimens as owner truth
- do not overfit doctrine from one attached specimen
- do not kill a surprising move solely because it looks unconventional
- do keep surprising profitable-looking behavior under live-aligned audit until
  it either proves itself or fails cleanly

## Current Best Read

`VERIFIED`: the overnight watcher audit reduced blindness materially.

`VERIFIED`: the clean attached final specimen supports the idea that maker and
taker may be honoring their actual jobs:

- maker abstains when the book is already too gone
- taker exploits a cheap-side extreme when the edge still survives

`INFERRED`: some earlier attached specimens suggest emergent smart behavior
rather than rigid script-following.

`UNKNOWN`: whether every one of those clever-looking moves is long-run steel.

## Immediate Next Uses

1. continue using overnight as a stress harness
2. compare these overnight watcher results against future Asia/crossover/peak
   watcher specimens
3. audit whether pinned-book extreme exploitation is a real repeatable taker
   strength
4. avoid reopening fake maker patients when the live-aligned watcher says the
   lane may already be abstaining correctly
