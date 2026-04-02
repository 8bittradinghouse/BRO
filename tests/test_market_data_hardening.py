import unittest
from unittest import mock

from prodesk.market_data import _http_get_json


class MarketDataHardeningTests(unittest.TestCase):
    def test_retry_after_negative_value_is_clamped(self):
        session = mock.Mock()
        resp_429 = mock.Mock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "-10"}
        resp_429.raise_for_status = mock.Mock()

        resp_ok = mock.Mock()
        resp_ok.status_code = 200
        resp_ok.headers = {}
        resp_ok.raise_for_status = mock.Mock()
        resp_ok.json.return_value = {"bids": [], "asks": []}
        session.get.side_effect = [resp_429, resp_ok]

        with mock.patch("prodesk.market_data.time.sleep") as sleep_mock:
            payload = _http_get_json(session, "https://example.test/book", params={}, timeout_sec=1.0, max_retries=1)

        self.assertEqual(payload, {"bids": [], "asks": []})
        self.assertGreaterEqual(float(sleep_mock.call_args_list[0].args[0]), 0.0)

    def test_invalid_json_retries_then_succeeds(self):
        session = mock.Mock()
        resp_bad = mock.Mock()
        resp_bad.status_code = 200
        resp_bad.headers = {}
        resp_bad.raise_for_status = mock.Mock()
        resp_bad.json.side_effect = ValueError("bad json")

        resp_ok = mock.Mock()
        resp_ok.status_code = 200
        resp_ok.headers = {}
        resp_ok.raise_for_status = mock.Mock()
        resp_ok.json.return_value = {"bids": [], "asks": []}
        session.get.side_effect = [resp_bad, resp_ok]

        with mock.patch("prodesk.market_data.time.sleep") as sleep_mock:
            payload = _http_get_json(session, "https://example.test/book", params={}, timeout_sec=1.0, max_retries=1)

        self.assertEqual(payload, {"bids": [], "asks": []})
        self.assertGreaterEqual(len(sleep_mock.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
