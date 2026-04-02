from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.canonical_authority import CAPABILITY_VALIDATE_POSTRUN
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.order_lifecycle_audit import run_audit


class OrderLifecycleAuditTests(unittest.TestCase):
    def _write_config(self, root: Path, log_dir: Path) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
        cfg["storage"]["log_dir"] = str(log_dir)
        cfg["targets"]["discovery"]["enabled"] = True
        cfg_path = root / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        return cfg_path

    def _write_rows(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
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
                        "previous_runtime_state": "standdown_no_targets",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "no_target_standdown": False,
                        "previous_book_feed_required": False,
                        "book_feed_required": True,
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
                        "no_target_standdown": False,
                        "previous_book_feed_required": False,
                        "book_feed_required": True,
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
                        "previous_runtime_state": "standdown_no_targets",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "no_target_standdown": False,
                        "previous_book_feed_required": False,
                        "book_feed_required": True,
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
                        "no_target_standdown": False,
                        "previous_book_feed_required": True,
                        "book_feed_required": True,
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
                "runtime_state_transition:kill_switch_runtime_state_mismatch:active",
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
                        "no_target_standdown": False,
                        "previous_book_feed_required": True,
                        "book_feed_required": True,
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
                        "previous_runtime_state": "standdown_no_targets",
                        "runtime_state": "active",
                        "active_targets_present": True,
                        "no_target_standdown": False,
                        "previous_book_feed_required": False,
                        "book_feed_required": True,
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
