from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

from prodesk.pyth_feed import PythFeed


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PythFeedTests(unittest.TestCase):
    def test_refresh_uses_user_agent_and_parses_success(self):
        feed = PythFeed(
            {
                "enabled": True,
                "rest_url": "https://hermes.pyth.network/v2/updates/price/latest?ids[]={feed_id}&parsed=true",
                "feed_id": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
                "symbol": "BTC/USD",
                "user_agent": "BRO/Test",
                "request_timeout_sec": 1.0,
                "poll_interval_sec": 0.0,
                "max_tick_age_sec": 60.0,
            }
        )
        payload = [
            {
                "price": {"price": "6500012", "expo": -2},
                "publish_time": 1_700_000_000,
            }
        ]
        response = _FakeResponse(json.dumps(payload).encode("utf-8"), status=200)

        with mock.patch("urllib.request.urlopen", return_value=response) as mocked_urlopen:
            feed.refresh()

        self.assertEqual(mocked_urlopen.call_count, 1)
        req = mocked_urlopen.call_args.args[0]
        self.assertEqual(req.get_header("User-agent"), "BRO/Test")
        self.assertTrue(req.full_url.startswith("https://hermes.pyth.network/v2/updates/price/latest?ids[]="))

        status = feed.status()
        self.assertEqual(status.get("operational_state"), "connected")
        self.assertEqual(int(status.get("last_http_status") or 0), 200)
        self.assertEqual(int(status.get("errors") or 0), 0)
        latest = feed.get_latest("BTC/USD")
        self.assertIsNotNone(latest)
        self.assertGreater(float(latest.price), 0.0)

    def test_refresh_http_403_is_explicit_fail_closed_unavailable_state(self):
        feed = PythFeed(
            {
                "enabled": True,
                "rest_url": "https://hermes.pyth.network/v2/updates/price/latest?ids[]={feed_id}&parsed=true",
                "feed_id": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
                "symbol": "BTC/USD",
                "request_timeout_sec": 1.0,
                "poll_interval_sec": 0.0,
                "max_tick_age_sec": 60.0,
            }
        )
        err = urllib.error.HTTPError(
            url="https://hermes.pyth.network/v2/updates/price/latest",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

        with mock.patch("urllib.request.urlopen", side_effect=err):
            feed.refresh()

        status = feed.status()
        self.assertFalse(bool(status.get("connected", False)))
        self.assertEqual(status.get("operational_state"), "unavailable_http_403")
        self.assertEqual(int(status.get("last_http_status") or 0), 403)
        self.assertGreaterEqual(int(status.get("errors") or 0), 1)
        self.assertIsNone(feed.get_latest("BTC/USD"))


if __name__ == "__main__":
    unittest.main()
