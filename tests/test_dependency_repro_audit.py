import json
import tempfile
import unittest
from pathlib import Path

from scripts.dependency_repro_audit import run_audit


class DependencyReproAuditTests(unittest.TestCase):
    _DIGEST = "sha256:" + ("a" * 64)

    def test_audit_refresh_then_check_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = root / "requirements.txt"
            docker = root / "Dockerfile"
            lock = root / "lock.json"
            req.write_text("requests==2.32.5\n", encoding="utf-8")
            docker.write_text(
                f"FROM python:3.11-slim@{self._DIGEST}\nRUN pip install --no-cache-dir -r /app/requirements.txt\n",
                encoding="utf-8",
            )

            refresh = run_audit(
                requirements_path=req,
                dockerfile_path=docker,
                lock_manifest_path=lock,
                refresh=True,
            )
            self.assertTrue(refresh["ok"], msg=refresh["findings"])
            check = run_audit(
                requirements_path=req,
                dockerfile_path=docker,
                lock_manifest_path=lock,
                refresh=False,
            )
            self.assertTrue(check["ok"], msg=check["findings"])

    def test_audit_fails_on_unpinned_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = root / "requirements.txt"
            docker = root / "Dockerfile"
            lock = root / "lock.json"
            req.write_text("requests>=2.0.0\n", encoding="utf-8")
            docker.write_text(
                f"FROM python:3.11-slim@{self._DIGEST}\nRUN pip install --no-cache-dir -r /app/requirements.txt\n",
                encoding="utf-8",
            )
            lock.write_text(json.dumps({"schema_version": 1, "files": {str(req): "x", str(docker): "y"}}), encoding="utf-8")

            result = run_audit(
                requirements_path=req,
                dockerfile_path=docker,
                lock_manifest_path=lock,
                refresh=False,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-1902", result["error_codes"])

    def test_manifest_preview_is_repo_relative_not_absolute_host_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "portable_repo"
            repo.mkdir(parents=True, exist_ok=True)
            req = repo / "requirements.txt"
            docker = repo / "Dockerfile"
            lock = repo / "ops" / "dependency_lock.json"
            req.write_text("requests==2.32.5\n", encoding="utf-8")
            docker.write_text(
                f"FROM python:3.11-slim@{self._DIGEST}\nRUN pip install --no-cache-dir -r /app/requirements.txt\n",
                encoding="utf-8",
            )

            refresh = run_audit(
                requirements_path=req,
                dockerfile_path=docker,
                lock_manifest_path=lock,
                refresh=True,
            )
            self.assertTrue(refresh["ok"], msg=refresh["findings"])
            files = dict(refresh.get("manifest_preview", {}).get("files", {}))
            self.assertIn("requirements.txt", files)
            self.assertIn("Dockerfile", files)
            self.assertFalse(any("/home/odah/bro/base" in key for key in files.keys()))

    def test_audit_fails_when_base_image_not_digest_pinned(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = root / "requirements.txt"
            docker = root / "Dockerfile"
            lock = root / "lock.json"
            req.write_text("requests==2.32.5\n", encoding="utf-8")
            docker.write_text("FROM python:3.11-slim\nRUN pip install --no-cache-dir -r /app/requirements.txt\n", encoding="utf-8")
            lock.write_text(json.dumps({"schema_version": 2, "files": {str(req): "x", str(docker): "y"}}), encoding="utf-8")

            result = run_audit(
                requirements_path=req,
                dockerfile_path=docker,
                lock_manifest_path=lock,
                refresh=False,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-1904", result["error_codes"])


if __name__ == "__main__":
    unittest.main()
