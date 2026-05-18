# BRO Pilot-Live Trust Packet 2: Maker Gate Legitimacy Board (2026-05-10)

## Purpose
- `VERIFIED`: this is the missing maker-local gate-legitimacy board for `Packet 2`.
- `VERIFIED`: it turns the Packet 2 “what is each gate allowed to decide?” question into an explicit board instead of a future promise.

## Authority Boundary
- `VERIFIED`: this board classifies live maker gates and interlocks.
- `VERIFIED`: it does not tune them, widen them, or bless them beyond their current owner role.

## Gate Legitimacy Board
| Gate / surface | Strong owner | May decide | Must not decide | Current call |
| --- | --- | --- | --- | --- |
| `maker_requires_ws_book_source` | `executor.py` market-data eligibility path | may block maker eligibility when required WS book truth is missing | may not certify expectancy, edge quality, or “good fight” quality by itself | `KEEP` |
| `market_reference_not_authoritative` | `executor.py` market-reference contract | may demote truth-thin rows from maker eligibility | may not stand in for a full economic verdict | `KEEP` |
| `timing_gate_min_sec_to_expiry` / `timing_gate_max_sec_to_expiry` | active paper maker competitiveness leaf | may confine current paper maker behavior to the current `(7s, 15s]` maker new-risk band | may not resurrect older `45-60s`, `50-60s`, or pre-correction `15-20 / 15` packet authority | `KEEP` |
| `selection_gate.require_secondary_oracle_confirmation` | active selection-gate leaf | may reject rows that lack confirmation | may not be treated as proof that surviving rows are economically safe | `KEEP` |
| `selection_gate.max_same_target_submit_count_prior` + `max_same_target_side_submit_count_prior` | active selection-gate leaf | may calm repeat-target churn | may not explain the whole loss shape without outcome-owner proof | `KEEP` |
| `selection_gate.min_depth_multiple` | active selection-gate runtime leaf aligned with blueprint/default lineage | may reject rows that do not satisfy the thicker-book cannon requirement | may not be mistaken for a complete economic verdict by itself | `KEEP` |
| `post_only_cross_touch_clamp` + `auth.enforce_post_only` | `order_manager.py` + `gateway.py` | may stop maker from crossing touch and becoming taker by accident | may not be mistaken for selectivity doctrine or economic proof | `KEEP` |
| `max_open_orders_per_token` + `max_total_open_orders` | risk/interlock layer | may cap maker order proliferation | may not certify fight quality or market-truth quality | `KEEP` |
| historical recovery-rate / recovery-authority family | removed recovery-rate / recovery-authority lineage with ignored dead-key compatibility only | may explain packet-era ancestry and artifact rereads | may not backfill maker permission, reintroduce maker recovery relaxations, or impersonate a live safety spine | `CUT / HISTORICAL-COMPAT LINEAGE` |
| `maker_queue_pressure` historical family | archive lineage only; current code removed the runtime/config/report family and keeps only a legacy-config ignored seam plus historical replay awareness | may explain ancestry only | may not mutate current maker posture or reassert live selectivity authority | `CUT / HISTORICAL-ONLY LINEAGE` |
| admission-shadow rubric | report/research bridge | may classify fights for study and hostile reread | may never directly gate live runtime by itself | `KEEP BUT FENCE` |

## Packet 2 Legitimacy Notes
- `VERIFIED`: the strong gate family now splits into three different classes:
  - runtime owner-law that may really block or allow
  - valid safety interlocks that may constrain runtime behavior
  - useful-but-fenced gates that may inform diagnosis without outranking stronger owners
- `VERIFIED`: the depth-multiple leaf is now back in live paper runtime doctrine:
  - it is active in code shape and shadow output
  - it survives in blueprint/default lineage
  - and current paper runtime leaf truth now carries `1.5`
  - Packet 2 should treat it as a real current maker gate, while still refusing to let it impersonate a full economics verdict
