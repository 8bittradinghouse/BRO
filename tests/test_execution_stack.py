import copy
import datetime as dt
import json
import tempfile
import time
import unittest
import warnings
from unittest import mock
from pathlib import Path

import yaml

from executor import ExecutionRunner
from prodesk.chainlink_feed import ChainlinkTick
from prodesk.common import utc_iso
from prodesk.config import (
    DEFAULT_EXECUTION_CONFIG,
    extract_config_compatibility_metadata,
    load_execution_config,
    validate_execution_config,
)
from prodesk.edge_truth_contract import EVENT_TAKER_DECISION, TAKER_CHAINLINK_REASON
from prodesk.gateway import PaperGateway, PostOnlyRejectError
from prodesk.latency_verifier import LatencySnapshot
from prodesk.logging_utils import EventLogger
from prodesk.models import BookTop, FillEvent, LiveOrder, OrderIntent, Position
from prodesk.operating_mode import MODE_CAUTIOUS
from prodesk.order_manager import OrderManager
from prodesk.risk import RiskEngine
from prodesk.strategy import MarketMakingStrategy
from prodesk.telemetry import Telemetry
from prodesk.wallet.wallet_truth_policy import PROVIDER_AMBIGUITY_REL_TOLERANCE_DEFAULT
from prodesk.wallet_doctrine import WalletAuthorization


