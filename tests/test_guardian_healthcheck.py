import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts.guardian_healthcheck import main


class GuardianHealthcheckTests(unittest.TestCase):
    def test_passes_with_fresh_status(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "btc_paper"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "status_2099-01-01.jsonl").write_text('{"ok":1}\n', encoding="utf-8")
            prev = os.sys.argv
            os.sys.argv = ["guardian_healthcheck.py", "--log-dir", str(log_dir), "--max-status-age-sec", "60"]
            try:
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 0)
            finally:
                os.sys.argv = prev

    def test_fails_when_status_stale(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "btc_paper"
            log_dir.mkdir(parents=True, exist_ok=True)
            status = log_dir / "status_2099-01-01.jsonl"
            status.write_text('{"ok":1}\n', encoding="utf-8")
            old = time.time() - 3600
            os.utime(status, (old, old))
            prev = os.sys.argv
            os.sys.argv = ["guardian_healthcheck.py", "--log-dir", str(log_dir), "--max-status-age-sec", "10"]
            try:
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 1)
            finally:
                os.sys.argv = prev

    def test_fails_when_guard_stop_file_exists(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "btc_paper"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "status_2099-01-01.jsonl").write_text('{"ok":1}\n', encoding="utf-8")
            stop = log_dir / "guard_stop.txt"
            stop.write_text("stop\n", encoding="utf-8")
            prev = os.sys.argv
            os.sys.argv = [
                "guardian_healthcheck.py",
                "--log-dir",
                str(log_dir),
                "--guard-stop-file",
                str(stop),
                "--max-status-age-sec",
                "60",
            ]
            try:
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 1)
            finally:
                os.sys.argv = prev


if __name__ == "__main__":
    unittest.main()
