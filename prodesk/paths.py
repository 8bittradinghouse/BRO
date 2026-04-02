from __future__ import annotations

import os
import pathlib
import uuid
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional


def _as_bool_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "y"}


def docker_mode_enabled() -> bool:
    return _as_bool_env(os.getenv("BRO_DOCKER_MODE"))


def _expand(raw: Any) -> str:
    return os.path.expanduser(os.path.expandvars(str(raw or "").strip()))


def _is_absolute_path_text(path_text: str) -> bool:
    if not path_text:
        return False
    if path_text.startswith("/"):
        return True
    return pathlib.Path(path_text).is_absolute()


def _resolve_docker_path(raw: str, *, category_root: str, default_abs: str) -> str:
    text = _expand(raw)
    if not text:
        return default_abs
    if _is_absolute_path_text(text):
        # Keep absolute paths as-is in docker mode, but disallow /app writes.
        normalized = str(PurePosixPath(text))
        return normalized
    clean = text.lstrip("./")
    return str(PurePosixPath(category_root) / PurePosixPath(clean))


def _resolve_host_path(raw: str, *, config_dir: pathlib.Path, default_relative: Optional[str] = None) -> str:
    text = _expand(raw)
    if not text and default_relative:
        text = default_relative
    if not text:
        return str(config_dir.resolve())
    p = pathlib.Path(text)
    if p.is_absolute():
        return str(p.resolve())
    return str((config_dir / p).resolve())


def _ensure_dir(path_text: str, *, ignore_permission_denied: bool = False) -> None:
    try:
        pathlib.Path(path_text).mkdir(parents=True, exist_ok=True, mode=0o750)
    except PermissionError:
        if ignore_permission_denied:
            return
        raise


def _assert_not_app_write(path_text: str, *, key: str) -> None:
    normalized = str(PurePosixPath(path_text))
    if normalized == "/app" or normalized.startswith("/app/"):
        raise ValueError(
            f"{key} resolved to {normalized} under /app while BRO_DOCKER_MODE=1; "
            "use /logs or /data mounted paths"
        )


def _is_container_mount_path(path_text: str) -> bool:
    normalized = str(PurePosixPath(path_text))
    return (
        normalized == "/logs"
        or normalized.startswith("/logs/")
        or normalized == "/data"
        or normalized.startswith("/data/")
    )


def _asset_mode_scope(cfg: Dict[str, Any]) -> str:
    asset_from_env = str(os.getenv("BRO_ASSET", "")).strip().lower()
    asset = asset_from_env or str(cfg.get("asset", {}).get("symbol", "btc")).strip().lower() or "btc"
    mode = str(cfg.get("mode", "paper")).strip().lower() or "paper"
    safe_asset = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in asset)
    safe_mode = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in mode)
    return f"{safe_asset}_{safe_mode}"


