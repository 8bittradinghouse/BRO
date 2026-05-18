from __future__ import annotations

import unittest

from prodesk.taker_competitiveness import (
    TakerCandidate,
    TakerCompetitivenessEngine,
    TakerCompetitivenessConfig,
    build_taker_competitiveness_policy,
)


class TakerCompetitivenessEngineTests(unittest.TestCase):
    def test_blocks_outside_final_window(self) -> None:
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig(
                enabled=True,
                final_window_enabled=True,
                final_window_sec=15.0,
                hard_min_target_usd=100.0,
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-outside",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig(
                enabled=True,
                hard_min_target_usd=100.0,
                hard_min_enforcement="skip_if_unachievable",
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-hard-min",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig(
                enabled=True,
                final_window_enabled=True,
                final_window_sec=30.0,
                hard_min_target_usd=1.0,
                dynamic_size_target_usd_cap=1.0,
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-final-window",
                    stage="EXTREME_ONLY",
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

    def test_stage_aggressiveness_payload_is_compatibility_only_for_canonical_engine(self) -> None:
        cfg = TakerCompetitivenessConfig.from_mapping(
            {
                "enabled": True,
                "hard_min_target_usd": 100.0,
                "dynamic_size_enabled": True,
                "dynamic_size_edge_start_abs": 0.12,
                "dynamic_size_edge_full_abs": 0.22,
                "dynamic_size_target_usd_cap": 250.0,
                "stage_aggressiveness": {
                    "EXTREME_ONLY": {"size_mult": 1.15, "price_aggress_bps": 2.0},
                },
            }
        )
        tool = TakerCompetitivenessEngine(cfg)
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-submit",
                    stage="EXTREME_ONLY",
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
        self.assertAlmostEqual(float(decision.price or 0.0), 0.50, places=9)
        self.assertAlmostEqual(float(decision.target_usd_resolved or 0.0), 250.0, places=9)
        self.assertTrue(bool(decision.hard_min_floor_applied))

    def test_budget_exhaustion_keeps_highest_conviction(self) -> None:
        tool = TakerCompetitivenessEngine(TakerCompetitivenessConfig(enabled=True))
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-low",
                    stage="EXTREME_ONLY",
                    sec_to_expiry=8.0,
                    edge_value=0.13,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.0,
                    max_feasible_target_usd=500.0,
                ),
                TakerCandidate(
                    token_id="tok-high",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "dynamic_preview_enabled": True,
                    "dynamic_size_enabled": False,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-low-size-high",
                    stage="EXTREME_ONLY",
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
                TakerCandidate(
                    token_id="tok-high-size-lower",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
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
                TakerCandidate(
                    token_id="tok-boost",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
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
                TakerCandidate(
                    token_id="tok-unknown",
                    stage="EXTREME_ONLY",
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

    def test_stage_window_override_is_ignored_for_canonical_taker(self) -> None:
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "final_window_enabled": True,
                    "final_window_sec": 60.0,
                    "stage_final_window_sec_by_stage": {"EXTREME_ONLY": 20.0},
                    "hard_min_target_usd": 100.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-stage-window",
                    stage="EXTREME_ONLY",
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
        self.assertTrue(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "")
        self.assertEqual(str(decision.timing_window_class or ""), "final_window")

    def test_numeric_boost_window_is_independent_of_timing_label(self) -> None:
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "final_window_enabled": True,
                    "final_window_sec": 20.0,
                    "stage_final_window_sec_by_stage": {"EXTREME_ONLY": 20.0},
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
                TakerCandidate(
                    token_id="tok-boost-window",
                    stage="EXTREME_ONLY",
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
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "hard_min_target_usd": 100.0,
                    "dynamic_preview_enabled": True,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-preview",
                    stage="EXTREME_ONLY",
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

    def test_hard_floor_remains_non_negotiable_with_compat_stage_payload(self) -> None:
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "hard_min_target_usd": 100.0,
                    "dynamic_size_enabled": False,
                    "stage_aggressiveness": {
                        "EXTREME_ONLY": {"size_mult": 0.5, "price_aggress_bps": 0.0},
                    },
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-floor",
                    stage="EXTREME_ONLY",
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

    def test_negative_edge_blocks_same_token_sell_under_buy_expected_winner_policy(self) -> None:
        tool = TakerCompetitivenessEngine(
            TakerCompetitivenessConfig.from_mapping(
                {
                    "enabled": True,
                    "normal_side_policy": "buy_expected_winner_only",
                    "hard_min_target_usd": 100.0,
                }
            )
        )
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-negative-edge",
                    stage="EXTREME_ONLY",
                    sec_to_expiry=8.0,
                    edge_value=-0.24,
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
        self.assertFalse(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "normal_taker_same_token_sell_forbidden")
        self.assertEqual(str(decision.side or ""), "SELL")
        self.assertEqual(str(decision.normal_taker_side_class or ""), "same_token_sell_blocked")
        self.assertEqual(str(decision.normal_side_policy or ""), "buy_expected_winner_only")

    def test_canonical_engine_ignores_stage_window_and_aggressive_overlays(self) -> None:
        cfg = build_taker_competitiveness_policy(
            {
                "enabled": True,
                "final_window_enabled": True,
                "final_window_sec": 7.0,
                "stage_final_window_sec_by_stage": {"EXTREME_ONLY": 20.0},
                "aggressive_window_enabled": True,
                "aggressive_window_sec": 3.0,
                "hard_min_target_usd": 1.0,
                "dynamic_size_target_usd_cap": 1.0,
            }
        )
        tool = TakerCompetitivenessEngine(cfg)
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-canonical-window",
                    stage="EXTREME_ONLY",
                    sec_to_expiry=10.0,
                    edge_value=0.25,
                    required_min_edge=0.10,
                    base_target_usd=1.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.8,
                    max_feasible_target_usd=100.0,
                )
            ],
            max_orders_per_cycle=1,
        )
        decision = result.decisions[0]
        self.assertFalse(bool(decision.should_submit))
        self.assertEqual(str(decision.block_reason or ""), "taker_outside_final_window")
        self.assertEqual(str(decision.timing_window_class or ""), "outside_window")

    def test_canonical_engine_budget_ordering_ignores_token_score(self) -> None:
        cfg = build_taker_competitiveness_policy(
            {
                "enabled": True,
                "dynamic_size_enabled": True,
                "dynamic_preview_enabled": True,
                "edge_weight": 0.1,
                "latency_score_weight": 0.9,
                "hard_min_target_usd": 100.0,
                "dynamic_size_target_usd_cap": 220.0,
                "final_window_enabled": True,
                "final_window_sec": 7.0,
            }
        )
        tool = TakerCompetitivenessEngine(cfg)
        result = tool.evaluate_batch(
            candidates=[
                TakerCandidate(
                    token_id="tok-a",
                    stage="EXTREME_ONLY",
                    sec_to_expiry=5.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=0.0,
                    max_feasible_target_usd=1000.0,
                    predicted_dynamic_feasible_target_usd=120.0,
                ),
                TakerCandidate(
                    token_id="tok-b",
                    stage="EXTREME_ONLY",
                    sec_to_expiry=5.0,
                    edge_value=0.22,
                    required_min_edge=0.10,
                    base_target_usd=100.0,
                    top_best_bid_price=0.49,
                    top_best_ask_price=0.50,
                    token_score=1.0,
                    max_feasible_target_usd=1000.0,
                    predicted_dynamic_feasible_target_usd=400.0,
                ),
            ],
            max_orders_per_cycle=1,
        )
        decisions = {row.token_id: row for row in result.decisions}
        self.assertTrue(bool(decisions["tok-a"].should_submit))
        self.assertFalse(bool(decisions["tok-b"].should_submit))
        self.assertEqual(str(decisions["tok-b"].block_reason or ""), "taker_order_budget_exhausted")
        self.assertGreater(float(decisions["tok-b"].conviction_score), float(decisions["tok-a"].conviction_score))

    def test_strict_policy_builder_rejects_noncanonical_stage_overlays(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "stage_final_window_sec_by_stage is retired",
        ):
            build_taker_competitiveness_policy(
                {
                    "enabled": True,
                    "final_window_enabled": True,
                    "final_window_sec": 7.0,
                    "aggressive_window_sec": 7.0,
                    "multi_oracle_boost_window_sec": 7.0,
                    "stage_final_window_sec_by_stage": {"LEGACY_STAGE": 20.0},
                },
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
