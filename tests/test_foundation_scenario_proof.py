import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class FoundationScenarioProofTests(unittest.TestCase):
    def test_generates_required_realism_stress_scenarios(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            out_root = Path(td) / "foundation_scenarios"
            timestamp = "20990101T000000Z"
            env = dict(os.environ)
            existing_pythonpath = str(env.get("PYTHONPATH", "")).strip()
            env["PYTHONPATH"] = "." if not existing_pythonpath else f".:{existing_pythonpath}"
            subprocess.run(
                [
                    sys.executable,
                    "scripts/foundation_scenario_proof.py",
                    "--config",
                    "configs/profiles/paper_universal.yaml",
                    "--out-root",
                    str(out_root),
                    "--timestamp",
                    timestamp,
                ],
                cwd=repo_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            scenario_root = out_root / timestamp
            summary_path = scenario_root / f"foundation_scenario_proof_{timestamp}.json"
            self.assertTrue(summary_path.exists(), msg=f"missing summary: {summary_path}")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            expected_scenarios = {
                "clean_canonical",
                "reconnect_transport",
                "disorder_injected",
                "degraded_source_fallback_pressure",
                "thin_liquidity_partial_fill",
                "poor_truth_no_action_standdown",
            }
            self.assertEqual(set(summary.get("scenarios", {}).keys()), expected_scenarios)
            self.assertEqual(summary.get("scenario_expectation_mode"), "expected_outcome_matrix")
            self.assertEqual(
                set(summary.get("expected_success_scenarios", [])),
                {"clean_canonical", "reconnect_transport", "disorder_injected", "thin_liquidity_partial_fill"},
            )
            self.assertEqual(
                set(summary.get("expected_failure_scenarios", [])),
                {"degraded_source_fallback_pressure", "poor_truth_no_action_standdown"},
            )
            criteria = summary.get("proof_success_criteria", {})
            self.assertTrue(bool(criteria.get("required_scenarios_present", False)))
            self.assertTrue(bool(criteria.get("scenario_audit_expectations_matched", False)))
            self.assertTrue(bool(criteria.get("ingress_injection_checks_all_true", False)))
            self.assertTrue(bool(criteria.get("degraded_scenarios_are_expected_to_fail_some_audits", False)))
            expectation_match = summary.get("scenario_expectation_match_by_scenario", {})
            self.assertEqual(set(expectation_match.keys()), expected_scenarios)
            self.assertTrue(all(bool(v) for v in expectation_match.values()))

            for name in sorted(expected_scenarios):
                details = summary["scenarios"][name]
                paths = details.get("paths", {})
                audits = details.get("audits", {})
                audit_paths = details.get("audit_paths", {})
                self.assertIsInstance(details.get("scenario_fixture_type"), str)
                self.assertIsInstance(details.get("scenario_execution_purpose"), str)
                self.assertIsInstance(details.get("scenario_realism_interpretation"), str)
                self.assertTrue(bool(details.get("scenario_expectation_match")))
                self.assertEqual(details.get("scenario_expectation_mismatches"), [])
                self.assertTrue(Path(str(paths.get("run_contract_path", ""))).exists())
                self.assertTrue(Path(str(paths.get("events_path", ""))).exists())
                self.assertIn("paper_harness_audit", audits)
                self.assertIn("order_lifecycle_audit", audits)
                for key in (
                    "websocket_hardening_audit",
                    "time_discipline_audit",
                    "paper_harness_audit",
                    "order_lifecycle_audit",
                ):
                    self.assertTrue(Path(str(audit_paths.get(key, ""))).exists())

            clean_audits = summary["scenarios"]["clean_canonical"]["audits"]
            self.assertTrue(clean_audits["websocket_hardening_audit"]["ok"])
            self.assertTrue(clean_audits["time_discipline_audit"]["ok"])
            self.assertTrue(clean_audits["paper_harness_audit"]["ok"])
            self.assertTrue(clean_audits["order_lifecycle_audit"]["ok"])
            clean_meta = summary["scenarios"]["clean_canonical"]
            self.assertEqual(clean_meta["scenario_fixture_type"], "bounded_approximation_fixture")
            self.assertIn("not_venue_queue_realism", clean_meta["scenario_realism_interpretation"])

            poor_truth_audits = summary["scenarios"]["poor_truth_no_action_standdown"]["audits"]
            self.assertFalse(poor_truth_audits["paper_harness_audit"]["ok"])
            self.assertFalse(poor_truth_audits["websocket_hardening_audit"]["ok"])

            ingress_checks = summary.get("ingress_injection_proof", {}).get("checks", {})
            for key in (
                "normal_feed_ordered_positive",
                "out_of_order_classified",
                "duplicate_classified",
                "missing_source_classified",
                "revision_classified",
            ):
                self.assertTrue(bool(ingress_checks.get(key, False)), msg=f"ingress check failed: {key}")


if __name__ == "__main__":
    unittest.main()
