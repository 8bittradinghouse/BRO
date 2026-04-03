import copy
import datetime as dt
import json
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from executor import ExecutionRunner
from prodesk.chainlink_feed import ChainlinkTick
from prodesk.common import utc_iso
from prodesk.config import DEFAULT_EXECUTION_CONFIG, validate_execution_config
from prodesk.gateway import PaperGateway, PostOnlyRejectError
from prodesk.latency_verifier import LatencySnapshot
from prodesk.logging_utils import EventLogger
from prodesk.models import BookTop, FillEvent, OrderIntent, Position
from prodesk.order_manager import OrderManager
from prodesk.risk import RiskEngine
from prodesk.strategy import MarketMakingStrategy
from prodesk.telemetry import Telemetry


class ExecutionStackTests(unittest.TestCase):
    def test_config_validation_requires_targets(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = []
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_allows_discovery_without_static_targets(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = []
        cfg["targets"]["discovery"]["enabled"] = True
        validate_execution_config(cfg)

    def test_config_rejects_duplicate_target_ids(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["dup", "dup"]
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_token_side_metadata(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["targets"]["token_side_by_token"] = {"tok1": "MAYBE"}
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_negative_open_orders_cache_ttl(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["auth"]["open_orders_cache_ttl_sec"] = -0.1
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_boolean_log_book_top(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["log_book_top"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_boolean_log_leadlag_book_move(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["log_leadlag_book_move"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_paper_queue_position_mode(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["paper_queue_position_mode"] = "bad_mode"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_maker_depth_target_ratio_bounds(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sizing"]["maker_depth_target_min_ratio"] = 0.3
        cfg["sizing"]["maker_depth_target_max_ratio"] = 0.2
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_boolean_log_async_flush(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["log_async_flush"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_positive_rest_fetch_max_workers(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["rest_fetch_max_workers"] = 0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_string_guard_stop_file(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["guard_stop_file"] = 123
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_boolean_clear_guard_stop_on_start(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["clear_guard_stop_on_start"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_setup_lock_missing_expected_profile(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["paper_enforce_setup_lock"] = True
        cfg["runtime"]["paper_expected_profile_name"] = ""
        cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_setup_lock_profile_mismatch(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["paper_enforce_setup_lock"] = True
        cfg["profile"]["name"] = "paper_stress"
        cfg["runtime"]["paper_expected_profile_name"] = "paper_discipline"
        cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_setup_lock_fingerprint_mismatch_against_meta(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["paper_enforce_setup_lock"] = True
        cfg["runtime"]["paper_expected_profile_name"] = str(cfg["profile"]["name"])
        cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
        cfg["_meta"] = {"effective_config_sha256": "b" * 64}
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_allows_setup_lock_when_profile_and_fingerprint_match(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["paper_enforce_setup_lock"] = True
        cfg["runtime"]["paper_expected_profile_name"] = str(cfg["profile"]["name"])
        cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
        cfg["_meta"] = {"effective_config_sha256": "a" * 64}
        validate_execution_config(cfg)

    def test_config_rejects_invalid_maker_competitiveness_timing_window(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["strategy"]["maker_competitiveness"]["timing_gate_min_sec_to_expiry"] = 61.0
        cfg["strategy"]["maker_competitiveness"]["timing_gate_max_sec_to_expiry"] = 60.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_maker_competitiveness_one_sided_stage(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["strategy"]["maker_competitiveness"]["one_sided_allowed_stages"] = ["SNIPER_PRIMARY"]
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_strategy_emits_two_sided_quotes(self):
        strategy = MarketMakingStrategy(DEFAULT_EXECUTION_CONFIG["strategy"])
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.45,
            best_bid_size=100,
            best_ask_price=0.55,
            best_ask_size=100,
        )
        pos = Position(token_id="t1", net_shares=0)
        intents = strategy.make_quotes("t1", top, pos)
        self.assertEqual(len(intents), 2)
        bid = [x for x in intents if x.side == "BUY"][0]
        ask = [x for x in intents if x.side == "SELL"][0]
        self.assertLess(bid.price, ask.price)
        self.assertGreater(bid.size, 0)

    def test_strategy_volatility_regime_adjusts_size_and_spread(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
        cfg["volatility"]["enabled"] = True
        cfg["min_spread"] = 0.001
        strategy = MarketMakingStrategy(cfg)
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        pos = Position(token_id="t1", net_shares=0)
        normal = strategy.make_quotes("t1", top, pos, realized_volatility=0.003)
        high = strategy.make_quotes("t1", top, pos, realized_volatility=0.02)
        low = strategy.make_quotes("t1", top, pos, realized_volatility=0.0001)

        normal_spread = [x for x in normal if x.side == "SELL"][0].price - [x for x in normal if x.side == "BUY"][0].price
        high_spread = [x for x in high if x.side == "SELL"][0].price - [x for x in high if x.side == "BUY"][0].price
        low_spread = [x for x in low if x.side == "SELL"][0].price - [x for x in low if x.side == "BUY"][0].price
        normal_size = [x for x in normal if x.side == "BUY"][0].size
        high_size = [x for x in high if x.side == "BUY"][0].size
        low_size = [x for x in low if x.side == "BUY"][0].size

        self.assertLess(high_spread, normal_spread)
        self.assertGreater(low_spread, normal_spread)
        self.assertGreater(high_size, normal_size)
        self.assertLess(low_size, normal_size)

    def test_order_manager_rejects_live_mode_without_explicit_wallet(self):
        runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
        strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
        risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
        risk_cfg["max_book_age_sec"] = 100.0

        gateway = PaperGateway()
        telemetry = Telemetry()
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(risk_cfg, positions)
        strategy = MarketMakingStrategy(strategy_cfg)
        with tempfile.TemporaryDirectory() as td:
            events = EventLogger(Path(td))
            with self.assertRaises(ValueError):
                OrderManager(
                    gateway,
                    strategy,
                    risk,
                    events,
                    telemetry,
                    runtime_cfg,
                    strategy_cfg,
                    mode="live",
                )

    def test_order_manager_places_orders_and_processes_fills(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            first = manager.step({"t1": top})
            self.assertGreaterEqual(first["actions"], 2)
            self.assertGreaterEqual(first["open_orders"], 2)

            cross = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.70,
                best_bid_size=100,
                best_ask_price=0.30,
                best_ask_size=100,
            )
            gateway.on_book(cross)
            second = manager.step({"t1": cross})
            self.assertGreaterEqual(second["fills"], 1)
            self.assertGreater(positions["t1"].buy_shares + positions["t1"].sell_shares, 0.0)

        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_one_sided_policy_and_competitiveness_context(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            manager.step(
                {"t1": top},
                side_policy_by_token={"t1": "BUY_ONLY"},
                competitiveness_context_by_token={
                    "t1": {
                        "side_policy": "BUY_ONLY",
                        "one_sided_active": True,
                        "edge_bucket": "0p10_0p20",
                        "size_multiplier_competitiveness": 1.2,
                        "spread_multiplier_competitiveness": 0.85,
                        "requote_delta_multiplier_competitiveness": 0.7,
                    }
                },
            )

            open_orders = gateway.get_open_orders()
            self.assertEqual(len(open_orders), 1)
            self.assertEqual(str(open_orders[0].side), "BUY")

            events.close()
            events = None
            order_submit_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "order_submit":
                        order_submit_rows.append(payload)
            self.assertTrue(order_submit_rows)
            competitiveness_payload = order_submit_rows[-1].get("maker_competitiveness")
            self.assertIsInstance(competitiveness_payload, dict)
            self.assertEqual(str(competitiveness_payload.get("side_policy") or ""), "BUY_ONLY")
            self.assertEqual(bool(competitiveness_payload.get("one_sided_active")), True)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_handles_cancel_exception_without_crashing_cycle(self):
        class _CancelBoomGateway(PaperGateway):
            def cancel_order(self, order_id: str) -> bool:  # type: ignore[override]
                raise RuntimeError("cancel failed")

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["max_order_size"] = 1000.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = _CancelBoomGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            order = gateway.place_order(
                OrderIntent(token_id="t1", side="BUY", price=0.45, size=10.0, tif="GTC", post_only=True, reason="test"),
                client_order_id="c1",
            )
            ok = manager._cancel_order(order, "test_cancel")
            self.assertFalse(ok)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_replace_not_blocked_by_stale_open_order_count(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_open_orders_per_token"] = 2

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top_a = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            first = manager.step({"t1": top_a})
            self.assertEqual(first["open_orders"], 2)

            top_b = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.40,
                best_bid_size=100,
                best_ask_price=0.50,
                best_ask_size=100,
            )
            second = manager.step({"t1": top_b})
            self.assertEqual(second["open_orders"], 2)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_replace_respects_min_rest_guard(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["maker_replace_min_rest_sec"] = 2.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top_a = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            first = manager.step({"t1": top_a})
            self.assertEqual(first["open_orders"], 2)

            top_b = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.40,
                best_bid_size=100,
                best_ask_price=0.50,
                best_ask_size=100,
            )
            second = manager.step({"t1": top_b})
            self.assertEqual(second["open_orders"], 2)
            self.assertEqual(int(second["actions"]), 0)
            self.assertEqual(
                dict(second.get("maker_no_submission_reason_by_token", {})).get("t1"),
                "replace_guard_min_rest",
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_replace_allowed_after_min_rest_elapsed(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["maker_replace_min_rest_sec"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top_a = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            first = manager.step({"t1": top_a})
            self.assertEqual(first["open_orders"], 2)

            old_ts = utc_iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=3))
            for open_order in gateway.get_open_orders():
                open_order.created_ts_utc = old_ts

            top_b = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.40,
                best_bid_size=100,
                best_ask_price=0.50,
                best_ask_size=100,
            )
            second = manager.step({"t1": top_b})
            self.assertEqual(second["open_orders"], 2)
            self.assertGreaterEqual(int(second["actions"]), 4)
            self.assertNotIn("t1", dict(second.get("maker_no_submission_reason_by_token", {})))
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_maker_no_submission_reason_surfaces_submit_rejected_subcause(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            # Force clamp-unavailable crossing so this test keeps validating reject semantics.
            strategy_cfg["tick_size"] = 1.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            class _AggressiveMakerStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_ask_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            manager = OrderManager(
                gateway,
                _AggressiveMakerStrategy(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
            )

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            gateway.on_book(top)
            summary = manager.step({"t1": top})
            self.assertEqual(summary["open_orders"], 0)
            self.assertEqual(int(summary["actions"]), 0)
            self.assertEqual(
                dict(summary.get("maker_no_submission_reason_by_token", {})).get("t1"),
                "submit_rejected_pre_submit_cross_guarded",
            )
            self.assertEqual(
                dict(summary.get("maker_no_submission_category_by_token", {})).get("t1"),
                "pre_submit_cross_guarded",
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_pre_submit_cross_guard_reject_does_not_consume_order_capacity(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            # Force clamp-unavailable crossing so local reject path remains exercised.
            strategy_cfg["tick_size"] = 1.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 1

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            gateway.on_book(top)

            doomed = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.55,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            first, first_reason = manager._place_order(doomed, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNone(first)
            self.assertEqual(first_reason, "pre_submit_cross_guarded")
            first_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(first_state.get("orders_used_accepted", -1)), 0)
            self.assertEqual(int(first_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertEqual(int(first_state.get("orders_transport_attempted_recent", -1)), 0)

            valid = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.45,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            second, second_reason = manager._place_order(valid, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(second)
            self.assertIsNone(second_reason)
            second_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(second_state.get("orders_used_accepted", -1)), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_pre_submit_cross_guard_clamps_crossing_quote_and_submits(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            strategy_cfg["tick_size"] = 0.001
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 5

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            gateway.on_book(top)

            crossing = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.55,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            placed, reason = manager._place_order(crossing, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(placed)
            self.assertIsNone(reason)
            assert placed is not None
            self.assertLess(float(placed.price), float(top.best_ask_price))
            self.assertEqual(int(telemetry.counters.get("pre_submit_cross_guard_adjusted", 0)), 1)
            self.assertEqual(int(telemetry.counters.get("pre_submit_cross_guarded", 0)), 0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_transport_reject_releases_reservation_and_preserves_capacity(self):
        class _RejectThenAcceptGateway(PaperGateway):
            def __init__(self) -> None:
                super().__init__()
                self._attempt = 0

            def place_order(self, intent: OrderIntent, client_order_id: str):  # type: ignore[override]
                self._attempt += 1
                if self._attempt == 1:
                    raise PostOnlyRejectError("simulated_transport_post_only_reject")
                return super().place_order(intent, client_order_id)

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 1

            gateway = _RejectThenAcceptGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            gateway.on_book(top)

            intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.45,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            first, first_reason = manager._place_order(intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNone(first)
            self.assertEqual(first_reason, "post_only_reject")
            first_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(first_state.get("orders_used_accepted", -1)), 0)
            self.assertEqual(int(first_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertEqual(int(first_state.get("orders_transport_attempted_recent", -1)), 1)

            second, second_reason = manager._place_order(intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(second)
            self.assertIsNone(second_reason)
            second_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(second_state.get("orders_used_accepted", -1)), 1)
            self.assertEqual(int(second_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertGreaterEqual(telemetry.counters.get("order_submission_released", 0), 1)
            self.assertGreaterEqual(telemetry.counters.get("order_submission_transport_attempted", 0), 2)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_soft_throttle_emits_causal_decision_basis(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 1

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            gateway.on_book(top)
            buy_intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.45,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            sell_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            first, first_reason = manager._place_order(buy_intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(first)
            self.assertIsNone(first_reason)
            second, second_reason = manager._place_order(sell_intent, top, open_orders_for_token=[], open_orders_total=1)
            self.assertIsNone(second)
            self.assertEqual(second_reason, "order_soft_throttle")
            self.assertGreaterEqual(telemetry.counters.get("order_soft_throttle_skips", 0), 1)
            events.close()
            events = None
            event_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "order_soft_throttle":
                        event_rows.append(payload)
            self.assertTrue(bool(event_rows))
            row = event_rows[-1]
            basis = row.get("soft_throttle_decision_basis") or {}
            self.assertEqual(str(row.get("submission_lane") or ""), "maker")
            self.assertEqual(str(basis.get("pool") or ""), "shared_order_rate_pool")
            self.assertEqual(str(basis.get("lane_attribution") or ""), "shared_pool_maker_and_taker")
            self.assertEqual(int(basis.get("orders_limit_60s") or 0), 1)
            self.assertEqual(int(basis.get("orders_soft_limit_60s") or 0), 1)
            self.assertEqual(int(basis.get("orders_soft_effective_used_60s") or 0), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_seen_trade_ids_bounded(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["seen_trade_ids_max"] = 2
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            manager._handle_fill(
                FillEvent(trade_id="x1", token_id="t1", side="BUY", price=0.4, size=1.0, ts_utc=utc_iso())
            )
            manager._handle_fill(
                FillEvent(trade_id="x2", token_id="t1", side="BUY", price=0.4, size=1.0, ts_utc=utc_iso())
            )
            manager._handle_fill(
                FillEvent(trade_id="x3", token_id="t1", side="BUY", price=0.4, size=1.0, ts_utc=utc_iso())
            )
            self.assertEqual(manager.snapshot_seen_trade_ids(), ["x2", "x3"])
            self.assertEqual(len(manager.seen_trade_ids), 2)

            manager._handle_fill(
                FillEvent(trade_id="x3", token_id="t1", side="BUY", price=0.4, size=1.0, ts_utc=utc_iso())
            )
            self.assertEqual(manager.snapshot_seen_trade_ids(), ["x2", "x3"])
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_process_fills_counts_unique_only(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            duplicate_fill = FillEvent(
                trade_id="dup-1",
                token_id="t1",
                side="BUY",
                price=0.4,
                size=1.0,
                ts_utc=utc_iso(),
            )
            gateway._fill_queue.extend([duplicate_fill, duplicate_fill])  # pylint: disable=protected-access
            self.assertEqual(manager.process_fills(), 1)
            self.assertEqual(telemetry.counters.get("fills"), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_paper_trade_ids_are_unique_across_restarts(self):
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.50,
            best_ask_size=100.0,
        )
        intent = OrderIntent(token_id="t1", side="BUY", price=0.50, size=5.0, tif="IOC", post_only=False)

        first_gateway = PaperGateway()
        first_gateway.on_book(top)
        first_gateway.place_order(intent, client_order_id="first")
        first_fill = first_gateway.poll_fills()[0]

        second_gateway = PaperGateway()
        second_gateway.on_book(top)
        second_gateway.place_order(intent, client_order_id="second")
        second_fill = second_gateway.poll_fills()[0]

        self.assertNotEqual(first_fill.trade_id, second_fill.trade_id)

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(PaperGateway(), strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)
            manager.restore_seen_trade_ids([first_fill.trade_id])

            self.assertTrue(manager._handle_fill(second_fill))  # pylint: disable=protected-access
            self.assertEqual(telemetry.counters.get("fills"), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_orphan_cancel_uses_tracked_tokens_not_books(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1"), "t2": Position(token_id="t2")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            gateway.place_order(
                OrderIntent(token_id="t2", side="BUY", price=0.4, size=5.0),
                client_order_id="manual-t2",
            )
            summary = manager.step({}, tracked_tokens={"t2"})
            self.assertEqual(summary["open_orders"], 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_cancel_non_target_orders_cancels_removed_token_orders(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1"), "t2": Position(token_id="t2")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            gateway.place_order(
                OrderIntent(token_id="t1", side="BUY", price=0.4, size=5.0),
                client_order_id="manual-t1",
            )
            gateway.place_order(
                OrderIntent(token_id="t2", side="BUY", price=0.4, size=5.0),
                client_order_id="manual-t2",
            )
            canceled = manager.cancel_non_target_orders({"t1"})
            self.assertEqual(canceled, 1)
            remaining = gateway.get_open_orders()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].token_id, "t1")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_soft_order_throttle_prevents_hard_rate_rejects(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 0.5
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_orders_per_min"] = 2
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            summary = manager.step({"t1": top})
            self.assertEqual(summary["open_orders"], 1)
            self.assertGreaterEqual(telemetry.counters.get("order_soft_throttle_skips", 0), 1)
            self.assertEqual(telemetry.counters.get("risk_rejects", 0), 0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_low_quality_quotes_are_skipped(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = True
            strategy_cfg["execution_quality"]["min_expected_fill_prob"] = 0.99
            strategy_cfg["execution_quality"]["queue_depth_scale"] = 1.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            summary = manager.step({"t1": top})
            self.assertEqual(summary["open_orders"], 0)
            self.assertGreaterEqual(telemetry.counters.get("low_quality_quote_skips", 0), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_deep_queue_quotes_are_skipped_by_queue_cap(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = True
            strategy_cfg["execution_quality"]["min_expected_fill_prob"] = 0.0
            strategy_cfg["execution_quality"]["max_queue_ahead_size"] = 20.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            summary = manager.step({"t1": top})
            self.assertEqual(summary["open_orders"], 0)
            self.assertGreaterEqual(telemetry.counters.get("low_quality_quote_skips", 0), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_respects_max_actions_override(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            summary = manager.step({"t1": top}, max_actions_override=1)
            self.assertEqual(summary["actions"], 1)
            self.assertEqual(summary["open_orders"], 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_fetches_open_orders_once_per_cycle(self):
        class _CountingPaperGateway(PaperGateway):
            def __init__(self):
                super().__init__()
                self.get_open_orders_calls = 0

            def get_open_orders(self):
                self.get_open_orders_calls += 1
                return super().get_open_orders()

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = _CountingPaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100,
                best_ask_price=0.55,
                best_ask_size=100,
            )
            manager.step({"t1": top})
            self.assertEqual(gateway.get_open_orders_calls, 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_taker_order_hits_book_immediately(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            risk_cfg["max_book_age_sec"] = 100.0
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=100,
                best_ask_price=0.51,
                best_ask_size=100,
            )
            gateway.on_book(top)
            ok = manager.place_taker_order(
                token_id="t1",
                side="BUY",
                price=0.51,
                size=10.0,
                target_usd=None,
                top=top,
                reason="unit_test_taker",
            )
            self.assertTrue(ok)
            self.assertEqual(telemetry.counters.get("taker_orders_submitted"), 1)
            self.assertEqual(telemetry.counters.get("taker_orders_filled"), 1)
            self.assertGreater(positions["t1"].buy_shares, 0.0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_notional_sizing_converts_usd_to_shares(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
            sizing_cfg["mode"] = "notional"
            sizing_cfg["min_usd"] = 1.0
            sizing_cfg["max_usd"] = 20.0
            sizing_cfg["target_usd"] = 5.0
            sizing_cfg["rounding"] = "floor"
            sizing_cfg["price_source"] = "mid"
            sizing_cfg["share_step"] = 0.01
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(
                gateway,
                strategy,
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=100,
                best_ask_price=0.51,
                best_ask_size=100,
            )
            from prodesk.models import OrderIntent

            sized = manager._resolve_order_size_shares(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.50, size=25.0),
                top,
            )
            self.assertAlmostEqual(sized or 0.0, 10.0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_notional_sizing_enforces_bounds_after_rounding(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
            sizing_cfg["mode"] = "notional"
            sizing_cfg["min_usd"] = 1.0
            sizing_cfg["max_usd"] = 20.0
            sizing_cfg["target_usd"] = 1.0
            sizing_cfg["rounding"] = "floor"
            sizing_cfg["price_source"] = "mid"
            sizing_cfg["share_step"] = 1.0
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(
                gateway,
                strategy,
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.98,
                best_bid_size=100,
                best_ask_price=1.00,
                best_ask_size=100,
            )
            from prodesk.models import OrderIntent

            sized = manager._resolve_order_size_shares(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.99, size=25.0),
                top,
            )
            self.assertIsNone(sized)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_notional_sizing_clamps_to_max_order_size(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["max_order_size"] = 200.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
            sizing_cfg["mode"] = "notional"
            sizing_cfg["min_usd"] = 1.0
            sizing_cfg["max_usd"] = 20.0
            sizing_cfg["target_usd"] = 20.0
            sizing_cfg["rounding"] = "floor"
            sizing_cfg["price_source"] = "mid"
            sizing_cfg["share_step"] = 0.01
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(
                gateway,
                strategy,
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.009,
                best_bid_size=100,
                best_ask_price=0.011,
                best_ask_size=100,
            )
            from prodesk.models import OrderIntent

            sized = manager._resolve_order_size_shares(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.01, size=25.0),
                top,
            )
            self.assertAlmostEqual(sized or 0.0, 200.0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_maker_notional_sizing_applies_competitive_depth_overlay(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["max_order_size"] = 1000.0
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
            sizing_cfg["mode"] = "notional"
            sizing_cfg["min_usd"] = 1.0
            sizing_cfg["max_usd"] = 300.0
            sizing_cfg["target_usd"] = 5.0
            sizing_cfg["maker_competitive_min_notional_usd"] = 100.0
            sizing_cfg["maker_competitive_max_notional_usd"] = 250.0
            sizing_cfg["maker_competitive_min_shares"] = 200.0
            sizing_cfg["maker_competitive_max_shares"] = 800.0
            sizing_cfg["maker_depth_target_min_ratio"] = 0.15
            sizing_cfg["maker_depth_target_max_ratio"] = 0.30
            sizing_cfg["maker_depth_target_ratio"] = 0.20
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(
                gateway,
                strategy,
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=2200.0,
                best_ask_price=0.51,
                best_ask_size=2200.0,
            )
            sized = manager._resolve_order_size_shares(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.50, size=25.0, tif="GTC", post_only=True),
                top,
            )
            # 20% of visible buy-side depth -> 440 shares at ~0.50 midpoint (220 USD notional).
            self.assertAlmostEqual(sized or 0.0, 440.0, places=6)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_taker_notional_sizing_ignores_maker_competitive_minimums(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
            sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
            sizing_cfg["mode"] = "notional"
            sizing_cfg["min_usd"] = 1.0
            sizing_cfg["max_usd"] = 300.0
            sizing_cfg["target_usd"] = 5.0
            sizing_cfg["maker_competitive_min_notional_usd"] = 100.0
            sizing_cfg["maker_competitive_min_shares"] = 200.0
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(
                gateway,
                strategy,
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=2200.0,
                best_ask_price=0.51,
                best_ask_size=2200.0,
            )
            sized = manager._resolve_order_size_shares(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.50, size=25.0, tif="IOC", post_only=False),
                top,
                notional_target_usd=5.0,
            )
            self.assertAlmostEqual(sized or 0.0, 10.0, places=6)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_paper_gateway_passive_touch_fill_generates_fills(self):
        gateway = PaperGateway(
            {
                "paper_passive_touch_fill_enabled": True,
                "paper_passive_touch_fill_ratio": 1.0,
                "paper_passive_min_rest_sec": 0.0,
                "paper_passive_min_fill_size": 0.01,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        gateway.on_book(top)
        gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.49, size=10.0, tif="GTC", post_only=True),
            client_order_id="cid1",
        )
        gateway.on_book(top)
        fills = gateway.poll_fills()
        self.assertTrue(fills)
        self.assertGreaterEqual(sum(fill.size for fill in fills), 10.0)
        self.assertTrue(all(str(fill.fill_policy_basis or "") == "synthetic_touch_fill" for fill in fills))
        self.assertTrue(all(str(fill.execution_realism_class or "") == "not_modeled" for fill in fills))

    def test_paper_gateway_rejects_post_only_crossing_order(self):
        gateway = PaperGateway()
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        gateway.on_book(top)

        with self.assertRaises(PostOnlyRejectError):
            gateway.place_order(
                OrderIntent(token_id="t1", side="BUY", price=0.51, size=5.0, tif="GTC", post_only=True),
                client_order_id="cid-post-only-explicit",
            )

        with self.assertRaises(PostOnlyRejectError):
            gateway.place_order(
                OrderIntent(token_id="t1", side="BUY", price=0.51, size=5.0, tif="GTC"),
                client_order_id="cid-post-only-default",
            )

        self.assertEqual(gateway.get_open_orders(), [])

    def test_paper_gateway_immediate_order_requires_touch_size(self):
        gateway = PaperGateway()
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.50,
            best_ask_size=None,
        )
        gateway.on_book(top)
        order = gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.50, size=7.0, tif="IOC", post_only=False),
            client_order_id="cid-ioc-missing-liq",
        )
        fills = gateway.poll_fills()
        self.assertEqual(order.status, "CANCELED")
        self.assertEqual(order.remaining_size, 7.0)
        self.assertFalse(fills)

    def test_paper_gateway_resting_cross_requires_touch_size(self):
        gateway = PaperGateway()
        initial = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.60,
            best_ask_size=100.0,
        )
        gateway.on_book(initial)
        placed = gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.55, size=10.0, tif="GTC", post_only=True),
            client_order_id="cid-resting-missing-liq",
        )
        self.assertEqual(placed.status, "OPEN")

        crossed_without_size = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.54,
            best_bid_size=100.0,
            best_ask_price=0.55,
            best_ask_size=None,
        )
        gateway.on_book(crossed_without_size)
        fills = gateway.poll_fills()
        open_orders = gateway.get_open_orders()

        self.assertFalse(fills)
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(open_orders[0].remaining_size, 10.0)
        self.assertEqual(open_orders[0].status, "OPEN")

    def test_paper_gateway_passive_near_touch_fill_generates_partial_fill(self):
        gateway = PaperGateway(
            {
                "paper_passive_touch_fill_enabled": True,
                "paper_passive_touch_fill_ratio": 0.0,
                "paper_passive_near_touch_band": 0.02,
                "paper_passive_near_touch_fill_ratio": 0.5,
                "paper_passive_min_rest_sec": 0.0,
                "paper_passive_min_fill_size": 0.01,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.50,
            best_bid_size=100,
            best_ask_price=0.52,
            best_ask_size=100,
        )
        gateway.on_book(top)
        gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.49, size=10.0, tif="GTC", post_only=True),
            client_order_id="cid2",
        )
        gateway.on_book(top)
        fills = gateway.poll_fills()
        self.assertTrue(fills)
        self.assertGreater(sum(fill.size for fill in fills), 0.0)
        self.assertTrue(all(str(fill.fill_policy_basis or "") == "synthetic_near_touch_fill" for fill in fills))
        self.assertTrue(all(str(fill.execution_realism_class or "") == "not_modeled" for fill in fills))

    def test_paper_gateway_background_fill_generates_fill_away_from_touch(self):
        gateway = PaperGateway(
            {
                "paper_passive_touch_fill_enabled": True,
                "paper_passive_touch_fill_ratio": 0.0,
                "paper_passive_near_touch_band": 0.0,
                "paper_passive_near_touch_fill_ratio": 0.0,
                "paper_background_fill_ratio": 0.1,
                "paper_passive_min_rest_sec": 0.0,
                "paper_passive_min_fill_size": 0.01,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.50,
            best_bid_size=100,
            best_ask_price=0.52,
            best_ask_size=100,
        )
        gateway.on_book(top)
        gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.45, size=10.0, tif="GTC", post_only=True),
            client_order_id="cid3",
        )
        gateway.on_book(top)
        fills = gateway.poll_fills()
        self.assertTrue(fills)
        self.assertGreater(sum(fill.size for fill in fills), 0.0)
        self.assertTrue(all(str(fill.fill_policy_basis or "") == "synthetic_background_fill" for fill in fills))
        self.assertTrue(all(str(fill.execution_realism_class or "") == "not_modeled" for fill in fills))

    def test_paper_gateway_tod_liquidity_scaler_reduces_immediate_fill_depth(self):
        gateway = PaperGateway(
            {
                "paper_liquidity_tod_scaler_enabled": True,
                "paper_liquidity_tod_start_hour_utc": 2,
                "paper_liquidity_tod_end_hour_utc": 6,
                "paper_liquidity_tod_depth_multiplier": 0.5,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc="2026-01-01T03:00:00Z",
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.50,
            best_ask_size=100.0,
        )
        gateway.on_book(top)
        order = gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.50, size=100.0, tif="IOC", post_only=False),
            client_order_id="cid-tod-liq",
        )
        fills = gateway.poll_fills()
        self.assertEqual(order.status, "PARTIAL")
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(float(fills[0].size), 50.0, places=6)
        self.assertAlmostEqual(float(fills[0].paper_liquidity_depth_multiplier or 0.0), 0.5, places=6)

    def test_paper_gateway_queue_proxy_reduces_resting_cross_fill(self):
        gateway = PaperGateway(
            {
                "paper_queue_position_mode": "bounded_top_depth_proxy",
                "paper_queue_position_ahead_ratio": 0.4,
            }
        )
        initial = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.60,
            best_ask_size=100.0,
        )
        gateway.on_book(initial)
        gateway.place_order(
            OrderIntent(token_id="t1", side="BUY", price=0.55, size=100.0, tif="GTC", post_only=True),
            client_order_id="cid-queue-proxy",
        )
        crossed = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.54,
            best_bid_size=100.0,
            best_ask_price=0.55,
            best_ask_size=100.0,
        )
        gateway.on_book(crossed)
        fills = gateway.poll_fills()
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(float(fills[0].size), 60.0, places=6)
        self.assertEqual(
            str(fills[0].fill_policy_basis or ""),
            "bounded_visible_liquidity_top_of_book_with_queue_proxy",
        )
        self.assertEqual(str(fills[0].paper_queue_position_mode or ""), "bounded_top_depth_proxy")
        self.assertAlmostEqual(float(fills[0].paper_queue_fill_multiplier or 0.0), 0.6, places=6)
        self.assertAlmostEqual(float(fills[0].paper_maker_eligible_depth or 0.0), 60.0, places=6)
        self.assertAlmostEqual(float(fills[0].paper_maker_depth_consumption_ratio or 0.0), 0.6, places=6)

    def test_paper_gateway_taker_lag_unknown_is_fail_closed_no_penalty(self):
        gateway = PaperGateway(
            {
                "paper_chainlink_lag_emulation_enabled": True,
                "paper_chainlink_lag_window_low_sec": 2.0,
                "paper_chainlink_lag_window_high_sec": 15.0,
                "paper_chainlink_lag_penalty_bps_within_window": 2.0,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.50,
            best_ask_size=100.0,
        )
        gateway.on_book(top)
        order = gateway.place_order(
            OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=10.0,
                tif="IOC",
                post_only=False,
                reason="sniper_taker_chainlink",
                oracle_tick_age_sec=3.0,
                token_median_lag_ms=None,
            ),
            client_order_id="cid-lag-unknown",
        )
        fills = gateway.poll_fills()
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(len(fills), 1)
        self.assertEqual(str(fills[0].paper_chainlink_lag_class or ""), "unknown")
        self.assertIsNone(fills[0].paper_chainlink_lag_sec_effective)
        self.assertAlmostEqual(float(fills[0].paper_chainlink_lag_penalty_bps or 0.0), 0.0, places=9)
        self.assertAlmostEqual(float(fills[0].price), 0.50, places=9)

    def test_paper_gateway_taker_lag_penalty_applies_when_classified(self):
        gateway = PaperGateway(
            {
                "paper_chainlink_lag_emulation_enabled": True,
                "paper_chainlink_lag_window_low_sec": 2.0,
                "paper_chainlink_lag_window_high_sec": 15.0,
                "paper_chainlink_lag_penalty_bps_below_window": 0.0,
                "paper_chainlink_lag_penalty_bps_within_window": 2.0,
                "paper_chainlink_lag_penalty_bps_above_window": 4.0,
            }
        )
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="ws",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.50,
            best_ask_size=100.0,
        )
        gateway.on_book(top)
        gateway.place_order(
            OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=10.0,
                tif="IOC",
                post_only=False,
                reason="sniper_taker_chainlink",
                oracle_tick_age_sec=3.0,
                token_median_lag_ms=5000.0,
            ),
            client_order_id="cid-lag-known",
        )
        fills = gateway.poll_fills()
        self.assertEqual(len(fills), 1)
        self.assertEqual(str(fills[0].paper_chainlink_lag_class or ""), "within_window")
        self.assertAlmostEqual(float(fills[0].paper_chainlink_lag_sec_effective or 0.0), 5.0, places=9)
        self.assertAlmostEqual(float(fills[0].paper_chainlink_lag_penalty_bps or 0.0), 2.0, places=9)
        self.assertAlmostEqual(float(fills[0].price), 0.50 * (1.0 + 2.0 / 10000.0), places=9)

    def test_runner_recovers_from_corrupt_state_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["chainlink"]["enabled"] = False

            state_path = Path(cfg["storage"]["state_path"])
            state_path.write_text("{not valid json", encoding="utf-8")

            runner = ExecutionRunner(cfg)
            try:
                self.assertEqual(runner.manager.snapshot_seen_trade_ids(), [])
                self.assertEqual(runner.token_ids, ["t1"])
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_sanitizes_invalid_state_values(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["chainlink"]["enabled"] = False

            state_path = Path(cfg["storage"]["state_path"])
            state_payload = {
                "positions": {
                    "t1": {
                        "net_shares": "bad",
                        "buy_shares": "1.25",
                        "sell_shares": None,
                        "bought_notional": "bad",
                        "sold_notional": "2.5",
                    }
                },
                "seen_trade_ids": "not-a-list",
            }
            state_path.write_text(json.dumps(state_payload), encoding="utf-8")

            runner = ExecutionRunner(cfg)
            try:
                pos = runner.risk.positions["t1"]
                self.assertEqual(pos.net_shares, 0.0)
                self.assertEqual(pos.buy_shares, 1.25)
                self.assertEqual(pos.sell_shares, 0.0)
                self.assertEqual(pos.bought_notional, 0.0)
                self.assertEqual(pos.sold_notional, 2.5)
                self.assertEqual(runner.manager.snapshot_seen_trade_ids(), [])
                self.assertIsNone(runner.manager.snapshot_last_fill_ts())
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_state_dump_persists_bounded_seen_trade_ids(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["persist_seen_trade_ids_max"] = 2

            runner = ExecutionRunner(cfg)
            try:
                runner.manager.restore_seen_trade_ids(["id1", "id2", "id3"])
                runner.manager.restore_last_fill_ts("2026-01-01T00:00:00Z")
                runner._dump_state()
                persisted = json.loads(Path(cfg["storage"]["state_path"]).read_text(encoding="utf-8"))
                self.assertEqual(persisted.get("seen_trade_ids"), ["id2", "id3"])
                self.assertEqual(persisted.get("last_fill_ts_utc"), "2026-01-01T00:00:00.000Z")
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_run_manifest_written_with_config_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["chainlink"]["enabled"] = False

            runner = ExecutionRunner(cfg)
            try:
                runner._write_run_manifest()
                self.assertTrue(runner.run_manifest_path.exists())
                payload = json.loads(runner.run_manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(payload.get("run_id"), runner.run_id)
                self.assertEqual(payload.get("bot_name"), "Bro")
                self.assertTrue(str(payload.get("config_fingerprint_sha256", "")))
                self.assertTrue(str(payload.get("profile_name", "")))
                self.assertTrue(str(payload.get("git_commit", "")))
                self.assertTrue(str(payload.get("status_path", "")))
                self.assertTrue(str(payload.get("events_path", "")))
                self.assertTrue(str(payload.get("start_ts", "")))
                self.assertIn("end_ts", payload)
                self.assertIn("config_source_path", payload)
                self.assertIn("config_source_sha256", payload)
                self.assertTrue(str(payload.get("code_fingerprint_sha256", "")))
                self.assertGreaterEqual(int(payload.get("code_fingerprint_file_count", 0)), 1)
                self.assertIsInstance(payload.get("runtime_env_hints"), dict)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_builds_chainlink_fair_probability_and_taker_snipes(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t1": utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=18))
            }
            cfg["targets"]["token_side_by_token"] = {"t1": "YES"}
            cfg["targets"]["token_strike_by_token"] = {"t1": 65000.0}
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["sniper"]["require_lag_verification"] = True
            cfg["latency_verifier"]["min_samples"] = 1
            cfg["latency_verifier"]["hit_threshold_ms"] = 1.0
            cfg["latency_verifier"]["armed_min_median_ms"] = 1.0
            cfg["latency_verifier"]["armed_min_hit_rate"] = 1.0
            cfg["latency_verifier"]["probation_min_median_ms"] = 1.0
            cfg["latency_verifier"]["probation_min_hit_rate"] = 1.0
            cfg["latency_verifier"]["arm_consecutive_cycles"] = 1
            cfg["sniper"]["taker"]["enabled"] = True
            cfg["sniper"]["taker"]["min_edge"] = 0.001
            cfg["sniper"]["taker"]["order_size"] = 5.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner._record_lag_sample("t1", 10.0)
                tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=65100.0,
                    source_ts_utc=None,
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic(),
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._latest_by_symbol["btc/usd"] = tick  # pylint: disable=protected-access
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.45,
                    best_bid_size=100,
                    best_ask_price=0.55,
                    best_ask_size=100,
                )
                books = {"t1": top}
                runner.gateway.on_book(top)
                latency_snapshot = runner.latency_verifier.snapshot(active_tokens=["t1"])
                latency_snapshot = runner.latency_verifier.snapshot(active_tokens=["t1"])
                fair = runner._build_fair_probability_map(books, latency_snapshot=latency_snapshot)
                self.assertIn("t1", fair)
                sniper_ctx = runner._sniper_context()
                self.assertTrue(sniper_ctx["active"])
                out = runner._run_sniper_taker(
                    books=books,
                    fair_probability_by_token=fair,
                    token_ids=["t1"],
                    stage_info_by_token={"t1": {"stage": "SNIPER_PRIMARY", "sec_to_expiry": 18.0}},
                    oracle_tick_age_sec=0.0,
                    latency_snapshot=latency_snapshot,
                    lag_verified_token_ids=["t1"],
                )
                self.assertGreaterEqual(out["submitted"], 1)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_fair_probability_math_is_bounded_monotonic_and_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                low = runner._fair_probability_up(spot=64000.0, strike=65000.0, sec_to_expiry=120.0)
                mid = runner._fair_probability_up(spot=65000.0, strike=65000.0, sec_to_expiry=120.0)
                high = runner._fair_probability_up(spot=66000.0, strike=65000.0, sec_to_expiry=120.0)

                self.assertGreater(low, 0.0)
                self.assertLess(low, 1.0)
                self.assertGreater(mid, 0.0)
                self.assertLess(mid, 1.0)
                self.assertGreater(high, 0.0)
                self.assertLess(high, 1.0)
                self.assertLess(low, mid)
                self.assertLess(mid, high)

                short_horizon = runner._fair_probability_up(spot=66000.0, strike=65000.0, sec_to_expiry=30.0)
                long_horizon = runner._fair_probability_up(spot=66000.0, strike=65000.0, sec_to_expiry=600.0)
                self.assertGreater(abs(short_horizon - 0.5), abs(long_horizon - 0.5))

                repeat = runner._fair_probability_up(spot=66000.0, strike=65000.0, sec_to_expiry=30.0)
                self.assertAlmostEqual(short_horizon, repeat, places=12)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_sniper_taker_prioritizes_highest_edge(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1", "t2"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["sniper"]["taker"]["enabled"] = True
            cfg["sniper"]["taker"]["max_orders_per_cycle"] = 1
            cfg["sniper"]["taker"]["min_edge"] = 0.001
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top1 = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100,
                    best_ask_price=0.51,
                    best_ask_size=100,
                )
                top2 = BookTop(
                    token_id="t2",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100,
                    best_ask_price=0.51,
                    best_ask_size=100,
                )
                books = {"t1": top1, "t2": top2}
                fair = {"t1": 0.52, "t2": 0.8}
                picked: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    picked.append(str(kwargs.get("token_id")))
                    return {"submitted": True, "fills_accepted": 0, "order_id": f"ord-{kwargs.get('token_id')}"}

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ):
                    out = runner._run_sniper_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1", "t2"],
                        stage_info_by_token={
                            "t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
                            "t2": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1", "t2"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(picked, ["t2"])
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_disarmed_signal_ignored_when_chainlink_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                snap = runner.latency_verifier.snapshot(active_tokens=["t1"])
                self.assertFalse(runner._disarmed_cycle_signal(snap))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_disarmed_signal_ignored_for_non_fault_lag_edge_absence(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                snap = LatencySnapshot(
                    state="disarmed",
                    previous_state="probation",
                    changed=True,
                    reason="lag_edge_not_present",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=1,
                    median_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    hit_rate=max(0.0, runner.latency_verifier.probation_min_hit_rate - 0.01),
                    armed=False,
                    probation=False,
                    disarmed=True,
                )
                self.assertFalse(runner._disarmed_cycle_signal(snap))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_disarmed_signal_retained_for_fault_disarm(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                snap = LatencySnapshot(
                    state="disarmed",
                    previous_state="armed",
                    changed=True,
                    reason="armed_thresholds_lost",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=1,
                    median_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.probation_min_median_ms,
                    hit_rate=max(0.0, runner.latency_verifier.probation_min_hit_rate - 0.01),
                    armed=False,
                    probation=False,
                    disarmed=True,
                )
                self.assertTrue(runner._disarmed_cycle_signal(snap))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_refresh_targets_empty_result_enters_standdown_and_clears_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1", "t2"]
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                result = type(
                    "DiscoveryStub",
                    (),
                    {
                        "token_ids": [],
                        "pairs_selected": 0,
                        "scanned_markets": 100,
                        "fee_eligible_markets": 10,
                        "contract_rejected_pairs": 5,
                        "allowlist_enabled": False,
                        "allowlist_rejected_pairs": 0,
                        "token_expiry_utc_by_token": {},
                        "token_side_by_token": {},
                        "token_strike_by_token": {},
                        "token_market_key_by_token": {},
                    },
                )()
                with mock.patch.object(runner.discovery, "discover", return_value=result):
                    runner._refresh_targets(force=True)
                self.assertEqual(runner.token_ids, [])
                self.assertEqual(runner.book_feed.status().get("token_count"), 0)
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_standdown"), 1.0)
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_active_targets"), 0.0)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_refresh_targets_transitions_between_standdown_and_active(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1", "t2"]
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                standdown_result = type(
                    "DiscoveryStub",
                    (),
                    {
                        "token_ids": [],
                        "pairs_selected": 0,
                        "scanned_markets": 100,
                        "fee_eligible_markets": 10,
                        "contract_rejected_pairs": 1,
                        "allowlist_enabled": False,
                        "allowlist_rejected_pairs": 0,
                        "token_expiry_utc_by_token": {},
                        "token_side_by_token": {},
                        "token_strike_by_token": {},
                        "token_market_key_by_token": {},
                    },
                )()
                active_result = type(
                    "DiscoveryStub",
                    (),
                    {
                        "token_ids": ["yes1", "no1"],
                        "pairs_selected": 1,
                        "scanned_markets": 100,
                        "fee_eligible_markets": 10,
                        "contract_rejected_pairs": 0,
                        "allowlist_enabled": False,
                        "allowlist_rejected_pairs": 0,
                        "token_expiry_utc_by_token": {"yes1": "2030-01-01T00:01:00.000Z", "no1": "2030-01-01T00:01:00.000Z"},
                        "token_side_by_token": {"yes1": "YES", "no1": "NO"},
                        "token_strike_by_token": {"yes1": 50000.0, "no1": 50000.0},
                        "token_market_key_by_token": {"yes1": "mk1", "no1": "mk1"},
                    },
                )()
                with mock.patch.object(runner.discovery, "discover", side_effect=[standdown_result, active_result, standdown_result]):
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, [])
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, ["yes1", "no1"])
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, [])
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runtime_semantics_marks_no_target_standdown_without_kill_switch(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._update_runtime_semantics(has_targets=False)
                self.assertEqual(runner._runtime_state, "no_target_standdown")
                self.assertFalse(runner.risk.kill_switch)
                self.assertFalse(runner._runtime_book_feed_required)
                self.assertTrue(bool(runner._runtime_no_target_standdown))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_external_guard_file_engages_kill_switch(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["guard_stop_file"] = str(Path(td) / "guard_stop.txt")
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            guard_path = Path(cfg["runtime"]["guard_stop_file"])
            guard_path.write_text("manual stop\n", encoding="utf-8")

            runner = ExecutionRunner(cfg)
            try:
                runner._apply_external_guard_stop()
                self.assertTrue(runner.risk.kill_switch)
                self.assertIn("external_guard_stop", runner.risk.kill_reason or "")
                self.assertEqual(runner.telemetry.gauges.get("external_guard_stop_active"), 1.0)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_external_guard_file_can_be_cleared_on_start(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["guard_stop_file"] = str(Path(td) / "guard_stop.txt")
            cfg["runtime"]["clear_guard_stop_on_start"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            guard_path = Path(cfg["runtime"]["guard_stop_file"])
            guard_path.write_text("stale guard\n", encoding="utf-8")

            runner = ExecutionRunner(cfg)
            try:
                self.assertTrue(guard_path.exists())
                runner._clear_external_guard_stop_on_start()
                self.assertFalse(guard_path.exists())
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_external_guard_uses_first_non_empty_line_as_reason(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["guard_stop_file"] = str(Path(td) / "guard_stop.txt")
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            guard_path = Path(cfg["runtime"]["guard_stop_file"])
            guard_path.write_text("\n\nmanual_critical_stop\nmetadata\n", encoding="utf-8")

            runner = ExecutionRunner(cfg)
            try:
                active, reason = runner._read_external_guard_stop()
                self.assertTrue(active)
                self.assertEqual(reason, "manual_critical_stop")
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_external_guard_directory_path_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["guard_stop_file"] = td
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner._apply_external_guard_stop()
                runner._apply_external_guard_stop()
                self.assertFalse(runner.risk.kill_switch)
                self.assertGreaterEqual(runner.telemetry.counters.get("external_guard_errors", 0), 1)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_latency_sampling_token_ids_include_only_ws_sources(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_ws", "t_rest", "t_unknown"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                books = {
                    "t_ws": BookTop(
                        token_id="t_ws",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.49,
                        best_bid_size=10.0,
                        best_ask_price=0.51,
                        best_ask_size=10.0,
                    ),
                    "t_rest": BookTop(
                        token_id="t_rest",
                        ts_utc=utc_iso(),
                        source="rest",
                        best_bid_price=0.49,
                        best_bid_size=10.0,
                        best_ask_price=0.51,
                        best_ask_size=10.0,
                    ),
                    "t_unknown": BookTop(
                        token_id="t_unknown",
                        ts_utc=utc_iso(),
                        source="",
                        best_bid_price=0.49,
                        best_bid_size=10.0,
                        best_ask_price=0.51,
                        best_ask_size=10.0,
                    ),
                }
                selected = runner._latency_sample_token_ids(books)
                self.assertEqual(selected, ["t_ws"])
                self.assertTrue(runner._book_source_is_ws(books["t_ws"]))
                self.assertFalse(runner._book_source_is_ws(books["t_rest"]))
                self.assertFalse(runner._book_source_is_ws(books["t_unknown"]))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()


if __name__ == "__main__":
    unittest.main()
