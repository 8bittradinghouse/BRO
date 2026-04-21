#!/usr/bin/env python3
"""Hardening audit for the offline simulator harness."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
from typing import Any, Dict, List


try:
    from simulator import _build_base_cfg, run_scenario
except ModuleNotFoundError:
    from prodesk.repo import resolve_repo_root

    _repo_root = resolve_repo_root(start=pathlib.Path(__file__).resolve().parent)
    _sim_path = (_repo_root / "simulator.py").resolve()
    _spec = importlib.util.spec_from_file_location("simulator", _sim_path)
    if _spec is None or _spec.loader is None:
        raise
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["simulator"] = _module
    _spec.loader.exec_module(_module)
    _build_base_cfg = _module._build_base_cfg
    run_scenario = _module.run_scenario


def _stable_result_payload(result: Any) -> Dict[str, Any]:
    payload = dataclasses.asdict(result)
    # Exclude labels that can vary by caller; keep behavioral metrics only.
    payload.pop("run_label", None)
    payload["notes"] = list(payload.get("notes") or [])
    return payload


def _stable_payload_hash(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def run_audit(*, config_path: pathlib.Path, steps: int, dt_sec: float) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    cfg = _build_base_cfg(config_path.resolve())

    with tempfile.TemporaryDirectory() as td:
        out_dir = pathlib.Path(td)

        a = run_scenario(
            name="baseline",
            cfg=cfg,
            steps=steps,
            dt_sec=dt_sec,
            seed=424242,
            out_dir=out_dir,
            difficulty="normal",
            run_label="repro_a",
        )
        b = run_scenario(
            name="baseline",
            cfg=cfg,
            steps=steps,
            dt_sec=dt_sec,
            seed=424242,
            out_dir=out_dir,
            difficulty="normal",
            run_label="repro_b",
        )
        c = run_scenario(
            name="baseline",
            cfg=cfg,
            steps=steps,
            dt_sec=dt_sec,
            seed=424242,
            out_dir=out_dir,
            difficulty="normal",
            run_label="repro_c",
        )
        stable_a = _stable_result_payload(a)
        stable_b = _stable_result_payload(b)
        stable_c = _stable_result_payload(c)
        if stable_a != stable_b or stable_a != stable_c:
            findings.append("reproducibility_mismatch:same_seed_baseline")
        repro_hashes = sorted(
            {
                _stable_payload_hash(stable_a),
                _stable_payload_hash(stable_b),
                _stable_payload_hash(stable_c),
            }
        )
        if len(repro_hashes) != 1:
            findings.append(f"reproducibility_hash_mismatch:{len(repro_hashes)}")

        # Ensure stress scenario completes and emits artifacts.
        c = run_scenario(
            name="chaos_day",
            cfg=cfg,
            steps=max(30, int(steps)),
            dt_sec=dt_sec,
            seed=777,
            out_dir=out_dir,
            difficulty="hard",
            run_label="chaos_probe",
        )
        if c.completed_steps < c.steps:
            findings.append("chaos_day_incomplete")
        if c.error_events > 0:
            findings.append(f"chaos_day_error_events:{c.error_events}")

        for rel in (
            "baseline/repro_a/scenario_meta.json",
            "baseline/repro_b/scenario_meta.json",
            "baseline/repro_c/scenario_meta.json",
            "chaos_day/chaos_probe/scenario_meta.json",
        ):
            p = out_dir / rel
            if not p.exists():
                findings.append(f"missing_artifact:{rel}")
            else:
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    findings.append(f"invalid_json:{rel}")
                    continue
                if not str(payload.get("config_fingerprint_sha256", "")).strip():
                    findings.append(f"missing_config_fingerprint:{rel}")

        if c.fills == 0:
            warnings.append("chaos_day_no_fills")

    return {
        "config_path": str(config_path.resolve()),
        "steps": int(steps),
        "dt_sec": float(dt_sec),
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "reproducibility_hash": (repro_hashes[0] if len(repro_hashes) == 1 else ""),
        "findings": findings,
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulator harness hardening audit")
    parser.add_argument("--config", default="configs/profiles/paper_universal.yaml", help="Execution config path")
    parser.add_argument("--steps", type=int, default=40, help="Scenario steps for audit runs")
    parser.add_argument("--dt-sec", type=float, default=1.0, help="Step interval in seconds")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        config_path=pathlib.Path(args.config),
        steps=max(1, int(args.steps)),
        dt_sec=max(0.1, float(args.dt_sec)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
