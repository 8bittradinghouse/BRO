from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional


def _env_bool(value: str) -> Optional[bool]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return None


def resolve_repo_root(*, start: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Resolve repo root in a path-agnostic manner."""
    env_root = str(os.getenv("BRO_REPO_ROOT", "")).strip()
    if env_root:
        return pathlib.Path(env_root).expanduser().resolve()

    cursor = (start or pathlib.Path.cwd()).resolve()
    for candidate in [cursor, *cursor.parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cursor


def current_git_commit(repo_root: pathlib.Path) -> str:
    env_commit = str(os.getenv("BRO_GIT_COMMIT", "")).strip()
    if env_commit:
        return env_commit
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""
    return out.strip()


def current_git_dirty(repo_root: pathlib.Path) -> bool:
    env_dirty = _env_bool(str(os.getenv("BRO_GIT_DIRTY", "")))
    if env_dirty is not None:
        return bool(env_dirty)
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False
    return bool(out.strip())
