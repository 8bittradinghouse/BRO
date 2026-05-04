# Storage Hygiene Docker Sweep - 2026-04-25

## VERIFIED
- Command run: `docker builder prune -af`
- Build cache before sweep: `4.279GB`
- Build cache after sweep: `0B`
- Containers before sweep: `0`
- Containers after sweep: `0`
- Local volumes before sweep: `0`
- Local volumes after sweep: `0`
- Remaining images:
  - `bro_btc_paper-bro-maker:latest` ~= `305MB`
  - `bro_btc_paper-bro-guardian:latest` ~= `305MB`

## Truth Classification
- VERIFIED: Docker was not “huge because active runtime was out of control.”
- VERIFIED: the real Docker bloat was build cache, not live containers or persistent volumes.
- VERIFIED: the builder-prune sweep reclaimed real VPS bytes without touching current BRO images.

## Residual Risk
- Future image rebuilds will regenerate cache. That is normal.
- If later storage pressure returns, inspect image churn and builder growth again before pruning anything broader than build cache.

## Next Recommended Move
- Leave the current maker/guardian images alone unless image churn becomes a measured storage issue.
- Focus the next BRO storage packet on raw event logs and stale session retention, not on Docker.
