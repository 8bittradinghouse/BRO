import unittest

from prodesk.operating_mode import (
    MODE_CAUTIOUS,
    MODE_MAKER_ONLY,
    MODE_NORMAL,
    MODE_SAFE_STOP,
    OperatingModeController,
)


class OperatingModeTests(unittest.TestCase):
    def _cfg(self):
        return {
            "enabled": True,
            "window_cycles": 10,
            "caution_stale_reject_ratio": 0.2,
            "maker_only_stale_reject_ratio": 0.4,
            "caution_outage_ratio": 0.2,
            "maker_only_outage_ratio": 0.5,
            "caution_error_ratio": 0.2,
            "maker_only_error_ratio": 0.5,
            "recover_healthy_cycles": 5,
            "safe_stop_severe_cycles": 4,
            "cautious_size_mult": 0.7,
            "maker_only_size_mult": 0.4,
            "cautious_spread_mult": 1.1,
            "maker_only_spread_mult": 1.25,
        }

    def test_moderate_faults_enter_cautious(self):
        ctrl = OperatingModeController(self._cfg())
        snap = None
        for _ in range(6):
            snap = ctrl.observe_cycle(
                risk_rejects=10,
                stale_rejects=3,
                outage_cycle=False,
                error_cycle=False,
            )
        self.assertIsNotNone(snap)
        self.assertEqual(snap.state, MODE_CAUTIOUS)  # type: ignore[union-attr]

    def test_severe_faults_eventually_safe_stop(self):
        ctrl = OperatingModeController(self._cfg())
        state = MODE_NORMAL
        for _ in range(20):
            snap = ctrl.observe_cycle(
                risk_rejects=10,
                stale_rejects=8,
                outage_cycle=True,
                error_cycle=True,
            )
            state = snap.state
        self.assertIn(state, {MODE_MAKER_ONLY, MODE_SAFE_STOP})
        self.assertEqual(ctrl.state, state)
        self.assertLessEqual(ctrl.size_multiplier(), 0.7)

    def test_recovery_returns_to_normal(self):
        ctrl = OperatingModeController(self._cfg())
        for _ in range(8):
            ctrl.observe_cycle(
                risk_rejects=10,
                stale_rejects=3,
                outage_cycle=False,
                error_cycle=False,
            )
        for _ in range(30):
            snap = ctrl.observe_cycle(
                risk_rejects=0,
                stale_rejects=0,
                outage_cycle=False,
                error_cycle=False,
            )
        self.assertEqual(snap.state, MODE_NORMAL)

    def test_single_stale_reject_does_not_escalate_without_min_evidence(self):
        ctrl = OperatingModeController(self._cfg())
        snap = ctrl.observe_cycle(
            risk_rejects=1,
            stale_rejects=1,
            outage_cycle=False,
            error_cycle=False,
        )
        self.assertEqual(snap.state, MODE_NORMAL)
        self.assertAlmostEqual(snap.stale_reject_ratio, 1.0, places=9)
        self.assertEqual(snap.risk_reject_count, 1)
        self.assertEqual(snap.stale_reject_count, 1)

    def test_stale_ratio_severe_triggers_when_min_evidence_is_met(self):
        cfg = self._cfg()
        cfg["caution_min_stale_reject_count"] = 1
        cfg["caution_min_risk_reject_count"] = 1
        cfg["maker_only_min_stale_reject_count"] = 2
        cfg["maker_only_min_risk_reject_count"] = 2
        ctrl = OperatingModeController(cfg)
        snap = ctrl.observe_cycle(
            risk_rejects=2,
            stale_rejects=2,
            outage_cycle=False,
            error_cycle=False,
        )
        self.assertEqual(snap.state, MODE_MAKER_ONLY)

    def test_non_stale_severe_signals_still_escalate_with_sparse_rejects(self):
        cfg = self._cfg()
        cfg["maker_only_min_stale_reject_count"] = 999
        cfg["maker_only_min_risk_reject_count"] = 999
        ctrl = OperatingModeController(cfg)
        snap = ctrl.observe_cycle(
            risk_rejects=0,
            stale_rejects=0,
            outage_cycle=True,
            error_cycle=False,
        )
        self.assertEqual(snap.state, MODE_MAKER_ONLY)


if __name__ == "__main__":
    unittest.main()
