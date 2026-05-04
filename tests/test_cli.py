import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prodesk import cli
from prodesk.config import load_execution_config


class BroCtlTests(unittest.TestCase):
    @staticmethod
    def _locked_paper_cfg(*, profile_name: str = "paper_universal") -> dict:
        fingerprint = "a" * 64
        return {
            "mode": "paper",
            "profile": {"name": profile_name},
            "runtime": {
                "paper_enforce_setup_lock": True,
                "paper_expected_profile_name": profile_name,
                "paper_expected_config_fingerprint_sha256": fingerprint,
            },
            "_meta": {"effective_config_sha256": fingerprint},
        }

    def test_prestart_strips_double_dash_and_runs_from_repo_root(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td).resolve()
            calls = []

            def _fake_run(cmd, check=False, cwd=None, timeout=None):  # noqa: ANN001
                calls.append({"cmd": list(cmd), "cwd": cwd, "check": check})
                return mock.Mock(returncode=0)

            with mock.patch("prodesk.cli.resolve_repo_root", return_value=repo_root), mock.patch(
                "subprocess.run", side_effect=_fake_run
            ), mock.patch(
                "sys.argv",
                ["broctl", "prestart", "--", "--allow-kill-switch", "--allow-guard-file"],
            ):
                with self.assertRaises(SystemExit) as ex:
                    cli.main()
                self.assertEqual(ex.exception.code, 0)

            self.assertEqual(len(calls), 1)
            cmd = calls[0]["cmd"]
            self.assertEqual(cmd[0], cli.sys.executable)
            self.assertIn("scripts/prestart_gate.py", cmd[1])
            self.assertNotIn("--", cmd)
            self.assertEqual(calls[0]["cwd"], str(repo_root))

    def test_ci_alias_maps_to_ci_validate(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td).resolve()
            calls = []

            def _fake_run(cmd, check=False, cwd=None, timeout=None):  # noqa: ANN001
                calls.append({"cmd": list(cmd), "cwd": cwd, "check": check})
                return mock.Mock(returncode=0)

            with mock.patch("prodesk.cli.resolve_repo_root", return_value=repo_root), mock.patch(
                "subprocess.run", side_effect=_fake_run
            ), mock.patch("sys.argv", ["broctl", "ci", "--", "--skip-pytest"]):
                with self.assertRaises(SystemExit) as ex:
                    cli.main()
                self.assertEqual(ex.exception.code, 0)

            self.assertEqual(len(calls), 1)
            cmd = calls[0]["cmd"]
            self.assertIn("scripts/ci_validate.py", cmd[1])
            self.assertIn("--skip-pytest", cmd)
            self.assertEqual(calls[0]["cwd"], str(repo_root))

    def test_explicit_config_does_not_duplicate_default_config_arg(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td).resolve()
            calls = []

            def _fake_run(cmd, check=False, cwd=None, timeout=None):  # noqa: ANN001
                calls.append({"cmd": list(cmd), "cwd": cwd, "check": check})
                return mock.Mock(returncode=0)

            with mock.patch("prodesk.cli.resolve_repo_root", return_value=repo_root), mock.patch(
                "subprocess.run", side_effect=_fake_run
            ), mock.patch(
                "sys.argv",
                ["broctl", "prestart", "--", "--config", "configs/profiles/paper_universal.yaml"],
            ):
                with self.assertRaises(SystemExit) as ex:
                    cli.main()
                self.assertEqual(ex.exception.code, 0)

            self.assertEqual(len(calls), 1)
            cmd = calls[0]["cmd"]
            self.assertEqual(cmd.count("--config"), 1)

    def test_paper_stress_command_is_not_registered(self):
        with self.assertRaises(SystemExit) as ex, mock.patch("sys.argv", ["broctl", "paper-stress"]):
            cli.main()
        self.assertEqual(ex.exception.code, 2)

    def test_paper_discipline_command_is_not_registered(self):
        with self.assertRaises(SystemExit) as ex, mock.patch("sys.argv", ["broctl", "paper-discipline"]):
            cli.main()
        self.assertEqual(ex.exception.code, 2)

    def test_paper_profile_command_is_not_registered(self):
        with self.assertRaises(SystemExit) as ex, mock.patch("sys.argv", ["broctl", "paper-profile"]):
            cli.main()
        self.assertEqual(ex.exception.code, 2)

    def test_subprocess_timeout_exits_with_124(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td).resolve()

            def _fake_run(cmd, check=False, cwd=None, timeout=None):  # noqa: ANN001
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout if timeout is not None else 1.0)

            with mock.patch("prodesk.cli.resolve_repo_root", return_value=repo_root), mock.patch(
                "subprocess.run", side_effect=_fake_run
            ), mock.patch("sys.argv", ["broctl", "prestart"]):
                with self.assertRaises(SystemExit) as ex:
                    cli.main()
                self.assertEqual(ex.exception.code, 124)


class ProfilePathResolutionTests(unittest.TestCase):
    def test_paper_universal_paths_resolve_to_repo_runtime_dirs(self):
        repo_root = Path(__file__).resolve().parents[1]
        cfg_path = repo_root / "configs/profiles/paper_universal.yaml"
        with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "0"}, clear=False):
            host_cfg = load_execution_config(cfg_path)
        with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "1"}, clear=False):
            docker_cfg = load_execution_config(cfg_path)
        self.assertTrue(str(host_cfg["storage"]["log_dir"]).endswith("/logs/paper_universal"))
        self.assertTrue(str(host_cfg["storage"]["state_path"]).endswith("/data/paper_universal/state.json"))
        self.assertTrue(str(docker_cfg["storage"]["log_dir"]).endswith("/logs/paper_universal"))
        self.assertTrue(str(docker_cfg["storage"]["state_path"]).endswith("/data/paper_universal/state.json"))

    def test_paper_setup_lock_fingerprint_is_stable_across_host_and_docker_modes(self):
        repo_root = Path(__file__).resolve().parents[1]
        cfg_path = repo_root / "configs/profiles/paper_universal.yaml"
        with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "0"}, clear=False):
            host_cfg = load_execution_config(cfg_path)
        with mock.patch.dict("os.environ", {"BRO_DOCKER_MODE": "1"}, clear=False):
            docker_cfg = load_execution_config(cfg_path)
        host_fp = str((host_cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
        docker_fp = str((docker_cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
        self.assertEqual(host_fp, docker_fp)


if __name__ == "__main__":
    unittest.main()
