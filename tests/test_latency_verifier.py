import unittest

from prodesk.latency_verifier import STATE_ARMED, STATE_DISARMED, LatencyVerifier


class LatencyVerifierTests(unittest.TestCase):
    def _cfg(self):
        return {
            "enabled": True,
            "window_samples": 200,
            "min_samples": 20,
            "hit_threshold_ms": 120.0,
            "armed_min_median_ms": 120.0,
            "armed_min_hit_rate": 0.6,
            "probation_min_median_ms": 80.0,
            "probation_min_hit_rate": 0.45,
            "arm_consecutive_cycles": 2,
            "disarm_consecutive_cycles": 2,
        }

    def test_stable_lag_arms_verifier(self):
        verifier = LatencyVerifier(self._cfg())
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=180.0)
            snap = verifier.snapshot(active_tokens=["tok1"])
        self.assertEqual(snap.state, STATE_ARMED)
        self.assertTrue(verifier.token_is_verified("tok1"))

    def test_lag_collapse_disarms_after_hysteresis(self):
        verifier = LatencyVerifier(self._cfg())
        for _ in range(40):
            verifier.observe(token_id="tok1", lag_ms=180.0)
            verifier.snapshot(active_tokens=["tok1"])
        self.assertEqual(verifier.snapshot(active_tokens=["tok1"]).state, STATE_ARMED)

        # Collapse to low lag and ensure state drops from ARMED.
        state = STATE_ARMED
        for _ in range(40):
            verifier.observe(token_id="tok1", lag_ms=25.0)
            state = verifier.snapshot(active_tokens=["tok1"]).state
        self.assertNotEqual(state, STATE_ARMED)
        self.assertIn(state, {STATE_DISARMED, "probation"})

    def test_prune_tokens_removes_old_windows(self):
        verifier = LatencyVerifier(self._cfg())
        verifier.observe(token_id="tok1", lag_ms=150.0)
        verifier.observe(token_id="tok2", lag_ms=150.0)
        verifier.prune_tokens({"tok1"})
        self.assertIsNotNone(verifier.token_stats("tok1"))
        self.assertIsNone(verifier.token_stats("tok2"))

    def test_confidence_score_degrades_on_drift(self):
        cfg = self._cfg()
        cfg["drift_window_samples"] = 20
        verifier = LatencyVerifier(cfg)
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=190.0)
        baseline = verifier.token_stats("tok1")
        self.assertIsNotNone(baseline)
        baseline_score = baseline.confidence_score  # type: ignore[union-attr]
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=25.0)
        degraded = verifier.token_stats("tok1")
        self.assertIsNotNone(degraded)
        self.assertLess(degraded.confidence_score, baseline_score)  # type: ignore[union-attr]
        self.assertGreaterEqual(degraded.drift_median_drop_ms, 0.0)  # type: ignore[union-attr]

    def test_strong_absolute_signal_stays_verified_under_drift(self):
        cfg = self._cfg()
        cfg["drift_window_samples"] = 20
        verifier = LatencyVerifier(cfg)
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=190.0)
            verifier.snapshot(active_tokens=["tok1"])
        self.assertTrue(verifier.token_is_verified("tok1"))

        # The token still clears absolute armed thresholds comfortably even
        # though recent lag drifts lower. Drift should degrade confidence
        # without zeroing verification on a still-strong token.
        for _ in range(80):
            verifier.observe(token_id="tok1", lag_ms=130.0)
            verifier.snapshot(active_tokens=["tok1"])

        stats = verifier.token_stats("tok1")
        self.assertIsNotNone(stats)
        self.assertGreaterEqual(stats.median_lag_ms, verifier.armed_min_median_ms)  # type: ignore[union-attr]
        self.assertGreaterEqual(stats.hit_rate, verifier.armed_min_hit_rate)  # type: ignore[union-attr]
        self.assertTrue(verifier.token_is_verified("tok1"))


if __name__ == "__main__":
    unittest.main()
