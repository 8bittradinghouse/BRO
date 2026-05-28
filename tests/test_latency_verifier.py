import unittest

from prodesk.latency_verifier import LatencyVerifier


class LatencyVerifierTests(unittest.TestCase):
    def _cfg(self):
        return {
            "enabled": True,
            "window_samples": 200,
            "hit_threshold_ms": 120.0,
            "max_sample_lag_ms": 20_000.0,
            "drift_window_samples": 20,
            "drift_max_median_drop_ms": 40.0,
            "drift_max_hit_rate_drop": 0.6,
        }

    def test_snapshot_reports_raw_latency_distribution(self):
        verifier = LatencyVerifier(self._cfg())
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=180.0)
        snap = verifier.snapshot(active_tokens=["tok1"])
        self.assertEqual(snap.sample_count, 80)
        self.assertEqual(snap.token_count, 1)
        self.assertGreaterEqual(snap.median_lag_ms, 180.0)
        self.assertGreaterEqual(snap.hit_rate, 1.0)

    def test_prune_tokens_removes_old_windows(self):
        verifier = LatencyVerifier(self._cfg())
        verifier.observe(token_id="tok1", lag_ms=150.0)
        verifier.observe(token_id="tok2", lag_ms=150.0)
        verifier.prune_tokens({"tok1"})
        self.assertIsNotNone(verifier.token_stats("tok1"))
        self.assertIsNone(verifier.token_stats("tok2"))

    def test_drift_metrics_remain_available_as_descriptive_stats(self):
        verifier = LatencyVerifier(self._cfg())
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=190.0)
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=25.0)
        stats = verifier.token_stats("tok1")
        self.assertIsNotNone(stats)
        self.assertGreaterEqual(stats.drift_median_drop_ms, 0.0)  # type: ignore[union-attr]
        self.assertGreaterEqual(stats.drift_hit_rate_drop, 0.0)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
