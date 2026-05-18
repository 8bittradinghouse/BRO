import json
import tempfile
import unittest
from pathlib import Path

from scripts.api_contract_drift_audit import run_audit


class ApiContractDriftAuditTests(unittest.TestCase):
    def test_audit_passes_valid_samples(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample = {
                "polymarket_orders": [
                    {"id": "o1", "asset_id": "tok1", "side": "BUY", "price": "0.45", "size": "10", "status": "OPEN"}
                ],
                "polymarket_trades": [
                    {"id": "t1", "asset_id": "tok1", "side": "BUY", "price": "0.46", "size": "2", "timestamp": 1735000000000}
                ],
                "rtds_stream_tick_event": {
                    "contract": "bro.rtds_stream.event.v1",
                    "event": "tick",
                    "symbol": "btc/usd",
                    "price": 64000.1,
                    "topic": "crypto_prices_chainlink",
                    "source_ts_utc": "2026-01-01T00:00:00Z",
                },
                "market_stream_top_event": {
                    "contract": "bro.market_stream.event.v1",
                    "event": "top",
                    "token_id": "tok1",
                    "best_bid_price": 0.45,
                    "best_bid_size": 10,
                    "best_ask_price": 0.46,
                    "best_ask_size": 9,
                    "received_ts_utc": "2026-01-01T00:00:00Z",
                },
            }
            path = root / "samples.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            result = run_audit(samples_path=path)
        self.assertTrue(result["ok"], msg=result["findings"])

    def test_audit_fails_on_missing_and_type_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample = {
                "polymarket_orders": [{"id": "o1", "asset_id": "tok1", "side": "BUY", "price": "x", "size": "10"}],
                "polymarket_trades": [{"id": "", "asset_id": "tok1", "side": "BUY", "price": "0.4", "size": "2"}],
                "rtds_stream_tick_event": {
                    "contract": "",
                    "event": "tick",
                    "symbol": "",
                    "price": "nan",
                    "topic": "",
                    "source_ts_utc": "",
                },
                "market_stream_top_event": {
                    "contract": "bro.market_stream.event.v1",
                    "event": "top",
                    "token_id": "tok1",
                    "best_bid_price": None,
                    "best_ask_price": None,
                    "best_bid_size": "oops",
                },
            }
            path = root / "samples.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            result = run_audit(samples_path=path)
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("api_contract_missing_field:polymarket_orders:status", text)
        self.assertIn("api_contract_type_mismatch:polymarket_orders:price:numeric", text)
        self.assertIn("api_contract_type_mismatch:polymarket_trades:timestamp:numeric", text)
        self.assertIn("api_contract_missing_field:market_stream_top_event:best_bid_or_best_ask", text)
        self.assertIn("api_contract_type_mismatch:market_stream_top_event:best_bid_size:numeric", text)
        self.assertIn("BRO-2301", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
