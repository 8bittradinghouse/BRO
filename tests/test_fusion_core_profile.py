import json
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fusion_core_profile  # noqa: E402


def _write_json(path: pathlib.Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: pathlib.Path, payloads: list[dict]) -> None:
    path.write_text("".join(json.dumps(payload, sort_keys=True) + "\n" for payload in payloads), encoding="utf-8")


def _build_maker_records(run_id: str, target_prefix: str, singlefill_count: int, multifill_count: int) -> list[dict]:
    records: list[dict] = []
    for idx in range(singlefill_count):
        singlefill_correct = idx % 2 == 0
        records.append(
            {
                "order_submit_id": f"{run_id}-single-{idx}",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": f"{target_prefix}-single-{idx % 3}",
                "outcome_truth_status": "complete",
                "fill_count": 1,
                "decision_quality": "correct" if singlefill_correct else "incorrect",
                "decision_component_x_size": 4.0 if singlefill_correct else -5.0,
                "execution_component_x_size": 1.0 if singlefill_correct else 0.5,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "BUY",
                "mid_price_decision": 0.4,
                "mid_price_eval": 0.45,
            }
        )
    for idx in range(multifill_count):
        records.append(
            {
                "order_submit_id": f"{run_id}-multi-{idx}",
                "submission_lane_truth": "maker",
                "submission_scope_hint": "maker",
                "target_ref": f"{target_prefix}-multi-{idx % 2}",
                "outcome_truth_status": "complete",
                "fill_count": 3,
                "decision_quality": "incorrect",
                "decision_component_x_size": -30.0,
                "execution_component_x_size": 10.0,
                "decision_reference_basis": "direct_book_midpoint",
                "eval_reference_basis": "edge_market_midpoint_series",
                "evaluation_horizon_ms": 5000,
                "order_side": "SELL",
                "mid_price_decision": 0.55,
                "mid_price_eval": 0.45,
            }
        )
    records.append(
        {
            "order_submit_id": f"{run_id}-incomplete",
            "submission_lane_truth": "maker",
            "submission_scope_hint": "maker",
            "target_ref": f"{target_prefix}-incomplete",
            "outcome_truth_status": "unknown_incomplete_lifecycle",
            "fill_count": 0,
            "decision_quality": "correct",
            "decision_reference_basis": "direct_book_midpoint",
            "eval_reference_basis": "edge_market_midpoint_series",
            "evaluation_horizon_ms": 5000,
        }
    )
    return records


