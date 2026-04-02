# R2 Latest-Run / Manifest Classification (2026-03-19 UTC)

Scope: repo-wide classification of latest-run and manifest convenience behavior.

Goal: ensure authoritative paths are explicit `run_id` + run-contract anchored, while convenience behavior (if retained) is clearly non-authoritative.

## Classification Table

| File | Function / Surface | Latest-run behavior present? | Authoritative? | Allowed? | Classification | Remediation |
|---|---|---:|---:|---:|---|---|
| `prodesk/artifact_identity.py` | `resolve_latest_run_id()` | Yes (legacy symbol) | Yes-risk if used | No | `SILENT_FALLBACK` (closed) | Fail-fast hard error (`latest_run_resolution_forbidden:use_explicit_run_id`). |
| `scripts/prelive_gate.py` | `_manifest_findings()` | No (explicit run manifest only) | Yes | Yes | `CANONICAL_COMPLIANT` | Keep explicit `run_id` required for manifest integrity. |
| `scripts/deploy_paper_clean.sh` | manifest identity section | Prior latest manifest lookup removed | Canonical wrapper-internal | Yes (explicit only) | `WRAPPER_OK` | Manifest identity prints only for explicit `--run-id`; otherwise non-authoritative skip message. |
| `scripts/reconcile_structural.sh` | manifest check | Yes (historical latest lookup) | No | No | `NEEDS_SURGICAL_FIX` (closed) | Replaced with explicit `run_id` manifest binding; no latest lookup. |
| `scripts/guardian_watchdog.py` | `--run-id-from-manifest` + `_resolve_run_id()` | Yes | No (authoritative gating blocks control) | Yes (opt-in only) | `WRAPPER_OK` after hardening | Default changed to `--no-run-id-from-manifest`; convenience remains explicit opt-in only. |
| `scripts/canonical_paper_session.py` | fresh manifest selection | Yes (freshness detection) | Yes | Yes | `CANONICAL_COMPLIANT` | Keep bounded fresh-manifest detection for startup binding; no latest-run authority for validation. |
| `scripts/canonical_paper_validation.sh` | run selection | No | Yes | Yes | `CANONICAL_COMPLIANT` | Explicit positional `run_id` mandatory. |
| `scripts/readiness_gate.py` | gate entrypoint | No (CLI requires run_id) | Yes | Yes | `CANONICAL_COMPLIANT` | Keep hard fail without `--run-id`. |
| `scripts/soak_hardening_gate.py` | gate entrypoint | No (`--run-id` required) | Yes | Yes | `CANONICAL_COMPLIANT` | Keep run-id requirement + runtime classification gating. |
| `scripts/websocket_reliability_gate.py` | gate entrypoint | Missing-run detection only | Yes | Yes | `CANONICAL_COMPLIANT` | Keep `websocket_slo_run_id_required` finding when run_id missing. |
| `scripts/guardian_healthcheck.py` | `_latest_status_file()` | Yes | No | Yes | `LEGACY_SAFE` | Container liveness-only check; not promotion/readiness authority. |
| `scripts/container_healthcheck.py` | `_latest_status_file()` | Yes | No | Yes | `LEGACY_SAFE` | Container liveness-only check; not canonical evidence authority. |
| `scripts/prestart_gate.py` | `_latest_status_row()` | Yes | Startup safety only | Yes | `LEGACY_SAFE` | Keep as prestart safety snapshot; do not use for run promotion/evidence. |
| `scripts/backup_daily.py` | `manifest_glob` scan | Yes | No | Yes | `LEGACY_SAFE` | Backup/export domain only; non-authoritative for runtime truth. |
| `scripts/rollback_drill.py` | latest backup bundle | Yes | No | Yes | `LEGACY_SAFE` | Restore drill only; non-authoritative for runtime truth. |

## Authoritative Rule Locked

Authoritative runtime/promotion/evidence checks must be anchored to:

1. explicit `run_id`
2. explicit `run_contract`/manifest path where applicable
3. declared session phase

No authoritative script may infer truth from implicit "latest run".

## R2 Residual Note

The only retained latest-run convenience is guardian manifest auto-resolution, and it is now:

- non-authoritative only
- default disabled
- never sufficient to arm/clear guard without verified canonical authority context

