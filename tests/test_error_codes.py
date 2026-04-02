import unittest

from prodesk.error_codes import ERROR_CODE_UNKNOWN, code_for_finding, summarize_error_codes


class ErrorCodesTests(unittest.TestCase):
    def test_code_for_finding_maps_known_prefix(self):
        self.assertEqual(code_for_finding("secret_load_failed:missing env var"), "BRO-1001")
        self.assertEqual(code_for_finding("status_ts_missing"), "BRO-1305")
        self.assertEqual(code_for_finding("status_counter_non_monotonic:counter.orders_submitted:1"), "BRO-1204")
        self.assertEqual(code_for_finding("performance_cycle_latency_p95_too_high:1>max:0"), "BRO-1802")
        self.assertEqual(code_for_finding("performance_cycle_span_market_data_too_high:1>max:0"), "BRO-1808")
        self.assertEqual(code_for_finding("websocket_evidence_chainlink_queue_size_too_high:2>max:1"), "BRO-2201")
        self.assertEqual(code_for_finding("websocket_promotion_chainlink_dropped_ticks_too_high:1>max:0"), "BRO-2201")
        self.assertEqual(code_for_finding("websocket_promotion_report_missing"), "BRO-2201")
        self.assertEqual(code_for_finding("api_contract_missing_field:polymarket_orders:id"), "BRO-2301")
        self.assertEqual(code_for_finding("soak_evidence_insufficient_runs:1<required:3"), "BRO-2401")
        self.assertEqual(code_for_finding("soak_evidence_promotion_passes_too_few:1<min:2"), "BRO-2402")
        self.assertEqual(code_for_finding("soak_delta_quote_uptime_ratio_regression:-0.1<min:-0.02"), "BRO-2403")
        self.assertEqual(code_for_finding("runtime_service_restart_policy_invalid:bro-maker:missing"), "BRO-2501")
        self.assertEqual(
            code_for_finding("readiness_runtime_non_promotable:NON_PROMOTABLE_NO_PARTICIPATION"),
            "BRO-2001",
        )
        self.assertEqual(
            code_for_finding("soak_runtime_non_promotable:NON_PROMOTABLE_NO_PARTICIPATION"),
            "BRO-2005",
        )
        self.assertEqual(
            code_for_finding("paper_harness_runtime_invalid:INVALID_SAFETY"),
            "BRO-2501",
        )
        self.assertEqual(code_for_finding("dependency_unpinned_requirement:line:1:x"), "BRO-1902")
        self.assertEqual(code_for_finding("soak_duration_too_short:1<min:2"), "BRO-2002")

    def test_unknown_mapping_returns_default(self):
        self.assertEqual(code_for_finding("something_else"), ERROR_CODE_UNKNOWN)

    def test_summarize_error_codes_unique_sorted(self):
        codes = summarize_error_codes(["status_ts_missing", "status_ts_missing", "secret_load_failed:x"])
        self.assertEqual(codes, ["BRO-1001", "BRO-1305"])


if __name__ == "__main__":
    unittest.main()
