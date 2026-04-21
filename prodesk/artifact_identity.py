from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional, Tuple


def _load_manifest(log_dir: pathlib.Path, run_id: Optional[str]) -> Tuple[Dict[str, Any], bool, str]:
    target = str(run_id or "").strip()
    if not target:
        return {}, False, "run_id_missing"
    exact = log_dir / f"run_manifest_{target}.json"
    if not exact.exists():
        return {}, False, "manifest_missing"
    try:
        payload = json.loads(exact.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        return {}, True, f"manifest_invalid_json:{exc.__class__.__name__}"
    if not isinstance(payload, dict):
        return {}, True, "manifest_invalid_root"
    return payload, True, ""


def resolve_latest_run_id(log_dir: pathlib.Path) -> str:
    raise ValueError(
        "latest_run_resolution_forbidden:use_explicit_run_id"
    )


def resolve_run_id(*, log_dir: pathlib.Path, run_id: Optional[str]) -> Optional[str]:
    explicit = str(run_id or "").strip()
    if explicit:
        return explicit
    return None


def build_artifact_identity(*, log_dir: pathlib.Path, run_id: Optional[str]) -> Dict[str, Any]:
    manifest, manifest_present, manifest_load_error = _load_manifest(log_dir.resolve(), run_id)
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    profile = config.get("profile") if isinstance(config.get("profile"), dict) else {}
    runtime_identity = (
        manifest.get("runtime_identity")
        if isinstance(manifest.get("runtime_identity"), dict)
        else {}
    )
    return {
        "run_id": str(manifest.get("run_id") or (run_id or "")),
        "manifest_schema_version": int(manifest.get("manifest_schema_version") or 0),
        "profile_name": str(profile.get("name") or runtime_identity.get("profile_name") or ""),
        "config_fingerprint_sha256": str(manifest.get("config_fingerprint_sha256") or ""),
        "code_fingerprint_sha256": str(manifest.get("code_fingerprint_sha256") or ""),
        "dependency_lock_sha256": str(runtime_identity.get("dependency_lock_sha256") or ""),
        "git_commit": str(runtime_identity.get("git_commit") or ""),
        "git_dirty": bool(runtime_identity.get("git_dirty", False)),
        "docker_image_hash": str(runtime_identity.get("docker_image_hash") or ""),
        "config_source_path": str(manifest.get("config_source_path") or ""),
        "config_source_sha256": str(manifest.get("config_source_sha256") or ""),
        "manifest_present": bool(manifest_present),
        "manifest_load_error": str(manifest_load_error or ""),
    }


def candidate_run_log_dirs(*, log_dir: pathlib.Path, run_id: str, max_depth: int = 2, max_results: int = 8) -> List[str]:
    """Return candidate log directories that contain run_manifest_<run_id>.json.

    This helper is intentionally diagnostic only; callers must still require
    explicit --log-dir/--run-id and must not auto-switch directories.
    """

    resolved = log_dir.resolve()
    target_run_id = str(run_id or "").strip()
    if not target_run_id:
        return []
    if max_depth < 0:
        max_depth = 0
    if max_results <= 0:
        return []

    base_depth = len(resolved.parts)
    target_name = f"run_manifest_{target_run_id}.json"
    out: List[str] = []
    seen: set[str] = set()

    for path in resolved.rglob(target_name):
        parent = path.parent.resolve()
        depth_from_base = len(parent.parts) - base_depth
        if depth_from_base < 0 or depth_from_base > max_depth:
            continue
        text = str(parent)
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max_results:
            break
    return sorted(out)
