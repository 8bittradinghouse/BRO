import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from scripts.alert_profile_audit import run_audit


class AlertProfileAuditTests(unittest.TestCase):
    def _write_cfg(self, root: Path) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        # Canonical doctrine fixtures must not set both doctrine and legacy sniper freshness keys.
        cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
        cfg["targets"]["token_ids"] = ["tok1"]
        path = root / "execution_config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    def test_alert_profile_audit_passes_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(Path(td))
            result = run_audit(config_path=cfg_path)
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_alert_profile_audit_flags_invalid_threshold_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            cfg["alerts"]["warn_thresholds"]["error_ratio"] = 0.30
            cfg["alerts"]["page_thresholds"]["error_ratio"] = 0.20
            cfg["alerts"]["auto_stop_thresholds"]["error_ratio"] = 0.25
            cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            result = run_audit(config_path=cfg_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("alerts:error_ratio:threshold_order_invalid" in x for x in result["findings"]))


if __name__ == "__main__":
    unittest.main()
