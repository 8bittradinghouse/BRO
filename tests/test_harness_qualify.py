import json
import tempfile
import unittest
from pathlib import Path

from scripts.harness_qualify import _load_policy, run_gate


class HarnessQualifyTests(unittest.TestCase):
    @staticmethod
    def _policy_with_overrides(**kwargs):
        base = _load_policy(Path("ops/harness_policy.yaml"))
        for section, values in kwargs.items():
            if isinstance(values, dict) and isinstance(base.get(section), dict):
                base[section].update(values)
            else:
                base[section] = values
        return base

    def test_harness_qualify_passes_with_skips(self):
        result = run_gate(
            config_path=Path("configs/profiles/paper_universal.yaml"),
            log_dir=Path("logs_exec/paper_universal"),
            run_id="",
            policy=self._policy_with_overrides(
                sim_harness={"enabled": False},
                fault_drill={"enabled": False},
                paper_harness={"run_integrity_enabled": False},
            ),
            force_skip_run_integrity=False,
            force_skip_sim_harness=False,
            force_skip_fault_drill=False,
        )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        self.assertEqual(result["finding_count"], 0)

    def test_harness_qualify_fails_required_fault_drill_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            drills_dir = Path(td) / "drills"
            drills_dir.mkdir(parents=True, exist_ok=True)
            # No drill evidence files -> should fail when fault drill check is required.
            result = run_gate(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=Path("logs_exec/paper_universal"),
                run_id="",
                policy=self._policy_with_overrides(
                    sim_harness={"enabled": False},
                    paper_harness={"run_integrity_enabled": False},
                    fault_drill={"enabled": True, "drills_dir": str(drills_dir), "max_age_days": 7.0},
                ),
                force_skip_run_integrity=False,
                force_skip_sim_harness=False,
                force_skip_fault_drill=False,
            )
        self.assertFalse(result["ok"])
        self.assertTrue(any(x.startswith("missing_recent_ok_drill:") for x in result["findings"]))

    def test_harness_qualify_passes_with_recent_drill_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            drills_dir = Path(td) / "drills"
            drills_dir.mkdir(parents=True, exist_ok=True)
            ts = "2099-01-01T00:00:00Z"
            faults = ("dns_failure", "packet_loss", "latency_spike", "endpoint_flap")
            for idx, fault in enumerate(faults, start=1):
                payload = {"ts_utc": ts, "fault_type": fault, "ok": True}
                (drills_dir / f"drill_{idx}.json").write_text(json.dumps(payload), encoding="utf-8")
            result = run_gate(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=Path("logs_exec/paper_universal"),
                run_id="",
                policy=self._policy_with_overrides(
                    sim_harness={"enabled": False},
                    paper_harness={"run_integrity_enabled": False},
                    fault_drill={"enabled": True, "drills_dir": str(drills_dir), "max_age_days": 36500.0},
                ),
                force_skip_run_integrity=False,
                force_skip_sim_harness=False,
                force_skip_fault_drill=False,
            )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        self.assertEqual(result["finding_count"], 0)

    def test_cli_force_skip_overrides_policy_enabled_fault_drill(self):
        with tempfile.TemporaryDirectory() as td:
            drills_dir = Path(td) / "drills"
            drills_dir.mkdir(parents=True, exist_ok=True)
            result = run_gate(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=Path("logs_exec/paper_universal"),
                run_id="",
                policy=self._policy_with_overrides(
                    sim_harness={"enabled": False},
                    paper_harness={"run_integrity_enabled": False},
                    fault_drill={"enabled": True, "drills_dir": str(drills_dir), "max_age_days": 7.0},
                ),
                force_skip_run_integrity=False,
                force_skip_sim_harness=False,
                force_skip_fault_drill=True,
            )
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
