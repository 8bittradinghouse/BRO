#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SWEEP_ROOT = REPO_ROOT / "logs_exec" / "paper_universal" / "maker_peak_session_sweeps"
CANONICAL_RUNNER = REPO_ROOT / "scripts" / "canonical_paper_session.sh"
SESSION_SUBPROCESS_TIMEOUT_SEC = max(
    30.0,
    float(os.getenv("MAKER_PEAK_SESSION_TIMEOUT_SEC", "3600")),
)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _parse_local_deadline(raw_value: str, tz_name: str) -> dt.datetime:
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise ValueError("empty local deadline")
    tz = ZoneInfo(tz_name)
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.astimezone(dt.timezone.utc)


def _parse_result_payload(stdout_text: str) -> dict[str, Any] | None:
    normalized = str(stdout_text or "").strip()
    if not normalized:
        return None
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded sequence of canonical 20m maker-session specimens and preserve an exact run ledger."
    )
    parser.add_argument("--session-count", type=int, default=18, help="Number of canonical sessions to run.")
    parser.add_argument("--active-minutes", type=float, default=20.0, help="Active runtime minutes per session.")
    parser.add_argument("--wait-sec", type=float, default=25.0, help="Deploy wait seconds per session.")
    parser.add_argument(
        "--stop-before-local",
        default="",
        help="Optional local wall-clock cutoff (ISO-like, e.g. 2026-04-28T14:00:00). No new session starts if its active window would cross this time.",
    )
    parser.add_argument(
        "--local-timezone",
        default="America/Chicago",
        help="Timezone used to interpret --stop-before-local.",
    )
    parser.add_argument(
        "--sweep-root",
        type=pathlib.Path,
        default=DEFAULT_SWEEP_ROOT,
        help="Root directory where the sweep ledger and logs will be stored.",
    )
    parser.add_argument(
        "--archive-export",
        dest="archive_export",
        action="store_true",
        help="Pass --archive-export into canonical paper runs.",
    )
    parser.add_argument(
        "--no-archive-export",
        dest="archive_export",
        action="store_false",
        help="Disable archive export for sweep runs.",
    )
    parser.set_defaults(archive_export=True)
    parser.add_argument(
        "--build-images",
        dest="build_images",
        action="store_true",
        help="Use canonical build behavior for each run.",
    )
    parser.add_argument(
        "--no-build",
        dest="build_images",
        action="store_false",
        help="Skip image build during each run.",
    )
    parser.set_defaults(build_images=True)
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="Stop the sweep on the first non-zero canonical session exit code.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the sweep plan and ledger scaffolding without executing runtime sessions.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.session_count <= 0:
        raise SystemExit("session-count must be positive")
    if args.active_minutes <= 0:
        raise SystemExit("active-minutes must be positive")
    if args.wait_sec <= 0:
        raise SystemExit("wait-sec must be positive")

    stop_before_utc: dt.datetime | None = None
    if str(args.stop_before_local or "").strip():
        try:
            stop_before_utc = _parse_local_deadline(str(args.stop_before_local), str(args.local_timezone))
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise SystemExit(f"invalid stop-before-local: {exc}") from exc

    sweep_stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    sweep_dir = args.sweep_root.resolve() / f"maker_peak_session_sweep_{sweep_stamp}"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    run_ids_path = sweep_dir / "run_ids.txt"
    ledger_path = sweep_dir / "run_ledger.jsonl"
    manifest_path = sweep_dir / "sweep_manifest.json"

    manifest = {
        "sweep_kind": "maker_peak_session_harvest",
        "generated_at_utc": _utc_now().isoformat().replace("+00:00", "Z"),
        "repo_root": str(REPO_ROOT),
        "canonical_runner": str(CANONICAL_RUNNER),
        "session_count": int(args.session_count),
        "active_minutes": float(args.active_minutes),
        "wait_sec": float(args.wait_sec),
        "stop_before_local": str(args.stop_before_local or ""),
        "local_timezone": str(args.local_timezone),
        "stop_before_utc": stop_before_utc.isoformat().replace("+00:00", "Z") if stop_before_utc else "",
        "archive_export": bool(args.archive_export),
        "build_images": bool(args.build_images),
        "dry_run": bool(args.dry_run),
        "run_ids_file": str(run_ids_path),
        "ledger_file": str(ledger_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "sweep_dir": str(sweep_dir),
                    "run_ids_file": str(run_ids_path),
                    "ledger_file": str(ledger_path),
                    "manifest_file": str(manifest_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    overall_exit_code = 0
    with run_ids_path.open("w", encoding="utf-8") as run_id_handle, ledger_path.open("w", encoding="utf-8") as ledger_handle:
        for index in range(1, int(args.session_count) + 1):
            if stop_before_utc is not None:
                projected_finish = _utc_now() + dt.timedelta(minutes=float(args.active_minutes), seconds=float(args.wait_sec))
                if projected_finish > stop_before_utc:
                    ledger_handle.write(
                        json.dumps(
                            {
                                "sequence_index": index,
                                "status": "not_started_deadline_guard",
                                "observed_at_utc": _utc_now().isoformat().replace("+00:00", "Z"),
                                "projected_finish_utc": projected_finish.isoformat().replace("+00:00", "Z"),
                                "stop_before_utc": stop_before_utc.isoformat().replace("+00:00", "Z"),
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    ledger_handle.flush()
                    os.fsync(ledger_handle.fileno())
                    break
            run_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            stdout_path = sweep_dir / f"{index:02d}_{run_id}_stdout.log"
            stderr_path = sweep_dir / f"{index:02d}_{run_id}_stderr.log"
            cmd = [
                str(CANONICAL_RUNNER),
                "--session-id",
                session_id,
                "--run-id",
                run_id,
                "--active-minutes",
                str(args.active_minutes),
                "--wait-sec",
                str(args.wait_sec),
            ]
            if args.archive_export:
                cmd.append("--archive-export")
            if not args.build_images:
                cmd.append("--no-build")

            started_at = _utc_now()
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
                timeout=float(SESSION_SUBPROCESS_TIMEOUT_SEC),
            )
            finished_at = _utc_now()
            stdout_path.write_text(str(proc.stdout or ""), encoding="utf-8")
            stderr_path.write_text(str(proc.stderr or ""), encoding="utf-8")
            payload = _parse_result_payload(str(proc.stdout or ""))
            report_root = (payload or {}).get("report_root")
            run_contract_path = (payload or {}).get("run_contract_path")
            ledger_row = {
                "sequence_index": index,
                "run_id": run_id,
                "session_id": session_id,
                "started_at_utc": started_at.isoformat().replace("+00:00", "Z"),
                "finished_at_utc": finished_at.isoformat().replace("+00:00", "Z"),
                "exit_code": int(proc.returncode),
                "report_root": str(report_root or ""),
                "run_contract_path": str(run_contract_path or ""),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            }
            ledger_handle.write(json.dumps(ledger_row, sort_keys=True) + "\n")
            run_id_handle.write(run_id + "\n")
            ledger_handle.flush()
            run_id_handle.flush()
            os.fsync(ledger_handle.fileno())
            os.fsync(run_id_handle.fileno())
            if proc.returncode != 0:
                overall_exit_code = proc.returncode
                if args.stop_on_failure:
                    break

    print(
        json.dumps(
            {
                "status": "completed",
                "overall_exit_code": int(overall_exit_code),
                "sweep_dir": str(sweep_dir),
                "run_ids_file": str(run_ids_path),
                "ledger_file": str(ledger_path),
                "manifest_file": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
