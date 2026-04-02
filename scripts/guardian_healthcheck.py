#!/usr/bin/env python3
"""Guardian container healthcheck based on status freshness and guard-stop state."""

from __future__ import annotations

import argparse
import pathlib
import time


def _latest_status_file(log_dir: pathlib.Path) -> pathlib.Path | None:
    files = sorted(log_dir.glob("status_*.jsonl"))
    if not files:
        return None
    return files[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro guardian container healthcheck")
    parser.add_argument("--log-dir", required=True, help="Run log directory path")
    parser.add_argument("--guard-stop-file", default="", help="Optional guard stop file path")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Max age of latest status file")
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve()
    if not log_dir.exists():
        raise SystemExit(1)

    guard_stop_raw = str(args.guard_stop_file or "").strip()
    if guard_stop_raw:
        guard_stop = pathlib.Path(guard_stop_raw).resolve()
        if guard_stop.exists():
            raise SystemExit(1)

    status_path = _latest_status_file(log_dir)
    if status_path is None or not status_path.exists():
        raise SystemExit(1)

    age_sec = time.time() - status_path.stat().st_mtime
    if age_sec > max(1.0, float(args.max_status_age_sec)):
        raise SystemExit(1)

    raise SystemExit(0)


if __name__ == "__main__":
    main()
