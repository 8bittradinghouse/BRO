#!/usr/bin/env python3
"""Container healthcheck for Bro."""

from __future__ import annotations

import argparse
import pathlib
import urllib.request
from typing import Any, Dict

import yaml


def _load_config(path: pathlib.Path) -> Dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _latest_status_file(log_dir: pathlib.Path) -> pathlib.Path | None:
    files = sorted(log_dir.glob("status_*.jsonl"))
    if not files:
        return None
    return files[-1]


def _status_fresh(log_dir: pathlib.Path, max_age_sec: float) -> bool:
    status_path = _latest_status_file(log_dir)
    if status_path is None or not status_path.exists():
        return False
    age = (pathlib.Path(status_path).stat().st_mtime)
    import time

    return (time.time() - age) <= max(1.0, float(max_age_sec))


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro container healthcheck")
    parser.add_argument("--config", default="/data/config.yaml", help="Runtime config path")
    parser.add_argument("--status-max-age-sec", type=float, default=180.0, help="Max age of latest status file")
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.config).resolve()
    if not cfg_path.exists():
        raise SystemExit(1)

    cfg = _load_config(cfg_path)
    metrics_cfg = cfg.get("metrics", {}) if isinstance(cfg, dict) else {}
    metrics_enabled = bool(metrics_cfg.get("enabled", True))

    if metrics_enabled:
        host = str(metrics_cfg.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(metrics_cfg.get("port", 9108))
        url = f"http://{host}:{port}/metrics"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if int(resp.status) >= 400:
                    raise SystemExit(1)
            return
        except Exception:
            raise SystemExit(1)

    storage_cfg = cfg.get("storage", {}) if isinstance(cfg, dict) else {}
    log_dir = pathlib.Path(str(storage_cfg.get("log_dir", "/logs"))).resolve()
    if not _status_fresh(log_dir, float(args.status_max_age_sec)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
