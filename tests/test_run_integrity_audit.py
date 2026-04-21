import json
import tempfile
import unittest
from pathlib import Path

from prodesk.canonical_authority import CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS
from prodesk.run_contract import build_run_contract, write_run_contract
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

    def test_validate_active_open_contract_scopes_parse_checks_to_recent_tail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-active-open-contract-tail"
            status_file = log_dir / "status_2099-03-07.jsonl"
            events_file = log_dir / "events_2099-03-07.jsonl"
            manifest_path = log_dir / f"run_manifest_{run_id}.json"
            contract_path = log_dir / f"run_contract_{run_id}.json"

            manifest_payload = {
                "run_id": run_id,
                "manifest_schema_version": 2,
                "mode": "paper",
                "profile_name": "test-profile",
                "git_commit": "deadbeef",
                "config_fingerprint_sha256": "a" * 64,
                "status_path": str(status_file.resolve()),
                "events_path": str(events_file.resolve()),
                "start_ts": "2099-03-07T00:00:00.000Z",
            }
            manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id},
            ]
            status_file.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")

            event_lines = ['{"this_is":"invalid_json_line"\n']
            event_lines.extend(
                json.dumps({"ts_utc": f"2099-03-07T00:00:{(i % 60):02d}.500Z", "run_id": run_id, "event_type": "cycle"})
                + "\n"
                for i in range(50)
            )
            events_file.write_text("".join(event_lines), encoding="utf-8")

            contract_payload = build_run_contract(
                session_id="sid-1",
                run_id=run_id,
                phase="active",
                session_type="canonical",
                authority_level="observational",
                allowed_actions=CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS,
                manifest_path=manifest_path,
                log_root=log_dir,
                state_root=root / "state",
                start_ts="2099-03-07T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2099-03-07T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(status_file),
                events_path=str(events_file),
                errors_path="",
                git_commit="deadbeef",
                config_fingerprint_sha256="a" * 64,
                code_fingerprint_sha256="b" * 64,
                code_fingerprint_file_count=1,
            )
            write_run_contract(contract_path, contract_payload, allow_open=True)

            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                run_contract_path=contract_path,
                session_phase="validate_active",
            )

        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertFalse(any(x.startswith("events_json_parse_errors:") for x in report.get("warnings", [])))

    def test_validate_active_open_contract_uses_tail_scoped_fill_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-active-open-contract-fill-tail"
            status_file = log_dir / "status_2099-03-07.jsonl"
            events_file = log_dir / "events_2099-03-07.jsonl"
            manifest_path = log_dir / f"run_manifest_{run_id}.json"
            contract_path = log_dir / f"run_contract_{run_id}.json"

            manifest_payload = {
                "run_id": run_id,
                "manifest_schema_version": 2,
                "mode": "paper",
                "profile_name": "test-profile",
                "git_commit": "deadbeef",
                "config_fingerprint_sha256": "a" * 64,
                "status_path": str(status_file.resolve()),
                "events_path": str(events_file.resolve()),
                "start_ts": "2099-03-07T00:00:00.000Z",
            }
            manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

            status_rows = [
                {"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id, "counter.fills": 2},
                {"ts_utc": "2099-03-07T00:00:01.000Z", "run_id": run_id, "counter.fills": 2},
            ]
            status_file.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")

            event_rows = [
                {
                    "ts_utc": "2099-03-07T00:00:00.100Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-1",
                },
                {
                    "ts_utc": "2099-03-07T00:00:00.200Z",
                    "run_id": run_id,
                    "event_type": "fill",
                    "trade_id": "paper-trade-abcdef123456-2",
                },
            ]
            event_rows.extend(
                {"ts_utc": f"2099-03-07T00:00:{(i % 60):02d}.500Z", "run_id": run_id, "event_type": "cycle"}
                for i in range(100)
            )
            events_file.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            contract_payload = build_run_contract(
                session_id="sid-2",
                run_id=run_id,
                phase="active",
                session_type="canonical",
                authority_level="observational",
                allowed_actions=CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS,
                manifest_path=manifest_path,
                log_root=log_dir,
                state_root=root / "state",
                start_ts="2099-03-07T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2099-03-07T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(status_file),
                events_path=str(events_file),
                errors_path="",
                git_commit="deadbeef",
                config_fingerprint_sha256="a" * 64,
                code_fingerprint_sha256="b" * 64,
                code_fingerprint_file_count=1,
            )
            write_run_contract(contract_path, contract_payload, allow_open=True)

            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                run_contract_path=contract_path,
                session_phase="validate_active",
            )

        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertTrue(
            any(x.startswith("fill_count_check_tail_scoped:events_tail=0:status_counter=2") for x in report.get("warnings", []))
        )
        self.assertFalse(any(x.startswith("fill_count_mismatch:") for x in report.get("warnings", [])))

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

    def test_status_stale_uses_run_contract_end_boundary_in_validate_postrun(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-postrun-stale-contract-boundary"
            start_ts = "2000-01-01T00:00:00.000Z"
            stop_ts = "2000-01-01T00:00:01.000Z"
            manifest_path = (log_dir / f"run_manifest_{run_id}.json").resolve()
            status_path = (log_dir / "status_2000-01-01.jsonl").resolve()
            events_path = (log_dir / "events_2000-01-01.jsonl").resolve()
            contract_path = (log_dir / f"run_contract_{run_id}.json").resolve()
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str(status_path),
                        "events_path": str(events_path),
                        "start_ts": start_ts,
                    }
                ),
                encoding="utf-8",
            )
            status_rows = [{"ts_utc": "2000-01-01T00:00:00.500Z", "run_id": run_id}]
            status_path.write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [{"ts_utc": "2000-01-01T00:00:00.700Z", "run_id": run_id, "event_type": "cycle"}]
            events_path.write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            contract = build_run_contract(
                session_id="sid-1",
                run_id=run_id,
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="observational",
                allowed_actions=list(CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS),
                manifest_path=manifest_path,
                log_root=log_dir,
                state_root=(root / "data").resolve(),
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
            )
            write_run_contract(contract_path, contract, allow_open=False)

            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=1.0,
                session_phase="validate_postrun",
                run_contract_path=contract_path,
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertFalse(any(x.startswith("latest_status_stale:") for x in report.get("findings", [])))
        self.assertFalse(any(x.startswith("latest_status_stale:") for x in report.get("warnings", [])))

    def test_json_parse_errors_are_warnings_in_validate_active(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-active-parse-errors"
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
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id}),
                        "{bad-status-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}),
                        "{bad-event-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                session_phase="validate_active",
            )
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertIn("status_json_parse_errors:1:phase=validate_active", report.get("warnings", []))
        self.assertIn("events_json_parse_errors:1:phase=validate_active", report.get("warnings", []))
        self.assertEqual(int(report.get("status_json_parse_error_count", -1)), 1)
        self.assertEqual(int(report.get("events_json_parse_error_count", -1)), 1)
        self.assertTrue(any(x.startswith("status_json_parse_error_paths:") for x in report.get("warnings", [])))
        self.assertTrue(any(x.startswith("events_json_parse_error_paths:") for x in report.get("warnings", [])))
        self.assertEqual(
            report.get("status_json_parse_error_paths"),
            {str((log_dir / "status_2099-03-07.jsonl").resolve()): 1},
        )
        self.assertEqual(
            report.get("events_json_parse_error_paths"),
            {str((log_dir / "events_2099-03-07.jsonl").resolve()): 1},
        )

    def test_json_parse_errors_fail_in_validate_postrun(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-postrun-parse-errors"
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
            (log_dir / "status_2099-03-07.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ts_utc": "2099-03-07T00:00:00.000Z", "run_id": run_id}),
                        "{bad-status-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "events_2099-03-07.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}),
                        "{bad-event-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0 * 24.0 * 365.0 * 100.0,
                session_phase="validate_postrun",
            )
        self.assertFalse(report["ok"])
        self.assertIn("status_json_parse_errors:1", report.get("findings", []))
        self.assertIn("events_json_parse_errors:1", report.get("findings", []))
        self.assertTrue(any(x.startswith("status_json_parse_error_paths:") for x in report.get("warnings", [])))
        self.assertTrue(any(x.startswith("events_json_parse_error_paths:") for x in report.get("warnings", [])))

    def test_unreadable_status_source_is_explicit_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-unreadable-status"
            unreadable_status = log_dir / "status_2099-03-07.jsonl"
            unreadable_status.mkdir(parents=True, exist_ok=True)
            events_path = log_dir / "events_2099-03-07.jsonl"
            events_path.write_text(
                json.dumps({"ts_utc": "2099-03-07T00:00:00.500Z", "run_id": run_id, "event_type": "cycle"}) + "\n",
                encoding="utf-8",
            )
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str(unreadable_status.resolve()),
                        "events_path": str(events_path.resolve()),
                        "start_ts": "2099-03-07T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            report = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                min_status_rows=1,
                status_tail_lines=10,
                event_tail_lines=10,
                max_status_age_sec=3600.0,
                session_phase="validate_postrun",
            )
        self.assertFalse(report["ok"])
        self.assertEqual(int(report.get("status_unreadable_count", -1)), 1)
        self.assertTrue(any(x.startswith("status_files_unreadable:1:") for x in report.get("findings", [])))


if __name__ == "__main__":
    unittest.main()
