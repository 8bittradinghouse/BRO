from __future__ import annotations

from pathlib import Path
import unittest

from prodesk.edge_truth_contract import (
    EDGE_ACTION_MAKER,
    EDGE_ACTION_NONE,
    EDGE_ACTION_TAKER,
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPE_TAKER,
    EDGE_LIFECYCLE_PHASE_FIELD,
    EdgeInputSnapshot,
    compute_edge_value,
    is_canonical_block_reason,
    lifecycle_phase_from_payload,
    lifecycle_phase_surface_fields,
    market_truth_required_from_payload,
    phase_allows_action,
    validate_edge_inputs,
)


class EdgeTruthContractTests(unittest.TestCase):
    def test_legacy_stage_family_vocabulary_is_quarantined_to_contract_boundary(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        scan_targets = [
            repo_root / "executor.py",
            repo_root / "prodesk",
            repo_root / "scripts",
        ]
        legacy_terms = (
            "effective_stage",
            "stage_bucket",
            "raw_stage",
            "maker_new_risk_allowed",
            "normal_taker_allowed",
            "late_window_authority_class",
            "stage_disallow_",
            "_token_stage_info",
            "stage_transition",
            "_taker_stage_window_token_ids",
        )
        allowed_paths = {
            repo_root / "prodesk" / "edge_truth_legacy_replay_compat.py",
        }
        offenders: dict[str, list[str]] = {}
        for target in scan_targets:
            files = [target] if target.is_file() else list(target.rglob("*.py"))
            for path in files:
                text = path.read_text(encoding="utf-8")
                hits = [term for term in legacy_terms if term in text]
                if hits and path not in allowed_paths:
                    offenders[str(path.relative_to(repo_root))] = hits
        self.assertEqual(offenders, {})

    def test_legacy_replay_compat_imports_are_whitelisted(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        allowed_importers = {
            repo_root / "scripts" / "nightly_soak_report.py",
            repo_root / "tests" / "test_order_lifecycle_audit.py",
        }
        offenders: list[str] = []
        this_file = Path(__file__).resolve()
        for path in list((repo_root / "scripts").rglob("*.py")) + list((repo_root / "tests").rglob("*.py")):
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8")
            if "prodesk.edge_truth_legacy_replay_compat" not in text:
                continue
            if path not in allowed_importers:
                offenders.append(str(path.relative_to(repo_root)))
        self.assertEqual(offenders, [])

    def test_validate_edge_inputs_passes_with_complete_snapshot(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=0.55,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=0.3,
                lifecycle_phase="prepare",
                evaluation_scope=EDGE_EVAL_SCOPE_MAKER,
            ),
            oracle_max_tick_age_sec=1.5,
        )
        self.assertTrue(bool(out.valid))
        self.assertEqual(str(out.reason_code), "ok")

    def test_validate_edge_inputs_fails_closed_on_missing_fair_probability(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=None,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=0.3,
                lifecycle_phase="prepare",
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
            ),
            oracle_max_tick_age_sec=1.5,
        )
        self.assertFalse(bool(out.valid))
        self.assertEqual(str(out.reason_code), "fair_probability_missing")

    def test_validate_edge_inputs_fails_closed_on_stale_oracle(self) -> None:
        out = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=0.55,
                market_probability=0.50,
                time_remaining_sec=45.0,
                oracle_tick_age_sec=3.0,
                lifecycle_phase="prepare",
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
            ),
            oracle_max_tick_age_sec=1.5,
        )
        self.assertFalse(bool(out.valid))
        self.assertEqual(str(out.reason_code), "oracle_tick_stale")

    def test_phase_allows_action_follows_canonical_policy(self) -> None:
        self.assertTrue(phase_allows_action("maker_window", EDGE_ACTION_MAKER))
        self.assertFalse(phase_allows_action("maker_window", EDGE_ACTION_TAKER))
        self.assertTrue(phase_allows_action("scan", EDGE_ACTION_NONE))
        self.assertFalse(phase_allows_action("scan", EDGE_ACTION_MAKER))
        self.assertFalse(phase_allows_action("prepare", EDGE_ACTION_MAKER))
        self.assertFalse(phase_allows_action("prepare", EDGE_ACTION_TAKER))
        self.assertFalse(phase_allows_action("resolve", EDGE_ACTION_TAKER))

    def test_lifecycle_phase_surface_fields_emit_canonical_field(self) -> None:
        payload = lifecycle_phase_surface_fields(lifecycle_phase="maker_window")
        self.assertEqual(payload[EDGE_LIFECYCLE_PHASE_FIELD], "maker_window")

    def test_lifecycle_phase_from_payload_is_lifecycle_only_for_active_rows(self) -> None:
        self.assertEqual(
            lifecycle_phase_from_payload(
                {
                    "lifecycle_phase": "maker_window",
                    "lineage_stage": "EXTREME_ONLY",
                    "time_remaining_sec": 12.0,
                }
            ),
            "maker_window",
        )
        self.assertEqual(
            lifecycle_phase_from_payload(
                {
                    "lifecycle_phase": "taker_window",
                    "lineage_stage": "EXTREME_ONLY",
                    "time_remaining_sec": 6.0,
                }
            ),
            "taker_window",
        )

    def test_lifecycle_phase_from_payload_fails_closed_on_legacy_only_active_rows(self) -> None:
        self.assertEqual(
            lifecycle_phase_from_payload(
                {
                    "lineage_stage": "EXTREME_ONLY",
                    "maker_new_risk_allowed": True,
                    "normal_taker_allowed": False,
                    "late_window_authority_class": "maker_new_risk_only",
                }
            ),
            "",
        )

    def test_market_truth_required_from_payload_prefers_canonical_field(self) -> None:
        self.assertTrue(
            market_truth_required_from_payload(
                {
                    "market_truth_required": True,
                    "book_feed_required": False,
                }
            )
        )

    def test_market_truth_required_from_payload_ignores_legacy_book_feed_alias(self) -> None:
        self.assertFalse(market_truth_required_from_payload({"book_feed_required": True}))

    def test_compute_edge_value_returns_none_when_missing_inputs(self) -> None:
        self.assertIsNone(compute_edge_value(fair_probability=None, market_probability=0.5))
        self.assertIsNone(compute_edge_value(fair_probability=0.6, market_probability=None))
        self.assertAlmostEqual(
            float(compute_edge_value(fair_probability=0.61, market_probability=0.54) or 0.0),
            0.07,
            places=9,
        )

    def test_block_reason_taxonomy_is_canonical(self) -> None:
        self.assertTrue(is_canonical_block_reason("edge_below_min"))
        self.assertTrue(is_canonical_block_reason("fair_probability_missing"))
        self.assertTrue(is_canonical_block_reason("maker_requires_ws_book_source"))
        self.assertTrue(is_canonical_block_reason("taker_requires_ws_book_source"))
        self.assertTrue(is_canonical_block_reason("taker_outside_final_window"))
        self.assertTrue(is_canonical_block_reason("taker_window_already_submitted"))
        self.assertTrue(is_canonical_block_reason("taker_hard_min_notional_unachievable"))
        self.assertTrue(is_canonical_block_reason("taker_dynamic_size_capped_by_risk"))
        self.assertTrue(is_canonical_block_reason("taker_visible_fill_ratio_below_min"))
        self.assertTrue(is_canonical_block_reason("taker_submit_price_below_floor"))
        self.assertTrue(is_canonical_block_reason("normal_taker_same_token_sell_forbidden"))
        self.assertTrue(is_canonical_block_reason("window_geometry_near_pinned"))
        self.assertTrue(is_canonical_block_reason("maker_edge_below_min"))
        retired_route_disabled = "complement" + "_route_disabled_pending_validation"
        retired_mapping = "complement" + "_token_mapping_unavailable"
        retired_price = "complement" + "_token_price_unavailable"
        self.assertFalse(is_canonical_block_reason(retired_route_disabled))
        self.assertFalse(is_canonical_block_reason(retired_mapping))
        self.assertFalse(is_canonical_block_reason(retired_price))
        self.assertTrue(is_canonical_block_reason("open_order_cleanup_required"))
        self.assertTrue(is_canonical_block_reason("settlement_hold_required"))
        self.assertTrue(is_canonical_block_reason("phase_disallow_maker"))
        self.assertTrue(is_canonical_block_reason("phase_disallow_taker"))
        self.assertFalse(is_canonical_block_reason("stage_disallow_maker"))
        self.assertFalse(is_canonical_block_reason("stage_disallow_taker"))
        self.assertFalse(is_canonical_block_reason("unspecified_no_action"))
        self.assertFalse(is_canonical_block_reason("some_random_reason"))


if __name__ == "__main__":
    unittest.main()
