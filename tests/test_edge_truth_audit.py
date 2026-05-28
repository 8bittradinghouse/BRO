from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prodesk.edge_truth_contract import normalize_block_reason
from prodesk.historical_recovery_replay_compat import (
    HISTORICAL_RECOVERY_ACTIVE_FIELD as _HISTORICAL_RECOVERY_ACTIVE_FIELD,
    HISTORICAL_RECOVERY_REASON_FIELD as _HISTORICAL_RECOVERY_REASON_FIELD,
)
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.edge_truth_audit import run_audit

_HISTORICAL_RECOVERY_REASON = "preexpiry_reduce_only_window_active"


def _legacy_stage_to_lifecycle_phase(stage: object) -> str:
    normalized = str(stage or "").strip().upper()
    if normalized == "EXPIRED":
        return "resolve"
    if normalized == "MAKER_LATE_WINDOW":
        return "maker_window"
    if normalized == "TAKER_COMMITMENT":
        return "taker_window"
    if normalized:
        return "prepare"
    return ""


def _historical_recovery_lineage(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        _HISTORICAL_RECOVERY_ACTIVE_FIELD: True,
        _HISTORICAL_RECOVERY_REASON_FIELD: _HISTORICAL_RECOVERY_REASON,
    }
    payload.update(overrides)
    return payload


