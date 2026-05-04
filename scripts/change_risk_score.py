#!/usr/bin/env python3
"""Path-based change risk scoring for CI hardening."""

from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
from typing import Any, Dict, Iterable, List


CRITICAL_PATTERNS = (
    "executor.py",
    "observer.py",
    "prodesk/*",
    "docker-compose.yml",
    "configs/*live*.yaml",
    "ops/systemd/*",
    "scripts/prelive_gate.py",
    "scripts/readiness_gate.py",
    "scripts/guardian_watchdog.py",
    "scripts/runtime_hardening_audit.py",
    "scripts/websocket_hardening_audit.py",
)

HIGH_PATTERNS = (
    "scripts/*.py",
    "requirements.txt",
    ".github/workflows/*",
    "Dockerfile",
)

MEDIUM_PATTERNS = (
    "tests/*",
    "README.md",
    "DRILLBOOK.md",
    "SECURITY.md",
)


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _score_path(path: str) -> int:
    p = str(path).strip()
    if not p:
        return 0
    if _matches_any(p, CRITICAL_PATTERNS):
        return 5
    if _matches_any(p, HIGH_PATTERNS):
        return 3
    if _matches_any(p, MEDIUM_PATTERNS):
        return 1
    return 1


def _risk_level(score: int) -> str:
    if score >= 15:
        return "critical"
    if score >= 8:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def run_score(changed_files: List[str]) -> Dict[str, Any]:
    normalized = [str(x).strip() for x in changed_files if str(x).strip()]
    score = sum(_score_path(p) for p in normalized)
    level = _risk_level(score)
    return {
        "changed_file_count": len(normalized),
        "changed_files": normalized,
        "score": int(score),
        "risk_level": level,
        "requires_extra_gates": level in {"high", "critical"},
        "ok": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro CI change-risk scoring")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed path (repeatable)")
    parser.add_argument("--changed-files-file", default="", help="Optional newline-delimited changed files path")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    changed: List[str] = list(args.changed_file or [])
    source = str(args.changed_files_file).strip()
    if source:
        src = pathlib.Path(source).resolve()
        if src.exists():
            changed.extend(src.read_text(encoding="utf-8", errors="ignore").splitlines())

    result = run_score(changed)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
