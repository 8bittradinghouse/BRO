import unittest

import analyze


class AnalyzeStatsTests(unittest.TestCase):
    def test_update_stats_tracks_spread_quote_and_trade_changes(self):
        stats = analyze.TokenStats()

        rec1 = {
            "ts_utc": "2026-01-01T00:00:00Z",
            "spread": 0.10,
            "best_bid_price": 0.40,
            "best_ask_price": 0.50,
            "last_trade_ts": "2026-01-01T00:00:00Z",
        }
        rec2 = {
            "ts_utc": "2026-01-01T00:00:01Z",
            "spread": 0.10,
            "best_bid_price": 0.40,
            "best_ask_price": 0.50,
            "last_trade_ts": "2026-01-01T00:00:00Z",
        }
        rec3 = {
            "ts_utc": "2026-01-01T00:00:02Z",
            "spread": 0.12,
            "best_bid_price": 0.42,
            "best_ask_price": 0.50,
            "last_trade_ts": "2026-01-01T00:00:02Z",
        }

        analyze.update_stats(stats, rec1)
        analyze.update_stats(stats, rec2)
        analyze.update_stats(stats, rec3)

        self.assertEqual(stats.records, 3)
        self.assertEqual(stats.spread_transitions, 2)
        self.assertEqual(stats.spread_changes, 1)
        self.assertEqual(stats.quote_transitions, 2)
        self.assertEqual(stats.quote_changes, 1)
        self.assertEqual(stats.bid_transitions, 2)
        self.assertEqual(stats.bid_changes, 1)
        self.assertEqual(stats.ask_transitions, 2)
        self.assertEqual(stats.ask_changes, 0)
        self.assertEqual(stats.trade_events, 2)


if __name__ == "__main__":
    unittest.main()
