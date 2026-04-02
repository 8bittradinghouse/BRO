import collections
import unittest

from prodesk.gateway import LiveClobGateway, _normalize_evm_address, _normalize_private_key


class _FakeClient:
    def __init__(self, *, orders=None, trades=None, cancel_response=None):
        self._orders = orders or []
        self._trades = trades or []
        self._cancel_response = {} if cancel_response is None else cancel_response
        self.orders_calls = 0

    def get_orders(self, *_args, **_kwargs):
        self.orders_calls += 1
        return self._orders

    def get_trades(self):
        return self._trades

    def cancel(self, _order_id):
        return self._cancel_response


class GatewayHardeningTests(unittest.TestCase):
    def test_normalize_private_key_accepts_0x_and_plain_hex(self):
        raw = "a" * 64
        self.assertEqual(_normalize_private_key(raw), "0x" + raw)
        self.assertEqual(_normalize_private_key("0x" + raw), "0x" + raw)

    def test_normalize_private_key_rejects_invalid_length_or_chars(self):
        with self.assertRaisesRegex(Exception, "32-byte hex"):
            _normalize_private_key("0x1234")
        with self.assertRaisesRegex(Exception, "32-byte hex"):
            _normalize_private_key("0x" + ("g" * 64))

    def test_normalize_evm_address_accepts_valid_hex(self):
        raw = "0x" + ("b" * 40)
        self.assertEqual(_normalize_evm_address(raw), raw)

    def test_normalize_evm_address_rejects_missing_prefix_or_bad_length(self):
        with self.assertRaisesRegex(Exception, "start with 0x"):
            _normalize_evm_address("b" * 40)
        with self.assertRaisesRegex(Exception, "20-byte hex"):
            _normalize_evm_address("0x1234")

    def test_get_open_orders_filters_closed_status_and_keeps_missing_created_ts_none(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = _FakeClient(
            orders=[
                {
                    "id": "open-1",
                    "asset_id": "tok-1",
                    "side": "BUY",
                    "price": "0.45",
                    "size": "12",
                    "remaining_size": "12",
                    "status": "OPEN",
                },
                {
                    "id": "closed-1",
                    "asset_id": "tok-1",
                    "side": "SELL",
                    "price": "0.55",
                    "size": "8",
                    "remaining_size": "0",
                    "status": "CANCELED",
                },
            ]
        )
        gateway._OpenOrderParams = lambda: None

        orders = gateway.get_open_orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].order_id, "open-1")
        self.assertIsNone(orders[0].created_ts_utc)

    def test_poll_fills_ignores_older_trade_timestamps(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = _FakeClient(
            trades=[
                {
                    "id": "old-1",
                    "asset_id": "tok-1",
                    "side": "BUY",
                    "price": "0.45",
                    "size": "1",
                    "timestamp": 1734999999000,
                },
                {
                    "id": "eq-1",
                    "asset_id": "tok-1",
                    "side": "BUY",
                    "price": "0.46",
                    "size": "1",
                    "timestamp": 1735000000000,
                },
                {
                    "id": "zero-size",
                    "asset_id": "tok-1",
                    "side": "BUY",
                    "price": "0.47",
                    "size": "0",
                    "timestamp": 1735000001000,
                },
            ]
        )
        gateway._seen_trade_ids = set()
        gateway._seen_trade_ids_queue = collections.deque()
        gateway._seen_trade_ids_max = 32
        gateway._last_trade_ts_epoch = 1735000000.0

        fills = gateway.poll_fills()
        self.assertEqual([f.trade_id for f in fills], ["eq-1"])
        self.assertGreaterEqual(gateway._last_trade_ts_epoch, 1735000000.0)

    def test_seed_fill_cursor_only_moves_forward(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway._last_trade_ts_epoch = None
        gateway.seed_fill_cursor("2026-01-01T00:00:01Z")
        first = gateway._last_trade_ts_epoch
        self.assertIsNotNone(first)
        gateway.seed_fill_cursor("2020-01-01T00:00:00Z")
        self.assertEqual(gateway._last_trade_ts_epoch, first)

    def test_get_open_orders_uses_ttl_cache_and_invalidation(self):
        client = _FakeClient(
            orders=[
                {
                    "id": "open-1",
                    "asset_id": "tok-1",
                    "side": "BUY",
                    "price": "0.45",
                    "size": "12",
                    "remaining_size": "12",
                    "status": "OPEN",
                }
            ]
        )
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = client
        gateway._OpenOrderParams = lambda: None
        gateway._open_orders_cache_ttl_sec = 1.0
        gateway._open_orders_cache = None
        gateway._open_orders_cache_expires_mono = 0.0

        orders1 = gateway.get_open_orders()
        orders2 = gateway.get_open_orders()
        self.assertEqual(len(orders1), 1)
        self.assertEqual(len(orders2), 1)
        self.assertEqual(client.orders_calls, 1)

        gateway._invalidate_open_orders_cache()
        orders3 = gateway.get_open_orders()
        self.assertEqual(len(orders3), 1)
        self.assertEqual(client.orders_calls, 2)

    def test_cancel_order_requires_explicit_confirmation(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = _FakeClient(cancel_response={"status": "unknown"})
        gateway._open_orders_cache = None
        gateway._open_orders_cache_expires_mono = 0.0
        with self.assertRaisesRegex(Exception, "cancel_order_unconfirmed"):
            gateway.cancel_order("ord-1")

    def test_cancel_order_accepts_explicit_boolean_confirmation(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = _FakeClient(cancel_response={"canceled": True})
        gateway._open_orders_cache = None
        gateway._open_orders_cache_expires_mono = 0.0
        self.assertTrue(gateway.cancel_order("ord-1"))

    def test_cancel_order_maps_not_found_to_false(self):
        gateway = LiveClobGateway.__new__(LiveClobGateway)
        gateway.client = _FakeClient(cancel_response={"status": "not_found"})
        gateway._open_orders_cache = None
        gateway._open_orders_cache_expires_mono = 0.0
        self.assertFalse(gateway.cancel_order("ord-1"))


if __name__ == "__main__":
    unittest.main()
