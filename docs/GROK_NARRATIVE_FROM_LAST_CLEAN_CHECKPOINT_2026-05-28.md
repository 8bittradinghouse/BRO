# GROK Narrative From Last Clean Checkpoint (2026-05-28)

## Anchor Used

This narrative is anchored at the last clean checkpoint that the active BRO
truth surfaces still explicitly name as the safe opening boundary for the
current body of work:

- `3e27630`
- `Lock Packet 3 wallet plan and retire stale lane semantics`

Why I used that anchor:

- `docs/NEXT_PACKET_PLAN.md` says this is the latest clean Packet 3 opening
  checkpoint and that the packet body after it is dirty / in-flight and not yet
  reclosed to a clean commit boundary.
- `docs/PROJECT_TRUTH_STATE.md` repeats that same opening-checkpoint story and
  treats the present work as an active current-code body rather than a cleanly
  reclosed packet.

So the story below is: what happened after the last clean checkpoint, why we
went down those lanes, what we proved, what we changed, and what is still open.

## The Big Why

The work after that checkpoint was not random cleanup and it was not normal
"engineer busywork." It was pilot-live trust construction.

The actual mission has been:

- make BRO trustworthy as a trade desk system
- prove money truth, execution truth, runtime truth, and doctrine truth
- refuse to promote paper behavior if the machine can still fake strength or
  hide failure

That is why the work kept branching into audits, hostile rereads, watcher
tooling, doctrine cleanup, and money-law surgery. Each branch came from a real
seam discovered in runtime, not from abstract curiosity.

## Chapter 1: The Lane Shift Out of Fake Authority and Into Real Battlefield Truth

The earlier cleanup packets had already removed a lot of stale blocker
authority from maker and taker. That changed the nature of the problem.

The question stopped being:

- "is BRO mostly choking on fake readiness, latency, or report semantics?"

and became:

- "now that more of the fake authority is gone, what does the machine actually
  do in live late-window conditions?"

That shift mattered because it moved the lane from semantics cleanup into
product-fit and launch-engineering truth. The remaining work was no longer just
about naming or reports. It was about whether maker and taker were selecting,
submitting, resting, filling, canceling, and settling in a way that deserved
trust.

## Chapter 2: Watcher Era - We Put Eyes on the Real Market

One of the biggest moves after the clean checkpoint was attaching a live market
watcher to the paper run loop.

This changed the game.

Before the watcher, a lot of readouts were internal. We could see what BRO said
it was doing, but we could not always compare that against the actual live book
the bot was looking at.

After the watcher:

- we could inspect live snapshots of the market around the actual decision
  windows
- we could compare submit decisions to the real external book geometry
- we could separate honest abstention from fake blockage
- we could identify pinned-book conditions, dead windows, and misleading
  apparent opportunities

This is where the whole "watcher era" packet body came from. It was not just a
new tool. It became one of the main trust engines for the project.

## Chapter 3: We Discovered That Some "Losses" Were Not Strategy Problems First - They Were Truth Problems

Once the watcher and raw run traces were active, several ugly seams showed up.

### 3A. Startup / supervision / closeout truth was not fully reliable

We found that:

- runs could appear active or complete on one surface and stale-open on another
- session wrappers and containers could get out of sync
- closeout truth could leak or stay falsely open after the actual run path had
  changed
- watcher cadence could continue following stale conditions after BRO itself
  had already returned to scan

This forced work in:

- canonical paper session lifecycle handling
- watcher closeout handling
- deploy startup ordering
- supervision / stop / phase contract truth

The point was not cosmetic neatness. If run state lies, then every later trust
claim is contaminated.

### 3B. Paper fill price truth had a real bug

This was one of the major finds.

We found a live specimen where:

- the order was submitted at one price
- but the fill later got recorded at a much more favorable price

That was not a strategy insight. It was a broken paper fill path.

The root cause was in the resting-order fill logic: the gateway was repricing a
resting order to a later snapshot on fill instead of honoring the posted order
price.

That made some wins look bigger and some losses look smaller than they really
were.

We fixed that. After the fix, later live specimens showed submit price and fill
price matching correctly on actual exercised orders.

That was one of the most important trust corrections in the whole thread.

## Chapter 4: We Ran a Forensic Accounting Audit Instead of Trusting Reports

After the price-path bug surfaced, we stopped assuming the money layer was
sound just because the summaries looked coherent.

We did a hostile, raw-only accounting pass over the watcher-era runs:

- raw fills
- raw settlements
- raw wallet balance changes
- raw status movement
- raw reservation behavior

The first important conclusion was:

- the ledger arithmetic mostly balanced
- but balancing arithmetic was not the same thing as economically correct truth

That led to two major red findings.

### 4A. Runtime and report PnL were fee-blind / economically incomplete

The machine was balancing cashflow, but active runtime truth was not fully
seating the actual fee law and economic adjustment law in the current-owner
money surfaces.

That meant the PnL could be arithmetically clean while still being a distorted
economic picture.

### 4B. Binary short capital semantics were wrong

