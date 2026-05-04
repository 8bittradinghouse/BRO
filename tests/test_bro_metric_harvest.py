import json
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bro_metric_harvest  # noqa: E402


def _write_json(path: pathlib.Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: pathlib.Path, payloads: list[dict]) -> None:
    path.write_text("".join(json.dumps(payload) + "\n" for payload in payloads), encoding="utf-8")


class BroMetricHarvestTests(unittest.TestCase):
    def test_harvest_reports_supports_exact_run_id_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            report_root = root / "reports"
            report_root.mkdir()
            out_dir = root / "out"
            run1 = report_root / "run-alpha"
            run2 = report_root / "run-beta"
            run1.mkdir()
            run2.mkdir()

            for run_dir, run_id, runtime_classification, start_ts in [
                (run1, "run-alpha", "VALID_ACTIVE", "2026-04-28T13:00:00Z"),
                (run2, "run-beta", "NON_PROMOTABLE_NO_PARTICIPATION", "2026-04-28T13:20:00Z"),
            ]:
                _write_json(
                    run_dir / "validation_summary.json",
                    {
                        "run_id": run_id,
                        "overall_exit_code": 0,
                        "ok": True,
                        "validator_determinism_ok": True,
                        "edge_truth_determinism_ok": True,
                        "non_edge_determinism_ok": True,
                    },
                )
                _write_json(
                    run_dir / "canonical_paper_validation.json",
                    {
                        "run_id": run_id,
                        "gate_passed": True,
                        "highest_passing_stage": "paper",
                        "blocking_stage": "pilot_live",
                        "runtime_classification": runtime_classification,
                    },
                )
                _write_json(
                    run_dir / "nightly_soak_report.json",
                    {
                        "artifact_identity": {"profile_name": "paper_universal"},
                        "runtime_classification": {"classification": runtime_classification},
                        "duration_minutes": 20.0,
                    },
                )
                _write_json(
                    root / f"run_contract_{run_id}.json",
                    {
                        "run_id": run_id,
                        "start_ts": start_ts,
                        "stop_ts": "2026-04-28T13:40:00Z",
                        "session_type": "paper_canonical",
                        "log_root": str(root),
                    },
                )

            run_id_file = root / "selected_runs.txt"
            run_id_file.write_text("run-beta\n# keep this line ignored\nrun-beta\n", encoding="utf-8")

            outputs = bro_metric_harvest.harvest_reports(
                report_root=report_root,
                out_dir=out_dir,
                run_id_file=run_id_file,
            )

            run_rows = [
                json.loads(line)
                for line in outputs["run_index_jsonl"].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(run_rows), 1)
            self.assertEqual(run_rows[0]["run_id"], "run-beta")
            manifest = json.loads(outputs["fma_bundle_manifest_json"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected_run_ids"], ["run-beta"])
            self.assertEqual(manifest["filters"]["run_ids"], ["run-beta"])
            self.assertEqual(
                pathlib.Path(manifest["filters"]["run_id_file"]).name,
                "selected_runs.txt",
            )

    def test_harvest_reports_writes_engineer_first_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            report_root = root / "reports"
            report_root.mkdir()
            out_dir = root / "out"
            run1 = report_root / "run-alpha"
            run2 = report_root / "run-beta"
            run3 = report_root / "run-gamma-dir"
            run1.mkdir()
            run2.mkdir()
            run3.mkdir()

            _write_json(
                run1 / "validation_summary.json",
                {
                    "run_id": "run-alpha",
                    "overall_exit_code": 0,
                    "ok": True,
                    "validator_determinism_ok": True,
                    "edge_truth_determinism_ok": True,
                    "non_edge_determinism_ok": True,
                    "outcome_truth_usability": "high",
                },
            )
            _write_json(
                run1 / "canonical_paper_validation.json",
                {
                    "run_id": "run-alpha",
                    "session_phase": "validate_postrun",
                    "gate_passed": True,
                    "reports_complete": True,
                    "highest_passing_stage": "paper",
                    "blocking_stage": "pilot_live",
                    "promotion_eligible": True,
                    "recommended_next_stage": "pilot_live",
                    "runtime_classification": "VALID_ACTIVE",
                },
            )
            _write_json(
                run1 / "nightly_soak_report.json",
                {
                    "artifact_identity": {"profile_name": "paper_universal"},
                    "runtime_classification": {
                        "classification": "VALID_ACTIVE",
                        "primary_suppression_cause": "none",
                        "promotion_eligible": True,
                        "metrics": {
                            "active_targets_seen": 1.0,
                            "meaningful_participation": 1.0,
                            "decision_events": 50.0,
                            "required_book_feed_disconnected_rows": 2.0,
                        },
                    },
                    "duration_minutes": 12.0,
                    "error_rows": 0.0,
                    "quote_uptime_ratio": 0.25,
                    "quote_diagnostics": {"quote_active_ratio": 0.2, "participation_ratio": 0.1},
                    "execution_paths": {
                        "maker_submits": 10.0,
                        "maker_fills": 2.0,
                        "maker_filled_orders": 2.0,
                        "maker_fill_rate": 0.2,
                        "maker_fire_rate_per_min": 0.8,
                        "taker_bonus_submits": 5.0,
                        "taker_bonus_fills": 4.0,
                        "taker_bonus_filled_orders": 4.0,
                        "taker_bonus_fill_rate": 0.8,
                        "taker_bonus_fire_rate_per_min": 0.4,
                    },
                    "maker_competitiveness": {
                        "timing_gate_blocked_count_decision": 11.0,
                        "timing_gate_blocked_count_edge_eval": 12.0,
                    },
                    "edge_truth": {
                        "maker_reference_direct_midpoint_activity": 100.0,
                        "maker_reference_bounded_fallback_activity": 6.0,
                        "maker_reference_direct_midpoint_action_activity": 8.0,
                        "maker_reference_bounded_fallback_action_activity": 1.0,
                        "maker_no_submission_cause_distribution": {"replace_guard_min_rest": 7},
                        "maker_block_reason_distribution": {"maker_timing_gate_closed": 11},
                    },
                    "maker_regression_sentinel": {
                        "triggered": False,
                        "maker_behavior_freeze_state": "provisional",
                        "watch_item_primary": "replace_guard_min_rest",
                    },
                    "maker_fireability": {
                        "active_window_row_count": 10.0,
                        "active_window_submit_count": 4.0,
                        "active_window_replace_guard_count": 3.0,
                        "active_window_quote_quality_skip_fill_probability_count": 2.0,
                        "active_window_quote_quality_skip_queue_depth_count": 1.0,
                        "active_window_sizing_reject_count": 1.0,
                        "active_window_low_price_viability_floor": 0.04375,
                        "active_window_viable_row_count": 8.0,
                        "active_window_impossible_row_count": 2.0,
                        "active_window_unknown_viability_row_count": 0.0,
                        "active_window_viable_target_count": 1.0,
                        "active_window_impossible_target_count": 1.0,
                        "active_window_mixed_viability_target_count": 0.0,
                        "active_window_unknown_viability_target_count": 0.0,
                        "active_window_low_price_conflict_price_band": {
                            "min": 0.02,
                            "p50": 0.03,
                            "max": 0.04,
                        },
                        "active_window_queue_depth_on_viable_targets_count": 0.0,
                        "active_window_queue_depth_on_impossible_targets_count": 1.0,
                        "active_window_queue_depth_on_mixed_targets_count": 0.0,
                        "active_window_queue_depth_on_unknown_targets_count": 0.0,
                        "raw_queue_depth_event_count": 1.0,
                        "raw_queue_depth_near_threshold_event_count": 1.0,
                        "raw_queue_depth_hard_miss_event_count": 0.0,
                        "raw_queue_depth_unknown_severity_event_count": 0.0,
                        "active_window_target_summary": [
                            {
                                "target_ref": "target-a",
                                "window_row_count": 6.0,
                                "submitted_count": 3.0,
                                "replace_guard_min_rest_count": 2.0,
                                "quote_quality_skip_fill_probability_count": 1.0,
                                "quote_quality_skip_queue_depth_count": 0.0,
                                "sizing_reject_count": 0.0,
                                "viability_class": "viable_only",
                                "viable_viability_row_count": 6.0,
                                "impossible_viability_row_count": 0.0,
                                "unknown_viability_row_count": 0.0,
                                "market_probability_min": 0.08,
                                "market_probability_p50": 0.10,
                                "market_probability_max": 0.12,
                                "submit_sec_to_expiry_sample": [59.9, 56.9, 52.9],
                                "submit_gap_sec_sample": [3.0, 4.0],
                            },
                            {
                                "target_ref": "target-b",
                                "window_row_count": 4.0,
                                "submitted_count": 1.0,
                                "replace_guard_min_rest_count": 1.0,
                                "quote_quality_skip_fill_probability_count": 1.0,
                                "quote_quality_skip_queue_depth_count": 1.0,
                                "sizing_reject_count": 1.0,
                                "viability_class": "impossible_only",
                                "viable_viability_row_count": 0.0,
                                "impossible_viability_row_count": 4.0,
                                "unknown_viability_row_count": 0.0,
                                "market_probability_min": 0.02,
                                "market_probability_p50": 0.03,
                                "market_probability_max": 0.04,
                                "submit_sec_to_expiry_sample": [58.9],
                                "submit_gap_sec_sample": [],
                            }
                        ],
                        "raw_quote_quality_skip_severity": {
                            "fill_probability_delta_bins": {
                                "within_0p005": 1,
                                "gt_0p015": 1,
                            },
                            "queue_depth_delta_bins": {
                                "within_25": 1,
                            },
                        },
                    },
                    "maker_sizing_competitiveness": {
                        "maker_submit_rows": 4.0,
                        "maker_size_resolution_rows": 4.0,
                        "maker_sizing_reject_rows": 1.0,
                        "maker_min_notional_max_shares_conflict_rows": 1.0,
                        "maker_sizing_reject_price_min": 0.02,
                        "maker_sizing_reject_price_p50": 0.03,
                        "maker_sizing_reject_price_max": 0.04,
                        "maker_sizing_reject_reason_distribution": {
                            "maker_hard_max_shares_cap": 1,
                            "maker_hard_min_notional_failed_after_rounding": 1,
                        },
                    },
                    "taker_competitiveness": {
                        "decision_count": 20.0,
                        "decision_to_submit_rate": 0.25,
                        "submit_capable_to_submit_rate": 0.5,
                        "submit_capable_dynamic_to_submit_rate": 0.4,
                        "blocked_decision_count": 3.0,
                        "risk_reject_after_capable_count_edge_eval": 2.0,
                        "fill_without_submit_stage_count": 0.0,
                        "hidden_blockage_detector": {
                            "decision_to_dynamic_predicted_delta": 4.0,
                            "dynamic_predicted_to_submit_delta": 1.0,
                            "submit_to_fill_delta": 1.0,
                        },
                        "decision_timing_window_distribution": {"final_window": 20},
                        "submit_timing_window_distribution": {"final_window": 5},
                        "fill_stage_distribution": {"MAKER_TAKER_SELECTIVE": 4},
                        "decision_predicted_reject_reason_distribution": {"size_too_small": 3},
                        "stage_final_risk_reject_reason_distribution": {"MAKER_TAKER_SELECTIVE": {"risk_reject_size_too_small": 2}},
                    },
                    "reduce_only_recovery": {
                        "edge_waiting_for_maker_exit_rows": 2.0,
                        "local_size_cap_classification": "nonflat_or_unknown_present",
                        "local_size_cap_nonflat_or_unknown_rows": 1.0,
                        "local_size_cap_flat_or_wrong_side_rows": 4.0,
                        "local_size_cap_unavailable_rows": 5.0,
                        "local_reject_lane_distribution": {"maker": 4, "taker": 1},
                    },
                    "wallet_authority": {
                        "authority_status_class": "authoritative",
                        "authoritative_wallet_contract_present": True,
                        "reservation_mismatch_candidate": False,
                        "reservation_mismatch_delta_usdc": 0.0,
                        "live_truth_gap_reasons": [],
                        "wallet_contract_surface_source": "wallet_contract",
                        "latest_contract": {
                            "order_capable_live": False,
                            "order_submit_eligible": True,
                            "deployable_capital": 650.5,
                            "stable_balance_total": 650.5,
                            "open_reserved": 0.0,
                            "protected_reserve": 0.0,
                        },
                    },
                    "risk_competitiveness": {
                        "global_exposure_cap_reject_count": 0.0,
                        "global_exposure_near_cap_count": 0.0,
                        "global_exposure_utilization_ratio_max": 0.3,
                        "global_exposure_utilization_ratio_p50": 0.1,
                        "global_exposure_utilization_ratio_p90": 0.2,
                        "reject_reason_distribution": {"size_too_small": 9},
                        "reject_count_by_lane": {"maker": 5, "taker": 4},
                    },
                    "valuation_truth": {
                        "valuation_degraded_ratio": 0.5,
                        "valuation_hard_degraded_ratio": 0.0,
                        "valuation_bruise_state": "recovered_clean",
                        "valuation_dominant_reason_family_run": "degraded_using_last_known_mid",
                        "valuation_dominant_held_unpriceable_cause_run": "none",
                        "valuation_dominant_source_degraded_rows": "last_known_mid",
                        "valuation_degraded_reason_family_counts_run": {
                            "degraded_using_last_known_mid": 2.0,
                        },
                        "valuation_source_counts_degraded_rows": {
                            "live_mid": 0.0,
                            "live_side_conservative_quote": 0.0,
                            "last_known_mid": 2.0,
                            "conservative_bound_hard_degraded": 0.0,
                            "hard_degraded": 0.0,
                        },
                        "valuation_hard_degraded_enter_count": 1.0,
                        "valuation_hard_degraded_clear_count": 1.0,
                        "held_unpriceable_unrecovered_meaningful_count": 0.0,
                        "held_unpriceable_unrecovered_dust_exempted_count": 1.0,
                        "held_unpriceable_escalation_ratio": 0.25,
                        "preexpiry_emergency_taker_attempt_count": 12.0,
                        "preexpiry_emergency_taker_block_count": 8.0,
                        "preexpiry_emergency_taker_fill_count": 4.0,
                        "preexpiry_emergency_taker_block_reason_counts": {"risk_reject_size_too_small": 8},
                    },
                    "market_data_source": {
                        "book_updates_rest_ratio": 0.3,
                        "book_updates_ws_delta": 90.0,
                        "book_updates_rest_delta": 30.0,
                        "book_updates_total_delta": 120.0,
                    },
                    "stale_data": {"disarmed_edge_blocks": 9.0},
                    "secondary_oracle_pyth": {"connected_ratio_when_enabled": 1.0, "unavailable_sample_count": 0.0},
                    "latency_distribution_ms": {"median_ms": 200.0, "p90_ms": 400.0, "p95_ms": 450.0, "sample_count": 12},
                    "execution_quality": {
                        "capture_minus_adverse": -5.0,
                        "realized_capture": 15.0,
                        "adverse_selection": 20.0,
                        "fills_scored": 6.0,
                    },
                },
            )
            _write_json(run1 / "edge_truth_audit.json", {"metrics": {"edge_rows": 100.0}, "ok": True, "finding_count": 0})
            _write_json(
                run1 / "order_lifecycle_audit.json",
                {
                    "ok": True,
                    "finding_count": 0,
                    "warning_count": 0,
                    "order_submit_decision_missing_count": 0,
                    "edge_decision_ingest_missing_count": 0,
                    "edge_decision_submit_missing_count": 0,
                    "duplicate_fill_trade_ids": [],
                    "duplicate_order_submit_ids": [],
                    "fill_without_submit_order_ids": [],
                    "cancel_without_submit_order_ids": ["x"],
                    "lifecycle_counts": {"order_submit": 5, "fill": 4},
                },
            )
            _write_json(
                run1 / "outcome_truth_audit.json",
                {
                    "attribution_usability_ratio": 0.9,
                    "filled_complete_ratio": 1.0,
                    "complete_classification_ratio": 0.95,
                    "record_claim_boundary_distribution": {"complete": 4},
                    "outcome_truth_status_distribution": {"complete": 4},
                    "slippage_summary": {"mean": 0.01},
                    "adverse_selection_summary": {"mean": 0.02},
                },
            )
            _write_jsonl(
                run1 / "outcome_truth_records.jsonl",
                [
                    {
                        "order_submit_id": "alpha-order-1",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-a",
                        "outcome_truth_status": "unknown_incomplete_lifecycle",
                        "fill_count": 0,
                        "decision_quality": "correct",
                        "decision_component_x_size": None,
                        "execution_component_x_size": None,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                    },
                    {
                        "order_submit_id": "alpha-order-2",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-a",
                        "outcome_truth_status": "complete",
                        "fill_count": 1,
                        "decision_quality": "correct",
                        "decision_component_x_size": 5.0,
                        "execution_component_x_size": 1.0,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                        "order_side": "BUY",
                        "mid_price_decision": 0.2,
                        "mid_price_eval": 0.25,
                        "edge_expected": 0.05,
                        "fill_total_size": 100.0,
                    },
                    {
                        "order_submit_id": "alpha-order-3",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-b",
                        "outcome_truth_status": "complete",
                        "fill_count": 3,
                        "decision_quality": "incorrect",
                        "decision_component_x_size": -30.0,
                        "execution_component_x_size": 20.0,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                        "order_side": "BUY",
                        "mid_price_decision": 0.545,
                        "mid_price_eval": 0.595,
                        "edge_expected": -0.05,
                        "fill_total_size": 618.5075,
                    },
                    {
                        "order_submit_id": "alpha-order-4",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-c",
                        "outcome_truth_status": "complete",
                        "fill_count": 3,
                        "decision_quality": "incorrect",
                        "decision_component_x_size": -30.0,
                        "execution_component_x_size": 20.0,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                        "order_side": "SELL",
                        "mid_price_decision": 0.455,
                        "mid_price_eval": 0.405,
                        "edge_expected": 0.05,
                        "fill_total_size": 618.5075,
                    },
                ],
            )
            _write_json(
                run1 / "soak_hardening_gate.json",
                {
                    "ok": True,
                    "readiness": {"blocking_stage": "pilot_live", "highest_passing_stage": "paper"},
                    "websocket": {
                        "ok": True,
                        "finding_count": 0,
                        "metrics": {
                            "chainlink_down_ratio": 0.01,
                            "chainlink_last_tick_age_p95_sec": 1.2,
                            "book_feed_down_ratio": 0.02,
                            "book_feed_last_msg_age_p95_sec": 1.5,
                        },
                    },
                    "integrity": {
                        "ok": True,
                        "finding_count": 0,
                        "warning_count": 0,
                        "fill_event_count": 4,
                        "cancel_all_event_count": 1,
                        "duplicate_fill_trade_id_count": 0,
                        "event_row_count": 400,
                        "status_row_count": 20,
                    },
                },
            )
            _write_jsonl(
                run2 / "outcome_truth_records.jsonl",
                [
                    {
                        "order_submit_id": "beta-order-1",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-x",
                        "outcome_truth_status": "unknown_incomplete_lifecycle",
                        "fill_count": 1,
                        "decision_quality": "incorrect",
                        "decision_component_x_size": None,
                        "execution_component_x_size": None,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                    },
                    {
                        "order_submit_id": "beta-order-2",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-y",
                        "outcome_truth_status": "complete",
                        "fill_count": 1,
                        "decision_quality": "incorrect",
                        "decision_component_x_size": -4.0,
                        "execution_component_x_size": 1.0,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                        "order_side": "BUY",
                        "mid_price_decision": 0.4,
                        "mid_price_eval": 0.5,
                        "edge_expected": -0.1,
                        "fill_total_size": 50.0,
                    },
                ],
            )

            _write_json(
                run2 / "validation_summary.json",
                {
                    "run_id": "run-beta",
                    "overall_exit_code": 1,
                    "ok": False,
                    "validator_determinism_ok": False,
                    "edge_truth_determinism_ok": False,
                    "non_edge_determinism_ok": False,
                    "outcome_truth_usability": "partial",
                },
            )
            _write_json(
                run2 / "canonical_paper_validation.json",
                {
                    "run_id": "run-beta",
                    "session_phase": "validate_postrun",
                    "gate_passed": False,
                    "reports_complete": False,
                    "highest_passing_stage": "smoke",
                    "blocking_stage": "paper",
                    "promotion_eligible": False,
                    "recommended_next_stage": "paper",
                    "runtime_classification": "SUPPRESSED",
                },
            )
            _write_json(
                run2 / "nightly_soak_report.json",
                {
                    "artifact_identity": {"profile_name": "paper_universal"},
                    "runtime_classification": {
                        "classification": "SUPPRESSED",
                        "primary_suppression_cause": "timing_gate",
                        "promotion_eligible": False,
                        "metrics": {
                            "active_targets_seen": 1.0,
                            "meaningful_participation": 0.0,
                            "decision_events": 5.0,
                            "required_book_feed_disconnected_rows": 5.0,
                        },
                    },
                    "duration_minutes": 5.0,
                    "error_rows": 2.0,
                    "quote_uptime_ratio": 0.01,
                    "quote_diagnostics": {"quote_active_ratio": 0.01, "participation_ratio": 0.0},
                    "execution_paths": {
                        "maker_submits": 0.0,
                        "maker_fills": 0.0,
                        "maker_filled_orders": 0.0,
                        "maker_fill_rate": 0.0,
                        "maker_fire_rate_per_min": 0.0,
                        "taker_bonus_submits": 0.0,
                        "taker_bonus_fills": 0.0,
                        "taker_bonus_filled_orders": 0.0,
                        "taker_bonus_fill_rate": 0.0,
                        "taker_bonus_fire_rate_per_min": 0.0,
                    },
                    "maker_competitiveness": {
                        "timing_gate_blocked_count_decision": 30.0,
                        "timing_gate_blocked_count_edge_eval": 30.0,
                    },
                    "edge_truth": {
                        "maker_reference_direct_midpoint_activity": 0.0,
                        "maker_reference_bounded_fallback_activity": 0.0,
                        "maker_reference_direct_midpoint_action_activity": 0.0,
                        "maker_reference_bounded_fallback_action_activity": 0.0,
                        "maker_no_submission_cause_distribution": {"no_desired_quote": 5},
                        "maker_block_reason_distribution": {"maker_timing_gate_closed": 30},
                    },
                    "maker_regression_sentinel": {
                        "triggered": True,
                        "maker_behavior_freeze_state": "freeze",
                        "watch_item_primary": "maker_timing_gate_closed",
                    },
                    "taker_competitiveness": {
                        "decision_count": 10.0,
                        "decision_to_submit_rate": 0.0,
                        "submit_capable_to_submit_rate": 0.0,
                        "submit_capable_dynamic_to_submit_rate": 0.0,
                        "blocked_decision_count": 10.0,
                        "risk_reject_after_capable_count_edge_eval": 5.0,
                        "fill_without_submit_stage_count": 1.0,
                        "hidden_blockage_detector": {
                            "decision_to_dynamic_predicted_delta": 10.0,
                            "dynamic_predicted_to_submit_delta": 0.0,
                            "submit_to_fill_delta": 0.0,
                        },
                        "decision_timing_window_distribution": {"final_window": 10},
                        "submit_timing_window_distribution": {},
                        "fill_stage_distribution": {},
                        "decision_predicted_reject_reason_distribution": {"size_too_small": 10},
                        "stage_final_risk_reject_reason_distribution": {"EXTREME_ONLY": {"risk_reject_size_too_small": 10}},
                    },
                    "reduce_only_recovery": {
                        "edge_waiting_for_maker_exit_rows": 0.0,
                        "local_size_cap_classification": "flat_or_wrong_side_only",
                        "local_size_cap_nonflat_or_unknown_rows": 0.0,
                        "local_size_cap_flat_or_wrong_side_rows": 2.0,
                        "local_size_cap_unavailable_rows": 2.0,
                        "local_reject_lane_distribution": {"taker": 2},
                    },
                    "wallet_authority": {
                        "authority_status_class": "bootstrap_non_authoritative",
                        "authoritative_wallet_contract_present": False,
                        "reservation_mismatch_candidate": True,
                        "reservation_mismatch_delta_usdc": 5.0,
                        "live_truth_gap_reasons": ["bootstrap"],
                        "wallet_contract_surface_source": "bootstrap_wallet",
                        "latest_contract": {
                            "order_capable_live": False,
                            "order_submit_eligible": False,
                            "deployable_capital": 100.0,
                            "stable_balance_total": 100.0,
                            "open_reserved": 5.0,
                            "protected_reserve": 10.0,
                        },
                    },
                    "risk_competitiveness": {
                        "global_exposure_cap_reject_count": 1.0,
                        "global_exposure_near_cap_count": 1.0,
                        "global_exposure_utilization_ratio_max": 0.9,
                        "global_exposure_utilization_ratio_p50": 0.8,
                        "global_exposure_utilization_ratio_p90": 0.85,
                        "reject_reason_distribution": {"size_too_small": 10},
                        "reject_count_by_lane": {"taker": 10},
                    },
                    "valuation_truth": {
                        "valuation_degraded_ratio": 1.0,
                        "valuation_hard_degraded_ratio": 0.5,
                        "valuation_bruise_state": "open_meaningful_unpriceable",
                        "valuation_dominant_reason_family_run": "hard_degraded",
                        "valuation_dominant_held_unpriceable_cause_run": "preexpiry_fetch_failure",
                        "valuation_dominant_source_degraded_rows": "hard_degraded",
                        "valuation_degraded_reason_family_counts_run": {
                            "hard_degraded": 1.0,
                        },
                        "valuation_source_counts_degraded_rows": {
                            "live_mid": 0.0,
                            "live_side_conservative_quote": 0.0,
                            "last_known_mid": 0.0,
                            "conservative_bound_hard_degraded": 0.0,
                            "hard_degraded": 1.0,
                        },
                        "valuation_hard_degraded_enter_count": 1.0,
                        "valuation_hard_degraded_clear_count": 0.0,
                        "held_unpriceable_unrecovered_meaningful_count": 1.0,
                        "held_unpriceable_unrecovered_dust_exempted_count": 0.0,
                        "held_unpriceable_escalation_ratio": 1.0,
                        "held_unpriceable_cause_counts_run": {
                            "preexpiry_fetch_failure": 1.0,
                        },
                        "preexpiry_emergency_taker_attempt_count": 3.0,
                        "preexpiry_emergency_taker_block_count": 3.0,
                        "preexpiry_emergency_taker_fill_count": 0.0,
                        "preexpiry_emergency_taker_block_reason_counts": {"risk_reject_size_too_small": 3},
                    },
                    "market_data_source": {
                        "book_updates_rest_ratio": 0.8,
                        "book_updates_ws_delta": 5.0,
                        "book_updates_rest_delta": 20.0,
                        "book_updates_total_delta": 25.0,
                    },
                    "stale_data": {"disarmed_edge_blocks": 12.0},
                    "secondary_oracle_pyth": {"connected_ratio_when_enabled": 0.5, "unavailable_sample_count": 2.0},
                    "latency_distribution_ms": {"median_ms": 900.0, "p90_ms": 1500.0, "p95_ms": 1800.0, "sample_count": 3},
                    "execution_quality": {
                        "capture_minus_adverse": -25.0,
                        "realized_capture": 5.0,
                        "adverse_selection": 30.0,
                        "fills_scored": 1.0,
                    },
                },
            )
            _write_json(run2 / "order_lifecycle_audit.json", {"ok": True, "finding_count": 1, "warning_count": 0, "fill_without_submit_order_ids": ["missing-submit"], "cancel_without_submit_order_ids": [], "duplicate_fill_trade_ids": ["dup"], "duplicate_order_submit_ids": [], "lifecycle_counts": {"fill": 1}})
            _write_json(run2 / "outcome_truth_audit.json", {"attribution_usability_ratio": 0.5, "filled_complete_ratio": 0.0, "complete_classification_ratio": 0.0, "record_claim_boundary_distribution": {"unknown": 1}, "outcome_truth_status_distribution": {"unknown_missing_data": 1}, "slippage_summary": {"mean": 0.0}, "adverse_selection_summary": {"mean": 0.0}})
            _write_json(run2 / "soak_hardening_gate.json", {"ok": False, "readiness": {"blocking_stage": "paper", "highest_passing_stage": "smoke"}, "websocket": {"ok": False, "finding_count": 1, "metrics": {"chainlink_down_ratio": 0.4, "chainlink_last_tick_age_p95_sec": 20.0, "book_feed_down_ratio": 0.5, "book_feed_last_msg_age_p95_sec": 25.0}}, "integrity": {"ok": False, "finding_count": 1, "warning_count": 0, "fill_event_count": 0, "cancel_all_event_count": 0, "duplicate_fill_trade_id_count": 1, "event_row_count": 10, "status_row_count": 3}})

            _write_json(
                run3 / "validation_summary.json",
                {
                    "run_id": "run-gamma",
                    "session_phase": "validate_postrun",
                    "overall_exit_code": 0,
                    "ok": True,
                    "validator_determinism_ok": True,
                    "edge_truth_determinism_ok": True,
                    "non_edge_determinism_ok": True,
                    "outcome_truth_usability": "medium",
                },
            )
            _write_json(
                run3 / "nightly_soak_report.json",
                {
                    "artifact_identity": {"profile_name": "paper_universal", "run_id": "nightly-run-gamma"},
                    "session_phase": "validate_postrun",
                    "runtime_classification": {
                        "classification": "VALID_ACTIVE",
                        "primary_suppression_cause": "none",
                        "promotion_eligible": True,
                        "metrics": {
                            "active_targets_seen": 1.0,
                            "meaningful_participation": 1.0,
                            "decision_events": 7.0,
                            "required_book_feed_disconnected_rows": 0.0,
                        },
                    },
                    "duration_minutes": 9.0,
                    "error_rows": 0.0,
                    "quote_uptime_ratio": 0.15,
                    "quote_diagnostics": {"quote_active_ratio": 0.1, "participation_ratio": 0.1},
                    "execution_paths": {
                        "maker_submits": 1.0,
                        "maker_fills": 1.0,
                        "maker_filled_orders": 1.0,
                        "maker_fill_rate": 1.0,
                        "maker_fire_rate_per_min": 0.1,
                        "taker_bonus_submits": 1.0,
                        "taker_bonus_fills": 1.0,
                        "taker_bonus_filled_orders": 1.0,
                        "taker_bonus_fill_rate": 1.0,
                        "taker_bonus_fire_rate_per_min": 0.1,
                    },
                    "maker_competitiveness": {"timing_gate_blocked_count_decision": 0.0, "timing_gate_blocked_count_edge_eval": 0.0},
                    "edge_truth": {
                        "maker_reference_direct_midpoint_activity": 2.0,
                        "maker_reference_bounded_fallback_activity": 2.0,
                        "maker_reference_direct_midpoint_action_activity": 1.0,
                        "maker_reference_bounded_fallback_action_activity": 1.0,
                        "maker_no_submission_cause_distribution": {},
                        "maker_block_reason_distribution": {},
                    },
                    "maker_regression_sentinel": {"triggered": False, "maker_behavior_freeze_state": "provisional", "watch_item_primary": "none"},
                    "taker_competitiveness": {
                        "decision_count": 2.0,
                        "decision_to_submit_rate": 0.5,
                        "submit_capable_to_submit_rate": 0.5,
                        "submit_capable_dynamic_to_submit_rate": 0.5,
                        "blocked_decision_count": 0.0,
                        "risk_reject_after_capable_count_edge_eval": 0.0,
                        "fill_without_submit_stage_count": 0.0,
                        "hidden_blockage_detector": {"decision_to_dynamic_predicted_delta": 0.0, "dynamic_predicted_to_submit_delta": 0.0, "submit_to_fill_delta": 0.0},
                        "decision_timing_window_distribution": {"final_window": 1, "outside_window": 1},
                        "submit_timing_window_distribution": {"final_window": 1},
                        "fill_stage_distribution": {"SNIPER_PRIMARY": 1},
                        "decision_predicted_reject_reason_distribution": {},
                        "stage_final_risk_reject_reason_distribution": {},
                    },
                    "reduce_only_recovery": {
                        "edge_waiting_for_maker_exit_rows": 0.0,
                        "local_size_cap_classification": "flat_or_wrong_side_only",
                        "local_size_cap_nonflat_or_unknown_rows": 0.0,
                        "local_size_cap_flat_or_wrong_side_rows": 0.0,
                        "local_size_cap_unavailable_rows": 0.0,
                        "local_reject_lane_distribution": {},
                    },
                    "wallet_authority": {
                        "authority_status_class": "authoritative",
                        "authoritative_wallet_contract_present": True,
                        "reservation_mismatch_candidate": False,
                        "reservation_mismatch_delta_usdc": 0.0,
                        "live_truth_gap_reasons": [],
                        "wallet_contract_surface_source": "wallet_contract",
                        "latest_contract": {
                            "order_capable_live": True,
                            "order_submit_eligible": True,
                            "deployable_capital": 200.0,
                            "stable_balance_total": 400.0,
                            "open_reserved": 20.0,
                            "protected_reserve": 30.0,
                        },
                    },
                    "risk_competitiveness": {
                        "global_exposure_cap_reject_count": 0.0,
                        "global_exposure_near_cap_count": 0.0,
                        "global_exposure_utilization_ratio_max": 0.2,
                        "global_exposure_utilization_ratio_p50": 0.1,
                        "global_exposure_utilization_ratio_p90": 0.15,
                        "reject_reason_distribution": {},
                        "reject_count_by_lane": {},
                    },
                    "valuation_truth": {
                        "valuation_degraded_ratio": 0.1,
                        "valuation_hard_degraded_ratio": 0.0,
                        "valuation_bruise_state": "none",
                        "valuation_dominant_reason_family_run": "none",
                        "valuation_dominant_held_unpriceable_cause_run": "none",
                        "valuation_dominant_source_degraded_rows": "none",
                        "valuation_degraded_reason_family_counts_run": {},
                        "valuation_source_counts_degraded_rows": {
                            "live_mid": 0.0,
                            "live_side_conservative_quote": 0.0,
                            "last_known_mid": 0.0,
                            "conservative_bound_hard_degraded": 0.0,
                            "hard_degraded": 0.0,
                        },
                        "valuation_hard_degraded_enter_count": 0.0,
                        "valuation_hard_degraded_clear_count": 0.0,
                        "held_unpriceable_unrecovered_meaningful_count": 0.0,
                        "held_unpriceable_unrecovered_dust_exempted_count": 0.0,
                        "held_unpriceable_escalation_ratio": 0.0,
                        "preexpiry_emergency_taker_attempt_count": 0.0,
                        "preexpiry_emergency_taker_block_count": 0.0,
                        "preexpiry_emergency_taker_fill_count": 0.0,
                        "preexpiry_emergency_taker_block_reason_counts": {},
                    },
                    "market_data_source": {
                        "book_updates_rest_ratio": 0.2,
                        "book_updates_ws_delta": 80.0,
                        "book_updates_rest_delta": 20.0,
                        "book_updates_total_delta": 100.0,
                    },
                    "stale_data": {"disarmed_edge_blocks": 0.0},
                    "secondary_oracle_pyth": {"connected_ratio_when_enabled": 1.0, "unavailable_sample_count": 0.0},
                    "latency_distribution_ms": {"median_ms": 100.0, "p90_ms": 200.0, "p95_ms": 250.0, "sample_count": 2},
                    "execution_quality": {"capture_minus_adverse": 1.0, "realized_capture": 2.0, "adverse_selection": 1.0, "fills_scored": 1.0},
                },
            )
            _write_json(run3 / "order_lifecycle_audit.json", {"ok": True, "finding_count": 0, "warning_count": 0, "fill_without_submit_order_ids": [], "cancel_without_submit_order_ids": [], "duplicate_fill_trade_ids": [], "duplicate_order_submit_ids": [], "lifecycle_counts": {"fill": 1}})
            _write_json(run3 / "outcome_truth_audit.json", {"attribution_usability_ratio": 1.0, "filled_complete_ratio": 1.0, "complete_classification_ratio": 1.0, "record_claim_boundary_distribution": {"complete": 1}, "outcome_truth_status_distribution": {"complete": 1}, "slippage_summary": {"mean": 0.0}, "adverse_selection_summary": {"mean": 0.0}})
            _write_jsonl(
                run3 / "outcome_truth_records.jsonl",
                [
                    {
                        "order_submit_id": "gamma-order-1",
                        "submission_lane_truth": "maker",
                        "submission_scope_hint": "maker",
                        "target_ref": "target-g",
                        "outcome_truth_status": "complete",
                        "fill_count": 2,
                        "decision_quality": "incorrect",
                        "decision_component_x_size": -3.0,
                        "execution_component_x_size": 0.5,
                        "decision_reference_basis": "direct_book_midpoint",
                        "eval_reference_basis": "edge_market_midpoint_series",
                        "evaluation_horizon_ms": 5000,
                        "order_side": "BUY",
                        "mid_price_decision": 0.3,
                        "mid_price_eval": 0.35,
                        "edge_expected": -0.05,
                        "fill_total_size": 40.0,
                    }
                ],
            )
            _write_json(run3 / "soak_hardening_gate.json", {"ok": True, "readiness": {"blocking_stage": "pilot_live", "highest_passing_stage": "paper"}, "websocket": {"ok": True, "finding_count": 0, "metrics": {"chainlink_down_ratio": 0.0, "chainlink_last_tick_age_p95_sec": 0.5, "book_feed_down_ratio": 0.0, "book_feed_last_msg_age_p95_sec": 0.5}}, "integrity": {"ok": True, "finding_count": 0, "warning_count": 0, "fill_event_count": 1, "cancel_all_event_count": 0, "duplicate_fill_trade_id_count": 0, "event_row_count": 50, "status_row_count": 5}})

            outputs = bro_metric_harvest.harvest_reports(report_root=report_root, out_dir=out_dir)

            for output_path in outputs.values():
                self.assertTrue(output_path.exists(), msg=f"missing output artifact: {output_path}")

            rows = [json.loads(line) for line in (out_dir / "run_index.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 3)
            alpha = next(row for row in rows if row["run_id"] == "run-alpha")
            beta = next(row for row in rows if row["run_id"] == "run-beta")
            gamma = next(row for row in rows if row["run_id"] == "run-gamma")
            self.assertEqual(alpha["maker_submits"], 10.0)
            self.assertEqual(alpha["wallet_deployable_capital"], 650.5)
            self.assertEqual(alpha["validation_status"], "pass")
            self.assertEqual(alpha["validation_status_source"], "derived")
            self.assertEqual(alpha["valuation_bruise_state"], "recovered_clean")
            self.assertEqual(alpha["valuation_dominant_reason_family_run"], "degraded_using_last_known_mid")
            self.assertEqual(alpha["taker_final_window_decision_count"], 20)
            self.assertEqual(alpha["maker_quote_quality_skip_total_count"], 0.0)
            self.assertEqual(alpha["maker_sizing_reject_total_count"], 0.0)
            self.assertEqual(alpha["maker_window_active_row_count"], 10.0)
            self.assertEqual(alpha["maker_window_submit_count"], 4.0)
            self.assertEqual(alpha["maker_window_quote_quality_skip_total_count"], 3.0)
            self.assertEqual(alpha["maker_window_sizing_reject_count"], 1.0)
            self.assertAlmostEqual(alpha["maker_window_submit_rate"], 0.4)
            self.assertAlmostEqual(alpha["maker_window_replace_guard_rate"], 0.3)
            self.assertAlmostEqual(alpha["maker_window_quote_quality_skip_rate"], 0.3)
            self.assertAlmostEqual(alpha["maker_window_sizing_reject_rate"], 0.1)
            self.assertAlmostEqual(alpha["maker_window_low_price_viability_floor"], 0.04375)
            self.assertEqual(alpha["maker_window_viable_row_count"], 8.0)
            self.assertEqual(alpha["maker_window_impossible_row_count"], 2.0)
            self.assertEqual(alpha["maker_min_notional_max_shares_conflict_rows"], 1.0)
            self.assertEqual(alpha["maker_window_queue_depth_on_impossible_targets_count"], 1.0)
            self.assertEqual(alpha["maker_raw_queue_depth_near_threshold_event_count"], 1.0)
            self.assertEqual(alpha["maker_window_viability_target_summary"][0]["target_ref"], "target-b")
            self.assertEqual(alpha["maker_window_queue_depth_target_summary"][0]["target_ref"], "target-b")
            self.assertEqual(alpha["maker_complete_record_count"], 3)
            self.assertEqual(alpha["maker_incomplete_record_count"], 1)
            self.assertAlmostEqual(alpha["maker_complete_bad_ratio"], 2.0 / 3.0)
            self.assertEqual(alpha["maker_multifill_complete_count"], 2)
            self.assertEqual(alpha["maker_multifill_complete_incorrect_ratio"], 1.0)
            self.assertEqual(alpha["maker_same_target_repeat_cluster_count"], 1)
            self.assertEqual(alpha["maker_complement_pair_cluster_count"], 1)
            self.assertEqual(alpha["maker_outcome_horizon_ms"], 5000)
            self.assertTrue(alpha["maker_eval_basis_requires_reconstructed_midpoint_flag"])
            self.assertEqual(beta["runtime_primary_suppression_cause"], "timing_gate")
            self.assertEqual(beta["lifecycle_fill_without_submit_count"], 1)
            self.assertTrue(beta["wallet_reservation_mismatch_candidate"])
            self.assertEqual(beta["validation_status"], "fail")
            self.assertEqual(beta["validation_policy_failed"], None)
            self.assertEqual(beta["valuation_bruise_state"], "open_meaningful_unpriceable")
            self.assertEqual(beta["valuation_dominant_held_unpriceable_cause_run"], "preexpiry_fetch_failure")
            self.assertEqual(beta["maker_no_submit_total_count"], 5.0)
            self.assertEqual(beta["risk_reject_total_count"], 10.0)
            self.assertEqual(beta["maker_lifecycle_gap_class_distribution"]["partial_fill_incomplete"], 1.0)
            self.assertEqual(gamma["run_id_source"], "validation_summary.json")
            self.assertEqual(gamma["runtime_classification"], "VALID_ACTIVE")
            self.assertEqual(gamma["runtime_classification_source"], "nightly_soak_report.json")
            self.assertEqual(gamma["validation_status_source"], "derived")
            self.assertEqual(gamma["valuation_bruise_state"], "none")
            self.assertEqual(gamma["taker_outside_window_decision_count"], 1)
            self.assertEqual(gamma["taker_final_window_decision_ratio"], 0.5)
            self.assertEqual(gamma["market_data_ws_ratio"], 0.8)
            self.assertEqual(gamma["wallet_reserved_ratio"], 0.05)
            self.assertEqual(gamma["maker_reference_fallback_ratio"], 0.5)
            self.assertEqual(gamma["outcome_truth_attribution_usability_ratio"], 1.0)

            catalog = json.loads((out_dir / "metric_catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["tool_alias"], "FMA")
            self.assertEqual(catalog["source_count"], 7)
            self.assertGreater(catalog["total_metric_keys"], 0)
            self.assertIn("nightly_soak_report.json", catalog["sources"])
            self.assertIn("execution_paths.maker_submits", catalog["sources"]["nightly_soak_report.json"]["paths"])
            self.assertIn("presence_ratio", catalog["sources"]["nightly_soak_report.json"]["paths"]["execution_paths.maker_submits"])

            anomaly_summary = json.loads((out_dir / "anomaly_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(anomaly_summary["flag_counts"]["wallet_non_authoritative"], 1)
            self.assertEqual(anomaly_summary["runtime_classification_counts"]["VALID_ACTIVE"], 2)
            self.assertEqual(anomaly_summary["wallet_authority_status_counts"]["authoritative"], 2)
            self.assertEqual(anomaly_summary["engineer_focus"]["run_scope"], "corpus")
            self.assertEqual(anomaly_summary["engineer_focus"]["maker_quote_quality_skip_total_count"], 0.0)
            self.assertEqual(anomaly_summary["engineer_focus"]["maker_window_active_row_count_total"], 10.0)
            self.assertEqual(anomaly_summary["engineer_focus"]["maker_window_sizing_reject_count_total"], 1.0)
            self.assertEqual(anomaly_summary["engineer_focus"]["maker_window_impossible_row_count_total"], 2.0)
            self.assertEqual(anomaly_summary["engineer_focus"]["risk_reject_total_count"], 19.0)
            self.assertEqual(anomaly_summary["engineer_focus"]["valuation_bruise_open_run_count"], 1)
            self.assertIn("maker_truth_population_note", anomaly_summary)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_complete_record_count_total"], 5.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_window_active_row_count_total"], 10.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_window_submit_count_total"], 4.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_window_quote_quality_skip_total_count"], 3.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_window_sizing_reject_count_total"], 1.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_window_impossible_row_count_total"], 2.0)
            self.assertEqual(
                anomaly_summary["maker_forensics"]["maker_min_notional_max_shares_conflict_rows_total"],
                1.0,
            )
            self.assertAlmostEqual(
                anomaly_summary["maker_forensics"]["maker_window_submit_rate_summary"]["mean"],
                0.4,
            )
            self.assertEqual(
                anomaly_summary["maker_forensics"]["maker_quote_quality_skip_fill_probability_severity_bins"]["within_0p005"],
                1,
            )
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_lifecycle_gap_class_counts"]["complete_multifill"], 3.0)
            self.assertEqual(anomaly_summary["maker_forensics"]["maker_reference_basis_summary"]["eval_reference_basis_distribution"]["edge_market_midpoint_series"], 7)
            self.assertEqual(anomaly_summary["aggregates"]["valuation_bruise_state_counts"]["recovered_clean"], 1)
            self.assertEqual(anomaly_summary["aggregates"]["valuation_bruise_state_counts"]["open_meaningful_unpriceable"], 1)
            self.assertEqual(anomaly_summary["aggregates"]["valuation_degraded_reason_family_counts"]["hard_degraded"], 1.0)
            self.assertEqual(
                anomaly_summary["aggregates"]["valuation_held_unpriceable_cause_counts"]["preexpiry_fetch_failure"],
                1.0,
            )
            self.assertEqual(anomaly_summary["aggregates"]["taker_decision_window_counts"]["final_window"], 31)
            self.assertEqual(anomaly_summary["aggregates"]["runtime_classification_counts"]["VALID_ACTIVE"], 2)
            self.assertEqual(anomaly_summary["coverage"]["field_coverage"]["runtime_classification"]["present_count"], 3)

            summary_csv = (out_dir / "maker_taker_summary.csv").read_text(encoding="utf-8")
            self.assertIn("wallet_deployable_capital", summary_csv)
            self.assertIn("taker_final_window_decision_count", summary_csv)
            self.assertIn("run_id_source", summary_csv)
            self.assertIn("market_data_ws_ratio", summary_csv)
            self.assertIn("validation_status", summary_csv)
            self.assertIn("valuation_bruise_state", summary_csv)
            self.assertIn("maker_quote_quality_skip_total_count", summary_csv)
            self.assertIn("maker_window_submit_rate", summary_csv)
            self.assertIn("maker_complete_bad_ratio", summary_csv)
            self.assertIn("maker_complement_pair_cluster_count", summary_csv)

            research_pack = (out_dir / "maker_research_pack.md").read_text(encoding="utf-8")
            self.assertIn("Forge Masters Archiver", research_pack)
            self.assertIn("Validation / Authority Quick Read", research_pack)
            self.assertIn("Money / Authority Surfaces", research_pack)
            self.assertIn("Maker Fireability / Window Surfaces", research_pack)
            self.assertIn("Valuation / Bruise Surfaces", research_pack)
            self.assertIn("Coverage / Thin Spots", research_pack)
            self.assertIn("Engineer focus scope", research_pack)
            self.assertIn("Maker Outcome / Lifecycle Surfaces", research_pack)
            self.assertIn("Maker Truth Population Guardrails", research_pack)

            manifest = json.loads((out_dir / "fma_bundle_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["tool_id"], "FM-1A1")
            self.assertTrue(manifest["bundle_closed_snapshot"])
            self.assertEqual(manifest["selected_run_count"], 3)
            self.assertIn("run_index.jsonl", manifest["artifact_files"])
            self.assertIn("maker_research_pack.md", manifest["artifact_files"])
            self.assertEqual(
                manifest["artifact_files"]["run_index.jsonl"]["output_key"],
                "run_index_jsonl",
            )

    def test_harvest_reports_writes_maker_admission_shadow_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            report_root = root / "reports"
            report_root.mkdir()
            out_dir = root / "out"
            run1 = report_root / "run-shadow"
            run1.mkdir()

            _write_json(run1 / "validation_summary.json", {"run_id": "run-alpha", "overall_exit_code": 0, "ok": True})
            _write_json(
                run1 / "canonical_paper_validation.json",
                {"run_id": "run-alpha", "gate_passed": True, "reports_complete": True, "runtime_classification": "VALID_ACTIVE"},
            )
            _write_json(
                run1 / "nightly_soak_report.json",
                {
                    "artifact_identity": {"profile_name": "paper_universal"},
                    "run_contract_path": str(root / "run_contract_run-alpha.json"),
                    "runtime_classification": {"classification": "VALID_ACTIVE", "primary_suppression_cause": "none"},
                    "execution_paths": {
                        "maker_submits": 1.0,
                        "maker_fills": 1.0,
                        "maker_filled_orders": 1.0,
                        "maker_fill_rate": 1.0,
                    },
                    "maker_competitiveness": {"timing_gate_blocked_count_decision": 0.0, "timing_gate_blocked_count_edge_eval": 0.0},
                    "edge_truth": {"maker_no_submission_cause_distribution": {}, "maker_block_reason_distribution": {}},
                    "maker_fireability": {
                        "active_window_row_count": 1.0,
                        "active_window_submit_count": 1.0,
                        "active_window_replace_guard_count": 0.0,
                        "active_window_quote_quality_skip_fill_probability_count": 0.0,
                        "active_window_quote_quality_skip_queue_depth_count": 0.0,
                        "active_window_sizing_reject_count": 0.0,
                        "active_window_viable_row_count": 1.0,
                        "active_window_impossible_row_count": 0.0,
                    },
                },
            )
            _write_json(
                root / "run_contract_run-alpha.json",
                {
                    "run_id": "run-alpha",
                    "start_ts": "2026-04-28T13:00:00Z",
                    "stop_ts": "2026-04-28T13:05:00Z",
                    "session_type": "paper_canonical",
                    "log_root": str(root),
                },
            )
            _write_json(
                run1 / "maker_fight_admission_shadow_summary.json",
                {
                    "admission_rubric_version": 1,
                    "maker_cannon_shadow_version": 1,
                    "population_class_counts": {"candidate": 2, "external_blocked": 0, "truth_thin": 0},
                    "admission_class_counts": {"clean": 1, "borderline": 0, "trash": 1},
                    "submit_rate_by_class": {"clean": 1.0, "borderline": 0.0, "trash": 1.0},
                    "complete_joined_count_by_class": {"clean": 1, "borderline": 0, "trash": 1},
                    "complete_bad_ratio_by_class": {"clean": 0.0, "borderline": 0.0, "trash": 1.0},
                    "multifill_incorrect_ratio_by_class": {"clean": 0.0, "borderline": 0.0, "trash": 1.0},
                    "dominant_driver_distribution": {"queue_delta_gt_50": 1, "queue_pressure": 1},
                    "top_trash_target_side_ref_counts": {"target-b|SELL": 1},
                    "top_clean_target_side_ref_counts": {"target-a|BUY": 1},
                    "cannon_window_class_distribution": {"10_to_15s": 1, "gt_20s": 1},
                    "maker_timing_band_class_distribution": {"10_to_15s": 1, "20_to_30s": 1},
                    "candidate_count_by_timing_band": {"10_to_15s": 1, "20_to_30s": 1},
                    "admission_class_distribution_by_timing_band": {
                        "10_to_15s": {"clean": 1, "borderline": 0, "trash": 0},
                        "20_to_30s": {"clean": 0, "borderline": 0, "trash": 1},
                    },
                    "submitted_count_by_timing_band": {"10_to_15s": 1, "20_to_30s": 1},
                    "complete_joined_count_by_timing_band": {"10_to_15s": 1, "20_to_30s": 1},
                    "complete_bad_ratio_by_timing_band": {"10_to_15s": 0.0, "20_to_30s": 1.0},
                    "multifill_incorrect_ratio_by_timing_band": {"10_to_15s": 0.0, "20_to_30s": 1.0},
                    "session_regime_class_distribution": {
                        "asia_dominant_heuristic": 1,
                        "usa_europe_peak_heuristic": 1,
                    },
                    "stack_pressure_class_distribution": {"below_soft_cap": 1, "within_hard_cap": 1},
                    "secondary_oracle_status_distribution": {"confirmed": 1, "direction_mismatch": 1},
                    "secondary_oracle_confirmation_distribution": {"confirmed": 1, "not_confirmed": 1},
                    "cannon_depth_requirement_counts": {"met": 1, "not_met": 1},
                    "depth_multiple_vs_cannon_target_summary": {"min": 0.6, "mean": 1.3, "max": 2.0},
                },
            )
            _write_json(
                run1 / "maker_fight_admission_calibration_audit.json",
                {
                    "admission_rubric_version": 1,
                    "maker_cannon_shadow_version": 1,
                    "outcome_truth_status_distribution_by_class": {"clean": {"complete": 1}, "trash": {"complete": 1}},
                    "claim_boundary_class_distribution_by_class": {"clean": {"complete": 1}, "trash": {"complete": 1}},
                    "evaluation_horizon_ms_distribution_by_class": {"clean": {"5000": 1}, "trash": {"5000": 1}},
                    "clean_but_bad_examples": [],
                    "trash_but_okay_examples": [],
                },
            )
            _write_json(
                run1 / "maker_cannon_late_window_probe_summary.json",
                {
                    "maker_cannon_probe_version": 3,
                    "population_class_counts": {"candidate": 2, "external_blocked": 0, "truth_thin": 0},
                    "full_cannon_candidate_count": 1,
                    "latent_market_truth_class_counts": {"evaluable": 2},
                    "latent_market_full_cannon_candidate_count": 1,
                    "latent_market_full_candidate_population_class_distribution": {"candidate": 1},
                    "latent_market_reject_reason_distribution": {"secondary_oracle_direction_mismatch": 1},
                    "latent_market_dominant_reject_reason_distribution": {"secondary_oracle_direction_mismatch": 1},
                    "external_blocked_latent_market_evaluable_count": 0,
                    "external_blocked_latent_market_full_cannon_candidate_count": 0,
                    "external_blocked_latent_market_reject_reason_distribution": {},
                    "full_candidate_runtime_stage_disallow_count": 1,
                    "reject_reason_distribution": {"secondary_oracle_direction_mismatch": 1},
                    "stage_distribution": {"MAKER_TAKER_SELECTIVE": 2},
                    "financial_posture_class_distribution": {"NORMAL": 2},
                    "cannon_window_class_distribution": {"10_to_15s": 1, "15_to_20s": 1},
                    "session_regime_class_distribution": {
                        "asia_dominant_heuristic": 1,
                        "usa_europe_peak_heuristic": 1,
                    },
                    "stack_pressure_class_distribution": {"below_soft_cap": 1, "within_hard_cap": 1},
                    "secondary_oracle_status_distribution": {"confirmed": 1, "direction_mismatch": 1},
                    "secondary_oracle_confirmation_distribution": {"confirmed": 1, "not_confirmed": 1},
                    "runtime_maker_stage_allowed_distribution": {"allowed": 1, "disallowed": 1},
                    "geometry_viable_counts": {"not_viable": 1, "viable": 1},
                    "cannon_depth_requirement_counts": {"met": 1, "not_met": 1},
                    "depth_multiple_vs_cannon_target_summary": {"min": 0.6, "mean": 1.3, "max": 2.0},
                    "total_maker_edge_eval_rows": 6,
                    "late_window_raw_row_count": 2,
                    "ignored_non_late_window_row_count": 4,
                },
            )
            _write_json(
                run1 / "maker_mid_window_probe_summary.json",
                {
                    "maker_mid_window_probe_version": 1,
                    "population_class_counts": {"candidate": 1, "external_blocked": 1, "truth_thin": 0},
                    "full_mid_window_candidate_count": 1,
                    "latent_market_truth_class_counts": {"evaluable": 2},
                    "latent_market_full_mid_window_candidate_count": 1,
                    "latent_market_full_candidate_population_class_distribution": {"candidate": 1},
                    "latent_market_reject_reason_distribution": {"insufficient_depth_multiple": 1},
                    "latent_market_dominant_reject_reason_distribution": {"insufficient_depth_multiple": 1},
                    "external_blocked_latent_market_evaluable_count": 1,
                    "external_blocked_latent_market_full_candidate_count": 0,
                    "external_blocked_latent_market_reject_reason_distribution": {
                        "insufficient_depth_multiple": 1
                    },
                    "full_candidate_runtime_stage_disallow_count": 0,
                    "reject_reason_distribution": {"insufficient_depth_multiple": 1},
                    "stage_distribution": {"OBSERVE": 2},
                    "market_reference_class_distribution": {"authoritative": 2},
                    "market_reference_mode_distribution": {"direct_midpoint": 2},
                    "market_reference_source_side_distribution": {"mid": 2},
                    "market_probability_band_distribution": {"mid_band": 2},
                    "favored_side_depth_class_distribution": {"positive": 2},
                    "financial_posture_class_distribution": {"HALT_NEW_RISK": 1, "NORMAL": 1},
                    "maker_timing_band_class_distribution": {"20_to_30s": 1, "30_to_45s": 1},
                    "session_regime_class_distribution": {
                        "transition_heuristic": 1,
                        "usa_europe_peak_heuristic": 1,
                    },
                    "stack_pressure_class_distribution": {"below_soft_cap": 2},
                    "secondary_oracle_status_distribution": {"confirmed": 2},
                    "secondary_oracle_confirmation_distribution": {"confirmed": 2},
                    "runtime_secondary_oracle_status_distribution": {"confirmed": 2},
                    "runtime_secondary_oracle_confirmation_distribution": {"confirmed": 2},
                    "runtime_maker_stage_allowed_distribution": {"allowed": 2},
                    "probe_visible_depth_fail_closed_zero_distribution": {"reported_or_not_needed": 2},
                    "geometry_viable_counts": {"viable": 2},
                    "cannon_depth_requirement_counts": {"met": 1, "not_met": 1},
                    "depth_multiple_vs_cannon_target_summary": {"min": 1.0, "mean": 1.4, "max": 1.8},
                    "total_maker_edge_eval_rows": 8,
                    "mid_window_raw_row_count": 2,
                    "ignored_non_mid_window_row_count": 6,
                },
            )
            _write_jsonl(
                run1 / "maker_fight_admission_shadow.jsonl",
                [
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-a|BUY",
                        "population_class": "candidate",
                        "admission_class": "clean",
                        "decision_result": "submitted",
                        "outcome_truth_status": "complete",
                        "decision_quality": "correct",
                        "dominant_driver": "queue_pressure",
                        "maker_cannon_shadow_version": 1,
                        "cannon_window_class": "10_to_15s",
                        "maker_timing_band_class": "10_to_15s",
                        "session_regime_class": "usa_europe_peak_heuristic",
                        "stack_pressure_class": "below_soft_cap",
                        "secondary_oracle_status": "confirmed",
                        "secondary_oracle_confirmation": True,
                        "cannon_depth_requirement_met": True,
                        "depth_multiple_vs_cannon_target": 2.0,
                    },
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-b|SELL",
                        "population_class": "candidate",
                        "admission_class": "trash",
                        "decision_result": "submitted",
                        "outcome_truth_status": "complete",
                        "decision_quality": "incorrect",
                        "dominant_driver": "queue_delta_gt_50",
                        "maker_cannon_shadow_version": 1,
                        "cannon_window_class": "gt_20s",
                        "maker_timing_band_class": "20_to_30s",
                        "session_regime_class": "asia_dominant_heuristic",
                        "stack_pressure_class": "within_hard_cap",
                        "secondary_oracle_status": "direction_mismatch",
                        "secondary_oracle_confirmation": False,
                        "cannon_depth_requirement_met": False,
                        "depth_multiple_vs_cannon_target": 0.6,
                    },
                ],
            )
            _write_jsonl(
                run1 / "maker_cannon_late_window_probe.jsonl",
                [
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-a|BUY",
                        "population_class": "candidate",
                        "full_cannon_candidate": True,
                        "stage": "MAKER_TAKER_SELECTIVE",
                        "financial_posture_class": "NORMAL",
                        "cannon_window_class": "10_to_15s",
                        "session_regime_class": "usa_europe_peak_heuristic",
                        "stack_pressure_class": "below_soft_cap",
                        "secondary_oracle_status": "confirmed",
                        "secondary_oracle_confirmation": True,
                        "runtime_maker_stage_allowed": False,
                        "geometry_viable": True,
                        "cannon_depth_requirement_met": True,
                        "depth_multiple_vs_cannon_target": 2.0,
                        "latent_market_truth_class": "evaluable",
                        "latent_market_candidate": True,
                        "latent_market_full_cannon_candidate": True,
                        "latent_market_reject_reasons": [],
                        "latent_market_dominant_reject_reason": None,
                        "maker_cannon_probe_version": 3,
                        "total_maker_edge_eval_rows": 6,
                        "late_window_raw_row_count": 2,
                        "ignored_non_late_window_row_count": 4,
                    },
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-b|SELL",
                        "population_class": "candidate",
                        "full_cannon_candidate": False,
                        "reject_reasons": ["secondary_oracle_direction_mismatch"],
                        "stage": "MAKER_TAKER_SELECTIVE",
                        "financial_posture_class": "NORMAL",
                        "cannon_window_class": "15_to_20s",
                        "session_regime_class": "asia_dominant_heuristic",
                        "stack_pressure_class": "within_hard_cap",
                        "secondary_oracle_status": "direction_mismatch",
                        "secondary_oracle_confirmation": False,
                        "runtime_maker_stage_allowed": True,
                        "geometry_viable": False,
                        "cannon_depth_requirement_met": False,
                        "depth_multiple_vs_cannon_target": 0.6,
                        "latent_market_truth_class": "evaluable",
                        "latent_market_candidate": True,
                        "latent_market_full_cannon_candidate": False,
                        "latent_market_reject_reasons": ["secondary_oracle_direction_mismatch"],
                        "latent_market_dominant_reject_reason": "secondary_oracle_direction_mismatch",
                        "maker_cannon_probe_version": 3,
                        "total_maker_edge_eval_rows": 6,
                        "late_window_raw_row_count": 2,
                        "ignored_non_late_window_row_count": 4,
                    },
                ],
            )
            _write_jsonl(
                run1 / "maker_mid_window_probe.jsonl",
                [
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-c|BUY",
                        "population_class": "candidate",
                        "full_mid_window_candidate": True,
                        "stage": "OBSERVE",
                        "market_reference_class": "authoritative",
                        "market_reference_mode": "direct_midpoint",
                        "market_reference_source_side": "mid",
                        "market_probability_band": "mid_band",
                        "favored_side_depth_class": "positive",
                        "financial_posture_class": "NORMAL",
                        "maker_timing_band_class": "20_to_30s",
                        "session_regime_class": "usa_europe_peak_heuristic",
                        "stack_pressure_class": "below_soft_cap",
                        "secondary_oracle_status": "confirmed",
                        "secondary_oracle_confirmation": True,
                        "runtime_secondary_oracle_status": "confirmed",
                        "runtime_secondary_oracle_confirmation": True,
                        "runtime_maker_stage_allowed": True,
                        "probe_visible_depth_fail_closed_zero": "reported_or_not_needed",
                        "geometry_viable": True,
                        "cannon_depth_requirement_met": True,
                        "depth_multiple_vs_cannon_target": 1.8,
                        "latent_market_truth_class": "evaluable",
                        "latent_market_candidate": True,
                        "latent_market_full_mid_window_candidate": True,
                        "latent_market_reject_reasons": [],
                        "latent_market_dominant_reject_reason": None,
                        "maker_mid_window_probe_version": 1,
                        "total_maker_edge_eval_rows": 8,
                        "mid_window_raw_row_count": 2,
                        "ignored_non_mid_window_row_count": 6,
                    },
                    {
                        "run_id": "run-alpha",
                        "target_side_ref": "target-d|SELL",
                        "population_class": "external_blocked",
                        "full_mid_window_candidate": False,
                        "reject_reasons": ["insufficient_depth_multiple"],
                        "stage": "OBSERVE",
                        "market_reference_class": "authoritative",
                        "market_reference_mode": "direct_midpoint",
                        "market_reference_source_side": "mid",
                        "market_probability_band": "mid_band",
                        "favored_side_depth_class": "positive",
                        "financial_posture_class": "HALT_NEW_RISK",
                        "maker_timing_band_class": "30_to_45s",
                        "session_regime_class": "transition_heuristic",
                        "stack_pressure_class": "below_soft_cap",
                        "secondary_oracle_status": "confirmed",
                        "secondary_oracle_confirmation": True,
                        "runtime_secondary_oracle_status": "confirmed",
                        "runtime_secondary_oracle_confirmation": True,
                        "runtime_maker_stage_allowed": True,
                        "probe_visible_depth_fail_closed_zero": "reported_or_not_needed",
                        "geometry_viable": True,
                        "cannon_depth_requirement_met": False,
                        "depth_multiple_vs_cannon_target": 1.0,
                        "latent_market_truth_class": "evaluable",
                        "latent_market_candidate": True,
                        "latent_market_full_mid_window_candidate": False,
                        "latent_market_reject_reasons": ["insufficient_depth_multiple"],
                        "latent_market_dominant_reject_reason": "insufficient_depth_multiple",
                        "maker_mid_window_probe_version": 1,
                        "total_maker_edge_eval_rows": 8,
                        "mid_window_raw_row_count": 2,
                        "ignored_non_mid_window_row_count": 6,
                    },
                ],
            )
            _write_jsonl(
                run1 / "outcome_truth_records.jsonl",
                [
                    {
                        "order_submit_id": "order-1",
                        "submission_lane_truth": "maker",
                        "outcome_truth_status": "complete",
                        "decision_quality": "correct",
                    },
                ],
            )

            outputs = bro_metric_harvest.harvest_reports(report_root=report_root, out_dir=out_dir)
            self.assertTrue(outputs["maker_fight_admission_shadow_rows_jsonl"].exists())
            self.assertTrue(outputs["maker_fight_admission_shadow_summary_json"].exists())
            self.assertTrue(outputs["maker_fight_admission_calibration_audit_json"].exists())
            self.assertTrue(outputs["maker_admission_target_side_summary_json"].exists())
            self.assertTrue(outputs["maker_cannon_late_window_probe_rows_jsonl"].exists())
            self.assertTrue(outputs["maker_cannon_late_window_probe_summary_json"].exists())
            self.assertTrue(outputs["maker_cannon_probe_session_sweep_json"].exists())
            self.assertTrue(outputs["maker_mid_window_probe_rows_jsonl"].exists())
            self.assertTrue(outputs["maker_mid_window_probe_summary_json"].exists())
            self.assertTrue(outputs["maker_mid_window_probe_session_sweep_json"].exists())

            shadow_summary = json.loads(
                outputs["maker_fight_admission_shadow_summary_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(shadow_summary["row_count"], 2)
            self.assertEqual(shadow_summary["admission_rubric_version"], 1)
            self.assertEqual(shadow_summary["maker_cannon_shadow_version"], 1)
            self.assertEqual(
                shadow_summary["cannon_window_class_distribution"],
                {"10_to_15s": 1, "gt_20s": 1},
            )
            self.assertEqual(
                shadow_summary["maker_timing_band_class_distribution"],
                {"10_to_15s": 1, "20_to_30s": 1},
            )
            self.assertEqual(
                shadow_summary["complete_bad_ratio_by_timing_band"],
                {"10_to_15s": 0.0, "20_to_30s": 1.0},
            )
            calibration_audit = json.loads(
                outputs["maker_fight_admission_calibration_audit_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(calibration_audit["admission_rubric_version"], 1)
            self.assertEqual(calibration_audit["maker_cannon_shadow_version"], 1)
            self.assertEqual(
                calibration_audit["maker_timing_band_class_distribution"],
                {"10_to_15s": 1, "20_to_30s": 1},
            )
            cannon_probe_summary = json.loads(
                outputs["maker_cannon_late_window_probe_summary_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(cannon_probe_summary["maker_cannon_probe_version"], 3)
            self.assertEqual(cannon_probe_summary["full_cannon_candidate_count"], 1)
            self.assertEqual(cannon_probe_summary["latent_market_full_cannon_candidate_count"], 1)
            self.assertEqual(
                cannon_probe_summary["cannon_window_class_distribution"],
                {"10_to_15s": 1, "15_to_20s": 1},
            )
            target_side_summary = json.loads(outputs["maker_admission_target_side_summary_json"].read_text(encoding="utf-8"))
            self.assertEqual(target_side_summary[0]["target_side_ref"], "target-b|SELL")
            bundle_rows = [
                json.loads(line)
                for line in outputs["maker_fight_admission_shadow_rows_jsonl"].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(bundle_rows), 2)
            self.assertEqual(bundle_rows[0]["maker_cannon_shadow_version"], 1)
            self.assertEqual(bundle_rows[0]["maker_timing_band_class"], "10_to_15s")
            cannon_probe_rows = [
                json.loads(line)
                for line in outputs["maker_cannon_late_window_probe_rows_jsonl"].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(cannon_probe_rows), 2)
            self.assertEqual(cannon_probe_rows[0]["maker_cannon_probe_version"], 3)
            session_sweep = json.loads(
                outputs["maker_cannon_probe_session_sweep_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(session_sweep["run_count"], 1)
            peak_bucket = session_sweep["session_bucket_summary"]["usa_europe_peak_heuristic"]
            self.assertEqual(peak_bucket["run_count"], 1)
            self.assertEqual(peak_bucket["full_cannon_candidate_count"], 1)
            self.assertEqual(peak_bucket["latent_market_full_cannon_candidate_count"], 1)
            self.assertEqual(session_sweep["run_summaries"][0]["run_start_hour_utc"], 13)
            mid_window_summary = json.loads(
                outputs["maker_mid_window_probe_summary_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(mid_window_summary["maker_mid_window_probe_version"], 1)
            self.assertEqual(mid_window_summary["full_mid_window_candidate_count"], 1)
            self.assertEqual(
                mid_window_summary["maker_timing_band_class_distribution"],
                {"20_to_30s": 1, "30_to_45s": 1},
            )
            mid_window_rows = [
                json.loads(line)
                for line in outputs["maker_mid_window_probe_rows_jsonl"].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(mid_window_rows), 2)
            self.assertEqual(mid_window_rows[0]["maker_mid_window_probe_version"], 1)
            mid_window_session_sweep = json.loads(
                outputs["maker_mid_window_probe_session_sweep_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(mid_window_session_sweep["run_count"], 1)
            peak_bucket_mid = mid_window_session_sweep["session_bucket_summary"]["usa_europe_peak_heuristic"]
            self.assertEqual(peak_bucket_mid["run_count"], 1)
            self.assertEqual(peak_bucket_mid["full_mid_window_candidate_count"], 1)
            self.assertEqual(
                peak_bucket_mid["latent_market_full_mid_window_candidate_count"],
                1,
            )

    def test_build_maker_outcome_forensics_detects_bounded_complement_pairs(self):
        records = [
            {
                "order_submit_id": "order-a",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-a",
                "outcome_truth_status": "complete",
                "fill_count": 3,
                "decision_quality": "incorrect",
                "decision_component_x_size": -30.925375,
                "execution_component_x_size": 25.4015125,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "BUY",
                "mid_price_decision": 0.545,
                "mid_price_eval": 0.595,
                "edge_expected": -0.05,
                "fill_total_size": 618.5075,
            },
            {
                "order_submit_id": "order-b",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-b",
                "outcome_truth_status": "complete",
                "fill_count": 3,
                "decision_quality": "incorrect",
                "decision_component_x_size": -30.925375,
                "execution_component_x_size": 25.4015125,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "SELL",
                "mid_price_decision": 0.455,
                "mid_price_eval": 0.405,
                "edge_expected": 0.05,
                "fill_total_size": 618.5075,
            },
        ]
        summary = bro_metric_harvest._build_maker_outcome_forensics(records)
        self.assertEqual(summary["maker_complement_pair_cluster_count"], 1)
        self.assertAlmostEqual(summary["maker_complement_pair_cluster_decision_debt_sum"], -61.85075)
        self.assertEqual(summary["maker_multifill_complete_count"], 2)
        self.assertEqual(summary["maker_multifill_complete_incorrect_ratio"], 1.0)
        self.assertEqual(summary["maker_same_target_repeat_cluster_count"], 0)

    def test_build_maker_outcome_forensics_classifies_lifecycle_gaps(self):
        records = [
            {
                "order_submit_id": "order-1",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-1",
                "outcome_truth_status": "unknown_incomplete_lifecycle",
                "fill_count": 0,
                "decision_quality": "correct",
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
            },
            {
                "order_submit_id": "order-2",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-2",
                "outcome_truth_status": "unknown_incomplete_lifecycle",
                "fill_count": 1,
                "decision_quality": "incorrect",
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
            },
            {
                "order_submit_id": "order-3",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-3",
                "outcome_truth_status": "complete",
                "fill_count": 1,
                "decision_quality": "correct",
                "decision_component_x_size": 1.0,
                "execution_component_x_size": 0.5,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "BUY",
                "mid_price_decision": 0.4,
                "mid_price_eval": 0.45,
                "edge_expected": 0.05,
                "fill_total_size": 10.0,
            },
            {
                "order_submit_id": "order-4",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": "target-4",
                "outcome_truth_status": "complete",
                "fill_count": 2,
                "decision_quality": "incorrect",
                "decision_component_x_size": -2.0,
                "execution_component_x_size": 0.5,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "SELL",
                "mid_price_decision": 0.6,
                "mid_price_eval": 0.55,
                "edge_expected": -0.05,
                "fill_total_size": 10.0,
            },
        ]
        summary = bro_metric_harvest._build_maker_outcome_forensics(records)
        self.assertEqual(summary["maker_lifecycle_gap_class_distribution"]["no_fill_incomplete"], 1.0)
        self.assertEqual(summary["maker_lifecycle_gap_class_distribution"]["partial_fill_incomplete"], 1.0)
        self.assertEqual(summary["maker_lifecycle_gap_class_distribution"]["complete_single_fill"], 1.0)
        self.assertEqual(summary["maker_lifecycle_gap_class_distribution"]["complete_multifill"], 1.0)


if __name__ == "__main__":
    unittest.main()
