from __future__ import annotations

import unittest

from prodesk.sniper_tool import SniperCandidate, SniperTool, SniperToolConfig


class SniperToolTests(unittest.TestCase):
    def test_blocks_outside_final_window(self) -> None:
        tool = SniperTool(
            SniperToolConfig(
                enabled=True,
                final_window_enabled=True,
                final_window_sec=15.0,
                hard_min_target_usd=100.0,
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-outside",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=25.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.8,
                    max_feasible_target_usd=500.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertFalse(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "taker_outside_final_window")
        self.assertEqual(str(decision.timing_window_class or ""), "outside_window")

    def test_blocks_when_hard_min_unachievable(self) -> None:
        tool = SniperTool(
            SniperToolConfig(
                enabled=True,
                hard_min_target_usd=100.0,
                hard_min_enforcement="skip_if_unachievable",
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-hard-min",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.25,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.9,
                    max_feasible_target_usd=40.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertFalse(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "taker_hard_min_notional_unachievable")
        self.assertTrue(bool(decision.hard_min_unachievable))

    def test_non_15s_final_window_uses_generic_window_label(self) -> None:
        tool = SniperTool(
            SniperToolConfig(
                enabled=True,
                final_window_enabled=True,
                final_window_sec=30.0,
                hard_min_target_usd=1.0,
                dynamic_size_target_usd_cap=1.0,
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-final-window",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=20.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=1.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.8,
                    max_feasible_target_usd=500.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertEqual(str(decision.timing_window_class or ""), "final_window")
        self.assertEqual(str(decision.aggressiveness_level or ""), "final_window")

    def test_submits_with_dynamic_size_and_stage_aggressiveness(self) -> None:
        cfg = SniperToolConfig.from_mapping(
            {
                "enabled": True,
                "hard_min_target_usd": 100.0,
                "dynamic_size_enabled": True,
                "dynamic_size_edge_start_abs": 0.12,
                "dynamic_size_edge_full_abs": 0.22,
                "dynamic_size_target_usd_cap": 250.0,
                "stage_aggressiveness": {
                    "SNIPER_PRIMARY": {"size_mult": 1.15, "price_aggress_bps": 2.0},
                },
            }
        )
        tool = SniperTool(cfg)
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-submit",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertEqual(str(decision.side or ""), "BUY")
        self.assertEqual(str(decision.timing_window_class or ""), "final15")
        self.assertEqual(str(decision.aggressiveness_level or ""), "final15")
        self.assertAlmostEqual(float(decision.price or 0.0), 0.50 * (1.0 + 2.0 / 10000.0), places=9)
        self.assertGreater(float(decision.target_usd_resolved or 0.0), 250.0)
        self.assertTrue(bool(decision.hard_min_floor_applied))

    def test_budget_exhaustion_keeps_highest_conviction(self) -> None:
        tool = SniperTool(SniperToolConfig(enabled=True))
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-low",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.13,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.0,
                    max_feasible_target_usd=500.0,
                ),
                SniperCandidate(
                    token_id="tok-high",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=500.0,
                ),
            ],
            max_orders_per_cycle=1,
        )
        decisions = {row.token_id: row for row in result.decisions}
        self.assertTrue(bool(decisions["tok-high"].should_submit))
        self.assertFalse(bool(decisions["tok-low"].should_submit))
        self.assertEqual(str(decisions["tok-low"].block_reason or ""), "taker_order_budget_exhausted")

    def test_budget_exhaustion_prefers_quality_over_feasible_size(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "dynamic_preview_enabled": True,
                    "dynamic_size_enabled": False,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-low-size-high",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.14,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.0,
                    max_feasible_target_usd=1000.0,
                    predicted_dynamic_feasible_target_usd=300.0,
                ),
                SniperCandidate(
                    token_id="tok-high-size-lower",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                    predicted_dynamic_feasible_target_usd=120.0,
                ),
            ],
            max_orders_per_cycle=1,
        )
        decisions = {row.token_id: row for row in result.decisions}
        self.assertTrue(bool(decisions["tok-high-size-lower"].should_submit))
        self.assertFalse(bool(decisions["tok-low-size-high"].should_submit))
        self.assertEqual(str(decisions["tok-low-size-high"].block_reason or ""), "taker_order_budget_exhausted")

    def test_multi_oracle_confirmation_boosts_target_cap(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "dynamic_size_enabled": True,
                    "dynamic_size_target_usd_cap": 250.0,
                    "multi_oracle_boost_enabled": True,
                    "multi_oracle_edge_threshold_abs": 0.20,
                    "multi_oracle_target_usd_cap": 350.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-boost",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.24,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                    multi_oracle_confirmation=True,
                    multi_oracle_status="confirmed",
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertTrue(bool(decision.multi_oracle_confirmation))
        self.assertTrue(bool(decision.multi_oracle_boost_applied))
        self.assertEqual(str(decision.multi_oracle_status or ""), "confirmed")
        self.assertGreater(float(decision.target_usd_resolved or 0.0), 250.0)

    def test_multi_oracle_unknown_never_applies_boost(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "dynamic_size_enabled": True,
                    "dynamic_size_target_usd_cap": 250.0,
                    "multi_oracle_boost_enabled": True,
                    "multi_oracle_edge_threshold_abs": 0.20,
                    "multi_oracle_target_usd_cap": 350.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-unknown",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.24,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                    multi_oracle_confirmation=False,
                    multi_oracle_status="unknown",
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertFalse(bool(decision.multi_oracle_boost_applied))
        self.assertEqual(str(decision.multi_oracle_status or ""), "unknown")
        self.assertLessEqual(float(decision.target_usd_resolved or 0.0), 250.0)

    def test_stage_window_override_applies_to_sniper_primary(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "final_window_enabled": True,
                    "final_window_sec": 60.0,
                    "stage_final_window_sec_by_stage": {"SNIPER_PRIMARY": 20.0},
                    "hard_min_target_usd": 100.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-stage-window",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=25.0,
                    edge_value=0.30,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.9,
                    max_feasible_target_usd=500.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertFalse(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "taker_outside_final_window")
        self.assertEqual(str(decision.timing_window_class or ""), "outside_window")

    def test_numeric_boost_window_is_independent_of_timing_label(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "final_window_enabled": True,
                    "final_window_sec": 20.0,
                    "stage_final_window_sec_by_stage": {"SNIPER_PRIMARY": 20.0},
                    "dynamic_size_enabled": True,
                    "dynamic_size_target_usd_cap": 250.0,
                    "multi_oracle_boost_enabled": True,
                    "multi_oracle_boost_window_sec": 15.0,
                    "multi_oracle_edge_threshold_abs": 0.20,
                    "multi_oracle_target_usd_cap": 350.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-boost-window",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=18.0,
                    edge_value=0.24,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                    multi_oracle_confirmation=True,
                    multi_oracle_status="confirmed",
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertEqual(str(decision.timing_window_class or ""), "final_window")
        self.assertFalse(bool(decision.multi_oracle_boost_eligible))
        self.assertFalse(bool(decision.multi_oracle_boost_applied))
        self.assertLessEqual(float(decision.target_usd_resolved or 0.0), 250.0)

    def test_dynamic_preview_is_advisory_not_authoritative(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "hard_min_target_usd": 100.0,
                    "dynamic_preview_enabled": True,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-preview",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.9,
                    max_feasible_target_usd=1000.0,
                    predicted_dynamic_feasible_target_usd=50.0,
                    predicted_dynamic_reject_reason="global_exposure_cap",
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertTrue(bool(decision.submit_capable_static))
        self.assertFalse(bool(decision.submit_capable_dynamic_predicted))
        self.assertEqual(str(decision.preview_authority or ""), "advisory_read_only")
        self.assertEqual(str(decision.predicted_reject_reason or ""), "global_exposure_cap")

    def test_hard_floor_remains_non_negotiable_after_stage_mult(self) -> None:
        tool = SniperTool(
            SniperToolConfig.from_mapping(
                {
                    "enabled": True,
                    "hard_min_target_usd": 100.0,
                    "dynamic_size_enabled": False,
                    "stage_aggressiveness": {
                        "SNIPER_PRIMARY": {"size_mult": 0.5, "price_aggress_bps": 0.0},
                    },
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                SniperCandidate(
                    token_id="tok-floor",
                    stage="SNIPER_PRIMARY",
                    sec_to_expiry=8.0,
                    edge_value=0.25,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.6,
                    max_feasible_target_usd=1000.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertTrue(bool(decision.should_submit))
        self.assertGreaterEqual(float(decision.target_usd_resolved or 0.0), 100.0)
        self.assertFalse(bool(decision.hard_min_unachievable))


if __name__ == "__main__":
    unittest.main()