The machine was treating short exposure too much like normal trade notional
rather than modeling the real gross binary liability correctly.

That matters because in binary markets a short can carry far more liability than
`price * size` suggests. That means capital truth, deployable truth, and loss
truth can all be misread if that model is wrong.

These were not paper cuts. They hit the bloodline of trust.

## Chapter 5: Money-Law Surgery

Once those red findings were proven, we did not stop at diagnosis. We hardened
the money system.

The core money-law packet did the following:

- corrected taker fee law into canonical runtime/report money truth
- moved runtime/report PnL onto cash truth instead of blended semantic guesswork
- kept slippage and adverse selection on attribution surfaces instead of letting
  them mutate wallet cash incorrectly
- changed short capital truth to gross binary liability rather than `price *
  size`
- fail-closed maker rebate cash to `0` in canonical runtime truth until exact
  payout truth exists
- split raw provider lock truth from interpreted BRO lock truth
- made reservation mismatch semantics-aware instead of pretending all lock
  surfaces meant the same thing
- added fee-authority resolution precedence rather than relying on stale or
  ambiguous defaults

That was then hostile-audited again.

The follow-up packet cleaned the remaining orange/yellow semantic residue:

- current-owner money language moved off ambiguous `*_notional` vocabulary
- active wallet truth now uses explicit lock fields
- cash-adjustment sign law was unified
- fee authority became explicit, ordered, and doctrine-safe
- emit surfaces were hard-cut to the new current-owner semantics, with legacy
  compatibility kept only at parse boundaries

The point of the two-step money packet was:

- first, fix the real blood loss
- second, make the language and audit surfaces hard enough that we can keep
  proving the bloodline later

## Chapter 6: We Reopened Maker and Taker Against the Original Blueprints

Once the money surfaces were materially healthier, the next trust question was
not "can we make it look coherent?" It was "are maker and taker even operating
according to the original intended doctrine?"

The user reposted the original maker and taker blueprints.

We did two things:

1. preserved exact OG copies word-for-word as exact replica docs
2. ran a hostile audit of the active maker and taker lanes against those OG
   blueprints

That audit showed clear drift.

Important drift examples:

- maker size was not OG size
- taker size was not OG size
- timing bands had drifted
- hard regime law was not fully seated
- edge thresholds were not fully seated as first-class owner law
- complement-route behavior existed on taker, which was not acceptable against
  the doctrine

That led to another key decision:

- restore the active machine to OG-tight blueprint behavior everywhere we could
- preserve only the deliberate size exceptions that the user explicitly wanted:
  maker around `$100`, taker `$25`

So we tightened both lanes end-to-end around the OG doctrine:

- `8-12s` timing band
- stronger edge law
- regime gating
- direct-path taker doctrine
- one-side maker doctrine
- lifecycle and gating alignment

This was not nostalgia. It was a controlled attempt to remove strategy drift so
we could test the actual thesis more honestly.

## Chapter 7: Complement Route Was Extincted

One especially important doctrine call happened during the taker reread.

We found that taker still had a complement-route behavior family:

- it could express a thesis through the opposite token path instead of through
  one canonical direct path

From a general software point of view that is not always inherently bad, but in
BRO it was unacceptable because:

- the doctrine is direct-path and single-op
- alternate equivalent routes create semantic ambiguity
- they blur exposure truth
- they make auditing and debugging much harder

So we cut it out of the active system:

- runtime
- config
- event payloads
- current-owner reports
- current-owner docs

We kept historical ancestry readable, but complement route stopped being valid
current-owner behavior.

That mattered because it simplified taker truth and removed one major source of
"smart-looking but semantically muddy" behavior.

## Chapter 8: The Watcher-Era Runs Showed That Some Windows Were Just Trash

After the OG-tight alignment and the watcher tooling were both active, we went
back through the recent 20-minute and 10-minute runs and asked a harder
question:

- what actually happened when BRO entered pinned or near-pinned windows?

That audit was very revealing.

We found:

- hard-pinned maker entries were bad or useless
- hard-pinned maker buys often just expired with no fill
- hard-pinned maker filled sells lost
- hard-pinned taker behaved more like a skewed lottery profile than a clean
  90% selective sword

That led to the right conclusion:

- the machine was still allowed to participate in garbage windows that should
  have been non-actionable by doctrine

This was not just a theory problem. It lined up directly with real recent
losses.

## Chapter 9: Garbage-Window Surgery and Same-Market Double-Expression Surgery

Once the pinned-window read was clear, we cut the escape hatches.

### 9A. Garbage-window exclusion

We made hard-pinned and near-pinned windows fail closed for both lanes.

The goal was simple:

- if the geometry is clearly trash, BRO should not get to participate just
  because some narrower sub-gate still says the edge is technically positive

This was about stopping bad participation before submit, not explaining it away
later in reports.

### 9B. Same-market double-expression suppression

We also had real evidence that maker had been expressing the same market in
stupid ways:

- entering one side
- then later lighting up the opposite side in the same market

