import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.soak_evidence_window_gate import run_gate


class SoakEvidenceWindowGateTests(unittest.TestCase):
    def _write_run(
        self,
        root: Path,
        name: str,
        *,
        reliability_ok: bool,
        utilization_ok: bool,
        promotion_ok: bool,
        duration_minutes: float,
    ) -> None:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "soak_hardening.json").write_text(
            json.dumps(
                {
                    "lanes": {
                        "reliability": {"ok": reliability_ok},
                        "utilization": {"ok": utilization_ok},
                    }
                }
            ),
            encoding="utf-8",
        )
        (d / "promotion.json").write_text(json.dumps({"ok": promotion_ok}), encoding="utf-8")
        (d / "websocket_reliability.json").write_text(json.dumps({"ok": reliability_ok}), encoding="utf-8")
        (d / "nightly.json").write_text(json.dumps({"duration_minutes": duration_minutes}), encoding="utf-8")

    def test_gate_passes_on_sufficient_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_run(root, "soak45_1", reliability_ok=True, utilization_ok=True, promotion_ok=True, duration_minutes=45)
            self._write_run(root, "soak45_2", reliability_ok=True, utilization_ok=True, promotion_ok=True, duration_minutes=45)
            self._write_run(root, "soak45_3", reliability_ok=True, utilization_ok=False, promotion_ok=True, duration_minutes=45)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump(
                    {
                        "required_runs": 3,
                        "min_reliability_passes": 3,
                        "min_utilization_passes": 2,
                        "min_promotion_passes": 2,
                        "min_total_duration_minutes": 120,
                    }
                ),
                encoding="utf-8",
            )
            out = run_gate(reports_root=root, policy_path=policy)
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_gate_fails_on_insufficient_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_run(root, "soak45_1", reliability_ok=True, utilization_ok=True, promotion_ok=True, duration_minutes=45)
            policy = root / "policy.yaml"
            policy.write_text(yaml.safe_dump({"required_runs": 3}), encoding="utf-8")
            out = run_gate(reports_root=root, policy_path=policy)
            self.assertFalse(out["ok"])
            self.assertIn("BRO-2401", out["error_codes"])


if __name__ == "__main__":
    unittest.main()
