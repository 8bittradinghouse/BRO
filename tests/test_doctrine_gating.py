from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from executor import (
    STAGE_EXTREME_ONLY,
    STAGE_EXPIRED,
    STAGE_MAKER_TAKER_SELECTIVE,
    STAGE_MAKER_POSITION,
    STAGE_OBSERVE,
    STAGE_SNIPER_PRIMARY,
    STAGE_UNKNOWN,
    ExecutionRunner,
)
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.edge_truth_contract import is_canonical_block_reason, stage_policy as edge_stage_policy
from prodesk.models import BookTop
from prodesk.common import utc_iso


class DoctrineGatingTests(unittest.TestCase):
    def _runner(self) -> ExecutionRunner:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["chainlink"]["enabled"] = False
        cfg["latency_verifier"]["score_enabled"] = False
        cfg["latency_verifier"]["require_armed_for_maker"] = False
        cfg["storage"]["log_dir"] = td.name
        cfg["storage"]["state_path"] = str(Path(td.name) / "state.json")
        return ExecutionRunner(cfg)

    def test_stage_policy_sniper_primary_is_taker_only(self):
        allow_maker, allow_taker = ExecutionRunner._stage_policy(STAGE_SNIPER_PRIMARY)
        self.assertFalse(allow_maker)
        self.assertTrue(allow_taker)
        self.assertEqual((allow_maker, allow_taker), edge_stage_policy(STAGE_SNIPER_PRIMARY))

    def test_stage_policy_uses_canonical_contract_source(self):
        for stage in (
            STAGE_OBSERVE,
            STAGE_MAKER_POSITION,
            STAGE_MAKER_TAKER_SELECTIVE,
            STAGE_SNIPER_PRIMARY,
            STAGE_EXTREME_ONLY,
            STAGE_EXPIRED,
            STAGE_UNKNOWN,
        ):
            self.assertEqual(ExecutionRunner._stage_policy(stage), edge_stage_policy(stage))

    def test_token_stage_info_observe_hold_is_deterministic(self):
        runner = self._runner()
        now = dt.datetime.now(dt.timezone.utc)
        runner.token_expiry_dt_by_token["t1"] = now + dt.timedelta(seconds=15)
        runner.token_market_key_by_token["t1"] = "mkt|expiry|strike|YES"
        runner.doctrine_min_observe_cycles_on_entry = 2
        runner.doctrine_min_observe_seconds_on_entry = 2.0
        runner._market_entry_cycle_by_token["t1"] = 0
        runner._market_entry_mono_by_token["t1"] = time.monotonic()
        runner._doctrine_cycle_index = 0

        held = runner._token_stage_info("t1")
        self.assertEqual(held["raw_stage"], STAGE_EXTREME_ONLY)
        self.assertEqual(held["stage"], STAGE_OBSERVE)
        self.assertTrue(held["observe_hold_active"])

        runner._doctrine_cycle_index = 3
        runner._market_entry_mono_by_token["t1"] = time.monotonic() - 3.0
        released = runner._token_stage_info("t1")
        self.assertEqual(released["stage"], STAGE_EXTREME_ONLY)
        self.assertFalse(released["observe_hold_active"])

    def test_expired_on_arrival_is_not_held(self):
        runner = self._runner()
        now = dt.datetime.now(dt.timezone.utc)
        runner.token_expiry_dt_by_token["t1"] = now - dt.timedelta(seconds=1)
        runner.token_market_key_by_token["t1"] = "mkt|expired|strike|YES"
        runner._market_entry_cycle_by_token["t1"] = 0
        runner._market_entry_mono_by_token["t1"] = time.monotonic()
        info = runner._token_stage_info("t1")
        self.assertEqual(info["stage"], STAGE_EXPIRED)
        self.assertFalse(info["observe_hold_active"])

    def test_maker_fail_closed_when_fair_is_missing(self):
        runner = self._runner()
        now = dt.datetime.now(dt.timezone.utc)
        runner.token_expiry_dt_by_token["t1"] = now + dt.timedelta(seconds=80)
        runner.token_strike_by_token["t1"] = 65000.0
        runner.token_side_by_token["t1"] = "YES"
        snapshot = runner.latency_verifier.snapshot(active_tokens=["t1"])
        reason = runner._maker_prereq_failure_reason(
            "t1",
            fair_probability_by_token={},
            latency_snapshot=snapshot,
            oracle_fresh=True,
        )
        self.assertEqual(reason, "fair_probability_unavailable")

    def test_maker_timing_gate_is_fail_closed_outside_window(self):
        runner = self._runner()
        runner.maker_comp_timing_gate_enabled = True
        runner.maker_comp_timing_gate_min_sec_to_expiry = 45.0
        runner.maker_comp_timing_gate_max_sec_to_expiry = 60.0
        self.assertFalse(runner._maker_timing_gate_open(None))
        self.assertFalse(runner._maker_timing_gate_open(30.0))
        self.assertTrue(runner._maker_timing_gate_open(50.0))
        self.assertFalse(runner._maker_timing_gate_open(75.0))

    def test_maker_canonical_mode_blocks_non_ws_book_source(self):
        runner = self._runner()
        ws_top = BookTop(
            token_id="t-ws",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        rest_top = BookTop(
            token_id="t-rest",
            ts_utc=utc_iso(),
            source="rest",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        prereq_failures: dict[str, str] = {}
        gated = runner._apply_canonical_maker_ws_source_gate(
            books={"t-ws": ws_top, "t-rest": rest_top},
            maker_eligible_tokens={"t-ws", "t-rest"},
            maker_prereq_failure_by_token=prereq_failures,
        )
        self.assertEqual(gated, {"t-ws"})
        self.assertEqual(str(prereq_failures.get("t-rest") or ""), "maker_requires_ws_book_source")
        self.assertTrue(is_canonical_block_reason("maker_requires_ws_book_source"))

    def test_extreme_only_uses_stricter_min_edge(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_taker_min_edge = 0.02
        runner.sniper_taker_extreme_edge_mult = 2.0
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        books = {"t1": top}
        fair = {"t1": 0.53}  # edge=0.03 < extreme required 0.04
        with mock.patch.object(
            runner.manager,
            "place_taker_order_with_outcome",
            return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-1"},
        ) as placed:
            out = runner._run_sniper_taker(
                books=books,
                fair_probability_by_token=fair,
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_EXTREME_ONLY, "sec_to_expiry": 15.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
            )
        self.assertEqual(out["submitted"], 0)
        placed.assert_not_called()

        with mock.patch.object(
            runner.manager,
            "place_taker_order_with_outcome",
            return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-1"},
        ) as placed:
            out = runner._run_sniper_taker(
                books=books,
                fair_probability_by_token=fair,
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 45.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
            )
        self.assertEqual(out["submitted"], 1)
        placed.assert_called_once()
        runner.events.close()
        event_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    event_rows.append(payload)
        self.assertGreaterEqual(len(event_rows), 2)
        blocked = [
            row
            for row in event_rows
            if str(row.get("stage") or "") == STAGE_EXTREME_ONLY
            and str(row.get("action_taken") or "") == "none"
        ]
        self.assertTrue(bool(blocked))
        self.assertEqual(str(blocked[-1].get("block_reason") or ""), "edge_below_min")
        submitted_rows = [
            row
            for row in event_rows
            if str(row.get("stage") or "") == STAGE_MAKER_TAKER_SELECTIVE
            and str(row.get("action_taken") or "") == "taker"
        ]
        self.assertTrue(bool(submitted_rows))
        self.assertIsNone(submitted_rows[-1].get("block_reason"))

    def test_stage_specific_min_edge_override_only_tightens_target_stage(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_taker_min_edge = 0.01
        runner.sniper_taker_extreme_edge_mult = 2.0
        runner.sniper_taker_min_edge_by_stage = {STAGE_MAKER_TAKER_SELECTIVE: 0.05}
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        books = {"t1": top}
        fair = {"t1": 0.53}  # edge=0.03

        with mock.patch.object(
            runner.manager,
            "place_taker_order_with_outcome",
            return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-1"},
        ) as placed:
            out_selective = runner._run_sniper_taker(
                books=books,
                fair_probability_by_token=fair,
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 45.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
            )
            out_primary = runner._run_sniper_taker(
                books=books,
                fair_probability_by_token=fair,
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_SNIPER_PRIMARY, "sec_to_expiry": 25.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
            )
        self.assertEqual(out_selective["submitted"], 0)
        self.assertEqual(out_primary["submitted"], 1)
        placed.assert_called_once()

    def test_sniper_taker_submit_event_emits_order_id(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_taker_min_edge = 0.01
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        with mock.patch.object(
            runner.manager,
            "place_taker_order_with_outcome",
            return_value={"submitted": True, "fills_accepted": 1, "order_id": "ord-42"},
        ):
            out = runner._run_sniper_taker(
                books={"t1": top},
                fair_probability_by_token={"t1": 0.53},
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 45.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
            )
        self.assertEqual(out["submitted"], 1)
        self.assertEqual(out["fills_accepted"], 1)
        runner.events.close()
        submit_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "sniper_taker_submit":
                    submit_rows.append(payload)
        self.assertTrue(bool(submit_rows))
        self.assertEqual(str(submit_rows[-1].get("order_id") or ""), "ord-42")

    def test_maker_edge_evaluation_emits_block_reason_when_not_submitted(self):
        runner = self._runner()
        stage_info = {
            "t1": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            }
        }
        runner._emit_maker_edge_evaluations(
            books={},
            stage_info_by_token=stage_info,
            maker_eval_token_ids={"t1"},
            maker_submitted_token_ids=set(),
            maker_submitted_order_ids_by_token={},
            maker_no_submission_reason_by_token={},
            maker_no_submission_category_by_token={},
            maker_prereq_failure_by_token={"t1": "fair_probability_unavailable"},
            fair_probability_by_token={},
            oracle_tick_age_sec=0.2,
            latency_state="armed",
            cycle_index=1,
        )
        runner.events.close()
        event_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    event_rows.append(payload)
        self.assertTrue(bool(event_rows))
        row = event_rows[-1]
        self.assertEqual(str(row.get("evaluation_scope") or ""), "maker")
        self.assertEqual(str(row.get("action_taken") or ""), "none")
        self.assertEqual(str(row.get("block_reason") or ""), "fair_probability_unavailable")

    def test_maker_edge_evaluation_emits_one_row_per_evaluated_token(self):
        runner = self._runner()
        top = BookTop(
            token_id="t2",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        stage_info = {
            "t1": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            },
            "t2": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            },
        }
        runner._emit_maker_edge_evaluations(
            books={"t2": top},
            stage_info_by_token=stage_info,
            maker_eval_token_ids={"t1", "t2"},
            maker_submitted_token_ids={"t2"},
            maker_submitted_order_ids_by_token={"t2": ["ord-maker-1"]},
            maker_no_submission_reason_by_token={},
            maker_no_submission_category_by_token={},
            maker_prereq_failure_by_token={"t1": "fair_probability_unavailable"},
            fair_probability_by_token={"t2": 0.55},
            oracle_tick_age_sec=0.2,
            latency_state="armed",
            cycle_index=2,
        )
        runner.events.close()
        rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    rows.append(payload)
        maker_rows = [row for row in rows if str(row.get("evaluation_scope") or "") == "maker"]
        self.assertEqual(len(maker_rows), 2)
        self.assertEqual({str(row.get("action_taken") or "") for row in maker_rows}, {"none", "maker"})
        for row in maker_rows:
            action = str(row.get("action_taken") or "")
            reason = str(row.get("block_reason") or "")
            self.assertTrue(bool(str(row.get("target_ref") or "").strip()))
            if action == "none":
                self.assertTrue(bool(reason))
                self.assertTrue(is_canonical_block_reason(reason))
            else:
                self.assertEqual(action, "maker")
                self.assertIsNone(row.get("block_reason"))

    def test_maker_edge_evaluation_emits_no_submission_cause_when_available(self):
        runner = self._runner()
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        stage_info = {
            "t1": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            }
        }
        runner._emit_maker_edge_evaluations(
            books={"t1": top},
            stage_info_by_token=stage_info,
            maker_eval_token_ids={"t1"},
            maker_submitted_token_ids=set(),
            maker_submitted_order_ids_by_token={},
            maker_no_submission_reason_by_token={"t1": "replace_guard_min_rest"},
            maker_no_submission_category_by_token={"t1": "replace_guard_min_rest"},
            maker_prereq_failure_by_token={},
            fair_probability_by_token={"t1": 0.55},
            oracle_tick_age_sec=0.2,
            latency_state="armed",
            cycle_index=3,
        )
        runner.events.close()
        event_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    event_rows.append(payload)
        self.assertTrue(bool(event_rows))
        row = event_rows[-1]
        self.assertEqual(str(row.get("evaluation_scope") or ""), "maker")
        self.assertEqual(str(row.get("action_taken") or ""), "none")
        self.assertEqual(str(row.get("block_reason") or ""), "maker_no_submission")
        self.assertEqual(str(row.get("maker_no_submission_cause") or ""), "replace_guard_min_rest")
        self.assertEqual(str(row.get("maker_no_submission_category") or ""), "replace_guard_min_rest")

    def test_maker_edge_evaluation_uses_bounded_single_side_touch_when_midpoint_missing(self):
        runner = self._runner()
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=None,
            best_bid_size=None,
            best_ask_price=0.63,
            best_ask_size=100.0,
        )
        stage_info = {
            "t1": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            }
        }
        runner._emit_maker_edge_evaluations(
            books={"t1": top},
            stage_info_by_token=stage_info,
            maker_eval_token_ids={"t1"},
            maker_submitted_token_ids={"t1"},
            maker_submitted_order_ids_by_token={"t1": ["ord-maker-1"]},
            maker_no_submission_reason_by_token={},
            maker_no_submission_category_by_token={},
            maker_prereq_failure_by_token={},
            fair_probability_by_token={"t1": 0.70},
            oracle_tick_age_sec=0.2,
            latency_state="armed",
            cycle_index=4,
        )
        runner.events.close()
        rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    rows.append(payload)
        self.assertTrue(bool(rows))
        row = rows[-1]
        self.assertEqual(str(row.get("evaluation_scope") or ""), "maker")
        self.assertEqual(str(row.get("action_taken") or ""), "maker")
        self.assertAlmostEqual(float(row.get("market_probability") or 0.0), 0.63, places=6)
        self.assertEqual(str(row.get("market_reference_mode") or ""), "bounded_single_side_touch")
        self.assertEqual(str(row.get("market_reference_basis") or ""), "ws_single_side_touch")
        self.assertEqual(str(row.get("market_reference_source_side") or ""), "ask")
        self.assertEqual(str(row.get("market_reference_class") or ""), "bounded_approximation")
        self.assertTrue(bool(row.get("market_reference_fallback_used", False)))
        self.assertEqual(str(row.get("decision_input_type") or ""), "bounded_derived")
        self.assertEqual(str(row.get("decision_input_data_class") or ""), "observed_other")

    def test_maker_edge_evaluation_keeps_market_probability_missing_when_bounded_fallback_disabled(self):
        runner = self._runner()
        runner.doctrine_maker_allow_bounded_single_side_reference = False
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.44,
            best_bid_size=100.0,
            best_ask_price=None,
            best_ask_size=None,
        )
        stage_info = {
            "t1": {
                "stage": STAGE_MAKER_TAKER_SELECTIVE,
                "sec_to_expiry": 50.0,
                "allow_maker": True,
                "allow_taker": True,
            }
        }
        runner._emit_maker_edge_evaluations(
            books={"t1": top},
            stage_info_by_token=stage_info,
            maker_eval_token_ids={"t1"},
            maker_submitted_token_ids=set(),
            maker_submitted_order_ids_by_token={},
            maker_no_submission_reason_by_token={},
            maker_no_submission_category_by_token={},
            maker_prereq_failure_by_token={},
            fair_probability_by_token={"t1": 0.70},
            oracle_tick_age_sec=0.2,
            latency_state="armed",
            cycle_index=5,
        )
        runner.events.close()
        rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    rows.append(payload)
        self.assertTrue(bool(rows))
        row = rows[-1]
        self.assertEqual(str(row.get("evaluation_scope") or ""), "maker")
        self.assertEqual(str(row.get("action_taken") or ""), "none")
        self.assertEqual(str(row.get("block_reason") or ""), "market_probability_missing")
        self.assertEqual(str(row.get("market_reference_mode") or ""), "missing")
        self.assertFalse(bool(row.get("market_reference_fallback_used", False)))

    def test_taker_edge_evaluation_emits_one_row_per_token_with_causality(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_enabled = True
        runner.sniper_taker_max_orders_per_cycle = 1
        runner.sniper_taker_min_edge = 0.01
        top1 = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        top2 = BookTop(
            token_id="t2",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        stage_info = {
            "t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 35.0},
            "t2": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 35.0},
        }
        with mock.patch.object(
            runner.manager,
            "place_taker_order_with_outcome",
            return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t2"},
        ):
            runner._run_sniper_taker(
                books={"t1": top1, "t2": top2},
                fair_probability_by_token={"t1": 0.50, "t2": 0.70},
                token_ids=["t1", "t2"],
                stage_info_by_token=stage_info,
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1", "t2"],
                cycle_index=3,
            )
        runner.events.close()
        rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    rows.append(payload)
        taker_rows = [row for row in rows if str(row.get("evaluation_scope") or "") == "taker"]
        self.assertEqual(len(taker_rows), 2)
        self.assertEqual({str(row.get("action_taken") or "") for row in taker_rows}, {"none", "taker"})
        for row in taker_rows:
            action = str(row.get("action_taken") or "")
            reason = str(row.get("block_reason") or "")
            self.assertTrue(bool(str(row.get("target_ref") or "").strip()))
            self.assertEqual(str(row.get("book_source") or ""), "ws")
            if action == "none":
                self.assertTrue(bool(reason))
                self.assertTrue(is_canonical_block_reason(reason))
            else:
                self.assertEqual(action, "taker")
                self.assertTrue(bool(row.get("submitted")))
                self.assertIsNone(row.get("block_reason"))

    def test_taker_canonical_mode_blocks_non_ws_book_sources(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_enabled = True
        runner.sniper_taker_max_orders_per_cycle = 1
        runner.sniper_taker_min_edge = 0.01
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="rest",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        with mock.patch.object(runner.manager, "place_taker_order_with_outcome") as placed:
            out = runner._run_sniper_taker(
                books={"t1": top},
                fair_probability_by_token={"t1": 0.70},
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 35.0}},
                oracle_tick_age_sec=0.0,
                lag_verified_token_ids=["t1"],
                cycle_index=4,
            )
        self.assertEqual(out["submitted"], 0)
        placed.assert_not_called()
        runner.events.close()
        edge_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    edge_rows.append(payload)
        self.assertTrue(bool(edge_rows))
        taker_row = edge_rows[-1]
        self.assertEqual(str(taker_row.get("evaluation_scope") or ""), "taker")
        self.assertEqual(str(taker_row.get("action_taken") or ""), "none")
        self.assertEqual(str(taker_row.get("block_reason") or ""), "taker_requires_ws_book_source")
        self.assertEqual(str(taker_row.get("book_source") or ""), "rest")

    def test_taker_blocks_when_oracle_not_fresh(self):
        runner = self._runner()
        runner.sniper_taker_enabled = True
        runner.sniper_enabled = True
        runner.sniper_taker_max_orders_per_cycle = 1
        runner.sniper_taker_min_edge = 0.01
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        with mock.patch.object(runner.manager, "place_taker_order_with_outcome") as placed:
            out = runner._run_sniper_taker(
                books={"t1": top},
                fair_probability_by_token={"t1": 0.70},
                token_ids=["t1"],
                stage_info_by_token={"t1": {"stage": STAGE_MAKER_TAKER_SELECTIVE, "sec_to_expiry": 35.0}},
                oracle_tick_age_sec=0.0,
                oracle_fresh=False,
                lag_verified_token_ids=["t1"],
                cycle_index=5,
            )
        self.assertEqual(out["submitted"], 0)
        placed.assert_not_called()
        runner.events.close()
        edge_rows: list[dict] = []
        for path in sorted(Path(runner.log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == "edge_evaluation":
                    edge_rows.append(payload)
        self.assertTrue(bool(edge_rows))
        taker_row = edge_rows[-1]
        self.assertEqual(str(taker_row.get("evaluation_scope") or ""), "taker")
        self.assertEqual(str(taker_row.get("action_taken") or ""), "none")
        self.assertEqual(str(taker_row.get("block_reason") or ""), "oracle_unavailable_or_stale")
        self.assertEqual(str(taker_row.get("book_source") or ""), "ws")


if __name__ == "__main__":
    unittest.main()