def _build_run_row(
    run_id: str,
    report_dir: pathlib.Path,
    *,
    repeat_clusters: int,
    complement_clusters: int,
    maker_complete_record_count: int = 9,
    maker_incomplete_record_count: int = 1,
    maker_multifill_complete_count: int = 2,
    maker_complete_bad_ratio: float = 2.0 / 9.0,
    taker_decision_count: float = 20.0,
    valuation_bruise_state: str = "recovered_clean",
) -> dict:
    return {
        "run_id": run_id,
        "report_dir": str(report_dir),
        "maker_submits": 10.0,
        "maker_quote_quality_skip_total_count": 2.0,
        "maker_sizing_reject_total_count": 1.0,
        "maker_no_submit_total_count": 3.0,
        "maker_window_sizing_reject_count": 1.0,
        "maker_window_low_price_viability_floor": 0.04375,
        "maker_window_viable_row_count": 6.0,
        "maker_window_impossible_row_count": 4.0,
        "maker_window_unknown_viability_row_count": 0.0,
        "maker_window_viable_target_count": 1.0,
        "maker_window_impossible_target_count": 1.0,
        "maker_window_mixed_viability_target_count": 0.0,
        "maker_window_unknown_viability_target_count": 0.0,
        "maker_window_low_price_conflict_price_band": {"min": 0.02, "p50": 0.03, "max": 0.04},
        "maker_window_queue_depth_on_viable_targets_count": 1.0,
        "maker_window_queue_depth_on_impossible_targets_count": 2.0,
        "maker_window_queue_depth_on_mixed_targets_count": 0.0,
        "maker_window_queue_depth_on_unknown_targets_count": 0.0,
        "maker_raw_queue_depth_event_count": 3.0,
        "maker_raw_queue_depth_near_threshold_event_count": 2.0,
        "maker_raw_queue_depth_hard_miss_event_count": 1.0,
        "maker_min_notional_max_shares_conflict_rows": 1.0,
        "maker_same_target_repeat_cluster_count": repeat_clusters,
        "maker_same_target_repeat_cluster_summary": [
            {
                "target_ref": f"{run_id}-target",
                "submit_count": 3.0,
                "complete_count": 2.0,
                "complete_decision_debt_sum": -12.0,
            }
        ]
        if repeat_clusters
        else [],
        "maker_complement_pair_cluster_count": complement_clusters,
        "maker_complement_pair_cluster_decision_debt_sum": -40.0 * complement_clusters,
        "maker_complement_pair_cluster_examples": [
            {"order_submit_id_a": f"{run_id}-pair-a", "order_submit_id_b": f"{run_id}-pair-b"}
        ]
        if complement_clusters
        else [],
        "maker_complete_record_count": maker_complete_record_count,
        "maker_incomplete_record_count": maker_incomplete_record_count,
        "maker_complete_bad_ratio": maker_complete_bad_ratio,
        "maker_incomplete_bad_ratio": 0.0,
        "maker_multifill_complete_count": maker_multifill_complete_count,
        "maker_multifill_complete_incorrect_ratio": 1.0 if maker_multifill_complete_count else None,
        "maker_execution_rescue_overcome_count": 0,
        "maker_execution_rescue_ratio_summary": {"count": 9, "mean": 0.5, "p50": 0.4, "p90": 0.8},
        "maker_reference_basis_summary": {
            "decision_reference_basis_distribution": {"direct_book_midpoint": 10},
            "eval_reference_basis_distribution": {"edge_market_midpoint_series": 10},
        },
        "maker_outcome_horizon_ms": 5000,
        "maker_reference_bounded_fallback_activity": 4.0,
        "valuation_bruise_state": valuation_bruise_state,
        "taker_decision_count": taker_decision_count,
        "taker_submits": 5.0 if taker_decision_count else 0.0,
        "taker_fills": 4.0 if taker_decision_count else 0.0,
        "taker_final_window_decision_count": 15.0 if taker_decision_count else 0.0,
        "taker_outside_window_decision_count": 2.0 if taker_decision_count else 0.0,
    }


