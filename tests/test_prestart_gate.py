import json
import tempfile
import unittest
from pathlib import Path

from scripts.prestart_gate import run_gate


class PrestartGateTests(unittest.TestCase):
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
            cfg = root / "cfg.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        'bot_name: "Bro-Test"',
                        'mode: "paper"',
                        "storage:",
                        f'  log_dir: "{log_dir}"',
                        f'  state_path: "{log_dir / "state.json"}"',
                        "runtime:",
                        f'  guard_stop_file: "{guard}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
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
            cfg = root / "cfg.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        'bot_name: "Bro-Test"',
                        'mode: "paper"',
                        "storage:",
                        f'  log_dir: "{log_dir}"',
                        f'  state_path: "{log_dir / "state.json"}"',
                        "runtime:",
                        f'  guard_stop_file: "{guard}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
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
            cfg = root / "cfg.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        'bot_name: "Bro-Test"',
                        'mode: "paper"',
                        "storage:",
                        f'  log_dir: "{base_log_dir}"',
                        f'  state_path: "{base_log_dir / "state.json"}"',
                        "runtime:",
                        f'  guard_stop_file: "{base_log_dir / "guard_stop.txt"}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_gate(config_path=cfg, allow_kill_switch=False, allow_guard_file=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)
        self.assertEqual(result["log_dir"], str(base_log_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
