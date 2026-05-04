import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.prestart_gate import run_gate


class PrestartGateTests(unittest.TestCase):
    @staticmethod
    def _write_valid_config(*, root: Path, log_dir: Path, guard: Path, runtime_overrides: dict | None = None) -> Path:
        cfg = {
            "bot_name": "Bro-Prestart-Test",
            "mode": "paper",
            "asset": {
                "symbol": "BTC",
                "chainlink_symbols": ["btc/usd"],
                "discovery_symbols": ["BTC"],
            },
            "targets": {
                "token_ids": [],
                "discovery": {"enabled": True},
            },
            "storage": {
                "log_dir": str(log_dir),
                "state_path": str(log_dir / "state.json"),
            },
            "runtime": {
                "guard_stop_file": str(guard),
                "paper_enforce_setup_lock": False,
            },
            "security": {
                "allowed_storage_roots": [str(log_dir)],
            },
        }
        if isinstance(runtime_overrides, dict):
            cfg["runtime"].update(runtime_overrides)
        path = root / "cfg.yaml"
        path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        return path

    def test_blocks_when_guard_file_present_or_kill_switch_true(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            guard = log_dir / "guard_stop.txt"
            guard.write_text("stop\n", encoding="utf-8")
            status = log_dir / "status_2026-01-01.jsonl"
            status.write_text(
                json.dumps({"ts_utc": "2026-01-01T00:00:00Z", "kill_switch": True, "kill_reason": "x"}) + "\n",
                encoding="utf-8",
            )
            cfg = self._write_valid_config(root=root, log_dir=log_dir, guard=guard)
            result = run_gate(config_path=cfg, allow_kill_switch=False, allow_guard_file=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["finding_count"], 2)

    def test_allows_with_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            guard = log_dir / "guard_stop.txt"
            guard.write_text("stop\n", encoding="utf-8")
            status = log_dir / "status_2026-01-01.jsonl"
            status.write_text(
                json.dumps({"ts_utc": "2026-01-01T00:00:00Z", "kill_switch": True, "kill_reason": "x"}) + "\n",
                encoding="utf-8",
            )
            cfg = self._write_valid_config(root=root, log_dir=log_dir, guard=guard)
            result = run_gate(config_path=cfg, allow_kill_switch=True, allow_guard_file=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)

    def test_does_not_auto_discover_scoped_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base_log_dir = root / "logs_exec"
            scoped = base_log_dir / "btc_paper"
            scoped.mkdir(parents=True, exist_ok=True)
            (scoped / "guard_stop.txt").write_text("stop\n", encoding="utf-8")
            (scoped / "status_2026-01-01.jsonl").write_text(
                json.dumps({"ts_utc": "2026-01-01T00:00:00Z", "kill_switch": True, "kill_reason": "y"}) + "\n",
                encoding="utf-8",
            )
            cfg = self._write_valid_config(
                root=root,
                log_dir=base_log_dir,
                guard=(base_log_dir / "guard_stop.txt"),
            )
            result = run_gate(config_path=cfg, allow_kill_switch=False, allow_guard_file=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)
        self.assertEqual(result["log_dir"], str(base_log_dir.resolve()))
        self.assertEqual(int(result.get("warning_count") or 0), 0)

    def test_unreadable_status_file_surfaces_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            guard = log_dir / "guard_stop.txt"
            # Directory matches status glob but is unreadable as JSONL file.
            (log_dir / "status_2026-01-01.jsonl").mkdir(parents=True, exist_ok=True)
            cfg = self._write_valid_config(root=root, log_dir=log_dir, guard=guard)
            result = run_gate(config_path=cfg, allow_kill_switch=False, allow_guard_file=True)
        self.assertTrue(result["ok"])
        self.assertEqual(int(result.get("finding_count") or 0), 0)
        self.assertGreaterEqual(int(result.get("warning_count") or 0), 1)
        self.assertTrue(any(str(x).startswith("status_file_read_error:") for x in result.get("warnings", [])))

    def test_fails_closed_when_setup_lock_mismatch_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            guard = log_dir / "guard_stop.txt"
            cfg = self._write_valid_config(
                root=root,
                log_dir=log_dir,
                guard=guard,
                runtime_overrides={
                    "paper_enforce_setup_lock": True,
                    "paper_expected_profile_name": "fixture_expected_profile",
                    "paper_expected_config_fingerprint_sha256": "a" * 64,
                },
            )
            result = run_gate(config_path=cfg, allow_kill_switch=True, allow_guard_file=True)
        self.assertFalse(result["ok"])
        self.assertEqual(int(result.get("finding_count") or 0), 1)
        self.assertTrue(any(str(x).startswith("config_load_error:") for x in result.get("findings", [])))


if __name__ == "__main__":
    unittest.main()