def normalize_execution_paths(cfg: Dict[str, Any], *, config_path: pathlib.Path) -> Dict[str, Any]:
    docker_mode = docker_mode_enabled()
    cfg_dir = config_path.parent.resolve()
    scope = _asset_mode_scope(cfg)

    default_log_dir = f"/logs/{scope}" if docker_mode else cfg.get("storage", {}).get("log_dir", "./logs_exec")
    default_state_path = f"/data/{scope}/state.json" if docker_mode else cfg.get("storage", {}).get("state_path", "./logs_exec/state.json")
    default_guard_path = f"/logs/{scope}/guard_stop.txt" if docker_mode else str(cfg.get("runtime", {}).get("guard_stop_file", "") or "")
    default_reconcile_path = f"/logs/{scope}/reconcile_latest.json" if docker_mode else str(cfg.get("ramp", {}).get("reconcile_status_path", "") or "")

    storage = cfg.setdefault("storage", {})
    runtime = cfg.setdefault("runtime", {})
    ramp = cfg.setdefault("ramp", {})
    alerts = cfg.setdefault("alerts", {})
    security = cfg.setdefault("security", {})

    if docker_mode:
        storage["log_dir"] = _resolve_docker_path(
            str(storage.get("log_dir", "")),
            category_root="/logs",
            default_abs=default_log_dir,
        )
        storage["state_path"] = _resolve_docker_path(
            str(storage.get("state_path", "")),
            category_root="/data",
            default_abs=default_state_path,
        )
        runtime["guard_stop_file"] = _resolve_docker_path(
            str(runtime.get("guard_stop_file", "")),
            category_root="/logs",
            default_abs=default_guard_path,
        )
        ramp["reconcile_status_path"] = _resolve_docker_path(
            str(ramp.get("reconcile_status_path", "")),
            category_root="/logs",
            default_abs=default_reconcile_path,
        )
        guardian_hook = str(alerts.get("guardian_hook_file", "")).strip()
        if guardian_hook:
            alerts["guardian_hook_file"] = _resolve_docker_path(
                guardian_hook,
                category_root="/logs",
                default_abs=str(PurePosixPath(storage["log_dir"]) / "guardian_hook.jsonl"),
            )
        for key_name, path_text in (
            ("storage.log_dir", str(storage["log_dir"])),
            ("storage.state_path", str(storage["state_path"])),
            ("runtime.guard_stop_file", str(runtime["guard_stop_file"])),
            ("ramp.reconcile_status_path", str(ramp["reconcile_status_path"])),
        ):
            _assert_not_app_write(path_text, key=key_name)
        roots = security.get("allowed_storage_roots", [])
        if not isinstance(roots, list):
            roots = []
        absolute_roots = {str(PurePosixPath(_expand(root))) for root in roots if str(root).strip()}
        absolute_roots.update({"/logs", "/data"})
        security["allowed_storage_roots"] = sorted(absolute_roots)
    else:
        storage["log_dir"] = _resolve_host_path(
            str(storage.get("log_dir", "")),
            config_dir=cfg_dir,
            default_relative="./logs_exec",
        )
        storage["state_path"] = _resolve_host_path(
            str(storage.get("state_path", "")),
            config_dir=cfg_dir,
            default_relative="./logs_exec/state.json",
        )
        guard_raw = str(runtime.get("guard_stop_file", "")).strip()
        if guard_raw:
            runtime["guard_stop_file"] = _resolve_host_path(guard_raw, config_dir=cfg_dir)
        reconcile_raw = str(ramp.get("reconcile_status_path", "")).strip()
        if reconcile_raw:
            ramp["reconcile_status_path"] = _resolve_host_path(reconcile_raw, config_dir=cfg_dir)
        hook_raw = str(alerts.get("guardian_hook_file", "")).strip()
        if hook_raw:
            alerts["guardian_hook_file"] = _resolve_host_path(hook_raw, config_dir=cfg_dir)
        roots_raw = security.get("allowed_storage_roots", [])
        if isinstance(roots_raw, list):
            normalized_roots = []
            for root in roots_raw:
                root_text = str(root or "").strip()
                if not root_text:
                    continue
                normalized_roots.append(_resolve_host_path(root_text, config_dir=cfg_dir))
            if normalized_roots:
                security["allowed_storage_roots"] = normalized_roots

    # In docker mode these resolve to container mount points (/logs, /data).
    # Unit tests and host-side tooling may not have permission to create them.
    allow_permission_skip = docker_mode
    if _is_container_mount_path(str(storage["log_dir"])) or _is_container_mount_path(str(storage["state_path"])):
        allow_permission_skip = True
    _ensure_dir(str(storage["log_dir"]), ignore_permission_denied=allow_permission_skip)
    _ensure_dir(
        str(pathlib.Path(str(storage["state_path"])).parent),
        ignore_permission_denied=allow_permission_skip,
    )
    guard_path = str(runtime.get("guard_stop_file", "")).strip()
    if guard_path:
        _ensure_dir(
            str(pathlib.Path(guard_path).parent),
            ignore_permission_denied=allow_permission_skip,
        )
    reconcile_path = str(ramp.get("reconcile_status_path", "")).strip()
    if reconcile_path:
        _ensure_dir(
            str(pathlib.Path(reconcile_path).parent),
            ignore_permission_denied=allow_permission_skip,
        )
    hook_path = str(alerts.get("guardian_hook_file", "")).strip()
    if hook_path:
        _ensure_dir(
            str(pathlib.Path(hook_path).parent),
            ignore_permission_denied=allow_permission_skip,
        )
    return cfg


def validate_runtime_write_paths(cfg: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    storage = cfg.get("storage", {})
    runtime = cfg.get("runtime", {})
    ramp = cfg.get("ramp", {})

    def _probe_write_dir(path_text: str, *, label: str) -> None:
        path = pathlib.Path(path_text).resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            findings.append(
                f"path_not_writable:{label}:{path}:mkdir_failed:{exc}; use mounted /logs or /data paths"
            )
            return
        probe = path / f".bro_write_probe_{uuid.uuid4().hex}.tmp"
        try:
            probe.write_text("probe", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            findings.append(
                f"path_not_writable:{label}:{path}:write_failed:{exc}; use mounted /logs or /data paths"
            )

    log_dir = str(storage.get("log_dir", "")).strip()
    state_path = str(storage.get("state_path", "")).strip()
    guard_stop_file = str(runtime.get("guard_stop_file", "")).strip()
    reconcile_status_path = str(ramp.get("reconcile_status_path", "")).strip()

    if log_dir:
        _probe_write_dir(log_dir, label="storage.log_dir")
    if state_path:
        _probe_write_dir(str(pathlib.Path(state_path).resolve().parent), label="storage.state_path_parent")
    if guard_stop_file:
        _probe_write_dir(str(pathlib.Path(guard_stop_file).resolve().parent), label="runtime.guard_stop_file_parent")
    if reconcile_status_path:
        _probe_write_dir(
            str(pathlib.Path(reconcile_status_path).resolve().parent),
            label="ramp.reconcile_status_path_parent",
        )

    return findings
