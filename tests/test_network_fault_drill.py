import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts.network_fault_drill import run_audit


class NetworkFaultDrillTests(unittest.TestCase):
    def test_audit_passes_when_all_required_recent_drills_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = dt.datetime.now(dt.timezone.utc)
            faults = ("dns_failure", "packet_loss", "latency_spike", "endpoint_flap")
            for idx, fault in enumerate(faults, start=1):
                payload = {
                    "fault_type": fault,
                    "ts_utc": (now - dt.timedelta(minutes=idx)).isoformat().replace("+00:00", "Z"),
                    "ok": True,
                }
                (root / f"drill_{idx}.json").write_text(json.dumps(payload), encoding="utf-8")

            result = run_audit(drills_dir=root, max_age_days=1.0)
            self.assertTrue(result["ok"], msg=result["findings"])

    def test_audit_fails_when_fault_type_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = dt.datetime.now(dt.timezone.utc)
            for idx, fault in enumerate(("dns_failure", "packet_loss", "latency_spike"), start=1):
                payload = {
                    "fault_type": fault,
                    "ts_utc": (now - dt.timedelta(minutes=idx)).isoformat().replace("+00:00", "Z"),
                    "ok": True,
                }
                (root / f"drill_{idx}.json").write_text(json.dumps(payload), encoding="utf-8")

            result = run_audit(drills_dir=root, max_age_days=1.0)
            self.assertFalse(result["ok"])
            self.assertIn("missing_recent_ok_drill:endpoint_flap", result["findings"])


if __name__ == "__main__":
    unittest.main()
