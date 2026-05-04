#!/usr/bin/env python3
"""Run local validation sequence aligned with CI critical checks."""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from prodesk.repo import resolve_repo_root

CI_VALIDATE_STEP_TIMEOUT_SEC = max(30.0, float(os.getenv("CI_VALIDATE_STEP_TIMEOUT_SEC", "1800")))


def _run_step(name: str, cmd: list[str], *, cwd: pathlib.Path) -> None:
    print(f"[ci_validate] step={name} cmd={' '.join(cmd)}")
    try:
        rc = subprocess.run(
            cmd,
            check=False,
            cwd=str(cwd),
            timeout=float(CI_VALIDATE_STEP_TIMEOUT_SEC),
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            "[ci_validate] timeout:"
            + f" step={name} timeout_sec={float(CI_VALIDATE_STEP_TIMEOUT_SEC):.1f}",
            file=sys.stderr,
        )
        raise SystemExit(124)
    if rc != 0:
        raise SystemExit(rc)


def _has_status_logs(log_dir: pathlib.Path) -> bool:
    try:
        return any(log_dir.glob("status_*.jsonl"))
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro local CI-equivalent validator")
    parser.add_argument("--config", default="configs/profiles/paper_universal.yaml", help="Execution config path")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Execution log directory for harness integrity audit")
    parser.add_argument(
        "--deep-harness",
        action="store_true",
        help="Run backstage harness qualification",
    )
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest run")
    parser.add_argument("--skip-run-integrity", action="store_true", help="Skip run integrity/harness audit step")
    args = parser.parse_args()

    repo_root = resolve_repo_root(start=pathlib.Path.cwd())
    py = sys.executable

    _run_step(
        "dependency_repro_audit",
        [py, str(repo_root / "scripts/dependency_repro_audit.py"), "--lock-manifest", "ops/dependency_lock.json"],
        cwd=repo_root,
    )
    _run_step(
        "profile_matrix_audit",
        [py, str(repo_root / "scripts/profile_matrix_audit.py")],
        cwd=repo_root,
    )
    _run_step(
        "runtime_hardening_audit",
        [py, str(repo_root / "scripts/runtime_hardening_audit.py"), "--compose", str(repo_root / "docker-compose.yml")],
        cwd=repo_root,
    )
    _run_step(
        "money_harness_exception_audit",
        [py, str(repo_root / "scripts/money_harness_exception_audit.py"), "--repo-root", str(repo_root)],
        cwd=repo_root,
    )
    _run_step(
        "config_consistency_audit",
        [
            py,
            str(repo_root / "scripts/config_consistency_audit.py"),
            "--primary",
            str(repo_root / "execution_config.yaml"),
            "--secondary",
            str(repo_root / "config.yaml"),
        ],
        cwd=repo_root,
    )
    config_path = pathlib.Path(args.config)
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()
    log_dir = pathlib.Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = (repo_root / log_dir).resolve()
    paper_harness_cmd = [
        py,
        str(repo_root / "scripts/paper_harness_audit.py"),
        "--config",
        str(config_path),
        "--log-dir",
        str(log_dir),
    ]
    if bool(args.skip_run_integrity) or not _has_status_logs(log_dir):
        paper_harness_cmd.append("--skip-run-integrity")
    _run_step("paper_harness_audit", paper_harness_cmd, cwd=repo_root)
    if bool(args.deep_harness):
        _run_step(
            "harness_qualify",
            [
                py,
                str(repo_root / "scripts/harness_qualify.py"),
                "--config",
                str(config_path),
                "--log-dir",
                str(log_dir),
                "--policy",
                str(repo_root / "ops" / "harness_policy.yaml"),
            ],
            cwd=repo_root,
        )
    if not bool(args.skip_pytest):
        _run_step("pytest", [py, "-m", "pytest", "-q"], cwd=repo_root)

    print("[ci_validate] all selected steps passed")


if __name__ == "__main__":
    main()
