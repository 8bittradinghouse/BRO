from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts.canonical_paper_session import (
    _compute_active_timing,
    _load_manifest_for_run_with_timeout,
    _observe_manifest_for_run_id,
    _stream_source_paths_for_window,
    build_parser,
    summarize_postrun_validation,
)


class CanonicalPaperSessionPostrunTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _write_minimal_reports(self, report_dir: Path, run_id: str, overall_exit_code: int) -> None:
        self._write_json(report_dir / "paper_harness_audit.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "paper_harness_audit_replay.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "websocket_hardening_audit.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "websocket_hardening_audit_replay.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "time_discipline_audit.json", {"ok": True, "run_id_filter": run_id})
        self._write_json(report_dir / "time_discipline_audit_replay.json", {"ok": True, "run_id_filter": run_id})
        self._write_json(report_dir / "guardian_profile_audit.json", {"ok": True})
        self._write_json(report_dir / "guardian_profile_audit_replay.json", {"ok": True})
        self._write_json(
            report_dir / "readiness_gate.json",
            {"highest_passing_stage": "paper", "blocking_stage": "pilot_live", "run_id_filter": run_id},
        )
        self._write_json(
            report_dir / "readiness_gate_replay.json",
            {"highest_passing_stage": "paper", "blocking_stage": "pilot_live", "run_id_filter": run_id},
        )
        self._write_json(
            report_dir / "nightly_soak_report.json",
            {"runtime_classification": {"classification": "VALID_ACTIVE", "promotion_eligible": True}},
        )
        self._write_json(
            report_dir / "nightly_soak_report_replay.json",
            {"runtime_classification": {"classification": "VALID_ACTIVE", "promotion_eligible": True}},
        )
        self._write_json(report_dir / "edge_truth_audit.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "edge_truth_audit_replay.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "order_lifecycle_audit.json", {"ok": True, "run_id_filter": run_id})
        self._write_json(report_dir / "order_lifecycle_audit_replay.json", {"ok": True, "run_id_filter": run_id})
        self._write_json(report_dir / "outcome_truth_audit.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "outcome_truth_audit_replay.json", {"ok": True, "run_id": run_id})
        self._write_json(report_dir / "soak_hardening_gate.json", {"ok": overall_exit_code == 0, "run_id": run_id})
        self._write_json(report_dir / "soak_hardening_gate_replay.json", {"ok": overall_exit_code == 0, "run_id": run_id})
        self._write_json(
            report_dir / "validation_summary.json",
            {
                "run_id": run_id,
                "overall_exit_code": int(overall_exit_code),
                "ok": bool(overall_exit_code == 0),
                "edge_truth_determinism_ok": True,
                "non_edge_determinism_ok": True,
                "validator_determinism_ok": True,
                "edge_truth_determinism": {
                    "determinism_ok": True,
                    "edge_records_sha256": "a" * 64,
                    "replay_edge_records_sha256": "a" * 64,
                    "replay_match": True,
                    "structural_consistency": {
                        "required_fields_sha256": "b" * 64,
                        "block_reason_taxonomy_sha256": "c" * 64,
                        "stage_policy_sha256": "d" * 64,
                        "audit_rule_set_sha256": "e" * 64,
                        "replay_required_fields_match": True,
                        "replay_block_reason_taxonomy_match": True,
                        "replay_stage_policy_match": True,
                        "replay_audit_rule_set_match": True,
                    },
                },
                "non_edge_determinism": {
                    "determinism_ok": True,
                    "validators": {
                        "paper_harness_audit": {"primary_sha256": "f" * 64, "replay_sha256": "f" * 64, "replay_match": True},
                        "websocket_hardening_audit": {"primary_sha256": "1" * 64, "replay_sha256": "1" * 64, "replay_match": True},
                        "time_discipline_audit": {"primary_sha256": "2" * 64, "replay_sha256": "2" * 64, "replay_match": True},
                        "guardian_profile_audit": {"primary_sha256": "3" * 64, "replay_sha256": "3" * 64, "replay_match": True},
                        "readiness_gate": {"primary_sha256": "4" * 64, "replay_sha256": "4" * 64, "replay_match": True},
                        "nightly_soak_report": {"primary_sha256": "5" * 64, "replay_sha256": "5" * 64, "replay_match": True},
                        "order_lifecycle_audit": {"primary_sha256": "6" * 64, "replay_sha256": "6" * 64, "replay_match": True},
                        "outcome_truth_audit": {"primary_sha256": "7" * 64, "replay_sha256": "7" * 64, "replay_match": True},
                        "soak_hardening_gate": {"primary_sha256": "8" * 64, "replay_sha256": "8" * 64, "replay_match": True},
                    },
                },
                "validator_exit_codes": {
                    "paper_harness_audit": 0,
                    "paper_harness_audit_replay": 0,
                    "websocket_hardening_audit": 0,
                    "websocket_hardening_audit_replay": 0,
                    "time_discipline_audit": 0,
                    "time_discipline_audit_replay": 0,
                    "guardian_profile_audit": 0,
                    "guardian_profile_audit_replay": 0,
                    "readiness_gate": 0,
                    "readiness_gate_replay": 0,
                    "nightly_soak_report": 0,
                    "nightly_soak_report_replay": 0,
                    "edge_truth_audit": 0,
                    "edge_truth_audit_replay": 0,
                    "order_lifecycle_audit": 0,
                    "order_lifecycle_audit_replay": 0,
                    "outcome_truth_audit": 0,
                    "outcome_truth_audit_replay": 0,
                    "soak_hardening_gate": int(overall_exit_code),
                    "soak_hardening_gate_replay": int(overall_exit_code),
                },
            },
        )

    def test_postrun_summary_pass_when_reports_complete_and_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td) / "reports" / "rid-pass"
            self._write_minimal_reports(report_dir, "rid-pass", overall_exit_code=0)
            summary = summarize_postrun_validation(run_id="rid-pass", report_dir=report_dir, script_exit_code=0)
            self.assertEqual(summary.get("status"), "pass")
            self.assertTrue(bool(summary.get("reports_complete")))
            self.assertFalse(bool(summary.get("execution_error")))
            self.assertTrue(bool(summary.get("gate_passed")))
            self.assertTrue(bool(summary.get("summary_exit_matches")))

    def test_postrun_summary_policy_failed_but_not_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td) / "reports" / "rid-policy"
            self._write_minimal_reports(report_dir, "rid-policy", overall_exit_code=2)
            summary = summarize_postrun_validation(run_id="rid-policy", report_dir=report_dir, script_exit_code=2)
            self.assertEqual(summary.get("status"), "policy_failed")
            self.assertTrue(bool(summary.get("reports_complete")))
            self.assertFalse(bool(summary.get("execution_error")))
            self.assertTrue(bool(summary.get("policy_failed")))
            self.assertTrue(bool(summary.get("summary_exit_matches")))

    def test_postrun_summary_marks_execution_error_when_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td) / "reports" / "rid-missing"
            self._write_minimal_reports(report_dir, "rid-missing", overall_exit_code=2)
            (report_dir / "soak_hardening_gate.json").unlink()
            summary = summarize_postrun_validation(run_id="rid-missing", report_dir=report_dir, script_exit_code=2)
            self.assertEqual(summary.get("status"), "execution_error")
            self.assertFalse(bool(summary.get("reports_complete")))
            self.assertTrue(bool(summary.get("execution_error")))
            self.assertIn("soak_hardening_gate", list(summary.get("missing_reports", [])))

    def test_postrun_summary_marks_execution_error_when_replay_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td) / "reports" / "rid-missing-replay"
            self._write_minimal_reports(report_dir, "rid-missing-replay", overall_exit_code=0)
            (report_dir / "edge_truth_audit_replay.json").unlink()
            summary = summarize_postrun_validation(
                run_id="rid-missing-replay",
                report_dir=report_dir,
                script_exit_code=0,
            )
            self.assertEqual(summary.get("status"), "execution_error")
            self.assertFalse(bool(summary.get("reports_complete")))
            self.assertTrue(bool(summary.get("execution_error")))
            self.assertIn("edge_truth_audit_replay", list(summary.get("missing_reports", [])))

    def test_postrun_summary_marks_execution_error_when_determinism_inconsistent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td) / "reports" / "rid-determinism"
            self._write_minimal_reports(report_dir, "rid-determinism", overall_exit_code=0)
            summary_path = report_dir / "validation_summary.json"
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            payload["validator_determinism_ok"] = False
            summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            summary = summarize_postrun_validation(
                run_id="rid-determinism",
                report_dir=report_dir,
                script_exit_code=0,
            )
            self.assertEqual(summary.get("status"), "execution_error")
            self.assertTrue(bool(summary.get("reports_complete")))
            self.assertTrue(bool(summary.get("execution_error")))
            self.assertFalse(bool(summary.get("determinism_consistent")))

    def test_observe_manifest_for_run_id_reads_expected_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            expected_manifest = log_dir / "run_manifest_run-expected.json"
            expected_manifest.write_text(json.dumps({"run_id": "run-expected"}) + "\n", encoding="utf-8")

            observed = _observe_manifest_for_run_id(
                log_dir=log_dir,
                run_id="run-expected",
                timeout_sec=0.0,
            )
            self.assertTrue(bool(observed.get("observed")))
            self.assertEqual(str(observed.get("manifest_path") or ""), str(expected_manifest.resolve()))
            self.assertEqual(str(observed.get("observed_run_id") or ""), "run-expected")

    def test_observe_manifest_for_run_id_is_non_authoritative_on_absence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            observed = _observe_manifest_for_run_id(
                log_dir=log_dir,
                run_id="missing",
                timeout_sec=0.1,
            )
            self.assertFalse(bool(observed.get("observed")))
            self.assertIn("manifest_path", observed)

    def test_observe_manifest_for_run_id_fails_on_run_id_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            mismatch_manifest = log_dir / "run_manifest_expected.json"
            mismatch_manifest.write_text(json.dumps({"run_id": "wrong-run"}) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                _observe_manifest_for_run_id(
                    log_dir=log_dir,
                    run_id="expected",
                    timeout_sec=0.2,
                )

    def test_load_manifest_for_run_with_timeout_reads_expected_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            manifest_path = log_dir / "run_manifest_run-load.json"
            payload = {"run_id": "run-load", "code_fingerprint_sha256": "a" * 64, "code_fingerprint_file_count": 5}
            manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            loaded = _load_manifest_for_run_with_timeout(log_dir=log_dir, run_id="run-load", timeout_sec=0.1)
            self.assertEqual(str(loaded.get("run_id") or ""), "run-load")
            self.assertEqual(str(loaded.get("code_fingerprint_sha256") or ""), "a" * 64)

    def test_load_manifest_for_run_with_timeout_fails_closed_on_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            with self.assertRaises(RuntimeError):
                _load_manifest_for_run_with_timeout(log_dir=log_dir, run_id="missing", timeout_sec=0.1)

    def test_stream_source_paths_for_window_includes_cross_day_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            (log_dir / "status_2026-03-19.jsonl").write_text("", encoding="utf-8")
            (log_dir / "status_2026-03-20.jsonl").write_text("", encoding="utf-8")
            start_dt = dt.datetime(2026, 3, 19, 23, 55, tzinfo=dt.timezone.utc)
            stop_dt = dt.datetime(2026, 3, 20, 0, 5, tzinfo=dt.timezone.utc)
            preferred = log_dir / "status_2026-03-20.jsonl"
            paths = _stream_source_paths_for_window(
                log_dir=log_dir,
                prefix="status",
                start_dt=start_dt,
                stop_dt=stop_dt,
                preferred_path=preferred,
            )
            resolved = {str(p.resolve()) for p in paths}
            self.assertIn(str((log_dir / "status_2026-03-19.jsonl").resolve()), resolved)
            self.assertIn(str((log_dir / "status_2026-03-20.jsonl").resolve()), resolved)

    def test_compute_active_timing_ignores_prestart_elapsed_when_not_runtime_capped(self) -> None:
        timing = _compute_active_timing(
            requested_active_sec=600.0,
            runtime_duration_sec=2700.0,
            cutoff_buffer_sec=30.0,
            pre_active_elapsed_sec=55.0,
        )
        self.assertFalse(bool(timing.get("runtime_capped")))
        self.assertEqual(float(timing.get("effective_active_sec")), 600.0)
        self.assertEqual(float(timing.get("active_wait_sec")), 600.0)
        self.assertEqual(str(timing.get("elapsed_source")), "active_phase")

    def test_compute_active_timing_subtracts_prestart_elapsed_when_runtime_capped(self) -> None:
        timing = _compute_active_timing(
            requested_active_sec=2700.0,
            runtime_duration_sec=2700.0,
            cutoff_buffer_sec=30.0,
            pre_active_elapsed_sec=40.0,
        )
        self.assertTrue(bool(timing.get("runtime_capped")))
        self.assertEqual(float(timing.get("effective_active_sec")), 2670.0)
        self.assertEqual(float(timing.get("active_wait_sec")), 2630.0)
        self.assertEqual(str(timing.get("elapsed_source")), "contract_start")

    def test_compute_active_timing_caps_wait_at_zero_when_prestart_elapsed_exceeds_effective(self) -> None:
        timing = _compute_active_timing(
            requested_active_sec=2700.0,
            runtime_duration_sec=2700.0,
            cutoff_buffer_sec=30.0,
            pre_active_elapsed_sec=3000.0,
        )
        self.assertTrue(bool(timing.get("runtime_capped")))
        self.assertEqual(float(timing.get("effective_active_sec")), 2670.0)
        self.assertEqual(float(timing.get("active_wait_sec")), 0.0)

    def test_build_parser_defaults_to_build_enabled(self) -> None:
        args = build_parser().parse_args([])
        self.assertTrue(bool(args.build_images))

    def test_build_parser_supports_no_build_fast_path(self) -> None:
        args = build_parser().parse_args(["--no-build"])
        self.assertFalse(bool(args.build_images))


if __name__ == "__main__":
    unittest.main()
