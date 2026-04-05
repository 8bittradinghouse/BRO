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
