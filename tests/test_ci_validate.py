import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import ci_validate


class CiValidateTests(unittest.TestCase):
    def test_run_step_applies_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td).resolve()
            with mock.patch("scripts.ci_validate.subprocess.run", return_value=mock.Mock(returncode=0)) as run_mock:
                ci_validate._run_step("unit", ["echo", "ok"], cwd=cwd)
        run_mock.assert_called_once()
        self.assertEqual(float(run_mock.call_args.kwargs["timeout"]), float(ci_validate.CI_VALIDATE_STEP_TIMEOUT_SEC))

    def test_run_step_timeout_exits_124(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td).resolve()
            with mock.patch(
                "scripts.ci_validate.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=1.0),
            ):
                with self.assertRaises(SystemExit) as ex:
                    ci_validate._run_step("unit", ["echo", "ok"], cwd=cwd)
        self.assertEqual(ex.exception.code, 124)

    def test_run_step_nonzero_propagates_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td).resolve()
            with mock.patch("scripts.ci_validate.subprocess.run", return_value=mock.Mock(returncode=3)):
                with self.assertRaises(SystemExit) as ex:
                    ci_validate._run_step("unit", ["echo", "ok"], cwd=cwd)
        self.assertEqual(ex.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
