import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import datetime as dt


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import maker_peak_session_harvest  # noqa: E402


class MakerPeakSessionHarvestTests(unittest.TestCase):
    def test_dry_run_writes_sweep_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sweep_root = pathlib.Path(tmpdir) / "sweeps"
            argv = [
                "maker_peak_session_harvest.py",
                "--session-count",
                "3",
                "--active-minutes",
                "20",
                "--wait-sec",
                "25",
                "--sweep-root",
                str(sweep_root),
                "--dry-run",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = maker_peak_session_harvest.main()
            self.assertEqual(rc, 0)

            sweep_dirs = sorted(sweep_root.glob("maker_peak_session_sweep_*"))
            self.assertEqual(len(sweep_dirs), 1)
            manifest = json.loads((sweep_dirs[0] / "sweep_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["session_count"], 3)
            self.assertEqual(manifest["active_minutes"], 20.0)
            self.assertEqual(manifest["wait_sec"], 25.0)
            self.assertTrue(manifest["dry_run"])
            self.assertTrue(manifest["archive_export"])

    def test_deadline_guard_stops_before_launching_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sweep_root = pathlib.Path(tmpdir) / "sweeps"
            argv = [
                "maker_peak_session_harvest.py",
                "--session-count",
                "3",
                "--active-minutes",
                "20",
                "--wait-sec",
                "25",
                "--sweep-root",
                str(sweep_root),
                "--stop-before-local",
                "2026-04-28T14:00:00",
                "--local-timezone",
                "America/Chicago",
            ]
            fake_now = dt.datetime(2026, 4, 28, 18, 50, 0, tzinfo=dt.timezone.utc)
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                maker_peak_session_harvest, "_utc_now", return_value=fake_now
            ), mock.patch.object(maker_peak_session_harvest.subprocess, "run") as mocked_run:
                rc = maker_peak_session_harvest.main()

            self.assertEqual(rc, 0)
            mocked_run.assert_not_called()
            sweep_dirs = sorted(sweep_root.glob("maker_peak_session_sweep_*"))
            self.assertEqual(len(sweep_dirs), 1)
            ledger_lines = (sweep_dirs[0] / "run_ledger.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_lines), 1)
            ledger_row = json.loads(ledger_lines[0])
            self.assertEqual(ledger_row["status"], "not_started_deadline_guard")
            manifest = json.loads((sweep_dirs[0] / "sweep_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stop_before_local"], "2026-04-28T14:00:00")
            self.assertEqual(manifest["local_timezone"], "America/Chicago")
            self.assertEqual(manifest["stop_before_utc"], "2026-04-28T19:00:00Z")

    def test_completed_runs_flush_ledger_before_next_session_starts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sweep_root = pathlib.Path(tmpdir) / "sweeps"
            argv = [
                "maker_peak_session_harvest.py",
                "--session-count",
                "2",
                "--active-minutes",
                "20",
                "--wait-sec",
                "25",
                "--sweep-root",
                str(sweep_root),
            ]

            def fake_run(cmd, cwd, capture_output, text, check, timeout):  # noqa: ARG001
                fake_run.calls += 1
                self.assertGreater(float(timeout), 0.0)
                sweep_dirs = sorted(sweep_root.glob("maker_peak_session_sweep_*"))
                self.assertEqual(len(sweep_dirs), 1)
                run_ids_path = sweep_dirs[0] / "run_ids.txt"
                ledger_path = sweep_dirs[0] / "run_ledger.jsonl"
                if fake_run.calls == 2:
                    self.assertEqual(run_ids_path.read_text(encoding="utf-8").count("\n"), 1)
                    self.assertEqual(ledger_path.read_text(encoding="utf-8").count("\n"), 1)
                payload = {"report_root": f"/tmp/report_{fake_run.calls}", "run_contract_path": f"/tmp/contract_{fake_run.calls}.json"}
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

            fake_run.calls = 0
            fake_now = dt.datetime(2026, 4, 28, 13, 0, 0, tzinfo=dt.timezone.utc)
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                maker_peak_session_harvest, "_utc_now", return_value=fake_now
            ), mock.patch.object(maker_peak_session_harvest.subprocess, "run", side_effect=fake_run):
                rc = maker_peak_session_harvest.main()

            self.assertEqual(rc, 0)
            self.assertEqual(fake_run.calls, 2)
            sweep_dirs = sorted(sweep_root.glob("maker_peak_session_sweep_*"))
            self.assertEqual(len(sweep_dirs), 1)
            run_ids = (sweep_dirs[0] / "run_ids.txt").read_text(encoding="utf-8").splitlines()
            ledger_rows = [
                json.loads(line)
                for line in (sweep_dirs[0] / "run_ledger.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(run_ids), 2)
            self.assertEqual(len(ledger_rows), 2)


if __name__ == "__main__":
    unittest.main()
