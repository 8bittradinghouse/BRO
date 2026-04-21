from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prodesk.run_contract import (
    apply_contract_bounds,
    build_run_contract,
    load_run_contract,
    write_run_contract,
)


class RunContractTests(unittest.TestCase):
    def test_closed_contract_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_run_contract(
                session_id="sid-1",
                run_id="rid-1",
                phase="start",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active"],
                manifest_path=root / "run_manifest_rid-1.json",
                log_root=root,
                state_root=root / "state",
                start_ts="2026-03-18T00:00:00.000Z",
                stop_ts="2026-03-18T00:10:00.000Z",
                evidence_slice_start_ts="2026-03-18T00:00:00.000Z",
                evidence_slice_end_ts="2026-03-18T00:10:00.000Z",
                status_path=str(root / "status_2026-03-18.jsonl"),
                events_path=str(root / "events_2026-03-18.jsonl"),
                errors_path=str(root / "errors_2026-03-18.jsonl"),
                git_commit="deadbeef",
                config_fingerprint_sha256="a" * 64,
                code_fingerprint_sha256="b" * 64,
                code_fingerprint_file_count=55,
            )
            out_path = root / "run_contract_rid-1.json"
            write_run_contract(out_path, payload, allow_open=False)
            loaded = load_run_contract(out_path, allow_open=False)
            self.assertEqual(str(loaded.get("run_id")), "rid-1")
            self.assertEqual(str(loaded.get("git_commit")), "deadbeef")
            self.assertEqual(str(loaded.get("config_fingerprint_sha256")), "a" * 64)
            self.assertEqual(str(loaded.get("code_fingerprint_sha256")), "b" * 64)
            self.assertEqual(int(loaded.get("code_fingerprint_file_count")), 55)

    def test_open_contract_requires_allow_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_run_contract(
                session_id="sid-open",
                run_id="rid-open",
                phase="start",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active"],
                manifest_path=root / "run_manifest_rid-open.json",
                log_root=root,
                state_root=root / "state",
                start_ts="2026-03-18T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-18T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(root / "status_2026-03-18.jsonl"),
                events_path=str(root / "events_2026-03-18.jsonl"),
                errors_path=str(root / "errors_2026-03-18.jsonl"),
            )
            out_path = root / "run_contract_open.json"
            write_run_contract(out_path, payload, allow_open=True)
            loaded = load_run_contract(out_path, allow_open=True)
            self.assertEqual(str(loaded.get("run_id")), "rid-open")
            with self.assertRaises(ValueError):
                load_run_contract(out_path, allow_open=False)

    def test_contract_fails_closed_without_allowed_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_run_contract(
                session_id="sid-open",
                run_id="rid-open",
                phase="start",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control"],
                manifest_path=root / "run_manifest_rid-open.json",
                log_root=root,
                state_root=root / "state",
                start_ts="2026-03-18T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-18T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(root / "status_2026-03-18.jsonl"),
                events_path=str(root / "events_2026-03-18.jsonl"),
                errors_path=str(root / "errors_2026-03-18.jsonl"),
            )
            payload["allowed_actions"] = []
            out_path = root / "run_contract_open.json"
            with self.assertRaises(ValueError):
                write_run_contract(out_path, payload, allow_open=True)

    def test_contract_fails_closed_on_unknown_allowed_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_run_contract(
                session_id="sid-open",
                run_id="rid-open",
                phase="start",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "unknown_action"],
                manifest_path=root / "run_manifest_rid-open.json",
                log_root=root,
                state_root=root / "state",
                start_ts="2026-03-18T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-18T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(root / "status_2026-03-18.jsonl"),
                events_path=str(root / "events_2026-03-18.jsonl"),
                errors_path=str(root / "errors_2026-03-18.jsonl"),
            )
            out_path = root / "run_contract_open.json"
            with self.assertRaises(ValueError):
                write_run_contract(out_path, payload, allow_open=True)

    def test_contract_fails_closed_on_legacy_complete_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_run_contract(
                session_id="sid-open",
                run_id="rid-open",
                phase="start",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "complete"],
                manifest_path=root / "run_manifest_rid-open.json",
                log_root=root,
                state_root=root / "state",
                start_ts="2026-03-18T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-18T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(root / "status_2026-03-18.jsonl"),
                events_path=str(root / "events_2026-03-18.jsonl"),
                errors_path=str(root / "errors_2026-03-18.jsonl"),
            )
            out_path = root / "run_contract_open.json"
            with self.assertRaises(ValueError):
                write_run_contract(out_path, payload, allow_open=True)

    def test_apply_contract_bounds_filters_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = {
                "run_id": "rid-2",
                "evidence_slice_start_ts": "2026-03-18T00:00:00.000Z",
                "evidence_slice_end_ts": "2026-03-18T00:05:00.000Z",
            }
            rows = [
                {"run_id": "rid-2", "ts_utc": "2026-03-18T00:00:30.000Z"},
                {"run_id": "rid-2", "ts_utc": "2026-03-18T00:05:30.000Z"},
                {"run_id": "other", "ts_utc": "2026-03-18T00:01:00.000Z"},
            ]
            filtered = apply_contract_bounds(rows, contract)
            self.assertEqual(len(filtered), 1)
            self.assertEqual(str(filtered[0].get("run_id")), "rid-2")


if __name__ == "__main__":
    unittest.main()
