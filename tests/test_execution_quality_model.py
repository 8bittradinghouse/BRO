from __future__ import annotations

import unittest

from prodesk.execution_quality import ExecutionQualityModel
from prodesk.models import BookTop, OrderIntent


class ExecutionQualityModelTests(unittest.TestCase):
    def test_inside_spread_prices_are_not_equivalent(self):
        model = ExecutionQualityModel({})

        sell_top = BookTop(
            token_id="sell-token",
            ts_utc="2026-04-29T00:00:00Z",
            source="synthetic",
            best_bid_price=0.60,
            best_bid_size=100.0,
            best_ask_price=0.62,
            best_ask_size=120.0,
        )
        sell_deep = model.assess_quote(
            intent=OrderIntent(token_id="sell-token", side="SELL", price=0.619, size=10.0),
            top=sell_top,
        )
        sell_near = model.assess_quote(
            intent=OrderIntent(token_id="sell-token", side="SELL", price=0.601, size=10.0),
            top=sell_top,
        )
        self.assertGreater(sell_near.expected_fill_prob, sell_deep.expected_fill_prob)

        buy_top = BookTop(
            token_id="buy-token",
            ts_utc="2026-04-29T00:00:00Z",
            source="synthetic",
            best_bid_price=0.40,
            best_bid_size=140.0,
            best_ask_price=0.42,
            best_ask_size=110.0,
        )
        buy_deep = model.assess_quote(
            intent=OrderIntent(token_id="buy-token", side="BUY", price=0.401, size=10.0),
            top=buy_top,
        )
        buy_near = model.assess_quote(
            intent=OrderIntent(token_id="buy-token", side="BUY", price=0.419, size=10.0),
            top=buy_top,
        )
        self.assertGreater(buy_near.expected_fill_prob, buy_deep.expected_fill_prob)

    def test_crossing_post_only_quote_is_penalized_vs_clamped_passive_quote(self):
        model = ExecutionQualityModel({})
        top = BookTop(
            token_id="maker-sell",
            ts_utc="2026-04-29T00:00:00Z",
            source="synthetic",
            best_bid_price=0.96,
            best_bid_size=440.71,
            best_ask_price=0.97,
            best_ask_size=440.71,
        )
        certified = model.assess_quote(
            intent=OrderIntent(
                token_id="maker-sell",
                side="SELL",
                price=0.874,
                size=259.07,
                post_only=True,
            ),
            top=top,
        )
        clamped = model.assess_quote(
            intent=OrderIntent(
                token_id="maker-sell",
                side="SELL",
                price=0.961,
                size=259.07,
                post_only=True,
            ),
            top=top,
        )
        self.assertGreater(clamped.expected_fill_prob, certified.expected_fill_prob)
        self.assertGreater(clamped.expected_quality_score, certified.expected_quality_score)

    def test_large_order_vs_tiny_visible_depth_gets_penalized(self):
        model = ExecutionQualityModel({})
        top = BookTop(
            token_id="depth-test",
            ts_utc="2026-04-29T00:00:00Z",
            source="synthetic",
            best_bid_price=0.41,
            best_bid_size=4.19,
            best_ask_price=0.42,
            best_ask_size=4.19,
        )
        small = model.assess_quote(
            intent=OrderIntent(token_id="depth-test", side="SELL", price=0.411, size=4.0),
            top=top,
        )
        huge = model.assess_quote(
            intent=OrderIntent(token_id="depth-test", side="SELL", price=0.411, size=602.41),
            top=top,
        )
        self.assertGreater(small.expected_fill_prob, huge.expected_fill_prob)
        self.assertLess(huge.expected_fill_prob, 0.05)


if __name__ == "__main__":
    unittest.main()
