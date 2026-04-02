#!/usr/bin/env python3
"""Audit guardian watchdog launch profile for resilient anti-false-stop settings."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Optional

import yaml


def _load_compose(path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("compose root must be a mapping")
    return payload


def _command_to_tokens(command: Any) -> List[str]:
    if isinstance(command, list):
        return [str(x) for x in command]
    if isinstance(command, str):
        return str(command).split()
    return []


def _get_arg_value(tokens: List[str], flag: str) -> Optional[str]:
    for idx, tok in enumerate(tokens):
        if tok == flag and idx + 1 < len(tokens):
            return str(tokens[idx + 1]).strip()
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1].strip()
    return None


def run_audit(*, compose_path: pathlib.Path) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []

    compose = _load_compose(compose_path.resolve())
    services = compose.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("compose file requires services mapping")
    guardian = services.get("bro-guardian")
    if not isinstance(guardian, dict):
        findings.append("service_missing:bro-guardian")
        return {"ok": False, "finding_count": len(findings), "warning_count": len(warnings), "findings": findings, "warnings": warnings}

    tokens = _command_to_tokens(guardian.get("command"))
    if not tokens:
        findings.append("bro-guardian:command_missing")
        return {"ok": False, "finding_count": len(findings), "warning_count": len(warnings), "findings": findings, "warnings": warnings}

    if "scripts/guardian_watchdog.py" not in " ".join(tokens):
        findings.append("bro-guardian:unexpected_command_target")

    if "--require-chainlink-connected" not in tokens:
        findings.append("bro-guardian:missing_require_chainlink_connected")
    if "--require-book-feed-connected" not in tokens:
        findings.append("bro-guardian:missing_require_book_feed_connected")
    if "--session-context-file" not in tokens:
        findings.append("bro-guardian:missing_session_context_file")
    elif not _get_arg_value(tokens, "--session-context-file"):
        findings.append("bro-guardian:session_context_file_empty")
    if "--session-token" not in tokens:
        findings.append("bro-guardian:missing_session_token")
    elif _get_arg_value(tokens, "--session-token") is None:
        findings.append("bro-guardian:session_token_missing_value")
    if "--require-authoritative-startup" not in tokens:
        findings.append("bro-guardian:missing_require_authoritative_startup")
    if "--no-run-id-from-manifest" not in tokens:
        findings.append("bro-guardian:missing_no_run_id_from_manifest")

    startup_grace_raw = _get_arg_value(tokens, "--startup-grace-sec")
    max_status_age_raw = _get_arg_value(tokens, "--max-status-age-sec")
    chain_age_raw = _get_arg_value(tokens, "--chainlink-disconnect-min-age-sec")
    book_age_raw = _get_arg_value(tokens, "--book-feed-disconnect-min-age-sec")
    confirm_raw = _get_arg_value(tokens, "--disconnect-confirm-polls")

    def _f(text: Optional[str], default: float) -> float:
        if text is None or not str(text).strip():
            return default
        try:
            return float(text)
        except Exception:
            return -1.0

    startup_grace = _f(startup_grace_raw, 90.0)
    max_status_age = _f(max_status_age_raw, 120.0)
    chain_age = _f(chain_age_raw, 20.0)
    book_age = _f(book_age_raw, 20.0)
    confirm = _f(confirm_raw, 3.0)

    if startup_grace < 60.0:
        findings.append(f"bro-guardian:startup_grace_too_low:{startup_grace}")
    if max_status_age < 90.0:
        findings.append(f"bro-guardian:max_status_age_too_low:{max_status_age}")
    if max_status_age > 300.0:
        warnings.append(f"bro-guardian:max_status_age_high:{max_status_age}")
    if chain_age < 20.0:
        findings.append(f"bro-guardian:chainlink_disconnect_min_age_too_low:{chain_age}")
    if book_age < 20.0:
        findings.append(f"bro-guardian:book_feed_disconnect_min_age_too_low:{book_age}")
    if confirm < 3.0:
        findings.append(f"bro-guardian:disconnect_confirm_polls_too_low:{confirm}")

    if "--no-trigger-on-kill-switch" not in tokens and "--trigger-on-kill-switch" not in tokens:
        warnings.append("bro-guardian:kill_switch_trigger_mode_implicit_default_true")

    return {
        "compose_path": str(compose_path.resolve()),
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro guardian launch profile audit")
    parser.add_argument("--compose", default="./docker-compose.yml", help="docker-compose file path")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()
    result = run_audit(compose_path=pathlib.Path(args.compose))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out = pathlib.Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
