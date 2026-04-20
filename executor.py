#!/usr/bin/env python3
"""Execution runner for paper/live Polymarket market making."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import os
import pathlib
import resource
import signal
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from prodesk.alerts import AlertNotifier
from prodesk.book_feed import MarketBookFeed, MarketBookFeedError
from prodesk.chainlink_feed import ChainlinkFeed, ChainlinkFeedError
from prodesk.canonical_authority import (
    ACTOR_EXECUTOR,
    CAPABILITY_EXECUTOR_RUN,
    AuthorityRequest,
    render_authority_denial,
    resolve_authority_decision,
)
from prodesk.common import parse_float, parse_ts, utc_iso, utc_now
from prodesk.config import load_execution_config, validate_execution_config
from prodesk.edge_truth_contract import (
    EDGE_ACTIONS,
    EDGE_ACTION_MAKER,
    EDGE_ACTION_NONE,
    EDGE_ACTION_TAKER,
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPES,
    EDGE_EVAL_SCOPE_TAKER,
    compute_edge_value,
    is_canonical_block_reason,
    validate_edge_inputs,
    EdgeInputSnapshot,
    stage_policy as edge_stage_policy,
)
from prodesk.exposure_classifier import (
    EXPOSURE_CLASS_DUST_ELIGIBLE,
    EXPOSURE_CLASS_DUST_QUARANTINED,
    EXPOSURE_CLASS_MEANINGFUL,
    ExposureClassifierConfig,
    classify_exposure_fail_closed,
    exposure_class_to_dict,
)
from prodesk.gateway import BaseGateway, GatewayError, LiveClobGateway, PaperGateway
from prodesk.logging_utils import EventLogger, configure_console_logging
from prodesk.latency_verifier import (
    STATE_ARMED,
    STATE_DISARMED,
    STATE_PROBATION,
    LatencySnapshot,
    LatencyVerifier,
)
from prodesk.market_discovery import MarketDiscovery
from prodesk.market_data import RestBookClient
from prodesk.models import Position
from prodesk.operating_mode import (
    MODE_CAUTIOUS,
    MODE_MAKER_ONLY,
    MODE_NORMAL,
    MODE_SAFE_STOP,
    OperatingModeController,
)
from prodesk.order_manager import OrderManager
from prodesk.pyth_feed import PythFeed
from prodesk.preflight import run_preflight_checks
from prodesk.prometheus_exporter import PrometheusExporter, PrometheusExporterError
from prodesk.repo import current_git_commit, current_git_dirty, resolve_repo_root
from prodesk.ramp_controller import SizeRampController
from prodesk.risk import RiskEngine
from prodesk.runtime_semantics import cycle_semantics, runtime_state_to_gauge
from prodesk.state_store import load_state, save_state
from prodesk.strategy import MarketMakingStrategy
from prodesk.sniper_tool import SniperCandidate, SniperDecision, SniperTool, SniperToolConfig
from prodesk.telemetry import Telemetry
from prodesk.tx_manager import TransactionManager
from prodesk.volatility import RealizedVolTracker
from prodesk.wallet_doctrine import create_wallet_doctrine


LOG = logging.getLogger("executor")

STAGE_OBSERVE = "OBSERVE"
STAGE_EVALUATE = "EVALUATE"
STAGE_MAKER_POSITION = "MAKER_POSITION"
STAGE_MAKER_TAKER_SELECTIVE = "MAKER_TAKER_SELECTIVE"
STAGE_SNIPER_PRIMARY = "SNIPER_PRIMARY"
STAGE_EXTREME_ONLY = "EXTREME_ONLY"
STAGE_EXPIRED = "EXPIRED"
STAGE_UNKNOWN = "UNKNOWN"
HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE = "preexpiry_fetch_failure"
HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED = "postexpiry_market_retired"
HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP = "unknown_data_gap"
FINANCIAL_POSTURE_NORMAL = "NORMAL"
FINANCIAL_POSTURE_PREEXPIRY_REDUCE_ONLY = "PREEXPIRY_REDUCE_ONLY"
FINANCIAL_POSTURE_HARD_DEGRADED_REDUCE_ONLY = "HARD_DEGRADED_REDUCE_ONLY"
FINANCIAL_POSTURE_HALT_NEW_RISK = "HALT_NEW_RISK"
RUN_MANIFEST_REQUIRED_FIELDS = (
    "manifest_schema_version",
    "ts_utc",
    "start_ts",
    "run_id",
    "profile_name",
    "mode",
    "status_path",
    "events_path",
    "config_fingerprint_sha256",
    "code_fingerprint_sha256",
)


class ExecutionRunner:
    _VALUATION_QUOTE_MIN = 0.0
    _VALUATION_QUOTE_MAX = 1.0
    _VALUATION_QUOTE_EPS = 1e-9

    def __init__(self, config: Dict[str, Any], *, config_source_path: Optional[pathlib.Path] = None):
        self.cfg = config
        self.config_source_path = config_source_path.resolve() if config_source_path is not None else None
        self.bot_name = str(self.cfg.get("bot_name", "Bro")).strip() or "Bro"
        explicit_run_id = str(os.getenv("BRO_RUN_ID", "")).strip()
        if explicit_run_id:
            try:
                uuid.UUID(explicit_run_id)
            except Exception as exc:
                raise ValueError(f"invalid BRO_RUN_ID (must be UUID): {explicit_run_id!r}") from exc
        canonical_session_call = str(os.getenv("BRO_CANONICAL_SESSION_CALL", "0")).strip() == "1"
        paper_mode = str(self.cfg.get("mode", "")).strip().lower() == "paper"
        if canonical_session_call and paper_mode and not explicit_run_id:
            raise ValueError("BRO_RUN_ID required for canonical paper execution")
        self.run_id = explicit_run_id or str(uuid.uuid4())
        storage_cfg = self.cfg["storage"]
        self.log_dir = pathlib.Path(storage_cfg["log_dir"]).resolve()
        self.state_path = pathlib.Path(storage_cfg["state_path"]).resolve()
        self.run_manifest_path = self.log_dir / f"run_manifest_{self.run_id}.json"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.repo_root = resolve_repo_root(start=pathlib.Path(__file__).resolve().parent)
        self.profile_name = str(self.cfg.get("profile", {}).get("name", "")).strip() or "default"
        self.config_fingerprint_sha256 = str(self.cfg.get("_meta", {}).get("effective_config_sha256", "")).strip()

        runtime_cfg = self.cfg.get("runtime", {})
        self.paper_enforce_setup_lock = bool(runtime_cfg.get("paper_enforce_setup_lock", False))
        self.paper_expected_profile_name = str(runtime_cfg.get("paper_expected_profile_name", "")).strip()
        self.paper_expected_config_fingerprint_sha256 = str(
            runtime_cfg.get("paper_expected_config_fingerprint_sha256", "")
        ).strip().lower()
        self.events = EventLogger(
            self.log_dir,
            writer_cfg={
                "async_flush": bool(runtime_cfg.get("log_async_flush", False)),
                "flush_every_records": int(runtime_cfg.get("log_flush_every_records", 1)),
                "flush_interval_sec": float(runtime_cfg.get("log_flush_interval_sec", 0.25)),
                "fsync_on_flush": bool(runtime_cfg.get("log_fsync_on_flush", False)),
            },
            default_fields={
                "run_id": self.run_id,
                "profile_name": self.profile_name,
                "config_fingerprint_sha256": self.config_fingerprint_sha256,
            },
        )
        self.alerts = AlertNotifier(self.cfg["alerts"])
        self.telemetry = Telemetry()
        self.token_ids = [str(x) for x in self.cfg["targets"]["token_ids"]]
        self._base_runtime_poll_interval_sec = float(self.cfg["runtime"]["poll_interval_sec"])
        self._base_runtime_actions_per_cycle = int(self.cfg["runtime"]["max_actions_per_cycle"])
        self.log_book_top = bool(self.cfg["runtime"].get("log_book_top", True))
        self.log_leadlag_book_move = bool(self.cfg["runtime"].get("log_leadlag_book_move", True))
        self.persist_seen_trade_ids_max = int(self.cfg["runtime"].get("persist_seen_trade_ids_max", 5000))
        guard_stop_file_raw = str(runtime_cfg.get("guard_stop_file", "")).strip()
        self.guard_stop_file: Optional[pathlib.Path] = (
            pathlib.Path(guard_stop_file_raw).resolve() if guard_stop_file_raw else None
        )
        self.clear_guard_stop_on_start = bool(runtime_cfg.get("clear_guard_stop_on_start", False))
        self._last_external_guard_active = False
        self._last_external_guard_reason = ""
        self._external_guard_error_log_interval_sec = 30.0
        self._external_guard_last_error_log_mono = 0.0
        self._runtime_state = "initializing"
        self._runtime_active_targets_present = bool(self.token_ids)
        self._runtime_no_target_standdown = False
        self._runtime_book_feed_required = bool(self.token_ids)
        self._runtime_promotion_eligibility_hint = False
        self._order_submission_attempts_last_cycle = 0
        self._state_save_error_log_interval_sec = 30.0
        self._state_save_last_error_log_mono = 0.0
        self.chainlink = ChainlinkFeed(self.cfg.get("chainlink", {}))
        secondary_oracle_cfg = self.cfg.get("secondary_oracle", {})
        if not isinstance(secondary_oracle_cfg, dict):
            secondary_oracle_cfg = {}
        pyth_cfg = secondary_oracle_cfg.get("pyth", {})
        if not isinstance(pyth_cfg, dict):
            pyth_cfg = {}
        self.pyth = PythFeed(pyth_cfg)
        self.pyth_symbol_for_targets = str(pyth_cfg.get("symbol", "BTC/USD")).strip() or "BTC/USD"
        self.book_feed = MarketBookFeed(self.cfg.get("market_data", {}).get("ws", {}))
        self.last_midpoint_by_token: Dict[str, Optional[float]] = {}
        self.last_midpoint_ts_mono_by_token: Dict[str, float] = {}
        self.last_volatility_by_token: Dict[str, float] = {}
        risk_cfg = self.cfg.get("risk", {})
        if not isinstance(risk_cfg, dict):
            risk_cfg = {}
        self.live_mid_max_age_sec = max(0.0, float(risk_cfg.get("max_book_age_sec", 0.0)))
        configured_one_sided_mid_age = parse_float(risk_cfg.get("one_sided_quote_max_age_sec"))
        self.one_sided_quote_max_age_sec = (
            max(0.0, float(configured_one_sided_mid_age))
            if configured_one_sided_mid_age is not None
            else 6.0
        )
        configured_last_known_mid_age = parse_float(risk_cfg.get("last_known_mid_max_age_sec"))
        self.last_known_mid_max_age_sec = (
            max(0.0, float(configured_last_known_mid_age))
            if configured_last_known_mid_age is not None
            else 6.0
        )
        self.position_dust_shares_epsilon = max(
            0.0,
            float(parse_float(risk_cfg.get("position_dust_shares_epsilon")) or 0.0),
        )
        self.position_dust_notional_usd_epsilon = max(
            0.0,
            float(parse_float(risk_cfg.get("position_dust_notional_usd_epsilon")) or 0.0),
        )
        self.position_dust_total_notional_usd_cap = max(
            0.0,
            float(parse_float(risk_cfg.get("position_dust_total_notional_usd_cap")) or 0.0),
        )
        self.position_dust_token_count_cap = max(
            1,
            int(float(parse_float(risk_cfg.get("position_dust_token_count_cap")) or 1.0)),
        )
        self.position_dust_max_age_sec = max(
            0.0,
            float(parse_float(risk_cfg.get("position_dust_max_age_sec")) or 0.0),
        )
        self.position_dust_enter_consecutive_cycles = max(
            1,
            int(float(parse_float(risk_cfg.get("position_dust_enter_consecutive_cycles")) or 1.0)),
        )
        self.position_dust_clear_consecutive_cycles = max(
            1,
            int(float(parse_float(risk_cfg.get("position_dust_clear_consecutive_cycles")) or 1.0)),
        )
        self._dust_classifier_cfg = ExposureClassifierConfig(
            dust_shares_epsilon=float(self.position_dust_shares_epsilon),
            dust_notional_usd_epsilon=float(self.position_dust_notional_usd_epsilon),
            dust_total_notional_usd_cap=float(self.position_dust_total_notional_usd_cap),
            dust_token_count_cap=int(self.position_dust_token_count_cap),
            dust_max_age_sec=float(self.position_dust_max_age_sec),
            dust_enter_consecutive_cycles=int(self.position_dust_enter_consecutive_cycles),
            dust_clear_consecutive_cycles=int(self.position_dust_clear_consecutive_cycles),
        )
        self.risk_min_order_size_shares = max(1e-9, float(risk_cfg.get("min_order_size", 1.0)))
        configured_held_unpriceable_escalation_sec = parse_float(risk_cfg.get("held_unpriceable_escalation_sec"))
        self.held_unpriceable_escalation_sec = (
            max(0.0, float(configured_held_unpriceable_escalation_sec))
            if configured_held_unpriceable_escalation_sec is not None
            else 120.0
        )
        self._valuation_degraded = False
        self._valuation_hard_degraded = False
        self._pnl_degraded = False
        self._loss_guard_degraded = False
        self._valuation_degraded_reasons: List[str] = []
        self._valuation_mid_source_counts: Dict[str, int] = {}
        self._valuation_mid_source_counts_raw: Dict[str, int] = {}
        self._valuation_mid_source_by_token: Dict[str, str] = {}
        self._held_unpriceable_token_ids: List[str] = []
        self._held_unpriceable_max_age_sec: float = 0.0
        self._held_unpriceable_age_by_token: Dict[str, float] = {}
        self._held_unpriceable_escalation_active: bool = False
        self._held_unpriceable_escalation_token_ids: List[str] = []
        self._held_unpriceable_escalation_reasons: List[str] = []
        self._held_unpriceable_escalation_max_age_sec: float = 0.0
        self._held_unpriceable_operator_action: str = "none"
        self._held_unpriceable_cause_by_token: Dict[str, str] = {}
        self._held_unpriceable_cause_counts: Dict[str, int] = {}
        self._held_unpriceable_dominant_cause: str = HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        self._financial_posture_class: str = FINANCIAL_POSTURE_NORMAL
        self._last_valuation_event_signature: Optional[Tuple[Any, ...]] = None
        self._last_held_unpriceable_escalation_signature: Optional[Tuple[Any, ...]] = None
        vol_cfg = self.cfg.get("strategy", {}).get("volatility", {})
        self.vol_tracker = RealizedVolTracker(float(vol_cfg.get("window_sec", 30.0)))
        self.prometheus = PrometheusExporter(self.cfg.get("metrics", {}))
        self.discovery = MarketDiscovery(self.cfg)
        self._last_discovery_allowlist_rejected_pairs = -1
        self._last_discovery_target_count = len(self.token_ids)
        self.next_target_refresh_monotonic = 0.0
        alerts_cfg = self.cfg.get("alerts", {})
        self.alert_warn_thresholds = dict(alerts_cfg.get("warn_thresholds", {}))
        self.alert_page_thresholds = dict(alerts_cfg.get("page_thresholds", {}))
        self.alert_auto_stop_thresholds = dict(alerts_cfg.get("auto_stop_thresholds", {}))
        self.alert_transport_enabled = bool(alerts_cfg.get("enabled", False))
        self.alert_auto_stop_control_authority_enabled = bool(self.alert_auto_stop_thresholds)
        self.alert_transport_disable_control_authority_unchanged = bool(
            (not self.alert_transport_enabled) and self.alert_auto_stop_control_authority_enabled
        )
        self.alert_auto_stop_min_samples = max(1, int(alerts_cfg.get("auto_stop_min_samples", 12)))
        self.alert_auto_stop_min_stale_rejects = max(1, int(alerts_cfg.get("auto_stop_min_stale_rejects", 8)))
        self.alert_auto_stop_min_risk_rejects = max(1, int(alerts_cfg.get("auto_stop_min_risk_rejects", 24)))
        guardian_hook_raw = str(alerts_cfg.get("guardian_hook_file", "")).strip()
        self.alert_guardian_hook_file: Optional[pathlib.Path] = (
            pathlib.Path(guardian_hook_raw).resolve() if guardian_hook_raw else None
        )
        self._mode_transition_mono: collections.deque[float] = collections.deque()
        self._mode_transition_window_sec = 600.0
        self._first_stale_burst_logged = False
        self.book_not_found_backoff_sec = max(1.0, float(runtime_cfg.get("book_not_found_backoff_sec", 90.0)))
        configured_held_book_not_found_backoff = parse_float(runtime_cfg.get("held_book_not_found_backoff_sec"))
        default_held_book_not_found_backoff_sec = min(10.0, self.book_not_found_backoff_sec)
        self.held_book_not_found_backoff_sec = (
            max(1.0, float(configured_held_book_not_found_backoff))
            if configured_held_book_not_found_backoff is not None
            else default_held_book_not_found_backoff_sec
        )
        self.held_book_not_found_backoff_sec = min(self.held_book_not_found_backoff_sec, self.book_not_found_backoff_sec)
        configured_held_book_not_found_force_refresh_interval = parse_float(
            runtime_cfg.get("held_book_not_found_force_refresh_interval_sec")
        )
        self.held_book_not_found_force_refresh_interval_sec = (
            max(0.0, float(configured_held_book_not_found_force_refresh_interval))
            if configured_held_book_not_found_force_refresh_interval is not None
            else 120.0
        )
        configured_held_book_not_found_force_refresh_min_unpriceable_age = parse_float(
            runtime_cfg.get("held_book_not_found_force_refresh_min_unpriceable_age_sec")
        )
        self.held_book_not_found_force_refresh_min_unpriceable_age_sec = (
            max(0.0, float(configured_held_book_not_found_force_refresh_min_unpriceable_age))
            if configured_held_book_not_found_force_refresh_min_unpriceable_age is not None
            else 30.0
        )
        configured_held_expired_reduce_only_grace = parse_float(
            runtime_cfg.get("held_expired_reduce_only_grace_sec")
        )
        self.held_expired_reduce_only_grace_sec = (
            max(0.0, float(configured_held_expired_reduce_only_grace))
            if configured_held_expired_reduce_only_grace is not None
            else 90.0
        )
        configured_held_preexpiry_reduce_only_sec = parse_float(
            runtime_cfg.get("held_preexpiry_reduce_only_sec")
        )
        self.held_preexpiry_reduce_only_sec = (
            max(0.0, float(configured_held_preexpiry_reduce_only_sec))
            if configured_held_preexpiry_reduce_only_sec is not None
            else 90.0
        )
        configured_expiry_boundary_epsilon_sec = parse_float(
            runtime_cfg.get("expiry_boundary_epsilon_sec")
        )
        self.expiry_boundary_epsilon_sec = (
            max(0.0, float(configured_expiry_boundary_epsilon_sec))
            if configured_expiry_boundary_epsilon_sec is not None
            else 1.0
        )
        self.dust_classifier_enforce_enabled = bool(
            runtime_cfg.get("dust_classifier_enforce_enabled", False)
        )
        configured_hard_degraded_clear_hysteresis_cycles = parse_float(
            runtime_cfg.get("valuation_hard_degraded_clear_consecutive_healthy_cycles")
        )
        self.valuation_hard_degraded_clear_consecutive_healthy_cycles = (
            max(1, int(float(configured_hard_degraded_clear_hysteresis_cycles)))
            if configured_hard_degraded_clear_hysteresis_cycles is not None
            else 2
        )
        configured_held_unpriceable_operator_action_min_emit_interval_sec = parse_float(
            runtime_cfg.get("held_unpriceable_operator_action_min_emit_interval_sec")
        )
        self.held_unpriceable_operator_action_min_emit_interval_sec = (
            max(0.0, float(configured_held_unpriceable_operator_action_min_emit_interval_sec))
            if configured_held_unpriceable_operator_action_min_emit_interval_sec is not None
            else 60.0
        )
        self._valuation_hard_degraded_enter_count: int = 0
        self._valuation_hard_degraded_clear_count: int = 0
        self._valuation_hard_degraded_pending_healthy_cycles: int = 0
        self._held_unpriceable_started_count: int = 0
        self._held_unpriceable_recovered_count: int = 0
        self._preexpiry_404_anomaly_count: int = 0
        self._preexpiry_404_anomaly_last_cycle: bool = False
        self._held_exposure_class_by_token: Dict[str, str] = {}
        self._held_exposure_detail_by_token: Dict[str, Dict[str, Any]] = {}
        self._held_dust_token_ids: List[str] = []
        self._held_dust_quarantined_token_ids: List[str] = []
        self._held_dust_total_notional_upper_bound_usd: float = 0.0
        self._held_dust_shadow_candidate_active: bool = False
        self._held_dust_shadow_active: bool = False
        self._held_dust_shadow_enter_pending_cycles: int = 0
        self._held_dust_shadow_clear_pending_cycles: int = 0
        self._held_dust_enforced_this_cycle: bool = False
        self._held_dust_effective_hard_degraded_exempt_count: int = 0
        self._held_dust_raw_hard_degraded_token_count: int = 0
        self._held_unpriceable_operator_action_last_emit_mono: float = 0.0
        self._book_not_found_backoff_mono_by_token: Dict[str, float] = {}
        self._held_book_not_found_last_mono_by_token: Dict[str, float] = {}
        self._held_book_not_found_force_refresh_next_mono_by_token: Dict[str, float] = {}
        self._held_unpriceable_since_mono_by_token: Dict[str, float] = {}

        self.token_expiry_utc_by_token: Dict[str, str] = {}
        self.token_expiry_dt_by_token: Dict[str, dt.datetime] = {}
        self._apply_token_expiry_map(
            self.cfg.get("targets", {}).get("token_expiry_utc_by_token", {}),
            source="config",
        )
        self.token_side_by_token: Dict[str, str] = {}
        self._apply_token_side_map(
            self.cfg.get("targets", {}).get("token_side_by_token", {}),
            source="config",
        )
        self.token_strike_by_token: Dict[str, float] = {}
        self._apply_token_strike_map(
            self.cfg.get("targets", {}).get("token_strike_by_token", {}),
            source="config",
        )
        self.token_market_key_by_token: Dict[str, str] = {}
        doctrine_cfg = self.cfg.get("doctrine", {})
        self.doctrine_mode = str(doctrine_cfg.get("mode", "canonical")).strip().lower() or "canonical"
        self.doctrine_oracle_max_tick_age_sec = float(
            doctrine_cfg.get("oracle_max_tick_age_sec", self.cfg.get("sniper", {}).get("max_chainlink_tick_age_sec", 1.5))
        )
        self.doctrine_maker_allow_bounded_single_side_reference = bool(
            doctrine_cfg.get("maker_allow_bounded_single_side_reference", True)
        )
        self._maker_market_reference_policy: Dict[str, Any] = {
            "direct_mode": "direct_midpoint",
            "bounded_fallback_mode": (
                "bounded_single_side_touch" if self.doctrine_maker_allow_bounded_single_side_reference else "disabled"
            ),
            "activation_requires": [
                "doctrine_mode_canonical",
                "evaluation_scope_maker",
                "book_source_ws",
                "midpoint_missing",
                "single_side_present",
                "maker_prereq_pass",
            ],
            "fallback_claim_class": "bounded_approximation",
            "missing_fallback_behavior": "fail_closed_market_probability_missing",
        }
        preflight_cfg = self.cfg.get("preflight", {}) if isinstance(self.cfg.get("preflight"), dict) else {}
        self._time_policy: Dict[str, Any] = {
            "source_of_truth": "utc_wall_clock",
            "fallback_logic": "source_ts_utc_then_ts_receive_utc_then_ts_event_utc",
            "skew_tolerance_ms": max(0.0, float(preflight_cfg.get("max_clock_skew_sec", 0.0) or 0.0) * 1000.0),
            "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
        }
        self.doctrine_min_observe_cycles_on_entry = max(0, int(doctrine_cfg.get("min_observe_cycles_on_entry", 2)))
        self.doctrine_min_observe_seconds_on_entry = max(
            0.0, float(doctrine_cfg.get("min_observe_seconds_on_entry", 2.0))
        )
        self._doctrine_cycle_index = 0
        self._market_entry_mono_by_token: Dict[str, float] = {}
        self._market_entry_cycle_by_token: Dict[str, int] = {}
        self._last_doctrine_signature_by_token: Dict[str, Tuple[str, str, str, bool, bool]] = {}
        self._last_doctrine_prereq_failure_by_token: Dict[str, str] = {}
        self._last_stage_by_token: Dict[str, str] = {}
        self._last_degraded_expiry_fallback_active = False

        sniper_cfg = self.cfg.get("sniper", {})
        self.sniper_enabled = bool(sniper_cfg.get("enabled", False))
        self.sniper_arming_horizon_sec = float(
            sniper_cfg.get("arming_horizon_sec", sniper_cfg.get("window_start_sec", 20.0))
        )
        self.sniper_execution_cutoff_sec = float(
            sniper_cfg.get("execution_cutoff_sec", sniper_cfg.get("window_end_sec", 15.0))
        )
        self.sniper_late_fire_priority_band_sec = float(
            sniper_cfg.get("late_fire_priority_band_sec", min(self.sniper_execution_cutoff_sec, 5.0))
        )
        # Backward-compatible aliases used by some tests.
        self.sniper_window_start_sec = self.sniper_arming_horizon_sec
        self.sniper_window_end_sec = self.sniper_execution_cutoff_sec
        self.sniper_allow_without_expiry_metadata = bool(sniper_cfg.get("allow_without_expiry_metadata", False))
        self.sniper_poll_interval_sec = float(sniper_cfg.get("poll_interval_sec", 0.2))
        self.sniper_max_actions_per_cycle = int(sniper_cfg.get("max_actions_per_cycle", 16))
        self.sniper_cancel_stale_action_budget = int(sniper_cfg.get("cancel_stale_action_budget", 6))
        self.sniper_cancel_orphan_action_budget = int(sniper_cfg.get("cancel_orphan_action_budget", 12))
        self.sniper_order_rate_soft_limit_pct = float(sniper_cfg.get("order_rate_soft_limit_pct", 1.0))
        self.sniper_cancel_rate_soft_limit_pct = float(sniper_cfg.get("cancel_rate_soft_limit_pct", 1.0))
        self.sniper_require_lag_verification = bool(sniper_cfg.get("require_lag_verification", True))
        self.sniper_max_chainlink_tick_age_sec = float(sniper_cfg.get("max_chainlink_tick_age_sec", 1.5))
        self.sniper_fair_vol_scale = float(sniper_cfg.get("fair_vol_scale", 1.0))
        self.chainlink_latency_sample_mid_move_min_delta = float(
            self.cfg.get("chainlink", {}).get(
                "latency_sample_mid_move_min_delta",
                self.cfg.get("chainlink", {}).get("mid_move_min_delta", 0.001),
            )
        )
        verifier_cfg = dict(self.cfg.get("latency_verifier", {}))
        if "window_samples" not in verifier_cfg:
            verifier_cfg["window_samples"] = int(sniper_cfg.get("lag_window_samples", 300))
        if "min_samples" not in verifier_cfg:
            verifier_cfg["min_samples"] = int(sniper_cfg.get("lag_min_samples", 80))
        if "hit_threshold_ms" not in verifier_cfg:
            verifier_cfg["hit_threshold_ms"] = float(sniper_cfg.get("lag_hit_threshold_ms", 120.0))
        if "armed_min_median_ms" not in verifier_cfg:
            verifier_cfg["armed_min_median_ms"] = float(sniper_cfg.get("lag_min_median_ms", 120.0))
        if "armed_min_hit_rate" not in verifier_cfg:
            verifier_cfg["armed_min_hit_rate"] = float(sniper_cfg.get("lag_min_hit_rate", 0.6))
        self.latency_verifier = LatencyVerifier(verifier_cfg)
        taker_cfg = sniper_cfg.get("taker", {})
        self.sniper_taker_enabled = bool(taker_cfg.get("enabled", False))
        self.sniper_taker_min_edge = float(taker_cfg.get("min_edge", 0.015))
        raw_min_edge_by_stage = taker_cfg.get("min_edge_by_stage", {})
        self.sniper_taker_min_edge_by_stage: Dict[str, float] = {}
        if isinstance(raw_min_edge_by_stage, dict):
            for stage_name, edge_value in raw_min_edge_by_stage.items():
                normalized_stage = str(stage_name or "").strip().upper()
                if not normalized_stage:
                    continue
                try:
                    normalized_edge = float(edge_value)
                except (TypeError, ValueError):
                    continue
                if normalized_edge < 0.0:
                    continue
                self.sniper_taker_min_edge_by_stage[normalized_stage] = normalized_edge
        self.sniper_taker_extreme_edge_mult = float(taker_cfg.get("extreme_edge_mult", 2.0))
        self.sniper_taker_order_size = float(taker_cfg.get("order_size", 20.0))
        self.sizing_mode = str(self.cfg.get("sizing", {}).get("mode", "shares")).strip().lower()
        self.sniper_taker_target_usd = float(
            taker_cfg.get("target_usd", self.cfg.get("sizing", {}).get("target_usd", 5.0))
        )
        self._active_target_usd = float(self.cfg.get("sizing", {}).get("target_usd", self.sniper_taker_target_usd))
        ramp_cfg = self.cfg.get("ramp", {})
        self.ramp = SizeRampController(ramp_cfg, base_target_usd=self._active_target_usd)
        self._active_target_usd = float(self.ramp.target_usd)
        self._sniper_ramp_allowed = bool(self.ramp.sniper_allowed)
        reconcile_status_path_raw = str(ramp_cfg.get("reconcile_status_path", "")).strip()
        if reconcile_status_path_raw:
            self.ramp_reconcile_status_path: Optional[pathlib.Path] = pathlib.Path(reconcile_status_path_raw).resolve()
        else:
            self.ramp_reconcile_status_path = self.log_dir / "reconcile_latest.json"
        self._reconcile_status_poll_interval_sec = 15.0
        self._last_reconcile_status_poll_mono = 0.0
        self._cached_reconcile_mismatch_ratio = 0.0
        self.sniper_taker_max_orders_per_cycle = int(taker_cfg.get("max_orders_per_cycle", 2))
        self.sniper_taker_per_token_cooldown_sec = float(taker_cfg.get("per_token_cooldown_sec", 0.25))
        raw_taker_stage_cooldown = taker_cfg.get("per_token_cooldown_sec_by_stage", {})
        self.sniper_taker_per_token_cooldown_sec_by_stage: Dict[str, float] = {}
        if isinstance(raw_taker_stage_cooldown, dict):
            for stage_name, value in raw_taker_stage_cooldown.items():
                normalized_stage = str(stage_name or "").strip().upper()
                if not normalized_stage:
                    continue
                try:
                    cooldown_sec = float(value)
                except (TypeError, ValueError):
                    continue
                if cooldown_sec < 0.0:
                    continue
                self.sniper_taker_per_token_cooldown_sec_by_stage[normalized_stage] = cooldown_sec
        taker_competitiveness_cfg = (
            taker_cfg.get("competitiveness", {}) if isinstance(taker_cfg.get("competitiveness", {}), dict) else {}
        )
        self.sniper_taker_competitiveness_cfg = SniperToolConfig.from_mapping(taker_competitiveness_cfg)
        self.sniper_tool = SniperTool(self.sniper_taker_competitiveness_cfg)
        risk_max_order_size = self.cfg.get("risk", {}).get("max_order_size")
        self.sniper_taker_max_order_size_shares = float(risk_max_order_size or 0.0)
        self.sniper_taker_sizing_max_usd = float(self.cfg.get("sizing", {}).get("max_usd", self.sniper_taker_target_usd))
        self.sniper_taker_wallet_max_notional_per_order_usdc = float(
            self.cfg.get("wallet", {}).get("max_notional_per_order_usdc", 0.0)
        )
        multi_oracle_capital_pct_cap = float(self.sniper_taker_competitiveness_cfg.multi_oracle_capital_pct_cap)
        wallet_cfg_for_sniper = self.cfg.get("wallet", {})
        if not isinstance(wallet_cfg_for_sniper, dict):
            wallet_cfg_for_sniper = {}
        paper_starting_usdc = parse_float(wallet_cfg_for_sniper.get("paper_starting_usdc"))
        protected_usdc_reserve = parse_float(wallet_cfg_for_sniper.get("protected_usdc_reserve"))
        capital_base_usd = None
        if paper_starting_usdc is not None:
            capital_base_usd = max(0.0, float(paper_starting_usdc))
            if protected_usdc_reserve is not None:
                capital_base_usd = max(0.0, capital_base_usd - max(0.0, float(protected_usdc_reserve)))
        self.sniper_taker_multi_oracle_cap_usd: Optional[float] = None
        self.sniper_taker_multi_oracle_cap_source = "disabled"
        self.sniper_taker_multi_oracle_cap_authority_class = "none"
        if multi_oracle_capital_pct_cap > 0.0 and isinstance(capital_base_usd, float) and capital_base_usd > 0.0:
            self.sniper_taker_multi_oracle_cap_usd = float(multi_oracle_capital_pct_cap * capital_base_usd)
            self.sniper_taker_multi_oracle_cap_source = "orchestration_heuristic_config_fallback"
            self.sniper_taker_multi_oracle_cap_authority_class = "derived"
        maker_comp_cfg = self.cfg.get("strategy", {}).get("maker_competitiveness", {})
        if not isinstance(maker_comp_cfg, dict):
            maker_comp_cfg = {}
        self.maker_comp_timing_gate_enabled = bool(maker_comp_cfg.get("timing_gate_enabled", False))
        self.maker_comp_timing_gate_min_sec_to_expiry = float(
            maker_comp_cfg.get("timing_gate_min_sec_to_expiry", 45.0)
        )
        self.maker_comp_timing_gate_max_sec_to_expiry = float(
            maker_comp_cfg.get("timing_gate_max_sec_to_expiry", 60.0)
        )
        self.maker_comp_edge_scale_enabled = bool(maker_comp_cfg.get("edge_scale_enabled", False))
        self.maker_comp_edge_scale_start_abs = float(maker_comp_cfg.get("edge_scale_start_abs", 0.05))
        self.maker_comp_edge_scale_full_abs = float(maker_comp_cfg.get("edge_scale_full_abs", 0.20))
        self.maker_comp_size_mult_max = float(maker_comp_cfg.get("size_mult_max", 1.35))
        self.maker_comp_spread_mult_min = float(maker_comp_cfg.get("spread_mult_min", 0.75))
        self.maker_comp_requote_delta_mult_min = float(maker_comp_cfg.get("requote_delta_mult_min", 0.50))
        self.maker_comp_one_sided_enabled = bool(maker_comp_cfg.get("one_sided_enabled", False))
        self.maker_comp_one_sided_edge_threshold_abs = float(
            maker_comp_cfg.get("one_sided_edge_threshold_abs", 0.18)
        )
        raw_one_sided_allowed_stages = maker_comp_cfg.get("one_sided_allowed_stages", [])
        if not isinstance(raw_one_sided_allowed_stages, list):
            raw_one_sided_allowed_stages = []
        self.maker_comp_one_sided_allowed_stages = {
            str(stage or "").strip().upper()
            for stage in raw_one_sided_allowed_stages
            if str(stage or "").strip()
        }
        self.maker_comp_base_requote_delta = max(
            1e-9,
            float(self.cfg.get("runtime", {}).get("replace_threshold", 0.005)),
        )
        chainlink_symbols = [str(x).lower().strip() for x in self.cfg.get("chainlink", {}).get("symbols", []) if str(x).strip()]
        self.chainlink_symbol_for_targets = str(
            self.cfg.get("chainlink", {}).get("symbol_for_targets", chainlink_symbols[0] if chainlink_symbols else "")
        ).lower()
        self._sniper_active = False
        self._last_latency_state: str = STATE_DISARMED
        self._latency_sampling_inactive_cycles = 0
        self._latency_sampling_inactive_log_interval_sec = 60.0
        self._latency_sampling_last_log_mono = 0.0
        self._latest_latency_snapshot = LatencySnapshot(
            state=STATE_DISARMED,
            previous_state=STATE_DISARMED,
            changed=False,
            reason="init",
            sample_count=0,
            token_count=0,
            median_lag_ms=0.0,
            p90_lag_ms=0.0,
            p95_lag_ms=0.0,
            hit_rate=0.0,
            armed=False,
            probation=False,
            disarmed=True,
        )
        self._last_taker_submit_mono_by_token: Dict[str, float] = {}
        operating_mode_cfg = self.cfg.get("operating_mode", {})
        self.operating_mode = OperatingModeController(operating_mode_cfg)
        self.operating_mode_ws_slo_enforce = bool(operating_mode_cfg.get("ws_slo_enforce_health", True))
        self.operating_mode_ws_slo_require_book_connected = bool(
            operating_mode_cfg.get("ws_slo_require_book_connected", True)
        )
        self.operating_mode_ws_slo_require_chainlink_connected = bool(
            operating_mode_cfg.get("ws_slo_require_chainlink_connected", True)
        )
        self.operating_mode_ws_slo_max_book_last_msg_age_sec = float(
            operating_mode_cfg.get("ws_slo_max_book_last_msg_age_sec", 12.0)
        )
        self.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = float(
            operating_mode_cfg.get("ws_slo_max_chainlink_last_tick_age_sec", 30.0)
        )
        self.operating_mode_ws_slo_bootstrap_grace_sec = max(
            0.0,
            float(operating_mode_cfg.get("ws_slo_bootstrap_grace_sec", 45.0)),
        )
        self._ws_slo_bootstrap_started_mono = time.monotonic()
        self._ws_slo_bootstrap_active = bool(self.token_ids) and self.operating_mode_ws_slo_bootstrap_grace_sec > 0.0
        self._last_ws_slo_bootstrap_active = self._ws_slo_bootstrap_active
        self._last_ws_slo_degraded = False
        self._last_ws_slo_reason = ""
        self._last_operating_mode_state = MODE_NORMAL

        state = self._load_state_safe()
        positions = self._restore_positions(state.get("positions", {}))
        risk_cfg = dict(self.cfg["risk"])
        risk_cfg["exposure_cap_mode"] = str(
            self.cfg.get("sizing", {}).get("exposure_cap_mode", risk_cfg.get("exposure_cap_mode", "per_market_total"))
        )
        self.risk = RiskEngine(risk_cfg, positions)
        self.strategy = MarketMakingStrategy(self.cfg["strategy"])

        mode = str(self.cfg["mode"]).lower()
        if mode == "paper":
            self.gateway: BaseGateway = PaperGateway(self.cfg.get("runtime", {}))
        else:
            self.gateway = LiveClobGateway(
                self.cfg["auth"],
                seen_trade_ids_max=int(self.cfg["runtime"].get("seen_trade_ids_max", 200000)),
            )
        if mode == "paper" and self.paper_enforce_setup_lock:
            observed_profile = str(self.profile_name).strip()
            expected_profile = str(self.paper_expected_profile_name).strip()
            if observed_profile != expected_profile:
                raise ValueError(
                    f"paper_setup_lock_profile_mismatch:observed={observed_profile}:expected={expected_profile}"
                )
            observed_fp = str(self.config_fingerprint_sha256).strip().lower()
            expected_fp = str(self.paper_expected_config_fingerprint_sha256).strip().lower()
            if observed_fp != expected_fp:
                raise ValueError(
                    "paper_setup_lock_config_fingerprint_mismatch:"
                    + f"observed={observed_fp or 'missing'}:expected={expected_fp or 'missing'}"
                )
            self.events.log_event(
                "paper_setup_lock_verified",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "profile_name": observed_profile,
                    "config_fingerprint_sha256": observed_fp,
                },
            )
        elif mode == "paper":
            if self.paper_expected_profile_name or self.paper_expected_config_fingerprint_sha256:
                self.events.log_event(
                    "paper_setup_lock_not_enforced",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "profile_name": self.profile_name,
                        "config_fingerprint_sha256": self.config_fingerprint_sha256,
                        "paper_expected_profile_name": self.paper_expected_profile_name,
                        "paper_expected_config_fingerprint_sha256": self.paper_expected_config_fingerprint_sha256,
                    },
                )
        self.tx_manager = TransactionManager(self.gateway)
        wallet_cfg = self.cfg.get("wallet", {})
        if not isinstance(wallet_cfg, dict):
            wallet_cfg = {}
        self.wallet = create_wallet_doctrine(
            wallet_cfg,
            mode=mode,
            gateway=self.gateway,
            event_logger=self.events.log_event,
            auth_cfg=(self.cfg.get("auth", {}) if isinstance(self.cfg.get("auth", {}), dict) else {}),
        )
        self.wallet.register_nonce_authority(self.tx_manager.nonce_authority())
        self.wallet.register_pending_tx_provider(self.tx_manager.pending_tx_snapshot)
        self._refresh_sniper_multi_oracle_cap_from_wallet()

        md = self.cfg["market_data"]
        self.book_client = RestBookClient(
            clob_url=str(md["clob_url"]),
            book_path=str(md["book_path"]),
            timeout_sec=float(md["timeout_sec"]),
            max_retries=int(md["max_retries"]),
        )
        self.rest_fetch_max_workers = max(1, int(self.cfg.get("runtime", {}).get("rest_fetch_max_workers", 4)))
        self._rest_fetch_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        if self.rest_fetch_max_workers > 1:
            self._rest_fetch_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.rest_fetch_max_workers,
                thread_name_prefix="rest-book",
            )

        self.manager = OrderManager(
            gateway=self.gateway,
            strategy=self.strategy,
            risk=self.risk,
            events=self.events,
            telemetry=self.telemetry,
            runtime_cfg=self.cfg["runtime"],
            strategy_cfg=self.cfg["strategy"],
            sizing_cfg=self.cfg.get("sizing", {}),
            mode=mode,
            wallet=self.wallet,
            tx_manager=self.tx_manager,
        )
        self.manager.sizing_target_usd = float(self._active_target_usd)
        if self.sizing_mode == "notional":
            self.sniper_taker_target_usd = float(self._active_target_usd)
        raw_seen_trade_ids = state.get("seen_trade_ids", [])
        if not isinstance(raw_seen_trade_ids, list):
            raw_seen_trade_ids = []
        self.manager.restore_seen_trade_ids([str(x) for x in raw_seen_trade_ids if str(x)])
        self.manager.restore_last_fill_ts(state.get("last_fill_ts_utc"))
        self.tx_manager.seed_fill_cursor(self.manager.snapshot_last_fill_ts())

        self.stop_requested = False
        self.consecutive_failures = 0
        self.last_kill_switch_state = False

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "positions": {},
            "seen_trade_ids": [],
            "last_fill_ts_utc": None,
            "last_status_ts_utc": None,
        }

    def _load_state_safe(self) -> Dict[str, Any]:
        try:
            return load_state(self.state_path)
        except Exception as exc:
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "state_store",
                    "action": "load_state",
                    "state_path": str(self.state_path),
                    "error": str(exc),
                }
            )
            return self._empty_state()

    @staticmethod
    def _metric_symbol(symbol: str) -> str:
        return symbol.lower().replace("/", "_").replace("-", "_")

    @staticmethod
    def _unique_ordered(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def _update_runtime_semantics(self, *, has_targets: bool) -> None:
        semantics = cycle_semantics(has_targets=has_targets, kill_switch=bool(self.risk.kill_switch))
        prev_state = self._runtime_state
        prev_book_required = self._runtime_book_feed_required
        self._runtime_state = semantics.runtime_state
        self._runtime_active_targets_present = bool(semantics.active_targets_present)
        self._runtime_no_target_standdown = bool(semantics.no_target_standdown)
        self._runtime_book_feed_required = bool(semantics.book_feed_required)
        self._runtime_promotion_eligibility_hint = bool(semantics.promotion_eligibility_hint)

        self.telemetry.set_gauge("runtime_state_code", runtime_state_to_gauge(self._runtime_state))
        self.telemetry.set_gauge("active_targets_present", 1.0 if self._runtime_active_targets_present else 0.0)
        self.telemetry.set_gauge("no_target_standdown", 1.0 if self._runtime_no_target_standdown else 0.0)
        self.telemetry.set_gauge("book_feed_required", 1.0 if self._runtime_book_feed_required else 0.0)
        self.telemetry.set_gauge(
            "promotion_eligibility_hint",
            1.0 if self._runtime_promotion_eligibility_hint else 0.0,
        )

        if self._runtime_state != prev_state or self._runtime_book_feed_required != prev_book_required:
            transition_reason_code = "runtime_state_changed"
            if self._runtime_state == prev_state and self._runtime_book_feed_required != prev_book_required:
                transition_reason_code = "book_requirement_changed"
            elif bool(self.risk.kill_switch):
                transition_reason_code = "kill_switch_engaged"
            elif self._runtime_state == "active":
                transition_reason_code = "targets_activated"
            elif self._runtime_state == "standdown_no_targets":
                transition_reason_code = "targets_absent"
            transition_reason_detail = (
                f"prev_state={prev_state};new_state={self._runtime_state};"
                f"prev_book_feed_required={int(bool(prev_book_required))};"
                f"book_feed_required={int(bool(self._runtime_book_feed_required))};"
                f"kill_switch={int(bool(self.risk.kill_switch))};"
                f"has_targets={int(bool(has_targets))}"
            )
            self.events.log_event(
                "runtime_state_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "previous_runtime_state": prev_state,
                    "runtime_state": self._runtime_state,
                    "active_targets_present": self._runtime_active_targets_present,
                    "no_target_standdown": self._runtime_no_target_standdown,
                    "previous_book_feed_required": bool(prev_book_required),
                    "book_feed_required": self._runtime_book_feed_required,
                    "kill_switch": bool(self.risk.kill_switch),
                    "transition_reason_code": transition_reason_code,
                    "transition_reason_detail": transition_reason_detail,
                },
            )

    def _apply_token_expiry_map(self, raw_map: Any, *, source: str) -> int:
        if not isinstance(raw_map, dict):
            return 0
        applied = 0
        for token_id_raw, expiry_raw in raw_map.items():
            token_id = str(token_id_raw).strip()
            expiry_ts = parse_ts(expiry_raw)
            if not token_id or expiry_ts is None:
                continue
            self.token_expiry_utc_by_token[token_id] = utc_iso(expiry_ts)
            self.token_expiry_dt_by_token[token_id] = expiry_ts
            applied += 1
        if applied:
            self.events.log_event(
                "token_expiry_map_update",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "source": source,
                    "applied_count": applied,
                },
            )
        return applied

    def _apply_token_side_map(self, raw_map: Any, *, source: str) -> int:
        if not isinstance(raw_map, dict):
            return 0
        applied = 0
        for token_id_raw, side_raw in raw_map.items():
            token_id = str(token_id_raw).strip()
            side = str(side_raw).strip().upper()
            if not token_id or side not in {"YES", "NO"}:
                continue
            self.token_side_by_token[token_id] = side
            applied += 1
        if applied:
            self.events.log_event(
                "token_side_map_update",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "source": source,
                    "applied_count": applied,
                },
            )
        return applied

    def _apply_token_strike_map(self, raw_map: Any, *, source: str) -> int:
        if not isinstance(raw_map, dict):
            return 0
        applied = 0
        for token_id_raw, strike_raw in raw_map.items():
            token_id = str(token_id_raw).strip()
            strike = parse_float(strike_raw)
            if not token_id or strike is None or strike <= 0:
                continue
            self.token_strike_by_token[token_id] = float(strike)
            applied += 1
        if applied:
            self.events.log_event(
                "token_strike_map_update",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "source": source,
                    "applied_count": applied,
                },
            )
        return applied

    def _fair_probability_up(self, *, spot: float, strike: float, sec_to_expiry: float) -> float:
        # Smooth logistic mapping of spot-vs-strike to up probability.
        t = max(1.0, sec_to_expiry)
        width = max(20.0, self.sniper_fair_vol_scale * 90.0 * (t / 300.0) ** 0.5)
        z = max(-20.0, min(20.0, (spot - strike) / width))
        return 1.0 / (1.0 + math.exp(-z))

    @staticmethod
    def _latency_state_to_gauge(state: str) -> float:
        if state == STATE_ARMED:
            return 2.0
        if state == STATE_PROBATION:
            return 1.0
        return 0.0

    @staticmethod
    def _operating_mode_to_gauge(state: str) -> float:
        if state == MODE_NORMAL:
            return 0.0
        if state == MODE_CAUTIOUS:
            return 1.0
        if state == MODE_MAKER_ONLY:
            return 2.0
        if state == MODE_SAFE_STOP:
            return 3.0
        return -1.0

    @staticmethod
    def _parse_threshold(value: Any) -> Optional[float]:
        parsed = parse_float(value)
        if parsed is None:
            return None
        return float(parsed)

    def _cancel_all_open_orders(self, *, event_name: str, reason: str, telemetry_counter: Optional[str] = None) -> None:
        cancel_summary = self.tx_manager.cancel_all_with_summary()
        confirmed_order_ids = [str(x) for x in cancel_summary.get("confirmed_canceled_order_ids", []) if str(x).strip()]
        released_locks = 0
        for order_id in confirmed_order_ids:
            self.wallet.release_order_lock(order_id)
            released_locks += 1
        if telemetry_counter:
            self.telemetry.incr(telemetry_counter)
        self.events.log_event(
            event_name,
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "gateway_reported_canceled_count": int(cancel_summary.get("gateway_reported_canceled_count", 0)),
                "canceled_count": int(cancel_summary.get("confirmed_canceled_count", 0)),
                "open_before_count": int(cancel_summary.get("open_before_count", 0)),
                "open_after_count": int(cancel_summary.get("open_after_count", 0)),
                "unconfirmed_open_count": len(cancel_summary.get("unconfirmed_order_ids", [])),
                "released_lock_count": int(released_locks),
                "reason": str(reason or ""),
            },
        )

    @staticmethod
    def _is_missing_book_not_found_error(error_text: str) -> bool:
        text = str(error_text)
        return "404" in text and "Not Found" in text and "/book" in text

    @staticmethod
    def _rest_fetch_result_for_token(
        *,
        token_id: str,
        requested_rest_token_ids: set[str],
        rest_books: Dict[str, tuple[Any, Any, float]],
        rest_errors: Dict[str, str],
    ) -> Tuple[Optional[tuple[Any, Any, float]], Optional[str], bool]:
        if token_id not in requested_rest_token_ids:
            return None, None, False
        fetched = rest_books.get(token_id)
        if fetched is not None:
            return fetched, None, True
        err_text = str(rest_errors.get(token_id, "") or "").strip()
        return None, (err_text or None), True

    def _recent_mode_transitions(self) -> int:
        now = time.monotonic()
        while self._mode_transition_mono and (now - self._mode_transition_mono[0]) > self._mode_transition_window_sec:
            self._mode_transition_mono.popleft()
        return len(self._mode_transition_mono)

    def _threshold_reasons(self, threshold_cfg: Dict[str, Any], mode_snapshot: Any) -> list[str]:
        reasons: list[str] = []
        stale_threshold = self._parse_threshold(threshold_cfg.get("stale_reject_ratio"))
        disarmed_threshold = self._parse_threshold(threshold_cfg.get("disarmed_ratio"))
        error_threshold = self._parse_threshold(threshold_cfg.get("error_ratio"))
        reconcile_threshold = self._parse_threshold(threshold_cfg.get("reconcile_mismatch_ratio"))
        transitions_threshold = self._parse_threshold(threshold_cfg.get("mode_transitions_window"))
        if stale_threshold is not None and mode_snapshot.stale_reject_ratio >= stale_threshold:
            reasons.append(f"stale_reject_ratio={mode_snapshot.stale_reject_ratio:.3f}")
        if disarmed_threshold is not None and mode_snapshot.disarmed_ratio >= disarmed_threshold:
            reasons.append(f"disarmed_ratio={mode_snapshot.disarmed_ratio:.3f}")
        if error_threshold is not None and mode_snapshot.error_ratio >= error_threshold:
            reasons.append(f"error_ratio={mode_snapshot.error_ratio:.3f}")
        if reconcile_threshold is not None and self._cached_reconcile_mismatch_ratio >= reconcile_threshold:
            reasons.append(f"reconcile_mismatch_ratio={self._cached_reconcile_mismatch_ratio:.3f}")
        if transitions_threshold is not None:
            transitions = self._recent_mode_transitions()
            if transitions >= int(transitions_threshold):
                reasons.append(f"mode_transitions_10m={transitions}")
        return reasons

    @staticmethod
    def _effective_risk_rejects(risk_rejects_delta: int, kill_switch_rejects_delta: int) -> int:
        return max(0, int(risk_rejects_delta) - max(0, int(kill_switch_rejects_delta)))

    @staticmethod
    def _stale_auto_stop_eligible(
        mode_snapshot: Any,
        *,
        min_samples: int,
        min_stale_rejects: int,
        min_risk_rejects: int,
    ) -> bool:
        sample_count = int(getattr(mode_snapshot, "sample_count", 0))
        stale_reject_count = int(getattr(mode_snapshot, "stale_reject_count", 0))
        risk_reject_count = int(getattr(mode_snapshot, "risk_reject_count", 0))
        return (
            sample_count >= max(1, int(min_samples))
            and stale_reject_count >= max(1, int(min_stale_rejects))
            and risk_reject_count >= max(1, int(min_risk_rejects))
        )

    @staticmethod
    def _conservative_mid_for_position(position: Position) -> float:
        net = float(position.net_shares)
        if net > 0.0:
            return 0.0
        if net < 0.0:
            return 1.0
        return 0.5

    @classmethod
    def _sanitize_probability_quote(cls, value: Any) -> Optional[float]:
        numeric = parse_float(value)
        if numeric is None:
            return None
        if not math.isfinite(float(numeric)):
            return None
        if numeric < (cls._VALUATION_QUOTE_MIN - cls._VALUATION_QUOTE_EPS):
            return None
        if numeric > (cls._VALUATION_QUOTE_MAX + cls._VALUATION_QUOTE_EPS):
            return None
        return min(cls._VALUATION_QUOTE_MAX, max(cls._VALUATION_QUOTE_MIN, float(numeric)))

    @staticmethod
    def _book_top_age_sec(top: Any) -> Optional[float]:
        ts_utc = str(getattr(top, "ts_utc", "") or "").strip()
        ts_dt = parse_ts(ts_utc)
        if ts_dt is None:
            return None
        now_dt = utc_now()
        return max(0.0, float((now_dt - ts_dt).total_seconds()))

    def _book_quote_snapshot(self, *, top: Any) -> Dict[str, Any]:
        bid_raw = getattr(top, "best_bid_price", None)
        ask_raw = getattr(top, "best_ask_price", None)
        mid_raw = getattr(top, "midpoint", None)
        bid = self._sanitize_probability_quote(bid_raw)
        ask = self._sanitize_probability_quote(ask_raw)
        mid = self._sanitize_probability_quote(mid_raw)
        reasons: List[str] = []
        if bid_raw is not None and bid is None:
            reasons.append("invalid_bid_quote")
        if ask_raw is not None and ask is None:
            reasons.append("invalid_ask_quote")
        if mid_raw is not None and mid is None:
            reasons.append("invalid_mid_quote")
        if bid is not None and ask is not None and bid > (ask + self._VALUATION_QUOTE_EPS):
            reasons.append("crossed_book_bid_gt_ask")
        if mid is not None and bid is not None and ask is not None:
            if mid < (bid - self._VALUATION_QUOTE_EPS) or mid > (ask + self._VALUATION_QUOTE_EPS):
                reasons.append("midpoint_outside_bid_ask")
        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "age_sec": self._book_top_age_sec(top),
            "sanity_reasons": reasons,
            "sane": not bool(reasons),
        }

    def _ws_quote_unusable_for_held_valuation(self, *, token_id: str, top: Any) -> bool:
        token = str(token_id or "").strip()
        if not token:
            return False
        pos = self.risk.positions.get(token)
        if pos is None:
            return False
        net = float(getattr(pos, "net_shares", 0.0) or 0.0)
        if abs(net) <= 1e-9:
            return False
        quote = self._book_quote_snapshot(top=top)
        if not bool(quote.get("sane")):
            return False
        quote_mid = parse_float(quote.get("mid"))
        quote_bid = parse_float(quote.get("bid"))
        quote_ask = parse_float(quote.get("ask"))
        required_side = "bid" if net > 1e-9 else "ask"
        required_side_mid = quote_bid if required_side == "bid" else quote_ask
        return bool(quote_mid is None and required_side_mid is None)

    def _rest_fetch_candidate_tokens_for_cycle(
        self,
        *,
        valuation_token_ids: List[str],
        ws_books: Dict[str, Any],
        now_mono: float,
    ) -> Tuple[List[str], set[str]]:
        candidates: List[str] = []
        held_ws_unusable_token_ids: set[str] = set()
        for token_id in valuation_token_ids:
            if self._book_not_found_backoff_mono_by_token.get(token_id, 0.0) > now_mono:
                continue
            ws_top = ws_books.get(token_id)
            if ws_top is None:
                candidates.append(token_id)
                continue
            if self._ws_quote_unusable_for_held_valuation(token_id=token_id, top=ws_top):
                candidates.append(token_id)
                held_ws_unusable_token_ids.add(token_id)
        return self._unique_ordered(candidates), held_ws_unusable_token_ids

    def _build_valuation_state(self, *, books: Dict[str, Any]) -> Dict[str, Any]:
        now_mono = time.monotonic()
        mids_by_token: Dict[str, float] = {}
        source_by_token: Dict[str, str] = {}
        degraded_reasons: List[str] = []
        hard_degraded_reasons: List[str] = []
        held_unpriceable_age_by_token: Dict[str, float] = {}
        held_unpriceable_cause_by_token: Dict[str, str] = {}
        conservative_mark_by_token: Dict[str, float] = {}
        open_order_tokens = self._open_order_token_ids()
        non_flat_positions: Dict[str, Position] = {
            str(token_id): pos
            for token_id, pos in self.risk.positions.items()
            if abs(float(pos.net_shares)) > 1e-9
        }
        for token_id, position in sorted(non_flat_positions.items(), key=lambda item: item[0]):
            top = books.get(token_id)
            quote = self._book_quote_snapshot(top=top) if top is not None else None
            quote_age_sec = quote.get("age_sec") if isinstance(quote, dict) else None
            sane_quote = bool(quote and quote.get("sane"))
            quote_mid = parse_float(quote.get("mid")) if isinstance(quote, dict) else None
            quote_bid = parse_float(quote.get("bid")) if isinstance(quote, dict) else None
            quote_ask = parse_float(quote.get("ask")) if isinstance(quote, dict) else None
            live_mid_age_fresh = bool(
                sane_quote and isinstance(quote_age_sec, (int, float)) and quote_age_sec <= (self.live_mid_max_age_sec + 1e-9)
            )
            one_sided_age_fresh = bool(
                sane_quote
                and isinstance(quote_age_sec, (int, float))
                and quote_age_sec <= (self.one_sided_quote_max_age_sec + 1e-9)
            )
            net = float(position.net_shares)
            conservative_mark = self._conservative_mid_for_position(position)
            conservative_mark_by_token[token_id] = float(conservative_mark)
            required_side = "bid" if net > 1e-9 else "ask"
            required_side_mid = quote_bid if required_side == "bid" else quote_ask
            if live_mid_age_fresh and quote_mid is not None:
                mids_by_token[token_id] = float(quote_mid)
                source_by_token[token_id] = "fresh_live_mid"
                self._held_unpriceable_since_mono_by_token.pop(token_id, None)
                continue
            if one_sided_age_fresh and required_side_mid is not None:
                mids_by_token[token_id] = float(required_side_mid)
                source_by_token[token_id] = "fresh_live_side_conservative_quote"
                self._held_unpriceable_since_mono_by_token.pop(token_id, None)
                continue

            last_mid = parse_float(self.last_midpoint_by_token.get(token_id))
            last_ts = self.last_midpoint_ts_mono_by_token.get(token_id)
            if last_mid is not None and isinstance(last_ts, (int, float)):
                age_sec = max(0.0, float(now_mono - float(last_ts)))
                if age_sec <= (float(self.last_known_mid_max_age_sec) + 1e-9):
                    mids_by_token[token_id] = float(last_mid)
                    source_by_token[token_id] = "fresh_last_known_mid"
                    self._held_unpriceable_since_mono_by_token.pop(token_id, None)
                    degraded_reasons.append(
                        f"degraded_using_last_known_mid:{token_id}:age_sec={age_sec:.3f}<=max_age_sec={float(self.last_known_mid_max_age_sec):.3f}"
                    )
                    continue

            mids_by_token[token_id] = float(conservative_mark)
            source_by_token[token_id] = "conservative_bound_hard_degraded"
            unpriceable_since = self._held_unpriceable_since_mono_by_token.get(token_id)
            if not isinstance(unpriceable_since, (int, float)):
                unpriceable_since = now_mono
                self._held_unpriceable_since_mono_by_token[token_id] = float(now_mono)
            held_unpriceable_age_by_token[token_id] = max(0.0, float(now_mono - float(unpriceable_since)))
            hard_reason_parts: List[str] = []
            if quote is None:
                hard_reason_parts.append("book_top_missing")
            else:
                if not sane_quote:
                    hard_reason_parts.extend([f"quote_sanity:{reason}" for reason in quote.get("sanity_reasons", [])])
                if not isinstance(quote_age_sec, (int, float)):
                    hard_reason_parts.append("quote_age_unknown")
                else:
                    live_mid_age_stale = float(quote_age_sec) > (float(self.live_mid_max_age_sec) + 1e-9)
                    one_sided_age_stale = float(quote_age_sec) > (float(self.one_sided_quote_max_age_sec) + 1e-9)
                    if live_mid_age_stale and one_sided_age_stale:
                        hard_reason_parts.append(
                            "quote_age_stale_for_live_mid_and_side:"
                            + f"quote_age_sec={float(quote_age_sec):.3f}"
                            + f":live_mid_max_age_sec={float(self.live_mid_max_age_sec):.3f}"
                            + f":one_sided_quote_max_age_sec={float(self.one_sided_quote_max_age_sec):.3f}"
                        )
                    elif live_mid_age_stale:
                        hard_reason_parts.append(
                            "quote_age_stale_for_live_mid:"
                            + f"quote_age_sec={float(quote_age_sec):.3f}"
                            + f":live_mid_max_age_sec={float(self.live_mid_max_age_sec):.3f}"
                        )
                    elif one_sided_age_stale:
                        hard_reason_parts.append(
                            "quote_age_stale_for_side_conservative:"
                            + f"quote_age_sec={float(quote_age_sec):.3f}"
                            + f":one_sided_quote_max_age_sec={float(self.one_sided_quote_max_age_sec):.3f}"
                        )
                if quote_mid is None:
                    hard_reason_parts.append("live_mid_missing")
                if required_side_mid is None:
                    hard_reason_parts.append(f"required_conservative_side_missing:{required_side}")
            if last_mid is None or not isinstance(last_ts, (int, float)):
                hard_reason_parts.append("last_known_mid_missing")
            else:
                last_age = max(0.0, float(now_mono - float(last_ts)))
                hard_reason_parts.append(
                    f"last_known_mid_age_sec={last_age:.3f}>last_known_mid_max_age_sec={float(self.last_known_mid_max_age_sec):.3f}"
                )
            held_unpriceable_cause = self._held_unpriceable_cause_class(
                token_id=token_id,
                quote=quote if isinstance(quote, dict) else None,
                now_mono=now_mono,
                hard_reason_parts=hard_reason_parts,
            )
            held_unpriceable_cause_by_token[token_id] = str(held_unpriceable_cause)
            hard_reason = "|".join(hard_reason_parts)
            hard_reason_row = f"hard_degraded:{token_id}:{hard_reason}"
            degraded_reasons.append(hard_reason_row)
            hard_degraded_reasons.append(hard_reason_row)

        non_flat_token_set = set(non_flat_positions.keys())
        for token_id in list(self._held_unpriceable_since_mono_by_token.keys()):
            if token_id not in non_flat_token_set:
                self._held_unpriceable_since_mono_by_token.pop(token_id, None)
        for token_id in list(self._held_book_not_found_last_mono_by_token.keys()):
            if token_id not in non_flat_token_set:
                self._held_book_not_found_last_mono_by_token.pop(token_id, None)
                self._held_book_not_found_force_refresh_next_mono_by_token.pop(token_id, None)
        held_unpriceable_token_ids = sorted(held_unpriceable_age_by_token.keys())
        held_unpriceable_max_age_sec = (
            max(held_unpriceable_age_by_token.values()) if held_unpriceable_age_by_token else 0.0
        )
        escalation_threshold_sec = float(self.held_unpriceable_escalation_sec)
        held_unpriceable_escalation_token_ids: List[str] = []
        held_unpriceable_escalation_reasons: List[str] = []
        if escalation_threshold_sec > 0.0:
            held_unpriceable_escalation_token_ids = sorted(
                token_id
                for token_id, age_sec in held_unpriceable_age_by_token.items()
                if float(age_sec) >= (escalation_threshold_sec - 1e-9)
            )
            for token_id in held_unpriceable_escalation_token_ids:
                age_sec = float(held_unpriceable_age_by_token.get(token_id, 0.0))
                held_unpriceable_escalation_reasons.append(
                    f"persistent_held_unpriceable:{token_id}:age_sec={age_sec:.3f}>=threshold_sec={escalation_threshold_sec:.3f}"
                )
        held_unpriceable_escalation_active = bool(held_unpriceable_escalation_token_ids)
        held_unpriceable_escalation_max_age_sec = (
            max(float(held_unpriceable_age_by_token.get(token_id, 0.0)) for token_id in held_unpriceable_escalation_token_ids)
            if held_unpriceable_escalation_token_ids
            else 0.0
        )
        held_unpriceable_operator_action = (
            "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable"
            if held_unpriceable_escalation_active
            else "none"
        )
        held_unpriceable_cause_counts_counter = collections.Counter(
            str(held_unpriceable_cause_by_token.get(token_id) or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP)
            for token_id in held_unpriceable_token_ids
        )
        held_unpriceable_cause_counts = {
            HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE: int(
                held_unpriceable_cause_counts_counter.get(HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE, 0)
            ),
            HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED: int(
                held_unpriceable_cause_counts_counter.get(HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED, 0)
            ),
            HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP: int(
                held_unpriceable_cause_counts_counter.get(HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP, 0)
            ),
        }
        held_unpriceable_dominant_cause = HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        if held_unpriceable_cause_counts:
            held_unpriceable_dominant_cause = max(
                held_unpriceable_cause_counts.items(),
                key=lambda kv: (int(kv[1]), str(kv[0])),
            )[0]

        held_exposure_class_by_token: Dict[str, str] = {}
        held_exposure_detail_by_token: Dict[str, Dict[str, Any]] = {}
        _base_classification_by_token: Dict[str, Any] = {}
        for token_id in sorted(non_flat_positions.keys()):
            position = non_flat_positions[token_id]
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            sec_to_expiry = (
                (expiry_dt - utc_now()).total_seconds()
                if isinstance(expiry_dt, dt.datetime)
                else None
            )
            lifecycle_flags = self._token_lifecycle_obligation_flags(
                token_id=token_id,
                now_mono=now_mono,
                sec_to_expiry=sec_to_expiry,
                open_order_present=bool(token_id in open_order_tokens),
            )
            unresolved_for_dust = bool(
                lifecycle_flags.get("recent_book_not_found", False)
                or lifecycle_flags.get("forced_refresh_pending", False)
                or lifecycle_flags.get("expired_reduce_only_grace_active", False)
                or lifecycle_flags.get("preexpiry_reduce_only_active", False)
            )
            dust_age_sec = float(held_unpriceable_age_by_token.get(token_id, 0.0) or 0.0)
            _base_classification_by_token[token_id] = classify_exposure_fail_closed(
                net_shares=float(getattr(position, "net_shares", 0.0) or 0.0),
                cfg=self._dust_classifier_cfg,
                conservative_mark_price=float(conservative_mark_by_token.get(token_id, 0.0) or 0.0),
                open_order_present=bool(lifecycle_flags.get("open_order_present", False)),
                unresolved_lifecycle_obligation=bool(unresolved_for_dust),
                dust_age_sec=float(dust_age_sec),
                aggregate_dust_notional_upper_bound_usd=0.0,
                aggregate_dust_token_count=0,
            )
        aggregate_dust_candidate_token_ids = sorted(
            token_id
            for token_id, classification in _base_classification_by_token.items()
            if bool(getattr(classification, "dust_share_eligible", False))
            and bool(getattr(classification, "dust_notional_eligible", False))
        )
        aggregate_dust_token_count = int(len(aggregate_dust_candidate_token_ids))
        aggregate_dust_notional_upper_bound_usd = float(
            sum(
                float(getattr(_base_classification_by_token[token_id], "dust_notional_upper_bound_usd", 0.0) or 0.0)
                for token_id in aggregate_dust_candidate_token_ids
            )
        )
        for token_id in sorted(non_flat_positions.keys()):
            position = non_flat_positions[token_id]
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            sec_to_expiry = (
                (expiry_dt - utc_now()).total_seconds()
                if isinstance(expiry_dt, dt.datetime)
                else None
            )
            lifecycle_flags = self._token_lifecycle_obligation_flags(
                token_id=token_id,
                now_mono=now_mono,
                sec_to_expiry=sec_to_expiry,
                open_order_present=bool(token_id in open_order_tokens),
            )
            unresolved_for_dust = bool(
                lifecycle_flags.get("recent_book_not_found", False)
                or lifecycle_flags.get("forced_refresh_pending", False)
                or lifecycle_flags.get("expired_reduce_only_grace_active", False)
                or lifecycle_flags.get("preexpiry_reduce_only_active", False)
            )
            dust_age_sec = float(held_unpriceable_age_by_token.get(token_id, 0.0) or 0.0)
            classification = classify_exposure_fail_closed(
                net_shares=float(getattr(position, "net_shares", 0.0) or 0.0),
                cfg=self._dust_classifier_cfg,
                conservative_mark_price=float(conservative_mark_by_token.get(token_id, 0.0) or 0.0),
                open_order_present=bool(lifecycle_flags.get("open_order_present", False)),
                unresolved_lifecycle_obligation=bool(unresolved_for_dust),
                dust_age_sec=float(dust_age_sec),
                aggregate_dust_notional_upper_bound_usd=float(aggregate_dust_notional_upper_bound_usd),
                aggregate_dust_token_count=int(aggregate_dust_token_count),
            )
            held_exposure_class_by_token[token_id] = str(classification.exposure_class)
            detail = exposure_class_to_dict(classification)
            detail.update(
                {
                    "token_id": str(token_id),
                    "source": str(source_by_token.get(token_id) or ""),
                    "net_shares": float(getattr(position, "net_shares", 0.0) or 0.0),
                    "conservative_mark_price": float(conservative_mark_by_token.get(token_id, 0.0) or 0.0),
                    "open_order_present": bool(lifecycle_flags.get("open_order_present", False)),
                    "unresolved_lifecycle_obligation": bool(unresolved_for_dust),
                    "unresolved_lifecycle_obligation_watch_state": bool(
                        lifecycle_flags.get("unresolved_lifecycle_obligation", False)
                    ),
                    "held_unpriceable_tracking_active": bool(
                        lifecycle_flags.get("held_unpriceable_tracking_active", False)
                    ),
                    "dust_age_sec": float(dust_age_sec),
                    "sec_to_expiry": (
                        float(sec_to_expiry)
                        if isinstance(sec_to_expiry, (int, float))
                        else None
                    ),
                    "aggregate_dust_notional_upper_bound_usd": float(aggregate_dust_notional_upper_bound_usd),
                    "aggregate_dust_token_count": int(aggregate_dust_token_count),
                }
            )
            held_exposure_detail_by_token[token_id] = detail
        held_dust_token_ids = sorted(
            token_id
            for token_id, klass in held_exposure_class_by_token.items()
            if str(klass) == EXPOSURE_CLASS_DUST_ELIGIBLE
        )
        held_dust_quarantined_token_ids = sorted(
            token_id
            for token_id, klass in held_exposure_class_by_token.items()
            if str(klass) == EXPOSURE_CLASS_DUST_QUARANTINED
        )
        hard_degraded_token_ids = sorted(
            token_id
            for token_id, source in source_by_token.items()
            if str(source) == "conservative_bound_hard_degraded"
        )
        hard_degraded_dust_eligible_token_ids = sorted(
            token_id
            for token_id in hard_degraded_token_ids
            if str(held_exposure_class_by_token.get(token_id) or EXPOSURE_CLASS_MEANINGFUL)
            == EXPOSURE_CLASS_DUST_ELIGIBLE
        )
        hard_degraded_meaningful_token_ids = sorted(
            token_id for token_id in hard_degraded_token_ids if token_id not in hard_degraded_dust_eligible_token_ids
        )

        source_counts = dict(collections.Counter(source_by_token.values()))
        summary_counts = {
            "live_mid": int(source_counts.get("fresh_live_mid", 0)),
            "live_side_conservative_quote": int(source_counts.get("fresh_live_side_conservative_quote", 0)),
            "last_known_mid": int(source_counts.get("fresh_last_known_mid", 0)),
            "conservative_bound_hard_degraded": int(source_counts.get("conservative_bound_hard_degraded", 0)),
            "hard_degraded": int(source_counts.get("conservative_bound_hard_degraded", 0)),
        }
        degraded = any(
            source in {"fresh_last_known_mid", "conservative_bound_hard_degraded"} for source in source_by_token.values()
        )
        hard_degraded = any(source == "conservative_bound_hard_degraded" for source in source_by_token.values())
        return {
            "mid_by_token": mids_by_token,
            "source_by_token": source_by_token,
            "source_counts": summary_counts,
            "source_counts_raw": source_counts,
            "degraded_reasons": list(degraded_reasons),
            "hard_degraded_reasons": list(hard_degraded_reasons),
            "valuation_degraded": bool(degraded),
            "valuation_hard_degraded": bool(hard_degraded),
            "position_tokens": sorted(non_flat_positions.keys()),
            "held_unpriceable_token_ids": held_unpriceable_token_ids,
            "held_unpriceable_count": int(len(held_unpriceable_token_ids)),
            "held_unpriceable_max_age_sec": float(held_unpriceable_max_age_sec),
            "held_unpriceable_age_by_token": {
                token_id: float(held_unpriceable_age_by_token[token_id]) for token_id in held_unpriceable_token_ids
            },
            "held_unpriceable_escalation_active": bool(held_unpriceable_escalation_active),
            "held_unpriceable_escalation_token_ids": list(held_unpriceable_escalation_token_ids),
            "held_unpriceable_escalation_count": int(len(held_unpriceable_escalation_token_ids)),
            "held_unpriceable_escalation_reasons": list(held_unpriceable_escalation_reasons),
            "held_unpriceable_escalation_max_age_sec": float(held_unpriceable_escalation_max_age_sec),
            "held_unpriceable_escalation_threshold_sec": float(escalation_threshold_sec),
            "held_unpriceable_defect_candidate": bool(held_unpriceable_escalation_active),
            "held_unpriceable_operator_action": str(held_unpriceable_operator_action),
            "held_unpriceable_cause_by_token": {
                token_id: str(held_unpriceable_cause_by_token.get(token_id) or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP)
                for token_id in held_unpriceable_token_ids
            },
            "held_unpriceable_cause_counts": dict(held_unpriceable_cause_counts),
            "held_unpriceable_dominant_cause": str(held_unpriceable_dominant_cause),
            "held_exposure_class_by_token": dict(held_exposure_class_by_token),
            "held_exposure_detail_by_token": dict(held_exposure_detail_by_token),
            "held_dust_token_ids": list(held_dust_token_ids),
            "held_dust_count": int(len(held_dust_token_ids)),
            "held_dust_quarantined_token_ids": list(held_dust_quarantined_token_ids),
            "held_dust_quarantined_count": int(len(held_dust_quarantined_token_ids)),
            "held_dust_total_notional_upper_bound_usd": float(aggregate_dust_notional_upper_bound_usd),
            "held_dust_candidate_token_ids": list(aggregate_dust_candidate_token_ids),
            "held_dust_candidate_count": int(aggregate_dust_token_count),
            "hard_degraded_token_ids": list(hard_degraded_token_ids),
            "hard_degraded_dust_eligible_token_ids": list(hard_degraded_dust_eligible_token_ids),
            "hard_degraded_meaningful_token_ids": list(hard_degraded_meaningful_token_ids),
            "raw_valuation_hard_degraded": bool(hard_degraded),
            "raw_valuation_degraded": bool(degraded),
        }

    def _apply_valuation_controls(self, *, books: Dict[str, Any], phase: str) -> Dict[str, Any]:
        valuation_state = self._build_valuation_state(books=books)
        raw_degraded = bool(valuation_state.get("valuation_degraded", False))
        raw_hard_degraded = bool(valuation_state.get("valuation_hard_degraded", False))
        degraded = bool(raw_degraded)
        hard_degraded = bool(raw_hard_degraded)
        degraded_reasons = [str(x) for x in list(valuation_state.get("degraded_reasons", [])) if str(x).strip()]
        source_counts = dict(valuation_state.get("source_counts", {}))
        source_counts_raw = dict(valuation_state.get("source_counts_raw", {}))
        held_exposure_class_by_token = {
            str(token_id): str(klass or "").strip().upper() or EXPOSURE_CLASS_MEANINGFUL
            for token_id, klass in dict(valuation_state.get("held_exposure_class_by_token", {})).items()
            if str(token_id).strip()
        }
        held_exposure_detail_by_token = {
            str(token_id): dict(detail)
            for token_id, detail in dict(valuation_state.get("held_exposure_detail_by_token", {})).items()
            if str(token_id).strip() and isinstance(detail, dict)
        }
        held_dust_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("held_dust_token_ids", []))
            if str(token_id).strip()
        ]
        held_dust_quarantined_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("held_dust_quarantined_token_ids", []))
            if str(token_id).strip()
        ]
        held_dust_total_notional_upper_bound_usd = float(
            valuation_state.get("held_dust_total_notional_upper_bound_usd", 0.0) or 0.0
        )
        hard_degraded_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("hard_degraded_token_ids", []))
            if str(token_id).strip()
        ]
        hard_degraded_dust_eligible_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("hard_degraded_dust_eligible_token_ids", []))
            if str(token_id).strip()
        ]
        hard_degraded_meaningful_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("hard_degraded_meaningful_token_ids", []))
            if str(token_id).strip()
        ]
        raw_dust_shadow_candidate_active = bool(
            raw_hard_degraded
            and hard_degraded_token_ids
            and (not hard_degraded_meaningful_token_ids)
            and len(hard_degraded_dust_eligible_token_ids) == len(hard_degraded_token_ids)
        )
        prev_shadow_hysteresis_active = bool(self._held_dust_shadow_active)
        if raw_dust_shadow_candidate_active:
            self._held_dust_shadow_enter_pending_cycles += 1
            self._held_dust_shadow_clear_pending_cycles = 0
            if self._held_dust_shadow_enter_pending_cycles >= int(self.position_dust_enter_consecutive_cycles):
                self._held_dust_shadow_active = True
        else:
            self._held_dust_shadow_enter_pending_cycles = 0
            if self._held_dust_shadow_active:
                self._held_dust_shadow_clear_pending_cycles += 1
                if self._held_dust_shadow_clear_pending_cycles >= int(self.position_dust_clear_consecutive_cycles):
                    self._held_dust_shadow_active = False
                    self._held_dust_shadow_clear_pending_cycles = 0
            else:
                self._held_dust_shadow_clear_pending_cycles = 0
        prev_shadow_candidate_active = bool(self._held_dust_shadow_candidate_active)
        self._held_dust_shadow_candidate_active = bool(raw_dust_shadow_candidate_active)
        shadow_active = bool(self._held_dust_shadow_active)
        if prev_shadow_candidate_active != self._held_dust_shadow_candidate_active:
            self.events.log_event(
                "held_dust_shadow_candidate_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "transition": (
                        "enter"
                        if self._held_dust_shadow_candidate_active
                        else "clear"
                    ),
                    "candidate_active": bool(self._held_dust_shadow_candidate_active),
                    "shadow_active": bool(shadow_active),
                    "position_dust_enter_consecutive_cycles": int(self.position_dust_enter_consecutive_cycles),
                    "position_dust_clear_consecutive_cycles": int(self.position_dust_clear_consecutive_cycles),
                    "enter_pending_cycles": int(self._held_dust_shadow_enter_pending_cycles),
                    "clear_pending_cycles": int(self._held_dust_shadow_clear_pending_cycles),
                    "hard_degraded_token_ids": list(hard_degraded_token_ids),
                    "hard_degraded_dust_eligible_token_ids": list(hard_degraded_dust_eligible_token_ids),
                    "hard_degraded_meaningful_token_ids": list(hard_degraded_meaningful_token_ids),
                },
            )
        if prev_shadow_hysteresis_active != shadow_active:
            self.events.log_event(
                "held_dust_shadow_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "transition": ("enter" if shadow_active else "clear"),
                    "shadow_active": bool(shadow_active),
                    "candidate_active": bool(self._held_dust_shadow_candidate_active),
                    "position_dust_enter_consecutive_cycles": int(self.position_dust_enter_consecutive_cycles),
                    "position_dust_clear_consecutive_cycles": int(self.position_dust_clear_consecutive_cycles),
                    "enter_pending_cycles": int(self._held_dust_shadow_enter_pending_cycles),
                    "clear_pending_cycles": int(self._held_dust_shadow_clear_pending_cycles),
                },
            )
        effective_hard_degraded_exempt_count = 0
        if bool(self.dust_classifier_enforce_enabled) and bool(shadow_active) and bool(raw_hard_degraded):
            if not hard_degraded_meaningful_token_ids and hard_degraded_dust_eligible_token_ids:
                effective_hard_degraded_exempt_count = int(len(hard_degraded_dust_eligible_token_ids))
                raw_hard_degraded = False
                raw_degraded = True
                degraded_reasons.append(
                    "hard_degraded_exempted_dust_only:"
                    + f"token_count={effective_hard_degraded_exempt_count}"
                    + f":shadow_active={int(shadow_active)}"
                )
        held_unpriceable_token_ids = [
            str(token_id) for token_id in list(valuation_state.get("held_unpriceable_token_ids", [])) if str(token_id)
        ]
        held_unpriceable_max_age_sec = float(valuation_state.get("held_unpriceable_max_age_sec", 0.0) or 0.0)
        held_unpriceable_age_by_token = {
            str(token_id): float(age_sec)
            for token_id, age_sec in dict(valuation_state.get("held_unpriceable_age_by_token", {})).items()
            if str(token_id)
        }
        held_unpriceable_escalation_active = bool(valuation_state.get("held_unpriceable_escalation_active", False))
        held_unpriceable_escalation_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("held_unpriceable_escalation_token_ids", []))
            if str(token_id)
        ]
        held_unpriceable_escalation_reasons = [
            str(reason).strip()
            for reason in list(valuation_state.get("held_unpriceable_escalation_reasons", []))
            if str(reason).strip()
        ]
        held_unpriceable_escalation_max_age_sec = float(
            valuation_state.get("held_unpriceable_escalation_max_age_sec", 0.0) or 0.0
        )
        held_unpriceable_escalation_threshold_sec = float(
            valuation_state.get("held_unpriceable_escalation_threshold_sec", 0.0) or 0.0
        )
        held_unpriceable_defect_candidate = bool(valuation_state.get("held_unpriceable_defect_candidate", False))
        held_unpriceable_operator_action = str(valuation_state.get("held_unpriceable_operator_action") or "none")
        held_unpriceable_cause_by_token = {
            str(token_id): str(cause).strip() or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
            for token_id, cause in dict(valuation_state.get("held_unpriceable_cause_by_token", {})).items()
            if str(token_id).strip()
        }
        held_unpriceable_cause_counts = {
            HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE: int(
                dict(valuation_state.get("held_unpriceable_cause_counts", {})).get(
                    HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE,
                    0,
                )
            ),
            HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED: int(
                dict(valuation_state.get("held_unpriceable_cause_counts", {})).get(
                    HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED,
                    0,
                )
            ),
            HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP: int(
                dict(valuation_state.get("held_unpriceable_cause_counts", {})).get(
                    HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP,
                    0,
                )
            ),
        }
        held_unpriceable_dominant_cause = str(
            valuation_state.get("held_unpriceable_dominant_cause") or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        ).strip() or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        self._held_dust_shadow_active = bool(shadow_active)
        self._held_dust_enforced_this_cycle = bool(
            self.dust_classifier_enforce_enabled and effective_hard_degraded_exempt_count > 0
        )
        self._held_dust_effective_hard_degraded_exempt_count = int(effective_hard_degraded_exempt_count)
        self._held_dust_raw_hard_degraded_token_count = int(len(hard_degraded_token_ids))
        prev_hard_degraded = bool(self._valuation_hard_degraded)
        prev_held_unpriceable_tokens = set(self._held_unpriceable_token_ids)
        next_held_unpriceable_tokens = set(held_unpriceable_token_ids)
        started_unpriceable_tokens = sorted(next_held_unpriceable_tokens - prev_held_unpriceable_tokens)
        recovered_unpriceable_tokens = sorted(prev_held_unpriceable_tokens - next_held_unpriceable_tokens)
        if raw_hard_degraded:
            self._valuation_hard_degraded_pending_healthy_cycles = 0
            hard_degraded = True
        elif prev_hard_degraded:
            self._valuation_hard_degraded_pending_healthy_cycles += 1
            required_cycles = max(1, int(self.valuation_hard_degraded_clear_consecutive_healthy_cycles))
            if self._valuation_hard_degraded_pending_healthy_cycles < required_cycles:
                hard_degraded = True
                degraded = True
                degraded_reasons.append(
                    "hard_degraded_clear_hysteresis_pending:"
                    + f"healthy_cycles={int(self._valuation_hard_degraded_pending_healthy_cycles)}"
                    + f"/required={int(required_cycles)}"
                )
            else:
                hard_degraded = False
                self._valuation_hard_degraded_pending_healthy_cycles = 0
        else:
            self._valuation_hard_degraded_pending_healthy_cycles = 0
            hard_degraded = False
        if (not prev_hard_degraded) and hard_degraded:
            self._valuation_hard_degraded_enter_count += 1
            self.telemetry.incr("valuation_hard_degraded_enter")
            self.events.log_event(
                "valuation_hard_degraded_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "transition": "enter",
                    "valuation_hard_degraded_enter_count": int(self._valuation_hard_degraded_enter_count),
                    "valuation_hard_degraded_clear_count": int(self._valuation_hard_degraded_clear_count),
                    "held_unpriceable_token_ids": list(held_unpriceable_token_ids),
                    "valuation_degraded_reasons": list(degraded_reasons),
                    "raw_valuation_hard_degraded": bool(valuation_state.get("raw_valuation_hard_degraded", False)),
                    "dust_shadow_active": bool(self._held_dust_shadow_active),
                    "dust_classifier_enforce_enabled": bool(self.dust_classifier_enforce_enabled),
                    "dust_hard_degraded_exempt_count": int(self._held_dust_effective_hard_degraded_exempt_count),
                },
            )
        elif prev_hard_degraded and (not hard_degraded):
            self._valuation_hard_degraded_clear_count += 1
            self.telemetry.incr("valuation_hard_degraded_clear")
            self.events.log_event(
                "valuation_hard_degraded_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "transition": "clear",
                    "valuation_hard_degraded_enter_count": int(self._valuation_hard_degraded_enter_count),
                    "valuation_hard_degraded_clear_count": int(self._valuation_hard_degraded_clear_count),
                    "held_unpriceable_token_ids": list(held_unpriceable_token_ids),
                    "valuation_degraded_reasons": list(degraded_reasons),
                    "raw_valuation_hard_degraded": bool(valuation_state.get("raw_valuation_hard_degraded", False)),
                    "dust_shadow_active": bool(self._held_dust_shadow_active),
                    "dust_classifier_enforce_enabled": bool(self.dust_classifier_enforce_enabled),
                    "dust_hard_degraded_exempt_count": int(self._held_dust_effective_hard_degraded_exempt_count),
                },
            )
        if started_unpriceable_tokens or recovered_unpriceable_tokens:
            self._held_unpriceable_started_count += int(len(started_unpriceable_tokens))
            self._held_unpriceable_recovered_count += int(len(recovered_unpriceable_tokens))
            self.telemetry.incr("held_unpriceable_started", int(len(started_unpriceable_tokens)))
            self.telemetry.incr("held_unpriceable_recovered", int(len(recovered_unpriceable_tokens)))
            self.events.log_event(
                "held_unpriceable_transition",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "started_token_ids": list(started_unpriceable_tokens),
                    "recovered_token_ids": list(recovered_unpriceable_tokens),
                    "started_count_delta": int(len(started_unpriceable_tokens)),
                    "recovered_count_delta": int(len(recovered_unpriceable_tokens)),
                    "held_unpriceable_started_count": int(self._held_unpriceable_started_count),
                    "held_unpriceable_recovered_count": int(self._held_unpriceable_recovered_count),
                },
            )

        self._valuation_degraded = degraded
        self._valuation_hard_degraded = hard_degraded
        self._pnl_degraded = degraded
        self._loss_guard_degraded = degraded
        self._valuation_degraded_reasons = degraded_reasons
        self._valuation_mid_source_counts = {str(k): int(v) for k, v in source_counts.items()}
        self._valuation_mid_source_counts_raw = {str(k): int(v) for k, v in source_counts_raw.items()}
        self._valuation_mid_source_by_token = {
            str(k): str(v) for k, v in dict(valuation_state.get("source_by_token", {})).items()
        }
        self._held_unpriceable_token_ids = list(held_unpriceable_token_ids)
        self._held_unpriceable_max_age_sec = float(held_unpriceable_max_age_sec)
        self._held_unpriceable_age_by_token = dict(held_unpriceable_age_by_token)
        self._held_unpriceable_escalation_active = bool(held_unpriceable_escalation_active)
        self._held_unpriceable_escalation_token_ids = list(held_unpriceable_escalation_token_ids)
        self._held_unpriceable_escalation_reasons = list(held_unpriceable_escalation_reasons)
        self._held_unpriceable_escalation_max_age_sec = float(held_unpriceable_escalation_max_age_sec)
        self._held_unpriceable_operator_action = str(held_unpriceable_operator_action)
        self._held_unpriceable_cause_by_token = dict(held_unpriceable_cause_by_token)
        self._held_unpriceable_cause_counts = dict(held_unpriceable_cause_counts)
        self._held_unpriceable_dominant_cause = str(held_unpriceable_dominant_cause)
        self._held_exposure_class_by_token = dict(held_exposure_class_by_token)
        self._held_exposure_detail_by_token = dict(held_exposure_detail_by_token)
        self._held_dust_token_ids = list(held_dust_token_ids)
        self._held_dust_quarantined_token_ids = list(held_dust_quarantined_token_ids)
        self._held_dust_total_notional_upper_bound_usd = float(held_dust_total_notional_upper_bound_usd)
        self._financial_posture_class = self._resolve_financial_posture_class(stage_info_by_token=None)

        self.risk.set_valuation_degraded_state(
            hard_degraded=hard_degraded,
            reasons=degraded_reasons,
        )
        self.risk.set_exposure_classification_state(
            exposure_class_by_token=held_exposure_class_by_token,
        )

        self.telemetry.set_gauge("valuation_degraded", 1.0 if degraded else 0.0)
        self.telemetry.set_gauge("valuation_hard_degraded", 1.0 if hard_degraded else 0.0)
        self.telemetry.set_gauge("pnl_degraded", 1.0 if degraded else 0.0)
        self.telemetry.set_gauge("loss_guard_degraded", 1.0 if degraded else 0.0)
        self.telemetry.set_gauge("valuation_held_unpriceable_count", float(len(held_unpriceable_token_ids)))
        self.telemetry.set_gauge("valuation_held_unpriceable_max_age_sec", float(held_unpriceable_max_age_sec))
        self.telemetry.set_gauge(
            "valuation_held_unpriceable_escalation_active",
            1.0 if held_unpriceable_escalation_active else 0.0,
        )
        self.telemetry.set_gauge(
            "valuation_held_unpriceable_escalation_count",
            float(len(held_unpriceable_escalation_token_ids)),
        )
        self.telemetry.set_gauge(
            "valuation_held_unpriceable_escalation_max_age_sec",
            float(held_unpriceable_escalation_max_age_sec),
        )
        self.telemetry.set_gauge(
            "valuation_hard_degraded_enter_count",
            float(self._valuation_hard_degraded_enter_count),
        )
        self.telemetry.set_gauge(
            "valuation_hard_degraded_clear_count",
            float(self._valuation_hard_degraded_clear_count),
        )
        self.telemetry.set_gauge(
            "held_unpriceable_started_count",
            float(self._held_unpriceable_started_count),
        )
        self.telemetry.set_gauge(
            "held_unpriceable_recovered_count",
            float(self._held_unpriceable_recovered_count),
        )
        self.telemetry.set_gauge(
            "preexpiry_404_anomaly_count",
            float(self._preexpiry_404_anomaly_count),
        )
        self.telemetry.set_gauge(
            "financial_posture_class",
            self._financial_posture_class_to_gauge(self._financial_posture_class),
        )
        self.telemetry.set_gauge(
            "held_dust_count",
            float(len(self._held_dust_token_ids)),
        )
        self.telemetry.set_gauge(
            "held_dust_quarantined_count",
            float(len(self._held_dust_quarantined_token_ids)),
        )
        self.telemetry.set_gauge(
            "held_dust_total_notional_upper_bound_usd",
            float(self._held_dust_total_notional_upper_bound_usd),
        )
        self.telemetry.set_gauge(
            "held_dust_shadow_candidate_active",
            1.0 if self._held_dust_shadow_candidate_active else 0.0,
        )
        self.telemetry.set_gauge(
            "held_dust_shadow_active",
            1.0 if self._held_dust_shadow_active else 0.0,
        )
        self.telemetry.set_gauge(
            "held_dust_enforced_this_cycle",
            1.0 if self._held_dust_enforced_this_cycle else 0.0,
        )
        self.telemetry.set_gauge(
            "held_dust_hard_degraded_exempt_count",
            float(self._held_dust_effective_hard_degraded_exempt_count),
        )

        signature = (
            bool(degraded),
            bool(hard_degraded),
            tuple(degraded_reasons),
            tuple(sorted(self._valuation_mid_source_counts.items(), key=lambda item: item[0])),
            tuple(held_unpriceable_token_ids),
            bool(held_unpriceable_escalation_active),
            tuple(held_unpriceable_escalation_token_ids),
            tuple(sorted(held_unpriceable_cause_counts.items(), key=lambda item: item[0])),
            str(held_unpriceable_dominant_cause),
            tuple(sorted(self._held_exposure_class_by_token.items(), key=lambda item: item[0])),
            tuple(self._held_dust_token_ids),
            tuple(self._held_dust_quarantined_token_ids),
            bool(self._held_dust_shadow_candidate_active),
            bool(self._held_dust_shadow_active),
            bool(self._held_dust_enforced_this_cycle),
            int(self._held_dust_effective_hard_degraded_exempt_count),
        )
        if signature != self._last_valuation_event_signature:
            self._last_valuation_event_signature = signature
            self.events.log_event(
                "valuation_degraded",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "valuation_degraded": bool(degraded),
                    "valuation_hard_degraded": bool(hard_degraded),
                    "pnl_degraded": bool(degraded),
                    "loss_guard_degraded": bool(degraded),
                    "valuation_degraded_reasons": list(degraded_reasons),
                    "valuation_mid_source_counts": dict(self._valuation_mid_source_counts),
                    "valuation_mid_source_counts_raw": dict(self._valuation_mid_source_counts_raw),
                    "valuation_position_token_count": int(len(valuation_state.get("position_tokens") or [])),
                    "held_unpriceable_token_count": int(len(held_unpriceable_token_ids)),
                    "held_unpriceable_token_ids": list(held_unpriceable_token_ids),
                    "held_unpriceable_max_age_sec": float(held_unpriceable_max_age_sec),
                    "held_unpriceable_age_by_token": dict(held_unpriceable_age_by_token),
                    "held_unpriceable_escalation_active": bool(held_unpriceable_escalation_active),
                    "held_unpriceable_escalation_token_count": int(len(held_unpriceable_escalation_token_ids)),
                    "held_unpriceable_escalation_token_ids": list(held_unpriceable_escalation_token_ids),
                    "held_unpriceable_escalation_max_age_sec": float(held_unpriceable_escalation_max_age_sec),
                    "held_unpriceable_escalation_threshold_sec": float(held_unpriceable_escalation_threshold_sec),
                    "held_unpriceable_escalation_reasons": list(held_unpriceable_escalation_reasons),
                    "held_unpriceable_defect_candidate": bool(held_unpriceable_defect_candidate),
                    "held_unpriceable_operator_action": str(held_unpriceable_operator_action),
                    "held_unpriceable_cause_by_token": dict(held_unpriceable_cause_by_token),
                    "held_unpriceable_cause_counts": dict(held_unpriceable_cause_counts),
                    "held_unpriceable_dominant_cause": str(held_unpriceable_dominant_cause),
                    "valuation_hard_degraded_enter_count": int(self._valuation_hard_degraded_enter_count),
                    "valuation_hard_degraded_clear_count": int(self._valuation_hard_degraded_clear_count),
                    "valuation_hard_degraded_pending_healthy_cycles": int(
                        self._valuation_hard_degraded_pending_healthy_cycles
                    ),
                    "valuation_hard_degraded_clear_consecutive_healthy_cycles": int(
                        self.valuation_hard_degraded_clear_consecutive_healthy_cycles
                    ),
                    "held_unpriceable_started_count": int(self._held_unpriceable_started_count),
                    "held_unpriceable_recovered_count": int(self._held_unpriceable_recovered_count),
                    "preexpiry_404_anomaly_count": int(self._preexpiry_404_anomaly_count),
                    "financial_posture_class": str(self._financial_posture_class),
                    "live_mid_max_age_sec": float(self.live_mid_max_age_sec),
                    "one_sided_quote_max_age_sec": float(self.one_sided_quote_max_age_sec),
                    "last_known_mid_max_age_sec": float(self.last_known_mid_max_age_sec),
                    "held_preexpiry_reduce_only_sec": float(self.held_preexpiry_reduce_only_sec),
                    "raw_valuation_degraded": bool(valuation_state.get("raw_valuation_degraded", False)),
                    "raw_valuation_hard_degraded": bool(valuation_state.get("raw_valuation_hard_degraded", False)),
                    "dust_classifier_enforce_enabled": bool(self.dust_classifier_enforce_enabled),
                    "held_exposure_class_by_token": dict(self._held_exposure_class_by_token),
                    "held_exposure_detail_by_token": dict(self._held_exposure_detail_by_token),
                    "held_dust_token_ids": list(self._held_dust_token_ids),
                    "held_dust_count": int(len(self._held_dust_token_ids)),
                    "held_dust_quarantined_token_ids": list(self._held_dust_quarantined_token_ids),
                    "held_dust_quarantined_count": int(len(self._held_dust_quarantined_token_ids)),
                    "held_dust_total_notional_upper_bound_usd": float(
                        self._held_dust_total_notional_upper_bound_usd
                    ),
                    "held_dust_shadow_candidate_active": bool(self._held_dust_shadow_candidate_active),
                    "held_dust_shadow_active": bool(self._held_dust_shadow_active),
                    "held_dust_shadow_enter_pending_cycles": int(self._held_dust_shadow_enter_pending_cycles),
                    "held_dust_shadow_clear_pending_cycles": int(self._held_dust_shadow_clear_pending_cycles),
                    "held_dust_enforced_this_cycle": bool(self._held_dust_enforced_this_cycle),
                    "held_dust_hard_degraded_exempt_count": int(
                        self._held_dust_effective_hard_degraded_exempt_count
                    ),
                    "held_dust_raw_hard_degraded_token_count": int(
                        self._held_dust_raw_hard_degraded_token_count
                    ),
                },
            )
        escalation_signature = (
            bool(held_unpriceable_escalation_active),
            tuple(held_unpriceable_escalation_token_ids),
            tuple(held_unpriceable_escalation_reasons),
            float(held_unpriceable_escalation_max_age_sec),
            float(held_unpriceable_escalation_threshold_sec),
            bool(held_unpriceable_defect_candidate),
            str(held_unpriceable_operator_action),
            tuple(sorted(held_unpriceable_cause_counts.items(), key=lambda item: item[0])),
            str(held_unpriceable_dominant_cause),
        )
        now_mono = time.monotonic()
        signature_changed = escalation_signature != self._last_held_unpriceable_escalation_signature
        throttle_interval_sec = float(self.held_unpriceable_operator_action_min_emit_interval_sec)
        emit_age_sec = max(0.0, now_mono - float(self._held_unpriceable_operator_action_last_emit_mono))
        throttle_elapsed = bool(
            throttle_interval_sec <= 0.0 or emit_age_sec >= (throttle_interval_sec - 1e-9)
        )
        should_emit_escalation_event = bool(
            signature_changed or (held_unpriceable_escalation_active and throttle_elapsed)
        )
        emit_reason = (
            "signature_change"
            if signature_changed
            else ("throttle_interval_elapsed" if should_emit_escalation_event else "suppressed_by_throttle")
        )
        if should_emit_escalation_event:
            self._last_held_unpriceable_escalation_signature = escalation_signature
            self._held_unpriceable_operator_action_last_emit_mono = now_mono
            self.events.log_event(
                "valuation_held_unpriceable_escalation",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "active": bool(held_unpriceable_escalation_active),
                    "defect_candidate": bool(held_unpriceable_defect_candidate),
                    "token_count": int(len(held_unpriceable_escalation_token_ids)),
                    "token_ids": list(held_unpriceable_escalation_token_ids),
                    "reasons": list(held_unpriceable_escalation_reasons),
                    "max_age_sec": float(held_unpriceable_escalation_max_age_sec),
                    "threshold_sec": float(held_unpriceable_escalation_threshold_sec),
                    "operator_action": str(held_unpriceable_operator_action),
                    "cause_counts": dict(held_unpriceable_cause_counts),
                    "dominant_cause": str(held_unpriceable_dominant_cause),
                    "severity": (
                        "critical"
                        if held_unpriceable_escalation_max_age_sec
                        >= (2.0 * max(1e-9, held_unpriceable_escalation_threshold_sec))
                        else "warning"
                    ),
                    "emit_reason": str(emit_reason),
                    "throttle_interval_sec": float(throttle_interval_sec),
                    "throttle_emit_age_sec": float(emit_age_sec),
                },
            )
        valuation_state["valuation_degraded"] = bool(degraded)
        valuation_state["valuation_hard_degraded"] = bool(hard_degraded)
        valuation_state["degraded_reasons"] = list(degraded_reasons)
        valuation_state["valuation_hard_degraded_pending_healthy_cycles"] = int(
            self._valuation_hard_degraded_pending_healthy_cycles
        )
        valuation_state["valuation_hard_degraded_clear_consecutive_healthy_cycles"] = int(
            self.valuation_hard_degraded_clear_consecutive_healthy_cycles
        )
        valuation_state["raw_valuation_degraded"] = bool(valuation_state.get("raw_valuation_degraded", False))
        valuation_state["raw_valuation_hard_degraded"] = bool(
            valuation_state.get("raw_valuation_hard_degraded", False)
        )
        valuation_state["held_exposure_class_by_token"] = dict(self._held_exposure_class_by_token)
        valuation_state["held_exposure_detail_by_token"] = dict(self._held_exposure_detail_by_token)
        valuation_state["held_dust_token_ids"] = list(self._held_dust_token_ids)
        valuation_state["held_dust_count"] = int(len(self._held_dust_token_ids))
        valuation_state["held_dust_quarantined_token_ids"] = list(self._held_dust_quarantined_token_ids)
        valuation_state["held_dust_quarantined_count"] = int(len(self._held_dust_quarantined_token_ids))
        valuation_state["held_dust_total_notional_upper_bound_usd"] = float(
            self._held_dust_total_notional_upper_bound_usd
        )
        valuation_state["dust_classifier_enforce_enabled"] = bool(self.dust_classifier_enforce_enabled)
        valuation_state["held_dust_shadow_candidate_active"] = bool(self._held_dust_shadow_candidate_active)
        valuation_state["held_dust_shadow_active"] = bool(self._held_dust_shadow_active)
        valuation_state["held_dust_shadow_enter_pending_cycles"] = int(self._held_dust_shadow_enter_pending_cycles)
        valuation_state["held_dust_shadow_clear_pending_cycles"] = int(self._held_dust_shadow_clear_pending_cycles)
        valuation_state["held_dust_enforced_this_cycle"] = bool(self._held_dust_enforced_this_cycle)
        valuation_state["held_dust_hard_degraded_exempt_count"] = int(
            self._held_dust_effective_hard_degraded_exempt_count
        )
        valuation_state["held_dust_raw_hard_degraded_token_count"] = int(
            self._held_dust_raw_hard_degraded_token_count
        )
        return valuation_state

    def _emit_guardian_hook(self, severity: str, message: str, details: Dict[str, Any]) -> None:
        path = self.alert_guardian_hook_file
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "severity": severity,
                "message": message,
                "details": details,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        except Exception as exc:
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "alerts",
                    "action": "guardian_hook_write",
                    "path": str(path),
                    "error": str(exc),
                }
            )

    def _evaluate_alert_policy(self, mode_snapshot: Any) -> None:
        warn_reasons = self._threshold_reasons(self.alert_warn_thresholds, mode_snapshot)
        page_reasons = self._threshold_reasons(self.alert_page_thresholds, mode_snapshot)
        auto_reasons = self._threshold_reasons(self.alert_auto_stop_thresholds, mode_snapshot)
        if auto_reasons and any(reason.startswith("stale_reject_ratio=") for reason in auto_reasons):
            if not self._stale_auto_stop_eligible(
                mode_snapshot,
                min_samples=self.alert_auto_stop_min_samples,
                min_stale_rejects=self.alert_auto_stop_min_stale_rejects,
                min_risk_rejects=self.alert_auto_stop_min_risk_rejects,
            ):
                auto_reasons = [reason for reason in auto_reasons if not reason.startswith("stale_reject_ratio=")]
                self.telemetry.incr("alert_auto_stop_suppressed_stale_ratio")
                self.events.log_event(
                    "alert_policy_auto_stop_suppressed",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "suppressed_reason": "stale_reject_ratio",
                        "sample_count": int(getattr(mode_snapshot, "sample_count", 0)),
                        "stale_reject_count": int(getattr(mode_snapshot, "stale_reject_count", 0)),
                        "risk_reject_count": int(getattr(mode_snapshot, "risk_reject_count", 0)),
                        "min_samples": self.alert_auto_stop_min_samples,
                        "min_stale_rejects": self.alert_auto_stop_min_stale_rejects,
                        "min_risk_rejects": self.alert_auto_stop_min_risk_rejects,
                    },
                )

        if warn_reasons:
            self.telemetry.incr("alert_warn_events")
            self.events.log_event(
                "alert_policy_warn",
                {"ts_utc": utc_iso(), "run_id": self.run_id, "reasons": warn_reasons},
            )
            self._emit_guardian_hook("warn", "alert_policy_warn", {"reasons": warn_reasons})
            self.alerts.notify(
                "warning",
                f"{self.bot_name} warn thresholds breached",
                {"run_id": self.run_id, "reasons": ",".join(warn_reasons[:6])},
                key="alert_policy_warn",
            )

        if page_reasons:
            self.telemetry.incr("alert_page_events")
            self.events.log_event(
                "alert_policy_page",
                {"ts_utc": utc_iso(), "run_id": self.run_id, "reasons": page_reasons},
            )
            self._emit_guardian_hook("page", "alert_policy_page", {"reasons": page_reasons})
            self.alerts.notify(
                "critical",
                f"{self.bot_name} page thresholds breached",
                {"run_id": self.run_id, "reasons": ",".join(page_reasons[:6])},
                key="alert_policy_page",
            )

        if auto_reasons and not self.risk.kill_switch:
            reason = "auto_safe_stop:" + ",".join(auto_reasons[:6])
            self.telemetry.incr("alert_auto_stop_events")
            self.events.log_event(
                "alert_policy_auto_stop",
                {"ts_utc": utc_iso(), "run_id": self.run_id, "reasons": auto_reasons},
            )
            self._emit_guardian_hook("auto_safe_stop", "alert_policy_auto_stop", {"reasons": auto_reasons})
            self.risk.set_kill_switch(reason)

    def _read_reconcile_mismatch_ratio(self) -> float:
        path = self.ramp_reconcile_status_path
        if path is None:
            return 0.0
        now = time.monotonic()
        if (now - self._last_reconcile_status_poll_mono) < self._reconcile_status_poll_interval_sec:
            return self._cached_reconcile_mismatch_ratio
        self._last_reconcile_status_poll_mono = now
        if not path.exists():
            self._cached_reconcile_mismatch_ratio = 0.0
            return self._cached_reconcile_mismatch_ratio
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mismatch = parse_float(payload.get("mismatch_ratio"))
            if mismatch is None:
                status = str(payload.get("status", "")).strip().lower()
                mismatch = 1.0 if status in {"mismatch", "failed", "error"} else 0.0
            self._cached_reconcile_mismatch_ratio = max(0.0, min(1.0, float(mismatch)))
        except Exception as exc:
            self.telemetry.incr("reconcile_status_read_errors")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "ramp",
                    "action": "read_reconcile_status",
                    "path": str(path),
                    "error": str(exc),
                }
            )
            self._cached_reconcile_mismatch_ratio = 1.0
        return self._cached_reconcile_mismatch_ratio

    def _disarmed_cycle_signal(self, latency_snapshot: LatencySnapshot) -> bool:
        if not self.latency_verifier.enabled:
            return False
        if not bool(self.cfg.get("chainlink", {}).get("enabled", False)):
            return False
        if latency_snapshot.sample_count < self.latency_verifier.min_samples:
            return False
        reason = str(getattr(latency_snapshot, "reason", "") or "").strip().lower()
        if reason == "lag_edge_not_present":
            # No detectable lag edge is an opportunity/market condition, not an ingest/runtime fault.
            # Keep trading gates fail-closed at the decision layer, but avoid escalating operating-mode
            # safety severity from this non-fault condition alone.
            return False
        return bool(latency_snapshot.disarmed)

    def _ws_slo_bootstrap_guard_active(self, *, has_targets: bool) -> bool:
        grace_sec = max(0.0, float(getattr(self, "operating_mode_ws_slo_bootstrap_grace_sec", 0.0)))
        if (not has_targets) or (not self.operating_mode_ws_slo_enforce) or grace_sec <= 0.0:
            self._ws_slo_bootstrap_active = False
            return False
        started_mono = float(getattr(self, "_ws_slo_bootstrap_started_mono", 0.0))
        now_mono = time.monotonic()
        if (not math.isfinite(started_mono)) or (started_mono <= 0.0) or (started_mono > now_mono):
            started_mono = now_mono
            self._ws_slo_bootstrap_started_mono = started_mono
        elapsed = max(0.0, now_mono - started_mono)
        active = elapsed < grace_sec
        self._ws_slo_bootstrap_active = bool(active)
        return bool(active)

    def _reset_ws_slo_bootstrap(self, *, reason: str) -> None:
        self._ws_slo_bootstrap_started_mono = time.monotonic()
        self._ws_slo_bootstrap_active = bool(self.token_ids) and self.operating_mode_ws_slo_bootstrap_grace_sec > 0.0
        self.events.log_event(
            "ws_slo_bootstrap_reset",
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "reason": str(reason),
                "token_count": len(self.token_ids),
                "grace_sec": float(self.operating_mode_ws_slo_bootstrap_grace_sec),
                "active": bool(self._ws_slo_bootstrap_active),
            },
        )

    def _ws_slo_degraded_cycle(
        self,
        *,
        has_targets: bool,
        book_feed_status: Dict[str, Any],
        chainlink_status: Dict[str, Any],
        all_targets_missing_ws_books: bool = False,
        rest_fallback_used_cycle: bool = False,
    ) -> Tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.operating_mode_ws_slo_enforce:
            return False, reasons
        if not has_targets:
            return False, reasons
        if self._ws_slo_bootstrap_guard_active(has_targets=has_targets):
            return False, reasons

        ws_enabled = bool(self.cfg.get("market_data", {}).get("ws", {}).get("enabled", False))
        if ws_enabled:
            book_connected = bool(book_feed_status.get("connected", False))
            if self.operating_mode_ws_slo_require_book_connected and not book_connected:
                reasons.append("book_feed_disconnected")
            if bool(all_targets_missing_ws_books) and bool(rest_fallback_used_cycle):
                reasons.append("book_feed_ws_books_missing_all_targets")
            book_age = parse_float(book_feed_status.get("last_msg_age_sec"))
            if (
                book_age is not None
                and book_age > float(self.operating_mode_ws_slo_max_book_last_msg_age_sec)
            ):
                reasons.append("book_feed_last_msg_age_high")

        chain_enabled = bool(self.cfg.get("chainlink", {}).get("enabled", False))
        if chain_enabled:
            chain_connected = bool(chainlink_status.get("connected", False))
            if self.operating_mode_ws_slo_require_chainlink_connected and not chain_connected:
                reasons.append("chainlink_disconnected")
            chain_age = parse_float(chainlink_status.get("last_tick_age_sec"))
            if (
                chain_age is not None
                and chain_age > float(self.operating_mode_ws_slo_max_chainlink_last_tick_age_sec)
            ):
                reasons.append("chainlink_last_tick_age_high")

        return bool(reasons), reasons

    def _build_fair_probability_map(self, books: Dict[str, Any], *, latency_snapshot: LatencySnapshot) -> Dict[str, float]:
        symbol_for_targets = self.chainlink_symbol_for_targets
        if not symbol_for_targets:
            return {}
        latest_chainlink = self.chainlink.get_latest(symbol_for_targets)
        if latest_chainlink is None:
            return {}
        if self.latency_verifier.require_armed_for_maker and not latency_snapshot.armed:
            return {}
        tick_age_sec = time.monotonic() - latest_chainlink.received_monotonic
        if tick_age_sec > self.doctrine_oracle_max_tick_age_sec:
            return {}

        out: Dict[str, float] = {}
        now = utc_now()
        for token_id in books.keys():
            strike = self.token_strike_by_token.get(token_id)
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            side = self.token_side_by_token.get(token_id)
            if strike is None or expiry_dt is None or side not in {"YES", "NO"}:
                continue
            if self.latency_verifier.require_armed_for_maker and not self._lag_verified(token_id):
                continue
            if self.latency_verifier.score_enabled:
                score = self.latency_verifier.token_score(token_id)
                if score < self.latency_verifier.score_min_for_maker:
                    continue
            sec_to_expiry = max(0.0, (expiry_dt - now).total_seconds())
            p_up = self._fair_probability_up(spot=latest_chainlink.price, strike=strike, sec_to_expiry=sec_to_expiry)
            fair = p_up if side == "YES" else (1.0 - p_up)
            fair = max(0.001, min(0.999, float(fair)))
            out[token_id] = fair
        return out

    def _build_secondary_fair_probability_map(self, token_ids: List[str]) -> Tuple[Dict[str, float], str]:
        if not bool(getattr(self.pyth, "enabled", False)):
            return {}, "disabled"
        latest_pyth = self.pyth.get_latest(self.pyth_symbol_for_targets)
        if latest_pyth is None:
            return {}, "unknown"
        now = utc_now()
        out: Dict[str, float] = {}
        for token_id in token_ids:
            strike = self.token_strike_by_token.get(token_id)
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            side = self.token_side_by_token.get(token_id)
            if strike is None or expiry_dt is None or side not in {"YES", "NO"}:
                continue
            sec_to_expiry = max(0.0, (expiry_dt - now).total_seconds())
            p_up = self._fair_probability_up(spot=latest_pyth.price, strike=strike, sec_to_expiry=sec_to_expiry)
            fair = p_up if side == "YES" else (1.0 - p_up)
            fair = max(0.001, min(0.999, float(fair)))
            out[token_id] = fair
        if not out:
            return {}, "unknown"
        return out, "available"

    def _record_lag_sample(
        self,
        token_id: str,
        lag_ms: float,
        *,
        ingest_lag_ms: Optional[float] = None,
        source_to_book_ms: Optional[float] = None,
    ) -> bool:
        return self.latency_verifier.observe(
            token_id=token_id,
            lag_ms=lag_ms,
            ingest_lag_ms=ingest_lag_ms,
            source_to_book_ms=source_to_book_ms,
        )

    def _lag_stats(self, token_id: str) -> Tuple[int, float, float]:
        stats = self.latency_verifier.token_stats(token_id)
        if stats is None:
            return 0, 0.0, 0.0
        return stats.sample_count, stats.median_lag_ms, stats.hit_rate

    def _lag_verified(self, token_id: str) -> bool:
        return self.latency_verifier.token_is_verified(token_id)

    def _sniper_context(self) -> Dict[str, Any]:
        now = utc_now()
        doctrine_mode = str(getattr(self, "doctrine_mode", "degraded")).strip().lower() or "degraded"
        arming_horizon_sec = float(
            getattr(self, "sniper_arming_horizon_sec", getattr(self, "sniper_window_start_sec", 20.0))
        )
        execution_cutoff_sec = float(
            getattr(self, "sniper_execution_cutoff_sec", getattr(self, "sniper_window_end_sec", 15.0))
        )
        near_tokens: Dict[str, float] = {}
        degraded_expiry_fallback_active = False
        for token_id in self.token_ids:
            expiry = self.token_expiry_dt_by_token.get(token_id)
            if expiry is None:
                continue
            sec_to_expiry = (expiry - now).total_seconds()
            if sec_to_expiry < 0:
                continue
            if execution_cutoff_sec <= sec_to_expiry <= arming_horizon_sec:
                near_tokens[token_id] = sec_to_expiry
        if not near_tokens and self.sniper_allow_without_expiry_metadata and doctrine_mode == "degraded":
            # Paper/runtime fallback: keep sniper evaluable when expiry metadata is absent
            # or when no targets are inside the configured window.
            fallback_tokens: list[str] = []
            for token_id in self.token_ids:
                expiry = self.token_expiry_dt_by_token.get(token_id)
                if expiry is None or (expiry - now).total_seconds() >= 0:
                    fallback_tokens.append(token_id)
            near_tokens = {token_id: float(arming_horizon_sec) for token_id in fallback_tokens}
            degraded_expiry_fallback_active = bool(near_tokens)
        last_fallback_active = bool(getattr(self, "_last_degraded_expiry_fallback_active", False))
        if degraded_expiry_fallback_active != last_fallback_active:
            self._last_degraded_expiry_fallback_active = degraded_expiry_fallback_active
            if hasattr(self, "events"):
                self.events.log_event(
                    "degraded_path_status",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": getattr(self, "run_id", ""),
                        "doctrine_mode": doctrine_mode,
                        "path": "sniper_allow_without_expiry_metadata",
                        "active": degraded_expiry_fallback_active,
                        "reason": (
                            "degraded_expiry_fallback_enabled"
                            if degraded_expiry_fallback_active
                            else "degraded_expiry_fallback_disabled"
                        ),
                    },
                )
        if not near_tokens:
            return {
                "active": False,
                "token_ids": [],
                "near_token_ids": [],
                "lag_verified_token_ids": [],
                "sec_to_expiry_min": None,
                "lag_verified_token_count": 0,
                "degraded_expiry_fallback_active": degraded_expiry_fallback_active,
            }

        lag_verified_tokens = [token_id for token_id in near_tokens.keys() if self._lag_verified(token_id)]
        active_tokens = lag_verified_tokens if self.sniper_require_lag_verification else list(near_tokens.keys())
        sec_to_expiry_min = min(near_tokens.values())
        if active_tokens:
            sec_to_expiry_min = min(near_tokens[token_id] for token_id in active_tokens)
        return {
            "active": bool(active_tokens),
            "token_ids": active_tokens,
            "near_token_ids": list(near_tokens.keys()),
            "lag_verified_token_ids": lag_verified_tokens,
            "sec_to_expiry_min": sec_to_expiry_min,
            "lag_verified_token_count": len(lag_verified_tokens),
            "degraded_expiry_fallback_active": degraded_expiry_fallback_active,
        }

    @staticmethod
    def _stage_name_for_sec_to_expiry(sec_to_expiry: Optional[float]) -> str:
        if sec_to_expiry is None:
            return STAGE_UNKNOWN
        if sec_to_expiry < 0:
            return STAGE_EXPIRED
        if sec_to_expiry > 120.0:
            return STAGE_OBSERVE
        if sec_to_expiry > 90.0:
            return STAGE_EVALUATE
        if sec_to_expiry > 60.0:
            return STAGE_MAKER_POSITION
        if sec_to_expiry > 30.0:
            return STAGE_MAKER_TAKER_SELECTIVE
        if sec_to_expiry > 20.0:
            return STAGE_SNIPER_PRIMARY
        return STAGE_EXTREME_ONLY

    @staticmethod
    def _stage_policy(stage: str) -> Tuple[bool, bool]:
        return edge_stage_policy(stage)

    def _resolve_taker_required_min_edge(self, stage: str) -> float:
        normalized_stage = str(stage or "").strip().upper()
        explicit_stage_edge = self.sniper_taker_min_edge_by_stage.get(normalized_stage)
        if explicit_stage_edge is not None:
            return float(explicit_stage_edge)
        required_min_edge = float(self.sniper_taker_min_edge)
        if normalized_stage == STAGE_EXTREME_ONLY:
            required_min_edge = float(self.sniper_taker_min_edge) * max(
                1.0, float(self.sniper_taker_extreme_edge_mult)
            )
        return required_min_edge

    def _resolve_taker_cooldown_sec(self, stage: str) -> float:
        normalized_stage = str(stage or "").strip().upper()
        explicit_stage_cooldown = self.sniper_taker_per_token_cooldown_sec_by_stage.get(normalized_stage)
        if isinstance(explicit_stage_cooldown, (int, float)):
            return max(0.0, float(explicit_stage_cooldown))
        return max(0.0, float(self.sniper_taker_per_token_cooldown_sec))

    def _emit_sniper_stage_window_semantic_check(self) -> None:
        if not bool(self.sniper_taker_competitiveness_cfg.enabled):
            return

        def _effective_window(stage_name: str) -> float:
            stage_windows = self.sniper_taker_competitiveness_cfg.stage_final_window_sec_by_stage
            candidate = stage_windows.get(stage_name)
            if isinstance(candidate, (int, float)) and float(candidate) > 0.0:
                return float(candidate)
            return float(self.sniper_taker_competitiveness_cfg.final_window_sec)

        # Stage bands are authoritative from _stage_name_for_sec_to_expiry.
        stage_bands: Dict[str, Tuple[float, float]] = {
            STAGE_MAKER_TAKER_SELECTIVE: (30.0, 60.0),
            STAGE_SNIPER_PRIMARY: (20.0, 30.0),
            STAGE_EXTREME_ONLY: (0.0, 20.0),
        }
        rows: Dict[str, Dict[str, Any]] = {}
        dead_count = 0
        for stage_name, (lower_exclusive, upper_inclusive) in stage_bands.items():
            effective_window_sec = _effective_window(stage_name)
            semantically_live = bool(effective_window_sec > lower_exclusive)
            if not semantically_live:
                dead_count += 1
            overlap_high = min(upper_inclusive, effective_window_sec)
            rows[stage_name] = {
                "interval_lower_exclusive_sec": float(lower_exclusive),
                "interval_upper_inclusive_sec": float(upper_inclusive),
                "effective_final_window_sec": float(effective_window_sec),
                "semantically_live": semantically_live,
                "overlap_high_sec": (float(overlap_high) if semantically_live else None),
                "semantic_dead_reason": (
                    None if semantically_live else "stage_window_non_overlapping_with_stage_interval"
                ),
            }

        self.events.log_event(
            "sniper_stage_window_semantic_check",
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "final_window_enabled": bool(self.sniper_taker_competitiveness_cfg.final_window_enabled),
                "default_final_window_sec": float(self.sniper_taker_competitiveness_cfg.final_window_sec),
                "stage_rows": rows,
                "semantic_dead_by_construction_count": int(dead_count),
                "semantic_status": ("ok" if dead_count == 0 else "warn"),
            },
        )

    @staticmethod
    def _taker_edge_bucket(edge_abs: Optional[float]) -> str:
        if not isinstance(edge_abs, (int, float)):
            return "unknown"
        edge = float(edge_abs)
        if edge <= 0.10:
            return "le_0p10"
        if edge <= 0.30:
            return "0p10_0p30"
        if edge <= 0.60:
            return "0p30_0p60"
        return "gt_0p60"

    def _taker_effective_max_target_usd(
        self,
        *,
        price: Optional[float],
    ) -> Optional[float]:
        if not isinstance(price, (int, float)) or float(price) <= 0.0:
            return None
        px = float(price)
        caps: List[float] = []
        if self.sniper_taker_sizing_max_usd > 0.0:
            caps.append(float(self.sniper_taker_sizing_max_usd))
        if self.sniper_taker_wallet_max_notional_per_order_usdc > 0.0:
            caps.append(float(self.sniper_taker_wallet_max_notional_per_order_usdc))
        strategy_share_cap = float(getattr(self.manager, "strategy_max_order_size", 0.0) or 0.0)
        if strategy_share_cap > 0.0:
            caps.append(strategy_share_cap * px)
        risk_share_cap = float(self.sniper_taker_max_order_size_shares or 0.0)
        if risk_share_cap > 0.0:
            caps.append(risk_share_cap * px)
        if not caps:
            return None
        value = min(caps)
        return float(value) if value > 0.0 else None

    def _refresh_sniper_multi_oracle_cap_from_wallet(self) -> None:
        pct_cap = float(self.sniper_taker_competitiveness_cfg.multi_oracle_capital_pct_cap)
        if pct_cap <= 0.0:
            self.sniper_taker_multi_oracle_cap_usd = None
            self.sniper_taker_multi_oracle_cap_source = "disabled"
            self.sniper_taker_multi_oracle_cap_authority_class = "none"
            return
        wallet_contract = self.wallet.status_contract()
        authority_class = str(wallet_contract.get("authority_status_class") or "").strip().lower()
        deployable_capital = parse_float(wallet_contract.get("deployable_capital"))
        if authority_class == "authoritative" and isinstance(deployable_capital, (int, float)) and deployable_capital > 0.0:
            self.sniper_taker_multi_oracle_cap_usd = float(pct_cap * float(deployable_capital))
            self.sniper_taker_multi_oracle_cap_source = "wallet_deployable_capital_authoritative"
            self.sniper_taker_multi_oracle_cap_authority_class = "live"
            return
        if isinstance(self.sniper_taker_multi_oracle_cap_usd, (int, float)) and self.sniper_taker_multi_oracle_cap_usd > 0.0:
            self.sniper_taker_multi_oracle_cap_source = "orchestration_heuristic_config_fallback"
            self.sniper_taker_multi_oracle_cap_authority_class = "derived"
            return
        self.sniper_taker_multi_oracle_cap_usd = None
        self.sniper_taker_multi_oracle_cap_source = "wallet_contract_unavailable"
        self.sniper_taker_multi_oracle_cap_authority_class = "bootstrap"

    def _maker_timing_gate_open(self, sec_to_expiry: Optional[float]) -> bool:
        if not self.maker_comp_timing_gate_enabled:
            return True
        if not isinstance(sec_to_expiry, (int, float)):
            return False
        sec = float(sec_to_expiry)
        return self.maker_comp_timing_gate_min_sec_to_expiry <= sec <= self.maker_comp_timing_gate_max_sec_to_expiry

    def _maker_edge_strength(self, edge_abs: Optional[float]) -> float:
        if (not self.maker_comp_edge_scale_enabled) or (not isinstance(edge_abs, (int, float))):
            return 0.0
        start = float(self.maker_comp_edge_scale_start_abs)
        full = float(self.maker_comp_edge_scale_full_abs)
        edge = max(0.0, float(edge_abs))
        if edge <= start:
            return 0.0
        if edge >= full:
            return 1.0
        span = max(1e-9, full - start)
        return max(0.0, min(1.0, (edge - start) / span))

    @staticmethod
    def _maker_edge_bucket(edge_abs: Optional[float]) -> str:
        if not isinstance(edge_abs, (int, float)):
            return "unknown"
        edge = float(edge_abs)
        if edge <= 0.05:
            return "le_0p05"
        if edge <= 0.10:
            return "0p05_0p10"
        if edge <= 0.20:
            return "0p10_0p20"
        return "gt_0p20"

    def _maker_competitiveness_profile(
        self,
        *,
        token_id: str,
        top: Any,
        fair_probability: Optional[float],
        stage: str,
        sec_to_expiry: Optional[float],
        base_size_multiplier: float,
        base_spread_multiplier: float,
        timing_gate_open: bool,
    ) -> Dict[str, Any]:
        market_probability = (
            float(getattr(top, "midpoint"))
            if top is not None and isinstance(getattr(top, "midpoint", None), (int, float))
            else None
        )
        fair = float(fair_probability) if isinstance(fair_probability, (int, float)) else None
        edge_signed = (fair - market_probability) if (fair is not None and market_probability is not None) else None
        edge_abs = abs(edge_signed) if edge_signed is not None else None
        strength = self._maker_edge_strength(edge_abs)
        size_mult_comp = 1.0 + ((float(self.maker_comp_size_mult_max) - 1.0) * strength)
        spread_mult_comp = 1.0 - ((1.0 - float(self.maker_comp_spread_mult_min)) * strength)
        requote_mult_comp = 1.0 - ((1.0 - float(self.maker_comp_requote_delta_mult_min)) * strength)
        size_multiplier_applied = max(0.01, float(base_size_multiplier) * size_mult_comp)
        spread_multiplier_applied = max(1e-6, float(base_spread_multiplier) * spread_mult_comp)
        requote_delta_applied = max(1e-9, float(self.maker_comp_base_requote_delta) * requote_mult_comp)

        normalized_stage = str(stage or "").strip().upper()
        one_sided_stage_allowed = normalized_stage in self.maker_comp_one_sided_allowed_stages
        side_policy = "TWO_SIDED"
        one_sided_active = False
        if (
            self.maker_comp_one_sided_enabled
            and one_sided_stage_allowed
            and edge_signed is not None
            and abs(edge_signed) >= float(self.maker_comp_one_sided_edge_threshold_abs)
        ):
            side_policy = "BUY_ONLY" if edge_signed >= 0.0 else "SELL_ONLY"
            one_sided_active = True

        edge_bucket = self._maker_edge_bucket(edge_abs)
        competitiveness_context = {
            "token_id": str(token_id),
            "stage": normalized_stage,
            "sec_to_expiry": (float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None),
            "timing_gate_enabled": bool(self.maker_comp_timing_gate_enabled),
            "timing_gate_open": bool(timing_gate_open),
            "timing_gate_min_sec_to_expiry": float(self.maker_comp_timing_gate_min_sec_to_expiry),
            "timing_gate_max_sec_to_expiry": float(self.maker_comp_timing_gate_max_sec_to_expiry),
            "edge_scale_enabled": bool(self.maker_comp_edge_scale_enabled),
            "edge_signed": (float(edge_signed) if edge_signed is not None else None),
            "edge_abs": (float(edge_abs) if edge_abs is not None else None),
            "edge_bucket": edge_bucket,
            "edge_strength_normalized": float(strength),
            "market_probability": market_probability,
            "fair_probability": fair,
            "size_multiplier_base": float(base_size_multiplier),
            "size_multiplier_competitiveness": float(size_mult_comp),
            "size_multiplier_applied": float(size_multiplier_applied),
            "spread_multiplier_base": float(base_spread_multiplier),
            "spread_multiplier_competitiveness": float(spread_mult_comp),
            "spread_multiplier_applied": float(spread_multiplier_applied),
            "requote_delta_base": float(self.maker_comp_base_requote_delta),
            "requote_delta_multiplier_competitiveness": float(requote_mult_comp),
            "requote_delta_applied": float(requote_delta_applied),
            "one_sided_enabled": bool(self.maker_comp_one_sided_enabled),
            "one_sided_allowed_stage": bool(one_sided_stage_allowed),
            "one_sided_edge_threshold_abs": float(self.maker_comp_one_sided_edge_threshold_abs),
            "side_policy": side_policy,
            "one_sided_active": bool(one_sided_active),
        }
        return {
            "size_multiplier_applied": float(size_multiplier_applied),
            "spread_multiplier_applied": float(spread_multiplier_applied),
            "requote_delta_applied": float(requote_delta_applied),
            "side_policy": side_policy,
            "context": competitiveness_context,
        }

    def _token_stage_info(self, token_id: str) -> Dict[str, Any]:
        now = utc_now()
        expiry = self.token_expiry_dt_by_token.get(token_id)
        market_key = str(self.token_market_key_by_token.get(token_id, "")).strip()
        reason = ""
        sec_to_expiry: Optional[float] = None
        raw_stage = STAGE_UNKNOWN
        hold_active = False
        hold_cycles_remaining = 0
        hold_seconds_remaining = 0.0
        if expiry is None:
            reason = "missing_expiry_metadata"
        else:
            sec_to_expiry = (expiry - now).total_seconds()
            if sec_to_expiry < 0:
                reason = "expired_market"
        if not market_key:
            reason = (reason or "missing_market_key")
        stage = self._stage_name_for_sec_to_expiry(sec_to_expiry)
        raw_stage = stage
        if not market_key:
            stage = STAGE_UNKNOWN
        expired_reduce_only_grace_active = (
            stage == STAGE_EXPIRED
            and self._held_expired_reduce_only_grace_active(token_id=token_id, sec_to_expiry=sec_to_expiry)
        )
        if expired_reduce_only_grace_active:
            stage = STAGE_MAKER_TAKER_SELECTIVE
            reason = "expired_reduce_only_grace_active"
        reduce_only_recovery = self._reduce_only_recovery_payload(
            token_id=token_id,
            sec_to_expiry=sec_to_expiry,
            expired_reduce_only_grace_active=bool(expired_reduce_only_grace_active),
        )
        if stage not in {STAGE_UNKNOWN, STAGE_EXPIRED}:
            entry_mono = self._market_entry_mono_by_token.get(token_id)
            entry_cycle = self._market_entry_cycle_by_token.get(token_id)
            if entry_mono is not None and entry_cycle is not None:
                elapsed_sec = max(0.0, time.monotonic() - entry_mono)
                elapsed_cycles = max(0, int(self._doctrine_cycle_index) - int(entry_cycle))
                hold_cycles_remaining = max(0, self.doctrine_min_observe_cycles_on_entry - elapsed_cycles)
                hold_seconds_remaining = max(0.0, self.doctrine_min_observe_seconds_on_entry - elapsed_sec)
                hold_active = (hold_cycles_remaining > 0) or (hold_seconds_remaining > 0.0)
                if hold_active:
                    stage = STAGE_OBSERVE
                    reason = f"observe_hold_active:{raw_stage}"
        allow_maker, allow_taker = self._stage_policy(stage)
        if bool(reduce_only_recovery.get("active", False)) and stage not in {STAGE_UNKNOWN, STAGE_EXPIRED}:
            allow_maker, allow_taker = True, True
            recovery_reason = str(reduce_only_recovery.get("reason") or "").strip()
            if recovery_reason:
                if reason:
                    reason_parts = [str(part).strip() for part in str(reason).split("|") if str(part).strip()]
                    if recovery_reason not in reason_parts:
                        reason = f"{reason}|{recovery_reason}"
                else:
                    reason = recovery_reason
        verdict = "pass" if stage not in {STAGE_UNKNOWN, STAGE_EXPIRED} else "fail"
        if verdict == "fail" and not reason:
            reason = "stage_not_tradeable"
        return {
            "stage": stage,
            "raw_stage": raw_stage,
            "sec_to_expiry": sec_to_expiry,
            "market_key": market_key,
            "observe_hold_active": hold_active,
            "observe_hold_cycles_remaining": hold_cycles_remaining,
            "observe_hold_seconds_remaining": hold_seconds_remaining,
            "expired_reduce_only_grace_active": bool(expired_reduce_only_grace_active),
            "preexpiry_reduce_only_active": bool(reduce_only_recovery.get("preexpiry_active", False)),
            "reduce_only_recovery_active": bool(reduce_only_recovery.get("active", False)),
            "reduce_only_recovery_reason": str(reduce_only_recovery.get("reason") or ""),
            "reduce_only_side": str(reduce_only_recovery.get("side") or "NONE"),
            "reduce_only_side_policy": str(reduce_only_recovery.get("side_policy") or "NONE"),
            "reduce_only_size_cap_shares": float(reduce_only_recovery.get("size_cap_shares", 0.0) or 0.0),
            "reduce_only_size_cap_below_min_order_size": bool(
                reduce_only_recovery.get("size_cap_below_min_order_size", False)
            ),
            "reduce_only_min_order_size_shares": float(
                reduce_only_recovery.get("min_order_size_shares", self.risk_min_order_size_shares) or 0.0
            ),
            "reduce_only_net_shares": float(reduce_only_recovery.get("net_shares", 0.0) or 0.0),
            "reduce_only_open_order_present": bool(reduce_only_recovery.get("open_order_present", False)),
            "held_preexpiry_reduce_only_sec": float(reduce_only_recovery.get("preexpiry_window_sec", 0.0) or 0.0),
            "allow_maker": allow_maker,
            "allow_taker": allow_taker,
            "doctrine_gate_verdict": verdict,
            "reason": reason,
        }

    def _emit_doctrine_decisions(
        self,
        stage_info_by_token: Dict[str, Dict[str, Any]],
        *,
        maker_prereq_failure_by_token: Optional[Dict[str, str]] = None,
    ) -> None:
        maker_prereq_failure_by_token = maker_prereq_failure_by_token or {}
        for token_id, info in stage_info_by_token.items():
            stage = str(info.get("stage", STAGE_UNKNOWN))
            base_verdict = str(info.get("doctrine_gate_verdict", "fail"))
            base_reason = str(info.get("reason", ""))
            allow_maker = bool(info.get("allow_maker", False))
            allow_taker = bool(info.get("allow_taker", False))
            prereq_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip()
            maker_prereq_ok = not bool(prereq_reason)
            verdict = base_verdict
            reason = base_reason
            if self.doctrine_mode == "canonical" and allow_maker and prereq_reason:
                verdict = "fail"
                reason = prereq_reason
            signature = (stage, verdict, reason, allow_maker, allow_taker)
            if self._last_doctrine_signature_by_token.get(token_id) == signature:
                continue
            prev_stage = self._last_stage_by_token.get(token_id)
            if prev_stage != stage:
                self.events.log_event(
                    "stage_transition",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "token_id": token_id,
                        "market_key": str(info.get("market_key", "")),
                        "from_stage": prev_stage or "",
                        "to_stage": stage,
                        "sec_to_expiry": info.get("sec_to_expiry"),
                        "doctrine_mode": self.doctrine_mode,
                    },
                )
                self._last_stage_by_token[token_id] = stage
            self._last_doctrine_signature_by_token[token_id] = signature
            self.events.log_event(
                "doctrine_decision",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_id": token_id,
                    "market_key": str(info.get("market_key", "")),
                    "doctrine_mode": self.doctrine_mode,
                    "stage": stage,
                    "raw_stage": str(info.get("raw_stage", stage)),
                    "sec_to_expiry": info.get("sec_to_expiry"),
                    "observe_hold_active": bool(info.get("observe_hold_active", False)),
                    "observe_hold_cycles_remaining": int(info.get("observe_hold_cycles_remaining", 0)),
                    "observe_hold_seconds_remaining": float(info.get("observe_hold_seconds_remaining", 0.0)),
                    "maker_allowed": allow_maker,
                    "maker_prereq_ok": maker_prereq_ok,
                    "taker_allowed": allow_taker,
                    "doctrine_gate_verdict": verdict,
                    "reason": reason,
                },
            )
            if stage == STAGE_UNKNOWN and verdict == "fail":
                last_failure = self._last_doctrine_prereq_failure_by_token.get(token_id)
                if last_failure != reason:
                    self._last_doctrine_prereq_failure_by_token[token_id] = reason
                    self.events.log_event(
                        "doctrine_prereq_failure",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "token_id": token_id,
                            "market_key": str(info.get("market_key", "")),
                            "doctrine_mode": self.doctrine_mode,
                            "stage": stage,
                            "doctrine_gate_verdict": verdict,
                            "reason": reason,
                        },
                    )

    def _oracle_freshness(self) -> Tuple[bool, Optional[float], str]:
        symbol_for_targets = self.chainlink_symbol_for_targets
        if not symbol_for_targets:
            return False, None, "chainlink_symbol_missing"
        latest_chainlink = self.chainlink.get_latest(symbol_for_targets)
        if latest_chainlink is None:
            return False, None, "chainlink_tick_missing"
        tick_age_sec = max(0.0, time.monotonic() - latest_chainlink.received_monotonic)
        if tick_age_sec > self.doctrine_oracle_max_tick_age_sec:
            return False, tick_age_sec, "chainlink_tick_stale"
        return True, tick_age_sec, ""

    def _maker_prereq_failure_reason(
        self,
        token_id: str,
        *,
        fair_probability_by_token: Dict[str, float],
        latency_snapshot: LatencySnapshot,
        oracle_fresh: bool,
        reduce_only_recovery_active: bool = False,
    ) -> str:
        if self.token_expiry_dt_by_token.get(token_id) is None:
            return "missing_expiry_metadata"
        if self.token_strike_by_token.get(token_id) is None:
            return "missing_threshold_metadata"
        if str(self.token_side_by_token.get(token_id, "")).strip().upper() not in {"YES", "NO"}:
            return "missing_side_metadata"
        if not oracle_fresh:
            return "oracle_unavailable_or_stale"
        if (
            self.latency_verifier.require_armed_for_maker
            and (not bool(reduce_only_recovery_active))
            and (not latency_snapshot.armed)
        ):
            return "latency_not_armed_for_maker"
        if (
            self.latency_verifier.require_armed_for_maker
            and (not bool(reduce_only_recovery_active))
            and (not self._lag_verified(token_id))
        ):
            return "token_lag_not_verified_for_maker"
        if self.latency_verifier.score_enabled and (not bool(reduce_only_recovery_active)):
            if self.latency_verifier.token_score(token_id) < self.latency_verifier.score_min_for_maker:
                return "token_score_below_maker_min"
        if (token_id not in fair_probability_by_token) and (not bool(reduce_only_recovery_active)):
            return "fair_probability_unavailable"
        return ""

    @staticmethod
    def _book_source(top: Any) -> str:
        if top is None:
            return ""
        return str(getattr(top, "source", "") or "").strip().lower()

    def _resolve_maker_market_reference(
        self,
        *,
        top: Any,
        maker_prereq_failure_reason: str,
    ) -> Dict[str, Any]:
        midpoint: Optional[float] = None
        if top is not None and isinstance(getattr(top, "midpoint", None), (int, float)):
            midpoint = float(getattr(top, "midpoint"))
        bid_price = float(getattr(top, "best_bid_price")) if isinstance(getattr(top, "best_bid_price", None), (int, float)) else None
        ask_price = float(getattr(top, "best_ask_price")) if isinstance(getattr(top, "best_ask_price", None), (int, float)) else None

        if midpoint is not None:
            return {
                "market_probability": midpoint,
                "market_reference_mode": "direct_midpoint",
                "market_reference_basis": "direct_book_midpoint",
                "market_reference_confidence": "authoritative",
                "market_reference_fallback_used": False,
                "market_reference_source_side": "none",
                "market_reference_class": "authoritative",
                "decision_input_type_override": None,
                "decision_input_data_class_override": None,
            }

        has_bid = bid_price is not None
        has_ask = ask_price is not None
        prereq_pass = not bool(str(maker_prereq_failure_reason or "").strip())
        fallback_allowed = bool(
            self.doctrine_mode == "canonical"
            and self.doctrine_maker_allow_bounded_single_side_reference
            and prereq_pass
            and self._book_source_is_ws(top)
            and (has_bid ^ has_ask)
        )
        if fallback_allowed:
            if has_bid and not has_ask:
                return {
                    "market_probability": float(bid_price),
                    "market_reference_mode": "bounded_single_side_touch",
                    "market_reference_basis": "ws_single_side_touch",
                    "market_reference_confidence": "bounded_low",
                    "market_reference_fallback_used": True,
                    "market_reference_source_side": "bid",
                    "market_reference_class": "bounded_approximation",
                    "decision_input_type_override": "bounded_derived",
                    "decision_input_data_class_override": "observed_other",
                }
            if has_ask and not has_bid:
                return {
                    "market_probability": float(ask_price),
                    "market_reference_mode": "bounded_single_side_touch",
                    "market_reference_basis": "ws_single_side_touch",
                    "market_reference_confidence": "bounded_low",
                    "market_reference_fallback_used": True,
                    "market_reference_source_side": "ask",
                    "market_reference_class": "bounded_approximation",
                    "decision_input_type_override": "bounded_derived",
                    "decision_input_data_class_override": "observed_other",
                }

        return {
            "market_probability": None,
            "market_reference_mode": "missing",
            "market_reference_basis": "missing",
            "market_reference_confidence": "none",
            "market_reference_fallback_used": False,
            "market_reference_source_side": "none",
            "market_reference_class": "not_available",
            "decision_input_type_override": None,
            "decision_input_data_class_override": None,
        }

    @staticmethod
    def _decision_input_type_from_source(source: str) -> str:
        normalized = str(source or "").strip().lower()
        if normalized in {"paper", "simulated", "synthetic", "emulated"}:
            return "emulated"
        if normalized in {"replay", "replayed"}:
            return "replayed"
        if normalized == "rest":
            return "bounded_derived"
        if normalized in {"ws", "chainlink"}:
            return "observed_live"
        if normalized:
            # Keep non-empty unknown sources explicit; do not silently upcast.
            return "unknown"
        return "unknown"

    @staticmethod
    def _execution_realism_class_for_scope(scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if normalized == EDGE_EVAL_SCOPE_MAKER:
            # Queue position / time-priority is not explicitly modeled in paper.
            return "not_modeled"
        if normalized == EDGE_EVAL_SCOPE_TAKER:
            # Immediate paper fills are bounded by observed top-of-book liquidity.
            return "bounded_approximation"
        return "not_modeled"

    @classmethod
    def _book_source_is_ws(cls, top: Any) -> bool:
        return cls._book_source(top) == "ws"

    @classmethod
    def _latency_sample_token_ids(cls, books: Dict[str, Any]) -> list[str]:
        return [str(token_id) for token_id, top in books.items() if cls._book_source_is_ws(top)]

    @classmethod
    def _apply_canonical_maker_ws_source_gate(
        cls,
        *,
        books: Dict[str, Any],
        maker_eligible_tokens: set[str],
        maker_prereq_failure_by_token: Dict[str, str],
        maker_reduce_only_recovery_tokens: Optional[set[str]] = None,
    ) -> set[str]:
        gated_tokens: set[str] = set()
        recovery_tokens = set(str(token_id) for token_id in (maker_reduce_only_recovery_tokens or set()))
        for token_id in maker_eligible_tokens:
            top = books.get(token_id)
            if top is None:
                gated_tokens.add(token_id)
                continue
            if not cls._book_source_is_ws(top):
                if token_id in recovery_tokens:
                    # Recovery-only lane may rely on REST fallback when WS quote shape
                    # is unusable for held-token unwind. Keep normal-mode WS doctrine intact.
                    gated_tokens.add(token_id)
                    continue
                maker_prereq_failure_by_token.setdefault(token_id, "maker_requires_ws_book_source")
                continue
            gated_tokens.add(token_id)
        return gated_tokens

    def _emit_edge_evaluation(
        self,
        *,
        token_id: str,
        target_ref: Optional[str] = None,
        evaluation_scope: str,
        stage: str,
        time_remaining_sec: Optional[float],
        fair_probability: Optional[float],
        market_probability: Optional[float],
        edge_value: Optional[float],
        oracle_tick_age_sec: Optional[float],
        latency_state: str,
        maker_allowed: bool,
        taker_allowed: bool,
        action_taken: str,
        block_reason: Optional[str],
        submitted: bool,
        filled: bool,
        result: Optional[Any] = None,
        cycle_index: Optional[int] = None,
        order_id: Optional[str] = None,
        submitted_order_ids: Optional[List[str]] = None,
        book_source: Optional[str] = None,
        maker_no_submission_cause: Optional[str] = None,
        maker_no_submission_category: Optional[str] = None,
        market_reference_mode: Optional[str] = None,
        market_reference_basis: Optional[str] = None,
        market_reference_confidence: Optional[str] = None,
        market_reference_fallback_used: Optional[bool] = None,
        market_reference_source_side: Optional[str] = None,
        market_reference_class: Optional[str] = None,
        decision_input_type_override: Optional[str] = None,
        decision_input_data_class_override: Optional[str] = None,
        taker_submit_reject_reason: Optional[str] = None,
        reduce_only_recovery_active: Optional[bool] = None,
        reduce_only_recovery_reason: Optional[str] = None,
    ) -> None:
        normalized_action = str(action_taken or EDGE_ACTION_NONE).strip().lower() or EDGE_ACTION_NONE
        normalized_scope = str(evaluation_scope or "").strip().lower()
        normalized_block_reason = str(block_reason or "").strip() or None
        if normalized_scope not in EDGE_EVAL_SCOPES:
            raise ValueError(f"edge_eval_scope_invalid:{normalized_scope or 'missing'}")
        if normalized_action not in EDGE_ACTIONS:
            raise ValueError(f"edge_eval_action_invalid:{normalized_action or 'missing'}")
        if normalized_action == EDGE_ACTION_NONE:
            if normalized_block_reason is None:
                raise ValueError("edge_eval_block_reason_missing_for_no_action")
            if not is_canonical_block_reason(normalized_block_reason):
                raise ValueError(f"edge_eval_block_reason_invalid:{normalized_block_reason}")
        else:
            if normalized_scope != normalized_action:
                raise ValueError(
                    f"edge_eval_scope_action_mismatch:scope={normalized_scope}:action={normalized_action}"
                )
            if normalized_block_reason is not None:
                raise ValueError("edge_eval_block_reason_present_for_action")
        if result is not None:
            raise ValueError("edge_eval_result_must_be_null")
        ts_utc = utc_iso()
        normalized_token_id = str(token_id or "").strip()
        normalized_target_ref = str(target_ref or "").strip() or None
        if normalized_target_ref is None and normalized_token_id:
            normalized_target_ref = hashlib.sha256(normalized_token_id.encode("utf-8")).hexdigest()[:16]
        normalized_submitted_order_ids = sorted(
            {
                str(value).strip()
                for value in list(submitted_order_ids or [])
                if str(value or "").strip()
            }
        )
        payload = {
            "ts_utc": ts_utc,
            "timestamp_utc": ts_utc,
            "run_id": self.run_id,
            "token_id": normalized_token_id,
            "target_ref": normalized_target_ref,
            "evaluation_scope": normalized_scope,
            "cycle_index": (int(cycle_index) if cycle_index is not None else int(self._doctrine_cycle_index)),
            "stage": str(stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN,
            "time_remaining_sec": (
                float(time_remaining_sec) if isinstance(time_remaining_sec, (int, float)) else None
            ),
            "fair_probability": (float(fair_probability) if isinstance(fair_probability, (int, float)) else None),
            "market_probability": (
                float(market_probability) if isinstance(market_probability, (int, float)) else None
            ),
            "edge_value": (float(edge_value) if isinstance(edge_value, (int, float)) else None),
            "oracle_tick_age_sec": (
                float(oracle_tick_age_sec) if isinstance(oracle_tick_age_sec, (int, float)) else None
            ),
            "latency_state": str(latency_state or "").strip().lower() or None,
            "maker_allowed": bool(maker_allowed),
            "taker_allowed": bool(taker_allowed),
            "action_taken": normalized_action,
            "block_reason": normalized_block_reason,
            "submitted": bool(submitted),
            "filled": bool(filled),
            "result": result,
            "order_id": (str(order_id).strip() if str(order_id or "").strip() else None),
            "submitted_order_ids": normalized_submitted_order_ids,
            "book_source": (str(book_source).strip().lower() if str(book_source or "").strip() else None),
            "maker_no_submission_cause": (
                str(maker_no_submission_cause or "").strip().lower() or None
            ),
            "maker_no_submission_category": (
                str(maker_no_submission_category or "").strip().lower() or None
            ),
            "taker_submit_reject_reason": (
                str(taker_submit_reject_reason or "").strip().lower() or None
            ),
            "reduce_only_recovery_active": bool(reduce_only_recovery_active),
            "reduce_only_recovery_reason": (
                str(reduce_only_recovery_reason or "").strip().lower() or None
            ),
        }
        if payload["block_reason"] != "maker_no_submission":
            payload["maker_no_submission_cause"] = None
            payload["maker_no_submission_category"] = None
        if payload["block_reason"] != "taker_submit_rejected":
            payload["taker_submit_reject_reason"] = None
        normalized_market_reference_mode = str(market_reference_mode or "").strip().lower()
        if not normalized_market_reference_mode:
            normalized_market_reference_mode = "direct_midpoint" if payload["market_probability"] is not None else "missing"
        normalized_market_reference_basis = str(market_reference_basis or "").strip().lower()
        if not normalized_market_reference_basis:
            if normalized_market_reference_mode == "direct_midpoint":
                normalized_market_reference_basis = "direct_book_midpoint"
            elif normalized_market_reference_mode == "bounded_single_side_touch":
                normalized_market_reference_basis = "ws_single_side_touch"
            else:
                normalized_market_reference_basis = "missing"
        normalized_market_reference_confidence = str(market_reference_confidence or "").strip().lower()
        if not normalized_market_reference_confidence:
            if normalized_market_reference_mode == "direct_midpoint":
                normalized_market_reference_confidence = "authoritative"
            elif normalized_market_reference_mode == "bounded_single_side_touch":
                normalized_market_reference_confidence = "bounded_low"
            else:
                normalized_market_reference_confidence = "none"
        normalized_market_reference_source_side = str(market_reference_source_side or "").strip().lower()
        if normalized_market_reference_source_side not in {"bid", "ask"}:
            normalized_market_reference_source_side = "none"
        fallback_used = (
            bool(market_reference_fallback_used)
            if market_reference_fallback_used is not None
            else (normalized_market_reference_mode == "bounded_single_side_touch")
        )
        normalized_market_reference_class = str(market_reference_class or "").strip().lower()
        if not normalized_market_reference_class:
            if normalized_market_reference_mode == "bounded_single_side_touch":
                normalized_market_reference_class = "bounded_approximation"
            elif normalized_market_reference_mode == "direct_midpoint":
                normalized_market_reference_class = "authoritative"
            else:
                normalized_market_reference_class = "not_available"
        payload["market_reference_mode"] = normalized_market_reference_mode
        payload["market_reference_basis"] = normalized_market_reference_basis
        payload["market_reference_confidence"] = normalized_market_reference_confidence
        payload["market_reference_fallback_used"] = bool(fallback_used)
        payload["market_reference_source_side"] = normalized_market_reference_source_side
        payload["market_reference_class"] = normalized_market_reference_class
        decision_input_source = str(payload.get("book_source") or "").strip().lower()
        if decision_input_type_override is not None:
            decision_input_type = str(decision_input_type_override or "").strip().lower()
        else:
            decision_input_type = self._decision_input_type_from_source(decision_input_source)
        if decision_input_type not in {"observed_live", "replayed", "bounded_derived", "emulated", "unknown"}:
            raise ValueError(f"edge_eval_decision_input_type_invalid:{decision_input_type or 'missing'}")
        decision_input_emulated = decision_input_type == "emulated"
        if decision_input_data_class_override is not None:
            decision_input_data_class = str(decision_input_data_class_override or "").strip().lower()
            if decision_input_data_class not in {"observed_live", "observed_other", "emulated", "unknown"}:
                raise ValueError(
                    f"edge_eval_decision_input_data_class_invalid:{decision_input_data_class or 'missing'}"
                )
        else:
            if decision_input_type == "emulated":
                decision_input_data_class = "emulated"
            elif decision_input_type == "observed_live":
                decision_input_data_class = "observed_live"
            elif decision_input_type in {"replayed", "bounded_derived"}:
                decision_input_data_class = "observed_other"
            else:
                decision_input_data_class = "unknown"
        payload["decision_input_source"] = decision_input_source or None
        payload["decision_input_type"] = decision_input_type
        payload["decision_input_emulated"] = bool(decision_input_emulated)
        payload["decision_input_data_class"] = decision_input_data_class
        payload["execution_realism_class"] = self._execution_realism_class_for_scope(normalized_scope)
        self.events.log_event("edge_evaluation", payload)

    @staticmethod
    def _target_ref_for_token(token_id: str) -> Optional[str]:
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id:
            return None
        return hashlib.sha256(normalized_token_id.encode("utf-8")).hexdigest()[:16]

    def _emit_maker_edge_evaluations(
        self,
        *,
        books: Dict[str, Any],
        stage_info_by_token: Dict[str, Dict[str, Any]],
        maker_eval_token_ids: set[str],
        maker_submitted_token_ids: set[str],
        maker_submitted_order_ids_by_token: Dict[str, List[str]],
        maker_no_submission_reason_by_token: Optional[Dict[str, str]],
        maker_no_submission_category_by_token: Optional[Dict[str, str]],
        maker_prereq_failure_by_token: Dict[str, str],
        fair_probability_by_token: Dict[str, float],
        oracle_tick_age_sec: Optional[float],
        latency_state: str,
        cycle_index: int,
    ) -> None:
        maker_no_submission_reason_by_token = maker_no_submission_reason_by_token or {}
        maker_no_submission_category_by_token = maker_no_submission_category_by_token or {}
        for token_id in sorted(str(x) for x in maker_eval_token_ids):
            info = stage_info_by_token.get(token_id, {})
            stage = str(info.get("stage", STAGE_UNKNOWN))
            default_maker_allowed, default_taker_allowed = edge_stage_policy(stage)
            maker_allowed = bool(info.get("allow_maker", default_maker_allowed))
            taker_allowed = bool(info.get("allow_taker", default_taker_allowed))
            time_remaining_sec = info.get("sec_to_expiry")
            reduce_only_recovery_active = bool(info.get("reduce_only_recovery_active", False))
            reduce_only_recovery_reason = str(info.get("reduce_only_recovery_reason") or "").strip()
            top = books.get(token_id)
            maker_prereq_failure_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip()
            market_reference = self._resolve_maker_market_reference(
                top=top,
                maker_prereq_failure_reason=maker_prereq_failure_reason,
            )
            market_probability = market_reference.get("market_probability")
            fair_probability = fair_probability_by_token.get(token_id)
            edge_value = compute_edge_value(
                fair_probability=fair_probability,
                market_probability=market_probability,
            )
            validation = validate_edge_inputs(
                EdgeInputSnapshot(
                    fair_probability=fair_probability,
                    market_probability=market_probability,
                    time_remaining_sec=time_remaining_sec,
                    oracle_tick_age_sec=oracle_tick_age_sec,
                    latency_state=latency_state,
                    stage=stage,
                    evaluation_scope=EDGE_EVAL_SCOPE_MAKER,
                ),
                oracle_max_tick_age_sec=float(self.doctrine_oracle_max_tick_age_sec),
                require_latency_state=bool(self.latency_verifier.require_armed_for_maker),
            )

            submitted = token_id in maker_submitted_token_ids
            submitted_order_ids = sorted(
                {
                    str(order_id).strip()
                    for order_id in list(maker_submitted_order_ids_by_token.get(token_id, []))
                    if str(order_id or "").strip()
                }
            )
            action_taken = EDGE_ACTION_MAKER if submitted else EDGE_ACTION_NONE
            block_reason: Optional[str] = None
            maker_no_submission_cause: Optional[str] = None
            maker_no_submission_category: Optional[str] = None
            if not submitted:
                if not maker_allowed:
                    block_reason = "stage_disallow_maker"
                elif maker_prereq_failure_reason:
                    block_reason = maker_prereq_failure_reason
                elif not validation.valid:
                    block_reason = str(validation.reason_code or "")
                else:
                    block_reason = "maker_no_submission"
                    maker_no_submission_cause = (
                        str(maker_no_submission_reason_by_token.get(token_id, "")).strip().lower() or "unspecified"
                    )
                    maker_no_submission_category = (
                        str(maker_no_submission_category_by_token.get(token_id, "")).strip().lower() or "unknown"
                    )

            self._emit_edge_evaluation(
                token_id=token_id,
                target_ref=self._target_ref_for_token(token_id),
                evaluation_scope=EDGE_EVAL_SCOPE_MAKER,
                stage=stage,
                time_remaining_sec=time_remaining_sec if isinstance(time_remaining_sec, (int, float)) else None,
                fair_probability=fair_probability if isinstance(fair_probability, (int, float)) else None,
                market_probability=market_probability if isinstance(market_probability, (int, float)) else None,
                edge_value=edge_value if isinstance(edge_value, (int, float)) else None,
                oracle_tick_age_sec=oracle_tick_age_sec,
                latency_state=latency_state,
                maker_allowed=maker_allowed,
                taker_allowed=taker_allowed,
                action_taken=action_taken,
                block_reason=block_reason,
                submitted=submitted,
                filled=False,
                result=None,
                cycle_index=cycle_index,
                order_id=(submitted_order_ids[0] if len(submitted_order_ids) == 1 else None),
                submitted_order_ids=submitted_order_ids,
                book_source=(self._book_source(top) or None),
                maker_no_submission_cause=maker_no_submission_cause,
                maker_no_submission_category=maker_no_submission_category,
                market_reference_mode=str(market_reference.get("market_reference_mode") or ""),
                market_reference_basis=str(market_reference.get("market_reference_basis") or ""),
                market_reference_confidence=str(market_reference.get("market_reference_confidence") or ""),
                market_reference_fallback_used=bool(market_reference.get("market_reference_fallback_used", False)),
                market_reference_source_side=str(market_reference.get("market_reference_source_side") or "none"),
                market_reference_class=str(market_reference.get("market_reference_class") or ""),
                decision_input_type_override=(
                    str(market_reference.get("decision_input_type_override"))
                    if market_reference.get("decision_input_type_override") is not None
                    else None
                ),
                decision_input_data_class_override=(
                    str(market_reference.get("decision_input_data_class_override"))
                    if market_reference.get("decision_input_data_class_override") is not None
                    else None
                ),
                reduce_only_recovery_active=reduce_only_recovery_active,
                reduce_only_recovery_reason=reduce_only_recovery_reason,
            )

    @staticmethod
    def _arrival_bucket(sec_to_expiry: Optional[float]) -> str:
        if sec_to_expiry is None:
            return "unknown_on_arrival"
        if sec_to_expiry < 0:
            return "expired_on_arrival"
        if sec_to_expiry <= 20.0:
            return "extreme_only_on_arrival"
        if sec_to_expiry <= 30.0:
            return "sniper_primary_on_arrival"
        return "normal_on_arrival"

    def _on_market_key_transition(self, token_id: str, old_key: str, new_key: str) -> None:
        now = utc_now()
        now_mono = time.monotonic()
        self._market_entry_mono_by_token[token_id] = now_mono
        self._market_entry_cycle_by_token[token_id] = int(self._doctrine_cycle_index)
        self.last_midpoint_by_token.pop(token_id, None)
        self.last_midpoint_ts_mono_by_token.pop(token_id, None)
        self.last_volatility_by_token.pop(token_id, None)
        self._last_taker_submit_mono_by_token.pop(token_id, None)
        self._last_doctrine_signature_by_token.pop(token_id, None)
        self._last_doctrine_prereq_failure_by_token.pop(token_id, None)
        self._last_stage_by_token.pop(token_id, None)
        expiry_dt = self.token_expiry_dt_by_token.get(token_id)
        sec_to_expiry = (expiry_dt - now).total_seconds() if expiry_dt is not None else None
        self.events.log_event(
            "market_epoch_transition",
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "token_id": token_id,
                "old_market_key": old_key,
                "new_market_key": new_key,
                "arrival_bucket": self._arrival_bucket(sec_to_expiry),
                "sec_to_expiry_on_arrival": sec_to_expiry,
                "observe_hold_min_cycles": int(self.doctrine_min_observe_cycles_on_entry),
                "observe_hold_min_seconds": float(self.doctrine_min_observe_seconds_on_entry),
            },
        )

    def _fetch_missing_books(
        self,
        token_ids: list[str],
    ) -> tuple[Dict[str, tuple[Any, Any, float]], Dict[str, str]]:
        if not token_ids:
            return {}, {}

        def _fetch_one(token_id: str) -> tuple[Any, Any, float]:
            started = time.monotonic()
            top, raw = self.book_client.fetch_book(token_id)
            latency_ms = (time.monotonic() - started) * 1000.0
            return top, raw, latency_ms

        books_by_token: Dict[str, tuple[Any, Any, float]] = {}
        errors_by_token: Dict[str, str] = {}
        pool = self._rest_fetch_pool
        if pool is None or len(token_ids) <= 1:
            for token_id in token_ids:
                try:
                    books_by_token[token_id] = _fetch_one(token_id)
                except Exception as exc:
                    errors_by_token[token_id] = str(exc)
            return books_by_token, errors_by_token

        future_by_token: Dict[concurrent.futures.Future[tuple[Any, Any, float]], str] = {}
        for token_id in token_ids:
            future_by_token[pool.submit(_fetch_one, token_id)] = token_id
        for future in concurrent.futures.as_completed(future_by_token):
            token_id = future_by_token[future]
            try:
                books_by_token[token_id] = future.result()
            except Exception as exc:
                errors_by_token[token_id] = str(exc)
        return books_by_token, errors_by_token

    def _run_sniper_taker(
        self,
        *,
        books: Dict[str, Any],
        fair_probability_by_token: Dict[str, float],
        realized_volatility_by_token: Optional[Dict[str, float]] = None,
        secondary_fair_probability_by_token: Optional[Dict[str, float]] = None,
        secondary_oracle_base_status: str = "disabled",
        token_ids: list[str],
        stage_info_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
        oracle_tick_age_sec: Optional[float] = None,
        latency_snapshot: Optional[LatencySnapshot] = None,
        mode_state: str = MODE_NORMAL,
        lag_ready_for_sniper: bool = True,
        lag_verified_token_ids: Optional[list[str]] = None,
        sniper_ramp_allowed: bool = True,
        cycle_index: Optional[int] = None,
        oracle_fresh: bool = True,
    ) -> Dict[str, Any]:
        if not token_ids:
            return {
                "attempts": 0,
                "submitted": 0,
                "submitted_token_ids": [],
                "filled_token_ids": [],
            }

        attempts = 0
        submitted = 0
        fills_accepted_total = 0
        submitted_token_ids: set[str] = set()
        filled_token_ids: set[str] = set()
        max_orders = max(0, int(self.sniper_taker_max_orders_per_cycle))
        stage_info_by_token = stage_info_by_token or {}
        lag_verified_set = {str(x) for x in (lag_verified_token_ids or [])}
        latency_state = (
            str(latency_snapshot.state).strip().lower()
            if isinstance(latency_snapshot, LatencySnapshot)
            else str(self._last_latency_state or "").strip().lower()
        )
        competitiveness_enabled = bool(self.sniper_taker_competitiveness_cfg.enabled)
        stable_cycle_index = int(self._doctrine_cycle_index if cycle_index is None else cycle_index)
        secondary_fair_probability_by_token = dict(secondary_fair_probability_by_token or {})
        secondary_oracle_base_status = str(secondary_oracle_base_status or "unknown").strip().lower() or "unknown"

        # Use limited taker budget on strongest edge opportunities first.
        token_order = sorted({str(token_id) for token_id in token_ids})
        stage_priority_rank = {
            STAGE_SNIPER_PRIMARY: 0,
            STAGE_MAKER_TAKER_SELECTIVE: 1,
            STAGE_EXTREME_ONLY: 2,
        }
        token_order.sort(
            key=lambda token_id: (
                (
                    int(
                        stage_priority_rank.get(
                            str((stage_info_by_token.get(token_id) or {}).get("stage") or STAGE_UNKNOWN).strip().upper(),
                            99,
                        )
                    )
                    if bool(self.sniper_taker_competitiveness_cfg.stage_priority_enabled)
                    else 0
                ),
                -abs(
                    float(
                        compute_edge_value(
                            fair_probability=fair_probability_by_token.get(token_id),
                            market_probability=(books.get(token_id).midpoint if books.get(token_id) is not None else None),
                        )
                        or 0.0
                    )
                ),
                token_id,
            )
        )

        for token_id in token_order:
            info = stage_info_by_token.get(token_id, {})
            stage = str(info.get("stage", STAGE_UNKNOWN))
            default_maker_allowed, default_taker_allowed = edge_stage_policy(stage)
            maker_allowed = bool(info.get("allow_maker", default_maker_allowed))
            taker_allowed = bool(info.get("allow_taker", default_taker_allowed))
            time_remaining_sec = info.get("sec_to_expiry")
            reduce_only_recovery_active = bool(info.get("reduce_only_recovery_active", False))
            reduce_only_recovery_reason = str(info.get("reduce_only_recovery_reason") or "").strip()
            reduce_only_side = str(info.get("reduce_only_side") or "NONE").strip().upper() or "NONE"
            reduce_only_size_cap_shares = float(info.get("reduce_only_size_cap_shares", 0.0) or 0.0)
            reduce_only_size_cap_below_min_order_size = bool(
                info.get("reduce_only_size_cap_below_min_order_size", False)
            )
            top = books.get(token_id)
            midpoint = top.midpoint if top is not None else None
            fair = fair_probability_by_token.get(token_id)
            edge = compute_edge_value(
                fair_probability=fair,
                market_probability=midpoint,
            )
            validation = validate_edge_inputs(
                EdgeInputSnapshot(
                    fair_probability=fair,
                    market_probability=midpoint,
                    time_remaining_sec=time_remaining_sec,
                    oracle_tick_age_sec=oracle_tick_age_sec,
                    latency_state=latency_state,
                    stage=stage,
                    evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
                ),
                oracle_max_tick_age_sec=float(self.doctrine_oracle_max_tick_age_sec),
                require_latency_state=bool(self.latency_verifier.require_armed_for_sniper),
            )

            action_taken = EDGE_ACTION_NONE
            block_reason: Optional[str] = None
            was_submitted = False
            was_filled = False
            emitted_order_id: Optional[str] = None
            taker_submit_reject_reason: Optional[str] = None
            decision_target_ref = self._target_ref_for_token(token_id)
            required_min_edge = self._resolve_taker_required_min_edge(stage)

            if not self.sniper_enabled:
                block_reason = "sniper_disabled"
            elif not self.sniper_taker_enabled:
                block_reason = "sniper_taker_disabled"
            elif max_orders <= 0:
                block_reason = "taker_budget_disabled"
            elif mode_state == MODE_MAKER_ONLY and (not reduce_only_recovery_active):
                block_reason = "operating_mode_maker_only"
            elif mode_state == MODE_SAFE_STOP and (not reduce_only_recovery_active):
                block_reason = "operating_mode_safe_stop"
            elif mode_state != MODE_NORMAL and (not reduce_only_recovery_active):
                block_reason = "operating_mode_non_normal"
            elif not lag_ready_for_sniper:
                block_reason = "latency_not_armed"
            elif not sniper_ramp_allowed:
                block_reason = "ramp_sniper_disabled"
            elif (
                self.sniper_require_lag_verification
                and token_id not in lag_verified_set
                and (not reduce_only_recovery_active)
            ):
                block_reason = "token_lag_not_verified"
            elif not taker_allowed:
                block_reason = "stage_disallow_taker"
            elif not oracle_fresh:
                block_reason = "oracle_unavailable_or_stale"
            elif (
                self.doctrine_mode == "canonical"
                and top is not None
                and (not self._book_source_is_ws(top))
                and (not reduce_only_recovery_active)
            ):
                block_reason = "taker_requires_ws_book_source"
            elif (not validation.valid) and not (
                reduce_only_recovery_active
                and str(validation.reason_code or "") in {"market_probability_missing", "fair_probability_missing"}
            ):
                block_reason = str(validation.reason_code or "")
            elif edge is None and (not reduce_only_recovery_active):
                block_reason = "edge_value_invalid"
            elif (not reduce_only_recovery_active) and abs(float(edge)) < float(required_min_edge):
                block_reason = "edge_below_min"
            else:
                if reduce_only_recovery_active:
                    if reduce_only_size_cap_below_min_order_size:
                        block_reason = "reduce_only_recovery_size_cap_below_min_order_size"
                    elif reduce_only_side not in {"BUY", "SELL"}:
                        block_reason = "reduce_only_recovery_no_reducing_side"
                    elif reduce_only_size_cap_shares <= 1e-9:
                        block_reason = "reduce_only_recovery_size_cap_unavailable"
                now_mono = time.monotonic()
                last_submit = self._last_taker_submit_mono_by_token.get(token_id)
                cooldown_sec = self._resolve_taker_cooldown_sec(stage)
                if (
                    last_submit is not None
                    and (now_mono - last_submit) < cooldown_sec
                ):
                    block_reason = "taker_token_cooldown"
                else:
                    if self.latency_verifier.score_enabled:
                        score = self.latency_verifier.token_score(token_id)
                        if score < self.latency_verifier.score_min_for_taker:
                            block_reason = "token_score_below_taker_min"
                    if block_reason is None and submitted >= max_orders:
                        block_reason = "taker_order_budget_exhausted"
                    if block_reason is None:
                        side = reduce_only_side if reduce_only_recovery_active else ("BUY" if float(edge) > 0.0 else "SELL")
                        touch_price = top.best_ask_price if side == "BUY" else top.best_bid_price
                        if touch_price is None:
                            block_reason = (
                                "reduce_only_recovery_touch_price_unavailable"
                                if reduce_only_recovery_active
                                else "taker_price_unavailable"
                            )
                        else:
                            token_stats = self.latency_verifier.token_stats(token_id)
                            token_median_lag_ms = (
                                float(token_stats.median_lag_ms)
                                if token_stats is not None
                                and isinstance(getattr(token_stats, "median_lag_ms", None), (int, float))
                                else None
                            )
                            confidence_score = self.latency_verifier.token_score(token_id)
                            multi_oracle_status = secondary_oracle_base_status
                            multi_oracle_confirmation = False
                            if (not reduce_only_recovery_active) and competitiveness_enabled and bool(
                                self.sniper_taker_competitiveness_cfg.multi_oracle_boost_enabled
                            ):
                                secondary_fair = secondary_fair_probability_by_token.get(token_id)
                                if secondary_oracle_base_status == "disabled":
                                    multi_oracle_status = "disabled"
                                elif not (
                                    isinstance(secondary_fair, (int, float))
                                    and isinstance(midpoint, (int, float))
                                    and isinstance(fair, (int, float))
                                    and isinstance(edge, (int, float))
                                ):
                                    multi_oracle_status = "unknown"
                                else:
                                    secondary_edge = compute_edge_value(
                                        fair_probability=float(secondary_fair),
                                        market_probability=float(midpoint),
                                    )
                                    if (
                                        secondary_edge is None
                                        or abs(float(secondary_edge)) <= 1e-12
                                        or abs(float(edge)) <= 1e-12
                                    ):
                                        multi_oracle_status = "unknown"
                                    else:
                                        same_direction = (float(secondary_edge) > 0.0) == (float(edge) > 0.0)
                                        multi_oracle_confirmation = bool(same_direction)
                                        multi_oracle_status = "confirmed" if same_direction else "direction_mismatch"
                            decision: Optional[SniperDecision] = None
                            if competitiveness_enabled and (not reduce_only_recovery_active):
                                self._refresh_sniper_multi_oracle_cap_from_wallet()
                                static_max_feasible_target_usd = self._taker_effective_max_target_usd(
                                    price=float(touch_price)
                                )
                                dynamic_preview: Dict[str, Any] = {
                                    "predicted_dynamic_feasible": False,
                                    "predicted_feasible_target_usd": None,
                                    "predicted_reject_reason": None,
                                    "preview_authority": "none",
                                }
                                if bool(self.sniper_taker_competitiveness_cfg.dynamic_preview_enabled):
                                    dynamic_preview = self.manager.preview_taker_dynamic_feasible_target(
                                        token_id=token_id,
                                        side=side,
                                        price=float(touch_price),
                                        target_usd_cap=static_max_feasible_target_usd,
                                        top=top,
                                        reason="sniper_taker_chainlink",
                                        stage=stage,
                                        realized_volatility=(
                                            realized_volatility_by_token.get(token_id)
                                            if isinstance(realized_volatility_by_token, dict)
                                            else None
                                        ),
                                        competitiveness_context={
                                            "stage": str(stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN,
                                            "sec_to_expiry": (
                                                float(time_remaining_sec)
                                                if isinstance(time_remaining_sec, (int, float))
                                                else None
                                            ),
                                            "edge_abs": abs(float(edge)),
                                        },
                                    )
                                decision = self.sniper_tool.evaluate_batch(
                                    candidates=[
                                        SniperCandidate(
                                            token_id=token_id,
                                            stage=str(stage or STAGE_UNKNOWN),
                                            sec_to_expiry=(
                                                float(time_remaining_sec)
                                                if isinstance(time_remaining_sec, (int, float))
                                                else None
                                            ),
                                            edge_value=float(edge),
                                            required_min_edge=float(required_min_edge),
                                            base_target_usd=float(self.sniper_taker_target_usd),
                                            top_best_bid_price=(
                                                float(top.best_bid_price)
                                                if isinstance(top.best_bid_price, (int, float))
                                                else None
                                            ),
                                            top_best_ask_price=(
                                                float(top.best_ask_price)
                                                if isinstance(top.best_ask_price, (int, float))
                                                else None
                                            ),
                                            token_score=float(confidence_score),
                                            max_feasible_target_usd=static_max_feasible_target_usd,
                                            predicted_dynamic_feasible_target_usd=(
                                                float(dynamic_preview.get("predicted_feasible_target_usd"))
                                                if isinstance(
                                                    dynamic_preview.get("predicted_feasible_target_usd"),
                                                    (int, float),
                                                )
                                                else None
                                            ),
                                            predicted_dynamic_reject_reason=(
                                                str(dynamic_preview.get("predicted_reject_reason"))
                                                if str(dynamic_preview.get("predicted_reject_reason") or "").strip()
                                                else None
                                            ),
                                            multi_oracle_confirmation=bool(multi_oracle_confirmation),
                                            multi_oracle_status=str(multi_oracle_status),
                                            multi_oracle_cap_usd=(
                                                float(self.sniper_taker_multi_oracle_cap_usd)
                                                if isinstance(self.sniper_taker_multi_oracle_cap_usd, (int, float))
                                                and self.sniper_taker_multi_oracle_cap_usd > 0.0
                                                else None
                                            ),
                                        )
                                    ],
                                    max_orders_per_cycle=max(0, int(max_orders - submitted)),
                                ).decisions[0]
                                self.events.log_event(
                                    "sniper_taker_decision",
                                    {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "token_id": token_id,
                                        "midpoint": midpoint,
                                        "fair_probability": fair,
                                        "edge": edge,
                                        "confidence_score": float(confidence_score),
                                        "cooldown_sec_applied": float(cooldown_sec),
                                        **decision.as_event_payload(),
                                    },
                                )
                                if decision.dynamic_size_capped_by_risk:
                                    self.telemetry.incr("taker_dynamic_size_capped_by_risk")
                                if str(decision.multi_oracle_status).strip().lower() == "unknown":
                                    self.telemetry.incr("taker_multi_oracle_unknown")
                                if bool(decision.multi_oracle_confirmation):
                                    self.telemetry.incr("taker_multi_oracle_confirmation")
                                if bool(decision.multi_oracle_boost_applied):
                                    self.telemetry.incr("taker_multi_oracle_boost_applied")
                                if not decision.should_submit:
                                    block_reason = str(decision.block_reason or "taker_submit_rejected")
                            if block_reason is None:
                                submit_side = (
                                    str(decision.side or side).strip().upper()
                                    if competitiveness_enabled and decision is not None
                                    else side
                                )
                                submit_price = (
                                    float(decision.price)
                                    if competitiveness_enabled and decision is not None and isinstance(decision.price, (int, float))
                                    else float(touch_price)
                                )
                                if reduce_only_recovery_active:
                                    submit_side = reduce_only_side
                                    submit_price = float(touch_price)
                                submit_target_usd = (
                                    float(decision.target_usd_resolved)
                                    if competitiveness_enabled and decision is not None
                                    else float(self.sniper_taker_target_usd)
                                )
                                competitiveness_context = (
                                    decision.as_competitiveness_payload()
                                    if competitiveness_enabled and decision is not None
                                    else None
                                )
                                if not isinstance(competitiveness_context, dict):
                                    competitiveness_context = {}
                                competitiveness_context["financial_posture_class"] = str(self._financial_posture_class)
                                if reduce_only_recovery_active:
                                    recovery_context = dict(competitiveness_context or {})
                                    recovery_context["financial_posture_class"] = str(self._financial_posture_class)
                                    recovery_context["reduce_only_recovery_active"] = True
                                    recovery_context["reduce_only_recovery_reason"] = str(reduce_only_recovery_reason)
                                    recovery_context["reduce_only_side"] = str(reduce_only_side)
                                    recovery_context["reduce_only_side_policy"] = (
                                        "BUY_ONLY" if reduce_only_side == "BUY" else "SELL_ONLY"
                                    )
                                    recovery_context["reduce_only_size_cap_shares"] = float(reduce_only_size_cap_shares)
                                    recovery_context["reduce_only_size_cap_below_min_order_size"] = bool(
                                        reduce_only_size_cap_below_min_order_size
                                    )
                                    recovery_context["reduce_only_min_order_size_shares"] = float(
                                        info.get("reduce_only_min_order_size_shares", self.risk_min_order_size_shares)
                                        or 0.0
                                    )
                                    recovery_context["preexpiry_reduce_only_active"] = bool(
                                        info.get("preexpiry_reduce_only_active", False)
                                    )
                                    recovery_context["held_preexpiry_reduce_only_sec"] = float(
                                        info.get("held_preexpiry_reduce_only_sec", 0.0) or 0.0
                                    )
                                    competitiveness_context = recovery_context
                                attempts += 1
                                outcome = self.manager.place_taker_order_with_outcome(
                                    token_id=token_id,
                                    side=submit_side,
                                    price=submit_price,
                                    size=(float(self.sniper_taker_order_size) if self.sizing_mode == "shares" else None),
                                    target_usd=submit_target_usd,
                                    top=top,
                                    reason="sniper_taker_chainlink",
                                    stage=str(stage or STAGE_UNKNOWN),
                                    target_ref=decision_target_ref,
                                    decision_reference_midpoint=(
                                        float(midpoint) if isinstance(midpoint, (int, float)) else None
                                    ),
                                    decision_reference_source="edge_decision_market_midpoint",
                                    decision_reference_lookup_key=(
                                        f"target_ref:{decision_target_ref}" if decision_target_ref else None
                                    ),
                                    decision_reference_ts_utc=utc_iso(),
                                    token_median_lag_ms=token_median_lag_ms,
                                    oracle_tick_age_sec=(
                                        float(oracle_tick_age_sec)
                                        if isinstance(oracle_tick_age_sec, (int, float))
                                        else None
                                    ),
                                    realized_volatility=(
                                        float(realized_volatility_by_token.get(token_id))
                                        if isinstance(realized_volatility_by_token, dict)
                                        and isinstance(realized_volatility_by_token.get(token_id), (int, float))
                                        else None
                                    ),
                                    competitiveness_context=competitiveness_context,
                                )
                                if bool(outcome.get("submitted", False)):
                                    submitted += 1
                                    submitted_token_ids.add(token_id)
                                    self._last_taker_submit_mono_by_token[token_id] = now_mono
                                    action_taken = EDGE_ACTION_TAKER
                                    was_submitted = True
                                    fills_accepted = int(outcome.get("fills_accepted", 0) or 0)
                                    if fills_accepted > 0:
                                        was_filled = True
                                        filled_token_ids.add(token_id)
                                    fills_accepted_total += max(0, fills_accepted)
                                    emitted_order_id = str(outcome.get("order_id") or "").strip() or None
                                    submit_payload: Dict[str, Any] = {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "token_id": token_id,
                                        "order_id": emitted_order_id,
                                        "side": submit_side,
                                        "price": float(submit_price),
                                        "size": (
                                            float(self.sniper_taker_order_size)
                                            if self.sizing_mode == "shares"
                                            else None
                                        ),
                                        "target_usd": float(submit_target_usd),
                                        "midpoint": midpoint,
                                        "fair_probability": fair,
                                        "edge": edge,
                                        "edge_abs": (abs(float(edge)) if isinstance(edge, (int, float)) else None),
                                        "edge_bucket": self._taker_edge_bucket(
                                            abs(float(edge)) if isinstance(edge, (int, float)) else None
                                        ),
                                        "edge_unknown_reason": (
                                            None if isinstance(edge, (int, float)) else "missing_edge_value"
                                        ),
                                        "required_min_edge": float(required_min_edge),
                                        "stage": (str(stage or "").strip().upper() or "UNKNOWN"),
                                        "stage_unknown_reason": (
                                            None if str(stage or "").strip() else "missing_stage_info"
                                        ),
                                        "confidence_score": float(confidence_score),
                                        "reduce_only_recovery_active": bool(reduce_only_recovery_active),
                                        "reduce_only_recovery_reason": (
                                            str(reduce_only_recovery_reason) if reduce_only_recovery_active else ""
                                        ),
                                        "reduce_only_side": (
                                            str(reduce_only_side) if reduce_only_recovery_active else "NONE"
                                        ),
                                        "reduce_only_size_cap_shares": (
                                            float(reduce_only_size_cap_shares) if reduce_only_recovery_active else 0.0
                                        ),
                                    }
                                    if competitiveness_enabled and decision is not None:
                                        submit_payload.update(
                                            {
                                                "edge_abs": float(decision.edge_abs),
                                                "edge_bucket": self._taker_edge_bucket(decision.edge_abs),
                                                "edge_unknown_reason": None,
                                                "conviction_score": float(decision.conviction_score),
                                                "timing_window_class": decision.timing_window_class,
                                                "aggressiveness_level": decision.aggressiveness_level,
                                                "price_aggress_bps_applied": float(
                                                    decision.price_aggress_bps_applied
                                                ),
                                                "target_usd_requested": float(decision.target_usd_requested),
                                                "target_usd_resolved": float(decision.target_usd_resolved),
                                                "hard_min_floor_applied": bool(decision.hard_min_floor_applied),
                                                "hard_min_unachievable": bool(decision.hard_min_unachievable),
                                                "dynamic_size_capped_by_risk": bool(
                                                    decision.dynamic_size_capped_by_risk
                                                ),
                                                "multi_oracle_confirmation": bool(
                                                    decision.multi_oracle_confirmation
                                                ),
                                                "multi_oracle_boost_applied": bool(
                                                    decision.multi_oracle_boost_applied
                                                ),
                                                "multi_oracle_status": str(
                                                    decision.multi_oracle_status or "unknown"
                                                ),
                                            }
                                        )
                                    self.events.log_event("sniper_taker_submit", submit_payload)
                                else:
                                    block_reason = "taker_submit_rejected"
                                    taker_submit_reject_reason = (
                                        str(outcome.get("submit_reject_reason") or "").strip().lower() or None
                                    )

            self._emit_edge_evaluation(
                token_id=token_id,
                target_ref=decision_target_ref,
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
                stage=stage,
                time_remaining_sec=time_remaining_sec if isinstance(time_remaining_sec, (int, float)) else None,
                fair_probability=fair if isinstance(fair, (int, float)) else None,
                market_probability=midpoint if isinstance(midpoint, (int, float)) else None,
                edge_value=edge if isinstance(edge, (int, float)) else None,
                oracle_tick_age_sec=oracle_tick_age_sec,
                latency_state=latency_state,
                maker_allowed=maker_allowed,
                taker_allowed=taker_allowed,
                action_taken=action_taken,
                block_reason=block_reason,
                submitted=was_submitted,
                filled=was_filled,
                result=None,
                cycle_index=stable_cycle_index,
                order_id=emitted_order_id,
                book_source=(self._book_source(top) or None),
                market_reference_mode=("direct_midpoint" if isinstance(midpoint, (int, float)) else "missing"),
                market_reference_basis=("direct_book_midpoint" if isinstance(midpoint, (int, float)) else "missing"),
                market_reference_confidence=("authoritative" if isinstance(midpoint, (int, float)) else "none"),
                market_reference_fallback_used=False,
                market_reference_source_side="none",
                market_reference_class=("authoritative" if isinstance(midpoint, (int, float)) else "not_available"),
                taker_submit_reject_reason=taker_submit_reject_reason,
                reduce_only_recovery_active=reduce_only_recovery_active,
                reduce_only_recovery_reason=reduce_only_recovery_reason,
            )

        return {
            "attempts": attempts,
            "submitted": submitted,
            "fills_accepted": fills_accepted_total,
            "submitted_token_ids": sorted(submitted_token_ids),
            "filled_token_ids": sorted(filled_token_ids),
        }

    def _open_order_token_ids(self) -> set[str]:
        out: set[str] = set()
        with contextlib.suppress(Exception):
            for order in self.tx_manager.get_open_orders():
                token_id = str(getattr(order, "token_id", "") or "").strip()
                if token_id:
                    out.add(token_id)
        return out

    def _non_flat_position_token_ids(self) -> set[str]:
        return {
            str(token_id)
            for token_id, pos in self.risk.positions.items()
            if abs(float(getattr(pos, "net_shares", 0.0) or 0.0)) > 1e-9
        }

    def _valuation_watch_token_ids(self) -> List[str]:
        watched = set(str(token_id) for token_id in self.token_ids)
        watched.update(self._non_flat_position_token_ids())
        watched.update(self._open_order_token_ids())
        return self._unique_ordered(sorted(watched))

    def _held_exposure_token_ids(self) -> set[str]:
        held = set(self._non_flat_position_token_ids())
        held.update(self._open_order_token_ids())
        return held

    @staticmethod
    def _financial_posture_class_to_gauge(posture: str) -> float:
        normalized = str(posture or "").strip().upper()
        mapping = {
            FINANCIAL_POSTURE_NORMAL: 0.0,
            FINANCIAL_POSTURE_PREEXPIRY_REDUCE_ONLY: 1.0,
            FINANCIAL_POSTURE_HARD_DEGRADED_REDUCE_ONLY: 2.0,
            FINANCIAL_POSTURE_HALT_NEW_RISK: 3.0,
        }
        return float(mapping.get(normalized, 0.0))

    def _resolve_financial_posture_class(self, *, stage_info_by_token: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        if bool(self.risk.kill_switch):
            return FINANCIAL_POSTURE_HALT_NEW_RISK
        if bool(self._valuation_hard_degraded):
            return FINANCIAL_POSTURE_HARD_DEGRADED_REDUCE_ONLY
        stage_map = stage_info_by_token if isinstance(stage_info_by_token, dict) else {}
        if any(bool((info or {}).get("reduce_only_recovery_active", False)) for info in stage_map.values()):
            return FINANCIAL_POSTURE_PREEXPIRY_REDUCE_ONLY
        return FINANCIAL_POSTURE_NORMAL

    def _held_unpriceable_cause_class(
        self,
        *,
        token_id: str,
        quote: Optional[Dict[str, Any]],
        now_mono: float,
        hard_reason_parts: List[str],
    ) -> str:
        has_held_404_signal = False
        if quote is None:
            last_not_found_mono = self._held_book_not_found_last_mono_by_token.get(token_id)
            if isinstance(last_not_found_mono, (int, float)):
                has_held_404_signal = True
                age_since_not_found_sec = max(0.0, float(now_mono - float(last_not_found_mono)))
                hard_reason_parts.append(f"held_book_not_found_404_age_sec={age_since_not_found_sec:.3f}")
        if not has_held_404_signal:
            return HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        expiry_dt = self.token_expiry_dt_by_token.get(token_id)
        sec_to_expiry = (
            (expiry_dt - utc_now()).total_seconds()
            if isinstance(expiry_dt, dt.datetime)
            else None
        )
        if isinstance(sec_to_expiry, (int, float)):
            boundary_eps = max(0.0, float(self.expiry_boundary_epsilon_sec))
            if float(sec_to_expiry) > (boundary_eps + 1e-9):
                return HELD_UNPRICEABLE_CAUSE_PREEXPIRY_FETCH_FAILURE
            if float(sec_to_expiry) < -(boundary_eps + 1e-9):
                return HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED
            return HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        return HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP

    def _discovery_carry_forward_token_ids(self, discovered_token_ids: List[str]) -> List[str]:
        discovered = set(str(token_id) for token_id in discovered_token_ids if str(token_id or "").strip())
        carry = sorted(token_id for token_id in self._held_exposure_token_ids() if token_id not in discovered)
        return self._unique_ordered(carry)

    def _held_expired_reduce_only_grace_active(
        self,
        *,
        token_id: str,
        sec_to_expiry: Optional[float],
    ) -> bool:
        if not isinstance(sec_to_expiry, (int, float)):
            return False
        if float(sec_to_expiry) >= 0.0:
            return False
        if float(self.held_expired_reduce_only_grace_sec) <= 0.0:
            return False
        if abs(float(sec_to_expiry)) > float(self.held_expired_reduce_only_grace_sec):
            return False
        if token_id in self._open_order_token_ids():
            return True
        pos = self.risk.positions.get(token_id)
        return bool(pos is not None and abs(float(getattr(pos, "net_shares", 0.0) or 0.0)) > 1e-9)

    def _token_lifecycle_obligation_flags(
        self,
        *,
        token_id: str,
        now_mono: Optional[float] = None,
        sec_to_expiry: Optional[float] = None,
        open_order_present: Optional[bool] = None,
    ) -> Dict[str, Any]:
        token = str(token_id or "").strip()
        if not token:
            return {
                "unresolved_lifecycle_obligation": False,
                "open_order_present": False,
                "recent_book_not_found": False,
                "forced_refresh_pending": False,
                "expired_reduce_only_grace_active": False,
                "preexpiry_reduce_only_active": False,
                "held_unpriceable_tracking_active": False,
                "sec_to_expiry": None,
            }
        now_mono_value = float(now_mono) if isinstance(now_mono, (int, float)) else float(time.monotonic())
        open_orders_flag = bool(open_order_present) if isinstance(open_order_present, bool) else bool(
            token in self._open_order_token_ids()
        )
        recent_book_not_found_mono = self._held_book_not_found_last_mono_by_token.get(token)
        recent_book_not_found = bool(
            isinstance(recent_book_not_found_mono, (int, float))
            and (
                now_mono_value - float(recent_book_not_found_mono)
            )
            <= (
                max(
                    float(self.held_book_not_found_force_refresh_min_unpriceable_age_sec),
                    float(self.held_book_not_found_backoff_sec),
                )
                + 1e-9
            )
        )
        forced_refresh_pending_mono = self._held_book_not_found_force_refresh_next_mono_by_token.get(token)
        forced_refresh_pending = bool(
            isinstance(forced_refresh_pending_mono, (int, float))
            and float(forced_refresh_pending_mono) > now_mono_value
        )
        sec = sec_to_expiry
        if not isinstance(sec, (int, float)):
            expiry_dt = self.token_expiry_dt_by_token.get(token)
            sec = (
                (expiry_dt - utc_now()).total_seconds()
                if isinstance(expiry_dt, dt.datetime)
                else None
            )
        expired_reduce_only_grace_active = self._held_expired_reduce_only_grace_active(
            token_id=token,
            sec_to_expiry=sec,
        )
        preexpiry_reduce_only_active = bool(
            isinstance(sec, (int, float))
            and float(sec) >= 0.0
            and float(self.held_preexpiry_reduce_only_sec) > 0.0
            and float(sec) <= (float(self.held_preexpiry_reduce_only_sec) + 1e-9)
        )
        held_unpriceable_tracking_active = bool(token in self._held_unpriceable_since_mono_by_token)
        unresolved_lifecycle_obligation = bool(
            held_unpriceable_tracking_active
            or recent_book_not_found
            or forced_refresh_pending
            or expired_reduce_only_grace_active
            or preexpiry_reduce_only_active
        )
        return {
            "unresolved_lifecycle_obligation": bool(unresolved_lifecycle_obligation),
            "open_order_present": bool(open_orders_flag),
            "recent_book_not_found": bool(recent_book_not_found),
            "forced_refresh_pending": bool(forced_refresh_pending),
            "expired_reduce_only_grace_active": bool(expired_reduce_only_grace_active),
            "preexpiry_reduce_only_active": bool(preexpiry_reduce_only_active),
            "held_unpriceable_tracking_active": bool(held_unpriceable_tracking_active),
            "sec_to_expiry": (float(sec) if isinstance(sec, (int, float)) else None),
        }

    def _reduce_only_recovery_payload(
        self,
        *,
        token_id: str,
        sec_to_expiry: Optional[float],
        expired_reduce_only_grace_active: bool,
    ) -> Dict[str, Any]:
        token = str(token_id or "").strip()
        min_order_size_shares = float(self.risk_min_order_size_shares)
        if not token:
            return {
                "active": False,
                "reason": "",
                "side": "NONE",
                "side_policy": "NONE",
                "size_cap_shares": 0.0,
                "size_cap_below_min_order_size": False,
                "min_order_size_shares": float(min_order_size_shares),
                "net_shares": 0.0,
                "open_order_present": False,
                "preexpiry_active": False,
                "expired_grace_active": bool(expired_reduce_only_grace_active),
                "sec_to_expiry": sec_to_expiry,
                "preexpiry_window_sec": float(self.held_preexpiry_reduce_only_sec),
            }
        pos = self.risk.positions.get(token)
        net_shares = float(getattr(pos, "net_shares", 0.0) or 0.0)
        open_order_present = token in self._open_order_token_ids()
        held_present = open_order_present or (abs(net_shares) > 1e-9)
        sec = float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None
        preexpiry_active = bool(
            held_present
            and isinstance(sec, float)
            and sec >= 0.0
            and float(self.held_preexpiry_reduce_only_sec) > 0.0
            and sec <= (float(self.held_preexpiry_reduce_only_sec) + 1e-9)
        )
        active = bool(expired_reduce_only_grace_active or preexpiry_active)
        side = "NONE"
        side_policy = "NONE"
        size_cap_shares = 0.0
        if net_shares > 1e-9:
            side = "SELL"
            side_policy = "SELL_ONLY"
            size_cap_shares = abs(float(net_shares))
        elif net_shares < -1e-9:
            side = "BUY"
            side_policy = "BUY_ONLY"
            size_cap_shares = abs(float(net_shares))
        size_cap_below_min_order_size = bool(
            active
            and float(size_cap_shares) > 1e-9
            and float(size_cap_shares) + 1e-9 < float(min_order_size_shares)
        )
        if not active:
            return {
                "active": False,
                "reason": "",
                "side": side,
                "side_policy": side_policy,
                "size_cap_shares": float(size_cap_shares),
                "size_cap_below_min_order_size": False,
                "min_order_size_shares": float(min_order_size_shares),
                "net_shares": float(net_shares),
                "open_order_present": bool(open_order_present),
                "preexpiry_active": False,
                "expired_grace_active": bool(expired_reduce_only_grace_active),
                "sec_to_expiry": sec_to_expiry,
                "preexpiry_window_sec": float(self.held_preexpiry_reduce_only_sec),
            }
        reason = (
            "expired_reduce_only_grace_active"
            if bool(expired_reduce_only_grace_active)
            else "preexpiry_reduce_only_window_active"
        )
        return {
            "active": True,
            "reason": str(reason),
            "side": side,
            "side_policy": side_policy,
            "size_cap_shares": float(size_cap_shares),
            "size_cap_below_min_order_size": bool(size_cap_below_min_order_size),
            "min_order_size_shares": float(min_order_size_shares),
            "net_shares": float(net_shares),
            "open_order_present": bool(open_order_present),
            "preexpiry_active": bool(preexpiry_active),
            "expired_grace_active": bool(expired_reduce_only_grace_active),
            "sec_to_expiry": sec_to_expiry,
            "preexpiry_window_sec": float(self.held_preexpiry_reduce_only_sec),
        }

    def _book_not_found_backoff_sec_for_token(
        self,
        *,
        token_id: str,
        held_exposure_tokens: Optional[set[str]] = None,
    ) -> float:
        token = str(token_id or "").strip()
        if not token:
            return float(self.book_not_found_backoff_sec)
        held_tokens = held_exposure_tokens if held_exposure_tokens is not None else self._held_exposure_token_ids()
        if token in held_tokens:
            return float(self.held_book_not_found_backoff_sec)
        return float(self.book_not_found_backoff_sec)

    def _watch_removal_conditions_met(self, token_id: str) -> bool:
        token = str(token_id or "").strip()
        if not token:
            return True
        pos = self.risk.positions.get(token)
        position_flat = bool(pos is None or abs(float(getattr(pos, "net_shares", 0.0) or 0.0)) <= 1e-9)
        lifecycle_flags = self._token_lifecycle_obligation_flags(token_id=token)
        has_open_order = bool(lifecycle_flags.get("open_order_present", False))
        unresolved_lifecycle_obligation = bool(lifecycle_flags.get("unresolved_lifecycle_obligation", False))
        return bool(position_flat and (not has_open_order) and (not unresolved_lifecycle_obligation))

    def _handle_missing_book_not_found_tokens(
        self,
        *,
        missing_book_not_found_tokens: List[str],
        held_exposure_tokens: Optional[set[str]] = None,
    ) -> Dict[str, List[str]]:
        if not missing_book_not_found_tokens or not self.discovery.enabled:
            return {"forced_refresh_tokens": [], "suppressed_held_tokens": []}
        unique_not_found = sorted(
            set(str(token_id) for token_id in missing_book_not_found_tokens if str(token_id or "").strip())
        )
        if not unique_not_found:
            return {"forced_refresh_tokens": [], "suppressed_held_tokens": []}
        held_tokens = held_exposure_tokens if held_exposure_tokens is not None else self._held_exposure_token_ids()
        forced_refresh_tokens = [token_id for token_id in unique_not_found if token_id not in held_tokens]
        held_not_found_tokens = [token_id for token_id in unique_not_found if token_id in held_tokens]
        forced_held_recovery_tokens: List[str] = []
        suppressed_held_tokens: List[str] = []
        now_mono = time.monotonic()
        for token_id in held_not_found_tokens:
            should_force_recovery = False
            if self.held_book_not_found_force_refresh_interval_sec > 0.0:
                unpriceable_since = self._held_unpriceable_since_mono_by_token.get(token_id)
                unpriceable_age_sec = (
                    max(0.0, float(now_mono - float(unpriceable_since)))
                    if isinstance(unpriceable_since, (int, float))
                    else 0.0
                )
                next_allowed_mono = self._held_book_not_found_force_refresh_next_mono_by_token.get(token_id, 0.0)
                if (
                    unpriceable_age_sec + 1e-9
                    >= float(self.held_book_not_found_force_refresh_min_unpriceable_age_sec)
                    and now_mono >= float(next_allowed_mono)
                ):
                    should_force_recovery = True
                    self._held_book_not_found_force_refresh_next_mono_by_token[token_id] = (
                        now_mono + float(self.held_book_not_found_force_refresh_interval_sec)
                    )
            if should_force_recovery:
                forced_held_recovery_tokens.append(token_id)
            else:
                suppressed_held_tokens.append(token_id)
        if forced_held_recovery_tokens:
            forced_refresh_tokens.extend(forced_held_recovery_tokens)
            forced_refresh_tokens = sorted(set(str(token_id) for token_id in forced_refresh_tokens if str(token_id)))
            self.telemetry.incr("target_refresh_forced_held_book_not_found_recovery")
            self.events.log_event(
                "target_refresh_forced_held_book_not_found_recovery",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_count": len(forced_held_recovery_tokens),
                    "token_ids": list(forced_held_recovery_tokens),
                    "reason": "persistent_held_exposure_book_not_found_recovery_refresh",
                    "min_unpriceable_age_sec": float(self.held_book_not_found_force_refresh_min_unpriceable_age_sec),
                    "refresh_interval_sec": float(self.held_book_not_found_force_refresh_interval_sec),
                },
            )
        if forced_refresh_tokens:
            self.telemetry.incr("target_refresh_forced_book_not_found")
            self.events.log_event(
                "target_refresh_forced_book_not_found",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_count": len(forced_refresh_tokens),
                    "token_ids": list(forced_refresh_tokens),
                    "forced_held_recovery_token_count": len(forced_held_recovery_tokens),
                    "forced_held_recovery_token_ids": list(forced_held_recovery_tokens),
                    "suppressed_held_token_count": len(suppressed_held_tokens),
                    "suppressed_held_token_ids": list(suppressed_held_tokens),
                },
            )
            self._refresh_targets(force=True)
        elif suppressed_held_tokens:
            self.telemetry.incr("target_refresh_suppressed_held_book_not_found")
            self.events.log_event(
                "target_refresh_suppressed_held_book_not_found",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_count": len(suppressed_held_tokens),
                    "token_ids": list(suppressed_held_tokens),
                    "reason": "held_exposure_book_not_found_no_discovery_refresh",
                },
            )
        return {
            "forced_refresh_tokens": list(forced_refresh_tokens),
            "suppressed_held_tokens": list(suppressed_held_tokens),
        }

    def _sync_book_feed_watch_tokens(self) -> None:
        self.book_feed.update_token_ids(self._valuation_watch_token_ids())

    def _refresh_targets(self, *, force: bool = False) -> None:
        if not self.discovery.enabled:
            return
        now = time.monotonic()
        if not force and now < self.next_target_refresh_monotonic:
            return
        self.next_target_refresh_monotonic = now + self.discovery.refresh_interval_sec

        try:
            result = self.discovery.discover()
        except Exception as exc:
            self.telemetry.incr("target_discovery_errors")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "target_discovery",
                    "error": str(exc),
                }
            )
            return

        discovered_ids = self._unique_ordered([str(x) for x in result.token_ids])
        carry_forward_ids = self._discovery_carry_forward_token_ids(discovered_ids)
        effective_discovered_ids = self._unique_ordered(discovered_ids + carry_forward_ids)
        self.telemetry.set_gauge("target_discovery_allowlist_enabled", 1.0 if result.allowlist_enabled else 0.0)
        self.telemetry.set_gauge("target_discovery_allowlist_rejected_pairs", float(result.allowlist_rejected_pairs))
        self.telemetry.set_gauge("target_discovery_contract_rejected_pairs", float(result.contract_rejected_pairs))
        if result.allowlist_enabled and result.allowlist_rejected_pairs > 0:
            self.telemetry.incr("target_discovery_allowlist_rejected_pairs", int(result.allowlist_rejected_pairs))
            if result.allowlist_rejected_pairs != self._last_discovery_allowlist_rejected_pairs:
                self.events.log_event(
                    "target_discovery_allowlist_drift",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "allowlist_rejected_pairs": int(result.allowlist_rejected_pairs),
                        "contract_rejected_pairs": int(result.contract_rejected_pairs),
                        "pairs_selected": int(result.pairs_selected),
                        "scanned_markets": int(result.scanned_markets),
                        "fee_eligible_markets": int(result.fee_eligible_markets),
                    },
                )
        self._last_discovery_allowlist_rejected_pairs = int(result.allowlist_rejected_pairs)
        discovered_expiry_map = {
            str(token_id): str(expiry_utc)
            for token_id, expiry_utc in result.token_expiry_utc_by_token.items()
            if str(token_id) and str(expiry_utc)
        }
        discovered_side_map = {
            str(token_id): str(side)
            for token_id, side in result.token_side_by_token.items()
            if str(token_id) and str(side)
        }
        discovered_strike_map = {
            str(token_id): float(strike)
            for token_id, strike in result.token_strike_by_token.items()
            if str(token_id)
        }
        discovered_market_key_map = {
            str(token_id): str(market_key)
            for token_id, market_key in result.token_market_key_by_token.items()
            if str(token_id) and str(market_key)
        }
        self.telemetry.set_gauge("target_discovery_active_targets", float(len(discovered_ids)))
        self.telemetry.set_gauge("target_discovery_standdown", 1.0 if not effective_discovered_ids else 0.0)
        if not effective_discovered_ids:
            self.telemetry.incr("target_discovery_empty")
            old = list(self.token_ids)
            if old:
                old_set = set(old)
                self.token_ids = []
                self._sync_book_feed_watch_tokens()
                self.token_market_key_by_token = {}
                self.token_expiry_utc_by_token = {}
                self.token_expiry_dt_by_token = {}
                self.token_side_by_token = {}
                self.token_strike_by_token = {}
                self._prune_removed_tokens(old_set=old_set, active_set=set())
                self.events.log_event(
                    "targets_standdown",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "old_token_count": len(old),
                        "old_token_ids": old,
                        "new_token_count": 0,
                        "new_token_ids": [],
                        "reason": "no_valid_targets_discovered",
                        "pairs_selected": int(result.pairs_selected),
                        "scanned_markets": int(result.scanned_markets),
                        "fee_eligible_markets": int(result.fee_eligible_markets),
                        "contract_rejected_pairs": int(result.contract_rejected_pairs),
                    },
                )
            elif self._last_discovery_target_count != 0:
                self.events.log_event(
                    "targets_standdown",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "old_token_count": 0,
                        "new_token_count": 0,
                        "reason": "no_valid_targets_discovered",
                        "pairs_selected": int(result.pairs_selected),
                        "scanned_markets": int(result.scanned_markets),
                        "fee_eligible_markets": int(result.fee_eligible_markets),
                        "contract_rejected_pairs": int(result.contract_rejected_pairs),
                    },
                )
            self._last_discovery_target_count = 0
            return

        if effective_discovered_ids != self.token_ids:
            old = list(self.token_ids)
            self.token_ids = effective_discovered_ids
            self._reset_ws_slo_bootstrap(reason="targets_updated")
            old_set = set(old)
            active_set = set(self.token_ids)
            self._apply_token_expiry_map(discovered_expiry_map, source="discovery")
            self._apply_token_side_map(discovered_side_map, source="discovery")
            self._apply_token_strike_map(discovered_strike_map, source="discovery")
            old_market_key_map = dict(self.token_market_key_by_token)
            self.token_market_key_by_token = {
                token_id: discovered_market_key_map.get(token_id, old_market_key_map.get(token_id, ""))
                for token_id in active_set
            }
            self.token_expiry_utc_by_token = {
                token_id: expiry_utc
                for token_id, expiry_utc in self.token_expiry_utc_by_token.items()
                if token_id in active_set
            }
            self.token_expiry_dt_by_token = {
                token_id: expiry_dt
                for token_id, expiry_dt in self.token_expiry_dt_by_token.items()
                if token_id in active_set
            }
            self.token_side_by_token = {
                token_id: side
                for token_id, side in self.token_side_by_token.items()
                if token_id in active_set
            }
            self.token_strike_by_token = {
                token_id: strike
                for token_id, strike in self.token_strike_by_token.items()
                if token_id in active_set
            }
            self.events.log_event(
                "targets_updated",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "old_token_count": len(old),
                    "new_token_count": len(self.token_ids),
                    "old_token_ids": old,
                    "new_token_ids": self.token_ids,
                    "pairs_selected": result.pairs_selected,
                    "scanned_markets": result.scanned_markets,
                    "fee_eligible_markets": result.fee_eligible_markets,
                    "contract_rejected_pairs": int(result.contract_rejected_pairs),
                    "allowlist_enabled": bool(result.allowlist_enabled),
                    "allowlist_rejected_pairs": int(result.allowlist_rejected_pairs),
                    "expiry_map_count": len(self.token_expiry_utc_by_token),
                    "side_map_count": len(self.token_side_by_token),
                    "strike_map_count": len(self.token_strike_by_token),
                    "market_key_map_count": len(self.token_market_key_by_token),
                    "discovered_token_count": len(discovered_ids),
                    "discovered_token_ids": list(discovered_ids),
                    "carry_forward_token_count": len(carry_forward_ids),
                    "carry_forward_token_ids": list(carry_forward_ids),
                },
            )
            self._sync_book_feed_watch_tokens()
            self._prune_removed_tokens(old_set=old_set, active_set=active_set)
            for token_id in self.token_ids:
                self.risk.positions.setdefault(token_id, Position(token_id=token_id))
                old_key = str(old_market_key_map.get(token_id, ""))
                new_key = str(self.token_market_key_by_token.get(token_id, ""))
                if new_key and old_key != new_key:
                    self._on_market_key_transition(token_id, old_key, new_key)
        else:
            if self._last_discovery_target_count != len(self.token_ids):
                self.events.log_event(
                    "targets_refreshed",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "token_count": len(self.token_ids),
                        "pairs_selected": result.pairs_selected,
                        "scanned_markets": result.scanned_markets,
                        "fee_eligible_markets": result.fee_eligible_markets,
                        "contract_rejected_pairs": int(result.contract_rejected_pairs),
                        "allowlist_enabled": bool(result.allowlist_enabled),
                        "allowlist_rejected_pairs": int(result.allowlist_rejected_pairs),
                        "expiry_map_count": len(discovered_expiry_map),
                        "side_map_count": len(discovered_side_map),
                        "strike_map_count": len(discovered_strike_map),
                        "market_key_map_count": len(discovered_market_key_map),
                    },
                )
            if discovered_expiry_map:
                self._apply_token_expiry_map(discovered_expiry_map, source="discovery_refresh")
            if discovered_side_map:
                self._apply_token_side_map(discovered_side_map, source="discovery_refresh")
            if discovered_strike_map:
                self._apply_token_strike_map(discovered_strike_map, source="discovery_refresh")
            if discovered_market_key_map:
                for token_id in self.token_ids:
                    old_key = str(self.token_market_key_by_token.get(token_id, ""))
                    new_key = str(discovered_market_key_map.get(token_id, old_key))
                    self.token_market_key_by_token[token_id] = new_key
                    if new_key and old_key != new_key:
                        self._on_market_key_transition(token_id, old_key, new_key)
        self._last_discovery_target_count = len(self.token_ids)

    @staticmethod
    def _restore_positions(raw: Any) -> Dict[str, Position]:
        positions: Dict[str, Position] = {}
        if not isinstance(raw, dict):
            return positions
        for token_id, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            net_shares = parse_float(payload.get("net_shares")) or 0.0
            buy_shares = parse_float(payload.get("buy_shares")) or 0.0
            sell_shares = parse_float(payload.get("sell_shares")) or 0.0
            bought_notional = parse_float(payload.get("bought_notional")) or 0.0
            sold_notional = parse_float(payload.get("sold_notional")) or 0.0
            positions[str(token_id)] = Position(
                token_id=str(token_id),
                net_shares=net_shares,
                buy_shares=buy_shares,
                sell_shares=sell_shares,
                bought_notional=bought_notional,
                sold_notional=sold_notional,
            )
        return positions

    @staticmethod
    def _position_is_effectively_empty(pos: Position, eps: float = 1e-9) -> bool:
        return (
            abs(pos.net_shares) <= eps
            and abs(pos.buy_shares) <= eps
            and abs(pos.sell_shares) <= eps
            and abs(pos.bought_notional) <= eps
            and abs(pos.sold_notional) <= eps
        )

    def _prune_removed_tokens(self, *, old_set: set[str], active_set: set[str]) -> None:
        self.vol_tracker.prune_tokens(active_set)
        self.latency_verifier.prune_tokens(active_set)
        for token_id in old_set - active_set:
            remove_watch_state = self._watch_removal_conditions_met(token_id)
            if remove_watch_state:
                self.last_midpoint_by_token.pop(token_id, None)
                self.last_midpoint_ts_mono_by_token.pop(token_id, None)
                self.last_volatility_by_token.pop(token_id, None)
                self._book_not_found_backoff_mono_by_token.pop(token_id, None)
                self._held_book_not_found_last_mono_by_token.pop(token_id, None)
                self._held_book_not_found_force_refresh_next_mono_by_token.pop(token_id, None)
            self.token_market_key_by_token.pop(token_id, None)
            self._market_entry_mono_by_token.pop(token_id, None)
            self._market_entry_cycle_by_token.pop(token_id, None)
            self._last_stage_by_token.pop(token_id, None)
            self._last_taker_submit_mono_by_token.pop(token_id, None)
            self._last_doctrine_signature_by_token.pop(token_id, None)
            self._last_doctrine_prereq_failure_by_token.pop(token_id, None)
            pos = self.risk.positions.get(token_id)
            if pos is not None and self._position_is_effectively_empty(pos):
                self.risk.positions.pop(token_id, None)

    def _dump_state(self) -> None:
        positions_payload: Dict[str, Dict[str, float]] = {}
        for token_id, pos in self.risk.positions.items():
            positions_payload[token_id] = {
                "net_shares": pos.net_shares,
                "buy_shares": pos.buy_shares,
                "sell_shares": pos.sell_shares,
                "bought_notional": pos.bought_notional,
                "sold_notional": pos.sold_notional,
            }
        state = {
            "positions": positions_payload,
            "seen_trade_ids": self.manager.snapshot_seen_trade_ids(limit=self.persist_seen_trade_ids_max),
            "last_fill_ts_utc": self.manager.snapshot_last_fill_ts(),
            "last_status_ts_utc": utc_iso(),
        }
        save_state(self.state_path, state)

    def _compute_code_fingerprint(self) -> Tuple[str, int]:
        root = pathlib.Path(__file__).resolve().parent
        candidates = [root / "executor.py"]
        candidates.extend(sorted((root / "prodesk").rglob("*.py")))
        digest = hashlib.sha256()
        count = 0
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            content = path.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_hash.encode("ascii"))
            digest.update(b"\n")
            count += 1
        return digest.hexdigest(), count

    def _validate_run_manifest_payload(self, payload: Dict[str, Any], *, context: str) -> None:
        missing = [field for field in RUN_MANIFEST_REQUIRED_FIELDS if not str(payload.get(field) or "").strip()]
        if missing:
            raise ValueError(f"run_manifest_missing_required_fields:{context}:{','.join(missing)}")
        if int(payload.get("manifest_schema_version") or 0) < 2:
            raise ValueError(
                f"run_manifest_schema_version_invalid:{context}:{payload.get('manifest_schema_version')!r}"
            )

    def _write_run_manifest(self) -> None:
        cfg_copy = json.loads(json.dumps(self.cfg, ensure_ascii=True, default=str))
        serialized = json.dumps(cfg_copy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        cfg_hash_computed = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        cfg_hash = str(self.config_fingerprint_sha256 or cfg_hash_computed)
        code_hash, code_file_count = self._compute_code_fingerprint()
        config_source_sha256 = ""
        config_source_path = ""
        if self.config_source_path is not None:
            config_source_path = str(self.config_source_path)
            try:
                config_source_sha256 = hashlib.sha256(self.config_source_path.read_bytes()).hexdigest()
            except Exception:
                config_source_sha256 = ""
        env_hints = {
            "BRO_CONFIG_PATH": os.environ.get("BRO_CONFIG_PATH", ""),
            "BRO_MODE": os.environ.get("BRO_MODE", ""),
            "BRO_ASSET": os.environ.get("BRO_ASSET", ""),
            "BRO_DOCKER_MODE": os.environ.get("BRO_DOCKER_MODE", ""),
            "BRO_PROFILE": os.environ.get("BRO_PROFILE", ""),
            "BRO_DOCKER_IMAGE_HASH": os.environ.get("BRO_DOCKER_IMAGE_HASH", ""),
        }
        dependency_lock_path = self.repo_root / "ops" / "dependency_lock.json"
        dependency_lock_sha256 = ""
        if dependency_lock_path.exists():
            try:
                dependency_lock_sha256 = hashlib.sha256(dependency_lock_path.read_bytes()).hexdigest()
            except Exception:
                dependency_lock_sha256 = ""
        runtime_identity = {
            "profile_name": self.profile_name,
            "effective_config_sha256": cfg_hash,
            "git_commit": current_git_commit(self.repo_root),
            "git_dirty": current_git_dirty(self.repo_root),
            "dependency_lock_sha256": dependency_lock_sha256,
            "docker_image_hash": str(os.environ.get("BRO_DOCKER_IMAGE_HASH", "")).strip(),
            "repo_root": str(self.repo_root),
            "runtime_key_values": {
                "mode": str(self.cfg.get("mode", "")),
                "target_usd": float(self.cfg.get("sizing", {}).get("target_usd", 0.0)),
                "sniper_arming_horizon_sec": float(self.cfg.get("sniper", {}).get("arming_horizon_sec", 0.0)),
                "sniper_execution_cutoff_sec": float(self.cfg.get("sniper", {}).get("execution_cutoff_sec", 0.0)),
                "sniper_late_fire_priority_band_sec": float(
                    self.cfg.get("sniper", {}).get("late_fire_priority_band_sec", 0.0)
                ),
                "risk_max_abs_position_shares": float(self.cfg.get("risk", {}).get("max_abs_position_shares", 0.0)),
                "risk_max_notional_per_token": float(self.cfg.get("risk", {}).get("max_notional_per_token", 0.0)),
            },
        }
        status_path = str((self.log_dir / f"status_{dt.datetime.now(dt.timezone.utc).date().isoformat()}.jsonl").resolve())
        events_path = str((self.log_dir / f"events_{dt.datetime.now(dt.timezone.utc).date().isoformat()}.jsonl").resolve())
        start_ts = utc_iso()
        payload = {
            "manifest_schema_version": 2,
            "ts_utc": start_ts,
            "start_ts": start_ts,
            "end_ts": "",
            "run_id": self.run_id,
            "bot_name": self.bot_name,
            "mode": self.cfg["mode"],
            "profile_name": self.profile_name,
            "git_commit": current_git_commit(self.repo_root),
            "status_path": status_path,
            "events_path": events_path,
            "python_version": sys.version,
            "config_fingerprint_sha256": cfg_hash,
            "config_source_path": config_source_path,
            "config_source_sha256": config_source_sha256,
            "code_fingerprint_sha256": code_hash,
            "code_fingerprint_file_count": code_file_count,
            "runtime_env_hints": env_hints,
            "runtime_identity": runtime_identity,
            "config": cfg_copy,
        }
        self._validate_run_manifest_payload(payload, context="start")
        self.run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _write_run_manifest_end(self) -> None:
        if not self.run_manifest_path.exists():
            return
        try:
            payload = json.loads(self.run_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        payload["end_ts"] = utc_iso()
        payload["status_path"] = str((self.log_dir / f"status_{dt.datetime.now(dt.timezone.utc).date().isoformat()}.jsonl").resolve())
        payload["events_path"] = str((self.log_dir / f"events_{dt.datetime.now(dt.timezone.utc).date().isoformat()}.jsonl").resolve())
        self._validate_run_manifest_payload(payload, context="end")
        self.run_manifest_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _read_external_guard_stop(self) -> Tuple[bool, str]:
        if self.guard_stop_file is None:
            return False, ""
        try:
            if not self.guard_stop_file.exists():
                return False, ""
            reason = "triggered"
            with self.guard_stop_file.open("r", encoding="utf-8", errors="ignore") as fh:
                for _ in range(16):
                    line = fh.readline()
                    if line == "":
                        break
                    text = line.strip()
                    if text:
                        reason = text
                        break
            return True, reason[:240]
        except Exception as exc:
            self.telemetry.incr("external_guard_errors")
            now_mono = time.monotonic()
            if (now_mono - self._external_guard_last_error_log_mono) >= self._external_guard_error_log_interval_sec:
                self._external_guard_last_error_log_mono = now_mono
                self.events.log_error(
                    {
                        "ts_utc": utc_iso(),
                        "component": "external_guard",
                        "action": "read_guard_file",
                        "path": str(self.guard_stop_file),
                        "error": str(exc),
                    }
                )
            return False, ""

    def _clear_external_guard_stop_on_start(self) -> None:
        if self.guard_stop_file is None or not self.clear_guard_stop_on_start:
            return
        try:
            if not self.guard_stop_file.exists():
                return
            self.guard_stop_file.unlink()
            self.events.log_event(
                "external_guard_stop_cleared_on_start",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "path": str(self.guard_stop_file),
                },
            )
        except Exception as exc:
            self.telemetry.incr("external_guard_errors")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "external_guard",
                    "action": "clear_guard_file_on_start",
                    "path": str(self.guard_stop_file),
                    "error": str(exc),
                }
            )

    def _apply_external_guard_stop(self) -> None:
        active, reason = self._read_external_guard_stop()
        self.telemetry.set_gauge("external_guard_stop_active", 1.0 if active else 0.0)
        if active != self._last_external_guard_active or reason != self._last_external_guard_reason:
            self.events.log_event(
                "external_guard_stop_state",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "active": active,
                    "path": str(self.guard_stop_file) if self.guard_stop_file is not None else "",
                    "reason": reason if active else "",
                },
            )
        self._last_external_guard_active = active
        self._last_external_guard_reason = reason
        if active and not self.risk.kill_switch:
            detail = reason or "triggered"
            self.risk.set_kill_switch(f"external_guard_stop:{detail}")

    def request_stop(self) -> None:
        self.stop_requested = True

    def _register_signals(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_args: self.request_stop())
            except Exception:
                pass

    def run(self, duration_min: Optional[float]) -> None:
        self._register_signals()
        runtime = self.cfg["runtime"]
        poll_interval = float(runtime["poll_interval_sec"])
        status_interval = float(runtime["status_interval_sec"])
        reconcile_interval = float(runtime["reconcile_interval_sec"])
        cancel_all_on_exit = bool(runtime.get("cancel_all_on_exit", True))
        max_consecutive_failures = int(runtime.get("max_consecutive_failures", 12))
        base_order_soft_limit = float(self.manager.order_rate_soft_limit_pct)
        base_cancel_soft_limit = float(self.manager.cancel_rate_soft_limit_pct)

        started = time.monotonic()
        stop_after_sec = duration_min * 60.0 if duration_min and duration_min > 0 else None
        next_status = time.monotonic()
        next_state_flush = time.monotonic()
        status_window_actions = 0
        status_window_fills = 0
        status_window_taker_actions = 0
        status_window_taker_submitted = 0
        status_window_taker_fills = 0
        status_window_order_submit_attempts = 0

        self.events.log_event(
            "runner_start",
            {
                "bot_name": self.bot_name,
                "ts_utc": utc_iso(),
                "mode": self.cfg["mode"],
                "token_count": len(self.token_ids),
                "token_ids": self.token_ids,
                "log_dir": str(self.log_dir),
                "state_path": str(self.state_path),
                "run_manifest_path": str(self.run_manifest_path),
                "guard_stop_file": str(self.guard_stop_file) if self.guard_stop_file is not None else "",
                "clear_guard_stop_on_start": self.clear_guard_stop_on_start,
                "run_id": self.run_id,
            },
        )
        self._emit_sniper_stage_window_semantic_check()
        cfg_meta = self.cfg.get("_meta", {}) if isinstance(self.cfg.get("_meta"), dict) else {}
        LOG.info(
            "Active profile: %s | Config fingerprint: %s | Config sources: %s",
            self.profile_name,
            (self.config_fingerprint_sha256 or "unknown"),
            cfg_meta.get("config_source_paths", []),
        )
        try:
            try:
                self._write_run_manifest()
            except Exception as exc:
                self.telemetry.incr("run_manifest_write_errors")
                self.events.log_error(
                    {
                        "ts_utc": utc_iso(),
                        "component": "runner_start",
                        "action": "write_run_manifest",
                        "path": str(self.run_manifest_path),
                        "error": str(exc),
                    }
                )
                raise
            self._clear_external_guard_stop_on_start()
            self._apply_external_guard_stop()
            self._refresh_targets(force=True)
            self.chainlink.start()
            self.pyth.start()
            self.book_feed.start(self._valuation_watch_token_ids())
            self.prometheus.start()
            while not self.stop_requested:
                if stop_after_sec is not None and (time.monotonic() - started) >= stop_after_sec:
                    LOG.info("Duration reached; stopping execution runner")
                    break

                self._doctrine_cycle_index += 1
                cycle_started = time.monotonic()
                phase_market_data_ms = 0.0
                phase_strategy_exec_ms = 0.0
                phase_state_io_ms = 0.0
                phase_status_io_ms = 0.0
                effective_poll_interval = poll_interval
                max_actions_override: Optional[int] = None
                stale_action_budget = 2
                orphan_action_budget: Optional[int] = None
                self._refresh_targets(force=False)
                self._sync_book_feed_watch_tokens()
                self.telemetry.set_gauge("target_count", float(len(self.token_ids)))
                has_targets = bool(self.token_ids)
                self._update_runtime_semantics(has_targets=has_targets)
                mode_state = self.operating_mode.state
                self.telemetry.set_gauge("operating_mode_state", self._operating_mode_to_gauge(mode_state))
                risk_rejects_start = int(self.telemetry.counters.get("risk_rejects", 0))
                stale_rejects_start = int(self.telemetry.counters.get("risk_reject_stale_book", 0))
                kill_switch_rejects_start = int(self.telemetry.counters.get("risk_reject_kill_switch", 0))
                order_submission_transport_attempted_start = int(
                    self.telemetry.counters.get("order_submission_transport_attempted", 0)
                )
                cycle_had_error = False
                self._apply_external_guard_stop()
                self.manager.sizing_target_usd = float(self._active_target_usd)
                if self.sizing_mode == "notional":
                    self.sniper_taker_target_usd = float(self._active_target_usd)
                self.telemetry.set_gauge("active_target_usd", float(self._active_target_usd))
                self.telemetry.set_gauge("sniper_ramp_allowed", 1.0 if self._sniper_ramp_allowed else 0.0)
                phase_market_started = time.monotonic()

                if mode_state == MODE_SAFE_STOP and not self.risk.kill_switch:
                    self.risk.set_kill_switch("operating_mode_safe_stop")

                stale_canceled = self.manager.cancel_stale_orders(action_budget=stale_action_budget)
                if stale_canceled:
                    self.telemetry.incr("stale_quote_cancels", stale_canceled)

                chainlink_ticks = self.chainlink.pop_ticks()
                if chainlink_ticks:
                    self.telemetry.incr("chainlink_ticks", len(chainlink_ticks))
                    for tick in chainlink_ticks:
                        metric_sym = self._metric_symbol(tick.symbol)
                        self.telemetry.set_gauge(f"chainlink_price.{metric_sym}", tick.price)
                        if self.chainlink.log_ticks:
                            self.events.log_event(
                                "chainlink_tick",
                                {
                                    "ts_utc": tick.received_ts_utc,
                                    "ts_receive_utc": tick.received_ts_utc,
                                    "run_id": self.run_id,
                                    "symbol": tick.symbol,
                                    "price": tick.price,
                                    "received_ts_utc": tick.received_ts_utc,
                                    "source_ts_utc": tick.source_ts_utc,
                                    "ts_source_utc": tick.source_ts_utc,
                                    "topic": tick.topic,
                                    "msg_type": tick.msg_type,
                                },
                            )
                chainlink_status_live = self.chainlink.status()
                self.pyth.refresh()
                pyth_status_live = self.pyth.status()
                if pyth_status_live.get("enabled", False):
                    self.telemetry.set_gauge(
                        "secondary_oracle_pyth_connected",
                        1.0 if bool(pyth_status_live.get("connected", False)) else 0.0,
                    )
                    pyth_age = pyth_status_live.get("last_tick_age_sec")
                    if isinstance(pyth_age, (int, float)):
                        self.telemetry.set_gauge("secondary_oracle_pyth_last_tick_age_sec", float(pyth_age))
                stale_oracle_cycle = False
                if chainlink_status_live.get("enabled", False):
                    if chainlink_status_live.get("connected", False):
                        self.telemetry.set_gauge("chainlink_connected", 1.0)
                    else:
                        self.telemetry.set_gauge("chainlink_connected", 0.0)
                        stale_oracle_cycle = True
                    age = chainlink_status_live.get("last_tick_age_sec")
                    if isinstance(age, (int, float)):
                        self.telemetry.set_gauge("chainlink_last_tick_age_sec", float(age))
                        if float(age) > self.doctrine_oracle_max_tick_age_sec:
                            stale_oracle_cycle = True

                ws_books = self.book_feed.snapshot_books()
                book_feed_status_live = self.book_feed.status()
                if book_feed_status_live.get("enabled", False):
                    self.telemetry.set_gauge(
                        "book_feed_connected",
                        1.0 if bool(book_feed_status_live.get("connected", False)) else 0.0,
                    )
                    ws_age = book_feed_status_live.get("last_msg_age_sec")
                    if isinstance(ws_age, (int, float)):
                        self.telemetry.set_gauge("book_feed_last_msg_age_sec", float(ws_age))

                books: Dict[str, Any] = {}
                valuation_books: Dict[str, Any] = {}
                volatility_by_token: Dict[str, float] = {}
                lag_samples_accepted_cycle = 0
                ws_updates_cycle = 0
                rest_updates_cycle = 0
                ws_updates_target_cycle = 0
                rest_updates_target_cycle = 0
                all_targets_missing_ws_books = False
                rest_fallback_used_cycle = False
                target_token_ids = self._unique_ordered([str(token_id) for token_id in self.token_ids if str(token_id).strip()])
                target_token_set = set(target_token_ids)
                valuation_token_ids = self._valuation_watch_token_ids()
                held_exposure_tokens = self._held_exposure_token_ids()
                if not has_targets:
                    self.telemetry.incr("no_target_cycles")
                now_mono = time.monotonic()
                self._preexpiry_404_anomaly_last_cycle = False
                missing_rest_tokens, held_ws_unusable_token_ids = self._rest_fetch_candidate_tokens_for_cycle(
                    valuation_token_ids=valuation_token_ids,
                    ws_books=ws_books,
                    now_mono=now_mono,
                )
                requested_rest_token_ids = set(missing_rest_tokens)
                rest_books, rest_errors = self._fetch_missing_books(missing_rest_tokens)
                missing_book_not_found_tokens: list[str] = []
                for token_id in valuation_token_ids:
                        raw = None
                        top = ws_books.get(token_id)
                        force_rest_for_held_unusable_quote = bool(
                            top is not None and token_id in held_ws_unusable_token_ids
                        )
                        if top is None or force_rest_for_held_unusable_quote:
                            backoff_until = self._book_not_found_backoff_mono_by_token.get(token_id, 0.0)
                            if backoff_until > time.monotonic():
                                if top is not None and force_rest_for_held_unusable_quote:
                                    force_rest_for_held_unusable_quote = False
                                else:
                                    continue
                            if force_rest_for_held_unusable_quote:
                                self.telemetry.incr("held_valuation_rest_fallback_attempted")
                                self.events.log_event(
                                    "held_valuation_rest_fallback_attempted",
                                    {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "token_id": token_id,
                                        "reason": "ws_quote_missing_mid_and_required_conservative_side",
                                    },
                                )
                            fetched, err_text, fetch_attempted = self._rest_fetch_result_for_token(
                                token_id=token_id,
                                requested_rest_token_ids=requested_rest_token_ids,
                                rest_books=rest_books,
                                rest_errors=rest_errors,
                            )
                            if not fetch_attempted:
                                # Backoff can expire after we snapshot rest-fetch candidates for the cycle.
                                # Defer this token to the next cycle instead of inventing a synthetic fetch error.
                                if top is None:
                                    continue
                            if fetched is None:
                                if not err_text:
                                    if top is None:
                                        continue
                                elif self.discovery.enabled and self._is_missing_book_not_found_error(err_text):
                                    backoff_sec = self._book_not_found_backoff_sec_for_token(
                                        token_id=token_id,
                                        held_exposure_tokens=held_exposure_tokens,
                                    )
                                    now_mono_error = time.monotonic()
                                    self._book_not_found_backoff_mono_by_token[token_id] = (
                                        now_mono_error + float(backoff_sec)
                                    )
                                    self._held_book_not_found_last_mono_by_token[token_id] = float(now_mono_error)
                                    missing_book_not_found_tokens.append(token_id)
                                    stage_info = self._token_stage_info(token_id)
                                    stage_name = str(stage_info.get("stage") or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN
                                    sec_to_expiry_val = stage_info.get("sec_to_expiry")
                                    sec_to_expiry = (
                                        float(sec_to_expiry_val)
                                        if isinstance(sec_to_expiry_val, (int, float))
                                        else None
                                    )
                                    preexpiry_404_anomaly = bool(
                                        token_id in held_exposure_tokens
                                        and isinstance(sec_to_expiry, float)
                                        and sec_to_expiry
                                        > (
                                            float(self.held_preexpiry_reduce_only_sec)
                                            + float(self.expiry_boundary_epsilon_sec)
                                            + 1e-9
                                        )
                                    )
                                    if preexpiry_404_anomaly:
                                        self._preexpiry_404_anomaly_count += 1
                                        self._preexpiry_404_anomaly_last_cycle = True
                                        self.telemetry.incr("preexpiry_404_anomaly")
                                        self.events.log_event(
                                            "preexpiry_404_anomaly",
                                            {
                                                "ts_utc": utc_iso(),
                                                "run_id": self.run_id,
                                                "token_id": token_id,
                                                "market_key": str(stage_info.get("market_key") or ""),
                                                "stage": stage_name,
                                                "sec_to_expiry": sec_to_expiry,
                                                "preexpiry_reduce_only_window_sec": float(
                                                    self.held_preexpiry_reduce_only_sec
                                                ),
                                                "held_exposure_token": bool(token_id in held_exposure_tokens),
                                                "expired_reduce_only_grace_active": bool(
                                                    stage_info.get("expired_reduce_only_grace_active", False)
                                                ),
                                                "reduce_only_recovery_active": bool(
                                                    stage_info.get("reduce_only_recovery_active", False)
                                                ),
                                                "reduce_only_recovery_reason": str(
                                                    stage_info.get("reduce_only_recovery_reason") or ""
                                                ),
                                                "error": err_text,
                                            },
                                        )
                                    self.telemetry.incr("book_not_found")
                                    self.events.log_event(
                                        "book_not_found",
                                        {
                                            "ts_utc": utc_iso(),
                                            "run_id": self.run_id,
                                            "token_id": token_id,
                                            "error": err_text,
                                            "backoff_sec": float(backoff_sec),
                                            "held_exposure_token": bool(token_id in held_exposure_tokens),
                                            "market_key": str(stage_info.get("market_key") or ""),
                                            "stage": stage_name,
                                            "sec_to_expiry": sec_to_expiry,
                                            "expired_reduce_only_grace_active": bool(
                                                stage_info.get("expired_reduce_only_grace_active", False)
                                            ),
                                            "preexpiry_reduce_only_active": bool(
                                                stage_info.get("preexpiry_reduce_only_active", False)
                                            ),
                                            "reduce_only_recovery_active": bool(
                                                stage_info.get("reduce_only_recovery_active", False)
                                            ),
                                            "reduce_only_recovery_reason": str(
                                                stage_info.get("reduce_only_recovery_reason") or ""
                                            ),
                                            "preexpiry_404_anomaly": bool(preexpiry_404_anomaly),
                                        },
                                    )
                                    if top is None:
                                        continue
                                else:
                                    self.telemetry.incr("book_errors")
                                    cycle_had_error = True
                                    self.events.log_error(
                                        {
                                            "ts_utc": utc_iso(),
                                            "component": "market_data",
                                            "token_id": token_id,
                                            "error": err_text,
                                        }
                                    )
                                    if top is None:
                                        continue
                            else:
                                top, raw, latency_ms = fetched
                                self._book_not_found_backoff_mono_by_token.pop(token_id, None)
                                self._held_book_not_found_last_mono_by_token.pop(token_id, None)
                                self.telemetry.set_gauge(
                                    f"book_fetch_latency_ms.{token_id}",
                                    latency_ms,
                                )
                                self.telemetry.incr("book_updates_rest")
                                if force_rest_for_held_unusable_quote:
                                    self.telemetry.incr("held_valuation_rest_fallback_applied")
                                    self.events.log_event(
                                        "held_valuation_rest_fallback_applied",
                                        {
                                            "ts_utc": utc_iso(),
                                            "run_id": self.run_id,
                                            "token_id": token_id,
                                            "reason": "rest_book_replaced_ws_unusable_quote",
                                        },
                                    )
                                rest_updates_cycle += 1
                                if token_id in target_token_set:
                                    rest_updates_target_cycle += 1
                        else:
                            self._book_not_found_backoff_mono_by_token.pop(token_id, None)
                            self._held_book_not_found_last_mono_by_token.pop(token_id, None)
                            self.telemetry.incr("book_updates_ws")
                            ws_updates_cycle += 1
                            if token_id in target_token_set:
                                ws_updates_target_cycle += 1
                        self.telemetry.incr("book_updates")
                        valuation_books[token_id] = top
                        if token_id in target_token_set:
                            books[token_id] = top
                        self.tx_manager.on_book(top)
                        if self.log_book_top:
                            self.events.log_event(
                                "book_top",
                                {
                                    "ts_utc": top.ts_utc,
                                    "token_id": token_id,
                                    "best_bid_price": top.best_bid_price,
                                    "best_bid_size": top.best_bid_size,
                                    "best_ask_price": top.best_ask_price,
                                    "best_ask_size": top.best_ask_size,
                                    "midpoint": top.midpoint,
                                    "spread": top.spread,
                                    "source": top.source,
                                    "raw_book_present": bool(raw),
                                    "from_ws": top.source == "ws",
                                },
                            )
                        midpoint = top.midpoint
                        prev_mid = self.last_midpoint_by_token.get(token_id)
                        if isinstance(midpoint, (int, float)):
                            self.last_midpoint_by_token[token_id] = float(midpoint)
                            self.last_midpoint_ts_mono_by_token[token_id] = time.monotonic()
                        realized_vol = self.vol_tracker.update(token_id, midpoint)
                        if realized_vol is not None:
                            volatility_by_token[token_id] = realized_vol
                            self.last_volatility_by_token[token_id] = realized_vol
                            self.telemetry.set_gauge(f"realized_vol.{token_id}", realized_vol)
                        mid_move_min = float(self.cfg.get("chainlink", {}).get("mid_move_min_delta", 0.001))
                        latency_sample_mid_move_min = float(
                            max(0.0, self.chainlink_latency_sample_mid_move_min_delta)
                        )
                        mid_delta = None
                        if midpoint is not None and prev_mid is not None:
                            mid_delta = midpoint - prev_mid
                        sample_triggered = (
                            mid_delta is not None and abs(mid_delta) >= latency_sample_mid_move_min
                        )
                        event_triggered = mid_delta is not None and abs(mid_delta) >= mid_move_min
                        if (
                            midpoint is not None
                            and prev_mid is not None
                            and sample_triggered
                            and self._book_source_is_ws(top)
                        ):
                            symbol_for_targets = self.chainlink_symbol_for_targets
                            latest_chainlink = self.chainlink.get_latest(symbol_for_targets)
                            lag_ms = None
                            chainlink_price = None
                            chainlink_ts = None
                            chainlink_source_ts = None
                            if latest_chainlink is not None:
                                lag_ms = (time.monotonic() - latest_chainlink.received_monotonic) * 1000.0
                                chainlink_price = latest_chainlink.price
                                chainlink_ts = latest_chainlink.received_ts_utc
                                chainlink_source_ts = latest_chainlink.source_ts_utc
                                self.telemetry.set_gauge(f"leadlag_last_ms.{token_id}", lag_ms)
                                ingest_lag_ms = None
                                source_to_book_ms = None
                                source_dt = parse_ts(chainlink_source_ts)
                                recv_dt = parse_ts(chainlink_ts)
                                now_dt = utc_now()
                                if source_dt is not None and recv_dt is not None:
                                    ingest_lag_ms = max(0.0, (recv_dt - source_dt).total_seconds() * 1000.0)
                                if source_dt is not None:
                                    source_to_book_ms = max(0.0, (now_dt - source_dt).total_seconds() * 1000.0)
                                accepted = self._record_lag_sample(
                                    token_id,
                                    lag_ms,
                                    ingest_lag_ms=ingest_lag_ms,
                                    source_to_book_ms=source_to_book_ms,
                                )
                                if accepted:
                                    lag_samples_accepted_cycle += 1
                                lag_count, lag_median, lag_hit_rate = self._lag_stats(token_id)
                                self.telemetry.set_gauge(f"leadlag_samples.{token_id}", float(lag_count))
                                self.telemetry.set_gauge(f"leadlag_median_ms.{token_id}", lag_median)
                                self.telemetry.set_gauge(f"leadlag_hit_rate.{token_id}", lag_hit_rate)
                                self.telemetry.set_gauge(
                                    f"leadlag_verified.{token_id}",
                                    1.0 if self._lag_verified(token_id) else 0.0,
                                )
                                if accepted and self.latency_verifier.log_sample_events:
                                    self.events.log_event(
                                        "latency_sample",
                                        {
                                            "ts_utc": utc_iso(),
                                            "run_id": self.run_id,
                                            "token_id": token_id,
                                            "reaction_lag_ms": lag_ms,
                                            "ingest_lag_ms": ingest_lag_ms,
                                            "source_to_book_ms": source_to_book_ms,
                                            "book_source": self._book_source(top),
                                        },
                                    )
                            if event_triggered and self.log_leadlag_book_move:
                                self.events.log_event(
                                    "leadlag_book_move",
                                    {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "token_id": token_id,
                                        "prev_midpoint": prev_mid,
                                        "midpoint": midpoint,
                                        "mid_delta": mid_delta,
                                        "chainlink_symbol": symbol_for_targets,
                                        "chainlink_price": chainlink_price,
                                        "chainlink_tick_ts_utc": chainlink_ts,
                                        "chainlink_source_ts_utc": chainlink_source_ts,
                                        "lag_ms": lag_ms,
                                        "book_source": self._book_source(top),
                                    },
                                )
                        elif midpoint is not None and prev_mid is not None and sample_triggered:
                            self.telemetry.incr("latency_samples_skipped_non_ws_source")
                rest_fallback_used_cycle = rest_updates_cycle > 0
                all_targets_missing_ws_books = (
                    bool(target_token_ids)
                    and ws_updates_target_cycle == 0
                    and rest_updates_target_cycle > 0
                )
                self._handle_missing_book_not_found_tokens(
                    missing_book_not_found_tokens=missing_book_not_found_tokens,
                    held_exposure_tokens=held_exposure_tokens,
                )

                # If no move-triggered samples were accepted this cycle, record a heartbeat lag sample
                # so the latency verifier has observability in low-volatility windows.
                ws_latency_token_ids = self._latency_sample_token_ids(books)
                if ws_latency_token_ids and lag_samples_accepted_cycle == 0:
                    symbol_for_targets = self.chainlink_symbol_for_targets
                    latest_chainlink = self.chainlink.get_latest(symbol_for_targets)
                    if latest_chainlink is not None:
                        now_mono = time.monotonic()
                        lag_ms = max(0.0, (now_mono - latest_chainlink.received_monotonic) * 1000.0)
                        source_dt = parse_ts(latest_chainlink.source_ts_utc)
                        recv_dt = parse_ts(latest_chainlink.received_ts_utc)
                        now_dt = utc_now()
                        ingest_lag_ms = None
                        source_to_book_ms = None
                        if source_dt is not None and recv_dt is not None:
                            ingest_lag_ms = max(0.0, (recv_dt - source_dt).total_seconds() * 1000.0)
                        if source_dt is not None:
                            source_to_book_ms = max(0.0, (now_dt - source_dt).total_seconds() * 1000.0)
                        heartbeat_accepted = 0
                        for token_id in ws_latency_token_ids:
                            if self._record_lag_sample(
                                token_id,
                                lag_ms,
                                ingest_lag_ms=ingest_lag_ms,
                                source_to_book_ms=source_to_book_ms,
                            ):
                                heartbeat_accepted += 1
                        if heartbeat_accepted > 0:
                            self.telemetry.incr("latency_heartbeat_samples", heartbeat_accepted)
                            self.events.log_event(
                                "latency_sample_heartbeat",
                                {
                                    "ts_utc": utc_iso(),
                                    "run_id": self.run_id,
                                    "token_count": heartbeat_accepted,
                                    "lag_ms": lag_ms,
                                    "ingest_lag_ms": ingest_lag_ms,
                                    "source_to_book_ms": source_to_book_ms,
                                },
                            )

                latency_snapshot = self.latency_verifier.snapshot(active_tokens=self.token_ids)
                self._latest_latency_snapshot = latency_snapshot
                if latency_snapshot.changed or latency_snapshot.state != self._last_latency_state:
                    self.events.log_event(
                        "latency_regime_change",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "state": latency_snapshot.state,
                            "previous_state": latency_snapshot.previous_state,
                            "reason": latency_snapshot.reason,
                            "sample_count": latency_snapshot.sample_count,
                            "token_count": latency_snapshot.token_count,
                            "median_lag_ms": latency_snapshot.median_lag_ms,
                            "p90_lag_ms": latency_snapshot.p90_lag_ms,
                            "p95_lag_ms": latency_snapshot.p95_lag_ms,
                            "hit_rate": latency_snapshot.hit_rate,
                        },
                    )
                self._last_latency_state = latency_snapshot.state
                self.telemetry.set_gauge(
                    "latency_verifier_state",
                    self._latency_state_to_gauge(latency_snapshot.state),
                )
                self.telemetry.set_gauge("latency_verifier_sample_count", float(latency_snapshot.sample_count))
                self.telemetry.set_gauge("latency_verifier_token_count", float(latency_snapshot.token_count))
                self.telemetry.set_gauge("latency_verifier_median_lag_ms", latency_snapshot.median_lag_ms)
                self.telemetry.set_gauge("latency_verifier_p90_lag_ms", latency_snapshot.p90_lag_ms)
                self.telemetry.set_gauge("latency_verifier_p95_lag_ms", latency_snapshot.p95_lag_ms)
                self.telemetry.set_gauge("latency_verifier_hit_rate", latency_snapshot.hit_rate)
                if (
                    bool(self.cfg.get("chainlink", {}).get("enabled", False))
                    and bool(chainlink_status_live.get("connected", False))
                    and latency_snapshot.sample_count <= 0
                ):
                    self._latency_sampling_inactive_cycles += 1
                    self.telemetry.set_gauge(
                        "latency_sampling_inactive_cycles",
                        float(self._latency_sampling_inactive_cycles),
                    )
                    now_mono = time.monotonic()
                    if (
                        self._latency_sampling_inactive_cycles >= 30
                        and (now_mono - self._latency_sampling_last_log_mono)
                        >= self._latency_sampling_inactive_log_interval_sec
                    ):
                        self._latency_sampling_last_log_mono = now_mono
                        self.events.log_event(
                            "latency_sampling_inactive",
                            {
                                "ts_utc": utc_iso(),
                                "run_id": self.run_id,
                                "inactive_cycles": self._latency_sampling_inactive_cycles,
                                "chainlink_connected": bool(chainlink_status_live.get("connected", False)),
                                "chainlink_last_tick_age_sec": chainlink_status_live.get("last_tick_age_sec"),
                                "book_feed_connected": bool(book_feed_status_live.get("connected", False)),
                            },
                        )
                else:
                    self._latency_sampling_inactive_cycles = 0
                    self.telemetry.set_gauge("latency_sampling_inactive_cycles", 0.0)
                phase_market_data_ms = max(0.0, (time.monotonic() - phase_market_started) * 1000.0)
                phase_strategy_started = time.monotonic()
                confidence_scores_by_token: Dict[str, float] = {}
                size_multiplier_by_token: Dict[str, float] = {}
                spread_multiplier_by_token: Dict[str, float] = {}
                if books:
                    for token_id in books.keys():
                        score = self.latency_verifier.token_score(token_id)
                        confidence_scores_by_token[token_id] = score
                        size_mult = self.latency_verifier.token_size_multiplier(token_id)
                        spread_mult = 1.0 + ((1.0 - max(0.0, min(score, 1.0))) * 0.5)
                        size_multiplier_by_token[token_id] = size_mult
                        spread_multiplier_by_token[token_id] = spread_mult
                        self.telemetry.set_gauge(f"edge_confidence_score.{token_id}", score)
                        self.telemetry.set_gauge(f"edge_size_multiplier.{token_id}", size_mult)
                        self.telemetry.set_gauge(f"edge_spread_multiplier.{token_id}", spread_mult)
                if confidence_scores_by_token:
                    avg_score = sum(confidence_scores_by_token.values()) / float(len(confidence_scores_by_token))
                else:
                    avg_score = 0.0
                self.telemetry.set_gauge("edge_confidence_score_avg", avg_score)
                self.telemetry.set_gauge("edge_confidence_token_count", float(len(confidence_scores_by_token)))

                stage_info_by_token = {token_id: self._token_stage_info(token_id) for token_id in self.token_ids}
                self._financial_posture_class = self._resolve_financial_posture_class(
                    stage_info_by_token=stage_info_by_token
                )
                self.telemetry.set_gauge(
                    "financial_posture_class",
                    self._financial_posture_class_to_gauge(self._financial_posture_class),
                )
                maker_stage_tokens = {
                    token_id for token_id, info in stage_info_by_token.items() if bool(info.get("allow_maker", False))
                }
                taker_stage_tokens = {
                    token_id for token_id, info in stage_info_by_token.items() if bool(info.get("allow_taker", False))
                }
                self.telemetry.set_gauge("doctrine_maker_stage_token_count", float(len(maker_stage_tokens)))
                self.telemetry.set_gauge("doctrine_taker_stage_token_count", float(len(taker_stage_tokens)))
                stage_counts = {
                    STAGE_OBSERVE: 0,
                    STAGE_EVALUATE: 0,
                    STAGE_MAKER_POSITION: 0,
                    STAGE_MAKER_TAKER_SELECTIVE: 0,
                    STAGE_SNIPER_PRIMARY: 0,
                    STAGE_EXTREME_ONLY: 0,
                    STAGE_EXPIRED: 0,
                    STAGE_UNKNOWN: 0,
                }
                doctrine_gate_fail_count = 0
                for info in stage_info_by_token.values():
                    stage_name = str(info.get("stage", STAGE_UNKNOWN))
                    stage_counts[stage_name] = stage_counts.get(stage_name, 0) + 1
                    if str(info.get("doctrine_gate_verdict", "fail")) != "pass":
                        doctrine_gate_fail_count += 1
                for stage_name, count in stage_counts.items():
                    self.telemetry.set_gauge(f"doctrine_stage_count.{stage_name.lower()}", float(count))
                self.telemetry.set_gauge("doctrine_gate_fail_count", float(doctrine_gate_fail_count))
                self.telemetry.set_gauge(
                    "doctrine_unknown_stage_token_count",
                    float(sum(1 for info in stage_info_by_token.values() if str(info.get("stage")) == STAGE_UNKNOWN)),
                )

                sniper_ctx = self._sniper_context()
                degraded_expiry_fallback_active = bool(sniper_ctx.get("degraded_expiry_fallback_active", False))
                self.telemetry.set_gauge(
                    "doctrine_degraded_expiry_fallback_active",
                    1.0 if degraded_expiry_fallback_active else 0.0,
                )
                near_tokens = list(sniper_ctx.get("near_token_ids", []))
                if not near_tokens:
                    near_tokens = list(sniper_ctx.get("token_ids", []))
                self.telemetry.set_gauge("sniper_near_token_count", float(len(near_tokens)))
                lag_ready_for_sniper = (not self.latency_verifier.require_armed_for_sniper) or latency_snapshot.armed
                candidate_sniper_tokens = list(sniper_ctx.get("token_ids", []))
                if self.sniper_require_lag_verification:
                    candidate_sniper_tokens = list(sniper_ctx.get("lag_verified_token_ids", []))
                if not lag_ready_for_sniper:
                    candidate_sniper_tokens = []
                if self.latency_verifier.score_enabled:
                    candidate_sniper_tokens = [
                        token_id
                        for token_id in candidate_sniper_tokens
                        if self.latency_verifier.token_score(token_id) >= self.latency_verifier.score_min_for_taker
                    ]
                candidate_sniper_tokens = [token_id for token_id in candidate_sniper_tokens if token_id in taker_stage_tokens]
                if not self._sniper_ramp_allowed:
                    candidate_sniper_tokens = []
                if mode_state in {MODE_MAKER_ONLY, MODE_SAFE_STOP}:
                    candidate_sniper_tokens = []
                sniper_active = bool(candidate_sniper_tokens) and self.sniper_enabled
                fair_probability_by_token = self._build_fair_probability_map(books, latency_snapshot=latency_snapshot)
                secondary_fair_probability_by_token, secondary_oracle_base_status = (
                    self._build_secondary_fair_probability_map(self.token_ids)
                )
                self.telemetry.set_gauge("fair_probability_token_count", float(len(fair_probability_by_token)))
                self.telemetry.set_gauge(
                    "secondary_fair_probability_token_count",
                    float(len(secondary_fair_probability_by_token)),
                )
                oracle_fresh, oracle_tick_age_sec, oracle_freshness_reason = self._oracle_freshness()
                if oracle_tick_age_sec is not None:
                    self.telemetry.set_gauge("doctrine_oracle_tick_age_sec", float(oracle_tick_age_sec))
                self.telemetry.set_gauge("doctrine_oracle_fresh", 1.0 if oracle_fresh else 0.0)
                maker_prereq_failure_by_token: Dict[str, str] = {}
                maker_timing_gate_open_by_token: Dict[str, bool] = {
                    token_id: self._maker_timing_gate_open(
                        stage_info_by_token.get(token_id, {}).get("sec_to_expiry")
                    )
                    for token_id in maker_stage_tokens
                }
                maker_eligible_tokens = set(maker_stage_tokens)
                if self.doctrine_mode == "canonical":
                    maker_eligible_tokens = set()
                    maker_reduce_only_recovery_tokens: set[str] = set()
                    for token_id in maker_stage_tokens:
                        info = stage_info_by_token.get(token_id, {})
                        timing_gate_open = bool(maker_timing_gate_open_by_token.get(token_id, False))
                        expired_reduce_only_grace_active = bool(info.get("expired_reduce_only_grace_active", False))
                        reduce_only_recovery_active = bool(info.get("reduce_only_recovery_active", False))
                        failure_reason = self._maker_prereq_failure_reason(
                            token_id,
                            fair_probability_by_token=fair_probability_by_token,
                            latency_snapshot=latency_snapshot,
                            oracle_fresh=oracle_fresh,
                            reduce_only_recovery_active=reduce_only_recovery_active,
                        )
                        if failure_reason:
                            maker_prereq_failure_by_token[token_id] = failure_reason
                            continue
                        if self.maker_comp_timing_gate_enabled and (not timing_gate_open) and (
                            not expired_reduce_only_grace_active
                        ) and (
                            not reduce_only_recovery_active
                        ):
                            maker_prereq_failure_by_token[token_id] = "maker_timing_gate_closed"
                            continue
                        maker_eligible_tokens.add(token_id)
                        if reduce_only_recovery_active:
                            maker_reduce_only_recovery_tokens.add(str(token_id))
                    maker_eligible_tokens = self._apply_canonical_maker_ws_source_gate(
                        books=books,
                        maker_eligible_tokens=maker_eligible_tokens,
                        maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                        maker_reduce_only_recovery_tokens=maker_reduce_only_recovery_tokens,
                    )
                self.telemetry.set_gauge("doctrine_maker_eligible_token_count", float(len(maker_eligible_tokens)))
                self.telemetry.set_gauge(
                    "doctrine_maker_prereq_failure_count",
                    float(len(maker_prereq_failure_by_token)),
                )
                self._emit_doctrine_decisions(
                    stage_info_by_token,
                    maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                )
                if sniper_active:
                    effective_poll_interval = min(poll_interval, self.sniper_poll_interval_sec)
                    max_actions_override = max(self._base_runtime_actions_per_cycle, self.sniper_max_actions_per_cycle)
                    stale_action_budget = max(stale_action_budget, self.sniper_cancel_stale_action_budget)
                    orphan_action_budget = max(1, self.sniper_cancel_orphan_action_budget)
                    self.manager.set_soft_rate_limits(
                        self.sniper_order_rate_soft_limit_pct,
                        self.sniper_cancel_rate_soft_limit_pct,
                    )
                    self.telemetry.incr("sniper_active_cycles")
                else:
                    self.manager.set_soft_rate_limits(base_order_soft_limit, base_cancel_soft_limit)
                mode_size_mult = self.operating_mode.size_multiplier()
                mode_spread_mult = self.operating_mode.spread_multiplier()
                if mode_size_mult <= 0.0 and not self.risk.kill_switch:
                    self.risk.set_kill_switch("operating_mode_safe_stop")
                if mode_state in {MODE_CAUTIOUS, MODE_MAKER_ONLY}:
                    for token_id in list(size_multiplier_by_token.keys()):
                        size_multiplier_by_token[token_id] = max(0.01, size_multiplier_by_token[token_id] * mode_size_mult)
                        spread_multiplier_by_token[token_id] = max(0.5, spread_multiplier_by_token[token_id] * mode_spread_mult)
                    if mode_state == MODE_MAKER_ONLY:
                        sniper_active = False
                        candidate_sniper_tokens = []
                if not sniper_active:
                    self.manager.set_soft_rate_limits(base_order_soft_limit, base_cancel_soft_limit)
                self.telemetry.set_gauge("sniper_mode_active", 1.0 if sniper_active else 0.0)
                self.telemetry.set_gauge("sniper_token_count", float(len(candidate_sniper_tokens)))
                self.telemetry.set_gauge("operating_mode_size_mult", mode_size_mult)
                self.telemetry.set_gauge("operating_mode_spread_mult", mode_spread_mult)
                sec_to_expiry_min = sniper_ctx.get("sec_to_expiry_min")
                if isinstance(sec_to_expiry_min, (int, float)):
                    self.telemetry.set_gauge("sniper_sec_to_expiry_min", float(sec_to_expiry_min))
                self.telemetry.set_gauge(
                    "sniper_lag_verified_token_count",
                    float(sniper_ctx.get("lag_verified_token_count", 0)),
                )
                if sniper_active != self._sniper_active:
                    self._sniper_active = sniper_active
                    self.events.log_event(
                        "sniper_mode_transition",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "active": sniper_active,
                            "token_count": len(candidate_sniper_tokens),
                            "token_ids": candidate_sniper_tokens,
                            "sec_to_expiry_min": sec_to_expiry_min,
                            "lag_verified_token_count": sniper_ctx.get("lag_verified_token_count", 0),
                            "lag_state": latency_snapshot.state,
                            "effective_poll_interval_sec": effective_poll_interval,
                            "max_actions_override": max_actions_override,
                        },
                    )
                if stale_action_budget > 2:
                    extra_stale_canceled = self.manager.cancel_stale_orders(action_budget=stale_action_budget - 2)
                    if extra_stale_canceled:
                        self.telemetry.incr("stale_quote_cancels", extra_stale_canceled)

                maker_requote_delta_by_token: Dict[str, float] = {}
                maker_side_policy_by_token: Dict[str, str] = {}
                maker_competitiveness_context_by_token: Dict[str, Dict[str, Any]] = {}
                maker_competitiveness_profiles_by_token: Dict[str, Dict[str, Any]] = {}
                maker_one_sided_buy_active_count = 0
                maker_one_sided_sell_active_count = 0
                for token_id in sorted(maker_stage_tokens):
                    info = stage_info_by_token.get(token_id, {})
                    sec_to_expiry = info.get("sec_to_expiry")
                    stage = str(info.get("stage", STAGE_UNKNOWN))
                    top = books.get(token_id)
                    fair = fair_probability_by_token.get(token_id)
                    base_size_mult = float(size_multiplier_by_token.get(token_id, 1.0))
                    base_spread_mult = float(spread_multiplier_by_token.get(token_id, 1.0))
                    profile = self._maker_competitiveness_profile(
                        token_id=token_id,
                        top=top,
                        fair_probability=fair,
                        stage=stage,
                        sec_to_expiry=sec_to_expiry,
                        base_size_multiplier=base_size_mult,
                        base_spread_multiplier=base_spread_mult,
                        timing_gate_open=bool(maker_timing_gate_open_by_token.get(token_id, True)),
                    )
                    maker_competitiveness_profiles_by_token[token_id] = profile
                    if token_id in maker_eligible_tokens:
                        size_multiplier_by_token[token_id] = float(profile["size_multiplier_applied"])
                        spread_multiplier_by_token[token_id] = float(profile["spread_multiplier_applied"])
                        maker_requote_delta_by_token[token_id] = float(profile["requote_delta_applied"])
                        side_policy = str(profile["side_policy"])
                        context_payload = dict(profile["context"])
                        context_payload["financial_posture_class"] = str(self._financial_posture_class)
                        reduce_only_recovery_active = bool(info.get("reduce_only_recovery_active", False))
                        reduce_only_side_policy = str(info.get("reduce_only_side_policy") or "NONE").upper()
                        reduce_only_side = str(info.get("reduce_only_side") or "NONE").upper()
                        reduce_only_size_cap = float(info.get("reduce_only_size_cap_shares", 0.0) or 0.0)
                        if reduce_only_recovery_active:
                            context_payload["reduce_only_recovery_active"] = True
                            context_payload["reduce_only_recovery_reason"] = str(
                                info.get("reduce_only_recovery_reason") or ""
                            )
                            context_payload["reduce_only_side"] = reduce_only_side
                            context_payload["reduce_only_side_policy"] = reduce_only_side_policy
                            context_payload["reduce_only_size_cap_shares"] = float(reduce_only_size_cap)
                            context_payload["reduce_only_size_cap_below_min_order_size"] = bool(
                                info.get("reduce_only_size_cap_below_min_order_size", False)
                            )
                            context_payload["reduce_only_min_order_size_shares"] = float(
                                info.get("reduce_only_min_order_size_shares", self.risk_min_order_size_shares) or 0.0
                            )
                            context_payload["reduce_only_net_shares"] = float(info.get("reduce_only_net_shares", 0.0) or 0.0)
                            context_payload["held_preexpiry_reduce_only_sec"] = float(
                                info.get("held_preexpiry_reduce_only_sec", 0.0) or 0.0
                            )
                            context_payload["preexpiry_reduce_only_active"] = bool(
                                info.get("preexpiry_reduce_only_active", False)
                            )
                            if reduce_only_side_policy in {"SELL_ONLY", "BUY_ONLY", "NONE"}:
                                side_policy = reduce_only_side_policy
                        maker_side_policy_by_token[token_id] = side_policy
                        maker_competitiveness_context_by_token[token_id] = context_payload
                    side_policy = str(profile.get("side_policy") or "TWO_SIDED").upper()
                    if side_policy == "BUY_ONLY":
                        maker_one_sided_buy_active_count += 1
                    elif side_policy == "SELL_ONLY":
                        maker_one_sided_sell_active_count += 1

                if (
                    self.maker_comp_timing_gate_enabled
                    or self.maker_comp_edge_scale_enabled
                    or self.maker_comp_one_sided_enabled
                ):
                    for token_id in sorted(maker_stage_tokens):
                        profile = maker_competitiveness_profiles_by_token.get(token_id, {})
                        context_payload = dict(profile.get("context") or {})
                        block_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip().lower()
                        self.events.log_event(
                            "maker_competitiveness_decision",
                            {
                                "ts_utc": utc_iso(),
                                "run_id": self.run_id,
                                "token_id": token_id,
                                "maker_stage_allowed": True,
                                "maker_eligible": bool(token_id in maker_eligible_tokens),
                                "block_reason": block_reason or None,
                                "timing_gate_blocked": block_reason == "maker_timing_gate_closed",
                                **context_payload,
                            },
                        )
                maker_timing_gate_blocked_count = sum(
                    1
                    for token_id in maker_stage_tokens
                    if str(maker_prereq_failure_by_token.get(token_id, "")).strip().lower() == "maker_timing_gate_closed"
                )
                self.telemetry.set_gauge("maker_timing_gate_blocked_count_last_cycle", float(maker_timing_gate_blocked_count))
                self.telemetry.set_gauge("maker_one_sided_buy_active_count_last_cycle", float(maker_one_sided_buy_active_count))
                self.telemetry.set_gauge(
                    "maker_one_sided_sell_active_count_last_cycle",
                    float(maker_one_sided_sell_active_count),
                )

                maker_eval_token_ids = set(maker_stage_tokens) | set(maker_prereq_failure_by_token.keys())
                maker_submitted_token_ids: set[str] = set()
                maker_submitted_order_ids_by_token: Dict[str, List[str]] = {}
                maker_no_submission_reason_by_token: Dict[str, str] = {}
                maker_no_submission_category_by_token: Dict[str, str] = {}
                taker_summary: Dict[str, Any] = {
                    "attempts": 0,
                    "submitted": 0,
                    "fills_accepted": 0,
                    "submitted_token_ids": [],
                    "filled_token_ids": [],
                }
                self.telemetry.set_gauge("maker_submitted_token_count_last_cycle", 0.0)
                self.telemetry.set_gauge("maker_no_submission_token_count_last_cycle", 0.0)
                self.telemetry.set_gauge("taker_attempts_last_cycle", 0.0)
                self.telemetry.set_gauge("taker_actions_last_cycle", 0.0)
                self.telemetry.set_gauge("taker_submitted_last_cycle", 0.0)
                self.telemetry.set_gauge("taker_fills_last_cycle", 0.0)
                self.telemetry.set_gauge("taker_filled_token_count_last_cycle", 0.0)
                lag_verified_token_ids = [str(x) for x in list(sniper_ctx.get("lag_verified_token_ids", []))]
                valuation_state = self._apply_valuation_controls(books=valuation_books, phase="pre_submit")

                if not books:
                    self.telemetry.set_gauge("quote_active", 0.0)
                    if has_targets:
                        orphan_canceled = self.manager.cancel_non_target_orders(
                            set(self.token_ids),
                            action_budget=orphan_action_budget,
                        )
                        if orphan_canceled:
                            self.telemetry.incr("orphan_orders_canceled_no_books", orphan_canceled)
                        self.consecutive_failures += 1
                        self.telemetry.incr("empty_book_cycles")
                    else:
                        self.consecutive_failures = 0
                    if maker_eval_token_ids:
                        self._emit_maker_edge_evaluations(
                            books=books,
                            stage_info_by_token=stage_info_by_token,
                            maker_eval_token_ids=maker_eval_token_ids,
                            maker_submitted_token_ids=maker_submitted_token_ids,
                            maker_submitted_order_ids_by_token=maker_submitted_order_ids_by_token,
                            maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                            maker_no_submission_category_by_token=maker_no_submission_category_by_token,
                            maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                            fair_probability_by_token=fair_probability_by_token,
                            oracle_tick_age_sec=oracle_tick_age_sec,
                            latency_state=latency_snapshot.state,
                            cycle_index=int(self._doctrine_cycle_index),
                        )
                    if near_tokens:
                        taker_summary = self._run_sniper_taker(
                            books=books,
                            fair_probability_by_token=fair_probability_by_token,
                            realized_volatility_by_token=volatility_by_token,
                            secondary_fair_probability_by_token=secondary_fair_probability_by_token,
                            secondary_oracle_base_status=secondary_oracle_base_status,
                            token_ids=[str(token_id) for token_id in near_tokens],
                            stage_info_by_token=stage_info_by_token,
                            oracle_tick_age_sec=oracle_tick_age_sec,
                            oracle_fresh=oracle_fresh,
                            latency_snapshot=latency_snapshot,
                            mode_state=mode_state,
                            lag_ready_for_sniper=lag_ready_for_sniper,
                                lag_verified_token_ids=lag_verified_token_ids,
                                sniper_ramp_allowed=self._sniper_ramp_allowed,
                                cycle_index=int(self._doctrine_cycle_index),
                            )
                    valuation_state = self._apply_valuation_controls(books=valuation_books, phase="post_submit")
                    mids = dict(valuation_state.get("mid_by_token", {}))
                    total_pnl, pnl_by_token = self.risk.mark_to_market(mids)
                    self.telemetry.set_gauge("total_pnl", total_pnl)
                    for token_id, token_pnl in pnl_by_token.items():
                        self.telemetry.set_gauge(f"token_pnl.{token_id}", token_pnl)

                    loss_check = self.risk.evaluate_loss_limits(mids)
                    if not loss_check.allowed:
                        self.risk.set_kill_switch(f"{loss_check.reason}:{loss_check.detail}")
                else:
                    try:
                        books_for_manager = {
                            token_id: top for token_id, top in books.items() if token_id in maker_eligible_tokens
                        }
                        fair_for_manager = {
                            token_id: value
                            for token_id, value in fair_probability_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        volatility_for_manager = {
                            token_id: value
                            for token_id, value in volatility_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        size_mult_for_manager = {
                            token_id: value
                            for token_id, value in size_multiplier_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        spread_mult_for_manager = {
                            token_id: value
                            for token_id, value in spread_multiplier_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        requote_delta_for_manager = {
                            token_id: value
                            for token_id, value in maker_requote_delta_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        side_policy_for_manager = {
                            token_id: value
                            for token_id, value in maker_side_policy_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        competitiveness_context_for_manager = {
                            token_id: dict(value)
                            for token_id, value in maker_competitiveness_context_by_token.items()
                            if token_id in maker_eligible_tokens
                        }
                        tracked_tokens_for_manager = (
                            set(self.token_ids) if self.doctrine_mode != "canonical" else set(maker_eligible_tokens)
                        )
                        summary = self.manager.step(
                            books_for_manager,
                            tracked_tokens=tracked_tokens_for_manager,
                            fair_probability_by_token=fair_for_manager,
                            realized_volatility_by_token=volatility_for_manager,
                            size_multiplier_by_token=size_mult_for_manager,
                            spread_multiplier_by_token=spread_mult_for_manager,
                            requote_delta_by_token=requote_delta_for_manager,
                            side_policy_by_token=side_policy_for_manager,
                            competitiveness_context_by_token=competitiveness_context_for_manager,
                            max_actions_override=max_actions_override,
                        )
                        maker_submitted_token_ids = {
                            str(token_id)
                            for token_id in list(summary.get("maker_submitted_token_ids", []))
                            if str(token_id).strip()
                        }
                        maker_submitted_order_ids_by_token = {
                            str(token_id): [
                                str(order_id).strip()
                                for order_id in list(order_ids or [])
                                if str(order_id or "").strip()
                            ]
                            for token_id, order_ids in dict(summary.get("maker_submitted_order_ids_by_token", {})).items()
                            if str(token_id).strip()
                        }
                        maker_no_submission_reason_by_token = {
                            str(token_id): str(reason).strip().lower()
                            for token_id, reason in dict(summary.get("maker_no_submission_reason_by_token", {})).items()
                            if str(token_id).strip() and str(reason).strip()
                        }
                        maker_no_submission_category_by_token = {
                            str(token_id): str(category).strip().lower()
                            for token_id, category in dict(summary.get("maker_no_submission_category_by_token", {})).items()
                            if str(token_id).strip() and str(category).strip()
                        }
                        if maker_eval_token_ids:
                            self._emit_maker_edge_evaluations(
                                books=books,
                                stage_info_by_token=stage_info_by_token,
                                maker_eval_token_ids=maker_eval_token_ids,
                                maker_submitted_token_ids=maker_submitted_token_ids,
                                maker_submitted_order_ids_by_token=maker_submitted_order_ids_by_token,
                                maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                                maker_no_submission_category_by_token=maker_no_submission_category_by_token,
                                maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                                fair_probability_by_token=fair_probability_by_token,
                                oracle_tick_age_sec=oracle_tick_age_sec,
                                latency_state=latency_snapshot.state,
                                cycle_index=int(self._doctrine_cycle_index),
                            )
                        if near_tokens:
                            taker_summary = self._run_sniper_taker(
                                books=books,
                                fair_probability_by_token=fair_probability_by_token,
                                realized_volatility_by_token=volatility_by_token,
                                secondary_fair_probability_by_token=secondary_fair_probability_by_token,
                                secondary_oracle_base_status=secondary_oracle_base_status,
                                token_ids=[str(token_id) for token_id in near_tokens],
                                stage_info_by_token=stage_info_by_token,
                                oracle_tick_age_sec=oracle_tick_age_sec,
                                oracle_fresh=oracle_fresh,
                                latency_snapshot=latency_snapshot,
                                mode_state=mode_state,
                                lag_ready_for_sniper=lag_ready_for_sniper,
                                lag_verified_token_ids=lag_verified_token_ids,
                                sniper_ramp_allowed=self._sniper_ramp_allowed,
                                cycle_index=int(self._doctrine_cycle_index),
                            )
                            if taker_summary["attempts"] > 0:
                                self.telemetry.incr("sniper_taker_attempts", taker_summary["attempts"])
                            if taker_summary["submitted"] > 0:
                                self.telemetry.incr("sniper_taker_submitted", taker_summary["submitted"])
                        self.telemetry.set_gauge("open_orders", float(summary["open_orders"]))
                        self.telemetry.set_gauge("actions_last_cycle", float(summary["actions"]))
                        self.telemetry.set_gauge("fills_last_cycle", float(summary["fills"]))
                        status_window_actions += int(summary.get("actions", 0) or 0)
                        status_window_fills += int(summary.get("fills", 0) or 0)
                        self.telemetry.set_gauge(
                            "maker_submitted_token_count_last_cycle",
                            float(len(maker_submitted_token_ids)),
                        )
                        self.telemetry.set_gauge(
                            "maker_no_submission_token_count_last_cycle",
                            float(len(maker_no_submission_reason_by_token)),
                        )
                        self.telemetry.set_gauge(
                            "taker_attempts_last_cycle",
                            float(taker_summary.get("attempts", 0)),
                        )
                        self.telemetry.set_gauge(
                            "taker_actions_last_cycle",
                            float(taker_summary.get("submitted", 0)),
                        )
                        self.telemetry.set_gauge(
                            "taker_submitted_last_cycle",
                            float(taker_summary.get("submitted", 0)),
                        )
                        self.telemetry.set_gauge(
                            "taker_fills_last_cycle",
                            float(taker_summary.get("fills_accepted", 0)),
                        )
                        status_window_taker_actions += int(taker_summary.get("submitted", 0) or 0)
                        status_window_taker_submitted += int(taker_summary.get("submitted", 0) or 0)
                        status_window_taker_fills += int(taker_summary.get("fills_accepted", 0) or 0)
                        self.telemetry.set_gauge(
                            "taker_filled_token_count_last_cycle",
                            float(len(taker_summary.get("filled_token_ids", []))),
                        )
                        quote_active = (
                            1.0
                            if (
                                int(summary["open_orders"]) > 0
                                or int(summary["actions"]) > 0
                                or int(taker_summary.get("attempts", 0)) > 0
                                or int(taker_summary.get("submitted", 0)) > 0
                            )
                            else 0.0
                        )
                        self.telemetry.set_gauge("quote_active", quote_active)

                        valuation_state = self._apply_valuation_controls(books=valuation_books, phase="post_submit")
                        mids = dict(valuation_state.get("mid_by_token", {}))
                        total_pnl, pnl_by_token = self.risk.mark_to_market(mids)
                        self.telemetry.set_gauge("total_pnl", total_pnl)
                        for token_id, token_pnl in pnl_by_token.items():
                            self.telemetry.set_gauge(f"token_pnl.{token_id}", token_pnl)

                        loss_check = self.risk.evaluate_loss_limits(mids)
                        if not loss_check.allowed:
                            self.risk.set_kill_switch(f"{loss_check.reason}:{loss_check.detail}")
                        self.consecutive_failures = 0
                    except Exception as exc:
                        self.telemetry.set_gauge("quote_active", 0.0)
                        self.consecutive_failures += 1
                        cycle_had_error = True
                        self.telemetry.incr("manager_errors")
                        self.events.log_error(
                            {
                                "ts_utc": utc_iso(),
                                "component": "order_manager",
                                "error": str(exc),
                            }
                        )

                risk_rejects_delta = int(self.telemetry.counters.get("risk_rejects", 0)) - risk_rejects_start
                stale_rejects_delta = int(self.telemetry.counters.get("risk_reject_stale_book", 0)) - stale_rejects_start
                kill_switch_rejects_delta = (
                    int(self.telemetry.counters.get("risk_reject_kill_switch", 0)) - kill_switch_rejects_start
                )
                order_submission_attempts_delta = (
                    int(self.telemetry.counters.get("order_submission_transport_attempted", 0))
                    - order_submission_transport_attempted_start
                )
                self._order_submission_attempts_last_cycle = max(0, int(order_submission_attempts_delta))
                status_window_order_submit_attempts += int(self._order_submission_attempts_last_cycle)
                self.telemetry.set_gauge(
                    "order_submission_attempts_last_cycle",
                    float(self._order_submission_attempts_last_cycle),
                )
                if (not has_targets) and self._order_submission_attempts_last_cycle > 0:
                    self.telemetry.incr("runtime_semantic_invariant_violations")
                    self.events.log_error(
                        {
                            "ts_utc": utc_iso(),
                            "component": "runtime_semantics",
                            "error": "no_target_order_submission_attempt",
                            "order_submission_attempts_last_cycle": int(self._order_submission_attempts_last_cycle),
                            "target_count": len(self.token_ids),
                            "runtime_state": self._runtime_state,
                        }
                    )
                    if not self.risk.kill_switch:
                        self.risk.set_kill_switch("runtime_invariant:no_target_order_submission_attempt")
                effective_risk_rejects_delta = self._effective_risk_rejects(
                    risk_rejects_delta,
                    kill_switch_rejects_delta,
                )
                if stale_rejects_delta > 0 and not self._first_stale_burst_logged:
                    self._first_stale_burst_logged = True
                    self.events.log_event(
                        "stale_reject_burst_first",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "stale_rejects_delta": stale_rejects_delta,
                            "risk_rejects_delta_raw": risk_rejects_delta,
                            "risk_rejects_delta_effective": effective_risk_rejects_delta,
                            "kill_switch_rejects_delta": kill_switch_rejects_delta,
                            "kill_switch_active": bool(self.risk.kill_switch),
                        },
                    )
                ws_slo_degraded, ws_slo_reasons = self._ws_slo_degraded_cycle(
                    has_targets=bool(has_targets),
                    book_feed_status=book_feed_status_live,
                    chainlink_status=chainlink_status_live,
                    all_targets_missing_ws_books=all_targets_missing_ws_books,
                    rest_fallback_used_cycle=rest_fallback_used_cycle,
                )
                ws_slo_bootstrap_active = bool(self._ws_slo_bootstrap_active)
                self.telemetry.set_gauge("ws_slo_bootstrap_active", 1.0 if ws_slo_bootstrap_active else 0.0)
                if ws_slo_bootstrap_active != self._last_ws_slo_bootstrap_active:
                    self.events.log_event(
                        "ws_slo_bootstrap_state",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "active": bool(ws_slo_bootstrap_active),
                            "token_count": len(self.token_ids),
                            "grace_sec": float(self.operating_mode_ws_slo_bootstrap_grace_sec),
                        },
                    )
                self._last_ws_slo_bootstrap_active = bool(ws_slo_bootstrap_active)
                self.telemetry.set_gauge("ws_slo_degraded_cycle", 1.0 if ws_slo_degraded else 0.0)
                ws_slo_reason = ",".join(ws_slo_reasons)
                if (
                    ws_slo_degraded != self._last_ws_slo_degraded
                    or ws_slo_reason != self._last_ws_slo_reason
                ):
                    self.events.log_event(
                        "ws_slo_state",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "degraded": bool(ws_slo_degraded),
                            "reasons": list(ws_slo_reasons),
                            "has_targets": bool(has_targets),
                            "book_connected": bool(book_feed_status_live.get("connected", False)),
                            "book_last_msg_age_sec": book_feed_status_live.get("last_msg_age_sec"),
                            "ws_updates_cycle": int(ws_updates_cycle),
                            "rest_updates_cycle": int(rest_updates_cycle),
                            "rest_fallback_used_cycle": bool(rest_fallback_used_cycle),
                            "all_targets_missing_ws_books": bool(all_targets_missing_ws_books),
                            "chainlink_connected": bool(chainlink_status_live.get("connected", False)),
                            "chainlink_last_tick_age_sec": chainlink_status_live.get("last_tick_age_sec"),
                        },
                    )
                self._last_ws_slo_degraded = bool(ws_slo_degraded)
                self._last_ws_slo_reason = ws_slo_reason
                disarmed_cycle = self._disarmed_cycle_signal(latency_snapshot)
                mode_snapshot = self.operating_mode.observe_cycle(
                    risk_rejects=effective_risk_rejects_delta,
                    stale_rejects=stale_rejects_delta,
                    outage_cycle=bool(has_targets and (not ws_slo_bootstrap_active) and ((not books) or ws_slo_degraded)),
                    disarmed_cycle=disarmed_cycle,
                    error_cycle=bool(cycle_had_error),
                )
                reconcile_mismatch_ratio = self._read_reconcile_mismatch_ratio()
                ramp_snapshot = self.ramp.observe_cycle(
                    reject_ratio=(1.0 if risk_rejects_delta > 0 else 0.0),
                    stale_oracle_ratio=(1.0 if stale_oracle_cycle else 0.0),
                    disarmed_ratio=(1.0 if disarmed_cycle else 0.0),
                    reconcile_mismatch_ratio=reconcile_mismatch_ratio,
                )
                self.telemetry.set_gauge("ramp_enabled", 1.0 if ramp_snapshot.enabled else 0.0)
                self.telemetry.set_gauge("ramp_target_usd", float(ramp_snapshot.target_usd))
                self.telemetry.set_gauge("ramp_sniper_allowed", 1.0 if ramp_snapshot.sniper_allowed else 0.0)
                self.telemetry.set_gauge("ramp_reconcile_mismatch_ratio", float(reconcile_mismatch_ratio))
                if ramp_snapshot.enabled:
                    self._active_target_usd = float(ramp_snapshot.target_usd)
                    self._sniper_ramp_allowed = bool(ramp_snapshot.sniper_allowed)
                    self.manager.sizing_target_usd = float(self._active_target_usd)
                    if self.sizing_mode == "notional":
                        self.sniper_taker_target_usd = float(self._active_target_usd)
                if ramp_snapshot.changed:
                    self.events.log_event(
                        "ramp_transition",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "target_usd": float(ramp_snapshot.target_usd),
                            "sniper_allowed": bool(ramp_snapshot.sniper_allowed),
                            "reason": ramp_snapshot.reason,
                            "reconcile_mismatch_ratio": float(reconcile_mismatch_ratio),
                        },
                    )
                self.telemetry.set_gauge("operating_mode_state", self._operating_mode_to_gauge(mode_snapshot.state))
                self.telemetry.set_gauge("operating_mode_stale_reject_ratio", mode_snapshot.stale_reject_ratio)
                self.telemetry.set_gauge("operating_mode_outage_ratio", mode_snapshot.outage_ratio)
                self.telemetry.set_gauge("operating_mode_disarmed_ratio", mode_snapshot.disarmed_ratio)
                self.telemetry.set_gauge("operating_mode_error_ratio", mode_snapshot.error_ratio)
                self.telemetry.set_gauge("operating_mode_risk_reject_count", float(mode_snapshot.risk_reject_count))
                self.telemetry.set_gauge("operating_mode_stale_reject_count", float(mode_snapshot.stale_reject_count))
                self.telemetry.set_gauge("cycle_risk_rejects_raw", float(max(0, risk_rejects_delta)))
                self.telemetry.set_gauge("cycle_risk_rejects_effective", float(max(0, effective_risk_rejects_delta)))
                self.telemetry.set_gauge("cycle_risk_rejects_kill_switch", float(max(0, kill_switch_rejects_delta)))
                if mode_snapshot.changed or mode_snapshot.state != self._last_operating_mode_state:
                    self._mode_transition_mono.append(time.monotonic())
                    self.events.log_event(
                        "operating_mode_transition",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "state": mode_snapshot.state,
                            "previous_state": mode_snapshot.previous_state,
                            "reason": mode_snapshot.reason,
                            "sample_count": mode_snapshot.sample_count,
                            "risk_reject_count": mode_snapshot.risk_reject_count,
                            "stale_reject_count": mode_snapshot.stale_reject_count,
                            "stale_reject_ratio": mode_snapshot.stale_reject_ratio,
                            "outage_ratio": mode_snapshot.outage_ratio,
                            "disarmed_ratio": mode_snapshot.disarmed_ratio,
                            "error_ratio": mode_snapshot.error_ratio,
                        },
                    )
                    if mode_snapshot.state != MODE_NORMAL:
                        self.alerts.notify(
                            "warning",
                            f"{self.bot_name} operating mode={mode_snapshot.state}",
                            {
                                "bot_name": self.bot_name,
                                "run_id": self.run_id,
                                "state": mode_snapshot.state,
                                "previous_state": mode_snapshot.previous_state,
                                "reason": mode_snapshot.reason,
                            },
                            key="operating_mode",
                        )
                    if mode_snapshot.state == MODE_SAFE_STOP and not self.risk.kill_switch:
                        self.risk.set_kill_switch("operating_mode_safe_stop")
                self._last_operating_mode_state = mode_snapshot.state
                self.telemetry.set_gauge("mode_transitions_10m", float(self._recent_mode_transitions()))
                self._evaluate_alert_policy(mode_snapshot)
                phase_strategy_exec_ms = max(0.0, (time.monotonic() - phase_strategy_started) * 1000.0)

                if self.consecutive_failures >= max_consecutive_failures:
                    reason = f"consecutive_failures={self.consecutive_failures}"
                    LOG.error("Kill switch engaged: %s", reason)
                    self.risk.set_kill_switch(reason)

                if self.risk.kill_switch and not self.last_kill_switch_state:
                    self.alerts.notify(
                        "critical",
                        f"{self.bot_name} kill switch engaged",
                        {
                            "bot_name": self.bot_name,
                            "run_id": self.run_id,
                            "reason": self.risk.kill_reason,
                            "mode": self.cfg["mode"],
                        },
                        key="kill_switch",
                    )
                    try:
                        self._cancel_all_open_orders(
                            event_name="kill_switch_cancel_all",
                            reason=self.risk.kill_reason,
                            telemetry_counter="kill_switch_cancel_all_calls",
                        )
                    except Exception as exc:
                        self.events.log_error(
                            {
                                "ts_utc": utc_iso(),
                                "component": "risk",
                                "action": "kill_switch_cancel_all",
                                "error": str(exc),
                            }
                        )
                self.last_kill_switch_state = self.risk.kill_switch

                now = time.monotonic()
                if now >= next_state_flush:
                    state_io_started = time.monotonic()
                    try:
                        self._dump_state()
                    except Exception as exc:
                        self.telemetry.incr("state_save_errors")
                        now_mono = time.monotonic()
                        if (now_mono - self._state_save_last_error_log_mono) >= self._state_save_error_log_interval_sec:
                            self._state_save_last_error_log_mono = now_mono
                            self.events.log_error(
                                {
                                    "ts_utc": utc_iso(),
                                    "component": "state_store",
                                    "action": "save_state",
                                    "state_path": str(self.state_path),
                                    "error": str(exc),
                                }
                            )
                    next_state_flush = now + reconcile_interval
                    phase_state_io_ms = max(0.0, (time.monotonic() - state_io_started) * 1000.0)

                if now >= next_status:
                    status_io_started = time.monotonic()
                    self._update_runtime_semantics(has_targets=bool(self.token_ids))
                    self.telemetry.set_gauge("actions_last_status_window", float(status_window_actions))
                    self.telemetry.set_gauge("fills_last_status_window", float(status_window_fills))
                    self.telemetry.set_gauge("taker_actions_last_status_window", float(status_window_taker_actions))
                    self.telemetry.set_gauge("taker_submitted_last_status_window", float(status_window_taker_submitted))
                    self.telemetry.set_gauge("taker_fills_last_status_window", float(status_window_taker_fills))
                    self.telemetry.set_gauge(
                        "order_submission_attempts_last_status_window",
                        float(status_window_order_submit_attempts),
                    )
                    telemetry = self.telemetry.snapshot()
                    positions = {token: pos.net_shares for token, pos in self.risk.positions.items()}
                    chainlink_status = self.chainlink.status()
                    book_feed_status = self.book_feed.status()
                    wallet_contract = self.wallet.status_contract()
                    guard_active, guard_reason = self._read_external_guard_stop()
                    status_ts_utc = utc_iso()
                    status_row = {
                        "bot_name": self.bot_name,
                        "ts_utc": status_ts_utc,
                        "ts_event_utc": status_ts_utc,
                        "ts_receive_utc": status_ts_utc,
                        "ts_source_utc": None,
                        "ts_decision_utc": status_ts_utc,
                        "time_policy": dict(self._time_policy),
                        "mode": self.cfg["mode"],
                        "doctrine_mode": self.doctrine_mode,
                        "maker_market_reference_policy": dict(self._maker_market_reference_policy),
                        "run_id": self.run_id,
                        "runtime_state": self._runtime_state,
                        "active_targets_present": self._runtime_active_targets_present,
                        "no_target_standdown": self._runtime_no_target_standdown,
                        "book_feed_required": self._runtime_book_feed_required,
                        "promotion_eligibility_hint": self._runtime_promotion_eligibility_hint,
                        "order_submission_attempts_last_cycle": int(self._order_submission_attempts_last_cycle),
                        "kill_switch": self.risk.kill_switch,
                        "kill_reason": self.risk.kill_reason or None,
                        "alert_transport_enabled": bool(self.alert_transport_enabled),
                        "auto_stop_control_authority_enabled": bool(self.alert_auto_stop_control_authority_enabled),
                        "transport_disable_control_authority_unchanged": bool(
                            self.alert_transport_disable_control_authority_unchanged
                        ),
                        "alert_semantics": {
                            "transport_enabled": bool(self.alert_transport_enabled),
                            "transport_layer_class": "notification_transport",
                            "control_authority_class": "risk_control_authority",
                            "auto_stop_control_authority_enabled": bool(self.alert_auto_stop_control_authority_enabled),
                            "transport_disable_control_authority_unchanged": bool(
                                self.alert_transport_disable_control_authority_unchanged
                            ),
                        },
                        "positions": positions,
                        "chainlink": chainlink_status,
                        "secondary_oracle": {"pyth": pyth_status_live},
                        "book_feed": book_feed_status,
                        "wallet_contract": wallet_contract,
                        "wallet_health_ok": bool(wallet_contract.get("wallet_health_ok", False)),
                        "wallet_health_reasons": list(wallet_contract.get("wallet_health_reasons", [])),
                        "wallet_authority_status_class": str(
                            wallet_contract.get("authority_status_class", "bootstrap_non_authoritative")
                        ),
                        "wallet_order_capable_live": bool(wallet_contract.get("order_capable_live", False)),
                        "wallet_order_submit_eligible": bool(wallet_contract.get("order_submit_eligible", False)),
                        "wallet_canonical_live_nonce_available": bool(
                            wallet_contract.get("canonical_live_nonce_available", False)
                        ),
                        "wallet_canonical_live_pending_wallet_tx_available": bool(
                            wallet_contract.get("canonical_live_pending_wallet_tx_available", False)
                        ),
                        "wallet_live_truth_gap_reasons": list(wallet_contract.get("live_truth_gap_reasons", [])),
                        "wallet_gas_balance": float(wallet_contract.get("gas_balance", 0.0)),
                        "wallet_gas_reserve_min": float(wallet_contract.get("gas_reserve_min", 0.0)),
                        "wallet_gas_ok": bool(wallet_contract.get("gas_ok", False)),
                        "wallet_stable_balance_total": float(wallet_contract.get("stable_balance_total", 0.0)),
                        "wallet_protected_reserve": float(wallet_contract.get("protected_reserve", 0.0)),
                        "wallet_open_reserved": float(wallet_contract.get("open_reserved", 0.0)),
                        "wallet_deployable_capital": float(wallet_contract.get("deployable_capital", 0.0)),
                        "wallet_approval_ok": bool(wallet_contract.get("approval_ok", False)),
                        "wallet_nonce_ok": bool(wallet_contract.get("nonce_ok", False)),
                        "wallet_reconcile_ok": bool(wallet_contract.get("reconcile_ok", False)),
                        "valuation_degraded": bool(self._valuation_degraded),
                        "valuation_hard_degraded": bool(self._valuation_hard_degraded),
                        "pnl_degraded": bool(self._pnl_degraded),
                        "loss_guard_degraded": bool(self._loss_guard_degraded),
                        "valuation_degraded_reasons": list(self._valuation_degraded_reasons),
                        "valuation_mid_source_counts": dict(self._valuation_mid_source_counts),
                        "valuation_mid_source_counts_raw": dict(self._valuation_mid_source_counts_raw),
                        "held_unpriceable_token_count": int(len(self._held_unpriceable_token_ids)),
                        "held_unpriceable_token_ids": list(self._held_unpriceable_token_ids),
                        "held_unpriceable_max_age_sec": float(self._held_unpriceable_max_age_sec),
                        "held_unpriceable_age_by_token": dict(self._held_unpriceable_age_by_token),
                        "held_unpriceable_escalation_active": bool(self._held_unpriceable_escalation_active),
                        "held_unpriceable_escalation_token_count": int(len(self._held_unpriceable_escalation_token_ids)),
                        "held_unpriceable_escalation_token_ids": list(self._held_unpriceable_escalation_token_ids),
                        "held_unpriceable_escalation_reasons": list(self._held_unpriceable_escalation_reasons),
                        "held_unpriceable_escalation_max_age_sec": float(self._held_unpriceable_escalation_max_age_sec),
                        "held_unpriceable_escalation_threshold_sec": float(self.held_unpriceable_escalation_sec),
                        "held_unpriceable_operator_action": str(self._held_unpriceable_operator_action),
                        "held_unpriceable_defect_candidate": bool(self._held_unpriceable_escalation_active),
                        "held_unpriceable_cause_by_token": dict(self._held_unpriceable_cause_by_token),
                        "held_unpriceable_cause_counts": dict(self._held_unpriceable_cause_counts),
                        "held_unpriceable_dominant_cause": str(self._held_unpriceable_dominant_cause),
                        "held_exposure_class_by_token": dict(self._held_exposure_class_by_token),
                        "held_exposure_detail_by_token": dict(self._held_exposure_detail_by_token),
                        "held_dust_token_ids": list(self._held_dust_token_ids),
                        "held_dust_count": int(len(self._held_dust_token_ids)),
                        "held_dust_quarantined_token_ids": list(self._held_dust_quarantined_token_ids),
                        "held_dust_quarantined_count": int(len(self._held_dust_quarantined_token_ids)),
                        "held_dust_total_notional_upper_bound_usd": float(
                            self._held_dust_total_notional_upper_bound_usd
                        ),
                        "held_dust_shadow_candidate_active": bool(self._held_dust_shadow_candidate_active),
                        "held_dust_shadow_active": bool(self._held_dust_shadow_active),
                        "held_dust_shadow_enter_pending_cycles": int(self._held_dust_shadow_enter_pending_cycles),
                        "held_dust_shadow_clear_pending_cycles": int(self._held_dust_shadow_clear_pending_cycles),
                        "held_dust_enforced_this_cycle": bool(self._held_dust_enforced_this_cycle),
                        "held_dust_hard_degraded_exempt_count": int(
                            self._held_dust_effective_hard_degraded_exempt_count
                        ),
                        "held_dust_raw_hard_degraded_token_count": int(
                            self._held_dust_raw_hard_degraded_token_count
                        ),
                        "valuation_raw_hard_degraded": bool(
                            self._held_dust_raw_hard_degraded_token_count > 0
                        ),
                        "valuation_raw_degraded": bool(
                            self._valuation_degraded or (self._held_dust_raw_hard_degraded_token_count > 0)
                        ),
                        "dust_classifier_enforce_enabled": bool(self.dust_classifier_enforce_enabled),
                        "runtime_expiry_boundary_epsilon_sec": float(self.expiry_boundary_epsilon_sec),
                        "valuation_hard_degraded_enter_count": int(self._valuation_hard_degraded_enter_count),
                        "valuation_hard_degraded_clear_count": int(self._valuation_hard_degraded_clear_count),
                        "valuation_hard_degraded_pending_healthy_cycles": int(
                            self._valuation_hard_degraded_pending_healthy_cycles
                        ),
                        "valuation_hard_degraded_clear_consecutive_healthy_cycles": int(
                            self.valuation_hard_degraded_clear_consecutive_healthy_cycles
                        ),
                        "held_unpriceable_started_count": int(self._held_unpriceable_started_count),
                        "held_unpriceable_recovered_count": int(self._held_unpriceable_recovered_count),
                        "preexpiry_404_anomaly_count": int(self._preexpiry_404_anomaly_count),
                        "preexpiry_404_anomaly_active": bool(self._preexpiry_404_anomaly_last_cycle),
                        "financial_posture_class": str(self._financial_posture_class),
                        "valuation_live_mid_max_age_sec": float(self.live_mid_max_age_sec),
                        "valuation_one_sided_quote_max_age_sec": float(self.one_sided_quote_max_age_sec),
                        "valuation_last_known_mid_max_age_sec": float(self.last_known_mid_max_age_sec),
                        "valuation_held_unpriceable_escalation_sec": float(self.held_unpriceable_escalation_sec),
                        "valuation_held_preexpiry_reduce_only_sec": float(self.held_preexpiry_reduce_only_sec),
                        "sniper_multi_oracle_cap_usd": (
                            float(self.sniper_taker_multi_oracle_cap_usd)
                            if isinstance(self.sniper_taker_multi_oracle_cap_usd, (int, float))
                            else None
                        ),
                        "sniper_multi_oracle_cap_source": str(self.sniper_taker_multi_oracle_cap_source),
                        "sniper_multi_oracle_cap_authority_class": str(
                            self.sniper_taker_multi_oracle_cap_authority_class
                        ),
                        "external_guard_active": bool(guard_active),
                        "external_guard": {
                            "configured": self.guard_stop_file is not None,
                            "path": str(self.guard_stop_file) if self.guard_stop_file is not None else "",
                            "active": guard_active,
                            "reason": guard_reason if guard_active else "",
                        },
                        **telemetry,
                    }
                    self.events.log_status(status_row)
                    self.prometheus.update(
                        telemetry,
                        {
                            "kill_switch": 1.0 if self.risk.kill_switch else 0.0,
                            "chainlink_connected": 1.0 if bool(chainlink_status.get("connected", False)) else 0.0,
                            "book_feed_connected": 1.0 if bool(book_feed_status.get("connected", False)) else 0.0,
                            "target_count": float(len(self.token_ids)),
                            "runtime_state_code": runtime_state_to_gauge(self._runtime_state),
                            "active_targets_present": 1.0 if self._runtime_active_targets_present else 0.0,
                            "no_target_standdown": 1.0 if self._runtime_no_target_standdown else 0.0,
                            "book_feed_required": 1.0 if self._runtime_book_feed_required else 0.0,
                            "promotion_eligibility_hint": 1.0 if self._runtime_promotion_eligibility_hint else 0.0,
                        },
                    )
                    LOG.info(
                        "status bot=%s mode=%s runtime_state=%s kill=%s guard=%s cycles=%s book_updates=%s fills=%s open_orders=%s total_pnl=%.4f cl_connected=%s ws_book_connected=%s cl_reconnects=%s ws_book_reconnects=%s positions=%s",
                        self.bot_name,
                        self.cfg["mode"],
                        self._runtime_state,
                        self.risk.kill_switch,
                        guard_active,
                        int(telemetry.get("counter.cycles", 0)),
                        int(telemetry.get("counter.book_updates", 0)),
                        int(telemetry.get("counter.fills", 0)),
                        int(telemetry.get("gauge.open_orders", 0)),
                        float(telemetry.get("gauge.total_pnl", 0.0)),
                        bool(chainlink_status.get("connected", False)),
                        bool(book_feed_status.get("connected", False)),
                        int(chainlink_status.get("reconnects", 0)),
                        int(book_feed_status.get("reconnects", 0)),
                        positions,
                    )
                    next_status = now + status_interval
                    status_window_actions = 0
                    status_window_fills = 0
                    status_window_taker_actions = 0
                    status_window_taker_submitted = 0
                    status_window_taker_fills = 0
                    status_window_order_submit_attempts = 0
                    phase_status_io_ms = max(0.0, (time.monotonic() - status_io_started) * 1000.0)

                cycle_elapsed = time.monotonic() - cycle_started
                rss_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                self.telemetry.set_gauge("process_rss_mb", rss_kb / 1024.0)
                self.telemetry.set_gauge("cycle_latency_ms", cycle_elapsed * 1000.0)
                cycle_latency_ms = cycle_elapsed * 1000.0
                self.telemetry.set_gauge("cycle_span_market_data_ms", phase_market_data_ms)
                self.telemetry.set_gauge("cycle_span_strategy_exec_ms", phase_strategy_exec_ms)
                self.telemetry.set_gauge("cycle_span_state_io_ms", phase_state_io_ms)
                self.telemetry.set_gauge("cycle_span_status_io_ms", phase_status_io_ms)
                residual = max(
                    0.0,
                    cycle_latency_ms - (phase_market_data_ms + phase_strategy_exec_ms + phase_state_io_ms + phase_status_io_ms),
                )
                self.telemetry.set_gauge("cycle_span_residual_ms", residual)
                time.sleep(max(0.0, effective_poll_interval - cycle_elapsed))
        finally:
            if cancel_all_on_exit:
                try:
                    self._cancel_all_open_orders(
                        event_name="cancel_all_on_exit",
                        reason="runner_shutdown",
                        telemetry_counter="shutdown_cancel_all_calls",
                    )
                except Exception as exc:
                    self.events.log_error(
                        {
                            "ts_utc": utc_iso(),
                            "component": "runner_shutdown",
                            "action": "cancel_all",
                            "error": str(exc),
                        }
                    )

            with contextlib.suppress(Exception):
                self._dump_state()
            with contextlib.suppress(Exception):
                self._write_run_manifest_end()
            with contextlib.suppress(Exception):
                self.events.log_event("runner_stop", {"ts_utc": utc_iso(), "run_id": self.run_id})
            with contextlib.suppress(Exception):
                self.events.close()
            with contextlib.suppress(Exception):
                self.book_client.close()
            if self._rest_fetch_pool is not None:
                with contextlib.suppress(Exception):
                    self._rest_fetch_pool.shutdown(wait=False, cancel_futures=True)
            with contextlib.suppress(Exception):
                self.tx_manager.close()
            with contextlib.suppress(Exception):
                self.discovery.close()
            with contextlib.suppress(Exception):
                self.book_feed.stop()
            with contextlib.suppress(Exception):
                self.chainlink.stop()
            with contextlib.suppress(Exception):
                self.pyth.stop()
            with contextlib.suppress(Exception):
                self.prometheus.stop()
            with contextlib.suppress(Exception):
                self.alerts.close()


def enforce_operator_entry_policy(*, mode: str, config: Optional[Dict[str, Any]] = None) -> None:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode != "paper":
        return
    if str(os.getenv("BRO_CANONICAL_SESSION_CALL", "0")).strip() != "1":
        raise SystemExit(
            "direct executor invocation is disabled for paper mode; "
            "use ./scripts/canonical_paper_session.sh"
        )
    session_token = str(os.getenv("BRO_CANONICAL_SESSION_TOKEN", "")).strip()
    context_file_raw = str(os.getenv("BRO_CANONICAL_SESSION_CONTEXT_FILE", "")).strip()
    run_id = str(os.getenv("BRO_RUN_ID", "")).strip()
    if not session_token or not context_file_raw:
        raise SystemExit(
            "canonical session handshake missing; "
            "use ./scripts/canonical_paper_session.sh"
        )
    if not run_id:
        raise SystemExit(
            "canonical run_id missing; "
            "use ./scripts/canonical_paper_session.sh"
        )
    try:
        uuid.UUID(run_id)
    except Exception as exc:
        raise SystemExit(
            f"canonical run_id invalid (must be UUID): {run_id!r}; "
            "use ./scripts/canonical_paper_session.sh"
        ) from exc
    log_dir = pathlib.Path("./logs_exec/paper_universal").resolve()
    if isinstance(config, dict):
        storage = config.get("storage")
        if isinstance(storage, dict):
            raw_log_dir = str(storage.get("log_dir") or "").strip()
            if raw_log_dir:
                log_dir = pathlib.Path(raw_log_dir).expanduser().resolve()
    decision = resolve_authority_decision(
        AuthorityRequest(
            actor=ACTOR_EXECUTOR,
            action=CAPABILITY_EXECUTOR_RUN,
            log_dir=log_dir,
            session_context_file=pathlib.Path(context_file_raw),
            session_token=session_token,
            run_id=run_id,
            require_authoritative=True,
            allow_open_contract=True,
        )
    )
    if not decision.authorized:
        raise SystemExit(render_authority_denial(decision, prefix="canonical_executor_authority_denied"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro Polymarket execution runner (paper/live)")
    parser.add_argument("--config", required=True, help="Path to execution config YAML")
    parser.add_argument("--duration-min", type=float, default=None, help="Optional run duration in minutes")
    parser.add_argument("--mode", choices=["paper", "live"], default=None, help="Optional mode override")
    parser.add_argument("--confirm-live", action="store_true", help="Required arming flag for live mode")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight checks (not recommended)")
    args = parser.parse_args()

    configure_console_logging()
    config_source_path = pathlib.Path(args.config).resolve()
    cfg = load_execution_config(config_source_path)

    if args.mode:
        cfg["mode"] = args.mode
        validate_execution_config(cfg)

    duration = args.duration_min
    if duration is None:
        cfg_duration = cfg["runtime"].get("duration_min")
        duration = float(cfg_duration) if cfg_duration is not None else None

    mode = str(cfg["mode"]).lower()
    enforce_operator_entry_policy(mode=mode, config=cfg)
    if mode == "live" and bool(cfg.get("preflight", {}).get("require_live_confirmation", True)) and not args.confirm_live:
        raise SystemExit("live mode requires --confirm-live (arming gate)")
    if mode == "live" and args.skip_preflight:
        raise SystemExit("live mode does not allow --skip-preflight")
    if mode == "live":
        findings = run_preflight_checks(cfg, mode_override=mode, confirm_live=args.confirm_live)
        if findings:
            msg = "; ".join(findings)
            raise SystemExit(f"preflight failed: {msg}")

    try:
        runner = ExecutionRunner(cfg, config_source_path=config_source_path)
        runner.run(duration)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    except GatewayError as exc:
        raise SystemExit(f"gateway init failed: {exc}") from exc
    except ChainlinkFeedError as exc:
        raise SystemExit(f"chainlink init failed: {exc}") from exc
    except MarketBookFeedError as exc:
        raise SystemExit(f"book feed init failed: {exc}") from exc
    except PrometheusExporterError as exc:
        raise SystemExit(f"prometheus init failed: {exc}") from exc


if __name__ == "__main__":
    main()
