from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.edge_truth_contract import legacy_stage_to_lifecycle_phase
from prodesk.canonical_authority import CAPABILITY_VALIDATE_POSTRUN
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.order_lifecycle_audit import run_audit


class OrderLifecycleAuditTests(unittest.TestCase):
    def _write_config(self, root: Path, log_dir: Path) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["taker"].pop("max_chainlink_tick_age_sec", None)
        cfg["taker"]["competitiveness"]["min_visible_fill_ratio"] = 0.5
        cfg["storage"]["log_dir"] = str(log_dir)
        cfg["targets"]["discovery"]["enabled"] = True
        cfg_path = root / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        return cfg_path

    def _write_rows(self, path: Path, rows: list[dict]) -> None:
        normalized_rows: list[dict] = []
        runtime_state_to_phase = {
            "active": "prepare",
            "prepare": "prepare",
            "scan": "scan",
            "safety_halt": "resolve",
            "resolve": "resolve",
            "maker_window": "maker_window",
            "taker_window": "taker_window",
        }
        for raw_row in rows:
            row = dict(raw_row)
            event_type = str(row.get("event_type") or "").strip()
            if event_type == "runtime_state_transition":
                runtime_state = str(row.get("runtime_state") or "").strip().lower()
                previous_runtime_state = str(row.get("previous_runtime_state") or "").strip().lower()
                lifecycle_phase = str(row.get("lifecycle_phase") or "").strip().lower()
                if not lifecycle_phase:
                    lifecycle_phase = runtime_state_to_phase.get(runtime_state, runtime_state or "prepare")
                    row["lifecycle_phase"] = lifecycle_phase
                if "scan_phase" not in row:
                    row["scan_phase"] = lifecycle_phase == "scan"
                if runtime_state in runtime_state_to_phase:
                    row["runtime_state"] = runtime_state_to_phase[runtime_state]
                if previous_runtime_state in runtime_state_to_phase:
                    row["previous_runtime_state"] = runtime_state_to_phase[previous_runtime_state]
            elif event_type == "order_submit" and "lifecycle_phase" not in row:
                stage_name = str(row.get("stage") or "").strip()
                lifecycle_phase = legacy_stage_to_lifecycle_phase(stage_name)
                if not lifecycle_phase:
                    execution_preference = str(row.get("execution_preference") or "").strip().lower()
                    lifecycle_phase = "taker_window" if execution_preference == "taker_only" else "maker_window"
                row["lifecycle_phase"] = lifecycle_phase
            normalized_rows.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in normalized_rows) + "\n"
        path.write_text(payload, encoding="utf-8")

    def _write_contract(self, *, root: Path, log_dir: Path, run_id: str, events_path: Path, status_path: Path) -> Path:
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        manifest_path.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")
        errors_path = log_dir / "errors_2026-03-22.jsonl"
        errors_path.write_text("", encoding="utf-8")
        contract_path = log_dir / f"run_contract_{run_id}.json"
        payload = build_run_contract(
            session_id="sid-lifecycle",
            run_id=run_id,
            phase="validate_postrun",
            session_type="paper_canonical",
            authority_level="authoritative",
            allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
            manifest_path=manifest_path,
            log_root=log_dir,
            state_root=root,
            start_ts="2026-03-22T00:00:00Z",
            stop_ts="2026-03-22T00:10:00Z",
            evidence_slice_start_ts="2026-03-22T00:00:00Z",
            evidence_slice_end_ts="2026-03-22T00:10:00Z",
            status_path=str(status_path),
            events_path=str(events_path),
            errors_path=str(errors_path),
            status_slice_path=str(status_path),
            events_slice_path=str(events_path),
            errors_slice_path=str(errors_path),
        )
        write_run_contract(contract_path, payload, allow_open=False)
        return contract_path

    def test_order_lifecycle_audit_passes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-pass"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "previous_runtime_state": "scan",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": False,
                        "market_truth_required": True,
                        "kill_switch": False,
                        "transition_reason_code": "targets_activated",
                        "transition_reason_detail": "details",
                    },
                    {
                        "event_type": "ws_slo_state",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "degraded": False,
                        "reasons": [],
                    },
                    {
                        "event_type": "operating_mode_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "state": "normal",
                        "previous_state": "normal",
                        "reason": "steady",
                    },
                    {
                        "event_type": "chainlink_tick",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "symbol": "btc/usd",
                        "price": 65000.0,
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:03Z",
                        "ts_event_utc": "2026-03-22T00:00:03Z",
                        "token_id": "tok-1",
                        "action_taken": "maker",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:03Z",
                        "ts_event_utc": "2026-03-22T00:00:03Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": None,
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:04Z",
                        "ts_event_utc": "2026-03-22T00:00:04Z",
                        "trade_id": "paper-trade-aaaaaaaaaaaa-1",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "source": "paper",
                    },
                    {
                        "event_type": "order_cancel",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:05Z",
                        "ts_event_utc": "2026-03-22T00:00:05Z",
                        "order_id": "ord-1",
                        "reason": "replace_quote",
                    },
                    {
                        "event_type": "cancel_all_on_exit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:06Z",
                        "ts_event_utc": "2026-03-22T00:00:06Z",
                        "reason": "runner_shutdown",
                        "canceled_count": 0,
                        "released_lock_count": 0,
                        "gateway_reported_canceled_count": 0,
                        "open_before_count": 0,
                        "open_after_count": 0,
                        "unconfirmed_open_count": 0,
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("finding_count", -1)), 0)
            self.assertEqual(int(payload.get("lifecycle_counts", {}).get("order_submit", -1)), 1)

    def test_order_lifecycle_audit_accepts_suppressed_commitment_cancel_and_terminal_window_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-commitment-hold"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "chainlink_tick",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "symbol": "btc/usd",
                        "price": 65000.0,
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "token_id": "tok-1",
                        "action_taken": "maker",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:03Z",
                        "ts_event_utc": "2026-03-22T00:00:03Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": "MAKER_TAKER_SELECTIVE",
                    },
                    {
                        "event_type": "order_cancel_suppressed",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:04Z",
                        "ts_event_utc": "2026-03-22T00:00:04Z",
                        "order_id": "ord-1",
                        "requested_cancel_reason": "launch_safe_selection_reject",
                        "request_origin": "maker_selection_gate",
                        "suppression_reason": "commitment_hold_active_pre_expiry",
                        "submission_lane": "maker",
                        "commitment_hold_active": True,
                        "commitment_hold_reason": "late_window_commitment",
                        "commitment_expiry_ts_utc": "2026-03-22T00:00:10Z",
                    },
                    {
                        "event_type": "order_cancel",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:10Z",
                        "ts_event_utc": "2026-03-22T00:00:10Z",
                        "order_id": "ord-1",
                        "reason": "commitment_window_ended",
                        "requested_cancel_reason": "commitment_window_ended",
                        "request_origin": "commitment_expiry_cleanup",
                        "cancel_class": "terminal_window_end",
                        "submission_lane": "maker",
                        "commitment_hold_active": True,
                        "commitment_hold_reason": "late_window_commitment",
                        "commitment_expiry_ts_utc": "2026-03-22T00:00:10Z",
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:11Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("lifecycle_counts", {}).get("order_cancel_suppressed", -1)), 1)
            self.assertEqual(payload.get("cancel_suppressed_without_submit_order_ids"), [])

    def test_order_lifecycle_audit_flags_preexpiry_routine_cancel_for_committed_maker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-commitment-routine-cancel"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "chainlink_tick",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "symbol": "btc/usd",
                        "price": 65000.0,
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "token_id": "tok-1",
                        "action_taken": "maker",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:03Z",
                        "ts_event_utc": "2026-03-22T00:00:03Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": "MAKER_TAKER_SELECTIVE",
                    },
                    {
                        "event_type": "order_cancel",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:04Z",
                        "ts_event_utc": "2026-03-22T00:00:04Z",
                        "order_id": "ord-1",
                        "reason": "launch_safe_selection_reject",
                        "requested_cancel_reason": "launch_safe_selection_reject",
                        "request_origin": "maker_selection_gate",
                        "cancel_class": "legacy_routine",
                        "submission_lane": "maker",
                        "commitment_hold_active": True,
                        "commitment_hold_reason": "late_window_commitment",
                        "commitment_expiry_ts_utc": "2026-03-22T00:00:10Z",
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:11Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(payload.get("ok")))
            self.assertIn("order_cancel:committed_maker_routine_cancel_pre_expiry", list(payload.get("findings") or []))

    def test_order_lifecycle_audit_full_scans_run_contract_event_slice(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-slice-full-scan"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "chainlink_tick",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "symbol": "btc/usd",
                        "price": 65000.0,
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "token_id": "tok-1",
                        "action_taken": "maker",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:03Z",
                        "ts_event_utc": "2026-03-22T00:00:03Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": "MAKER_TAKER_SELECTIVE",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:04Z",
                        "ts_event_utc": "2026-03-22T00:00:04Z",
                    },
                    {
                        "event_type": "order_cancel",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:05Z",
                        "ts_event_utc": "2026-03-22T00:00:05Z",
                        "order_id": "ord-1",
                        "reason": "replace_quote",
                    },
                    {
                        "event_type": "cancel_all_on_exit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:06Z",
                        "ts_event_utc": "2026-03-22T00:00:06Z",
                        "reason": "runner_shutdown",
                        "canceled_count": 0,
                        "released_lock_count": 0,
                        "gateway_reported_canceled_count": 0,
                        "open_before_count": 0,
                        "open_after_count": 0,
                        "unconfirmed_open_count": 0,
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=3,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("finding_count", -1)), 0)
            self.assertEqual(int(payload.get("warning_count", -1)), 0)
            self.assertEqual(int(payload.get("events_considered", -1)), 6)
            self.assertEqual(payload.get("event_source_mode"), "run_contract_slice_full_scan")
            self.assertEqual(int(payload.get("events_max_lines_per_file", -1)), 0)
            self.assertEqual(payload.get("cancel_without_submit_order_ids"), [])

    def test_order_lifecycle_audit_fails_when_transition_fields_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-missing-transition"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": False,
                        "market_truth_required": True,
                        "kill_switch": False,
                        "transition_reason_code": "targets_activated",
                        "transition_reason_detail": "details",
                    }
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(payload.get("ok")))
            self.assertIn(
                "runtime_state_transition:missing_required_field:previous_runtime_state",
                "\n".join(str(x) for x in payload.get("findings", [])),
            )

    def test_order_lifecycle_audit_fails_when_fill_has_no_submit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-fill-without-submit"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "previous_runtime_state": "scan",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": False,
                        "market_truth_required": True,
                        "kill_switch": False,
                        "transition_reason_code": "targets_activated",
                        "transition_reason_detail": "details",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "trade_id": "paper-trade-aaaaaaaaaaaa-1",
                        "order_id": "ord-missing",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 2.0,
                        "source": "paper",
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(payload.get("ok")))
            self.assertIn(
                "fill:order_id_without_submit:ord-missing",
                "\n".join(str(x) for x in payload.get("findings", [])),
            )

    def test_order_lifecycle_audit_fails_on_kill_switch_runtime_state_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-kill-mismatch"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "previous_runtime_state": "active",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": True,
                        "market_truth_required": True,
                        "kill_switch": True,
                        "transition_reason_code": "kill_switch_engaged",
                        "transition_reason_detail": "details",
                    }
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(payload.get("ok")))
            self.assertIn(
                "runtime_state_transition:kill_switch_runtime_state_mismatch:prepare",
                "\n".join(str(x) for x in payload.get("findings", [])),
            )

    def test_order_lifecycle_audit_accepts_kill_switch_safety_halt_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-kill-safety-halt"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "previous_runtime_state": "active",
                        "runtime_state": "safety_halt",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": True,
                        "market_truth_required": True,
                        "kill_switch": True,
                        "transition_reason_code": "kill_switch_engaged",
                        "transition_reason_detail": "details",
                    }
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "safety_halt"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("finding_count", -1)), 0)

    def test_order_lifecycle_audit_replay_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-replay"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "runtime_state_transition",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "previous_runtime_state": "scan",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "scan_phase": False,
                        "previous_market_truth_required": False,
                        "market_truth_required": True,
                        "kill_switch": False,
                        "transition_reason_code": "targets_activated",
                        "transition_reason_detail": "details",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": None,
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            first = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            second = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertEqual(first.get("lifecycle_records_sha256"), second.get("lifecycle_records_sha256"))
            self.assertEqual(first.get("required_fields_sha256"), second.get("required_fields_sha256"))
            self.assertEqual(first.get("audit_rule_set_sha256"), second.get("audit_rule_set_sha256"))

    def test_order_lifecycle_audit_detects_missing_decision_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._write_config(root, log_dir)
            run_id = "rid-missing-linkage"
            events_path = log_dir / "events_2026-03-22.jsonl"
            status_path = log_dir / "status_2026-03-22.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "chainlink_tick",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:00Z",
                        "ts_event_utc": "2026-03-22T00:00:00Z",
                        "symbol": "btc/usd",
                        "price": 65000.0,
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "ts_event_utc": "2026-03-22T00:00:01Z",
                        "token_id": "tok-other",
                        "action_taken": "maker",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "ts_event_utc": "2026-03-22T00:00:02Z",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 10.0,
                        "reason_code": "mm_quote",
                        "execution_preference": "maker_preferred",
                        "market_id": "m-1",
                        "window_id": "2026-03-22T00:00",
                        "stage": None,
                    },
                ],
            )
            self._write_rows(
                status_path,
                [{"run_id": run_id, "ts_utc": "2026-03-22T00:00:07Z", "runtime_state": "active"}],
            )
            contract_path = self._write_contract(
                root=root,
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                config_path=cfg_path,
                log_dir=log_dir,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(payload.get("ok")))
            findings = "\n".join(str(x) for x in payload.get("findings", []))
            self.assertIn("order_submit:missing_edge_decision_link:ord-1", findings)


if __name__ == "__main__":
    unittest.main()
