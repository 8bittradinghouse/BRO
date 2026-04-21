#!/usr/bin/env python3
"""Audit consistency between two execution config profiles."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Tuple

import yaml

from prodesk.config import load_execution_config

CRITICAL_PATHS: Tuple[str, ...] = (
    "runtime.paper_passive_touch_fill_enabled",
    "runtime.paper_passive_touch_fill_ratio",
    "runtime.paper_passive_min_rest_sec",
    "runtime.paper_passive_min_fill_size",
    "runtime.paper_passive_near_touch_band",
    "runtime.paper_passive_near_touch_fill_ratio",
    "runtime.paper_background_fill_ratio",
    "sniper.enabled",
    "sniper.arming_horizon_sec",
    "sniper.execution_cutoff_sec",
    "sniper.late_fire_priority_band_sec",
    "sniper.allow_without_expiry_metadata",
    "sniper.require_lag_verification",
    "doctrine.mode",
    "doctrine.oracle_max_tick_age_sec",
    "doctrine.min_observe_cycles_on_entry",
    "doctrine.min_observe_seconds_on_entry",
    "sniper.taker.enabled",
    "sniper.taker.min_edge",
    "sniper.taker.extreme_edge_mult",
    "sniper.taker.order_size",
    "sniper.taker.target_usd",
    "sniper.taker.max_orders_per_cycle",
    "sniper.taker.per_token_cooldown_sec",
)


def _load_effective_config(path: pathlib.Path) -> Dict[str, Any]:
    resolved = path.resolve()
    try:
        loaded = load_execution_config(resolved)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, ValueError, TypeError, RuntimeError):
        pass
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{resolved} root must be a mapping")
    return payload


def _get_path(cfg: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def run_audit(primary_path: pathlib.Path, secondary_path: pathlib.Path, paths: List[str]) -> Dict[str, Any]:
    primary = _load_effective_config(primary_path.resolve())
    secondary = _load_effective_config(secondary_path.resolve())
    findings: List[str] = []

    for dotted in paths:
        p_ok, p_val = _get_path(primary, dotted)
        s_ok, s_val = _get_path(secondary, dotted)
        if not p_ok:
            findings.append(f"primary_missing:{dotted}")
            continue
        if not s_ok:
            findings.append(f"secondary_missing:{dotted}")
            continue
        if p_val != s_val:
            findings.append(f"value_mismatch:{dotted}:primary={p_val!r}:secondary={s_val!r}")

    return {
        "primary": str(primary_path.resolve()),
        "secondary": str(secondary_path.resolve()),
        "path_count": len(paths),
        "finding_count": len(findings),
        "findings": findings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro config consistency audit")
    parser.add_argument("--primary", default="execution_config.yaml", help="Primary config path")
    parser.add_argument("--secondary", default="config.yaml", help="Secondary config path")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Dotted path to compare; can be repeated. Defaults to critical runtime/sniper paths.",
    )
    args = parser.parse_args()

    primary = pathlib.Path(args.primary)
    secondary = pathlib.Path(args.secondary)
    if not primary.exists() or not secondary.exists():
        missing = [str(p) for p in (primary, secondary) if not p.exists()]
        print(json.dumps({"ok": False, "finding_count": len(missing), "findings": [f"missing_file:{m}" for m in missing]}))
        raise SystemExit(2)

    paths = [str(p).strip() for p in args.path if str(p).strip()]
    if not paths:
        paths = list(CRITICAL_PATHS)

    report = run_audit(primary, secondary, paths)
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
