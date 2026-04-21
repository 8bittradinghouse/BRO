import tempfile
import unittest
from pathlib import Path

from scripts.money_harness_exception_audit import run_audit


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class MoneyHarnessExceptionAuditTests(unittest.TestCase):
    def test_allows_explicit_broad_exception_waivers_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write(root / "executor.py", "def noop():\n    return 1\n")
            _write(
                root / "prodesk" / "wallet" / "wallet_controller.py",
                """
def _emit(self):
    try:
        self._event_logger("x", {})
    except Exception:
        # Wallet authority must remain functional even if telemetry emission fails.
        return
""".strip()
                + "\n",
            )
            _write(
                root / "scripts" / "canonical_paper_session.py",
                """
def run(self):
    try:
        phase()
    except BaseException as exc:
        self._finalize_failure_closeout(exc)
        raise
""".strip()
                + "\n",
            )
            result = run_audit(repo_root=root)
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_flags_non_waived_broad_exception(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write(root / "executor.py", "def noop():\n    return 1\n")
            _write(root / "prodesk" / "example.py", "def f():\n    try:\n        return 1\n    except Exception:\n        return 0\n")
            _write(root / "scripts" / "stub.py", "def g():\n    return 1\n")
            result = run_audit(repo_root=root)
        self.assertFalse(result["ok"])
        self.assertTrue(any(str(item.get("tag")) == "broad_exception" for item in result["findings"]))

    def test_flags_subprocess_without_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write(root / "executor.py", "def noop():\n    return 1\n")
            _write(root / "prodesk" / "ok.py", "def f():\n    return True\n")
            _write(root / "scripts" / "run.py", "import subprocess\n\ndef f():\n    return subprocess.run(['echo', 'x'])\n")
            result = run_audit(repo_root=root)
        self.assertFalse(result["ok"])
        self.assertTrue(any(str(item.get("tag")) == "subprocess_without_timeout" for item in result["findings"]))


if __name__ == "__main__":
    unittest.main()
