import os
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

if os.getenv("BRO_ENABLE_LEGACY_SIMULATOR_TESTS", "").strip() != "1":
    raise unittest.SkipTest("legacy simulator audit tests are disabled by default")

from scripts.sim_harness_audit import run_audit


class SimHarnessAuditTests(unittest.TestCase):
    def test_sim_harness_audit_passes_on_repo_config(self):
        result = run_audit(
            config_path=Path("configs/profiles/paper_universal.yaml"),
            steps=20,
            dt_sec=1.0,
        )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        self.assertEqual(result["finding_count"], 0)

    def test_sim_harness_audit_rejects_missing_config(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.yaml"
            with self.assertRaises(FileNotFoundError):
                run_audit(config_path=missing, steps=10, dt_sec=1.0)

    def test_sim_harness_script_executes_directly(self):
        cmd = [
            sys.executable,
            "scripts/sim_harness_audit.py",
            "--config",
            "configs/profiles/paper_universal.yaml",
            "--steps",
            "5",
            "--dt-sec",
            "1.0",
        ]
        proc = subprocess.run(cmd, cwd="/home/odah/bro/base", capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")


if __name__ == "__main__":
    unittest.main()
