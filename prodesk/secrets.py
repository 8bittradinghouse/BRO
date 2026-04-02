from __future__ import annotations

import pathlib
import shlex
import subprocess
from typing import Any, Dict, Tuple


class SecretLoadError(RuntimeError):
    pass


def _load_from_env(*, env_name: str, label: str) -> str:
    import os

    value = str(os.getenv(env_name, "")).strip()
    if not value:
        raise SecretLoadError(f"missing env var for {label}: {env_name}")
    return value


def _load_from_file(*, path_raw: Any, label: str) -> str:
    path = pathlib.Path(str(path_raw or "").strip()).expanduser().resolve()
    if not str(path):
        raise SecretLoadError(f"file path missing for {label}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise SecretLoadError(f"failed to read file for {label}: {path}: {exc.__class__.__name__}") from exc
    if not value:
        raise SecretLoadError(f"empty file secret for {label}: {path}")
    return value


def _load_from_manager(*, source: Dict[str, Any], label: str) -> str:
    argv = source.get("argv")
    if isinstance(argv, list) and argv:
        cmd = [str(x) for x in argv]
    else:
        command = str(source.get("command", "")).strip()
        if not command:
            raise SecretLoadError(f"manager command missing for {label}")
        cmd = shlex.split(command)
    timeout_sec = max(0.5, float(source.get("timeout_sec", 5.0)))
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout_sec)
    except Exception as exc:
        raise SecretLoadError(f"manager command failed for {label}: {exc.__class__.__name__}") from exc
    if proc.returncode != 0:
        raise SecretLoadError(f"manager command non-zero for {label}: rc={proc.returncode}")
    value = str(proc.stdout or "").strip()
    if not value:
        raise SecretLoadError(f"manager command empty output for {label}")
    return value


def _resolve_secret(*, label: str, source: Dict[str, Any], legacy_env: str) -> Tuple[str, str]:
    mode = str(source.get("mode", "env")).strip().lower() or "env"
    if mode == "env":
        env_name = str(source.get("env", legacy_env)).strip() or legacy_env
        return _load_from_env(env_name=env_name, label=label), f"env:{env_name}"
    if mode == "file":
        return _load_from_file(path_raw=source.get("path", ""), label=label), "file"
    if mode == "manager":
        return _load_from_manager(source=source, label=label), "manager"
    raise SecretLoadError(f"unsupported secret source mode for {label}: {mode}")


def load_auth_secrets(auth_cfg: Dict[str, Any]) -> Tuple[str, str, Dict[str, str]]:
    private_key_env = str(auth_cfg.get("private_key_env", "POLYMARKET_PRIVATE_KEY")).strip() or "POLYMARKET_PRIVATE_KEY"
    funder_env = str(auth_cfg.get("funder_env", "POLYMARKET_FUNDER")).strip() or "POLYMARKET_FUNDER"

    private_key_source = auth_cfg.get("private_key_source")
    if not isinstance(private_key_source, dict):
        private_key_source = {"mode": "env", "env": private_key_env}

    funder_source = auth_cfg.get("funder_source")
    if not isinstance(funder_source, dict):
        funder_source = {"mode": "env", "env": funder_env}

    private_key, private_meta = _resolve_secret(
        label="private_key",
        source=private_key_source,
        legacy_env=private_key_env,
    )
    funder, funder_meta = _resolve_secret(
        label="funder",
        source=funder_source,
        legacy_env=funder_env,
    )
    return private_key, funder, {"private_key_source": private_meta, "funder_source": funder_meta}
