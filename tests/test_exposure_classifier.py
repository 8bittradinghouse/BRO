import unittest

from prodesk.exposure_classifier import (
    EXPOSURE_CLASS_DUST_ELIGIBLE,
    EXPOSURE_CLASS_DUST_QUARANTINED,
    EXPOSURE_CLASS_MEANINGFUL,
    ExposureClassifierConfig,
    classify_exposure,
    classify_exposure_fail_closed,
)


class ExposureClassifierTests(unittest.TestCase):
    @staticmethod
    def _cfg() -> ExposureClassifierConfig:
        return ExposureClassifierConfig(
            dust_shares_epsilon=0.25,
            dust_notional_usd_epsilon=1.0,
            dust_total_notional_usd_cap=5.0,
            dust_token_count_cap=4,
            dust_max_age_sec=900.0,
            dust_enter_consecutive_cycles=2,
            dust_clear_consecutive_cycles=2,
        )

    def test_classify_exposure_marks_meaningful_when_above_thresholds(self):
        out = classify_exposure(
            net_shares=2.0,
            cfg=self._cfg(),
            conservative_mark_price=0.75,
        )
        self.assertEqual(out.exposure_class, EXPOSURE_CLASS_MEANINGFUL)
        self.assertFalse(out.dust_share_eligible)
        self.assertFalse(out.dust_notional_eligible)
        self.assertFalse(out.dust_gate_eligible)
        self.assertEqual(out.dust_reason, "meaningful")

    def test_classify_exposure_marks_dust_eligible_when_bounded_and_clean(self):
        out = classify_exposure(
            net_shares=0.2,
            cfg=self._cfg(),
            conservative_mark_price=0.5,
            open_order_present=False,
            unresolved_lifecycle_obligation=False,
            dust_age_sec=5.0,
            aggregate_dust_notional_upper_bound_usd=0.5,
            aggregate_dust_token_count=1,
        )
        self.assertEqual(out.exposure_class, EXPOSURE_CLASS_DUST_ELIGIBLE)
        self.assertTrue(out.dust_share_eligible)
        self.assertTrue(out.dust_notional_eligible)
        self.assertTrue(out.dust_gate_eligible)
        self.assertEqual(out.dust_reason, "eligible")

    def test_classify_exposure_marks_quarantined_when_eligibility_blocked(self):
        out = classify_exposure(
            net_shares=0.2,
            cfg=self._cfg(),
            conservative_mark_price=0.5,
            open_order_present=True,
            unresolved_lifecycle_obligation=False,
            dust_age_sec=5.0,
            aggregate_dust_notional_upper_bound_usd=0.5,
            aggregate_dust_token_count=1,
        )
        self.assertEqual(out.exposure_class, EXPOSURE_CLASS_DUST_QUARANTINED)
        self.assertTrue(out.dust_share_eligible)
        self.assertTrue(out.dust_notional_eligible)
        self.assertFalse(out.dust_gate_eligible)
        self.assertEqual(out.dust_reason, "quarantined")

    def test_classify_exposure_fail_closed_defaults_to_meaningful_on_invalid_input(self):
        out = classify_exposure_fail_closed(
            net_shares=0.1,
            cfg=self._cfg(),
            conservative_mark_price=0.5,
            aggregate_dust_token_count="not-a-number",
        )
        self.assertEqual(out.exposure_class, EXPOSURE_CLASS_MEANINGFUL)
        self.assertFalse(out.dust_gate_eligible)
        self.assertEqual(out.dust_reason, "fail_closed")


if __name__ == "__main__":
    unittest.main()
