import tempfile
import unittest
from pathlib import Path

from scripts.rollback_drill import run_drill


class RollbackDrillTests(unittest.TestCase):
    def _write_bundle(self, root: Path, *, include_state: bool = True, include_manifest: bool = True) -> Path:
        backup_dir = root / "backups"
        log_dir = root / "logs_exec"
        backup_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "status_2026-03-07.jsonl").write_text('{"ok":1}\n', encoding="utf-8")
        if include_manifest:
            (log_dir / "run_manifest_demo.json").write_text('{"run_id":"demo"}\n', encoding="utf-8")
        state_path = root / "state.json"
        if include_state:
            state_path.write_text('{"state":"ok"}\n', encoding="utf-8")

        from scripts.backup_daily import main as backup_main  # local import to avoid CLI side effects at import time
        import sys

        argv = sys.argv[:]
        try:
            sys.argv = [
                "backup_daily.py",
                "--log-dir",
                str(log_dir),
                "--state-path",
                str(state_path),
                "--out-dir",
                str(backup_dir),
            ]
            backup_main()
        finally:
            sys.argv = argv
        return backup_dir

    def test_rollback_drill_passes_on_valid_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            backup_dir = self._write_bundle(Path(td), include_state=True, include_manifest=True)
            report = run_drill(backup_dir=backup_dir, require_state=True, require_manifest=True)
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertGreaterEqual(int(report.get("log_file_count", 0)), 1)
        self.assertTrue(bool(report.get("state_present")))
        self.assertGreaterEqual(int(report.get("manifest_count", 0)), 1)

    def test_rollback_drill_fails_when_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            backup_dir = self._write_bundle(Path(td), include_state=True, include_manifest=True)
            bundle = sorted(backup_dir.glob("bro_backup_*.tar.gz"))[-1]
            bundle.write_bytes(bundle.read_bytes() + b"tamper")
            report = run_drill(backup_dir=backup_dir, require_state=False, require_manifest=False)
        self.assertFalse(report["ok"])
        self.assertTrue(any("bundle_hash_mismatch" in x for x in report["findings"]))


if __name__ == "__main__":
    unittest.main()
