# BRO G-Frame Core-Restoration Packet Program, Rehardening-Gated

## Summary
This version hard-locks the core-restoration packet program to the persisted
BRO-local rehardening gate instead of leaving the QRF behavior implicit.

Supporting BRO-local control doctrine:
- `BRO_CANONICAL_DOCTRINE.txt`
- `BRO_EDGE_DOCTRINE.txt`
- `BRO_OUTCOME_TRUTH_DOCTRINE.txt`
- `BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt`
- `docs/DOCTRINE_RUNBOOK.md`
- `docs/BRO_WALLET_DOCTRINE.md`

Board stays locked:
- whole fighter still `Needs Work`
- core-frame G-frame restoration may close complete before whole-fighter proof
  does
- `Maker`, `Taker`, and `Sniper` are weapons
- `Wallet` and the canonical paper harness rack are core modules
- weapons remain diagnostic-only on the current BRO-local board call

Public API / schema changes:
- none
- this is an investigation-and-closure program only

Program-wide no-drift locks:
- no “close enough”
- no “mostly done”
- no “it passed once”
- no “the harness is clean so the fighter is clean”
- no “weapon symptom means weapon packet”
- no “report looks fine” when runtime/current proof disagrees
- no runtime or behavior mutation hidden inside packet investigation

## Rehardening Gate
Every packet in this program must begin by running the BRO-local
rehardening gate below and recording the result.

Required gate order:
1. mission frame
2. doctrine frame
3. authority frame
4. pathology frame
5. semantic frame
6. intervention frame
7. drift frame
8. proof frame
9. failure-signature frame

Required gate output:
- real problem
- authoritative surface
- surface purpose
- authority owner
- disease vs symptom
- smallest correct-layer move
- what proves closure
- what this packet must not change

If this cannot be stated cleanly, the packet is `NO-GO`.

## Authority Lock
Each packet must anchor to this authority order:

1. `docs/PROJECT_TRUTH_STATE.md`
2. `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`
3. relevant BRO-local doctrine/runbook surfaces for the lane being judged
4. current-code runtime proof plus manifest / contract / report artifacts
5. validator / gate / audit surfaces for the lane being judged
6. `docs/CURRENT_BASELINE.md` as reference-only
7. packet-local prior blockers and packet-local forensic artifacts

Hard rules:
- no historical green anchor outranks current-code truth
- no report surface outranks runtime truth
- no packet-local interpretation outranks doctrine

## Canonical Board Sink
The live G-frame board state is owned by:
- `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`

Board-sink law:
- packet docs are packet-local forensic evidence records
- packet docs do not independently own the running whole-fighter board state
- a packet is not complete until the audit board sink is updated

Minimum required board-sink updates after each packet:
- `G-Frame Verdict Board`
- `Drift Register`
- `Ambiguity Register`
- `Closure Matrix`
- `Weapon Authorization Gate`
- `Next Packet Recommendation`

Provisional-status law:
- if Packet 1 or Packet 2 still carries a blocking contradiction, later packets
  may investigate but their board-state verdicts must be marked:
  - `provisional / subordinate to earlier blocker`

## Shared Packet Structure
Every packet must produce these sections, in this order:

1. `Authority Lock`
- current pickup point
- authority chain
- no-change list
- current blocker being judged

2. `Rehardening Gate`
- 100-foot hardening stack result
- surface purpose
- authority owner
- stop-the-line status
- go / no-go for deeper audit

3. `Module Intake`
- authoritative sources
- current evidence anchors
- downstream consumers
- stale surfaces that must not dominate judgment

4. `Pass 1`
- doctrine -> runtime -> transforms -> validators -> reports -> operators -> transport

5. `Pass 2`
- operators -> reports -> validators -> mirrors/reconstructors -> emitters -> doctrine

6. `Dependency Review`
- upstream dependency
- downstream blast radius
- false-closure risk
- no-shortcut zones

7. `Binary Verdict`
- `Completed G-Frame / 8bit-worthy`
- or `Needs Work`

8. `Closure Matrix Update`
- what must become true
- required proof artifact
- currently missing proof

Required packet deliverables:
1. authority chain sheet
2. contradiction matrix
3. drift register delta
4. ambiguity register delta
5. closure matrix delta
6. binary verdict card
7. board-sink update on `docs/BRO_GFRAME_CORE_FIGHTER_AUDIT_2026-05-01.md`

