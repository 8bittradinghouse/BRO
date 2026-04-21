#!/usr/bin/env python3
"""Audit multi-profile config matrix for isolation and symbol consistency."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List


from prodesk.config import load_execution_config


DEFAULT_PROFILES = [
    "configs/btc_paper.yaml",
    "configs/sol_paper.yaml",
    "configs/xrp_paper.yaml",
    "configs/btc_paper_docker.yaml",
    "configs/profiles/paper_universal.yaml",
    "configs/profiles/live_canary.yaml",
    "configs/profiles/live_pilot.yaml",
]


def _resolve_path(cfg_path: pathlib.Path, raw: str) -> pathlib.Path:
    p = pathlib.Path(str(raw or "").strip())
    if p.is_absolute():
        return p.resolve()
    return (cfg_path.parent / p).resolve()


def run_audit(*, profile_paths: List[pathlib.Path]) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    load_errors: List[str] = []
    resolved_profiles = [p.resolve() for p in profile_paths]
    seen_logs: Dict[str, str] = {}
    seen_states: Dict[str, str] = {}
    seen_guards: Dict[str, str] = {}

    for cfg_path in resolved_profiles:
        profile_label = cfg_path.name
        try:
            cfg = load_execution_config(cfg_path)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:  # pragma: no cover - exercised by unit test through run_audit
            error = f"{profile_label}:load_error:{exc.__class__.__name__}:{exc}"
            findings.append(error)
            load_errors.append(error)
            continue
        mode = str(cfg.get("mode", "")).strip().lower()
        if mode not in {"paper", "live"}:
            findings.append(f"{profile_label}:mode_invalid:{mode}")

        asset = cfg.get("asset", {}) if isinstance(cfg.get("asset"), dict) else {}
        symbol = str(asset.get("symbol", "")).strip().upper()
        if not symbol:
            findings.append(f"{profile_label}:asset_symbol_missing")
        chain_syms = [str(x).strip().lower() for x in list(asset.get("chainlink_symbols") or [])]
        if symbol and f"{symbol.lower()}/usd" not in chain_syms:
            findings.append(f"{profile_label}:chainlink_symbol_missing:{symbol.lower()}/usd")
        discovery_syms = [str(x).strip().upper() for x in list(asset.get("discovery_symbols") or [])]
        if symbol and symbol not in discovery_syms:
            findings.append(f"{profile_label}:discovery_symbol_missing:{symbol}")

        storage = cfg.get("storage", {}) if isinstance(cfg.get("storage"), dict) else {}
        runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
        security = cfg.get("security", {}) if isinstance(cfg.get("security"), dict) else {}
        log_dir = _resolve_path(cfg_path, str(storage.get("log_dir", "")))
        state_path = _resolve_path(cfg_path, str(storage.get("state_path", "")))
        guard_path = _resolve_path(cfg_path, str(runtime.get("guard_stop_file", "")))
        roots = [_resolve_path(cfg_path, str(x)) for x in list(security.get("allowed_storage_roots") or [])]

        log_key = str(log_dir)
        state_key = str(state_path)
        guard_key = str(guard_path)
        if mode == "paper":
            if log_key in seen_logs:
                findings.append(f"{profile_label}:log_dir_collision_with:{seen_logs[log_key]}")
            else:
                seen_logs[log_key] = profile_label
            if state_key in seen_states:
                findings.append(f"{profile_label}:state_path_collision_with:{seen_states[state_key]}")
            else:
                seen_states[state_key] = profile_label
            if guard_key in seen_guards:
                findings.append(f"{profile_label}:guard_stop_file_collision_with:{seen_guards[guard_key]}")
            else:
                seen_guards[guard_key] = profile_label

        if roots:
            log_ok = any(log_dir == root or log_dir.is_relative_to(root) for root in roots)
            state_ok = any(state_path == root or state_path.is_relative_to(root) for root in roots)
            if not log_ok:
                findings.append(f"{profile_label}:log_dir_outside_allowed_storage_roots")
            if not state_ok:
                findings.append(f"{profile_label}:state_path_outside_allowed_storage_roots")
        else:
            warnings.append(f"{profile_label}:allowed_storage_roots_empty")

    return {
        "profile_count": len(resolved_profiles),
        "profiles": [str(p) for p in resolved_profiles],
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "load_errors": load_errors,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro multi-profile matrix audit")
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help="Config profile path; can be repeated (default: BTC/SOL/XRP + btc_paper_docker).",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    raw_profiles = [str(x).strip() for x in args.profile if str(x).strip()]
    if not raw_profiles:
        raw_profiles = list(DEFAULT_PROFILES)
    profiles = [pathlib.Path(p) for p in raw_profiles]
    result = run_audit(profile_paths=profiles)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
