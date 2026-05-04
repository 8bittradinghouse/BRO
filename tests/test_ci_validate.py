import subprocess
import tempfile
import unittest
from io import StringIO
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

    def test_help_text_does_not_mention_legacy_simulator(self):
        stdout = StringIO()
        with mock.patch("sys.argv", ["ci_validate.py", "--help"]), mock.patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as ex:
                ci_validate.main()
        self.assertEqual(ex.exception.code, 0)
        text = stdout.getvalue()
        self.assertIn("Run backstage harness qualification", text)
        self.assertNotIn("legacy simulator", text)


if __name__ == "__main__":
    unittest.main()
