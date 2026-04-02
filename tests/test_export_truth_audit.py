from __future__ import annotations

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from scripts.export_truth_audit import run_audit


class ExportTruthAuditTests(unittest.TestCase):
    def _write_zip(self, path: Path, members: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for member, content in members.items():
                zf.writestr(member, content)

    def test_export_truth_audit_passes_with_exact_manifest_and_clean_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_zip = root / "BRO_repo_snapshot_test.zip"
            run_zip = root / "BRO_run_evidence_test.zip"
            self._write_zip(repo_zip, {"src/app.py": "print('ok')\n"})
            self._write_zip(run_zip, {"logs_exec/paper_universal/run_manifest_x.json": "{\"run_id\":\"x\"}\n"})

            manifest = root / "BRO_export_manifest_test.txt"
            manifest.write_text(
                "\n".join(
                    [
                        f"- {repo_zip.name} ({repo_zip.stat().st_size} bytes)",
                        f"- {run_zip.name} ({run_zip.stat().st_size} bytes)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            # Ensure manifest mtime is newer than payloads.
            now = time.time() + 1.0
            os.utime(manifest, (now, now))

            result = run_audit(
                manifest_path=manifest,
                payload_paths=[repo_zip, run_zip],
            )
            self.assertTrue(bool(result.get("ok")), msg=result.get("findings"))
            self.assertEqual(int(result.get("finding_count", -1)), 0)

    def test_export_truth_audit_fails_on_forbidden_manifest_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = root / "BRO_repo_snapshot_test.zip"
            self._write_zip(payload, {"src/app.py": "print('ok')\n"})
            manifest = root / "BRO_export_manifest_test.txt"
            manifest.write_text(
                f"- {payload.name} ({payload.stat().st_size} bytes)\n"
                "- consultant bundle generated after this manifest\n",
                encoding="utf-8",
            )
            result = run_audit(manifest_path=manifest, payload_paths=[payload])
            self.assertFalse(bool(result.get("ok")))
            text = "\n".join(str(x) for x in result.get("findings", []))
            self.assertIn("manifest_forbidden_phrase_present:generated after this manifest", text)

    def test_export_truth_audit_fails_on_zip_noise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = root / "BRO_repo_snapshot_test.zip"
            self._write_zip(payload, {"prodesk/__pycache__/x.pyc": "bad"})
            manifest = root / "BRO_export_manifest_test.txt"
            manifest.write_text(f"- {payload.name} ({payload.stat().st_size} bytes)\n", encoding="utf-8")
            now = time.time() + 1.0
            os.utime(manifest, (now, now))
            result = run_audit(manifest_path=manifest, payload_paths=[payload])
            self.assertFalse(bool(result.get("ok")))
            self.assertTrue(any("zip_noise_present" in str(x) for x in result.get("findings", [])))

    def test_export_truth_audit_fails_when_manifest_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = root / "BRO_repo_snapshot_test.zip"
            self._write_zip(payload, {"src/app.py": "print('ok')\n"})
            manifest = root / "BRO_export_manifest_test.txt"
            manifest.write_text(f"- {payload.name} (999999 bytes)\n", encoding="utf-8")
            now = time.time() + 1.0
            os.utime(manifest, (now, now))
            result = run_audit(manifest_path=manifest, payload_paths=[payload])
            self.assertFalse(bool(result.get("ok")))
            self.assertTrue(any("manifest_payload_mismatch" in str(x) for x in result.get("findings", [])))

    def test_export_truth_audit_fails_when_manifest_is_older_than_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = root / "BRO_repo_snapshot_test.zip"
            self._write_zip(payload, {"src/app.py": "print('ok')\n"})
            manifest = root / "BRO_export_manifest_test.txt"
            manifest.write_text(f"- {payload.name} ({payload.stat().st_size} bytes)\n", encoding="utf-8")
            # Force payload newer than manifest.
            base = time.time()
            os.utime(manifest, (base, base))
            os.utime(payload, (base + 1.0, base + 1.0))
            result = run_audit(manifest_path=manifest, payload_paths=[payload])
            self.assertFalse(bool(result.get("ok")))
            self.assertTrue(any("manifest_mtime_before_payload" in str(x) for x in result.get("findings", [])))


if __name__ == "__main__":
    unittest.main()
