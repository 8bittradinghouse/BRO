#!/usr/bin/env python3
"""Deep qualification gate for BRO paper harness trustworthiness."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional

import yaml

from prodesk.reporting import decision_item
from prodesk.repo import resolve_repo_root
from scripts.network_fault_drill import run_audit as run_fault_drill_audit
from scripts.paper_harness_audit import run_audit as run_paper_harness_audit

DEFAULT_POLICY: Dict[str, Any] = {
    "paper_harness": {
        "enabled": True,
        "run_integrity_enabled": True,
        "min_status_rows": 1,
        "max_status_age_sec": 3153600000.0,
    },
    "sim_harness": {
        "enabled": True,
        "config": "configs/profiles/paper_universal.yaml",
        "steps": 20,
        "dt_sec": 1.0,
    },
    "fault_drill": {
        "enabled": False,
        "drills_dir": "./ops/drills",
        "max_age_days": 7.0,
    },
}
SIM_HARNESS_AUDIT_TIMEOUT_SEC = 180.0


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_policy(policy_path: pathlib.Path) -> Dict[str, Any]:
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("harness policy root must be a mapping")
    return _deep_merge(DEFAULT_POLICY, payload)


def _run_sim_harness_audit_subprocess(*, sim_config_path: pathlib.Path, repo_root: pathlib.Path) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str((repo_root / "scripts" / "sim_harness_audit.py").resolve()),
        "--config",
        str(sim_config_path.resolve()),
        "--steps",
        "20",
        "--dt-sec",
        "1.0",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=float(SIM_HARNESS_AUDIT_TIMEOUT_SEC),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "finding_count": 1,
            "findings": [f"sim_harness_audit_timeout:timeout_sec={float(SIM_HARNESS_AUDIT_TIMEOUT_SEC):.1f}"],
            "warning_count": 0,
            "warnings": [],
        }
    output = (proc.stdout or "").strip()
    if not output:
        output = (proc.stderr or "").strip()
    if not output:
        return {
            "ok": False,
            "finding_count": 1,
            "findings": [f"sim_harness_audit_no_output:rc={proc.returncode}"],
            "warning_count": 0,
            "warnings": [],
        }
    payload: Dict[str, Any]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        # Fallback: tolerate surrounding non-JSON lines.
        start = output.find("{")
        end = output.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(output[start : end + 1])
            except json.JSONDecodeError:
                payload = {
                    "ok": False,
                    "finding_count": 1,
                    "findings": [f"sim_harness_audit_unparseable_output:rc={proc.returncode}"],
                    "warning_count": 0,
                    "warnings": [],
                }
        else:
            payload = {
                "ok": False,
                "finding_count": 1,
                "findings": [f"sim_harness_audit_unparseable_output:rc={proc.returncode}"],
                "warning_count": 0,
                "warnings": [],
            }
    if proc.returncode != 0 and bool(payload.get("ok", False)):
        payload = dict(payload)
        findings = list(payload.get("findings", []))
        findings.append(f"sim_harness_audit_nonzero_exit:{proc.returncode}")
        payload["findings"] = findings
        payload["finding_count"] = len(findings)
        payload["ok"] = False
    return payload


def run_gate(
    *,
    config_path: pathlib.Path,
    log_dir: pathlib.Path,
    run_id: str,
    policy: Dict[str, Any],
    force_skip_run_integrity: bool,
    force_skip_sim_harness: bool,
    force_skip_fault_drill: bool,
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    decision_trace: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}
    repo_root = resolve_repo_root(start=pathlib.Path(__file__).resolve().parent)
    paper_policy = policy.get("paper_harness", {}) if isinstance(policy.get("paper_harness"), dict) else {}
    sim_policy = policy.get("sim_harness", {}) if isinstance(policy.get("sim_harness"), dict) else {}
    drill_policy = policy.get("fault_drill", {}) if isinstance(policy.get("fault_drill"), dict) else {}

    paper_enabled = bool(paper_policy.get("enabled", True))
    run_integrity_enabled = bool(paper_policy.get("run_integrity_enabled", True)) and (not bool(force_skip_run_integrity))
    run_integrity_min_rows = max(1, int(paper_policy.get("min_status_rows", 1)))
    run_integrity_max_age = max(1.0, float(paper_policy.get("max_status_age_sec", 3153600000.0)))

    sim_enabled = bool(sim_policy.get("enabled", True)) and (not bool(force_skip_sim_harness))
    sim_config_path = pathlib.Path(str(sim_policy.get("config", "configs/profiles/paper_universal.yaml")))

    fault_enabled = bool(drill_policy.get("enabled", False)) and (not bool(force_skip_fault_drill))
    drills_dir = pathlib.Path(str(drill_policy.get("drills_dir", "./ops/drills")))
    drill_max_age_days = max(0.1, float(drill_policy.get("max_age_days", 7.0)))

    if paper_enabled:
        paper = run_paper_harness_audit(
            config_path=config_path.resolve(),
            log_dir=log_dir.resolve(),
            run_id=str(run_id or ""),
            skip_run_integrity=(not run_integrity_enabled),
            min_status_rows=run_integrity_min_rows,
            max_status_age_sec=run_integrity_max_age,
        )
        checks["paper_harness_audit"] = paper
        findings.extend(str(x) for x in paper.get("findings", []))
        warnings.extend(str(x) for x in paper.get("warnings", []))
        decision_trace.append(
            decision_item(
                check="paper_harness_audit",
                level="hard_fail",
                metric="finding_count",
                comparator="max",
                value=float(paper.get("finding_count", 0)),
                threshold=0.0,
                passed=(int(paper.get("finding_count", 0)) == 0),
                note="canonical paper harness config + optional run-integrity checks",
            )
        )
    else:
        checks["paper_harness_audit"] = {"skipped": True}
        decision_trace.append(
            decision_item(
                check="paper_harness_audit",
                level="advisory",
                metric="skipped",
                comparator="max",
                value=1.0,
                threshold=0.0,
                passed=True,
                note="disabled by policy",
            )
        )

    if sim_enabled:
        sim = _run_sim_harness_audit_subprocess(
            sim_config_path=sim_config_path.resolve(),
            repo_root=repo_root,
        )
        checks["sim_harness_audit"] = sim
        findings.extend(str(x) for x in sim.get("findings", []))
        warnings.extend(str(x) for x in sim.get("warnings", []))
        decision_trace.append(
            decision_item(
                check="sim_harness_audit",
                level="hard_fail",
                metric="finding_count",
                comparator="max",
                value=float(sim.get("finding_count", 0)),
                threshold=0.0,
                passed=(int(sim.get("finding_count", 0)) == 0),
                note="determinism and simulator artifact integrity",
            )
        )
    else:
        checks["sim_harness_audit"] = {"skipped": True}
        decision_trace.append(
            decision_item(
                check="sim_harness_audit",
                level="advisory",
                metric="skipped",
                comparator="max",
                value=1.0,
                threshold=0.0,
                passed=True,
                note="skipped by operator flag",
            )
        )

    if fault_enabled:
        if drills_dir.resolve().exists():
            drills = run_fault_drill_audit(
                drills_dir=drills_dir.resolve(),
                max_age_days=max(0.1, float(drill_max_age_days)),
            )
            checks["network_fault_drill_audit"] = drills
            findings.extend(str(x) for x in drills.get("findings", []))
            decision_trace.append(
                decision_item(
                    check="network_fault_drill_audit",
                    level="hard_fail",
                    metric="finding_count",
                    comparator="max",
                    value=float(drills.get("finding_count", 0)),
                    threshold=0.0,
                    passed=(int(drills.get("finding_count", 0)) == 0),
                    note="recent successful fault-injection evidence",
                )
            )
        else:
            checks["network_fault_drill_audit"] = {"skipped": True, "reason": "drills_dir_missing"}
            warnings.append(f"harness_fault_drill_skipped_missing_dir:{drills_dir.resolve()}")
            decision_trace.append(
                decision_item(
                    check="network_fault_drill_audit",
                    level="advisory",
                    metric="skipped_missing_dir",
                    comparator="max",
                    value=1.0,
                    threshold=0.0,
                    passed=True,
                    note="drills dir missing; skipped as advisory",
                )
            )
    else:
        checks["network_fault_drill_audit"] = {"skipped": True}
        decision_trace.append(
            decision_item(
                check="network_fault_drill_audit",
                level="advisory",
                metric="skipped",
                comparator="max",
                value=1.0,
                threshold=0.0,
                passed=True,
                note="skipped by operator flag",
            )
        )

    checks["summary"] = {
        "hard_fail_findings": len(findings),
        "warning_count": len(warnings),
    }
    return {
        "config_path": str(config_path.resolve()),
        "sim_config_path": str(sim_config_path.resolve()),
        "policy": policy,
        "log_dir": str(log_dir.resolve()),
        "run_id": str(run_id or ""),
        "decision_trace": decision_trace,
        "checks": checks,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO deep paper-harness qualification gate")
    parser.add_argument("--config", default="configs/profiles/paper_universal.yaml", help="Paper execution config path")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Paper execution log directory")
    parser.add_argument("--run-id", default="", help="Optional explicit run_id for run-integrity checks")
    parser.add_argument("--policy", default="ops/harness_policy.yaml", help="Harness qualification policy path")
    parser.add_argument("--skip-run-integrity", action="store_true", help="Skip run-integrity checks in paper harness audit")
    parser.add_argument("--skip-sim-harness", action="store_true", help="Skip simulator harness audit")
    parser.add_argument("--skip-fault-drill", action="store_true", help="Skip network fault drill evidence audit")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()
    policy = _load_policy(pathlib.Path(args.policy).resolve())

    result = run_gate(
        config_path=pathlib.Path(args.config),
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        policy=policy,
        force_skip_run_integrity=bool(args.skip_run_integrity),
        force_skip_sim_harness=bool(args.skip_sim_harness),
        force_skip_fault_drill=bool(args.skip_fault_drill),
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