class EdgeTruthAuditTests(unittest.TestCase):
    def _write_event_rows(self, log_dir: Path, rows: list[dict]) -> Path:
        path = log_dir / "events_2026-03-22.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized_rows: list[dict] = []
        for row in rows:
            out = dict(row)
            if "ts_utc" not in out:
                out["ts_utc"] = out.get("timestamp_utc")
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and str(out.get("stage") or "").strip().upper() == "EXTREME_ONLY"
                and (not bool(out.get(_HISTORICAL_RECOVERY_ACTIVE_FIELD, False)))
                and "maker_phase_allowed" not in out
                and "taker_phase_allowed" not in out
            ):
                sec_value = out.get("time_remaining_sec")
                sec = float(sec_value) if isinstance(sec_value, (int, float)) else None
                out["maker_phase_allowed"] = bool(sec is not None and 0.0 <= sec <= 10.0 + 1e-9 and sec > 5.0 + 1e-9)
                out["taker_phase_allowed"] = bool(sec is not None and 0.0 <= sec <= 5.0 + 1e-9)
                out["open_order_cleanup_required"] = False
                out["settlement_hold_required"] = False
                out["unresolved_lifecycle_obligation"] = False
                out["cancel_fail_closed"] = False
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and "lifecycle_phase" not in out
            ):
                if bool(out.get("maker_phase_allowed", False)):
                    out["lifecycle_phase"] = "maker_window"
                elif bool(out.get("taker_phase_allowed", False)):
                    out["lifecycle_phase"] = "taker_window"
                else:
                    out["lifecycle_phase"] = _legacy_stage_to_lifecycle_phase(out.get("stage"))
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and "maker_phase_allowed" not in out
            ):
                out["maker_phase_allowed"] = bool(out.get("lifecycle_phase") == "maker_window")
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and "taker_phase_allowed" not in out
            ):
                out["taker_phase_allowed"] = bool(out.get("lifecycle_phase") == "taker_window")
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and "maker_gate_open" not in out
            ):
                out["maker_gate_open"] = bool(out.get("maker_phase_allowed", False))
            if (
                str(out.get("event_type") or "").strip() == "edge_evaluation"
                and "taker_gate_open" not in out
            ):
                out["taker_gate_open"] = bool(out.get("taker_phase_allowed", False))
            if str(out.get("event_type") or "").strip() == "edge_evaluation":
                out["block_reason"] = normalize_block_reason(out.get("block_reason"))
                for legacy_field in (
                    "maker_allowed",
                    "taker_allowed",
                    "maker_new_risk_allowed",
                    "normal_taker_allowed",
                    "late_window_authority_class",
                ):
                    out.pop(legacy_field, None)
            normalized_rows.append(out)
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in normalized_rows) + "\n"
        path.write_text(payload, encoding="utf-8")
        return path

    def _write_status_rows(self, log_dir: Path, rows: list[dict]) -> Path:
        path = log_dir / "status_2026-03-22.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        path.write_text(payload, encoding="utf-8")
        return path

    def _write_contract(
        self,
        *,
        log_dir: Path,
        run_id: str,
        events_path: Path,
        status_path: Path,
    ) -> Path:
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        manifest_path.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")
        errors_path = log_dir / "errors_2026-03-22.jsonl"
        errors_path.write_text("", encoding="utf-8")
        contract_path = log_dir / f"run_contract_{run_id}.json"
        payload = build_run_contract(
            session_id="sess-edge",
            run_id=run_id,
            phase="validate_postrun",
            session_type="paper",
            authority_level="authoritative",
            allowed_actions=["validate_postrun"],
            manifest_path=manifest_path,
            log_root=log_dir,
            state_root=log_dir / "state.json",
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

    def _config_path(self) -> Path:
        return (Path(__file__).resolve().parents[1] / "configs/profiles/paper_universal.yaml").resolve()

    def test_edge_truth_audit_passes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-pass"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": 0.55,
                        "market_probability": 0.50,
                        "edge_value": 0.05,
                        "oracle_tick_age_sec": 0.2,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 10,
                        "order_id": "ord-t1",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t2",
                        "timestamp_utc": "2026-03-22T00:00:01Z",
                        "stage": "MAKER_POSITION",
                        "time_remaining_sec": 65.0,
                        "fair_probability": 0.51,
                        "market_probability": 0.50,
                        "edge_value": 0.01,
                        "oracle_tick_age_sec": 0.2,
                        "latency_state": "armed",
                        "maker_phase_allowed": True,
                        "taker_phase_allowed": False,
                        "action_taken": "none",
                        "block_reason": "maker_no_submission",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 10,
                        "order_id": None,
                    },
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                        "kill_switch": False,
                        "external_guard_active": False,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(int(out.get("finding_count", -1)), 0)

    def test_edge_truth_audit_fails_when_no_action_has_no_block_reason(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-missing-block"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "MAKER_POSITION",
                        "time_remaining_sec": 70.0,
                        "fair_probability": 0.55,
                        "market_probability": 0.50,
                        "edge_value": 0.05,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": True,
                        "taker_phase_allowed": False,
                        "action_taken": "none",
                        "block_reason": None,
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 11,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("block_reason_missing_for_no_action", findings)

    def test_edge_truth_audit_allows_negative_time_remaining_for_expired_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-expired-negative-time-remaining"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t-expired",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXPIRED",
                        "time_remaining_sec": -12.0,
                        "fair_probability": 0.5,
                        "market_probability": 0.5,
                        "edge_value": 0.0,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": False,
                        "action_taken": "none",
                        "block_reason": "phase_disallow_taker",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 13,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertNotIn("time_remaining_sec_invalid", findings)

    def test_edge_truth_audit_fails_on_action_with_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-invalid-action"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": None,
                        "market_probability": 0.50,
                        "edge_value": None,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 12,
                        "order_id": "ord-1",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("action_with_invalid_edge_inputs", findings)

    def test_edge_truth_audit_detects_duplicate_opportunity_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-dup"
            row = {
                "event_type": "edge_evaluation",
                "run_id": run_id,
                "token_id": "t1",
                "timestamp_utc": "2026-03-22T00:00:00Z",
                "stage": "EXTREME_ONLY",
                "time_remaining_sec": 6.0,
                "fair_probability": 0.55,
                "market_probability": 0.50,
                "edge_value": 0.05,
                "oracle_tick_age_sec": 0.2,
                "latency_state": "armed",
                "maker_phase_allowed": False,
                "taker_phase_allowed": True,
                "action_taken": "none",
                "block_reason": "edge_below_min",
                "submitted": False,
                "filled": False,
                "result": None,
                "evaluation_scope": "taker",
                "cycle_index": 13,
                "order_id": None,
            }
            events_path = self._write_event_rows(log_dir, [row, dict(row)])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("edge_non_deterministic_duplicate_key", findings)
            self.assertIn("edge_duplicate_opportunity_key", findings)

    def test_edge_truth_audit_detects_duplicate_opportunity_key_with_non_identical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-dup-opportunity"
            row_1 = {
                "event_type": "edge_evaluation",
                "run_id": run_id,
                "token_id": "t1",
                "timestamp_utc": "2026-03-22T00:00:00Z",
                "stage": "EXTREME_ONLY",
                "time_remaining_sec": 6.0,
                "fair_probability": 0.55,
                "market_probability": 0.50,
                "edge_value": 0.05,
                "oracle_tick_age_sec": 0.2,
                "latency_state": "armed",
                "maker_phase_allowed": False,
                "taker_phase_allowed": True,
                "action_taken": "none",
                "block_reason": "edge_below_min",
                "submitted": False,
                "filled": False,
                "result": None,
                "evaluation_scope": "taker",
                "cycle_index": 14,
                "order_id": None,
            }
            row_2 = dict(row_1)
            row_2["timestamp_utc"] = "2026-03-22T00:00:01Z"
            events_path = self._write_event_rows(log_dir, [row_1, row_2])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("edge_duplicate_opportunity_key", findings)

    def test_edge_truth_audit_fails_when_identity_is_unverifiable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-redacted-id"
            row_1 = {
                "event_type": "edge_evaluation",
                "run_id": run_id,
                "token_id": "[REDACTED]",
                "timestamp_utc": "2026-03-22T00:00:00Z",
                "stage": "EXTREME_ONLY",
                "time_remaining_sec": 6.0,
                "fair_probability": 0.55,
                "market_probability": 0.50,
                "edge_value": 0.05,
                "oracle_tick_age_sec": 0.2,
                "latency_state": "armed",
                "maker_phase_allowed": False,
                "taker_phase_allowed": True,
                "action_taken": "none",
                "block_reason": "edge_below_min",
                "submitted": False,
                "filled": False,
                "result": None,
                "evaluation_scope": "taker",
                "cycle_index": 14,
                "order_id": None,
            }
            row_2 = dict(row_1)
            row_2["timestamp_utc"] = "2026-03-22T00:00:01Z"
            events_path = self._write_event_rows(log_dir, [row_1, row_2])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("edge_opportunity_identity_unverifiable_rows:2", findings)
            metrics = out.get("metrics", {})
            self.assertEqual(float(metrics.get("opportunity_identity_unverifiable_rows", 0.0)), 2.0)

    def test_edge_truth_audit_allows_redacted_token_when_target_ref_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-redacted-with-ref"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "[REDACTED]",
                        "target_ref": "targetabc123",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "SNIPER_PRIMARY",
                        "time_remaining_sec": 20.0,
                        "fair_probability": 0.60,
                        "market_probability": 0.50,
                        "edge_value": 0.10,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": False,
                        "action_taken": "none",
                        "block_reason": "edge_below_min",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 22,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(int(out.get("finding_count", -1)), 0)

    def test_edge_truth_audit_detects_duplicate_opportunity_key_with_target_ref(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-target-ref"
            row_1 = {
                "event_type": "edge_evaluation",
                "run_id": run_id,
                "token_id": "[REDACTED]",
                "target_ref": "abc123",
                "timestamp_utc": "2026-03-22T00:00:00Z",
                "stage": "EXTREME_ONLY",
                "time_remaining_sec": 6.0,
                "fair_probability": 0.55,
                "market_probability": 0.50,
                "edge_value": 0.05,
                "oracle_tick_age_sec": 0.2,
                "latency_state": "armed",
                "maker_phase_allowed": False,
                "taker_phase_allowed": True,
                "action_taken": "none",
                "block_reason": "edge_below_min",
                "submitted": False,
                "filled": False,
                "result": None,
                "evaluation_scope": "taker",
                "cycle_index": 14,
                "order_id": None,
            }
            row_2 = dict(row_1)
            row_2["timestamp_utc"] = "2026-03-22T00:00:01Z"
            row_2["edge_value"] = 0.049
            events_path = self._write_event_rows(log_dir, [row_1, row_2])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("edge_duplicate_opportunity_key", findings)

    def test_edge_truth_audit_allows_same_resolved_target_ref_with_distinct_source_target_refs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-source-target-ref"
            row_1 = {
                "event_type": "edge_evaluation",
                "run_id": run_id,
                "token_id": "[REDACTED]",
                "target_ref": "resolved-yes",
                "source_target_ref": "source-yes",
                "timestamp_utc": "2026-03-22T00:00:00Z",
                "stage": "EXTREME_ONLY",
                "time_remaining_sec": 6.0,
                "fair_probability": 0.60,
                "market_probability": 0.50,
                "edge_value": 0.10,
                "oracle_tick_age_sec": 0.2,
                "latency_state": "armed",
                "maker_phase_allowed": False,
                "taker_phase_allowed": True,
                "action_taken": "none",
                "block_reason": "edge_below_min",
                "submitted": False,
                "filled": False,
                "result": None,
                "evaluation_scope": "taker",
                "cycle_index": 14,
                "order_id": None,
            }
            row_2 = dict(row_1)
            row_2["source_target_ref"] = "source-no"
            row_2["timestamp_utc"] = "2026-03-22T00:00:01Z"
            events_path = self._write_event_rows(log_dir, [row_1, row_2])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(int(out.get("finding_count", -1)), 0)

    def test_edge_truth_audit_replay_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-stable"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": 0.55,
                        "market_probability": 0.50,
                        "edge_value": 0.05,
                        "oracle_tick_age_sec": 0.2,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "action_taken": "none",
                        "block_reason": "edge_below_min",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 15,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out1 = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            out2 = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertEqual(str(out1.get("edge_records_sha256")), str(out2.get("edge_records_sha256")))
            self.assertEqual(str(out1.get("required_fields_sha256")), str(out2.get("required_fields_sha256")))
            self.assertEqual(
                str(out1.get("block_reason_taxonomy_sha256")),
                str(out2.get("block_reason_taxonomy_sha256")),
            )
            self.assertEqual(str(out1.get("phase_policy_sha256")), str(out2.get("phase_policy_sha256")))
            self.assertEqual(str(out1.get("audit_rule_set_sha256")), str(out2.get("audit_rule_set_sha256")))
            self.assertEqual(list(out1.get("findings", [])), list(out2.get("findings", [])))

    def test_edge_truth_audit_fails_on_scope_action_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-scope-mismatch"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": 0.60,
                        "market_probability": 0.50,
                        "edge_value": 0.10,
                        "oracle_tick_age_sec": 0.2,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 16,
                        "order_id": "ord-scope",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("scope_action_mismatch", findings)

    def test_edge_truth_audit_fails_on_run_contract_run_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            contract_run_id = "rid-contract"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": contract_run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "MAKER_POSITION",
                        "time_remaining_sec": 50.0,
                        "fair_probability": 0.54,
                        "market_probability": 0.50,
                        "edge_value": 0.04,
                        "oracle_tick_age_sec": 0.2,
                        "latency_state": "armed",
                        "maker_phase_allowed": True,
                        "taker_phase_allowed": False,
                        "action_taken": "none",
                        "block_reason": "maker_no_submission",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 17,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": contract_run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=contract_run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id="rid-requested",
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("run_contract_run_id_mismatch", findings)

    def test_edge_truth_audit_fails_on_result_without_submission(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-result"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": 0.60,
                        "market_probability": 0.50,
                        "edge_value": 0.10,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": False,
                        "filled": False,
                        "result": "win",
                        "evaluation_scope": "taker",
                        "cycle_index": 18,
                        "order_id": "ord-1",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("result_must_be_null", findings)

    def test_edge_truth_audit_fails_when_eligibility_flags_mismatch_stage_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-eligibility"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "SNIPER_PRIMARY",
                        "time_remaining_sec": 24.0,
                        "fair_probability": 0.55,
                        "market_probability": 0.50,
                        "edge_value": 0.05,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": True,
                        "taker_phase_allowed": True,
                        "action_taken": "maker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 19,
                        "order_id": "ord-eligibility",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("taker_phase_allowed_mismatch", findings)

    def test_edge_truth_audit_allows_recovery_override_for_stage_policy_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-recovery-override"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "SNIPER_PRIMARY",
                        "time_remaining_sec": 24.0,
                        "fair_probability": 0.55,
                        "market_probability": 0.50,
                        "edge_value": 0.05,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": False,
                        "open_order_cleanup_required": True,
                        "unresolved_lifecycle_obligation": True,
                        "cancel_fail_closed": True,
                        **_historical_recovery_lineage(),
                        "action_taken": "none",
                        "block_reason": "open_order_cleanup_required",
                        "submitted": False,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "maker",
                        "cycle_index": 19,
                        "order_id": None,
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertNotIn("maker_phase_allowed_mismatch", findings)
            self.assertNotIn("taker_phase_allowed_mismatch", findings)

    def test_edge_truth_audit_allows_recovery_action_with_missing_fair_probability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-recovery-missing-fair"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "MAKER_TAKER_SELECTIVE",
                        "time_remaining_sec": 18.0,
                        "fair_probability": None,
                        "market_probability": 0.42,
                        "edge_value": None,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "settlement_hold_required": True,
                        "unresolved_lifecycle_obligation": True,
                        **_historical_recovery_lineage(),
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": False,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 20,
                        "order_id": "ord-recovery-missing-fair",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertNotIn("action_with_invalid_edge_inputs:fair_probability_missing", findings)

    def test_edge_truth_audit_allows_recovery_action_with_missing_market_probability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-recovery-missing-market"
            events_path = self._write_event_rows(
                log_dir,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "t1",
                        "timestamp_utc": "2026-03-22T00:00:00Z",
                        "stage": "EXTREME_ONLY",
                        "time_remaining_sec": 6.0,
                        "fair_probability": 0.77,
                        "market_probability": None,
                        "edge_value": None,
                        "oracle_tick_age_sec": 0.1,
                        "latency_state": "armed",
                        "maker_phase_allowed": False,
                        "taker_phase_allowed": True,
                        "settlement_hold_required": True,
                        "unresolved_lifecycle_obligation": True,
                        **_historical_recovery_lineage(),
                        "action_taken": "taker",
                        "block_reason": None,
                        "submitted": True,
                        "filled": True,
                        "result": None,
                        "evaluation_scope": "taker",
                        "cycle_index": 22,
                        "order_id": "ord-recovery-missing-market",
                    }
                ],
            )
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "active",
                        "target_count": 1,
                    }
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertNotIn("action_with_invalid_edge_inputs:market_probability_missing", findings)

    def test_edge_truth_audit_allows_scan_only_zero_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            run_id = "rid-standdown"
            events_path = self._write_event_rows(log_dir, [])
            status_path = self._write_status_rows(
                log_dir,
                [
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:01Z",
                        "lifecycle_phase": "scan",
                        "target_count": 0,
                        "kill_switch": False,
                        "external_guard_active": False,
                    },
                    {
                        "run_id": run_id,
                        "ts_utc": "2026-03-22T00:00:02Z",
                        "lifecycle_phase": "scan",
                        "target_count": 0,
                        "kill_switch": False,
                        "external_guard_active": False,
                    },
                ],
            )
            run_contract_path = self._write_contract(
                log_dir=log_dir,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            out = run_audit(
                log_dir=log_dir,
                run_id=run_id,
                config_path=self._config_path(),
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(int(out.get("finding_count", -1)), 0)


if __name__ == "__main__":
    unittest.main()
