import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.paper_harness_realism_contract import (
    EXERCISED_HARNESS_REALISM_FIELD,
    HARNESS_REALISM_BREAKDOWN_KEYS,
    HARNESS_REALISM_GRADE_AUTHORITY,
    HARNESS_REALISM_GRADE_SEMANTICS,
    normalize_nightly_exercised_harness_realism,
)
from prodesk.historical_recovery_replay_compat import (
    HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_FIELD as _HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_SEC_FIELD,
    HISTORICAL_RECOVERY_ACTIVE_FIELD as _HISTORICAL_RECOVERY_ACTIVE_FIELD,
    HISTORICAL_RECOVERY_REASON_FIELD as _HISTORICAL_RECOVERY_REASON_FIELD,
)
from scripts.nightly_soak_report import (
    _maker_blocker_ledger_bundle,
    _maker_probe_rows_with_snapshot_truth,
    _maker_phase_allowed_from_row,
    _maker_quote_construction_bundle,
    _maker_selection_authority_bundle,
    _maker_survival_counterfactual_summary,
    _maker_truth_readiness,
    _maker_zero_submit_root_cause_bundle,
    _write_support_artifacts,
    build_report,
    render_human_summary,
)


_HISTORICAL_PREEXPIRY_EMERGENCY_EVENT = "preexpiry_emergency_taker_unwind"
_HISTORICAL_RECOVERY_REASON = "preexpiry_reduce_only_window_active"
_HISTORICAL_RECOVERY_WAITING_BLOCK_REASON = "reduce_only_recovery_waiting_for_maker_exit"
_HISTORICAL_RECOVERY_SIZE_CAP_BELOW_MIN_ORDER_SIZE = (
    "reduce_only_recovery_size_cap_below_min_order_size"
)
_HISTORICAL_RECOVERY_TOUCH_PRICE_UNAVAILABLE = (
    "reduce_only_recovery_touch_price_unavailable"
)
_HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED = "maker_to_taker_recovery_handoff_disabled"


def _historical_recovery_lineage(active=True, reason=_HISTORICAL_RECOVERY_REASON, **overrides):
    payload = {_HISTORICAL_RECOVERY_ACTIVE_FIELD: bool(active)}
    if active and reason is not None:
        payload[_HISTORICAL_RECOVERY_REASON_FIELD] = reason
    payload.update(overrides)
    return payload


def _historical_taker_authority_flags(
    *,
    taker_phase_allowed=False,
    **overrides,
):
    payload = {
        "taker_phase_allowed": taker_phase_allowed,
    }
    payload.update(overrides)
    return payload


def _historical_recovery_runtime_config(
    *,
    held_preexpiry_reduce_only_sec=50.0,
    preexpiry_emergency_taker_window_sec=45.0,
    terminal_unwind_halt_new_risk_sec=45.0,
    **overrides,
):
    payload = {
        "held_preexpiry_reduce_only_sec": held_preexpiry_reduce_only_sec,
        _HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_SEC_FIELD: preexpiry_emergency_taker_window_sec,
        "terminal_unwind_halt_new_risk_sec": terminal_unwind_halt_new_risk_sec,
    }
    payload.update(overrides)
    return payload


