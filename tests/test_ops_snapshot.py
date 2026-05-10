import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.ops_snapshot import run_snapshot


class OpsSnapshotTests(unittest.TestCase):
    def _write_fixture(self, root: Path, run_id: str = "rid-1") -> Path:
        log_dir = root / "logs_exec"
        log_dir.mkdir(parents=True, exist_ok=True)
        status_path = (log_dir / "status_2026-03-07.jsonl").resolve()
        events_path = (log_dir / "events_2026-03-07.jsonl").resolve()
        (log_dir / f"run_manifest_{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "manifest_schema_version": 2,
                    "profile_name": "test-profile",
                    "git_commit": "deadbeef",
                    "config_fingerprint_sha256": "a" * 64,
                    "code_fingerprint_sha256": "b" * 64,
                    "status_path": str(status_path),
                    "events_path": str(events_path),
                    "start_ts": "2099-01-01T00:00:00.000Z",
                }
            ),
            encoding="utf-8",
        )
        (log_dir / "status_2026-03-07.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"ts_utc": "2099-01-01T00:00:00.000Z", "run_id": run_id, "gauge.open_orders": 1}),
                    json.dumps({"ts_utc": "2099-01-01T00:00:01.000Z", "run_id": run_id, "gauge.open_orders": 1}),
                    json.dumps({"ts_utc": "2099-01-01T00:00:02.000Z", "run_id": run_id, "gauge.open_orders": 1}),
                    json.dumps({"ts_utc": "2099-01-01T00:00:03.000Z", "run_id": run_id, "gauge.open_orders": 1}),
                    json.dumps({"ts_utc": "2099-01-01T00:00:04.000Z", "run_id": run_id, "gauge.open_orders": 1}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (log_dir / "events_2026-03-07.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"ts_utc": "2099-01-01T00:00:00.100Z", "run_id": run_id, "event_type": "order_submit"}),
                    json.dumps({"ts_utc": "2099-01-01T00:00:00.200Z", "run_id": run_id, "event_type": "fill", "price": 0.5, "size": 1.0, "side": "BUY", "token_id": "t1"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (log_dir / "errors_2026-03-07.jsonl").write_text("", encoding="utf-8")
        return log_dir

    @mock.patch("scripts.ops_snapshot._safe_run")
    def test_ops_snapshot_returns_ok_for_valid_fixture(self, safe_run_mock: mock.Mock):
        safe_run_mock.return_value = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
        with tempfile.TemporaryDirectory() as td:
            log_dir = self._write_fixture(Path(td))
            out = run_snapshot(
                log_dir=log_dir,
                run_id="rid-1",
                compose_project_name="",
                min_status_rows=2,
                max_status_age_sec=3153600000.0,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(str(out.get("run_id")), "rid-1")
        self.assertIn("financial_summary", out)

    @mock.patch("scripts.ops_snapshot._safe_run")
    def test_ops_snapshot_fails_without_manifest_and_status(self, safe_run_mock: mock.Mock):
        safe_run_mock.return_value = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            out = run_snapshot(
                log_dir=log_dir,
                run_id="",
                compose_project_name="",
                min_status_rows=2,
                max_status_age_sec=3153600000.0,
            )
        self.assertFalse(out["ok"])
        self.assertIn("integrity", out)

    @mock.patch("scripts.ops_snapshot.run_run_integrity_audit")
    @mock.patch("scripts.ops_snapshot.build_report")
    @mock.patch("scripts.ops_snapshot._safe_run")
    def test_ops_snapshot_propagates_run_contract_and_phase(
        self,
        safe_run_mock: mock.Mock,
        build_report_mock: mock.Mock,
        integrity_mock: mock.Mock,
    ):
        safe_run_mock.return_value = {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
        build_report_mock.return_value = {
            "run_duration_minutes": 0.0,
            "execution_quality": {},
            "taker": {},
            "quote_uptime_ratio": 0.0,
            "error_rows": 0.0,
        }
        integrity_mock.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            contract_path = Path(td) / "run_contract_rid-1.json"
            contract_path.write_text("{}", encoding="utf-8")
            out = run_snapshot(
                log_dir=Path(td),
                run_id="rid-1",
                compose_project_name="",
                min_status_rows=1,
                max_status_age_sec=1.0,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
            )
        self.assertTrue(out["ok"])
        build_report_mock.assert_called_once()
        self.assertEqual(build_report_mock.call_args.kwargs["run_contract_path"], contract_path)
        self.assertEqual(build_report_mock.call_args.kwargs["session_phase"], "validate_postrun")
        integrity_mock.assert_called_once()
        self.assertEqual(integrity_mock.call_args.kwargs["run_contract_path"], contract_path)
        self.assertEqual(integrity_mock.call_args.kwargs["session_phase"], "validate_postrun")


if __name__ == "__main__":
    unittest.main()
