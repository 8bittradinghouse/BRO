from __future__ import annotations

import unittest

from prodesk.edge_truth_contract import (
    EDGE_ACTION_MAKER,
    EDGE_ACTION_NONE,
    EDGE_ACTION_TAKER,
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPE_TAKER,
    EdgeInputSnapshot,
    compute_edge_value,
    is_canonical_block_reason,
    stage_allows_action,
    validate_edge_inputs,
)


class EdgeTruthContractTests(unittest.TestCase):
    def test_validate_edge_inputs_passes_with_complete_snapshot(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=0.55,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=0.3,
                latency_state="armed",
                stage="MAKER_TAKER_SELECTIVE",
                evaluation_scope=EDGE_EVAL_SCOPE_MAKER,
            ),
            oracle_max_tick_age_sec=1.5,
            require_latency_state=True,
        )
        self.assertTrue(bool(out.valid))
        self.assertEqual(str(out.reason_code), "ok")

    def test_validate_edge_inputs_fails_closed_on_missing_fair_probability(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=None,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=0.3,
                latency_state="armed",
                stage="MAKER_TAKER_SELECTIVE",
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
            ),
            oracle_max_tick_age_sec=1.5,
            require_latency_state=True,
        )
        self.assertFalse(bool(out.valid))
        self.assertEqual(str(out.reason_code), "fair_probability_missing")

    def test_validate_edge_inputs_fails_closed_on_stale_oracle(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=0.55,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=3.0,
                latency_state="armed",
                stage="MAKER_TAKER_SELECTIVE",
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
            ),
            oracle_max_tick_age_sec=1.5,
            require_latency_state=True,
        )
        self.assertFalse(bool(out.valid))
        self.assertEqual(str(out.reason_code), "oracle_tick_stale")

    def test_validate_edge_inputs_fails_when_latency_required_but_missing(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=0.55,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=0.2,
                latency_state=None,
                stage="SNIPER_PRIMARY",
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
            ),
            oracle_max_tick_age_sec=1.5,
            require_latency_state=True,
        )
        self.assertFalse(bool(out.valid))
        self.assertEqual(str(out.reason_code), "latency_state_missing")

    def test_stage_allows_action_follows_canonical_policy(self) -> None:
        self.assertTrue(stage_allows_action("MAKER_TAKER_SELECTIVE", EDGE_ACTION_MAKER))
        self.assertTrue(stage_allows_action("MAKER_TAKER_SELECTIVE", EDGE_ACTION_TAKER))
        self.assertTrue(stage_allows_action("OBSERVE", EDGE_ACTION_NONE))
        self.assertFalse(stage_allows_action("OBSERVE", EDGE_ACTION_MAKER))
        self.assertFalse(stage_allows_action("SNIPER_PRIMARY", EDGE_ACTION_MAKER))
        self.assertTrue(stage_allows_action("SNIPER_PRIMARY", EDGE_ACTION_TAKER))

    def test_compute_edge_value_returns_none_when_missing_inputs(self) -> None:
        self.assertIsNone(compute_edge_value(fair_probability=None, market_probability=0.5))
        self.assertIsNone(compute_edge_value(fair_probability=0.6, market_probability=None))
        self.assertAlmostEqual(
            float(compute_edge_value(fair_probability=0.61, market_probability=0.54) or 0.0),
            0.07,
            places=9,
        )

    def test_block_reason_taxonomy_is_canonical(self) -> None:
        self.assertTrue(is_canonical_block_reason("edge_below_min"))
        self.assertTrue(is_canonical_block_reason("fair_probability_missing"))
        self.assertTrue(is_canonical_block_reason("maker_requires_ws_book_source"))
        self.assertTrue(is_canonical_block_reason("taker_requires_ws_book_source"))
        self.assertTrue(is_canonical_block_reason("taker_outside_final_window"))
        self.assertTrue(is_canonical_block_reason("taker_hard_min_notional_unachievable"))
        self.assertTrue(is_canonical_block_reason("taker_dynamic_size_capped_by_risk"))
        self.assertTrue(is_canonical_block_reason("reduce_only_recovery_size_cap_below_min_order_size"))
        self.assertTrue(is_canonical_block_reason("reduce_only_recovery_no_reducing_side"))
        self.assertTrue(is_canonical_block_reason("reduce_only_recovery_size_cap_unavailable"))
        self.assertTrue(is_canonical_block_reason("reduce_only_recovery_touch_price_unavailable"))
        self.assertFalse(is_canonical_block_reason("unspecified_no_action"))
        self.assertFalse(is_canonical_block_reason("some_random_reason"))


if __name__ == "__main__":
    unittest.main()