class NightlySoakReportTests(unittest.TestCase):
    def test_maker_blocker_ledger_promotes_sizing_feasibility_after_steel_selection_verdicts(self):
        bundle = _maker_blocker_ledger_bundle(
            maker_edge_rows=[
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "maker_gate_stage": "lane_readiness_gate",
                    "maker_gate_reason": "maker_timing_gate_closed",
                    "maker_gate_owner_family": "executor_lane_readiness",
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "maker_gate_stage": "selection_gate",
                    "maker_gate_reason": "launch_safe_selection_insufficient_depth_multiple",
                    "maker_gate_owner_family": "order_manager_selection",
                },
            ],
            snapshot_rows=[
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple", "visible_depth_notional_usd": 0.0},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple", "visible_depth_notional_usd": 10.0},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple", "visible_depth_notional_usd": 15.0},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple", "visible_depth_notional_usd": 25.0},
            ],
            selection_authority_rows=[
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple"},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple"},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple"},
                {"runtime_decision_block_reason": "launch_safe_selection_insufficient_depth_multiple"},
                {"runtime_decision_block_reason": "sizing_reject"},
                {"runtime_decision_block_reason": "sizing_reject"},
                {"runtime_decision_block_reason": "sizing_reject"},
            ],
        )
        summary = bundle["summary"]
        self.assertEqual(summary.get("maker_blocker_ledger_version"), 3)
        selection_truth = summary.get("later_stage_runtime_diagnostic_truth", {})
        self.assertEqual(selection_truth.get("selection_gate_verdict"), "keep_now_steel")
        self.assertEqual(selection_truth.get("sizing_verdict"), "active_patient")
        self.assertEqual(
            selection_truth.get("runtime_blocker_family_status"),
            "open_runtime_patient",
        )
        self.assertNotIn("quote_quality_verdict", selection_truth)
        self.assertNotIn("next_packet2_patient", selection_truth)
        self.assertNotIn("dominant_runtime_blocker_reason", summary)
        self.assertNotIn("dominant_runtime_blocker_bucket", summary)
        self.assertNotIn("runtime_reason_distribution", summary)
        self.assertNotIn("runtime_bucket_distribution", summary)
        self.assertNotIn("closed_family_assertions", summary)
        self.assertEqual(summary.get("historical_family_labels_retired_from_active_owner_surfaces"), True)
        whole_lane = summary.get("whole_lane_runtime_blocker_truth", {})
        self.assertEqual(
            whole_lane.get("maker_gate_stage_distribution"),
            {"lane_readiness_gate": 1, "selection_gate": 1},
        )
        self.assertEqual(
            whole_lane.get("maker_gate_owner_family_distribution"),
            {"executor_lane_readiness": 1, "order_manager_selection": 1},
        )
        self.assertEqual(
            whole_lane.get("dominant_earliest_blocker_reason"),
            "launch_safe_selection_insufficient_depth_multiple",
        )

    def test_maker_probe_rows_with_snapshot_truth_prefers_canonical_lifecycle_window(self):
        rows = _maker_probe_rows_with_snapshot_truth(
            probe_rows=[
                {
                    "token_id": "token-alpha",
                    "target_side_ref": "target-alpha|SELL",
                    "ts_decision_utc": "2026-05-18T03:59:45.723Z",
                    "sec_to_expiry": 14.304199,
                    "lifecycle_phase": "maker_window",
                    "selection_gate_min_sec_to_expiry": 90.0,
                    "selection_gate_max_sec_to_expiry": None,
                }
            ],
            snapshot_rows=[
                {
                    "token_id": "token-alpha",
                    "target_side_ref": "target-alpha|SELL",
                    "ts_decision_utc": "2026-05-18T03:59:45.715Z",
                    "decision_result": "submitted",
                    "order_submit_id": "paper-order-2",
                }
            ],
            run_manifest={
                "config": {
                    "strategy": {
                        "maker_market_viability": {
                            "selection_gate": {
                                "min_sec_to_expiry": 90.0,
                            }
                        }
                    }
                }
            },
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("matched_snapshot_present"), True)
        self.assertEqual(row.get("matched_snapshot_decision_result"), "submitted")
        self.assertEqual(row.get("launch_safe_selection_timing_window_met"), True)

    def test_maker_zero_submit_root_cause_bundle_steps_aside_when_maker_participated(self):
        root_cause = _maker_zero_submit_root_cause_bundle(
            probe_rows=[],
            snapshot_rows=[
                {
                    "decision_result": "selection_rejected",
                    "selection_gate_primary_reject_reason": "insufficient_depth_multiple",
                },
                {
                    "decision_result": "submitted",
                    "order_submit_id": "paper-order-2",
                },
            ],
            quote_starvation_rows=[],
            quote_starvation_summary={},
            waterfall={"stages": {"selection_rejected_rows": {"count": 0}}},
            runtime_classification={"runtime_class_name": "VALID_ACTIVE"},
            truth_rows=[],
        )
        self.assertEqual(
            root_cause.get("zero_submit_classification"),
            "not_applicable_maker_participated",
        )
        self.assertEqual(root_cause.get("decision_readiness"), "not_applicable_submit_run")
        self.assertEqual(root_cause.get("authoritative_for_canonical_selection"), False)
        self.assertEqual(root_cause.get("current_owner_artifact"), "maker_blocker_ledger.json")
        self.assertEqual(int(root_cause.get("submitted_snapshot_row_count", 0)), 1)

    def test_build_report_maker_participation_waterfall_uses_runtime_lane_readiness_owner_not_secondary_oracle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-maker-lane-readiness-waterfall"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "strategy": {
                                "maker_market_viability": {
                                    "selection_gate": {
                                        "min_sec_to_expiry": 6.0,
                                        "max_sec_to_expiry": 9.0,
                                    }
                                }
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-lag",
                    "target_ref": "target-lag",
                    "target_side_ref": "target-lag|BUY",
                    "ts_decision_utc": "2026-01-01T12:00:12Z",
                    "time_remaining_sec": 8.0,
                    "lifecycle_phase": "maker_window",
                    "maker_phase_allowed": True,
                    "maker_gate_open": True,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "none",
                    "block_reason": "maker_timing_gate_closed",
                    "maker_gate_stage": "lane_readiness_gate",
                    "maker_gate_reason": "maker_timing_gate_closed",
                    "maker_gate_owner_family": "executor_lane_readiness",
                    "maker_gate_terminal": True,
                    "fair_probability": 0.58,
                    "market_probability": 0.50,
                    "edge_value": 0.08,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "secondary_oracle_status": "direction_mismatch",
                    "secondary_oracle_confirmation": False,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 100.0,
                    **_historical_recovery_lineage(active=False),
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            waterfall = support_artifacts.get("maker_participation_waterfall", {})
            blocker_ledger = support_artifacts.get("maker_blocker_ledger", {})
            self.assertEqual(
                waterfall.get("canonical_gate_truth", {}).get("maker_gate_stage_distribution"),
                {"lane_readiness_gate": 1},
            )
            self.assertEqual(
                waterfall.get("authoritative_for_runtime_blocker_truth"),
                False,
            )
            self.assertEqual(
                waterfall.get("current_owner_artifact"),
                "maker_blocker_ledger.json",
            )
            self.assertEqual(
                int(waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get("count", 0)),
                0,
            )
            self.assertEqual(
                waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get(
                    "loss_reason_distribution", {}
                ),
                {"maker_timing_gate_closed": 1},
            )
            self.assertNotIn(
                "secondary_oracle_not_confirmed",
                waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get(
                    "loss_reason_distribution", {}
                ),
            )
            self.assertEqual(
                blocker_ledger.get("whole_lane_runtime_blocker_truth", {}).get(
                    "dominant_earliest_blocker_reason"
                ),
                "maker_timing_gate_closed",
            )

    def test_maker_phase_allowed_helper_prefers_canonical_lifecycle_truth(self):
        self.assertTrue(
            _maker_phase_allowed_from_row(
                {
                    "lifecycle_phase": "maker_window",
                    "maker_phase_allowed": True,
                }
            )
        )
        self.assertTrue(_maker_phase_allowed_from_row({"lifecycle_phase": "maker_window"}))
        self.assertFalse(_maker_phase_allowed_from_row({"lifecycle_phase": "prepare"}))

    def test_maker_quote_construction_bundle_classifies_cleanup_and_snapshot_gap_residue(self):
        bundle = _maker_quote_construction_bundle(
            truth_rows=[
                {
                    "truth_readiness_state": "authoritative_complete",
                    "desired_quote_present": None,
                    "maker_no_submission_cause": None,
                    "maker_no_submission_category": None,
                    "block_reason": "open_order_cleanup_required",
                    "open_maker_orders_total": 2,
                    "open_orders_for_token_count": 1,
                    "open_orders_same_side_count": 1,
                    "same_target_submit_count_prior": 1,
                    "same_target_side_submit_count_prior": 1,
                    "fair_probability": 0.55,
                    "market_probability": 0.50,
                    "geometry_viable": True,
                    "depth_multiple_vs_cannon_target": 2.0,
                    "favored_side_depth_truth_state": "nonzero_visible",
                    "lineage_stage": "EXTREME_ONLY",
                },
                {
                    "truth_readiness_state": "authoritative_complete",
                    "desired_quote_present": None,
                    "maker_no_submission_cause": None,
                    "maker_no_submission_category": None,
                    "block_reason": None,
                    "open_maker_orders_total": 0,
                    "open_orders_for_token_count": 0,
                    "open_orders_same_side_count": 0,
                    "same_target_submit_count_prior": 1,
                    "same_target_side_submit_count_prior": 0,
                    "fair_probability": 0.55,
                    "market_probability": 0.50,
                    "geometry_viable": True,
                    "depth_multiple_vs_cannon_target": 2.0,
                    "favored_side_depth_truth_state": "nonzero_visible",
                    "lineage_stage": "EXTREME_ONLY",
                },
            ]
        )
        rows = bundle["rows"]
        self.assertEqual(rows[0]["quote_construction_primary_cause"], "open_order_cleanup_required")
        self.assertEqual(rows[1]["quote_construction_primary_cause"], "resting_quote_already_live_or_snapshot_gap")
        self.assertEqual(
            bundle["summary"]["quote_construction_primary_cause_distribution"],
            {
                "open_order_cleanup_required": 1,
                "resting_quote_already_live_or_snapshot_gap": 1,
            },
        )

    def test_maker_selection_authority_counterfactual_replays_certified_keep_block_set(self):
        snapshot_rows = [
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-1",
                "order_submit_id": "paper-order-1",
                "target_ref": "c09c10c6949dcaee",
                "target_side_ref": "c09c10c6949dcaee|BUY",
                "side": "BUY",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:29:04.215Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-2",
                "order_submit_id": "paper-order-2",
                "target_ref": "466f27bd7f1d3019",
                "target_side_ref": "466f27bd7f1d3019|SELL",
                "side": "SELL",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:30:04.215Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-3",
                "order_submit_id": "paper-order-3",
                "target_ref": "55847ba47a05dc46",
                "target_side_ref": "55847ba47a05dc46|SELL",
                "side": "SELL",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:34:01.754Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-4",
                "order_submit_id": "paper-order-4",
                "target_ref": "55847ba47a05dc46",
                "target_side_ref": "55847ba47a05dc46|BUY",
                "side": "BUY",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:34:04.765Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-5",
                "order_submit_id": "paper-order-5",
                "target_ref": "b446a3128a6a1252",
                "target_side_ref": "b446a3128a6a1252|SELL",
                "side": "SELL",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:35:04.215Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-6",
                "order_submit_id": "paper-order-6",
                "target_ref": "aa50e402749083c5",
                "target_side_ref": "aa50e402749083c5|SELL",
                "side": "SELL",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:36:04.215Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
            {
                "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                "market_snapshot_id": "shadow-7",
                "order_submit_id": "paper-order-7",
                "target_ref": "aa50e402749083c5",
                "target_side_ref": "aa50e402749083c5|SELL",
                "side": "SELL",
                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                "lifecycle_phase": "maker_window",
                "ts_decision_utc": "2026-04-29T11:36:07.215Z",
                "decision_result": "submitted",
                "secondary_oracle_confirmation": True,
            },
        ]
        snapshot_meta_by_order_id = {
            "paper-order-1": {
                "edge_abs": 0.22,
                "one_sided_edge_threshold_abs": 0.15,
                "maker_timing_gate_open": True,
                "market_reference_class": "authoritative",
                "one_sided_allowed_phase": True,
                "one_sided_allowed_authority": True,
            },
            "paper-order-3": {
                "edge_abs": 0.04181324922988065,
                "one_sided_edge_threshold_abs": 0.15,
                "maker_timing_gate_open": True,
                "market_reference_class": "authoritative",
                "one_sided_allowed_phase": False,
                "one_sided_allowed_authority": True,
            },
        }
        for row in snapshot_rows:
            row.update(snapshot_meta_by_order_id.get(str(row.get("order_submit_id") or ""), {}))
        events = []
        for order_id, one_sided_active, side_policy, side in [
            ("paper-order-1", True, "BUY_ONLY", "BUY"),
            ("paper-order-2", True, "SELL_ONLY", "SELL"),
            ("paper-order-3", False, "TWO_SIDED", "SELL"),
            ("paper-order-4", False, "TWO_SIDED", "BUY"),
            ("paper-order-5", True, "SELL_ONLY", "SELL"),
            ("paper-order-6", True, "SELL_ONLY", "SELL"),
            ("paper-order-7", True, "SELL_ONLY", "SELL"),
        ]:
            events.append(
                {
                    "event_type": "order_submit",
                    "run_id": "9563888b-bca3-4073-b7ec-71752928ec67",
                    "order_id": order_id,
                    "submission_lane": "maker",
                    "side": side,
                    "maker_market_viability": {
                        "one_sided_active": one_sided_active,
                        "side_policy": side_policy,
                        "market_reference_mode": "direct_midpoint",
                    },
                }
            )
        outcome_truth_records = [
            {
                "order_submit_id": f"paper-order-{idx}",
                "decision_quality": "correct" if idx in {1, 2} else "incorrect",
                "execution_quality": "favorable",
                "edge_realized_x_size": 100.0 - idx,
                "outcome_truth_status": "complete",
            }
            for idx in range(1, 8)
        ]
        bundle = _maker_selection_authority_bundle(
            events=events,
            snapshot_rows=snapshot_rows,
            outcome_truth_records=outcome_truth_records,
            run_manifest={"config": {"profile": {"name": "paper_universal"}}},
            run_id="9563888b-bca3-4073-b7ec-71752928ec67",
        )
        counterfactual = bundle["counterfactual"]
        self.assertEqual(
            counterfactual.get("keep_order_submit_ids"),
            [
                "paper-order-1",
                "paper-order-2",
                "paper-order-3",
                "paper-order-5",
                "paper-order-6",
            ],
        )
        self.assertEqual(counterfactual.get("block_order_submit_ids"), ["paper-order-4", "paper-order-7"])
        summary = bundle["audit"]
        self.assertEqual(
            summary.get("blocked_count_by_canonical_reject_reason", {}),
            {"selection_prior_target_submit": 2},
        )
        self.assertEqual(
            summary.get("blocked_count_by_canonical_reject_reason", {}).get("selection_prior_target_submit"),
            2,
        )
        summary_row_by_order_id = {
            str(row.get("order_submit_id") or ""): row for row in bundle["audit"].get("rows", [])
        }
        row_by_order_id = {
            str(row.get("order_submit_id") or ""): row for row in bundle["counterfactual"].get("rows", [])
        }
        admitted_row = dict(row_by_order_id["paper-order-3"])
        self.assertEqual(summary_row_by_order_id["paper-order-3"].get("one_sided_allowed_authority"), True)
        self.assertEqual(admitted_row.get("edge_abs"), 0.04181324922988065)
        self.assertEqual(admitted_row.get("one_sided_edge_threshold_abs"), 0.15)
        self.assertEqual(admitted_row.get("timing_gate_open"), True)
        self.assertEqual(admitted_row.get("market_reference_class"), "authoritative")
        self.assertEqual(admitted_row.get("maker_phase_allowed"), True)
        self.assertEqual(admitted_row.get("one_sided_allowed_phase"), False)
        self.assertEqual(admitted_row.get("one_sided_allowed_authority"), True)
        self.assertEqual(admitted_row.get("one_sided_active"), False)
        self.assertEqual(admitted_row.get("counterfactual_decision"), "admitted")
        self.assertEqual(admitted_row.get("counterfactual_primary_reject_reason"), None)

    def test_maker_selection_authority_counterfactual_blocks_insufficient_depth_multiple(self):
        bundle = _maker_selection_authority_bundle(
            events=[],
            snapshot_rows=[
                {
                    "run_id": "rid-depth-block",
                    "market_snapshot_id": "shadow-depth-1",
                    "target_ref": "target-depth",
                    "target_side_ref": "target-depth|BUY",
                    "side": "BUY",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "lifecycle_phase": "maker_window",
                    "ts_decision_utc": "2026-04-29T11:40:04.215Z",
                    "decision_result": "viability_rejected",
                    "decision_block_reason": "launch_safe_selection_insufficient_depth_multiple",
                    "selection_gate_primary_reject_reason": "insufficient_depth_multiple",
                    "selection_gate_all_reject_reasons": ["insufficient_depth_multiple"],
                    "secondary_oracle_confirmation": True,
                    "cannon_depth_requirement_met": False,
                    "cannon_min_depth_multiple": 1.5,
                    "depth_multiple_vs_cannon_target": 0.4,
                }
            ],
            outcome_truth_records=[],
            run_manifest={"config": {"profile": {"name": "paper_universal"}}},
            run_id="rid-depth-block",
        )
        summary = bundle["audit"]
        self.assertEqual(
            summary.get("counterfactual_decision_distribution"),
            {"blocked": 1},
        )
        self.assertEqual(
            summary.get("blocked_count_by_canonical_reject_reason"),
            {"insufficient_depth_multiple": 1},
        )

    def test_maker_survival_counterfactual_treats_commitment_window_end_as_terminal_cleanup(self):
        classification, counterfactuals = _maker_survival_counterfactual_summary(
            {
                "survival_plane": {
                    "cancel_reason": "commitment_window_ended",
                    "cancel_class": "terminal_window_end",
                    "counterfactuals": {
                        "counterfactual_a_certified_quote": {"depth_requirement_met": False},
                        "counterfactual_b_resting_submitted_quote": {"depth_requirement_met": False},
                        "counterfactual_c_entry_gate_only": {"cancel_would_still_be_required": False},
                    },
                }
            }
        )
        self.assertEqual(classification, "terminal_commitment_window_end_cleanup")
        self.assertIsInstance(counterfactuals, dict)

    def test_maker_truth_readiness_marks_one_sided_missing_midpoint(self):
        readiness, primary = _maker_truth_readiness(
            {
                "market_reference_class": "not_available",
                "market_reference_mode": "missing",
                "pair_truth_basis": "one_sided_ws_missing_midpoint",
            }
        )
        self.assertEqual(readiness, "missing_truth_input")
        self.assertEqual(primary, "one_sided_ws_missing_midpoint")

    def test_write_support_artifacts_marks_zero_submit_artifacts_non_authoritative_on_submit_runs(self):
        with tempfile.TemporaryDirectory() as td:
            report_dir = Path(td)
            support_artifacts = {
                "maker_market_snapshot_rows": [
                    {
                        "market_snapshot_id": "shadow-1",
                        "decision_result": "submitted",
                        "target_ref": "target-alpha",
                        "target_side_ref": "target-alpha|BUY",
                        "side": "BUY",
                    }
                ],
                "maker_market_snapshot_summary": {},
                "maker_market_snapshot_calibration_audit": {},
                "maker_cannon_late_window_probe_rows": [],
                "maker_cannon_late_window_probe_summary": {},
                "maker_mid_window_probe_rows": [],
                "maker_mid_window_probe_summary": {},
                "maker_quote_starvation_audit_rows": [],
                "maker_quote_starvation_summary": {},
                "maker_truth_reference_starvation_rows": [],
                "maker_truth_reference_starvation_summary": {},
                "maker_quote_construction_audit_rows": [],
                "maker_quote_construction_summary": {},
                "maker_participation_waterfall": {},
                "maker_timing_band_diagnostic_matrix": {},
                "maker_timing_band_decision": {},
                "maker_zero_submit_root_cause_audit": {"known_truths": {}},
                "maker_quote_integrity_manifest": {},
                "maker_quote_integrity_trace_rows": [],
                "maker_execution_quality_semantics": {},
                "maker_quote_mutation_summary": {},
                "maker_resting_order_survival_audit": {},
                "maker_quote_integrity_summary": {},
                "maker_selection_authority_audit": {},
                "maker_selection_authority_counterfactual": {},
                "financial_outcome_reconciliation": {"status": "quantities_distinct_fill_notional_reconciled"},
            }
            _write_support_artifacts(report_dir, support_artifacts)
            for name in (
                "maker_participation_waterfall.json",
                "maker_quote_construction_summary.json",
                "maker_truth_reference_starvation_summary.json",
                "maker_market_snapshot_calibration_audit.json",
            ):
                payload = json.loads((report_dir / name).read_text(encoding="utf-8"))
                self.assertEqual(payload.get("authoritative_for_canonical_selection"), False)
                self.assertEqual(payload.get("applicability"), "descriptive_only")
                self.assertEqual(payload.get("current_owner_artifact"), "maker_blocker_ledger.json")
            zero_submit_payload = json.loads(
                (report_dir / "maker_zero_submit_root_cause_audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(zero_submit_payload.get("authoritative_for_canonical_selection"), False)
            self.assertEqual(zero_submit_payload.get("applicability"), "not_applicable_submit_run")
            self.assertEqual(
                zero_submit_payload.get("current_owner_artifact"),
                "maker_blocker_ledger.json",
            )
            self.assertEqual(
                zero_submit_payload.get("authoritative_for_runtime_blocker_truth"),
                False,
            )
            quote_integrity_payload = json.loads(
                (report_dir / "maker_quote_integrity_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                quote_integrity_payload.get("authoritative_for_runtime_blocker_truth"),
                False,
            )
            self.assertEqual(
                quote_integrity_payload.get("authoritative_for_current_owner_law"),
                False,
            )
            self.assertEqual(
                quote_integrity_payload.get("current_owner_artifact"),
                "maker_blocker_ledger.json",
            )
            reconciliation_payload = json.loads(
                (report_dir / "financial_outcome_reconciliation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                reconciliation_payload.get("applicability"),
                "support_only_cross_surface_reconciliation",
            )
            self.assertEqual(
                reconciliation_payload.get("status"),
                "quantities_distinct_fill_notional_reconciled",
            )

    def test_build_report_basic_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "risk_reject", "reason": "stale_book"},
                {"event_type": "book_top", "token_id": "t1", "midpoint": 0.51},
                {"event_type": "order_submit", "order_id": "o1", "reason": "taker_chainlink"},
                {"event_type": "taker_submit", "token_id": "t1"},
                {"event_type": "fill", "order_id": "o1", "token_id": "t1", "side": "BUY", "price": 0.50, "size": 10},
            ]
            status = [
                {"gauge.open_orders": 1},
                {"gauge.open_orders": 0},
            ]
            errors = [{"component": "market_data"}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("\n".join(json.dumps(x) for x in errors) + "\n", encoding="utf-8")

            report = build_report(root)
            self.assertEqual(report.get("schema_version"), 2)
            self.assertEqual(report["reject_reason_distribution"].get("stale_book"), 1)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 0.5)
            self.assertEqual(report["errors_by_component"].get("market_data"), 1)
            self.assertEqual(report["edge_activation_quality_by_signal_posture"], {})
            self.assertIn("latency_distribution_ms", report)
            self.assertIn("taker", report)
            self.assertNotIn("sniper", report)
            self.assertIn("execution_paths", report)
            self.assertIn("financial_performance", report)
            self.assertIn("financial_outcome_reconciliation", report)
            self.assertIn("edge_truth", report)
            self.assertIn(EXERCISED_HARNESS_REALISM_FIELD, report)
            self.assertNotIn("harness_realism_grade", report)
            self.assertNotIn("harness_realism_grade_breakdown", report)
            self.assertNotIn("harness_realism_grade_semantics", report)
            self.assertNotIn("harness_realism_grade_authority", report)
            exercised = report[EXERCISED_HARNESS_REALISM_FIELD]
            self.assertIsInstance(exercised, dict)
            self.assertIn("grade", exercised)
            self.assertIn("breakdown", exercised)
            self.assertIn("semantics", exercised)
            self.assertIn("authority", exercised)
            self.assertIn("taker_lineage_stage_net_breakout", report)
            self.assertIn("mode_transitions", report)
            self.assertIn("pickoff_indicator", report)
            self.assertIn("runtime_classification", report)
            self.assertIn("runtime_resource", report)
            self.assertIn("primary_suppression_cause", report)
            self.assertIn("contributing_suppression_causes", report)
            self.assertIn("suppression_dominated_run", report)
            self.assertIn("execution_starvation_mode", report)
            self.assertIn("protected_no_trade_explanation", report)
            self.assertIn("control_authority_clarity", report)
            self.assertIn("protection_path_trigger_chain", report)
            self.assertIn("lifecycle_residue", report)
            self.assertIn("execution_quality_lane_attribution", report)
            self.assertIn("execution_quality_decision_reference_lane_attribution", report)
            self.assertNotIn("reduce_only_recovery", report)
            self.assertNotIn("preexpiry_recovery_churn", report)
            self.assertIn("taker_opportunity_suppression", report)
            self.assertIn("taker_doctrine_breaches", report)
            self.assertIn("taker_intent_gate_posture_matrix", report)
            self.assertNotIn("recovery_cost_benefit", report)
            self.assertGreaterEqual(report["execution_paths"].get("maker_submits", 0.0), 0.0)
            self.assertEqual(report["edge_truth"].get("rows_total"), 0.0)

    def test_build_report_includes_financial_performance_by_lane(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-financial-by-lane"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "order_submit",
                    "order_id": "maker-order-1",
                    "submission_lane": "maker",
                    "reason": "maker_quote",
                    "token_id": "tok-maker",
                    "price": 0.40,
                    "size": 10.0,
                    "ts_utc": "2026-01-01T00:00:00Z",
                    "run_id": run_id,
                },
                {
                    "event_type": "fill",
                    "order_id": "maker-order-1",
                    "token_id": "tok-maker",
                    "side": "BUY",
                    "price": 0.40,
                    "size": 10.0,
                    "ts_utc": "2026-01-01T00:00:01Z",
                    "run_id": run_id,
                },
                {
                    "event_type": "wallet_position_settled",
                    "token_id": "tok-maker",
                    "settlement_side": "SELL",
                    "settlement_size_shares": 10.0,
                    "settlement_price": 1.0,
                    "ts_utc": "2026-01-01T00:05:00Z",
                    "run_id": run_id,
                },
                {
                    "event_type": "order_submit",
                    "order_id": "taker-order-1",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "token_id": "tok-taker",
                    "price": 0.90,
                    "size": 5.0,
                    "ts_utc": "2026-01-01T00:10:00Z",
                    "run_id": run_id,
                },
                {
                    "event_type": "fill",
                    "order_id": "taker-order-1",
                    "token_id": "tok-taker",
                    "side": "SELL",
                    "price": 0.90,
                    "size": 5.0,
                    "ts_utc": "2026-01-01T00:10:01Z",
                    "run_id": run_id,
                },
                {
                    "event_type": "wallet_position_settled",
                    "token_id": "tok-taker",
                    "settlement_side": "BUY",
                    "settlement_size_shares": 5.0,
                    "settlement_price": 1.0,
                    "ts_utc": "2026-01-01T00:15:00Z",
                    "run_id": run_id,
                },
            ]
            status = [
                {
                    "run_id": run_id,
                    "gauge.open_orders": 0,
                    "gauge.total_pnl": 5.5,
                    "ts_utc": "2026-01-01T00:15:05Z",
                    "wallet_contract": {
                        "stable_balance_total": 1000.0,
                        "deployable_capital": 900.0,
                        "protected_reserve": 100.0,
                        "authority_status_class": "authoritative",
                    },
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "config": {
                            "wallet": {
                                "paper_starting_usdc": 1000.0,
                                "protected_usdc_reserve": 100.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "outcome_truth_audit.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "run_claim_boundary": {
                            "claim_scope": "observational_only_fixed_horizon_outcome_truth",
                        },
                        "lane_outcome_truth": {
                            "maker": {
                                "claim_boundary": {
                                    "interpretation": "directional_fixed_horizon_edge_realization_not_ledger_pnl",
                                },
                                "fill_notional_sum": 4.0,
                                "edge_realized_x_size_sum": 3.0,
                                "execution_component_x_size_sum": 2.5,
                                "decision_component_x_size_sum": 0.5,
                                "complete_outcome_records": 1,
                                "total_outcome_records": 1,
                                "unknown_outcome_records": 0,
                            },
                            "normal_taker": {
                                "claim_boundary": {
                                    "interpretation": "directional_fixed_horizon_edge_realization_not_ledger_pnl",
                                },
                                "fill_notional_sum": 4.5,
                                "edge_realized_x_size_sum": -0.25,
                                "execution_component_x_size_sum": -0.25,
                                "decision_component_x_size_sum": 0.0,
                                "complete_outcome_records": 1,
                                "total_outcome_records": 1,
                                "unknown_outcome_records": 0,
                            },
                        },
                        "complete_outcome_records": 2,
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            financial = report.get("financial_performance", {})
            financial_outcome_reconciliation = report.get("financial_outcome_reconciliation", {})
            capital_progression = financial.get("capital_progression", {})
            overall = financial.get("overall", {})
            by_lane = financial.get("by_lane", {})
            maker = by_lane.get("maker", {})
            taker = by_lane.get("taker", {})

            self.assertEqual(int(financial.get("ledger_version") or 0), 2)
            self.assertAlmostEqual(float(capital_progression.get("configured_base_capital_usd") or 0.0), 1000.0, places=6)
            self.assertAlmostEqual(
                float(capital_progression.get("configured_starting_deployable_capital_usd") or 0.0),
                900.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(capital_progression.get("opening_wallet_deployable_capital_usd") or 0.0),
                900.0,
                places=6,
            )
            self.assertTrue(bool(capital_progression.get("opening_wallet_matches_configured_base_capital")))
            self.assertTrue(bool(capital_progression.get("opening_wallet_matches_configured_deployable_capital")))
            self.assertAlmostEqual(
                float(capital_progression.get("ending_wallet_stable_balance_total_usd") or 0.0),
                1000.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(capital_progression.get("ending_wallet_deployable_capital_usd") or 0.0),
                900.0,
                places=6,
            )
            self.assertAlmostEqual(float(overall.get("net_pnl_usd") or 0.0), 5.5, places=6)
            self.assertAlmostEqual(float(financial.get("latest_total_pnl_usd") or 0.0), 5.5, places=6)
            self.assertEqual(
                str(financial.get("latest_total_pnl_source") or ""),
                "status_gauge_total_pnl_reconciled",
            )
            self.assertAlmostEqual(float(financial.get("latest_status_total_pnl_usd") or 0.0), 5.5, places=6)
            self.assertTrue(bool(financial.get("reconciled_with_status_total_pnl")))
            self.assertEqual(int(overall.get("closed_trade_count") or 0), 2)
            self.assertAlmostEqual(float(overall.get("win_rate") or 0.0), 0.5, places=6)
            self.assertAlmostEqual(float(overall.get("avg_submitted_order_size_shares") or 0.0), 7.5, places=6)
            self.assertAlmostEqual(float(overall.get("avg_filled_order_size_shares") or 0.0), 7.5, places=6)
            self.assertAlmostEqual(float(maker.get("net_pnl_usd") or 0.0), 6.0, places=6)
            self.assertAlmostEqual(float(maker.get("win_rate") or 0.0), 1.0, places=6)
            self.assertEqual(int(maker.get("winning_trade_count") or 0), 1)
            self.assertAlmostEqual(float(maker.get("avg_submitted_order_size_shares") or 0.0), 10.0, places=6)
            self.assertAlmostEqual(float(maker.get("avg_filled_order_size_shares") or 0.0), 10.0, places=6)
            self.assertAlmostEqual(float(taker.get("net_pnl_usd") or 0.0), -0.5, places=6)
            self.assertAlmostEqual(float(taker.get("win_rate") or 0.0), 0.0, places=6)
            self.assertEqual(int(taker.get("losing_trade_count") or 0), 1)
            self.assertAlmostEqual(float(taker.get("avg_submitted_order_size_shares") or 0.0), 5.0, places=6)
            self.assertAlmostEqual(float(taker.get("avg_filled_order_size_shares") or 0.0), 5.0, places=6)
            self.assertEqual(
                financial_outcome_reconciliation.get("status"),
                "quantities_distinct_fill_notional_reconciled",
            )
            self.assertFalse(
                bool(
                    financial_outcome_reconciliation.get("claim_boundary", {}).get(
                        "numeric_equality_expected"
                    )
                )
            )
            self.assertTrue(
                bool(
                    financial_outcome_reconciliation.get("overall", {}).get(
                        "fill_notional_reconciles"
                    )
                )
            )
            self.assertTrue(
                bool(
                    financial_outcome_reconciliation.get("by_lane", {})
                    .get("maker", {})
                    .get("fill_notional_reconciles")
                )
            )

    def test_build_report_financial_performance_treats_slippage_and_adverse_as_non_cash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-financial-cash-vs-attribution"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"

            events = [
                {
                    "event_type": "order_submit",
                    "run_id": run_id,
                    "order_id": "ord-1",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "token_id": "t1",
                    "target_ref": "target-1",
                    "price": 0.5,
                    "size": 10.0,
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "fill",
                    "run_id": run_id,
                    "order_id": "ord-1",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "token_id": "t1",
                    "target_ref": "target-1",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10.0,
                    "taker_fee_usd": 0.1,
                    "maker_rebate_usd": 0.0,
                    "slippage_cost_usd": 1.0,
                    "adverse_selection_cost_usd": 2.0,
                    "ts_utc": "2026-01-01T00:00:02Z",
                },
                {
                    "event_type": "wallet_position_settled",
                    "run_id": run_id,
                    "token_id": "t1",
                    "target_ref": "target-1",
                    "market_key": "mkt-1",
                    "token_side": "YES",
                    "settlement_side": "SELL",
                    "settlement_size_shares": 10.0,
                    "settlement_price": 1.0,
                    "settlement_notional_usd": 10.0,
                    "ts_utc": "2026-01-01T00:05:00Z",
                },
            ]
            status = [
                {
                    "run_id": run_id,
                    "gauge.open_orders": 0,
                    "gauge.total_pnl": 4.9,
                    "ts_utc": "2026-01-01T00:05:01Z",
                    "wallet_contract": {
                        "stable_balance_total": 1004.9,
                        "deployable_capital": 1004.9,
                        "authority_status_class": "authoritative",
                    },
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "profile_name": "paper_universal"}),
                encoding="utf-8",
            )
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "outcome_truth_audit.json").write_text(
                json.dumps({"run_id": run_id, "lane_outcome_truth": {}, "complete_outcome_records": 0}),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            financial = report.get("financial_performance", {})
            overall = financial.get("overall", {})
            taker = financial.get("by_lane", {}).get("taker", {})

            self.assertAlmostEqual(float(overall.get("net_pnl_usd") or 0.0), 4.9, places=6)
            self.assertAlmostEqual(float(financial.get("latest_total_pnl_usd") or 0.0), 4.9, places=6)
            self.assertAlmostEqual(float(overall.get("taker_fee_usd") or 0.0), 0.1, places=6)
            self.assertAlmostEqual(float(overall.get("slippage_cost_usd") or 0.0), 1.0, places=6)
            self.assertAlmostEqual(float(overall.get("adverse_selection_cost_usd") or 0.0), 2.0, places=6)
            self.assertAlmostEqual(float(overall.get("net_cash_adjustment_usd") or 0.0), -0.1, places=6)
            self.assertAlmostEqual(float(taker.get("net_pnl_usd") or 0.0), 4.9, places=6)
            self.assertEqual(
                str(financial.get("accounting_basis") or ""),
                "fill_cashflow_plus_cash_adjustments_plus_wallet_position_settled",
            )

    def test_build_report_prefers_wallet_state_refresh_and_ledger_closeout_over_stale_status_tail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-stale-status-tail"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"

            events = [
                {
                    "event_type": "order_submit",
                    "run_id": run_id,
                    "order_id": "ord-1",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "token_id": "t1",
                    "target_ref": "target-1",
                    "price": 0.5,
                    "size": 10.0,
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "fill",
                    "run_id": run_id,
                    "order_id": "ord-1",
                    "submission_lane": "taker",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10.0,
                    "ts_utc": "2026-01-01T00:00:02Z",
                },
                {
                    "event_type": "wallet_position_settled",
                    "run_id": run_id,
                    "settlement_side": "SELL",
                    "settlement_size_shares": 10.0,
                    "settlement_price": 1.0,
                    "ts_utc": "2026-01-01T00:00:10Z",
                },
                {
                    "event_type": "wallet_state_refresh",
                    "run_id": run_id,
                    "ts_utc": "2026-01-01T00:00:10Z",
                    "authority_status_class": "authoritative",
                    "stable_balance_total": 1005.0,
                    "deployable_capital": 1005.0,
                    "protected_reserve": 0.0,
                    "reservation_locked_usdc": 0.0,
                    "position_liability_locked_usdc": 0.0,
                    "locked_total_usdc": 0.0,
                    "order_capable_live": False,
                    "order_submit_eligible": True,
                    "canonical_live_nonce_available": False,
                    "canonical_live_pending_wallet_tx_available": False,
                    "live_truth_gap_reasons": [],
                },
            ]
            status = [
                {
                    "run_id": run_id,
                    "ts_utc": "2026-01-01T00:00:05Z",
                    "gauge.total_pnl": 0.0,
                    "wallet_contract": {
                        "authority_status_class": "authoritative",
                        "stable_balance_total": 1000.0,
                        "deployable_capital": 995.0,
                        "protected_reserve": 0.0,
                        "reservation_locked_usdc": 5.0,
                        "position_liability_locked_usdc": 0.0,
                        "locked_total_usdc": 5.0,
                        "order_capable_live": False,
                        "order_submit_eligible": True,
                        "canonical_live_nonce_available": False,
                        "canonical_live_pending_wallet_tx_available": False,
                        "live_truth_gap_reasons": [],
                    },
                }
            ]
            run_manifest = {
                "run_id": run_id,
                "profile_name": "paper_universal",
                "config": {
                    "wallet": {
                        "paper_starting_usdc": 1000.0,
                        "protected_usdc_reserve": 0.0,
                    }
                },
            }

            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(json.dumps(run_manifest), encoding="utf-8")
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "outcome_truth_audit.json").write_text(
                json.dumps({"run_id": run_id, "run_claim_boundary": {}, "lane_outcome_truth": {}}),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            financial = report.get("financial_performance", {})
            capital = financial.get("capital_progression", {})
            wallet = report.get("wallet_authority", {})
            financial_outcome_reconciliation = report.get("financial_outcome_reconciliation", {})

            self.assertAlmostEqual(float(financial.get("latest_total_pnl_usd") or 0.0), 5.0, places=6)
            self.assertEqual(
                str(financial.get("latest_total_pnl_source") or ""),
                "ledger_closeout_over_status_snapshot",
            )
            self.assertAlmostEqual(float(financial.get("latest_status_total_pnl_usd") or 0.0), 0.0, places=6)
            self.assertFalse(bool(financial.get("reconciled_with_status_total_pnl")))
            self.assertAlmostEqual(float(capital.get("ending_wallet_stable_balance_total_usd") or 0.0), 1005.0, places=6)
            self.assertAlmostEqual(float(capital.get("ending_wallet_deployable_capital_usd") or 0.0), 1005.0, places=6)
            self.assertAlmostEqual(float(capital.get("ending_wallet_reservation_locked_usd") or 0.0), 0.0, places=6)
            self.assertEqual(str(capital.get("ending_wallet_source") or ""), "wallet_state_refresh_event")
            self.assertEqual(str(wallet.get("wallet_contract_surface_source") or ""), "wallet_state_refresh_event")
            self.assertAlmostEqual(
                float(((wallet.get("latest_contract") or {}).get("stable_balance_total") or 0.0)),
                1005.0,
                places=6,
            )
            self.assertEqual(
                str(financial_outcome_reconciliation.get("status") or ""),
                "outcome_truth_audit_missing",
            )
            self.assertEqual(financial_outcome_reconciliation.get("by_lane"), {})

            summary = render_human_summary(report)
            self.assertIn("base_capital_start_usd=1000.0000", summary)
            self.assertIn("ending_stable_balance_total_usd=1005.0000", summary)
            self.assertIn("avg_submitted_order_notional_usd=5.0000", summary)
            self.assertIn("avg_submitted_order_size_shares=10.0000", summary)

    def test_build_report_includes_runtime_resource_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events_path.write_text("", encoding="utf-8")
            status_rows = [
                {
                    "run_id": "rid-res",
                    "gauge.process_cpu_percent": 10.0,
                    "gauge.process_rss_mb": 200.0,
                    "gauge.system_load1": 0.4,
                    "gauge.system_mem_available_mb": 1200.0,
                    "gauge.system_mem_available_ratio": 0.60,
                    "gauge.system_swap_used_mb": 50.0,
                },
                {
                    "run_id": "rid-res",
                    "runtime_resource": {
                        "process_cpu_percent": 40.0,
                        "process_rss_mb": 260.0,
                        "system_load1": 0.9,
                        "system_mem_available_mb": 900.0,
                        "system_mem_available_ratio": 0.45,
                        "system_swap_used_mb": 90.0,
                    },
                },
            ]
            status_path.write_text("\n".join(json.dumps(x) for x in status_rows) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            report = build_report(root, run_id="rid-res")
            resources = report.get("runtime_resource", {})
            self.assertEqual(float(resources.get("resource_status_rows", 0.0)), 2.0)
            self.assertGreaterEqual(float(resources.get("process_cpu_percent_p95", 0.0)), 10.0)
            self.assertAlmostEqual(float(resources.get("process_cpu_percent_max", 0.0)), 40.0, places=6)
            self.assertAlmostEqual(float(resources.get("process_rss_mb_max", 0.0)), 260.0, places=6)
            self.assertAlmostEqual(float(resources.get("system_load1_max", 0.0)), 0.9, places=6)

    def test_build_report_includes_taker_lineage_stage_net_breakout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "book_top",
                    "token_id": "tok-1",
                    "midpoint": 0.52,
                    "ts_utc": "2026-01-01T00:00:00Z",
                },
                {
                    "event_type": "order_submit",
                    "order_id": "o-stage",
                    "reason": "taker_chainlink",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "fill",
                    "order_id": "o-stage",
                    "token_id": "tok-1",
                    "side": "BUY",
                    "price": 0.50,
                    "size": 10,
                    "ts_utc": "2026-01-01T00:00:02Z",
                },
            ]
            status = [{"gauge.open_orders": 0, "ts_utc": "2026-01-01T00:00:05Z"}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            breakout = report.get("taker_lineage_stage_net_breakout", {})
            self.assertIn("MAKER_TAKER_SELECTIVE", breakout)
            row = breakout["MAKER_TAKER_SELECTIVE"]
            self.assertEqual(float(row.get("fills_scored", 0.0)), 1.0)
            self.assertAlmostEqual(float(row.get("capture", 0.0)), 0.2, places=6)
            self.assertAlmostEqual(float(row.get("adverse_selection", 0.0)), 0.0, places=6)
            self.assertAlmostEqual(float(row.get("net", 0.0)), 0.2, places=6)

    def test_taker_stage_delta_accounting_primary_causes_match_decision_delta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_decision",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "edge_abs": 0.22,
                    "conviction_score": 0.7,
                    "timing_window_class": "outside_window",
                    "submit_capable_static": False,
                    "submit_capable_dynamic_predicted": None,
                    "should_submit": False,
                    "block_reason": "taker_outside_final_window",
                    "multi_oracle_status": "confirmed",
                },
                {
                    "event_type": "taker_decision",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "edge_abs": 0.44,
                    "conviction_score": 0.9,
                    "timing_window_class": "final_window",
                    "submit_capable_static": True,
                    "submit_capable_dynamic_predicted": True,
                    "should_submit": True,
                    "block_reason": None,
                    "multi_oracle_status": "confirmed",
                },
                {
                    "event_type": "order_submit",
                    "order_id": "o-stage-1",
                    "reason": "taker_chainlink",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "edge_abs": 0.44,
                        "conviction_score": 0.9,
                        "timing_window_class": "final_window",
                        "multi_oracle_status": "confirmed",
                    },
                },
                {
                    "event_type": "fill",
                    "order_id": "o-stage-1",
                    "token_id": "tok-1",
                    "side": "BUY",
                    "price": 0.50,
                    "size": 1.0,
                },
            ]
            status = [{"gauge.open_orders": 0}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            taker = report.get("taker_competitiveness", {})
            self.assertIsInstance(taker.get("lineage_stage_reduction_primary_cause_counters"), dict)
            self.assertIsInstance(taker.get("lineage_stage_reduction_delta_accounting"), dict)
            self.assertIsInstance(taker.get("lineage_stage_first_claim_guard"), dict)
            self.assertTrue(bool(taker.get("lineage_stage_first_claim_guard", {}).get("lineage_stage_evidence_required_before_aggregate_claim")))

            mts_delta = (taker.get("lineage_stage_reduction_delta_accounting") or {}).get("MAKER_TAKER_SELECTIVE") or {}
            self.assertEqual(float(mts_delta.get("decision_to_submit_delta", 0.0)), 1.0)
            self.assertEqual(float(mts_delta.get("primary_reduction_cause_total", 0.0)), 1.0)
            self.assertTrue(bool(mts_delta.get("primary_reduction_cause_total_matches_delta", False)))

    def test_taker_stage_delta_accounting_marks_event_counter_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_decision",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "edge_abs": 0.44,
                    "conviction_score": 0.9,
                    "timing_window_class": "final_window",
                    "submit_capable_static": True,
                    "submit_capable_dynamic_predicted": True,
                    "should_submit": True,
                    "block_reason": None,
                    "multi_oracle_status": "confirmed",
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "taker",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "none",
                    "block_reason": "taker_submit_rejected",
                    "taker_submit_reject_reason": "size_notional_bounds",
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "taker",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "none",
                    "block_reason": "taker_submit_rejected",
                    "taker_submit_reject_reason": "size_notional_bounds",
                },
            ]
            status = [{"gauge.open_orders": 0}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            mts_delta = (
                (report.get("taker_competitiveness", {}).get("lineage_stage_reduction_delta_accounting") or {})
                .get("MAKER_TAKER_SELECTIVE")
                or {}
            )
            self.assertEqual(float(mts_delta.get("decision_to_submit_delta", 0.0)), 1.0)
            self.assertEqual(float(mts_delta.get("primary_reduction_cause_total", 0.0)), 2.0)
            self.assertFalse(bool(mts_delta.get("primary_reduction_cause_total_matches_delta", True)))
            self.assertTrue(bool(mts_delta.get("primary_reduction_cause_total_exceeds_delta", False)))
            self.assertTrue(bool(mts_delta.get("primary_reduction_cause_overlap_possible", False)))
            self.assertAlmostEqual(
                float(mts_delta.get("primary_reduction_cause_total_delta_difference", 0.0)),
                1.0,
                places=6,
            )

    def test_build_report_includes_risk_competitiveness_section(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "risk_reject",
                    "submission_lane": "maker",
                    "reason": "global_exposure_cap",
                    "risk_decision_basis": {
                        "dynamic_scaling": {"scaling_class": "conservative"},
                        "global_exposure_guard": {"projected_to_cap_ratio": 1.05, "near_cap": True},
                    },
                },
                {
                    "event_type": "order_submit",
                    "submission_lane": "taker",
                    "order_id": "o1",
                    "reason": "taker_chainlink",
                    "risk_decision_basis": {
                        "dynamic_scaling": {"scaling_class": "aggressive"},
                        "global_exposure_guard": {"projected_to_cap_ratio": 0.60, "near_cap": False},
                    },
                },
            ]
            status = [{"gauge.open_orders": 0}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            risk_comp = report.get("risk_competitiveness", {})
            self.assertIsInstance(risk_comp, dict)
            self.assertEqual(float((risk_comp.get("decision_count_by_lane") or {}).get("maker", 0.0)), 1.0)
            self.assertEqual(float((risk_comp.get("decision_count_by_lane") or {}).get("taker", 0.0)), 1.0)
            self.assertEqual(float((risk_comp.get("reject_count_by_lane") or {}).get("maker", 0.0)), 1.0)
            self.assertEqual(float(risk_comp.get("global_exposure_cap_reject_count", 0.0)), 1.0)
            self.assertEqual(float((risk_comp.get("scaling_class_distribution") or {}).get("conservative", 0.0)), 1.0)
            self.assertEqual(float((risk_comp.get("scaling_class_distribution") or {}).get("aggressive", 0.0)), 1.0)

    def test_build_report_includes_wallet_authority_surface(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "wallet_state_refresh"},
                {"event_type": "wallet_health_gate"},
                {"event_type": "wallet_reservation_created"},
                {"event_type": "book_top", "token_id": "t1", "midpoint": 0.5},
            ]
            status = [
                {
                    "wallet_contract": {
                        "gas_balance": 2.0,
                        "gas_reserve_min": 0.1,
                        "gas_ok": True,
                        "stable_balance_total": 1000.0,
                        "protected_reserve": 50.0,
                        "reservation_locked_usdc": 10.0,
                        "position_liability_locked_usdc": 0.0,
                        "locked_total_usdc": 10.0,
                        "deployable_capital": 940.0,
                        "approval_ok": True,
                        "nonce_ok": True,
                        "reconcile_ok": True,
                        "wallet_health_ok": True,
                        "wallet_health_reasons": [],
                        "authority_status_class": "bootstrap_non_authoritative",
                        "order_capable_live": False,
                        "order_submit_eligible": False,
                        "canonical_live_nonce_available": False,
                        "canonical_live_pending_wallet_tx_available": False,
                        "live_truth_gap_reasons": ["canonical_live_nonce_unavailable:missing"],
                    }
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            wallet = report.get("wallet_authority", {})
            self.assertIsInstance(wallet, dict)
            self.assertEqual(float(((wallet.get("latest_contract") or {}).get("deployable_capital") or 0.0)), 940.0)
            self.assertEqual(float(((wallet.get("event_counts") or {}).get("wallet_state_refresh") or 0.0)), 1.0)
            self.assertEqual(float(((wallet.get("event_counts") or {}).get("wallet_health_gate") or 0.0)), 1.0)
            self.assertEqual(float(((wallet.get("event_counts") or {}).get("wallet_reservation_created") or 0.0)), 1.0)
            self.assertEqual(str(wallet.get("authority_status_class") or ""), "bootstrap_non_authoritative")
            self.assertFalse(bool(wallet.get("order_capable_live")))
            self.assertFalse(bool(wallet.get("order_submit_eligible")))
            self.assertFalse(bool(wallet.get("canonical_live_nonce_available")))
            self.assertFalse(bool(wallet.get("canonical_live_pending_wallet_tx_available")))
            self.assertTrue(isinstance(wallet.get("live_truth_gap_reasons"), list))
            self.assertIn("reservation_mismatch_candidate", wallet)
            self.assertIn("reservation_mismatch_delta_usdc", wallet)
            self.assertIn("reservation_mismatch_detail", wallet)
            self.assertFalse(bool(wallet.get("reservation_mismatch_candidate")))
            self.assertAlmostEqual(float(wallet.get("reservation_mismatch_delta_usdc") or 0.0), 0.0, places=9)

    def test_wallet_authority_legacy_fallback_is_non_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [{"event_type": "wallet_state_refresh"}]
            status = [
                {
                    "wallet_gas_balance": 2.0,
                    "wallet_gas_reserve_min": 0.1,
                    "wallet_gas_ok": True,
                    "wallet_stable_balance_total": 1000.0,
                    "wallet_protected_reserve": 50.0,
                    "wallet_reservation_locked_usdc": 10.0,
                    "wallet_position_liability_locked_usdc": 0.0,
                    "wallet_locked_total_usdc": 10.0,
                    "wallet_deployable_capital": 940.0,
                    "wallet_approval_ok": True,
                    "wallet_nonce_ok": True,
                    "wallet_reconcile_ok": True,
                    "wallet_health_ok": True,
                    "wallet_health_reasons": [],
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            wallet = report.get("wallet_authority", {})
            self.assertTrue(bool(wallet.get("legacy_fallback_used")))
            self.assertFalse(bool(wallet.get("authoritative_wallet_contract_present")))
            self.assertEqual(
                str(wallet.get("wallet_contract_surface_source") or ""),
                "legacy_reconstructed_wallet_surface",
            )
            self.assertEqual(str(wallet.get("authority_status_class") or ""), "legacy_fallback_non_authoritative")
            self.assertFalse(bool(wallet.get("order_capable_live")))
            self.assertFalse(bool(wallet.get("order_submit_eligible")))
            self.assertFalse(bool(wallet.get("canonical_live_nonce_available")))
            self.assertFalse(bool(wallet.get("canonical_live_pending_wallet_tx_available")))
            gaps = wallet.get("live_truth_gap_reasons") or []
            self.assertTrue(any("legacy_wallet_contract_fallback_reconstructed_surface" in str(x) for x in gaps))
            self.assertFalse(bool(wallet.get("reservation_mismatch_candidate")))
            self.assertAlmostEqual(float(wallet.get("reservation_mismatch_delta_usdc") or 0.0), 0.0, places=9)
            self.assertIn("legacy_wallet_contract_fallback_reconstructed_surface", str(wallet.get("reservation_mismatch_detail") or ""))

    def test_build_report_exposes_valuation_truth_and_horizon_split(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "book_top", "token_id": "t1", "midpoint": 0.50, "ts_utc": "2026-01-01T00:00:00Z"},
                {
                    "event_type": "fill",
                    "order_id": "o1",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.49,
                    "size": 1.0,
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {"event_type": "book_top", "token_id": "t1", "midpoint": 0.47, "ts_utc": "2026-01-01T00:00:04Z"},
            ]
            status = [
                {"gauge.open_orders": 1, "valuation_degraded": False, "valuation_hard_degraded": False},
                {
                    "gauge.open_orders": 1,
                    "valuation_degraded": True,
                    "valuation_hard_degraded": True,
                    "pnl_degraded": True,
                    "loss_guard_degraded": True,
                    "valuation_degraded_reasons": [
                        "hard_degraded:t1:book_top_missing|held_ws_missing_or_unusable_age_sec=31.250|last_known_mid_missing"
                    ],
                    "valuation_mid_source_counts": {"hard_degraded": 1, "conservative_bound_hard_degraded": 1},
                    "held_unpriceable_escalation_active": True,
                    "held_unpriceable_escalation_token_ids": ["t1"],
                    "held_unpriceable_escalation_reasons": [
                        "persistent_held_unpriceable:t1:age_sec=130.000>=threshold_sec=120.000"
                    ],
                    "held_unpriceable_escalation_max_age_sec": 130.0,
                    "held_unpriceable_escalation_threshold_sec": 120.0,
                    "held_unpriceable_defect_candidate": True,
                    "held_unpriceable_operator_action": (
                        "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable"
                    ),
                    "held_unpriceable_non_defect_token_ids": [],
                    "held_unpriceable_meaningful_escalation_token_ids": ["t1"],
                    "valuation_hard_degraded_enter_count": 2,
                    "valuation_hard_degraded_clear_count": 1,
                    "held_unpriceable_started_count": 3,
                    "held_unpriceable_recovered_count": 1,
                    "preexpiry_ws_missing_or_unusable_anomaly_count": 2,
                    "preexpiry_ws_missing_or_unusable_anomaly_active": True,
                    "lifecycle_context_mismatch_count": 4,
                    "lifecycle_context_missing_sec_to_expiry_count": 2,
                    "settlement_hold_required_count": 7,
                    "open_order_cleanup_required_count": 3,
                    "unresolved_lifecycle_obligation_count": 4,
                    "cancel_fail_closed_count": 3,
                    "held_unpriceable_cause_counts": {
                        "postexpiry_market_retired": 1,
                        "preexpiry_ws_missing_or_unusable": 2,
                    },
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})
            self.assertEqual(float(valuation_truth.get("status_rows") or 0.0), 2.0)
            self.assertEqual(float(valuation_truth.get("valuation_degraded_rows") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("valuation_hard_degraded_rows") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_escalation_rows") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_defect_candidate_rows") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_ws_missing_or_unusable_rows") or 0.0), 1.0)
            self.assertGreater(float(valuation_truth.get("held_ws_missing_or_unusable_ratio") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("preexpiry_ws_missing_or_unusable_anomaly_rows") or 0.0), 1.0)
            self.assertGreater(float(valuation_truth.get("preexpiry_ws_missing_or_unusable_anomaly_ratio") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("valuation_hard_degraded_enter_count") or 0.0), 2.0)
            self.assertEqual(float(valuation_truth.get("valuation_hard_degraded_clear_count") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_started_count") or 0.0), 3.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_recovered_count") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_raw_count") or 0.0), 2.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_non_defect_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_meaningful_count") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("preexpiry_ws_missing_or_unusable_anomaly_count") or 0.0), 2.0)
            self.assertEqual(float(valuation_truth.get("lifecycle_context_mismatch_count") or 0.0), 4.0)
            self.assertEqual(
                float(valuation_truth.get("lifecycle_context_missing_sec_to_expiry_count") or 0.0),
                2.0,
            )
            self.assertEqual(float(valuation_truth.get("settlement_hold_required_count") or 0.0), 7.0)
            self.assertEqual(float(valuation_truth.get("open_order_cleanup_required_count") or 0.0), 3.0)
            self.assertEqual(float(valuation_truth.get("unresolved_lifecycle_obligation_count") or 0.0), 4.0)
            self.assertEqual(float(valuation_truth.get("cancel_fail_closed_count") or 0.0), 3.0)
            self.assertEqual(
                float(
                    (valuation_truth.get("held_unpriceable_cause_counts_latest") or {}).get(
                        "preexpiry_ws_missing_or_unusable"
                    )
                    or 0.0
                ),
                2.0,
            )
            self.assertEqual(float(valuation_truth.get("latest_settlement_hold_required_count") or 0.0), 7.0)
            self.assertEqual(float(valuation_truth.get("latest_open_order_cleanup_required_count") or 0.0), 3.0)
            self.assertIn(
                "held_ws_missing_or_unusable_age_sec",
                " ".join(str(x) for x in list(valuation_truth.get("latest_valuation_degraded_reasons") or [])),
            )
            self.assertTrue(bool(valuation_truth.get("latest_held_unpriceable_escalation_active")))
            self.assertEqual(float(valuation_truth.get("latest_held_unpriceable_token_count") or 0.0), 0.0)
            self.assertEqual(list(valuation_truth.get("latest_held_unpriceable_token_ids") or []), [])
            self.assertTrue(bool(valuation_truth.get("latest_held_unpriceable_defect_candidate")))
            self.assertEqual(
                list(valuation_truth.get("latest_held_unpriceable_escalation_token_ids") or []),
                ["t1"],
            )
            self.assertIn(
                "persistent_held_unpriceable:t1:",
                " ".join(str(x) for x in list(valuation_truth.get("latest_held_unpriceable_escalation_reasons") or [])),
            )
            self.assertIn(
                "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable",
                str(valuation_truth.get("latest_held_unpriceable_operator_action") or ""),
            )
            run_counts = dict(valuation_truth.get("valuation_source_counts_run") or {})
            self.assertEqual(float(run_counts.get("hard_degraded") or 0.0), 1.0)
            self.assertEqual(float(run_counts.get("conservative_bound_hard_degraded") or 0.0), 1.0)
            degraded_source_counts = dict(valuation_truth.get("valuation_source_counts_degraded_rows") or {})
            self.assertEqual(float(degraded_source_counts.get("hard_degraded") or 0.0), 1.0)
            self.assertEqual(float(degraded_source_counts.get("conservative_bound_hard_degraded") or 0.0), 1.0)
            reason_family_counts = dict(valuation_truth.get("valuation_degraded_reason_family_counts_run") or {})
            self.assertEqual(float(reason_family_counts.get("hard_degraded") or 0.0), 1.0)
            self.assertEqual(str(valuation_truth.get("valuation_bruise_state") or ""), "open_meaningful_unpriceable")
            self.assertEqual(str(valuation_truth.get("valuation_dominant_reason_family_run") or ""), "hard_degraded")
            self.assertEqual(
                str(valuation_truth.get("valuation_dominant_held_unpriceable_cause_run") or ""),
                "preexpiry_ws_missing_or_unusable",
            )
            self.assertEqual(
                str(valuation_truth.get("valuation_dominant_source_degraded_rows") or ""),
                "conservative_bound_hard_degraded",
            )

            immediate = report.get("execution_quality_immediate_midpoint", {})
            horizon = report.get("execution_quality_horizon_outcome", {})
            self.assertIn("immediate_capture", immediate)
            self.assertIn("immediate_adverse_selection", immediate)
            self.assertIn("immediate_capture_minus_adverse", immediate)
            self.assertIn("horizon_outcome_horizon_sec", horizon)
            self.assertIn("horizon_outcome_adverse_after_fill_count", horizon)
            self.assertIn("horizon_outcome_adverse_after_fill_ratio", horizon)

            summary = render_human_summary(report)
            self.assertIn("execution_quality_immediate_midpoint=", summary)
            self.assertIn("pickoff=horizon_sec=", summary)
            self.assertIn("valuation_truth=", summary)
            self.assertIn("bruise_state=open_meaningful_unpriceable", summary)
            self.assertIn("held_ws_missing_or_unusable_ratio=", summary)
            self.assertIn("preexpiry_ws_missing_or_unusable_anomaly_ratio=", summary)
            self.assertIn("held_unpriceable_escalation_ratio=", summary)

    def test_build_report_does_not_overderive_non_defect_unpriceable_from_pending_unpriceable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status_path = root / "status_2026-01-01.jsonl"
            status = [
                {
                    "valuation_degraded": True,
                    "valuation_hard_degraded": True,
                    "held_unpriceable_started_count": 1,
                    "held_unpriceable_recovered_count": 0,
                    "held_unpriceable_token_ids": ["t_pre"],
                    "held_unpriceable_meaningful_escalation_token_ids": [],
                    "held_unpriceable_non_defect_token_ids": [],
                    "held_unpriceable_defect_candidate": False,
                    "held_unpriceable_operator_action": "none",
                }
            ]
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_raw_count") or 0.0), 1.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_non_defect_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("held_unpriceable_unrecovered_meaningful_count") or 0.0), 0.0)
            self.assertEqual(list(valuation_truth.get("latest_held_unpriceable_non_defect_token_ids") or []), [])
            self.assertEqual(
                str(valuation_truth.get("valuation_bruise_state") or ""),
                "held_unpriceable_not_fully_recovered",
            )

    def test_build_report_valuation_truth_ignores_dead_emergency_events_for_lifecycle_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "outcome": "blocked",
                    "blocked_reason": _HISTORICAL_RECOVERY_TOUCH_PRICE_UNAVAILABLE,
                    "reason": f"blocked_{_HISTORICAL_RECOVERY_TOUCH_PRICE_UNAVAILABLE}",
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "outcome": "blocked",
                    "outcome_reason": f"blocked_{_HISTORICAL_RECOVERY_TOUCH_PRICE_UNAVAILABLE}",
                    "taker_submit_reject_reason": "",
                    "ts_utc": "2026-01-01T00:00:02Z",
                },
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "outcome": "filled",
                    "reason": "filled",
                    "ts_utc": "2026-01-01T00:00:03Z",
                },
            ]
            status = [
                {
                    "valuation_degraded": False,
                    "valuation_hard_degraded": False,
                    "settlement_hold_required_count": 0,
                    "open_order_cleanup_required_count": 0,
                    "unresolved_lifecycle_obligation_count": 0,
                    "cancel_fail_closed_count": 0,
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})
            self.assertEqual(float(valuation_truth.get("settlement_hold_required_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("open_order_cleanup_required_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("unresolved_lifecycle_obligation_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("cancel_fail_closed_count") or 0.0), 0.0)

    def test_build_report_valuation_truth_marks_recovered_clean_from_event_only_transient_bruise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "valuation_degraded",
                    "ts_utc": "2026-01-01T00:00:01Z",
                    "valuation_degraded": True,
                    "valuation_hard_degraded": False,
                    "valuation_degraded_reasons": [
                        "degraded_using_last_known_mid:t1:age_sec=4.000<=max_age_sec=6.000"
                    ],
                    "valuation_mid_source_counts": {"last_known_mid": 1},
                },
                {
                    "event_type": "valuation_degraded",
                    "ts_utc": "2026-01-01T00:00:02Z",
                    "valuation_degraded": False,
                    "valuation_hard_degraded": False,
                    "valuation_degraded_reasons": [],
                    "valuation_mid_source_counts": {"live_mid": 1},
                },
            ]
            status = [
                {
                    "ts_status_utc": "2026-01-01T00:00:03Z",
                    "valuation_degraded": False,
                    "valuation_hard_degraded": False,
                    "valuation_degraded_reasons": [],
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})

            self.assertEqual(float(valuation_truth.get("valuation_degraded_rows") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("valuation_degraded_event_rows") or 0.0), 1.0)
            self.assertTrue(bool(valuation_truth.get("valuation_truth_sampling_gap_detected")))
            self.assertEqual(str(valuation_truth.get("valuation_bruise_state") or ""), "recovered_clean")
            self.assertEqual(str(valuation_truth.get("valuation_dominant_reason_family_run") or ""), "degraded_using_last_known_mid")
            self.assertEqual(str(valuation_truth.get("valuation_dominant_source_degraded_rows") or ""), "last_known_mid")
            self.assertEqual(str(valuation_truth.get("latest_valuation_truth_source_class") or ""), "status_row")
            self.assertEqual(list(valuation_truth.get("latest_valuation_degraded_reasons") or []), [])

            summary = render_human_summary(report)
            self.assertIn("bruise_state=recovered_clean", summary)
            self.assertIn("degraded_event_rows=1", summary)
            self.assertIn('latest_truth_source="status_row"', summary)

    def test_build_report_valuation_truth_uses_newer_event_as_latest_truth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "valuation_degraded",
                    "ts_utc": "2026-01-01T00:00:02Z",
                    "valuation_degraded": True,
                    "valuation_hard_degraded": False,
                    "valuation_degraded_reasons": [
                        "degraded_using_last_known_mid:t1:age_sec=5.000<=max_age_sec=6.000"
                    ],
                    "valuation_mid_source_counts": {"last_known_mid": 1},
                }
            ]
            status = [
                {
                    "ts_status_utc": "2026-01-01T00:00:01Z",
                    "valuation_degraded": False,
                    "valuation_hard_degraded": False,
                    "valuation_degraded_reasons": [],
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})

            self.assertEqual(str(valuation_truth.get("latest_valuation_truth_source_class") or ""), "valuation_event")
            self.assertTrue(bool(valuation_truth.get("latest_valuation_degraded")))
            self.assertEqual(
                str(valuation_truth.get("valuation_bruise_state") or ""),
                "degraded_not_fully_cleared",
            )
            self.assertIn(
                "degraded_using_last_known_mid:t1:",
                " ".join(str(x) for x in list(valuation_truth.get("latest_valuation_degraded_reasons") or [])),
            )

    def test_build_report_valuation_truth_respects_emergency_event_repeat_count_delta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "outcome": "blocked",
                    "blocked_reason": _HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED,
                    "reason": f"blocked_{_HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED}",
                    "compression_mode": "initial",
                    "repeat_count_delta": 1,
                    "repeat_count_total": 1,
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "outcome": "blocked",
                    "blocked_reason": _HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED,
                    "reason": f"blocked_{_HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED}",
                    "compression_mode": "repeat_summary",
                    "repeat_count_delta": 4,
                    "repeat_count_total": 5,
                    "ts_utc": "2026-01-01T00:00:30Z",
                },
            ]
            status = [
                {
                    "valuation_degraded": False,
                    "valuation_hard_degraded": False,
                    "settlement_hold_required_count": 0,
                    "open_order_cleanup_required_count": 0,
                    "unresolved_lifecycle_obligation_count": 0,
                    "cancel_fail_closed_count": 0,
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            valuation_truth = report.get("valuation_truth", {})
            self.assertEqual(float(valuation_truth.get("settlement_hold_required_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("open_order_cleanup_required_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("unresolved_lifecycle_obligation_count") or 0.0), 0.0)
            self.assertEqual(float(valuation_truth.get("cancel_fail_closed_count") or 0.0), 0.0)

    def test_execution_paths_use_unique_filled_orders_for_fill_rate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "order_submit", "order_id": "m1", "reason": "maker_quote"},
                {"event_type": "fill", "order_id": "m1", "token_id": "t1", "side": "BUY", "price": 0.50, "size": 1},
                {"event_type": "fill", "order_id": "m1", "token_id": "t1", "side": "BUY", "price": 0.50, "size": 1},
                {"event_type": "order_submit", "order_id": "t1", "reason": "taker_chainlink"},
                {"event_type": "fill", "order_id": "t1", "token_id": "t2", "side": "BUY", "price": 0.50, "size": 1},
                {"event_type": "fill", "order_id": "t1", "token_id": "t2", "side": "BUY", "price": 0.50, "size": 1},
            ]
            status = [{"gauge.open_orders": 0}]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root)
            paths = report.get("execution_paths", {})
            self.assertEqual(float(paths.get("maker_submits") or 0.0), 1.0)
            self.assertEqual(float(paths.get("maker_fills") or 0.0), 2.0)
            self.assertEqual(float(paths.get("maker_filled_orders") or 0.0), 1.0)
            self.assertAlmostEqual(float(paths.get("maker_fill_rate") or 0.0), 1.0)
            self.assertEqual(float(report.get("maker_submits") or 0.0), 1.0)
            self.assertEqual(float(report.get("maker_fills") or 0.0), 2.0)
            self.assertEqual(float(report.get("maker_filled_orders") or 0.0), 1.0)
            self.assertAlmostEqual(float(report.get("maker_fill_rate") or 0.0), 1.0)
            self.assertEqual(float(paths.get("taker_bonus_submits") or 0.0), 1.0)
            self.assertEqual(float(paths.get("taker_bonus_fills") or 0.0), 2.0)
            self.assertEqual(float(paths.get("taker_bonus_filled_orders") or 0.0), 1.0)
            self.assertAlmostEqual(float(paths.get("taker_bonus_fill_rate") or 0.0), 1.0)
            self.assertEqual(float(report.get("taker_bonus_submits") or 0.0), 1.0)
            self.assertEqual(float(report.get("taker_bonus_fills") or 0.0), 2.0)
            self.assertEqual(float(report.get("taker_bonus_filled_orders") or 0.0), 1.0)
            self.assertAlmostEqual(float(report.get("taker_bonus_fill_rate") or 0.0), 1.0)

    def test_build_report_run_id_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "risk_reject", "reason": "stale_book", "run_id": "r1"},
                {"event_type": "risk_reject", "reason": "order_rate_limit", "run_id": "r2"},
            ]
            status = [
                {"run_id": "r1", "gauge.open_orders": 1},
                {"run_id": "r2", "gauge.open_orders": 0},
            ]
            errors = [
                {"run_id": "r1", "component": "market_data"},
                {"run_id": "r2", "component": "gateway"},
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status) + "\n", encoding="utf-8")
            errors_path.write_text("\n".join(json.dumps(x) for x in errors) + "\n", encoding="utf-8")

            report = build_report(root, run_id="r1")
            self.assertEqual(report.get("schema_version"), 2)
            self.assertEqual(report["reject_reason_distribution"].get("stale_book"), 1)
            self.assertIsNone(report["reject_reason_distribution"].get("order_rate_limit"))
            self.assertEqual(report["errors_by_component"].get("market_data"), 1)
            self.assertIsNone(report["errors_by_component"].get("gateway"))

    def test_build_report_emits_run_commit_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-lineage"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "config_fingerprint_sha256": "cfg123",
                        "code_fingerprint_sha256": "code456",
                        "runtime_identity": {"git_commit": "abc789"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id)
            lineage = report.get("run_commit_lineage", {})
            self.assertEqual(str(lineage.get("run_id") or ""), run_id)
            self.assertEqual(str(lineage.get("git_commit") or ""), "abc789")
            self.assertEqual(str(lineage.get("config_fingerprint_sha256") or ""), "cfg123")
            self.assertEqual(str(lineage.get("code_fingerprint_sha256") or ""), "code456")
            self.assertTrue(bool(lineage.get("complete")))
            exercised = report.get(EXERCISED_HARNESS_REALISM_FIELD, {})
            self.assertEqual(exercised.get("semantics"), HARNESS_REALISM_GRADE_SEMANTICS)
            self.assertEqual(exercised.get("authority"), HARNESS_REALISM_GRADE_AUTHORITY)
            self.assertEqual(
                tuple((exercised.get("breakdown") or {}).keys()),
                HARNESS_REALISM_BREAKDOWN_KEYS,
            )

    def test_normalize_nightly_exercised_harness_realism_reads_legacy_top_level_fields(self):
        legacy_report = {
            "event_files": 1,
            "harness_realism_grade": 80,
            "harness_realism_grade_breakdown": {
                "tod_liquidity_scaling": 0,
                "maker_queue_proxy_depth_model": 20,
                "taker_depth_slippage_model": 20,
                "taker_lag_emulation_with_unknown_guard": 20,
                "truth_surface_completeness": 20,
            },
            "harness_realism_grade_semantics": HARNESS_REALISM_GRADE_SEMANTICS,
            "harness_realism_grade_authority": HARNESS_REALISM_GRADE_AUTHORITY,
        }
        normalized = normalize_nightly_exercised_harness_realism(legacy_report)
        self.assertEqual(normalized.get("grade"), 80)
        self.assertEqual(normalized.get("semantics"), HARNESS_REALISM_GRADE_SEMANTICS)
        self.assertEqual(normalized.get("authority"), HARNESS_REALISM_GRADE_AUTHORITY)
        self.assertEqual(tuple((normalized.get("breakdown") or {}).keys()), HARNESS_REALISM_BREAKDOWN_KEYS)

    def test_normalize_nightly_exercised_harness_realism_does_not_reinterpret_canonical_audit_payload(self):
        canonical_audit_payload = {
            "checks": {},
            "harness_realism_grade": 100,
            "harness_realism_grade_breakdown": {
                "tod_liquidity_scaling": 20,
                "maker_queue_proxy_depth_model": 20,
                "taker_depth_slippage_model": 20,
                "taker_lag_emulation_with_unknown_guard": 20,
                "truth_surface_completeness": 20,
            },
            "harness_realism_grade_semantics": HARNESS_REALISM_GRADE_SEMANTICS,
            "harness_realism_grade_authority": HARNESS_REALISM_GRADE_AUTHORITY,
        }
        normalized = normalize_nightly_exercised_harness_realism(canonical_audit_payload)
        self.assertEqual(normalized.get("grade"), 0)
        self.assertEqual(tuple((normalized.get("breakdown") or {}).keys()), HARNESS_REALISM_BREAKDOWN_KEYS)

    def test_build_report_quote_uptime_uses_quote_activity_signal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "gauge.actions_last_cycle": 2},
                {"gauge.open_orders": 0, "gauge.quote_active": 1},
                {"gauge.open_orders": 0, "gauge.actions_last_cycle": 0, "gauge.quote_active": 0},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 3.0)

    def test_build_report_quote_uptime_uses_taker_quick_read_signals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "gauge.taker_actions_last_cycle": 1},
                {"gauge.open_orders": 0, "gauge.taker_fills_last_cycle": 2},
                {"gauge.open_orders": 0, "gauge.quote_active": 0},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 3.0)

    def test_build_report_quote_uptime_uses_status_window_activity_signals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "gauge.actions_last_status_window": 2},
                {"gauge.open_orders": 0, "gauge.taker_submitted_last_status_window": 1},
                {"gauge.open_orders": 0, "gauge.quote_active": 0},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 3.0)

    def test_build_report_quote_uptime_uses_maker_status_window_signals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "gauge.maker_submitted_token_count_last_status_window": 1},
                {"gauge.open_orders": 0, "gauge.maker_fills_last_status_window": 2},
                {"gauge.open_orders": 0, "gauge.quote_active": 0},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 3.0)

    def test_build_report_quote_uptime_uses_counter_deltas(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "counter.orders_submitted": 10, "counter.orders_canceled": 2},
                {"gauge.open_orders": 0, "counter.orders_submitted": 11, "counter.orders_canceled": 2},
                {"gauge.open_orders": 0, "counter.orders_submitted": 11, "counter.orders_canceled": 3},
                {"gauge.open_orders": 0, "counter.orders_submitted": 11, "counter.orders_canceled": 3},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 4.0)

    def test_build_report_quote_uptime_counts_risk_reject_activity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "counter.risk_rejects": 10},
                {"gauge.open_orders": 0, "counter.risk_rejects": 11},
                {"gauge.open_orders": 0, "counter.risk_rejects": 12},
                {"gauge.open_orders": 0, "counter.risk_rejects": 12},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 4.0)

    def test_build_report_quote_uptime_uses_rolling_usage_gauges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"gauge.open_orders": 0, "gauge.orders_used_60s": 0, "gauge.cancels_used_60s": 0},
                {"gauge.open_orders": 0, "gauge.orders_used_60s": 3, "gauge.cancels_used_60s": 0},
                {"gauge.open_orders": 0, "gauge.orders_used_60s": 0, "gauge.cancels_used_60s": 1},
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            self.assertAlmostEqual(report["quote_uptime_ratio"], 2.0 / 3.0)

    def test_build_report_run_scoped_file_selection_uses_manifest_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r-manifest"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "events_path": "/logs/paper_universal/events_2026-01-10.jsonl",
                        "status_path": "/logs/paper_universal/status_2026-01-10.jsonl",
                    }
                ),
                encoding="utf-8",
            )
            (root / "events_2025-01-01.jsonl").write_text(
                json.dumps({"event_type": "risk_reject", "reason": "order_rate_limit", "run_id": run_id}) + "\n",
                encoding="utf-8",
            )
            (root / "events_2026-01-10.jsonl").write_text(
                json.dumps({"event_type": "risk_reject", "reason": "stale_book", "run_id": run_id}) + "\n",
                encoding="utf-8",
            )
            (root / "status_2025-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-10.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 1}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2025-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "errors_2026-01-10.jsonl").write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, max_lines_per_file=1000)
            self.assertEqual(report["reject_reason_distribution"].get("stale_book"), 1)
            self.assertIsNone(report["reject_reason_distribution"].get("order_rate_limit"))

    def test_build_report_edge_truth_section_uses_edge_evaluation_events(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-edge",
                    "token_id": "t1",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_cause": "submit_rejected_post_only_reject",
                    "market_reference_mode": "missing",
                    "pair_truth_basis": "one_sided_ws_missing_midpoint",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-edge",
                    "token_id": "t2",
                    "evaluation_scope": "taker",
                    "action_taken": "taker",
                    "block_reason": None,
                    "market_reference_mode": "direct_midpoint",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-edge",
                    "order_id": "o1",
                    "reason": "maker_quote",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": "rid-edge", "gauge.open_orders": 1}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-edge")
            edge_truth = report.get("edge_truth", {})
            self.assertEqual(edge_truth.get("rows_total"), 2.0)
            self.assertEqual(edge_truth.get("maker_rows"), 1.0)
            self.assertEqual(edge_truth.get("taker_rows"), 1.0)
            self.assertEqual(edge_truth.get("action_rows"), 1.0)
            self.assertEqual(edge_truth.get("blocked_rows"), 1.0)
            self.assertEqual(edge_truth.get("maker_blocked_rows"), 1.0)
            self.assertEqual(edge_truth.get("taker_blocked_rows"), 0.0)
            distribution = edge_truth.get("block_reason_distribution", {})
            self.assertEqual(distribution.get("maker_no_submission"), 1)
            maker_distribution = edge_truth.get("maker_block_reason_distribution", {})
            self.assertEqual(maker_distribution.get("maker_no_submission"), 1)
            taker_distribution = edge_truth.get("taker_block_reason_distribution", {})
            self.assertEqual(taker_distribution, {})
            maker_cause_distribution = edge_truth.get("maker_no_submission_cause_distribution", {})
            self.assertEqual(maker_cause_distribution.get("submit_rejected_post_only_reject"), 1)
            self.assertEqual(edge_truth.get("maker_market_reference_missing_count"), 1.0)
            self.assertEqual(edge_truth.get("maker_market_reference_one_sided_context_count"), 1.0)
            self.assertEqual(report.get("maker_reference_direct_midpoint_activity"), 0.0)
            self.assertEqual(report.get("maker_reference_missing_activity"), 1.0)

    def test_build_report_emits_maker_competitiveness_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "maker_market_viability_decision",
                    "run_id": "rid-competitiveness",
                    "token_id": "t1",
                    "viability_allowed": False,
                    "primary_reject_reason": "phase_disallow_maker",
                    "one_sided_active": True,
                    "side_policy": "BUY_ONLY",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-competitiveness",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_timing_gate_closed",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-competitiveness",
                    "order_id": "m1",
                    "reason": "mm_quote:high_vol",
                    "maker_market_viability": {
                        "edge_bucket": "0p10_0p20",
                        "one_sided_active": True,
                        "side_policy": "BUY_ONLY",
                        "size_multiplier_competitiveness": 1.2,
                        "spread_multiplier_competitiveness": 0.8,
                        "requote_delta_multiplier_competitiveness": 0.7,
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-competitiveness",
                    "order_id": "m1",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 1,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-competitiveness", "gauge.open_orders": 1}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-competitiveness")
            maker_comp = report.get("maker_market_viability", {})
            self.assertEqual(float(maker_comp.get("timing_gate_blocked_count_edge_eval") or 0.0), 1.0)
            self.assertEqual(float(maker_comp.get("timing_gate_blocked_count_decision") or 0.0), 1.0)
            self.assertEqual(float(maker_comp.get("one_sided_activation_submit_buy_count") or 0.0), 1.0)
            submit_buckets = maker_comp.get("maker_submit_edge_bucket_distribution", {})
            fill_buckets = maker_comp.get("maker_fill_edge_bucket_distribution", {})
            self.assertEqual(int(submit_buckets.get("0p10_0p20", 0)), 1)
            self.assertEqual(int(fill_buckets.get("0p10_0p20", 0)), 1)
            aggressiveness = maker_comp.get("aggressiveness_application_counts", {})
            self.assertEqual(int(aggressiveness.get("size_scaled", 0)), 1)
            self.assertEqual(int(aggressiveness.get("spread_tightened", 0)), 1)
            self.assertEqual(int(aggressiveness.get("requote_tightened", 0)), 1)

    def test_build_report_keeps_legacy_queue_pressure_lineage_out_of_current_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-queue-pressure"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            events = [
                {
                    "event_type": "maker_queue_pressure_adjustment",
                    "run_id": run_id,
                    "token_id": "t1",
                    "side": "BUY",
                    "adopted": True,
                    "gate_conversion": True,
                    "replace_guard_blocked": False,
                },
                {
                    "event_type": "maker_queue_pressure_adjustment",
                    "run_id": run_id,
                    "token_id": "t2",
                    "side": "BUY",
                    "adopted": False,
                    "gate_conversion": False,
                    "replace_guard_blocked": True,
                },
            ]
            outcome_rows = [
                {
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "decision_quality": "correct",
                },
                {
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "decision_quality": "incorrect",
                },
                {
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "decision_quality": "neutral",
                },
                {
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "unknown_missing_data",
                    "decision_quality": "incorrect",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")
            (reports_dir / "outcome_truth_records.jsonl").write_text(
                "\n".join(json.dumps(x) for x in outcome_rows) + "\n",
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            maker_comp = report.get("maker_market_viability", {})
            self.assertNotIn("maker_queue_pressure_candidate_count", maker_comp)
            self.assertNotIn("maker_queue_pressure_adopted_count", maker_comp)
            self.assertNotIn("maker_queue_pressure_gate_conversion_count", maker_comp)
            self.assertNotIn("maker_queue_pressure_replace_guard_blocked_count", maker_comp)
            support_artifacts = report.get("_support_artifacts", {})
            manifest = support_artifacts.get("maker_quote_integrity_manifest", {})
            self.assertEqual(
                manifest.get("historical_only_lineage_event_counts", {}).get("maker_queue_pressure_adjustment"),
                2,
            )
            lineage_summary = (
                manifest.get("historical_only_lineage_event_summaries", {}).get(
                    "maker_queue_pressure_adjustment",
                    {},
                )
            )
            self.assertEqual(int(lineage_summary.get("count") or 0), 2)
            self.assertEqual(int(lineage_summary.get("adopted_count") or 0), 1)
            self.assertEqual(int(lineage_summary.get("gate_conversion_count") or 0), 1)
            self.assertEqual(int(lineage_summary.get("replace_guard_blocked_count") or 0), 1)
            self.assertEqual(lineage_summary.get("side_distribution"), {"BUY": 2})
            self.assertEqual(lineage_summary.get("stage_distribution"), {"unknown": 2})
            self.assertAlmostEqual(float(report.get("maker_complete_count") or 0.0), 3.0, places=9)
            self.assertAlmostEqual(float(report.get("maker_complete_correct_rate") or 0.0), 1.0 / 3.0, places=9)
            self.assertAlmostEqual(float(report.get("maker_complete_incorrect_rate") or 0.0), 1.0 / 3.0, places=9)
            self.assertAlmostEqual(float(report.get("maker_complete_neutral_rate") or 0.0), 1.0 / 3.0, places=9)

    def test_build_report_emits_maker_fireability_window_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            run_manifest_path = root / "run_manifest_rid-fireability.json"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-fireability",
                    "evaluation_scope": "maker",
                    "target_ref": "target-a",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 59.0,
                    "market_probability": 0.10,
                    "action_taken": "none",
                    "submitted": True,
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-fireability",
                    "evaluation_scope": "maker",
                    "target_ref": "target-a",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 58.0,
                    "market_probability": 0.11,
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_category": "replace_guard_min_rest",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-fireability",
                    "evaluation_scope": "maker",
                    "target_ref": "target-a",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 55.0,
                    "market_probability": 0.12,
                    "action_taken": "none",
                    "submitted": True,
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-fireability",
                    "evaluation_scope": "maker",
                    "target_ref": "target-d",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 54.0,
                    "market_probability": 0.015,
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_category": "sizing_reject",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-fireability",
                    "evaluation_scope": "maker",
                    "target_ref": "target-c",
                    "lineage_stage": "MAKER_POSITION",
                    "time_remaining_sec": 61.0,
                    "action_taken": "none",
                    "block_reason": "maker_timing_gate_closed",
                },
            ]
            run_manifest = {
                "run_id": "rid-fireability",
                "profile_name": "paper_universal",
                "config": {
                    "runtime": {},
                    "risk": {},
                    "latency_verifier": {},
                    "sniper": {"taker": {"competitiveness": {}}},
                    "sizing": {
                        "maker_competitive_min_notional_usd": 350.0,
                        "maker_competitive_max_shares": 8000.0,
                    },
                    "strategy": {
                        "maker_market_viability": {
                            "timing_gate_enabled": True,
                            "timing_gate_min_sec_to_expiry": 50.0,
                            "timing_gate_max_sec_to_expiry": 60.0,
                        },
                        "execution_quality": {
                            "min_expected_fill_prob": 0.045,
                            "max_queue_ahead_size": 300.0,
                        },
                    },
                },
            }
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": "rid-fireability", "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            run_manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")

            report = build_report(root, run_id="rid-fireability")
            maker_fireability = report.get("maker_fireability", {})
            self.assertEqual(maker_fireability.get("config_complete"), True)
            self.assertEqual(float(maker_fireability.get("active_window_row_count") or 0.0), 4.0)
            self.assertEqual(float(maker_fireability.get("active_window_submit_count") or 0.0), 2.0)
            self.assertEqual(float(maker_fireability.get("active_window_replace_guard_count") or 0.0), 1.0)
            self.assertNotIn("active_window_quote_quality_skip_fill_probability_count", maker_fireability)
            self.assertNotIn("active_window_quote_quality_skip_queue_depth_count", maker_fireability)
            self.assertEqual(float(maker_fireability.get("active_window_sizing_reject_count") or 0.0), 1.0)
            self.assertAlmostEqual(float(maker_fireability.get("active_window_submit_rate") or 0.0), 0.5, places=9)
            self.assertAlmostEqual(
                float(maker_fireability.get("active_window_replace_guard_rate") or 0.0),
                0.25,
                places=9,
            )
            self.assertAlmostEqual(
                float(maker_fireability.get("active_window_sizing_reject_rate") or 0.0),
                0.25,
                places=9,
            )
            self.assertAlmostEqual(
                float(maker_fireability.get("active_window_low_price_viability_floor") or 0.0),
                0.04375,
                places=9,
            )
            self.assertEqual(float(maker_fireability.get("active_window_viable_row_count") or 0.0), 3.0)
            self.assertEqual(float(maker_fireability.get("active_window_impossible_row_count") or 0.0), 1.0)
            self.assertEqual(float(maker_fireability.get("active_window_viable_target_count") or 0.0), 1.0)
            self.assertEqual(float(maker_fireability.get("active_window_impossible_target_count") or 0.0), 1.0)
            self.assertEqual(
                maker_fireability.get("active_window_lifecycle_phase_distribution"),
                {"prepare": 4},
            )
            self.assertEqual(
                maker_fireability.get("active_window_lineage_stage_distribution"),
                {"MAKER_TAKER_SELECTIVE": 4},
            )
            self.assertEqual(
                maker_fireability.get("active_window_maker_phase_allowed_distribution"),
                {"disallowed": 4},
            )
            self.assertNotIn("active_window_stage_distribution", maker_fireability)
            block_distribution = maker_fireability.get("active_window_block_reason_distribution", {})
            self.assertEqual(int(block_distribution.get("submitted", 0)), 2)
            self.assertEqual(int(block_distribution.get("replace_guard_min_rest", 0)), 1)
            self.assertEqual(int(block_distribution.get("sizing_reject", 0)), 1)
            self.assertNotIn("raw_quote_quality_skip_severity", maker_fireability)
            self.assertNotIn("raw_queue_depth_near_threshold_event_count", maker_fireability)
            self.assertNotIn("raw_queue_depth_hard_miss_event_count", maker_fireability)
            self.assertEqual(
                maker_fireability.get("active_window_low_price_conflict_price_band"),
                {"min": 0.015, "p50": 0.015, "max": 0.015},
            )
            self.assertNotIn("active_window_queue_depth_on_impossible_targets_count", maker_fireability)
            target_summary = maker_fireability.get("active_window_target_summary", [])
            target_a = next(item for item in target_summary if item.get("target_ref") == "target-a")
            target_d = next(item for item in target_summary if item.get("target_ref") == "target-d")
            self.assertEqual(float(target_a.get("window_row_count") or 0.0), 3.0)
            self.assertEqual(float(target_a.get("submitted_count") or 0.0), 2.0)
            self.assertEqual(target_a.get("viability_class"), "viable_only")
            self.assertEqual(target_a.get("submit_sec_to_expiry_sample"), [59.0, 55.0])
            self.assertEqual(target_a.get("submit_gap_sec_sample"), [4.0])
            self.assertEqual(target_d.get("viability_class"), "impossible_only")
            self.assertAlmostEqual(float(target_d.get("market_probability_p50") or 0.0), 0.015, places=9)

    def test_build_report_emits_maker_market_snapshot_support_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-admission-shadow"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            events = [
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "shadow-clean",
                    "target_side_ref": "target-a|BUY",
                    "ts_decision_utc": "2026-01-01T12:00:05Z",
                    "sec_to_expiry": 12.0,
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "viability_class": "viable_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": 0.0,
                    "fill_prob_margin": 0.02,
                    "same_target_side_snapshot_count_prior": 0,
                    "desired_quote_price": 0.50,
                    "visible_depth_shares": 1400.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "open_maker_orders_total": 2,
                    "size_to_visible_depth_ratio": 0.4,
                    "decision_result": "submitted",
                    "order_submit_id": "order-clean",
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "shadow-trash",
                    "target_side_ref": "target-b|SELL",
                    "ts_decision_utc": "2026-01-01T03:00:05Z",
                    "sec_to_expiry": 25.0,
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "viability_class": "impossible_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": 70.0,
                    "fill_prob_margin": -0.02,
                    "same_target_side_snapshot_count_prior": 2,
                    "desired_quote_price": 0.60,
                    "visible_depth_shares": 400.0,
                    "secondary_oracle_status": "direction_mismatch",
                    "secondary_oracle_confirmation": False,
                    "open_maker_orders_total": 5,
                    "size_to_visible_depth_ratio": 1.3,
                    "decision_result": "submitted",
                    "order_submit_id": "order-trash",
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "shadow-external",
                    "target_side_ref": "target-c|BUY",
                    "ts_decision_utc": "2026-01-01T09:00:05Z",
                    "sec_to_expiry": 18.0,
                    "financial_posture_class": "HALT_NEW_RISK",
                    "market_reference_class": "authoritative",
                    "viability_class": "viable_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": 0.0,
                    "fill_prob_margin": 0.01,
                    "same_target_side_snapshot_count_prior": 0,
                    "desired_quote_price": 0.40,
                    "visible_depth_shares": 200.0,
                    "secondary_oracle_status": "unknown",
                    "secondary_oracle_confirmation": False,
                    "open_maker_orders_total": 7,
                    "size_to_visible_depth_ratio": 0.3,
                    "decision_result": "quote_unchanged",
                    "order_submit_id": None,
                    **_historical_recovery_lineage(active=False),
                },
            ]
            outcome_rows = [
                {
                    "order_submit_id": "order-clean",
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "claim_boundary_class": "complete",
                    "evaluation_horizon_ms": 5000,
                    "decision_quality": "correct",
                    "fill_count": 1,
                },
                {
                    "order_submit_id": "order-trash",
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "claim_boundary_class": "complete",
                    "evaluation_horizon_ms": 5000,
                    "decision_quality": "incorrect",
                    "fill_count": 2,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (reports_dir / "outcome_truth_records.jsonl").write_text(
                "\n".join(json.dumps(x) for x in outcome_rows) + "\n",
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            summary = support_artifacts.get("maker_market_snapshot_summary", {})
            calibration = support_artifacts.get("maker_market_snapshot_calibration_audit", {})
            rows = support_artifacts.get("maker_market_snapshot_rows", [])

            self.assertEqual(int(summary.get("population_class_counts", {}).get("candidate", 0)), 2)
            self.assertEqual(int(summary.get("population_class_counts", {}).get("external_blocked", 0)), 1)
            self.assertEqual(int(summary.get("admission_class_counts", {}).get("clean", 0)), 1)
            self.assertEqual(int(summary.get("admission_class_counts", {}).get("trash", 0)), 1)
            self.assertEqual(int(summary.get("maker_market_snapshot_version", 0)), 1)
            self.assertEqual(summary.get("cannon_window_class_distribution"), {"15_to_20s": 1, "10_to_15s": 1, "gt_20s": 1})
            self.assertEqual(
                summary.get("maker_timing_band_class_distribution"),
                {"10_to_15s": 1, "15_to_20s": 1, "20_to_30s": 1},
            )
            self.assertEqual(
                summary.get("candidate_count_by_timing_band"),
                {"10_to_15s": 1, "20_to_30s": 1},
            )
            self.assertEqual(
                summary.get("admission_class_distribution_by_timing_band"),
                {
                    "10_to_15s": {"clean": 1, "borderline": 0, "trash": 0},
                    "20_to_30s": {"clean": 0, "borderline": 0, "trash": 1},
                },
            )
            self.assertEqual(summary.get("submitted_count_by_timing_band"), {"10_to_15s": 1, "20_to_30s": 1})
            self.assertTrue(bool(rows))
            self.assertIn(str(rows[0].get("lifecycle_phase") or ""), {"prepare", "maker_window", "taker_window", "resolve", "scan", "unknown"})
            self.assertNotIn("effective_stage", rows[0])
            self.assertNotIn("stage_bucket", rows[0])
            self.assertNotIn("raw_stage", rows[0])
            self.assertNotIn("stage", rows[0])
            self.assertEqual(
                summary.get("complete_joined_count_by_timing_band"),
                {"10_to_15s": 1, "20_to_30s": 1},
            )
            self.assertEqual(
                summary.get("complete_bad_ratio_by_timing_band"),
                {"10_to_15s": 0.0, "20_to_30s": 1.0},
            )
            self.assertEqual(
                summary.get("session_regime_class_distribution"),
                {
                    "asia_dominant_heuristic": 1,
                    "transition_heuristic": 1,
                    "usa_europe_peak_heuristic": 1,
                },
            )
            self.assertEqual(
                summary.get("stack_pressure_class_distribution"),
                {"below_soft_cap": 1, "over_hard_cap": 1, "within_hard_cap": 1},
            )
            self.assertEqual(
                summary.get("secondary_oracle_status_distribution"),
                {"confirmed": 1, "direction_mismatch": 1, "unknown": 1},
            )
            self.assertEqual(
                summary.get("secondary_oracle_confirmation_distribution"),
                {"confirmed": 1, "not_confirmed": 2},
            )
            self.assertEqual(
                summary.get("cannon_depth_requirement_counts"),
                {"met": 2, "not_met": 1},
            )
            self.assertAlmostEqual(
                float(summary.get("depth_multiple_vs_cannon_target_summary", {}).get("mean") or 0.0),
                (7.0 + (240.0 / 100.0) + (80.0 / 100.0)) / 3.0,
                places=9,
            )
            self.assertAlmostEqual(
                float(summary.get("complete_bad_ratio_by_class", {}).get("trash", 0.0)),
                1.0,
                places=9,
            )
            self.assertEqual(
                int(calibration.get("complete_joined_count_by_class", {}).get("clean", 0)),
                1,
            )
            self.assertEqual(
                calibration.get("maker_timing_band_class_distribution"),
                {"10_to_15s": 1, "15_to_20s": 1, "20_to_30s": 1},
            )
            self.assertEqual(int(calibration.get("maker_market_snapshot_version", 0)), 1)
            self.assertEqual(len(rows), 3)

            _write_support_artifacts(reports_dir, support_artifacts)
            self.assertTrue((reports_dir / "maker_market_snapshot.jsonl").exists())
            self.assertTrue((reports_dir / "maker_market_snapshot_summary.json").exists())
            self.assertTrue((reports_dir / "maker_market_snapshot_calibration_audit.json").exists())

    def test_build_report_backfills_legacy_maker_market_snapshot_from_quote_and_submit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-legacy-admission-shadow"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"

            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": run_id,
                    "evaluation_scope": "maker",
                    "target_ref": "target-clean",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.45,
                    "market_probability": 0.55,
                    "edge_value": -0.10,
                    "cycle_index": 10,
                    "submitted": True,
                    "order_id": "order-clean",
                    "raw_stage": "EXTREME_ONLY",
                    "ts_decision_utc": "2026-01-01T00:00:10Z",
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "order_submit",
                    "run_id": run_id,
                    "submission_lane": "maker",
                    "order_id": "order-clean",
                    "target_ref": "target-clean",
                    "side": "SELL",
                    "price": 0.551,
                    "size": 600.0,
                    "queue_ahead_size": 100.0,
                    "expected_fill_prob": 0.14,
                    "financial_posture_class": "NORMAL",
                    "decision_reference_recoverable": True,
                    "ts_decision_utc": "2026-01-01T00:00:10.050000Z",
                    "maker_market_viability": {
                        "fair_probability": 0.45,
                        "market_probability": 0.55,
                        "edge_signed": -0.10,
                        "financial_posture_class": "NORMAL",
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        **_historical_recovery_lineage(active=False),
                    },
                    "size_resolution": {
                        "price_used": 0.55,
                        "visible_depth_shares": 400.0,
                        "resolved_shares": 600.0,
                    },
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": run_id,
                    "evaluation_scope": "maker",
                    "target_ref": "target-trash",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.70,
                    "market_probability": 0.60,
                    "edge_value": 0.10,
                    "cycle_index": 11,
                    "submitted": False,
                    "ts_decision_utc": "2026-01-01T00:00:11Z",
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "quote_quality_skip",
                    "run_id": run_id,
                    "side": "BUY",
                    "price": 0.60,
                    "size": 500.0,
                    "queue_ahead_size": 380.0,
                    "expected_fill_prob": 0.01,
                    "min_expected_fill_prob": 0.045,
                    "skip_reason": "queue_ahead_too_deep",
                    "ts_decision_utc": "2026-01-01T00:00:11.050000Z",
                    **_historical_recovery_lineage(active=False),
                },
            ]
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_status_utc": "2026-01-01T00:00:09Z",
                    "financial_posture_class": "NORMAL",
                    "gauge.open_orders": 0,
                }
            ]
            outcome_rows = [
                {
                    "order_submit_id": "order-clean",
                    "submission_lane_truth": "maker",
                    "outcome_truth_status": "complete",
                    "claim_boundary_class": "not_provable_missing_inputs",
                    "evaluation_horizon_ms": 5000,
                    "decision_quality": "correct",
                    "fill_count": 1,
                }
            ]
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "strategy": {
                                "execution_quality": {
                                    "min_expected_fill_prob": 0.045,
                                    "max_queue_ahead_size": 300.0,
                                }
                            },
                            "sizing": {
                                "maker_competitive_min_notional_usd": 350.0,
                                "maker_competitive_max_shares": 8000.0,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text("\n".join(json.dumps(x) for x in status_rows) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (reports_dir / "outcome_truth_records.jsonl").write_text(
                "\n".join(json.dumps(x) for x in outcome_rows) + "\n",
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            summary = support_artifacts.get("maker_market_snapshot_summary", {})
            calibration = support_artifacts.get("maker_market_snapshot_calibration_audit", {})
            rows = support_artifacts.get("maker_market_snapshot_rows", [])

            self.assertEqual(int(summary.get("row_count", 0)), 2)
            self.assertEqual(
                summary.get("snapshot_source_class_distribution", {}).get("legacy_quote_or_submit_backfill_v1"),
                2,
            )
            self.assertEqual(int(summary.get("population_class_counts", {}).get("candidate", 0)), 2)
            self.assertEqual(int(summary.get("admission_class_counts", {}).get("clean", 0)), 2)
            self.assertEqual(int(summary.get("admission_class_counts", {}).get("trash", 0)), 0)
            self.assertEqual(int(summary.get("maker_market_snapshot_version", 0)), 1)
            self.assertEqual(summary.get("secondary_oracle_status_distribution"), {"unknown": 2})
            self.assertEqual(summary.get("secondary_oracle_confirmation_distribution"), {"not_confirmed": 2})
            self.assertEqual(summary.get("cannon_window_class_distribution"), {"unknown": 2})
            self.assertEqual(summary.get("maker_timing_band_class_distribution"), {"unknown": 2})
            self.assertTrue(bool(rows))
            rows_by_target = {str(row.get("target_ref") or ""): row for row in rows}
            self.assertEqual(
                str(rows_by_target["target-clean"].get("lineage_stage") or ""),
                "LINEAGE_ONLY_0_TO_20S",
            )
            self.assertEqual(str(rows_by_target["target-trash"].get("lineage_stage") or ""), "UNKNOWN")
            self.assertEqual(str(rows_by_target["target-clean"].get("lifecycle_phase") or ""), "prepare")
            self.assertNotIn("effective_stage", rows_by_target["target-clean"])
            self.assertNotIn("stage_bucket", rows_by_target["target-clean"])
            self.assertNotIn("raw_stage", rows_by_target["target-clean"])
            self.assertNotIn("stage", rows_by_target["target-clean"])
            self.assertEqual(
                int(calibration.get("complete_joined_count_by_class", {}).get("clean", 0)),
                1,
            )
            self.assertEqual(len(rows), 2)

    def test_build_report_emits_maker_cannon_late_window_probe_support_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-cannon-probe"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "sizing": {
                                "maker_competitive_min_notional_usd": 350.0,
                                "maker_competitive_max_shares": 8000.0,
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-clean",
                    "target_ref": "target-clean",
                    "ts_decision_utc": "2026-01-01T12:00:05Z",
                    "time_remaining_sec": 8.0,
                    "lineage_stage": "EXTREME_ONLY",
                    "maker_phase_allowed": False,
                    "fair_probability": 0.62,
                    "market_probability": 0.55,
                    "edge_value": 0.07,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 2000.0,
                    "open_maker_orders_total": 1,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-trash",
                    "target_ref": "target-trash",
                    "ts_decision_utc": "2026-01-01T12:00:07Z",
                    "time_remaining_sec": 8.5,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.12,
                    "market_probability": 0.03,
                    "edge_value": 0.09,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "direction_mismatch",
                    "secondary_oracle_confirmation": False,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 500.0,
                    "open_maker_orders_total": 4,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-external",
                    "target_ref": "target-external",
                    "ts_decision_utc": "2026-01-01T12:00:08Z",
                    "time_remaining_sec": 9.0,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.58,
                    "market_probability": 0.52,
                    "edge_value": 0.06,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 1800.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-thin",
                    "target_ref": "target-thin",
                    "ts_decision_utc": "2026-01-01T12:00:09Z",
                    "time_remaining_sec": 7.5,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.60,
                    "market_probability": 0.57,
                    "edge_value": 0.03,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": None,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-backfill",
                    "ts_decision_utc": "2026-01-01T12:00:08.972000Z",
                    "best_bid_price": 0.49,
                    "best_bid_size": 1500.0,
                    "source": "rest",
                },
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-backfill",
                    "ts_decision_utc": "2026-01-01T12:00:08.978000Z",
                    "best_ask_price": 0.51,
                    "best_ask_size": 1700.0,
                    "source": "rest",
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-backfill",
                    "target_ref": "target-backfill",
                    "ts_decision_utc": "2026-01-01T12:00:09Z",
                    "time_remaining_sec": 8.0,
                    "lineage_stage": "EXTREME_ONLY",
                    "maker_phase_allowed": False,
                    "fair_probability": 0.70,
                    "market_probability": None,
                    "edge_value": None,
                    "market_reference_mode": "missing",
                    "market_reference_basis": "missing",
                    "market_reference_source_side": "none",
                    "market_reference_class": "not_available",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": None,
                    "probe_visible_depth_shares": None,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-old",
                    "target_ref": "target-old",
                    "ts_decision_utc": "2026-01-01T12:00:10Z",
                    "time_remaining_sec": 35.0,
                    "lineage_stage": "MAKER_POSITION",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.54,
                    "market_probability": 0.50,
                    "edge_value": 0.04,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 900.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            summary = support_artifacts.get("maker_cannon_late_window_probe_summary", {})
            rows = support_artifacts.get("maker_cannon_late_window_probe_rows", [])

            self.assertEqual(int(summary.get("maker_cannon_probe_version", 0)), 3)
            self.assertEqual(int(summary.get("row_count", 0)), 5)
            self.assertEqual(int(summary.get("total_maker_edge_eval_rows", 0)), 6)
            self.assertEqual(int(summary.get("late_window_raw_row_count", 0)), 5)
            self.assertEqual(int(summary.get("ignored_non_late_window_row_count", 0)), 1)
            self.assertEqual(
                summary.get("population_class_counts"),
                {"candidate": 3, "external_blocked": 1, "truth_thin": 1},
            )
            self.assertEqual(int(summary.get("full_cannon_candidate_count", 0)), 1)
            self.assertEqual(
                int(summary.get("latent_market_full_cannon_candidate_count", 0)),
                2,
            )
            self.assertEqual(
                int(summary.get("external_blocked_latent_market_evaluable_count", 0)),
                1,
            )
            self.assertEqual(
                int(summary.get("external_blocked_latent_market_full_cannon_candidate_count", 0)),
                1,
            )
            self.assertEqual(int(summary.get("full_candidate_runtime_phase_disallow_count", 0)), 0)
            self.assertEqual(
                summary.get("reject_reason_distribution"),
                {
                    "financial_posture_halt_new_risk": 1,
                    "insufficient_depth_multiple": 2,
                    "market_probability": 1,
                    "non_viable_geometry": 1,
                    "secondary_oracle_direction_mismatch": 1,
                    "stack_soft_cap_reached": 1,
                },
            )
            self.assertEqual(
                summary.get("latent_market_truth_class_counts"),
                {"evaluable": 4, "truth_thin": 1},
            )
            self.assertEqual(
                summary.get("latent_market_reject_reason_distribution"),
                {
                    "insufficient_depth_multiple": 2,
                    "non_viable_geometry": 1,
                    "secondary_oracle_direction_mismatch": 1,
                },
            )
            self.assertEqual(
                summary.get("external_blocked_latent_market_reject_reason_distribution"),
                {},
            )
            self.assertEqual(
                summary.get("lifecycle_phase_distribution"),
                {"maker_window": 5},
            )
            self.assertNotIn("stage_distribution", summary)
            self.assertEqual(
                summary.get("market_reference_class_distribution"),
                {"authoritative": 4, "not_available": 1},
            )
            self.assertEqual(
                summary.get("market_reference_mode_distribution"),
                {"direct_midpoint": 4, "missing": 1},
            )
            self.assertEqual(
                summary.get("market_reference_source_side_distribution"),
                {"none": 5},
            )
            self.assertEqual(
                summary.get("market_probability_band_distribution"),
                {"0p01_to_0p05": 1, "interior": 3, "unknown": 1},
            )
            self.assertEqual(
                summary.get("favored_side_depth_class_distribution"),
                {"positive": 4, "zero_imputed": 1},
            )
            self.assertEqual(
                summary.get("maker_phase_allowed_distribution"),
                {"allowed": 5},
            )
            self.assertEqual(
                summary.get("probe_visible_depth_fail_closed_zero_distribution"),
                {"imputed_zero": 1, "reported_or_not_needed": 4},
            )
            self.assertEqual(summary.get("cannon_window_class_distribution"), {"le_10s": 5})
            self.assertEqual(summary.get("geometry_viable_counts"), {"not_viable": 1, "unknown": 1, "viable": 3})
            self.assertEqual(summary.get("cannon_depth_requirement_counts"), {"met": 2, "not_met": 2, "unknown": 1})
            self.assertEqual(len(rows), 5)
            clean_row = next(row for row in rows if str(row.get("token_id")) == "t-clean")
            self.assertEqual(
                str(clean_row.get("lineage_stage") or ""),
                "LINEAGE_ONLY_0_TO_20S",
            )
            self.assertEqual(bool(clean_row.get("maker_phase_allowed")), True)
            self.assertNotIn("stage", clean_row)
            self.assertNotIn("effective_stage", clean_row)
            self.assertEqual(bool(clean_row.get("full_cannon_candidate")), True)
            self.assertNotIn("maker_stage_or_policy_disallow", list(clean_row.get("reject_reasons") or []))
            self.assertEqual(str(clean_row.get("latent_market_truth_class") or ""), "evaluable")
            self.assertEqual(bool(clean_row.get("latent_market_full_cannon_candidate")), True)
            backfill_row = next(row for row in rows if str(row.get("token_id")) == "t-backfill")
            self.assertEqual(bool(backfill_row.get("market_reference_backfill_applied")), True)
            self.assertEqual(str(backfill_row.get("market_reference_class") or ""), "not_available")
            self.assertEqual(str(backfill_row.get("market_reference_mode") or ""), "missing")
            self.assertEqual(str(backfill_row.get("market_reference_source_side") or ""), "none")
            self.assertEqual(str(backfill_row.get("probe_favored_side") or ""), "BUY")
            self.assertIsNone(backfill_row.get("market_probability"))
            self.assertEqual(float(backfill_row.get("probe_visible_depth_shares")), 1500.0)
            self.assertEqual(str(backfill_row.get("population_class") or ""), "truth_thin")
            self.assertEqual(str(backfill_row.get("latent_market_truth_class") or ""), "truth_thin")
            self.assertEqual(bool(backfill_row.get("full_cannon_candidate")), False)
            external_row = next(row for row in rows if str(row.get("token_id")) == "t-external")
            self.assertEqual(str(external_row.get("population_class") or ""), "external_blocked")
            self.assertEqual(bool(external_row.get("latent_market_full_cannon_candidate")), True)
            thin_row = next(row for row in rows if str(row.get("token_id")) == "t-thin")
            self.assertEqual(bool(thin_row.get("probe_visible_depth_fail_closed_zero_imputed")), True)
            self.assertEqual(
                float(thin_row.get("probe_visible_depth_shares"))
                if thin_row.get("probe_visible_depth_shares") is not None
                else -1.0,
                0.0,
            )
            self.assertEqual(bool(thin_row.get("full_cannon_candidate")), False)
            self.assertEqual(
                list(thin_row.get("latent_market_reject_reasons") or []),
                ["insufficient_depth_multiple"],
            )

            _write_support_artifacts(reports_dir, support_artifacts)
            self.assertTrue((reports_dir / "maker_cannon_late_window_probe.jsonl").exists())
            self.assertTrue((reports_dir / "maker_cannon_late_window_probe_summary.json").exists())

    def test_build_report_preserves_runtime_truth_when_backfill_exists_in_probe_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-brain-preserve-runtime"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            (root / f"run_manifest_{run_id}.json").write_text(json.dumps({"config": {}}) + "\n", encoding="utf-8")
            events = [
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-late",
                    "ts_decision_utc": "2026-01-01T12:00:09.950000Z",
                    "best_bid_price": 0.49,
                    "best_bid_size": 1200.0,
                    "source": "rest",
                },
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-late",
                    "ts_decision_utc": "2026-01-01T12:00:09.980000Z",
                    "best_ask_price": 0.51,
                    "best_ask_size": 1300.0,
                    "source": "rest",
                },
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-mid",
                    "ts_decision_utc": "2026-01-01T12:00:29.950000Z",
                    "best_bid_price": 0.47,
                    "best_bid_size": 900.0,
                    "source": "rest",
                },
                {
                    "event_type": "book_top",
                    "run_id": run_id,
                    "token_id": "t-mid",
                    "ts_decision_utc": "2026-01-01T12:00:29.980000Z",
                    "best_ask_price": 0.53,
                    "best_ask_size": 1100.0,
                    "source": "rest",
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-late",
                    "target_ref": "target-late",
                    "ts_decision_utc": "2026-01-01T12:00:10Z",
                    "time_remaining_sec": 10.0,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.63,
                    "market_probability": None,
                    "edge_value": None,
                    "market_reference_mode": "missing",
                    "market_reference_basis": "missing",
                    "market_reference_source_side": "none",
                    "market_reference_class": "not_available",
                    "financial_posture_class": "NORMAL",
                    "secondary_fair_probability": 0.61,
                    "secondary_oracle_status": "unknown",
                    "secondary_oracle_confirmation": False,
                    "probe_favored_side": None,
                    "probe_visible_depth_shares": None,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-mid",
                    "target_ref": "target-mid",
                    "ts_decision_utc": "2026-01-01T12:00:30Z",
                    "time_remaining_sec": 30.0,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "fair_probability": 0.37,
                    "market_probability": None,
                    "edge_value": None,
                    "market_reference_mode": "missing",
                    "market_reference_basis": "missing",
                    "market_reference_source_side": "none",
                    "market_reference_class": "not_available",
                    "financial_posture_class": "NORMAL",
                    "secondary_fair_probability": 0.39,
                    "secondary_oracle_status": "unknown",
                    "secondary_oracle_confirmation": False,
                    "probe_favored_side": None,
                    "probe_visible_depth_shares": None,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            late_rows = support_artifacts.get("maker_cannon_late_window_probe_rows", [])
            mid_rows = support_artifacts.get("maker_mid_window_probe_rows", [])

            late_row = next(row for row in late_rows if str(row.get("token_id")) == "t-late")
            mid_row = next(row for row in mid_rows if str(row.get("token_id")) == "t-mid")

            for row in (late_row, mid_row):
                self.assertEqual(str(row.get("market_reference_class") or ""), "not_available")
                self.assertEqual(str(row.get("market_reference_mode") or ""), "missing")
                self.assertEqual(str(row.get("market_reference_basis") or ""), "missing")
                self.assertEqual(str(row.get("market_reference_source_side") or ""), "none")
                self.assertEqual(str(row.get("secondary_oracle_status") or ""), "unknown")
                self.assertEqual(bool(row.get("secondary_oracle_confirmation")), False)
                self.assertIsNone(row.get("market_probability"))
                self.assertEqual(bool(row.get("market_reference_backfill_applied")), True)
                self.assertEqual(str(row.get("population_class") or ""), "truth_thin")

    def test_build_report_emits_maker_zero_submit_reconciliation_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-maker-zero-submit-audit"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "strategy": {
                                "maker_market_viability": {
                                    "selection_gate": {
                                        "min_sec_to_expiry": 6.0,
                                        "max_sec_to_expiry": 9.0,
                                    }
                                }
                            },
                            "sizing": {
                                "maker_competitive_min_notional_usd": 350.0,
                                "maker_competitive_max_shares": 8000.0,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-prereq",
                    "target_ref": "target-prereq",
                    "ts_decision_utc": "2026-01-01T12:00:01Z",
                    "time_remaining_sec": 35.0,
                    "lineage_stage": "MAKER_POSITION",
                    "maker_phase_allowed": False,
                    "action_taken": "none",
                    "block_reason": "phase_disallow_maker",
                    "fair_probability": 0.55,
                    "market_probability": 0.50,
                    "edge_value": 0.05,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 500.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-starve",
                    "target_ref": "target-starve",
                    "ts_decision_utc": "2026-01-01T12:00:12Z",
                    "time_remaining_sec": 8.0,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_cause": "no_desired_quote",
                    "maker_no_submission_category": "no_desired_quote",
                    "fair_probability": 0.58,
                    "market_probability": 0.50,
                    "edge_value": 0.08,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 100.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-reject",
                    "target_ref": "target-reject",
                    "ts_decision_utc": "2026-01-01T12:00:11.500000Z",
                    "time_remaining_sec": 8.5,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_cause": "launch_safe_selection_insufficient_depth_multiple",
                    "maker_no_submission_category": "unknown",
                    "fair_probability": 0.60,
                    "market_probability": 0.50,
                    "edge_value": 0.10,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 100.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-offband",
                    "target_ref": "target-offband",
                    "ts_decision_utc": "2026-01-01T12:00:18Z",
                    "time_remaining_sec": 18.0,
                    "lineage_stage": "EXTREME_ONLY",
                    "maker_phase_allowed": False,
                    "action_taken": "none",
                    "block_reason": "phase_disallow_maker",
                    "fair_probability": 0.65,
                    "market_probability": 0.50,
                    "edge_value": 0.15,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 2000.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "shadow-reject",
                    "token_id": "t-reject",
                    "target_ref": "target-reject",
                    "target_side_ref": "target-reject|BUY",
                    "side": "BUY",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "ts_decision_utc": "2026-01-01T12:00:11.450000Z",
                    "sec_to_expiry": 8.5,
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "viability_class": "viable_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": 0.0,
                    "fill_prob_margin": 0.02,
                    "same_target_side_snapshot_count_prior": 0,
                    "same_target_side_submit_count_prior": 0,
                    "desired_quote_price": 0.50,
                    "visible_depth_shares": 100.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "open_maker_orders_total": 0,
                    "size_to_visible_depth_ratio": 0.5,
                    "selection_gate_min_sec_to_expiry": 6.0,
                    "selection_gate_max_sec_to_expiry": 9.0,
                    "selection_gate_primary_reject_reason": "insufficient_depth_multiple",
                    "selection_gate_all_reject_reasons": ["insufficient_depth_multiple"],
                    "decision_result": "viability_rejected",
                    "decision_block_reason": "launch_safe_selection_insufficient_depth_multiple",
                    "order_submit_id": None,
                    **_historical_recovery_lineage(active=False),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            self.assertIsInstance(report.get("maker_market_snapshot_summary"), dict)
            self.assertIsInstance(report.get("maker_snapshot_summary"), dict)
            self.assertEqual(report.get("maker_snapshot_summary"), report.get("maker_market_snapshot_summary"))
            self.assertIsInstance(report.get("maker_viability_summary"), dict)
            self.assertEqual(report.get("maker_viability_summary"), report.get("maker_market_viability"))
            self.assertIsInstance(report.get("maker_participation_waterfall"), dict)
            self.assertIsInstance(report.get("maker_participation_summary"), dict)
            self.assertEqual(report.get("maker_participation_summary"), report.get("maker_participation_waterfall"))
            self.assertIsInstance(report.get("maker_zero_submit_root_cause_audit"), dict)
            waterfall = support_artifacts.get("maker_participation_waterfall", {})
            quote_starvation = support_artifacts.get("maker_quote_starvation_summary", {})
            truth_reference = support_artifacts.get("maker_truth_reference_starvation_summary", {})
            quote_construction = support_artifacts.get("maker_quote_construction_summary", {})
            timing_matrix = support_artifacts.get("maker_timing_band_diagnostic_matrix", {})
            timing_decision = support_artifacts.get("maker_timing_band_decision", {})
            root_cause = support_artifacts.get("maker_zero_submit_root_cause_audit", {})
            starvation_rows = support_artifacts.get("maker_quote_starvation_audit_rows", [])

            self.assertEqual(int(waterfall.get("reconciliation", {}).get("maker_rows_total", 0)), 4)
            self.assertEqual(int(waterfall.get("reconciliation", {}).get("terminal_rows_accounted", 0)), 4)
            self.assertEqual(bool(waterfall.get("reconciliation", {}).get("accounting_closed")), True)
            self.assertEqual(int(waterfall.get("stages", {}).get("stage_band_allowed_rows", {}).get("count", 0)), 2)
            self.assertEqual(int(waterfall.get("stages", {}).get("prequote_prereq_pass_rows", {}).get("count", 0)), 2)
            self.assertEqual(
                int(waterfall.get("stages", {}).get("desired_quote_present_rows", {}).get("count", 0)),
                1,
            )
            self.assertEqual(int(waterfall.get("stages", {}).get("desired_quote_missing_rows", {}).get("count", 0)), 1)
            self.assertEqual(int(waterfall.get("stages", {}).get("snapshot_rows", {}).get("count", 0)), 1)
            self.assertEqual(int(waterfall.get("stages", {}).get("selection_rejected_rows", {}).get("count", 0)), 1)
            self.assertEqual(
                waterfall.get("terminal_path_counts"),
                {
                    "desired_quote_missing": 1,
                    "stage_band_excluded": 2,
                    "selection_rejected": 1,
                },
            )
            self.assertEqual(int(quote_starvation.get("row_count", 0)), 1)
            self.assertEqual(int(quote_starvation.get("quote_starvation_row_count", 0)), 1)
            self.assertEqual(
                quote_starvation.get("maker_no_submission_cause_distribution"),
                {"no_desired_quote": 1},
            )
            self.assertEqual(
                quote_starvation.get("market_reference_mode_distribution"),
                {"direct_midpoint": 1},
            )
            self.assertEqual(
                quote_starvation.get("fair_probability_presence_distribution"),
                {"present": 1},
            )
            self.assertEqual(len(starvation_rows), 1)
            self.assertEqual(starvation_rows[0].get("desired_quote_present"), False)
            self.assertEqual(int(truth_reference.get("row_count", 0)), 2)
            self.assertEqual(
                truth_reference.get("truth_readiness_distribution"),
                truth_reference.get("truth_readiness_state_distribution"),
            )
            self.assertEqual(int(quote_construction.get("truth_sound_row_count", 0)), 2)
            self.assertEqual(int(quote_construction.get("authoritative_complete_row_count", 0)), 2)
            self.assertEqual(
                timing_matrix.get("bands", {}).get("15_to_20s", {}).get("observational_candidate_quality", {}).get(
                    "full_cannon_candidate_count"
                ),
                1,
            )
            self.assertEqual(
                timing_matrix.get("bands", {}).get("15_to_20s", {}).get(
                    "runtime_eligible_active_band_quality", {}
                ).get("row_count"),
                0,
            )
            self.assertEqual(
                timing_matrix.get("bands", {}).get("aggregate_10_to_20s", {}).get("band_kind"),
                "derived_aggregate",
            )
            self.assertEqual(
                str(timing_decision.get("recommended_action") or ""),
                "no_timing_change_truth_or_quoteability_dominant",
            )
            self.assertEqual(
                str(timing_decision.get("recommended_timing_action") or ""),
                "no_timing_change_truth_or_quoteability_dominant",
            )
            self.assertEqual(
                root_cause.get("zero_submit_classification"),
                "mixed-cause starvation",
            )
            self.assertEqual(
                root_cause.get("decision_readiness"),
                "ready_for_truth_packet",
            )
            self.assertIsInstance(root_cause.get("known_truths"), dict)
            self.assertEqual(int(root_cause.get("snapshot_row_count", 0)), 1)
            self.assertEqual(int(root_cause.get("snapshot_selection_rejected_row_count", 0)), 1)
            contradiction_codes = {
                entry.get("code") for entry in root_cause.get("contradiction_ledger", [])
            }
            self.assertIn("off_band_full_cannon_opportunities_present", contradiction_codes)

            _write_support_artifacts(reports_dir, support_artifacts)
            self.assertTrue((reports_dir / "maker_quote_starvation_audit.jsonl").exists())
            self.assertTrue((reports_dir / "maker_quote_starvation_summary.json").exists())
            self.assertTrue((reports_dir / "maker_truth_reference_starvation_audit.jsonl").exists())
            self.assertTrue((reports_dir / "maker_truth_reference_starvation_summary.json").exists())
            self.assertTrue((reports_dir / "maker_quote_construction_audit.jsonl").exists())
            self.assertTrue((reports_dir / "maker_quote_construction_summary.json").exists())
            self.assertTrue((reports_dir / "maker_participation_waterfall.json").exists())
            self.assertTrue((reports_dir / "maker_timing_band_diagnostic_matrix.json").exists())
            self.assertTrue((reports_dir / "maker_timing_band_decision.json").exists())
            self.assertTrue((reports_dir / "maker_zero_submit_root_cause_audit.json").exists())
            self.assertTrue((reports_dir / "maker_zero_submit_specimen_manifest.json").exists())
            self.assertTrue((reports_dir / "maker_zero_submit_specimen_comparison.json").exists())
            shadow_rows_written = [
                json.loads(line)
                for line in (reports_dir / "maker_market_snapshot.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                shadow_rows_written[0].get("selection_gate_primary_reject_reason"),
                "insufficient_depth_multiple",
            )
            self.assertEqual(
                shadow_rows_written[0].get("selection_gate_all_reject_reasons"),
                ["insufficient_depth_multiple"],
            )
            comparison = json.loads(
                (reports_dir / "maker_zero_submit_specimen_comparison.json").read_text(encoding="utf-8")
            )
            self.assertIn("specimens", comparison)
            self.assertIn("current_run_focus", comparison)

    def test_build_report_emits_maker_quote_integrity_audit_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "484e533d-c9a1-4ac4-bc0d-ce379c624e09"
            events_path = root / "events_2026-04-29.jsonl"
            status_path = root / "status_2026-04-29.jsonl"
            errors_path = root / "errors_2026-04-29.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "profile": {
                                "name": "paper_universal_maker_launch_safe_caliber_250",
                            },
                            "strategy": {
                                "tick_size": 0.001,
                                "execution_quality": {
                                    "enabled": True,
                                    "min_expected_fill_prob": 0.045,
                                    "max_queue_ahead_size": 300.0,
                                    "queue_depth_scale": 120.0,
                                    "distance_scale": 0.02,
                                    "adverse_selection_penalty": 0.3,
                                },
                                "maker_market_viability": {
                                    "selection_gate": {
                                        "min_sec_to_expiry": 10.0,
                                        "max_sec_to_expiry": 15.0,
                                    }
                                },
                            },
                            "sizing": {
                                "maker_competitive_min_notional_usd": 250.0,
                                "maker_competitive_max_shares": 8000.0,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "maker-shadow-56877145-S-9",
                    "token_id": "token-sell",
                    "target_ref": "58b3014fd8bd6ce7",
                    "target_side_ref": "58b3014fd8bd6ce7|SELL",
                    "side": "SELL",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "raw_stage": "EXTREME_ONLY",
                    "ts_decision_utc": "2026-04-29T07:04:48.115Z",
                    "ts_event_utc": "2026-04-29T07:04:48.215Z",
                    "sec_to_expiry": 11.811867,
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "fair_probability": 0.7625117917167464,
                    "market_probability": 0.965,
                    "viability_class": "viable_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": -145.7515,
                    "fill_prob_margin": 0.047179597760081474,
                    "same_target_side_snapshot_count_prior": 3,
                    "same_target_side_submit_count_prior": 0,
                    "desired_quote_price": 0.874,
                    "visible_depth_shares": 440.71,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "open_maker_orders_total": 0,
                    "size_to_visible_depth_ratio": 0.5878468834380886,
                    "selection_gate_min_sec_to_expiry": 10.0,
                    "selection_gate_max_sec_to_expiry": 15.0,
                    "selection_gate_primary_reject_reason": None,
                    "selection_gate_all_reject_reasons": [],
                    "launch_safe_selection_passed": True,
                    "cannon_target_notional_usd": 250.0,
                    "cannon_min_depth_multiple": 1.5,
                    "depth_multiple_vs_cannon_target": 1.54072216,
                    "expected_fill_prob": 0.09217959776008147,
                    "queue_ahead_size": 154.24849999999998,
                    "decision_result": "submitted",
                    "decision_block_reason": None,
                    "order_submit_id": "paper-order-1",
                    "replace_guard_would_block": False,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "pre_submit_cross_guard_adjusted",
                    "run_id": run_id,
                    "token_id": "token-sell",
                    "side": "SELL",
                    "submission_lane": "maker",
                    "original_price": 0.874,
                    "adjusted_price": 0.961,
                    "best_bid_price": 0.96,
                    "best_ask_price": 0.97,
                    "tick_size": 0.001,
                    "ts_decision_utc": "2026-04-29T07:04:48.216Z",
                    "ts_event_utc": "2026-04-29T07:04:48.216Z",
                },
                {
                    "event_type": "order_submit",
                    "run_id": run_id,
                    "token_id": "token-sell",
                    "order_id": "paper-order-1",
                    "target_ref": "58b3014fd8bd6ce7",
                    "side": "SELL",
                    "submission_lane": "maker",
                    "price": 0.961,
                    "size": 259.07,
                    "expected_fill_prob": 0.09217959776008147,
                    "queue_ahead_size": 154.24849999999998,
                    "distance_to_touch": 0.0,
                    "ts_decision_utc": "2026-04-29T07:04:48.224Z",
                    "ts_event_utc": "2026-04-29T07:04:48.224Z",
                    "maker_market_viability": {
                        "market_snapshot_id": "maker-shadow-56877145-S-9",
                        "launch_safe_selection_passed": True,
                        "depth_multiple_vs_cannon_target": 1.54072216,
                    },
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "token-sell",
                    "target_ref": "58b3014fd8bd6ce7",
                    "ts_decision_utc": "2026-04-29T07:04:48.227Z",
                    "ts_event_utc": "2026-04-29T07:04:48.227Z",
                    "time_remaining_sec": 11.811867,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "action_taken": "maker",
                    "block_reason": None,
                    "fair_probability": 0.7625117917167464,
                    "market_probability": 0.965,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "SELL",
                    "probe_visible_depth_shares": 440.71,
                    "open_maker_orders_total": 1,
                    "submitted_order_ids": ["paper-order-1"],
                    "order_id": "paper-order-1",
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "maker_market_snapshot",
                    "run_id": run_id,
                    "market_snapshot_id": "maker-shadow-56877145-S-11",
                    "token_id": "token-sell",
                    "target_ref": "58b3014fd8bd6ce7",
                    "target_side_ref": "58b3014fd8bd6ce7|SELL",
                    "side": "SELL",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "raw_stage": "EXTREME_ONLY",
                    "ts_decision_utc": "2026-04-29T07:04:49.164Z",
                    "ts_event_utc": "2026-04-29T07:04:49.220Z",
                    "sec_to_expiry": 10.802218,
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "fair_probability": 0.7625117917167464,
                    "market_probability": 0.965,
                    "viability_class": "viable_only",
                    "sizing_conflict": False,
                    "queue_delta_shares": -136.8275,
                    "fill_prob_margin": 0.08480571384094183,
                    "same_target_side_snapshot_count_prior": 4,
                    "same_target_side_submit_count_prior": 1,
                    "desired_quote_price": 0.874,
                    "visible_depth_shares": 323.35,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "open_maker_orders_total": 1,
                    "size_to_visible_depth_ratio": 0.801299,
                    "selection_gate_min_sec_to_expiry": 10.0,
                    "selection_gate_max_sec_to_expiry": 15.0,
                    "selection_gate_primary_reject_reason": "insufficient_depth_multiple",
                    "selection_gate_all_reject_reasons": ["insufficient_depth_multiple"],
                    "launch_safe_selection_passed": False,
                    "cannon_target_notional_usd": 250.0,
                    "cannon_min_depth_multiple": 1.5,
                    "depth_multiple_vs_cannon_target": 1.1304316,
                    "expected_fill_prob": 0.12980571384094183,
                    "queue_ahead_size": 113.1725,
                    "decision_result": "selection_rejected",
                    "decision_block_reason": "launch_safe_selection_insufficient_depth_multiple",
                    "order_submit_id": None,
                    "replace_guard_would_block": True,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "order_cancel",
                    "run_id": run_id,
                    "token_id": "token-sell",
                    "order_id": "paper-order-1",
                    "price": 0.961,
                    "side": "SELL",
                    "size": 259.07,
                    "reason": "launch_safe_selection_reject",
                    "cancel_class": "legacy_routine",
                    "submission_lane": "maker",
                    "ts_decision_utc": "2026-04-29T07:04:49.222Z",
                    "ts_event_utc": "2026-04-29T07:04:49.222Z",
                },
                {
                    "event_type": "order_cancel_suppressed",
                    "run_id": run_id,
                    "token_id": "token-sell",
                    "order_id": "paper-order-1",
                    "side": "SELL",
                    "price": 0.961,
                    "remaining_size": 259.07,
                    "requested_cancel_reason": "replace_quote",
                    "request_origin": "maker_replace_logic",
                    "cancel_class_requested": "legacy_routine",
                    "suppression_reason": "commitment_hold_active_pre_expiry",
                    "submission_lane": "maker",
                    "commitment_hold_active": True,
                    "commitment_hold_reason": "late_window_commitment",
                    "commitment_expiry_ts_utc": "2026-04-29T07:05:00Z",
                    "ts_decision_utc": "2026-04-29T07:04:48.900Z",
                    "ts_event_utc": "2026-04-29T07:04:48.900Z",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            manifest = support_artifacts.get("maker_quote_integrity_manifest", {})
            trace_rows = support_artifacts.get("maker_quote_integrity_trace_rows", [])
            semantics = support_artifacts.get("maker_execution_quality_semantics", {})
            mutation_summary = support_artifacts.get("maker_quote_mutation_summary", {})
            survival_audit = support_artifacts.get("maker_resting_order_survival_audit", {})
            summary = support_artifacts.get("maker_quote_integrity_summary", {})

            self.assertEqual(manifest.get("primary_run_id"), run_id)
            self.assertEqual(manifest.get("used_existing_events_only"), True)
            self.assertEqual(manifest.get("required_minimal_runtime_fields"), False)
            self.assertNotIn("maker_queue_pressure_adjustment", manifest.get("observed_event_type_counts", {}))
            self.assertEqual(manifest.get("historical_only_lineage_event_counts", {}), {})
            self.assertEqual(manifest.get("historical_only_lineage_event_summaries", {}), {})
            self.assertEqual(len(trace_rows), 1)
            trace = trace_rows[0]
            self.assertEqual(trace.get("model_plane", {}).get("desired_quote_price"), 0.874)
            self.assertEqual(
                trace.get("quote_plane", {}).get("pre_submit_cross_guard_adjusted_price"),
                0.961,
            )
            self.assertEqual(
                trace.get("quote_plane", {}).get("mutation_classification"),
                "material_cross_guard_only",
            )
            self.assertEqual(
                trace.get("survival_plane", {}).get("cancel_reason"),
                "launch_safe_selection_reject",
            )
            self.assertEqual(
                trace.get("survival_plane", {}).get("cancel_class"),
                "legacy_routine",
            )
            self.assertEqual(
                trace.get("survival_plane", {}).get("survival_classification"),
                "cancel_only_due_to_aggressive_survival_policy",
            )
            self.assertEqual(
                semantics.get("quality_model_semantics"),
                "inside_spread_blind_spot_present",
            )
            self.assertEqual(
                mutation_summary.get("mutation_classification_distribution"),
                {"material_cross_guard_only": 1},
            )
            self.assertEqual(
                survival_audit.get("survival_classification_distribution"),
                {"cancel_only_due_to_aggressive_survival_policy": 1},
            )
            self.assertEqual(
                summary.get("specimen_regime_class"),
                "overnight_logic_specimen",
            )
            self.assertEqual(summary.get("authoritative_for_runtime_blocker_truth"), False)
            self.assertEqual(summary.get("authoritative_for_current_owner_law"), False)
            self.assertEqual(
                summary.get("applicability"),
                "descriptive_only",
            )
            self.assertEqual(
                summary.get("current_owner_artifact"),
                "maker_blocker_ledger.json",
            )
            self.assertEqual(summary.get("peak_hours_economic_conclusion_allowed"), False)
            self.assertEqual(summary.get("order_cancel_class_distribution"), {"legacy_routine": 1})
            self.assertEqual(
                summary.get("order_cancel_suppressed_requested_reason_distribution"),
                {"replace_quote": 1},
            )
            self.assertEqual(summary.get("next_repair_lane"), "A. Quality-model repair")
            disclosures = list(summary.get("disclosures") or [])
            self.assertNotIn(
                "The repaired $250 specimen occurred around 02:04 AM Central, so economic fill-rate and PnL conclusions remain non-authoritative.",
                disclosures,
            )
            self.assertIn(
                "This artifact is support-only and may not outrank canonical runtime blocker truth.",
                disclosures,
            )

            _write_support_artifacts(reports_dir, support_artifacts)
            self.assertTrue((reports_dir / "maker_quote_integrity_manifest.json").exists())
            self.assertTrue((reports_dir / "maker_quote_integrity_trace.jsonl").exists())
            self.assertTrue((reports_dir / "maker_execution_quality_semantics.json").exists())
            self.assertTrue((reports_dir / "maker_quote_mutation_summary.json").exists())
            self.assertTrue((reports_dir / "maker_resting_order_survival_audit.json").exists())
            self.assertTrue((reports_dir / "maker_quote_integrity_summary.json").exists())
            self.assertTrue((reports_dir / "maker_selection_authority_audit.json").exists())
            self.assertTrue((reports_dir / "maker_selection_authority_counterfactual.json").exists())

    def test_build_report_zero_snapshot_reconciliation_discloses_runtime_eligible_pre_snapshot_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-maker-zero-shadow"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "strategy": {
                                "maker_market_viability": {
                                    "selection_gate": {
                                        "min_sec_to_expiry": 10.0,
                                        "max_sec_to_expiry": 15.0,
                                    }
                                }
                            },
                            "sizing": {
                                "maker_competitive_min_notional_usd": 350.0,
                                "maker_competitive_max_shares": 8000.0,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-starve",
                    "target_ref": "target-starve",
                    "ts_decision_utc": "2026-01-01T12:00:12Z",
                    "time_remaining_sec": 8.0,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "maker_phase_allowed": True,
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_cause": "no_desired_quote",
                    "maker_no_submission_category": "no_desired_quote",
                    "fair_probability": 0.58,
                    "market_probability": 0.50,
                    "edge_value": 0.08,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_favored_side": "BUY",
                    "probe_visible_depth_shares": 100.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            shadow_rows = support_artifacts.get("maker_market_snapshot_rows", [])
            root_cause = support_artifacts.get("maker_zero_submit_root_cause_audit", {})
            waterfall = support_artifacts.get("maker_participation_waterfall", {})

            self.assertEqual(shadow_rows, [])
            self.assertEqual(
                int(waterfall.get("stages", {}).get("snapshot_rows", {}).get("count", 0)),
                0,
            )
            contradiction_codes = {
                entry.get("code") for entry in root_cause.get("contradiction_ledger", [])
            }
            self.assertIn(
                "zero_snapshot_with_runtime_eligible_pre_snapshot_rows",
                contradiction_codes,
            )
            self.assertEqual(
                support_artifacts.get("maker_quote_starvation_summary", {}).get(
                    "maker_no_submission_cause_distribution"
                ),
                {"no_desired_quote": 1},
            )
            self.assertEqual(
                root_cause.get("decision_readiness"),
                "ready_for_truth_packet",
            )

    def test_build_report_emits_maker_mid_window_probe_support_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-mid-window-probe"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            reports_dir = root / "reports" / run_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = root / f"run_manifest_{run_id}.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "sizing": {
                                "maker_competitive_min_notional_usd": 350.0,
                                "maker_competitive_max_shares": 8000.0,
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events = [
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-mid-clean",
                    "target_ref": "target-mid-clean",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 25.0,
                    "ts_decision_utc": "2026-01-01T13:00:25Z",
                    "fair_probability": 0.60,
                    "market_probability": 0.50,
                    "edge_value": 0.10,
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "financial_posture_class": "NORMAL",
                    "maker_phase_allowed": False,
                    "secondary_fair_probability": 0.62,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_visible_depth_shares": 1400.0,
                    "open_maker_orders_total": 1,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-mid-trash",
                    "target_ref": "target-mid-trash",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "time_remaining_sec": 35.0,
                    "ts_decision_utc": "2026-01-01T13:00:35Z",
                    "fair_probability": 0.70,
                    "market_probability": 0.60,
                    "edge_value": 0.10,
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "financial_posture_class": "NORMAL",
                    "maker_phase_allowed": True,
                    "secondary_fair_probability": 0.72,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_visible_depth_shares": 100.0,
                    "open_maker_orders_total": 2,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-mid-external",
                    "target_ref": "target-mid-external",
                    "lineage_stage": "EXTREME_ONLY",
                    "time_remaining_sec": 40.0,
                    "ts_decision_utc": "2026-01-01T02:00:40Z",
                    "fair_probability": 0.48,
                    "market_probability": 0.44,
                    "edge_value": 0.04,
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_source_side": "none",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "maker_phase_allowed": False,
                    "secondary_fair_probability": 0.49,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_visible_depth_shares": 1200.0,
                    "open_maker_orders_total": 7,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "run_id": run_id,
                    "token_id": "t-ignored",
                    "target_ref": "target-ignored",
                    "lineage_stage": "EXTREME_ONLY",
                    "time_remaining_sec": 15.0,
                    "ts_decision_utc": "2026-01-01T13:00:15Z",
                    "fair_probability": 0.55,
                    "market_probability": 0.50,
                    "edge_value": 0.05,
                    "market_reference_class": "authoritative",
                    "financial_posture_class": "NORMAL",
                    "maker_phase_allowed": False,
                    "secondary_fair_probability": 0.56,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "probe_visible_depth_shares": 1500.0,
                    "open_maker_orders_total": 0,
                    **_historical_recovery_lineage(active=False),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, include_support_artifacts=True)
            support_artifacts = report.pop("_support_artifacts", {})
            summary = support_artifacts.get("maker_mid_window_probe_summary", {})
            rows = support_artifacts.get("maker_mid_window_probe_rows", [])

            self.assertEqual(int(summary.get("maker_mid_window_probe_version", 0)), 1)
            self.assertEqual(int(summary.get("row_count", 0)), 3)
            self.assertEqual(int(summary.get("total_maker_edge_eval_rows", 0)), 4)
            self.assertEqual(int(summary.get("mid_window_raw_row_count", 0)), 3)
            self.assertEqual(int(summary.get("ignored_non_mid_window_row_count", 0)), 1)
            self.assertEqual(summary.get("population_class_counts"), {"candidate": 2, "external_blocked": 1})
            self.assertEqual(int(summary.get("full_mid_window_candidate_count", 0)), 1)
            self.assertEqual(
                int(summary.get("latent_market_full_mid_window_candidate_count", 0)),
                2,
            )
            self.assertEqual(
                summary.get("maker_timing_band_class_distribution"),
                {"20_to_30s": 1, "30_to_45s": 2},
            )
            self.assertEqual(
                summary.get("lifecycle_phase_distribution"),
                {"prepare": 3},
            )
            self.assertNotIn("stage_distribution", summary)
            self.assertEqual(
                summary.get("reject_reason_distribution"),
                {
                    "financial_posture_halt_new_risk": 1,
                    "insufficient_depth_multiple": 1,
                },
            )
            self.assertEqual(
                summary.get("maker_phase_allowed_distribution"),
                {"disallowed": 3},
            )
            self.assertEqual(
                summary.get("probe_visible_depth_fail_closed_zero_distribution"),
                {"reported_or_not_needed": 3},
            )
            self.assertEqual(len(rows), 3)
            clean_row = next(row for row in rows if str(row.get("token_id")) == "t-mid-clean")
            self.assertEqual(bool(clean_row.get("full_mid_window_candidate")), True)
            self.assertEqual(bool(clean_row.get("maker_phase_allowed")), False)
            trash_row = next(row for row in rows if str(row.get("token_id")) == "t-mid-trash")
            self.assertEqual(bool(trash_row.get("full_mid_window_candidate")), False)
            self.assertEqual(bool(trash_row.get("maker_phase_allowed")), False)
            self.assertEqual(list(trash_row.get("reject_reasons") or []), ["insufficient_depth_multiple"])

            _write_support_artifacts(reports_dir, support_artifacts)
            self.assertTrue((reports_dir / "maker_mid_window_probe.jsonl").exists())
            self.assertTrue((reports_dir / "maker_mid_window_probe_summary.json").exists())

    def test_build_report_emits_maker_sizing_competitiveness_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "order_submit",
                    "run_id": "rid-maker-sizing",
                    "order_id": "m1",
                    "submission_lane": "maker",
                    "size_resolution": {
                        "size_decision_reasons": [
                            "maker_hard_min_notional_floor",
                            "maker_hard_min_shares_floor",
                            "maker_depth_target_notional_floor",
                        ],
                        "maker_hard_floor_active": True,
                        "maker_depth_scaling_active": True,
                        "resolved_notional_usd": 120.0,
                        "visible_depth_shares": 500.0,
                        "effective_depth_shares": 300.0,
                        "maker_depth_target_ratio_applied": 0.2,
                    },
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-maker-sizing",
                    "order_id": "m2",
                    "submission_lane": "maker",
                    "size_resolution": {
                        "size_decision_reasons": [
                            "maker_hard_max_notional_cap",
                            "maker_hard_max_shares_cap",
                        ],
                        "maker_hard_floor_active": True,
                        "maker_depth_scaling_active": False,
                        "resolved_notional_usd": 280.0,
                        "visible_depth_shares": 600.0,
                        "effective_depth_shares": 600.0,
                        "maker_depth_target_ratio_applied": 0.2,
                    },
                },
                {
                    "event_type": "risk_reject",
                    "run_id": "rid-maker-sizing",
                    "submission_lane": "maker",
                    "reason": "size_notional_bounds",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "size_resolution": {
                        "size_decision_reasons": [
                            "maker_hard_min_notional_floor",
                            "maker_hard_max_shares_cap",
                            "maker_hard_min_notional_failed_after_rounding",
                        ],
                        "price_used": 0.12,
                        "maker_hard_notional_range_usd": {
                            "min": 100.0,
                            "max": 250.0,
                        },
                        "maker_hard_share_range": {
                            "min": 200.0,
                            "max": 800.0,
                        },
                    },
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-maker-sizing", "gauge.open_orders": 1}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-maker-sizing")
            maker_sizing = report.get("maker_sizing_competitiveness", {})
            self.assertEqual(float(maker_sizing.get("maker_submit_rows") or 0.0), 2.0)
            self.assertEqual(float(maker_sizing.get("maker_size_resolution_rows") or 0.0), 2.0)
            self.assertEqual(float(maker_sizing.get("hard_min_notional_floor_applied_count") or 0.0), 1.0)
            self.assertEqual(float(maker_sizing.get("hard_min_share_floor_applied_count") or 0.0), 1.0)
            self.assertEqual(float(maker_sizing.get("depth_target_notional_floor_applied_count") or 0.0), 1.0)
            self.assertEqual(float(maker_sizing.get("hard_max_notional_cap_applied_count") or 0.0), 1.0)
            self.assertEqual(float(maker_sizing.get("hard_max_share_cap_applied_count") or 0.0), 1.0)
            self.assertEqual(float(maker_sizing.get("hard_floor_active_rows") or 0.0), 2.0)
            self.assertEqual(float(maker_sizing.get("depth_scaling_active_rows") or 0.0), 1.0)
            self.assertAlmostEqual(float(maker_sizing.get("resolved_notional_usd_p50") or 0.0), 120.0, places=6)
            self.assertEqual(float(maker_sizing.get("maker_sizing_reject_rows") or 0.0), 1.0)
            self.assertEqual(
                float(maker_sizing.get("maker_min_notional_max_shares_conflict_rows") or 0.0),
                1.0,
            )
            self.assertEqual(
                int(
                    (maker_sizing.get("maker_sizing_reject_reason_distribution") or {}).get(
                        "maker_hard_min_notional_failed_after_rounding",
                        0,
                    )
                ),
                1,
            )
            self.assertEqual(
                int((maker_sizing.get("maker_sizing_reject_lineage_stage_distribution") or {}).get("MAKER_TAKER_SELECTIVE", 0)),
                1,
            )
            self.assertEqual(
                int(
                    (maker_sizing.get("maker_sizing_reject_financial_posture_distribution") or {}).get(
                        "HALT_NEW_RISK",
                        0,
                    )
                ),
                1,
            )
            self.assertAlmostEqual(
                float(maker_sizing.get("maker_sizing_reject_max_shares_notional_max") or 0.0),
                96.0,
                places=6,
            )

    def test_build_report_emits_lifecycle_residue_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-recovery",
                    "block_reason": _HISTORICAL_RECOVERY_WAITING_BLOCK_REASON,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    **_historical_recovery_lineage(),
                },
                {
                    "event_type": "order_submission_rejected_local",
                    "run_id": "rid-recovery",
                    "submission_lane": "maker",
                    "reason": "reduce_only_recovery_size_cap_unavailable",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "reduce_only_size_cap_shares": 0.0,
                    "reduce_only_net_shares_live": 0.0,
                    "reduce_only_dynamic_size_cap_source": "live_position_flat_or_wrong_side",
                    **_historical_recovery_lineage(),
                },
                {
                    "event_type": "order_submission_accepted",
                    "run_id": "rid-recovery",
                    "submission_lane": "maker",
                    **_historical_recovery_lineage(),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps(
                    {
                        "run_id": "rid-recovery",
                        "gauge.open_orders": 0,
                        "settlement_hold_required_count": 2,
                        "open_order_cleanup_required_count": 1,
                        "unresolved_lifecycle_obligation_count": 2,
                        "cancel_fail_closed_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-recovery")
            lifecycle_residue = report.get("lifecycle_residue", {})
            self.assertEqual(float(lifecycle_residue.get("settlement_hold_required_count") or 0.0), 2.0)
            self.assertEqual(float(lifecycle_residue.get("open_order_cleanup_required_count") or 0.0), 1.0)
            self.assertEqual(float(lifecycle_residue.get("unresolved_lifecycle_obligation_count") or 0.0), 2.0)
            self.assertEqual(float(lifecycle_residue.get("cancel_fail_closed_count") or 0.0), 1.0)
            self.assertEqual(str(lifecycle_residue.get("classification") or ""), "mixed_open_and_settlement")
            self.assertNotIn("reduce_only_recovery", report)
            summary = render_human_summary(report)
            self.assertIn("lifecycle_residue=", summary)
            self.assertIn("classification=mixed_open_and_settlement", summary)

    def test_build_report_removes_top_level_recovery_cost_surfaces(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "order_submit",
                    "run_id": "rid-recovery-cost",
                    "order_id": "rec-meaningful",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "side": "SELL",
                    "price": 0.44,
                    "size": 10.0,
                    "decision_reference_midpoint": 0.50,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "sec_to_expiry": 31.0,
                    "risk_decision_basis": {
                        "intent_exposure_class": "MEANINGFUL",
                        "reduce_only_terminal_min_notional_usd": 2.0,
                    },
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "financial_posture_class": "HALT_NEW_RISK",
                        "sec_to_expiry": 31.0,
                        "reduce_only_size_cap_shares": 10.0,
                        "reduce_only_size_cap_below_min_order_size": False,
                        "reduce_only_min_order_size_shares": 1.0,
                        "reduce_only_net_shares": 10.0,
                        **_historical_recovery_lineage(),
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-recovery-cost",
                    "order_id": "rec-meaningful",
                    "side": "SELL",
                    "price": 0.44,
                    "size": 10.0,
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-recovery-cost",
                    "order_id": "rec-dust",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "side": "BUY",
                    "price": 0.56,
                    "size": 0.5,
                    "decision_reference_midpoint": 0.50,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "PREEXPIRY_REDUCE_ONLY",
                    "sec_to_expiry": 18.0,
                    "risk_decision_basis": {
                        "intent_exposure_class": "DUST_ELIGIBLE",
                        "reduce_only_terminal_min_notional_usd": 2.0,
                    },
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "financial_posture_class": "PREEXPIRY_REDUCE_ONLY",
                        "sec_to_expiry": 18.0,
                        "reduce_only_size_cap_shares": 0.5,
                        "reduce_only_size_cap_below_min_order_size": True,
                        "reduce_only_min_order_size_shares": 1.0,
                        "reduce_only_net_shares": 0.5,
                        **_historical_recovery_lineage(),
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-recovery-cost",
                    "order_id": "rec-dust",
                    "side": "BUY",
                    "price": 0.56,
                    "size": 0.5,
                },
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "run_id": "rid-recovery-cost",
                    "outcome": "filled",
                    "maker_reduce_only_exit_blocked": True,
                    "maker_no_submission_reason": "submit_rejected_quote_quality_skip_fill_probability",
                },
                {
                    "event_type": _HISTORICAL_PREEXPIRY_EMERGENCY_EVENT,
                    "run_id": "rid-recovery-cost",
                    "outcome": "blocked",
                    "blocked_reason": _HISTORICAL_RECOVERY_SIZE_CAP_BELOW_MIN_ORDER_SIZE,
                    "maker_reduce_only_exit_blocked": True,
                    "maker_no_submission_reason": (
                        f"submit_rejected_{_HISTORICAL_RECOVERY_SIZE_CAP_BELOW_MIN_ORDER_SIZE}"
                    ),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-recovery-cost",
                    "evaluation_scope": "taker",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "none",
                    "block_reason": _HISTORICAL_RECOVERY_WAITING_BLOCK_REASON,
                    "maker_phase_allowed": True,
                    "taker_phase_allowed": True,
                    "time_remaining_sec": 18.0,
                    **_historical_recovery_lineage(),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-recovery-cost",
                    "evaluation_scope": "taker",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "submitted",
                    "block_reason": None,
                    "maker_phase_allowed": True,
                    "taker_phase_allowed": True,
                    "time_remaining_sec": 17.5,
                    **_historical_recovery_lineage(),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-recovery-cost", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-recovery-cost")
            self.assertNotIn("recovery_cost_benefit", report)
            self.assertNotIn("reduce_only_recovery", report)
            self.assertNotIn("preexpiry_recovery_churn", report)
            self.assertNotIn("terminal_handoff_deadband", report)
            summary = render_human_summary(report)
            self.assertNotIn("recovery_cost_benefit=", summary)
            self.assertNotIn("preexpiry_emergency_handoff=", summary)
            self.assertNotIn("preexpiry_recovery_taker_gate=", summary)
            self.assertNotIn("reduce_only_recovery=", summary)

    def test_build_report_emits_taker_competitiveness_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-competitiveness",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "taker_outside_final_window",
                    "lineage_stage": "SNIPER_PRIMARY",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-competitiveness",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "taker_submit_rejected",
                    "taker_submit_reject_reason": "risk_reject_notional_cap",
                    "lineage_stage": "SNIPER_PRIMARY",
                },
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-competitiveness",
                    "token_id": "t1",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": False,
                    "timing_window_class": "outside_window",
                    "edge_abs": 0.08,
                    "conviction_score": 0.10,
                    "block_reason": "taker_outside_final_window",
                    "aggressiveness_level": "none",
                    "hard_min_unachievable": False,
                    "dynamic_size_capped_by_risk": False,
                    "multi_oracle_status": "unknown",
                    "multi_oracle_confirmation": False,
                    "multi_oracle_boost_applied": False,
                },
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-competitiveness",
                    "token_id": "t2",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": True,
                    "timing_window_class": "final15",
                    "edge_abs": 0.24,
                    "conviction_score": 0.82,
                    "block_reason": None,
                    "aggressiveness_level": "final15",
                    "hard_min_unachievable": False,
                    "dynamic_size_capped_by_risk": True,
                    "multi_oracle_status": "confirmed",
                    "multi_oracle_confirmation": True,
                    "multi_oracle_boost_applied": True,
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-taker-competitiveness",
                    "order_id": "taker-1",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "decision_to_submit_latency_ms": 187.5,
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.24,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "price_aggress_bps_applied": 2.0,
                        "hard_min_floor_applied": True,
                        "dynamic_size_capped_by_risk": True,
                        "multi_oracle_status": "confirmed",
                        "multi_oracle_confirmation": True,
                        "multi_oracle_boost_applied": True,
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-taker-competitiveness",
                    "order_id": "taker-1",
                    "token_id": "t2",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10,
                    "fill_policy_basis": "visible_liquidity_top_of_book",
                    "execution_realism_class": "not_modeled",
                    "paper_queue_position_mode": "not_applicable",
                    "paper_chainlink_lag_class": "within_window",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps(
                    {
                        "run_id": "rid-taker-competitiveness",
                        "gauge.open_orders": 0,
                        "secondary_oracle": {
                            "pyth": {
                                "enabled": True,
                                "connected": False,
                                "requests": 2,
                                "errors": 2,
                                "last_error": "HTTP Error 403",
                                "last_http_status": 403,
                                "operational_state": "unavailable_http_403",
                                "feed_id": "feed",
                                "symbol": "BTC/USD",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-taker-competitiveness")
            taker_comp = report.get("taker_competitiveness", {})
            self.assertEqual(float(taker_comp.get("decision_count") or 0.0), 2.0)
            self.assertEqual(float(taker_comp.get("submit_capable_static_decision_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("submit_capable_dynamic_predicted_count") or 0.0), 0.0)
            self.assertEqual(float(taker_comp.get("submit_capable_dynamic_predicted_unknown_count") or 0.0), 2.0)
            self.assertEqual(float(taker_comp.get("submit_capable_decision_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("blocked_decision_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("actual_submit_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("fill_count") or 0.0), 1.0)
            self.assertAlmostEqual(float(taker_comp.get("decision_to_submit_rate") or 0.0), 0.5, places=9)
            self.assertAlmostEqual(float(taker_comp.get("submit_capable_to_submit_rate") or 0.0), 1.0, places=9)
            self.assertAlmostEqual(float(taker_comp.get("submit_capable_dynamic_to_submit_rate") or 0.0), 0.0, places=9)
            self.assertAlmostEqual(float(taker_comp.get("fill_rate_from_submits") or 0.0), 1.0, places=9)
            self.assertEqual(float(taker_comp.get("outside_window_blocked_count_edge_eval") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("risk_reject_after_capable_count_edge_eval") or 0.0), 1.0)
            edge_eval_submit_reject = taker_comp.get("edge_eval_submit_reject_reason_distribution", {})
            self.assertEqual(int(edge_eval_submit_reject.get("risk_reject_notional_cap", 0)), 1)
            decision_blocks = taker_comp.get("decision_block_reason_distribution", {})
            self.assertEqual(int(decision_blocks.get("taker_outside_final_window", 0)), 1)
            decision_windows = taker_comp.get("decision_timing_window_distribution", {})
            self.assertEqual(int(decision_windows.get("outside_window", 0)), 1)
            self.assertEqual(int(decision_windows.get("final15", 0)), 1)
            submit_buckets = taker_comp.get("submit_edge_bucket_distribution", {})
            self.assertEqual(int(submit_buckets.get("0p10_0p30", 0)), 1)
            fill_buckets = taker_comp.get("fill_edge_bucket_distribution", {})
            self.assertEqual(int(fill_buckets.get("0p10_0p30", 0)), 1)
            fill_policy = taker_comp.get("fill_policy_basis_distribution", {})
            self.assertEqual(int(fill_policy.get("visible_liquidity_top_of_book", 0)), 1)
            fill_realism = taker_comp.get("fill_execution_realism_class_distribution", {})
            self.assertEqual(int(fill_realism.get("not_modeled", 0)), 1)
            fill_queue_mode = taker_comp.get("fill_queue_position_mode_distribution", {})
            self.assertEqual(int(fill_queue_mode.get("not_applicable", 0)), 1)
            lag_distribution = taker_comp.get("lag_class_distribution", {})
            self.assertEqual(int(lag_distribution.get("within_window", 0)), 1)
            self.assertEqual(float(taker_comp.get("dynamic_size_capped_by_risk_count_decision") or 0.0), 1.0)
            decision_oracle_status = taker_comp.get("decision_multi_oracle_status_distribution", {})
            self.assertEqual(int(decision_oracle_status.get("unknown", 0)), 1)
            self.assertEqual(int(decision_oracle_status.get("confirmed", 0)), 1)
            submit_oracle_status = taker_comp.get("submit_multi_oracle_status_distribution", {})
            self.assertEqual(int(submit_oracle_status.get("confirmed", 0)), 1)
            latency_summary = taker_comp.get("decision_to_submit_latency_ms_summary", {})
            self.assertAlmostEqual(float(latency_summary.get("sample_count") or 0.0), 1.0, places=9)
            self.assertAlmostEqual(float(latency_summary.get("median_ms") or 0.0), 187.5, places=9)
            normal_latency_summary = taker_comp.get("normal_competitiveness_decision_to_submit_latency_ms_summary", {})
            self.assertAlmostEqual(float(normal_latency_summary.get("sample_count") or 0.0), 1.0, places=9)
            self.assertAlmostEqual(float(normal_latency_summary.get("p90_ms") or 0.0), 187.5, places=9)
            self.assertEqual(float(taker_comp.get("multi_oracle_available_count_decision") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("multi_oracle_confirmation_count_decision") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("multi_oracle_boost_eligible_count_decision") or 0.0), 0.0)
            self.assertEqual(float(taker_comp.get("multi_oracle_boost_applied_count_decision") or 0.0), 1.0)
            aggressiveness = taker_comp.get("aggressiveness_application_counts", {})
            self.assertEqual(int(aggressiveness.get("price_aggressed", 0)), 1)
            self.assertEqual(int(aggressiveness.get("hard_min_floor_applied", 0)), 1)
            submit_stage = taker_comp.get("submit_lineage_stage_distribution", {})
            self.assertEqual(int(submit_stage.get("SNIPER_PRIMARY", 0)), 1)
            fill_stage = taker_comp.get("fill_lineage_stage_distribution", {})
            self.assertEqual(int(fill_stage.get("SNIPER_PRIMARY", 0)), 1)
            self.assertEqual(float(taker_comp.get("submit_unknown_lineage_stage_count") or 0.0), 0.0)
            self.assertEqual(float(taker_comp.get("fill_without_submit_lineage_stage_count") or 0.0), 0.0)
            stage_funnel = taker_comp.get("lineage_stage_funnel_metrics", {})
            sniper_stage = stage_funnel.get("SNIPER_PRIMARY", {})
            self.assertEqual(float(sniper_stage.get("decision_count") or 0.0), 2.0)
            self.assertEqual(float(sniper_stage.get("submit_capable_static_count") or 0.0), 1.0)
            self.assertEqual(float(sniper_stage.get("submit_capable_dynamic_predicted_count") or 0.0), 0.0)
            self.assertEqual(float(sniper_stage.get("actual_submit_count") or 0.0), 1.0)
            self.assertEqual(float(sniper_stage.get("fill_count") or 0.0), 1.0)
            stage_reduction = taker_comp.get("lineage_stage_reduction_cause_counters", {})
            sniper_reduction = stage_reduction.get("SNIPER_PRIMARY", {})
            self.assertEqual(int(sniper_reduction.get("reduction_due_to_timing_gate", 0)), 1)
            self.assertEqual(int(sniper_reduction.get("reduction_due_to_final_risk_reject", 0)), 1)
            hidden = taker_comp.get("lineage_stage_hidden_blockage_detector", {})
            sniper_hidden = hidden.get("SNIPER_PRIMARY", {})
            self.assertEqual(float(sniper_hidden.get("decision_to_dynamic_predicted_delta") or 0.0), 2.0)
            self.assertEqual(float(sniper_hidden.get("dynamic_predicted_to_submit_delta") or 0.0), 0.0)
            self.assertEqual(float(sniper_hidden.get("submit_to_fill_delta") or 0.0), 0.0)
            overall_hidden = taker_comp.get("hidden_blockage_detector", {})
            self.assertEqual(float(overall_hidden.get("decision_to_dynamic_predicted_delta") or 0.0), 2.0)
            stage_rejects = taker_comp.get("lineage_stage_final_risk_reject_reason_distribution", {})
            self.assertEqual(
                int(((stage_rejects.get("SNIPER_PRIMARY") or {}).get("risk_reject_notional_cap") or 0)),
                1,
            )

            pyth_stats = report.get("secondary_oracle_pyth", {})
            self.assertEqual(float(pyth_stats.get("sample_count") or 0.0), 1.0)
            self.assertEqual(float(pyth_stats.get("enabled_sample_count") or 0.0), 1.0)
            self.assertEqual(float(pyth_stats.get("connected_sample_count") or 0.0), 0.0)
            self.assertEqual(
                int((pyth_stats.get("operational_state_distribution") or {}).get("unavailable_http_403", 0)),
                1,
            )
            self.assertEqual(int(((pyth_stats.get("latest") or {}).get("last_http_status") or 0)), 403)

    def test_build_report_emits_normal_taker_side_policy_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-side-policy",
                    "token_id": "t-neg",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": False,
                    "timing_window_class": "final15",
                    "edge_abs": 0.24,
                    "conviction_score": 0.82,
                    "block_reason": "normal_taker_same_token_sell_forbidden",
                    "aggressiveness_level": "final15",
                    "normal_side_policy": "buy_expected_winner_only",
                    "normal_taker_side_class": "same_token_sell_blocked",
                },
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-side-policy",
                    "token_id": "t-pos",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": True,
                    "timing_window_class": "final15",
                    "edge_abs": 0.24,
                    "conviction_score": 0.82,
                    "block_reason": None,
                    "aggressiveness_level": "final15",
                    "normal_side_policy": "buy_expected_winner_only",
                    "normal_taker_side_class": "buy_expected_winner",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-taker-side-policy",
                    "order_id": "ord-buy",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.24,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "normal_side_policy": "buy_expected_winner_only",
                        "normal_taker_side_class": "buy_expected_winner",
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-taker-side-policy",
                    "order_id": "ord-buy",
                    "token_id": "t-pos",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-taker-side-policy", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-taker-side-policy")
            taker_comp = report.get("taker_competitiveness", {})
            decision_classes = taker_comp.get("decision_normal_taker_side_class_distribution", {})
            self.assertEqual(int(decision_classes.get("same_token_sell_blocked", 0)), 1)
            self.assertEqual(int(decision_classes.get("buy_expected_winner", 0)), 1)
            self.assertEqual(
                float(taker_comp.get("normal_taker_same_token_sell_blocked_count_decision") or 0.0),
                1.0,
            )
            submit_classes = taker_comp.get("submit_normal_taker_side_class_distribution", {})
            self.assertEqual(int(submit_classes.get("buy_expected_winner", 0)), 1)
            submit_policy = taker_comp.get("submit_normal_side_policy_distribution", {})
            self.assertEqual(int(submit_policy.get("buy_expected_winner_only", 0)), 1)

    def test_build_report_prefers_top_level_taker_submit_lineage_and_side_class(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "order_submit",
                    "run_id": "rid-top-level-submit-truth",
                    "order_id": "ord-top-level",
                    "reason": "taker_chainlink",
                    "submission_lane": "taker",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "normal_taker_side_class": "buy_expected_winner",
                    "taker_competitiveness": {
                        "lineage_stage": "EXTREME_ONLY",
                        "edge_abs": 0.24,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "normal_taker_side_class": "same_token_sell_blocked",
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-top-level-submit-truth",
                    "order_id": "ord-top-level",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.50,
                    "size": 10,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-top-level-submit-truth", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-top-level-submit-truth")
            taker_comp = report.get("taker_competitiveness", {})
            submit_stage = taker_comp.get("submit_lineage_stage_distribution", {})
            self.assertEqual(int(submit_stage.get("SNIPER_PRIMARY", 0)), 1)
            self.assertEqual(int(submit_stage.get("EXTREME_ONLY", 0)), 0)
            submit_classes = taker_comp.get("submit_normal_taker_side_class_distribution", {})
            self.assertEqual(int(submit_classes.get("buy_expected_winner", 0)), 1)
            self.assertEqual(int(submit_classes.get("same_token_sell_blocked", 0)), 0)

    def test_build_report_classifies_canonical_taker_submit_without_competitiveness_payload_as_normal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "order_submit",
                    "run_id": "rid-canonical-submit-without-comp-payload",
                    "order_id": "ord-canonical",
                    "reason": "taker_chainlink",
                    "submission_lane": "taker",
                    "lifecycle_phase": "taker_window",
                    "lineage_stage": "TAKER_COMMITMENT",
                    "normal_taker_side_class": "buy_expected_winner",
                    "target_usd_requested": 5.0,
                    "target_usd_resolved": 5.0,
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-canonical-submit-without-comp-payload",
                    "order_id": "ord-canonical",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.25,
                    "size": 20.0,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-canonical-submit-without-comp-payload", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-canonical-submit-without-comp-payload")
            taker_comp = report.get("taker_competitiveness", {})
            submit_class_distribution = taker_comp.get("submit_class_distribution", {})
            self.assertEqual(int(submit_class_distribution.get("normal_competitiveness", 0)), 1)
            self.assertEqual(int(submit_class_distribution.get("true_unknown", 0)), 0)
            fill_class_distribution = taker_comp.get("fill_class_distribution", {})
            self.assertEqual(int(fill_class_distribution.get("normal_competitiveness", 0)), 1)
            self.assertEqual(int(fill_class_distribution.get("true_unknown", 0)), 0)

    def test_build_report_emits_taker_opportunity_suppression_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "normal_taker_authority_closed",
                    "lineage_stage": "OBSERVE",
                    "book_source": "ws",
                    "fair_probability": 0.79,
                    "market_probability": 0.70,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "edge_below_min",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "book_source": "ws",
                    "fair_probability": 0.55,
                    "market_probability": 0.50,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "market_probability_missing",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "book_source": "ws",
                    "fair_probability": 0.55,
                    "market_probability": None,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "taker_submit_rejected",
                    "taker_submit_reject_reason": "risk_reject_notional_cap",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "book_source": "ws",
                    "fair_probability": 0.90,
                    "market_probability": 0.30,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "taker",
                    "block_reason": "",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "book_source": "ws",
                    "edge_abs": 0.40,
                    **_historical_taker_authority_flags(
                        taker_phase_allowed=True,
                    ),
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": _HISTORICAL_RECOVERY_WAITING_BLOCK_REASON,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "book_source": "ws",
                    "fair_probability": 0.95,
                    "market_probability": 0.50,
                    "settlement_hold_required": True,
                    "unresolved_lifecycle_obligation": True,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-suppression",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": _HISTORICAL_RECOVERY_SIZE_CAP_BELOW_MIN_ORDER_SIZE,
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "book_source": "ws",
                    "fair_probability": 0.99,
                    "market_probability": 0.80,
                    "settlement_hold_required": True,
                    "unresolved_lifecycle_obligation": True,
                    **_historical_taker_authority_flags(),
                    **_historical_recovery_lineage(),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-taker-suppression", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-taker-suppression")
            suppression = report.get("taker_opportunity_suppression", {})
            self.assertEqual(float(suppression.get("total_taker_edge_eval_count") or 0.0), 7.0)
            normal = suppression.get("normal", {})
            recovery = suppression.get("lifecycle_residue", {})
            self.assertEqual(float(normal.get("edge_eval_count") or 0.0), 5.0)
            self.assertEqual(float(normal.get("taker_enabled_phase_eval_count") or 0.0), 4.0)
            self.assertEqual(float(normal.get("submit_candidate_count") or 0.0), 2.0)
            self.assertEqual(float(normal.get("action_taken_taker_count") or 0.0), 1.0)
            self.assertAlmostEqual(float(normal.get("submit_candidate_to_action_rate") or 0.0), 0.5, places=9)
            normal_classes = normal.get("suppression_class_distribution", {})
            self.assertEqual(int(normal_classes.get("late_window_authority_gate", 0)), 1)
            self.assertEqual(int(normal_classes.get("edge_filter", 0)), 1)
            self.assertEqual(int(normal_classes.get("truth_missing", 0)), 1)
            self.assertEqual(int(normal_classes.get("risk_size_or_exposure", 0)), 1)
            self.assertEqual(int(normal_classes.get("submitted", 0)), 1)
            taker_phase_allowed_distribution = normal.get("taker_phase_allowed_distribution", {})
            self.assertEqual(int(taker_phase_allowed_distribution.get("false", 0)), 4)
            self.assertEqual(int(taker_phase_allowed_distribution.get("true", 0)), 1)
            normal_edges = normal.get("edge_bucket_distribution", {})
            self.assertEqual(int(normal_edges.get("le_0p10", 0)), 2)
            self.assertEqual(int(normal_edges.get("0p30_0p60", 0)), 1)
            self.assertEqual(int(normal_edges.get("gt_0p60", 0)), 1)
            self.assertEqual(int(normal_edges.get("unknown", 0)), 1)
            self.assertEqual(float(recovery.get("edge_eval_count") or 0.0), 2.0)
            self.assertEqual(float(recovery.get("submit_candidate_count") or 0.0), 0.0)
            recovery_classes = recovery.get("suppression_class_distribution", {})
            self.assertEqual(int(recovery_classes.get("lifecycle_residue_policy", 0)), 2)
            self.assertEqual(int(recovery_classes.get("risk_size_or_exposure", 0)), 0)
            recovery_settlement_hold = recovery.get("settlement_hold_required_distribution", {})
            self.assertEqual(int(recovery_settlement_hold.get("true", 0)), 2)
            recovery_unresolved = recovery.get("unresolved_lifecycle_obligation_distribution", {})
            self.assertEqual(int(recovery_unresolved.get("true", 0)), 2)
            historical_recovery_lineage = recovery.get("historical_recovery_authority_lineage_distribution", {})
            self.assertEqual(int(historical_recovery_lineage.get("true", 0)), 0)
            recovery_rejects = recovery.get("submit_reject_reason_distribution", {})
            self.assertEqual(int(recovery_rejects.get("risk_reject_size_too_small", 0)), 0)
            summary = render_human_summary(report)
            self.assertIn("taker_opportunity_suppression=", summary)
            self.assertIn("normal_submit_candidates=2", summary)
            self.assertIn("lifecycle_residue_submit_candidates=0", summary)

    def test_build_report_classifies_current_lifecycle_block_reasons_as_residue_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-current-lifecycle-reasons",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "settlement_hold_required",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "book_source": "ws",
                    "fair_probability": 0.95,
                    "market_probability": 0.50,
                    "settlement_hold_required": True,
                    "unresolved_lifecycle_obligation": True,
                    **_historical_taker_authority_flags(),
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-current-lifecycle-reasons",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "open_order_cleanup_required",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "book_source": "ws",
                    "fair_probability": 0.90,
                    "market_probability": 0.55,
                    "open_order_cleanup_required": True,
                    "cancel_fail_closed": True,
                    **_historical_taker_authority_flags(),
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-current-lifecycle-reasons", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-current-lifecycle-reasons")
            suppression = report.get("taker_opportunity_suppression", {})
            residue = suppression.get("lifecycle_residue", {})
            residue_classes = residue.get("suppression_class_distribution", {})
            self.assertEqual(int(residue_classes.get("lifecycle_residue_policy", 0)), 2)
            self.assertEqual(int(residue_classes.get("other", 0)), 0)
            self.assertEqual(int(residue.get("settlement_hold_required_distribution", {}).get("true", 0)), 1)
            self.assertEqual(int(residue.get("open_order_cleanup_required_distribution", {}).get("true", 0)), 1)
            self.assertEqual(int(residue.get("cancel_fail_closed_distribution", {}).get("true", 0)), 1)

    def test_build_report_normalizes_legacy_complement_route_metrics_to_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            retired_route_key = "complement" + "_route_applied"
            retired_side_class = "complement" + "_buy"
            retired_mapping_reason = "complement" + "_token_mapping_unavailable"
            retired_mapping_class = "complement" + "_mapping_unavailable"
            retired_comp_token_key = "normal_taker_" + "complement_token_id"
            retired_comp_route_key = "normal_taker_" + "complement" + "_route_applied"
            events = [
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-complement",
                    "token_id": "t-no",
                    "source_token_id": "t-yes",
                    "submit_token_id": "t-no",
                    "complement_token_id": "t-no",
                    retired_route_key: True,
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": True,
                    "timing_window_class": "final15",
                    "edge_abs": 0.15,
                    "conviction_score": 0.82,
                    "block_reason": None,
                    "aggressiveness_level": "final15",
                    "normal_side_policy": "buy_expected_winner_only",
                    "normal_taker_side_class": retired_side_class,
                },
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-complement",
                    "token_id": "t-missing",
                    "source_token_id": "t-missing",
                    "submit_token_id": "t-missing",
                    retired_route_key: False,
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": False,
                    "timing_window_class": "final15",
                    "edge_abs": 0.24,
                    "conviction_score": 0.82,
                    "block_reason": retired_mapping_reason,
                    "aggressiveness_level": "final15",
                    "normal_side_policy": "buy_expected_winner_only",
                    "normal_taker_side_class": retired_mapping_class,
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-taker-complement",
                    "order_id": "ord-complement",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.15,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "normal_side_policy": "buy_expected_winner_only",
                        "normal_taker_side_class": retired_side_class,
                        "normal_taker_source_token_id": "t-yes",
                        "normal_taker_submit_token_id": "t-no",
                        retired_comp_token_key: "t-no",
                        retired_comp_route_key: True,
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-taker-complement",
                    "order_id": "ord-complement",
                    "token_id": "t-no",
                    "side": "BUY",
                    "price": 0.46,
                    "size": 10,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-taker-complement", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-taker-complement")
            taker_comp = report.get("taker_competitiveness", {})
            decision_classes = taker_comp.get("decision_normal_taker_side_class_distribution", {})
            self.assertEqual(int(decision_classes.get("unknown", 0)), 2)
            self.assertNotIn(retired_side_class, decision_classes)
            self.assertNotIn(retired_mapping_class, decision_classes)
            self.assertNotIn("complement" + "_token_mapping_failure_count_decision", taker_comp)
            submit_classes = taker_comp.get("submit_normal_taker_side_class_distribution", {})
            self.assertEqual(int(submit_classes.get("unknown", 0)), 1)
            self.assertNotIn(retired_side_class, submit_classes)
            submit_policy = taker_comp.get("submit_normal_side_policy_distribution", {})
            self.assertEqual(int(submit_policy.get("buy_expected_winner_only", 0)), 1)

    def test_taker_competitiveness_classifies_recovery_override_submits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-taker-recovery-taxonomy",
                    "token_id": "normal-token",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "should_submit": True,
                    "timing_window_class": "final15",
                    "edge_abs": 0.24,
                    "conviction_score": 0.82,
                    "block_reason": None,
                    "aggressiveness_level": "final15",
                    "submit_capable_static": True,
                    "submit_capable_dynamic_predicted": True,
                    "multi_oracle_status": "confirmed",
                    "multi_oracle_confirmation": True,
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-taker-recovery-taxonomy",
                    "order_id": "normal-submit",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.24,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "multi_oracle_status": "confirmed",
                        "multi_oracle_confirmation": True,
                        **_historical_recovery_lineage(active=False),
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-taker-recovery-taxonomy",
                    "order_id": "normal-submit",
                    "token_id": "normal-token",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10,
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-taker-recovery-taxonomy",
                    "order_id": "recovery-submit",
                    "reason": "taker_chainlink",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "edge_abs": 0.74,
                        "reduce_only_side": "SELL",
                        **_historical_recovery_lineage(),
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-taker-recovery-taxonomy",
                    "order_id": "recovery-submit",
                    "token_id": "recovery-token",
                    "side": "SELL",
                    "price": 0.4,
                    "size": 10,
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-taker-recovery-taxonomy", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-taker-recovery-taxonomy")
            taker_comp = report.get("taker_competitiveness", {})
            self.assertEqual(float(taker_comp.get("actual_submit_count") or 0.0), 2.0)
            self.assertEqual(float(taker_comp.get("normal_competitiveness_submit_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("lifecycle_residue_override_submit_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("true_unknown_submit_count") or 0.0), 0.0)
            self.assertEqual(
                float(taker_comp.get("lifecycle_residue_override_without_normal_payload_count") or 0.0),
                1.0,
            )
            submit_classes = taker_comp.get("submit_class_distribution", {})
            self.assertEqual(int(submit_classes.get("normal_competitiveness", 0)), 1)
            self.assertEqual(int(submit_classes.get("lifecycle_residue_override", 0)), 1)
            self.assertEqual(int(submit_classes.get("true_unknown", 0)), 0)
            self.assertEqual(
                int((taker_comp.get("submit_conviction_bucket_distribution") or {}).get("gt_0p66", 0)),
                1,
            )
            self.assertNotIn("unknown", taker_comp.get("submit_conviction_bucket_distribution") or {})
            self.assertEqual(
                int((taker_comp.get("submit_timing_window_distribution") or {}).get("final15", 0)),
                1,
            )
            self.assertNotIn("unknown", taker_comp.get("submit_timing_window_distribution") or {})
            self.assertEqual(
                int((taker_comp.get("submit_multi_oracle_status_distribution") or {}).get("confirmed", 0)),
                1,
            )
            self.assertNotIn("unknown", taker_comp.get("submit_multi_oracle_status_distribution") or {})
            recovery_edges = taker_comp.get("submit_lifecycle_residue_override_edge_bucket_distribution", {})
            self.assertEqual(int(recovery_edges.get("gt_0p60", 0)), 1)
            recovery_reasons = taker_comp.get("submit_lifecycle_residue_override_reason_distribution", {})
            self.assertEqual(int(recovery_reasons.get("preexpiry_reduce_only_window_active", 0)), 1)
            fill_classes = taker_comp.get("fill_class_distribution", {})
            self.assertEqual(int(fill_classes.get("normal_competitiveness", 0)), 1)
            self.assertEqual(int(fill_classes.get("lifecycle_residue_override", 0)), 1)
            self.assertAlmostEqual(
                float(taker_comp.get("normal_competitiveness_decision_to_submit_rate") or 0.0),
                1.0,
                places=9,
            )

    def test_taker_intent_gate_posture_matrix_splits_normal_and_recovery_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_window_semantic_check",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "ts_utc": "2026-01-01T00:00:00Z",
                    "final_window_enabled": True,
                    "default_final_window_sec": 60.0,
                    "semantic_dead_by_construction_count": 0,
                    "semantic_status": "ok",
                    "phase_rows": {
                        "MAKER_TAKER_SELECTIVE": {
                            "effective_final_window_sec": 60.0,
                            "semantically_live": True,
                        },
                        "SNIPER_PRIMARY": {
                            "effective_final_window_sec": 30.0,
                            "semantically_live": True,
                        },
                    },
                },
                {
                    "event_type": "taker_decision",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "edge_abs": 0.35,
                    "required_min_edge": 0.30,
                    "timing_window_class": "final_window",
                    "conviction_score": 0.80,
                    "submit_capable_static": True,
                    "submit_capable_dynamic_predicted": True,
                    "should_submit": True,
                },
                {
                    "event_type": "taker_submit",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "edge_abs": 0.35,
                    "required_min_edge": 0.30,
                    "timing_window_class": "final_window",
                    "conviction_score": 0.80,
                    **_historical_recovery_lineage(active=False),
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "order_id": "normal",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.35,
                        "required_min_edge": 0.30,
                        "timing_window_class": "final_window",
                        "conviction_score": 0.80,
                        "submit_capable_static": True,
                        "submit_capable_dynamic_predicted": True,
                        **_historical_recovery_lineage(active=False),
                    },
                    "risk_decision_basis": {
                        "min_sec_to_expiry_for_new_exposure": 45.0,
                    },
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "order_id": "normal",
                    "token_id": "token-normal",
                    "side": "BUY",
                    "price": 0.51,
                    "size": 10,
                },
                {
                    "event_type": "taker_submit",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "edge_abs": 0.10,
                    "required_min_edge": 0.20,
                    **_historical_recovery_lineage(),
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-gate-posture",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-1",
                    "order_id": "recovery",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "edge_abs": 0.10,
                        **_historical_recovery_lineage(),
                    },
                    "risk_decision_basis": {
                        "min_sec_to_expiry_for_new_exposure": 45.0,
                        **_historical_recovery_lineage(),
                    },
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-gate-posture", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-gate-posture")
            matrix = report.get("taker_intent_gate_posture_matrix", {})
            self.assertEqual(
                matrix.get("observed_intent_classification"),
                "mixed_normal_and_lifecycle_residue_taker_activity_observed",
            )
            self.assertEqual(
                int((matrix.get("event_class_distribution") or {}).get("normal_competitiveness", 0)),
                4,
            )
            self.assertEqual(
                int((matrix.get("event_class_distribution") or {}).get("lifecycle_residue_override", 0)),
                2,
            )
            self.assertEqual(
                int((matrix.get("event_class_distribution") or {}).get("true_unknown", 0)),
                0,
            )
            self.assertEqual(float(matrix.get("normal_below_required_min_edge_count") or 0.0), 0.0)
            self.assertEqual(float(matrix.get("lifecycle_residue_override_below_required_min_edge_count") or 0.0), 1.0)
            self.assertEqual(matrix.get("lifecycle_residue_override_crossed_normal_edge_gate_observed"), True)
            latest_semantics = matrix.get("latest_stage_window_semantics") or {}
            phase_windows = latest_semantics.get("phase_rows", {})
            stage_windows = latest_semantics.get("stage_rows", {})
            self.assertEqual(phase_windows, stage_windows)
            self.assertEqual(
                float((phase_windows.get("MAKER_TAKER_SELECTIVE") or {}).get("effective_final_window_sec") or 0.0),
                60.0,
            )
            required_min_edge = matrix.get("required_min_edge_by_intent_lineage_stage", {})
            normal_sniper = (required_min_edge.get("normal_competitiveness") or {}).get("SNIPER_PRIMARY", {})
            recovery_mts = (required_min_edge.get("lifecycle_residue_override") or {}).get("MAKER_TAKER_SELECTIVE", {})
            self.assertEqual(float(normal_sniper.get("min") or 0.0), 0.30)
            self.assertEqual(float(recovery_mts.get("min") or 0.0), 0.20)
            summary = render_human_summary(report)
            self.assertIn("taker_intent_gate_posture=", summary)
            self.assertIn("lifecycle_residue_below_required_min_edge=1", summary)

    def test_taker_intent_gate_posture_matrix_detects_gate_only_normal_activity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-gate-only-normal",
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-gate-only",
                    "evaluation_scope": "taker",
                    "lineage_stage": "EXTREME_ONLY",
                    "effective_stage": "EXTREME_ONLY",
                    "stage_bucket": "EXTREME_ONLY",
                    "raw_stage": "EXTREME_ONLY",
                    "action_taken": "none",
                    "block_reason": "edge_below_min",
                    "book_source": "ws",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "edge_value": 0.10,
                    "required_min_edge": 0.11,
                    **_historical_recovery_lineage(active=False),
                }
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-gate-only-normal", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-gate-only-normal")
            matrix = report.get("taker_intent_gate_posture_matrix", {})
            self.assertEqual(
                matrix.get("observed_intent_classification"),
                "normal_taker_gate_activity_observed_no_submit",
            )
            self.assertEqual(
                int((matrix.get("event_class_distribution") or {}).get("normal_competitiveness", 0)),
                1,
            )
            self.assertEqual(matrix.get("submit_event_distribution"), {})
            self.assertEqual(float(matrix.get("normal_below_required_min_edge_count") or 0.0), 1.0)
            required_min_edge = matrix.get("required_min_edge_by_intent_lineage_stage", {})
            normal_extreme = (
                (required_min_edge.get("normal_competitiveness") or {}).get("LINEAGE_ONLY_0_TO_20S", {})
            )
            self.assertEqual(float(normal_extreme.get("min") or 0.0), 0.11)
            self.assertEqual(float(normal_extreme.get("max") or 0.0), 0.11)
            summary = render_human_summary(report)
            self.assertIn("taker_intent_gate_posture=", summary)
            self.assertIn("normal_taker_gate_activity_observed_no_submit", summary)

    def test_taker_intent_gate_posture_matrix_keeps_shutdown_semantic_check_under_tail_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-stage-window-tail"
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "taker_window_semantic_check",
                    "run_id": run_id,
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-tail",
                    "ts_utc": "2026-01-01T00:00:00Z",
                    "final_window_enabled": True,
                    "default_final_window_sec": 60.0,
                    "semantic_dead_by_construction_count": 0,
                    "semantic_status": "ok",
                    "phase_rows": {
                        "EXTREME_ONLY": {
                            "effective_final_window_sec": 60.0,
                            "semantically_live": True,
                        },
                    },
                },
            ]
            for idx in range(20):
                events.append(
                    {
                        "event_type": "edge_evaluation",
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "config_fingerprint_sha256": "cfg-tail",
                        "evaluation_scope": "taker",
                        "lineage_stage": "OBSERVE",
                        "effective_stage": "OBSERVE",
                        "stage_bucket": "OBSERVE",
                        "raw_stage": "OBSERVE",
                        "action_taken": "none",
                        "block_reason": "phase_disallow_taker",
                        "edge_value": 0.01,
                        "required_min_edge": 0.015,
                    }
                )
            events.append(
                {
                    "event_type": "taker_window_semantic_check",
                    "run_id": run_id,
                    "profile_name": "paper_universal",
                    "config_fingerprint_sha256": "cfg-tail",
                    "ts_utc": "2026-01-01T00:09:59Z",
                    "final_window_enabled": True,
                    "default_final_window_sec": 7.0,
                    "semantic_dead_by_construction_count": 0,
                    "semantic_status": "ok",
                    "phase_rows": {
                        "EXTREME_ONLY": {
                            "effective_final_window_sec": 7.0,
                            "semantically_live": True,
                        },
                    },
                }
            )
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id, max_lines_per_file=5)
            matrix = report.get("taker_intent_gate_posture_matrix", {})
            self.assertEqual(float(matrix.get("stage_window_semantic_check_count") or 0.0), 1.0)
            latest_semantics = matrix.get("latest_stage_window_semantics") or {}
            stage_rows = latest_semantics.get("stage_rows", {})
            phase_rows = latest_semantics.get("phase_rows", {})
            self.assertEqual(stage_rows, phase_rows)
            self.assertAlmostEqual(
                float((stage_rows.get("EXTREME_ONLY") or {}).get("effective_final_window_sec") or 0.0),
                7.0,
                places=9,
            )

    def test_build_report_emits_taker_doctrine_breach_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-taker-breaches"
            events = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": run_id,
                    "evaluation_scope": "taker",
                    "lineage_stage": "EXTREME_ONLY",
                    "action_taken": "none",
                    "block_reason": "normal_taker_authority_closed",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": run_id,
                    "evaluation_scope": "taker",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "action_taken": "none",
                    "block_reason": _HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED,
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": run_id,
                    "evaluation_scope": "taker",
                    "lineage_stage": "EXTREME_ONLY",
                    "action_taken": "none",
                    "block_reason": "taker_recovery_disabled_in_taker_scope",
                },
                {
                    "event_type": "order_submit",
                    "run_id": run_id,
                    "order_id": "ord-hard-window",
                    "submission_lane": "taker",
                    "reason": "taker_chainlink",
                    "lineage_stage": "EXTREME_ONLY",
                    "taker_competitiveness": {
                        "lineage_stage": "EXTREME_ONLY",
                        "conviction_score": 0.9,
                        "timing_window_class": "final_window",
                        "multi_oracle_status": "confirmed",
                        "submit_capable_static": True,
                        "submit_capable_dynamic_predicted": True,
                        "sec_to_expiry": 9.0,
                    },
                },
            ]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root, run_id=run_id)
            breaches = report.get("taker_doctrine_breaches", {})
            self.assertEqual(float(breaches.get("hard_window_submit_violation_count") or 0.0), 1.0)
            self.assertEqual(
                float(breaches.get("historical_recovery_handoff_disabled_count") or 0.0),
                1.0,
            )
            self.assertEqual(
                float(breaches.get("historical_recovery_disabled_in_taker_scope_count") or 0.0),
                1.0,
            )
            self.assertEqual(
                int(
                    (breaches.get("block_reason_distribution") or {}).get(
                        "normal_taker_authority_closed",
                        0,
                    )
                ),
                1,
            )
            self.assertEqual(
                int(
                    (breaches.get("block_reason_distribution") or {}).get(
                        _HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED,
                        0,
                    )
                ),
                1,
            )
            summary = render_human_summary(report)
            self.assertIn("taker_doctrine_breaches=", summary)
            self.assertIn("hard_window_submit_violations=1", summary)
            self.assertIn("historical_recovery_handoff_disabled=1", summary)

    def test_taker_config_gate_posture_uses_run_manifest_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-config-posture"
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "git_commit": "abc123",
                        "config_fingerprint_sha256": "cfg-posture",
                        "config": {
                            "runtime": _historical_recovery_runtime_config(
                                held_preexpiry_reduce_only_sec=90.0,
                            ),
                            "risk": {
                                "min_sec_to_expiry_for_new_exposure": 45.0,
                                "dynamic_scaling": {
                                    "enabled": True,
                                    "tod_enabled": True,
                                    "tod_start_hour_utc": 2,
                                    "tod_end_hour_utc": 6,
                                    "tod_thin_liquidity_mult": 0.9,
                                    "edge_start_abs": 0.10,
                                    "edge_full_abs": 0.30,
                                    "edge_mult_max": 1.15,
                                    "unknown_input_policy": "no_aggressive_uplift",
                                },
                            },
                            "latency_verifier": {
                                "enabled": True,
                                "hit_threshold_ms": 40,
                            },
                            "taker": {
                                "max_chainlink_tick_age_sec": 1.5,
                                "min_edge": 0.015,
                                "min_edge_by_stage": {
                                    "MAKER_TAKER_SELECTIVE": 0.20,
                                    "SNIPER_PRIMARY": 0.30,
                                },
                                "max_orders_per_cycle": 2,
                                "per_token_cooldown_sec": 0.25,
                                "competitiveness": {
                                    "hard_min_target_usd": 100.0,
                                    "hard_min_enforcement": "skip_if_unachievable",
                                    "dynamic_size_enabled": True,
                                    "dynamic_size_edge_start_abs": 0.16,
                                    "dynamic_size_edge_full_abs": 0.35,
                                    "dynamic_size_target_usd_cap": 220.0,
                                    "final_window_enabled": True,
                                    "final_window_sec": 60.0,
                                    "aggressive_window_enabled": False,
                                    "price_aggress_bps_max": 8.0,
                                    "multi_oracle_boost_enabled": True,
                                    "multi_oracle_boost_window_sec": 30.0,
                                    "multi_oracle_edge_threshold_abs": 0.20,
                                    "multi_oracle_target_usd_cap": 350.0,
                                    "multi_oracle_capital_pct_cap": 0.18,
                                },
                            },
                            "strategy": {
                                "maker_market_viability": {
                                    "timing_gate_enabled": True,
                                    "timing_gate_min_sec_to_expiry": 45.0,
                                    "timing_gate_max_sec_to_expiry": 60.0,
                                    "one_sided_enabled": True,
                                    "one_sided_edge_threshold_abs": 0.15,
                                },
                                "execution_quality": {
                                    "min_expected_fill_prob": 0.045,
                                    "max_queue_ahead_size": 300.0,
                                    "reduce_only_recovery_min_expected_fill_prob_floor": 0.02,
                                    "reduce_only_recovery_max_queue_ahead_size_multiplier": 2.0,
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            posture = report.get("taker_config_gate_posture", {})
            self.assertEqual(posture.get("config_present"), True)
            self.assertEqual(
                posture.get("boundary_class"),
                "normal_new_exposure_allowed_inside_held_recovery_window",
            )
            boundary = posture.get("boundary_alignment", {})
            self.assertEqual(boundary.get("normal_can_open_inside_held_recovery_window"), True)
            self.assertNotIn("taker_token_readiness", posture)
            self.assertEqual((posture.get("latency_verifier") or {}).get("hit_threshold_ms"), 40.0)
            gates = posture.get("normal_taker_entry_gates") or {}
            self.assertNotIn("normal_allowed_final_window_by_stage", gates)
            self.assertNotIn("stage_final_window_sec_by_stage", gates)
            self.assertNotIn("aggressive_window_enabled", gates)
            flags = set(posture.get("posture_flags") or [])
            self.assertIn("normal_entry_recovery_boundary_overlap", flags)
            self.assertIn("taker_default_final_window_ge_60s", flags)
            summary = render_human_summary(report)
            self.assertIn("taker_config_gate_posture=", summary)
            self.assertIn("boundary_class=normal_new_exposure_allowed_inside_held_recovery_window", summary)
            self.assertEqual(
                (boundary.get("normal_can_open_inside_taker_final_window")),
                True,
            )
            self.assertIn("normal_entry_taker_final_window_overlap", flags)
            self.assertIn("normal_can_open_inside_final_window=1", summary)
            self.assertIn("final_window_overlap_max_sec=15.00", summary)

    def test_taker_config_gate_posture_flags_final_window_entry_band(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-config-final-window-band"
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "git_commit": "abc123",
                        "config_fingerprint_sha256": "cfg-posture",
                        "config": {
                            "runtime": _historical_recovery_runtime_config(
                                held_preexpiry_reduce_only_sec=45.0,
                            ),
                            "risk": {
                                "min_sec_to_expiry_for_new_exposure": 45.0,
                            },
                            "taker": {
                                "competitiveness": {
                                    "final_window_sec": 60.0,
                                    "final_window_floor_sec": 4.0,
                                    "min_visible_fill_ratio": 1.5,
                                    "min_submit_price": 0.05,
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            posture = report.get("taker_config_gate_posture", {})
            self.assertEqual(
                posture.get("boundary_class"),
                "normal_new_exposure_allowed_inside_taker_final_window",
            )
            boundary = posture.get("boundary_alignment", {})
            self.assertEqual(boundary.get("aligned_terminal_boundary"), True)
            self.assertEqual(boundary.get("normal_can_open_inside_held_recovery_window"), False)
            self.assertEqual(boundary.get("normal_can_open_inside_taker_final_window"), True)
            self.assertEqual(
                boundary.get("normal_taker_final_window_overlap_stages"),
                ["default"],
            )
            self.assertAlmostEqual(
                float(boundary.get("max_normal_entry_width_inside_final_window_sec") or 0.0),
                15.0,
                places=9,
            )
            self.assertIn("normal_entry_taker_final_window_overlap", set(posture.get("posture_flags") or []))
            summary = render_human_summary(report)
            self.assertIn("boundary_class=normal_new_exposure_allowed_inside_taker_final_window", summary)
            self.assertIn("normal_can_open_inside_final_window=1", summary)

    def test_taker_config_gate_posture_allows_pre_recovery_entry_band(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-config-pre-recovery-entry-band"
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "git_commit": "abc123",
                        "config_fingerprint_sha256": "cfg-posture",
                        "config": {
                            "runtime": _historical_recovery_runtime_config(),
                            "risk": {
                                "min_sec_to_expiry_for_new_exposure": 50.0,
                            },
                            "taker": {
                                "competitiveness": {
                                    "final_window_sec": 60.0,
                                },
                            },
                            "strategy": {
                                "maker_market_viability": {
                                    "timing_gate_enabled": True,
                                    "timing_gate_min_sec_to_expiry": 50.0,
                                    "timing_gate_max_sec_to_expiry": 60.0,
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            posture = report.get("taker_config_gate_posture", {})
            self.assertEqual(
                posture.get("boundary_class"),
                "normal_new_exposure_allowed_inside_taker_final_window",
            )
            boundary = posture.get("boundary_alignment", {})
            self.assertEqual(boundary.get("aligned_terminal_boundary"), False)
            self.assertEqual(boundary.get("normal_can_open_inside_held_recovery_window"), False)
            self.assertEqual(boundary.get("normal_can_open_inside_taker_final_window"), True)
            self.assertEqual(
                boundary.get("normal_taker_final_window_overlap_stages"),
                ["default"],
            )
            self.assertAlmostEqual(
                float(boundary.get("max_normal_entry_width_inside_final_window_sec") or 0.0),
                10.0,
                places=9,
            )
            flags = set(posture.get("posture_flags") or [])
            self.assertIn("normal_entry_taker_final_window_overlap", flags)
            self.assertNotIn("normal_entry_recovery_boundary_overlap", flags)
            summary = render_human_summary(report)
            self.assertIn("boundary_class=normal_new_exposure_allowed_inside_taker_final_window", summary)
            self.assertIn("final_window_overlap_max_sec=10.00", summary)
            self.assertNotIn("maker_taker_terminal_handoff=", summary)

    def test_taker_config_gate_posture_uses_taker_lane_override_for_boundary_truth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-config-taker-lane-override"
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "git_commit": "abc123",
                        "config_fingerprint_sha256": "cfg-posture",
                        "config": {
                            "runtime": _historical_recovery_runtime_config(),
                            "risk": {
                                "min_sec_to_expiry_for_new_exposure": 50.0,
                                "min_sec_to_expiry_for_new_exposure_by_lane": {
                                    "taker": 0.0,
                                },
                            },
                            "taker": {
                                "competitiveness": {
                                    "final_window_sec": 60.0,
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            posture = report.get("taker_config_gate_posture", {})
            self.assertEqual(
                posture.get("boundary_class"),
                "normal_new_exposure_allowed_inside_taker_final_window",
            )
            boundary = posture.get("boundary_alignment", {})
            self.assertAlmostEqual(
                float(boundary.get("min_sec_to_expiry_for_new_exposure")),
                0.0,
                places=9,
            )
            self.assertAlmostEqual(
                float(boundary.get("min_sec_to_expiry_for_new_exposure_global")),
                50.0,
                places=9,
            )
            self.assertEqual(
                dict(boundary.get("min_sec_to_expiry_for_new_exposure_by_lane") or {}),
                {"taker": 0.0},
            )
            self.assertEqual(
                str(boundary.get("min_sec_to_expiry_for_new_exposure_source") or ""),
                "lane_override",
            )
            self.assertEqual(
                boundary.get("normal_taker_final_window_overlap_stages"),
                ["default"],
            )
            self.assertAlmostEqual(
                float(boundary.get("max_normal_entry_width_inside_final_window_sec") or 0.0),
                60.0,
                places=9,
            )
            gates = posture.get("normal_taker_entry_gates", {})
            self.assertAlmostEqual(
                float(gates.get("min_sec_to_expiry_for_new_exposure")),
                0.0,
                places=9,
            )
            self.assertEqual(str(gates.get("min_sec_to_expiry_for_new_exposure_source") or ""), "lane_override")
            self.assertAlmostEqual(float(gates.get("final_window_floor_sec") or 0.0), 4.0, places=9)
            self.assertAlmostEqual(float(gates.get("min_visible_fill_ratio") or 0.0), 1.5, places=9)
            self.assertAlmostEqual(float(gates.get("min_submit_price") or 0.0), 0.05, places=9)

    def test_terminal_handoff_deadband_emits_wait_only_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-terminal-deadband"
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "evaluation_scope": "taker",
                                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                                "action_taken": "none",
                                "block_reason": _HISTORICAL_RECOVERY_WAITING_BLOCK_REASON,
                                "maker_phase_allowed": True,
                                "taker_phase_allowed": True,
                                "time_remaining_sec": 49.0,
                                **_historical_recovery_lineage(),
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "evaluation_scope": "taker",
                                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                                "action_taken": "none",
                                "block_reason": _HISTORICAL_RECOVERY_WAITING_BLOCK_REASON,
                                "maker_phase_allowed": True,
                                "taker_phase_allowed": True,
                                "time_remaining_sec": 48.0,
                                **_historical_recovery_lineage(),
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "evaluation_scope": "taker",
                                "lineage_stage": "MAKER_TAKER_SELECTIVE",
                                "action_taken": "taker",
                                "block_reason": None,
                                "maker_phase_allowed": True,
                                "taker_phase_allowed": True,
                                "time_remaining_sec": 44.0,
                                **_historical_recovery_lineage(),
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "profile_name": "paper_universal",
                        "git_commit": "abc123",
                        "config_fingerprint_sha256": "cfg-terminal-deadband",
                        "config": {
                            "runtime": _historical_recovery_runtime_config(),
                            "risk": {
                                "min_sec_to_expiry_for_new_exposure": 0.0,
                            },
                            "sniper": {
                                "taker": {
                                    "competitiveness": {
                                        "final_window_sec": 60.0,
                                    },
                                },
                            },
                            "strategy": {
                                "maker_market_viability": {
                                    "timing_gate_enabled": True,
                                    "timing_gate_min_sec_to_expiry": 50.0,
                                    "timing_gate_max_sec_to_expiry": 60.0,
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, run_id=run_id)
            self.assertNotIn("terminal_handoff_deadband", report)
            summary = render_human_summary(report)
            self.assertNotIn("terminal_handoff_deadband=", summary)

    def test_execution_quality_lane_attribution_splits_normal_and_recovery_taker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "maker-token",
                    "midpoint": 0.50,
                    "ts_utc": "2026-01-01T00:00:00Z",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-lane-attribution",
                    "order_id": "maker-order",
                    "reason": "mm_quote:high_vol",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "decision_reference_midpoint": 0.50,
                    "ts_utc": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-lane-attribution",
                    "order_id": "maker-order",
                    "token_id": "maker-token",
                    "side": "BUY",
                    "price": 0.49,
                    "size": 10,
                    "ts_utc": "2026-01-01T00:00:02Z",
                },
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "maker-token",
                    "midpoint": 0.48,
                    "ts_utc": "2026-01-01T00:00:03Z",
                },
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "normal-token",
                    "midpoint": 0.50,
                    "ts_utc": "2026-01-01T00:00:04Z",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-lane-attribution",
                    "order_id": "normal-order",
                    "reason": "taker_chainlink",
                    "lineage_stage": "SNIPER_PRIMARY",
                    "decision_reference_midpoint": 0.50,
                    "taker_competitiveness": {
                        "lineage_stage": "SNIPER_PRIMARY",
                        "edge_abs": 0.24,
                        "conviction_score": 0.82,
                        "timing_window_class": "final15",
                        "multi_oracle_status": "confirmed",
                        "submit_capable_static": True,
                        "submit_capable_dynamic_predicted": True,
                        "sec_to_expiry": 55.0,
                        "held_preexpiry_reduce_only_sec": 90.0,
                        **_historical_recovery_lineage(active=False),
                    },
                    "risk_decision_basis": {
                        "min_sec_to_expiry_for_new_exposure": 45.0,
                    },
                    "ts_utc": "2026-01-01T00:00:05Z",
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-lane-attribution",
                    "order_id": "normal-order",
                    "token_id": "normal-token",
                    "side": "SELL",
                    "price": 0.52,
                    "size": 10,
                    "ts_utc": "2026-01-01T00:00:06Z",
                },
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "normal-token",
                    "midpoint": 0.54,
                    "ts_utc": "2026-01-01T00:00:07Z",
                },
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "recovery-token",
                    "midpoint": 0.50,
                    "ts_utc": "2026-01-01T00:00:07.100Z",
                },
                {
                    "event_type": "order_submit",
                    "run_id": "rid-lane-attribution",
                    "order_id": "recovery-order",
                    "reason": "taker_chainlink",
                    "lineage_stage": "MAKER_TAKER_SELECTIVE",
                    "decision_reference_midpoint": 0.50,
                    "taker_competitiveness": {
                        "lineage_stage": "MAKER_TAKER_SELECTIVE",
                        "edge_abs": 0.74,
                        "sec_to_expiry": 54.5,
                        "held_preexpiry_reduce_only_sec": 90.0,
                        **_historical_recovery_lineage(),
                    },
                    "risk_decision_basis": {
                        "min_sec_to_expiry_for_new_exposure": 45.0,
                    },
                    "ts_utc": "2026-01-01T00:00:07.200Z",
                },
                {
                    "event_type": "fill",
                    "run_id": "rid-lane-attribution",
                    "order_id": "recovery-order",
                    "token_id": "recovery-token",
                    "side": "BUY",
                    "price": 0.55,
                    "size": 10,
                    "ts_utc": "2026-01-01T00:00:07.500Z",
                },
                {
                    "event_type": "book_top",
                    "run_id": "rid-lane-attribution",
                    "token_id": "recovery-token",
                    "midpoint": 0.56,
                    "ts_utc": "2026-01-01T00:00:08Z",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"run_id": "rid-lane-attribution", "gauge.open_orders": 0}) + "\n",
                encoding="utf-8",
            )
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-lane-attribution")
            lane_attr = report.get("execution_quality_lane_attribution", {})
            by_lane = lane_attr.get("by_lane", {})
            maker = by_lane.get("maker", {})
            normal = by_lane.get("normal_taker", {})
            recovery = by_lane.get("lifecycle_residue_taker", {})

            self.assertAlmostEqual(float(maker.get("immediate_capture_minus_adverse") or 0.0), 0.10, places=9)
            self.assertAlmostEqual(float(normal.get("immediate_capture_minus_adverse") or 0.0), 0.20, places=9)
            self.assertAlmostEqual(
                float(recovery.get("immediate_capture_minus_adverse") or 0.0),
                -0.50,
                places=9,
            )
            self.assertAlmostEqual(
                float((lane_attr.get("total") or {}).get("immediate_capture_minus_adverse") or 0.0),
                -0.20,
                places=9,
            )
            reconciliation = lane_attr.get("reconciliation", {})
            self.assertEqual(reconciliation.get("immediate_capture_minus_adverse_reconciles"), True)
            self.assertEqual(reconciliation.get("horizon_fills_scored_reconciles"), True)
            self.assertEqual(reconciliation.get("horizon_adverse_after_fill_count_reconciles"), True)
            self.assertEqual(float(maker.get("horizon_adverse_after_fill_count") or 0.0), 1.0)
            self.assertEqual(float(normal.get("horizon_adverse_after_fill_count") or 0.0), 1.0)
            self.assertEqual(float(recovery.get("horizon_adverse_after_fill_count") or 0.0), 0.0)
            self.assertEqual(int((normal.get("submit_lineage_stage_distribution") or {}).get("SNIPER_PRIMARY", 0)), 1)
            self.assertEqual(
                int((recovery.get("submit_lineage_stage_distribution") or {}).get("MAKER_TAKER_SELECTIVE", 0)),
                1,
            )
            self.assertNotIn("preexpiry_recovery_churn", report)
            decision_ref = report.get("execution_quality_decision_reference_lane_attribution", {})
            decision_by_lane = decision_ref.get("by_lane", {})
            self.assertAlmostEqual(
                float((decision_by_lane.get("maker") or {}).get("immediate_capture_minus_adverse") or 0.0),
                0.10,
                places=9,
            )
            self.assertAlmostEqual(
                float((decision_by_lane.get("normal_taker") or {}).get("immediate_capture_minus_adverse") or 0.0),
                0.20,
                places=9,
            )
            self.assertAlmostEqual(
                float(
                    (decision_by_lane.get("lifecycle_residue_taker") or {}).get(
                        "immediate_capture_minus_adverse"
                    )
                    or 0.0
                ),
                -0.50,
                places=9,
            )
            summary = render_human_summary(report)
            self.assertIn("execution_quality_decision_reference_lane_attribution=", summary)

    def test_build_report_emits_maker_regression_sentinel_triggered_for_near_zero_pattern(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = [
                {
                    "event_type": "order_submit",
                    "run_id": "rid-maker-sentinel",
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "order_id": "m1",
                    "reason": "mm_quote:high_vol",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-maker-sentinel",
                    "ts_utc": "2099-01-01T00:20:00Z",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_category": "replace_guard_min_rest",
                },
            ]
            status = [{"run_id": "rid-maker-sentinel", "ts_utc": "2099-01-01T00:10:00Z", "gauge.open_orders": 0}]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-maker-sentinel")
            sentinel = report.get("maker_regression_sentinel", {})
            self.assertEqual(sentinel.get("observational_only"), True)
            self.assertEqual(sentinel.get("maker_behavior_freeze_state"), "provisional_freeze_no_runtime_change")
            self.assertEqual(sentinel.get("triggered"), True)
            self.assertIn("near_zero_maker_submit_fill_pattern", sentinel.get("regression_reasons", []))
            self.assertIn("watch_item_distribution", sentinel)

    def test_build_report_emits_maker_regression_sentinel_not_triggered_for_healthy_activity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = []
            for idx in range(8):
                ts = f"2099-01-01T00:{idx:02d}:00Z"
                events.append(
                    {
                        "event_type": "order_submit",
                        "run_id": "rid-maker-healthy",
                        "ts_utc": ts,
                        "order_id": f"m{idx}",
                        "reason": "mm_quote:high_vol",
                    }
                )
                if idx < 5:
                    events.append(
                        {
                            "event_type": "fill",
                            "run_id": "rid-maker-healthy",
                            "ts_utc": ts,
                            "order_id": f"m{idx}",
                            "token_id": "t1",
                            "side": "BUY",
                            "price": 0.5,
                            "size": 1,
                        }
                    )
            events.append(
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-maker-healthy",
                    "ts_utc": "2099-01-01T00:20:00Z",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                    "maker_no_submission_category": "replace_guard_min_rest",
                }
            )
            status = [{"run_id": "rid-maker-healthy", "ts_utc": "2099-01-01T00:10:00Z", "gauge.open_orders": 1}]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-maker-healthy")
            sentinel = report.get("maker_regression_sentinel", {})
            self.assertEqual(sentinel.get("triggered"), False)
            self.assertEqual(float(sentinel.get("maker_submits") or 0.0), 8.0)
            self.assertEqual(float(sentinel.get("maker_fills") or 0.0), 5.0)

    def test_build_report_stale_stats_use_edge_evaluation_block_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "risk_reject", "reason": "stale_book", "run_id": "rid-stale"},
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-stale",
                    "action_taken": "none",
                    "block_reason": "oracle_unavailable_or_stale",
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-stale",
                    "action_taken": "none",
                    "block_reason": "maker_timing_gate_closed",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": "rid-stale", "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-stale")
            stale = report.get("stale_data", {})
            self.assertEqual(stale.get("stale_book_rejects"), 1.0)
            self.assertEqual(stale.get("stale_oracle_edge_blocks"), 1.0)

    def test_build_report_includes_market_data_source_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {
                    "counter.book_updates": 10,
                    "counter.book_updates_ws": 8,
                    "pair_truth_pair_count": 2,
                    "pair_truth_missing_pair_count": 1,
                    "pair_truth_one_sided_pair_count": 0,
                    "pair_truth_authoritative_pair_count": 1,
                    "pair_truth_one_sided_base_keys": [],
                },
                {
                    "counter.book_updates": 16,
                    "counter.book_updates_ws": 12,
                    "pair_truth_pair_count": 2,
                    "pair_truth_missing_pair_count": 1,
                    "pair_truth_one_sided_pair_count": 1,
                    "pair_truth_authoritative_pair_count": 0,
                    "pair_truth_one_sided_base_keys": ["mkt-b"],
                },
                {
                    "counter.book_updates": 22,
                    "counter.book_updates_ws": 15,
                    "pair_truth_pair_count": 2,
                    "pair_truth_missing_pair_count": 0,
                    "pair_truth_one_sided_pair_count": 1,
                    "pair_truth_authoritative_pair_count": 1,
                    "pair_truth_one_sided_base_keys": ["mkt-b", "mkt-c"],
                },
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            source_stats = report.get("market_data_source", {})
            self.assertEqual(source_stats.get("book_updates_total_delta"), 12.0)
            self.assertEqual(source_stats.get("book_updates_ws_delta"), 7.0)
            self.assertEqual(source_stats.get("pair_truth_pair_count_max"), 2.0)
            self.assertEqual(source_stats.get("pair_truth_missing_pair_count_max"), 1.0)
            self.assertEqual(source_stats.get("pair_truth_one_sided_pair_count_max"), 1.0)
            self.assertEqual(source_stats.get("pair_truth_authoritative_pair_count_max"), 1.0)
            self.assertAlmostEqual(float(source_stats.get("pair_truth_missing_pair_row_ratio") or 0.0), 2.0 / 3.0)
            self.assertAlmostEqual(float(source_stats.get("pair_truth_one_sided_row_ratio") or 0.0), 2.0 / 3.0)
            self.assertEqual(source_stats.get("pair_truth_one_sided_base_keys_seen"), ["mkt-b", "mkt-c"])

    def test_build_report_emits_transport_vs_control_authority_clarity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": False,
                    "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.1},
                    "alert_transport_enabled": False,
                    "auto_stop_control_authority_enabled": True,
                    "transport_disable_control_authority_unchanged": True,
                }
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            clarity = report.get("control_authority_clarity", {})
            self.assertEqual(clarity.get("alert_transport_enabled"), False)
            self.assertEqual(clarity.get("auto_stop_control_authority_enabled"), True)
            self.assertEqual(clarity.get("transport_disable_control_authority_unchanged"), True)
            self.assertEqual(
                clarity.get("transport_disable_semantics"),
                "transport_disabled_control_authority_unchanged",
            )
            self.assertEqual(clarity.get("control_authority_observation_status"), "explicit")

    def test_build_report_marks_control_authority_unknown_when_status_fields_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": False,
                    "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.1},
                }
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            clarity = report.get("control_authority_clarity", {})
            self.assertIsNone(clarity.get("alert_transport_enabled"))
            self.assertIsNone(clarity.get("auto_stop_control_authority_enabled"))
            self.assertEqual(clarity.get("transport_disable_semantics"), "unknown_status_fields_missing")
            self.assertEqual(clarity.get("control_authority_observation_status"), "unknown_missing_fields")

    def test_build_report_emits_suppression_observability_for_zero_order_safety_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = [
                {
                    "event_type": "operating_mode_transition",
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "previous_state": "normal",
                    "state": "safe_stop",
                    "reason": "severe_from_maker_only",
                },
                {
                    "event_type": "kill_switch_cancel_all",
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "reason": "operating_mode_safe_stop",
                },
                {
                    "event_type": "edge_evaluation",
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "action_taken": "none",
                    "block_reason": "operating_mode_safe_stop",
                },
            ]
            status = [
                {
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": True,
                    "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.1},
                }
            ]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            report = build_report(root)
            self.assertEqual(report.get("suppression_dominated_run"), True)
            self.assertEqual(report.get("execution_starvation_mode"), "kill_switch")
            self.assertIn("primary_suppression_cause", report)
            chain = report.get("protection_path_trigger_chain", {})
            self.assertEqual(chain.get("final_execution_starvation_mode"), "kill_switch")
            self.assertEqual(chain.get("trigger_chain_interpretation"), "causal_suppression_chain")
            self.assertTrue(isinstance(chain.get("first_kill_switch_cancel_all"), dict))

    def test_build_report_surfaces_ambiguous_primary_suppression_cause(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": True,
                    "external_guard_active": False,
                    "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": 45.0},
                }
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            report = build_report(root)
            self.assertEqual(report.get("ambiguous_suppression_cause"), True)
            self.assertEqual(report.get("primary_suppression_cause"), "none")
            contributing = set(report.get("contributing_suppression_causes", []))
            self.assertIn("safety_kill_switch_or_external_guard", contributing)
            self.assertIn("safety_required_market_truth_disconnected", contributing)

    def test_build_report_infers_suppression_mode_from_block_reason_distribution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = [
                {
                    "event_type": "edge_evaluation",
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "action_taken": "none",
                    "block_reason": "normal_taker_authority_closed",
                },
                {
                    "event_type": "edge_evaluation",
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "action_taken": "none",
                    "block_reason": "normal_taker_authority_closed",
                },
                {
                    "event_type": "edge_evaluation",
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "action_taken": "none",
                    "block_reason": "taker_requires_ws_book_source",
                },
            ]
            status = [
                {
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": False,
                    "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.1},
                }
            ]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            with mock.patch(
                "scripts.nightly_soak_report.classify_runtime",
                return_value={
                    "classification": "VALID_ACTIVE",
                    "primary_suppression_cause": "synthetic_gate_signal",
                    "contributing_suppression_causes": [],
                    "ambiguous_suppression_cause": False,
                },
            ):
                report = build_report(root)

            self.assertEqual(report.get("suppression_dominated_run"), True)
            self.assertEqual(report.get("execution_starvation_mode"), "late_window_authority_gate")
            self.assertEqual(report.get("inferred_suppression_reason"), "normal_taker_authority_closed")
            self.assertEqual(int(report.get("inferred_suppression_reason_count") or 0), 2)
            chain = report.get("protection_path_trigger_chain", {})
            self.assertEqual(chain.get("inferred_suppression_reason"), "normal_taker_authority_closed")
            self.assertEqual(int(chain.get("inferred_suppression_reason_count") or 0), 2)

    def test_build_report_marks_trigger_chain_as_observational_when_not_suppressed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = [
                {"event_type": "order_submit", "ts_utc": "2099-01-01T00:00:00Z", "order_id": "o1"},
                {"event_type": "fill", "ts_utc": "2099-01-01T00:00:01Z", "order_id": "o1", "token_id": "t1", "side": "BUY", "price": 0.51, "size": 1},
            ]
            status = [
                {
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "lifecycle_phase": "prepare",
                    "lifecycle_phase": "prepare",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "kill_switch": False,
                    "book_feed": {"enabled": True, "connected": True, "last_msg_age_sec": 0.2},
                }
            ]
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in events) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(x) for x in status) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            report = build_report(root)
            chain = report.get("protection_path_trigger_chain", {})
            self.assertEqual(report.get("suppression_dominated_run"), False)
            self.assertEqual(chain.get("trigger_chain_interpretation"), "observational_timeline_only")


if __name__ == "__main__":
    unittest.main()
