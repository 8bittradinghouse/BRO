import pathlib
import unittest
from unittest import mock

from prodesk.repo import current_git_commit, current_git_dirty


class RepoIdentityTests(unittest.TestCase):
    def test_current_git_commit_uses_env_override(self):
        with mock.patch.dict("os.environ", {"BRO_GIT_COMMIT": "abc123"}, clear=False):
            with mock.patch("subprocess.check_output", side_effect=RuntimeError("should not run")):
                self.assertEqual(current_git_commit(pathlib.Path(".")), "abc123")

    def test_current_git_dirty_uses_env_override_true(self):
        with mock.patch.dict("os.environ", {"BRO_GIT_DIRTY": "1"}, clear=False):
            with mock.patch("subprocess.check_output", side_effect=RuntimeError("should not run")):
                self.assertTrue(current_git_dirty(pathlib.Path(".")))

    def test_current_git_dirty_uses_env_override_false(self):
        with mock.patch.dict("os.environ", {"BRO_GIT_DIRTY": "0"}, clear=False):
            with mock.patch("subprocess.check_output", side_effect=RuntimeError("should not run")):
                self.assertFalse(current_git_dirty(pathlib.Path(".")))


if __name__ == "__main__":
    unittest.main()
