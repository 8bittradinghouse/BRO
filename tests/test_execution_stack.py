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
from prodesk.config import DEFAULT_EXECUTION_CONFIG, load_execution_config, validate_execution_config
from prodesk.gateway import PaperGateway, PostOnlyRejectError
from prodesk.latency_verifier import LatencySnapshot
from prodesk.logging_utils import EventLogger
from prodesk.models import BookTop, FillEvent, LiveOrder, OrderIntent, Position
from prodesk.order_manager import OrderManager
from prodesk.risk import RiskEngine
from prodesk.strategy import MarketMakingStrategy
from prodesk.telemetry import Telemetry
from prodesk.wallet.wallet_truth_policy import PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT
from prodesk.wallet_doctrine import WalletAuthorization


class ExecutionStackTests(unittest.TestCase):
    @staticmethod
    def _risk_cfg_without_expiry_gate() -> dict:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
        cfg["min_sec_to_expiry_for_new_exposure"] = 0.0
        return cfg

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

    def test_paper_universal_profile_wires_held_book_not_found_recovery_thresholds(self):
        cfg_path = (Path(__file__).resolve().parents[1] / "configs/profiles/paper_universal.yaml").resolve()
        cfg = load_execution_config(cfg_path)
        runtime = dict(cfg.get("runtime") or {})
        risk = dict(cfg.get("risk") or {})
        strategy = dict(cfg.get("strategy") or {})
        maker_comp = dict(strategy.get("maker_competitiveness") or {})
        self.assertAlmostEqual(float(runtime.get("held_book_not_found_backoff_sec") or 0.0), 5.0, places=9)
        self.assertAlmostEqual(
            float(runtime.get("held_book_not_found_force_refresh_min_unpriceable_age_sec") or 0.0),
            20.0,
            places=9,
        )
        self.assertAlmostEqual(
            float(runtime.get("held_book_not_found_force_refresh_interval_sec") or 0.0),
            45.0,
            places=9,
        )
        self.assertAlmostEqual(float(risk.get("min_sec_to_expiry_for_new_exposure") or 0.0), 45.0, places=9)
        self.assertLess(
            float(risk.get("min_sec_to_expiry_for_new_exposure") or 0.0),
            float(maker_comp.get("timing_gate_max_sec_to_expiry") or 0.0),
        )
        expected = str(runtime.get("paper_expected_config_fingerprint_sha256") or "").strip().lower()
        observed = str((cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
        self.assertEqual(expected, observed)

    def test_config_rejects_duplicate_target_ids(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["dup", "dup"]
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_risk_dynamic_scaling_bounds(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["risk"]["dynamic_scaling"]["min_effective_mult"] = 0.9
        cfg["risk"]["dynamic_scaling"]["max_effective_mult"] = 0.8
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

    def test_config_rejects_maker_competitive_floor_when_notional_mode_disabled(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sizing"]["mode"] = "shares"
        cfg["sizing"]["maker_competitive_min_notional_usd"] = 100.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_sniper_stage_window_boost_alignment(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        taker_comp = cfg["sniper"]["taker"]["competitiveness"]
        taker_comp["stage_final_window_sec_by_stage"] = {"SNIPER_PRIMARY": 12.0}
        taker_comp["multi_oracle_boost_window_sec"] = 15.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_sniper_stage_cooldown_key(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sniper"]["taker"]["per_token_cooldown_sec_by_stage"] = {"INVALID_STAGE": 0.75}
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_wallet_chain_outside_polygon(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["wallet"]["chain"] = "ethereum"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_wallet_gas_target_below_min(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["wallet"]["min_pol_gas_reserve"] = 0.2
        cfg["wallet"]["gas_reserve_target_pol"] = 0.1
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_wallet_provider_ambiguity_abs_tolerance_non_positive(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["wallet"]["provider_ambiguity_abs_tolerance"] = 0.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_wallet_provider_ambiguity_rel_tolerance_negative(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["wallet"]["provider_ambiguity_rel_tolerance"] = -PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_wallet_physical_treasury_without_address(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["wallet"]["treasury_mode"] = "physical"
        cfg["wallet"]["treasury_wallet_address"] = ""
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_live_wallet_allowance_without_spender_targets(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["mode"] = "live"
        cfg["wallet"]["require_allowance"] = True
        cfg["wallet"]["approval_spender_targets"] = []
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_non_boolean_live_order_submission_enabled(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["auth"]["live_order_submission_enabled"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_order_capable_live_without_strict_wallet_truth_flags(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["mode"] = "live"
        cfg["auth"]["live_order_submission_enabled"] = True
        cfg["wallet"]["require_live_nonce_snapshot"] = False
        cfg["wallet"]["require_live_nonce_value"] = True
        cfg["wallet"]["require_live_pending_tx_snapshot"] = True
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_allows_live_diagnostic_mode_without_strict_wallet_truth_flags(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["mode"] = "live"
        cfg["auth"]["live_order_submission_enabled"] = False
        cfg["wallet"]["require_allowance"] = False
        cfg["wallet"]["require_live_nonce_snapshot"] = False
        cfg["wallet"]["require_live_nonce_value"] = False
        cfg["wallet"]["require_live_pending_tx_snapshot"] = False
        validate_execution_config(cfg)

    def test_config_rejects_unachievable_maker_notional_floor(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sizing"]["maker_competitive_min_notional_usd"] = 100.0
        cfg["sizing"]["max_usd"] = 50.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_unachievable_maker_share_floor_against_risk_cap(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sizing"]["maker_competitive_min_shares"] = 200.0
        cfg["risk"]["max_order_size"] = 100.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_taker_stage_aggressiveness_size_mult_below_one(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sniper"]["taker"]["competitiveness"]["stage_aggressiveness"]["SNIPER_PRIMARY"]["size_mult"] = 0.9
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
        risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_risk_veto_blocks_before_wallet_authorization_path(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_notional_per_token"] = 1.0

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
            with mock.patch.object(manager.wallet, "authorize_intent", side_effect=AssertionError("wallet should not be called")):
                placed, reason = manager._place_order(
                    OrderIntent(token_id="t1", side="BUY", price=0.55, size=100.0, tif="GTC", post_only=True, reason="test"),
                    top,
                    open_orders_for_token=[],
                    open_orders_total=0,
                )
            self.assertIsNone(placed)
            self.assertTrue(str(reason or "").startswith("risk_reject"))
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_wallet_veto_blocks_even_when_risk_allows(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_notional_per_token"] = 1000.0

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
            with mock.patch.object(
                manager.wallet,
                "authorize_intent",
                return_value=WalletAuthorization(
                    allowed=False,
                    action="reject",
                    approved_size=0.0,
                    reason="wallet_test_veto",
                    detail="test",
                    halt=False,
                ),
            ):
                placed, reason = manager._place_order(
                    OrderIntent(token_id="t1", side="BUY", price=0.45, size=2.0, tif="GTC", post_only=True, reason="test"),
                    top,
                    open_orders_for_token=[],
                    open_orders_total=0,
                )
            self.assertIsNone(placed)
            self.assertEqual(reason, "wallet_reject")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_places_orders_and_processes_fills(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_submit_no_ack_missing_order_id_rolls_back_lock_and_is_idempotent(self):
        class _NoAckGateway(PaperGateway):
            def place_order(self, intent: OrderIntent, client_order_id: str):  # type: ignore[override]
                order = super().place_order(intent, client_order_id)
                order.order_id = ""
                order.status = "SUBMITTED"
                return order

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 2

            gateway = _NoAckGateway()
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
            order, reason = manager._place_order(intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNone(order)
            self.assertEqual(reason, "order_submit_no_ack")

            wallet_status = manager.wallet.status()
            self.assertEqual(float(wallet_status.get("pending_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertEqual(float(wallet_status.get("order_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertGreaterEqual(float(wallet_status.get("locked_usdc", 0.0) or 0.0), 0.0)
            self.assertGreaterEqual(float(telemetry.counters.get("order_submit_no_ack", 0)), 1.0)

            events.close()
            events = None
            cleanup_lock_id = ""
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "wallet_reservation_cleanup":
                        cleanup_lock_id = str(payload.get("lock_id") or "").strip()
            self.assertTrue(cleanup_lock_id)

            manager._cleanup_failed_submission(
                wallet_lock_id=cleanup_lock_id,
                submission_lane="maker",
                cleanup_reason="timeout_cleanup_retry",
                release_submission_reservation=True,
            )
            wallet_status_after_retry = manager.wallet.status()
            self.assertEqual(float(wallet_status_after_retry.get("pending_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertEqual(float(wallet_status_after_retry.get("order_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertGreaterEqual(float(wallet_status_after_retry.get("locked_usdc", 0.0) or 0.0), 0.0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_closed_immediately_ack_releases_pending_lock_without_order_lock(self):
        class _ClosedImmediatelyGateway(PaperGateway):
            def place_order(self, intent: OrderIntent, client_order_id: str):  # type: ignore[override]
                order = super().place_order(intent, client_order_id)
                order.status = "CLOSED"
                order.remaining_size = 0.0
                return order

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 1.0
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 2

            gateway = _ClosedImmediatelyGateway()
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
            order, reason = manager._place_order(intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(order)
            self.assertIsNone(reason)
            wallet_status = manager.wallet.status()
            self.assertEqual(float(wallet_status.get("pending_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertEqual(float(wallet_status.get("order_lock_usdc", 0.0) or 0.0), 0.0)
            self.assertGreaterEqual(float(wallet_status.get("locked_usdc", 0.0) or 0.0), 0.0)
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_risk_decision_basis_emitted_on_reject_and_accept(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_notional_per_token"] = 5.0
            risk_cfg["dynamic_scaling"]["enabled"] = True
            risk_cfg["dynamic_scaling"]["edge_enabled"] = True
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

            rejected_intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=20.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            placed, reject_reason = manager._place_order(rejected_intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNone(placed)
            self.assertTrue(str(reject_reason or "").startswith("risk_reject"))

            accepted_intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
            )
            placed_ok, accept_reason = manager._place_order(accepted_intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNotNone(placed_ok)
            self.assertIsNone(accept_reason)

            events.close()
            events = None
            risk_reject_rows: list[dict] = []
            order_submit_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "risk_reject":
                        risk_reject_rows.append(payload)
                    if str(payload.get("event_type") or "") == "order_submit":
                        order_submit_rows.append(payload)
            self.assertTrue(risk_reject_rows)
            self.assertTrue(order_submit_rows)
            reject_basis = risk_reject_rows[-1].get("risk_decision_basis")
            submit_basis = order_submit_rows[-1].get("risk_decision_basis")
            self.assertIsInstance(reject_basis, dict)
            self.assertIsInstance(submit_basis, dict)
            self.assertTrue(isinstance((reject_basis or {}).get("dynamic_scaling"), dict))
            self.assertTrue(isinstance((submit_basis or {}).get("dynamic_scaling"), dict))
            self.assertEqual(str(risk_reject_rows[-1].get("stage") or ""), "UNKNOWN")
            self.assertEqual(str(risk_reject_rows[-1].get("stage_source") or ""), "risk_decision_basis")
            self.assertIsNone(risk_reject_rows[-1].get("stage_unknown_reason"))
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_sizing_reject_emits_top_level_lane_stage_without_parser_fallback(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

            intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
                stage="SNIPER_PRIMARY",
            )
            with mock.patch.object(
                manager,
                "_resolve_order_size_shares_with_details",
                return_value=(None, {"forced": True}),
            ):
                placed, reject_reason = manager._place_order(intent, top, open_orders_for_token=[], open_orders_total=0)
            self.assertIsNone(placed)
            self.assertEqual(reject_reason, "sizing_reject")

            events.close()
            events = None
            risk_reject_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "risk_reject":
                        risk_reject_rows.append(payload)
            self.assertTrue(risk_reject_rows)
            row = next(
                (x for x in risk_reject_rows if str(x.get("reason") or "") == "size_notional_bounds"),
                risk_reject_rows[-1],
            )
            self.assertEqual(str(row.get("submission_lane") or ""), "maker")
            self.assertEqual(str(row.get("stage") or ""), "SNIPER_PRIMARY")
            self.assertEqual(str(row.get("stage_source") or ""), "intent")
            self.assertIsNone(row.get("stage_unknown_reason"))
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_sizing_reject_uses_risk_context_stage_when_intent_stage_missing(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

            intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.50,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
                stage=None,
            )
            with mock.patch.object(
                manager,
                "_resolve_order_size_shares_with_details",
                return_value=(None, {"forced": True}),
            ):
                placed, reject_reason = manager._place_order(
                    intent,
                    top,
                    open_orders_for_token=[],
                    open_orders_total=0,
                    risk_context={"stage": "SNIPER_PRIMARY", "submission_lane": "maker"},
                )
            self.assertIsNone(placed)
            self.assertEqual(reject_reason, "sizing_reject")

            events.close()
            events = None
            risk_reject_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "risk_reject":
                        risk_reject_rows.append(payload)
            self.assertTrue(risk_reject_rows)
            row = next(
                (x for x in risk_reject_rows if str(x.get("reason") or "") == "size_notional_bounds"),
                risk_reject_rows[-1],
            )
            self.assertEqual(str(row.get("submission_lane") or ""), "maker")
            self.assertEqual(str(row.get("stage") or ""), "SNIPER_PRIMARY")
            self.assertEqual(str(row.get("stage_source") or ""), "risk_context")
            self.assertIsNone(row.get("stage_unknown_reason"))
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_process_fills_releases_order_lock_when_filled_order_no_longer_open(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            fill = FillEvent(
                trade_id="fill-closed",
                order_id="order-closed",
                token_id="t1",
                side="BUY",
                price=0.4,
                size=1.0,
                ts_utc=utc_iso(),
            )
            with (
                mock.patch.object(manager.tx_manager, "poll_fills", return_value=[fill]),
                mock.patch.object(manager.tx_manager, "get_open_orders", return_value=[]),
                mock.patch.object(manager, "_handle_fill", return_value=True),
                mock.patch.object(manager.wallet, "release_order_lock") as release_mock,
            ):
                accepted = manager.process_fills()
            self.assertEqual(accepted, 1)
            release_mock.assert_called_once_with("order-closed")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_process_fills_keeps_order_lock_when_filled_order_still_open(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            fill = FillEvent(
                trade_id="fill-open",
                order_id="order-open",
                token_id="t1",
                side="BUY",
                price=0.4,
                size=1.0,
                ts_utc=utc_iso(),
            )
            open_order = LiveOrder(
                order_id="order-open",
                token_id="t1",
                side="BUY",
                price=0.4,
                size=5.0,
                remaining_size=4.0,
                status="OPEN",
            )
            with (
                mock.patch.object(manager.tx_manager, "poll_fills", return_value=[fill]),
                mock.patch.object(manager.tx_manager, "get_open_orders", return_value=[open_order]),
                mock.patch.object(manager, "_handle_fill", return_value=True),
                mock.patch.object(manager.wallet, "release_order_lock") as release_mock,
            ):
                accepted = manager.process_fills()
            self.assertEqual(accepted, 1)
            release_mock.assert_not_called()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_taker_order_submit_carries_stage(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            outcome = manager.place_taker_order_with_outcome(
                token_id="t1",
                side="BUY",
                price=0.51,
                size=10.0,
                target_usd=None,
                top=top,
                reason="sniper_taker_chainlink",
                stage="SNIPER_PRIMARY",
                competitiveness_context={"stage": "SNIPER_PRIMARY"},
            )
            self.assertTrue(bool(outcome.get("submitted", False)))
            events.close()
            events = None

            stage_values: list[str] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if str(row.get("event_type") or "") != "order_submit":
                        continue
                    if str(row.get("reason") or "").strip().lower() != "sniper_taker_chainlink":
                        continue
                    stage_values.append(str(row.get("stage") or ""))

            self.assertEqual(stage_values, ["SNIPER_PRIMARY"])
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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
            risk_cfg = self._risk_cfg_without_expiry_gate()
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

    def test_runner_valuation_state_uses_freshness_bounded_last_known_mids(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 3.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 3.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=5.0)
                now = time.monotonic()

                runner.last_midpoint_by_token["t1"] = 0.42
                runner.last_midpoint_ts_mono_by_token["t1"] = now - 1.0
                fresh_state = runner._build_valuation_state(books={})
                self.assertEqual(str((fresh_state.get("source_by_token") or {}).get("t1") or ""), "fresh_last_known_mid")
                self.assertAlmostEqual(float((fresh_state.get("mid_by_token") or {}).get("t1") or 0.0), 0.42, places=9)
                self.assertFalse(bool(fresh_state.get("valuation_hard_degraded", False)))

                runner.last_midpoint_ts_mono_by_token["t1"] = now - 10.0
                stale_state = runner._build_valuation_state(books={})
                self.assertEqual(
                    str((stale_state.get("source_by_token") or {}).get("t1") or ""),
                    "conservative_bound_hard_degraded",
                )
                self.assertAlmostEqual(float((stale_state.get("mid_by_token") or {}).get("t1", -1.0)), 0.0, places=9)
                self.assertTrue(bool(stale_state.get("valuation_hard_degraded", False)))
                reasons = list(stale_state.get("degraded_reasons") or [])
                self.assertTrue(any(str(r).startswith("hard_degraded:t1:") for r in reasons))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_uses_one_sided_conservative_quote_for_non_flat_positions(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 6.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 2.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=3.0)
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.41,
                    best_bid_size=120.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                state = runner._build_valuation_state(books={"t1": top})
                self.assertEqual(
                    str((state.get("source_by_token") or {}).get("t1") or ""),
                    "fresh_live_side_conservative_quote",
                )
                self.assertAlmostEqual(float((state.get("mid_by_token") or {}).get("t1", 0.0)), 0.41, places=9)
                self.assertFalse(bool(state.get("valuation_hard_degraded", False)))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_rejects_malformed_one_sided_quotes(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 6.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 2.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=4.0)
                malformed = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=1.2,
                    best_bid_size=10.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                state = runner._build_valuation_state(books={"t1": malformed})
                self.assertEqual(
                    str((state.get("source_by_token") or {}).get("t1") or ""),
                    "conservative_bound_hard_degraded",
                )
                reasons = list(state.get("degraded_reasons") or [])
                self.assertTrue(any("quote_sanity:invalid_bid_quote" in str(r) for r in reasons))
                self.assertTrue(bool(state.get("valuation_hard_degraded", False)))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_reason_marks_missing_required_side_without_stale_age(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 6.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 2.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=3.0)
                ask_only_top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=None,
                    best_bid_size=None,
                    best_ask_price=0.63,
                    best_ask_size=10.0,
                )
                state = runner._build_valuation_state(books={"t1": ask_only_top})
                self.assertEqual(
                    str((state.get("source_by_token") or {}).get("t1") or ""),
                    "conservative_bound_hard_degraded",
                )
                reasons = [str(x) for x in list(state.get("degraded_reasons") or [])]
                self.assertTrue(any("live_mid_missing" in reason for reason in reasons))
                self.assertTrue(any("required_conservative_side_missing:bid" in reason for reason in reasons))
                self.assertFalse(any("quote_age_stale_for_live_mid" in reason for reason in reasons))
                self.assertFalse(any("quote_age_stale_for_side_conservative" in reason for reason in reasons))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_exposes_and_clears_held_unpriceable_surfaces(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 6.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 2.0
            cfg["risk"]["held_unpriceable_escalation_sec"] = 0.5
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=3.0)
                with mock.patch("executor.time.monotonic", side_effect=[100.0, 100.8, 101.0]):
                    hard_state = runner._build_valuation_state(books={})
                    escalated_state = runner._build_valuation_state(books={})
                self.assertEqual(
                    str((hard_state.get("source_by_token") or {}).get("t1") or ""),
                    "conservative_bound_hard_degraded",
                )
                self.assertEqual(list(hard_state.get("held_unpriceable_token_ids") or []), ["t1"])
                self.assertEqual(int(hard_state.get("held_unpriceable_count") or 0), 1)
                self.assertGreaterEqual(float(hard_state.get("held_unpriceable_max_age_sec", -1.0)), 0.0)
                self.assertIn("t1", dict(hard_state.get("held_unpriceable_age_by_token") or {}))
                self.assertFalse(bool(hard_state.get("held_unpriceable_escalation_active", False)))

                self.assertTrue(bool(escalated_state.get("held_unpriceable_escalation_active", False)))
                self.assertEqual(list(escalated_state.get("held_unpriceable_escalation_token_ids") or []), ["t1"])
                self.assertEqual(int(escalated_state.get("held_unpriceable_escalation_count") or 0), 1)
                self.assertTrue(bool(escalated_state.get("held_unpriceable_defect_candidate", False)))
                self.assertAlmostEqual(
                    float(escalated_state.get("held_unpriceable_escalation_max_age_sec") or 0.0),
                    0.8,
                    places=6,
                )
                self.assertEqual(
                    str(escalated_state.get("held_unpriceable_operator_action") or ""),
                    "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable",
                )
                escalation_reasons = [str(x) for x in list(escalated_state.get("held_unpriceable_escalation_reasons") or [])]
                self.assertTrue(any("persistent_held_unpriceable:t1:" in reason for reason in escalation_reasons))

                priceable_top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.44,
                    best_bid_size=50.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                recovered_state = runner._build_valuation_state(books={"t1": priceable_top})
                self.assertEqual(
                    str((recovered_state.get("source_by_token") or {}).get("t1") or ""),
                    "fresh_live_side_conservative_quote",
                )
                self.assertEqual(int(recovered_state.get("held_unpriceable_count") or 0), 0)
                self.assertEqual(list(recovered_state.get("held_unpriceable_token_ids") or []), [])
                self.assertFalse(bool(recovered_state.get("held_unpriceable_escalation_active", False)))
                self.assertEqual(list(recovered_state.get("held_unpriceable_escalation_token_ids") or []), [])
                self.assertEqual(str(recovered_state.get("held_unpriceable_operator_action") or ""), "none")
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_rest_fetch_result_for_token_skips_unrequested_token(self):
        fetched, err_text, attempted = ExecutionRunner._rest_fetch_result_for_token(
            token_id="t1",
            requested_rest_token_ids={"t2"},
            rest_books={"t1": (None, None, 1.0)},
            rest_errors={"t1": "boom"},
        )
        self.assertFalse(attempted)
        self.assertIsNone(fetched)
        self.assertIsNone(err_text)

        fetched2, err_text2, attempted2 = ExecutionRunner._rest_fetch_result_for_token(
            token_id="t2",
            requested_rest_token_ids={"t2"},
            rest_books={},
            rest_errors={"t2": "upstream_timeout"},
        )
        self.assertTrue(attempted2)
        self.assertIsNone(fetched2)
        self.assertEqual(err_text2, "upstream_timeout")

    def test_book_not_found_backoff_prefers_held_exposure_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["book_not_found_backoff_sec"] = 90.0
            cfg["runtime"]["held_book_not_found_backoff_sec"] = 10.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=1.0)
                held_tokens = runner._held_exposure_token_ids()  # pylint: disable=protected-access
                held_backoff = runner._book_not_found_backoff_sec_for_token(  # pylint: disable=protected-access
                    token_id=held,
                    held_exposure_tokens=held_tokens,
                )
                normal_backoff = runner._book_not_found_backoff_sec_for_token(  # pylint: disable=protected-access
                    token_id="other-token",
                    held_exposure_tokens=held_tokens,
                )
                self.assertAlmostEqual(float(held_backoff), 10.0, places=6)
                self.assertAlmostEqual(float(normal_backoff), 90.0, places=6)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_held_only_book_not_found_suppresses_forced_target_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=1.0)
                with mock.patch.object(runner, "_refresh_targets") as refresh_mock:
                    outcome = runner._handle_missing_book_not_found_tokens(  # pylint: disable=protected-access
                        missing_book_not_found_tokens=[held],
                        held_exposure_tokens={held},
                    )
                refresh_mock.assert_not_called()
                self.assertEqual(
                    outcome,
                    {
                        "forced_refresh_tokens": [],
                        "suppressed_held_tokens": [held],
                    },
                )
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_book_not_found", 0)),
                    1,
                )
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_book_not_found", 0)), 0)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_held_only_book_not_found_can_force_recovery_refresh_when_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["held_book_not_found_force_refresh_interval_sec"] = 60.0
            cfg["runtime"]["held_book_not_found_force_refresh_min_unpriceable_age_sec"] = 10.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=1.0)
                runner._held_unpriceable_since_mono_by_token[held] = time.monotonic() - 45.0  # pylint: disable=protected-access
                with mock.patch.object(runner, "_refresh_targets") as refresh_mock:
                    outcome = runner._handle_missing_book_not_found_tokens(  # pylint: disable=protected-access
                        missing_book_not_found_tokens=[held],
                        held_exposure_tokens={held},
                    )
                refresh_mock.assert_called_once_with(force=True)
                self.assertEqual(
                    outcome,
                    {
                        "forced_refresh_tokens": [held],
                        "suppressed_held_tokens": [],
                    },
                )
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_book_not_found", 0)), 1)
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_forced_held_book_not_found_recovery", 0)),
                    1,
                )
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_book_not_found", 0)),
                    0,
                )
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_mixed_book_not_found_refreshes_only_non_held_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                other = "discovery-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=1.0)
                with mock.patch.object(runner, "_refresh_targets") as refresh_mock:
                    outcome = runner._handle_missing_book_not_found_tokens(  # pylint: disable=protected-access
                        missing_book_not_found_tokens=[held, other],
                        held_exposure_tokens={held},
                    )
                refresh_mock.assert_called_once_with(force=True)
                self.assertEqual(
                    outcome,
                    {
                        "forced_refresh_tokens": [other],
                        "suppressed_held_tokens": [held],
                    },
                )
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_book_not_found", 0)), 1)
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_book_not_found", 0)),
                    0,
                )
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_tags_book_not_found_404_on_hard_degraded_reason(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                token_id = "t1"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=10.0)
                runner.last_midpoint_ts_mono_by_token[token_id] = time.monotonic() - 60.0
                runner.last_midpoint_by_token[token_id] = 0.51
                runner._held_book_not_found_last_mono_by_token[token_id] = time.monotonic() - 8.0  # pylint: disable=protected-access

                state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                reasons = [str(x) for x in list(state.get("degraded_reasons") or [])]
                self.assertTrue(any("hard_degraded:t1:" in reason for reason in reasons))
                self.assertTrue(any("held_book_not_found_404_age_sec=" in reason for reason in reasons))
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_watch_tokens_persist_for_non_flat_and_open_orders(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=2.0)
                watched = set(runner._valuation_watch_token_ids())
                self.assertIn(held, watched)

                runner.risk.positions[held] = Position(token_id=held, net_shares=0.0)
                runner.gateway._open_orders["o1"] = LiveOrder(  # pylint: disable=protected-access
                    order_id="o1",
                    token_id=held,
                    side="BUY",
                    price=0.5,
                    size=10.0,
                    remaining_size=10.0,
                    status="OPEN",
                )
                watched_with_open_order = set(runner._valuation_watch_token_ids())
                self.assertIn(held, watched_with_open_order)

                runner.gateway._open_orders.pop("o1", None)  # pylint: disable=protected-access
                watched_after_cleanup = set(runner._valuation_watch_token_ids())
                self.assertNotIn(held, watched_after_cleanup)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_prune_removed_tokens_preserves_watch_state_for_non_flat_positions(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-token"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=2.0)
                runner.last_midpoint_by_token[token_id] = 0.44
                runner.last_midpoint_ts_mono_by_token[token_id] = time.monotonic()
                runner._book_not_found_backoff_mono_by_token[token_id] = time.monotonic() + 30.0  # pylint: disable=protected-access

                runner._prune_removed_tokens(old_set={token_id}, active_set=set())

                self.assertIn(token_id, runner.last_midpoint_by_token)
                self.assertIn(token_id, runner.last_midpoint_ts_mono_by_token)
                self.assertIn(token_id, runner._book_not_found_backoff_mono_by_token)  # pylint: disable=protected-access
                self.assertIn(token_id, runner.risk.positions)
            finally:
                runner.events.close()
                runner.book_client.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_prune_removed_tokens_clears_watch_state_when_flat_and_no_open_orders(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                token_id = "flat-token"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=0.0)
                runner.last_midpoint_by_token[token_id] = 0.51
                runner.last_midpoint_ts_mono_by_token[token_id] = time.monotonic()
                runner._book_not_found_backoff_mono_by_token[token_id] = time.monotonic() + 30.0  # pylint: disable=protected-access

                runner._prune_removed_tokens(old_set={token_id}, active_set=set())

                self.assertNotIn(token_id, runner.last_midpoint_by_token)
                self.assertNotIn(token_id, runner.last_midpoint_ts_mono_by_token)
                self.assertNotIn(token_id, runner._book_not_found_backoff_mono_by_token)  # pylint: disable=protected-access
                self.assertNotIn(token_id, runner.risk.positions)
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
            cfg["risk"]["min_sec_to_expiry_for_new_exposure"] = 0.0
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
