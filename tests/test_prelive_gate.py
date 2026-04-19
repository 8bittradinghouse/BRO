import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from scripts.prelive_gate import run_prelive_gate


class PreliveGateTests(unittest.TestCase):
    def _write_cfg(self, root: Path, *, mode: str = "live", allow_taker: bool = True) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        # Canonical doctrine fixtures must not set both doctrine and legacy sniper freshness keys.
        cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
        cfg["mode"] = mode
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["chainlink"]["enabled"] = False
        cfg["market_data"]["ws"]["enabled"] = False
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["auth"]["allow_taker"] = allow_taker
        if str(mode).strip().lower() == "live":
            cfg["wallet"]["approval_spender_targets"] = ["0x1111111111111111111111111111111111111111"]
        cfg["storage"]["log_dir"] = str(root / "logs_exec")
        cfg["storage"]["state_path"] = str(root / "logs_exec" / "state.json")
        cfg["runtime"]["guard_stop_file"] = str(root / "logs_exec" / "guard_stop.txt")
        path = root / "execution_config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    def _write_manifest(self, root: Path, run_id: str = "rid-test") -> str:
        log_dir = root / "logs_exec"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "manifest_schema_version": 2,
            "run_id": run_id,
            "config_fingerprint_sha256": "a" * 64,
            "config_source_sha256": "b" * 64,
            "code_fingerprint_sha256": "c" * 64,
        }
        (log_dir / f"run_manifest_{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        (log_dir / "status_2099-01-01.jsonl").write_text(json.dumps({"ts_utc": now, "run_id": run_id}) + "\n", encoding="utf-8")
        return run_id

    def _write_backup_bundle(self, root: Path) -> Path:
        backup_dir = root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        bundle = backup_dir / "bro_backup_2026-03-07.tar.gz"
        bundle.write_bytes(b"backup-data")
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        (backup_dir / f"{bundle.name}.sha256").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
        return backup_dir

    def test_prelive_gate_flags_missing_wallet_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            with mock.patch.dict(os.environ, {}, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                    allow_env_secrets_in_live=True,
                )
        self.assertFalse(result["ok"])
        self.assertTrue(any("secret_load_failed:" in x for x in result["findings"]))

    def test_prelive_gate_passes_minimal_when_env_valid_and_checks_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                    allow_env_secrets_in_live=True,
                )
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_prelive_gate_blocks_env_secret_sources_in_live_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                )
        self.assertFalse(result["ok"])
        self.assertTrue(any("live_secret_source_env_not_allowed:private_key" == x for x in result["findings"]))

    def test_prelive_gate_fails_on_missing_manifest_when_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            backup_dir = self._write_backup_bundle(root)
            run_id = "rid-missing"
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                    allow_env_secrets_in_live=True,
                )
        self.assertFalse(result["ok"])
        self.assertIn("run_manifest_missing", result["findings"])

    def test_prelive_gate_accepts_file_secret_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            pk_file = root / "pk.txt"
            funder_file = root / "funder.txt"
            pk_file.write_text("0x" + ("a" * 64), encoding="utf-8")
            funder_file.write_text("0x" + ("b" * 40), encoding="utf-8")
            payload["auth"]["private_key_source"] = {"mode": "file", "path": str(pk_file)}
            payload["auth"]["funder_source"] = {"mode": "file", "path": str(funder_file)}
            cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            env = {"SECURITY_ACK": "YES"}
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                    allow_env_secrets_in_live=True,
                )
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_prelive_gate_fails_live_discovery_without_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            payload["targets"]["discovery"]["enabled"] = True
            payload["targets"]["discovery"]["allow_token_ids"] = []
            cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                )
        self.assertFalse(result["ok"])
        self.assertIn("live_discovery_allow_token_ids_missing", result["findings"])

    def test_prelive_gate_accepts_live_discovery_with_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            payload["targets"]["discovery"]["enabled"] = True
            payload["targets"]["discovery"]["allow_token_ids"] = ["tok1", "tok2"]
            payload["targets"]["token_ids"] = ["tok1"]
            cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
            run_id = self._write_manifest(root)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="pilot_live",
                    run_id=run_id,
                    skip_readiness=True,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_run_integrity_audit=True,
                    allow_env_secrets_in_live=True,
                )
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_prelive_gate_requires_explicit_run_id_when_readiness_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = "rid-explicit-required"
            self._write_manifest(root, run_id=run_id)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = run_prelive_gate(
                    config_path=cfg_path,
                    policy_path=Path("ops/ramp_policy.yaml"),
                    required_stage="paper",
                    run_id=None,
                    skip_readiness=False,
                    skip_runtime_audit=True,
                    skip_config_consistency=True,
                    skip_manifest_check=False,
                    manifest_max_age_hours=48.0,
                    manifest_min_schema_version=2,
                    skip_backup_check=False,
                    backup_dir=backup_dir,
                    backup_max_age_hours=48.0,
                    skip_time_discipline_audit=True,
                    skip_run_integrity_audit=False,
                    allow_env_secrets_in_live=True,
                )
        self.assertFalse(result["ok"])
        self.assertIn("prelive_run_id_required", result["findings"])


if __name__ == "__main__":
    unittest.main()
