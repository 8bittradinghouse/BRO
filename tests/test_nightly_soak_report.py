import json
import tempfile
import unittest
from pathlib import Path

from scripts.nightly_soak_report import build_report


class NightlySoakReportTests(unittest.TestCase):
    def test_build_report_basic_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {"event_type": "risk_reject", "reason": "stale_book"},
                {"event_type": "book_top", "token_id": "t1", "midpoint": 0.51},
                {"event_type": "order_submit", "order_id": "o1", "reason": "sniper_taker_chainlink"},
                {"event_type": "latency_regime_change", "state": "armed"},
                {"event_type": "sniper_taker_submit", "token_id": "t1"},
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
            self.assertIn("armed", report["edge_activation_quality_by_regime"])
            self.assertIn("latency_distribution_ms", report)
            self.assertIn("sniper", report)
            self.assertIn("execution_paths", report)
            self.assertIn("edge_truth", report)
            self.assertIn("harness_realism_grade", report)
            self.assertIn("harness_realism_grade_breakdown", report)
            self.assertIn("taker_stage_net_breakout", report)
            self.assertIn("mode_transitions", report)
            self.assertIn("pickoff_indicator", report)
            self.assertIn("runtime_classification", report)
            self.assertIn("primary_suppression_cause", report)
            self.assertIn("contributing_suppression_causes", report)
            self.assertIn("suppression_dominated_run", report)
            self.assertIn("execution_starvation_mode", report)
            self.assertIn("protected_no_trade_explanation", report)
            self.assertIn("control_authority_clarity", report)
            self.assertIn("protection_path_trigger_chain", report)
            self.assertGreaterEqual(report["execution_paths"].get("maker_submits", 0.0), 0.0)
            self.assertEqual(report["edge_truth"].get("rows_total"), 0.0)

    def test_build_report_includes_taker_stage_net_breakout(self):
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
                    "reason": "sniper_taker_chainlink",
                    "stage": "MAKER_TAKER_SELECTIVE",
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
            breakout = report.get("taker_stage_net_breakout", {})
            self.assertIn("MAKER_TAKER_SELECTIVE", breakout)
            row = breakout["MAKER_TAKER_SELECTIVE"]
            self.assertEqual(float(row.get("fills_scored", 0.0)), 1.0)
            self.assertAlmostEqual(float(row.get("capture", 0.0)), 0.2, places=6)
            self.assertAlmostEqual(float(row.get("adverse_selection", 0.0)), 0.0, places=6)
            self.assertAlmostEqual(float(row.get("net", 0.0)), 0.2, places=6)

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
                    "reason": "sniper_taker_chainlink",
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
                {"event_type": "order_submit", "order_id": "t1", "reason": "sniper_taker_chainlink"},
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
            self.assertEqual(float(paths.get("taker_bonus_submits") or 0.0), 1.0)
            self.assertEqual(float(paths.get("taker_bonus_fills") or 0.0), 2.0)
            self.assertEqual(float(paths.get("taker_bonus_filled_orders") or 0.0), 1.0)
            self.assertAlmostEqual(float(paths.get("taker_bonus_fill_rate") or 0.0), 1.0)

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
                    "market_reference_mode": "bounded_single_side_touch",
                    "market_reference_source_side": "ask",
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
            self.assertEqual(edge_truth.get("maker_market_reference_fallback_count"), 1.0)
            self.assertEqual(edge_truth.get("maker_market_reference_fallback_bid_count"), 0.0)
            self.assertEqual(edge_truth.get("maker_market_reference_fallback_ask_count"), 1.0)
            self.assertEqual(report.get("maker_market_reference_fallback_bid_count"), 0.0)
            self.assertEqual(report.get("maker_market_reference_fallback_ask_count"), 1.0)
            self.assertEqual(report.get("maker_reference_direct_midpoint_activity"), 0.0)
            self.assertEqual(report.get("maker_reference_bounded_fallback_activity"), 1.0)

    def test_build_report_emits_maker_competitiveness_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events_path = root / "events_2026-01-01.jsonl"
            status_path = root / "status_2026-01-01.jsonl"
            errors_path = root / "errors_2026-01-01.jsonl"
            events = [
                {
                    "event_type": "maker_competitiveness_decision",
                    "run_id": "rid-competitiveness",
                    "token_id": "t1",
                    "timing_gate_blocked": True,
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
                    "maker_competitiveness": {
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
            maker_comp = report.get("maker_competitiveness", {})
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
                },
                {
                    "event_type": "edge_evaluation",
                    "run_id": "rid-taker-competitiveness",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "taker_submit_rejected",
                    "taker_submit_reject_reason": "risk_reject_notional_cap",
                },
                {
                    "event_type": "sniper_taker_decision",
                    "run_id": "rid-taker-competitiveness",
                    "token_id": "t1",
                    "stage": "SNIPER_PRIMARY",
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
                    "event_type": "sniper_taker_decision",
                    "run_id": "rid-taker-competitiveness",
                    "token_id": "t2",
                    "stage": "SNIPER_PRIMARY",
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
                    "reason": "sniper_taker_chainlink",
                    "stage": "SNIPER_PRIMARY",
                    "taker_competitiveness": {
                        "stage": "SNIPER_PRIMARY",
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
            self.assertEqual(float(taker_comp.get("submit_capable_decision_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("blocked_decision_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("actual_submit_count") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("fill_count") or 0.0), 1.0)
            self.assertAlmostEqual(float(taker_comp.get("decision_to_submit_rate") or 0.0), 0.5, places=9)
            self.assertAlmostEqual(float(taker_comp.get("submit_capable_to_submit_rate") or 0.0), 1.0, places=9)
            self.assertAlmostEqual(float(taker_comp.get("fill_rate_from_submits") or 0.0), 1.0, places=9)
            self.assertEqual(float(taker_comp.get("outside_window_blocked_count_edge_eval") or 0.0), 1.0)
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
            lag_distribution = taker_comp.get("lag_class_distribution", {})
            self.assertEqual(int(lag_distribution.get("within_window", 0)), 1)
            self.assertEqual(float(taker_comp.get("dynamic_size_capped_by_risk_count_decision") or 0.0), 1.0)
            decision_oracle_status = taker_comp.get("decision_multi_oracle_status_distribution", {})
            self.assertEqual(int(decision_oracle_status.get("unknown", 0)), 1)
            self.assertEqual(int(decision_oracle_status.get("confirmed", 0)), 1)
            submit_oracle_status = taker_comp.get("submit_multi_oracle_status_distribution", {})
            self.assertEqual(int(submit_oracle_status.get("confirmed", 0)), 1)
            self.assertEqual(float(taker_comp.get("multi_oracle_confirmation_count_decision") or 0.0), 1.0)
            self.assertEqual(float(taker_comp.get("multi_oracle_boost_applied_count_decision") or 0.0), 1.0)
            aggressiveness = taker_comp.get("aggressiveness_application_counts", {})
            self.assertEqual(int(aggressiveness.get("price_aggressed", 0)), 1)
            self.assertEqual(int(aggressiveness.get("hard_min_floor_applied", 0)), 1)
            submit_stage = taker_comp.get("submit_stage_distribution", {})
            self.assertEqual(int(submit_stage.get("SNIPER_PRIMARY", 0)), 1)
            fill_stage = taker_comp.get("fill_stage_distribution", {})
            self.assertEqual(int(fill_stage.get("SNIPER_PRIMARY", 0)), 1)
            self.assertEqual(float(taker_comp.get("submit_unknown_stage_count") or 0.0), 0.0)
            self.assertEqual(float(taker_comp.get("fill_without_submit_stage_count") or 0.0), 0.0)

            pyth_stats = report.get("secondary_oracle_pyth", {})
            self.assertEqual(float(pyth_stats.get("sample_count") or 0.0), 1.0)
            self.assertEqual(float(pyth_stats.get("enabled_sample_count") or 0.0), 1.0)
            self.assertEqual(float(pyth_stats.get("connected_sample_count") or 0.0), 0.0)
            self.assertEqual(
                int((pyth_stats.get("operational_state_distribution") or {}).get("unavailable_http_403", 0)),
                1,
            )
            self.assertEqual(int(((pyth_stats.get("latest") or {}).get("last_http_status") or 0)), 403)

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
                    "maker_no_submission_category": "quote_quality_skip_queue_depth",
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
                    "block_reason": "latency_not_armed",
                },
                {
                    "event_type": "edge_blocked_reason",
                    "run_id": "rid-stale",
                    "reason": "stale_should_not_count",
                },
            ]
            events_path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
            status_path.write_text(json.dumps({"run_id": "rid-stale", "gauge.open_orders": 0}) + "\n", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")

            report = build_report(root, run_id="rid-stale")
            stale = report.get("stale_data", {})
            self.assertEqual(stale.get("stale_book_rejects"), 1.0)
            self.assertEqual(stale.get("stale_oracle_edge_blocks"), 1.0)
            self.assertEqual(stale.get("disarmed_edge_blocks"), 1.0)

    def test_build_report_includes_market_data_source_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {"counter.book_updates": 10, "counter.book_updates_ws": 8, "counter.book_updates_rest": 2},
                {"counter.book_updates": 16, "counter.book_updates_ws": 12, "counter.book_updates_rest": 4},
                {"counter.book_updates": 22, "counter.book_updates_ws": 15, "counter.book_updates_rest": 7},
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
            self.assertEqual(source_stats.get("book_updates_rest_delta"), 5.0)
            self.assertAlmostEqual(float(source_stats.get("book_updates_rest_ratio") or 0.0), 5.0 / 12.0)

    def test_build_report_emits_transport_vs_control_authority_clarity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            status = [
                {
                    "runtime_state": "active",
                    "active_targets_present": True,
                    "no_target_standdown": False,
                    "book_feed_required": True,
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
                    "runtime_state": "active",
                    "active_targets_present": True,
                    "no_target_standdown": False,
                    "book_feed_required": True,
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
                    "runtime_state": "active",
                    "active_targets_present": True,
                    "no_target_standdown": False,
                    "book_feed_required": True,
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
                    "runtime_state": "active",
                    "active_targets_present": True,
                    "no_target_standdown": False,
                    "book_feed_required": True,
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
            self.assertIn("safety_required_book_feed_disconnected", contributing)

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
                    "runtime_state": "active",
                    "active_targets_present": True,
                    "no_target_standdown": False,
                    "book_feed_required": True,
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
