from __future__ import annotations

import ipaddress
import os
import pathlib
import stat
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse


_TLS_SCHEMES = {"https", "wss"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PRIVATE_SUFFIXES = (".internal", ".local", ".localdomain", ".lan", ".home.arpa")


def run_security_checks(cfg: Dict[str, Any], *, mode: str) -> List[str]:
    security_cfg = cfg.get("security", {})
    if not bool(security_cfg.get("enabled", True)):
        return []

    findings: List[str] = []
    allowlist = _normalize_host_set(security_cfg.get("allowed_hosts", []))
    enforce_allowlist = bool(security_cfg.get("enforce_host_allowlist", True))
    require_tls = bool(security_cfg.get("require_tls", True))
    block_private = bool(security_cfg.get("block_private_network_hosts", True))

    for label, url in _configured_urls(cfg):
        findings.extend(
            _validate_remote_url(
                label=label,
                url=url,
                require_tls=require_tls,
                block_private_network_hosts=block_private,
                enforce_host_allowlist=enforce_allowlist,
                allowed_hosts=allowlist,
            )
        )

    if bool(security_cfg.get("enforce_storage_roots", True)):
        findings.extend(_check_storage_roots(cfg, security_cfg))

    if bool(security_cfg.get("check_path_symlinks", True)):
        findings.extend(_check_symlink_paths(cfg))

    if bool(security_cfg.get("check_file_permissions", True)):
        findings.extend(_check_file_permissions(cfg))

    findings.extend(_check_metrics_bind(cfg, security_cfg, mode=mode))
    findings.extend(_check_runtime_identity(security_cfg, mode=mode))
    return findings


def _configured_urls(cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    urls: List[Tuple[str, str]] = []
    auth = cfg.get("auth", {})
    market_data = cfg.get("market_data", {})
    chainlink = cfg.get("chainlink", {})
    discovery = cfg.get("targets", {}).get("discovery", {})
    preflight = cfg.get("preflight", {})

    _append_nonempty(urls, "auth.host", auth.get("host"))
    _append_nonempty(urls, "market_data.clob_url", market_data.get("clob_url"))
    _append_nonempty(urls, "market_data.ws.url", market_data.get("ws", {}).get("url"))
    _append_nonempty(urls, "chainlink.ws_url", chainlink.get("ws_url"))
    if bool(discovery.get("enabled", False)):
        _append_nonempty(urls, "targets.discovery.gamma_url", discovery.get("gamma_url"))

    endpoint_urls = preflight.get("endpoint_urls", [])
    if isinstance(endpoint_urls, list):
        for idx, raw in enumerate(endpoint_urls):
            _append_nonempty(urls, f"preflight.endpoint_urls[{idx}]", raw)

    alerts = cfg.get("alerts", {})
    if bool(alerts.get("enabled", False)):
        env_name = str(alerts.get("webhook_url_env", "POLY_BOT_ALERT_WEBHOOK")).strip()
        if env_name:
            _append_nonempty(urls, f"env:{env_name}", os.getenv(env_name))
    return urls


def _append_nonempty(out: List[Tuple[str, str]], label: str, raw_value: Any) -> None:
    value = str(raw_value or "").strip()
    if value:
        out.append((label, value))


def _normalize_host_set(values: Any) -> Set[str]:
    out: Set[str] = set()
    if not isinstance(values, list):
        return out
    for raw in values:
        host = str(raw or "").strip().lower().rstrip(".")
        if host:
            out.add(host)
    return out


def _validate_remote_url(
    *,
    label: str,
    url: str,
    require_tls: bool,
    block_private_network_hosts: bool,
    enforce_host_allowlist: bool,
    allowed_hosts: Set[str],
) -> List[str]:
    findings: List[str] = []
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").strip().lower()
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not scheme or not host:
        return [f"security.invalid_url:{label}"]
    if parsed.username or parsed.password:
        findings.append(f"security.url_credentials_not_allowed:{label}")
    if require_tls and scheme not in _TLS_SCHEMES:
        findings.append(f"security.insecure_scheme:{label}:{scheme}")
    if block_private_network_hosts and _is_private_or_local_host(host):
        findings.append(f"security.private_host_blocked:{label}:{host}")
    if enforce_host_allowlist and host not in allowed_hosts:
        findings.append(f"security.host_not_allowlisted:{label}:{host}")
    return findings


def _is_private_or_local_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    if not normalized:
        return True
    if normalized in _LOCAL_HOSTS:
        return True
    if normalized.endswith(_PRIVATE_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_storage_roots(cfg: Dict[str, Any], security_cfg: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    roots_raw = security_cfg.get("allowed_storage_roots", [])
    roots = _resolved_roots(roots_raw)
    if not roots:
        return ["security.allowed_storage_roots_empty"]

    storage = cfg.get("storage", {})
    runtime = cfg.get("runtime", {})
    to_check: List[Tuple[str, pathlib.Path]] = [
        ("storage.log_dir", pathlib.Path(str(storage.get("log_dir", ""))).resolve()),
        ("storage.state_path", pathlib.Path(str(storage.get("state_path", ""))).resolve()),
    ]
    guard_stop_raw = str(runtime.get("guard_stop_file", "")).strip()
    if guard_stop_raw:
        to_check.append(("runtime.guard_stop_file", pathlib.Path(guard_stop_raw).resolve()))

    for label, path in to_check:
        if not _path_within_any_root(path, roots):
            findings.append(f"security.path_outside_allowed_roots:{label}:{path}")
    return findings


def _resolved_roots(roots_raw: Any) -> List[pathlib.Path]:
    roots: List[pathlib.Path] = []
    if not isinstance(roots_raw, list):
        return roots
    for raw in roots_raw:
        text = str(raw or "").strip()
        if not text:
            continue
        roots.append(pathlib.Path(text).resolve())
    return roots


def _path_within_any_root(path: pathlib.Path, roots: Sequence[pathlib.Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _check_symlink_paths(cfg: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    storage = cfg.get("storage", {})
    runtime = cfg.get("runtime", {})

    paths: List[Tuple[str, pathlib.Path]] = [
        ("storage.log_dir", pathlib.Path(str(storage.get("log_dir", ""))).expanduser()),
        ("storage.state_path", pathlib.Path(str(storage.get("state_path", ""))).expanduser()),
    ]
    guard_stop_raw = str(runtime.get("guard_stop_file", "")).strip()
    if guard_stop_raw:
        paths.append(("runtime.guard_stop_file", pathlib.Path(guard_stop_raw).expanduser()))

    for label, path in paths:
        abs_path = path if path.is_absolute() else (pathlib.Path.cwd() / path)
        if _path_or_parent_is_symlink(abs_path):
            findings.append(f"security.symlink_path_blocked:{label}:{abs_path}")
    return findings


def _path_or_parent_is_symlink(path: pathlib.Path) -> bool:
    chain: List[pathlib.Path] = [path]
    chain.extend(path.parents)
    for candidate in chain:
        try:
            if candidate.exists() and candidate.is_symlink():
                return True
        except OSError:
            continue
    return False


def _check_file_permissions(cfg: Dict[str, Any]) -> List[str]:
    # Permission bit checks are POSIX only.
    if os.name != "posix":
        return []

    findings: List[str] = []
    storage = cfg.get("storage", {})
    runtime = cfg.get("runtime", {})
    paths: List[Tuple[str, pathlib.Path]] = [
        ("storage.state_path", pathlib.Path(str(storage.get("state_path", ""))).resolve()),
    ]
    guard_stop_raw = str(runtime.get("guard_stop_file", "")).strip()
    if guard_stop_raw:
        paths.append(("runtime.guard_stop_file", pathlib.Path(guard_stop_raw).resolve()))

    for label, path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            mode_bits = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            findings.append(f"security.permission_check_failed:{label}:{exc.__class__.__name__}")
            continue
        if mode_bits & (stat.S_IWGRP | stat.S_IWOTH):
            findings.append(f"security.file_permissions_too_open:{label}:{oct(mode_bits)}")
    return findings


def _check_metrics_bind(cfg: Dict[str, Any], security_cfg: Dict[str, Any], *, mode: str) -> List[str]:
    if mode != "live":
        return []
    if not bool(security_cfg.get("enforce_local_metrics_bind_in_live", True)):
        return []
    metrics = cfg.get("metrics", {})
    if not bool(metrics.get("enabled", False)):
        return []
    host = str(metrics.get("host", "")).strip().lower()
    if host in _LOCAL_HOSTS:
        return []
    return [f"security.metrics_bind_not_local:{host}"]


def _check_runtime_identity(security_cfg: Dict[str, Any], *, mode: str) -> List[str]:
    if os.name != "posix":
        return []
    if not hasattr(os, "geteuid"):
        return []
    if os.geteuid() != 0:
        return []
    if mode == "live" and not bool(security_cfg.get("allow_root_user_in_live", False)):
        return ["security.running_as_root_live"]
    if mode != "live" and not bool(security_cfg.get("allow_root_user_in_paper", True)):
        return ["security.running_as_root_paper"]
    return []
