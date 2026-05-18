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

from prodesk.canonical_authority import CAPABILITY_VALIDATE_POSTRUN
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.prelive_gate import run_prelive_gate


class PreliveGateTests(unittest.TestCase):
    @staticmethod
    def _time_policy() -> dict:
        return {
            "source_of_truth": "utc_wall_clock",
            "fallback_logic": "source_ts_utc_then_ts_receive_utc_then_ts_event_utc",
            "skew_tolerance_ms": 120.0,
            "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
        }

    def _write_cfg(
        self,
        root: Path,
        *,
        mode: str = "live",
        allow_taker: bool = True,
        include_legacy_queue_pressure: bool = False,
    ) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        # Canonical doctrine fixtures must not set both doctrine and deprecated taker freshness keys.
        cfg["taker"].pop("max_chainlink_tick_age_sec", None)
        cfg["mode"] = mode
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["chainlink"]["enabled"] = False
        cfg["market_data"]["ws"]["enabled"] = False
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["auth"]["allow_taker"] = allow_taker
        if str(mode).strip().lower() == "live":
            cfg["wallet"]["approval_spender_targets"] = ["0x1111111111111111111111111111111111111111"]
        if include_legacy_queue_pressure:
            cfg["strategy"]["maker_competitiveness"]["queue_pressure"] = {
                "enabled": "definitely_not_bool",
                "allowed_stages": ["EXTREME_ONLY"],
                "inside_price_ticks": 0,
            }
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

    def _write_host_time_sync_artifacts(self, *, root: Path, session_id: str, run_id: str, sample_count: int = 1) -> None:
        report_root = root / "logs_exec" / "sessions" / session_id / "reports"
        report_root.mkdir(parents=True, exist_ok=True)
        base_payload = {
            "session_id": session_id,
            "run_id": run_id,
            "available": True,
            "clock_state": "synced",
            "system_clock_synchronized": True,
            "ntp_service_active": True,
            "stratum": 2,
            "offset_ms": 2.5,
            "jitter_ms": 1.5,
            "root_distance_ms": 36.0,
        }
        start_payload = dict(base_payload, phase="active_start")
        stop_payload = dict(base_payload, phase="active_stop")
        (report_root / "host_time_sync_active_start.json").write_text(
            json.dumps(start_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (report_root / "host_time_sync_active_stop.json").write_text(
            json.dumps(stop_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sample_rows = []
        for idx in range(sample_count):
            sample_rows.append(dict(base_payload, phase="active_sample", elapsed_active_sec=float(idx * 60)))
        (report_root / "host_time_sync_active_samples.jsonl").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in sample_rows) + ("\n" if sample_rows else ""),
            encoding="utf-8",
        )

    def _write_authoritative_time_contract(self, root: Path, run_id: str) -> Path:
        log_dir = root / "logs_exec"
        log_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        status_path = log_dir / "status_2099-01-01.jsonl"
        events_path = log_dir / "events_2099-01-01.jsonl"
        if not events_path.exists():
            events_path.write_text("", encoding="utf-8")
        now = dt.datetime.now(dt.timezone.utc)
        status_rows = [
            {
                "ts_utc": (now - dt.timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
                "run_id": run_id,
                "time_policy": self._time_policy(),
            }
        ]
        status_path.write_text("\n".join(json.dumps(row) for row in status_rows) + "\n", encoding="utf-8")
        start_ts = (now - dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        stop_ts = (now - dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        session_id = f"sid-{run_id}"
        contract_payload = build_run_contract(
            session_id=session_id,
            run_id=run_id,
            phase="validate_postrun",
            session_type="live_prelive",
            authority_level="authoritative",
            allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
            manifest_path=manifest_path,
            log_root=log_dir,
            state_root=root,
            start_ts=start_ts,
            stop_ts=stop_ts,
            evidence_slice_start_ts=start_ts,
            evidence_slice_end_ts=stop_ts,
            status_path=str(status_path),
            events_path=str(events_path),
            errors_path="",
            status_slice_path=str(status_path),
            events_slice_path=str(events_path),
        )
        contract_path = log_dir / f"run_contract_{run_id}.json"
        write_run_contract(contract_path, contract_payload, allow_open=False)
        self._write_host_time_sync_artifacts(root=root, session_id=session_id, run_id=run_id)
        return contract_path

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
            self._write_authoritative_time_contract(root, run_id)
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

    def test_prelive_gate_surfaces_ignored_legacy_queue_pressure_warning_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(
                root,
                mode="live",
                allow_taker=True,
                include_legacy_queue_pressure=True,
            )
            run_id = self._write_manifest(root)
            self._write_authoritative_time_contract(root, run_id)
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
        self.assertEqual(int(result.get("compatibility_warning_count") or 0), 1)
        self.assertIn(
            "strategy.maker_competitiveness.queue_pressure",
            list((result.get("checks", {}).get("config_compatibility", {}) or {}).get("ignored_compatibility_fields") or []),
        )
        self.assertIn(
            "removed queue-pressure compatibility surface",
            "\n".join(str(x) for x in result.get("compatibility_warnings") or []),
        )

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
            self._write_authoritative_time_contract(root, run_id)
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
            self._write_authoritative_time_contract(root, run_id)
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

    def test_prelive_gate_passes_run_contract_path_into_time_discipline_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = self._write_manifest(root)
            contract_path = self._write_authoritative_time_contract(root, run_id)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("scripts.prelive_gate.run_time_discipline_audit", return_value={"findings": []}) as audit_mock:
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
        kwargs = audit_mock.call_args.kwargs
        self.assertEqual(Path(str(kwargs.get("run_contract_path"))).resolve(), contract_path.resolve())

    def test_prelive_gate_surfaces_timing_warnings_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            run_id = self._write_manifest(root)
            self._write_authoritative_time_contract(root, run_id)
            backup_dir = self._write_backup_bundle(root)
            env = {
                "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
                "POLYMARKET_FUNDER": "0x" + ("b" * 40),
                "SECURITY_ACK": "YES",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch(
                    "scripts.prelive_gate.run_websocket_hardening_audit",
                    return_value={"findings": [], "warnings": ["timing_watch_book_feed_last_msg_age_warn_band_p95:9.100>warn:9.000:limit:12.000"]},
                ), mock.patch(
                    "scripts.prelive_gate.run_time_discipline_audit",
                    return_value={"findings": [], "warnings": ["timing_watch_duplicate_selection_gate_timing_owner_active"]},
                ):
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
        self.assertEqual(int(result.get("timing_warning_count") or 0), 2)
        self.assertIn(
            "timing_watch_duplicate_selection_gate_timing_owner_active",
            result.get("timing_warnings", []),
        )
        self.assertTrue(
            any(str(x).startswith("timing_watch_book_feed_last_msg_age_warn_band_p95:") for x in result.get("timing_warnings", [])),
            msg=result.get("timing_warnings", []),
        )

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

    def test_prelive_gate_does_not_expect_removed_taker_enable_bridge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=True)
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
            run_id = self._write_manifest(root)
            self._write_authoritative_time_contract(root, run_id)
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
        self.assertTrue(result["ok"])
        self.assertNotIn("taker_enable_bridge_mismatch", "\n".join(result["findings"]))

    def test_prelive_gate_auth_taker_mismatch_uses_effective_taker_enable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root, mode="live", allow_taker=False)
            run_id = self._write_manifest(root)
            self._write_authoritative_time_contract(root, run_id)
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
        self.assertFalse(result["ok"])
        self.assertIn("auth_taker_mismatch:taker.enabled=true but auth.allow_taker=false", result["findings"])


if __name__ == "__main__":
    unittest.main()
