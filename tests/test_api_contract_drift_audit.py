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
                "chainlink_message": {
                    "payload": {"symbol": "btc/usd", "value": "64000.1", "timestamp": "2026-01-01T00:00:00Z"}
                },
                "book_feed_message": {"asset_id": "tok1", "bids": [[0.45, 10]], "asks": [[0.46, 9]]},
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
                "chainlink_message": {"payload": {"symbol": "", "value": "nan", "timestamp": ""}},
                "book_feed_message": {"asset_id": "tok1", "bids": {}, "asks": []},
            }
            path = root / "samples.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            result = run_audit(samples_path=path)
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("api_contract_missing_field:polymarket_orders:status", text)
        self.assertIn("api_contract_type_mismatch:polymarket_orders:price:numeric", text)
        self.assertIn("api_contract_type_mismatch:polymarket_trades:timestamp:numeric", text)
        self.assertIn("api_contract_type_mismatch:book_feed_message:bids:list", text)
        self.assertIn("BRO-2301", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
