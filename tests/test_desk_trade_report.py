import json
import tempfile
import unittest
from pathlib import Path

from scripts.desk_trade_report import build_report


class DeskTradeReportTests(unittest.TestCase):
    def test_build_report_basic_pnl_and_avg_prices(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = [
                {"ts_utc": "2026-03-08T00:00:00.000Z", "event_type": "order_submit", "run_id": "r1"},
                {"ts_utc": "2026-03-08T00:00:01.000Z", "event_type": "book_top", "token_id": "t1", "midpoint": 0.60, "run_id": "r1"},
                {"ts_utc": "2026-03-08T00:00:02.000Z", "event_type": "fill", "token_id": "t1", "side": "BUY", "price": 0.50, "size": 10, "run_id": "r1"},
                {"ts_utc": "2026-03-08T00:00:03.000Z", "event_type": "fill", "token_id": "t1", "side": "SELL", "price": 0.70, "size": 6, "run_id": "r1"},
            ]
            (root / "events_2026-03-08.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            report = build_report(log_dir=root, run_id="r1", date_str="2026-03-08")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["fills"], 2.0)
        self.assertAlmostEqual(report["avg_entry_price"], 0.5, places=8)
        self.assertAlmostEqual(report["avg_exit_price"], 0.7, places=8)
        self.assertAlmostEqual(report["realized_cashflow"], -0.8, places=8)
        self.assertAlmostEqual(report["net_position_qty"], 4.0, places=8)
        self.assertAlmostEqual(report["mark_to_mid_value"], 2.4, places=8)
        self.assertAlmostEqual(report["pnl_mark_to_mid"], 1.6, places=8)


if __name__ == "__main__":
    unittest.main()