def _build_bundle(
    root: pathlib.Path,
    *,
    run_count: int,
    with_manifest: bool,
    explicit_paths_only: bool = False,
    singlefill_count: int = 7,
    multifill_count: int = 2,
    taker_decision_count: float = 20.0,
    deep_record_run_count: int | None = None,
    valuation_bruise_states: list[str] | None = None,
) -> tuple[pathlib.Path, dict[str, pathlib.Path]]:
    root.mkdir(parents=True, exist_ok=True)
    bundle_dir = root / "bundle"
    bundle_dir.mkdir()
    report_root = root / "reports"
    report_root.mkdir()
    rows = []
    effective_deep_record_run_count = run_count if deep_record_run_count is None else deep_record_run_count
    for idx in range(run_count):
        run_id = f"run-{idx + 1}"
        report_dir = report_root / run_id
        report_dir.mkdir()
        if idx < effective_deep_record_run_count:
            _write_jsonl(
                report_dir / "outcome_truth_records.jsonl",
                _build_maker_records(run_id, f"target-{idx+1}", singlefill_count, multifill_count),
            )
        valuation_bruise_state = (
            valuation_bruise_states[idx]
            if valuation_bruise_states is not None and idx < len(valuation_bruise_states)
            else "recovered_clean"
        )
        valuation_degraded_rows = 0.0 if valuation_bruise_state == "none" else 1.0
        valuation_hard_degraded_rows = 1.0 if any(
            token in valuation_bruise_state for token in ("hard", "open_", "unpriceable")
        ) else 0.0
        held_unpriceable_started_count = 1.0 if "unpriceable" in valuation_bruise_state else 0.0
        held_unpriceable_recovered_count = 1.0 if valuation_bruise_state == "recovered_clean" else 0.0
        _write_json(
            report_dir / "nightly_soak_report.json",
            {
                "edge_truth": {
                    "maker_no_submission_category_distribution": {
                        "quote_quality_skip_fill_probability": 2,
                        "quote_quality_skip_queue_depth": 1,
                        "sizing_reject": 1,
                    },
                    "maker_no_submission_cause_distribution": {
                        "submit_rejected_quote_quality_skip_fill_probability": 2,
                        "submit_rejected_quote_quality_skip_queue_depth": 1,
                        "submit_rejected_sizing_reject": 1,
                    },
                    "maker_reference_bounded_fallback_activity": 4.0,
                },
                "maker_sizing_competitiveness": {
                    "maker_sizing_reject_rows": 2.0,
                    "maker_size_resolution_rows": 10.0,
                    "maker_submit_rows": 10.0,
                },
                "valuation_truth": {
                    "status_rows": 21.0,
                    "valuation_bruise_state": valuation_bruise_state,
                    "valuation_degraded_rows": valuation_degraded_rows,
                    "valuation_hard_degraded_rows": valuation_hard_degraded_rows,
                    "held_unpriceable_started_count": held_unpriceable_started_count,
                    "held_unpriceable_recovered_count": held_unpriceable_recovered_count,
                    "valuation_degraded_reason_family_counts_run": {
                        "degraded_using_last_known_mid": 1
                    },
                },
            },
        )
        rows.append(
            _build_run_row(
                run_id,
                report_dir,
                repeat_clusters=3 if idx < 2 else 0,
                complement_clusters=5 if idx == 0 else 0,
                maker_complete_record_count=singlefill_count + multifill_count,
                maker_incomplete_record_count=1,
                maker_multifill_complete_count=multifill_count,
                maker_complete_bad_ratio=(float(multifill_count) / float(singlefill_count + multifill_count))
                if (singlefill_count + multifill_count)
                else None,
                taker_decision_count=taker_decision_count,
                valuation_bruise_state=valuation_bruise_state,
            )
        )

    _write_jsonl(bundle_dir / "run_index.jsonl", rows)
    _write_json(
        bundle_dir / "anomaly_summary.json",
        {
            "schema_version": 5,
            "tool_alias": "FMA",
            "run_count": run_count,
            "coverage": {"field_coverage": {}},
        },
    )
    _write_json(
        bundle_dir / "metric_catalog.json",
        {
            "schema_version": 5,
            "tool_alias": "FMA",
            "run_count": run_count,
            "source_count": 3,
        },
    )
    if with_manifest:
        _write_json(
            bundle_dir / "fma_bundle_manifest.json",
            {
                "manifest_schema_version": 1,
                "bundle_closed_snapshot": True,
                "tool_id": "FM-1A1",
                "tool_alias": "FMA",
                "tool_name": "Forge Masters Archiver",
                "tool_schema_version": 5,
                "selected_run_count": run_count,
            },
        )

    if explicit_paths_only:
        return bundle_dir, {
            "run_index_jsonl": bundle_dir / "run_index.jsonl",
            "anomaly_summary_json": bundle_dir / "anomaly_summary.json",
            "metric_catalog_json": bundle_dir / "metric_catalog.json",
        }
    return bundle_dir, {}


