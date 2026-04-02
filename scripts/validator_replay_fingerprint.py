#!/usr/bin/env python3
"""Compute normalized deterministic replay fingerprints for canonical validators."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _canonical_sha256(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _normalize_stale_warning(text: str) -> str:
    if text.startswith("latest_status_stale:"):
        return "latest_status_stale:<normalized>"
    if text.startswith("status_ts_too_stale:"):
        return "status_ts_too_stale:<normalized>"
    return text


def _normalize_payload(validator: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(payload)

    if validator == "nightly_soak_report":
        normalized.pop("ts_utc", None)

    elif validator == "readiness_gate":
        report = normalized.get("report")
        if isinstance(report, dict):
            report.pop("ts_utc", None)

    elif validator == "time_discipline_audit":
        normalized.pop("status_age_sec", None)
        findings = normalized.get("findings")
        if isinstance(findings, list):
            normalized["findings"] = [
                _normalize_stale_warning(str(item)) for item in findings
            ]

    elif validator == "soak_hardening_gate":
        readiness = normalized.get("readiness")
        if isinstance(readiness, dict):
            report = readiness.get("report")
            if isinstance(report, dict):
                report.pop("ts_utc", None)
        integrity = normalized.get("integrity")
        if isinstance(integrity, dict):
            warnings = integrity.get("warnings")
            if isinstance(warnings, list):
                integrity["warnings"] = [
                    _normalize_stale_warning(str(item)) for item in warnings
                ]

    elif validator == "paper_harness_audit":
        checks = normalized.get("checks")
        if isinstance(checks, dict):
            run_integrity = checks.get("run_integrity")
            if isinstance(run_integrity, dict):
                warnings = run_integrity.get("warnings")
                if isinstance(warnings, list):
                    run_integrity["warnings"] = [
                        _normalize_stale_warning(str(item)) for item in warnings
                    ]
        warnings = normalized.get("warnings")
        if isinstance(warnings, list):
            normalized["warnings"] = [
                _normalize_stale_warning(str(item)) for item in warnings
            ]

    return normalized


def _load_json(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"parse_error:{exc}"
    if not isinstance(payload, dict):
        return None, "invalid_root:not_object"
    return payload, ""


def compute_replay_fingerprints(
    pairs: List[Tuple[str, Path, Path]],
) -> Dict[str, Any]:
    validators: Dict[str, Dict[str, Any]] = {}
    determinism_ok = True

    for validator, primary_path, replay_path in pairs:
        entry: Dict[str, Any] = {
            "primary_sha256": "",
            "replay_sha256": "",
            "replay_match": False,
        }
        primary_payload, primary_err = _load_json(primary_path)
        replay_payload, replay_err = _load_json(replay_path)
        if primary_err or replay_err:
            determinism_ok = False
            if primary_err:
                entry["primary_error"] = primary_err
            if replay_err:
                entry["replay_error"] = replay_err
            validators[str(validator)] = entry
            continue

        normalized_primary = _normalize_payload(validator, primary_payload or {})
        normalized_replay = _normalize_payload(validator, replay_payload or {})
        primary_sha = _canonical_sha256(normalized_primary)
        replay_sha = _canonical_sha256(normalized_replay)
        replay_match = primary_sha == replay_sha
        if not replay_match:
            determinism_ok = False
        entry["primary_sha256"] = primary_sha
        entry["replay_sha256"] = replay_sha
        entry["replay_match"] = bool(replay_match)
        validators[str(validator)] = entry

    return {
        "determinism_ok": bool(determinism_ok),
        "validators": validators,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Canonical validator replay fingerprint calculator")
    parser.add_argument(
        "--pair",
        action="append",
        nargs=3,
        metavar=("VALIDATOR", "PRIMARY_JSON", "REPLAY_JSON"),
        help="Validator output pair to compare (repeatable)",
        required=True,
    )
    args = parser.parse_args()

    pairs: List[Tuple[str, Path, Path]] = []
    for item in args.pair:
        validator, primary_raw, replay_raw = item
        pairs.append((str(validator), Path(primary_raw).resolve(), Path(replay_raw).resolve()))

    payload = compute_replay_fingerprints(pairs)
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
