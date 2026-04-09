#!/usr/bin/env python3
"""Bounded doctrine-truth audit for canonical wallet authority surfaces.

This audit is intentionally narrow:
- explicit allowlist only
- phrase-source matching from one canonical source
- targeted deprecated-surface checks for known authority-path functions
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]

CANONICAL_PHRASE_SOURCE = Path("docs/DOCTRINE_LIMITATION_PHRASES.json")

# Explicitly bounded canonical audit scope (no handoffs/archive scanning).
CANONICAL_ALLOWLIST: Sequence[Path] = (
    Path("docs/DOCTRINE_RUNBOOK.md"),
    Path("docs/BASELINE_LOCK_20260408.md"),
    Path("docs/WALLET_SEMANTIC_BOUNDARY_CHANGES.md"),
    Path("scripts/nightly_soak_report.py"),
    Path("prodesk/wallet/wallet_health.py"),
)

# Canonical docs that must carry the phrase block exactly.
CANONICAL_PHRASE_REQUIRED_DOCS: Sequence[Path] = (
    Path("docs/DOCTRINE_RUNBOOK.md"),
    Path("docs/BASELINE_LOCK_20260408.md"),
)

# Report/status fields that must remain visible to prevent authority-class misread.
REPORT_REQUIRED_FIELDS: Sequence[str] = (
    "authority_status_class",
    "order_capable_live",
    "order_submit_eligible",
    "canonical_live_nonce_available",
    "canonical_live_pending_wallet_tx_available",
    "live_truth_gap_reasons",
)


@dataclass(frozen=True)
class TargetedDeprecatedSurfaceGuard:
    path: Path
    function_name: str
    forbidden_patterns: Sequence[str]


TARGETED_DEPRECATED_SURFACE_GUARDS: Sequence[TargetedDeprecatedSurfaceGuard] = (
    TargetedDeprecatedSurfaceGuard(
        path=Path("prodesk/wallet/wallet_health.py"),
        function_name="build_wallet_health_contract",
        forbidden_patterns=(
            'status.get("wallet_snapshot")',
            'status.get("allowance_snapshot")',
            'status.get("nonce_snapshot")',
            'status.get("pending_tx_snapshot")',
        ),
    ),
    TargetedDeprecatedSurfaceGuard(
        path=Path("prodesk/wallet/wallet_controller.py"),
        function_name="authorize_intent",
        forbidden_patterns=(
            '.get("wallet_snapshot")',
            '.get("allowance_snapshot")',
            '.get("nonce_snapshot")',
            '.get("pending_tx_snapshot")',
        ),
    ),
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_phrase_source(repo_root: Path) -> Dict[str, str]:
    source_path = repo_root / CANONICAL_PHRASE_SOURCE
    payload = json.loads(_read_text(source_path))
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"invalid phrase source payload: {source_path}")
    phrases: Dict[str, str] = {}
    for key, value in payload.items():
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not k or not v:
            raise ValueError(f"invalid phrase source entry: {key!r}={value!r}")
        phrases[k] = v
    return phrases


def _function_source_segment(path: Path, function_name: str) -> str:
    text = _read_text(path)
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            if node.end_lineno is None:
                continue
            start = max(1, int(node.lineno))
            end = max(start, int(node.end_lineno))
            return "\n".join(lines[start - 1 : end])
    raise ValueError(f"function_not_found:{path}:{function_name}")


def run_audit(*, repo_root: Path = REPO_ROOT) -> Dict[str, object]:
    errors: List[str] = []
    findings: List[str] = []

    for rel_path in CANONICAL_ALLOWLIST:
        path = repo_root / rel_path
        if not path.exists():
            errors.append(f"allowlist_missing:{rel_path}")
        else:
            findings.append(f"allowlist_checked:{rel_path}")

    try:
        phrase_map = _load_phrase_source(repo_root)
    except Exception as exc:  # pragma: no cover - covered via tests through run_audit result
        return {
            "ok": False,
            "errors": [f"phrase_source_load_failed:{exc}"],
            "findings": findings,
            "phrase_source": str(CANONICAL_PHRASE_SOURCE),
            "allowlist": [str(p) for p in CANONICAL_ALLOWLIST],
        }

    phrase_hits: Dict[str, List[str]] = {key: [] for key in phrase_map}
    for rel_path in CANONICAL_ALLOWLIST:
        path = repo_root / rel_path
        if not path.exists():
            continue
        text = _read_text(path)
        for key, phrase in phrase_map.items():
            if phrase in text:
                phrase_hits[key].append(str(rel_path))

    for key, matches in phrase_hits.items():
        if not matches:
            errors.append(f"phrase_missing_allowlist:{key}:{phrase_map[key]}")
        else:
            findings.append(f"phrase_allowlist_match:{key}:{','.join(matches)}")

    for rel_path in CANONICAL_PHRASE_REQUIRED_DOCS:
        path = repo_root / rel_path
        if not path.exists():
            errors.append(f"phrase_required_doc_missing:{rel_path}")
            continue
        text = _read_text(path)
        for key, phrase in phrase_map.items():
            if phrase not in text:
                errors.append(f"phrase_required_doc_mismatch:{rel_path}:{key}")

    report_path = repo_root / "scripts/nightly_soak_report.py"
    if report_path.exists():
        report_text = _read_text(report_path)
        for field in REPORT_REQUIRED_FIELDS:
            if field not in report_text:
                errors.append(f"report_field_missing:{field}")
    else:
        errors.append("report_file_missing:scripts/nightly_soak_report.py")

    for guard in TARGETED_DEPRECATED_SURFACE_GUARDS:
        path = repo_root / guard.path
        if not path.exists():
            errors.append(f"guard_path_missing:{guard.path}")
            continue
        try:
            segment = _function_source_segment(path, guard.function_name)
        except Exception as exc:
            errors.append(f"guard_function_missing:{guard.path}:{guard.function_name}:{exc}")
            continue
        for pattern in guard.forbidden_patterns:
            if pattern in segment:
                errors.append(
                    f"deprecated_surface_consumed_in_authority_path:{guard.path}:{guard.function_name}:{pattern}"
                )
        findings.append(f"targeted_guard_checked:{guard.path}:{guard.function_name}")

    return {
        "ok": not errors,
        "errors": errors,
        "findings": findings,
        "phrase_source": str(CANONICAL_PHRASE_SOURCE),
        "allowlist": [str(p) for p in CANONICAL_ALLOWLIST],
        "phrase_hits": phrase_hits,
        "targeted_guards": [
            {
                "path": str(g.path),
                "function_name": g.function_name,
                "forbidden_pattern_count": len(g.forbidden_patterns),
            }
            for g in TARGETED_DEPRECATED_SURFACE_GUARDS
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO doctrine-truth bounded audit")
    parser.add_argument("--json-out", default="", help="Optional JSON output path")
    args = parser.parse_args()

    result = run_audit()
    if args.json_out:
        out_path = Path(args.json_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if bool(result.get("ok")) else 1)


if __name__ == "__main__":
    main()
