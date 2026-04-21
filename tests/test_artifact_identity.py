import json
import tempfile
import unittest
from pathlib import Path

from prodesk.artifact_identity import build_artifact_identity


class ArtifactIdentityTests(unittest.TestCase):
    def test_build_artifact_identity_reports_manifest_missing(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            identity = build_artifact_identity(log_dir=log_dir, run_id="rid-missing")

        self.assertEqual(identity.get("run_id"), "rid-missing")
        self.assertFalse(bool(identity.get("manifest_present")))
        self.assertEqual(identity.get("manifest_load_error"), "manifest_missing")

    def test_build_artifact_identity_reports_manifest_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-invalid"
            (log_dir / f"run_manifest_{run_id}.json").write_text("{broken", encoding="utf-8")

            identity = build_artifact_identity(log_dir=log_dir, run_id=run_id)

        self.assertTrue(bool(identity.get("manifest_present")))
        self.assertIn("manifest_invalid_json:", str(identity.get("manifest_load_error") or ""))

    def test_build_artifact_identity_reads_valid_manifest_fields(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "rid-valid"
            manifest = {
                "run_id": run_id,
                "manifest_schema_version": 2,
                "config_fingerprint_sha256": "a" * 64,
                "code_fingerprint_sha256": "b" * 64,
                "config_source_path": "/tmp/config.yaml",
                "config_source_sha256": "c" * 64,
                "runtime_identity": {
                    "dependency_lock_sha256": "d" * 64,
                    "git_commit": "deadbeef",
                    "git_dirty": False,
                    "docker_image_hash": "sha256:abc",
                    "profile_name": "paper_universal",
                },
                "config": {"profile": {"name": "paper_universal"}},
            }
            (log_dir / f"run_manifest_{run_id}.json").write_text(json.dumps(manifest), encoding="utf-8")

            identity = build_artifact_identity(log_dir=log_dir, run_id=run_id)

        self.assertTrue(bool(identity.get("manifest_present")))
        self.assertEqual(identity.get("manifest_load_error"), "")
        self.assertEqual(identity.get("profile_name"), "paper_universal")
        self.assertEqual(identity.get("git_commit"), "deadbeef")
        self.assertEqual(identity.get("config_fingerprint_sha256"), "a" * 64)
        self.assertEqual(identity.get("code_fingerprint_sha256"), "b" * 64)


if __name__ == "__main__":
    unittest.main()
