import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts.soak_hardening_gate import run_gate


class SoakHardeningGateTests(unittest.TestCase):
    def _write_fixture(self, root: Path, run_id: str) -> Path:
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"run_manifest_{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "manifest_schema_version": 2,
                    "profile_name": "test-profile",
                    "git_commit": "deadbeef",
                    "config_fingerprint_sha256": "a" * 64,
                    "code_fingerprint_sha256": "b" * 64,
                    "status_path": str((log_dir / "status_2099-01-01.jsonl").resolve()),
                    "events_path": str((log_dir / "events_2099-01-01.jsonl").resolve()),
                    "start_ts": "2099-01-01T00:00:00.000Z",
                }
            ),
            encoding="utf-8",
        )
        status_rows = [
            {
                "run_id": run_id,
                "ts_utc": "2099-01-01T00:00:00Z",
                "gauge.open_orders": 1,
                "gauge.cycle_latency_ms": 100.0,
                "gauge.process_rss_mb": 256.0,
                "gauge.orders_used_60s": 10.0,
                "gauge.orders_limit_60s": 100.0,
                "gauge.cancels_used_60s": 10.0,
                "gauge.cancels_limit_60s": 100.0,
                "gauge.latency_sampling_inactive_cycles": 1.0,
                "counter.book_updates": 11.0,
                "counter.book_updates_ws": 10.0,
                "pair_truth_pair_count": 1.0,
                "pair_truth_missing_pair_count": 0.0,
                "pair_truth_one_sided_pair_count": 0.0,
                "pair_truth_authoritative_pair_count": 1.0,
                "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                "chainlink": {
                    "connected": True,
                    "reconnects": 0,
                    "last_tick_age_sec": 0.5,
                    "queue_size": 0,
                    "dropped_ticks": 0,
                },
            },
            {
                "run_id": run_id,
                "ts_utc": "2099-01-01T00:30:00Z",
                "gauge.open_orders": 1,
                "gauge.cycle_latency_ms": 120.0,
                "gauge.process_rss_mb": 260.0,
                "gauge.orders_used_60s": 12.0,
                "gauge.orders_limit_60s": 100.0,
                "gauge.cancels_used_60s": 14.0,
                "gauge.cancels_limit_60s": 100.0,
                "gauge.latency_sampling_inactive_cycles": 2.0,
                "counter.book_updates": 22.0,
                "counter.book_updates_ws": 20.0,
                "pair_truth_pair_count": 1.0,
                "pair_truth_missing_pair_count": 0.0,
                "pair_truth_one_sided_pair_count": 0.0,
                "pair_truth_authoritative_pair_count": 1.0,
                "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                "chainlink": {
                    "connected": True,
                    "reconnects": 0,
                    "last_tick_age_sec": 0.5,
                    "queue_size": 0,
                    "dropped_ticks": 0,
                },
            },
        ]
        (log_dir / "status_2099-01-01.jsonl").write_text(
            "\n".join(json.dumps(r) for r in status_rows) + "\n",
            encoding="utf-8",
        )
        (log_dir / "events_2099-01-01.jsonl").write_text("", encoding="utf-8")
        (log_dir / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")
        return log_dir

    def test_soak_gate_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump(
                    {
                        "stage_order": ["paper"],
                        "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.5}},
                    }
                ),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {
                            "min_status_rows": 1,
                            "max_cycle_latency_p95_ms": 500,
                            "max_cycle_latency_max_ms": 1000,
                            "max_process_rss_mb": 1024,
                            "max_order_capacity_used_ratio": 1.0,
                            "max_cancel_capacity_used_ratio": 1.0,
                            "max_latency_inactive_cycles": 20,
                        },
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                            "max_book_feed_reconnects_per_hour": 1000000.0,
                            "max_chainlink_reconnects_per_hour": 1000000.0,
                            "max_book_feed_last_msg_age_sec": 1000000.0,
                            "max_chainlink_last_tick_age_sec": 1000000.0,
                            "max_chainlink_dropped_ticks": 1000000.0,
                            "max_chainlink_queue_size": 1000000.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "events_2099-01-01.jsonl").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "ts_utc": "2099-01-01T00:05:00Z",
                        "event_type": "order_submit",
                        "order_id": "m1",
                        "reason": "maker_quote",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertIn("decision_trace", result)
            soak_report = result.get("soak_report", {})
            self.assertIn("runtime_primary_suppression_cause", soak_report)
            self.assertIn("runtime_contributing_suppression_causes", soak_report)
            self.assertIn("runtime_ambiguous_suppression_cause", soak_report)
            self.assertIn("suppression_dominated_run", soak_report)
            self.assertIn("execution_starvation_mode", soak_report)
            self.assertIn("protected_no_trade_explanation", soak_report)
            self.assertIn("control_authority_clarity", soak_report)
            self.assertIn("protection_path_trigger_chain", soak_report)
            self.assertIn("preexpiry_ws_missing_or_unusable_anomaly_count", soak_report)
            self.assertIn("lifecycle_context_mismatch_count", soak_report)
            self.assertIn("lifecycle_context_missing_sec_to_expiry_count", soak_report)
            self.assertIn("valuation_counter_limits", soak_report)

    def test_soak_gate_uses_readiness_runtime_findings_as_required_stage_causes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-runtime-override"
            log_dir = self._write_fixture(root, run_id)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch(
                    "scripts.soak_hardening_gate.run_readiness_gate",
                    return_value={
                        "highest_passing_stage": None,
                        "blocking_stage": "paper",
                        "recommended_next_stage": "paper",
                        "runtime_findings": [
                            "readiness_runtime_invalid:INVALID_SAFETY",
                            "readiness_runtime_non_promotable:INVALID_SAFETY",
                        ],
                        "stage_results": [{"stage": "paper", "passed": True, "checks": []}],
                    },
                ),
                mock.patch("scripts.soak_hardening_gate.run_integrity_audit", return_value={"findings": []}),
                mock.patch("scripts.soak_hardening_gate.run_performance_budget_gate", return_value={"findings": []}),
                mock.patch("scripts.soak_hardening_gate.run_websocket_reliability_gate", return_value={"findings": []}),
                mock.patch(
                    "scripts.soak_hardening_gate.build_report",
                    return_value={
                        "duration_minutes": 20.0,
                        "quote_uptime_ratio": 1.0,
                        "error_rows": 0.0,
                        "execution_paths": {"maker_submits": 0.0, "maker_fill_rate": 0.0, "taker_bonus_submits": 0.0, "taker_bonus_fills": 0.0, "taker_bonus_fill_rate": 0.0},
                        "execution_quality": {"capture_minus_adverse": 0.0},
                        "market_data_source": {},
                        "valuation_truth": {},
                        "runtime_classification": {"classification": "INVALID_SAFETY", "promotion_eligible": False, "metrics": {}},
                        "primary_suppression_cause": "safety_kill_switch_or_external_guard",
                        "contributing_suppression_causes": ["kill_switch"],
                        "ambiguous_suppression_cause": False,
                        "suppression_dominated_run": True,
                        "execution_starvation_mode": "kill_switch",
                        "protected_no_trade_explanation": "safety_control_engaged",
                        "control_authority_clarity": {},
                        "protection_path_trigger_chain": {},
                    },
                ),
            ):
                result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)

            finding = next(
                item for item in result["findings"] if item.startswith("soak_readiness_below_required_stage:")
            )
            self.assertIn("readiness_runtime_invalid:INVALID_SAFETY", finding)
            self.assertIn("readiness_runtime_non_promotable:INVALID_SAFETY", finding)

    def test_soak_gate_allows_non_defect_unpriceable_residue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "gauge.open_orders": 1,
                    "gauge.cycle_latency_ms": 100.0,
                    "gauge.process_rss_mb": 256.0,
                    "gauge.orders_used_60s": 10.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 10.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 1.0,
                    "counter.book_updates": 11.0,
                    "counter.book_updates_ws": 10.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 0.5,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:30:00Z",
                    "gauge.open_orders": 0,
                    "gauge.cycle_latency_ms": 120.0,
                    "gauge.process_rss_mb": 260.0,
                    "gauge.orders_used_60s": 12.0,
                    "gauge.orders_limit_60s": 100.0,
                    "gauge.cancels_used_60s": 14.0,
                    "gauge.cancels_limit_60s": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 2.0,
                    "counter.book_updates": 22.0,
                    "counter.book_updates_ws": 20.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 0.5,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                    "valuation_degraded": True,
                    "valuation_hard_degraded": False,
                    "valuation_raw_hard_degraded": True,
                    "held_unpriceable_started_count": 1,
                    "held_unpriceable_recovered_count": 0,
                    "held_unpriceable_unrecovered_non_defect_count": 1,
                    "held_unpriceable_unrecovered_meaningful_count": 0,
                    "held_unpriceable_token_count": 1,
                    "held_unpriceable_token_ids": ["t1"],
                    "held_unpriceable_non_defect_token_ids": ["t1"],
                    "held_unpriceable_meaningful_escalation_token_ids": [],
                },
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {
                            "min_status_rows": 1,
                            "max_order_capacity_used_ratio": 1.0,
                            "max_cancel_capacity_used_ratio": 1.0,
                        },
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "max_held_unpriceable_unrecovered_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertEqual(result["soak_report"]["held_unpriceable_unrecovered_raw_count"], 1.0)
            self.assertEqual(result["soak_report"]["held_unpriceable_unrecovered_non_defect_count"], 1.0)
            self.assertEqual(result["soak_report"]["held_unpriceable_unrecovered_meaningful_count"], 0.0)

    def test_soak_gate_fails_when_lifecycle_context_mismatch_count_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_rows = [json.loads(line) for line in status_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            status_rows[-1]["lifecycle_context_mismatch_count"] = 1
            status_rows[-1]["lifecycle_context_missing_sec_to_expiry_count"] = 0
            status_rows[-1]["preexpiry_ws_missing_or_unusable_anomaly_count"] = 0
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                            "max_book_feed_reconnects_per_hour": 1000000.0,
                            "max_chainlink_reconnects_per_hour": 1000000.0,
                            "max_book_feed_last_msg_age_sec": 1000000.0,
                            "max_chainlink_last_tick_age_sec": 1000000.0,
                            "max_chainlink_dropped_ticks": 1000000.0,
                            "max_chainlink_queue_size": 1000000.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "max_lifecycle_context_mismatch_count": 0,
                            "max_lifecycle_context_missing_sec_to_expiry_count": 0,
                            "max_preexpiry_ws_missing_or_unusable_anomaly_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "events_2099-01-01.jsonl").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "ts_utc": "2099-01-01T00:05:00Z",
                        "event_type": "order_submit",
                        "order_id": "m1",
                        "reason": "maker_quote",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("soak_lifecycle_context_mismatch_count_too_high:" in finding for finding in result.get("findings", [])),
                msg=result.get("findings", []),
            )

    def test_soak_gate_exposes_pair_truth_missing_metrics_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "gauge.open_orders": 1,
                    "counter.book_updates": 10.0,
                    "counter.book_updates_ws": 1.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 1.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 0.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:30:00Z",
                    "gauge.open_orders": 1,
                    "counter.book_updates": 20.0,
                    "counter.book_updates_ws": 2.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 1.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 0.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                            "min_book_updates_ws_delta": 1,
                            "min_book_updates_total_delta": 1,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "events_2099-01-01.jsonl").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "ts_utc": "2099-01-01T00:05:00Z",
                        "event_type": "order_submit",
                        "order_id": "m1",
                        "reason": "maker_quote",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result.get("findings", []))
            self.assertAlmostEqual(float(result["soak_report"]["pair_truth_missing_pair_row_ratio"]), 1.0)
            self.assertAlmostEqual(float(result["soak_report"]["pair_truth_missing_pair_count_max"]), 1.0)

    def test_soak_gate_resolves_repo_owned_budget_and_policy_outside_repo_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            repo_root = Path(__file__).resolve().parents[1]
            old_cwd = Path.cwd()
            try:
                os.chdir(repo_root.parent.parent)
                result = run_gate(
                    log_dir=log_dir,
                    run_id=run_id,
                    budget_path=Path("ops/soak_budget.yaml"),
                )
            finally:
                os.chdir(old_cwd)
        self.assertIn("budget_path", result)
        self.assertEqual(
            str(result.get("budget_path") or ""),
            str((repo_root / "ops" / "soak_budget.yaml").resolve()),
        )

    def test_soak_gate_fails_on_duration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1},
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                            "max_book_feed_reconnects_per_hour": 1000000.0,
                            "max_chainlink_reconnects_per_hour": 1000000.0,
                            "max_book_feed_last_msg_age_sec": 1000000.0,
                            "max_chainlink_last_tick_age_sec": 1000000.0,
                            "max_chainlink_dropped_ticks": 1000000.0,
                            "max_chainlink_queue_size": 1000000.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 60, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2002", result["error_codes"])

    def test_soak_gate_fails_when_maker_and_taker_bonus_inactive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "min_maker_submits": 1,
                            "min_taker_bonus_submits": 1,
                            "min_taker_bonus_fills": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertIn("BRO-2005", result["error_codes"])

    def test_soak_gate_default_event_scan_preserves_execution_path_signals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            rows = [
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:00Z", "event_type": "order_submit", "order_id": "m1"},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": "fill", "order_id": "m1"},
            ]
            for idx in range(6000):
                rows.append(
                    {
                        "run_id": run_id,
                        "ts_utc": "2099-01-01T00:00:02Z",
                        "event_type": "risk_reject",
                        "reason": f"r{idx}",
                    }
                )
            event_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 10000,
                            "min_maker_submits": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertGreaterEqual(result["soak_report"]["maker_submits"], 1.0)
            self.assertFalse(any("soak_maker_submits_too_low:" in f for f in result["findings"]))

    def test_soak_gate_fails_when_maker_fill_rate_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:00Z", "event_type": "order_submit", "order_id": "m1", "reason": "maker_quote"},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": "fill", "order_id": "m1", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 1.0},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:02Z", "event_type": "order_submit", "order_id": "m2", "reason": "maker_quote"},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:03Z", "event_type": "fill", "order_id": "m2", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 1.0},
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "min_maker_submits": 1,
                            "max_maker_fill_rate": 0.50,
                            "maker_fill_rate_enforcement_min_submits": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("soak_maker_fill_rate_too_high:" in finding for finding in result.get("findings", [])),
                msg=result.get("findings", []),
            )

    def test_soak_gate_fails_when_execution_quality_breaches_floor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "book_top",
                    "token_id": "t1",
                    "midpoint": 0.50,
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "order_submit",
                    "order_id": "m1",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.80,
                    "size": 1.0,
                    "reason": "maker_quote",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "event_type": "fill",
                    "order_id": "m1",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.80,
                    "size": 1.0,
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {
                            "min_status_rows": 1,
                            "max_order_capacity_used_ratio": 1.0,
                            "max_cancel_capacity_used_ratio": 1.0,
                        },
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "min_execution_quality_capture_minus_adverse": -0.10,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertIn("BRO-1503", result["error_codes"])
            self.assertFalse(result["lanes"]["reliability"]["ok"])
            self.assertTrue(
                any(
                    "soak_execution_quality_capture_minus_adverse_too_low:" in finding
                    for finding in result.get("findings", [])
                ),
                msg=result.get("findings", []),
            )

    def test_soak_gate_skips_maker_fill_rate_check_below_min_submit_sample(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:00Z", "event_type": "order_submit", "order_id": "m1", "reason": "maker_quote"},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": "fill", "order_id": "m1", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 1.0},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:02Z", "event_type": "order_submit", "order_id": "m2", "reason": "maker_quote"},
                {"run_id": run_id, "ts_utc": "2099-01-01T00:00:03Z", "event_type": "fill", "order_id": "m2", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 1.0},
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "min_maker_submits": 1,
                            "max_maker_fill_rate": 0.50,
                            "maker_fill_rate_enforcement_min_submits": 5,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result.get("findings", []))

    def test_soak_gate_prefers_decision_reference_execution_quality_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "book_top",
                    "token_id": "t1",
                    "midpoint": 0.60,
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "order_submit",
                    "order_id": "t1",
                    "token_id": "t1",
                    "reason": "taker_chainlink",
                    "side": "BUY",
                    "decision_reference_midpoint": 0.90,
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "event_type": "fill",
                    "order_id": "t1",
                    "token_id": "t1",
                    "side": "BUY",
                    "price": 0.80,
                    "size": 1.0,
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {
                            "min_status_rows": 1,
                            "max_order_capacity_used_ratio": 1.0,
                            "max_cancel_capacity_used_ratio": 1.0,
                        },
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.5,
                            "max_error_rows": 0,
                            "min_execution_quality_capture_minus_adverse": -0.10,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            findings = result.get("findings", [])
            self.assertFalse(
                any("soak_execution_quality_capture_minus_adverse_too_low:" in finding for finding in findings),
                msg=findings,
            )
            soak = result.get("soak_report", {})
            self.assertEqual(
                soak.get("execution_quality_capture_minus_adverse_source"),
                "execution_quality_decision_reference_lane_attribution.total.immediate_capture_minus_adverse",
            )
            self.assertAlmostEqual(float(soak.get("execution_quality_capture_minus_adverse") or 0.0), 0.10, places=9)
            self.assertFalse(
                any("soak_maker_fill_rate_too_high:" in finding for finding in result.get("findings", [])),
                msg=result.get("findings", []),
            )
            fill_rate_enforcement = result["soak_report"].get("maker_fill_rate_enforcement", {})
            self.assertEqual(float(fill_rate_enforcement.get("min_submits") or 0.0), 5.0)
            self.assertFalse(bool(fill_rate_enforcement.get("applied")))

    def test_soak_gate_blocks_non_promotable_standdown_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "test-profile",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "code_fingerprint_sha256": "b" * 64,
                        "status_path": str((log_dir / "status_2099-01-01.jsonl").resolve()),
                        "events_path": str((log_dir / "events_2099-01-01.jsonl").resolve()),
                        "start_ts": "2099-01-01T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "lifecycle_phase": "scan",
                                "lifecycle_phase": "scan",
                                "active_targets_present": False,
                                "scan_phase": True,
                                "market_truth_required": False,
                                "kill_switch": False,
                                "book_feed": {"connected": False, "reconnects": 0, "last_msg_age_sec": 45.0},
                                "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5},
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:30:00Z",
                                "lifecycle_phase": "scan",
                                "lifecycle_phase": "scan",
                                "active_targets_present": False,
                                "scan_phase": True,
                                "market_truth_required": False,
                                "kill_switch": False,
                                "book_feed": {"connected": False, "reconnects": 0, "last_msg_age_sec": 45.0},
                                "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "events_2099-01-01.jsonl").write_text("", encoding="utf-8")
            (log_dir / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")

            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1},
                        "websocket": {
                            "min_status_rows": 1,
                            "max_book_feed_down_ratio": 1.0,
                            "max_chainlink_down_ratio": 1.0,
                            "max_book_feed_reconnects_per_hour": 1000000.0,
                            "max_chainlink_reconnects_per_hour": 1000000.0,
                            "max_book_feed_last_msg_age_sec": 1000000.0,
                            "max_chainlink_last_tick_age_sec": 1000000.0,
                            "max_chainlink_dropped_ticks": 1000000.0,
                            "max_chainlink_queue_size": 1000000.0,
                        },
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.0, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertTrue(any("soak_runtime_non_promotable:" in f for f in result["findings"]))

    def test_soak_gate_reliability_mode_treats_quote_uptime_as_reliability_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "counter.book_updates": 10.0,
                    "counter.book_updates_ws": 10.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:30:00Z",
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "counter.book_updates": 20.0,
                    "counter.book_updates_ws": 20.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "ts_utc": "2099-01-01T00:05:00Z",
                        "event_type": "order_submit",
                        "order_id": "m1",
                        "reason": "maker_quote",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertTrue(any("soak_quote_uptime_too_low:" in f for f in result["findings"]))
            self.assertFalse(result["lanes"]["reliability"]["ok"])

    def test_soak_gate_skips_quote_uptime_when_no_quote_or_submit_activity_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "counter.book_updates": 10.0,
                    "counter.book_updates_ws": 10.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:30:00Z",
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "counter.book_updates": 20.0,
                    "counter.book_updates_ws": 20.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.5, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(any("soak_quote_uptime_too_low:" in f for f in result["findings"]), msg=result["findings"])
            quote_trace = next(
                item for item in result["decision_trace"] if item.get("check") == "soak_quote_uptime_ratio"
            )
            self.assertTrue(bool(quote_trace.get("passed")))
            self.assertIn("applicable=0", str(quote_trace.get("note") or ""))
            self.assertFalse(bool(result["soak_report"].get("quote_uptime_applicable")))

    def test_soak_gate_reliability_fails_when_active_targets_have_no_meaningful_participation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "lifecycle_phase": "active",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "gauge.actions_last_cycle": 0,
                    "gauge.order_submission_attempts_last_cycle": 0,
                    "counter.book_updates": 10.0,
                    "counter.book_updates_ws": 10.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:30:00Z",
                    "lifecycle_phase": "active",
                    "active_targets_present": True,
                    "market_truth_required": True,
                    "gauge.open_orders": 0,
                    "gauge.quote_active": 0,
                    "gauge.actions_last_cycle": 0,
                    "gauge.order_submission_attempts_last_cycle": 0,
                    "counter.book_updates": 20.0,
                    "counter.book_updates_ws": 20.0,
                    "pair_truth_pair_count": 1.0,
                    "pair_truth_missing_pair_count": 0.0,
                    "pair_truth_one_sided_pair_count": 0.0,
                    "pair_truth_authoritative_pair_count": 1.0,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.5},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 0.5, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_path.write_text(
                "\n".join(
                    json.dumps(
                        {"run_id": run_id, "ts_utc": "2099-01-01T00:00:01Z", "event_type": event_type}
                    )
                    for event_type in ("targets_updated", "targets_refreshed")
                )
                + "\n",
                encoding="utf-8",
            )
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {"min_duration_minutes": 20, "min_quote_uptime_ratio": 0.0, "max_error_rows": 0},
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(
                    "soak_active_target_execution_participation_missing:" in finding
                    for finding in result["findings"]
                ),
                msg=result["findings"],
            )
            self.assertFalse(result["lanes"]["reliability"]["ok"])

    def test_soak_gate_quote_uptime_tolerance_is_explicit_and_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump(
                    {
                        "comparison_tolerance": {
                            "metrics": {
                                "quote_uptime_ratio": {"min_eps": 0.001},
                            }
                        },
                        "stage_order": ["paper"],
                        "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.0}},
                    }
                ),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 1.0005,
                            "max_error_rows": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertIn("comparison_tolerance", result["soak_report"])
            self.assertEqual(
                result["soak_report"]["comparison_tolerance"]["metric_overrides"]["quote_uptime_ratio"]["min_eps"],
                0.001,
            )
            self.assertEqual(
                result["readiness"]["comparison_tolerance"]["metric_overrides"]["quote_uptime_ratio"]["min_eps"],
                0.001,
            )

    def test_soak_gate_opportunity_aware_maker_enforcement_uses_maker_scope_distribution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_no_submission",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "token_lag_not_verified_for_maker",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "taker",
                    "action_taken": "none",
                    "block_reason": "phase_disallow_taker",
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.0}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.0,
                            "max_error_rows": 0,
                            "min_maker_submits": 1,
                            "maker_submit_enforcement": {
                                "mode": "opportunity_aware",
                                "min_opportunity_rows": 1,
                                "non_actionable_block_reasons": [
                                    "maker_no_submission",
                                    "token_lag_not_verified_for_maker",
                                ],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertFalse(any("soak_maker_submits_too_low:" in finding for finding in result["findings"]))
            maker_enforcement = result["soak_report"]["maker_submit_enforcement"]
            self.assertEqual(maker_enforcement["mode"], "opportunity_aware")
            self.assertFalse(maker_enforcement["applied"])
            self.assertEqual(maker_enforcement["reason"], "insufficient_actionable_opportunity_rows")
            self.assertTrue(maker_enforcement["opportunity_surface_ok"])
            self.assertEqual(maker_enforcement["required_submits"], 0.0)
            self.assertEqual(maker_enforcement["maker_rows_total"], 2.0)
            self.assertEqual(maker_enforcement["maker_non_actionable_block_rows"], 2.0)
            self.assertEqual(maker_enforcement["maker_actionable_opportunity_rows"], 0.0)

    def test_soak_gate_default_opportunity_policy_excludes_invalid_maker_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "market_probability_missing",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "time_remaining_sec_invalid",
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.0}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.0,
                            "max_error_rows": 0,
                            "min_maker_submits": 1,
                            "maker_submit_enforcement": {
                                "mode": "opportunity_aware",
                                "min_opportunity_rows": 1,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertFalse(any("soak_maker_submits_too_low:" in finding for finding in result["findings"]))
            maker_enforcement = result["soak_report"]["maker_submit_enforcement"]
            self.assertFalse(maker_enforcement["applied"])
            self.assertEqual(maker_enforcement["required_submits"], 0.0)
            self.assertEqual(maker_enforcement["maker_rows_total"], 2.0)
            self.assertEqual(maker_enforcement["maker_non_actionable_block_rows"], 2.0)
            self.assertEqual(maker_enforcement["maker_actionable_opportunity_rows"], 0.0)

    def test_soak_gate_opportunity_aware_caps_required_submits_by_actionable_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "maker_timing_gate_closed",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "token_lag_not_verified_for_maker",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "quote_quality_skip_queue_depth",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:03Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "quote_quality_skip_fill_probability",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:04Z",
                    "event_type": "order_submit",
                    "order_id": "m1",
                    "reason": "maker_quote",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:05Z",
                    "event_type": "order_submit",
                    "order_id": "m2",
                    "reason": "maker_quote",
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.0}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.0,
                            "max_error_rows": 0,
                            "min_maker_submits": 50,
                            "maker_submit_enforcement": {
                                "mode": "opportunity_aware",
                                "min_opportunity_rows": 1,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertFalse(any("soak_maker_submits_too_low:" in finding for finding in result["findings"]))
            maker_enforcement = result["soak_report"]["maker_submit_enforcement"]
            self.assertEqual(maker_enforcement["mode"], "opportunity_aware")
            self.assertTrue(maker_enforcement["applied"])
            self.assertEqual(maker_enforcement["required_submits"], 2.0)
            self.assertEqual(maker_enforcement["maker_rows_total"], 4.0)
            self.assertEqual(maker_enforcement["maker_non_actionable_block_rows"], 2.0)
            self.assertEqual(maker_enforcement["maker_actionable_opportunity_rows"], 2.0)

    def test_soak_gate_default_opportunity_policy_excludes_lifecycle_residue_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            log_dir = self._write_fixture(root, run_id)
            event_path = log_dir / "events_2099-01-01.jsonl"
            event_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "phase_disallow_maker",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:01Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "settlement_hold_required",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:02Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "open_order_cleanup_required",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:03Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "quote_quality_skip_queue_depth",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:04Z",
                    "event_type": "edge_evaluation",
                    "evaluation_scope": "maker",
                    "action_taken": "none",
                    "block_reason": "quote_quality_skip_fill_probability",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:05Z",
                    "event_type": "order_submit",
                    "order_id": "m1",
                    "reason": "maker_quote",
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:06Z",
                    "event_type": "order_submit",
                    "order_id": "m2",
                    "reason": "maker_quote",
                },
            ]
            event_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            policy = root / "policy.yaml"
            policy.write_text(
                yaml.safe_dump({"stage_order": ["paper"], "stages": {"paper": {"min_status_rows": 1, "min_quote_uptime_ratio": 0.0}}}),
                encoding="utf-8",
            )
            budget = root / "budget.yaml"
            budget.write_text(
                yaml.safe_dump(
                    {
                        "gate_mode": "reliability",
                        "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                        "performance": {"min_status_rows": 1, "max_order_capacity_used_ratio": 1.0, "max_cancel_capacity_used_ratio": 1.0},
                        "websocket": {"min_status_rows": 1, "max_book_feed_down_ratio": 1.0, "max_chainlink_down_ratio": 1.0},
                        "readiness": {"policy": str(policy), "required_stage": "paper"},
                        "soak": {
                            "min_duration_minutes": 20,
                            "min_quote_uptime_ratio": 0.0,
                            "max_error_rows": 0,
                            "min_maker_submits": 50,
                            "maker_submit_enforcement": {
                                "mode": "opportunity_aware",
                                "min_opportunity_rows": 1,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_gate(log_dir=log_dir, run_id=run_id, budget_path=budget)
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertFalse(any("soak_maker_submits_too_low:" in finding for finding in result["findings"]))
            maker_enforcement = result["soak_report"]["maker_submit_enforcement"]
            self.assertEqual(maker_enforcement["required_submits"], 2.0)
            self.assertEqual(maker_enforcement["maker_rows_total"], 5.0)
            self.assertEqual(maker_enforcement["maker_non_actionable_block_rows"], 3.0)
            self.assertEqual(maker_enforcement["maker_actionable_opportunity_rows"], 2.0)


if __name__ == "__main__":
    unittest.main()
