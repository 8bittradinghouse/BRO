#!/usr/bin/env python3
"""Audit export manifest/payload truth alignment with hard-fail semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import zipfile
from typing import Any, Dict, List, Optional, Set

FORBIDDEN_MANIFEST_PHRASES = (
    "generated after this manifest",
    "approximate",
    "partial match",
    "partial-only",
)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_noise_member(path_text: str) -> Optional[str]:
    normalized = str(path_text or "").strip().lstrip("/")
    if not normalized:
        return None
    wrapped = f"/{normalized}/"
    if "/__pycache__/" in wrapped:
        return "__pycache__"
    if "/.venv/" in wrapped:
        return ".venv"
    if "/node_modules/" in wrapped:
        return "node_modules"
    if normalized.endswith(".pyc"):
        return "*.pyc"
    if normalized.endswith("/.DS_Store") or normalized == ".DS_Store":
        return ".DS_Store"
    if normalized.endswith("/.env") or normalized == ".env":
        return ".env"
    return None


def _manifest_mentions_exact_payload(manifest_text: str, payload_path: pathlib.Path, size_bytes: int) -> bool:
    payload_name = payload_path.name
    payload_render = str(payload_path).replace("\\", "/")
    size_text = str(int(size_bytes))
    line_patterns = [
        rf"{re.escape(payload_name)}[^\n]*{re.escape(size_text)}",
        rf"{re.escape(payload_render)}[^\n]*{re.escape(size_text)}",
        rf"{re.escape(payload_name)}[^\n]*size_bytes={re.escape(size_text)}",
        rf"{re.escape(payload_render)}[^\n]*size_bytes={re.escape(size_text)}",
    ]
    return any(re.search(pattern, manifest_text) is not None for pattern in line_patterns)


def _parse_allow_noise_prefixes(entries: List[str]) -> Dict[str, Set[str]]:
    allow_map: Dict[str, Set[str]] = {}
    for raw in entries:
        text = str(raw or "").strip()
        if not text or ":" not in text:
            continue
        payload_raw, _, prefix_raw = text.partition(":")
        payload_key = payload_raw.strip()
        prefix = prefix_raw.strip().lstrip("/")
        if not payload_key or not prefix:
            continue
        allow_map.setdefault(payload_key, set()).add(prefix)
    return allow_map


def run_audit(
    *,
    manifest_path: pathlib.Path,
    payload_paths: List[pathlib.Path],
    require_manifest_mtime_after_payloads: bool = True,
    allow_noise_prefixes: Optional[Dict[str, Set[str]]] = None,
    forbidden_manifest_phrases: Optional[List[str]] = None,
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    payload_reports: List[Dict[str, Any]] = []

    manifest = manifest_path.resolve()
    if not manifest.exists():
        findings.append(f"manifest_missing:{manifest}")
        return {
            "ok": False,
            "finding_count": len(findings),
            "warning_count": len(warnings),
            "findings": findings,
            "warnings": warnings,
            "manifest_path": str(manifest),
            "payloads": payload_reports,
        }

    manifest_text = manifest.read_text(encoding="utf-8", errors="ignore")
    phrases = forbidden_manifest_phrases if forbidden_manifest_phrases is not None else list(FORBIDDEN_MANIFEST_PHRASES)
    for phrase in phrases:
        phrase_text = str(phrase or "").strip().lower()
        if phrase_text and phrase_text in manifest_text.lower():
            findings.append(f"manifest_forbidden_phrase_present:{phrase_text}")

    manifest_mtime = float(manifest.stat().st_mtime)
    allow_prefix_map = allow_noise_prefixes or {}

    for payload_path in payload_paths:
        resolved = payload_path.resolve()
        report: Dict[str, Any] = {
            "path": str(resolved),
            "exists": resolved.exists(),
            "is_zip": resolved.suffix.lower() == ".zip",
            "size_bytes": 0,
            "sha256": "",
            "mtime_utc": "",
            "manifest_exact_match": False,
            "noise_hits": [],
        }
        if not resolved.exists():
            findings.append(f"payload_missing:{resolved}")
            payload_reports.append(report)
            continue

        stat = resolved.stat()
        report["size_bytes"] = int(stat.st_size)
        report["mtime_utc"] = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        report["sha256"] = _sha256_file(resolved)
        report["manifest_exact_match"] = _manifest_mentions_exact_payload(
            manifest_text=manifest_text,
            payload_path=resolved,
            size_bytes=int(stat.st_size),
        )
        if not bool(report["manifest_exact_match"]):
            findings.append(f"manifest_payload_mismatch:{resolved.name}:size={int(stat.st_size)}")

        if require_manifest_mtime_after_payloads and manifest_mtime < float(stat.st_mtime):
            findings.append(f"manifest_mtime_before_payload:{resolved.name}")

        if report["is_zip"]:
            allow_prefixes = set()
            allow_prefixes.update(allow_prefix_map.get(str(resolved), set()))
            allow_prefixes.update(allow_prefix_map.get(resolved.name, set()))
            try:
                with zipfile.ZipFile(resolved, "r") as zf:
                    for member in zf.namelist():
                        normalized = str(member or "").strip().lstrip("/")
                        if not normalized:
                            continue
                        if any(normalized.startswith(prefix.rstrip("/") + "/") or normalized == prefix.rstrip("/") for prefix in allow_prefixes):
                            continue
                        noise_class = _is_noise_member(normalized)
                        if noise_class is not None:
                            report["noise_hits"].append({"member": normalized, "noise_class": noise_class})
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                findings.append(f"zip_scan_failed:{resolved.name}:{exc.__class__.__name__}")
            if report["noise_hits"]:
                findings.append(f"zip_noise_present:{resolved.name}:count={len(report['noise_hits'])}")

        payload_reports.append(report)

    return {
        "ok": len(findings) == 0,
        "manifest_path": str(manifest),
        "manifest_sha256": _sha256_file(manifest),
        "require_manifest_mtime_after_payloads": bool(require_manifest_mtime_after_payloads),
        "payloads": payload_reports,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO export truth hard-fail audit")
    parser.add_argument("--manifest", required=True, help="Export manifest path")
    parser.add_argument(
        "--payload",
        action="append",
        default=[],
        help="Payload file path (repeatable, include all exported artifacts)",
    )
    parser.add_argument(
        "--allow-noise-prefix",
        action="append",
        default=[],
        help="Optional noise allowlist override in form '<payload_name_or_path>:<zip_member_prefix>'",
    )
    parser.add_argument(
        "--forbidden-manifest-phrase",
        action="append",
        default=[],
        help="Optional extra forbidden manifest phrase (repeatable)",
    )
    parser.add_argument(
        "--skip-manifest-mtime-check",
        action="store_true",
        help="Disable manifest mtime >= payload mtime requirement",
    )
    parser.add_argument("--out", default="", help="Optional JSON output path")
    args = parser.parse_args()

    extra_phrases = [str(x).strip() for x in args.forbidden_manifest_phrase if str(x).strip()]
    phrase_list = list(FORBIDDEN_MANIFEST_PHRASES) + extra_phrases
    result = run_audit(
        manifest_path=pathlib.Path(args.manifest),
        payload_paths=[pathlib.Path(text) for text in args.payload],
        require_manifest_mtime_after_payloads=(not bool(args.skip_manifest_mtime_check)),
        allow_noise_prefixes=_parse_allow_noise_prefixes(list(args.allow_noise_prefix)),
        forbidden_manifest_phrases=phrase_list,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if bool(result.get("ok", False)) else 2)


if __name__ == "__main__":
    main()
