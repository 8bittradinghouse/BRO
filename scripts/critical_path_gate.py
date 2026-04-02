#!/usr/bin/env python3
"""Extra strict CI gate for high-risk code-path changes."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from typing import Any, Dict

import yaml


from prodesk.config import DEFAULT_EXECUTION_CONFIG
from scripts.prelive_gate import run_prelive_gate


def _write_backup_bundle(root: pathlib.Path) -> pathlib.Path:
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    bundle = backup_dir / "bro_backup_ci.tar.gz"
    bundle.write_bytes(b"ci-backup")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (backup_dir / f"{bundle.name}.sha256").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return backup_dir


def run_gate() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        # Canonical doctrine fixtures must not set both doctrine and legacy sniper freshness keys.
        cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
        cfg["mode"] = "live"
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["targets"]["token_expiry_utc_by_token"] = {"tok1": "2099-01-01T00:00:00Z"}
        cfg["targets"]["token_side_by_token"] = {"tok1": "YES"}
        cfg["targets"]["token_strike_by_token"] = {"tok1": 1.0}
        cfg["chainlink"]["enabled"] = False
        cfg["market_data"]["ws"]["enabled"] = False
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["check_clock_sync"] = False
        cfg["preflight"]["check_endpoint_health"] = False
        cfg["auth"]["allow_taker"] = True
        cfg["storage"]["log_dir"] = str(root / "logs_exec")
        cfg["storage"]["state_path"] = str(root / "logs_exec" / "state.json")
        cfg["runtime"]["guard_stop_file"] = str(root / "logs_exec" / "guard_stop.txt")

        cfg_path = root / "execution_config_live_ci.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

        log_dir = pathlib.Path(cfg["storage"]["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = "ci-critical-risk-run"
        now = dt.datetime.now(dt.timezone.utc)
        manifest = {
            "manifest_schema_version": 2,
            "run_id": run_id,
            "profile_name": "ci-critical",
            "git_commit": "deadbeef",
            "config_fingerprint_sha256": "a" * 64,
            "config_source_sha256": "b" * 64,
            "code_fingerprint_sha256": "c" * 64,
            "status_path": str((log_dir / "status_2099-01-01.jsonl").resolve()),
            "events_path": str((log_dir / "events_2099-01-01.jsonl").resolve()),
            "start_ts": now.isoformat().replace("+00:00", "Z"),
        }
        (log_dir / f"run_manifest_{run_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
        status = {"run_id": run_id, "ts_utc": now.isoformat().replace("+00:00", "Z")}
        event = {"run_id": run_id, "ts_utc": now.isoformat().replace("+00:00", "Z"), "event_type": "cycle"}
        (log_dir / "status_2099-01-01.jsonl").write_text(json.dumps(status) + "\n", encoding="utf-8")
        (log_dir / "events_2099-01-01.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

        backup_dir = _write_backup_bundle(root)

        env = {
            "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
            "POLYMARKET_FUNDER": "0x" + ("b" * 40),
            "SECURITY_ACK": "YES",
        }
        old_env = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            result = run_prelive_gate(
                config_path=cfg_path,
                policy_path=pathlib.Path("ops/ramp_policy.yaml"),
                required_stage="pilot_live",
                run_id=run_id,
                skip_readiness=True,
                skip_runtime_audit=False,
                skip_config_consistency=True,
                skip_manifest_check=False,
                manifest_max_age_hours=48.0,
                manifest_min_schema_version=2,
                skip_backup_check=False,
                backup_dir=backup_dir,
                backup_max_age_hours=48.0,
                skip_websocket_audit=False,
                skip_guardian_profile_audit=False,
                skip_alert_profile_audit=False,
                skip_time_discipline_audit=True,
                skip_run_integrity_audit=False,
                run_integrity_min_status_rows=1,
                run_integrity_max_status_age_sec=3153600000.0,
                allow_env_secrets_in_live=True,
            )
        finally:
            for key, prev in old_env.items():
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

        return {
            "ok": bool(result.get("ok", False)),
            "finding_count": int(result.get("finding_count", 0)),
            "findings": list(result.get("findings", [])),
        }


def main() -> None:
    result = run_gate()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
