import copy
import datetime as dt
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from prodesk.common import utc_iso
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.models import BookTop, FillEvent, LiveOrder, OrderIntent, Position
from prodesk.preflight import run_preflight_checks
from prodesk.risk import RiskEngine

_VALID_PK = "0x" + ("a" * 64)
_VALID_FUNDER = "0x" + ("b" * 40)


class PreflightAndRiskTests(unittest.TestCase):
    @staticmethod
    def _risk_cfg() -> dict:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        # Most tests in this module validate legacy risk behaviors and should
        # remain independent from the expiry new-exposure gate unless explicit.
        cfg["min_sec_to_expiry_for_new_exposure"] = 0.0
        return cfg

    def test_live_preflight_requires_confirmation(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = True
        findings = run_preflight_checks(cfg, mode_override="live", confirm_live=False)
        self.assertIn("live_confirmation_missing", findings)

    def test_live_preflight_requires_security_ack(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        with mock.patch.dict(
            "os.environ",
            {"POLYMARKET_PRIVATE_KEY": _VALID_PK, "POLYMARKET_FUNDER": _VALID_FUNDER},
            clear=True,
        ):
            findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)
        self.assertIn("security_ack_missing:SECURITY_ACK", findings)

    def test_live_preflight_accepts_security_ack(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        env = {
            "POLYMARKET_PRIVATE_KEY": _VALID_PK,
            "POLYMARKET_FUNDER": _VALID_FUNDER,
            "SECURITY_ACK": "YES",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)
        self.assertNotIn("security_ack_missing:SECURITY_ACK", findings)

    def test_live_preflight_uses_file_secret_sources_without_env_vars(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pk_path = root / "private_key.txt"
            funder_path = root / "funder.txt"
            pk_path.write_text(_VALID_PK, encoding="utf-8")
            funder_path.write_text(_VALID_FUNDER, encoding="utf-8")
            cfg["auth"]["private_key_source"] = {"mode": "file", "path": str(pk_path)}
            cfg["auth"]["funder_source"] = {"mode": "file", "path": str(funder_path)}
            with mock.patch.dict("os.environ", {"SECURITY_ACK": "YES"}, clear=True):
                findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)

        self.assertFalse(any(finding.startswith("missing_env:") for finding in findings))
        self.assertFalse(any(finding.startswith("secret_load_failed:") for finding in findings))
        self.assertFalse(any(finding.startswith("invalid_private_key:") for finding in findings))
        self.assertFalse(any(finding.startswith("invalid_funder:") for finding in findings))

    def test_live_preflight_reports_missing_env_for_env_secret_sources(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["t1"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        with mock.patch.dict("os.environ", {"SECURITY_ACK": "YES"}, clear=True):
            findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)
        self.assertIn("missing_env:POLYMARKET_PRIVATE_KEY", findings)
        self.assertIn("missing_env:POLYMARKET_FUNDER", findings)

    def test_preflight_duplicate_tokens_detected(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc", "abc"]
        cfg["preflight"]["check_market_data"] = False
        with tempfile.TemporaryDirectory() as td:
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertIn("duplicate_token_ids_detected", findings)

    def test_preflight_flags_invalid_state_file(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        with tempfile.TemporaryDirectory() as td:
            cfg["storage"]["log_dir"] = td
            state_path = Path(td) / "state.json"
            state_path.write_text("{bad json", encoding="utf-8")
            cfg["storage"]["state_path"] = str(state_path)
            findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertTrue(any(x.startswith("state_file_invalid:") for x in findings))

    def test_risk_cancel_rate_limit(self):
        cfg = self._risk_cfg()
        cfg["max_cancels_per_min"] = 1
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        allow_first = risk.can_cancel()
        self.assertTrue(allow_first.allowed)
        risk.on_order_canceled()
        deny_second = risk.can_cancel()
        self.assertFalse(deny_second.allowed)

    def test_mark_to_market_and_loss_limits(self):
        cfg = self._risk_cfg()
        cfg["max_total_loss"] = 5.0
        cfg["max_loss_per_token"] = 3.0
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        risk.on_fill(FillEvent(trade_id="x1", token_id="t1", side="BUY", price=0.9, size=10, ts_utc="2026-01-01T00:00:00Z"))
        total_pnl, pnl_by_token = risk.mark_to_market({"t1": 0.2})
        self.assertLess(total_pnl, 0)
        self.assertLess(pnl_by_token["t1"], 0)
        decision = risk.evaluate_loss_limits({"t1": 0.2})
        self.assertFalse(decision.allowed)

    def test_wallet_guardian_drawdown_snapshot_reports_threshold_breach(self):
        cfg = self._risk_cfg()
        cfg["max_total_loss"] = 5.0
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        risk.on_fill(FillEvent(trade_id="x1", token_id="t1", side="BUY", price=0.9, size=10, ts_utc="2026-01-01T00:00:00Z"))
        snapshot = risk.wallet_guardian_drawdown_snapshot({"t1": 0.2})
        self.assertTrue(bool(snapshot.get("enabled")))
        self.assertFalse(bool(snapshot.get("within_limit", True)))
        self.assertEqual(str(snapshot.get("law_name") or ""), "daily_loss_hard_pause")
        self.assertEqual(str(snapshot.get("legacy_reason") or ""), "max_total_loss")

    def test_mark_to_market_keeps_realized_pnl_for_flat_position_without_mid(self):
        cfg = self._risk_cfg()
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        risk.on_fill(FillEvent(trade_id="x1", token_id="t1", side="BUY", price=0.4, size=10, ts_utc="2026-01-01T00:00:00Z"))
        risk.on_fill(FillEvent(trade_id="x2", token_id="t1", side="SELL", price=0.6, size=10, ts_utc="2026-01-01T00:00:01Z"))
        total_pnl, pnl_by_token = risk.mark_to_market({})
        self.assertAlmostEqual(total_pnl, 2.0, places=9)
        self.assertAlmostEqual(float(pnl_by_token.get("t1", 0.0)), 2.0, places=9)

    def test_mark_to_market_tracks_missing_mid_skip_by_exposure_class(self):
        cfg = self._risk_cfg()
        positions = {"t1": Position(token_id="t1", net_shares=1.0)}
        risk = RiskEngine(cfg, positions)
        risk.set_exposure_classification_state(exposure_class_by_token={"t1": "DUST_ELIGIBLE"})

        total_pnl, pnl_by_token = risk.mark_to_market({})

        self.assertAlmostEqual(total_pnl, 0.0, places=9)
        self.assertEqual(pnl_by_token, {})
        self.assertEqual(
            int(risk._last_mark_to_market_skipped_nonflat_by_class.get("DUST_ELIGIBLE", 0)),  # pylint: disable=protected-access
            1,
        )

    def test_validate_order_hard_degraded_blocks_risk_increase_allows_pure_reduce(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        positions = {"t1": Position(token_id="t1", net_shares=5.0)}
        risk = RiskEngine(cfg, positions)
        risk.set_valuation_degraded_state(hard_degraded=True, reasons=["missing_mid:t1"])
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

        blocked = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "valuation_hard_degraded_risk_increase_blocked")

        allowed_reduce = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=3.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertTrue(allowed_reduce.allowed)

        allowed_flatten = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=5.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertTrue(allowed_flatten.allowed)

        blocked_cross = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=6.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertFalse(blocked_cross.allowed)
        self.assertEqual(blocked_cross.reason, "valuation_hard_degraded_risk_increase_blocked")

    def test_validate_order_no_longer_blocks_risk_increase_on_removed_dust_capacity_authority(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["position_dust_token_count_cap"] = 4
        positions = {
            "dust1": Position(token_id="dust1", net_shares=0.2),
            "dust2": Position(token_id="dust2", net_shares=0.2),
            "dust3": Position(token_id="dust3", net_shares=0.2),
            "dust4": Position(token_id="dust4", net_shares=0.2),
            "new1": Position(token_id="new1", net_shares=0.0),
            "held1": Position(token_id="held1", net_shares=5.0),
        }
        risk = RiskEngine(cfg, positions)
        risk.set_exposure_classification_state(
            exposure_class_by_token={
                "dust1": "DUST_ELIGIBLE",
                "dust2": "DUST_ELIGIBLE",
                "dust3": "DUST_ELIGIBLE",
                "dust4": "DUST_ELIGIBLE",
            },
        )
        top_new = BookTop(
            token_id="new1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        from prodesk.models import OrderIntent

        allowed_new = risk.validate_order(
            OrderIntent(token_id="new1", side="BUY", price=0.5, size=1.0),
            top=top_new,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 90.0},
        )
        self.assertTrue(allowed_new.allowed)

        top_reduce = BookTop(
            token_id="held1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        allowed_reduce = risk.validate_order(
            OrderIntent(token_id="held1", side="SELL", price=0.5, size=3.0),
            top=top_reduce,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 90.0},
        )
        self.assertTrue(allowed_reduce.allowed)

        risk_shadow = RiskEngine(cfg, {"new1": Position(token_id="new1", net_shares=0.0)})
        risk_shadow.set_exposure_classification_state(
            exposure_class_by_token={
                "dust1": "DUST_ELIGIBLE",
                "dust2": "DUST_ELIGIBLE",
                "dust3": "DUST_ELIGIBLE",
                "dust4": "DUST_ELIGIBLE",
            },
        )
        shadow_allowed = risk_shadow.validate_order(
            OrderIntent(token_id="new1", side="BUY", price=0.5, size=1.0),
            top=top_new,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 90.0},
        )
        self.assertTrue(shadow_allowed.allowed)

    def test_validate_order_blocks_new_exposure_when_sec_to_expiry_unknown(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["min_sec_to_expiry_for_new_exposure"] = 120.0
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {}
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
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

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "PREEXPIRY_REDUCE_ONLY",
            },
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "new_exposure_sec_to_expiry_unknown_blocked")
        self.assertIsInstance(decision.basis, dict)
        self.assertAlmostEqual(
            float(decision.basis.get("min_sec_to_expiry_for_new_exposure") or 0.0),
            120.0,
            places=9,
        )
        self.assertEqual(str(decision.basis.get("financial_posture_class") or ""), "PREEXPIRY_REDUCE_ONLY")

    def test_validate_order_basis_prefers_canonical_lifecycle_phase_over_stage_compat(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["max_notional_per_token"] = 5.0
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
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

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=20.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "lifecycle_phase": "taker_window",
                "stage": "MAKER_TAKER_SELECTIVE",
                "sec_to_expiry": 45.0,
            },
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "notional_cap")
        self.assertIsInstance(decision.basis, dict)
        self.assertEqual(str(decision.basis.get("lifecycle_phase") or ""), "taker_window")
        self.assertNotIn("stage", decision.basis)

    def test_validate_order_blocks_new_exposure_below_expiry_threshold_and_allows_reduce(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["min_sec_to_expiry_for_new_exposure"] = 120.0
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {}
        positions = {"t1": Position(token_id="t1", net_shares=5.0)}
        risk = RiskEngine(cfg, positions)
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

        blocked_buy = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 60.0},
        )
        self.assertFalse(blocked_buy.allowed)
        self.assertEqual(blocked_buy.reason, "new_exposure_expiry_gate_blocked")

        allowed_reduce = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=3.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 60.0},
        )
        self.assertTrue(allowed_reduce.allowed)

        allowed_flatten = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=5.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 60.0},
        )
        self.assertTrue(allowed_flatten.allowed)

        blocked_cross = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=6.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 60.0},
        )
        self.assertFalse(blocked_cross.allowed)
        self.assertEqual(blocked_cross.reason, "new_exposure_expiry_gate_blocked")

    def test_validate_order_uses_lane_specific_new_exposure_expiry_gate_for_taker(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["min_sec_to_expiry_for_new_exposure"] = 120.0
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {"taker": 0.0}
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
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

        blocked_maker = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 60.0},
        )
        self.assertFalse(blocked_maker.allowed)
        self.assertEqual(blocked_maker.reason, "new_exposure_expiry_gate_blocked")
        self.assertEqual(str(blocked_maker.basis.get("min_sec_to_expiry_for_new_exposure_source") or ""), "global")

        allowed_taker = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "taker", "stage": "SNIPER_PRIMARY", "sec_to_expiry": 26.0},
        )
        self.assertTrue(allowed_taker.allowed)
        self.assertIsInstance(allowed_taker.basis, dict)
        self.assertAlmostEqual(
            float(allowed_taker.basis.get("min_sec_to_expiry_for_new_exposure")),
            0.0,
            places=9,
        )
        self.assertAlmostEqual(
            float(allowed_taker.basis.get("min_sec_to_expiry_for_new_exposure_global")),
            120.0,
            places=9,
        )
        self.assertEqual(str(allowed_taker.basis.get("min_sec_to_expiry_for_new_exposure_source") or ""), "lane_override")

    def test_validate_order_requires_sec_to_expiry_when_lifecycle_context_enforced(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["min_sec_to_expiry_for_new_exposure"] = 0.0
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {}
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
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

        blocked = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "NORMAL",
                "require_lifecycle_context_for_decisions": True,
            },
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "new_exposure_sec_to_expiry_unknown_blocked")
        self.assertIsInstance(blocked.basis, dict)
        self.assertTrue(bool(blocked.basis.get("require_lifecycle_context_for_decisions")))

        allowed_when_disabled = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "NORMAL",
                "require_lifecycle_context_for_decisions": False,
            },
        )
        self.assertTrue(allowed_when_disabled.allowed)

    def test_validate_order_blocks_normal_taker_same_token_sell_and_removed_recovery_sell(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        positions = {"t1": Position(token_id="t1", net_shares=0.0), "t2": Position(token_id="t2", net_shares=5.0)}
        risk = RiskEngine(cfg, positions)
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        top_reduce = BookTop(
            token_id="t2",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.49,
            best_bid_size=100,
            best_ask_price=0.51,
            best_ask_size=100,
        )
        from prodesk.models import OrderIntent

        blocked = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "taker",
                "stage": "SNIPER_PRIMARY",
                "financial_posture_class": "NORMAL",
                "sec_to_expiry": 90.0,
            },
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "normal_taker_same_token_sell_forbidden")
        self.assertEqual(str((blocked.basis or {}).get("risk_authority") or ""), "taker_side_policy")

        maker_verdict = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "NORMAL",
                "sec_to_expiry": 90.0,
            },
        )
        self.assertTrue(maker_verdict.allowed)

        reduce_only_allowed = risk.validate_order(
            OrderIntent(token_id="t2", side="SELL", price=0.5, size=3.0),
            top=top_reduce,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "taker",
                "stage": "SNIPER_PRIMARY",
                "financial_posture_class": "NORMAL",
                "sec_to_expiry": 90.0,
            },
        )
        self.assertFalse(reduce_only_allowed.allowed)
        self.assertEqual(reduce_only_allowed.reason, "normal_taker_same_token_sell_forbidden")

    def test_validate_order_halt_new_risk_blocks_increase_and_does_not_reopen_taker_sell(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        positions = {"t1": Position(token_id="t1", net_shares=5.0)}
        risk = RiskEngine(cfg, positions)
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

        blocked_increase = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "taker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 30.0,
            },
        )
        self.assertFalse(blocked_increase.allowed)
        self.assertEqual(blocked_increase.reason, "terminal_unwind_halt_new_risk_blocked")
        self.assertEqual(str(blocked_increase.basis.get("risk_authority") or ""), "terminal_unwind_halt_new_risk")

        blocked_reduce = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "taker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 30.0,
            },
        )
        self.assertFalse(blocked_reduce.allowed)
        self.assertEqual(blocked_reduce.reason, "normal_taker_same_token_sell_forbidden")

        blocked_cross = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=6.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "taker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 30.0,
            },
        )
        self.assertFalse(blocked_cross.allowed)
        self.assertEqual(blocked_cross.reason, "normal_taker_same_token_sell_forbidden")
        self.assertEqual(str((blocked_cross.basis or {}).get("risk_authority") or ""), "taker_side_policy")

    def test_validate_order_removed_terminal_reduce_only_notional_exemption_is_no_longer_available(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["min_order_size"] = 5.0
        positions = {"t1": Position(token_id="t1", net_shares=4.5)}
        risk = RiskEngine(cfg, positions)
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

        blocked_terminal_reduce = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=4.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 20.0,
            },
        )
        self.assertFalse(blocked_terminal_reduce.allowed)
        self.assertEqual(blocked_terminal_reduce.reason, "size_too_small")

        blocked_non_terminal = risk.validate_order(
            OrderIntent(token_id="t1", side="SELL", price=0.5, size=4.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "NORMAL",
                "sec_to_expiry": 20.0,
            },
        )
        self.assertFalse(blocked_non_terminal.allowed)
        self.assertEqual(blocked_non_terminal.reason, "size_too_small")

        blocked_risk_increase = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=4.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={
                "submission_lane": "maker",
                "stage": "MAKER_TAKER_SELECTIVE",
                "financial_posture_class": "HALT_NEW_RISK",
                "sec_to_expiry": 20.0,
            },
        )
        self.assertFalse(blocked_risk_increase.allowed)
        self.assertEqual(blocked_risk_increase.reason, "terminal_unwind_halt_new_risk_blocked")

    def test_validate_order_removed_recovery_rate_reservation_is_ignored(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)["risk"]
        cfg["max_book_age_sec"] = 5.0
        cfg["max_orders_per_min"] = 3
        cfg["order_rate_recovery_reserved_slots"] = 1
        cfg["min_sec_to_expiry_for_new_exposure_by_lane"] = {}
        positions = {"t1": Position(token_id="t1", net_shares=2.0)}
        risk = RiskEngine(cfg, positions)
        now = risk._monotonic()  # pylint: disable=protected-access
        risk.order_timestamps.append(now)
        risk.order_timestamps.append(now)
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

        allowed_after_removed_reservation = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=1.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "stage": "MAKER_TAKER_SELECTIVE", "sec_to_expiry": 90.0},
        )
        self.assertTrue(allowed_after_removed_reservation.allowed)

    def test_validate_order_rejects_stale_book(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 0.001
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        top = BookTop(
            token_id="t1",
            ts_utc="2000-01-01T00:00:00Z",
            source="test",
            best_bid_price=0.4,
            best_bid_size=1,
            best_ask_price=0.6,
            best_ask_size=1,
        )
        from prodesk.models import OrderIntent

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=5),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "stale_book")

    def test_validate_order_rejects_future_book_timestamp(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["max_book_future_skew_sec"] = 0.5
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        future_ts = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
        top = BookTop(
            token_id="t1",
            ts_utc=future_ts.isoformat().replace("+00:00", "Z"),
            source="test",
            best_bid_price=0.4,
            best_bid_size=1,
            best_ask_price=0.6,
            best_ask_size=1,
        )
        from prodesk.models import OrderIntent

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=5),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "future_book_timestamp")

    def test_validate_order_rejects_notional_cap_per_side(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["max_notional_per_token"] = 5.0
        cfg["exposure_cap_mode"] = "per_side"
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
        top = BookTop(
            token_id="t1",
            ts_utc=utc_iso(),
            source="test",
            best_bid_price=0.4,
            best_bid_size=1,
            best_ask_price=0.6,
            best_ask_size=1,
        )
        from prodesk.models import OrderIntent

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=20),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "notional_cap_long")

    def test_validate_order_rejects_when_pending_same_side_exceeds_position_cap(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["max_abs_position_shares"] = 100.0
        positions = {"t1": Position(token_id="t1", net_shares=0.0)}
        risk = RiskEngine(cfg, positions)
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

        pending = LiveOrder(
            order_id="o-1",
            token_id="t1",
            side="BUY",
            price=0.5,
            size=90.0,
            remaining_size=90.0,
            status="OPEN",
        )
        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=20.0),
            top=top,
            open_orders_for_token=[pending],
            open_orders_total=1,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "position_cap")

    def test_dynamic_risk_scaling_unknown_input_does_not_allow_aggressive_uplift(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["dynamic_scaling"]["enabled"] = True
        cfg["dynamic_scaling"]["edge_enabled"] = True
        cfg["dynamic_scaling"]["edge_mult_max"] = 1.25
        cfg["dynamic_scaling"]["volatility_enabled"] = True
        cfg["dynamic_scaling"]["volatility_low_mult"] = 1.05
        cfg["dynamic_scaling"]["unknown_input_policy"] = "no_aggressive_uplift"
        positions = {"t1": Position(token_id="t1")}
        risk = RiskEngine(cfg, positions)
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

        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.50, size=5.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            risk_context={"submission_lane": "maker", "realized_volatility": 0.001, "sec_to_expiry": 90.0},
        )
        self.assertTrue(decision.allowed)
        self.assertIsInstance(decision.basis, dict)
        dynamic_scaling = decision.basis.get("dynamic_scaling") if isinstance(decision.basis, dict) else {}
        unknown_inputs = dynamic_scaling.get("unknown_inputs")
        self.assertIsInstance(unknown_inputs, list)
        self.assertIn("edge_abs", unknown_inputs)
        self.assertLessEqual(float(dynamic_scaling.get("effective_multiplier", 0.0)), 1.0)
        self.assertNotEqual(dynamic_scaling.get("scaling_class"), "aggressive")

    def test_global_exposure_guard_rejects_combined_projected_exposure(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["global_exposure_guard"]["enabled"] = True
        cfg["global_exposure_guard"]["max_global_notional_usd"] = 30.0
        positions = {
            "t1": Position(token_id="t1", net_shares=40.0, buy_shares=40.0, bought_notional=20.0),
            "t2": Position(token_id="t2"),
        }
        risk = RiskEngine(cfg, positions)
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

        resting = LiveOrder(
            order_id="o-1",
            token_id="t2",
            side="BUY",
            price=0.5,
            size=30.0,
            remaining_size=30.0,
            status="OPEN",
        )
        decision = risk.validate_order(
            OrderIntent(token_id="t1", side="BUY", price=0.5, size=20.0),
            top=top,
            open_orders_for_token=[],
            open_orders_total=1,
            open_orders_all=[resting],
            reference_mid_by_token={"t1": 0.5, "t2": 0.5},
            risk_context={"submission_lane": "maker", "edge_abs": 0.2, "sec_to_expiry": 90.0},
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "global_exposure_cap")
        self.assertIsInstance(decision.basis, dict)
        guard = decision.basis.get("global_exposure_guard") if isinstance(decision.basis, dict) else {}
        self.assertGreater(float(guard.get("projected_total_notional", 0.0)), float(guard.get("effective_cap_usd", 0.0)))

    def test_wallet_guardian_order_law_snapshot_reports_global_exposure(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["global_exposure_guard"]["enabled"] = True
        cfg["global_exposure_guard"]["max_global_notional_usd"] = 30.0
        positions = {
            "t1": Position(token_id="t1", net_shares=40.0, buy_shares=40.0, bought_notional=20.0),
            "t2": Position(token_id="t2"),
        }
        risk = RiskEngine(cfg, positions)
        resting = LiveOrder(
            order_id="o-1",
            token_id="t2",
            side="BUY",
            price=0.5,
            size=30.0,
            remaining_size=30.0,
            status="OPEN",
        )
        snapshot = risk.wallet_guardian_order_law_snapshot(
            intent=OrderIntent(token_id="t1", side="BUY", price=0.5, size=20.0),
            open_orders_all=[resting],
            reference_mid_by_token={"t1": 0.5, "t2": 0.5},
            risk_context={"submission_lane": "maker", "edge_abs": 0.2, "sec_to_expiry": 90.0},
        )
        guard = snapshot.get("global_exposure_guard") or {}
        self.assertTrue(bool(guard.get("enabled")))
        self.assertFalse(bool(guard.get("within_cap", True)))
        self.assertEqual(str(snapshot.get("primary_owner") or ""), "wallet_guardian")
        self.assertEqual(str(snapshot.get("mirror_owner") or ""), "risk_engine_transition")

    def test_global_exposure_taker_reserve_applies_to_all_taker_submissions(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        cfg["global_exposure_guard"]["enabled"] = True
        cfg["global_exposure_guard"]["max_global_notional_usd"] = 30.0
        cfg["global_exposure_guard"]["taker_reserved_notional_usd"] = 5.0
        positions = {
            "t1": Position(token_id="t1", net_shares=40.0, buy_shares=40.0, bought_notional=20.0),
        }
        risk = RiskEngine(cfg, positions)
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

        intent = OrderIntent(token_id="t1", side="BUY", price=0.5, size=16.0)

        first_taker_decision = risk.validate_order(
            intent,
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            open_orders_all=[],
            reference_mid_by_token={"t1": 0.5},
            risk_context={"submission_lane": "taker", "stage": "MAKER_TAKER_SELECTIVE"},
        )
        self.assertFalse(first_taker_decision.allowed)
        self.assertEqual(first_taker_decision.reason, "global_exposure_cap")
        self.assertIsInstance(first_taker_decision.basis, dict)
        first_taker_guard = first_taker_decision.basis.get("global_exposure_guard") or {}
        self.assertTrue(bool(first_taker_guard.get("taker_reserve_applied")))

        extreme_only_taker_decision = risk.validate_order(
            intent,
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
            open_orders_all=[],
            reference_mid_by_token={"t1": 0.5},
            risk_context={"submission_lane": "taker", "stage": "EXTREME_ONLY"},
        )
        self.assertFalse(extreme_only_taker_decision.allowed)
        self.assertEqual(extreme_only_taker_decision.reason, "global_exposure_cap")
        self.assertIsInstance(extreme_only_taker_decision.basis, dict)
        extreme_only_guard = extreme_only_taker_decision.basis.get("global_exposure_guard") or {}
        self.assertTrue(bool(extreme_only_guard.get("taker_reserve_applied")))

    def test_preview_order_feasibility_is_read_only_and_non_authoritative(self):
        cfg = self._risk_cfg()
        cfg["max_book_age_sec"] = 5.0
        positions = {"t1": Position(token_id="t1", net_shares=0.0)}
        risk = RiskEngine(cfg, positions)
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

        intent = OrderIntent(token_id="t1", side="BUY", price=0.5, size=10.0)
        preview = risk.preview_order_feasibility(
            intent,
            top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        final = risk.validate_order(
            intent,
            top=top,
            open_orders_for_token=[],
            open_orders_total=0,
        )
        self.assertEqual(bool(preview.allowed), bool(final.allowed))
        self.assertEqual(str(preview.reason or ""), str(final.reason or ""))
        self.assertIsInstance(preview.basis, dict)
        self.assertEqual(str((preview.basis or {}).get("preview_authority") or ""), "advisory_read_only")
        self.assertTrue(bool((preview.basis or {}).get("preview_non_authoritative")))
        self.assertIsInstance(final.basis, dict)
        self.assertIsNone((final.basis or {}).get("preview_authority"))

    def test_preflight_clock_sync_finding_when_skew_exceeded(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["check_clock_sync"] = True
        cfg["preflight"]["max_clock_skew_sec"] = 1.0
        fake_resp = mock.Mock()
        fake_resp.headers = {"Date": "Wed, 01 Jan 2020 00:00:00 GMT"}
        with mock.patch(
            "prodesk.preflight.capture_host_time_sync_snapshot",
            return_value={"available": False, "clock_state": "partial_visibility"},
        ):
            with mock.patch("prodesk.preflight.requests.Session.get", return_value=fake_resp):
                findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertIn("clock_sync_host_partial_visibility", findings)
        self.assertTrue(any(x.startswith("clock_skew_fallback_exceeded:") for x in findings))

    def test_preflight_endpoint_health_finding(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["check_endpoint_health"] = True
        cfg["preflight"]["endpoint_urls"] = ["https://example.invalid/healthz"]
        with mock.patch("prodesk.preflight.requests.Session.get", side_effect=RuntimeError("boom")):
            findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertTrue(any(x.startswith("endpoint_health_failed:") for x in findings))

    def test_live_preflight_flags_existing_guard_stop_file(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        with tempfile.TemporaryDirectory() as td:
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["runtime"]["guard_stop_file"] = str(Path(td) / "guard_stop.txt")
            Path(cfg["runtime"]["guard_stop_file"]).write_text("manual stop\n", encoding="utf-8")
            with mock.patch.dict(
                "os.environ",
                {"POLYMARKET_PRIVATE_KEY": _VALID_PK, "POLYMARKET_FUNDER": _VALID_FUNDER},
            ):
                findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)
        self.assertIn("guard_stop_file_present", findings)

    def test_live_preflight_allows_guard_stop_if_clearing_enabled(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "live"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        cfg["preflight"]["require_live_confirmation"] = False
        cfg["runtime"]["clear_guard_stop_on_start"] = True
        with tempfile.TemporaryDirectory() as td:
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["runtime"]["guard_stop_file"] = str(Path(td) / "guard_stop.txt")
            Path(cfg["runtime"]["guard_stop_file"]).write_text("manual stop\n", encoding="utf-8")
            with mock.patch.dict(
                "os.environ",
                {"POLYMARKET_PRIVATE_KEY": _VALID_PK, "POLYMARKET_FUNDER": _VALID_FUNDER},
            ):
                findings = run_preflight_checks(cfg, mode_override="live", confirm_live=True)
        self.assertNotIn("guard_stop_file_present", findings)

    def test_preflight_flags_guard_stop_directory_path(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        with tempfile.TemporaryDirectory() as td:
            cfg["storage"]["log_dir"] = td
            cfg["storage"]["state_path"] = str(Path(td) / "state.json")
            cfg["runtime"]["guard_stop_file"] = td
            findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertIn("guard_stop_path_is_directory", findings)

    def test_preflight_includes_security_findings(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["token_ids"] = ["abc"]
        cfg["preflight"]["check_market_data"] = False
        cfg["market_data"]["clob_url"] = "https://example.com"
        findings = run_preflight_checks(cfg, mode_override="paper", confirm_live=False)
        self.assertTrue(
            any(x.startswith("security.host_not_allowlisted:market_data.clob_url:example.com") for x in findings)
        )


if __name__ == "__main__":
    unittest.main()
