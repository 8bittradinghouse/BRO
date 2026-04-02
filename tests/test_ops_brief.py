import unittest

from scripts.ops_brief import build_brief


class OpsBriefTests(unittest.TestCase):
    def test_ops_brief_ok(self):
        payload = {
            "run_id": "r1",
            "integrity": {"ok": True, "status_row_count": 20},
            "financial_summary": {
                "fill_count": 12,
                "orders_submitted": 20,
                "execution_capture_minus_adverse": 0.42,
                "sniper_submits": 5,
                "sniper_fills": 3,
                "sniper_fill_rate": 0.6,
                "sniper_midpoint_win_rate_proxy": 0.5,
                "quote_uptime_ratio": 0.95,
                "error_rows": 1,
            },
        }
        brief = build_brief(payload)
        self.assertEqual(brief["severity"], "OK")
        self.assertTrue(brief["integrity_ok"])

    def test_ops_brief_page_when_integrity_bad(self):
        payload = {
            "run_id": "r2",
            "integrity": {"ok": False, "status_row_count": 1},
            "financial_summary": {"error_rows": 0},
        }
        brief = build_brief(payload)
        self.assertEqual(brief["severity"], "PAGE")


if __name__ == "__main__":
    unittest.main()
