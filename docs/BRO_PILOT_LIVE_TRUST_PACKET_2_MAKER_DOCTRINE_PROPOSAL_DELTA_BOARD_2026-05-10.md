# BRO Pilot-Live Trust Packet 2: Maker Doctrine Proposal Delta Board (2026-05-10)

## Purpose
- `VERIFIED`: this board compares the `Galaxy Mega Maker Cannon` doctrine proposal against current maker owner truth.
- `VERIFIED`: it exists so Packet 2 can talk in one language about what is already active steel, what is partial, and what is still only design intent.

## Authority Boundary
- `VERIFIED`: the doctrine proposal is a high-value design target.
- `VERIFIED`: it is not automatic runtime authority.
- `VERIFIED`: this board is a delta map, not a tuning order.

## Doctrine Proposal Delta Board
| Proposal item | Proposal intent | Current owner surface | Current state | Packet 2 call |
| --- | --- | --- | --- | --- |
| pure maker / `postOnly` | maker stays maker-only | `gateway.py`, `auth.enforce_post_only`, `order_manager.py` cross-touch clamp | active current steel | `KEEP` |
| maker gate opens at `15s`; taker handoff opens at `7s` | fixed late-window handoff, not a drifting outer band | `paper_universal.yaml` maker timing gate + late-window authority mapping | active current paper steel | `KEEP` |
| secondary oracle confirmation | require confirmation before cannon fire | selection-gate leaf in current paper profile | active current steel | `KEEP` |
| explicit dual-oracle directional agreement + `0.20` semantics | formal agreement and meaningful delta threshold | no clean maker-local runtime owner fully closes this yet | still partly design intent / unresolved machine semantics | `OPEN DELTA` |
| fixed `$100` cannon shot | bounded desk-viable maker shot size | selection gate + sizing target in current paper profile | active current paper steel | `KEEP` |
| `1.5x` depth multiple | require thicker books around the `$100` shot | active paper selection-gate leaf plus code/default lineage all align at `1.5` | active current paper steel | `KEEP` |
| stacked open-order cap `4-6` | bounded concurrency | current shared risk shell keeps `max_open_orders_per_token=4` and broader total open-order guardrails | partially aligned | `KEEP / WATCH SHARED SHELL` |
| explicit skip-reason logging | every rejected fight should say why | selection-gate reject reasons, no-submit reasons, and shadow rows are present | active current steel | `KEEP` |
| `95%+` win-rate aspiration | cannon-grade fight quality target | no current runtime owner proves this | design target only | `DESIGN INTENT ONLY` |

## Delta Notes That Matter Now
- `VERIFIED`: the depth-multiple rule is now back in live alignment with the proposal/default `1.5x` cannon story.
- `VERIFIED`: the late-window doctrine is now materially aligned on the fixed
  `15s` maker-open / `7s` taker-handoff law instead of the older `15-20s`
  wording.
- `VERIFIED`: the dual-oracle confirmation idea partially exists, but the proposal's full agreement-and-delta semantics are not yet source-closed as maker runtime law.
- `VERIFIED`: Packet 2 should use this board to prevent two bad moves:
  - rewriting the proposal downward to match drift residue
  - pretending proposal intent is already live runtime steel
