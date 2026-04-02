import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_integrity_audit import run_audit


class RunIntegrityAuditTests(unittest.TestCase):
    def _write_fixture(self, root: Path, run_id: str = "rid-1") -> Path:
        log_dir = root / "logs_exec"
        log_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id": run_id,
            "manifest_schema_version": 2,
            "profile_name": "test-profile",
            "git_commit": "deadbeef",
            "config_fingerprint_sha256": "a" * 64,
            "status_path": str((log_dir / "status_2026-03-07.jsonl").resolve()),
            "events_path": str((log_dir / "events_2026-03-07.jsonl").resolve()),
            "start_ts": "2026-03-07T00:00:00.000Z",
        }
        (log_dir / f"run_manifest_{run_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
        status_rows = [
            {"ts_utc": "2026-03-07T00:00:00.000Z", "run_id": run_id},
            {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id},
        ]
        (log_dir / "status_2026-03-07.jsonl").write_text(
            "\n".join(json.dumps(r) for r in status_rows) + "\n",
            encoding="utf-8",
        )
        event_rows = [{"ts_utc": "2026-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}]
        (log_dir / "events_2026-03-07.jsonl").write_text(
            "\n".join(json.dumps(r) for r in event_rows) + "\n",
            encoding="utf-8",
        )
        return log_dir

    def test_run_integrity_passes_valid_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = self._write_fixture(Path(td))
            report = run_audit(
                log_dir=log_dir,
                run_id="rid-1",
                min_status_rows=2,
                status_tail_lines=100,
                event_tail_lines=100,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))

    def test_run_integrity_fails_when_runtime_identity_present_but_docker_hash_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-runtime-identity-missing-docker-hash"
            manifest = {
                "run_id": run_id,
                "manifest_schema_version": 2,
                "profile_name": "test-profile",
                "git_commit": "deadbeef",
                "config_fingerprint_sha256": "a" * 64,
                "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                "start_ts": "2099-03-07T00:00:00.000Z",
                "runtime_identity": {
                    "effective_config_sha256": "a" * 64,
                    "dependency_lock_sha256": "d" * 64,
                    "docker_image_hash": "",
                },
            }
            (log_dir / f"run_manifest_{run_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [{"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=100,
                event_tail_lines=100,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertFalse(report["ok"])
        self.assertIn(
            "run_manifest_runtime_identity_missing_field:docker_image_hash",
            report.get("findings", []),
        )

    def test_run_integrity_fails_missing_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "run_manifest_rid-x.json").write_text('{"run_id":"rid-x"}', encoding="utf-8")
            report = run_audit(
                log_dir=log_dir,
                run_id="rid-x",
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=60.0,
            )
        self.assertFalse(report["ok"])
        self.assertIn("status_files_missing", report["findings"])

    def test_run_integrity_fails_non_monotonic_counters(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-y"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.orders_submitted": 5},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.orders_submitted": 4},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            (log_dir / "events_2099-03-07.jsonl").write_text(
                '{"ts_utc":"2099-03-07T00:00:00.500Z","run_id":"rid-y","event_type":"cycle"}\n',
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertFalse(report["ok"])
        self.assertTrue(any("status_counter_non_monotonic:counter.orders_submitted" in x for x in report["findings"]))
        self.assertIn("BRO-1204", report["error_codes"])

    def test_run_integrity_fails_paper_legacy_trade_id_format(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-paper"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "mode": "paper",
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 1},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 1},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.500Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-1",
                }
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertFalse(report["ok"])
        self.assertIn("paper_trade_id_format_invalid:1", report["findings"])

    def test_run_integrity_fill_counter_check_uses_full_fill_scan_not_tail_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-tail"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "mode": "paper",
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 1},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 1},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.100Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-1",
                }
            ]
            event_rows.extend(
                {"ts_utc": f"2099-03-07T00:00:{(i % 60):02d}.500Z", "run_id": run_id, "event_type": "cycle"}
                for i in range(500)
            )
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertNotIn("fill_count_mismatch:events=0:status_counter=1", report.get("findings", []))

    def test_fill_count_mismatch_is_warning_in_validate_active(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-active-mismatch"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "mode": "paper",
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 1},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 1},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.500Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-1",
                },
                {
                    "ts_utc": "2099-03-07T00:00:00.600Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-2",
                },
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                session_phase="validate_active",
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertFalse(any(x.startswith("fill_count_mismatch:") for x in report.get("findings", [])))
        self.assertTrue(
            any(x.startswith("fill_count_mismatch:events=2:status_counter=1:phase=validate_active") for x in report.get("warnings", []))
        )

    def test_fill_count_mismatch_fails_in_validate_postrun(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-postrun-mismatch"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "mode": "paper",
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 1},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 1},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.500Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-1",
                },
                {
                    "ts_utc": "2099-03-07T00:00:00.600Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-2",
                },
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                session_phase="validate_postrun",
            )
        self.assertFalse(report["ok"])
        self.assertIn("fill_count_mismatch:events=2:status_counter=1", report.get("findings", []))

    def test_fill_count_mismatch_is_warning_when_only_due_to_postrun_status_lag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-postrun-status-lag"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "mode": "paper",
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 1},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 1},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.500Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-1",
                },
                {
                    "ts_utc": "2099-03-07T00:00:01.500Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-2",
                },
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                session_phase="validate_postrun",
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertFalse(any(x.startswith("fill_count_mismatch:") for x in report.get("findings", [])))
        self.assertTrue(
            any(
                x.startswith(
                    "fill_count_mismatch:events=2:status_counter=1:postrun_status_lag:fills_after_latest_status=1"
                )
                for x in report.get("warnings", [])
            )
        )

    def test_cancel_all_lock_release_cannot_exceed_canceled_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-cancel-all-lock-mismatch"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"},
                {
                    "ts_utc": "2099-03-07T00:00:00.800Z",
                    "run_id": run_id,
                    "event_type": "cancel_all_on_exit",
                    "canceled_count": 0,
                    "released_lock_count": 2,
                },
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertFalse(report["ok"])
        self.assertIn("cancel_all_lock_release_exceeds_canceled:2>0", report.get("findings", []))

    def test_cancel_all_lock_release_invariant_passes_when_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-cancel-all-lock-ok"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2099-03-07.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-03-07.jsonl").resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id},
            ]
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"},
                {
                    "ts_utc": "2099-03-07T00:00:00.800Z",
                    "run_id": run_id,
                    "event_type": "cancel_all_on_exit",
                    "canceled_count": 2,
                    "released_lock_count": 2,
                },
            ]
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=2,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))

    def test_status_stale_fails_in_validate_active(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-active-stale"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2000-01-01.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2000-01-01.jsonl").resolve()),
                        "start_ts": "2000-01-01T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [{"ts_utc": "2000-01-01T00:00:00.000Z", "run_id": run_id}]
            (log_dir / "status_2000-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [{"ts_utc": "2000-01-01T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}]
            (log_dir / "events_2000-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=1.0,
                session_phase="validate_active",
            )
        self.assertFalse(report["ok"])
        self.assertTrue(any(x.startswith("latest_status_stale:") for x in report.get("findings", [])))

    def test_status_stale_is_warning_in_validate_postrun(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-postrun-stale"
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((log_dir / "status_2000-01-01.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2000-01-01.jsonl").resolve()),
                        "start_ts": "2000-01-01T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [{"ts_utc": "2000-01-01T00:00:00.000Z", "run_id": run_id}]
            (log_dir / "status_2000-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [{"ts_utc": "2000-01-01T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}]
            (log_dir / "events_2000-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=1.0,
                session_phase="validate_postrun",
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertFalse(any(x.startswith("latest_status_stale:") for x in report.get("findings", [])))
        self.assertTrue(
            any(
                x.startswith("latest_status_stale:") and x.endswith(":phase=validate_postrun")
                for x in report.get("warnings", [])
            )
        )


if __name__ == "__main__":
    unittest.main()
