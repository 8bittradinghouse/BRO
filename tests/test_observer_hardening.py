import asyncio
import copy
import tempfile
import unittest
from unittest import mock

import requests

import observer


class ObserverHardeningTests(unittest.TestCase):
    def _make_observer(self):
        cfg = copy.deepcopy(observer.DEFAULTS)
        tmp_dir = tempfile.TemporaryDirectory()
        cfg["storage"]["log_dir"] = tmp_dir.name
        cfg["spot"]["enabled"] = False
        token_id = "tok-1"
        metas = {token_id: observer.TokenMeta(token_id=token_id, side="YES")}
        obs = observer.Observer(cfg, metas)
        return obs, token_id, tmp_dir

    def test_parse_float_rejects_non_finite(self):
        self.assertIsNone(observer.parse_float("nan"))
        self.assertIsNone(observer.parse_float("inf"))
        self.assertIsNone(observer.parse_float("-inf"))
        self.assertEqual(observer.parse_float("0"), 0.0)

    def test_best_level_handles_zero_price(self):
        price, size = observer.best_level([{"price": "0", "size": "123"}], is_bid=True)
        self.assertEqual(price, 0.0)
        self.assertEqual(size, 123.0)

    def test_empty_levels_clear_stale_quotes(self):
        obs, token_id, tmp_dir = self._make_observer()
        try:
            state = obs.token_states[token_id]
            changed = obs._update_quotes_from_payload(
                state,
                {
                    "token_id": token_id,
                    "bids": [{"price": "0.40", "size": "10"}],
                    "asks": [{"price": "0.60", "size": "8"}],
                },
            )
            self.assertTrue(changed)
            self.assertEqual(state.best_bid_price, 0.4)
            self.assertEqual(state.best_ask_price, 0.6)

            changed = obs._update_quotes_from_payload(
                state,
                {
                    "token_id": token_id,
                    "bids": [],
                    "asks": [],
                },
            )
            self.assertTrue(changed)
            self.assertIsNone(state.best_bid_price)
            self.assertIsNone(state.best_ask_price)
        finally:
            obs.market_writer.close()
            obs.spot_writer.close()
            obs.rest_session.close()
            obs.spot_session.close()
            tmp_dir.cleanup()

    def test_non_trade_payload_does_not_mutate_trade_fields(self):
        obs, token_id, tmp_dir = self._make_observer()
        try:
            payload = {
                "token_id": token_id,
                "event_type": "book",
                "price": "0.55",
                "bids": [{"price": "0.50", "size": "7"}],
                "asks": [{"price": "0.60", "size": "9"}],
            }
            asyncio.run(obs._process_market_payload(payload, source="ws"))
            state = obs.token_states[token_id]
            self.assertIsNone(state.last_trade_price)
            self.assertIsNone(state.last_trade_ts)
            self.assertIsNone(state.last_trade_side)
        finally:
            obs.market_writer.close()
            obs.spot_writer.close()
            obs.rest_session.close()
            obs.spot_session.close()
            tmp_dir.cleanup()

    def test_trade_payload_is_logged_even_without_state_change(self):
        obs, token_id, tmp_dir = self._make_observer()
        try:
            payload = {
                "token_id": token_id,
                "event_type": "trade",
                "last_trade_price": "0.51",
                "last_trade_side": "BUY",
                "last_trade_ts": "2026-01-01T00:00:00Z",
            }
            asyncio.run(obs._process_market_payload(payload, source="ws"))
            asyncio.run(obs._process_market_payload(payload, source="ws"))
            path = obs.market_writer.current_path
            self.assertIsNotNone(path)
            with path.open("r", encoding="utf-8") as fh:
                lines = [line for line in fh if line.strip()]
            self.assertEqual(len(lines), 2)
        finally:
            obs.market_writer.close()
            obs.spot_writer.close()
            obs.rest_session.close()
            obs.spot_session.close()
            tmp_dir.cleanup()

    def test_discovery_failure_returns_empty_result(self):
        cfg = copy.deepcopy(observer.DEFAULTS)
        cfg["targets"]["discovery"]["enabled"] = True
        cfg["gamma"]["max_pages"] = 1
        cfg["gamma"]["page_limit"] = 50
        session = requests.Session()
        try:
            with mock.patch("observer.http_get_json", side_effect=RuntimeError("boom")), mock.patch.object(
                observer.LOG, "warning"
            ):
                result = observer.discover_tokens(cfg, session, symbols_override=None)
            self.assertEqual(result, {})
        finally:
            session.close()

    def test_http_get_json_clamps_negative_retry_after(self):
        session = mock.Mock()
        resp_429 = mock.Mock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "-5"}
        resp_429.raise_for_status = mock.Mock()

        resp_ok = mock.Mock()
        resp_ok.status_code = 200
        resp_ok.headers = {}
        resp_ok.raise_for_status = mock.Mock()
        resp_ok.json.return_value = {"ok": True}
        session.get.side_effect = [resp_429, resp_ok]

        with mock.patch("observer.time.sleep") as sleep_mock:
            payload = observer.http_get_json(session, "https://example.test", max_retries=1)

        self.assertEqual(payload, {"ok": True})
        self.assertGreaterEqual(float(sleep_mock.call_args_list[0].args[0]), 0.0)

    def test_validate_config_rejects_bad_ws_guardrails(self):
        cfg = copy.deepcopy(observer.DEFAULTS)
        cfg["ws"]["reconnect_backoff_initial_sec"] = 5.0
        cfg["ws"]["reconnect_backoff_max_sec"] = 1.0
        with self.assertRaises(ValueError):
            observer.validate_config(cfg)

        cfg = copy.deepcopy(observer.DEFAULTS)
        cfg["ws"]["ping_interval_sec"] = 10.0
        cfg["ws"]["ping_timeout_sec"] = 5.0
        with self.assertRaises(ValueError):
            observer.validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