Those were not smart "paired" plays. They were semantically ugly and
economically confusing.

So we added same-market single-expression pruning:

- one market
- one maker expression
- weaker complementary candidates get pruned

This was an important doctrine and risk cleanup, not just a cosmetic behavior
change.

## Chapter 10: Validation Runs Became Part of the Engineering, Not an Afterthought

After each major packet, we kept running watched paper sessions and inspecting
raw events rather than trusting tests or summaries alone.

These runs were used to validate:

- money path truth
- price path truth
- pinned-window exclusion
- same-market expression suppression
- watcher closeout behavior
- canonical session/run-contract truth
- fee-authority and wallet emit surfaces

That is how several more truths emerged:

### 10A. We proved a real thesis loss without a math lie

In one key live specimen, maker bought a token cleanly at one submitted price,
filled at that same price in exact chunks, and then the token settled worthless.

That mattered because it proved:

- the accounting path was clean
- the fill-price path was clean
- the loss was a real bad bet, not a money corruption artifact

That is painful but valuable truth. It narrows the patient back to selection
and thesis quality.

### 10B. We proved the garbage-window packet was helping

Later watched runs showed:

- hard-pinned windows getting blocked instead of entered
- no reappearance of the earlier dumb same-market double-maker behavior on the
  watched blocked specimens

That was important evidence that the garbage-window packet was doing real work,
not just changing labels.

## Chapter 11: The Hour-Run Found a Different Escape Hatch - The CLI Was Killing the Wrapper

The most recent long run introduced a different kind of problem.

We launched an hour-long watched paper run expecting:

- the watcher to stay attached
- the canonical paper session to supervise the full envelope
- the run to stop cleanly at the intended end

Instead, we found that the top-level `broctl` path had its own generic
subprocess timeout:

- `1800s` / 30 minutes

That meant:

- the canonical paper wrapper got killed at ~30 minutes
- the underlying containers kept running
- BRO kept taking later windows underneath
- canonical session truth and run-contract truth stayed falsely active

This was not a strategy issue. It was a supervision envelope bug.

We traced the root cause to `prodesk/cli.py` and fixed it so canonical paper
runs no longer inherit that generic timeout. Canonical paper sessions now own
their own lifecycle instead of being pre-killed by the front door.

This matters because every future long validation run depends on that envelope
being real.

## What This Whole Thread Actually Did

If Grok needs the short high-accountability version, it is this:

1. We moved from fake-authority cleanup into real live-truth qualification.
2. We added watcher-based external market truth so BRO could be judged against
   the actual live book.
3. We found and fixed a real paper fill-price bug that had been flattering some
   results.
4. We ran a hostile forensic accounting audit and found real money-law defects.
5. We corrected money truth, short liability truth, fee truth, wallet lock
   truth, and current-owner money semantics.
6. We re-audited maker and taker against the original blueprints and tightened
   the active machine back toward OG doctrine, except for deliberate size
   overrides.
7. We extincted taker complement-route behavior because it violated the direct
   single-path doctrine.
8. We proved that hard-pinned garbage windows were a real disease and added
   fail-closed geometry exclusions for both lanes.
9. We cut same-market double-maker expression so BRO stops doing semantically
   dumb multi-side plays in one market.
10. We kept validating all of this on watched live specimens instead of trusting
    tests or wrappers alone.
11. The latest long-run validation found a separate supervision envelope bug in
    `broctl`, and we patched that too.

## What We Know Now

Things we are materially more confident about than before this thread:

- money truth is much harder and cleaner than it was
- fill-price path is materially healthier and has post-fix specimen evidence
- complement route is no longer muddying taker truth
- garbage pinned windows are much harder for the machine to enter
- same-market double-maker expression is much tighter
- watcher + raw event inspection is now a first-class trust tool

Things still not fully closed:

- we still need fresh post-fix long-run proof that the hour-envelope now closes
  cleanly under the `broctl` timeout fix
- top-level fee-authority status emit still needs to be fully truthful on all
  current-owner surfaces
- some old money-language residue is still leaking in status fields
- the remaining front-line question is more about selection / thesis quality
  than hidden arithmetic corruption

## Why This Matters Before Moving Forward

The whole point of the thread was not to make the repo prettier.

It was to convert a system that could still fake strength into one that can be
interrogated and trusted under pressure.

That is why the path looked messy:

- watcher work
- accounting audit
- doctrine rereads
- garbage-window surgery
- complement excision
- envelope supervision fixes

Those were not random side quests. They were discovered obligations on the road
to a trustworthy pilot-live desk.

## Current Best Read for Grok

The desk is in a much stronger state than it was at the last clean checkpoint,
but the current body is still in-flight and not yet reclosed to a clean commit
boundary.

The main open battlefield question is no longer:

- "is the system secretly lying about money everywhere?"

It is more:

- "with money truth and doctrine much cleaner, are the remaining loser
  specimens real thesis/selection failures, and can the machine now stay out of
  garbage windows long enough to show genuinely selective strength?"

That is the story of what happened after the last clean checkpoint.
