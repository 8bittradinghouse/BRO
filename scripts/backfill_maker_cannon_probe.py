#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = REPO_ROOT / "logs_exec" / "paper_universal" / "reports"
DEFAULT_LOG_DIR = REPO_ROOT / "logs_exec" / "paper_universal"
DEFAULT_MANIFEST_OUT = DEFAULT_LOG_DIR / "maker_cannon_probe_backfill_manifest.json"
NIGHTLY_SOAK_REPORT_SCRIPT = REPO_ROOT / "scripts" / "nightly_soak_report.py"
RECUT_SUBPROCESS_TIMEOUT_SEC = max(
    30.0,
    float(os.getenv("BACKFILL_MAKER_CANNON_PROBE_TIMEOUT_SEC", "1800")),
)


def _parse_utc_ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _session_bucket_from_ts(value: Any) -> str:
    parsed = _parse_utc_ts(value)
    if parsed is None:
        return "unknown"
    hour = int(parsed.hour)
    if 0 <= hour < 8:
        return "asia_dominant_heuristic"
    if 12 <= hour < 20:
        return "usa_europe_peak_heuristic"
    return "transition_heuristic"


def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _discover_run_dirs(report_root: pathlib.Path) -> list[pathlib.Path]:
    run_dirs = [path for path in report_root.iterdir() if path.is_dir()]
    run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return run_dirs


def _load_run_context(run_dir: pathlib.Path, log_dir: pathlib.Path) -> dict[str, Any]:
    nightly = _load_json(run_dir / "nightly_soak_report.json") or {}
    validation = _load_json(run_dir / "validation_summary.json") or {}
    canonical = _load_json(run_dir / "canonical_paper_validation.json") or {}

    run_id = (
        validation.get("run_id")
        or canonical.get("run_id")
        or (nightly.get("artifact_identity") or {}).get("run_id")
        or run_dir.name
    )
    run_contract_path = nightly.get("run_contract_path")
    if isinstance(run_contract_path, str) and run_contract_path.strip():
        contract_path = pathlib.Path(run_contract_path)
    else:
        contract_path = log_dir / f"run_contract_{run_id}.json"
    contract = _load_json(contract_path) or {}

    runtime_classification = canonical.get("runtime_classification")
    if not runtime_classification:
        runtime_classification = ((nightly.get("runtime_classification") or {}).get("classification"))

    duration_minutes = _coerce_float(nightly.get("duration_minutes"))
    start_ts = contract.get("start_ts")
    return {
        "run_dir": run_dir,
        "run_id": str(run_id),
        "report_root": run_dir.parent,
        "log_dir": pathlib.Path(contract.get("log_root") or nightly.get("log_dir") or log_dir),
        "runtime_classification": runtime_classification,
        "duration_minutes": duration_minutes,
        "run_start_ts_utc": start_ts,
        "run_start_session_bucket": _session_bucket_from_ts(start_ts),
        "has_probe_summary": (run_dir / "maker_cannon_late_window_probe_summary.json").exists(),
        "has_probe_rows": (run_dir / "maker_cannon_late_window_probe.jsonl").exists(),
    }


def discover_run_contexts(report_root: pathlib.Path, log_dir: pathlib.Path) -> list[dict[str, Any]]:
    return [_load_run_context(run_dir, log_dir) for run_dir in _discover_run_dirs(report_root)]


