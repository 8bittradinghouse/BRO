import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.paths import normalize_execution_paths, validate_runtime_write_paths


class PathNormalizationTests(unittest.TestCase):
    def test_docker_mode_normalizes_relative_paths_to_logs_and_data(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["asset"]["symbol"] = "SOL"
        cfg["storage"]["log_dir"] = "./logs_exec_custom"
        cfg["storage"]["state_path"] = "./state/state.json"
        cfg["runtime"]["guard_stop_file"] = "./guard/stop.txt"
        cfg["ramp"]["reconcile_status_path"] = "./ramp/reconcile.json"
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "execution_config.yaml"
            config_path.write_text("mode: live\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "1"}, clear=False):
                out = normalize_execution_paths(cfg, config_path=config_path)
        self.assertEqual(out["storage"]["log_dir"], "/logs/logs_exec_custom")
        self.assertEqual(out["storage"]["state_path"], "/data/state/state.json")
        self.assertEqual(out["runtime"]["guard_stop_file"], "/logs/guard/stop.txt")
        self.assertEqual(out["ramp"]["reconcile_status_path"], "/logs/ramp/reconcile.json")

    def test_docker_mode_applies_defaults_when_paths_missing(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["asset"]["symbol"] = "XRP"
        cfg["storage"]["log_dir"] = ""
        cfg["storage"]["state_path"] = ""
        cfg["runtime"]["guard_stop_file"] = ""
        cfg["ramp"]["reconcile_status_path"] = ""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "execution_config.yaml"
            config_path.write_text("mode: paper\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "1"}, clear=False):
                out = normalize_execution_paths(cfg, config_path=config_path)
        self.assertEqual(out["storage"]["log_dir"], "/logs/xrp_paper")
        self.assertEqual(out["storage"]["state_path"], "/data/xrp_paper/state.json")
        self.assertEqual(out["runtime"]["guard_stop_file"], "/logs/xrp_paper/guard_stop.txt")
        self.assertEqual(out["ramp"]["reconcile_status_path"], "/logs/xrp_paper/reconcile_latest.json")

    def test_validate_runtime_write_paths_reports_non_writable_target(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            blocked = root / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            cfg["storage"]["log_dir"] = str(blocked)
            cfg["storage"]["state_path"] = str(root / "state" / "state.json")
            cfg["runtime"]["guard_stop_file"] = str(root / "guard" / "guard_stop.txt")
            cfg["ramp"]["reconcile_status_path"] = str(root / "reconcile" / "latest.json")
            findings = validate_runtime_write_paths(cfg)
        self.assertTrue(any(x.startswith("path_not_writable:storage.log_dir:") for x in findings))

    def test_host_mode_normalizes_security_storage_roots_relative_to_config(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["security"]["allowed_storage_roots"] = ["../logs_exec_btc_paper"]
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "configs"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "execution_config.yaml"
            config_path.write_text("mode: paper\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "0"}, clear=False):
                out = normalize_execution_paths(cfg, config_path=config_path)
        self.assertEqual(
            out["security"]["allowed_storage_roots"],
            [str((config_dir / "../logs_exec_btc_paper").resolve())],
        )

    def test_host_mode_tolerates_container_mount_paths_for_tooling_loads(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["storage"]["log_dir"] = "/logs/btc_paper"
        cfg["storage"]["state_path"] = "/data/btc_paper/state.json"
        cfg["runtime"]["guard_stop_file"] = "/logs/btc_paper/guard_stop.txt"
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "execution_config.yaml"
            config_path.write_text("mode: paper\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "0"}, clear=False):
                out = normalize_execution_paths(cfg, config_path=config_path)
        self.assertEqual(out["storage"]["log_dir"], "/logs/btc_paper")
        self.assertEqual(out["storage"]["state_path"], "/data/btc_paper/state.json")


if __name__ == "__main__":
    unittest.main()
