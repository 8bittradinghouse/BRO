from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.outcome_truth_audit import _validate_records, run_audit


class OutcomeTruthAuditTests(unittest.TestCase):
    def _write_rows(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        path.write_text(payload, encoding="utf-8")

    def _write_contract(self, *, log_dir: Path, run_id: str, events_path: Path, status_path: Path) -> Path:
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        manifest_path.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")
        errors_path = log_dir / "errors_2026-03-30.jsonl"
        errors_path.write_text("", encoding="utf-8")
        contract_path = log_dir / f"run_contract_{run_id}.json"
        payload = build_run_contract(
            session_id="sid-outcome",
            run_id=run_id,
            phase="validate_postrun",
            session_type="paper_canonical",
            authority_level="authoritative",
            allowed_actions=["validate_postrun"],
            manifest_path=manifest_path,
            log_root=log_dir,
            state_root=log_dir,
            start_ts="2026-03-30T00:00:00Z",
            stop_ts="2026-03-30T00:10:00Z",
            evidence_slice_start_ts="2026-03-30T00:00:00Z",
            evidence_slice_end_ts="2026-03-30T00:10:00Z",
            status_path=str(status_path),
            events_path=str(events_path),
            errors_path=str(errors_path),
            status_slice_path=str(status_path),
            events_slice_path=str(events_path),
            errors_slice_path=str(errors_path),
        )
        write_run_contract(contract_path, payload, allow_open=False)
        return contract_path

    def _policy_path(self) -> Path:
        return (Path(__file__).resolve().parents[1] / "ops/outcome_truth_policy.json").resolve()

    def _policy_payload(self) -> dict:
        return json.loads(self._policy_path().read_text(encoding="utf-8"))

    def _base_complete_record(self) -> dict:
        return {
            "decision_id": "decision:rid:ord-1",
            "order_submit_id": "ord-1",
            "fill_trade_id": "tr-1",
            "ts_decision_utc": "2026-03-30T00:00:00.000Z",
            "ts_fill_utc": "2026-03-30T00:00:01.000Z",
            "ts_eval_utc": "2026-03-30T00:00:05.000Z",
            "mid_price_decision": 0.50,
            "fill_price": 0.49,
            "mid_price_eval": 0.55,
            "edge_expected": 0.02,
            "edge_expected_known": True,
            "slippage": -0.01,
            "adverse_selection": -0.06,
            "edge_realized": 0.06,
            "decision_quality": "correct",
            "execution_quality": "favorable",
            "combined_outcome_class": "correct_decision_good_execution",
            "evaluation_horizon_ms": 5000,
            "outcome_truth_status": "complete",
            "missing_fields": [],
            "claim_boundary_class": "not_provable_missing_inputs",
            "record_claim_boundary_class": "complete",
            "decision_reference_status": "recovered_explicit_decision_reference",
            "decision_reference_source": "order_submit.decision_reference_midpoint",
            "decision_reference_basis": "direct_book_midpoint",
            "decision_reference_lookup_key": "order_submit:ord-1",
            "decision_reference_recoverable": True,
            "eval_reference_status": "recovered_timestamp_bound_artifact_lookup",
            "eval_reference_source": "edge_evaluation.target_ref_series",
            "eval_reference_basis": "edge_market_midpoint_series",
            "eval_reference_lookup_key": "target_ref:deadbeef",
            "eval_reference_recoverable": True,
            "decision_reference_link_status": "linked_via_order_id_to_edge_target_ref",
            "eval_reference_link_status": "linked_via_target_ref",
            "reference_linkage_mode": "explicit_decision_reference>target_ref_linkage>timestamp_bound_artifact_lookup>token_lookup_non_redacted>unknown",
            "reference_linkage_complete": True,
            "maker_edge_linkage_attempted": False,
            "maker_edge_linkage_resolved": False,
            "maker_edge_linkage_ambiguous": False,
            "maker_edge_linkage_missing": False,
            "decision_anchor_ts_utc": "2026-03-30T00:00:00.000Z",
            "decision_anchor_source": "decision_reference_ts_utc",
            "order_side": "BUY",
            "token_id": "tok-1",
            "decision_component": 0.05,
            "execution_component": 0.01,
            "market_component": 0.0,
            "fill_count": 1,
            "fill_total_size": 10.0,
            "decision_quality_basis": "directional_mid_eval_vs_mid_decision",
            "execution_quality_basis": "directional_mid_decision_vs_fill",
            "combined_outcome_basis": "decision_quality_plus_execution_quality",
            "claim_boundary": {"layer": "outcome_truth_observational", "record_class": "not_provable_missing_inputs"},
        }

    def test_validate_records_fails_mixed_horizon_detected(self) -> None:
        policy = self._policy_payload()
        first = self._base_complete_record()
        second = self._base_complete_record()
        second["decision_id"] = "decision:rid:ord-2"
        second["order_submit_id"] = "ord-2"
        second["fill_trade_id"] = "tr-2"
        second["evaluation_horizon_ms"] = 4000
        second["ts_eval_utc"] = "2026-03-30T00:00:04.000Z"
        findings = _validate_records(records=[first, second], policy=policy)
        self.assertIn("mixed_evaluation_horizon_detected", findings)

    def test_validate_records_fails_missing_horizon(self) -> None:
        policy = self._policy_payload()
        record = self._base_complete_record()
        record.pop("evaluation_horizon_ms", None)
        findings = _validate_records(records=[record], policy=policy)
        self.assertIn("missing_evaluation_horizon", findings)

    def test_outcome_truth_audit_passes_complete_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-pass"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "reports" / "outcome_truth_records.jsonl"

            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-1",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("complete_outcome_records", -1)), 1)
            self.assertEqual(int(payload.get("unknown_outcome_records", -1)), 0)
            self.assertEqual(int(payload.get("decision_quality_distribution", {}).get("correct", -1)), 1)
            self.assertEqual(int(payload.get("execution_quality_distribution", {}).get("favorable", -1)), 1)
            self.assertEqual(
                int(payload.get("combined_outcome_distribution", {}).get("correct_decision_good_execution", -1)),
                1,
            )
            self.assertTrue(records_path.exists())
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0].get("claim_boundary_class") or ""), "not_provable_missing_inputs")

    def test_outcome_truth_audit_emits_lane_outcome_truth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-lane-truth"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"

            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-normal",
                        "token_id": "tok-normal",
                        "target_ref": "ref-normal",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-normal",
                        "token_id": "tok-normal",
                        "target_ref": "ref-normal",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "reason": "taker_chainlink",
                        "execution_preference": "taker_only",
                        "decision_reference_midpoint": 0.50,
                        "decision_reference_ts_utc": "2026-03-30T00:00:00Z",
                        "taker_competitiveness": {
                            "conviction_score": 0.90,
                            "timing_window_class": "final_window",
                            "multi_oracle_status": "confirmed",
                        },
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-normal",
                        "order_id": "ord-normal",
                        "token_id": "tok-normal",
                        "target_ref": "ref-normal",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-normal",
                        "target_ref": "ref-normal",
                        "action_taken": "none",
                        "evaluation_scope": "taker",
                        "block_reason": "edge_below_min",
                        "market_probability": 0.55,
                        "ts_decision_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-normal",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-recovery",
                        "token_id": "tok-recovery",
                        "target_ref": "ref-recovery",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": -0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-recovery",
                        "token_id": "tok-recovery",
                        "target_ref": "ref-recovery",
                        "side": "SELL",
                        "price": 0.45,
                        "size": 10.0,
                        "reason": "taker_chainlink",
                        "execution_preference": "taker_only",
                        "decision_reference_midpoint": 0.50,
                        "decision_reference_ts_utc": "2026-03-30T00:00:10Z",
                        "settlement_hold_required": True,
                        "unresolved_lifecycle_obligation": True,
                        "taker_competitiveness": {
                            "settlement_hold_required": True,
                            "unresolved_lifecycle_obligation": True,
                        },
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-recovery",
                        "order_id": "ord-recovery",
                        "token_id": "tok-recovery",
                        "target_ref": "ref-recovery",
                        "side": "SELL",
                        "price": 0.45,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:11Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-recovery",
                        "target_ref": "ref-recovery",
                        "action_taken": "none",
                        "evaluation_scope": "taker",
                        "block_reason": "edge_below_min",
                        "market_probability": 0.55,
                        "ts_decision_utc": "2026-03-30T00:00:15Z",
                        "ts_utc": "2026-03-30T00:00:15Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-recovery",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:15Z",
                        "ts_utc": "2026-03-30T00:00:15Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:20Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            lanes = payload.get("lane_outcome_truth", {})

            normal = lanes.get("normal_taker", {})
            self.assertEqual(int(normal.get("total_outcome_records", -1)), 1)
            self.assertEqual(int(normal.get("filled_complete", -1)), 1)
            self.assertAlmostEqual(float(normal.get("edge_realized_x_size_sum", 0.0)), 0.60)
            self.assertAlmostEqual(float(normal.get("execution_component_x_size_sum", 0.0)), 0.10)
            self.assertEqual(int(normal.get("decision_quality_distribution", {}).get("correct", -1)), 1)
            self.assertEqual(int(normal.get("execution_quality_distribution", {}).get("favorable", -1)), 1)

            recovery = lanes.get("lifecycle_residue_taker", {})
            self.assertEqual(int(recovery.get("total_outcome_records", -1)), 1)
            self.assertEqual(int(recovery.get("filled_complete", -1)), 1)
            self.assertEqual(int(recovery.get("lifecycle_residue_records", -1)), 1)
            self.assertAlmostEqual(float(recovery.get("edge_realized_x_size_sum", 0.0)), -1.0)
            self.assertAlmostEqual(float(recovery.get("execution_component_x_size_sum", 0.0)), -0.50)
            self.assertEqual(int(recovery.get("decision_quality_distribution", {}).get("incorrect", -1)), 1)
            self.assertEqual(int(recovery.get("execution_quality_distribution", {}).get("unfavorable", -1)), 1)

            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_order = {str(row.get("order_submit_id") or ""): row for row in rows}
            self.assertEqual(str(by_order["ord-normal"].get("submission_lane_truth") or ""), "normal_taker")
            self.assertEqual(
                str(by_order["ord-recovery"].get("submission_lane_truth") or ""),
                "lifecycle_residue_taker",
            )

    def test_outcome_truth_audit_emits_commitment_lane_outcome_truth_for_normal_taker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-commitment-truth"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"

            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-commit",
                        "token_id": "tok-commit",
                        "target_ref": "ref-commit",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.05,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-commit",
                        "token_id": "tok-commit",
                        "target_ref": "ref-commit",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "sec_to_expiry": 10.0,
                        "reason": "taker_chainlink",
                        "execution_preference": "taker_only",
                        "decision_reference_midpoint": 0.50,
                        "decision_reference_ts_utc": "2026-03-30T00:00:00Z",
                        "taker_competitiveness": {
                            "conviction_score": 0.95,
                            "timing_window_class": "final_window",
                            "multi_oracle_status": "confirmed",
                            "sec_to_expiry": 10.0,
                        },
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-commit",
                        "order_id": "ord-commit",
                        "token_id": "tok-commit",
                        "target_ref": "ref-commit",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-commit",
                        "target_ref": "ref-commit",
                        "action_taken": "none",
                        "evaluation_scope": "taker",
                        "block_reason": "edge_below_min",
                        "market_probability": 0.49,
                        "ts_decision_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-commit",
                        "target_ref": "ref-commit",
                        "action_taken": "none",
                        "evaluation_scope": "taker",
                        "block_reason": "normal_taker_authority_closed",
                        "market_probability": 0.75,
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:12Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )

            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))

            observational = payload.get("lane_outcome_truth", {}).get("normal_taker", {})
            self.assertEqual(int(observational.get("decision_quality_distribution", {}).get("incorrect", -1)), 1)

            commitment = payload.get("commitment_lane_outcome_truth", {}).get("normal_taker", {})
            self.assertEqual(int(commitment.get("complete_outcome_records", -1)), 1)
            self.assertEqual(int(commitment.get("decision_quality_distribution", {}).get("correct", -1)), 1)
            self.assertAlmostEqual(float(commitment.get("edge_realized_x_size_sum", 0.0)), 2.5)
            self.assertAlmostEqual(float(commitment.get("commitment_horizon_ms_summary", {}).get("max", 0.0)), 10000.0)

    def test_outcome_truth_audit_marks_unknown_incomplete_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-incomplete"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-2",
                        "token_id": "tok-1",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.01,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-2",
                        "token_id": "tok-1",
                        "side": "SELL",
                        "price": 0.52,
                        "size": 4.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.51,
                        "best_ask_price": 0.53,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("complete_outcome_records", -1)), 0)
            self.assertEqual(int(payload.get("incomplete_lifecycle_records", -1)), 1)

    def test_outcome_truth_audit_emits_record_and_run_claim_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-claim-boundary"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-complete",
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-partial",
                        "token_id": "tok-2",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.01,
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-partial",
                        "token_id": "tok-2",
                        "side": "SELL",
                        "price": 0.60,
                        "size": 5.0,
                        "decision_reference_midpoint": 0.60,
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-partial",
                        "order_id": "ord-partial",
                        "token_id": "tok-2",
                        "side": "SELL",
                        "price": 0.60,
                        "size": 5.0,
                        "ts_utc": "2026-03-30T00:00:11Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:15Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            run_claim_boundary = payload.get("run_claim_boundary", {})
            self.assertEqual(int(run_claim_boundary.get("complete_records", -1)), 1)
            self.assertEqual(int(run_claim_boundary.get("partial_records", -1)), 1)
            self.assertEqual(int(run_claim_boundary.get("unknown_records", -1)), 0)
            self.assertAlmostEqual(float(run_claim_boundary.get("completeness_ratio", -1.0)), 0.5)

            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_order = {str(row.get("order_submit_id") or ""): row for row in rows}
            self.assertEqual(str(by_order["ord-complete"].get("record_claim_boundary_class") or ""), "complete")
            self.assertEqual(
                str(by_order["ord-partial"].get("record_claim_boundary_class") or ""),
                "missing_eval_reference",
            )

    def test_outcome_truth_audit_unknown_missing_eval_reference_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-unknown-eval"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-unknown",
                        "token_id": "tok-unknown",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-unknown",
                        "token_id": "tok-unknown",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 2.0,
                        "decision_reference_midpoint": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-unknown",
                        "order_id": "ord-unknown",
                        "token_id": "tok-unknown",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 2.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0].get("outcome_truth_status") or ""), "unknown_missing_eval_reference")
            self.assertEqual(str(rows[0].get("decision_quality") or ""), "unknown")
            self.assertEqual(str(rows[0].get("combined_outcome_class") or ""), "unknown")

    def test_outcome_truth_audit_recovers_redacted_references_via_target_ref(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-redacted-recover"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-redacted",
                        "token_id": "[REDACTED]",
                        "target_ref": "ref-redacted",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "[REDACTED]",
                        "target_ref": "ref-redacted",
                        "action_taken": "none",
                        "evaluation_scope": "taker",
                        "block_reason": "edge_below_min",
                        "market_probability": 0.55,
                        "ts_decision_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-redacted",
                        "token_id": "[REDACTED]",
                        "target_ref": "ref-redacted",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 3.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-redacted",
                        "order_id": "ord-redacted",
                        "token_id": "[REDACTED]",
                        "target_ref": "ref-redacted",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 3.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("complete_outcome_records", -1)), 1)
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(str(rows[0].get("decision_reference_status") or ""), "recovered_target_ref_linkage")
            self.assertEqual(str(rows[0].get("eval_reference_status") or ""), "recovered_timestamp_bound_artifact_lookup")

    def test_outcome_truth_audit_marks_unknown_missing_decision_reference_when_unrecoverable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-missing-decision-ref"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-missing",
                        "token_id": "[REDACTED]",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.01,
                        "market_probability": None,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-missing",
                        "token_id": "[REDACTED]",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-missing",
                        "order_id": "ord-missing",
                        "token_id": "[REDACTED]",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(str(rows[0].get("outcome_truth_status") or ""), "unknown_missing_decision_reference")

    def test_outcome_truth_audit_emits_usability_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-usability"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 2.0,
                        "decision_reference_midpoint": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-1",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 2.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertIn("attribution_usability_ratio", payload)
            self.assertIn("recoverable_but_missing_count", payload)
            self.assertIn("decision_reference_recovered_count", payload)
            self.assertIn("eval_reference_recovered_count", payload)

    def test_outcome_truth_audit_maker_fallback_linkage_unique_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-maker-fallback-unique"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-1",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-1",
                        "action_taken": "none",
                        "evaluation_scope": "maker",
                        "block_reason": "edge_below_min",
                        "market_probability": 0.55,
                        "ts_decision_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-maker-1",
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "reason": "mm_quote:maker",
                        "execution_preference": "maker_preferred",
                        "decision_reference_ts_utc": "2026-03-30T00:00:00Z",
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-maker-1",
                        "order_id": "ord-maker-1",
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("maker_edge_linkage_attempted_count", -1)), 1)
            self.assertEqual(int(payload.get("maker_edge_linkage_resolved_count", -1)), 1)
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(str(row.get("decision_reference_link_status") or ""), "linked_via_target_ref_decision_ts_unique")
            self.assertTrue(bool(row.get("maker_edge_linkage_attempted")))
            self.assertTrue(bool(row.get("maker_edge_linkage_resolved")))
            self.assertEqual(str(row.get("decision_reference_basis") or ""), "edge_market_midpoint")
            self.assertEqual(str(row.get("eval_reference_basis") or ""), "edge_market_midpoint_series")

    def test_outcome_truth_audit_maker_fallback_linkage_ambiguous_stays_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-maker-fallback-ambiguous"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-amb",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-amb",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.03,
                        "market_probability": 0.51,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-maker-amb",
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-amb",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "reason": "mm_quote:maker",
                        "execution_preference": "maker_preferred",
                        "decision_reference_ts_utc": "2026-03-30T00:00:00Z",
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-maker-amb",
                        "order_id": "ord-maker-amb",
                        "token_id": "tok-maker",
                        "target_ref": "maker-ref-amb",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("maker_edge_linkage_attempted_count", -1)), 1)
            self.assertEqual(int(payload.get("maker_edge_linkage_ambiguous_count", -1)), 1)
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(str(row.get("decision_reference_link_status") or ""), "maker_target_ref_decision_ts_ambiguous")
            self.assertEqual(str(row.get("outcome_truth_status") or ""), "unknown_missing_linkage")
            self.assertTrue(bool(row.get("maker_edge_linkage_ambiguous")))

    def test_outcome_truth_audit_emits_filled_cohort_metrics_alongside_submit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-filled-cohort-split"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "decision_reference_midpoint": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-complete",
                        "order_id": "ord-complete",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 1.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-incomplete",
                        "token_id": "tok-2",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.01,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-incomplete",
                        "token_id": "tok-2",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "reason": "mm_quote:maker",
                        "execution_preference": "maker_preferred",
                        "target_ref": "maker-ref-2",
                        "decision_reference_ts_utc": "2026-03-30T00:00:10Z",
                        "ts_decision_utc": "2026-03-30T00:00:10Z",
                        "ts_utc": "2026-03-30T00:00:10Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:20Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            self.assertEqual(int(payload.get("total_outcome_records", -1)), 2)
            self.assertEqual(int(payload.get("complete_outcome_records", -1)), 1)
            self.assertEqual(int(payload.get("filled_total", -1)), 1)
            self.assertEqual(int(payload.get("filled_complete", -1)), 1)
            self.assertEqual(int(payload.get("filled_unknown", -1)), 0)
            self.assertAlmostEqual(float(payload.get("filled_complete_ratio", -1.0)), 1.0)
            self.assertAlmostEqual(float(payload.get("attribution_usability_ratio", -1.0)), 0.5)

    def test_outcome_truth_audit_prefers_decision_reference_ts_utc_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-anchor-precedence"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-anchor",
                        "target_ref": "anchor-ref-1",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.02,
                        "market_probability": 0.50,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "token_id": "tok-anchor",
                        "target_ref": "anchor-ref-1",
                        "action_taken": "maker",
                        "evaluation_scope": "maker",
                        "edge_value": 0.03,
                        "market_probability": 0.60,
                        "ts_decision_utc": "2026-03-30T00:00:02Z",
                        "ts_utc": "2026-03-30T00:00:02Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-anchor-1",
                        "token_id": "tok-anchor",
                        "target_ref": "anchor-ref-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "reason": "mm_quote:maker",
                        "execution_preference": "maker_preferred",
                        "decision_reference_ts_utc": "2026-03-30T00:00:00Z",
                        "ts_decision_utc": "2026-03-30T00:00:02Z",
                        "ts_utc": "2026-03-30T00:00:02Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-anchor-1",
                        "order_id": "ord-anchor-1",
                        "token_id": "tok-anchor",
                        "target_ref": "anchor-ref-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 1.0,
                        "ts_utc": "2026-03-30T00:00:03Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:07Z"}])
            contract_path = self._write_contract(log_dir=root, run_id=run_id, events_path=events_path, status_path=status_path)
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(str(row.get("decision_anchor_source") or ""), "decision_reference_ts_utc")
            self.assertEqual(str(row.get("decision_anchor_ts_utc") or ""), "2026-03-30T00:00:00.000Z")
            self.assertAlmostEqual(float(row.get("mid_price_decision") or -1.0), 0.50)

    def test_outcome_truth_audit_classification_ignores_pnl_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(status_path, [{"ts_utc": "2026-03-30T00:00:06Z"}])

            def _run_case(run_id: str, with_pnl: bool) -> dict:
                events_path = root / f"events_{run_id}.jsonl"
                events = [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-1",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                ]
                if with_pnl:
                    for evt in events:
                        evt["pnl"] = 999999.0
                self._write_rows(events_path, events)
                contract_path = self._write_contract(
                    log_dir=root,
                    run_id=run_id,
                    events_path=events_path,
                    status_path=status_path,
                )
                out = run_audit(
                    log_dir=root,
                    run_id=run_id,
                    run_contract_path=contract_path,
                    policy_path=self._policy_path(),
                    session_phase="validate_postrun",
                    max_lines_per_file=0,
                )
                self.assertTrue(bool(out.get("ok")), msg=out.get("findings"))
                return out

            baseline = _run_case("rid-outcome-no-pnl", with_pnl=False)
            injected = _run_case("rid-outcome-with-pnl", with_pnl=True)
            self.assertEqual(baseline.get("decision_quality_distribution"), injected.get("decision_quality_distribution"))
            self.assertEqual(baseline.get("execution_quality_distribution"), injected.get("execution_quality_distribution"))
            self.assertEqual(baseline.get("combined_outcome_distribution"), injected.get("combined_outcome_distribution"))

    def test_outcome_truth_audit_aggregates_multi_fill_vwap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-multi"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            records_path = root / "records.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-3",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.03,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-3",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-a",
                        "order_id": "ord-3",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 4.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-b",
                        "order_id": "ord-3",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.51,
                        "size": 6.0,
                        "ts_utc": "2026-03-30T00:00:02Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.53,
                        "best_ask_price": 0.55,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            payload = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
                records_out_path=records_path,
            )
            self.assertTrue(bool(payload.get("ok")), msg=payload.get("findings"))
            rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            fill_price = float(rows[0].get("fill_price"))
            self.assertAlmostEqual(fill_price, 0.502, places=9)
            self.assertTrue(str(rows[0].get("fill_trade_id") or "").startswith("multi:"))

    def test_outcome_truth_audit_fails_on_noncanonical_policy_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-policy-fail"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(events_path, [])
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )
            bad_policy = root / "outcome_truth_policy_bad.json"
            payload = json.loads(self._policy_path().read_text(encoding="utf-8"))
            payload["evaluation_horizon_ms"] = 4000
            bad_policy.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            out = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=bad_policy,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertFalse(bool(out.get("ok")))
            findings = "\n".join(str(x) for x in out.get("findings", []))
            self.assertIn("outcome_truth_policy_horizon_must_equal:5000", findings)

    def test_outcome_truth_audit_replay_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-outcome-stable"
            events_path = root / "events_2026-03-30.jsonl"
            status_path = root / "status_2026-03-30.jsonl"
            self._write_rows(
                events_path,
                [
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "action_taken": "taker",
                        "evaluation_scope": "taker",
                        "edge_value": 0.02,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "order_submit",
                        "run_id": run_id,
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.50,
                        "size": 10.0,
                        "ts_decision_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "fill",
                        "run_id": run_id,
                        "trade_id": "tr-1",
                        "order_id": "ord-1",
                        "token_id": "tok-1",
                        "side": "BUY",
                        "price": 0.49,
                        "size": 10.0,
                        "ts_utc": "2026-03-30T00:00:01Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.49,
                        "best_ask_price": 0.51,
                        "ts_event_utc": "2026-03-30T00:00:00Z",
                        "ts_utc": "2026-03-30T00:00:00Z",
                    },
                    {
                        "event_type": "book_top",
                        "run_id": run_id,
                        "token_id": "tok-1",
                        "best_bid_price": 0.54,
                        "best_ask_price": 0.56,
                        "ts_event_utc": "2026-03-30T00:00:05Z",
                        "ts_utc": "2026-03-30T00:00:05Z",
                    },
                ],
            )
            self._write_rows(status_path, [{"run_id": run_id, "ts_utc": "2026-03-30T00:00:06Z"}])
            contract_path = self._write_contract(
                log_dir=root,
                run_id=run_id,
                events_path=events_path,
                status_path=status_path,
            )

            out1 = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            out2 = run_audit(
                log_dir=root,
                run_id=run_id,
                run_contract_path=contract_path,
                policy_path=self._policy_path(),
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertEqual(str(out1.get("outcome_records_sha256") or ""), str(out2.get("outcome_records_sha256") or ""))
            self.assertEqual(str(out1.get("policy_sha256") or ""), str(out2.get("policy_sha256") or ""))
            self.assertEqual(str(out1.get("audit_rule_set_sha256") or ""), str(out2.get("audit_rule_set_sha256") or ""))
            self.assertEqual(list(out1.get("findings", [])), list(out2.get("findings", [])))
            out1_hash = hashlib.sha256(
                json.dumps(out1, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            out2_hash = hashlib.sha256(
                json.dumps(out2, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            self.assertEqual(out1_hash, out2_hash)


if __name__ == "__main__":
    unittest.main()
