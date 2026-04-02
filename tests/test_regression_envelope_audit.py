import json
import tempfile
import unittest
from pathlib import Path

from scripts.regression_envelope_audit import run_audit


class RegressionEnvelopeAuditTests(unittest.TestCase):
    def test_audit_passes_when_metrics_within_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            baseline = root / "baseline.json"
            nightly = root / "nightly.json"
            perf = root / "performance.json"
            baseline.write_text(
                json.dumps(
                    {
                        "nightly": {"quote_uptime_ratio": {"min": 0.5, "max": 1.0}},
                        "performance": {"metrics.cycle_latency_p95_ms": {"max": 1000.0}},
                    }
                ),
                encoding="utf-8",
            )
            nightly.write_text(json.dumps({"quote_uptime_ratio": 0.8, "error_rows": 0}), encoding="utf-8")
            perf.write_text(json.dumps({"metrics": {"cycle_latency_p95_ms": 400.0}}), encoding="utf-8")
            result = run_audit(
                baseline_path=baseline,
                nightly_report_path=nightly,
                performance_report_path=perf,
            )
        self.assertTrue(result["ok"], msg=result["findings"])

    def test_audit_fails_on_breach_and_missing_metric(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            baseline = root / "baseline.json"
            nightly = root / "nightly.json"
            perf = root / "performance.json"
            baseline.write_text(
                json.dumps(
                    {
                        "nightly": {"quote_uptime_ratio": {"min": 0.9}},
                        "performance": {"metrics.cycle_latency_p95_ms": {"max": 10.0}},
                    }
                ),
                encoding="utf-8",
            )
            nightly.write_text(json.dumps({"quote_uptime_ratio": 0.2}), encoding="utf-8")
            perf.write_text(json.dumps({"metrics": {}}), encoding="utf-8")
            result = run_audit(
                baseline_path=baseline,
                nightly_report_path=nightly,
                performance_report_path=perf,
            )
        self.assertFalse(result["ok"])
        self.assertIn("BRO-2101", result["error_codes"])
        self.assertIn("BRO-2102", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
