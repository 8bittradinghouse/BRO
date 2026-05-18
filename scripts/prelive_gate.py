#!/usr/bin/env python3
"""Unified pre-live go/no-go gate for Bro."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
from typing import Any, Dict, Optional


from prodesk.common import utc_iso
from prodesk.config import extract_config_compatibility_metadata, load_execution_config
from prodesk.error_codes import summarize_error_codes
from prodesk.gateway import _normalize_evm_address, _normalize_private_key
from prodesk.repo import resolve_repo_root
from prodesk.reporting import decision_item
from prodesk.run_contract import run_contract_path as build_run_contract_path
from prodesk.secrets import SecretLoadError, load_auth_secrets
from scripts.alert_profile_audit import run_audit as run_alert_profile_audit
from scripts.config_consistency_audit import CRITICAL_PATHS, run_audit as run_config_consistency_audit
from scripts.guardian_profile_audit import run_audit as run_guardian_profile_audit
from scripts.time_discipline_audit import run_audit as run_time_discipline_audit
from scripts.prestart_gate import run_gate as run_prestart_gate
from scripts.readiness_gate import _load_policy, _resolve_effective_log_dir, run_readiness_gate
from scripts.run_integrity_audit import run_audit as run_run_integrity_audit
from scripts.runtime_hardening_audit import run_audit as run_runtime_hardening_audit
from scripts.security_audit import run_security_audit
from scripts.websocket_hardening_audit import run_audit as run_websocket_hardening_audit


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path_for_run(log_dir: pathlib.Path, run_id: str) -> pathlib.Path:
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_manifest_run_id_required")
    return log_dir / f"run_manifest_{rid}.json"


def _manifest_findings(
    log_dir: pathlib.Path,
    *,
    run_id: str,
    max_age_hours: float,
    min_schema_version: int,
) -> list[str]:
    findings: list[str] = []
    rid = str(run_id or "").strip()
    if not rid:
        findings.append("run_manifest_run_id_required")
        return findings
    manifest = _manifest_path_for_run(log_dir, rid)
    if not manifest.exists():
        findings.append("run_manifest_missing")
        return findings
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        findings.append(f"run_manifest_invalid_json:{manifest.name}:{exc.__class__.__name__}")
        return findings
    if not isinstance(payload, dict):
        findings.append(f"run_manifest_not_mapping:{manifest.name}")
        return findings

    payload_run_id = str(payload.get("run_id", "")).strip()
    if payload_run_id != rid:
        findings.append(f"run_manifest_run_id_mismatch:{payload_run_id or 'missing'}!={rid}")

    schema_version = int(payload.get("manifest_schema_version", 0) or 0)
    if schema_version < int(min_schema_version):
        findings.append(f"run_manifest_schema_too_old:{schema_version}<min:{int(min_schema_version)}")

    for key in ("run_id", "config_fingerprint_sha256", "code_fingerprint_sha256", "config_source_sha256"):
        if not str(payload.get(key, "")).strip():
            findings.append(f"run_manifest_missing_field:{key}")

    now = dt.datetime.now(dt.timezone.utc)
    mtime = dt.datetime.fromtimestamp(manifest.stat().st_mtime, tz=dt.timezone.utc)
    age_hours = (now - mtime).total_seconds() / 3600.0
    if age_hours > float(max_age_hours):
        findings.append(f"run_manifest_stale_hours:{age_hours:.2f}>max:{float(max_age_hours):.2f}")

    return findings


def _backup_bundle_findings(backup_dir: pathlib.Path, *, max_age_hours: float) -> list[str]:
    findings: list[str] = []
    if not backup_dir.exists():
        findings.append(f"backup_dir_missing:{backup_dir}")
        return findings
    bundles = sorted(backup_dir.glob("bro_backup_*.tar.gz"), key=lambda p: p.stat().st_mtime)
    if not bundles:
        findings.append(f"backup_bundle_missing:{backup_dir}")
        return findings

    latest = bundles[-1]
    now = dt.datetime.now(dt.timezone.utc)
    mtime = dt.datetime.fromtimestamp(latest.stat().st_mtime, tz=dt.timezone.utc)
    age_hours = (now - mtime).total_seconds() / 3600.0
    if age_hours > float(max_age_hours):
        findings.append(f"backup_bundle_stale_hours:{age_hours:.2f}>max:{float(max_age_hours):.2f}")

    hash_path = latest.with_suffix(latest.suffix + ".sha256")
    if not hash_path.exists():
        findings.append(f"backup_bundle_hash_missing:{hash_path.name}")
        return findings

    try:
        line = hash_path.read_text(encoding="utf-8").strip().splitlines()[0]
        expected = line.split()[0].strip().lower()
    except (OSError, UnicodeError, IndexError, ValueError) as exc:
        findings.append(f"backup_bundle_hash_invalid:{hash_path.name}:{exc.__class__.__name__}")
        return findings

    actual = _sha256_file(latest).lower()
    if expected != actual:
        findings.append(f"backup_bundle_hash_mismatch:{latest.name}")
    return findings


def _wallet_env_findings(cfg: Dict[str, Any]) -> list[str]:
    findings: list[str] = []
    auth = cfg.get("auth", {})
    security = cfg.get("security", {})
    taker = cfg.get("taker", {})

    pk_env = str(auth.get("private_key_env", "POLYMARKET_PRIVATE_KEY")).strip()
    funder_env = str(auth.get("funder_env", "POLYMARKET_FUNDER")).strip()
    ack_env = str(security.get("live_security_ack_env", "SECURITY_ACK")).strip() or "SECURITY_ACK"
    ack_expected = str(security.get("live_security_ack_value", "YES")).strip() or "YES"

    ack_value = str(os.getenv(ack_env, "")).strip()
    try:
        pk_value, funder_value, source_meta = load_auth_secrets(auth)
    except SecretLoadError as exc:
        findings.append(f"secret_load_failed:{exc}")
        source_meta = {}
        pk_value = ""
        funder_value = ""

    if pk_value:
        try:
            _normalize_private_key(pk_value)
        except (TypeError, ValueError) as exc:
            source = str(source_meta.get("private_key_source", pk_env))
            findings.append(f"invalid_private_key:{source}:{exc}")
    if funder_value:
        try:
            _normalize_evm_address(funder_value)
        except (TypeError, ValueError) as exc:
            source = str(source_meta.get("funder_source", funder_env))
            findings.append(f"invalid_funder:{source}:{exc}")

    if bool(security.get("require_live_security_ack", True)) and ack_value != ack_expected:
        findings.append(f"security_ack_mismatch:{ack_env}")

    effective_taker_enabled = bool(taker.get("enabled", False))
    allow_taker = bool(auth.get("allow_taker", False))
    if effective_taker_enabled and not allow_taker:
        findings.append("auth_taker_mismatch:taker.enabled=true but auth.allow_taker=false")

    return findings


def _live_discovery_findings(cfg: Dict[str, Any]) -> list[str]:
    findings: list[str] = []
    mode = str(cfg.get("mode", "paper")).strip().lower()
    if mode != "live":
        return findings

    targets = cfg.get("targets", {}) if isinstance(cfg.get("targets"), dict) else {}
    discovery = targets.get("discovery", {}) if isinstance(targets.get("discovery"), dict) else {}
    if not bool(discovery.get("enabled", False)):
        return findings

    allow_token_ids = [
        str(token_id).strip()
        for token_id in discovery.get("allow_token_ids", [])
        if str(token_id).strip()
    ]
    if not allow_token_ids:
        findings.append("live_discovery_allow_token_ids_missing")
        return findings

    allow_set = set(allow_token_ids)
    token_ids = [str(token_id).strip() for token_id in targets.get("token_ids", []) if str(token_id).strip()]
    out_of_allow = [token_id for token_id in token_ids if token_id not in allow_set]
    if out_of_allow:
        findings.append("live_discovery_token_ids_outside_allowlist:" + ",".join(out_of_allow[:8]))
    return findings


def _live_secret_source_findings(cfg: Dict[str, Any], *, allow_env_secrets_in_live: bool) -> list[str]:
    findings: list[str] = []
    mode = str(cfg.get("mode", "paper")).strip().lower()
    if mode != "live":
        return findings
    if bool(allow_env_secrets_in_live):
        return findings

    auth = cfg.get("auth", {}) if isinstance(cfg.get("auth"), dict) else {}
    sources = {
        "private_key": auth.get("private_key_source"),
        "funder": auth.get("funder_source"),
    }
    for label, source in sources.items():
        source_map = source if isinstance(source, dict) else {}
        source_mode = str(source_map.get("mode", "env")).strip().lower() or "env"
        if source_mode == "env":
            findings.append(f"live_secret_source_env_not_allowed:{label}")
    return findings


def run_prelive_gate(
    *,
    config_path: pathlib.Path,
    policy_path: pathlib.Path,
    required_stage: str,
    run_id: Optional[str],
    skip_readiness: bool,
    skip_runtime_audit: bool,
    skip_config_consistency: bool,
    skip_manifest_check: bool,
    manifest_max_age_hours: float,
    manifest_min_schema_version: int,
    skip_backup_check: bool,
    backup_dir: pathlib.Path,
    backup_max_age_hours: float,
    skip_websocket_audit: bool = False,
    skip_guardian_profile_audit: bool = False,
    skip_alert_profile_audit: bool = False,
    skip_time_discipline_audit: bool = False,
    time_discipline_max_allowed_skew_sec: float = 0.25,
    skip_run_integrity_audit: bool = False,
    run_integrity_min_status_rows: int = 5,
    run_integrity_max_status_age_sec: float = 180.0,
    allow_env_secrets_in_live: bool = False,
) -> Dict[str, Any]:
    cfg = load_execution_config(config_path.resolve())
    repo_root = resolve_repo_root()
    findings: list[str] = []
    timing_warnings: list[str] = []
    compatibility_warnings: list[str] = []
    decision_trace: list[dict[str, Any]] = []
    checks: Dict[str, Any] = {}
    compatibility_meta = extract_config_compatibility_metadata(cfg)
    ignored_compatibility_fields = list(compatibility_meta.get("ignored_compatibility_fields") or [])
    compatibility_warnings.extend(str(x) for x in compatibility_meta.get("compatibility_warnings", []))
    checks["config_compatibility"] = {
        "ignored_compatibility_fields": ignored_compatibility_fields,
        "compatibility_warning_count": int(compatibility_meta.get("compatibility_warning_count") or 0),
        "compatibility_warnings": list(compatibility_meta.get("compatibility_warnings") or []),
    }

    mode = str(cfg.get("mode", "paper")).strip().lower()
    if mode != "live":
        findings.append(f"config_mode_not_live:{mode}")
    decision_trace.append(
        decision_item(
            check="config_mode",
            level="hard_fail",
            metric="mode",
            comparator="eq",
            value=1.0 if mode == "live" else 0.0,
            threshold=1.0,
            passed=(mode == "live"),
            note=f"mode={mode}",
        )
    )

    sec = run_security_audit(config_path=config_path, mode_override="live")
    checks["security_audit"] = sec
    findings.extend(str(x) for x in sec.get("findings", []))

    if not skip_config_consistency:
        config_consistency = run_config_consistency_audit(
            primary_path=config_path,
            secondary_path=pathlib.Path("config.yaml"),
            paths=list(CRITICAL_PATHS),
        )
        checks["config_consistency_audit"] = config_consistency
        findings.extend(str(x) for x in config_consistency.get("findings", []))
    else:
        checks["config_consistency_audit"] = {"skipped": True}

    prestart = run_prestart_gate(config_path=config_path, allow_kill_switch=False, allow_guard_file=False)
    checks["prestart_gate"] = prestart
    findings.extend(str(x) for x in prestart.get("findings", []))

    wallet_findings = _wallet_env_findings(cfg)
    checks["wallet_env"] = {"finding_count": len(wallet_findings), "findings": wallet_findings}
    findings.extend(wallet_findings)
    discovery_findings = _live_discovery_findings(cfg)
    checks["live_discovery"] = {"finding_count": len(discovery_findings), "findings": discovery_findings}
    findings.extend(discovery_findings)
    decision_trace.append(
        decision_item(
            check="live_discovery_allowlist",
            level="hard_fail",
            metric="finding_count",
            comparator="max",
            value=float(len(discovery_findings)),
            threshold=0.0,
            passed=(len(discovery_findings) == 0),
            note="live mode with discovery enabled requires explicit allow_token_ids",
        )
    )
    secret_source_findings = _live_secret_source_findings(
        cfg,
        allow_env_secrets_in_live=bool(allow_env_secrets_in_live),
    )
    checks["live_secret_sources"] = {
        "finding_count": len(secret_source_findings),
        "findings": secret_source_findings,
    }
    findings.extend(secret_source_findings)
    decision_trace.append(
        decision_item(
            check="live_secret_source_policy",
            level="hard_fail",
            metric="finding_count",
            comparator="max",
            value=float(len(secret_source_findings)),
            threshold=0.0,
            passed=(len(secret_source_findings) == 0),
            note="live mode forbids env-based secret sources unless explicitly overridden",
        )
    )

    effective_log_dir = _resolve_effective_log_dir(pathlib.Path(str(cfg.get("storage", {}).get("log_dir", "./logs_exec"))))
    selected_run_id = (run_id or "").strip()
    if not selected_run_id and ((not skip_run_integrity_audit) or (not skip_readiness) or (not skip_manifest_check)):
        findings.append("prelive_run_id_required")
    if not skip_manifest_check:
        if not selected_run_id:
            checks["run_manifest"] = {"skipped": True, "reason": "run_id_required"}
            decision_trace.append(
                decision_item(
                    check="run_manifest_integrity",
                    level="hard_fail",
                    metric="run_id_required",
                    comparator="eq",
                    value=0.0,
                    threshold=1.0,
                    passed=False,
                    note="explicit run_id is required for authoritative manifest checks",
                )
            )
        else:
            manifest_findings = _manifest_findings(
                effective_log_dir,
                run_id=selected_run_id,
                max_age_hours=float(manifest_max_age_hours),
                min_schema_version=int(manifest_min_schema_version),
            )
            checks["run_manifest"] = {"finding_count": len(manifest_findings), "findings": manifest_findings}
            findings.extend(manifest_findings)
            decision_trace.append(
                decision_item(
                    check="run_manifest_integrity",
                    level="hard_fail",
                    metric="finding_count",
                    comparator="max",
                    value=float(len(manifest_findings)),
                    threshold=0.0,
                    passed=(len(manifest_findings) == 0),
                    note="manifest schema/freshness/required fields for explicit run_id",
                )
            )
    else:
        checks["run_manifest"] = {"skipped": True}
        decision_trace.append(
            decision_item(
                check="run_manifest_integrity",
                level="advisory",
                metric="skipped",
                comparator="max",
                value=1.0,
                threshold=0.0,
                passed=True,
                note="skipped by operator flag",
            )
        )

    if not skip_backup_check:
        backup_findings = _backup_bundle_findings(backup_dir.resolve(), max_age_hours=float(backup_max_age_hours))
        checks["backup_bundle"] = {"finding_count": len(backup_findings), "findings": backup_findings}
        findings.extend(backup_findings)
        decision_trace.append(
            decision_item(
                check="backup_bundle_integrity",
                level="hard_fail",
                metric="finding_count",
                comparator="max",
                value=float(len(backup_findings)),
                threshold=0.0,
                passed=(len(backup_findings) == 0),
                note="bundle freshness + sha256 sidecar",
            )
        )
    else:
        checks["backup_bundle"] = {"skipped": True}
        decision_trace.append(
            decision_item(
                check="backup_bundle_integrity",
                level="advisory",
                metric="skipped",
                comparator="max",
                value=1.0,
                threshold=0.0,
                passed=True,
                note="skipped by operator flag",
            )
        )

    if not skip_runtime_audit:
        compose_path = repo_root / "docker-compose.yml"
        runtime_audit = run_runtime_hardening_audit(
            compose_path=compose_path,
            log_dir=effective_log_dir,
            data_dir=repo_root / "data",
        )
        checks["runtime_hardening_audit"] = runtime_audit
        findings.extend(str(x) for x in runtime_audit.get("findings", []))
    else:
        checks["runtime_hardening_audit"] = {"skipped": True}

    if not skip_websocket_audit:
        ws_audit = run_websocket_hardening_audit(config_path=config_path)
        checks["websocket_hardening_audit"] = ws_audit
        findings.extend(str(x) for x in ws_audit.get("findings", []))
        timing_warnings.extend(str(x) for x in ws_audit.get("warnings", []))
    else:
        checks["websocket_hardening_audit"] = {"skipped": True}

    if not skip_guardian_profile_audit:
        guardian_audit = run_guardian_profile_audit(
            compose_path=repo_root / "docker-compose.yml",
            config_path=config_path.resolve(),
        )
        checks["guardian_profile_audit"] = guardian_audit
        findings.extend(str(x) for x in guardian_audit.get("findings", []))
        compatibility_warnings.extend(str(x) for x in guardian_audit.get("compatibility_warnings", []))
    else:
        checks["guardian_profile_audit"] = {"skipped": True}

    if not skip_alert_profile_audit:
        alert_audit = run_alert_profile_audit(config_path=config_path)
        checks["alert_profile_audit"] = alert_audit
        findings.extend(str(x) for x in alert_audit.get("findings", []))
    else:
        checks["alert_profile_audit"] = {"skipped": True}

    if not skip_time_discipline_audit:
        time_run_contract_path = None
        if selected_run_id:
            time_run_contract_path = build_run_contract_path(
                log_dir=effective_log_dir,
                run_id=selected_run_id,
            )
        time_audit = run_time_discipline_audit(
            config_path=config_path,
            log_dir=effective_log_dir,
            run_id=(selected_run_id or None),
            run_contract_path=time_run_contract_path,
            max_allowed_skew_sec=float(time_discipline_max_allowed_skew_sec),
            max_status_age_sec=315360000.0,
            min_status_rows=1,
        )
        checks["time_discipline_audit"] = time_audit
        findings.extend(str(x) for x in time_audit.get("findings", []))
        timing_warnings.extend(str(x) for x in time_audit.get("warnings", []))
    else:
        checks["time_discipline_audit"] = {"skipped": True}

    if not skip_run_integrity_audit:
        integrity_audit = run_run_integrity_audit(
            log_dir=effective_log_dir,
            run_id=selected_run_id,
            min_status_rows=max(1, int(run_integrity_min_status_rows)),
            status_tail_lines=800,
            event_tail_lines=800,
            max_status_age_sec=float(run_integrity_max_status_age_sec),
        )
        checks["run_integrity_audit"] = integrity_audit
        findings.extend(str(x) for x in integrity_audit.get("findings", []))
    else:
        checks["run_integrity_audit"] = {"skipped": True}

    if not skip_readiness:
        policy = _load_policy(policy_path.resolve())
        readiness = run_readiness_gate(log_dir=effective_log_dir, policy=policy, run_id=(selected_run_id or None))
        checks["readiness_gate"] = readiness
        checks["readiness_run_id"] = selected_run_id
        stage_order = list(policy.get("stage_order", []))
        highest = readiness.get("highest_passing_stage")
        if required_stage and required_stage in stage_order:
            required_idx = stage_order.index(required_stage)
            highest_idx = stage_order.index(highest) if highest in stage_order else -1
            if highest_idx < required_idx:
                findings.append(
                    f"readiness_below_required_stage:required={required_stage}:highest={highest or 'none'}"
                )
    else:
        checks["readiness_gate"] = {"skipped": True}

    unique_findings = sorted(set(findings))
    unique_timing_warnings = sorted(set(timing_warnings))
    unique_compatibility_warnings = sorted(set(compatibility_warnings))
    return {
        "ts_utc": utc_iso(),
        "repo_root": str(repo_root),
        "config_path": str(config_path.resolve()),
        "required_stage": required_stage,
        "ok": len(unique_findings) == 0,
        "finding_count": len(unique_findings),
        "findings": unique_findings,
        "timing_warning_count": len(unique_timing_warnings),
        "timing_warnings": unique_timing_warnings,
        "compatibility_warning_count": len(unique_compatibility_warnings),
        "compatibility_warnings": unique_compatibility_warnings,
        "decision_trace": decision_trace,
        "error_codes": summarize_error_codes(unique_findings),
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro pre-live go/no-go gate")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--policy", default="ops/ramp_policy.yaml", help="Readiness policy path")
    parser.add_argument(
        "--required-stage",
        default="pilot_live",
        help="Minimum readiness stage required for go/no-go",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Explicit run_id for authoritative checks (required unless manifest/readiness/run-integrity are skipped)",
    )
    parser.add_argument("--skip-readiness", action="store_true", help="Skip readiness stage evaluation")
    parser.add_argument("--skip-runtime-audit", action="store_true", help="Skip runtime hardening audit")
    parser.add_argument("--skip-config-consistency", action="store_true", help="Skip config consistency audit")
    parser.add_argument("--skip-manifest-check", action="store_true", help="Skip run manifest freshness/integrity checks")
    parser.add_argument(
        "--manifest-max-age-hours",
        type=float,
        default=48.0,
        help="Maximum allowed age of explicit run manifest",
    )
    parser.add_argument(
        "--manifest-min-schema-version",
        type=int,
        default=2,
        help="Minimum run manifest schema version required",
    )
    parser.add_argument("--skip-backup-check", action="store_true", help="Skip backup bundle integrity/freshness checks")
    parser.add_argument("--backup-dir", default="./backups", help="Backup bundle directory")
    parser.add_argument("--backup-max-age-hours", type=float, default=36.0, help="Maximum allowed age of latest backup bundle")
    parser.add_argument("--skip-websocket-audit", action="store_true", help="Skip websocket/feed hardening audit")
    parser.add_argument("--skip-guardian-profile-audit", action="store_true", help="Skip guardian launch profile audit")
    parser.add_argument("--skip-alert-profile-audit", action="store_true", help="Skip alert threshold profile audit")
    parser.add_argument("--skip-time-discipline-audit", action="store_true", help="Skip clock/time discipline audit")
    parser.add_argument(
        "--time-discipline-max-allowed-skew-sec",
        type=float,
        default=0.25,
        help="Upper bound for configured preflight.max_clock_skew_sec",
    )
    parser.add_argument("--skip-run-integrity-audit", action="store_true", help="Skip run integrity audit")
    parser.add_argument("--run-integrity-min-status-rows", type=int, default=5, help="Minimum status rows for run integrity audit")
    parser.add_argument("--run-integrity-max-status-age-sec", type=float, default=180.0, help="Maximum latest status age for run integrity audit")
    parser.add_argument(
        "--allow-env-secrets-in-live",
        action="store_true",
        help="Allow env-based secret sources in live mode (legacy override; not recommended)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_prelive_gate(
        config_path=pathlib.Path(args.config),
        policy_path=pathlib.Path(args.policy),
        required_stage=str(args.required_stage).strip(),
        run_id=str(args.run_id).strip() or None,
        skip_readiness=bool(args.skip_readiness),
        skip_runtime_audit=bool(args.skip_runtime_audit),
        skip_config_consistency=bool(args.skip_config_consistency),
        skip_manifest_check=bool(args.skip_manifest_check),
        manifest_max_age_hours=float(args.manifest_max_age_hours),
        manifest_min_schema_version=int(args.manifest_min_schema_version),
        skip_backup_check=bool(args.skip_backup_check),
        backup_dir=pathlib.Path(args.backup_dir),
        backup_max_age_hours=float(args.backup_max_age_hours),
        skip_websocket_audit=bool(args.skip_websocket_audit),
        skip_guardian_profile_audit=bool(args.skip_guardian_profile_audit),
        skip_alert_profile_audit=bool(args.skip_alert_profile_audit),
        skip_time_discipline_audit=bool(args.skip_time_discipline_audit),
        time_discipline_max_allowed_skew_sec=float(args.time_discipline_max_allowed_skew_sec),
        skip_run_integrity_audit=bool(args.skip_run_integrity_audit),
        run_integrity_min_status_rows=int(args.run_integrity_min_status_rows),
        run_integrity_max_status_age_sec=float(args.run_integrity_max_status_age_sec),
        allow_env_secrets_in_live=bool(args.allow_env_secrets_in_live),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