## Stop-The-Line Triggers
A packet must pause and reclassify if any of these appear:
- doctrine and runtime disagree
- runtime and reporting disagree on core meaning
- the truth anchor is contested or too dirty for the claim being made
- mirrors / backfills / descriptive-only surfaces multiply around one truth lane
- the packet starts building translation machinery instead of clarifying authority
- a later packet tries to certify closure while an earlier blocker still stands
- the packet sounds more confident than the evidence earns

## Packet Sequence
Run in this order:
1. Bones / Identity / Release-Truth Lock
2. Spinal Cord / Canonical-Proving-Path Failure Chain
3. Rack / Paper-Harness Truth-Sync Confirmation
4. Grip / Wallet-Authority Closure Map
5. Brain / Semantic-Ownership Closure Map
6. Nervous System / Consumer-Truth Closure Map
7. G-Frame Regrade And Weapon Authorization Gate

Dependency law:
- Packet 1 and Packet 2 are foundational
- if either still has a blocking contradiction, Packets 3-6 may investigate but
  their verdicts must be marked:
  - `provisional / subordinate to earlier blocker`

Why this order is locked:
- Bones decides what truth can certify anything else.
- Spinal cord decides whether the fighter is actually traversing the proving
  path.
- Rack then proves whether the harness is honestly describing that fighter.
- Grip determines whether authority/core control is stage-worthy.
- Brain and nervous system close language ownership and consumer-truth only
  after the frame’s identity and proving posture are clear.

## Packet Objectives
### Packet 1: Bones
- define what current-code proof can certify
- define clean-anchor standard

Questions to answer:
- what exactly is the current authoritative proof anchor?
- what can a dirty-worktree current-code proof legitimately prove?
- what can it inform but not certify?
- what is the minimum standard for a true G-frame closure anchor?

Investigation scope:
- current worktree truth and blast radius
- active branch / `HEAD` / dirty-state posture
- run manifest / run contract / config fingerprint / code fingerprint continuity
- separation between current-truth, baseline-truth, and packet-truth surfaces

Required outputs:
- release-truth legitimacy call
- clean-anchor standard
- stale-anchor demotion rules
- packet-safe language rules for future closure claims

Exit criteria:
- one authoritative current-truth chain
- one explicit clean-anchor standard
- no ambiguity about what the active proof can and cannot claim

### Packet 2: Spinal Cord
- localize the canonical path failure chain
- decide whether the active maker continuity issue is still core-frame work

Questions to answer:
- where exactly does the active proof collapse?
- is the failure in runtime posture, decision path, validation chain, or
  cross-layer coupling?
- is the canonical path itself healthy while the fighter is failing inside it,
  or is the path reporting layer itself suspect?

Investigation scope:
- canonical session lifecycle from preflight through `validate_postrun`
- original packet-local failure specimen `8db2c7fc-630e-4cdb-a2fe-1ba14a93a204`
- stage progression, participation collapse, and runtime classification chain
- replay/determinism validator integrity on the same anchor

Required outputs:
- spinal-cord failure chain
- authoritative failure point
- proof of whether the failure is runtime-core or downstream interpretation
- verdict on whether the maker late-window issue is still core-frame work

Exit criteria:
- one explicit failure chain with no unexplained hop
- one authoritative choke point
- clean statement on whether this remains fighter-core work

### Packet 3: Rack
- prove the harness tells the truth about the current fighter
- include canonical watched-run truth alignment

Questions to answer:
- is the rack synced to the current fighter, or only historically hardened?
- are descriptive-only semantics staying bounded?
- is any connector/gate/report surface silently turning rack health into
  fighter-health overclaim?

Investigation scope:
- current fighter truth vs rack truth on the same current-code anchor
- proving-lineage tuple integrity
- realism / claim-boundary semantics
- canonical 10-minute watched run as truth-sync evidence, not wrapper theater

Required outputs:
- rack truth-sync verdict
- proving-lineage integrity verdict
- watched-run truth alignment notes
- explicit answer to whether the rack overstates current fighter health

Exit criteria:
- rack is confirmed as the sole canonical proving lane
- watched canonical run can be used as honest evidence for the current fighter
- no connector surface silently upgrades the fighter’s health claim

### Packet 4: Grip
- classify wallet/live-authority gaps by paper-stage vs live-stage criticality

Questions to answer:
- which current wallet/live-authority gaps are paper-stage-blocking?
- which are tolerated for paper stage but must block live claims?
- is any local or derived surface acting as a hidden substitute for canonical
  live wallet truth?

Investigation scope:
- wallet authority ownership and precedence
- live nonce truth
- live pending-wallet-tx truth
- order-capable / submit-eligible semantics
- fail-closed capital authority behavior

