#!/usr/bin/env python3
"""Dependency lock + build reproducibility audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import tomllib
from typing import Any, Dict, List

from prodesk.error_codes import summarize_error_codes
from prodesk.repo import resolve_repo_root

PIN_RE = re.compile(r"^[A-Za-z0-9_.\-]+==[^\s]+$")
FROM_DIGEST_RE = re.compile(
    r"^\s*FROM\s+[^\s@]+(?:@[^\s]+)?\s*(?:AS\s+[A-Za-z0-9_.\-]+)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FROM_DIGEST_TOKEN_RE = re.compile(r"@sha256:[0-9a-f]{64}$", re.IGNORECASE)


def _normalize_dep_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


def _parse_pin_line(text: str) -> tuple[str, str] | None:
    if not PIN_RE.match(text):
        return None
    raw_name, raw_version = text.split("==", 1)
    name = _normalize_dep_name(raw_name)
    version = str(raw_version).strip()
    if not name or not version:
        return None
    return name, version


def _parse_requirements_pins(req_path: pathlib.Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    lines = req_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for raw in lines:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        parsed = _parse_pin_line(text)
        if parsed is None:
            continue
        name, version = parsed
        out[name] = version
    return out


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _pinned_requirement_findings(req_path: pathlib.Path) -> List[str]:
    findings: List[str] = []
    lines = req_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if not PIN_RE.match(text):
            findings.append(f"dependency_unpinned_requirement:line:{i}:{text}")
    return findings


def _docker_repro_findings(dockerfile: pathlib.Path) -> List[str]:
    findings: List[str] = []
    text = dockerfile.read_text(encoding="utf-8", errors="ignore")
    if "pip install --no-cache-dir -r /app/requirements.txt" not in text:
        findings.append("dockerfile_missing_requirements_install_step")
    from_lines = FROM_DIGEST_RE.findall(text)
    if not from_lines:
        findings.append("dockerfile_missing_from")
        return findings
    first_from = str(from_lines[0]).strip().split()
    if len(first_from) >= 2:
        image_token = str(first_from[1]).strip()
        if not FROM_DIGEST_TOKEN_RE.search(image_token):
            findings.append("dockerfile_base_image_unpinned_digest")
    else:
        findings.append("dockerfile_missing_from")
    return findings


def _pyproject_dependency_findings(pyproject_path: pathlib.Path, requirements_path: pathlib.Path) -> List[str]:
    findings: List[str] = []
    req_pins = _parse_requirements_pins(requirements_path)
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"pyproject_invalid_toml:{exc.__class__.__name__}"]
    project = payload.get("project", {})
    deps = project.get("dependencies", [])
    if not isinstance(deps, list):
        return ["pyproject_dependencies_invalid"]
    py_pins: Dict[str, str] = {}
    for dep in deps:
        text = str(dep or "").strip()
        if not text:
            continue
        parsed = _parse_pin_line(text)
        if parsed is None:
            findings.append(f"pyproject_dependency_unpinned:{text}")
            continue
        name, version = parsed
        py_pins[name] = version
        req_version = req_pins.get(name)
        if req_version is None:
            findings.append(f"pyproject_dependency_missing_in_requirements:{name}")
            continue
        if req_version != version:
            findings.append(f"pyproject_dependency_version_mismatch:{name}:pyproject={version}:requirements={req_version}")
    for req_name in sorted(req_pins.keys()):
        if req_name not in py_pins:
            findings.append(f"requirements_dependency_missing_in_pyproject:{req_name}")
    return findings


def _relative_to_root(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path.resolve())


def run_audit(
    *,
    requirements_path: pathlib.Path,
    dockerfile_path: pathlib.Path,
    lock_manifest_path: pathlib.Path,
    pyproject_path: pathlib.Path | None = None,
    refresh: bool,
) -> Dict[str, Any]:
    findings: List[str] = []
    repo_root = resolve_repo_root(start=requirements_path.parent)

    if not requirements_path.exists():
        findings.append(f"requirements_missing:{_relative_to_root(requirements_path, repo_root)}")
    if not dockerfile_path.exists():
        findings.append(f"dockerfile_missing:{_relative_to_root(dockerfile_path, repo_root)}")
    if pyproject_path is not None and not pyproject_path.exists():
        findings.append(f"pyproject_missing:{_relative_to_root(pyproject_path, repo_root)}")

    if findings:
        return {
            "ok": False,
            "finding_count": len(findings),
            "findings": findings,
            "error_codes": summarize_error_codes(findings),
        }

    findings.extend(_pinned_requirement_findings(requirements_path))
    findings.extend(_docker_repro_findings(dockerfile_path))
    if pyproject_path is not None:
        findings.extend(_pyproject_dependency_findings(pyproject_path, requirements_path))

    req_key = _relative_to_root(requirements_path, repo_root)
    docker_key = _relative_to_root(dockerfile_path, repo_root)
    manifest_payload = {
        "schema_version": 2,
        "files": {
            req_key: _sha256_file(requirements_path),
            docker_key: _sha256_file(dockerfile_path),
        },
    }
    if pyproject_path is not None and pyproject_path.exists():
        pyproject_key = _relative_to_root(pyproject_path, repo_root)
        manifest_payload["files"][pyproject_key] = _sha256_file(pyproject_path)

    if refresh:
        lock_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        lock_manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        if not lock_manifest_path.exists():
            findings.append(f"lock_manifest_missing:{_relative_to_root(lock_manifest_path, repo_root)}")
        else:
            try:
                existing = json.loads(lock_manifest_path.read_text(encoding="utf-8"))
            except Exception:
                findings.append(f"lock_manifest_invalid_json:{_relative_to_root(lock_manifest_path, repo_root)}")
                existing = {}
            if isinstance(existing, dict):
                expected_files = existing.get("files", {})
                if isinstance(expected_files, dict):
                    for path_text, expected_hash in expected_files.items():
                        p_raw = pathlib.Path(str(path_text))
                        actual_path = p_raw if p_raw.is_absolute() else (repo_root / p_raw)
                        if not actual_path.exists():
                            findings.append(
                                f"lock_manifest_tracked_file_missing:{_relative_to_root(actual_path, repo_root)}"
                            )
                            continue
                        actual_hash = _sha256_file(actual_path)
                        if str(expected_hash).strip().lower() != actual_hash.lower():
                            findings.append(
                                f"lock_manifest_hash_mismatch:{_relative_to_root(actual_path, repo_root)}"
                            )
                else:
                    findings.append("lock_manifest_files_invalid")

    return {
        "ok": len(findings) == 0,
        "finding_count": len(findings),
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "manifest_preview": manifest_payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro dependency lock + build reproducibility audit")
    parser.add_argument("--requirements", default="requirements.txt")
    parser.add_argument("--dockerfile", default="Dockerfile")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--lock-manifest", default="ops/dependency_lock.json")
    parser.add_argument("--refresh", action="store_true", help="Refresh lock manifest with current file hashes")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()
    repo_root = resolve_repo_root()
    requirements_path = pathlib.Path(args.requirements).expanduser()
    dockerfile_path = pathlib.Path(args.dockerfile).expanduser()
    pyproject_path = pathlib.Path(args.pyproject).expanduser()
    lock_manifest_path = pathlib.Path(args.lock_manifest).expanduser()
    if not requirements_path.is_absolute():
        requirements_path = (repo_root / requirements_path).resolve()
    else:
        requirements_path = requirements_path.resolve()
    if not dockerfile_path.is_absolute():
        dockerfile_path = (repo_root / dockerfile_path).resolve()
    else:
        dockerfile_path = dockerfile_path.resolve()
    if not lock_manifest_path.is_absolute():
        lock_manifest_path = (repo_root / lock_manifest_path).resolve()
    else:
        lock_manifest_path = lock_manifest_path.resolve()
    if not pyproject_path.is_absolute():
        pyproject_path = (repo_root / pyproject_path).resolve()
    else:
        pyproject_path = pyproject_path.resolve()

    result = run_audit(
        requirements_path=requirements_path,
        dockerfile_path=dockerfile_path,
        lock_manifest_path=lock_manifest_path,
        pyproject_path=pyproject_path,
        refresh=bool(args.refresh),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
