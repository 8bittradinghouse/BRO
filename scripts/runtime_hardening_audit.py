#!/usr/bin/env python3
"""Runtime hardening audit for Bro docker deployments."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import resource
import stat
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml


from prodesk.error_codes import summarize_error_codes


def _load_compose(path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("compose file root must be a mapping")
    return payload


def _env_to_map(values: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if isinstance(values, dict):
        for k, v in values.items():
            out[str(k)] = str(v)
        return out
    if isinstance(values, list):
        for item in values:
            text = str(item)
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            out[key.strip()] = value.strip()
    return out


_ENV_REF_RE = re.compile(r"^\$\{[A-Z0-9_:-]+\}$")


def _parse_numeric_or_env(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if _ENV_REF_RE.match(text):
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _parse_volume(raw: Any) -> Tuple[str, str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", "", ""
    # Support compose env-default syntax in source paths, e.g.
    # ${BRO_CONFIG_PATH:-./execution_config.yaml}:/config/config.yaml:ro
    match = re.match(r"^(.*):(/[^:]+?)(?::([^:]+))?$", text)
    if match:
        src = match.group(1) or ""
        dst = match.group(2) or ""
        mode = match.group(3) or ""
        return src, dst, mode
    parts = text.rsplit(":", 2)
    if len(parts) == 1:
        return "", parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[2]


def _check_volumes(name: str, svc: Dict[str, Any], findings: List[str], warnings: List[str]) -> None:
    volumes = svc.get("volumes", [])
    if not isinstance(volumes, list) or not volumes:
        findings.append(f"runtime_service_volumes_missing:{name}")
        return
    config_mount_seen = False
    config_mount_read_only = True
    has_logs_rw = False
    has_data_rw = False
    for raw in volumes:
        _src, dst, mode = _parse_volume(raw)
        if not dst:
            continue
        mode_text = mode.strip().lower()
        read_only = "ro" in mode_text.split(",")
        if dst == "/config" or dst == "/config/config.yaml" or dst.startswith("/config/"):
            config_mount_seen = True
            if not read_only:
                config_mount_read_only = False
        if dst == "/logs":
            has_logs_rw = not read_only
        if dst == "/data":
            has_data_rw = not read_only
    if name == "bro-maker":
        if not config_mount_seen or not config_mount_read_only:
            findings.append(f"runtime_service_config_mount_not_read_only:{name}")
        if not has_logs_rw:
            findings.append(f"runtime_service_logs_mount_not_writable:{name}")
        if not has_data_rw:
            findings.append(f"runtime_service_data_mount_not_writable:{name}")
    if name == "bro-guardian" and not has_logs_rw:
        warnings.append(f"runtime_service_logs_mount_not_writable:{name}")


def _check_service(name: str, svc: Dict[str, Any], findings: List[str], warnings: List[str]) -> None:
    if not bool(svc.get("init", False)):
        findings.append(f"runtime_service_init_not_enabled:{name}")

    restart_policy = str(svc.get("restart", "")).strip().lower()
    if restart_policy not in {"unless-stopped", "always", "on-failure"}:
        findings.append(f"runtime_service_restart_policy_invalid:{name}:{restart_policy or 'missing'}")

    healthcheck = svc.get("healthcheck")
    if not isinstance(healthcheck, dict):
        findings.append(f"runtime_service_healthcheck_missing:{name}")
    elif bool(healthcheck.get("disable", False)):
        findings.append(f"runtime_service_healthcheck_disabled:{name}")
    else:
        for field in ("test", "interval", "timeout", "retries"):
            if field not in healthcheck:
                findings.append(f"runtime_service_healthcheck_field_missing:{name}:{field}")

    user_value = str(svc.get("user", "")).strip()
    if not user_value:
        findings.append(f"runtime_service_user_not_set:{name}")
    elif user_value in {"0", "root", "0:0", "root:root"}:
        findings.append(f"runtime_service_user_is_root:{name}")

    cap_drop = svc.get("cap_drop", [])
    if "ALL" not in cap_drop:
        findings.append(f"runtime_service_cap_drop_missing_all:{name}")

    sec_opts = svc.get("security_opt", [])
    if "no-new-privileges:true" not in sec_opts:
        findings.append(f"runtime_service_security_opt_missing_no_new_privileges:{name}")

    if not bool(svc.get("read_only", False)):
        findings.append(f"runtime_service_read_only_not_enabled:{name}")

    tmpfs = svc.get("tmpfs", [])
    if not isinstance(tmpfs, list) or not any("/tmp" in str(item) and "noexec" in str(item) for item in tmpfs):
        findings.append(f"runtime_service_tmpfs_tmp_hardening_missing:{name}")

    pids_limit = svc.get("pids_limit")
    if not isinstance(pids_limit, int) or pids_limit <= 0:
        findings.append(f"runtime_service_pids_limit_invalid:{name}")

    ulimits = svc.get("ulimits", {})
    nofile = (ulimits or {}).get("nofile", {})
    soft = nofile.get("soft")
    hard = nofile.get("hard")
    if soft is None or hard is None:
        findings.append(f"runtime_service_ulimit_nofile_missing:{name}")
    soft_num = _parse_numeric_or_env(soft)
    hard_num = _parse_numeric_or_env(hard)
    if soft_num is not None and soft_num < 8192:
        findings.append(f"runtime_service_ulimit_nofile_soft_too_low:{name}:{soft_num}")
    if hard_num is not None and hard_num < 8192:
        findings.append(f"runtime_service_ulimit_nofile_hard_too_low:{name}:{hard_num}")
    nproc = (ulimits or {}).get("nproc")
    nproc_num = _parse_numeric_or_env(nproc)
    if nproc is None:
        warnings.append(f"runtime_service_ulimit_nproc_missing:{name}")
    elif nproc_num is not None and nproc_num < 1024:
        warnings.append(f"runtime_service_ulimit_nproc_low:{name}:{nproc_num}")

    env = _env_to_map(svc.get("environment", {}))
    if env.get("PYTHONUNBUFFERED", "") != "1":
        findings.append(f"runtime_service_pythonunbuffered_not_set:{name}")
    if env.get("PYTHONFAULTHANDLER", "") != "1":
        findings.append(f"runtime_service_pythonfaulthandler_not_set:{name}")
    if env.get("MALLOC_ARENA_MAX", "") != "2":
        warnings.append(f"runtime_service_malloc_arena_max_not_2:{name}")

    logging_cfg = svc.get("logging", {})
    if str(logging_cfg.get("driver", "")) != "json-file":
        warnings.append(f"runtime_service_logging_driver_not_json_file:{name}")
    opts = logging_cfg.get("options", {}) if isinstance(logging_cfg, dict) else {}
    if "max-size" not in opts or "max-file" not in opts:
        warnings.append(f"runtime_service_logging_rotation_not_set:{name}")

    _check_volumes(name, svc, findings, warnings)


def _check_metrics_localhost(services: Dict[str, Any], findings: List[str]) -> None:
    maker = services.get("bro-maker")
    if not isinstance(maker, dict):
        return
    ports = maker.get("ports", [])
    for raw in ports or []:
        text = str(raw).strip()
        if not text:
            continue
        if text.startswith("127.0.0.1:"):
            continue
        findings.append(f"runtime_service_metrics_bind_not_localhost:bro-maker:{text}")


def _check_guardian_dependency(services: Dict[str, Any], findings: List[str]) -> None:
    guardian = services.get("bro-guardian")
    if not isinstance(guardian, dict):
        return
    depends_on = guardian.get("depends_on")
    if not isinstance(depends_on, dict):
        findings.append("runtime_service_depends_on_missing:bro-guardian")
        return
    maker_dep = depends_on.get("bro-maker")
    if not isinstance(maker_dep, dict):
        findings.append("runtime_service_depends_on_missing_maker_health:bro-guardian")
        return
    condition = str(maker_dep.get("condition", "")).strip()
    if condition != "service_healthy":
        findings.append(f"runtime_service_depends_on_condition_invalid:bro-guardian:{condition or 'missing'}")


def _probe_dir(path: pathlib.Path, label: str, findings: List[str], warnings: List[str]) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        findings.append(f"runtime_host_dir_mkdir_failed:{label}:{path}:{exc.__class__.__name__}")
        return

    try:
        mode_bits = stat.S_IMODE(path.stat().st_mode)
        if mode_bits & stat.S_IWOTH:
            findings.append(f"runtime_host_dir_world_writable:{label}:{path}:{oct(mode_bits)}")
        elif mode_bits & stat.S_IROTH:
            warnings.append(f"runtime_host_dir_world_readable:{label}:{path}:{oct(mode_bits)}")
    except OSError as exc:
        warnings.append(f"runtime_host_dir_stat_failed:{label}:{path}:{exc.__class__.__name__}")

    probe = path / ".bro_runtime_probe.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        findings.append(f"runtime_host_dir_not_writable:{label}:{path}:{exc.__class__.__name__}")


def run_audit(*, compose_path: pathlib.Path, log_dir: pathlib.Path, data_dir: pathlib.Path) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    compose = _load_compose(compose_path)
    services = compose.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("compose file requires services mapping")

    for service_name in ("bro-maker", "bro-guardian"):
        service = services.get(service_name)
        if not isinstance(service, dict):
            findings.append(f"runtime_service_missing:{service_name}")
            continue
        _check_service(service_name, service, findings, warnings)

    _check_metrics_localhost(services, findings)
    _check_guardian_dependency(services, findings)
    _probe_dir(log_dir.resolve(), "host_log_dir", findings, warnings)
    _probe_dir(data_dir.resolve(), "host_data_dir", findings, warnings)

    nofile_soft, nofile_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if nofile_soft < 8192:
        findings.append(f"runtime_host_nofile_soft_too_low:{nofile_soft}")
    if nofile_hard < 8192:
        findings.append(f"runtime_host_nofile_hard_too_low:{nofile_hard}")

    return {
        "compose_path": str(compose_path.resolve()),
        "log_dir": str(log_dir.resolve()),
        "data_dir": str(data_dir.resolve()),
        "nofile_soft": int(nofile_soft),
        "nofile_hard": int(nofile_hard),
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro runtime hardening audit")
    parser.add_argument("--compose", default="./docker-compose.yml", help="docker-compose file path")
    parser.add_argument("--log-dir", default=os.getenv("BRO_LOG_DIR", "./logs_exec"), help="host log dir")
    parser.add_argument("--data-dir", default=os.getenv("BRO_DATA_DIR", "./data"), help="host data dir")
    parser.add_argument("--out", default="", help="optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        compose_path=pathlib.Path(args.compose),
        log_dir=pathlib.Path(args.log_dir),
        data_dir=pathlib.Path(args.data_dir),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out = pathlib.Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
