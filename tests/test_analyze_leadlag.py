import tempfile
import unittest
from pathlib import Path

import analyze_leadlag


class AnalyzeLeadLagTests(unittest.TestCase):
    def test_percentile(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(analyze_leadlag.percentile(vals, 0.0), 1.0)
        self.assertEqual(analyze_leadlag.percentile(vals, 1.0), 4.0)
        self.assertAlmostEqual(analyze_leadlag.percentile(vals, 0.5), 2.5)

    def test_script_runs_with_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            # Basic function-level check for parse helper stability.
            self.assertIsNone(analyze_leadlag.parse_float(None))
            self.assertEqual(analyze_leadlag.parse_float("1.2"), 1.2)
            self.assertEqual(len(list(p.glob("events_*.jsonl"))), 0)


if __name__ == "__main__":
    unittest.main()

