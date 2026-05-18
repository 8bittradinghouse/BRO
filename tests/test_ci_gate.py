import unittest
import json
from pathlib import Path
from unittest import mock

from scripts import ci_gate


class CiGateTests(unittest.TestCase):
    def test_compileall_step_stays_on_canonical_paths(self):
        run_steps = []

        def _record_step(name, cmd):  # noqa: ANN001
            run_steps.append((name, list(cmd)))
            if "--out" not in cmd:
                return
            out_path = Path(cmd[cmd.index("--out") + 1])
            payload = {}
            if name == "readiness_gate":
                payload = {"highest_passing_stage": "paper"}
            elif name == "nightly_soak_report":
                payload = {
                    "schema_version": 2,
                    "quote_uptime_ratio": 1.0,
                    "error_rows": 0.0,
                    "execution_quality": {"capture_minus_adverse": 0.0},
                }
            elif name == "reconcile_daily":
                payload = {"schema_version": 3, "verification_level": "paper_wallet_simulation_verified"}
            elif name == "desk_trade_report":
                payload = {"schema_version": 1}
            elif name == "promotion_evidence_gate":
                payload = {"ok": True}
            elif name == "paper_live_parity":
                payload = {"ok": True}
            else:
                payload = {"ok": True}
            out_path.write_text(json.dumps(payload), encoding="utf-8")

        with mock.patch("scripts.ci_gate.subprocess.run", return_value=mock.Mock(returncode=0)), mock.patch(
            "scripts.ci_gate.run_step", side_effect=_record_step
        ), mock.patch(
            "scripts.ci_gate.load_execution_config",
            return_value={"storage": {}, "runtime": {}, "_meta": {}},
        ), mock.patch("sys.argv", ["ci_gate.py"]):
            ci_gate.main()

        compile_cmd = None
        for name, cmd in run_steps:
            if name == "compileall":
                compile_cmd = cmd
                break

        self.assertIsNotNone(compile_cmd)
        self.assertEqual(compile_cmd[:3], [mock.ANY, "-m", "compileall"])
        self.assertIn("executor.py", compile_cmd)
        self.assertIn("prodesk", compile_cmd)
        self.assertIn("scripts", compile_cmd)
        self.assertIn("tests", compile_cmd)
        self.assertNotIn("simulator.py", compile_cmd)


if __name__ == "__main__":
    unittest.main()
