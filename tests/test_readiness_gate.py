import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts.readiness_gate import _load_policy, _resolve_effective_log_dir, run_readiness_gate


class ReadinessGateTests(unittest.TestCase):
    def test_resolve_effective_log_dir_no_longer_auto_selects_child(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "logs_exec"
            base.mkdir(parents=True, exist_ok=True)
            (base / "btc_paper").mkdir(parents=True, exist_ok=True)
            (base / "sol_paper").mkdir(parents=True, exist_ok=True)
            (base / "btc_paper" / "status_2026-01-01.jsonl").write_text("{}", encoding="utf-8")
            (base / "sol_paper" / "status_2026-01-02.jsonl").write_text("{}", encoding="utf-8")
            resolved = _resolve_effective_log_dir(base)
            self.assertEqual(resolved, base.resolve())

    def test_resolve_effective_log_dir_maps_container_logs_to_host_root(self):
        with tempfile.TemporaryDirectory() as td:
            host_root = Path(td) / "logs_exec"
            host_root.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict("os.environ", {"BRO_LOG_DIR": str(host_root)}, clear=False):
                resolved = _resolve_effective_log_dir(Path("/logs/paper_universal"))
            self.assertEqual(resolved, (host_root / "paper_universal").resolve())

    def test_run_readiness_gate_returns_highest_passing_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-1"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"run_id": run_id, "event_type": "risk_reject", "reason": "order_rate_limit"}),
                        json.dumps({"run_id": run_id, "event_type": "book_top", "token_id": "t1", "midpoint": 0.51}),
                        json.dumps({"run_id": run_id, "event_type": "fill", "token_id": "t1", "side": "BUY", "price": 0.50, "size": 10}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"run_id": run_id, "gauge.open_orders": 1, "gauge.operating_mode_state": 0.0}),
                        json.dumps({"run_id": run_id, "gauge.open_orders": 0, "gauge.operating_mode_state": 0.0}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper", "pilot"],
                "stages": {
                    "paper": {
                        "min_status_rows": 1,
                        "min_quote_uptime_ratio": 0.4,
                        "max_error_rows": 1,
                    },
                    "pilot": {
                        "min_status_rows": 20,
                    },
                },
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)

            self.assertEqual(result["highest_passing_stage"], "paper")
            self.assertEqual(result["recommended_next_stage"], "pilot")

    def test_kill_switch_events_can_fail_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-2"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "event_type": "kill_switch_cancel_all"}) + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 0, "gauge.operating_mode_state": 0.0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {
                    "paper": {
                        "max_kill_switch_events": 0,
                    }
                },
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)

            self.assertIsNone(result["highest_passing_stage"])
            self.assertEqual(result["blocking_stage"], "paper")

    def test_stage_promotion_requires_contiguous_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-3"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                json.dumps({"run_id": run_id, "gauge.open_orders": 1, "gauge.operating_mode_state": 0.0}) + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper", "pilot", "scaled"],
                "stages": {
                    "paper": {"min_status_rows": 99},
                    "pilot": {"min_status_rows": 1},
                    "scaled": {"min_status_rows": 1},
                },
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)

            self.assertIsNone(result["highest_passing_stage"])
            self.assertEqual(result["blocking_stage"], "paper")
            self.assertEqual(result["recommended_next_stage"], "paper")

    def test_runtime_resource_metric_can_drive_stage_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-resource"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "gauge.open_orders": 0,
                                "gauge.operating_mode_state": 0.0,
                                "gauge.process_cpu_percent": 10.0,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "gauge.open_orders": 0,
                                "gauge.operating_mode_state": 0.0,
                                "gauge.process_cpu_percent": 45.0,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")
            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {
                    "paper": {
                        "min_status_rows": 1,
                        "max_resource_process_cpu_percent_max": 30,
                    }
                },
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)
            self.assertIsNone(result["highest_passing_stage"])
            self.assertEqual(result["blocking_stage"], "paper")

    def test_run_id_filter_avoids_cross_run_contamination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "run_manifest_target.json").write_text(
                json.dumps({"run_id": "target", "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"event_type": "kill_switch_cancel_all", "run_id": "other"}),
                        json.dumps({"event_type": "risk_reject", "reason": "stale_book", "run_id": "target"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"run_id": "other", "gauge.open_orders": 0, "gauge.operating_mode_state": 3.0}),
                        json.dumps({"run_id": "target", "gauge.open_orders": 1, "gauge.operating_mode_state": 0.0}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {"paper": {"max_kill_switch_events": 0, "min_quote_uptime_ratio": 0.5}},
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id="target")

            self.assertEqual(result["highest_passing_stage"], "paper")

    def test_policy_rejects_invalid_criterion_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {"paper": {"status_rows": 10}},
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_policy(policy_path)

    def test_policy_rejects_unknown_metric_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {"paper": {"min_not_a_real_metric": 1}},
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_policy(policy_path)

    def test_policy_rejects_non_numeric_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {"paper": {"min_status_rows": "many"}},
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                _load_policy(policy_path)

    def test_runtime_non_promotable_blocks_stage_even_if_policy_metrics_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-standdown"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )
            (root / "events_2026-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2026-01-01T00:00:00Z",
                                "runtime_state": "no_target_standdown",
                                "active_targets_present": False,
                                "no_target_standdown": True,
                                "book_feed_required": False,
                                "kill_switch": False,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2026-01-01T00:30:00Z",
                                "runtime_state": "no_target_standdown",
                                "active_targets_present": False,
                                "no_target_standdown": True,
                                "book_feed_required": False,
                                "kill_switch": False,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "errors_2026-01-01.jsonl").write_text("", encoding="utf-8")

            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {"paper": {"min_status_rows": 1}},
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)
            result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)

            self.assertIsNone(result["highest_passing_stage"])
            self.assertEqual(result["blocking_stage"], "paper")
            self.assertIn("runtime_non_promotable", ",".join(result.get("runtime_findings", [])))
            self.assertEqual(
                result["metrics"].get("runtime_classification_name"),
                "NON_PROMOTABLE_NO_PARTICIPATION",
            )

    def test_readiness_gate_passes_tail_limit_to_report_and_metric_loads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-tail"
            policy_path = root / "policy.yaml"
            policy_payload = {"stage_order": ["paper"], "stages": {"paper": {}}}
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)

            fake_report = {
                "status_rows": 1,
                "error_rows": 0,
                "quote_uptime_ratio": 1.0,
                "primary_suppression_cause": "none",
                "contributing_suppression_causes": [],
                "ambiguous_suppression_cause": False,
                "suppression_dominated_run": False,
                "execution_starvation_mode": "none",
                "protected_no_trade_explanation": "execution_not_suppression_dominated",
                "maker_reference_direct_midpoint_activity": 8.0,
                "maker_reference_bounded_fallback_activity": 3.0,
                "maker_reference_direct_midpoint_action_activity": 2.0,
                "maker_reference_bounded_fallback_action_activity": 1.0,
                "maker_market_reference_fallback_bid_count": 1.0,
                "maker_market_reference_fallback_ask_count": 2.0,
                "quote_diagnostics": {
                    "quote_window_ratio": 1.0,
                    "quote_active_within_window_ratio": 0.5,
                    "participation_ratio": 0.25,
                    "participation_within_window_ratio": 0.25,
                },
                "reject_reason_distribution": {},
                "execution_quality": {"capture_minus_adverse": 0.0},
                "runtime_classification": {
                    "classification": "VALID_ACTIVE",
                    "promotion_eligible": True,
                    "metrics": {"status_rows": 1.0, "standdown_rows": 0.0},
                },
            }

            with mock.patch("scripts.readiness_gate.build_report", return_value=fake_report) as mock_report:
                result = run_readiness_gate(
                    log_dir=root,
                    policy=policy,
                    run_id=run_id,
                    report_max_lines_per_file=321,
                )

            self.assertEqual(result["highest_passing_stage"], "paper")
            self.assertEqual(mock_report.call_args.kwargs.get("max_lines_per_file"), 321)
            self.assertEqual(result["metrics"].get("readiness_report_max_lines_per_file"), 321.0)
            self.assertEqual(result["metrics"].get("quote_window_ratio"), 1.0)
            self.assertEqual(result["metrics"].get("quote_active_within_window_ratio"), 0.5)
            self.assertEqual(result["metrics"].get("participation_ratio"), 0.25)
            self.assertEqual(result["metrics"].get("participation_within_quote_window_ratio"), 0.25)
            self.assertEqual(result["metrics"].get("runtime_primary_suppression_cause"), "none")
            self.assertEqual(result["metrics"].get("execution_starvation_mode"), "none")
            self.assertEqual(result["metrics"].get("suppression_dominated_run"), 0.0)
            self.assertEqual(result["metrics"].get("maker_reference_direct_midpoint_activity"), 8.0)
            self.assertEqual(result["metrics"].get("maker_reference_bounded_fallback_activity"), 3.0)
            self.assertEqual(result["metrics"].get("maker_market_reference_fallback_bid_count"), 1.0)
            self.assertEqual(result["metrics"].get("maker_market_reference_fallback_ask_count"), 2.0)
            self.assertEqual(result.get("suppression_summary", {}).get("primary_suppression_cause"), "none")
            self.assertEqual(result.get("suppression_summary", {}).get("execution_starvation_mode"), "none")

    def test_readiness_gate_surfaces_lifecycle_context_missing_metric(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-lifecycle-missing"
            policy_path = root / "policy.yaml"
            policy_payload = {
                "stage_order": ["paper"],
                "stages": {
                    "paper": {
                        "max_lifecycle_context_missing_sec_to_expiry_count": 0,
                    }
                },
            }
            policy_path.write_text(yaml.safe_dump(policy_payload), encoding="utf-8")
            policy = _load_policy(policy_path)

            fake_report = {
                "status_rows": 2,
                "error_rows": 0,
                "quote_uptime_ratio": 1.0,
                "reject_reason_distribution": {},
                "execution_quality": {"capture_minus_adverse": 0.0},
                "runtime_classification": {
                    "classification": "VALID_ACTIVE",
                    "promotion_eligible": True,
                    "metrics": {"status_rows": 2.0, "standdown_rows": 0.0},
                },
                "valuation_truth": {
                    "lifecycle_context_missing_sec_to_expiry_count": 1.0,
                },
            }

            with mock.patch("scripts.readiness_gate.build_report", return_value=fake_report):
                result = run_readiness_gate(log_dir=root, policy=policy, run_id=run_id)

            self.assertIsNone(result["highest_passing_stage"])
            self.assertEqual(result["blocking_stage"], "paper")
            self.assertEqual(
                result["metrics"].get("lifecycle_context_missing_sec_to_expiry_count"),
                1.0,
            )


if __name__ == "__main__":
    unittest.main()
