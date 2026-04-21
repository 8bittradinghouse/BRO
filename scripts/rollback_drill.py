#!/usr/bin/env python3
"""Verify latest backup bundle is restorable and structurally valid."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import tarfile
import tempfile
import inspect
from typing import Any, Dict, Optional


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _latest_bundle(backup_dir: pathlib.Path) -> Optional[pathlib.Path]:
    bundles = sorted(backup_dir.glob("bro_backup_*.tar.gz"), key=lambda p: p.stat().st_mtime)
    return bundles[-1] if bundles else None


def run_drill(*, backup_dir: pathlib.Path, require_state: bool, require_manifest: bool) -> Dict[str, Any]:
    findings: list[str] = []
    latest = _latest_bundle(backup_dir.resolve())
    if latest is None:
        findings.append(f"backup_bundle_missing:{backup_dir.resolve()}")
        return {"ok": False, "finding_count": len(findings), "findings": findings}

    hash_path = latest.with_suffix(latest.suffix + ".sha256")
    if not hash_path.exists():
        findings.append(f"bundle_hash_missing:{hash_path.name}")
        return {"ok": False, "finding_count": len(findings), "findings": findings, "bundle": str(latest)}

    try:
        expected = hash_path.read_text(encoding="utf-8").strip().splitlines()[0].split()[0].lower()
    except (OSError, UnicodeError, IndexError, ValueError) as exc:
        findings.append(f"bundle_hash_invalid:{hash_path.name}:{exc.__class__.__name__}")
        return {"ok": False, "finding_count": len(findings), "findings": findings, "bundle": str(latest)}

    actual = _sha256_file(latest).lower()
    if expected != actual:
        findings.append(f"bundle_hash_mismatch:{latest.name}")
        return {"ok": False, "finding_count": len(findings), "findings": findings, "bundle": str(latest)}

    with tempfile.TemporaryDirectory() as td:
        extract_dir = pathlib.Path(td) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(latest, "r:gz") as tf:
            kwargs: Dict[str, Any] = {}
            if "filter" in inspect.signature(tf.extractall).parameters:
                kwargs["filter"] = "data"
            tf.extractall(extract_dir, **kwargs)

        log_files = sorted((extract_dir / "logs").rglob("*.jsonl")) if (extract_dir / "logs").exists() else []
        manifests = sorted((extract_dir / "manifests").glob("run_manifest_*.json")) if (extract_dir / "manifests").exists() else []
        state_file = extract_dir / "state" / "state.json"

        if not log_files:
            findings.append("rollback_extract_missing_logs")
        if require_manifest and not manifests:
            findings.append("rollback_extract_missing_manifest")
        if require_state and not state_file.exists():
            findings.append("rollback_extract_missing_state")

        return {
            "ok": len(findings) == 0,
            "finding_count": len(findings),
            "findings": findings,
            "backup_dir": str(backup_dir.resolve()),
            "bundle": str(latest.resolve()),
            "bundle_sha256": actual,
            "log_file_count": len(log_files),
            "manifest_count": len(manifests),
            "state_present": bool(state_file.exists()),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro backup rollback drill")
    parser.add_argument("--backup-dir", default="./backups", help="Backup bundle directory")
    parser.add_argument("--require-state", action="store_true", help="Require state/state.json in extracted bundle")
    parser.add_argument("--require-manifest", action="store_true", help="Require run_manifest in extracted bundle")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_drill(
        backup_dir=pathlib.Path(args.backup_dir),
        require_state=bool(args.require_state),
        require_manifest=bool(args.require_manifest),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
