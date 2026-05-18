import unittest
import time

from prodesk.book_feed import MarketBookFeed
from prodesk.common import parse_ts
from prodesk.stream_worker_contracts import EVENT_FATAL


class MarketBookFeedTests(unittest.TestCase):
    def test_message_parsing_updates_snapshot(self):
        feed = MarketBookFeed(
            {
                "enabled": True,
                "stale_after_sec": 10.0,
            }
        )
        feed.update_token_ids(["t1"])
        msg = {
            "type": "book",
            "payload": {
                "token_id": "t1",
                "best_bid": "0.48",
                "best_ask": "0.52",
                "best_bid_size": "10",
                "best_ask_size": "8",
                "timestamp": 1735000000000,
            },
        }
        feed._handle_message_obj(msg, received_monotonic=time.monotonic())
        snapshot = feed.snapshot_books(max_age_sec=100.0)
        self.assertIn("t1", snapshot)
        top = snapshot["t1"]
        self.assertEqual(top.best_bid_price, 0.48)
        self.assertEqual(top.best_ask_price, 0.52)
        self.assertEqual(top.best_bid_size, 10.0)
        self.assertEqual(top.best_ask_size, 8.0)
        self.assertEqual(top.source, "legacy_fixture_top")

    def test_explicit_token_filter_blocks_unknown_tokens(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        feed._handle_message_obj({"token_id": "t2", "best_bid": "0.4", "best_ask": "0.6"}, received_monotonic=10.0)
        self.assertEqual(feed.snapshot_books(max_age_sec=100.0), {})

    def test_payload_timestamp_far_skewed_falls_back_to_receive_time(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        msg = {"token_id": "t1", "best_bid": "0.48", "best_ask": "0.52"}
        before = parse_ts("2026-01-01T00:00:00Z")
        feed._handle_message_obj(msg, received_monotonic=time.monotonic())
        snapshot = feed.snapshot_books(max_age_sec=100.0)
        self.assertIn("t1", snapshot)
        ts = parse_ts(snapshot["t1"].ts_utc)
        self.assertIsNotNone(ts)
        self.assertIsNotNone(before)
        self.assertGreater(ts, before)

    def test_reconnect_accounting_splits_startup_vs_steady(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])

        feed._record_reconnect(error="startup_disconnect")
        status_startup = feed.status()
        self.assertEqual(status_startup["reconnects"], 1)
        self.assertEqual(status_startup["reconnects_startup"], 1)
        self.assertEqual(status_startup["reconnects_steady"], 0)
        self.assertFalse(status_startup["primed"])

        feed._handle_message_obj(
            {"token_id": "t1", "best_bid": "0.40", "best_ask": "0.60"},
            received_monotonic=time.monotonic(),
        )
        feed._record_reconnect(error="steady_disconnect")
        status_steady = feed.status()
        self.assertEqual(status_steady["reconnects"], 2)
        self.assertEqual(status_steady["reconnects_startup"], 1)
        self.assertEqual(status_steady["reconnects_steady"], 1)
        self.assertTrue(status_steady["primed"])

    def test_token_universe_change_resets_primed_state(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        feed._handle_message_obj(
            {"token_id": "t1", "best_bid": "0.41", "best_ask": "0.61"},
            received_monotonic=time.monotonic(),
        )
        self.assertTrue(feed.status()["primed"])

        feed.update_token_ids(["t2"])
        self.assertFalse(feed.status()["primed"])

    def test_request_resubscribe_preserves_snapshot_and_resets_primed_state(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        feed._handle_message_obj(
            {"token_id": "t1", "best_bid": "0.41", "best_ask": "0.61"},
            received_monotonic=time.monotonic(),
        )
        self.assertTrue(feed.status()["primed"])
        self.assertIn("t1", feed.snapshot_books(max_age_sec=100.0))

        feed.request_resubscribe()

        self.assertFalse(feed.status()["primed"])
        self.assertIn("t1", feed.snapshot_books(max_age_sec=100.0))
        self.assertTrue(feed._resubscribe_event.is_set())  # pylint: disable=protected-access

    def test_transport_freshness_does_not_imply_data_freshness(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        with feed._lock:
            feed._connected = True
            feed._transport_connected = True
            feed._last_transport_monotonic = time.monotonic()
            feed._last_msg_monotonic = None
        status = feed.status()
        self.assertTrue(status["transport_connected"])
        self.assertFalse(status["connected"])
        self.assertIsNotNone(status["last_transport_msg_age_sec"])
        self.assertIsNone(status["last_msg_age_sec"])
        self.assertIn("thread_alive", status)
        self.assertFalse(bool(status["thread_alive"]))

    def test_worker_fatal_marks_failed_closed_status(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        feed._mark_worker_fatal(reason="queue_overflow", restart_exhausted=True)
        status = feed.status()
        self.assertEqual(status["subscription_state"], "failed_closed")
        self.assertFalse(status["worker_usable"])
        self.assertEqual(status["worker_fatal_reason"], "queue_overflow")
        self.assertTrue(status["worker_restart_exhausted"])

    def test_worker_fatal_event_raises_protocol_error(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        with self.assertRaisesRegex(Exception, "market worker fatal:decode_error"):
            feed._handle_worker_payload(
                {"event": EVENT_FATAL, "fatal_reason": "decode_error"},
                received_monotonic=time.monotonic(),
            )

    def test_configured_unusable_health_event_is_nonfatal_and_updates_status(self):
        feed = MarketBookFeed({"enabled": True, "stale_after_sec": 10.0})
        feed.update_token_ids(["t1"])
        feed._handle_worker_payload(
            {
                "contract": "bro.market_stream.event.v1",
                "event": "ack",
                "request_id": "probe",
                "provider": "rs-clob-client-v2",
                "contract_version": "v1",
                "subscription_state": "configured",
            },
            received_monotonic=time.monotonic(),
        )
        feed._handle_worker_payload(
            {
                "contract": "bro.market_stream.event.v1",
                "event": "health",
                "provider": "rs-clob-client-v2",
                "contract_version": "v1",
                "connected": False,
                "transport_connected": False,
                "usable": False,
                "fatal_reason": None,
                "restart_exhausted": False,
                "subscription_state": "configured",
                "last_error": None,
            },
            received_monotonic=time.monotonic(),
        )
        status = feed.status()
        self.assertEqual(status["subscription_state"], "configured")
        self.assertFalse(status["worker_usable"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["transport_connected"])


if __name__ == "__main__":
    unittest.main()
