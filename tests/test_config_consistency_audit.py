import tempfile
import unittest
from pathlib import Path

from scripts.config_consistency_audit import run_audit


class ConfigConsistencyAuditTests(unittest.TestCase):
    def _write(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    def test_run_audit_passes_when_values_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = root / "a.yaml"
            secondary = root / "b.yaml"
            body = (
                "runtime:\n"
                "  paper_passive_touch_fill_enabled: true\n"
                "taker:\n"
                "  enabled: true\n"
            )
            self._write(primary, body)
            self._write(secondary, body)
            report = run_audit(
                primary,
                secondary,
                [
                    "runtime.paper_passive_touch_fill_enabled",
                    "taker.enabled",
                ],
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["finding_count"], 0)

    def test_run_audit_finds_missing_and_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = root / "a.yaml"
            secondary = root / "b.yaml"
            self._write(primary, "taker:\n  enabled: true\n")
            self._write(secondary, "taker:\n  enabled: false\n")
            report = run_audit(primary, secondary, ["taker.enabled", "taker.custom_missing"])
            self.assertFalse(report["ok"])
            findings = "\n".join(report["findings"])
            self.assertIn("value_mismatch:taker.enabled", findings)
            self.assertIn("primary_missing:taker.custom_missing", findings)


if __name__ == "__main__":
    unittest.main()
