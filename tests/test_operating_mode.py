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
            "caution_disarmed_ratio": 0.2,
            "maker_only_disarmed_ratio": 0.5,
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
                disarmed_cycle=False,
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
                disarmed_cycle=True,
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
                disarmed_cycle=False,
                error_cycle=False,
            )
        for _ in range(30):
            snap = ctrl.observe_cycle(
                risk_rejects=0,
                stale_rejects=0,
                outage_cycle=False,
                disarmed_cycle=False,
                error_cycle=False,
            )
        self.assertEqual(snap.state, MODE_NORMAL)


if __name__ == "__main__":
    unittest.main()
