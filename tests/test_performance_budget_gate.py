import json
import tempfile
import unittest
from pathlib import Path

from scripts.performance_budget_gate import run_gate


class PerformanceBudgetGateTests(unittest.TestCase):
    def test_gate_passes_within_thresholds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 120.0,
                    "gauge.process_rss_mb": 300.0,
                    "gauge.orders_used_60s": 10.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 10.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                },
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 180.0,
                    "gauge.process_rss_mb": 320.0,
                    "gauge.orders_used_60s": 20.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 30.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 2.0,
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_gate(
                log_dir=root,
                run_id=run_id,
                max_cycle_latency_p95_ms=500.0,
                max_cycle_latency_max_ms=1000.0,
                max_process_rss_mb=1024.0,
                max_order_capacity_used_ratio=1.0,
                max_cancel_capacity_used_ratio=1.0,
                max_order_capacity_breach_rows=0,
                max_cancel_capacity_breach_rows=0,
                max_order_capacity_breach_ratio=0.0,
                max_cancel_capacity_breach_ratio=0.0,
                max_latency_inactive_cycles=10.0,
                max_market_data_span_ms=500.0,
                max_strategy_exec_span_ms=500.0,
                max_state_io_span_ms=500.0,
                max_status_io_span_ms=500.0,
                max_cycle_residual_span_ms=500.0,
                min_status_rows=2,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertIn("decision_trace", result)

    def test_gate_fails_on_latency_and_memory_breach(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 1500.0,
                    "gauge.process_rss_mb": 3000.0,
                    "gauge.orders_used_60s": 120.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 120.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 999.0,
                }
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_gate(
                log_dir=root,
                run_id=run_id,
                max_cycle_latency_p95_ms=200.0,
                max_cycle_latency_max_ms=300.0,
                max_process_rss_mb=500.0,
                max_order_capacity_used_ratio=1.0,
                max_cancel_capacity_used_ratio=1.0,
                max_order_capacity_breach_rows=0,
                max_cancel_capacity_breach_rows=0,
                max_order_capacity_breach_ratio=0.0,
                max_cancel_capacity_breach_ratio=0.0,
                max_latency_inactive_cycles=10.0,
                max_market_data_span_ms=500.0,
                max_strategy_exec_span_ms=500.0,
                max_state_io_span_ms=500.0,
                max_status_io_span_ms=500.0,
                max_cycle_residual_span_ms=500.0,
                min_status_rows=1,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-1802", result["error_codes"])
            self.assertIn("BRO-1804", result["error_codes"])

    def test_gate_fails_on_component_latency_spans(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 200.0,
                    "gauge.process_rss_mb": 256.0,
                    "gauge.orders_used_60s": 10.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 10.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                    "gauge.cycle_span_market_data_ms": 700.0,
                    "gauge.cycle_span_strategy_exec_ms": 800.0,
                    "gauge.cycle_span_state_io_ms": 900.0,
                    "gauge.cycle_span_status_io_ms": 1000.0,
                    "gauge.cycle_span_residual_ms": 1100.0,
                }
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_gate(
                log_dir=root,
                run_id=run_id,
                max_cycle_latency_p95_ms=500.0,
                max_cycle_latency_max_ms=500.0,
                max_process_rss_mb=500.0,
                max_order_capacity_used_ratio=1.0,
                max_cancel_capacity_used_ratio=1.0,
                max_order_capacity_breach_rows=0,
                max_cancel_capacity_breach_rows=0,
                max_order_capacity_breach_ratio=0.0,
                max_cancel_capacity_breach_ratio=0.0,
                max_latency_inactive_cycles=10.0,
                max_market_data_span_ms=100.0,
                max_strategy_exec_span_ms=100.0,
                max_state_io_span_ms=100.0,
                max_status_io_span_ms=100.0,
                max_cycle_residual_span_ms=100.0,
                min_status_rows=1,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-1808", result["error_codes"])
            self.assertIn("BRO-1809", result["error_codes"])
            self.assertIn("BRO-1810", result["error_codes"])
            self.assertIn("BRO-1811", result["error_codes"])
            self.assertIn("BRO-1812", result["error_codes"])

    def test_gate_allows_single_capacity_spike_when_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 100.0,
                    "gauge.process_rss_mb": 256.0,
                    "gauge.orders_used_60s": 97.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 97.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                },
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 110.0,
                    "gauge.process_rss_mb": 256.0,
                    "gauge.orders_used_60s": 100.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 100.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                },
                {
                    "run_id": run_id,
                    "gauge.cycle_latency_ms": 90.0,
                    "gauge.process_rss_mb": 256.0,
                    "gauge.orders_used_60s": 95.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 95.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_gate(
                log_dir=root,
                run_id=run_id,
                max_cycle_latency_p95_ms=500.0,
                max_cycle_latency_max_ms=1000.0,
                max_process_rss_mb=1024.0,
                max_order_capacity_used_ratio=0.98,
                max_cancel_capacity_used_ratio=0.98,
                max_order_capacity_breach_rows=1,
                max_cancel_capacity_breach_rows=1,
                max_order_capacity_breach_ratio=1.0,
                max_cancel_capacity_breach_ratio=1.0,
                max_latency_inactive_cycles=10.0,
                max_market_data_span_ms=500.0,
                max_strategy_exec_span_ms=500.0,
                max_state_io_span_ms=500.0,
                max_status_io_span_ms=500.0,
                max_cycle_residual_span_ms=500.0,
                min_status_rows=3,
            )
            self.assertTrue(result["ok"], msg=result["findings"])


if __name__ == "__main__":
    unittest.main()
