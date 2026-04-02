import json
import tempfile
import unittest
from pathlib import Path

from scripts.promotion_evidence_gate import run_gate


class PromotionEvidenceGateTests(unittest.TestCase):
    def test_gate_surfaces_maker_reference_activity_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
                "maker_reference_direct_midpoint_activity": 9.0,
                "maker_reference_bounded_fallback_activity": 4.0,
                "maker_market_reference_fallback_bid_count": 1.0,
                "maker_market_reference_fallback_ask_count": 3.0,
            }
            reconcile = {"mismatch_ratio": 0.0, "verification_level": "venue_verified"}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")

            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            metrics = result.get("metrics", {})
            self.assertEqual(metrics.get("maker_reference_direct_midpoint_activity"), 9.0)
            self.assertEqual(metrics.get("maker_reference_bounded_fallback_activity"), 4.0)
            self.assertEqual(metrics.get("maker_market_reference_fallback_bid_count"), 1.0)
            self.assertEqual(metrics.get("maker_market_reference_fallback_ask_count"), 3.0)

    def test_gate_passes_for_good_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 0.995,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 1.2},
            }
            reconcile = {"mismatch_ratio": 0.01, "verification_level": "venue_verified"}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")

            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.99,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertIn("decision_trace", result)

    def test_gate_fails_for_bad_reconcile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {"mismatch_ratio": 0.2}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")

            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("reconcile_mismatch_ratio_too_high" in item for item in result["findings"]))

    def test_gate_fails_for_websocket_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {"mismatch_ratio": 0.0}
            websocket = {
                "metrics": {
                    "book_feed_down_ratio": 0.9,
                    "chainlink_down_ratio": 0.9,
                    "book_feed_reconnects_per_hour": 100.0,
                    "chainlink_reconnects_per_hour": 100.0,
                    "chainlink_dropped_ticks_max": 5.0,
                }
            }
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            websocket_path = root / "websocket.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            websocket_path.write_text(json.dumps(websocket), encoding="utf-8")

            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=websocket_path,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2201", result["error_codes"])

    def test_gate_marks_reconcile_venue_unavailable_as_advisory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {"mismatch_ratio": 0.0, "verification_level": "venue_unavailable"}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(any("reconcile_not_fully_venue_verified:venue_unavailable" == x for x in result["advisories"]))

    def test_gate_accepts_allowed_nonvenue_verification_level(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {"mismatch_ratio": 0.0, "verification_level": "paper_sim_verified"}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
                allowed_nonvenue_verification_levels=["paper_sim_verified"],
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["advisories"])

    def test_gate_fails_when_websocket_report_required_but_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {"mismatch_ratio": 0.0}
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
                websocket_report_required=True,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2201", result["error_codes"])

    def test_gate_fails_when_reconcile_status_reports_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
            }
            reconcile = {
                "mismatch_ratio": 0.0,
                "status": "mismatch",
                "exceeds_threshold": True,
            }
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("reconcile_status_not_ok:mismatch" == x for x in result["findings"]))
            self.assertTrue(any("reconcile_exceeds_threshold_true" == x for x in result["findings"]))

    def test_gate_fails_when_soak_and_reconcile_artifact_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = {
                "quote_uptime_ratio": 1.0,
                "error_rows": 0,
                "execution_quality": {"capture_minus_adverse": 0.5},
                "artifact_identity": {"run_id": "run-a", "config_fingerprint_sha256": "abc"},
            }
            reconcile = {
                "mismatch_ratio": 0.0,
                "artifact_identity": {"run_id": "run-b", "config_fingerprint_sha256": "abc"},
            }
            soak_path = root / "soak.json"
            reconcile_path = root / "reconcile.json"
            soak_path.write_text(json.dumps(soak), encoding="utf-8")
            reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
            result = run_gate(
                soak_report_path=soak_path,
                reconcile_report_path=reconcile_path,
                websocket_report_path=None,
                min_uptime_ratio=0.95,
                max_error_rows=0,
                min_execution_quality_net=0.0,
                max_reconcile_mismatch_ratio=0.02,
                max_websocket_book_feed_down_ratio=0.2,
                max_websocket_chainlink_down_ratio=0.2,
                max_websocket_book_feed_reconnects_per_hour=40.0,
                max_websocket_chainlink_reconnects_per_hour=40.0,
                max_websocket_chainlink_dropped_ticks=0.0,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("artifact_identity_mismatch:run_id:soak_vs_reconcile" == x for x in result["findings"]))


if __name__ == "__main__":
    unittest.main()
