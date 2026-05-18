import json
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAP_PATH = REPO_ROOT / "docs" / "SOLAR_SLUG_MAKER_TRUTH_SEMANTICS_MAP.json"


class MakerTruthSemanticsMapTests(unittest.TestCase):
    def test_map_is_valid_json_with_expected_top_level_shape(self):
        payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))

        self.assertEqual(payload.get("schema_name"), "solar_slug_maker_truth_semantics_map")
        self.assertEqual(payload.get("schema_version"), 1)
        self.assertEqual(payload.get("weapon_alias"), "Solar Slug Maker Cannon")
        self.assertEqual(payload.get("canonical_lane"), "maker")
        self.assertFalse(bool(payload.get("authority_boundary", {}).get("allows_strategy_tuning")))
        self.assertFalse(bool(payload.get("authority_boundary", {}).get("allows_runtime_behavior_change")))

    def test_map_contains_required_truth_populations_and_pathways(self):
        payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))

        truth_populations = set(payload.get("truth_populations", {}).keys())
        self.assertTrue(
            {
                "decision_cycle_truth",
                "submit_truth",
                "filled_order_truth",
                "fill_event_truth",
                "complete_outcome_truth",
                "campaign_ledger_truth",
            }.issubset(truth_populations)
        )

        pathways = payload.get("pathways", [])
        pathway_ids = [pathway.get("id") for pathway in pathways]
        self.assertEqual(
            pathway_ids,
            [
                "market_reference_substrate",
                "decision_cycle_edge_truth",
                "stage_and_lane_eligibility",
                "risk_sizing_quote_quality_friction",
                "submit_ownership_lifecycle",
                "fill_event_execution_surface",
                "outcome_truth_forensic_surface",
                "harvest_compression_shop_tooling",
            ],
        )
        for pathway in pathways:
            self.assertTrue(bool(pathway.get("primary_artifacts")))
            self.assertTrue(bool(pathway.get("high_value_fields")))
            self.assertTrue(bool(pathway.get("plain_english")))


if __name__ == "__main__":
    unittest.main()
