import tempfile
import unittest
from pathlib import Path
import json

from scripts.paper_harness_audit import run_audit


class PaperHarnessAuditTests(unittest.TestCase):
    def test_paper_harness_audit_passes_canonical_profile(self):
        result = run_audit(
            config_path=Path("configs/profiles/paper_universal.yaml"),
            log_dir=None,
            run_id="",
            skip_run_integrity=True,
            min_status_rows=1,
            max_status_age_sec=60.0,
        )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        self.assertEqual(result["finding_count"], 0)

    def test_paper_harness_audit_passes_primary_execution_config(self):
        result = run_audit(
            config_path=Path("execution_config.yaml"),
            log_dir=None,
            run_id="",
            skip_run_integrity=True,
            min_status_rows=1,
            max_status_age_sec=60.0,
        )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        self.assertEqual(result["finding_count"], 0)

    def test_paper_harness_audit_rejects_nonrealistic_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "bad.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        "extends: /home/odah/bro/base/configs/profiles/paper_universal.yaml",
                        "profile:",
                        "  name: paper_variant",
                        "chainlink:",
                        "  enabled: false",
                        "market_data:",
                        "  ws:",
                        "    enabled: false",
                        "runtime:",
                        "  paper_passive_touch_fill_enabled: true",
                        "  paper_passive_touch_fill_ratio: 0.15",
                        "  paper_background_fill_ratio: 0.03",
                        "  paper_enforce_setup_lock: false",
                        "doctrine:",
                        "  mode: degraded",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_audit(
                config_path=cfg,
                log_dir=None,
                run_id="",
                skip_run_integrity=True,
                min_status_rows=1,
                max_status_age_sec=60.0,
            )
        self.assertFalse(result["ok"])
        findings = set(result.get("findings", []))
        self.assertIn("paper_harness_chainlink_disabled", findings)
        self.assertIn("paper_harness_book_ws_disabled", findings)
        self.assertIn("paper_harness_profile_name_invalid:paper_variant", findings)
        self.assertIn("paper_harness_passive_touch_fill_enabled", findings)
        self.assertIn("paper_harness_touch_fill_ratio_positive", findings)
        self.assertIn("paper_harness_background_fill_ratio_positive", findings)
        self.assertIn("paper_harness_doctrine_mode_invalid:degraded", findings)

    def test_paper_harness_audit_rejects_degraded_profile_class(self):
        result = run_audit(
            config_path=Path("execution_config_nometrics_runtime.yaml"),
            log_dir=None,
            run_id="",
            skip_run_integrity=True,
            min_status_rows=1,
            max_status_age_sec=60.0,
        )
        self.assertFalse(result["ok"])
        findings = set(result.get("findings", []))
        self.assertIn("paper_harness_profile_class_invalid:degraded", findings)
        self.assertIn("paper_harness_doctrine_mode_invalid:degraded", findings)

    def test_paper_harness_audit_flags_non_promotable_runtime_classification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-standdown"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "paper_universal",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((root / "status_2099-01-01.jsonl").resolve()),
                        "events_path": str((root / "events_2099-01-01.jsonl").resolve()),
                        "start_ts": "2099-01-01T00:00:00.000Z",
                        "mode": "paper",
                    }
                ),
                encoding="utf-8",
            )
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
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
                                "ts_utc": "2099-01-01T00:30:00Z",
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
            (root / "events_2099-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")

            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=False,
                min_status_rows=1,
                max_status_age_sec=3153600000.0,
            )
            self.assertFalse(result["ok"])
            self.assertIn(
                "paper_harness_runtime_non_promotable:NON_PROMOTABLE_NO_PARTICIPATION",
                set(result.get("findings", [])),
            )

    def test_paper_harness_audit_flags_high_rest_fallback_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-rest-ratio"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "paper_universal",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((root / "status_2099-01-01.jsonl").resolve()),
                        "events_path": str((root / "events_2099-01-01.jsonl").resolve()),
                        "start_ts": "2099-01-01T00:00:00.000Z",
                        "mode": "paper",
                    }
                ),
                encoding="utf-8",
            )
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "runtime_state": "active",
                                "active_targets_present": True,
                                "book_feed_required": True,
                                "kill_switch": False,
                                "gauge.open_orders": 1,
                                "counter.book_updates": 10.0,
                                "counter.book_updates_ws": 1.0,
                                "counter.book_updates_rest": 9.0,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:30:00Z",
                                "runtime_state": "active",
                                "active_targets_present": True,
                                "book_feed_required": True,
                                "kill_switch": False,
                                "gauge.open_orders": 1,
                                "counter.book_updates": 20.0,
                                "counter.book_updates_ws": 2.0,
                                "counter.book_updates_rest": 18.0,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "events_2099-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")

            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=False,
                min_status_rows=1,
                max_status_age_sec=3153600000.0,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("paper_harness_book_updates_rest_ratio_high:" in finding for finding in result.get("findings", [])),
                msg=result.get("findings", []),
            )

    def test_paper_harness_audit_uses_soak_budget_as_threshold_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-budget-threshold"
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "manifest_schema_version": 2,
                        "profile_name": "paper_universal",
                        "git_commit": "deadbeef",
                        "config_fingerprint_sha256": "a" * 64,
                        "status_path": str((root / "status_2099-01-01.jsonl").resolve()),
                        "events_path": str((root / "events_2099-01-01.jsonl").resolve()),
                        "start_ts": "2099-01-01T00:00:00.000Z",
                        "mode": "paper",
                    }
                ),
                encoding="utf-8",
            )
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "runtime_state": "active",
                                "active_targets_present": True,
                                "book_feed_required": True,
                                "kill_switch": False,
                                "gauge.open_orders": 1,
                                "counter.book_updates": 10.0,
                                "counter.book_updates_ws": 7.0,
                                "counter.book_updates_rest": 3.0,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:30:00Z",
                                "runtime_state": "active",
                                "active_targets_present": True,
                                "book_feed_required": True,
                                "kill_switch": False,
                                "gauge.open_orders": 1,
                                "counter.book_updates": 20.0,
                                "counter.book_updates_ws": 14.0,
                                "counter.book_updates_rest": 6.0,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "events_2099-01-01.jsonl").write_text("", encoding="utf-8")
            (root / "errors_2099-01-01.jsonl").write_text("", encoding="utf-8")
            budget_path = root / "budget.yaml"
            budget_path.write_text(
                "\n".join(
                    [
                        "websocket:",
                        "  max_book_updates_rest_ratio: 0.20",
                        "  min_book_updates_ws_delta: 1",
                        "  min_book_updates_total_delta: 1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=False,
                min_status_rows=1,
                max_status_age_sec=3153600000.0,
                budget_path=budget_path,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("paper_harness_book_updates_rest_ratio_high:" in finding for finding in result.get("findings", [])),
                msg=result.get("findings", []),
            )

    def test_paper_harness_audit_surfaces_decision_input_counts_and_blocks_emulated_actions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-edge-input-types"
            (root / "events_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "action_taken": "maker",
                                "decision_input_emulated": True,
                                "decision_input_data_class": "emulated",
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:01Z",
                                "action_taken": "none",
                                "decision_input_emulated": False,
                                "decision_input_data_class": "observed_live",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=True,
                min_status_rows=1,
                max_status_age_sec=60.0,
            )
            self.assertFalse(result["ok"])
            findings = set(result.get("findings", []))
            self.assertIn("paper_harness_edge_action_on_emulated_input:1", findings)
            checks = result.get("checks", {})
            self.assertEqual(int(checks.get("edge_evaluation_rows", 0)), 2)
            input_counts = checks.get("edge_decision_input_type_counts", {})
            self.assertEqual(int(input_counts.get("emulated", 0)), 1)
            self.assertEqual(int(input_counts.get("observed_live", 0)), 1)
            action_counts = checks.get("edge_action_counts_by_input_type", {})
            self.assertEqual(int(action_counts.get("emulated", 0)), 1)

    def test_paper_harness_audit_surfaces_realism_policy_and_claim_boundary(self):
        result = run_audit(
            config_path=Path("configs/profiles/paper_universal.yaml"),
            log_dir=None,
            run_id="",
            skip_run_integrity=True,
            min_status_rows=1,
            max_status_age_sec=60.0,
        )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        checks = result.get("checks", {})
        maker_policy = checks.get("maker_policy", {})
        taker_policy = checks.get("taker_policy", {})
        claim_boundary = checks.get("paper_claim_boundary", {})
        realism_summary = checks.get("paper_execution_realism_summary", {})
        self.assertEqual(maker_policy.get("maker_realism_class"), "not_modeled")
        self.assertEqual(maker_policy.get("queue_position_mode"), "not_modeled")
        self.assertEqual(taker_policy.get("taker_realism_class"), "bounded_approximation")
        self.assertEqual(taker_policy.get("price_basis"), "best_touch")
        self.assertEqual(claim_boundary.get("control_plane_truth"), "authoritative")
        self.assertIs(claim_boundary.get("live_pnl_equivalence"), False)
        self.assertIn(claim_boundary.get("decision_source_truth"), {"authoritative", "bounded_approximation"})
        self.assertIn(claim_boundary.get("action_source_truth"), {"authoritative", "bounded_approximation"})
        self.assertEqual(claim_boundary.get("source_truth"), claim_boundary.get("action_source_truth"))
        self.assertEqual(claim_boundary.get("source_truth_semantics"), "legacy_alias_of_action_source_truth")
        self.assertEqual(realism_summary.get("maker_realism_class"), "not_modeled")
        self.assertEqual(realism_summary.get("taker_realism_class"), "bounded_approximation")

    def test_paper_harness_audit_flags_missing_realism_disclosure_and_fill_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-missing-realism-disclosure"
            (root / "events_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "action_taken": "taker",
                                "decision_input_emulated": False,
                                "decision_input_data_class": "observed_live",
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "order_submit",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:01Z",
                                "order_id": "paper-order-1",
                                "token_id": "tok-1",
                                "side": "BUY",
                                "price": 0.5,
                                "size": 5.0,
                                "reason_code": "sniper_taker_chainlink",
                                "execution_preference": "taker_only",
                                "market_id": "m-1",
                                "window_id": "w-1",
                                "stage": "SNIPER_PRIMARY",
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "fill",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:02Z",
                                "trade_id": "paper-trade-aaaaaaaaaaaa-1",
                                "order_id": "paper-order-1",
                                "token_id": "tok-1",
                                "side": "BUY",
                                "price": 0.5,
                                "size": 1.0,
                                "source": "paper",
                                "decision_input_type": "observed_live",
                                "execution_realism_class": "bounded_approximation",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=True,
                min_status_rows=1,
                max_status_age_sec=60.0,
            )
        self.assertFalse(result["ok"])
        findings = set(result.get("findings", []))
        self.assertIn("paper_harness_edge_decision_input_type_missing:1", findings)
        self.assertIn("paper_harness_edge_execution_realism_class_missing:1", findings)
        self.assertIn("paper_harness_fill_policy_disclosure_missing:1", findings)
        claim_boundary = result.get("checks", {}).get("paper_claim_boundary", {})
        self.assertIs(claim_boundary.get("live_pnl_equivalence"), False)

    def test_paper_harness_claim_boundary_splits_decision_vs_action_source_truth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "audit-source-truth-layer-split"
            (root / "events_2099-01-01.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:00Z",
                                "action_taken": "none",
                                "block_reason": "stale_book",
                                "decision_input_source": "rest",
                                "decision_input_emulated": False,
                                "decision_input_data_class": "observed_other",
                                "decision_input_type": "bounded_derived",
                                "execution_realism_class": "not_modeled",
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "edge_evaluation",
                                "run_id": run_id,
                                "ts_utc": "2099-01-01T00:00:01Z",
                                "action_taken": "taker",
                                "decision_input_source": "ws",
                                "decision_input_emulated": False,
                                "decision_input_data_class": "observed_live",
                                "decision_input_type": "observed_live",
                                "execution_realism_class": "bounded_approximation",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_audit(
                config_path=Path("configs/profiles/paper_universal.yaml"),
                log_dir=root,
                run_id=run_id,
                skip_run_integrity=True,
                min_status_rows=1,
                max_status_age_sec=60.0,
            )
        self.assertTrue(result["ok"], msg=f"unexpected findings: {result['findings']}")
        claim_boundary = result.get("checks", {}).get("paper_claim_boundary", {})
        self.assertEqual(claim_boundary.get("decision_source_truth"), "bounded_approximation")
        self.assertEqual(claim_boundary.get("action_source_truth"), "authoritative")
        self.assertEqual(claim_boundary.get("source_truth"), "authoritative")
        self.assertEqual(claim_boundary.get("source_truth_semantics"), "legacy_alias_of_action_source_truth")


if __name__ == "__main__":
    unittest.main()
