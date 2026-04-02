import copy
import tempfile
import unittest
from pathlib import Path

from prodesk.config import DEFAULT_EXECUTION_CONFIG, validate_execution_config
from prodesk.security import run_security_checks


class SecurityHardeningTests(unittest.TestCase):
    def test_default_security_checks_pass_in_paper_mode(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        findings = run_security_checks(cfg, mode="paper")
        self.assertEqual(findings, [])

    def test_detects_insecure_scheme(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["market_data"]["clob_url"] = "http://clob.polymarket.com"
        findings = run_security_checks(cfg, mode="paper")
        self.assertTrue(any(x.startswith("security.insecure_scheme:market_data.clob_url:http") for x in findings))

    def test_detects_non_allowlisted_host(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["market_data"]["clob_url"] = "https://example.com"
        findings = run_security_checks(cfg, mode="paper")
        self.assertTrue(any(x.startswith("security.host_not_allowlisted:market_data.clob_url:example.com") for x in findings))

    def test_detects_private_host(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["chainlink"]["ws_url"] = "wss://localhost/ws"
        findings = run_security_checks(cfg, mode="paper")
        self.assertTrue(any(x.startswith("security.private_host_blocked:chainlink.ws_url:localhost") for x in findings))

    def test_detects_storage_paths_outside_allowed_roots(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td).resolve()
            cfg["storage"]["log_dir"] = str(outside / "logs")
            cfg["storage"]["state_path"] = str(outside / "state.json")
            findings = run_security_checks(cfg, mode="paper")
        self.assertTrue(any(x.startswith("security.path_outside_allowed_roots:storage.log_dir:") for x in findings))

    def test_detects_symlink_storage_paths(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["security"]["enforce_storage_roots"] = False
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            target = root / "real_logs"
            target.mkdir(parents=True, exist_ok=True)
            symlink_path = root / "logs_link"
            try:
                symlink_path.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unsupported in this environment")
            cfg["storage"]["log_dir"] = str(symlink_path)
            cfg["storage"]["state_path"] = str(root / "state.json")
            findings = run_security_checks(cfg, mode="paper")
        self.assertTrue(any(x.startswith("security.symlink_path_blocked:storage.log_dir:") for x in findings))

    def test_detects_live_metrics_not_local(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["metrics"]["enabled"] = True
        cfg["metrics"]["host"] = "0.0.0.0"
        findings = run_security_checks(cfg, mode="live")
        self.assertIn("security.metrics_bind_not_local:0.0.0.0", findings)

    def test_config_rejects_empty_allowlist_when_enforced(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["security"]["enforce_host_allowlist"] = True
        cfg["security"]["allowed_hosts"] = []
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)


if __name__ == "__main__":
    unittest.main()
