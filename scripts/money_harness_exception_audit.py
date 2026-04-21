#!/usr/bin/env python3
"""Audit money-harness code for unapproved broad exception/suppress patterns."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    tag: str
    snippet: str


_EXCEPT_BROAD_RE = re.compile(r"^\s*except\s+(?:Exception|BaseException)(?:\s+as\s+[A-Za-z_]\w*)?\s*:")
_SUPPRESS_EXCEPTION_RE = re.compile(r"suppress\(Exception\)")
_SUBPROCESS_RUN_RE = re.compile(r"subprocess\.run\(")
_TIMEOUT_KW_RE = re.compile(r"\btimeout\s*=")

_SCAN_TARGETS: Tuple[str, ...] = ("executor.py", "prodesk", "scripts")


def _allowed_broad_exception(path: pathlib.Path, line_no: int, lines: List[str]) -> bool:
    rel = path.as_posix()
    line = lines[line_no - 1].strip()
    window = "\n".join(lines[max(0, line_no - 3) : min(len(lines), line_no + 6)])
    if rel == "prodesk/wallet/wallet_controller.py" and line in {"except Exception:", "except Exception as exc:"}:
        return "Wallet authority must remain functional even if telemetry emission fails." in window
    if rel == "scripts/canonical_paper_session.py" and line == "except BaseException as exc:":
        return "self._finalize_failure_closeout(exc)" in window
    return False


def _line_findings(path: pathlib.Path, lines: List[str]) -> List[Finding]:
    findings: List[Finding] = []
    for idx, line in enumerate(lines, start=1):
        text = line.strip()
        if _EXCEPT_BROAD_RE.search(line):
            if not _allowed_broad_exception(path, idx, lines):
                findings.append(Finding(path=path.as_posix(), line=idx, tag="broad_exception", snippet=text))
        if _SUPPRESS_EXCEPTION_RE.search(text):
            findings.append(Finding(path=path.as_posix(), line=idx, tag="suppressed_exception", snippet=text))
    return findings


def _call_block_has_timeout(lines: List[str], start_idx: int) -> bool:
    depth = 0
    seen_open = False
    for idx in range(start_idx, min(len(lines), start_idx + 40)):
        text = lines[idx]
        if _TIMEOUT_KW_RE.search(text):
            return True
        for ch in text:
            if ch == "(":
                depth += 1
                seen_open = True
            elif ch == ")" and depth > 0:
                depth -= 1
                if seen_open and depth == 0:
                    return False
    return False


def _subprocess_findings(path: pathlib.Path, lines: List[str]) -> List[Finding]:
    findings: List[Finding] = []
    for idx, line in enumerate(lines):
        text = line.strip()
        if not _SUBPROCESS_RUN_RE.search(text):
            continue
        if _call_block_has_timeout(lines, idx):
            continue
        findings.append(
            Finding(
                path=path.as_posix(),
                line=idx + 1,
                tag="subprocess_without_timeout",
                snippet=text,
            )
        )
    return findings


def _scan_file(path: pathlib.Path, *, root: pathlib.Path) -> List[Finding]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return [
            Finding(
                path=str(path.relative_to(root).as_posix()),
                line=1,
                tag="file_read_error",
                snippet="unable_to_read_file",
            )
        ]
    lines = raw.splitlines()
    rel = path.relative_to(root)
    out = _line_findings(rel, lines)
    out.extend(_subprocess_findings(rel, lines))
    return out


def run_audit(*, repo_root: pathlib.Path) -> Dict[str, Any]:
    all_findings: List[Finding] = []
    scanned_paths: List[str] = []

    for target in _SCAN_TARGETS:
        path = (repo_root / target).resolve()
        if path.is_file():
            scanned_paths.append(str(path.relative_to(repo_root)))
            all_findings.extend(_scan_file(path, root=repo_root))
            continue
        if not path.is_dir():
            continue
        for file_path in sorted(path.rglob("*.py")):
            scanned_paths.append(str(file_path.relative_to(repo_root)))
            all_findings.extend(_scan_file(file_path, root=repo_root))

    findings_payload = [
        {
            "path": finding.path,
            "line": int(finding.line),
            "tag": finding.tag,
            "snippet": finding.snippet,
        }
        for finding in all_findings
    ]

    return {
        "scan_scope": list(_SCAN_TARGETS),
        "scanned_file_count": len(scanned_paths),
        "scanned_paths": scanned_paths,
        "finding_count": len(findings_payload),
        "findings": findings_payload,
        "ok": len(findings_payload) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO money-harness broad-exception audit")
    parser.add_argument("--repo-root", default=".", help="Repository root to scan")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo_root).resolve()
    result = run_audit(repo_root=repo_root)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if bool(result.get("ok", False)) else 2)


if __name__ == "__main__":
    main()
