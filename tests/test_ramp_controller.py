import unittest

from prodesk.ramp_controller import SizeRampController


class RampControllerTests(unittest.TestCase):
    def test_upshift_when_window_healthy(self):
        ctrl = SizeRampController(
            {
                "enabled": True,
                "start_usd": 1.0,
                "step_usd": 1.0,
                "max_usd": 5.0,
                "evaluation_window_cycles": 2,
                "downshift_reject_ratio": 0.5,
                "downshift_stale_oracle_ratio": 0.5,
                "downshift_disarmed_ratio": 0.5,
                "downshift_reconcile_mismatch_ratio": 0.5,
                "disable_taker_on_breach": True,
            },
            base_target_usd=1.0,
        )
        snap_a = ctrl.observe_cycle(
            reject_ratio=0.0,
            stale_oracle_ratio=0.0,
            disarmed_ratio=0.0,
            reconcile_mismatch_ratio=0.0,
        )
        self.assertFalse(snap_a.changed)
        snap_b = ctrl.observe_cycle(
            reject_ratio=0.0,
            stale_oracle_ratio=0.0,
            disarmed_ratio=0.0,
            reconcile_mismatch_ratio=0.0,
        )
        self.assertTrue(snap_b.changed)
        self.assertEqual(snap_b.target_usd, 2.0)
        self.assertTrue(snap_b.taker_ramp_enabled)

    def test_downshift_and_disable_taker_on_breach(self):
        ctrl = SizeRampController(
            {
                "enabled": True,
                "start_usd": 1.0,
                "step_usd": 1.0,
                "max_usd": 5.0,
                "evaluation_window_cycles": 2,
                "downshift_reject_ratio": 0.2,
                "downshift_stale_oracle_ratio": 0.5,
                "downshift_disarmed_ratio": 0.5,
                "downshift_reconcile_mismatch_ratio": 0.5,
                "disable_taker_on_breach": True,
            },
            base_target_usd=4.0,
        )
        ctrl.observe_cycle(
            reject_ratio=1.0,
            stale_oracle_ratio=0.0,
            disarmed_ratio=0.0,
            reconcile_mismatch_ratio=0.0,
        )
        snap = ctrl.observe_cycle(
            reject_ratio=1.0,
            stale_oracle_ratio=0.0,
            disarmed_ratio=0.0,
            reconcile_mismatch_ratio=0.0,
        )
        self.assertEqual(snap.target_usd, 3.0)
        self.assertFalse(snap.taker_ramp_enabled)
        self.assertTrue(snap.changed)

    def test_target_clamped_at_start_floor(self):
        ctrl = SizeRampController(
            {
                "enabled": True,
                "start_usd": 1.0,
                "step_usd": 1.0,
                "max_usd": 2.0,
                "evaluation_window_cycles": 1,
                "downshift_reject_ratio": 0.1,
                "downshift_stale_oracle_ratio": 0.1,
                "downshift_disarmed_ratio": 0.1,
                "downshift_reconcile_mismatch_ratio": 0.1,
                "disable_taker_on_breach": True,
            },
            base_target_usd=1.0,
        )
        snap = ctrl.observe_cycle(
            reject_ratio=1.0,
            stale_oracle_ratio=1.0,
            disarmed_ratio=1.0,
            reconcile_mismatch_ratio=1.0,
        )
        self.assertEqual(snap.target_usd, 1.0)
        self.assertFalse(snap.taker_ramp_enabled)


if __name__ == "__main__":
    unittest.main()
