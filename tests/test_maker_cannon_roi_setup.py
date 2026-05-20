import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import maker_cannon_roi_setup  # noqa: E402


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class MakerCannonRoiSetupTests(unittest.TestCase):
    def test_analyze_bundle_scores_active_and_late_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = pathlib.Path(tmpdir)
            _write_jsonl(
                bundle / "maker_market_snapshot_rows.jsonl",
                [
                    {
                        "population_class": "candidate",
                        "secondary_oracle_confirmation": True,
                        "secondary_oracle_price_delta_abs": 0.5,
                        "stack_pressure_class": "below_soft_cap",
                        "cannon_depth_requirement_met": True,
                        "viability_class": "viable_only",
                        "sizing_conflict": False,
                        "fill_prob_margin": 0.1,
                        "same_target_side_submit_count_prior": 0,
                        "order_submit_id": "submit-1",
                    },
                    {
                        "population_class": "candidate",
                        "secondary_oracle_confirmation": False,
                        "secondary_oracle_price_delta_abs": 0.5,
                        "stack_pressure_class": "within_hard_cap",
                        "cannon_depth_requirement_met": False,
                        "viability_class": "viable_only",
                        "sizing_conflict": False,
                        "fill_prob_margin": -0.1,
                        "same_target_side_submit_count_prior": 3,
                        "order_submit_id": None,
                    },
                ],
            )
            _write_jsonl(
                bundle / "maker_cannon_late_window_probe_rows.jsonl",
                [
                    {
                        "population_class": "candidate",
                        "latent_market_truth_class": "evaluable",
                        "full_cannon_candidate": True,
                        "latent_market_full_cannon_candidate": True,
                        "cannon_window_class": "15_to_20s",
                        "secondary_oracle_confirmation": True,
                        "cannon_depth_requirement_met": True,
                    },
                    {
                        "population_class": "external_blocked",
                        "latent_market_truth_class": "evaluable",
                        "full_cannon_candidate": False,
                        "latent_market_full_cannon_candidate": True,
                        "cannon_window_class": "10_to_15s",
                        "secondary_oracle_confirmation": True,
                        "cannon_depth_requirement_met": True,
                        "latent_market_reject_reasons": ["insufficient_depth_multiple"],
                    },
                ],
            )

            report = maker_cannon_roi_setup.analyze_bundle(bundle)

            self.assertEqual(report["active_keeper_lane"]["candidate_row_count"], 2)
            self.assertEqual(report["active_keeper_lane"]["combined_profiles"]["grok_core"]["pass_count"], 1)
            self.assertEqual(
                report["active_keeper_lane"]["combined_profiles"]["grok_core_plus_bro_safety"]["pass_count"],
                1,
            )
            self.assertEqual(report["late_window_probe_lane"]["full_cannon_candidate_count"], 1)
            self.assertEqual(report["late_window_probe_lane"]["latent_market_full_cannon_candidate_count"], 2)
            self.assertIn("depth_requirement_1p5x", report["recommendation"]["promote_now_candidates"])
            self.assertIn("secondary_oracle_delta_abs_ge_0p20", report["recommendation"]["needs_formalization"])

    def test_main_writes_json_and_markdown_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = pathlib.Path(tmpdir)
            _write_jsonl(bundle / "maker_market_snapshot_rows.jsonl", [])
            _write_jsonl(bundle / "maker_cannon_late_window_probe_rows.jsonl", [])

            argv = [
                "maker_cannon_roi_setup.py",
                "--bundle-dir",
                str(bundle),
                "--output-stem",
                "roi_test",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = maker_cannon_roi_setup.main()

            self.assertEqual(rc, 0)
            self.assertTrue((bundle / "roi_test.json").exists())
            self.assertTrue((bundle / "roi_test.md").exists())


if __name__ == "__main__":
    unittest.main()
