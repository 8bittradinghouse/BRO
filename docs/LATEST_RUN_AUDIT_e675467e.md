# Latest Run Audit: `e675467e-368a-49db-bc03-f35c96aebba8`

## Run Identity
- Run ID: `e675467e-368a-49db-bc03-f35c96aebba8`
- Session ID: `32c2d1fc-9280-4a2e-9b1a-2ac0e7b8f7ed`
- Profile: `paper_universal`
- Git commit: `519f6ed188c7bde92e674512072d34ecc9d0ba1e`
- Config fingerprint: `bc8873395234bb1aef36b8d3f8d3d07a786ae8cad1a37f3ab1dcf18d48d293e9`
- Code fingerprint: `26f54c31fa5e2e93357313ed46eadc7074b77a8cc718ccef15d3bded0883c517`
- Packet role: second-pass proof for pre-expiry emergency taker telemetry compression

## Gate Results
- `canonical_paper_validation.json`: `status=pass`
- `canonical_paper_validation.json`: `runtime_classification=VALID_ACTIVE`
- `canonical_paper_validation.json`: `policy_failed=false`
- `canonical_paper_validation.json`: `reports_complete=true`
- `canonical_paper_validation.json`: `determinism_consistent=true`
- Validator exit codes: all `0`

## Runtime Truth
- `maker_submits=14`
- `maker_fills=12`
- `maker_fill_rate=0.5714285714285714`
- `preexpiry_emergency_taker_block_count=160`
- `maker_to_taker_recovery_handoff_disabled_count=160`
- `same_market_lane_collision_block_count=0`
- `waiting_for_maker_exit_count=0`
- `valuation_hard_degraded_enter_count=2`
- `valuation_hard_degraded_clear_count=2`
- Final status:
  - `gauge.total_pnl=68.74698999999998`
  - `valuation_degraded=false`
  - `held_unpriceable_token_count=0`

## Compression Proof
- Raw event rows for `preexpiry_emergency_taker_unwind`: `20`
- Composition:
  - `initial` rows: `8`
  - `repeat_summary` rows: `12`
- Status-authoritative blocked count: `160`
- Compression surface examples included:
  - `repeat_count_delta`
  - `repeat_count_total`
  - `repeat_distinct_token_count=2`
  - `repeat_token_ids_sample=[...]`
- Truth classification:
  - VERIFIED: control-plane and validator health remained clean after the runtime telemetry change.
  - VERIFIED: blocked-handoff chatter compressed materially from `160` blocked attempts to `20` event rows while preserving authoritative counters.
  - VERIFIED: same-market protection and maker-first dead-handoff doctrine were unchanged.

## Scope Verdict
VERIFIED: this packet changed telemetry shape, not taker authority, not recovery doctrine, not wallet/risk semantics, and not market-lane law.

VERIFIED_CLOSED: the low-risk telemetry compression packet is successful enough to treat as an earned shop improvement.