class ExecutionStackTests(unittest.TestCase):
    _HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT = "preexpiry_emergency_taker_unwind"
    _HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED = "maker_to_taker_recovery_handoff_disabled"
    _HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_SEC_FIELD = "preexpiry_emergency_taker_window_sec"
    _HISTORICAL_RECOVERY_MIN_FILL_PROB_FIELD = (
        "reduce_only_recovery_min_expected_fill_prob_floor"
    )
    _HISTORICAL_RECOVERY_MAX_QUEUE_FIELD = (
        "reduce_only_recovery_max_queue_ahead_size_multiplier"
    )
    _HISTORICAL_RECOVERY_RATE_RESERVED_SLOTS_FIELD = "order_rate_recovery_reserved_slots"
    _HISTORICAL_ORDER_SOFT_THROTTLE_BYPASS_RECOVERY_COUNTER = (
        "order_soft_throttle_bypass_reduce_only_recovery"
    )

    @staticmethod
    def _risk_cfg_without_expiry_gate() -> dict:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["risk"])
        cfg["min_sec_to_expiry_for_new_exposure"] = 0.0
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {}
        return cfg

    @staticmethod
    def _read_event_rows(log_dir: Path, *, event_type: str) -> list[dict]:
        rows: list[dict] = []
        for path in sorted(Path(log_dir).glob("events_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("event_type") or "") == event_type:
                    rows.append(payload)
        return rows

    @staticmethod
    def _historical_recovery_lineage_stage_info(
        *,
        stage: str = "MAKER_TAKER_SELECTIVE",
        sec_to_expiry: float = 45.0,
        taker_gate_open: bool = True,
        reduce_only_side: str = "SELL",
        reduce_only_size_cap_shares: float = 2.0,
        expired_reduce_only_grace_active: bool = True,
        **extra: object,
    ) -> dict:
        info: dict[str, object] = {
            "stage": stage,
            "sec_to_expiry": sec_to_expiry,
            "maker_phase_allowed": False,
            "taker_phase_allowed": taker_gate_open,
            "maker_gate_open": False,
            "taker_gate_open": taker_gate_open,
            # Historical-only lineage hints kept in one place so they do not sprawl
            # across current execution-stack semantics.
            "reduce_only_recovery_active": True,
            "reduce_only_recovery_reason": "preexpiry_reduce_only_window_active",
            "reduce_only_side": reduce_only_side,
            "reduce_only_size_cap_shares": reduce_only_size_cap_shares,
            "expired_reduce_only_grace_active": expired_reduce_only_grace_active,
        }
        info.update(extra)
        return info

    @staticmethod
    def _selection_gate_runtime_strategy_sizing_cfg(
        *,
        min_sec_to_expiry: float | None = None,
        max_sec_to_expiry: float | None = None,
        min_depth_multiple: float = 1.5,
        max_same_target_submit_count_prior: int = 999,
        max_same_target_side_submit_count_prior: int = 1,
    ) -> tuple[dict, dict, dict]:
        runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
        strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
        sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
        strategy_cfg["execution_quality"]["enabled"] = False
        strategy_cfg["maker_competitiveness"]["selection_gate"]["enabled"] = True
        strategy_cfg["maker_competitiveness"]["selection_gate"][
            "require_secondary_oracle_confirmation"
        ] = True
        strategy_cfg["maker_competitiveness"]["selection_gate"][
            "cannon_target_notional_usd"
        ] = 20.0
        strategy_cfg["maker_competitiveness"]["selection_gate"]["min_depth_multiple"] = float(
            min_depth_multiple
        )
        strategy_cfg["maker_competitiveness"]["selection_gate"][
            "max_same_target_submit_count_prior"
        ] = int(max_same_target_submit_count_prior)
        strategy_cfg["maker_competitiveness"]["selection_gate"][
            "max_same_target_side_submit_count_prior"
        ] = int(max_same_target_side_submit_count_prior)
        strategy_cfg["maker_competitiveness"]["selection_gate"]["min_sec_to_expiry"] = (
            float(min_sec_to_expiry)
            if isinstance(min_sec_to_expiry, (int, float))
            else None
        )
        strategy_cfg["maker_competitiveness"]["selection_gate"]["max_sec_to_expiry"] = (
            float(max_sec_to_expiry)
            if isinstance(max_sec_to_expiry, (int, float))
            else None
        )
        sizing_cfg["mode"] = "shares"
        sizing_cfg["maker_competitive_min_notional_usd"] = 0.0
        sizing_cfg["maker_competitive_max_notional_usd"] = 0.0
        sizing_cfg["maker_competitive_max_shares"] = 0.0
        return runtime_cfg, strategy_cfg, sizing_cfg

    def _run_loaded_config_maker_snapshot(self, cfg_path: Path) -> dict:
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            cfg = load_execution_config(cfg_path)
            runtime_cfg = copy.deepcopy(cfg["runtime"])
            strategy_cfg = copy.deepcopy(cfg["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = copy.deepcopy(cfg["risk"])
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            fixed_shadow_ts = dt.datetime.now(dt.timezone.utc)
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                now_fn=lambda: fixed_shadow_ts,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(fixed_shadow_ts),
                source="test",
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 18.5,
                    "secondary_fair_probability": 0.58,
                    "secondary_edge_value": 0.09,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "chainlink_spot_price": 100125.0,
                    "secondary_oracle_spot_price": 100118.0,
                    "secondary_oracle_price_delta_abs": 7.0,
                    "secondary_oracle_price_delta_bps": 0.699125,
                }
            }
            first = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=7)
            second = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=8)
            open_orders = gateway.get_open_orders()
            events.close()
            events = None
            queue_pressure_rows = self._read_event_rows(
                Path(tmp.name),
                event_type="maker_queue_pressure_adjustment",
            )
            return {
                "first_summary": first,
                "second_summary": second,
                "open_orders": [
                    {
                        "token_id": str(order.token_id),
                        "side": str(order.side),
                        "price": float(order.price),
                        "remaining_size": float(order.remaining_size),
                    }
                    for order in open_orders
                ],
                "queue_pressure_rows": queue_pressure_rows,
            }
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

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

    def test_paper_universal_profile_wires_held_ws_missing_recovery_thresholds(self):
        cfg_path = (Path(__file__).resolve().parents[1] / "configs/profiles/paper_universal.yaml").resolve()
        cfg = load_execution_config(cfg_path)
        runtime = dict(cfg.get("runtime") or {})
        risk = dict(cfg.get("risk") or {})
        strategy = dict(cfg.get("strategy") or {})
        maker_comp = dict(strategy.get("maker_competitiveness") or {})
        selection_gate = dict(maker_comp.get("selection_gate") or {})
        self.assertAlmostEqual(
            float(runtime.get("held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec") or 0.0),
            20.0,
            places=9,
        )
        self.assertAlmostEqual(
            float(runtime.get("held_ws_missing_or_unusable_refresh_interval_sec") or 0.0),
            45.0,
            places=9,
        )
        self.assertNotIn("held_preexpiry_reduce_only_sec", runtime)
        self.assertNotIn(self._HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_SEC_FIELD, runtime)
        self.assertNotIn("terminal_unwind_halt_new_risk_sec", runtime)
        self.assertTrue(bool(runtime.get("require_lifecycle_context_for_decisions", False)))
        self.assertAlmostEqual(float(risk.get("min_sec_to_expiry_for_new_exposure") or 0.0), 15.0, places=9)
        lane_overrides = dict(risk.get("min_sec_to_expiry_for_new_exposure_by_lane") or {})
        self.assertAlmostEqual(float(lane_overrides.get("maker")), 7.0, places=9)
        self.assertAlmostEqual(float(lane_overrides.get("taker")), 0.0, places=9)
        taker = dict(cfg.get("taker") or {})
        competitiveness = dict(taker.get("competitiveness") or {})
        self.assertAlmostEqual(float(competitiveness.get("final_window_sec") or 0.0), 7.0, places=9)
        self.assertNotIn("stage_final_window_sec_by_stage", competitiveness)
        execution_quality = dict(strategy.get("execution_quality") or {})
        sizing = dict(cfg.get("sizing") or {})
        wallet = dict(cfg.get("wallet") or {})
        self.assertNotIn(self._HISTORICAL_RECOVERY_MIN_FILL_PROB_FIELD, execution_quality)
        self.assertNotIn(self._HISTORICAL_RECOVERY_MAX_QUEUE_FIELD, execution_quality)
        self.assertNotIn("timing_gate_min_sec_to_expiry", maker_comp)
        self.assertNotIn("timing_gate_max_sec_to_expiry", maker_comp)
        lifecycle = dict(cfg.get("lifecycle") or {})
        selection = dict(lifecycle.get("selection") or {})
        phase = dict(lifecycle.get("phase") or {})
        self.assertAlmostEqual(float(phase.get("maker_window_open_sec") or 0.0), 15.0, places=9)
        self.assertAlmostEqual(float(phase.get("taker_window_open_sec") or 0.0), 7.0, places=9)
        self.assertEqual(bool(selection.get("enabled")), True)
        self.assertNotIn("allowed_stages", selection_gate)
        self.assertEqual(bool(selection.get("require_secondary_oracle_confirmation")), True)
        self.assertNotIn("require_one_sided_active", selection)
        self.assertAlmostEqual(float(selection.get("cannon_target_notional_usd") or 0.0), 100.0, places=9)
        self.assertAlmostEqual(float(selection.get("maker_min_depth_multiple") or 0.0), 1.5, places=9)
        self.assertEqual(
            int(float(selection.get("max_same_target_submit_count_prior"))),
            1,
        )
        self.assertEqual(
            int(float(selection.get("max_same_target_side_submit_count_prior"))),
            1,
        )
        self.assertAlmostEqual(float(sizing.get("target_usd") or 0.0), 100.0, places=9)
        self.assertAlmostEqual(float(sizing.get("max_usd") or 0.0), 101.0, places=9)
        self.assertAlmostEqual(
            float(sizing.get("maker_competitive_min_notional_usd") or 0.0),
            100.0,
            places=9,
        )

        runtime_cfg_for_manager = dict(cfg.get("runtime") or {})
        runtime_cfg_for_manager["lifecycle"] = dict(cfg.get("lifecycle") or {})
        gateway = PaperGateway()
        tmp = tempfile.TemporaryDirectory()
        events = EventLogger(Path(tmp.name))
        telemetry = Telemetry()
        try:
            risk_engine = RiskEngine(self._risk_cfg_without_expiry_gate(), {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                object(),
                risk_engine,
                events,
                telemetry,
                runtime_cfg_for_manager,
                cfg["strategy"],
                sizing_cfg=cfg.get("sizing", {}),
            )
            self.assertAlmostEqual(float(manager.maker_selection_gate_min_sec_to_expiry or 0.0), 7.0, places=9)
            self.assertAlmostEqual(float(manager.maker_selection_gate_max_sec_to_expiry or 0.0), 15.0, places=9)
        finally:
            events.close()
            tmp.cleanup()

    def test_order_manager_lifecycle_selection_gate_uses_phase_window_not_market_admission_floor(self):
        runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
        runtime_cfg["lifecycle"] = {
            "selection": {
                "enabled": True,
                "min_sec_to_expiry": 90.0,
                "maker_min_depth_multiple": 1.5,
                "require_secondary_oracle_confirmation": True,
                "cannon_target_notional_usd": 20.0,
                "max_same_target_submit_count_prior": 999,
                "max_same_target_side_submit_count_prior": 1,
            },
            "phase": {
                "maker_window_open_sec": 15.0,
                "taker_window_open_sec": 7.0,
            },
        }
        strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
        strategy_cfg["execution_quality"]["enabled"] = False
        sizing_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["sizing"])
        sizing_cfg["mode"] = "shares"
        sizing_cfg["maker_competitive_min_notional_usd"] = 0.0
        sizing_cfg["maker_competitive_max_notional_usd"] = 0.0
        sizing_cfg["maker_competitive_max_shares"] = 0.0
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(self._risk_cfg_without_expiry_gate(), {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                object(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            self.assertAlmostEqual(float(manager.maker_selection_gate_min_sec_to_expiry or 0.0), 7.0, places=9)
            self.assertAlmostEqual(float(manager.maker_selection_gate_max_sec_to_expiry or 0.0), 15.0, places=9)
            verdict = manager._evaluate_maker_selection_gate(  # pylint: disable=protected-access
                shadow_event={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "desired_quote_price": 20.0,
                    "visible_depth_shares": 2.0,
                    "same_target_side_submit_count_prior": 0,
                    "secondary_oracle_confirmation": True,
                    "one_sided_active": True,
                    "sec_to_expiry": 12.0,
                    "maker_phase_allowed": True,
                    "lifecycle_phase": "maker_window",
                },
                competitiveness_context={"stage": "MAKER_TAKER_SELECTIVE", "lifecycle_phase": "maker_window"},
            )
            self.assertEqual(bool(verdict.get("applied")), True)
            self.assertEqual(bool(verdict.get("timing_window_met")), True)
            self.assertNotEqual(str(verdict.get("primary_reject_reason") or ""), "timing_window_out_of_band")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_config_rejects_unknown_min_sec_to_expiry_lane_override(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["risk"]["min_sec_to_expiry_for_new_exposure_by_lane"] = {"legacy": 0.0}
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_load_config_rejects_retired_lifecycle_selection_one_sided_requirement(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["lifecycle"]["selection"]["require_one_sided_active"] = True
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
            yaml.safe_dump(cfg, tmp, sort_keys=False)
            path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                load_execution_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_load_config_rejects_retired_strategy_selection_gate_one_sided_requirement(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["strategy"]["maker_competitiveness"]["selection_gate"]["require_one_sided_active"] = True
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
            yaml.safe_dump(cfg, tmp, sort_keys=False)
            path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                load_execution_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_config_rejects_selection_gate_timing_min_above_max(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["strategy"]["maker_competitiveness"]["selection_gate"]["min_sec_to_expiry"] = 15.0
        cfg["strategy"]["maker_competitiveness"]["selection_gate"]["max_sec_to_expiry"] = 10.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

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

    def test_config_rejects_new_exposure_expiry_gate_above_maker_timing_max(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["risk"]["min_sec_to_expiry_for_new_exposure"] = 90.0
        cfg["strategy"]["maker_competitiveness"]["timing_gate_max_sec_to_expiry"] = 60.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_ignores_removed_preexpiry_recovery_window(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["runtime"]["held_preexpiry_reduce_only_sec"] = 30.0
        cfg["targets"]["token_ids"] = ["tok1"]
        validate_execution_config(cfg)

    def test_config_ignores_removed_terminal_unwind_halt_window(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["held_preexpiry_reduce_only_sec"] = 45.0
        cfg["runtime"]["terminal_unwind_halt_new_risk_sec"] = 60.0
        validate_execution_config(cfg)

    def test_config_ignores_removed_preexpiry_emergency_taker_window(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["held_preexpiry_reduce_only_sec"] = 45.0
        cfg["runtime"][self._HISTORICAL_PREEXPIRY_EMERGENCY_WINDOW_SEC_FIELD] = 60.0
        validate_execution_config(cfg)

    def test_config_ignores_removed_terminal_min_notional_floor(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["risk"]["reduce_only_terminal_min_notional_usd"] = 8.0
        cfg["risk"]["min_order_size"] = 5.0
        cfg["strategy"]["min_order_size"] = 5.0
        validate_execution_config(cfg)

    def test_config_rejects_non_boolean_require_lifecycle_context_for_decisions(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["require_lifecycle_context_for_decisions"] = "yes"
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_financial_posture_ignores_removed_preexpiry_recovery_hints(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.risk = type("RiskStub", (), {"kill_switch": False})()
        runner._valuation_hard_degraded = False
        runner.risk_min_order_size_shares = 1.0

        posture = runner._resolve_financial_posture_class(
            stage_info_by_token={
                "tok1": {
                    "reduce_only_recovery_active": True,
                    "sec_to_expiry": 5.0,
                    "reduce_only_net_shares": 10.0,
                    "reduce_only_open_order_present": False,
                }
            }
        )
        self.assertEqual(posture, "NORMAL")

        runner._valuation_hard_degraded = True
        hard_degraded = runner._resolve_financial_posture_class(
            stage_info_by_token={
                "tok1": {
                    "reduce_only_recovery_active": True,
                    "sec_to_expiry": 12.0,
                    "reduce_only_net_shares": 10.0,
                    "reduce_only_open_order_present": False,
                }
            }
        )
        self.assertEqual(hard_degraded, "HARD_DEGRADED_REDUCE_ONLY")

    def test_build_submission_lifecycle_context_surfaces_settlement_hold_without_mismatch(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.risk = type("RiskStub", (), {"kill_switch": False})()
        runner._valuation_hard_degraded = False
        runner._financial_posture_class = "NORMAL"
        runner.risk_min_order_size_shares = 1.0
        runner.require_lifecycle_context_for_decisions = True
        runner._lifecycle_context_mismatch_count = 0
        runner._lifecycle_context_missing_sec_to_expiry_count = 0
        runner.run_id = "unit-test"
        runner.telemetry = type("TelemetryStub", (), {"incr": lambda *args, **kwargs: None})()
        runner.events = type("EventStub", (), {"log_event": lambda *args, **kwargs: None})()

        context = runner._build_submission_lifecycle_context(
            token_id="tok1",
            info={
                "stage": "MAKER_TAKER_SELECTIVE",
                "sec_to_expiry": 5.0,
                "settlement_hold_required": True,
                "open_order_cleanup_required": False,
                "unresolved_lifecycle_obligation": False,
                "cancel_fail_closed": False,
                "held_net_shares": 3.0,
            },
            submission_lane="maker",
            stage="MAKER_TAKER_SELECTIVE",
        )
        self.assertEqual(str(context.get("lifecycle_phase") or ""), "prepare")
        self.assertTrue(bool(context.get("market_truth_required")))
        self.assertFalse(bool(context.get("maker_phase_allowed")))
        self.assertFalse(bool(context.get("taker_phase_allowed")))
        self.assertEqual(str(context.get("financial_posture_class") or ""), "NORMAL")
        self.assertTrue(bool(context.get("settlement_hold_required")))
        self.assertFalse(bool(context.get("open_order_cleanup_required")))
        self.assertFalse(bool(context.get("lifecycle_context_mismatch")))
        self.assertTrue(bool(context.get("lifecycle_context_present")))
        self.assertNotIn("stage", context)
        self.assertNotIn("effective_stage", context)
        self.assertNotIn("stage_bucket", context)
        self.assertNotIn("raw_stage", context)
        self.assertNotIn("maker_new_risk_allowed", context)
        self.assertNotIn("normal_taker_allowed", context)
        self.assertNotIn("late_window_authority_class", context)
        self.assertEqual(int(runner._lifecycle_context_mismatch_count), 0)

    def test_lifecycle_context_missing_event_emits_lifecycle_truth_without_stage_family_fields(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.risk = type("RiskStub", (), {"kill_switch": False})()
        runner._valuation_hard_degraded = False
        runner._financial_posture_class = "NORMAL"
        runner.risk_min_order_size_shares = 1.0
        runner.require_lifecycle_context_for_decisions = True
        runner._lifecycle_context_mismatch_count = 0
        runner._lifecycle_context_missing_sec_to_expiry_count = 0
        runner.run_id = "unit-test"
        runner.telemetry = type("TelemetryStub", (), {"incr": lambda *args, **kwargs: None})()
        captured: list[tuple[str, dict]] = []
        runner.events = type(
            "EventStub",
            (),
            {"log_event": lambda self, event_type, payload: captured.append((str(event_type), dict(payload)))},
        )()

        runner._build_submission_lifecycle_context(
            token_id="tok1",
            info={
                "stage": "MAKER_LATE_WINDOW",
                "lifecycle_phase": "maker_window",
                "settlement_hold_required": False,
                "open_order_cleanup_required": False,
                "unresolved_lifecycle_obligation": False,
                "cancel_fail_closed": False,
            },
            submission_lane="maker",
            stage="MAKER_LATE_WINDOW",
        )

        missing_rows = [payload for event_type, payload in captured if event_type == "lifecycle_context_missing"]
        self.assertTrue(bool(missing_rows))
        row = missing_rows[-1]
        self.assertEqual(str(row.get("lifecycle_phase") or ""), "maker_window")
        self.assertEqual(str(row.get("detail") or ""), "missing_sec_to_expiry")
        self.assertNotIn("stage", row)
        self.assertNotIn("effective_stage", row)
        self.assertNotIn("stage_bucket", row)
        self.assertNotIn("raw_stage", row)

    def test_load_execution_config_ignores_removed_recovery_relaxation_knobs(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "cfg.yaml"
            cfg_path.write_text(
                yaml.safe_dump(
                    {
                        "mode": "live",
                        "runtime": {
                            "paper_enforce_setup_lock": False,
                        },
                        "targets": {
                            "token_ids": ["tok1"],
                        },
                        "wallet": {
                            "require_allowance": False,
                        },
                        "strategy": {
                            "execution_quality": {
                                "min_expected_fill_prob": 0.05,
                                self._HISTORICAL_RECOVERY_MIN_FILL_PROB_FIELD: 0.10,
                                self._HISTORICAL_RECOVERY_MAX_QUEUE_FIELD: 6.0,
                            }
                        }
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                loaded = load_execution_config(cfg_path)
            execution_quality = dict((loaded.get("strategy") or {}).get("execution_quality") or {})
            self.assertNotIn(self._HISTORICAL_RECOVERY_MIN_FILL_PROB_FIELD, execution_quality)
            self.assertNotIn(self._HISTORICAL_RECOVERY_MAX_QUEUE_FIELD, execution_quality)
            compatibility = extract_config_compatibility_metadata(loaded)
            self.assertIn(
                f"strategy.execution_quality.{self._HISTORICAL_RECOVERY_MIN_FILL_PROB_FIELD}",
                list(compatibility.get("ignored_compatibility_fields") or []),
            )
            self.assertIn(
                f"strategy.execution_quality.{self._HISTORICAL_RECOVERY_MAX_QUEUE_FIELD}",
                list(compatibility.get("ignored_compatibility_fields") or []),
            )
            self.assertGreaterEqual(len(caught), 2)

    def test_config_rejects_invalid_dust_classifier_bounds(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["risk"]["position_dust_notional_usd_epsilon"] = 2.0
        cfg["risk"]["position_dust_total_notional_usd_cap"] = 1.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

        cfg2 = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg2["targets"]["token_ids"] = ["tok1"]
        cfg2["risk"]["position_dust_shares_epsilon"] = 2.0
        cfg2["risk"]["min_order_size"] = 1.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg2)

        cfg3 = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg3["targets"]["token_ids"] = ["tok1"]
        cfg3["runtime"]["expiry_boundary_epsilon_sec"] = 9.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg3)

    def test_config_ignores_removed_order_rate_recovery_reserved_slots(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["risk"]["max_orders_per_min"] = 10
        cfg["risk"][self._HISTORICAL_RECOVERY_RATE_RESERVED_SLOTS_FIELD] = 10
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

    def test_config_rejects_non_positive_held_ws_missing_refresh_interval(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["runtime"]["held_ws_missing_or_unusable_refresh_interval_sec"] = -1
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
        cfg["profile"]["name"] = "fixture_profile_a"
        cfg["runtime"]["paper_expected_profile_name"] = "fixture_profile_b"
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
        cfg["taker"]["competitiveness"]["min_visible_fill_ratio"] = 0.5
        cfg["runtime"]["paper_enforce_setup_lock"] = True
        cfg["runtime"]["paper_expected_profile_name"] = str(cfg["profile"]["name"])
        cfg["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
        cfg["_meta"] = {"effective_config_sha256": "a" * 64}
        validate_execution_config(cfg)

    def test_config_rejects_invalid_maker_competitiveness_timing_window(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["lifecycle"]["phase"]["maker_window_open_sec"] = 6.0
        cfg["lifecycle"]["phase"]["taker_window_open_sec"] = 7.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_legacy_maker_competitiveness_one_sided_stage_surface(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["strategy"]["maker_competitiveness"]["one_sided_allowed_stages"] = ["EXTREME_ONLY"]
        with self.assertRaisesRegex(
            ValueError,
            "strategy.maker_competitiveness.one_sided_allowed_stages is retired",
        ):
            validate_execution_config(cfg)

    def test_load_execution_config_ignores_legacy_maker_queue_pressure_surface(self):
        with tempfile.TemporaryDirectory() as td, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg_path = Path(td) / "legacy_queue_pressure.yaml"
            cfg_path.write_text(
                "\n".join(
                    [
                        "targets:",
                        "  token_ids: [tok1]",
                        "strategy:",
                        "  maker_competitiveness:",
                        "    queue_pressure:",
                        "      enabled: definitely_not_bool",
                        "      allowed_stages: [EXTREME_ONLY]",
                        "      inside_price_ticks: 0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cfg = load_execution_config(cfg_path)
            maker_comp = dict((cfg.get("strategy") or {}).get("maker_competitiveness") or {})
            self.assertNotIn("queue_pressure", maker_comp)
            meta = dict(cfg.get("_meta") or {})
            self.assertIn(
                "strategy.maker_competitiveness.queue_pressure",
                list(meta.get("ignored_compatibility_fields") or []),
            )
            self.assertTrue(
                any(
                    "strategy.maker_competitiveness.queue_pressure" in str(w.message)
                    for w in caught
                )
            )

    def test_legacy_maker_queue_pressure_config_matches_clean_runtime_behavior(self):
        with tempfile.TemporaryDirectory() as td, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            root = Path(td)
            clean_cfg_path = root / "clean.yaml"
            clean_cfg_path.write_text("targets:\n  token_ids: [tok1]\n", encoding="utf-8")
            legacy_cfg_path = root / "legacy.yaml"
            legacy_cfg_path.write_text(
                "\n".join(
                    [
                        "targets:",
                        "  token_ids: [tok1]",
                        "strategy:",
                        "  maker_competitiveness:",
                        "    queue_pressure:",
                        "      enabled: true",
                        "      allowed_stages: [MAKER_TAKER_SELECTIVE]",
                        "      inside_price_ticks: 1",
                        "      max_queue_ahead_size: 100",
                        "      min_expected_fill_prob: 0.10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            clean_snapshot = self._run_loaded_config_maker_snapshot(clean_cfg_path)
            legacy_snapshot = self._run_loaded_config_maker_snapshot(legacy_cfg_path)

        self.assertEqual(clean_snapshot["first_summary"]["open_orders"], 1)
        self.assertEqual(legacy_snapshot["first_summary"]["open_orders"], 1)
        self.assertEqual(
            clean_snapshot["second_summary"].get("maker_no_submission_reason_by_token", {}),
            legacy_snapshot["second_summary"].get("maker_no_submission_reason_by_token", {}),
        )
        self.assertEqual(clean_snapshot["open_orders"], legacy_snapshot["open_orders"])
        self.assertEqual(clean_snapshot["queue_pressure_rows"], [])
        self.assertEqual(legacy_snapshot["queue_pressure_rows"], [])
        self.assertTrue(
            any(
                "strategy.maker_competitiveness.queue_pressure" in str(w.message)
                for w in caught
            )
        )

    def test_malformed_legacy_maker_queue_pressure_config_matches_clean_runtime_behavior(self):
        with tempfile.TemporaryDirectory() as td, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            root = Path(td)
            clean_cfg_path = root / "clean.yaml"
            clean_cfg_path.write_text("targets:\n  token_ids: [tok1]\n", encoding="utf-8")
            malformed_cfg_path = root / "malformed.yaml"
            malformed_cfg_path.write_text(
                "\n".join(
                    [
                        "targets:",
                        "  token_ids: [tok1]",
                        "strategy:",
                        "  maker_competitiveness:",
                        "    queue_pressure:",
                        "      enabled: definitely_not_bool",
                        "      allowed_stages: [EXTREME_ONLY, nonsense_stage]",
                        "      inside_price_ticks: -7",
                        "      max_queue_ahead_size: nope",
                        "      min_expected_fill_prob: still_nope",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            clean_snapshot = self._run_loaded_config_maker_snapshot(clean_cfg_path)
            malformed_snapshot = self._run_loaded_config_maker_snapshot(malformed_cfg_path)
            loaded_cfg = load_execution_config(malformed_cfg_path)

        self.assertEqual(clean_snapshot["first_summary"]["open_orders"], 1)
        self.assertEqual(malformed_snapshot["first_summary"]["open_orders"], 1)
        self.assertEqual(
            clean_snapshot["second_summary"].get("maker_no_submission_reason_by_token", {}),
            malformed_snapshot["second_summary"].get("maker_no_submission_reason_by_token", {}),
        )
        self.assertEqual(clean_snapshot["open_orders"], malformed_snapshot["open_orders"])
        self.assertEqual(malformed_snapshot["queue_pressure_rows"], [])
        maker_comp = dict((loaded_cfg.get("strategy") or {}).get("maker_competitiveness") or {})
        self.assertNotIn("queue_pressure", maker_comp)
        self.assertTrue(
            any(
                "strategy.maker_competitiveness.queue_pressure" in str(w.message)
                for w in caught
            )
        )

    def test_config_rejects_maker_competitive_floor_when_notional_mode_disabled(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["sizing"]["mode"] = "shares"
        cfg["sizing"]["maker_competitive_min_notional_usd"] = 100.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_taker_stage_window_boost_alignment(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["lifecycle"]["phase"]["taker_window_open_sec"] = 6.0
        cfg["lifecycle"]["lane_gates"]["taker"]["multi_oracle_boost_window_sec"] = 7.0
        with self.assertRaises(ValueError):
            validate_execution_config(cfg)

    def test_config_rejects_legacy_taker_stage_cooldown_surface(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["taker"]["per_token_cooldown_sec_by_stage"] = {"LEGACY_STAGE": 0.75}
        with self.assertRaisesRegex(
            ValueError,
            "taker.per_token_cooldown_sec_by_stage is retired",
        ):
            validate_execution_config(cfg)

    def test_config_rejects_invalid_taker_min_edge_stage_key(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["taker"]["min_edge_by_stage"] = {"MAKER_TAKER_SELECTIVE": 0.20}
        with self.assertRaisesRegex(ValueError, "taker.min_edge_by_stage is retired"):
            validate_execution_config(cfg)

    def test_config_rejects_legacy_taker_stage_final_window_surface(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["taker"]["competitiveness"]["stage_final_window_sec_by_stage"] = {
            "EXTREME_ONLY": 7.0
        }
        with self.assertRaisesRegex(
            ValueError,
            "taker.competitiveness.stage_final_window_sec_by_stage is retired",
        ):
            validate_execution_config(cfg)

    def test_config_rejects_legacy_taker_stage_priority_surface(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["taker"]["competitiveness"]["stage_priority_enabled"] = True
        with self.assertRaisesRegex(
            ValueError,
            "taker.competitiveness.stage_priority_enabled is retired",
        ):
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

    def test_config_rejects_taker_stage_aggressiveness_current_authority(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["taker"]["competitiveness"]["stage_aggressiveness"] = {
            "EXTREME_ONLY": {"size_mult": 0.9, "price_aggress_bps": 0.0}
        }
        with self.assertRaisesRegex(ValueError, "stage_aggressiveness is retired for current configs"):
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

    def test_wallet_resize_is_revalidated_by_risk_before_submit(self):
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
                    allowed=True,
                    action="reduce",
                    approved_size=0.1,
                    reason="wallet_resize",
                    detail="test",
                    halt=False,
                    lock_id="lock-1",
                    authorization_id="lock-1",
                ),
            ):
                with mock.patch.object(manager.wallet, "release_pending_lock", wraps=manager.wallet.release_pending_lock) as release_lock:
                    with mock.patch.object(
                        manager.tx_manager,
                        "submit_order",
                        side_effect=AssertionError("submit_order should not be reached after post-wallet risk reject"),
                    ):
                        placed, reason = manager._place_order(
                            OrderIntent(
                                token_id="t1",
                                side="BUY",
                                price=0.45,
                                size=2.0,
                                tif="GTC",
                                post_only=True,
                                reason="test",
                            ),
                            top,
                            open_orders_for_token=[],
                            open_orders_total=0,
                        )
                    release_lock.assert_called_once_with("lock-1")
            self.assertIsNone(placed)
            self.assertEqual(reason, "risk_reject_size_too_small")
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
            first = manager.step(
                {"t1": top},
                competitiveness_context_by_token={"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}},
            )
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
            second = manager.step(
                {"t1": cross},
                competitiveness_context_by_token={"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}},
            )
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
                        "stage": "MAKER_TAKER_SELECTIVE",
                        "sec_to_expiry": 45.0,
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

    def test_order_manager_maker_selection_gate_submits_when_launch_safe_requirements_pass(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_sec_to_expiry=10.0,
                max_sec_to_expiry=15.0,
            )
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
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
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 12.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                }
            }
            summary = manager.step({"t1": top}, competitiveness_context_by_token=context)
            self.assertEqual(summary["open_orders"], 1)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            self.assertEqual(len(shadow_rows), 1)
            self.assertEqual(len(submit_rows), 1)
            shadow = shadow_rows[0]
            submit = submit_rows[0]
            maker_comp = submit.get("maker_competitiveness")
            self.assertIsInstance(maker_comp, dict)
            self.assertEqual(str(shadow.get("decision_result") or ""), "submitted")
            self.assertEqual(bool(shadow.get("launch_safe_selection_enabled")), True)
            self.assertEqual(bool(shadow.get("launch_safe_selection_applied")), True)
            self.assertEqual(bool(shadow.get("launch_safe_selection_passed")), True)
            self.assertEqual(bool(shadow.get("launch_safe_selection_timing_window_met")), True)
            self.assertEqual(bool(shadow.get("cannon_depth_requirement_met")), True)
            self.assertEqual(bool(shadow.get("repeat_target_side_calm")), True)
            self.assertEqual(
                bool(maker_comp.get("launch_safe_selection_passed")),
                True,
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_rejects_when_timing_unknown(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_sec_to_expiry=10.0,
                max_sec_to_expiry=15.0,
            )
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(self._risk_cfg_without_expiry_gate(), {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                object(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            verdict = manager._evaluate_maker_selection_gate(  # pylint: disable=protected-access
                shadow_event={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "desired_quote_price": 20.0,
                    "visible_depth_shares": 2.0,
                    "same_target_side_submit_count_prior": 0,
                    "secondary_oracle_confirmation": True,
                },
                competitiveness_context={"stage": "MAKER_TAKER_SELECTIVE"},
            )
            self.assertEqual(bool(verdict.get("applied")), True)
            self.assertEqual(bool(verdict.get("passed")), False)
            self.assertEqual(str(verdict.get("primary_reject_reason") or ""), "timing_window_unknown")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_rejects_when_timing_above_window(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_sec_to_expiry=10.0,
                max_sec_to_expiry=15.0,
            )
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(self._risk_cfg_without_expiry_gate(), {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                object(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            verdict = manager._evaluate_maker_selection_gate(  # pylint: disable=protected-access
                shadow_event={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "desired_quote_price": 20.0,
                    "visible_depth_shares": 2.0,
                    "same_target_side_submit_count_prior": 0,
                    "secondary_oracle_confirmation": True,
                    "sec_to_expiry": 16.0,
                },
                competitiveness_context={"stage": "MAKER_TAKER_SELECTIVE"},
            )
            self.assertEqual(bool(verdict.get("applied")), True)
            self.assertEqual(bool(verdict.get("passed")), False)
            self.assertEqual(str(verdict.get("primary_reject_reason") or ""), "timing_window_out_of_band")
            self.assertEqual(bool(verdict.get("timing_window_met")), False)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_rejects_when_timing_below_window(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_sec_to_expiry=10.0,
                max_sec_to_expiry=15.0,
            )
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(self._risk_cfg_without_expiry_gate(), {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                object(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            verdict = manager._evaluate_maker_selection_gate(  # pylint: disable=protected-access
                shadow_event={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "desired_quote_price": 20.0,
                    "visible_depth_shares": 2.0,
                    "same_target_side_submit_count_prior": 0,
                    "secondary_oracle_confirmation": True,
                    "sec_to_expiry": 9.0,
                },
                competitiveness_context={"stage": "MAKER_TAKER_SELECTIVE"},
            )
            self.assertEqual(bool(verdict.get("applied")), True)
            self.assertEqual(bool(verdict.get("passed")), False)
            self.assertEqual(str(verdict.get("primary_reject_reason") or ""), "timing_window_out_of_band")
            self.assertEqual(bool(verdict.get("timing_window_met")), False)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_preserves_committed_live_order_when_depth_later_deteriorates(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg()
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 55.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                }
            }
            good_top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            first = manager.step({"t1": good_top}, competitiveness_context_by_token=context)
            self.assertEqual(first["open_orders"], 1)

            shallow_top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.49,
                best_bid_size=10.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            second = manager.step({"t1": shallow_top}, competitiveness_context_by_token=context)
            self.assertEqual(second["open_orders"], 1)
            self.assertEqual(
                dict(second.get("maker_no_submission_reason_by_token", {})).get("t1"),
                "maker_commitment_hold_active",
            )

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            self.assertEqual(len(shadow_rows), 1)
            self.assertEqual(bool(shadow_rows[0].get("launch_safe_selection_passed")), True)
            self.assertEqual(len(gateway.get_open_orders()), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_rejects_without_secondary_confirmation(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg()
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
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
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 55.0,
                    "secondary_oracle_status": "direction_mismatch",
                    "secondary_oracle_confirmation": False,
                }
            }
            summary = manager.step({"t1": top}, competitiveness_context_by_token=context)
            self.assertEqual(summary["open_orders"], 0)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            self.assertEqual(len(shadow_rows), 1)
            shadow = shadow_rows[0]
            self.assertEqual(str(shadow.get("decision_result") or ""), "selection_rejected")
            self.assertEqual(
                str(shadow.get("decision_block_reason") or ""),
                "launch_safe_selection_secondary_oracle_not_confirmed",
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_rejects_prior_same_side_submit(self):
        runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg()
        risk_cfg = self._risk_cfg_without_expiry_gate()
        risk_cfg["max_book_age_sec"] = 100.0
        with tempfile.TemporaryDirectory() as tmp:
            gateway = PaperGateway()
            events = EventLogger(Path(tmp))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})

            class _NoopStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return []

            manager = OrderManager(
                gateway,
                _NoopStrategy(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                sizing_cfg=sizing_cfg,
            )
            verdict = manager._evaluate_maker_selection_gate(  # pylint: disable=protected-access
                shadow_event={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "desired_quote_price": 0.50,
                    "visible_depth_shares": 100.0,
                    "same_target_submit_count_prior": 2,
                    "same_target_side_submit_count_prior": 2,
                    "secondary_oracle_confirmation": True,
                    "sec_to_expiry": 55.0,
                },
                competitiveness_context={
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "secondary_oracle_confirmation": True,
                    "sec_to_expiry": 55.0,
                },
            )
            self.assertEqual(bool(verdict.get("applied")), True)
            self.assertEqual(bool(verdict.get("passed")), False)
            self.assertEqual(str(verdict.get("primary_reject_reason") or ""), "selection_prior_same_side_submit")
            self.assertIn(
                "selection_prior_same_side_submit",
                list(verdict.get("reject_reasons") or []),
            )
            self.assertEqual(int(float(verdict.get("same_target_submit_count_prior") or 0.0)), 2)
            self.assertEqual(int(float(verdict.get("same_target_side_submit_count_prior") or 0.0)), 2)
            self.assertEqual(bool(verdict.get("repeat_target_side_calm")), False)
            events.close()

    def test_order_manager_maker_selection_gate_rejects_prior_target_submit_on_opposite_side(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_depth_multiple=0.0,
                max_same_target_submit_count_prior=0,
                max_same_target_side_submit_count_prior=0,
            )
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _MutableStrategy:
                def __init__(self) -> None:
                    self.side = "BUY"

                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side=self.side,
                            price=float(top.best_bid_price or 0.0)
                            if self.side == "BUY"
                            else float(top.best_ask_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            strategy = _MutableStrategy()
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
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
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            buy_context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 12.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "one_sided_active": True,
                    "side_policy": "BUY_ONLY",
                }
            }
            sell_context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "fair_probability": 0.40,
                    "market_probability": 0.51,
                    "edge_signed": -0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 11.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "one_sided_active": True,
                    "side_policy": "SELL_ONLY",
                }
            }
            first = manager.step({"t1": top}, competitiveness_context_by_token=buy_context)
            self.assertEqual(first["open_orders"], 1)
            strategy.side = "SELL"
            second = manager.step({"t1": top}, competitiveness_context_by_token=sell_context)
            self.assertEqual(second["open_orders"], 1)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            self.assertEqual(len(submit_rows), 1)
            self.assertEqual(len(shadow_rows), 1)
            self.assertEqual(
                dict(second.get("maker_no_submission_reason_by_token", {})).get("t1"),
                "maker_commitment_hold_active",
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_maker_selection_gate_allows_low_depth_when_depth_gate_zero(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg, strategy_cfg, sizing_cfg = self._selection_gate_runtime_strategy_sizing_cfg(
                min_depth_multiple=0.0,
                max_same_target_submit_count_prior=0,
                max_same_target_side_submit_count_prior=0,
            )
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
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
                best_bid_size=1.0,
                best_ask_price=0.51,
                best_ask_size=1.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "market_reference_mode": "direct_midpoint",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 12.0,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "one_sided_active": True,
                    "side_policy": "BUY_ONLY",
                }
            }
            summary = manager.step({"t1": top}, competitiveness_context_by_token=context)
            self.assertEqual(summary["open_orders"], 1)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            self.assertEqual(len(shadow_rows), 1)
            shadow = shadow_rows[0]
            self.assertEqual(str(shadow.get("decision_result") or ""), "submitted")
            self.assertEqual(bool(shadow.get("launch_safe_selection_passed")), True)
            self.assertEqual(bool(shadow.get("cannon_depth_requirement_met")), True)
            self.assertLess(float(shadow.get("depth_multiple_vs_cannon_target") or 0.0), 1.5)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_ignores_dead_reduce_only_size_cap_hint_on_submit(self):
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
            positions = {"t1": Position(token_id="t1", net_shares=5.0)}
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
            intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.55,
                size=10.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reason = manager._place_order(  # pylint: disable=protected-access
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                    # Historical recovery-era hint; current runtime must ignore it.
                    "reduce_only_size_cap_shares": 1.25,
                },
            )
            self.assertIsNotNone(placed)
            self.assertIn(reason, (None, ""))
            self.assertAlmostEqual(float(placed.size), 10.0, places=9)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_emits_maker_fight_admission_shadow_and_submit_linkage(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-alpha",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            fixed_shadow_ts = dt.datetime.now(dt.timezone.utc)
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
                risk,
                events,
                telemetry,
                runtime_cfg,
                strategy_cfg,
                now_fn=lambda: fixed_shadow_ts,
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(fixed_shadow_ts),
                source="test",
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.60,
                    "market_probability": 0.49,
                    "edge_signed": 0.11,
                    "edge_abs": 0.11,
                    "sec_to_expiry": 14.5,
                    "secondary_fair_probability": 0.58,
                    "secondary_edge_value": 0.09,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                    "chainlink_spot_price": 100125.0,
                    "secondary_oracle_spot_price": 100118.0,
                    "secondary_oracle_price_delta_abs": 7.0,
                    "secondary_oracle_price_delta_bps": 0.699125,
                }
            }
            first = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=7)
            self.assertEqual(first["open_orders"], 1)
            second = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=8)
            self.assertEqual(second["open_orders"], 1)
            self.assertEqual(int(second["actions"]), 0)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            self.assertEqual(len(shadow_rows), 1)
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            self.assertEqual(len(submit_rows), 1)

            first_shadow = shadow_rows[0]
            submit_row = submit_rows[0]
            maker_comp = submit_row.get("maker_competitiveness")
            self.assertIsInstance(maker_comp, dict)
            self.assertEqual(str(first_shadow.get("target_side_ref") or ""), "target-alpha|BUY")
            self.assertEqual(str(first_shadow.get("decision_result") or ""), "submitted")
            self.assertEqual(str(first_shadow.get("ts_decision_utc") or ""), utc_iso(fixed_shadow_ts))
            self.assertEqual(str(first_shadow.get("ts_event_utc") or ""), utc_iso(fixed_shadow_ts))
            self.assertEqual(str(first_shadow.get("ts_utc") or ""), utc_iso(fixed_shadow_ts))
            self.assertEqual(str(first_shadow.get("order_submit_id") or ""), str(submit_row.get("order_id") or ""))
            self.assertEqual(
                str(maker_comp.get("admission_shadow_id") or ""),
                str(first_shadow.get("admission_shadow_id") or ""),
            )
            self.assertAlmostEqual(float(first_shadow.get("sec_to_expiry") or 0.0), 14.5, places=9)
            self.assertEqual(str(first_shadow.get("secondary_oracle_status") or ""), "confirmed")
            self.assertEqual(bool(first_shadow.get("secondary_oracle_confirmation")), True)
            self.assertAlmostEqual(float(first_shadow.get("desired_quote_price") or 0.0), 0.49, places=9)
            self.assertAlmostEqual(float(first_shadow.get("chainlink_spot_price") or 0.0), 100125.0, places=9)
            self.assertAlmostEqual(
                float(first_shadow.get("secondary_oracle_spot_price") or 0.0), 100118.0, places=9
            )
            self.assertEqual(int(float(first_shadow.get("open_maker_orders_total") or 0.0)), 0)
            self.assertEqual(int(float(first_shadow.get("open_orders_for_token_count") or 0.0)), 0)
            self.assertEqual(int(float(first_shadow.get("open_orders_same_side_count") or 0.0)), 0)
            self.assertEqual(int(float(first_shadow.get("same_target_side_shadow_count_prior") or 0.0)), 0)
            self.assertEqual(int(float(first_shadow.get("same_target_side_submit_count_prior") or 0.0)), 0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_shadow_uses_submission_candidate_quote_when_post_only_clamp_applies(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _CrossingSellStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="SELL",
                            price=0.40,
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test_cross_guard",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-cross-guard",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _CrossingSellStrategy(),
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
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.61,
                    "market_probability": 0.55,
                    "edge_signed": -0.06,
                    "edge_abs": 0.06,
                    "sec_to_expiry": 12.0,
                    "secondary_fair_probability": 0.60,
                    "secondary_edge_value": -0.05,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                }
            }
            result = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=11)
            self.assertEqual(result["open_orders"], 1)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            clamp_rows = self._read_event_rows(Path(tmp.name), event_type="pre_submit_cross_guard_adjusted")
            self.assertEqual(len(shadow_rows), 1)
            self.assertEqual(len(submit_rows), 1)
            self.assertEqual(len(clamp_rows), 1)

            shadow = shadow_rows[0]
            submit = submit_rows[0]
            clamp = clamp_rows[0]
            self.assertEqual(str(shadow.get("decision_result") or ""), "submitted")
            self.assertAlmostEqual(float(shadow.get("strategy_quote_price") or 0.0), 0.40, places=9)
            self.assertAlmostEqual(float(shadow.get("desired_quote_price") or 0.0), 0.491, places=9)
            self.assertAlmostEqual(
                float(shadow.get("submission_candidate_quote_price") or 0.0),
                0.491,
                places=9,
            )
            self.assertEqual(bool(shadow.get("pre_submit_cross_guard_preview_applied")), True)
            self.assertAlmostEqual(
                float(shadow.get("pre_submit_cross_guard_preview_original_price") or 0.0),
                0.40,
                places=9,
            )
            self.assertAlmostEqual(
                float(shadow.get("pre_submit_cross_guard_preview_adjusted_price") or 0.0),
                0.491,
                places=9,
            )
            self.assertAlmostEqual(float(clamp.get("original_price") or 0.0), 0.40, places=9)
            self.assertAlmostEqual(float(clamp.get("adjusted_price") or 0.0), 0.491, places=9)
            self.assertAlmostEqual(float(submit.get("price") or 0.0), 0.491, places=9)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_revalidates_risk_on_post_only_clamped_quote(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_notional_per_token"] = 4.5

            class _CrossingSellStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="SELL",
                            price=0.40,
                            size=10.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test_cross_guard_risk",
                            stage="MAKER_TAKER_SELECTIVE",
                            target_ref="target-cross-guard-risk",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _CrossingSellStrategy(),
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
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            context = {
                "t1": {
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "NORMAL",
                    "market_reference_class": "authoritative",
                    "fair_probability": 0.61,
                    "market_probability": 0.55,
                    "edge_signed": -0.06,
                    "edge_abs": 0.06,
                    "sec_to_expiry": 12.0,
                    "secondary_fair_probability": 0.60,
                    "secondary_edge_value": -0.05,
                    "secondary_oracle_status": "confirmed",
                    "secondary_oracle_confirmation": True,
                }
            }
            result = manager.step({"t1": top}, competitiveness_context_by_token=context, cycle_index=12)
            self.assertEqual(result["open_orders"], 0)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            clamp_rows = self._read_event_rows(Path(tmp.name), event_type="pre_submit_cross_guard_adjusted")
            risk_rows = self._read_event_rows(Path(tmp.name), event_type="risk_reject")
            self.assertEqual(len(shadow_rows), 1)
            self.assertEqual(len(submit_rows), 0)
            self.assertEqual(len(clamp_rows), 1)
            self.assertEqual(len(risk_rows), 1)

            shadow = shadow_rows[0]
            risk_reject = risk_rows[0]
            self.assertEqual(str(shadow.get("decision_result") or ""), "submit_rejected")
            self.assertEqual(str(shadow.get("decision_block_reason") or ""), "risk_reject_notional_cap")
            self.assertAlmostEqual(float(shadow.get("desired_quote_price") or 0.0), 0.491, places=9)
            self.assertAlmostEqual(float(risk_reject.get("price") or 0.0), 0.491, places=9)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_does_not_emit_maker_fight_admission_shadow_for_side_disallowed_rows(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            class _PinnedBuyStrategy:
                def make_quotes(self, token_id, top, position, **kwargs):  # noqa: ANN001, ANN002, ANN003
                    return [
                        OrderIntent(
                            token_id=token_id,
                            side="BUY",
                            price=float(top.best_bid_price or 0.0),
                            size=5.0,
                            tif="GTC",
                            post_only=True,
                            reason="mm_quote:test",
                            stage="MAKER_TAKER_SELECTIVE",
                        )
                    ]

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            risk = RiskEngine(risk_cfg, {"t1": Position(token_id="t1")})
            manager = OrderManager(
                gateway,
                _PinnedBuyStrategy(),
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
                best_bid_price=0.49,
                best_bid_size=100.0,
                best_ask_price=0.51,
                best_ask_size=100.0,
            )
            summary = manager.step(
                {"t1": top},
                side_policy_by_token={"t1": "SELL_ONLY"},
                competitiveness_context_by_token={"t1": {"financial_posture_class": "NORMAL"}},
            )
            self.assertEqual(summary["open_orders"], 0)

            events.close()
            events = None
            shadow_rows = self._read_event_rows(Path(tmp.name), event_type="maker_fight_admission_shadow")
            self.assertEqual(shadow_rows, [])
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_runner_maker_edge_evaluation_emits_cannon_probe_fields(self):
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
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=120.0,
                    best_ask_price=0.51,
                    best_ask_size=180.0,
                )
                runner._emit_maker_edge_evaluations(
                    books={"t1": top},
                    stage_info_by_token={"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 12.0}},
                    maker_eval_token_ids={"t1"},
                    maker_submitted_token_ids=set(),
                    maker_submitted_order_ids_by_token={},
                    maker_no_submission_reason_by_token={},
                    maker_no_submission_category_by_token={},
                    maker_prereq_failure_by_token={},
                    fair_probability_by_token={"t1": 0.60},
                    maker_competitiveness_profiles_by_token={
                        "t1": {
                            "context": {
                                "secondary_fair_probability": 0.58,
                                "secondary_oracle_status": "confirmed",
                                "secondary_oracle_confirmation": True,
                                "chainlink_spot_price": 100125.0,
                                "secondary_oracle_spot_price": 100118.0,
                                "secondary_oracle_price_delta_abs": 7.0,
                                "secondary_oracle_price_delta_bps": 0.699125,
                            }
                        }
                    },
                    oracle_tick_age_sec=0.0,
                    latency_state="armed",
                    cycle_index=7,
                )
                runner.events.close()
                edge_rows = self._read_event_rows(Path(td), event_type="edge_evaluation")
                maker_rows = [row for row in edge_rows if str(row.get("evaluation_scope") or "") == "maker"]
                self.assertEqual(len(maker_rows), 1)
                row = maker_rows[0]
                self.assertEqual(str(row.get("financial_posture_class") or ""), "NORMAL")
                self.assertEqual(str(row.get("secondary_oracle_status") or ""), "confirmed")
                self.assertEqual(bool(row.get("secondary_oracle_confirmation")), True)
                self.assertEqual(str(row.get("probe_favored_side") or ""), "BUY")
                self.assertAlmostEqual(float(row.get("probe_visible_depth_shares") or 0.0), 120.0, places=9)
                self.assertEqual(int(float(row.get("open_maker_orders_total") or 0.0)), 0)
                self.assertAlmostEqual(float(row.get("chainlink_spot_price") or 0.0), 100125.0, places=9)
                self.assertAlmostEqual(float(row.get("secondary_oracle_spot_price") or 0.0), 100118.0, places=9)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()

    def test_runner_maker_cannon_probe_token_ids_include_late_window_non_maker_tokens(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["t1", "t2", "t3", "t4"]
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["chainlink"]["enabled"] = False

        runner = ExecutionRunner(cfg)
        stage_info_by_token = {
            "t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 12.0, "maker_gate_open": False},
            "t2": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 55.0, "maker_gate_open": True},
            "t3": {"stage": "EXTREME_ONLY", "sec_to_expiry": 19.5, "maker_gate_open": False},
            "t4": {"stage": "EXPIRED", "sec_to_expiry": -1.0, "maker_gate_open": False},
        }
        books = {
            "t1": object(),
            "t2": object(),
            "t3": object(),
        }
        token_ids = runner._maker_cannon_probe_token_ids(
            stage_info_by_token=stage_info_by_token,
            books=books,
        )
        self.assertEqual(token_ids, {"t1", "t3"})
        runner.chainlink.stop()
        runner.alerts.close()

    def test_order_manager_dead_reduce_only_size_cap_hint_does_not_bypass_terminal_halt_new_risk(self):
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
            positions = {"t1": Position(token_id="t1", net_shares=-97.347)}
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
            intent = OrderIntent(
                token_id="t1",
                side="BUY",
                price=0.54,
                size=200.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reason = manager._place_order(  # pylint: disable=protected-access
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "sec_to_expiry": 45.0,
                    # Historical recovery-era hint; current runtime must not let it
                    # reopen HALT_NEW_RISK when the submit is not purely risk reducing.
                    "reduce_only_size_cap_shares": 297.347,
                },
            )
            self.assertIsNone(placed)
            self.assertEqual(str(reason or ""), "risk_reject_terminal_unwind_halt_new_risk_blocked")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_dead_reduce_only_hint_does_not_reopen_flat_terminal_submit(self):
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
            positions = {"t1": Position(token_id="t1", net_shares=0.0)}
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
            intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=200.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reason = manager._place_order(  # pylint: disable=protected-access
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "financial_posture_class": "HALT_NEW_RISK",
                    "sec_to_expiry": 45.0,
                    # Historical recovery-era hint; current runtime must ignore it.
                    "reduce_only_size_cap_shares": 297.347,
                },
            )
            self.assertIsNone(placed)
            self.assertEqual(str(reason or ""), "risk_reject_terminal_unwind_halt_new_risk_blocked")
            self.assertEqual(int(telemetry.counters.get("risk_rejects", 0)), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_recovery_size_cap_fallback_no_longer_overrides_maker_notional_floor_conflict(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            sizing_cfg = {
                "mode": "notional",
                "min_usd": 1.0,
                "max_usd": 20.0,
                "target_usd": 5.0,
                "rounding": "floor",
                "price_source": "mid",
                "share_step": 0.01,
                "maker_competitive_min_notional_usd": 100.0,
                "maker_competitive_max_notional_usd": 500.0,
                "maker_competitive_min_shares": 0.0,
                "maker_competitive_max_shares": 200.0,
            }

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=3.0)}
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
                best_bid_price=0.41,
                best_bid_size=100.0,
                best_ask_price=0.43,
                best_ask_size=100.0,
            )
            recovery_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.43,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed_recovery, reject_recovery = manager._place_order(  # pylint: disable=protected-access
                recovery_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                    "reduce_only_size_cap_shares": 2.0,
                    "reduce_only_min_order_size_shares": 1.0,
                },
            )
            self.assertIsNone(placed_recovery)
            self.assertEqual(str(reject_recovery), "sizing_reject")

            non_recovery_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.43,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="maker_quote",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed_non_recovery, reject_non_recovery = manager._place_order(  # pylint: disable=protected-access
                non_recovery_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                },
            )
            self.assertIsNone(placed_non_recovery)
            self.assertEqual(str(reject_non_recovery), "sizing_reject")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_recovery_quality_relaxation_removed_from_maker_lane(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = True
            strategy_cfg["execution_quality"]["max_queue_ahead_size"] = 100.0
            strategy_cfg["execution_quality"]["min_expected_fill_prob"] = 0.06
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=5.0)}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100.0,
                best_ask_price=0.55,
                best_ask_size=150.0,
            )

            reduce_only_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed_recovery, reject_recovery = manager._place_order(  # pylint: disable=protected-access
                reduce_only_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                    "reduce_only_size_cap_shares": 2.0,
                },
            )
            self.assertIsNone(placed_recovery)
            self.assertEqual(str(reject_recovery), "quote_quality_skip_queue_depth")

            non_recovery_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="maker_quote",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed_non_recovery, reject_non_recovery = manager._place_order(  # pylint: disable=protected-access
                non_recovery_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                },
            )
            self.assertIsNone(placed_non_recovery)
            self.assertEqual(str(reject_non_recovery), "quote_quality_skip_queue_depth")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_quote_quality_skip_emits_effective_thresholds(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = True
            strategy_cfg["execution_quality"]["max_queue_ahead_size"] = 100.0
            strategy_cfg["execution_quality"]["min_expected_fill_prob"] = 0.06
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=0.0)}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100.0,
                best_ask_price=0.55,
                best_ask_size=150.0,
            )
            intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=2.0,
                tif="GTC",
                post_only=True,
                reason="maker_quote",
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reject_reason = manager._place_order(  # pylint: disable=protected-access
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 45.0,
                },
            )
            self.assertIsNone(placed)
            self.assertEqual(str(reject_reason), "quote_quality_skip_queue_depth")
            events.close()
            events = None
            quote_skip_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "quote_quality_skip":
                        quote_skip_rows.append(payload)
            self.assertTrue(quote_skip_rows)
            row = quote_skip_rows[-1]
            self.assertEqual(str(row.get("skip_reason") or ""), "queue_ahead_too_deep")
            self.assertAlmostEqual(float(row.get("default_max_queue_ahead_size") or 0.0), 100.0, places=9)
            self.assertAlmostEqual(float(row.get("effective_max_queue_ahead_size") or 0.0), 100.0, places=9)
            self.assertAlmostEqual(float(row.get("default_min_expected_fill_prob") or 0.0), 0.06, places=9)
            self.assertAlmostEqual(float(row.get("effective_min_expected_fill_prob") or 0.0), 0.06, places=9)
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
            self.assertEqual(len(risk.cancel_timestamps), 0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_order_manager_consumes_cancel_budget_only_on_confirmed_cancel(self):
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

            order = gateway.place_order(
                OrderIntent(token_id="t1", side="BUY", price=0.45, size=10.0, tif="GTC", post_only=True, reason="test"),
                client_order_id="c1",
            )
            ok = manager._cancel_order(order, "test_cancel")
            self.assertTrue(ok)
            self.assertEqual(len(risk.cancel_timestamps), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_maker_submit_rejects_when_commitment_expiry_context_missing(self):
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
                best_bid_price=0.45,
                best_bid_size=100.0,
                best_ask_price=0.55,
                best_ask_size=100.0,
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
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reject_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE"},
            )
            self.assertIsNone(placed)
            self.assertEqual(reject_reason, "maker_commitment_context_missing")
            self.assertEqual(gateway.get_open_orders(), [])
            self.assertEqual(float(manager.wallet.status().get("pending_lock_usdc", 0.0) or 0.0), 0.0)

            reject_rows = self._read_event_rows(Path(tmp.name), event_type="order_submission_rejected_local")
            self.assertTrue(reject_rows)
            self.assertEqual(str(reject_rows[-1].get("reason") or ""), "maker_commitment_context_missing")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_maker_submit_persists_commitment_metadata_and_rehydrates_open_orders(self):
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
                best_bid_price=0.45,
                best_bid_size=100.0,
                best_ask_price=0.55,
                best_ask_size=100.0,
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
                stage="MAKER_TAKER_SELECTIVE",
            )
            placed, reject_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={
                    "submission_lane": "maker",
                    "stage": "MAKER_TAKER_SELECTIVE",
                    "sec_to_expiry": 12.0,
                },
            )
            self.assertIsNotNone(placed)
            self.assertIsNone(reject_reason)
            self.assertTrue(bool(getattr(placed, "commitment_hold_active", False)))
            self.assertEqual(str(getattr(placed, "submission_lane", "") or ""), "maker")
            self.assertTrue(str(getattr(placed, "commitment_expiry_ts_utc", "") or "").strip())

            refreshed = manager.tx_manager.get_open_orders()
            self.assertEqual(len(refreshed), 1)
            self.assertTrue(bool(getattr(refreshed[0], "commitment_hold_active", False)))
            self.assertEqual(str(getattr(refreshed[0], "submission_lane", "") or ""), "maker")
            submit_rows = self._read_event_rows(Path(tmp.name), event_type="order_submit")
            self.assertTrue(submit_rows)
            submit_row = submit_rows[-1]
            self.assertEqual(bool(submit_row.get("commitment_hold_active")), True)
            submit_ts = dt.datetime.fromisoformat(str(submit_row["ts_utc"]).replace("Z", "+00:00"))
            expiry_ts = dt.datetime.fromisoformat(str(submit_row["commitment_expiry_ts_utc"]).replace("Z", "+00:00"))
            self.assertAlmostEqual((expiry_ts - submit_ts).total_seconds(), 12.0, places=2)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_commitment_hold_suppresses_routine_cancel_and_emits_suppressed_event(self):
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

            future_expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            order = gateway.place_order(
                OrderIntent(
                    token_id="t1",
                    side="BUY",
                    price=0.45,
                    size=10.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=future_expiry,
                ),
                client_order_id="c1",
            )
            result = manager._request_cancel_order(
                order,
                "launch_safe_selection_reject",
                request_origin="maker_selection_gate",
            )
            self.assertFalse(bool(result.get("executed", False)))
            self.assertTrue(bool(result.get("suppressed", False)))
            self.assertEqual(len(gateway.get_open_orders()), 1)

            suppressed_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel_suppressed")
            self.assertEqual(len(suppressed_rows), 1)
            self.assertEqual(str(suppressed_rows[0].get("request_origin") or ""), "maker_selection_gate")
            self.assertEqual(str(suppressed_rows[0].get("requested_cancel_reason") or ""), "launch_safe_selection_reject")
            self.assertEqual(str(suppressed_rows[0].get("suppression_reason") or ""), "commitment_hold_active_pre_expiry")
            self.assertEqual(self._read_event_rows(Path(tmp.name), event_type="order_cancel"), [])
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_preserves_committed_maker_order_under_tracked_cleanup(self):
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
            positions = {"t2": Position(token_id="t2")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            future_expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            gateway.place_order(
                OrderIntent(
                    token_id="t2",
                    side="BUY",
                    price=0.4,
                    size=5.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=future_expiry,
                ),
                client_order_id="manual-t2",
            )
            summary = manager.step(
                {},
                tracked_tokens={"t1"},
                tracked_token_cancel_reason_by_token={"t2": "maker_timing_gate_closed"},
            )
            self.assertEqual(summary["open_orders"], 1)
            self.assertEqual(len(gateway.get_open_orders()), 1)
            self.assertEqual(self._read_event_rows(Path(tmp.name), event_type="order_cancel"), [])
            suppressed_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel_suppressed")
            requested = {(str(row.get("request_origin") or ""), str(row.get("requested_cancel_reason") or "")) for row in suppressed_rows}
            self.assertIn(("tracked_token_cleanup", "maker_timing_gate_closed"), requested)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_preserves_committed_maker_order_under_orphan_cleanup(self):
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
            positions = {"t2": Position(token_id="t2")}
            risk = RiskEngine(risk_cfg, positions)
            strategy = MarketMakingStrategy(strategy_cfg)
            manager = OrderManager(gateway, strategy, risk, events, telemetry, runtime_cfg, strategy_cfg)

            future_expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            gateway.place_order(
                OrderIntent(
                    token_id="t2",
                    side="BUY",
                    price=0.4,
                    size=5.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=future_expiry,
                ),
                client_order_id="manual-t2",
            )
            summary = manager.step({}, tracked_tokens={"t1"})
            self.assertEqual(summary["open_orders"], 1)
            self.assertEqual(len(gateway.get_open_orders()), 1)
            self.assertEqual(self._read_event_rows(Path(tmp.name), event_type="order_cancel"), [])
            suppressed_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel_suppressed")
            requested = {(str(row.get("request_origin") or ""), str(row.get("requested_cancel_reason") or "")) for row in suppressed_rows}
            self.assertIn(("non_target_cleanup", "orphan_token_order"), requested)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_market_family_cleanup_suppresses_preexpiry_committed_maker_cancel(self):
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

            future_expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            gateway.place_order(
                OrderIntent(
                    token_id="t1",
                    side="BUY",
                    price=0.4,
                    size=5.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=future_expiry,
                ),
                client_order_id="manual-t1",
            )
            canceled = manager.cancel_orders_for_tokens(
                {"t1"},
                reason="targeted_token_cleanup",
            )
            self.assertEqual(canceled, 0)
            self.assertEqual(len(gateway.get_open_orders()), 1)
            suppressed_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel_suppressed")
            self.assertEqual(str(suppressed_rows[-1].get("request_origin") or ""), "targeted_token_cleanup")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_expired_committed_maker_order_cancels_with_terminal_reason(self):
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
            now_dt = dt.datetime.now(dt.timezone.utc)
            manager._now_fn = lambda: now_dt  # type: ignore[attr-defined]

            order = gateway.place_order(
                OrderIntent(
                    token_id="t1",
                    side="BUY",
                    price=0.4,
                    size=5.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=utc_iso(now_dt - dt.timedelta(seconds=1)),
                ),
                client_order_id="manual-t1",
            )
            order.remaining_size = 2.0
            summary = manager.step({}, tracked_tokens={"t1"})
            self.assertEqual(summary["open_orders"], 0)
            self.assertEqual(gateway.get_open_orders(), [])
            cancel_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel")
            self.assertEqual(len(cancel_rows), 1)
            self.assertEqual(str(cancel_rows[0].get("reason") or ""), "commitment_window_ended")
            self.assertEqual(str(cancel_rows[0].get("cancel_class") or ""), "terminal_window_end")
            self.assertEqual(float(cancel_rows[0].get("size") or 0.0), 2.0)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_commitment_held_maker_orders_stay_live_across_quote_change(self):
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
            context = {"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}}
            first = manager.step({"t1": top_a}, competitiveness_context_by_token=context)
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
            second = manager.step({"t1": top_b}, competitiveness_context_by_token=context)
            self.assertEqual(second["open_orders"], 2)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_commitment_held_maker_orders_ignore_min_rest_requote_path(self):
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
            context = {"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}}
            first = manager.step({"t1": top_a}, competitiveness_context_by_token=context)
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
            second = manager.step({"t1": top_b}, competitiveness_context_by_token=context)
            self.assertEqual(second["open_orders"], 2)
            self.assertEqual(int(second["actions"]), 0)
            self.assertEqual(
                dict(second.get("maker_no_submission_reason_by_token", {})),
                {"t1": "maker_commitment_hold_active"},
            )
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_step_blocks_opposite_side_submit_while_maker_commitment_is_active(self):
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

            future_expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            gateway.place_order(
                OrderIntent(
                    token_id="t1",
                    side="BUY",
                    price=0.45,
                    size=5.0,
                    tif="GTC",
                    post_only=True,
                    reason="test",
                    submission_lane="maker",
                    commitment_hold_active=True,
                    commitment_hold_reason="late_window_commitment",
                    commitment_expiry_ts_utc=future_expiry,
                ),
                client_order_id="manual-buy",
            )
            top = BookTop(
                token_id="t1",
                ts_utc=utc_iso(),
                source="test",
                best_bid_price=0.45,
                best_bid_size=100.0,
                best_ask_price=0.55,
                best_ask_size=100.0,
            )
            summary = manager.step(
                {"t1": top},
                side_policy_by_token={"t1": "SELL_ONLY"},
                competitiveness_context_by_token={
                    "t1": {
                        "stage": "MAKER_TAKER_SELECTIVE",
                        "financial_posture_class": "NORMAL",
                        "sec_to_expiry": 18.0,
                    }
                },
            )
            self.assertEqual(summary["open_orders"], 1)
            self.assertEqual(len(gateway.get_open_orders()), 1)
            self.assertEqual(
                str((summary.get("maker_no_submission_reason_by_token") or {}).get("t1") or ""),
                "maker_commitment_hold_active",
            )
            self.assertEqual(self._read_event_rows(Path(tmp.name), event_type="order_cancel"), [])
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_commitment_held_maker_orders_remain_live_after_min_rest_elapsed(self):
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
            context = {"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}}
            first = manager.step({"t1": top_a}, competitiveness_context_by_token=context)
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
            second = manager.step({"t1": top_b}, competitiveness_context_by_token=context)
            self.assertEqual(second["open_orders"], 2)
            self.assertEqual(int(second["actions"]), 0)
            self.assertEqual(
                dict(second.get("maker_no_submission_reason_by_token", {})),
                {"t1": "maker_commitment_hold_active"},
            )
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

    def test_maker_no_submission_reason_surfaces_reduce_only_dust_below_min_order_size(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["min_order_size"] = 1.0

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=0.68)}
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
            summary = manager.step(
                {"t1": top},
                side_policy_by_token={"t1": "SELL_ONLY"},
                competitiveness_context_by_token={
                    "t1": self._historical_recovery_lineage_stage_info(
                        reduce_only_side_policy="SELL_ONLY",
                        reduce_only_size_cap_shares=0.68,
                        reduce_only_size_cap_below_min_order_size=True,
                    )
                },
            )
            self.assertEqual(summary["open_orders"], 1)
            self.assertEqual(int(summary["actions"]), 1)
            self.assertEqual(dict(summary.get("maker_no_submission_reason_by_token", {})), {})
            self.assertEqual(dict(summary.get("maker_no_submission_category_by_token", {})), {})
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
            first, first_reason = manager._place_order(
                doomed,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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
            second, second_reason = manager._place_order(
                valid,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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
            placed, reason = manager._place_order(
                crossing,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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
            first, first_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
            self.assertIsNone(first)
            self.assertEqual(first_reason, "post_only_reject")
            first_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(first_state.get("orders_used_accepted", -1)), 0)
            self.assertEqual(int(first_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertEqual(int(first_state.get("orders_transport_attempted_recent", -1)), 1)

            second, second_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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

    def test_submit_exception_releases_reservation_and_preserves_capacity(self):
        class _RuntimeRejectThenAcceptGateway(PaperGateway):
            def __init__(self) -> None:
                super().__init__()
                self._attempt = 0

            def place_order(self, intent: OrderIntent, client_order_id: str):  # type: ignore[override]
                self._attempt += 1
                if self._attempt == 1:
                    raise RuntimeError("simulated_transport_runtime_exception")
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

            gateway = _RuntimeRejectThenAcceptGateway()
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
            first, first_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
            self.assertIsNone(first)
            self.assertEqual(first_reason, "order_submit_exception")
            first_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(first_state.get("orders_used_accepted", -1)), 0)
            self.assertEqual(int(first_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertEqual(int(first_state.get("orders_transport_attempted_recent", -1)), 1)

            second, second_reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
            self.assertIsNotNone(second)
            self.assertIsNone(second_reason)
            second_state = risk.order_capacity_state(soft_limit_pct=1.0)
            self.assertEqual(int(second_state.get("orders_used_accepted", -1)), 1)
            self.assertEqual(int(second_state.get("orders_reserved_outstanding", -1)), 0)
            self.assertGreaterEqual(telemetry.counters.get("order_submit_failures", 0), 1)
            self.assertGreaterEqual(telemetry.counters.get("order_submission_released", 0), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_wallet_confirm_submission_failed_handles_cancel_exception_without_crash(self):
        class _CancelExceptionGateway(PaperGateway):
            def cancel_order(self, order_id: str) -> bool:  # type: ignore[override]
                raise RuntimeError("cancel runtime boom")

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 2

            gateway = _CancelExceptionGateway()
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
            with mock.patch.object(manager.wallet, "confirm_submission", return_value=False):
                placed, reason = manager._place_order(
                    intent,
                    top,
                    open_orders_for_token=[],
                    open_orders_total=0,
                    risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
                )
            self.assertIsNone(placed)
            self.assertEqual(reason, "wallet_confirm_submission_failed")
            self.assertGreaterEqual(int(telemetry.counters.get("wallet_halts", 0)), 1)
            self.assertGreaterEqual(int(telemetry.counters.get("wallet_confirm_submission_cancel_failures", 0)), 1)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_cancel_exception_logs_failure_and_preserves_wallet_lock(self):
        class _CancelExceptionGateway(PaperGateway):
            def cancel_order(self, order_id: str) -> bool:  # type: ignore[override]
                raise RuntimeError("cancel runtime boom")

        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 4

            gateway = _CancelExceptionGateway()
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
            order, reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
            self.assertIsNotNone(order)
            self.assertIsNone(reason)
            assert order is not None

            canceled = manager._cancel_order(order, "test_cancel_exception", request_origin="runner_shutdown")
            self.assertFalse(canceled)
            self.assertGreaterEqual(int(telemetry.counters.get("cancel_failures", 0)), 1)
            wallet_status = manager.wallet.status()
            self.assertGreater(float(wallet_status.get("order_lock_usdc", 0.0) or 0.0), 0.0)
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
            order, reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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
            order, reason = manager._place_order(
                intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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
            first, first_reason = manager._place_order(
                buy_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
            self.assertIsNotNone(first)
            self.assertIsNone(first_reason)
            second, second_reason = manager._place_order(
                sell_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=1,
                risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0},
            )
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

    def test_order_soft_throttle_no_longer_bypasses_for_maker_recovery_rows(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            runtime_cfg["order_rate_soft_limit_pct"] = 0.5
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["max_orders_per_min"] = 2
            risk_cfg[self._HISTORICAL_RECOVERY_RATE_RESERVED_SLOTS_FIELD] = 1

            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=1.0)}
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

            recovery_intent = OrderIntent(
                token_id="t1",
                side="SELL",
                price=0.56,
                size=1.0,
                tif="GTC",
                post_only=True,
                reason="legacy_lifecycle_probe",
                stage="MAKER_TAKER_SELECTIVE",
            )
            recovery_context = {
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "sec_to_expiry": 45.0,
                "reduce_only_size_cap_shares": 1.0,
            }

            placed_recovery, recovery_reason = manager._place_order(
                recovery_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context=recovery_context,
            )
            self.assertIsNotNone(placed_recovery)
            self.assertEqual(str(recovery_reason or ""), "")
            self.assertEqual(
                telemetry.counters.get(
                    self._HISTORICAL_ORDER_SOFT_THROTTLE_BYPASS_RECOVERY_COUNTER,
                    0,
                ),
                0,
            )

            events.close()
            events = None
            throttle_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "order_soft_throttle":
                        throttle_rows.append(payload)
            self.assertEqual(throttle_rows, [])
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
            placed, reject_reason = manager._place_order(
                rejected_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "lifecycle_phase": "prepare", "sec_to_expiry": 45.0},
            )
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
            placed_ok, accept_reason = manager._place_order(
                accepted_intent,
                top,
                open_orders_for_token=[],
                open_orders_total=0,
                risk_context={"submission_lane": "maker", "lifecycle_phase": "prepare", "sec_to_expiry": 45.0},
            )
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
            self.assertEqual(str((reject_basis or {}).get("lifecycle_phase") or ""), "prepare")
            self.assertEqual(str((submit_basis or {}).get("lifecycle_phase") or ""), "prepare")
            self.assertNotIn("stage", reject_basis or {})
            self.assertNotIn("stage", submit_basis or {})
            self.assertEqual(str(risk_reject_rows[-1].get("lifecycle_phase") or ""), "prepare")
            self.assertEqual(str(risk_reject_rows[-1].get("lifecycle_phase_source") or ""), "risk_decision_basis")
            self.assertIsNone(risk_reject_rows[-1].get("lifecycle_phase_unknown_reason"))
            self.assertNotIn("stage", risk_reject_rows[-1])
            self.assertNotIn("stage_source", risk_reject_rows[-1])
            self.assertNotIn("stage_unknown_reason", risk_reject_rows[-1])
            self.assertEqual(str(order_submit_rows[-1].get("lifecycle_phase") or ""), "prepare")
            self.assertEqual(str(order_submit_rows[-1].get("lifecycle_phase_source") or ""), "risk_decision_basis")
            self.assertIsNone(order_submit_rows[-1].get("lifecycle_phase_unknown_reason"))
            self.assertNotIn("stage", order_submit_rows[-1])
            self.assertNotIn("stage_source", order_submit_rows[-1])
            self.assertNotIn("stage_unknown_reason", order_submit_rows[-1])
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_sizing_reject_emits_lifecycle_phase_without_stage_family_fallback(self):
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
                stage="EXTREME_ONLY",
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
            self.assertEqual(str(row.get("lifecycle_phase") or ""), "prepare")
            self.assertEqual(str(row.get("lifecycle_phase_source") or ""), "intent_stage_compat")
            self.assertIsNone(row.get("lifecycle_phase_unknown_reason"))
            self.assertNotIn("stage", row)
            self.assertNotIn("stage_source", row)
            self.assertNotIn("stage_unknown_reason", row)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_sizing_reject_uses_risk_context_lifecycle_compat_when_intent_stage_missing(self):
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
                    risk_context={"stage": "EXTREME_ONLY", "submission_lane": "maker"},
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
            self.assertEqual(str(row.get("lifecycle_phase") or ""), "prepare")
            self.assertEqual(str(row.get("lifecycle_phase_source") or ""), "risk_context_stage_compat")
            self.assertIsNone(row.get("lifecycle_phase_unknown_reason"))
            self.assertNotIn("stage", row)
            self.assertNotIn("stage_source", row)
            self.assertNotIn("stage_unknown_reason", row)
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_dead_terminal_notional_fallback_hint_does_not_override_sizing_reject(self):
        tmp = tempfile.TemporaryDirectory()
        events = None
        try:
            runtime_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["runtime"])
            strategy_cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG["strategy"])
            strategy_cfg["execution_quality"]["enabled"] = False
            risk_cfg = self._risk_cfg_without_expiry_gate()
            risk_cfg["max_book_age_sec"] = 100.0
            risk_cfg["min_order_size"] = 5.0
            risk_cfg["reduce_only_terminal_min_notional_usd"] = 2.0
            gateway = PaperGateway()
            events = EventLogger(Path(tmp.name))
            telemetry = Telemetry()
            positions = {"t1": Position(token_id="t1", net_shares=4.5)}
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
                side="SELL",
                price=0.50,
                size=20.0,
                tif="GTC",
                post_only=True,
                reason="mm_quote:test",
                stage="MAKER_TAKER_SELECTIVE",
            )
            risk_context = {
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 20.0,
                "reduce_only_side": "SELL",
                "reduce_only_size_cap_shares": 4.0,
                "reduce_only_size_cap_below_min_order_size": True,
                "reduce_only_min_order_size_shares": 5.0,
            }
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
                    risk_context=risk_context,
                )
            self.assertIsNone(placed)
            self.assertEqual(str(reject_reason or ""), "sizing_reject")

            events.close()
            events = None
            submit_rows: list[dict] = []
            sizing_reject_rows: list[dict] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if str(payload.get("event_type") or "") == "order_submit":
                        submit_rows.append(payload)
                    if (
                        str(payload.get("event_type") or "") == "risk_reject"
                        and str(payload.get("reason") or "") == "size_notional_bounds"
                    ):
                        sizing_reject_rows.append(payload)
            self.assertEqual(submit_rows, [])
            self.assertTrue(sizing_reject_rows)
            self.assertEqual(dict(sizing_reject_rows[-1].get("size_resolution") or {}), {"forced": True})
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

    def test_step_tracked_ineligible_token_uses_explicit_cancel_reason_not_orphan(self):
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
            summary = manager.step(
                {},
                tracked_tokens={"t1", "t2"},
                tracked_token_cancel_reason_by_token={"t2": "maker_timing_gate_closed"},
            )
            self.assertEqual(summary["open_orders"], 0)
            self.assertEqual(gateway.get_open_orders(), [])
            cancel_rows = self._read_event_rows(Path(tmp.name), event_type="order_cancel")
            self.assertEqual(len(cancel_rows), 1)
            self.assertEqual(str(cancel_rows[0].get("reason") or ""), "maker_timing_gate_closed")
        finally:
            if events is not None:
                events.close()
            tmp.cleanup()

    def test_remove_token_order_if_present_logs_local_tracking_miss(self):
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

            token_orders: list[LiveOrder] = []
            order = LiveOrder(
                order_id="missing-order",
                token_id="t1",
                side="BUY",
                price=0.4,
                size=1.0,
                remaining_size=1.0,
                status="OPEN",
            )
            removed = manager._remove_token_order_if_present(  # pylint: disable=protected-access
                token_orders,
                order,
                remove_reason="unit_test",
            )
            self.assertFalse(removed)
            self.assertEqual(token_orders, [])
            self.assertGreaterEqual(int(telemetry.counters.get("token_order_local_remove_miss", 0)), 1)
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
            summary = manager.step(
                {"t1": top},
                competitiveness_context_by_token={"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}},
            )
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
            summary = manager.step(
                {"t1": top},
                competitiveness_context_by_token={"t1": {"stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 45.0}},
                max_actions_override=1,
            )
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
                reason=TAKER_CHAINLINK_REASON,
                stage="EXTREME_ONLY",
                decision_reference_ts_utc="2026-01-01T00:00:00.000Z",
                competitiveness_context={"stage": "EXTREME_ONLY"},
            )
            self.assertTrue(bool(outcome.get("submitted", False)))
            events.close()
            events = None

            stage_values: list[str] = []
            decision_to_submit_latency_ms: list[float] = []
            for path in sorted(Path(tmp.name).glob("events_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if str(row.get("event_type") or "") != "order_submit":
                        continue
                    if str(row.get("reason") or "").strip().lower() != TAKER_CHAINLINK_REASON:
                        continue
                    stage_values.append(str(row.get("lifecycle_phase") or ""))
                    decision_to_submit_latency_ms.append(float(row.get("decision_to_submit_latency_ms") or 0.0))

            self.assertEqual(stage_values, ["prepare"])
            self.assertEqual(len(decision_to_submit_latency_ms), 1)
            self.assertGreater(decision_to_submit_latency_ms[0], 0.0)
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

    def test_maker_notional_sizing_rounds_up_to_step_for_hard_min_notional_floor(self):
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
            sizing_cfg["rounding"] = "floor"
            sizing_cfg["price_source"] = "mid"
            sizing_cfg["share_step"] = 0.01
            sizing_cfg["maker_competitive_min_notional_usd"] = 100.0
            sizing_cfg["maker_competitive_max_notional_usd"] = 250.0
            sizing_cfg["maker_competitive_min_shares"] = 200.0
            sizing_cfg["maker_competitive_max_shares"] = 800.0
            sizing_cfg["maker_depth_target_min_ratio"] = 0.0
            sizing_cfg["maker_depth_target_max_ratio"] = 0.0
            sizing_cfg["maker_depth_target_ratio"] = 0.0
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
                best_bid_price=0.40,
                best_bid_size=2000.0,
                best_ask_price=0.41,
                best_ask_size=2000.0,
            )
            sized, details = manager._resolve_order_size_shares_with_details(  # pylint: disable=protected-access
                OrderIntent(token_id="t1", side="BUY", price=0.405, size=25.0, tif="GTC", post_only=True),
                top,
                notional_target_usd=5.0,
            )
            self.assertIsNotNone(sized)
            self.assertAlmostEqual(float(sized or 0.0), 246.92, places=6)
            self.assertGreaterEqual(float((details or {}).get("resolved_notional_usd") or 0.0), 100.0)
            self.assertIn(
                "maker_hard_min_notional_roundup_to_step",
                list((details or {}).get("size_decision_reasons") or []),
            )
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

    def test_paper_gateway_queue_proxy_is_removed_and_defaults_to_not_modeled(self):
        gateway = PaperGateway(
            {
                "paper_queue_position_mode": "not_modeled",
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
        self.assertAlmostEqual(float(fills[0].size), 100.0, places=6)
        self.assertEqual(
            str(fills[0].fill_policy_basis or ""),
            "visible_liquidity_top_of_book",
        )
        self.assertEqual(str(fills[0].paper_queue_position_mode or ""), "not_modeled")
        self.assertAlmostEqual(float(fills[0].paper_queue_fill_multiplier or 0.0), 1.0, places=6)
        self.assertAlmostEqual(float(fills[0].paper_maker_eligible_depth or 0.0), 100.0, places=6)
        self.assertAlmostEqual(float(fills[0].paper_maker_depth_consumption_ratio or 0.0), 1.0, places=6)

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
                reason=TAKER_CHAINLINK_REASON,
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
                reason=TAKER_CHAINLINK_REASON,
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_ladder_supplies_mid_before_pnl_and_loss_checks(self):
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
                    best_bid_price=0.43,
                    best_bid_size=50.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                valuation_state = runner._apply_valuation_controls(  # pylint: disable=protected-access
                    books={"t1": top},
                    phase="unit_test",
                )
                mids = dict(valuation_state.get("mid_by_token") or {})
                self.assertIn("t1", mids)
                self.assertIsInstance(mids.get("t1"), float)
                total_pnl, pnl_by_token = runner.risk.mark_to_market(mids)
                self.assertIn("t1", pnl_by_token)
                self.assertIsInstance(float(total_pnl), float)
                loss_decision = runner.risk.evaluate_loss_limits(mids)
                self.assertTrue(bool(loss_decision.allowed))
            finally:
                runner.events.close()
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
                self.assertEqual(list(recovered_state.get("held_unpriceable_non_defect_token_ids") or []), [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_apply_valuation_controls_tracks_hard_degraded_and_unpriceable_transitions(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["max_book_age_sec"] = 6.0
            cfg["risk"]["one_sided_quote_max_age_sec"] = 6.0
            cfg["risk"]["last_known_mid_max_age_sec"] = 2.0
            cfg["runtime"]["valuation_hard_degraded_clear_consecutive_healthy_cycles"] = 1
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=2.0)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    hard_state = runner._apply_valuation_controls(books={}, phase="test_hard")  # pylint: disable=protected-access
                self.assertTrue(bool(hard_state.get("valuation_hard_degraded", False)))
                self.assertEqual(runner._valuation_hard_degraded_enter_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._valuation_hard_degraded_clear_count, 0)  # pylint: disable=protected-access
                self.assertEqual(runner._held_unpriceable_started_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._held_unpriceable_recovered_count, 0)  # pylint: disable=protected-access

                recovery_top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.46,
                    best_bid_size=80.0,
                    best_ask_price=0.54,
                    best_ask_size=80.0,
                )
                with mock.patch("executor.time.monotonic", return_value=101.0):
                    recovered_state = runner._apply_valuation_controls(  # pylint: disable=protected-access
                        books={"t1": recovery_top},
                        phase="test_recovered",
                    )
                self.assertFalse(bool(recovered_state.get("valuation_hard_degraded", False)))
                self.assertEqual(runner._valuation_hard_degraded_enter_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._valuation_hard_degraded_clear_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._held_unpriceable_started_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._held_unpriceable_recovered_count, 1)  # pylint: disable=protected-access

                valuation_rows: list[dict] = []
                hard_transition_rows: list[dict] = []
                for path in sorted(Path(td).glob("events_*.jsonl")):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        line = str(line).strip()
                        if not line:
                            continue
                        payload = json.loads(line)
                        event_type = str(payload.get("event_type") or "")
                        if event_type == "valuation_degraded":
                            valuation_rows.append(payload)
                        elif event_type == "valuation_hard_degraded_transition":
                            hard_transition_rows.append(payload)
                self.assertTrue(valuation_rows)
                self.assertTrue(hard_transition_rows)
                first_row = valuation_rows[0]
                self.assertTrue(str(first_row.get("reason") or "").strip())
                self.assertTrue(str(first_row.get("reason_source") or "").strip())
                self.assertTrue(str(first_row.get("token_id_source") or "").strip())
                self.assertIsNotNone(first_row.get("token_id"))
                self.assertIn("hard_degraded", str(first_row.get("reason") or ""))
                for row in hard_transition_rows:
                    self.assertNotIn("dust_hard_degraded_exempt_count", row)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_apply_valuation_controls_uses_hard_degraded_clear_hysteresis(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["valuation_hard_degraded_clear_consecutive_healthy_cycles"] = 2
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=2.0)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    hard_state = runner._apply_valuation_controls(books={}, phase="test_hard")  # pylint: disable=protected-access
                self.assertTrue(bool(hard_state.get("valuation_hard_degraded", False)))
                self.assertEqual(runner._valuation_hard_degraded_enter_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._valuation_hard_degraded_clear_count, 0)  # pylint: disable=protected-access

                recovery_top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.46,
                    best_bid_size=80.0,
                    best_ask_price=0.54,
                    best_ask_size=80.0,
                )
                with mock.patch("executor.time.monotonic", return_value=101.0):
                    recovered_state_pending = runner._apply_valuation_controls(  # pylint: disable=protected-access
                        books={"t1": recovery_top},
                        phase="test_recovered_pending",
                    )
                self.assertTrue(bool(recovered_state_pending.get("valuation_hard_degraded", False)))
                self.assertEqual(runner._valuation_hard_degraded_clear_count, 0)  # pylint: disable=protected-access
                self.assertEqual(runner._valuation_hard_degraded_pending_healthy_cycles, 1)  # pylint: disable=protected-access

                with mock.patch("executor.time.monotonic", return_value=102.0):
                    recovered_state_clear = runner._apply_valuation_controls(  # pylint: disable=protected-access
                        books={"t1": recovery_top},
                        phase="test_recovered_clear",
                    )
                self.assertFalse(bool(recovered_state_clear.get("valuation_hard_degraded", False)))
                self.assertEqual(runner._valuation_hard_degraded_clear_count, 1)  # pylint: disable=protected-access
                self.assertEqual(runner._valuation_hard_degraded_pending_healthy_cycles, 0)  # pylint: disable=protected-access
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_apply_valuation_controls_dust_shadow_mode_emits_without_enforcing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["position_dust_shares_epsilon"] = 0.5
            cfg["risk"]["position_dust_notional_usd_epsilon"] = 2.0
            cfg["risk"]["position_dust_total_notional_usd_cap"] = 5.0
            cfg["risk"]["position_dust_token_count_cap"] = 4
            cfg["risk"]["position_dust_max_age_sec"] = 900.0
            cfg["risk"]["min_sec_to_expiry_for_new_exposure"] = 0.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=0.2)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    state = runner._apply_valuation_controls(books={}, phase="test_dust_shadow")  # pylint: disable=protected-access
                self.assertTrue(bool(state.get("valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("raw_valuation_hard_degraded", False)))
                self.assertEqual(
                    str((state.get("held_exposure_class_by_token") or {}).get("t1") or ""),
                    "DUST_QUARANTINED",
                )
                self.assertNotIn("held_dust_shadow_active", state)
                self.assertNotIn("held_dust_enforced_this_cycle", state)
                self.assertNotIn("held_dust_hard_degraded_exempt_count", state)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_apply_valuation_controls_dust_enforce_exempts_dust_only_hard_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["position_dust_shares_epsilon"] = 0.5
            cfg["risk"]["position_dust_notional_usd_epsilon"] = 2.0
            cfg["risk"]["position_dust_total_notional_usd_cap"] = 5.0
            cfg["risk"]["position_dust_token_count_cap"] = 4
            cfg["risk"]["position_dust_max_age_sec"] = 900.0
            cfg["risk"]["min_sec_to_expiry_for_new_exposure"] = 0.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=0.2)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    state = runner._apply_valuation_controls(books={}, phase="test_dust_enforce")  # pylint: disable=protected-access
                self.assertTrue(bool(state.get("valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("raw_valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("valuation_degraded", False)))
                self.assertNotIn("held_dust_shadow_active", state)
                self.assertNotIn("held_dust_enforced_this_cycle", state)
                self.assertNotIn("held_dust_hard_degraded_exempt_count", state)
                self.assertTrue(bool(runner.risk.valuation_degraded_state().get("hard_degraded", False)))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_apply_valuation_controls_dust_only_escalation_is_not_defect_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["held_unpriceable_escalation_sec"] = 0.5
            cfg["risk"]["position_dust_shares_epsilon"] = 0.5
            cfg["risk"]["position_dust_notional_usd_epsilon"] = 2.0
            cfg["risk"]["position_dust_total_notional_usd_cap"] = 5.0
            cfg["risk"]["position_dust_token_count_cap"] = 4
            cfg["risk"]["position_dust_max_age_sec"] = 900.0
            cfg["risk"]["min_sec_to_expiry_for_new_exposure"] = 0.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=0.2)
                runner._held_unpriceable_since_mono_by_token["t1"] = 100.0  # pylint: disable=protected-access
                with mock.patch("executor.time.monotonic", return_value=100.8):
                    state = runner._apply_valuation_controls(books={}, phase="test_dust_escalation")  # pylint: disable=protected-access
                self.assertTrue(bool(state.get("valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("raw_valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("held_unpriceable_escalation_active", False)))
                self.assertTrue(bool(state.get("held_unpriceable_defect_candidate", False)))
                self.assertEqual(
                    str(state.get("held_unpriceable_operator_action") or ""),
                    "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable",
                )
                self.assertEqual(
                    list(state.get("held_unpriceable_meaningful_escalation_token_ids") or []),
                    ["t1"],
                )
                self.assertTrue(bool(runner._held_unpriceable_defect_candidate))  # pylint: disable=protected-access
                self.assertEqual(  # pylint: disable=protected-access
                    list(runner._held_unpriceable_meaningful_escalation_token_ids),
                    ["t1"],
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_classifies_held_unpriceable_cause_taxonomy(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_pre", "t_post"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t_pre": utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=45)),
                "t_post": utc_iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=45)),
            }
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t_pre"] = Position(token_id="t_pre", net_shares=2.0)
                runner.risk.positions["t_post"] = Position(token_id="t_post", net_shares=-2.0)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    runner._held_unpriceable_since_mono_by_token["t_pre"] = 95.0  # pylint: disable=protected-access
                    runner._held_unpriceable_since_mono_by_token["t_post"] = 95.0  # pylint: disable=protected-access
                    state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                cause_by_token = dict(state.get("held_unpriceable_cause_by_token") or {})
                self.assertEqual(str(cause_by_token.get("t_pre") or ""), "preexpiry_ws_missing_or_unusable")
                self.assertEqual(str(cause_by_token.get("t_post") or ""), "postexpiry_market_retired")
                cause_counts = dict(state.get("held_unpriceable_cause_counts") or {})
                self.assertEqual(int(cause_counts.get("preexpiry_ws_missing_or_unusable") or 0), 1)
                self.assertEqual(int(cause_counts.get("postexpiry_market_retired") or 0), 1)
                self.assertEqual(int(cause_counts.get("unknown_data_gap") or 0), 0)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_allows_postexpiry_recent_404_dust_classification(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_post"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t_post": utc_iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=200))
            }
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["position_dust_shares_epsilon"] = 1.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t_post"] = Position(token_id="t_post", net_shares=-0.72)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    runner._held_unpriceable_since_mono_by_token["t_post"] = 95.0  # pylint: disable=protected-access
                    runner._held_ws_missing_or_unusable_refresh_next_mono_by_token["t_post"] = 130.0  # pylint: disable=protected-access
                    state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                self.assertEqual(
                    str((state.get("held_exposure_class_by_token") or {}).get("t_post") or ""),
                    "DUST_ELIGIBLE",
                )
                self.assertEqual(list(state.get("held_unpriceable_non_defect_token_ids") or []), [])
                detail = dict((state.get("held_exposure_detail_by_token") or {}).get("t_post") or {})
                self.assertFalse(bool(detail.get("unresolved_lifecycle_obligation", True)))
                self.assertTrue(bool(detail.get("postexpiry_retired_recent_ws_missing_or_unusable_dust_exempted", False)))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_allows_postexpiry_retired_dust_past_age_cap(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_post"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t_post": utc_iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1200))
            }
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["held_unpriceable_escalation_sec"] = 0.5
            cfg["risk"]["position_dust_shares_epsilon"] = 1.0
            cfg["risk"]["position_dust_max_age_sec"] = 900.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t_post"] = Position(token_id="t_post", net_shares=-0.72)
                runner._held_unpriceable_since_mono_by_token["t_post"] = 100.0  # pylint: disable=protected-access
                with mock.patch("executor.time.monotonic", return_value=1100.0):
                    runner._held_ws_missing_or_unusable_refresh_next_mono_by_token["t_post"] = 1130.0  # pylint: disable=protected-access
                    state = runner._apply_valuation_controls(books={}, phase="test_postexpiry_retired_dust")  # pylint: disable=protected-access
                self.assertTrue(bool(state.get("valuation_hard_degraded", False)))
                self.assertTrue(bool(state.get("raw_valuation_hard_degraded", False)))
                self.assertFalse(bool(state.get("held_unpriceable_defect_candidate", True)))
                self.assertEqual(
                    str((state.get("held_exposure_class_by_token") or {}).get("t_post") or ""),
                    "DUST_ELIGIBLE",
                )
                self.assertEqual(list(state.get("held_unpriceable_non_defect_token_ids") or []), ["t_post"])
                detail = dict((state.get("held_exposure_detail_by_token") or {}).get("t_post") or {})
                self.assertGreater(float(detail.get("dust_age_sec") or 0.0), 900.0)
                self.assertEqual(float(detail.get("dust_age_sec_for_classification")), 0.0)
                self.assertTrue(bool(detail.get("dust_age_gate_bypassed", False)))
                self.assertEqual(str(detail.get("dust_age_gate_bypass_reason") or ""), "postexpiry_retired_recent_ws_missing_or_unusable")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_keeps_preexpiry_recent_404_dust_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_pre"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t_pre": utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=45))
            }
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["position_dust_shares_epsilon"] = 1.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["t_pre"] = Position(token_id="t_pre", net_shares=0.72)
                with mock.patch("executor.time.monotonic", return_value=100.0):
                    runner._held_unpriceable_since_mono_by_token["t_pre"] = 95.0  # pylint: disable=protected-access
                    state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                self.assertEqual(
                    str((state.get("held_exposure_class_by_token") or {}).get("t_pre") or ""),
                    "DUST_QUARANTINED",
                )
                detail = dict((state.get("held_exposure_detail_by_token") or {}).get("t_pre") or {})
                self.assertTrue(bool(detail.get("unresolved_lifecycle_obligation", False)))
                self.assertFalse(bool(detail.get("postexpiry_retired_recent_ws_missing_or_unusable_dust_exempted", True)))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_prune_removed_tokens_preserves_watch_state_when_lifecycle_obligation_present(self):
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
                token_id = "flat-token-obligation"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=0.0)
                runner.last_midpoint_by_token[token_id] = 0.50
                runner.last_midpoint_ts_mono_by_token[token_id] = time.monotonic()
                runner._held_unpriceable_since_mono_by_token[token_id] = time.monotonic() - 1.0  # pylint: disable=protected-access

                runner._prune_removed_tokens(old_set={token_id}, active_set=set())

                self.assertIn(token_id, runner.last_midpoint_by_token)
                self.assertIn(token_id, runner.last_midpoint_ts_mono_by_token)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_held_only_ws_missing_suppresses_forced_target_refresh(self):
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
                    outcome = runner._handle_ws_missing_or_unusable_tokens(  # pylint: disable=protected-access
                        missing_or_unusable_tokens=[held],
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
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_ws_missing_or_unusable", 0)),
                    1,
                )
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_ws_missing_or_unusable", 0)), 0)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_held_only_ws_missing_can_force_recovery_refresh_when_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["chainlink"]["enabled"] = False
            cfg["runtime"]["held_ws_missing_or_unusable_refresh_interval_sec"] = 60.0
            cfg["runtime"]["held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec"] = 10.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                held = "held-token"
                runner.risk.positions[held] = Position(token_id=held, net_shares=1.0)
                runner._held_unpriceable_since_mono_by_token[held] = time.monotonic() - 45.0  # pylint: disable=protected-access
                with mock.patch.object(runner, "_refresh_targets") as refresh_mock:
                    outcome = runner._handle_ws_missing_or_unusable_tokens(  # pylint: disable=protected-access
                        missing_or_unusable_tokens=[held],
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
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_ws_missing_or_unusable", 0)), 1)
                self.assertEqual(
                    int(
                        runner.telemetry.counters.get(
                            "target_refresh_forced_held_ws_missing_or_unusable_recovery",
                            0,
                        )
                    ),
                    1,
                )
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_ws_missing_or_unusable", 0)),
                    0,
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_mixed_ws_missing_refreshes_only_non_held_tokens(self):
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
                    outcome = runner._handle_ws_missing_or_unusable_tokens(  # pylint: disable=protected-access
                        missing_or_unusable_tokens=[held, other],
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
                self.assertEqual(int(runner.telemetry.counters.get("target_refresh_forced_ws_missing_or_unusable", 0)), 1)
                self.assertEqual(
                    int(runner.telemetry.counters.get("target_refresh_suppressed_held_ws_missing_or_unusable", 0)),
                    0,
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_valuation_state_tags_ws_missing_or_unusable_on_hard_degraded_reason(self):
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
                runner._held_unpriceable_since_mono_by_token[token_id] = time.monotonic() - 8.0  # pylint: disable=protected-access

                state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                reasons = [str(x) for x in list(state.get("degraded_reasons") or [])]
                self.assertTrue(any("hard_degraded:t1:" in reason for reason in reasons))
                self.assertTrue(any("held_ws_missing_or_unusable_age_sec=" in reason for reason in reasons))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_ws_quote_unusable_for_held_valuation_requires_missing_mid_and_required_side(self):
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
                long_token = "held-long"
                short_token = "held-short"
                flat_token = "flat-token"
                runner.risk.positions[long_token] = Position(token_id=long_token, net_shares=2.0)
                runner.risk.positions[short_token] = Position(token_id=short_token, net_shares=-2.0)
                runner.risk.positions[flat_token] = Position(token_id=flat_token, net_shares=0.0)

                ask_only = BookTop(
                    token_id=long_token,
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=None,
                    best_bid_size=None,
                    best_ask_price=0.62,
                    best_ask_size=50.0,
                )
                bid_only = BookTop(
                    token_id=short_token,
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.38,
                    best_bid_size=50.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                long_usable = BookTop(
                    token_id=long_token,
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.41,
                    best_bid_size=25.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )

                self.assertTrue(
                    runner._ws_quote_unusable_for_held_valuation(token_id=long_token, top=ask_only),  # pylint: disable=protected-access
                )
                self.assertTrue(
                    runner._ws_quote_unusable_for_held_valuation(token_id=short_token, top=bid_only),  # pylint: disable=protected-access
                )
                self.assertFalse(
                    runner._ws_quote_unusable_for_held_valuation(token_id=long_token, top=long_usable),  # pylint: disable=protected-access
                )
                self.assertFalse(
                    runner._ws_quote_unusable_for_held_valuation(token_id=flat_token, top=ask_only),  # pylint: disable=protected-access
                )
                self.assertFalse(
                    runner._ws_quote_unusable_for_held_valuation(token_id="no-position", top=ask_only),  # pylint: disable=protected-access
                )
            finally:
                runner.events.close()
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_sync_book_feed_watch_tokens_resets_bootstrap_and_logs_on_watch_change(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._last_book_feed_watch_token_ids = ["old-token"]  # pylint: disable=protected-access
                with mock.patch.object(
                    runner, "_transport_watch_token_ids", return_value=["new-token-a", "new-token-b"]
                ), mock.patch.object(runner.book_feed, "update_token_ids") as update_mock, mock.patch.object(
                    runner, "_reset_ws_slo_bootstrap"
                ) as reset_mock, mock.patch.object(runner.events, "log_event") as log_mock:
                    runner._sync_book_feed_watch_tokens()  # pylint: disable=protected-access

                update_mock.assert_called_once_with(["new-token-a", "new-token-b"])
                reset_mock.assert_called_once_with(
                    reason="book_feed_watch_tokens_updated",
                    activate_grace=True,
                )
                log_mock.assert_called_once()
                self.assertEqual(log_mock.call_args.args[0], "book_feed_watch_tokens_updated")
                payload = dict(log_mock.call_args.args[1])
                self.assertEqual(payload.get("old_token_count"), 1)
                self.assertEqual(payload.get("new_token_count"), 2)
                self.assertEqual(payload.get("old_token_ids"), ["old-token"])
                self.assertEqual(payload.get("new_token_ids"), ["new-token-a", "new-token-b"])
                self.assertEqual(payload.get("authoritative_active_token_ids"), ["t1"])
                self.assertEqual(payload.get("active_watch_addition_token_ids"), ["t1"])
                self.assertTrue(bool(payload.get("ws_slo_grace_applied")))
                self.assertEqual(runner._last_book_feed_watch_token_ids, ["new-token-a", "new-token-b"])  # pylint: disable=protected-access
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_sync_book_feed_watch_tokens_no_bootstrap_reset_when_watch_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._last_book_feed_watch_token_ids = ["same-token"]  # pylint: disable=protected-access
                with mock.patch.object(
                    runner, "_transport_watch_token_ids", return_value=["same-token"]
                ), mock.patch.object(runner.book_feed, "update_token_ids") as update_mock, mock.patch.object(
                    runner, "_reset_ws_slo_bootstrap"
                ) as reset_mock, mock.patch.object(runner.events, "log_event") as log_mock:
                    runner._sync_book_feed_watch_tokens()  # pylint: disable=protected-access

                update_mock.assert_called_once_with(["same-token"])
                reset_mock.assert_not_called()
                log_mock.assert_not_called()
                self.assertEqual(runner._last_book_feed_watch_token_ids, ["same-token"])  # pylint: disable=protected-access
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_sync_book_feed_watch_tokens_lifecycle_only_change_does_not_apply_active_grace(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._last_book_feed_watch_token_ids = ["t1"]  # pylint: disable=protected-access
                runner.risk.positions["held-token"] = Position(token_id="held-token", net_shares=2.0)
                with mock.patch.object(runner, "_reset_ws_slo_bootstrap") as reset_mock, mock.patch.object(
                    runner.events, "log_event"
                ) as log_mock:
                    runner._sync_book_feed_watch_tokens()  # pylint: disable=protected-access

                reset_mock.assert_called_once_with(
                    reason="book_feed_watch_tokens_updated",
                    activate_grace=False,
                )
                payload = dict(log_mock.call_args.args[1])
                self.assertEqual(payload.get("authoritative_active_token_ids"), ["t1"])
                self.assertEqual(payload.get("active_watch_addition_token_ids"), [])
                self.assertEqual(payload.get("new_token_ids"), ["held-token", "t1"])
                self.assertFalse(bool(payload.get("ws_slo_grace_applied")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_reconcile_pair_authority_promotes_pending_without_ws_slo_grace(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._challenger_token_ids = ["yes1", "no1"]  # pylint: disable=protected-access
                with mock.patch.object(runner, "_pair_tokens_market_valid", return_value=True), mock.patch.object(
                    runner, "_reset_ws_slo_bootstrap"
                ) as reset_mock:
                    runner._reconcile_pair_authority()  # pylint: disable=protected-access

                self.assertEqual(runner.token_ids, ["yes1", "no1"])
                self.assertEqual(runner._challenger_token_ids, [])  # pylint: disable=protected-access
                reset_mock.assert_called_once_with(
                    reason="owned_market_promoted_from_challenger",
                    activate_grace=False,
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_refresh_targets_non_actionable_pair_becomes_pending_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                result = type(
                    "DiscoveryStub",
                    (),
                    {
                        "token_ids": ["yes1", "no1"],
                        "candidate_pairs_token_ids": [["yes1", "no1"]],
                        "pairs_selected": 1,
                        "scanned_markets": 100,
                        "fee_eligible_markets": 10,
                        "contract_rejected_pairs": 0,
                        "allowlist_enabled": False,
                        "allowlist_rejected_pairs": 0,
                        "token_expiry_utc_by_token": {
                            "yes1": "2030-01-01T00:05:00.000Z",
                            "no1": "2030-01-01T00:05:00.000Z",
                        },
                        "token_side_by_token": {"yes1": "YES", "no1": "NO"},
                        "token_strike_by_token": {"yes1": 50000.0, "no1": 50000.0},
                        "token_market_key_by_token": {"yes1": "mk1", "no1": "mk1"},
                    },
                )()
                with mock.patch.object(runner.discovery, "discover", return_value=result):
                    runner._refresh_targets(force=True)  # pylint: disable=protected-access

                self.assertEqual(runner.token_ids, ["yes1", "no1"])
                self.assertEqual(runner._challenger_token_ids, [])  # pylint: disable=protected-access
                self.assertEqual(set(runner._valuation_watch_token_ids()), {"yes1", "no1"})  # pylint: disable=protected-access
                self.assertEqual(
                    set(runner._transport_watch_token_ids()),  # pylint: disable=protected-access
                    {"yes1", "no1"},
                )
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_active_targets"), 2.0)
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_challenger_token_count"), 0.0)
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_standdown"), 0.0)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_manager_tracked_token_ids_excludes_pending_but_keeps_lifecycle_watch(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["active-yes", "active-no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._challenger_token_ids = ["future-yes", "future-no"]  # pylint: disable=protected-access
                runner.risk.positions["held-token"] = Position(token_id="held-token", net_shares=1.0)
                tracked = runner._manager_tracked_token_ids()  # pylint: disable=protected-access
                self.assertEqual(tracked, {"active-yes", "active-no", "held-token"})
            finally:
                runner.events.close()
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
                runner._held_ws_missing_or_unusable_refresh_next_mono_by_token[token_id] = time.monotonic() + 30.0  # pylint: disable=protected-access

                runner._prune_removed_tokens(old_set={token_id}, active_set=set())

                self.assertIn(token_id, runner.last_midpoint_by_token)
                self.assertIn(token_id, runner.last_midpoint_ts_mono_by_token)
                self.assertIn(token_id, runner._held_ws_missing_or_unusable_refresh_next_mono_by_token)  # pylint: disable=protected-access
                self.assertIn(token_id, runner.risk.positions)
            finally:
                runner.events.close()
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

                runner._prune_removed_tokens(old_set={token_id}, active_set=set())

                self.assertNotIn(token_id, runner.last_midpoint_by_token)
                self.assertNotIn(token_id, runner.last_midpoint_ts_mono_by_token)
                self.assertNotIn(token_id, runner._held_ws_missing_or_unusable_refresh_next_mono_by_token)  # pylint: disable=protected-access
                self.assertNotIn(token_id, runner.risk.positions)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_builds_chainlink_fair_probability_and_canonical_taker_only_snipes_inside_final_window(self):
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
            cfg["taker"]["require_lag_verification"] = True
            cfg["latency_verifier"]["min_samples"] = 1
            cfg["latency_verifier"]["hit_threshold_ms"] = 1.0
            cfg["latency_verifier"]["armed_min_median_ms"] = 1.0
            cfg["latency_verifier"]["armed_min_hit_rate"] = 1.0
            cfg["latency_verifier"]["probation_min_median_ms"] = 1.0
            cfg["latency_verifier"]["probation_min_hit_rate"] = 1.0
            cfg["latency_verifier"]["arm_consecutive_cycles"] = 1
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["order_size"] = 5.0
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
                taker_ctx = runner._taker_context()
                self.assertTrue(taker_ctx["active"])
                out_closed = runner._run_taker(
                    books=books,
                    fair_probability_by_token=fair,
                    token_ids=["t1"],
                    stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 18.0}},
                    oracle_tick_age_sec=0.0,
                    latency_snapshot=latency_snapshot,
                    lag_verified_token_ids=["t1"],
                )
                self.assertEqual(out_closed["submitted"], 0)
                out_live = runner._run_taker(
                    books=books,
                    fair_probability_by_token=fair,
                    token_ids=["t1"],
                    stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0}},
                    oracle_tick_age_sec=0.0,
                    latency_snapshot=latency_snapshot,
                    lag_verified_token_ids=["t1"],
                )
                self.assertGreaterEqual(out_live["submitted"], 1)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_taker_fair_probability_map_does_not_inherit_maker_lag_gate(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t-maker", "t-taker"]
            expiry = utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t-maker": expiry,
                "t-taker": expiry,
            }
            cfg["targets"]["token_side_by_token"] = {"t-maker": "YES", "t-taker": "YES"}
            cfg["targets"]["token_strike_by_token"] = {"t-maker": 65000.0, "t-taker": 65000.0}
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["require_armed_for_maker"] = True
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                books = {
                    token_id: BookTop(
                        token_id=token_id,
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.45,
                        best_bid_size=100,
                        best_ask_price=0.55,
                        best_ask_size=100,
                    )
                    for token_id in ["t-maker", "t-taker"]
                }
                latency_snapshot = LatencySnapshot(
                    state="armed",
                    previous_state="armed",
                    changed=False,
                    reason="ok",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=2,
                    median_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    hit_rate=runner.latency_verifier.armed_min_hit_rate,
                    armed=True,
                    probation=False,
                    disarmed=False,
                )

                with mock.patch.object(runner, "_lag_verified", side_effect=lambda token_id: token_id == "t-maker"):
                    maker_fair = runner._build_fair_probability_map(  # pylint: disable=protected-access
                        books,
                        latency_snapshot=latency_snapshot,
                        scope="maker",
                    )
                    taker_fair = runner._build_fair_probability_map(  # pylint: disable=protected-access
                        books,
                        latency_snapshot=latency_snapshot,
                        scope="taker",
                    )

                self.assertIn("t-maker", maker_fair)
                self.assertNotIn("t-taker", maker_fair)
                self.assertIn("t-maker", taker_fair)
                self.assertIn("t-taker", taker_fair)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_taker_fair_probability_map_still_fails_closed_on_stale_oracle(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["token_expiry_utc_by_token"] = {
                "t1": utc_iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
            }
            cfg["targets"]["token_side_by_token"] = {"t1": "YES"}
            cfg["targets"]["token_strike_by_token"] = {"t1": 65000.0}
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=65100.0,
                    source_ts_utc=None,
                    received_ts_utc=utc_iso(),
                    received_monotonic=(
                        time.monotonic() - runner.doctrine_oracle_max_tick_age_sec - 0.5
                    ),
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._latest_by_symbol["btc/usd"] = tick  # pylint: disable=protected-access
                books = {
                    "t1": BookTop(
                        token_id="t1",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.45,
                        best_bid_size=100,
                        best_ask_price=0.55,
                        best_ask_size=100,
                    )
                }
                latency_snapshot = LatencySnapshot(
                    state="armed",
                    previous_state="armed",
                    changed=False,
                    reason="ok",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=1,
                    median_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    hit_rate=runner.latency_verifier.armed_min_hit_rate,
                    armed=True,
                    probation=False,
                    disarmed=False,
                )

                taker_fair = runner._build_fair_probability_map(  # pylint: disable=protected-access
                    books,
                    latency_snapshot=latency_snapshot,
                    scope="taker",
                )

                self.assertEqual(taker_fair, {})
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_taker_fair_probability_map_rejects_timestamp_like_strike_near_expiry(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            expiry_dt = dt.datetime(2026, 4, 24, 15, 55, 0, tzinfo=dt.timezone.utc)
            cfg["targets"]["token_expiry_utc_by_token"] = {"t1": utc_iso(expiry_dt)}
            cfg["targets"]["token_side_by_token"] = {"t1": "NO"}
            cfg["targets"]["token_strike_by_token"] = {"t1": float(expiry_dt.timestamp() - 300.0)}
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=78000.0,
                    source_ts_utc=None,
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic(),
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._latest_by_symbol["btc/usd"] = tick  # pylint: disable=protected-access
                books = {
                    "t1": BookTop(
                        token_id="t1",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.45,
                        best_bid_size=100,
                        best_ask_price=0.55,
                        best_ask_size=100,
                    )
                }
                latency_snapshot = LatencySnapshot(
                    state="armed",
                    previous_state="armed",
                    changed=False,
                    reason="ok",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=1,
                    median_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    hit_rate=runner.latency_verifier.armed_min_hit_rate,
                    armed=True,
                    probation=False,
                    disarmed=False,
                )
                taker_fair = runner._build_fair_probability_map(  # pylint: disable=protected-access
                    books,
                    latency_snapshot=latency_snapshot,
                    scope="taker",
                )
                self.assertEqual(taker_fair, {})
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_taker_fair_probability_map_uses_open_anchor_when_static_strike_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            expiry_dt = dt.datetime(2026, 4, 24, 15, 55, 0, tzinfo=dt.timezone.utc)
            anchor_dt = expiry_dt - dt.timedelta(minutes=5)
            cfg["targets"]["token_expiry_utc_by_token"] = {"t1": utc_iso(expiry_dt)}
            cfg["targets"]["token_side_by_token"] = {"t1": "YES"}
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner._apply_token_open_anchor_map({"t1": utc_iso(anchor_dt)}, source="test")  # pylint: disable=protected-access
                anchor_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=65000.0,
                    source_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=200)),
                    received_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=250)),
                    received_monotonic=time.monotonic() - 2.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                latest_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=65150.0,
                    source_ts_utc=utc_iso(expiry_dt - dt.timedelta(seconds=20)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic(),
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(anchor_tick)  # pylint: disable=protected-access
                runner.chainlink._ingest_tick(latest_tick)  # pylint: disable=protected-access
                books = {
                    "t1": BookTop(
                        token_id="t1",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.45,
                        best_bid_size=100,
                        best_ask_price=0.55,
                        best_ask_size=100,
                    )
                }
                latency_snapshot = LatencySnapshot(
                    state="armed",
                    previous_state="armed",
                    changed=False,
                    reason="ok",
                    sample_count=runner.latency_verifier.min_samples,
                    token_count=1,
                    median_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p90_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    p95_lag_ms=runner.latency_verifier.armed_min_median_ms,
                    hit_rate=runner.latency_verifier.armed_min_hit_rate,
                    armed=True,
                    probation=False,
                    disarmed=False,
                )
                taker_fair = runner._build_fair_probability_map(  # pylint: disable=protected-access
                    books,
                    latency_snapshot=latency_snapshot,
                    scope="taker",
                )
                self.assertIn("t1", taker_fair)
                self.assertGreater(float(taker_fair["t1"]), 0.5)
            finally:
                runner.events.close()
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_prioritizes_highest_edge(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1", "t2"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["min_edge"] = 0.001
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
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1", "t2"],
                        stage_info_by_token={
                            "t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t2": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1", "t2"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(picked, ["t2"])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_blocks_negative_edge_same_token_sell(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=AssertionError("negative-edge normal taker SELL should be blocked"),
                ):
                    out = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={"t1": 0.40},
                        token_ids=["t1"],
                        stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0}},
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertIn("complement_token_mapping_unavailable", emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_routes_negative_edge_to_complement_buy(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.69,
                        best_bid_size=100.0,
                        best_ask_price=0.71,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }
                fair = {"t_yes": 0.40, "t_no": 0.60}
                placed: list[dict[str, object]] = []
                edge_evals: list[dict[str, object]] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed.append(dict(kwargs))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t-no"}

                def _capture_edge_eval(**kwargs):
                    edge_evals.append(dict(kwargs))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["submitted_token_ids"], ["t_no"])
                self.assertEqual(len(placed), 1)
                self.assertEqual(str(placed[0].get("token_id") or ""), "t_no")
                self.assertEqual(str(placed[0].get("side") or ""), "BUY")
                self.assertEqual(
                    str(placed[0].get("target_ref") or ""),
                    str(runner._target_ref_for_token("t_no") or ""),
                )
                self.assertTrue(
                    any(
                        str(payload.get("token_id") or "") == "t_no"
                        and str(payload.get("action_taken") or "") == "taker"
                        for payload in edge_evals
                    )
                )
                self.assertTrue(
                    any(
                        str(payload.get("token_id") or "") == "t_no"
                        and str(payload.get("target_ref") or "") == str(runner._target_ref_for_token("t_no") or "")
                        and str(payload.get("source_target_ref") or "") == str(runner._target_ref_for_token("t_yes") or "")
                        for payload in edge_evals
                    )
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_latches_same_market_window_after_first_fill(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["per_token_cooldown_sec"] = 0.0
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                placed: list[dict[str, object]] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed.append(dict(kwargs))
                    return {"submitted": True, "fills_accepted": 1, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    first = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={"t1": 0.60},
                        token_ids=["t1"],
                        stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0}},
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                    second = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={"t1": 0.60},
                        token_ids=["t1"],
                        stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0}},
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )

                self.assertEqual(first["submitted"], 1)
                self.assertEqual(second["submitted"], 0)
                self.assertEqual(len(placed), 1)
                self.assertIn("taker_window_already_submitted", emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_does_not_cancel_sibling_open_orders_after_fill(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.49,
                        best_bid_size=100.0,
                        best_ask_price=0.51,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 1, "order_id": "ord-t-yes"},
                ), mock.patch.object(
                    runner,
                    "_open_order_token_ids",
                    side_effect=[set(), {"t_no"}, {"t_no"}, {"t_no"}],
                ), mock.patch.object(
                    runner.manager,
                    "cancel_orders_for_tokens",
                    return_value=1,
                ) as cancel_mock:
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token={"t_yes": 0.60, "t_no": 0.40},
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )

                self.assertEqual(out["submitted"], 1)
                cancel_mock.assert_not_called()
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_allows_submit_with_sibling_inventory_present(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                runner.risk.positions["t_no"] = Position(token_id="t_no", net_shares=-20.0)
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.49,
                        best_bid_size=100.0,
                        best_ask_price=0.51,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t-yes"},
                ), mock.patch.object(
                    runner,
                    "_emit_edge_evaluation",
                    side_effect=_capture_edge_eval,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token={"t_yes": 0.60, "t_no": 0.40},
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )
                self.assertEqual(out["submitted"], 1)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_blocks_complement_buy_when_route_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = False
            cfg["taker"]["competitiveness"]["allow_complement_buy_route"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.69,
                        best_bid_size=100.0,
                        best_ask_price=0.71,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }
                fair = {"t_yes": 0.40, "t_no": 0.60}
                emitted_block_reasons: list[str] = []
                placed: list[dict[str, object]] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed.append(dict(kwargs))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-direct"}

                with mock.patch.object(
                    runner,
                    "_emit_edge_evaluation",
                    side_effect=_capture_edge_eval,
                ), mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )
                self.assertIn("complement_route_disabled_pending_validation", emitted_block_reasons)
                if placed:
                    self.assertEqual(str(placed[0].get("token_id") or ""), "t_no")
                    self.assertEqual(str(placed[0].get("side") or ""), "BUY")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_routes_negative_edge_to_complement_buy_with_competitiveness_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["competitiveness"]["enabled"] = True
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.69,
                        best_bid_size=100.0,
                        best_ask_price=0.71,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }
                fair = {"t_yes": 0.40, "t_no": 0.60}
                placed: list[dict[str, object]] = []
                decision_rows: list[dict[str, object]] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed.append(dict(kwargs))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t-no"}

                original_log_event = runner.events.log_event

                def _capture_log_event(event_type, payload):
                    if str(event_type) == EVENT_TAKER_DECISION:
                        decision_rows.append(dict(payload))
                    return original_log_event(event_type, payload)

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_log_event,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )
                self.assertGreaterEqual(out["submitted"], 0)
                self.assertTrue(
                    any(
                        str(row.get("token_id") or "") == "t_no"
                        and str(row.get("normal_taker_side_class") or "") == "complement_buy"
                        for row in decision_rows
                    )
                )
                if placed:
                    self.assertEqual(str(placed[0].get("token_id") or ""), "t_no")
                    self.assertEqual(str(placed[0].get("side") or ""), "BUY")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_complement_buy_uses_source_token_score(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["target_usd"] = 150.0
            cfg["taker"]["competitiveness"]["enabled"] = True
            cfg["taker"]["competitiveness"]["allow_complement_buy_route"] = True
            cfg["taker"]["competitiveness"]["hard_min_target_usd"] = 20.0
            cfg["taker"]["competitiveness"]["dynamic_size_target_usd_cap"] = 150.0
            cfg["latency_verifier"]["score_enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_side_by_token = {"t_yes": "YES", "t_no": "NO"}
                runner.token_market_key_by_token = {
                    "t_yes": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t_no": "cond-1|2026-04-24T00:05:00Z|95000|NO",
                }
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.69,
                        best_bid_size=100.0,
                        best_ask_price=0.71,
                        best_ask_size=100.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.44,
                        best_bid_size=100.0,
                        best_ask_price=0.46,
                        best_ask_size=100.0,
                    ),
                }
                fair = {"t_yes": 0.40, "t_no": 0.60}
                placed: list[dict[str, object]] = []
                emitted_block_reasons: list[str] = []
                decision_rows: list[dict[str, object]] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed.append(dict(kwargs))
                    competitiveness_context = dict(kwargs.get("competitiveness_context") or {})
                    self.assertAlmostEqual(
                        float(competitiveness_context.get("normal_taker_source_token_score") or 0.0),
                        0.95,
                        places=9,
                    )
                    self.assertAlmostEqual(
                        float(competitiveness_context.get("normal_taker_submit_token_score") or 0.0),
                        0.0,
                        places=9,
                    )
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-complement"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                original_log_event = runner.events.log_event

                def _capture_log_event(event_type, payload):
                    if str(event_type) == EVENT_TAKER_DECISION:
                        decision_rows.append(dict(payload))
                    return original_log_event(event_type, payload)

                def _fake_token_score(token_id: str) -> float:
                    return 0.95 if str(token_id or "") == "t_yes" else 0.0

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(
                    runner,
                    "_emit_edge_evaluation",
                    side_effect=_capture_edge_eval,
                ), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_log_event,
                ), mock.patch.object(
                    runner.latency_verifier,
                    "token_score",
                    side_effect=_fake_token_score,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t_yes", "t_no"],
                        stage_info_by_token={
                            "t_yes": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                            "t_no": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0},
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t_yes", "t_no"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(len(placed), 1)
                self.assertEqual(str(placed[0].get("token_id") or ""), "t_no")
                self.assertEqual(str(placed[0].get("side") or ""), "BUY")
                self.assertNotIn("token_score_below_taker_min", emitted_block_reasons)
                complement_rows = [
                    row
                    for row in decision_rows
                    if str(row.get("token_id") or "") == "t_no"
                    and str(row.get("normal_taker_side_class") or "") == "complement_buy"
                ]
                self.assertTrue(bool(complement_rows))
                self.assertAlmostEqual(float(complement_rows[-1].get("confidence_score") or 0.0), 0.95, places=9)
                self.assertAlmostEqual(
                    float(complement_rows[-1].get("normal_taker_source_token_score") or 0.0),
                    0.95,
                    places=9,
                )
                self.assertAlmostEqual(
                    float(complement_rows[-1].get("normal_taker_submit_token_score") or 0.0),
                    0.0,
                    places=9,
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_blocks_meaningless_visible_fill_when_ratio_below_floor(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["target_usd"] = 150.0
            cfg["strategy"]["max_order_size"] = 8000.0
            cfg["sizing"]["max_usd"] = 351.0
            cfg["wallet"]["max_notional_per_order_usdc"] = 351.0
            cfg["risk"]["max_order_size"] = 8000.0
            cfg["risk"]["max_abs_position_shares"] = 8000.0
            cfg["taker"]["competitiveness"]["enabled"] = True
            cfg["taker"]["competitiveness"]["hard_min_target_usd"] = 150.0
            cfg["taker"]["competitiveness"]["dynamic_size_target_usd_cap"] = 150.0
            cfg["taker"]["competitiveness"]["final_window_sec"] = 60.0
            cfg["taker"]["competitiveness"]["min_visible_fill_ratio"] = 0.5
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                books = {
                    "t1": BookTop(
                        token_id="t1",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.60,
                        best_bid_size=100.0,
                        best_ask_price=0.64,
                        best_ask_size=6.0,
                    )
                }
                decision_rows: list[dict[str, object]] = []

                def _capture_log_event(event_type, payload):
                    if str(event_type) == EVENT_TAKER_DECISION:
                        decision_rows.append(dict(payload))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=AssertionError("tiny visible fill should be blocked before submit"),
                ), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_log_event,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token={"t1": 0.999},
                        token_ids=["t1"],
                        stage_info_by_token={"t1": {"stage": "EXTREME_ONLY", "sec_to_expiry": 5.0}},
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertTrue(
                    any(
                        str(row.get("block_reason") or "") == "taker_visible_fill_ratio_below_min"
                        for row in decision_rows
                    )
                )
                blocked_rows = [
                    row
                    for row in decision_rows
                    if str(row.get("block_reason") or "") == "taker_visible_fill_ratio_below_min"
                ]
                self.assertTrue(bool(blocked_rows))
                self.assertAlmostEqual(float(blocked_rows[-1].get("visible_fill_notional_usd") or 0.0), 3.84, places=9)
                self.assertGreater(float(blocked_rows[-1].get("target_usd_resolved") or 0.0), 0.0)
                self.assertAlmostEqual(
                    float(blocked_rows[-1].get("visible_fill_ratio") or 0.0),
                    float(blocked_rows[-1].get("visible_fill_notional_usd") or 0.0)
                    / float(blocked_rows[-1].get("target_usd_resolved") or 1.0),
                    places=9,
                )
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_does_not_force_dead_recovery_side_hint(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}  # positive edge would normally pick BUY
                submitted_sides: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
                self.assertEqual(submitted_sides, ["BUY"])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_respects_lag_verification_when_dead_recovery_hints_are_present(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["taker"]["require_lag_verification"] = True
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=[],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("token_lag_not_verified", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_remains_blocked_in_non_normal_mode_even_with_dead_recovery_hints(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        mode_state=MODE_CAUTIOUS,
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("operating_mode_non_normal", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_non_recovery_still_blocked_in_non_normal_mode(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=AssertionError("non-recovery taker submit should be blocked in non-normal mode"),
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": {
                                "stage": "MAKER_TAKER_SELECTIVE",
                                "sec_to_expiry": 45.0,
                                "taker_gate_open": True,
                                "reduce_only_recovery_active": False,
                            }
                        },
                        mode_state=MODE_CAUTIOUS,
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertIn("operating_mode_non_normal", emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_uses_normal_one_sided_block_when_dead_recovery_hints_are_present(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.5  # should be bypassed in recovery mode
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,  # SELL reduce-only touch is available
                    best_bid_size=100.0,
                    best_ask_price=None,  # midpoint unavailable
                    best_ask_size=None,
                )
                books = {"t1": top}
                fair = {"t1": 0.8}
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("market_probability_missing", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_surfaces_fair_probability_missing_when_dead_recovery_hints_are_present(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={},
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("fair_probability_missing", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_token_score_gate_is_not_bypassed_by_dead_recovery_hints(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = True
            cfg["latency_verifier"]["score_min_for_taker"] = 0.95
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(
                    runner,
                    "_emit_edge_evaluation",
                    side_effect=_capture_edge_eval,
                ), mock.patch.object(
                    runner.latency_verifier,
                    "token_score",
                    return_value=0.0,
                ):
                    out = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={"t1": 0.8},
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=5.0,
                                reduce_only_side="BUY",
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("token_score_below_taker_min", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_requires_ws_book_source_even_with_dead_recovery_hints(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="rest",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                submitted_sides: list[str] = []
                emitted_block_reasons: list[str] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books={"t1": top},
                        fair_probability_by_token={},
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertEqual(submitted_sides, [])
                self.assertIn("taker_requires_ws_book_source", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_canonical_maker_ws_source_gate_never_allows_rest_only_books(self):
        top_rest = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="rest",
            best_bid_price=0.49,
            best_bid_size=100.0,
            best_ask_price=0.51,
            best_ask_size=100.0,
        )
        books = {"t1": top_rest}

        prereq_failures_normal: dict[str, str] = {}
        gated_normal = ExecutionRunner._apply_canonical_maker_ws_source_gate(
            books=books,
            maker_eligible_tokens={"t1"},
            maker_prereq_failure_by_token=prereq_failures_normal,
        )
        self.assertNotIn("t1", gated_normal)
        self.assertEqual(prereq_failures_normal.get("t1"), "maker_requires_ws_book_source")

    def test_build_maker_handoff_no_submission_reason_by_token_merges_prereq_failures(self):
        merged = ExecutionRunner._build_maker_handoff_no_submission_reason_by_token(
            maker_no_submission_reason_by_token={"t1": "risk_reject", "t2": "replace_cancel_unavailable"},
            maker_prereq_failure_by_token={"t2": "maker_timing_gate_closed", "t3": "maker_timing_gate_closed"},
        )
        self.assertEqual(
            merged,
            {
                "t1": "risk_reject",
                "t2": "replace_cancel_unavailable",
                "t3": "maker_timing_gate_closed",
            },
        )

    def test_maker_reduce_only_exit_block_helper_is_removed(self):
        self.assertFalse(hasattr(ExecutionRunner, "_maker_reduce_only_exit_blocked"))

    def test_runner_taker_blocks_when_normal_touch_price_is_missing_even_with_dead_recovery_hints(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=None,  # required for SELL reduce-only path
                    best_bid_size=None,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                books = {"t1": top}
                fair = {"t1": 0.2}
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch(
                    "executor.validate_edge_inputs",
                    return_value=mock.Mock(valid=True, reason_code="", detail=None),
                ), mock.patch("executor.compute_edge_value", return_value=-0.2):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 0)
                self.assertIn("complement_token_mapping_unavailable", emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_uses_normal_bounded_market_reference_block_when_dead_recovery_hints_are_present(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=None,
                    best_ask_size=None,
                )
                books = {"t1": top}
                fair = {"t1": 0.8}
                emitted_rows: list[dict] = []

                def _capture_edge_eval(**kwargs):
                    emitted_rows.append(dict(kwargs))

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info()
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )

                self.assertEqual(out["submitted"], 0)
                self.assertEqual(len(emitted_rows), 1)
                row = emitted_rows[0]
                self.assertEqual(str(row.get("block_reason") or ""), "market_probability_missing")
                self.assertEqual(str(row.get("market_reference_mode") or ""), "missing")
                self.assertEqual(str(row.get("market_reference_class") or ""), "not_available")
                self.assertFalse(bool(row.get("market_reference_fallback_used")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_ignores_dead_recovery_hints_without_normal_taker_authority(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                ) as place_mock:
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=25.0,
                                taker_gate_open=False,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={},
                    )
                self.assertEqual(out["submitted"], 0)
                place_mock.assert_not_called()
                self.assertTrue(emitted_block_reasons)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_submits_normally_when_maker_exit_is_blocked_but_taker_authority_is_live(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emergency_events: list[dict] = []

                def _capture_event(event_type, payload):
                    if str(event_type) == self._HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT:
                        emergency_events.append(dict(payload or {}))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_event,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=5.0,
                                reduce_only_size_cap_shares=12.0,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={"t1": "risk_reject"},
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
                self.assertEqual(emergency_events, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_does_not_emit_emergency_repeat_summary_when_recovery_family_is_dead(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emergency_events: list[dict] = []

                def _capture_event(event_type, payload):
                    if str(event_type) == self._HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT:
                        emergency_events.append(dict(payload or {}))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_event,
                ):
                    for _ in range(2):
                        out = runner._run_taker(
                            books=books,
                            fair_probability_by_token=fair,
                            token_ids=["t1"],
                            stage_info_by_token={
                                "t1": self._historical_recovery_lineage_stage_info(
                                    sec_to_expiry=5.0,
                                    reduce_only_size_cap_shares=12.0,
                                    expired_reduce_only_grace_active=False,
                                )
                            },
                            oracle_tick_age_sec=0.0,
                            lag_verified_token_ids=["t1"],
                            maker_submitted_token_ids=set(),
                            maker_no_submission_reason_by_token={"t1": "risk_reject"},
                        )
                        self.assertIn(out["submitted"], {0, 1})

                self.assertEqual(emergency_events, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_does_not_emit_emergency_repeat_summary_across_tokens_when_recovery_family_is_dead(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1", "t2"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                books = {
                    "t1": BookTop(
                        token_id="t1",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.49,
                        best_bid_size=100.0,
                        best_ask_price=0.51,
                        best_ask_size=100.0,
                    ),
                    "t2": BookTop(
                        token_id="t2",
                        ts_utc=utc_iso(),
                        source="ws",
                        best_bid_price=0.49,
                        best_bid_size=100.0,
                        best_ask_price=0.51,
                        best_ask_size=100.0,
                    ),
                }
                fair = {"t1": 0.8, "t2": 0.8}
                emergency_events: list[dict] = []

                def _capture_event(event_type, payload):
                    if str(event_type) == self._HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT:
                        emergency_events.append(dict(payload or {}))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_event,
                ):
                    for token_id in ["t1", "t2"]:
                        out = runner._run_taker(
                            books=books,
                            fair_probability_by_token=fair,
                            token_ids=[token_id],
                            stage_info_by_token={
                                token_id: self._historical_recovery_lineage_stage_info(
                                    sec_to_expiry=5.0,
                                    reduce_only_size_cap_shares=12.0,
                                    expired_reduce_only_grace_active=False,
                                )
                            },
                            oracle_tick_age_sec=0.0,
                            lag_verified_token_ids=[token_id],
                            maker_submitted_token_ids=set(),
                            maker_no_submission_reason_by_token={token_id: "risk_reject"},
                        )
                        self.assertIn(out["submitted"], {0, 1})

                self.assertEqual(emergency_events, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_submits_normally_when_maker_timing_gate_closed_but_taker_authority_is_live(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=5.0,
                                reduce_only_size_cap_shares=12.0,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={"t1": "maker_timing_gate_closed"},
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_ignores_timing_gate_handoff_override_before_old_emergency_window(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emergency_events: list[dict] = []

                def _capture_event(event_type, payload):
                    if str(event_type) == self._HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT:
                        emergency_events.append(dict(payload or {}))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_event,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=49.0,
                                reduce_only_size_cap_shares=12.0,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={"t1": "maker_timing_gate_closed"},
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
                self.assertEqual(emergency_events, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_ignores_non_timing_maker_handoff_before_old_emergency_window(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=49.0,
                                reduce_only_size_cap_shares=12.0,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={"t1": "risk_reject"},
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_allows_same_market_reentry_when_timing_and_truth_allow(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_market_key_by_token = {"t1": "cond-1|2026-04-24T00:05:00Z|95000|YES"}
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=25.0)
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
                fair = {"t1": 0.8}
                emitted_block_reasons: list[str] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    return_value={"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"},
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": {
                                "stage": "EXTREME_ONLY",
                                "sec_to_expiry": 5.0,
                                "taker_gate_open": True,
                            }
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(out["fills_accepted"], 0)
                self.assertNotIn("normal_taker_authority_closed", emitted_block_reasons)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_allows_different_market_while_other_market_is_live(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t2"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_market_key_by_token = {
                    "t1": "cond-1|2026-04-24T00:05:00Z|95000|YES",
                    "t2": "cond-2|2026-04-24T00:10:00Z|95000|YES",
                }
                runner.risk.positions["t1"] = Position(token_id="t1", net_shares=25.0)
                top = BookTop(
                    token_id="t2",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=0.39,
                    best_bid_size=100.0,
                    best_ask_price=0.41,
                    best_ask_size=100.0,
                )
                books = {"t2": top}
                fair = {"t2": 0.8}
                placed_orders: list[dict[str, object]] = []

                def _fake_place_taker_order_with_outcome(**kwargs):
                    placed_orders.append(dict(kwargs))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t2"}

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", return_value=None):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t2"],
                        stage_info_by_token={
                            "t2": {
                                "stage": "EXTREME_ONLY",
                                "sec_to_expiry": 5.0,
                                "taker_gate_open": True,
                            }
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t2"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(len(placed_orders), 1)
                self.assertEqual(str(placed_orders[0].get("token_id") or ""), "t2")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_ignores_dead_recovery_sell_hint_when_normal_buy_path_is_live(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                top = BookTop(
                    token_id="t1",
                    ts_utc=utc_iso(),
                    source="ws",
                    best_bid_price=None,  # required for SELL reduce-only path
                    best_bid_size=None,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                books = {"t1": top}
                fair = {"t1": 0.8}
                emergency_events: list[dict] = []
                emitted_block_reasons: list[str] = []
                submitted_sides: list[str] = []

                def _capture_event(event_type, payload):
                    if str(event_type) == self._HISTORICAL_PREEXPIRY_EMERGENCY_UNWIND_EVENT:
                        emergency_events.append(dict(payload or {}))

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                with mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ), mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch.object(
                    runner.events,
                    "log_event",
                    side_effect=_capture_event,
                ), mock.patch(
                    "executor.validate_edge_inputs",
                    return_value=mock.Mock(valid=True, reason_code="", detail=None),
                ), mock.patch("executor.compute_edge_value", return_value=0.2):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                sec_to_expiry=5.0,
                                expired_reduce_only_grace_active=False,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                        maker_submitted_token_ids=set(),
                        maker_no_submission_reason_by_token={"t1": "no_desired_quote"},
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(submitted_sides, ["BUY"])
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
                self.assertEqual(emergency_events, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_taker_ignores_dead_reduce_only_size_cap_hint(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["taker"]["enabled"] = True
            cfg["taker"]["min_edge"] = 0.001
            cfg["taker"]["max_orders_per_cycle"] = 1
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
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
                fair = {"t1": 0.8}
                emitted_block_reasons: list[str] = []
                submitted_sides: list[str] = []
                captured_contexts: list[dict] = []

                def _capture_edge_eval(**kwargs):
                    emitted_block_reasons.append(str(kwargs.get("block_reason") or ""))

                def _fake_place_taker_order_with_outcome(**kwargs):
                    submitted_sides.append(str(kwargs.get("side") or ""))
                    captured_contexts.append(dict(kwargs.get("competitiveness_context") or {}))
                    return {"submitted": True, "fills_accepted": 0, "order_id": "ord-t1"}

                with mock.patch.object(runner, "_emit_edge_evaluation", side_effect=_capture_edge_eval), mock.patch.object(
                    runner.manager,
                    "place_taker_order_with_outcome",
                    side_effect=_fake_place_taker_order_with_outcome,
                ):
                    out = runner._run_taker(
                        books=books,
                        fair_probability_by_token=fair,
                        token_ids=["t1"],
                        stage_info_by_token={
                            "t1": self._historical_recovery_lineage_stage_info(
                                reduce_only_size_cap_shares=0.68,
                                reduce_only_size_cap_below_min_order_size=True,
                            )
                        },
                        oracle_tick_age_sec=0.0,
                        lag_verified_token_ids=["t1"],
                    )
                self.assertEqual(out["submitted"], 1)
                self.assertEqual(submitted_sides, ["BUY"])
                self.assertEqual(len(captured_contexts), 1)
                self.assertNotIn(self._HISTORICAL_MAKER_TO_TAKER_HANDOFF_DISABLED, emitted_block_reasons)
            finally:
                runner.events.close()
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_start_uses_transport_watch_set_not_valuation_watch_set(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["active-yes", "active-no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner._challenger_token_ids = ["pending-yes", "pending-no"]  # pylint: disable=protected-access
                runner.risk.positions["held-token"] = Position(token_id="held-token", net_shares=1.0)
                with mock.patch.object(runner, "_write_run_manifest"), mock.patch.object(
                    runner, "_clear_external_guard_stop_on_start"
                ), mock.patch.object(runner, "_apply_external_guard_stop"), mock.patch.object(
                    runner.chainlink, "start"
                ), mock.patch.object(runner.pyth, "start"), mock.patch.object(
                    runner.prometheus, "start"
                ), mock.patch.object(runner.book_feed, "start") as book_feed_start:
                    runner.run(duration_min=1e-9)

                book_feed_start.assert_called_once_with(
                    ["active-no", "active-yes", "held-token", "pending-no", "pending-yes"]
                )
            finally:
                runner.events.close()
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_refresh_targets_carries_forward_non_flat_held_tokens_on_empty_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held1"]
            cfg["targets"]["token_expiry_utc_by_token"] = {"held1": "2030-01-01T00:01:00.000Z"}
            cfg["targets"]["token_side_by_token"] = {"held1": "YES"}
            cfg["targets"]["token_strike_by_token"] = {"held1": 50000.0}
            cfg["targets"]["token_market_key_by_token"] = {"held1": "mk-held1"}
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                runner.risk.positions["held1"] = Position(token_id="held1", net_shares=2.0)
                runner.token_market_key_by_token["held1"] = "mk-held1"
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
                self.assertEqual(runner._challenger_token_ids, [])  # pylint: disable=protected-access
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_standdown"), 1.0)
                self.assertEqual(runner.telemetry.gauges.get("target_discovery_active_targets"), 0.0)
                self.assertEqual(str(runner.token_market_key_by_token.get("held1") or ""), "mk-held1")
                self.assertIn("held1", set(runner._valuation_watch_token_ids()))  # pylint: disable=protected-access
                self.assertEqual(set(runner._transport_watch_token_ids()), {"held1"})  # pylint: disable=protected-access
            finally:
                runner.events.close()
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
                with mock.patch.object(
                    runner.discovery,
                    "discover",
                    side_effect=[standdown_result, active_result, standdown_result],
                ):
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, [])
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, ["yes1", "no1"])
                    runner._refresh_targets(force=True)
                    self.assertEqual(runner.token_ids, ["yes1", "no1"])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_sets_resolve_hold_for_recently_expired_held_token(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-expired"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-expired"
                expiry = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-expired"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=3.0)
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertEqual(str(stage_info.get("lifecycle_phase") or ""), "resolve")
                self.assertEqual(str(stage_info.get("lineage_stage") or ""), "EXPIRED")
                self.assertNotIn("stage", stage_info)
                self.assertEqual(str(stage_info.get("reason") or ""), "expired_market")
                self.assertTrue(bool(stage_info.get("settlement_hold_required")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_enforces_recovery_only_authority_in_mid_extreme_window(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["late-maker"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["strategy"]["maker_competitiveness"]["timing_gate_enabled"] = True
            cfg["strategy"]["maker_competitiveness"]["timing_gate_min_sec_to_expiry"] = 10.0
            cfg["strategy"]["maker_competitiveness"]["timing_gate_max_sec_to_expiry"] = 15.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "late-maker"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=12)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-late-maker"
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertEqual(str(stage_info.get("lifecycle_phase") or ""), "maker_window")
                self.assertEqual(str(stage_info.get("lineage_stage") or ""), "EXTREME_ONLY")
                self.assertNotIn("effective_stage", stage_info)
                self.assertNotIn("stage_bucket", stage_info)
                self.assertNotIn("raw_stage", stage_info)
                self.assertNotIn("stage", stage_info)
                self.assertTrue(bool(stage_info.get("maker_timing_gate_open")))
                self.assertFalse(bool(stage_info.get("maker_timing_stage_override_active")))
                self.assertTrue(bool(stage_info.get("maker_gate_open")))
                self.assertFalse(bool(stage_info.get("taker_gate_open")))
                self.assertTrue(bool(stage_info.get("maker_phase_allowed")))
                self.assertFalse(bool(stage_info.get("taker_phase_allowed")))
                self.assertNotIn("maker_new_risk_allowed", stage_info)
                self.assertNotIn("normal_taker_allowed", stage_info)
                self.assertNotIn("late_window_authority_class", stage_info)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_maker_competitiveness_one_sided_uses_maker_phase_authority(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["late-maker"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["strategy"]["maker_competitiveness"]["one_sided_enabled"] = True
            cfg["strategy"]["maker_competitiveness"]["one_sided_edge_threshold_abs"] = 0.18
            cfg["strategy"]["maker_competitiveness"]["timing_gate_enabled"] = True
            cfg["strategy"]["maker_competitiveness"]["timing_gate_min_sec_to_expiry"] = 10.0
            cfg["strategy"]["maker_competitiveness"]["timing_gate_max_sec_to_expiry"] = 15.0
            cfg["lifecycle"]["lane_gates"]["maker"]["one_sided_enabled"] = True
            cfg["lifecycle"]["lane_gates"]["maker"]["one_sided_edge_threshold_abs"] = 0.18
            cfg["lifecycle"]["lane_gates"]["maker"]["timing_gate_enabled"] = True
            cfg["lifecycle"]["lane_gates"]["maker"]["timing_gate_min_sec_to_expiry"] = 10.0
            cfg["lifecycle"]["lane_gates"]["maker"]["timing_gate_max_sec_to_expiry"] = 15.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "late-maker"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=12)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-late-maker"
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                top = BookTop(
                    token_id=token_id,
                    ts_utc=utc_iso(),
                    source="test",
                    best_bid_price=0.49,
                    best_bid_size=100.0,
                    best_ask_price=0.51,
                    best_ask_size=100.0,
                )
                market_reference = {
                    "market_probability": 0.50,
                    "market_reference_mode": "direct_midpoint",
                    "market_reference_basis": "direct_book_midpoint",
                    "market_reference_source_side": "none",
                    "market_reference_class": "authoritative",
                }

                without_authority = runner._maker_competitiveness_profile(  # pylint: disable=protected-access
                    token_id=token_id,
                    top=top,
                    market_reference=market_reference,
                    fair_probability=0.70,
                    secondary_fair_probability=None,
                    secondary_oracle_status="disabled",
                    chainlink_spot_price=None,
                    secondary_oracle_spot_price=None,
                    stage="MAKER_LATE_WINDOW",
                    lifecycle_phase=str(stage_info.get("lifecycle_phase") or "maker_window"),
                    lineage_stage=str(stage_info.get("lineage_stage") or "EXTREME_ONLY"),
                    sec_to_expiry=float(stage_info.get("sec_to_expiry") or 0.0),
                    base_size_multiplier=1.0,
                    base_spread_multiplier=1.0,
                    timing_gate_open=True,
                    maker_phase_allowed=False,
                )
                with_authority = runner._maker_competitiveness_profile(  # pylint: disable=protected-access
                    token_id=token_id,
                    top=top,
                    market_reference=market_reference,
                    fair_probability=0.70,
                    secondary_fair_probability=None,
                    secondary_oracle_status="disabled",
                    chainlink_spot_price=None,
                    secondary_oracle_spot_price=None,
                    stage="MAKER_LATE_WINDOW",
                    lifecycle_phase=str(stage_info.get("lifecycle_phase") or "maker_window"),
                    lineage_stage=str(stage_info.get("lineage_stage") or "EXTREME_ONLY"),
                    sec_to_expiry=float(stage_info.get("sec_to_expiry") or 0.0),
                    base_size_multiplier=1.0,
                    base_spread_multiplier=1.0,
                    timing_gate_open=True,
                    maker_phase_allowed=bool(stage_info.get("maker_phase_allowed")),
                )
                low_edge_with_authority = runner._maker_competitiveness_profile(  # pylint: disable=protected-access
                    token_id=token_id,
                    top=top,
                    market_reference=market_reference,
                    fair_probability=0.55,
                    secondary_fair_probability=None,
                    secondary_oracle_status="disabled",
                    chainlink_spot_price=None,
                    secondary_oracle_spot_price=None,
                    stage="MAKER_LATE_WINDOW",
                    lifecycle_phase=str(stage_info.get("lifecycle_phase") or "maker_window"),
                    lineage_stage=str(stage_info.get("lineage_stage") or "EXTREME_ONLY"),
                    sec_to_expiry=float(stage_info.get("sec_to_expiry") or 0.0),
                    base_size_multiplier=1.0,
                    base_spread_multiplier=1.0,
                    timing_gate_open=True,
                    maker_phase_allowed=bool(stage_info.get("maker_phase_allowed")),
                )

                self.assertEqual(str(without_authority.get("side_policy") or ""), "TWO_SIDED")
                self.assertFalse(bool((without_authority.get("context") or {}).get("one_sided_allowed_phase")))
                self.assertFalse(bool((without_authority.get("context") or {}).get("one_sided_allowed_authority")))
                self.assertEqual(str((with_authority.get("context") or {}).get("lifecycle_phase") or ""), "maker_window")
                self.assertEqual(str((with_authority.get("context") or {}).get("lineage_stage") or ""), "EXTREME_ONLY")
                self.assertEqual(str(with_authority.get("side_policy") or ""), "BUY_ONLY")
                self.assertTrue(bool((with_authority.get("context") or {}).get("one_sided_allowed_phase")))
                self.assertTrue(bool((with_authority.get("context") or {}).get("one_sided_allowed_authority")))
                self.assertTrue(bool((with_authority.get("context") or {}).get("one_sided_active")))
                self.assertEqual(str(low_edge_with_authority.get("side_policy") or ""), "TWO_SIDED")
                self.assertTrue(bool((low_edge_with_authority.get("context") or {}).get("one_sided_allowed_phase")))
                self.assertTrue(bool((low_edge_with_authority.get("context") or {}).get("one_sided_allowed_authority")))
                self.assertFalse(bool((low_edge_with_authority.get("context") or {}).get("one_sided_active")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_settles_postexpiry_binary_no_position_from_authoritative_chainlink_tick(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["chainlink"]["symbols"] = ["btc/usd"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-no"
                expiry_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=3)
                runner._apply_token_expiry_map({token_id: utc_iso(expiry_dt)}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "NO"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-no|NO"
                runner.risk.positions[token_id] = Position(
                    token_id=token_id,
                    net_shares=2.0,
                    buy_shares=2.0,
                    bought_notional=0.12,
                )
                pre_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=50010.0,
                    source_ts_utc=utc_iso(expiry_dt - dt.timedelta(seconds=1)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic() - 2.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                post_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=49990.0,
                    source_ts_utc=utc_iso(expiry_dt + dt.timedelta(seconds=1)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic() - 1.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(pre_tick)  # pylint: disable=protected-access
                runner.chainlink._ingest_tick(post_tick)  # pylint: disable=protected-access

                settled = runner._apply_postexpiry_binary_settlement()  # pylint: disable=protected-access

                self.assertEqual(settled, 1)
                position = runner.risk.positions[token_id]
                self.assertAlmostEqual(float(position.net_shares), 0.0, places=9)
                self.assertAlmostEqual(float(position.sell_shares), 2.0, places=9)
                self.assertAlmostEqual(float(position.sold_notional), 2.0, places=9)
                valuation_state = runner._build_valuation_state(books={})  # pylint: disable=protected-access
                self.assertFalse(bool(valuation_state.get("valuation_hard_degraded", False)))
                self.assertEqual(int(valuation_state.get("held_unpriceable_count", 0) or 0), 0)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_leaves_postexpiry_binary_position_unsettled_without_authoritative_tick(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["chainlink"]["symbols"] = ["btc/usd"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-no"
                expiry_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=3)
                runner._apply_token_expiry_map({token_id: utc_iso(expiry_dt)}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "NO"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-no|NO"
                runner.risk.positions[token_id] = Position(
                    token_id=token_id,
                    net_shares=2.0,
                    buy_shares=2.0,
                    bought_notional=0.12,
                )
                pre_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=50010.0,
                    source_ts_utc=utc_iso(expiry_dt - dt.timedelta(seconds=1)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic() - 1.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(pre_tick)  # pylint: disable=protected-access

                settled = runner._apply_postexpiry_binary_settlement()  # pylint: disable=protected-access

                self.assertEqual(settled, 0)
                position = runner.risk.positions[token_id]
                self.assertAlmostEqual(float(position.net_shares), 2.0, places=9)
                self.assertAlmostEqual(float(position.sell_shares), 0.0, places=9)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_leaves_postexpiry_binary_position_unsettled_with_timestamp_like_strike(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["chainlink"]["symbols"] = ["btc/usd"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-no"
                expiry_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=3)
                runner._apply_token_expiry_map({token_id: utc_iso(expiry_dt)}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "NO"
                runner.token_strike_by_token[token_id] = float(expiry_dt.timestamp() - 300.0)
                runner.token_market_key_by_token[token_id] = "mk-held-no|NO"
                runner.risk.positions[token_id] = Position(
                    token_id=token_id,
                    net_shares=2.0,
                    buy_shares=2.0,
                    bought_notional=0.12,
                )
                post_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=49990.0,
                    source_ts_utc=utc_iso(expiry_dt + dt.timedelta(seconds=1)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic() - 1.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(post_tick)  # pylint: disable=protected-access

                settled = runner._apply_postexpiry_binary_settlement()  # pylint: disable=protected-access

                self.assertEqual(settled, 0)
                position = runner.risk.positions[token_id]
                self.assertAlmostEqual(float(position.net_shares), 2.0, places=9)
                self.assertAlmostEqual(float(position.sell_shares), 0.0, places=9)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runner_settles_postexpiry_binary_position_using_open_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-yes"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["chainlink"]["symbols"] = ["btc/usd"]
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-yes"
                expiry_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=3)
                anchor_dt = expiry_dt - dt.timedelta(minutes=5)
                runner._apply_token_expiry_map({token_id: utc_iso(expiry_dt)}, source="test")  # pylint: disable=protected-access
                runner._apply_token_open_anchor_map({token_id: utc_iso(anchor_dt)}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_market_key_by_token[token_id] = "mk-held-yes|YES"
                runner.risk.positions[token_id] = Position(
                    token_id=token_id,
                    net_shares=2.0,
                    buy_shares=2.0,
                    bought_notional=0.12,
                )
                anchor_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=50000.0,
                    source_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=100)),
                    received_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=150)),
                    received_monotonic=time.monotonic() - 2.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                post_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=50010.0,
                    source_ts_utc=utc_iso(expiry_dt + dt.timedelta(seconds=1)),
                    received_ts_utc=utc_iso(),
                    received_monotonic=time.monotonic() - 1.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(anchor_tick)  # pylint: disable=protected-access
                runner.chainlink._ingest_tick(post_tick)  # pylint: disable=protected-access

                settled = runner._apply_postexpiry_binary_settlement()  # pylint: disable=protected-access

                self.assertEqual(settled, 1)
                position = runner.risk.positions[token_id]
                self.assertAlmostEqual(float(position.net_shares), 0.0, places=9)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_sets_settlement_hold_for_held_token(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-preexpiry"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-preexpiry"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-preexpiry"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=2.0)
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertFalse(bool(stage_info.get("open_order_cleanup_required")))
                self.assertTrue(bool(stage_info.get("settlement_hold_required")))
                self.assertFalse(bool(stage_info.get("unresolved_lifecycle_obligation")))
                self.assertFalse(bool(stage_info.get("cancel_fail_closed")))
                self.assertEqual(str(stage_info.get("lifecycle_phase") or ""), "resolve")
                self.assertFalse(bool(stage_info.get("maker_gate_open")))
                self.assertFalse(bool(stage_info.get("taker_gate_open")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_keeps_settlement_hold_for_live_held_position(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-preexpiry"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-preexpiry"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-preexpiry"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=2.0)
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertTrue(bool(stage_info.get("settlement_hold_required")))
                self.assertFalse(bool(stage_info.get("open_order_cleanup_required")))
                self.assertAlmostEqual(float(stage_info.get("held_net_shares") or 0.0), 2.0, places=9)
                self.assertEqual(str(stage_info.get("lifecycle_phase") or ""), "resolve")
                self.assertFalse(bool(stage_info.get("maker_gate_open")))
                self.assertFalse(bool(stage_info.get("taker_gate_open")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_keeps_settlement_hold_when_posture_not_normal(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-preexpiry"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-preexpiry"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-preexpiry"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=2.0)
                runner._financial_posture_class = "HALT_NEW_RISK"  # pylint: disable=protected-access
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertTrue(bool(stage_info.get("settlement_hold_required")))
                self.assertFalse(bool(stage_info.get("open_order_cleanup_required")))
                self.assertFalse(bool(stage_info.get("cancel_fail_closed")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_token_lifecycle_info_does_not_surface_dead_reduce_only_size_cap_flags(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-preexpiry"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["risk"]["min_order_size"] = 1.0
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-preexpiry"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-preexpiry"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=0.68)
                stage_info = runner._token_lifecycle_info(token_id)  # pylint: disable=protected-access
                self.assertTrue(bool(stage_info.get("settlement_hold_required")))
                self.assertIsNone(stage_info.get("reduce_only_size_cap_shares"))
                self.assertIsNone(stage_info.get("reduce_only_size_cap_below_min_order_size"))
                self.assertFalse(bool(stage_info.get("cancel_fail_closed")))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_maker_prereq_allows_recovery_without_lag_or_fair_probability(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["held-preexpiry"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["require_armed_for_maker"] = True
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                token_id = "held-preexpiry"
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=45)).isoformat().replace("+00:00", "Z")
                runner._apply_token_expiry_map({token_id: expiry}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                runner.token_strike_by_token[token_id] = 50000.0
                runner.token_market_key_by_token[token_id] = "mk-held-preexpiry"
                runner.risk.positions[token_id] = Position(token_id=token_id, net_shares=2.0)
                latency_snapshot = mock.Mock(armed=True)

                default_reason = runner._maker_prereq_failure_reason(  # pylint: disable=protected-access
                    token_id,
                    fair_probability_by_token={},
                    latency_snapshot=latency_snapshot,
                    oracle_fresh=True,
                )
                self.assertEqual(default_reason, "token_lag_not_verified_for_maker")

                recovery_reason = runner._maker_prereq_failure_reason(  # pylint: disable=protected-access
                    token_id,
                    fair_probability_by_token={},
                    latency_snapshot=latency_snapshot,
                    oracle_fresh=True,
                )
                self.assertEqual(recovery_reason, "token_lag_not_verified_for_maker")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_maker_prereq_accepts_open_anchor_as_threshold_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t1"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["latency_verifier"]["require_armed_for_maker"] = False
            cfg["latency_verifier"]["score_enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                token_id = "t1"
                expiry_dt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
                anchor_dt = expiry_dt - dt.timedelta(minutes=5)
                runner._apply_token_expiry_map({token_id: utc_iso(expiry_dt)}, source="test")  # pylint: disable=protected-access
                runner._apply_token_open_anchor_map({token_id: utc_iso(anchor_dt)}, source="test")  # pylint: disable=protected-access
                runner.token_side_by_token[token_id] = "YES"
                anchor_tick = ChainlinkTick(
                    symbol="btc/usd",
                    price=50000.0,
                    source_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=100)),
                    received_ts_utc=utc_iso(anchor_dt + dt.timedelta(milliseconds=150)),
                    received_monotonic=time.monotonic() - 1.0,
                    topic="crypto_prices_chainlink",
                    msg_type="*",
                )
                runner.chainlink._ingest_tick(anchor_tick)  # pylint: disable=protected-access

                reason = runner._maker_prereq_failure_reason(  # pylint: disable=protected-access
                    token_id,
                    fair_probability_by_token={token_id: 0.55},
                    latency_snapshot=mock.Mock(armed=True),
                    oracle_fresh=True,
                )
                self.assertEqual(reason, "")
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_refresh_targets_applies_open_anchor_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = []
            cfg["targets"]["discovery"]["enabled"] = True
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            runner = ExecutionRunner(cfg)
            try:
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
                        "token_strike_by_token": {},
                        "token_open_anchor_utc_by_token": {
                            "yes1": "2030-01-01T00:00:00.000Z",
                            "no1": "2030-01-01T00:00:00.000Z",
                        },
                        "token_market_key_by_token": {"yes1": "mk1", "no1": "mk1"},
                    },
                )()
                with mock.patch.object(runner.discovery, "discover", return_value=active_result):
                    runner._refresh_targets(force=True)
                self.assertEqual(runner.token_open_anchor_utc_by_token.get("yes1"), "2030-01-01T00:00:00.000Z")
                self.assertEqual(runner.token_open_anchor_utc_by_token.get("no1"), "2030-01-01T00:00:00.000Z")
                self.assertIsNotNone(runner.token_open_anchor_dt_by_token.get("yes1"))
                self.assertIsNotNone(runner.token_open_anchor_dt_by_token.get("no1"))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runtime_semantics_marks_owned_market_prepare_without_kill_switch(self):
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
                self.assertEqual(runner._runtime_state, "prepare")
                self.assertFalse(runner.risk.kill_switch)
                self.assertTrue(runner._runtime_market_truth_required)
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_runtime_transition_emits_canonical_market_truth_fields_only(self):
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
                rows = self._read_event_rows(Path(td), event_type="runtime_state_transition")
                self.assertTrue(rows)
                latest = rows[-1]
                self.assertEqual(str(latest.get("runtime_state") or ""), "prepare")
                self.assertEqual(str(latest.get("lifecycle_phase") or ""), "prepare")
                self.assertEqual(str(latest.get("transition_reason_code") or ""), "owned_market_prepare")
                self.assertTrue(bool(latest.get("market_truth_required")))
                self.assertNotIn("book_feed_required", latest)
                self.assertNotIn("no_target_standdown", latest)
            finally:
                runner.events.close()
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
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_latency_sampling_token_ids_include_only_ws_sources(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_ws", "t_official_ws", "t_rest", "t_unknown"]
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
                    "t_official_ws": BookTop(
                        token_id="t_official_ws",
                        ts_utc=utc_iso(),
                        source="official_ws_price_change",
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
                self.assertEqual(selected, ["t_ws", "t_official_ws"])
                self.assertTrue(runner._book_source_is_ws(books["t_ws"]))
                self.assertTrue(runner._book_source_is_ws(books["t_official_ws"]))
                self.assertFalse(runner._book_source_is_ws(books["t_rest"]))
                self.assertFalse(runner._book_source_is_ws(books["t_unknown"]))
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()

    def test_pair_truth_map_accepts_official_ws_books_as_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["targets"]["token_ids"] = ["t_yes", "t_no"]
            cfg["targets"]["discovery"]["enabled"] = False
            cfg["chainlink"]["enabled"] = False
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")

            runner = ExecutionRunner(cfg)
            try:
                runner.token_market_key_by_token["t_yes"] = "base-key|2026-05-16T03:15:00.000Z|na|YES"
                runner.token_market_key_by_token["t_no"] = "base-key|2026-05-16T03:15:00.000Z|na|NO"
                runner.token_side_by_token["t_yes"] = "YES"
                runner.token_side_by_token["t_no"] = "NO"
                books = {
                    "t_yes": BookTop(
                        token_id="t_yes",
                        ts_utc=utc_iso(),
                        source="official_ws_price_change",
                        best_bid_price=0.64,
                        best_bid_size=10.0,
                        best_ask_price=0.65,
                        best_ask_size=10.0,
                    ),
                    "t_no": BookTop(
                        token_id="t_no",
                        ts_utc=utc_iso(),
                        source="official_ws_book",
                        best_bid_price=0.35,
                        best_bid_size=10.0,
                        best_ask_price=0.36,
                        best_ask_size=10.0,
                    ),
                }
                pair_truth = runner._build_pair_truth_map(books=books, token_ids=["t_yes", "t_no"])
                self.assertEqual(pair_truth["base-key|2026-05-16T03:15:00.000Z|na"]["pair_truth_class"], "authoritative")
                missing, one_sided = runner._pair_truth_base_keys_by_class(pair_truth)
                self.assertEqual(missing, [])
                self.assertEqual(one_sided, [])
            finally:
                runner.events.close()
                runner.gateway.close()
                runner.discovery.close()
                runner.chainlink.stop()
                runner.alerts.close()


if __name__ == "__main__":
    unittest.main()
