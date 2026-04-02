import json
import tempfile
import unittest
from pathlib import Path

from scripts.paper_live_parity import run_parity


class PaperLiveParityTests(unittest.TestCase):
    def test_parity_passes_with_small_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paper = {
                "quote_uptime_ratio": 0.95,
                "error_rows": 1,
                "execution_quality": {"capture_minus_adverse": 2.5},
                "sniper": {"fill_rate": 0.6},
                "latency_distribution_ms": {"p90_ms": 120.0},
            }
            live = {
                "quote_uptime_ratio": 0.90,
                "error_rows": 2,
                "execution_quality": {"capture_minus_adverse": 2.0},
                "sniper": {"fill_rate": 0.7},
                "latency_distribution_ms": {"p90_ms": 150.0},
            }
            paper_path = root / "paper.json"
            live_path = root / "live.json"
            paper_path.write_text(json.dumps(paper), encoding="utf-8")
            live_path.write_text(json.dumps(live), encoding="utf-8")

            result = run_parity(
                paper_report_path=paper_path,
                live_report_path=live_path,
                max_uptime_gap=0.10,
                max_error_rows_gap=2.0,
                max_capture_gap=1.0,
                max_sniper_fill_rate_gap=0.2,
                max_latency_p90_gap_ms=40.0,
            )
            self.assertTrue(result["ok"], msg=result["findings"])

    def test_parity_fails_with_large_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paper = {"quote_uptime_ratio": 1.0, "error_rows": 0, "execution_quality": {"capture_minus_adverse": 10.0}}
            live = {"quote_uptime_ratio": 0.1, "error_rows": 12, "execution_quality": {"capture_minus_adverse": -5.0}}
            paper_path = root / "paper.json"
            live_path = root / "live.json"
            paper_path.write_text(json.dumps(paper), encoding="utf-8")
            live_path.write_text(json.dumps(live), encoding="utf-8")

            result = run_parity(
                paper_report_path=paper_path,
                live_report_path=live_path,
                max_uptime_gap=0.2,
                max_error_rows_gap=1.0,
                max_capture_gap=3.0,
                max_sniper_fill_rate_gap=0.1,
                max_latency_p90_gap_ms=20.0,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("parity_gap_quote_uptime" in x for x in result["findings"]))
            self.assertIn("BRO-1701", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
