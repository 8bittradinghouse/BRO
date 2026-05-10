import json
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "docs" / "FM_2A1_FUSION_CORE_PROFILING_TOOL_SPEC.json"


class FusionCoreProfilingToolSpecTests(unittest.TestCase):
    def test_spec_has_expected_identity_and_boundaries(self):
        payload = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

        self.assertEqual(payload.get("tool_id"), "FM-2A1")
        self.assertEqual(payload.get("tool_name"), "Fusion Core Profiling Tool")
        self.assertEqual(payload.get("tool_role"), "lathe")
        self.assertEqual(payload.get("status"), "implemented_v7_threshold_pressure_hardened")
        self.assertEqual(payload.get("implementation_pass_status", {}).get("profile_quality_promotion"), "implemented")
        self.assertEqual(payload.get("implementation_pass_status", {}).get("lane_maturity_and_contract_hardening"), "implemented")
        self.assertEqual(payload.get("implementation_pass_status", {}).get("metric_drift_diff_hardening"), "implemented")
        self.assertEqual(payload.get("implementation_pass_status", {}).get("calibration_and_promotion_audit_hardening"), "implemented")
        self.assertEqual(payload.get("implementation_pass_status", {}).get("threshold_pressure_matrix_hardening"), "implemented")

        scope = payload.get("scope", {})
        self.assertEqual(scope.get("lane_scope"), ["maker", "taker"])
        self.assertTrue(bool(scope.get("read_only")))
        self.assertFalse(bool(scope.get("allows_runtime_mutation")))
        self.assertFalse(bool(scope.get("allows_config_mutation")))
        self.assertFalse(bool(scope.get("allows_action_recommendation")))
        self.assertEqual(scope.get("lane_depth_policy", {}).get("maker"), "full_depth_at_or_above_coverage_threshold_else_mixed_depth_partial_deep_or_bounded_depth")
        self.assertEqual(scope.get("lane_depth_policy", {}).get("taker"), "bounded_depth_only")
        self.assertNotIn("sniper", scope.get("lane_depth_policy", {}))

    def test_spec_keeps_fma_contract_surface_and_policy_math_out_of_scope(self):
        payload = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

        decoupling = payload.get("decoupling_contract", {})
        self.assertTrue(decoupling.get("hard_decoupled_from_fma"))
        self.assertFalse(decoupling.get("shares_business_logic_with_fma"))

        upstream = payload.get("upstream_contract", {})
        self.assertEqual(upstream.get("preferred_manifest"), "fma_bundle_manifest.json")
        self.assertTrue(upstream.get("supports_legacy_bundle_derivation"))
        self.assertIn("run_index.jsonl", upstream.get("required_inputs", []))

        in_scope = set(payload.get("math_families_in_scope", []))
        out_of_scope = set(payload.get("math_families_out_of_scope", []))
        self.assertIn("expected_value_conditional_expectation", in_scope)
        self.assertIn("stochastic_survival_hazard", in_scope)
        self.assertIn("microstructure_inventory_cost", in_scope)
        self.assertIn("bayesian_log_odds_updating", out_of_scope)
        self.assertIn("optimization_control", out_of_scope)
        self.assertIn("risk_sizing_utility", out_of_scope)
        self.assertIn("fusion_core_calibration_audit.json", payload.get("planned_outputs", []))
        self.assertIn("fusion_core_threshold_pressure_matrix.json", payload.get("planned_outputs", []))
        self.assertIn("calibration_audit", payload.get("support_tooling", []))
        self.assertIn("threshold_pressure_matrix", payload.get("support_tooling", []))
        self.assertIn("bounded_lane_grade_cap", payload.get("profile_quality_guards", []))
        self.assertIn("weak_strength_signal_downgrade", payload.get("profile_quality_guards", []))
        self.assertIn("partial_deep_coverage_truth", payload.get("profile_quality_guards", []))
        self.assertIn("headline_metric_drift_detection", payload.get("profile_quality_guards", []))
        self.assertIn("profile_promotion_readiness_surface", payload.get("profile_quality_guards", []))
        self.assertIn("threshold_policy_visibility", payload.get("quality_gates", []))
        self.assertIn("multi_policy_grade_projection", payload.get("profile_quality_guards", []))
        self.assertIn("threshold_sensitivity_visibility", payload.get("quality_gates", []))


if __name__ == "__main__":
    unittest.main()
