import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.forensics_bundle import run_bundle


class ForensicsBundleTests(unittest.TestCase):
    def test_run_bundle_writes_archive_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            out_dir = root / "out"
            cfg = root / "cfg.yaml"
            run_id = "rid-1"

            log_dir.mkdir(parents=True, exist_ok=True)
            cfg.write_text("mode: paper\n", encoding="utf-8")
            cfg_hash = hashlib.sha256(cfg.read_bytes()).hexdigest()

            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (log_dir / "status_2099-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:00:00Z"}) + "\n",
                encoding="utf-8",
            )
            (log_dir / "events_2099-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": "cycle"}) + "\n",
                encoding="utf-8",
            )
            (log_dir / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")

            result = run_bundle(
                log_dir=log_dir,
                config_path=cfg,
                run_id=run_id,
                out_dir=out_dir,
                status_tail_lines=50,
                event_tail_lines=50,
                error_tail_lines=50,
            )

            self.assertTrue(result["ok"], msg=result["findings"])
            bundle_dir = Path(result["bundle_dir"])
            self.assertTrue(bundle_dir.exists())
            self.assertTrue(Path(result["bundle_tar_gz"]).exists())

            summary = json.loads((bundle_dir / "incident_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["run_id"], run_id)
            self.assertEqual(summary["config_fingerprint_sha256"], cfg_hash)
            self.assertGreaterEqual(int(summary["status_row_count"]), 1)

    def test_run_bundle_requires_explicit_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            out_dir = root / "out"
            cfg = root / "cfg.yaml"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg.write_text("mode: paper\n", encoding="utf-8")
            result = run_bundle(
                log_dir=log_dir,
                config_path=cfg,
                run_id="",
                out_dir=out_dir,
                status_tail_lines=50,
                event_tail_lines=50,
                error_tail_lines=50,
            )
            self.assertFalse(result["ok"])
            self.assertIn("run_id_required", result["findings"])

    def test_run_bundle_respects_contract_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            out_dir = root / "out"
            cfg = root / "cfg.yaml"
            run_id = "rid-1"
            contract_path = log_dir / f"run_contract_{run_id}.json"
            manifest_path = log_dir / f"run_manifest_{run_id}.json"
            status_path = log_dir / "status_2099-01-01.jsonl"
            events_path = log_dir / "events_2099-01-01.jsonl"
            errors_path = log_dir / "errors_2099-01-01.jsonl"

            log_dir.mkdir(parents=True, exist_ok=True)
            cfg.write_text("mode: paper\n", encoding="utf-8")
            manifest_path.write_text(json.dumps({"run_id": run_id, "manifest_schema_version": 2}), encoding="utf-8")
            status_path.write_text(
                "\n".join(
                    [
                        json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:00:00Z", "counter.orders_submitted": 1}),
                        json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:10:00Z", "counter.orders_submitted": 9}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            events_path.write_text(
                "\n".join(
                    [
                        json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": "edge_evaluation", "action_taken": "none", "block_reason": "stale_book"}),
                        json.dumps({"run_id": run_id, "ts_utc": "2099-01-01T00:10:01Z", "event_type": "edge_evaluation", "action_taken": "none", "block_reason": "stale_book"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            contract = build_run_contract(
                session_id="sid-1",
                run_id=run_id,
                phase="validate_postrun",
                session_type="paper",
                authority_level="authoritative",
                allowed_actions=["validate_postrun"],
                manifest_path=manifest_path,
                log_root=log_dir,
                state_root=root / "state.json",
                start_ts="2099-01-01T00:00:00Z",
                stop_ts="2099-01-01T00:00:05Z",
                evidence_slice_start_ts="2099-01-01T00:00:00Z",
                evidence_slice_end_ts="2099-01-01T00:00:05Z",
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path=str(errors_path),
            )
            write_run_contract(contract_path, contract, allow_open=False)

            result = run_bundle(
                log_dir=log_dir,
                config_path=cfg,
                run_id=run_id,
                out_dir=out_dir,
                status_tail_lines=50,
                event_tail_lines=50,
                error_tail_lines=50,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
            )

            self.assertTrue(result["ok"], msg=result["findings"])
            summary = json.loads((Path(result["bundle_dir"]) / "incident_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(int(summary["status_row_count"]), 1)
            self.assertEqual(int(summary["event_row_count"]), 1)
            self.assertEqual(summary["run_contract_path"], str(contract_path.resolve()))


if __name__ == "__main__":
    unittest.main()
