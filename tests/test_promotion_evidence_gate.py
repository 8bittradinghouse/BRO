import json
import tempfile
import unittest
from pathlib import Path

from scripts.promotion_evidence_gate import run_gate


class PromotionEvidenceGateTests(unittest.TestCase):
    def _good_identity(
        self,
        *,
        run_id: str = "run-a",
        config_fingerprint_sha256: str = "a" * 64,
        code_fingerprint_sha256: str = "b" * 64,
        git_commit: str = "deadbeef",
        profile_name: str = "paper_universal",
        manifest_present: bool = True,
        manifest_load_error: str = "",
    ) -> dict:
        return {
            "run_id": run_id,
            "config_fingerprint_sha256": config_fingerprint_sha256,
            "code_fingerprint_sha256": code_fingerprint_sha256,
            "git_commit": git_commit,
            "profile_name": profile_name,
            "manifest_present": manifest_present,
            "manifest_load_error": manifest_load_error,
        }

    def _good_soak(self) -> dict:
        identity = self._good_identity()
        return {
            "quote_uptime_ratio": 0.995,
            "error_rows": 0,
            "execution_quality": {"capture_minus_adverse": 1.2},
            "artifact_identity": dict(identity),
            "run_commit_lineage": {
                "run_id": identity["run_id"],
                "git_commit": identity["git_commit"],
                "config_fingerprint_sha256": identity["config_fingerprint_sha256"],
                "code_fingerprint_sha256": identity["code_fingerprint_sha256"],
                "complete": True,
            },
        }

    def _good_reconcile(self) -> dict:
        return {
            "mismatch_ratio": 0.01,
            "verification_level": "venue_verified",
            "artifact_identity": self._good_identity(),
        }

    def _good_websocket(self) -> dict:
        return {
            "metrics": {
                "book_feed_down_ratio": 0.0,
                "book_feed_worker_unusable_rows": 0.0,
                "book_feed_worker_restart_exhausted_rows": 0.0,
                "chainlink_down_ratio": 0.0,
                "chainlink_worker_unusable_rows": 0.0,
                "chainlink_worker_restart_exhausted_rows": 0.0,
                "book_feed_reconnects_per_hour": 0.0,
                "chainlink_reconnects_per_hour": 0.0,
                "chainlink_dropped_ticks_max": 0.0,
                "gateway_heartbeat_missing_or_invalid_rows": 0.0,
                "gateway_heartbeat_disabled_resting_rows": 0.0,
                "gateway_matching_engine_error_rows": 0.0,
            },
            "artifact_identity": self._good_identity(),
        }

    def _run_gate(self, root: Path, *, soak: dict, reconcile: dict, websocket: dict | None = None, **kwargs) -> dict:
        soak_path = root / "soak.json"
        reconcile_path = root / "reconcile.json"
        soak_path.write_text(json.dumps(soak), encoding="utf-8")
        reconcile_path.write_text(json.dumps(reconcile), encoding="utf-8")
        websocket_path = None
        if websocket is not None:
            websocket_path = root / "websocket.json"
            websocket_path.write_text(json.dumps(websocket), encoding="utf-8")

        return run_gate(
            soak_report_path=soak_path,
            reconcile_report_path=reconcile_path,
            websocket_report_path=websocket_path,
            min_uptime_ratio=0.95,
            max_error_rows=0,
            min_execution_quality_net=0.0,
            max_reconcile_mismatch_ratio=0.02,
            max_websocket_book_feed_down_ratio=0.2,
            max_websocket_chainlink_down_ratio=0.2,
            max_websocket_book_feed_reconnects_per_hour=40.0,
            max_websocket_chainlink_reconnects_per_hour=40.0,
            max_websocket_chainlink_dropped_ticks=0.0,
            **kwargs,
        )

    def test_gate_surfaces_maker_reference_activity_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak.update(
                {
                    "maker_reference_direct_midpoint_activity": 9.0,
                    "maker_reference_missing_activity": 4.0,
                    "maker_market_reference_missing_count": 1.0,
                    "maker_market_reference_one_sided_context_count": 3.0,
                }
            )
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertTrue(result["ok"], msg=result["findings"])
            metrics = result.get("metrics", {})
            self.assertEqual(metrics.get("maker_reference_direct_midpoint_activity"), 9.0)
            self.assertEqual(metrics.get("maker_reference_missing_activity"), 4.0)
            self.assertEqual(metrics.get("maker_market_reference_missing_count"), 1.0)
            self.assertEqual(metrics.get("maker_market_reference_one_sided_context_count"), 3.0)

    def test_gate_passes_for_good_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self._run_gate(root, soak=self._good_soak(), reconcile=self._good_reconcile())
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertIn("decision_trace", result)
            identity = result.get("artifact_identity", {})
            self.assertEqual(identity.get("code_fingerprint_sha256"), "b" * 64)
            self.assertTrue(bool(identity.get("manifest_present")))
            self.assertEqual(str(identity.get("manifest_load_error") or ""), "")

    def test_gate_fails_for_bad_reconcile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["mismatch_ratio"] = 0.2
            result = self._run_gate(root, soak=self._good_soak(), reconcile=reconcile)
            self.assertFalse(result["ok"])
            self.assertTrue(any("reconcile_mismatch_ratio_too_high" in item for item in result["findings"]))

    def test_gate_fails_for_websocket_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            websocket = self._good_websocket()
            websocket["metrics"] = {
                "book_feed_down_ratio": 0.9,
                "chainlink_down_ratio": 0.9,
                "book_feed_reconnects_per_hour": 100.0,
                "chainlink_reconnects_per_hour": 100.0,
                "chainlink_dropped_ticks_max": 5.0,
            }
            result = self._run_gate(root, soak=self._good_soak(), reconcile=self._good_reconcile(), websocket=websocket)
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2201", result["error_codes"])

    def test_gate_fails_for_gateway_websocket_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            websocket = self._good_websocket()
            websocket["metrics"]["gateway_heartbeat_missing_or_invalid_rows"] = 1.0
            result = self._run_gate(root, soak=self._good_soak(), reconcile=self._good_reconcile(), websocket=websocket)
            self.assertFalse(result["ok"])
            self.assertIn(
                "websocket_promotion_gateway_heartbeat_missing_or_invalid_rows_too_high",
                "\n".join(result["findings"]),
            )

    def test_gate_fails_for_worker_unusable_websocket_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            websocket = self._good_websocket()
            websocket["metrics"]["book_feed_worker_unusable_rows"] = 1.0
            result = self._run_gate(root, soak=self._good_soak(), reconcile=self._good_reconcile(), websocket=websocket)
            self.assertFalse(result["ok"])
            self.assertIn(
                "websocket_promotion_book_feed_worker_unusable_rows_too_high",
                "\n".join(result["findings"]),
            )

    def test_gate_marks_reconcile_venue_unavailable_as_advisory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["verification_level"] = "venue_unavailable"
            reconcile["mismatch_ratio"] = 0.0
            result = self._run_gate(root, soak=self._good_soak(), reconcile=reconcile)
            self.assertTrue(result["ok"])
            self.assertIn("reconcile_not_fully_venue_verified:venue_unavailable", result["advisories"])

    def test_gate_accepts_allowed_nonvenue_verification_level(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["verification_level"] = "paper_sim_verified"
            reconcile["mismatch_ratio"] = 0.0
            result = self._run_gate(
                root,
                soak=self._good_soak(),
                reconcile=reconcile,
                allowed_nonvenue_verification_levels=["paper_sim_verified"],
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["advisories"])

    def test_gate_fails_when_websocket_report_required_but_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self._run_gate(
                root,
                soak=self._good_soak(),
                reconcile=self._good_reconcile(),
                websocket_report_required=True,
            )
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2201", result["error_codes"])

    def test_gate_fails_when_reconcile_status_reports_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["mismatch_ratio"] = 0.0
            reconcile["status"] = "mismatch"
            reconcile["exceeds_threshold"] = True
            result = self._run_gate(root, soak=self._good_soak(), reconcile=reconcile)
            self.assertFalse(result["ok"])
            self.assertIn("reconcile_status_not_ok:mismatch", result["findings"])
            self.assertIn("reconcile_exceeds_threshold_true", result["findings"])

    def test_gate_fails_when_soak_and_reconcile_artifact_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["artifact_identity"]["run_id"] = "run-b"
            result = self._run_gate(root, soak=self._good_soak(), reconcile=reconcile)
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_mismatch:run_id:soak_vs_reconcile", result["findings"])

    def test_gate_fails_when_soak_and_websocket_code_fingerprint_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            websocket = self._good_websocket()
            websocket["artifact_identity"]["code_fingerprint_sha256"] = "c" * 64
            result = self._run_gate(root, soak=self._good_soak(), reconcile=self._good_reconcile(), websocket=websocket)
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_mismatch:code_fingerprint_sha256:soak_vs_websocket", result["findings"])

    def test_gate_fails_when_required_code_fingerprint_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["artifact_identity"]["code_fingerprint_sha256"] = ""
            soak["run_commit_lineage"]["code_fingerprint_sha256"] = ""
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_missing_field:soak:code_fingerprint_sha256", result["findings"])

    def test_gate_fails_when_profile_name_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["artifact_identity"]["profile_name"] = ""
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_missing_field:soak:profile_name", result["findings"])

    def test_gate_fails_when_manifest_is_not_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["artifact_identity"]["manifest_present"] = False
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_missing_manifest:soak", result["findings"])

    def test_gate_fails_when_manifest_load_error_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["artifact_identity"]["manifest_load_error"] = "manifest_invalid_json"
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_manifest_load_error:soak:manifest_invalid_json", result["findings"])

    def test_gate_fails_when_hash_lineage_is_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reconcile = self._good_reconcile()
            reconcile["artifact_identity"]["config_fingerprint_sha256"] = "abc"
            result = self._run_gate(root, soak=self._good_soak(), reconcile=reconcile)
            self.assertFalse(result["ok"])
            self.assertIn("artifact_identity_invalid_sha256:reconcile:config_fingerprint_sha256", result["findings"])

    def test_gate_fails_when_soak_run_commit_lineage_is_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["run_commit_lineage"]["complete"] = False
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("soak_run_commit_lineage_incomplete", result["findings"])

    def test_gate_fails_when_soak_run_commit_lineage_disagrees_with_artifact_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            soak = self._good_soak()
            soak["run_commit_lineage"]["code_fingerprint_sha256"] = "c" * 64
            result = self._run_gate(root, soak=soak, reconcile=self._good_reconcile())
            self.assertFalse(result["ok"])
            self.assertIn("soak_run_commit_lineage_mismatch:code_fingerprint_sha256", result["findings"])


if __name__ == "__main__":
    unittest.main()
