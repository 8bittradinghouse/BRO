import unittest

from scripts.critical_path_gate import run_gate


class CriticalPathGateTests(unittest.TestCase):
    def test_gate_passes_ci_fixture(self):
        result = run_gate()
        self.assertTrue(result["ok"], msg=result["findings"])


if __name__ == "__main__":
    unittest.main()
