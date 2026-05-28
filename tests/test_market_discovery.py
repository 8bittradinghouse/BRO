import copy
import datetime as dt
import requests
import unittest
from unittest import mock

from prodesk.common import utc_iso
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.market_discovery import MarketDiscovery


class MarketDiscoveryTests(unittest.TestCase):
    def _cfg(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = []
        cfg["targets"]["discovery"]["enabled"] = True
        cfg["targets"]["discovery"]["symbols"] = ["BTC"]
        cfg["targets"]["discovery"]["keywords_any"] = ["5 minute", "up or down"]
        cfg["targets"]["discovery"]["max_pairs"] = 1
        cfg["targets"]["discovery"]["max_pages"] = 1
        cfg["targets"]["discovery"]["page_limit"] = 100
        cfg["targets"]["discovery"]["require_fee_enabled"] = True
        cfg["lifecycle"]["selection"]["max_sec_to_expiry"] = 0.0
        cfg["lifecycle"]["selection"]["min_market_age_sec"] = 0.0
        return cfg

    def test_discovery_selects_single_earliest_pair(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": True,
            },
            {
                "id": "m2",
                "conditionId": "c2",
                "question": "BTC 5 minute up or down now?",
                "clobTokenIds": ["yes2", "no2"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:02:00Z",
                "active": True,
            },
            {
                "id": "m3",
                "conditionId": "c3",
                "question": "ETH 5 minute up or down?",
                "clobTokenIds": ["yes3", "no3"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:03:00Z",
                "active": True,
            },
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.pairs_selected, 1)
            self.assertEqual(result.token_ids, ["yes1", "no1"])
            self.assertEqual(result.candidate_pairs_token_ids, [["yes1", "no1"]])
        finally:
            discovery.close()

    def test_discovery_handles_payload_wrapper(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = {
            "markets": [
                {
                    "id": "m1",
                    "conditionId": "c1",
                    "question": "BTC 5 minute up or down?",
                    "clobTokenIds": ["yes1", "no1"],
                    "endDateIso": "2030-01-01T00:01:00Z",
                    "active": True,
                }
            ]
        }
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes1", "no1"])
        finally:
            discovery.close()

    def test_discovery_returns_expiry_mapping(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down above $65000?",
                "clobTokenIds": ["yes1", "no1"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_expiry_utc_by_token.get("yes1"), "2030-01-01T00:01:00.000Z")
            self.assertEqual(result.token_expiry_utc_by_token.get("no1"), "2030-01-01T00:01:00.000Z")
            self.assertEqual(result.token_side_by_token.get("yes1"), "YES")
            self.assertEqual(result.token_side_by_token.get("no1"), "NO")
            self.assertEqual(result.token_strike_by_token.get("yes1"), 65000.0)
            self.assertEqual(result.token_strike_by_token.get("no1"), 65000.0)
        finally:
            discovery.close()

    def test_discovery_maps_up_down_outcomes_and_event_start_anchor(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute Up or Down - test window",
                "clobTokenIds": ["up1", "down1"],
                "outcomes": ["Up", "Down"],
                "eventStartTime": "2030-01-01T00:00:00Z",
                "endDateIso": "2030-01-01T00:05:00Z",
                "isFeeEnabled": True,
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["up1", "down1"])
            self.assertEqual(result.token_side_by_token.get("up1"), "YES")
            self.assertEqual(result.token_side_by_token.get("down1"), "NO")
            self.assertEqual(result.token_open_anchor_utc_by_token.get("up1"), "2030-01-01T00:00:00.000Z")
            self.assertEqual(result.token_open_anchor_utc_by_token.get("down1"), "2030-01-01T00:00:00.000Z")
            self.assertNotIn("up1", result.token_strike_by_token)
            self.assertNotIn("down1", result.token_strike_by_token)
        finally:
            discovery.close()

    def test_discovery_rejects_timestamp_like_numeric_strike_and_falls_back_to_question_price(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down above $65000?",
                "clobTokenIds": ["yes1", "no1"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "referencePrice": 1893456000.0,
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_strike_by_token.get("yes1"), 65000.0)
            self.assertEqual(result.token_strike_by_token.get("no1"), 65000.0)
        finally:
            discovery.close()

    def test_discovery_drops_timestamp_like_numeric_strike_without_price_fallback(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "referencePrice": 1893456000.0,
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertNotIn("yes1", result.token_strike_by_token)
            self.assertNotIn("no1", result.token_strike_by_token)
        finally:
            discovery.close()

    def test_discovery_filters_explicit_fee_disabled_markets(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "isFeeEnabled": False,
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": True,
            },
            {
                "id": "m2",
                "conditionId": "c2",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes2", "no2"],
                "isFeeEnabled": True,
                "endDateIso": "2030-01-01T00:02:00Z",
                "active": True,
            },
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes2", "no2"])
            self.assertEqual(result.fee_eligible_markets, 1)
        finally:
            discovery.close()

    def test_discovery_respects_allow_token_ids_filter(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["allow_token_ids"] = ["yes2", "no2"]
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": True,
            },
            {
                "id": "m2",
                "conditionId": "c2",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes2", "no2"],
                "outcomes": ["Yes", "No"],
                "endDateIso": "2030-01-01T00:02:00Z",
                "active": True,
            },
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes2", "no2"])
            self.assertTrue(result.allowlist_enabled)
            self.assertEqual(result.allowlist_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_falls_back_to_event_slug_probe(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["event_slug_probe_enabled"] = True
        cfg["targets"]["discovery"]["event_slug_prefix"] = "btc-updown-5m"
        discovery = MarketDiscovery(cfg)

        fallback_market = {
            "id": "m_fallback",
            "conditionId": "c_fallback",
            "question": "Bitcoin Up or Down - test window",
            "clobTokenIds": ["yes_fallback", "no_fallback"],
            "outcomes": ["Up", "Down"],
            "endDateIso": "2030-01-01T00:05:00Z",
            "active": True,
        }

        def _fake_http_get_json(_session, _url, **kwargs):  # noqa: ANN001
            params = kwargs.get("params", {})
            if isinstance(params, dict) and str(params.get("slug", "")).startswith("btc-updown-5m-"):
                return [fallback_market]
            return []

        try:
            with mock.patch("prodesk.market_discovery._http_get_json", side_effect=_fake_http_get_json):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes_fallback", "no_fallback"])
            self.assertEqual(result.pairs_selected, 1)
        finally:
            discovery.close()

    def test_discovery_primary_gamma_failure_falls_back_to_event_slug_probe(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["event_slug_probe_enabled"] = True
        cfg["targets"]["discovery"]["event_slug_prefix"] = "btc-updown-5m"
        discovery = MarketDiscovery(cfg)

        fallback_market = {
            "id": "m_primary_error_fallback",
            "conditionId": "c_primary_error_fallback",
            "question": "Bitcoin Up or Down - primary gamma failure fallback",
            "clobTokenIds": ["yes_primary_error", "no_primary_error"],
            "outcomes": ["Up", "Down"],
            "endDateIso": "2030-01-01T00:05:00Z",
            "active": True,
        }

        def _fake_http_get_json(_session, url, **kwargs):  # noqa: ANN001
            params = kwargs.get("params", {})
            slug = str(params.get("slug", ""))
            if str(url).endswith("/markets") and not slug:
                raise requests.HTTPError("403 Client Error")
            if slug.startswith("btc-updown-5m-"):
                return [fallback_market]
            return []

        try:
            with mock.patch("prodesk.market_discovery._http_get_json", side_effect=_fake_http_get_json):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes_primary_error", "no_primary_error"])
            self.assertEqual(result.pairs_selected, 1)
        finally:
            discovery.close()

    def test_discovery_slug_probe_falls_back_to_events_endpoint_when_markets_slug_errors(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["event_slug_probe_enabled"] = True
        cfg["targets"]["discovery"]["event_slug_prefix"] = "btc-updown-5m"
        discovery = MarketDiscovery(cfg)

        fallback_market = {
            "id": "m_event_fallback",
            "conditionId": "c_event_fallback",
            "question": "Bitcoin Up or Down - event fallback",
            "clobTokenIds": ["yes_event", "no_event"],
            "outcomes": ["Up", "Down"],
            "endDateIso": "2030-01-01T00:05:00Z",
            "active": True,
        }

        def _fake_http_get_json(_session, url, **kwargs):  # noqa: ANN001
            params = kwargs.get("params", {})
            slug = str(params.get("slug", ""))
            if not slug.startswith("btc-updown-5m-"):
                return []
            if str(url).endswith("/markets"):
                raise requests.HTTPError("500 Server Error")
            if str(url).endswith("/events"):
                return [
                    {
                        "slug": slug,
                        "title": "Bitcoin Up or Down - event fallback",
                        "markets": [fallback_market],
                    }
                ]
            return []

        try:
            with mock.patch("prodesk.market_discovery._http_get_json", side_effect=_fake_http_get_json):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes_event", "no_event"])
            self.assertEqual(result.pairs_selected, 1)
        finally:
            discovery.close()

    def test_discovery_event_slug_probe_can_be_disabled(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["event_slug_probe_enabled"] = False
        cfg["targets"]["discovery"]["event_slug_prefix"] = "btc-updown-5m"
        discovery = MarketDiscovery(cfg)

        def _fake_http_get_json(_session, _url, **kwargs):  # noqa: ANN001
            params = kwargs.get("params", {})
            if isinstance(params, dict) and str(params.get("slug", "")).startswith("btc-updown-5m-"):
                return [
                    {
                        "id": "m_fallback",
                        "conditionId": "c_fallback",
                        "question": "Bitcoin Up or Down - test window",
                        "clobTokenIds": ["yes_fallback", "no_fallback"],
                        "outcomes": ["Up", "Down"],
                        "endDateIso": "2030-01-01T00:05:00Z",
                        "active": True,
                    }
                ]
            return []

        try:
            with mock.patch("prodesk.market_discovery._http_get_json", side_effect=_fake_http_get_json):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
        finally:
            discovery.close()

    def test_discovery_rejects_missing_token_ids_even_if_keywords_match(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["", ""],
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_inactive_market_even_with_valid_shape(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": "2030-01-01T00:01:00Z",
                "active": False,
            },
            {
                "id": "m2",
                "conditionId": "c2",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes2", "no2"],
                "endDateIso": "2030-01-01T00:02:00Z",
                "active": True,
            },
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes2", "no2"])
            self.assertEqual(result.pairs_selected, 1)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_expired_market_contract(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": "2000-01-01T00:01:00Z",
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_market_outside_ownership_entry_ceiling(self):
        cfg = self._cfg()
        cfg["lifecycle"]["selection"]["max_sec_to_expiry"] = 90.0
        cfg["lifecycle"]["selection"]["min_market_age_sec"] = 0.0
        discovery = MarketDiscovery(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": utc_iso(now + dt.timedelta(seconds=91)),
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_accepts_market_inside_ownership_entry_ceiling(self):
        cfg = self._cfg()
        cfg["lifecycle"]["selection"]["max_sec_to_expiry"] = 90.0
        cfg["lifecycle"]["selection"]["min_market_age_sec"] = 0.0
        discovery = MarketDiscovery(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": utc_iso(now + dt.timedelta(seconds=89)),
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, ["yes1", "no1"])
            self.assertEqual(result.pairs_selected, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_market_younger_than_min_market_age(self):
        cfg = self._cfg()
        cfg["lifecycle"]["selection"]["max_sec_to_expiry"] = 90.0
        cfg["lifecycle"]["selection"]["min_market_age_sec"] = 60.0
        discovery = MarketDiscovery(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "eventStartTime": utc_iso(now - dt.timedelta(seconds=59)),
                "endDateIso": utc_iso(now + dt.timedelta(seconds=80)),
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_market_missing_open_anchor_when_age_gate_required(self):
        cfg = self._cfg()
        cfg["lifecycle"]["selection"]["max_sec_to_expiry"] = 90.0
        cfg["lifecycle"]["selection"]["min_market_age_sec"] = 60.0
        discovery = MarketDiscovery(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": utc_iso(now + dt.timedelta(seconds=80)),
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_rejects_date_only_end_without_precise_timestamp(self):
        cfg = self._cfg()
        discovery = MarketDiscovery(cfg)
        payload = [
            {
                "id": "m1",
                "conditionId": "c1",
                "question": "BTC 5 minute up or down?",
                "clobTokenIds": ["yes1", "no1"],
                "endDateIso": "2030-01-01",
                "active": True,
            }
        ]
        try:
            with mock.patch("prodesk.market_discovery._http_get_json", return_value=payload):
                result = discovery.discover()
            self.assertEqual(result.token_ids, [])
            self.assertEqual(result.pairs_selected, 0)
            self.assertGreaterEqual(result.contract_rejected_pairs, 1)
        finally:
            discovery.close()

    def test_discovery_slug_probe_accepts_date_only_end_iso_when_precise_end_present(self):
        cfg = self._cfg()
        cfg["targets"]["discovery"]["event_slug_probe_enabled"] = True
        cfg["targets"]["discovery"]["event_slug_prefix"] = "btc-updown-5m"
        discovery = MarketDiscovery(cfg)

        now = dt.datetime.now(dt.timezone.utc)
        fallback_market = {
            "id": "m_fallback",
            "conditionId": "c_fallback",
            "question": "Bitcoin Up or Down - test window",
            "clobTokenIds": ["yes_fallback", "no_fallback"],
            "outcomes": ["Up", "Down"],
            "active": True,
            # Real gamma shape can include date-only endDateIso while a precise
            # endDate is present.
            "endDateIso": now.date().isoformat(),
            "endDate": utc_iso(now + dt.timedelta(minutes=5)),
        }

        def _fake_http_get_json(_session, _url, **kwargs):  # noqa: ANN001
            params = kwargs.get("params", {})
            if isinstance(params, dict) and str(params.get("slug", "")).startswith("btc-updown-5m-"):
                return [fallback_market]
            return []

        try:
            with mock.patch("prodesk.market_discovery._http_get_json", side_effect=_fake_http_get_json):
                result = discovery.discover()
            self.assertEqual(result.pairs_selected, 1)
            self.assertEqual(result.token_ids, ["yes_fallback", "no_fallback"])
        finally:
            discovery.close()


if __name__ == "__main__":
    unittest.main()
