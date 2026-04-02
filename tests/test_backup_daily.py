import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BackupDailyScriptTests(unittest.TestCase):
    def test_backup_daily_creates_bundle_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            backup_dir = root / "backups"
            log_dir.mkdir(parents=True, exist_ok=True)
            backup_dir.mkdir(parents=True, exist_ok=True)

            (log_dir / "status_2026-03-07.jsonl").write_text('{"ok":1}\n', encoding="utf-8")
            (log_dir / "events_2026-03-07.jsonl").write_text('{"event":"x"}\n', encoding="utf-8")
            (log_dir / "run_manifest_demo.json").write_text('{"run_id":"demo"}\n', encoding="utf-8")
            state_path = root / "state.json"
            state_path.write_text('{"state":"ok"}\n', encoding="utf-8")

            cmd = [
                sys.executable,
                "scripts/backup_daily.py",
                "--log-dir",
                str(log_dir),
                "--state-path",
                str(state_path),
                "--out-dir",
                str(backup_dir),
                "--require-files-min",
                "2",
                "--exclude-glob",
                "events_*",
            ]
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

            bundles = sorted(backup_dir.glob("bro_backup_*.tar.gz"))
            self.assertEqual(len(bundles), 1)
            bundle = bundles[0]
            self.assertTrue((backup_dir / f"{bundle.name}.sha256").exists())
            meta_path = backup_dir / f"{bundle.name}.meta.json"
            self.assertTrue(meta_path.exists())
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(meta.get("files_added", 0)), 2)
            self.assertIn("events_*", list(meta.get("exclude_globs", [])))

    def test_backup_daily_fails_when_minimum_files_not_met(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            backup_dir = root / "backups"
            log_dir.mkdir(parents=True, exist_ok=True)
            backup_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                sys.executable,
                "scripts/backup_daily.py",
                "--log-dir",
                str(log_dir),
                "--out-dir",
                str(backup_dir),
                "--require-files-min",
                "1",
            ]
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("files_added_below_min", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
