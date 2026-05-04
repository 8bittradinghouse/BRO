import json
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import backfill_maker_cannon_probe  # noqa: E402


def _write_json(path: pathlib.Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class BackfillMakerCannonProbeTests(unittest.TestCase):
    def test_select_run_contexts_filters_by_missing_runtime_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            report_root = root / "reports"
            report_root.mkdir()
            log_dir = root / "logs"
            log_dir.mkdir()

            peak_run = report_root / "run-peak"
            peak_run.mkdir()
            _write_json(
                peak_run / "nightly_soak_report.json",
                {
                    "runtime_classification": {"classification": "VALID_ACTIVE"},
                    "duration_minutes": 12.0,
                },
            )
            _write_json(
                log_dir / "run_contract_run-peak.json",
                {
                    "run_id": "run-peak",
                    "start_ts": "2026-04-28T13:00:00Z",
                    "log_root": str(log_dir),
                },
            )

            transition_run = report_root / "run-transition"
            transition_run.mkdir()
            _write_json(
                transition_run / "nightly_soak_report.json",
                {
                    "runtime_classification": {"classification": "VALID_ACTIVE"},
                    "duration_minutes": 12.0,
                },
            )
            _write_json(
                transition_run / "maker_cannon_late_window_probe_summary.json",
                {"maker_cannon_probe_version": 2},
            )
            _write_json(
                log_dir / "run_contract_run-transition.json",
                {
                    "run_id": "run-transition",
                    "start_ts": "2026-04-28T09:00:00Z",
                    "log_root": str(log_dir),
                },
            )

            suppressed_run = report_root / "run-suppressed"
            suppressed_run.mkdir()
            _write_json(
                suppressed_run / "nightly_soak_report.json",
                {
                    "runtime_classification": {"classification": "SUPPRESSED"},
                    "duration_minutes": 12.0,
                },
            )
            _write_json(
                log_dir / "run_contract_run-suppressed.json",
                {
                    "run_id": "run-suppressed",
                    "start_ts": "2026-04-28T03:00:00Z",
                    "log_root": str(log_dir),
                },
            )

            contexts = backfill_maker_cannon_probe.discover_run_contexts(
                report_root=report_root,
                log_dir=log_dir,
            )
            selected = backfill_maker_cannon_probe.select_run_contexts(
                contexts,
                runtime_classification="VALID_ACTIVE",
                start_session_bucket="usa_europe_peak_heuristic",
                only_missing=True,
            )

            self.assertEqual([item["run_id"] for item in selected], ["run-peak"])
            self.assertEqual(selected[0]["run_start_session_bucket"], "usa_europe_peak_heuristic")
            self.assertFalse(selected[0]["has_probe_summary"])

    def test_build_recut_command_targets_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            run_dir = root / "reports" / "run-alpha"
            run_dir.mkdir(parents=True)
            context = {
                "run_dir": run_dir,
                "run_id": "run-alpha",
                "log_dir": root / "logs",
            }
            command = backfill_maker_cannon_probe.build_recut_command(
                context,
                python_executable="/usr/bin/python3",
            )

            self.assertEqual(command[0], "/usr/bin/python3")
            self.assertIn("--run-id", command)
            self.assertIn("run-alpha", command)
            self.assertIn(str(run_dir / "nightly_soak_report.json"), command)
            self.assertIn(str(run_dir / "nightly_soak_report.txt"), command)


if __name__ == "__main__":
    unittest.main()
