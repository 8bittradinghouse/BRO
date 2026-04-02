#!/usr/bin/env python3
"""Unified operator CLI for Bro workflows."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from typing import List

from prodesk.config import load_execution_config
from prodesk.repo import resolve_repo_root

CANONICAL_PAPER_CONFIG = "configs/profiles/paper_universal.yaml"
CANONICAL_PAPER_LOG_DIR = "./logs_exec/paper_universal"
CANONICAL_PAPER_PROFILE_NAME = "paper_universal"
CANONICAL_PAPER_SESSION_SCRIPT = "scripts/canonical_paper_session.sh"


def _run(cmd: List[str], *, cwd: pathlib.Path) -> int:
    print(f"[broctl] exec: {' '.join(cmd)}")
    return subprocess.run(cmd, check=False, cwd=str(cwd)).returncode


def _normalize_extra_args(values: List[str]) -> List[str]:
    if values and values[0] == "--":
        return values[1:]
    return values


def _has_option(args: List[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in args)


def _merge_defaults(extra: List[str], defaults: List[str]) -> List[str]:
    normalized = _normalize_extra_args(extra)
    merged = list(defaults)
    if _has_option(normalized, "--config") and "--config" in merged:
        idx = merged.index("--config")
        if idx + 1 < len(merged):
            del merged[idx : idx + 2]
        else:
            del merged[idx]
    return [*merged, *normalized]


def _extract_option_value(args: List[str], option: str) -> str | None:
    normalized = _normalize_extra_args(args)
    prefix = f"{option}="
    for idx, arg in enumerate(normalized):
        if arg == option and idx + 1 < len(normalized):
            return normalized[idx + 1]
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _run_script(repo_root: pathlib.Path, script_rel: str, extra: List[str], defaults: List[str]) -> int:
    script_path = repo_root / script_rel
    cmd = [sys.executable, str(script_path), *_merge_defaults(extra, defaults)]
    return _run(cmd, cwd=repo_root)


def _run_required_paper_preflight(
    repo_root: pathlib.Path,
    *,
    extra: List[str],
    default_config: str,
    default_log_dir: str,
) -> int:
    config_path = _extract_option_value(extra, "--config") or default_config
    log_dir = _extract_option_value(extra, "--log-dir") or default_log_dir
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "harness_qualify.py"),
        "--config",
        config_path,
        "--log-dir",
        log_dir,
        "--policy",
        "ops/harness_policy.yaml",
    ]
    return _run(cmd, cwd=repo_root)


def _resolve_path(repo_root: pathlib.Path, raw_path: str) -> pathlib.Path:
    path = pathlib.Path(str(raw_path))
    if path.is_absolute():
        return path.resolve()
    return (repo_root / path).resolve()


def _assert_canonical_paper_target(
    repo_root: pathlib.Path,
    *,
    extra: List[str],
    canonical_config: str,
    canonical_log_dir: str,
) -> None:
    explicit_config = _extract_option_value(extra, "--config")
    if explicit_config:
        explicit_resolved = _resolve_path(repo_root, explicit_config)
        canonical_resolved = _resolve_path(repo_root, canonical_config)
        if explicit_resolved != canonical_resolved:
            raise SystemExit(
                "[broctl] canonical paper mode forbids config overrides:"
                + f" observed={explicit_resolved} required={canonical_resolved}"
            )
    explicit_log_dir = _extract_option_value(extra, "--log-dir")
    if explicit_log_dir:
        explicit_log_resolved = _resolve_path(repo_root, explicit_log_dir)
        canonical_log_resolved = _resolve_path(repo_root, canonical_log_dir)
        if explicit_log_resolved != canonical_log_resolved:
            raise SystemExit(
                "[broctl] canonical paper mode forbids log-dir overrides:"
                + f" observed={explicit_log_resolved} required={canonical_log_resolved}"
            )


def _assert_paper_setup_lock(repo_root: pathlib.Path, *, extra: List[str], default_config: str) -> None:
    config_raw = _extract_option_value(extra, "--config") or default_config
    config_path = _resolve_path(repo_root, config_raw)
    try:
        cfg = load_execution_config(config_path)
    except Exception as exc:
        raise SystemExit(f"[broctl] failed loading config for setup lock check: {config_path} ({exc})") from exc
    mode_override = (_extract_option_value(extra, "--mode") or "").strip().lower()
    mode = mode_override or str(cfg.get("mode", "")).strip().lower()
    if mode != "paper":
        return
    runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
    if not bool(runtime.get("paper_enforce_setup_lock", False)):
        raise SystemExit(f"[broctl] paper setup lock is required but disabled: {config_path}")
    profile_name = str((cfg.get("profile") or {}).get("name") or "").strip()
    expected_profile = str(runtime.get("paper_expected_profile_name", "")).strip()
    if profile_name != CANONICAL_PAPER_PROFILE_NAME:
        raise SystemExit(
            "[broctl] canonical paper profile mismatch:"
            + f" observed={profile_name or 'missing'} required={CANONICAL_PAPER_PROFILE_NAME}"
        )
    if not expected_profile or profile_name != expected_profile:
        raise SystemExit(
            "[broctl] paper setup lock profile mismatch:"
            + f" observed={profile_name or 'missing'} expected={expected_profile or 'missing'} config={config_path}"
        )
    observed_fp = str((cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
    expected_fp = str(runtime.get("paper_expected_config_fingerprint_sha256", "")).strip().lower()
    if not expected_fp or observed_fp != expected_fp:
        raise SystemExit(
            "[broctl] paper setup lock fingerprint mismatch:"
            + f" observed={observed_fp or 'missing'} expected={expected_fp or 'missing'} config={config_path}"
        )


def _run_canonical_paper_session(repo_root: pathlib.Path, *, extra: List[str]) -> int:
    normalized = _normalize_extra_args(extra)
    forbidden_options = ("--config", "--log-dir", "--mode")
    for opt in forbidden_options:
        if _has_option(normalized, opt):
            raise SystemExit(f"[broctl] canonical paper session forbids {opt}; use default canonical path")
    cmd = [str((repo_root / CANONICAL_PAPER_SESSION_SCRIPT).resolve()), *normalized]
    return _run(cmd, cwd=repo_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro unified operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prestart = sub.add_parser("prestart", help="Run prestart safety gate")
    p_prestart.add_argument("args", nargs=argparse.REMAINDER)

    p_paper = sub.add_parser("paper", help="Run canonical paper profile")
    p_paper.add_argument("args", nargs=argparse.REMAINDER)

    p_stress = sub.add_parser("paper-stress", help="Run paper stress profile")
    p_stress.add_argument("args", nargs=argparse.REMAINDER)

    p_discipline = sub.add_parser("paper-discipline", help="Run paper discipline profile")
    p_discipline.add_argument("args", nargs=argparse.REMAINDER)

    p_promote = sub.add_parser("promote", help="Run promotion evidence gate")
    p_promote.add_argument("args", nargs=argparse.REMAINDER)

    p_canary = sub.add_parser("canary", help="Run prelive gate for live canary profile")
    p_canary.add_argument("args", nargs=argparse.REMAINDER)

    p_reconcile = sub.add_parser("reconcile", help="Run reconciliation report")
    p_reconcile.add_argument("args", nargs=argparse.REMAINDER)

    p_incident = sub.add_parser("incident", help="Build incident forensics bundle")
    p_incident.add_argument("args", nargs=argparse.REMAINDER)

    p_audit = sub.add_parser("audit", help="Run local CI-equivalent validation")
    p_audit.add_argument("args", nargs=argparse.REMAINDER)

    p_harness = sub.add_parser("harness", help="Run paper harness integrity audit on logs")
    p_harness.add_argument("args", nargs=argparse.REMAINDER)

    p_harness_qual = sub.add_parser("harness-qualify", help="Run deep paper harness qualification gate")
    p_harness_qual.add_argument("args", nargs=argparse.REMAINDER)

    p_ci = sub.add_parser("ci", help="Alias for local CI-equivalent validation")
    p_ci.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    repo_root = resolve_repo_root(start=pathlib.Path(__file__).resolve().parent)

    if args.command == "prestart":
        rc = _run_script(
            repo_root,
            "scripts/prestart_gate.py",
            list(args.args),
            ["--config", CANONICAL_PAPER_CONFIG],
        )
    elif args.command in {"paper", "paper-stress", "paper-discipline"}:
        _assert_canonical_paper_target(
            repo_root,
            extra=list(args.args),
            canonical_config=CANONICAL_PAPER_CONFIG,
            canonical_log_dir=CANONICAL_PAPER_LOG_DIR,
        )
        _assert_paper_setup_lock(
            repo_root,
            extra=list(args.args),
            default_config=CANONICAL_PAPER_CONFIG,
        )
        rc = _run_canonical_paper_session(repo_root, extra=list(args.args))
    elif args.command == "promote":
        rc = _run_script(
            repo_root,
            "scripts/promotion_evidence_gate.py",
            list(args.args),
            ["--policy", "ops/promotion_policy.yaml"],
        )
    elif args.command == "canary":
        rc = _run_script(
            repo_root,
            "scripts/prelive_gate.py",
            list(args.args),
            ["--config", "configs/profiles/live_canary.yaml", "--policy", "ops/ramp_policy.yaml"],
        )
    elif args.command == "reconcile":
        if "--run-id" not in list(args.args):
            raise SystemExit("broctl reconcile requires explicit --run-id")
        rc = _run_script(
            repo_root,
            "scripts/reconcile_daily.py",
            list(args.args),
            ["--config", CANONICAL_PAPER_CONFIG, "--log-dir", CANONICAL_PAPER_LOG_DIR],
        )
    elif args.command == "incident":
        rc = _run_script(
            repo_root,
            "scripts/forensics_bundle.py",
            list(args.args),
            ["--log-dir", CANONICAL_PAPER_LOG_DIR, "--config", CANONICAL_PAPER_CONFIG],
        )
    elif args.command in {"audit", "ci"}:
        rc = _run_script(repo_root, "scripts/ci_validate.py", list(args.args), [])
    elif args.command == "harness":
        rc = _run_script(
            repo_root,
            "scripts/paper_harness_audit.py",
            list(args.args),
            [
                "--config",
                CANONICAL_PAPER_CONFIG,
                "--log-dir",
                CANONICAL_PAPER_LOG_DIR,
                "--min-status-rows",
                "1",
                "--max-status-age-sec",
                "3153600000",
            ],
        )
    elif args.command == "harness-qualify":
        rc = _run_script(
            repo_root,
            "scripts/harness_qualify.py",
            list(args.args),
            [
                "--config",
                CANONICAL_PAPER_CONFIG,
                "--log-dir",
                CANONICAL_PAPER_LOG_DIR,
                "--policy",
                "ops/harness_policy.yaml",
            ],
        )
    else:
        raise SystemExit(f"unsupported command: {args.command}")

    raise SystemExit(int(rc))


if __name__ == "__main__":
    main()