class FusionCoreProfileTests(unittest.TestCase):
    def test_corpus_bundle_with_manifest_produces_decoupled_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            bundle_dir, _ = _build_bundle(root, run_count=3, with_manifest=True)
            out_dir = root / "lathe-out"

            outputs = fusion_core_profile.build_profiles(bundle_dir=bundle_dir, out_dir=out_dir, mode="auto")

            self.assertIn("fusion_core_profile_catalog_json", outputs)
            self.assertIn("fusion_core_calibration_audit_json", outputs)
            self.assertIn("fusion_core_threshold_pressure_matrix_json", outputs)
            audit = json.loads((out_dir / "fusion_core_input_contract_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["manifest_status"], "present")
            self.assertEqual(audit["snapshot_contract_status"], "explicit_closed_snapshot")
            self.assertEqual(audit["snapshot_integrity_class"], "closed_snapshot")
            self.assertEqual(audit["manifest_derivation_reason"], "preferred_manifest_present")
            self.assertEqual(audit["resolved_mode"], "corpus")
            self.assertTrue(audit["ok"])
            self.assertFalse(audit["ok_with_warnings"])
            self.assertEqual(audit["contract_health"], "clean")
            self.assertEqual(audit["warning_count"], 0)
            self.assertEqual(audit["deep_artifact_coverage_summary"]["runs_with_nightly_soak_report"], 3)

            readiness = json.loads((out_dir / "fusion_core_lane_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["lanes"]["maker"]["depth_class"], "full_depth")
            self.assertEqual(readiness["lanes"]["maker"]["deep_coverage_ratio"], 1.0)
            self.assertEqual(readiness["lanes"]["maker"]["promotion_blockers"], [])
            self.assertEqual(readiness["lanes"]["taker"]["depth_class"], "bounded_depth")
            self.assertEqual(readiness["lanes"]["sniper"]["depth_class"], "bounded_depth")
            self.assertFalse(readiness["lanes"]["sniper"]["can_emit_profiles"])

            profiles = json.loads((out_dir / "fusion_core_profile_catalog.json").read_text(encoding="utf-8"))
            profile_ids = [profile["profile_id"] for profile in profiles]
            self.assertEqual(profile_ids, sorted(profile_ids))

            families = {profile["profile_family"]: profile for profile in profiles}
            self.assertIn("outcome_balance", families)
            self.assertIn("multifill_wound", families)
            self.assertIn("singlefill_strength", families)
            self.assertIn("repeat_target_cluster", families)
            self.assertIn("complement_pair_cluster", families)
            self.assertIn("friction_burden", families)
            self.assertIn("viability_shadow", families)
            self.assertIn("valuation_pressure", families)
            self.assertIn("window_conversion_overview", families)
            self.assertEqual(families["outcome_balance"]["stability_grade"], "strong")
            self.assertTrue(families["outcome_balance"]["promotion_readiness"]["strong_ready"])
            self.assertEqual(families["multifill_wound"]["stability_grade"], "bounded")
            self.assertEqual(families["repeat_target_cluster"]["stability_grade"], "bounded")
            self.assertIn("bounded_repeat_target_heuristic", families["repeat_target_cluster"]["heuristic_flags"])
            self.assertEqual(families["outcome_balance"]["lifecycle_basis"], "derived_fill_geometry")
            self.assertEqual(families["singlefill_strength"]["stability_grade"], "bounded")
            self.assertIn("family_signal_not_positive", families["singlefill_strength"]["downgrade_reason_codes"])
            self.assertIn("family_signal_not_positive", families["singlefill_strength"]["promotion_readiness"]["strong_blockers"])
            self.assertEqual(families["window_conversion_overview"]["stability_grade"], "bounded")
            self.assertIn("lane_depth_cap", families["window_conversion_overview"]["promotion_readiness"]["strong_blockers"])
            self.assertEqual(families["viability_shadow"]["stability_grade"], "strong")
            self.assertEqual(families["viability_shadow"]["metrics"]["impossible_row_total"], 12.0)
            self.assertEqual(
                families["viability_shadow"]["metrics"]["queue_depth_on_impossible_targets_total"],
                6.0,
            )
            self.assertEqual(
                families["viability_shadow"]["metrics"]["maker_min_notional_max_shares_conflict_rows_total"],
                3.0,
            )
            self.assertEqual(
                families["execution_rescue_geometry"]["metrics"]["execution_rescue_ratio_excluded_low_debt_count"],
                0,
            )
            self.assertEqual(families["valuation_pressure"]["stability_grade"], "strong")
            self.assertEqual(families["valuation_pressure"]["metrics"]["valuation_bruise_state"], "recovered_clean")
            self.assertEqual(
                families["valuation_pressure"]["metrics"]["valuation_bruise_state_distribution"],
                {"recovered_clean": 3.0},
            )
            self.assertIn("quote_quality_skip_decision_row_count", families["friction_burden"]["population_accounting"])

            blanks = json.loads((out_dir / "fusion_core_candidate_blanks.json").read_text(encoding="utf-8"))
            blank_families = {blank["profile_family"] for blank in blanks}
            self.assertIn("multifill_wound", blank_families)
            self.assertIn("repeat_target_cluster", blank_families)
            self.assertIn("complement_pair_cluster", blank_families)
            self.assertIn("valuation_pressure", blank_families)
            self.assertTrue(all("live threshold" in blank["safety_note"] for blank in blanks))
            complement_blank = next(blank for blank in blanks if blank["profile_family"] == "complement_pair_cluster")
            self.assertIn("sample_complement_pair_examples", complement_blank["evidence_summary"])
            self.assertNotIn("complement_pair_examples_by_run", complement_blank["evidence_summary"])

            matrix = json.loads((out_dir / "fusion_core_stability_matrix.json").read_text(encoding="utf-8"))
            self.assertEqual(matrix["mode"], "corpus")
            self.assertGreaterEqual(matrix["grade_counts"]["bounded"], 1)
            self.assertGreaterEqual(matrix["grade_counts"]["strong"], 1)

            calibration = json.loads((out_dir / "fusion_core_calibration_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(calibration["policy"]["global"]["strong_min_sample_count"], 20)
            self.assertGreaterEqual(calibration["lane_summary"]["maker"]["strong_ready_profile_count"], 1)
            self.assertIn("lane_depth_cap", calibration["lane_summary"]["taker"]["strong_blocker_counts"])

            threshold_matrix = json.loads((out_dir / "fusion_core_threshold_pressure_matrix.json").read_text(encoding="utf-8"))
            self.assertIn("current", threshold_matrix["preset_projections"])
            self.assertIn("tighter", threshold_matrix["preset_projections"])
            self.assertIn("looser", threshold_matrix["preset_projections"])
            self.assertTrue(threshold_matrix["pressure_sensitive_profiles"])
            self.assertGreaterEqual(threshold_matrix["threshold_invariant_profile_count"], 1)
            pressure_ids = {item["profile_id"] for item in threshold_matrix["pressure_sensitive_profiles"]}
            self.assertTrue(any("outcome_balance" in profile_id for profile_id in pressure_ids))
            taker_projection = threshold_matrix["preset_projections"]["looser"]["profile_projections"][
                "taker|window_conversion_overview|run_summary|basis=run_summary_mixed|horizon=run_summary_mixed|slice=window_conversion"
            ]
            self.assertEqual(taker_projection["stability_grade"], "bounded")
            self.assertIn("lane_depth_cap", taker_projection["promotion_readiness"]["strong_blockers"])

    def test_explicit_paths_specimen_mode_caps_strength_and_derives_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            bundle_dir, explicit_paths = _build_bundle(
                root,
                run_count=1,
                with_manifest=False,
                explicit_paths_only=True,
                multifill_count=0,
                taker_decision_count=0.0,
            )
            out_dir = root / "lathe-out"

            fusion_core_profile.build_profiles(
                run_index_path=explicit_paths["run_index_jsonl"],
                anomaly_summary_path=explicit_paths["anomaly_summary_json"],
                metric_catalog_path=explicit_paths["metric_catalog_json"],
                out_dir=out_dir,
                mode="auto",
            )

            audit = json.loads((out_dir / "fusion_core_input_contract_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["manifest_status"], "explicit_paths_derived")
            self.assertEqual(audit["manifest_derivation_reason"], "explicit_artifact_paths_no_bundle_manifest")
            self.assertEqual(audit["snapshot_contract_status"], "explicit_artifact_contract_derived")
            self.assertEqual(audit["resolved_mode"], "specimen")
            self.assertTrue(audit["ok"])
            self.assertTrue(audit["ok_with_warnings"])
            self.assertEqual(audit["contract_health"], "warning")

            profiles = json.loads((out_dir / "fusion_core_profile_catalog.json").read_text(encoding="utf-8"))
            self.assertTrue(profiles)
            self.assertNotIn("strong", {profile["stability_grade"] for profile in profiles})
            self.assertTrue(any("mode_cap_specimen_only" in (profile.get("downgrade_reason_codes") or []) for profile in profiles if profile["sample_count"] > 0))
            suppressed = {profile["profile_family"]: profile for profile in profiles if profile["stability_grade"] == "suppressed"}
            self.assertIn("zero_eligible_records", suppressed["multifill_wound"]["suppression_reason_codes"])

    def test_legacy_bundle_warning_and_partial_deep_coverage_are_reported_honestly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            bundle_dir, _ = _build_bundle(
                root,
                run_count=3,
                with_manifest=False,
                deep_record_run_count=2,
                valuation_bruise_states=["none", "recovered_clean", "hard_degraded_not_fully_cleared"],
            )
            out_dir = root / "lathe-out"

            fusion_core_profile.build_profiles(bundle_dir=bundle_dir, out_dir=out_dir, mode="auto")

            audit = json.loads((out_dir / "fusion_core_input_contract_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["manifest_status"], "legacy_derived")
            self.assertTrue(audit["ok"])
            self.assertTrue(audit["ok_with_warnings"])
            self.assertEqual(audit["contract_health"], "warning")
            self.assertIn("manifest_missing_using_legacy_derived", audit["warning_findings"])

            readiness = json.loads((out_dir / "fusion_core_lane_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["lanes"]["maker"]["depth_class"], "mixed_depth_partial_deep")
            self.assertAlmostEqual(readiness["lanes"]["maker"]["deep_coverage_ratio"], 2.0 / 3.0)
            self.assertIn("deep_coverage_below_full_depth_threshold", readiness["lanes"]["maker"]["promotion_blockers"])

            profiles = json.loads((out_dir / "fusion_core_profile_catalog.json").read_text(encoding="utf-8"))
            self.assertTrue(any(profile["profile_family"] == "outcome_balance" for profile in profiles))
            valuation_profile = next(profile for profile in profiles if profile["profile_family"] == "valuation_pressure")
            self.assertEqual(valuation_profile["metrics"]["valuation_bruise_state"], "mixed_bruise_states")
            self.assertEqual(
                valuation_profile["metrics"]["valuation_bruise_state_distribution"],
                {
                    "hard_degraded_not_fully_cleared": 1.0,
                    "none": 1.0,
                    "recovered_clean": 1.0,
                },
            )
            self.assertIn("lane_partial_deep_coverage", valuation_profile["promotion_readiness"]["strong_blockers"])

            threshold_matrix = json.loads((out_dir / "fusion_core_threshold_pressure_matrix.json").read_text(encoding="utf-8"))
            pressure_ids = {item["profile_id"] for item in threshold_matrix["pressure_sensitive_profiles"]}
            self.assertTrue(any("outcome_balance" in profile_id for profile_id in pressure_ids))

    def test_profile_diff_reports_grade_change_between_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            baseline_bundle, _ = _build_bundle(root / "baseline", run_count=3, with_manifest=True)
            current_bundle, _ = _build_bundle(root / "current", run_count=1, with_manifest=True)
            baseline_out = root / "baseline-out"
            current_out = root / "current-out"

            fusion_core_profile.build_profiles(bundle_dir=baseline_bundle, out_dir=baseline_out, mode="corpus")
            fusion_core_profile.build_profiles(
                bundle_dir=current_bundle,
                out_dir=current_out,
                mode="auto",
                diff_baseline_dir=baseline_out,
            )

            diff_payload = json.loads((current_out / "fusion_core_profile_diff.json").read_text(encoding="utf-8"))
            self.assertTrue(diff_payload["grade_changes"])
            self.assertEqual(diff_payload["comparison_class"], "corpus_vs_specimen")
            self.assertTrue(diff_payload["expected_mode_cap_downgrades"])
            changed_ids = {change["profile_id"] for change in diff_payload["grade_changes"]}
            self.assertTrue(any("outcome_balance" in profile_id for profile_id in changed_ids))

    def test_profile_diff_reports_metric_drift_when_grade_holds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            baseline_bundle, _ = _build_bundle(root / "baseline", run_count=3, with_manifest=True, taker_decision_count=20.0)
            current_bundle, _ = _build_bundle(root / "current", run_count=3, with_manifest=True, taker_decision_count=40.0)
            baseline_out = root / "baseline-out"
            current_out = root / "current-out"

            fusion_core_profile.build_profiles(bundle_dir=baseline_bundle, out_dir=baseline_out, mode="corpus")
            fusion_core_profile.build_profiles(
                bundle_dir=current_bundle,
                out_dir=current_out,
                mode="corpus",
                diff_baseline_dir=baseline_out,
            )

            diff_payload = json.loads((current_out / "fusion_core_profile_diff.json").read_text(encoding="utf-8"))
            self.assertEqual(diff_payload["comparison_class"], "corpus_vs_corpus")
            self.assertTrue(diff_payload["metric_value_changes"])
            self.assertTrue(diff_payload["metric_drift_candidates"])
            drift_ids = {change["profile_id"] for change in diff_payload["metric_drift_candidates"]}
            self.assertTrue(any("window_conversion_overview" in profile_id for profile_id in drift_ids))

    def test_script_text_stays_decoupled_from_fma_business_logic(self):
        script_text = (REPO_ROOT / "scripts" / "fusion_core_profile.py").read_text(encoding="utf-8")
        self.assertNotIn("import bro_metric_harvest", script_text)
        self.assertNotIn("from bro_metric_harvest", script_text)


if __name__ == "__main__":
    unittest.main()