Required outputs:
- wallet authority gap map
- stage-criticality map for every open gap
- exact paper-stage closure requirement
- exact live-stage closure requirement

Exit criteria:
- all paper-stage-critical wallet gaps are either closed or explicitly proven
  non-blocking
- no substitute authority path is carrying the fighter
- grip semantics remain fail-closed and truthful

### Packet 5: Brain
- classify split language into mirror, mutation, alias, or blocker

Questions to answer:
- which active-path concepts still have multiple names or multiple owners?
- which seams are harmless mirrors and which are active truth mutation?
- which report-side terms are merely descriptive and which are silently
  re-owning meaning?

Investigation scope:
- current-code-active concepts first
- doctrine/runtime/report/operator surfaces for each concept
- mirror vs mutation vs cosmetic alias classification
- highest-authority concepts that still split language

Required outputs:
- semantic ownership ledger
- mirror-vs-mutation ledger
- highest-authority fix order
- exact “brain still split here” register

Exit criteria:
- one concept, one term, one owner on the active path
- no downstream surface silently upgrades or re-owns runtime truth
- doctrine and current-code evidence do not materially disagree on meaning

### Packet 6: Nervous System
- classify downstream consumers into honest, bounded mirror, descriptive-only,
  or truth-mutating

Questions to answer:
- which consumer surfaces are honest?
- which are bounded mirrors?
- which are descriptive-only?
- which are truth-mutating reconstructors that can mislead packet selection or
  closure claims?

Investigation scope:
- audits
- readiness
- nightly reports
- metric harvest/readouts
- operator-facing summaries
- especially report/reconstruction seams already known in the current audit

Required outputs:
- consumer classification register
- truth-mutation risk map
- no-cut / no-shortcut zones for consumer surfaces
- exact nervous-system seams still blocking G-frame closure

Exit criteria:
- downstream consumers do not materially mutate runtime ownership or meaning
- mirrors are bounded and explicitly non-authoritative
- operator-facing surfaces tell the same story as runtime truth

### Packet 7: Regrade
- issue one board call
- either weapons remain diagnostic-only or they become primary-authorized

Questions to answer:
- is the G-frame frame-restoration block complete on current truth?
- is `BRO` now a `Completed G-Frame / 8bit-worthy` fighter?
- or does the remaining open work now live only in a post-restoration macro
  proof frontier?
- are weapons still diagnostic-only, or finally authorized as primary work?

Required outputs:
- updated verdict board for all six modules
- updated drift register
- updated ambiguity register
- updated closure matrix
- one weapon authorization decision

Weapon authorization requirements:
- current-code canonical proof passes the intended paper-stage gate
- rack truthfully proves the current fighter on the same anchor
- wallet/core authority is strong enough for the claimed phase
- report/readout surfaces no longer materially distort runtime truth
- release-truth posture is strong enough to support a real closure claim
- the active maker continuity issue no longer reads as a core-frame defect

## Test Plan
### Shared acceptance for every packet
- current-code evidence outranks historical memory
- Rehardening Gate is completed first
- Pass 1 and Pass 2 are both completed
- disease vs symptom is explicit
- no-change list is preserved
- stop-the-line triggers are checked
- binary verdict is issued
- closure matrix is updated
- provisional status is applied when earlier blockers remain

### Required proof by packet
- **Bones**
  - dirty proof vs clean anchor
  - manifest / contract / fingerprint continuity
- **Spinal Cord**
  - stage collapse localization
  - replay / determinism legitimacy
- **Rack**
  - rack truth vs current fighter truth on same anchor
  - canonical watched run alignment
- **Grip**
  - live authority gap criticality map
  - canonical wallet precedence check
- **Brain**
  - one concept / one term / one owner audit
  - mirror vs mutation ledger
- **Nervous System**
  - consumer classification
  - report/runtime reinterpretation risk
  - operator distortion risk
- **Regrade**
  - all six modules graded from current authority-ranked truth
  - weapon gate decided with no middle category

## Assumptions
- The rehardening gate defined inside this program is the governing gate for
  BRO-local packet work.
- If operator continuity/shop references disagree with BRO-local truth, the
  BRO-local truth screen, board sink, doctrine surfaces, and runtime artifacts
  win.
- Broader external shop continuity may preserve macro restoration context, but
  it does not govern this BRO-local packet program.
- No edits happen inside the packet program until the chosen packet is
  investigated, mapped, discussed, and explicitly authorized.
