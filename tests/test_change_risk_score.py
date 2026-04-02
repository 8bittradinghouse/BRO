import unittest

from scripts.change_risk_score import run_score


class ChangeRiskScoreTests(unittest.TestCase):
    def test_high_risk_for_critical_paths(self):
        result = run_score(["executor.py", "prodesk/gateway.py"])
        self.assertIn(result["risk_level"], {"high", "critical"})
        self.assertTrue(result["requires_extra_gates"])

    def test_low_risk_for_single_doc_change(self):
        result = run_score(["README.md"])
        self.assertEqual(result["risk_level"], "low")
        self.assertFalse(result["requires_extra_gates"])


if __name__ == "__main__":
    unittest.main()