def select_run_contexts(
    contexts: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    limit: int | None = None,
    runtime_classification: str | None = None,
    min_duration_minutes: float | None = None,
    start_session_bucket: str | None = None,
    only_missing: bool = False,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for context in contexts:
        if run_id and context["run_id"] != run_id:
            continue
        if runtime_classification and context.get("runtime_classification") != runtime_classification:
            continue
        if min_duration_minutes is not None:
            duration = _coerce_float(context.get("duration_minutes"))
            if duration is None or duration < float(min_duration_minutes):
                continue
        if start_session_bucket and context.get("run_start_session_bucket") != start_session_bucket:
            continue
        if only_missing and bool(context.get("has_probe_summary")):
            continue
        selected.append(context)
    selected.sort(
        key=lambda item: (
            str(item.get("run_start_ts_utc") or ""),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    if limit is not None:
        selected = selected[:limit]
    selected.reverse()
    return selected


def build_recut_command(context: dict[str, Any], python_executable: str | None = None) -> list[str]:
    run_dir = pathlib.Path(context["run_dir"])
    return [
        python_executable or sys.executable,
        str(NIGHTLY_SOAK_REPORT_SCRIPT),
        "--log-dir",
        str(pathlib.Path(context["log_dir"])),
        "--run-id",
        str(context["run_id"]),
        "--out",
        str(run_dir / "nightly_soak_report.json"),
        "--summary-out",
        str(run_dir / "nightly_soak_report.txt"),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill maker cannon probe support artifacts across historical report runs.")
    parser.add_argument("--report-root", type=pathlib.Path, default=DEFAULT_REPORT_ROOT, help="Per-run report directory root.")
    parser.add_argument("--log-dir", type=pathlib.Path, default=DEFAULT_LOG_DIR, help="Log root passed to nightly_soak_report.")
    parser.add_argument("--run-id", default=None, help="Backfill only one run id.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the latest N selected runs.")
    parser.add_argument(
        "--runtime-classification",
        default="VALID_ACTIVE",
        help="Optional runtime-classification filter. Use empty string to disable.",
    )
    parser.add_argument("--min-duration-minutes", type=float, default=5.0, help="Minimum nightly duration to include.")
    parser.add_argument(
        "--start-session-bucket",
        default=None,
        choices=[
            "unknown",
            "asia_dominant_heuristic",
            "transition_heuristic",
            "usa_europe_peak_heuristic",
        ],
        help="Optional run-start heuristic session bucket filter.",
    )
    parser.add_argument("--only-missing", action="store_true", help="Only rebuild runs that do not yet have cannon-probe summary artifacts.")
    parser.add_argument("--manifest-out", type=pathlib.Path, default=DEFAULT_MANIFEST_OUT, help="Where to write the backfill manifest.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_root = args.report_root.resolve()
    log_dir = args.log_dir.resolve()
    contexts = discover_run_contexts(report_root=report_root, log_dir=log_dir)
    selected = select_run_contexts(
        contexts,
        run_id=args.run_id,
        limit=args.limit,
        runtime_classification=(args.runtime_classification or None),
        min_duration_minutes=args.min_duration_minutes,
        start_session_bucket=args.start_session_bucket,
        only_missing=bool(args.only_missing),
    )

    manifest: dict[str, Any] = {
        "tool": "backfill_maker_cannon_probe",
        "report_root": str(report_root),
        "default_log_dir": str(log_dir),
        "selected_run_count": int(len(selected)),
        "filters": {
            "run_id": args.run_id,
            "limit": args.limit,
            "runtime_classification": args.runtime_classification,
            "min_duration_minutes": args.min_duration_minutes,
            "start_session_bucket": args.start_session_bucket,
            "only_missing": bool(args.only_missing),
        },
        "runs": [],
    }

    overall_exit = 0
    for context in selected:
        command = build_recut_command(context)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=float(RECUT_SUBPROCESS_TIMEOUT_SEC),
        )
        manifest["runs"].append(
            {
                "run_id": context["run_id"],
                "run_start_ts_utc": context.get("run_start_ts_utc"),
                "run_start_session_bucket": context.get("run_start_session_bucket"),
                "runtime_classification": context.get("runtime_classification"),
                "duration_minutes": context.get("duration_minutes"),
                "had_probe_summary_before": bool(context.get("has_probe_summary")),
                "exit_code": int(completed.returncode),
                "stderr_tail": completed.stderr.strip().splitlines()[-10:],
            }
        )
        if completed.returncode != 0:
            overall_exit = completed.returncode

    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main())
