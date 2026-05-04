import unittest

from prodesk.chainlink_feed import ChainlinkFeed


class ChainlinkFeedTests(unittest.TestCase):
    def test_message_parsing_updates_latest_and_queue(self):
        feed = ChainlinkFeed(
            {
                "enabled": True,
                "topic": "crypto_prices_chainlink",
                "symbols": ["btc/usd"],
            }
        )
        msg = {
            "topic": "crypto_prices_chainlink",
            "type": "price",
            "payload": {
                "symbol": "btc/usd",
                "value": "65000.5",
                "timestamp": 1735000000000,
            },
        }
        feed._handle_message_obj(msg, received_monotonic=100.0)
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.symbol, "btc/usd")
        self.assertEqual(latest.price, 65000.5)
        ticks = feed.pop_ticks()
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0].price, 65000.5)

    def test_ignores_wrong_topic_or_bad_payload(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink"})
        feed._handle_message_obj({"topic": "other", "payload": {"symbol": "btc/usd", "value": 1}}, 0.0)
        feed._handle_message_obj({"topic": "crypto_prices_chainlink", "payload": {"symbol": "", "value": 1}}, 0.0)
        feed._handle_message_obj({"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": None}}, 0.0)
        self.assertIsNone(feed.get_latest("btc/usd"))
        self.assertEqual(feed.pop_ticks(), [])

    def test_symbol_filter_applies(self):
        feed = ChainlinkFeed(
            {
                "enabled": True,
                "topic": "crypto_prices_chainlink",
                "symbols": ["btc/usd"],
            }
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "eth/usd", "value": "3000"}},
            0.0,
        )
        self.assertIsNone(feed.get_latest("eth/usd"))
        self.assertEqual(feed.pop_ticks(), [])

    def test_price_alias_is_supported(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "price": "65001.25"}},
            0.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65001.25)

    def test_nested_data_payload_is_supported(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {
                "topic": "crypto_prices_chainlink",
                "type": "price",
                "data": {"symbol": "btc/usd", "value": "65002.75"},
            },
            0.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65002.75)

    def test_symbol_normalization_accepts_compact_pairs(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "BTCUSD", "value": "65003.00"}},
            0.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.symbol, "btc/usd")
        self.assertEqual(latest.price, 65003.0)

    def test_payload_data_batch_without_symbol_uses_subscription_symbol(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {
                "topic": "crypto_prices_chainlink",
                "type": "price",
                "payload": {
                    "data": [
                        {"timestamp": 1735000000000, "value": "65004.00"},
                        {"timestamp": 1735000001000, "value": "65004.25"},
                    ]
                },
            },
            received_monotonic=100.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65004.25)
        ticks = feed.pop_ticks()
        self.assertEqual(len(ticks), 2)

    def test_payload_without_topic_is_accepted(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {
                "payload": {
                    "data": [
                        {"timestamp": 1735000000000, "value": "65005.50"},
                    ]
                },
            },
            received_monotonic=100.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65005.5)

    def test_topic_alias_crypto_prices_is_accepted(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {
                "topic": "crypto_prices",
                "type": "subscribe",
                "payload": {"symbol": "btc/usd", "value": "65006.0", "timestamp": 1735000000000},
            },
            received_monotonic=100.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65006.0)

    def test_symbol_normalization_accepts_usdt_quotes(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {
                "topic": "crypto_prices",
                "type": "update",
                "payload": {"symbol": "btcusdt", "value": "65007.5", "timestamp": 1735000000000},
            },
            received_monotonic=100.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.symbol, "btc/usd")
        self.assertEqual(latest.price, 65007.5)

    def test_subscribe_message_includes_crypto_prices_fallback_unfiltered(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        sub = feed._build_subscribe_message()
        self.assertEqual(sub.get("action"), "subscribe")
        subscriptions = sub.get("subscriptions", [])
        self.assertTrue(any(item.get("topic") == "crypto_prices_chainlink" for item in subscriptions))
        self.assertTrue(
            any(item.get("topic") == "crypto_prices" and item.get("filters") == "" for item in subscriptions)
        )

    def test_out_of_order_source_timestamp_is_dropped(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65010", "timestamp": 2000}},
            received_monotonic=200.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "64000", "timestamp": 1000}},
            received_monotonic=201.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65010.0)
        status = feed.status()
        self.assertEqual(int(status.get("disorder_dropped_ticks", 0)), 1)

    def test_duplicate_source_timestamp_is_counted_and_dropped(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        msg = {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65011", "timestamp": 2000}}
        feed._handle_message_obj(msg, received_monotonic=200.0)
        feed._handle_message_obj(msg, received_monotonic=201.0)
        status = feed.status()
        self.assertEqual(int(status.get("duplicate_ticks", 0)), 1)
        self.assertEqual(int(status.get("disorder_dropped_ticks", 0)), 0)

    def test_same_timestamp_revision_is_accepted_and_counted(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65012", "timestamp": 2000}},
            received_monotonic=200.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65012.5", "timestamp": 2000}},
            received_monotonic=201.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65012.5)
        status = feed.status()
        self.assertEqual(int(status.get("same_timestamp_revisions", 0)), 1)

    def test_missing_source_timestamp_dropped_after_timestamped_baseline(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65013", "timestamp": 2000}},
            received_monotonic=200.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65014"}},
            received_monotonic=201.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65013.0)
        status = feed.status()
        self.assertEqual(int(status.get("missing_source_ts_dropped_ticks", 0)), 1)

    def test_receive_monotonic_fallback_applies_when_source_timestamp_missing_on_both(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65020"}},
            received_monotonic=200.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65021"}},
            received_monotonic=201.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "64999"}},
            received_monotonic=199.0,
        )
        latest = feed.get_latest("btc/usd")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.price, 65021.0)
        status = feed.status()
        self.assertEqual(int(status.get("disorder_dropped_ticks", 0)), 1)

    def test_status_surfaces_ordering_policy_and_classification_counts(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65030", "timestamp": 2000}},
            received_monotonic=200.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65031", "timestamp": 2001}},
            received_monotonic=201.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65030", "timestamp": 2000}},
            received_monotonic=202.0,
        )
        status = feed.status()
        policy = status.get("ordering_policy")
        self.assertIsInstance(policy, dict)
        assert isinstance(policy, dict)
        self.assertEqual(policy.get("primary"), "source_timestamp")
        self.assertEqual(policy.get("fallback"), "receive_monotonic")
        self.assertEqual(policy.get("tolerance_ms"), 0)
        self.assertEqual(policy.get("tie_breaker"), "same_timestamp_price_revision")
        class_counts = status.get("ordering_classification_counts")
        self.assertIsInstance(class_counts, dict)
        assert isinstance(class_counts, dict)
        for key in ("ordered", "out_of_order", "duplicate", "revision", "missing_source_time"):
            self.assertIn(key, class_counts)
        self.assertGreaterEqual(int(class_counts.get("ordered", 0)), 1)
        self.assertGreaterEqual(int(class_counts.get("out_of_order", 0)), 1)
        self.assertIn("thread_alive", status)
        self.assertFalse(bool(status["thread_alive"]))

    def test_get_first_at_or_after_returns_earliest_authoritative_tick(self):
        feed = ChainlinkFeed({"enabled": True, "topic": "crypto_prices_chainlink", "symbols": ["btc/usd"]})
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "64999", "timestamp": 1000}},
            received_monotonic=100.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65001", "timestamp": 3000}},
            received_monotonic=101.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65002", "timestamp": 3000}},
            received_monotonic=102.0,
        )
        feed._handle_message_obj(
            {"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "65005", "timestamp": 5000}},
            received_monotonic=103.0,
        )
        tick = feed.get_first_at_or_after("btc/usd", "1970-01-01T00:41:40.500Z")
        self.assertIsNotNone(tick)
        assert tick is not None
        self.assertEqual(tick.source_ts_utc, "1970-01-01T00:50:00.000Z")
        self.assertEqual(tick.price, 65002.0)


if __name__ == "__main__":
    unittest.main()
