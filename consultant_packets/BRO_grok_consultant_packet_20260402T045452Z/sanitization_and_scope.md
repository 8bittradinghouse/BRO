# Sanitization and Scope

## Included

- compact, machine-readable summaries
- doctrine-safe change descriptions
- run IDs and aggregate metrics
- no secrets, no auth material

## Excluded on purpose

- `.env` and any secret-bearing files
- raw full-day logs (`logs_exec/**` bulk)
- wallet/private tx details
- unrelated historical zip exports
- host/system-private metadata not required for review

## Privacy posture

- token identifiers in runtime surfaces may be redacted by system policy
- packet is designed for third-party engineering review, not full operational mirroring
