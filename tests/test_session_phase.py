from __future__ import annotations

import unittest

from prodesk.session_phase import (
    assert_valid_phase_transition,
    enforce_validation_phase,
    validation_surface_for_phase,
)


class SessionPhaseTests(unittest.TestCase):
    def test_phase_transition_requires_next_step(self) -> None:
        assert_valid_phase_transition("preflight", "start")
        with self.assertRaises(ValueError):
            assert_valid_phase_transition("preflight", "validate_active")

    def test_validation_surface_exposes_active_phase_contract(self) -> None:
        surface = validation_surface_for_phase("validate_active")
        self.assertIn("run_integrity_audit", surface.get("legal_validations", []))
        self.assertIn("websocket_reliability_gate", surface.get("actionable_failures", []))
        self.assertIn("nightly_soak_report", surface.get("informational_failures", []))

    def test_validation_surface_exposes_postrun_websocket_hardening(self) -> None:
        surface = validation_surface_for_phase("validate_postrun")
        self.assertIn("websocket_hardening_audit", surface.get("legal_validations", []))
        self.assertIn("websocket_hardening_audit", surface.get("actionable_failures", []))
        self.assertIn("outcome_truth_audit", surface.get("legal_validations", []))
        self.assertIn("outcome_truth_audit", surface.get("actionable_failures", []))

    def test_enforce_validation_phase_rejects_illegal_phase(self) -> None:
        phase = enforce_validation_phase(validation_name="readiness_gate", session_phase="validate_postrun")
        self.assertEqual(phase, "validate_postrun")
        with self.assertRaises(ValueError):
            enforce_validation_phase(validation_name="readiness_gate", session_phase="validate_active")


if __name__ == "__main__":
    unittest.main()
