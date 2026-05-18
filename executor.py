#!/usr/bin/env python3
"""Execution runner for paper/live Polymarket market making."""

from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
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
from typing import Any, Collection, Dict, List, Mapping, Optional, Tuple

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
    EDGE_CHALLENGER_MARKET_REF_FIELD,
    EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD,
    EDGE_LIFECYCLE_PHASE_FIELD,
    EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD,
    EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD,
    EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD,
    EDGE_MAKER_GATE_OPEN_FIELD,
    EDGE_MAKER_PHASE_ALLOWED_FIELD,
    EDGE_MARKET_TRUTH_REQUIRED_FIELD,
    EDGE_OWNED_MARKET_REF_FIELD,
    EDGE_OWNERSHIP_DROP_REASON_FIELD,
    EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD,
    EDGE_STAGE_BUCKET_FIELD,
    EDGE_STAGE_EFFECTIVE_FIELD,
    EDGE_TAKER_GATE_OPEN_FIELD,
    EDGE_TAKER_PHASE_ALLOWED_FIELD,
    EVENT_TAKER_DECISION,
    EVENT_TAKER_WINDOW_SEMANTIC_CHECK,
    EVENT_TAKER_SUBMIT,
    EDGE_ACTIONS,
    EDGE_ACTION_MAKER,
    EDGE_ACTION_NONE,
    EDGE_ACTION_TAKER,
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPES,
    EDGE_EVAL_SCOPE_TAKER,
    TAKER_CHAINLINK_REASON,
    compute_edge_value,
    is_canonical_block_reason,
    lane_permission_surface_fields,
    legacy_stage_to_lifecycle_phase,
    lifecycle_phase_from_payload,
    lifecycle_phase_surface_fields,
    lifecycle_surface_fields,
    lineage_stage_surface_fields,
    market_truth_surface_fields,
    ownership_surface_fields,
    lineage_stage_from_payload,
    validate_edge_inputs,
    EdgeInputSnapshot,
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
from prodesk.models import BookTop, Position, book_source_is_ws, decision_input_type_from_book_source
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
from prodesk.taker_competitiveness import (
    TakerCandidate,
    TakerDecision,
    TakerCompetitivenessConfig,
    TakerCompetitivenessEngine,
    build_taker_competitiveness_policy,
)
from prodesk.telemetry import Telemetry
from prodesk.time_sync import capture_host_time_sync_snapshot
from prodesk.tx_manager import TransactionManager
from prodesk.volatility import RealizedVolTracker
from prodesk.wallet_doctrine import create_wallet_doctrine


LOG = logging.getLogger("executor")

STAGE_OBSERVE = "OBSERVE"
STAGE_EVALUATE = "EVALUATE"
STAGE_MAKER_POSITION = "MAKER_POSITION"
STAGE_MAKER_TAKER_SELECTIVE = "MAKER_TAKER_SELECTIVE"
STAGE_SNIPER_PRIMARY = "SNIPER_PRIMARY"
STAGE_LATE_DIAGNOSTIC = "LATE_DIAGNOSTIC"
STAGE_MAKER_LATE_WINDOW = "MAKER_LATE_WINDOW"
STAGE_TAKER_COMMITMENT = "TAKER_COMMITMENT"
STAGE_EXTREME_ONLY = "EXTREME_ONLY"
STAGE_EXPIRED = "EXPIRED"
STAGE_UNKNOWN = "UNKNOWN"
CANONICAL_LIVE_TAKER_STAGE_NAMES = frozenset(
    stage_name
    for stage_name in (STAGE_TAKER_COMMITMENT,)
)
HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE = "preexpiry_ws_missing_or_unusable"
HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED = "postexpiry_market_retired"
HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP = "unknown_data_gap"
FINANCIAL_POSTURE_NORMAL = "NORMAL"
FINANCIAL_POSTURE_PREEXPIRY_REDUCE_ONLY = "PREEXPIRY_REDUCE_ONLY"
FINANCIAL_POSTURE_HARD_DEGRADED_REDUCE_ONLY = "HARD_DEGRADED_REDUCE_ONLY"
FINANCIAL_POSTURE_HALT_NEW_RISK = "HALT_NEW_RISK"
MAKER_PAIRED_TOUCH_MAX_DELTA_SEC = 0.10
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
EXECUTION_RUNTIME_EXCEPTIONS = (
    MarketBookFeedError,
    ChainlinkFeedError,
    GatewayError,
    OSError,
    TimeoutError,
    ConnectionError,
    RuntimeError,
    TypeError,
    ValueError,
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
            except ValueError as exc:
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
        proc_times = os.times()
        self._last_process_cpu_total_sec = max(
            0.0,
            float(getattr(proc_times, "user", 0.0)) + float(getattr(proc_times, "system", 0.0)),
        )
        self._last_process_cpu_sample_mono = time.monotonic()
        self._resource_metrics_error_log_interval_sec = 60.0
        self._resource_metrics_last_error_log_mono = 0.0
        self.token_ids = [str(x) for x in self.cfg["targets"]["token_ids"]]
        self._challenger_token_ids: List[str] = []
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
        self._runtime_lifecycle_phase = "scan"
        self._runtime_active_targets_present = bool(self.token_ids)
        self._runtime_promotion_eligibility_hint = False
        self._runtime_owned_market_ref: Optional[str] = None
        self._runtime_challenger_market_ref: Optional[str] = None
        self._runtime_market_truth_required = bool(self.token_ids)
        self._runtime_maker_phase_allowed = False
        self._runtime_taker_phase_allowed = False
        self._runtime_maker_gate_open = False
        self._runtime_taker_gate_open = False
        self._runtime_ownership_drop_reason: Optional[str] = None
        self._runtime_ownership_replacement_reason: Optional[str] = None
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
        self._dust_classifier_cfg = ExposureClassifierConfig(
            dust_shares_epsilon=float(self.position_dust_shares_epsilon),
            dust_notional_usd_epsilon=float(self.position_dust_notional_usd_epsilon),
            dust_total_notional_usd_cap=float(self.position_dust_total_notional_usd_cap),
            dust_token_count_cap=int(self.position_dust_token_count_cap),
            dust_max_age_sec=float(self.position_dust_max_age_sec),
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
        self._held_unpriceable_defect_candidate: bool = False
        self._held_unpriceable_operator_action: str = "none"
        self._held_unpriceable_non_defect_token_ids: List[str] = []
        self._held_unpriceable_meaningful_escalation_token_ids: List[str] = []
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
        self._last_discovery_candidate_pairs_token_ids: List[List[str]] = []
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
        configured_held_ws_missing_refresh_interval = parse_float(
            runtime_cfg.get("held_ws_missing_or_unusable_refresh_interval_sec")
        )
        self.held_ws_missing_or_unusable_refresh_interval_sec = (
            max(0.0, float(configured_held_ws_missing_refresh_interval))
            if configured_held_ws_missing_refresh_interval is not None
            else 120.0
        )
        configured_held_ws_missing_refresh_min_unpriceable_age = parse_float(
            runtime_cfg.get("held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec")
        )
        self.held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec = (
            max(0.0, float(configured_held_ws_missing_refresh_min_unpriceable_age))
            if configured_held_ws_missing_refresh_min_unpriceable_age is not None
            else 30.0
        )
        configured_expiry_boundary_epsilon_sec = parse_float(
            runtime_cfg.get("expiry_boundary_epsilon_sec")
        )
        self.expiry_boundary_epsilon_sec = (
            max(0.0, float(configured_expiry_boundary_epsilon_sec))
            if configured_expiry_boundary_epsilon_sec is not None
            else 1.0
        )
        self.require_lifecycle_context_for_decisions = bool(
            runtime_cfg.get("require_lifecycle_context_for_decisions", True)
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
        self._preexpiry_ws_missing_or_unusable_anomaly_count: int = 0
        self._preexpiry_ws_missing_or_unusable_anomaly_last_cycle: bool = False
        self._lifecycle_context_mismatch_count: int = 0
        self._lifecycle_context_missing_sec_to_expiry_count: int = 0
        self._held_exposure_class_by_token: Dict[str, str] = {}
        self._held_exposure_detail_by_token: Dict[str, Dict[str, Any]] = {}
        self._held_dust_token_ids: List[str] = []
        self._held_dust_quarantined_token_ids: List[str] = []
        self._held_dust_total_notional_upper_bound_usd: float = 0.0
        self._held_dust_effective_hard_degraded_exempt_count: int = 0
        self._held_dust_raw_hard_degraded_token_count: int = 0
        self._held_unpriceable_operator_action_last_emit_mono: float = 0.0
        self._held_ws_missing_or_unusable_refresh_next_mono_by_token: Dict[str, float] = {}
        self._held_unpriceable_since_mono_by_token: Dict[str, float] = {}

        self.token_expiry_utc_by_token: Dict[str, str] = {}
        self.token_expiry_dt_by_token: Dict[str, dt.datetime] = {}
        self._apply_token_expiry_map(
            self.cfg.get("targets", {}).get("token_expiry_utc_by_token", {}),
            source="config",
        )
        self.token_open_anchor_utc_by_token: Dict[str, str] = {}
        self.token_open_anchor_dt_by_token: Dict[str, dt.datetime] = {}
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
            doctrine_cfg.get("oracle_max_tick_age_sec", self.cfg.get("taker", {}).get("max_chainlink_tick_age_sec", 1.5))
        )
        self._maker_paired_touch_max_delta_sec = float(MAKER_PAIRED_TOUCH_MAX_DELTA_SEC)
        self._maker_last_ws_bid_quote_by_token: Dict[str, Dict[str, Any]] = {}
        self._maker_last_ws_ask_quote_by_token: Dict[str, Dict[str, Any]] = {}
        self._maker_market_reference_policy: Dict[str, Any] = {
            "direct_mode": "direct_midpoint",
            "paired_fallback_mode": "backfilled_paired_touch",
            "bounded_fallback_mode": "disabled",
            "paired_touch_max_delta_sec": float(self._maker_paired_touch_max_delta_sec),
            "activation_requires": [
                "doctrine_mode_canonical",
                "evaluation_scope_maker",
                "book_source_ws",
                "midpoint_missing",
                "recent_complementary_ws_side_within_delta",
                "maker_prereq_pass",
            ],
            "paired_fallback_claim_class": "authoritative",
            "fallback_claim_class": "disabled",
            "missing_fallback_behavior": "fail_closed_market_probability_missing",
        }
        self._taker_market_reference_policy: Dict[str, Any] = {
            "direct_mode": "direct_midpoint",
            "paired_fallback_mode": "disabled",
            "bounded_fallback_mode": "disabled",
            "activation_requires": [
                "doctrine_mode_canonical",
                "evaluation_scope_taker",
                "book_source_ws",
                "midpoint_present",
            ],
            "direct_claim_class": "authoritative",
            "fallback_claim_class": "disabled",
            "missing_claim_class": "not_available",
            "missing_fallback_behavior": "fail_closed_market_probability_missing",
        }
        preflight_cfg = self.cfg.get("preflight", {}) if isinstance(self.cfg.get("preflight"), dict) else {}
        time_policy_cfg = self.cfg.get("time_policy", {}) if isinstance(self.cfg.get("time_policy"), dict) else {}
        self._time_policy: Dict[str, Any] = {
            "source_of_truth": "utc_wall_clock",
            "fallback_logic": "source_ts_utc_then_ts_receive_utc_then_ts_event_utc",
            "skew_tolerance_ms": max(0.0, float(time_policy_cfg.get("skew_tolerance_ms", 120.0) or 0.0)),
            "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
        }
        self._host_time_sync_refresh_interval_sec = 60.0
        self._host_time_sync_last_refresh_mono = 0.0
        self._host_time_sync_snapshot: Dict[str, Any] = {}
        self._last_host_time_sync_signature: Optional[Tuple[Any, ...]] = None
        self._refresh_host_time_sync_snapshot(force=True)
        self.doctrine_min_observe_cycles_on_entry = max(0, int(doctrine_cfg.get("min_observe_cycles_on_entry", 2)))
        self.doctrine_min_observe_seconds_on_entry = max(
            0.0, float(doctrine_cfg.get("min_observe_seconds_on_entry", 2.0))
        )
        self._doctrine_cycle_index = 0
        self._market_entry_mono_by_token: Dict[str, float] = {}
        self._market_entry_cycle_by_token: Dict[str, int] = {}
        self._last_doctrine_signature_by_token: Dict[str, Tuple[str, str, str, bool, bool]] = {}
        self._last_doctrine_prereq_failure_by_token: Dict[str, str] = {}
        self._last_lifecycle_phase_by_token: Dict[str, str] = {}
        self._last_degraded_expiry_fallback_active = False

        taker_cfg = self.cfg.get("taker", {})
        self.taker_arming_horizon_sec = float(taker_cfg.get("arming_horizon_sec", 20.0))
        self.taker_execution_cutoff_sec = float(taker_cfg.get("execution_cutoff_sec", 15.0))
        self.taker_late_fire_priority_band_sec = float(
            taker_cfg.get("late_fire_priority_band_sec", min(self.taker_execution_cutoff_sec, 5.0))
        )
        self.taker_allow_without_expiry_metadata = bool(taker_cfg.get("allow_without_expiry_metadata", False))
        self.taker_poll_interval_sec = float(taker_cfg.get("poll_interval_sec", 0.2))
        self.taker_max_actions_per_cycle = int(taker_cfg.get("max_actions_per_cycle", 16))
        self.taker_cancel_stale_action_budget = int(taker_cfg.get("cancel_stale_action_budget", 6))
        self.taker_cancel_orphan_action_budget = int(taker_cfg.get("cancel_orphan_action_budget", 12))
        self.taker_order_rate_soft_limit_pct = float(taker_cfg.get("order_rate_soft_limit_pct", 1.0))
        self.taker_cancel_rate_soft_limit_pct = float(taker_cfg.get("cancel_rate_soft_limit_pct", 1.0))
        self.taker_require_lag_verification = bool(taker_cfg.get("require_lag_verification", True))
        self.taker_max_chainlink_tick_age_sec = float(taker_cfg.get("max_chainlink_tick_age_sec", 1.5))
        self.taker_fair_vol_scale = float(taker_cfg.get("fair_vol_scale", 1.0))
        self.chainlink_latency_sample_mid_move_min_delta = float(
            self.cfg.get("chainlink", {}).get(
                "latency_sample_mid_move_min_delta",
                self.cfg.get("chainlink", {}).get("mid_move_min_delta", 0.001),
            )
        )
        verifier_cfg = dict(self.cfg.get("latency_verifier", {}))
        if "window_samples" not in verifier_cfg:
            verifier_cfg["window_samples"] = int(taker_cfg.get("lag_window_samples", 300))
        if "min_samples" not in verifier_cfg:
            verifier_cfg["min_samples"] = int(taker_cfg.get("lag_min_samples", 80))
        if "hit_threshold_ms" not in verifier_cfg:
            verifier_cfg["hit_threshold_ms"] = float(taker_cfg.get("lag_hit_threshold_ms", 120.0))
        if "armed_min_median_ms" not in verifier_cfg:
            verifier_cfg["armed_min_median_ms"] = float(taker_cfg.get("lag_min_median_ms", 120.0))
        if "armed_min_hit_rate" not in verifier_cfg:
            verifier_cfg["armed_min_hit_rate"] = float(taker_cfg.get("lag_min_hit_rate", 0.6))
        self.latency_verifier = LatencyVerifier(verifier_cfg)
        self.taker_enabled = bool(taker_cfg.get("enabled", False))
        self.taker_min_edge = float(taker_cfg.get("min_edge", 0.015))
        self.taker_extreme_edge_mult = float(taker_cfg.get("extreme_edge_mult", 2.0))
        self.taker_order_size = float(taker_cfg.get("order_size", 20.0))
        self.sizing_mode = str(self.cfg.get("sizing", {}).get("mode", "shares")).strip().lower()
        self.taker_target_usd = float(
            taker_cfg.get("target_usd", self.cfg.get("sizing", {}).get("target_usd", 5.0))
        )
        self._active_target_usd = float(self.cfg.get("sizing", {}).get("target_usd", self.taker_target_usd))
        ramp_cfg = self.cfg.get("ramp", {})
        self.ramp = SizeRampController(ramp_cfg, base_target_usd=self._active_target_usd)
        self._active_target_usd = float(self.ramp.target_usd)
        self._taker_ramp_allowed = bool(self.ramp.taker_allowed)
        reconcile_status_path_raw = str(ramp_cfg.get("reconcile_status_path", "")).strip()
        if reconcile_status_path_raw:
            self.ramp_reconcile_status_path: Optional[pathlib.Path] = pathlib.Path(reconcile_status_path_raw).resolve()
        else:
            self.ramp_reconcile_status_path = self.log_dir / "reconcile_latest.json"
        self._reconcile_status_poll_interval_sec = 15.0
        self._last_reconcile_status_poll_mono = 0.0
        self._cached_reconcile_mismatch_ratio = 0.0
        self.taker_max_orders_per_cycle = int(taker_cfg.get("max_orders_per_cycle", 2))
        self.taker_per_token_cooldown_sec = float(taker_cfg.get("per_token_cooldown_sec", 0.25))
        lifecycle_cfg = self.cfg.get("lifecycle", {})
        if not isinstance(lifecycle_cfg, dict):
            lifecycle_cfg = {}
        lifecycle_phase_cfg = lifecycle_cfg.get("phase", {})
        if not isinstance(lifecycle_phase_cfg, dict):
            lifecycle_phase_cfg = {}
        lifecycle_lane_gates_cfg = lifecycle_cfg.get("lane_gates", {})
        if not isinstance(lifecycle_lane_gates_cfg, dict):
            lifecycle_lane_gates_cfg = {}
        lifecycle_maker_lane_cfg = lifecycle_lane_gates_cfg.get("maker", {})
        if not isinstance(lifecycle_maker_lane_cfg, dict):
            lifecycle_maker_lane_cfg = {}
        lifecycle_taker_lane_cfg = lifecycle_lane_gates_cfg.get("taker", {})
        if not isinstance(lifecycle_taker_lane_cfg, dict):
            lifecycle_taker_lane_cfg = {}
        self.lifecycle_maker_window_open_sec = float(
            lifecycle_phase_cfg.get("maker_window_open_sec", 15.0)
        )
        self.lifecycle_taker_window_open_sec = float(
            lifecycle_phase_cfg.get("taker_window_open_sec", 7.0)
        )
        taker_competitiveness_cfg = (
            dict(taker_cfg.get("competitiveness", {}))
            if isinstance(taker_cfg.get("competitiveness", {}), dict)
            else {}
        )
        taker_competitiveness_cfg["final_window_enabled"] = bool(
            lifecycle_taker_lane_cfg.get(
                "final_window_enabled",
                taker_competitiveness_cfg.get("final_window_enabled", True),
            )
        )
        taker_competitiveness_cfg["final_window_sec"] = float(self.lifecycle_taker_window_open_sec)
        taker_competitiveness_cfg["aggressive_window_enabled"] = bool(
            lifecycle_taker_lane_cfg.get(
                "aggressive_window_enabled",
                taker_competitiveness_cfg.get("aggressive_window_enabled", False),
            )
        )
        taker_competitiveness_cfg["aggressive_window_sec"] = float(
            lifecycle_taker_lane_cfg.get(
                "aggressive_window_sec",
                taker_competitiveness_cfg.get("aggressive_window_sec", self.lifecycle_taker_window_open_sec),
            )
        )
        taker_competitiveness_cfg["multi_oracle_boost_enabled"] = bool(
            lifecycle_taker_lane_cfg.get(
                "multi_oracle_boost_enabled",
                taker_competitiveness_cfg.get("multi_oracle_boost_enabled", False),
            )
        )
        taker_competitiveness_cfg["multi_oracle_boost_window_sec"] = float(
            lifecycle_taker_lane_cfg.get(
                "multi_oracle_boost_window_sec",
                taker_competitiveness_cfg.get(
                    "multi_oracle_boost_window_sec",
                    self.lifecycle_taker_window_open_sec,
                ),
            )
        )
        self.taker_competitiveness_cfg = build_taker_competitiveness_policy(taker_competitiveness_cfg)
        self.taker_competitiveness_engine = TakerCompetitivenessEngine(self.taker_competitiveness_cfg)
        risk_max_order_size = self.cfg.get("risk", {}).get("max_order_size")
        self.taker_max_order_size_shares = float(risk_max_order_size or 0.0)
        self.taker_sizing_max_usd = float(self.cfg.get("sizing", {}).get("max_usd", self.taker_target_usd))
        self.taker_wallet_max_notional_per_order_usdc = float(
            self.cfg.get("wallet", {}).get("max_notional_per_order_usdc", 0.0)
        )
        multi_oracle_capital_pct_cap = float(self.taker_competitiveness_cfg.multi_oracle_capital_pct_cap)
        wallet_cfg_for_taker = self.cfg.get("wallet", {})
        if not isinstance(wallet_cfg_for_taker, dict):
            wallet_cfg_for_taker = {}
        paper_starting_usdc = parse_float(wallet_cfg_for_taker.get("paper_starting_usdc"))
        protected_usdc_reserve = parse_float(wallet_cfg_for_taker.get("protected_usdc_reserve"))
        capital_base_usd = None
        if paper_starting_usdc is not None:
            capital_base_usd = max(0.0, float(paper_starting_usdc))
            if protected_usdc_reserve is not None:
                capital_base_usd = max(0.0, capital_base_usd - max(0.0, float(protected_usdc_reserve)))
        self.taker_multi_oracle_cap_usd: Optional[float] = None
        self.taker_multi_oracle_cap_source = "disabled"
        self.taker_multi_oracle_cap_authority_class = "none"
        if multi_oracle_capital_pct_cap > 0.0 and isinstance(capital_base_usd, float) and capital_base_usd > 0.0:
            self.taker_multi_oracle_cap_usd = float(multi_oracle_capital_pct_cap * capital_base_usd)
            self.taker_multi_oracle_cap_source = "orchestration_heuristic_config_fallback"
            self.taker_multi_oracle_cap_authority_class = "derived"
        maker_comp_cfg = self.cfg.get("strategy", {}).get("maker_competitiveness", {})
        if not isinstance(maker_comp_cfg, dict):
            maker_comp_cfg = {}
        self.maker_comp_timing_gate_enabled = bool(
            lifecycle_maker_lane_cfg.get(
                "timing_gate_enabled",
                maker_comp_cfg.get("timing_gate_enabled", False),
            )
        )
        self.maker_comp_timing_gate_min_sec_to_expiry = float(self.lifecycle_taker_window_open_sec)
        self.maker_comp_timing_gate_max_sec_to_expiry = float(self.lifecycle_maker_window_open_sec)
        self.maker_comp_edge_scale_enabled = bool(
            lifecycle_maker_lane_cfg.get(
                "edge_scale_enabled",
                maker_comp_cfg.get("edge_scale_enabled", False),
            )
        )
        self.maker_comp_edge_scale_start_abs = float(
            lifecycle_maker_lane_cfg.get(
                "edge_scale_start_abs",
                maker_comp_cfg.get("edge_scale_start_abs", 0.05),
            )
        )
        self.maker_comp_edge_scale_full_abs = float(
            lifecycle_maker_lane_cfg.get(
                "edge_scale_full_abs",
                maker_comp_cfg.get("edge_scale_full_abs", 0.20),
            )
        )
        self.maker_comp_size_mult_max = float(
            lifecycle_maker_lane_cfg.get(
                "size_mult_max",
                maker_comp_cfg.get("size_mult_max", 1.35),
            )
        )
        self.maker_comp_spread_mult_min = float(
            lifecycle_maker_lane_cfg.get(
                "spread_mult_min",
                maker_comp_cfg.get("spread_mult_min", 0.75),
            )
        )
        self.maker_comp_requote_delta_mult_min = float(
            lifecycle_maker_lane_cfg.get(
                "requote_delta_mult_min",
                maker_comp_cfg.get("requote_delta_mult_min", 0.50),
            )
        )
        self.maker_comp_one_sided_enabled = bool(
            lifecycle_maker_lane_cfg.get(
                "one_sided_enabled",
                maker_comp_cfg.get("one_sided_enabled", False),
            )
        )
        self.maker_comp_one_sided_edge_threshold_abs = float(
            lifecycle_maker_lane_cfg.get(
                "one_sided_edge_threshold_abs",
                maker_comp_cfg.get("one_sided_edge_threshold_abs", 0.18),
            )
        )
        self.maker_comp_base_requote_delta = max(
            1e-9,
            float(self.cfg.get("runtime", {}).get("replace_threshold", 0.005)),
        )
        chainlink_symbols = [str(x).lower().strip() for x in self.cfg.get("chainlink", {}).get("symbols", []) if str(x).strip()]
        self.chainlink_symbol_for_targets = str(
            self.cfg.get("chainlink", {}).get("symbol_for_targets", chainlink_symbols[0] if chainlink_symbols else "")
        ).lower()
        self._taker_active = False
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
        self._taker_window_submit_lock_keys: set[str] = set()
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
        self._ws_slo_bootstrap_reason = "startup_targets_present" if self._ws_slo_bootstrap_active else ""
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
        self._refresh_taker_multi_oracle_cap_from_wallet()

        runtime_cfg_for_manager = dict(self.cfg["runtime"])
        runtime_cfg_for_manager["lifecycle"] = dict(self.cfg.get("lifecycle", {}))

        self.manager = OrderManager(
            gateway=self.gateway,
            strategy=self.strategy,
            risk=self.risk,
            events=self.events,
            telemetry=self.telemetry,
            runtime_cfg=runtime_cfg_for_manager,
            strategy_cfg=self.cfg["strategy"],
            sizing_cfg=self.cfg.get("sizing", {}),
            mode=mode,
            wallet=self.wallet,
            tx_manager=self.tx_manager,
        )
        self.manager.sizing_target_usd = float(self._active_target_usd)
        if self.sizing_mode == "notional":
            self.taker_target_usd = float(self._active_target_usd)
        raw_seen_trade_ids = state.get("seen_trade_ids", [])
        if not isinstance(raw_seen_trade_ids, list):
            raw_seen_trade_ids = []
        self.manager.restore_seen_trade_ids([str(x) for x in raw_seen_trade_ids if str(x)])
        self.manager.restore_last_fill_ts(state.get("last_fill_ts_utc"))
        self.tx_manager.seed_fill_cursor(self.manager.snapshot_last_fill_ts())

        self.stop_requested = False
        self.consecutive_failures = 0
        self.last_kill_switch_state = False

    @property
    def taker_enabled(self) -> bool:
        return bool(getattr(self, "_taker_enabled", False))

    @taker_enabled.setter
    def taker_enabled(self, value: Any) -> None:
        self._taker_enabled = bool(value)

    @property
    def taker_min_edge(self) -> float:
        return float(getattr(self, "_taker_min_edge", 0.0))

    @taker_min_edge.setter
    def taker_min_edge(self, value: Any) -> None:
        self._taker_min_edge = float(value)

    @property
    def taker_extreme_edge_mult(self) -> float:
        return float(getattr(self, "_taker_extreme_edge_mult", 0.0))

    @taker_extreme_edge_mult.setter
    def taker_extreme_edge_mult(self, value: Any) -> None:
        self._taker_extreme_edge_mult = float(value)

    @property
    def taker_order_size(self) -> float:
        return float(getattr(self, "_taker_order_size", 0.0))

    @taker_order_size.setter
    def taker_order_size(self, value: Any) -> None:
        self._taker_order_size = float(value)

    @property
    def taker_target_usd(self) -> float:
        return float(getattr(self, "_taker_target_usd", 0.0))

    @taker_target_usd.setter
    def taker_target_usd(self, value: Any) -> None:
        self._taker_target_usd = float(value)

    @property
    def taker_max_orders_per_cycle(self) -> int:
        return int(getattr(self, "_taker_max_orders_per_cycle", 0))

    @taker_max_orders_per_cycle.setter
    def taker_max_orders_per_cycle(self, value: Any) -> None:
        self._taker_max_orders_per_cycle = int(value)

    @property
    def taker_per_token_cooldown_sec(self) -> float:
        return float(getattr(self, "_taker_per_token_cooldown_sec", 0.0))

    @taker_per_token_cooldown_sec.setter
    def taker_per_token_cooldown_sec(self, value: Any) -> None:
        self._taker_per_token_cooldown_sec = float(value)

    @property
    def taker_competitiveness_cfg(self) -> TakerCompetitivenessConfig:
        return getattr(self, "_taker_competitiveness_cfg")

    @taker_competitiveness_cfg.setter
    def taker_competitiveness_cfg(self, value: TakerCompetitivenessConfig) -> None:
        self._taker_competitiveness_cfg = value

    @property
    def taker_competitiveness_engine(self) -> TakerCompetitivenessEngine:
        return getattr(self, "_taker_competitiveness_engine")

    @taker_competitiveness_engine.setter
    def taker_competitiveness_engine(self, value: TakerCompetitivenessEngine) -> None:
        self._taker_competitiveness_engine = value

    @property
    def taker_max_order_size_shares(self) -> float:
        return float(getattr(self, "_taker_max_order_size_shares", 0.0))

    @taker_max_order_size_shares.setter
    def taker_max_order_size_shares(self, value: Any) -> None:
        self._taker_max_order_size_shares = float(value)

    @property
    def taker_sizing_max_usd(self) -> float:
        return float(getattr(self, "_taker_sizing_max_usd", 0.0))

    @taker_sizing_max_usd.setter
    def taker_sizing_max_usd(self, value: Any) -> None:
        self._taker_sizing_max_usd = float(value)

    @property
    def taker_wallet_max_notional_per_order_usdc(self) -> float:
        return float(getattr(self, "_taker_wallet_max_notional_per_order_usdc", 0.0))

    @taker_wallet_max_notional_per_order_usdc.setter
    def taker_wallet_max_notional_per_order_usdc(self, value: Any) -> None:
        self._taker_wallet_max_notional_per_order_usdc = float(value)

    @property
    def taker_multi_oracle_cap_usd(self) -> Optional[float]:
        return getattr(self, "_taker_multi_oracle_cap_usd", None)

    @taker_multi_oracle_cap_usd.setter
    def taker_multi_oracle_cap_usd(self, value: Optional[float]) -> None:
        self._taker_multi_oracle_cap_usd = value

    @property
    def taker_multi_oracle_cap_source(self) -> str:
        return str(getattr(self, "_taker_multi_oracle_cap_source", "disabled"))

    @taker_multi_oracle_cap_source.setter
    def taker_multi_oracle_cap_source(self, value: Any) -> None:
        self._taker_multi_oracle_cap_source = str(value)

    @property
    def taker_multi_oracle_cap_authority_class(self) -> str:
        return str(getattr(self, "_taker_multi_oracle_cap_authority_class", "none"))

    @taker_multi_oracle_cap_authority_class.setter
    def taker_multi_oracle_cap_authority_class(self, value: Any) -> None:
        self._taker_multi_oracle_cap_authority_class = str(value)

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "positions": {},
            "seen_trade_ids": [],
            "last_fill_ts_utc": None,
            "last_status_ts_utc": None,
        }

    def _refresh_host_time_sync_snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        now_mono = time.monotonic()
        if (
            not force
            and self._host_time_sync_snapshot
            and (now_mono - self._host_time_sync_last_refresh_mono) < self._host_time_sync_refresh_interval_sec
        ):
            return dict(self._host_time_sync_snapshot)

        snapshot = capture_host_time_sync_snapshot(timeout_sec=2.0)
        if not isinstance(snapshot, dict):
            snapshot = {
                "available": False,
                "clock_state": "unknown",
                "sample_ts_utc": utc_iso(),
                "source": "timedatectl",
            }
        self._host_time_sync_snapshot = dict(snapshot)
        self._host_time_sync_last_refresh_mono = now_mono

        offset_ms = parse_float(snapshot.get("offset_ms"))
        jitter_ms = parse_float(snapshot.get("jitter_ms"))
        root_distance_ms = parse_float(snapshot.get("root_distance_ms"))
        if offset_ms is not None:
            self.telemetry.set_gauge("host_time_sync_offset_ms", float(offset_ms))
        if jitter_ms is not None:
            self.telemetry.set_gauge("host_time_sync_jitter_ms", float(jitter_ms))
        if root_distance_ms is not None:
            self.telemetry.set_gauge("host_time_sync_root_distance_ms", float(root_distance_ms))

        signature = (
            str(snapshot.get("clock_state") or ""),
            snapshot.get("system_clock_synchronized"),
            snapshot.get("ntp_service_active"),
            str(snapshot.get("server") or ""),
            round(float(offset_ms), 3) if offset_ms is not None else None,
        )
        if force or signature != self._last_host_time_sync_signature:
            self._last_host_time_sync_signature = signature
            self.events.log_event(
                "host_time_sync_snapshot",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "host_time_sync": dict(snapshot),
                },
            )
        return dict(snapshot)

    def _load_state_safe(self) -> Dict[str, Any]:
        try:
            return load_state(self.state_path)
        except (OSError, ValueError) as exc:
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

    def _discovery_primary_pair_token_ids(self, discovered_token_ids: List[str]) -> List[str]:
        pair_rows = list(getattr(self, "_last_discovery_candidate_pairs_token_ids", []) or [])
        if pair_rows:
            for pair_ids in pair_rows:
                normalized = self._unique_ordered(
                    [str(token_id).strip() for token_id in list(pair_ids or []) if str(token_id).strip()]
                )
                if len(normalized) >= 2:
                    return normalized[:2]
        normalized_discovered = self._unique_ordered(
            [str(token_id).strip() for token_id in list(discovered_token_ids or []) if str(token_id).strip()]
        )
        if len(normalized_discovered) < 2:
            return []
        return normalized_discovered[:2]

    def _lifecycle_watch_token_ids(self) -> List[str]:
        watched = set(self._non_flat_position_token_ids())
        watched.update(self._open_order_token_ids())
        return self._unique_ordered(sorted(watched))

    def _transport_watch_token_ids(self) -> List[str]:
        watched = set(str(token_id) for token_id in self.token_ids)
        watched.update(str(token_id) for token_id in self._challenger_token_ids)
        watched.update(self._lifecycle_watch_token_ids())
        return self._unique_ordered(sorted(token_id for token_id in watched if str(token_id).strip()))

    def _manager_tracked_token_ids(self) -> set[str]:
        tracked = set(str(token_id) for token_id in self.token_ids)
        tracked.update(self._lifecycle_watch_token_ids())
        return {token_id for token_id in tracked if str(token_id).strip()}

    def _retained_token_metadata_ids(self) -> set[str]:
        retained = set(str(token_id) for token_id in self.token_ids)
        retained.update(str(token_id) for token_id in self._challenger_token_ids)
        retained.update(self._lifecycle_watch_token_ids())
        return {token_id for token_id in retained if str(token_id).strip()}

    def _pair_tokens_market_valid(self, token_ids: Collection[str]) -> bool:
        normalized = self._unique_ordered([str(token_id).strip() for token_id in list(token_ids or []) if str(token_id).strip()])
        if len(normalized) < 2:
            return False
        boundary_eps = max(float(self.expiry_boundary_epsilon_sec), 1e-9)
        now = utc_now()
        for token_id in normalized:
            market_key = str(self.token_market_key_by_token.get(token_id, "")).strip()
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            if not market_key or not isinstance(expiry_dt, dt.datetime):
                return False
            if (expiry_dt - now).total_seconds() <= -boundary_eps:
                return False
        return True

    def _set_challenger_token_ids(
        self,
        new_token_ids: Collection[str],
        *,
        reason: str,
        event_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = self._unique_ordered([str(token_id).strip() for token_id in list(new_token_ids or []) if str(token_id).strip()])
        old = list(self._challenger_token_ids)
        if normalized == old:
            return
        self._challenger_token_ids = list(normalized)
        payload: Dict[str, Any] = {
            "ts_utc": utc_iso(),
            "run_id": self.run_id,
            "reason": str(reason or "").strip(),
            "old_token_count": len(old),
            "old_token_ids": list(old),
            "new_token_count": len(normalized),
            "new_token_ids": list(normalized),
        }
        if isinstance(event_extra, dict):
            payload.update(event_extra)
        self.events.log_event("challenger_targets_updated", payload)

    def _set_authoritative_active_token_ids(
        self,
        new_token_ids: Collection[str],
        *,
        reason: str,
        apply_ws_slo_grace: bool,
        event_type: str = "targets_updated",
        event_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = self._unique_ordered([str(token_id).strip() for token_id in list(new_token_ids or []) if str(token_id).strip()])
        old = list(self.token_ids)
        if normalized == old:
            return
        self.token_ids = list(normalized)
        self._reset_ws_slo_bootstrap(
            reason=str(reason or "").strip() or "targets_updated",
            activate_grace=bool(apply_ws_slo_grace),
        )
        payload: Dict[str, Any] = {
            "ts_utc": utc_iso(),
            "run_id": self.run_id,
            "reason": str(reason or "").strip(),
            "old_token_count": len(old),
            "old_token_ids": list(old),
            "new_token_count": len(self.token_ids),
            "new_token_ids": list(self.token_ids),
            "challenger_token_count": len(self._challenger_token_ids),
            "challenger_token_ids": list(self._challenger_token_ids),
            "ws_slo_grace_applied": bool(apply_ws_slo_grace),
        }
        if isinstance(event_extra, dict):
            payload.update(event_extra)
        self.events.log_event(event_type, payload)

    def _reconcile_pair_authority(self) -> None:
        if self.token_ids and not self._pair_tokens_market_valid(self.token_ids):
            self._set_authoritative_active_token_ids(
                [],
                reason="owned_market_invalidated",
                apply_ws_slo_grace=False,
                event_type="owned_market_invalidated",
            )
            self._refresh_targets(force=True)
            return
        if self._challenger_token_ids and not self._pair_tokens_market_valid(self._challenger_token_ids):
            self._set_challenger_token_ids(
                [],
                reason="challenger_pair_invalidated",
            )
            self._refresh_targets(force=True)
            return
        if (not self.token_ids) and self._challenger_token_ids and self._pair_tokens_market_valid(
            self._challenger_token_ids
        ):
            promoted = list(self._challenger_token_ids)
            self._set_challenger_token_ids([], reason="challenger_promoted_to_owned_market")
            self._set_authoritative_active_token_ids(
                promoted,
                reason="owned_market_promoted_from_challenger",
                apply_ws_slo_grace=False,
                event_type="owned_market_promoted",
            )

    def _update_runtime_semantics(self, *, has_targets: bool) -> None:
        info_by_token = {
            token_id: self._token_lifecycle_info(token_id)
            for token_id in self.token_ids
            if str(token_id).strip()
        }
        if bool(self.risk.kill_switch) and info_by_token:
            lifecycle_phase = "resolve"
        elif not info_by_token:
            lifecycle_phase = "scan"
        elif any(str(info.get(EDGE_LIFECYCLE_PHASE_FIELD) or "") == "resolve" for info in info_by_token.values()):
            lifecycle_phase = "resolve"
        elif any(str(info.get(EDGE_LIFECYCLE_PHASE_FIELD) or "") == "taker_window" for info in info_by_token.values()):
            lifecycle_phase = "taker_window"
        elif any(str(info.get(EDGE_LIFECYCLE_PHASE_FIELD) or "") == "maker_window" for info in info_by_token.values()):
            lifecycle_phase = "maker_window"
        else:
            lifecycle_phase = "prepare"
        owned_market_ref = self._market_ref_for_token_ids(self.token_ids)
        challenger_market_ref = self._market_ref_for_token_ids(self._challenger_token_ids)
        market_truth_required = bool(info_by_token)
        maker_phase_allowed = any(bool(info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, False)) for info in info_by_token.values())
        taker_phase_allowed = any(bool(info.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, False)) for info in info_by_token.values())
        maker_gate_open = any(bool(info.get(EDGE_MAKER_GATE_OPEN_FIELD, False)) for info in info_by_token.values())
        taker_gate_open = any(bool(info.get(EDGE_TAKER_GATE_OPEN_FIELD, False)) for info in info_by_token.values())
        prev_state = self._runtime_state
        prev_market_truth_required = self._runtime_market_truth_required
        self._runtime_state = lifecycle_phase
        self._runtime_lifecycle_phase = lifecycle_phase
        self._runtime_active_targets_present = bool(info_by_token)
        self._runtime_promotion_eligibility_hint = bool(
            info_by_token and not bool(self.risk.kill_switch) and lifecycle_phase != "resolve"
        )
        self._runtime_owned_market_ref = owned_market_ref
        self._runtime_challenger_market_ref = challenger_market_ref
        self._runtime_market_truth_required = bool(market_truth_required)
        self._runtime_maker_phase_allowed = bool(maker_phase_allowed)
        self._runtime_taker_phase_allowed = bool(taker_phase_allowed)
        self._runtime_maker_gate_open = bool(maker_gate_open)
        self._runtime_taker_gate_open = bool(taker_gate_open)
        self._runtime_ownership_drop_reason = None
        self._runtime_ownership_replacement_reason = None

        self.telemetry.set_gauge("runtime_state_code", runtime_state_to_gauge(self._runtime_state))
        self.telemetry.set_gauge("active_targets_present", 1.0 if self._runtime_active_targets_present else 0.0)
        self.telemetry.set_gauge("market_truth_required", 1.0 if self._runtime_market_truth_required else 0.0)
        self.telemetry.set_gauge("maker_phase_allowed", 1.0 if self._runtime_maker_phase_allowed else 0.0)
        self.telemetry.set_gauge("taker_phase_allowed", 1.0 if self._runtime_taker_phase_allowed else 0.0)
        self.telemetry.set_gauge("maker_gate_open", 1.0 if self._runtime_maker_gate_open else 0.0)
        self.telemetry.set_gauge("taker_gate_open", 1.0 if self._runtime_taker_gate_open else 0.0)
        self.telemetry.set_gauge(
            "promotion_eligibility_hint",
            1.0 if self._runtime_promotion_eligibility_hint else 0.0,
        )

        if self._runtime_state != prev_state or self._runtime_market_truth_required != prev_market_truth_required:
            transition_reason_code = "runtime_state_changed"
            if self._runtime_state == prev_state and self._runtime_market_truth_required != prev_market_truth_required:
                transition_reason_code = "market_truth_requirement_changed"
            elif bool(self.risk.kill_switch):
                transition_reason_code = "kill_switch_engaged"
            elif self._runtime_state == "scan":
                transition_reason_code = "owned_market_absent"
            elif self._runtime_state == "prepare":
                transition_reason_code = "owned_market_prepare"
            elif self._runtime_state == "maker_window":
                transition_reason_code = "maker_window_open"
            elif self._runtime_state == "taker_window":
                transition_reason_code = "taker_window_open"
            elif self._runtime_state == "resolve":
                transition_reason_code = "resolve_required"
            transition_reason_detail = (
                f"prev_state={prev_state};new_state={self._runtime_state};"
                f"previous_market_truth_required={int(bool(prev_market_truth_required))};"
                f"market_truth_required={int(bool(self._runtime_market_truth_required))};"
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
                    "lifecycle_phase": self._runtime_lifecycle_phase,
                    "active_targets_present": self._runtime_active_targets_present,
                    "scan_phase": self._runtime_lifecycle_phase == "scan",
                    "previous_market_truth_required": bool(prev_market_truth_required),
                    "market_truth_required": self._runtime_market_truth_required,
                    "owned_market_ref": self._runtime_owned_market_ref,
                    "challenger_market_ref": self._runtime_challenger_market_ref,
                    "maker_phase_allowed": self._runtime_maker_phase_allowed,
                    "taker_phase_allowed": self._runtime_taker_phase_allowed,
                    "maker_gate_open": self._runtime_maker_gate_open,
                    "taker_gate_open": self._runtime_taker_gate_open,
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

    def _apply_token_open_anchor_map(self, raw_map: Any, *, source: str) -> int:
        if not isinstance(raw_map, dict):
            return 0
        applied = 0
        for token_id_raw, anchor_raw in raw_map.items():
            token_id = str(token_id_raw).strip()
            anchor_dt = parse_ts(anchor_raw)
            if not token_id or anchor_dt is None:
                continue
            self.token_open_anchor_utc_by_token[token_id] = utc_iso(anchor_dt)
            self.token_open_anchor_dt_by_token[token_id] = anchor_dt
            applied += 1
        if applied:
            self.events.log_event(
                "token_open_anchor_map_update",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "source": source,
                    "applied_count": applied,
                },
            )
        return applied

    @staticmethod
    def _strike_looks_like_epoch_near_expiry(*, strike: float, expiry_dt: Optional[dt.datetime]) -> bool:
        parsed = parse_float(strike)
        if parsed is None or parsed < 1_000_000_000.0:
            return False
        if not isinstance(expiry_dt, dt.datetime):
            return False
        return abs(float(parsed) - float(expiry_dt.timestamp())) <= 86400.0

    def _apply_token_strike_map(self, raw_map: Any, *, source: str) -> int:
        if not isinstance(raw_map, dict):
            return 0
        applied = 0
        for token_id_raw, strike_raw in raw_map.items():
            token_id = str(token_id_raw).strip()
            strike = parse_float(strike_raw)
            if not token_id or strike is None or strike <= 0:
                continue
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            if self._strike_looks_like_epoch_near_expiry(strike=float(strike), expiry_dt=expiry_dt):
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

    def _token_price_anchor(self, token_id: str) -> Optional[float]:
        token = str(token_id or "").strip()
        if not token:
            return None
        strike = self.token_strike_by_token.get(token)
        expiry_dt = self.token_expiry_dt_by_token.get(token)
        if isinstance(strike, (int, float)):
            strike_value = float(strike)
            if not self._strike_looks_like_epoch_near_expiry(strike=strike_value, expiry_dt=expiry_dt):
                return strike_value
        anchor_dt = self.token_open_anchor_dt_by_token.get(token)
        symbol_for_targets = str(self.chainlink_symbol_for_targets or "").strip()
        if not isinstance(anchor_dt, dt.datetime) or not symbol_for_targets:
            return None
        anchor_tick = self.chainlink.get_first_at_or_after(symbol_for_targets, utc_iso(anchor_dt))
        if anchor_tick is None:
            return None
        anchor_source_dt = parse_ts(anchor_tick.source_ts_utc)
        if anchor_source_dt is None or anchor_source_dt < anchor_dt:
            return None
        return float(anchor_tick.price)

    def _fair_probability_up(self, *, spot: float, strike: float, sec_to_expiry: float) -> float:
        # Smooth logistic mapping of spot-vs-strike to up probability.
        t = max(1.0, sec_to_expiry)
        width = max(20.0, self.taker_fair_vol_scale * 90.0 * (t / 300.0) ** 0.5)
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
    def _read_proc_meminfo_kb() -> Dict[str, float]:
        path = pathlib.Path("/proc/meminfo")
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return {}
        out: Dict[str, float] = {}
        for line in lines:
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            tokens = str(raw).strip().split()
            if not tokens:
                continue
            try:
                value = float(tokens[0])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                out[str(key).strip()] = value
        return out

    @staticmethod
    def _runtime_resource_snapshot_from_telemetry(telemetry: Dict[str, Any]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        metric_names = (
            "process_cpu_percent",
            "process_cpu_percent_normalized",
            "process_rss_mb",
            "system_load1",
            "system_load5",
            "system_load15",
            "system_mem_total_mb",
            "system_mem_available_mb",
            "system_mem_available_ratio",
            "system_swap_total_mb",
            "system_swap_used_mb",
            "system_swap_used_ratio",
        )
        for name in metric_names:
            raw = telemetry.get(f"gauge.{name}")
            value = parse_float(raw)
            if value is None:
                continue
            if not math.isfinite(float(value)):
                continue
            out[name] = float(value)
        return out

    def _sample_runtime_resource_metrics(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        now_mono = time.monotonic()
        proc_times = os.times()
        proc_total_cpu_sec = max(
            0.0,
            float(getattr(proc_times, "user", 0.0)) + float(getattr(proc_times, "system", 0.0)),
        )
        elapsed = max(1e-6, now_mono - float(self._last_process_cpu_sample_mono))
        cpu_delta = max(0.0, proc_total_cpu_sec - float(self._last_process_cpu_total_sec))
        process_cpu_percent = max(0.0, min(10000.0, (cpu_delta / elapsed) * 100.0))
        cpu_count = max(1.0, float(os.cpu_count() or 1.0))
        process_cpu_percent_normalized = max(0.0, min(100.0, process_cpu_percent / cpu_count))
        out["process_cpu_percent"] = float(process_cpu_percent)
        out["process_cpu_percent_normalized"] = float(process_cpu_percent_normalized)
        self._last_process_cpu_sample_mono = now_mono
        self._last_process_cpu_total_sec = proc_total_cpu_sec

        try:
            load1, load5, load15 = os.getloadavg()
            out["system_load1"] = max(0.0, float(load1))
            out["system_load5"] = max(0.0, float(load5))
            out["system_load15"] = max(0.0, float(load15))
        except OSError as exc:
            now = time.monotonic()
            if (now - self._resource_metrics_last_error_log_mono) >= self._resource_metrics_error_log_interval_sec:
                self._resource_metrics_last_error_log_mono = now
                self.events.log_error(
                    {
                        "ts_utc": utc_iso(),
                        "component": "runtime_resource",
                        "action": "getloadavg",
                        "error": str(exc),
                    }
                )

        meminfo_kb = self._read_proc_meminfo_kb()
        mem_total_kb = max(0.0, float(meminfo_kb.get("MemTotal", 0.0)))
        mem_available_kb = max(
            0.0,
            float(meminfo_kb.get("MemAvailable", meminfo_kb.get("MemFree", 0.0))),
        )
        swap_total_kb = max(0.0, float(meminfo_kb.get("SwapTotal", 0.0)))
        swap_free_kb = max(0.0, float(meminfo_kb.get("SwapFree", 0.0)))
        swap_used_kb = max(0.0, swap_total_kb - swap_free_kb)
        if mem_total_kb > 0.0:
            out["system_mem_total_mb"] = float(mem_total_kb / 1024.0)
            out["system_mem_available_mb"] = float(mem_available_kb / 1024.0)
            out["system_mem_available_ratio"] = float(min(1.0, max(0.0, mem_available_kb / mem_total_kb)))
        if swap_total_kb > 0.0:
            out["system_swap_total_mb"] = float(swap_total_kb / 1024.0)
            out["system_swap_used_mb"] = float(swap_used_kb / 1024.0)
            out["system_swap_used_ratio"] = float(min(1.0, max(0.0, swap_used_kb / swap_total_kb)))
        elif "SwapTotal" in meminfo_kb:
            out["system_swap_total_mb"] = 0.0
            out["system_swap_used_mb"] = 0.0
            out["system_swap_used_ratio"] = 0.0

        return out

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

    def _resolve_ws_truth_for_token(
        self,
        *,
        token_id: str,
        top: Any,
    ) -> Dict[str, Any]:
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id or top is None or not self._book_source_is_ws(top):
            return {
                "truth_class": "missing",
                "truth_basis": "missing",
                "resolved_top": top,
            }
        if isinstance(getattr(top, "midpoint", None), (int, float)):
            return {
                "truth_class": "authoritative",
                "truth_basis": "direct_midpoint",
                "resolved_top": top,
            }
        paired = self._resolve_maker_paired_touch_reference(
            token_id=normalized_token_id,
            top=top,
            maker_prereq_failure_reason="",
        )
        if paired is not None:
            resolved_top, _ = paired
            return {
                "truth_class": "authoritative",
                "truth_basis": "backfilled_paired_touch",
                "resolved_top": resolved_top,
            }
        has_bid = isinstance(getattr(top, "best_bid_price", None), (int, float))
        has_ask = isinstance(getattr(top, "best_ask_price", None), (int, float))
        if has_bid ^ has_ask:
            return {
                "truth_class": "missing",
                "truth_basis": "one_sided_ws_missing_midpoint",
                "resolved_top": top,
            }
        return {
            "truth_class": "missing",
            "truth_basis": "missing",
            "resolved_top": top,
        }

    def _build_pair_truth_map(
        self,
        *,
        books: Dict[str, Any],
        token_ids: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        pair_truth_by_base_key: Dict[str, Dict[str, Any]] = {}
        for raw_token_id in token_ids:
            token_id = str(raw_token_id or "").strip()
            if not token_id:
                continue
            market_key = str(self.token_market_key_by_token.get(token_id, "")).strip()
            base_key = self._market_base_key_from_market_key(market_key) or token_id
            side = str(self.token_side_by_token.get(token_id, "")).strip().upper() or "UNKNOWN"
            truth = self._resolve_ws_truth_for_token(token_id=token_id, top=books.get(token_id))
            pair_entry = pair_truth_by_base_key.setdefault(
                base_key,
                {
                    "market_base_key": base_key,
                    "pair_truth_class": "missing",
                    "pair_truth_basis": "pair_missing_all_sides",
                    "pair_truth_owner_scope": "market_base_pair",
                    "pair_missing_token_count": 0,
                    "pair_one_sided_token_count": 0,
                    "pair_authoritative_token_count": 0,
                    "token_truth_by_token": {},
                    "token_truth_by_side": {},
                },
            )
            token_truth = {
                "token_id": token_id,
                "token_side": side,
                "pair_truth_class": str(truth.get("truth_class") or "missing"),
                "pair_truth_basis": str(truth.get("truth_basis") or "missing"),
            }
            pair_entry["token_truth_by_token"][token_id] = token_truth
            pair_entry["token_truth_by_side"][side] = token_truth
        for pair_entry in pair_truth_by_base_key.values():
            token_truths = list(dict(pair_entry.get("token_truth_by_token") or {}).values())
            missing_count = sum(
                1 for truth in token_truths if str(truth.get("pair_truth_class") or "").strip().lower() == "missing"
            )
            one_sided_count = sum(
                1
                for truth in token_truths
                if str(truth.get("pair_truth_basis") or "").strip().lower() == "one_sided_ws_missing_midpoint"
            )
            authoritative_count = sum(
                1
                for truth in token_truths
                if str(truth.get("pair_truth_class") or "").strip().lower() == "authoritative"
            )
            pair_entry["pair_missing_token_count"] = int(missing_count)
            pair_entry["pair_one_sided_token_count"] = int(one_sided_count)
            pair_entry["pair_authoritative_token_count"] = int(authoritative_count)
            if authoritative_count > 0:
                pair_entry["pair_truth_class"] = "authoritative"
                pair_entry["pair_truth_basis"] = "pair_has_authoritative_side"
            elif one_sided_count > 0:
                pair_entry["pair_truth_class"] = "missing"
                pair_entry["pair_truth_basis"] = "pair_missing_one_sided_only"
            else:
                pair_entry["pair_truth_class"] = "missing"
                pair_entry["pair_truth_basis"] = "pair_missing_all_sides"
        return pair_truth_by_base_key

    @staticmethod
    def _pair_truth_for_token(
        *,
        pair_truth_by_base_key: Dict[str, Dict[str, Any]],
        market_base_key: str,
        token_id: str,
    ) -> Dict[str, Any]:
        pair_entry = dict(pair_truth_by_base_key.get(str(market_base_key or "").strip()) or {})
        token_truth = dict(pair_entry.get("token_truth_by_token", {}).get(str(token_id or "").strip()) or {})
        return {
            "pair_truth_class": str(token_truth.get("pair_truth_class") or "missing"),
            "pair_truth_basis": str(token_truth.get("pair_truth_basis") or "missing"),
            "pair_truth_owner_scope": str(pair_entry.get("pair_truth_owner_scope") or "market_base_pair"),
            "pair_missing_token_count": int(pair_entry.get("pair_missing_token_count") or 0),
            "pair_one_sided_token_count": int(pair_entry.get("pair_one_sided_token_count") or 0),
            "pair_authoritative_token_count": int(pair_entry.get("pair_authoritative_token_count") or 0),
        }

    @staticmethod
    def _pair_truth_base_keys_by_class(
        pair_truth_by_base_key: Dict[str, Dict[str, Any]],
    ) -> Tuple[List[str], List[str]]:
        missing_base_keys: List[str] = []
        one_sided_base_keys: List[str] = []
        for base_key, pair_truth in pair_truth_by_base_key.items():
            truth_class = str(pair_truth.get("pair_truth_class") or "").strip().lower()
            truth_basis = str(pair_truth.get("pair_truth_basis") or "").strip().lower()
            normalized_base_key = str(base_key or "").strip()
            if not normalized_base_key:
                continue
            if truth_class == "missing":
                missing_base_keys.append(normalized_base_key)
            if truth_basis == "pair_missing_one_sided_only":
                one_sided_base_keys.append(normalized_base_key)
        return sorted(missing_base_keys), sorted(one_sided_base_keys)

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
        for token_id in list(self._held_ws_missing_or_unusable_refresh_next_mono_by_token.keys()):
            if token_id not in non_flat_token_set:
                self._held_ws_missing_or_unusable_refresh_next_mono_by_token.pop(token_id, None)
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
        held_unpriceable_non_defect_token_ids = sorted(
            token_id
            for token_id in held_unpriceable_escalation_token_ids
            if str(held_unpriceable_cause_by_token.get(token_id) or "") == HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED
        )
        held_unpriceable_meaningful_escalation_token_ids = sorted(
            token_id
            for token_id in held_unpriceable_escalation_token_ids
            if token_id not in set(held_unpriceable_non_defect_token_ids)
        )
        held_unpriceable_defect_candidate = bool(held_unpriceable_meaningful_escalation_token_ids)
        held_unpriceable_operator_action = (
            "review_market_data_coverage_for_held_tokens_and_keep_reduce_only_until_priceable"
            if held_unpriceable_defect_candidate
            else "none"
        )
        held_unpriceable_cause_counts_counter = collections.Counter(
            str(held_unpriceable_cause_by_token.get(token_id) or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP)
            for token_id in held_unpriceable_token_ids
        )
        held_unpriceable_cause_counts = {
            HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE: int(
                held_unpriceable_cause_counts_counter.get(
                    HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE,
                    0,
                )
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
            cause_for_token = str(
                held_unpriceable_cause_by_token.get(token_id) or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
            )
            postexpiry_retired_recent_ws_missing_or_unusable = bool(
                lifecycle_flags.get("held_ws_missing_or_unusable_tracking_active", False)
                and cause_for_token == HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED
                and isinstance(sec_to_expiry, (int, float))
                and float(sec_to_expiry) <= (-max(float(self.expiry_boundary_epsilon_sec), 1e-9))
            )
            unresolved_for_dust = bool(
                (
                    bool(lifecycle_flags.get("held_ws_missing_or_unusable_tracking_active", False))
                    and (not bool(postexpiry_retired_recent_ws_missing_or_unusable))
                )
                or (
                    bool(lifecycle_flags.get("held_ws_missing_or_unusable_refresh_pending", False))
                    and (not bool(postexpiry_retired_recent_ws_missing_or_unusable))
                )
            )
            dust_age_sec = float(held_unpriceable_age_by_token.get(token_id, 0.0) or 0.0)
            dust_age_sec_for_classification = (
                0.0 if postexpiry_retired_recent_ws_missing_or_unusable else float(dust_age_sec)
            )
            _base_classification_by_token[token_id] = classify_exposure_fail_closed(
                net_shares=float(getattr(position, "net_shares", 0.0) or 0.0),
                cfg=self._dust_classifier_cfg,
                conservative_mark_price=float(conservative_mark_by_token.get(token_id, 0.0) or 0.0),
                open_order_present=bool(lifecycle_flags.get("open_order_present", False)),
                unresolved_lifecycle_obligation=bool(unresolved_for_dust),
                dust_age_sec=float(dust_age_sec_for_classification),
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
            cause_for_token = str(
                held_unpriceable_cause_by_token.get(token_id) or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
            )
            postexpiry_retired_recent_ws_missing_or_unusable = bool(
                lifecycle_flags.get("held_ws_missing_or_unusable_tracking_active", False)
                and cause_for_token == HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED
                and isinstance(sec_to_expiry, (int, float))
                and float(sec_to_expiry) <= (-max(float(self.expiry_boundary_epsilon_sec), 1e-9))
            )
            unresolved_for_dust = bool(
                (
                    bool(lifecycle_flags.get("held_ws_missing_or_unusable_tracking_active", False))
                    and (not bool(postexpiry_retired_recent_ws_missing_or_unusable))
                )
                or (
                    bool(lifecycle_flags.get("held_ws_missing_or_unusable_refresh_pending", False))
                    and (not bool(postexpiry_retired_recent_ws_missing_or_unusable))
                )
            )
            dust_age_sec = float(held_unpriceable_age_by_token.get(token_id, 0.0) or 0.0)
            dust_age_sec_for_classification = (
                0.0 if postexpiry_retired_recent_ws_missing_or_unusable else float(dust_age_sec)
            )
            classification = classify_exposure_fail_closed(
                net_shares=float(getattr(position, "net_shares", 0.0) or 0.0),
                cfg=self._dust_classifier_cfg,
                conservative_mark_price=float(conservative_mark_by_token.get(token_id, 0.0) or 0.0),
                open_order_present=bool(lifecycle_flags.get("open_order_present", False)),
                unresolved_lifecycle_obligation=bool(unresolved_for_dust),
                dust_age_sec=float(dust_age_sec_for_classification),
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
                    "postexpiry_retired_recent_ws_missing_or_unusable_dust_exempted": bool(
                        postexpiry_retired_recent_ws_missing_or_unusable
                    ),
                    "held_unpriceable_tracking_active": bool(
                        lifecycle_flags.get("held_unpriceable_tracking_active", False)
                    ),
                    "dust_age_sec": float(dust_age_sec),
                    "dust_age_sec_for_classification": float(dust_age_sec_for_classification),
                    "dust_age_gate_bypassed": bool(postexpiry_retired_recent_ws_missing_or_unusable),
                    "dust_age_gate_bypass_reason": (
                        "postexpiry_retired_recent_ws_missing_or_unusable"
                        if postexpiry_retired_recent_ws_missing_or_unusable
                        else "none"
                    ),
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
            "held_unpriceable_defect_candidate": bool(held_unpriceable_defect_candidate),
            "held_unpriceable_operator_action": str(held_unpriceable_operator_action),
            "held_unpriceable_non_defect_token_ids": list(held_unpriceable_non_defect_token_ids),
            "held_unpriceable_meaningful_escalation_token_ids": list(
                held_unpriceable_meaningful_escalation_token_ids
            ),
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
        effective_hard_degraded_exempt_count = 0
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
        held_unpriceable_non_defect_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("held_unpriceable_non_defect_token_ids", []))
            if str(token_id).strip()
        ]
        held_unpriceable_cause_by_token = {
            str(token_id): str(cause).strip() or HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
            for token_id, cause in dict(valuation_state.get("held_unpriceable_cause_by_token", {})).items()
            if str(token_id).strip()
        }
        held_unpriceable_cause_counts = {
            HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE: int(
                dict(valuation_state.get("held_unpriceable_cause_counts", {})).get(
                    HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE,
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
        meaningful_held_unpriceable_escalation_token_ids = [
            str(token_id)
            for token_id in list(valuation_state.get("held_unpriceable_meaningful_escalation_token_ids", []))
            if str(token_id).strip()
        ]
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
        self._held_unpriceable_defect_candidate = bool(held_unpriceable_defect_candidate)
        self._held_unpriceable_operator_action = str(held_unpriceable_operator_action)
        self._held_unpriceable_non_defect_token_ids = list(held_unpriceable_non_defect_token_ids)
        self._held_unpriceable_meaningful_escalation_token_ids = list(
            meaningful_held_unpriceable_escalation_token_ids
        )
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
            "preexpiry_ws_missing_or_unusable_anomaly_count",
            float(self._preexpiry_ws_missing_or_unusable_anomaly_count),
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
        )
        valuation_event_reason = ""
        valuation_event_reason_source = "degraded_reasons_first"
        if degraded_reasons:
            valuation_event_reason = str(degraded_reasons[0]).strip()
        elif bool(degraded):
            valuation_event_reason = "valuation_degraded_unspecified"
            valuation_event_reason_source = "degraded_fallback_unspecified"
        else:
            valuation_event_reason = "valuation_not_degraded"
            valuation_event_reason_source = "not_degraded_default"
        valuation_event_token_id: Optional[str] = None
        if held_unpriceable_escalation_token_ids:
            valuation_event_token_id = str(held_unpriceable_escalation_token_ids[0]).strip() or None
        elif held_unpriceable_token_ids:
            valuation_event_token_id = str(held_unpriceable_token_ids[0]).strip() or None
        elif hard_degraded_meaningful_token_ids:
            valuation_event_token_id = str(hard_degraded_meaningful_token_ids[0]).strip() or None
        elif hard_degraded_token_ids:
            valuation_event_token_id = str(hard_degraded_token_ids[0]).strip() or None
        else:
            position_tokens = [str(x).strip() for x in list(valuation_state.get("position_tokens", [])) if str(x).strip()]
            if position_tokens:
                valuation_event_token_id = position_tokens[0]
        valuation_event_token_source = (
            "held_unpriceable_escalation_token"
            if held_unpriceable_escalation_token_ids
            else (
                "held_unpriceable_token"
                if held_unpriceable_token_ids
                else (
                    "hard_degraded_meaningful_token"
                    if hard_degraded_meaningful_token_ids
                    else (
                        "hard_degraded_token"
                        if hard_degraded_token_ids
                        else (
                            "position_token"
                            if valuation_event_token_id is not None
                            else "none"
                        )
                    )
                )
            )
        )
        if signature != self._last_valuation_event_signature:
            self._last_valuation_event_signature = signature
            self.events.log_event(
                "valuation_degraded",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "phase": str(phase or "unknown"),
                    "reason": str(valuation_event_reason),
                    "reason_source": str(valuation_event_reason_source),
                    "token_id": (str(valuation_event_token_id) if valuation_event_token_id is not None else None),
                    "token_id_source": str(valuation_event_token_source),
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
                    "held_unpriceable_non_defect_token_ids": list(held_unpriceable_non_defect_token_ids),
                    "held_unpriceable_meaningful_escalation_token_ids": list(
                        meaningful_held_unpriceable_escalation_token_ids
                    ),
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
                    "preexpiry_ws_missing_or_unusable_anomaly_count": int(
                        self._preexpiry_ws_missing_or_unusable_anomaly_count
                    ),
                    "financial_posture_class": str(self._financial_posture_class),
                    "live_mid_max_age_sec": float(self.live_mid_max_age_sec),
                    "one_sided_quote_max_age_sec": float(self.one_sided_quote_max_age_sec),
                    "last_known_mid_max_age_sec": float(self.last_known_mid_max_age_sec),
                    "raw_valuation_degraded": bool(valuation_state.get("raw_valuation_degraded", False)),
                    "raw_valuation_hard_degraded": bool(valuation_state.get("raw_valuation_hard_degraded", False)),
                    "held_exposure_class_by_token": dict(self._held_exposure_class_by_token),
                    "held_exposure_detail_by_token": dict(self._held_exposure_detail_by_token),
                    "held_dust_token_ids": list(self._held_dust_token_ids),
                    "held_dust_count": int(len(self._held_dust_token_ids)),
                    "held_dust_quarantined_token_ids": list(self._held_dust_quarantined_token_ids),
                    "held_dust_quarantined_count": int(len(self._held_dust_quarantined_token_ids)),
                    "held_dust_total_notional_upper_bound_usd": float(
                        self._held_dust_total_notional_upper_bound_usd
                    ),
                    "held_dust_raw_hard_degraded_token_count": int(self._held_dust_raw_hard_degraded_token_count),
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
        valuation_state["held_dust_raw_hard_degraded_token_count"] = int(
            self._held_dust_raw_hard_degraded_token_count
        )
        valuation_state["held_unpriceable_defect_candidate"] = bool(held_unpriceable_defect_candidate)
        valuation_state["held_unpriceable_operator_action"] = str(held_unpriceable_operator_action)
        valuation_state["held_unpriceable_non_defect_token_ids"] = list(held_unpriceable_non_defect_token_ids)
        valuation_state["held_unpriceable_meaningful_escalation_token_ids"] = list(
            meaningful_held_unpriceable_escalation_token_ids
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
        except (OSError, TypeError, ValueError) as exc:
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
        except (OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
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
        bootstrap_reason = str(getattr(self, "_ws_slo_bootstrap_reason", "") or "")
        bootstrap_active = bool(getattr(self, "_ws_slo_bootstrap_active", False))
        if (not math.isfinite(started_mono)) or (started_mono > now_mono):
            if not (bootstrap_active or bootstrap_reason):
                self._ws_slo_bootstrap_started_mono = 0.0
                self._ws_slo_bootstrap_active = False
                return False
            started_mono = now_mono
            self._ws_slo_bootstrap_started_mono = started_mono
        if started_mono <= 0.0:
            self._ws_slo_bootstrap_active = False
            return False
        elapsed = max(0.0, now_mono - started_mono)
        active = elapsed < grace_sec
        self._ws_slo_bootstrap_active = bool(active)
        return bool(active)

    def _reset_ws_slo_bootstrap(self, *, reason: str, activate_grace: bool = True) -> None:
        if activate_grace:
            self._ws_slo_bootstrap_started_mono = time.monotonic()
            self._ws_slo_bootstrap_active = bool(self.token_ids) and self.operating_mode_ws_slo_bootstrap_grace_sec > 0.0
            self._ws_slo_bootstrap_reason = str(reason)
        else:
            self._ws_slo_bootstrap_started_mono = 0.0
            self._ws_slo_bootstrap_active = False
            self._ws_slo_bootstrap_reason = ""
        self.events.log_event(
            "ws_slo_bootstrap_reset",
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "reason": str(reason),
                "reason_class": "steady_state_self_heal"
                if str(reason) in {"book_feed_ws_pair_truth_missing", "book_feed_ws_pair_truth_missing_all_pairs"}
                else "startup_or_target_refresh",
                "token_count": len(self.token_ids),
                "grace_sec": float(self.operating_mode_ws_slo_bootstrap_grace_sec),
                "grace_applied": bool(self._ws_slo_bootstrap_active),
                "active": bool(self._ws_slo_bootstrap_active),
            },
        )

    def _ws_slo_degraded_cycle(
        self,
        *,
        has_targets: bool,
        book_feed_status: Dict[str, Any],
        chainlink_status: Dict[str, Any],
        pair_missing_base_keys: Optional[List[str]] = None,
        all_target_pairs_missing_ws: bool = False,
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
            if pair_missing_base_keys:
                reasons.append("book_feed_ws_pair_truth_missing")
            if bool(all_target_pairs_missing_ws):
                reasons.append("book_feed_ws_pair_truth_missing_all_pairs")
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

    def _maybe_request_book_feed_resubscribe_for_target_ws_gap(
        self,
        *,
        ws_slo_reasons: list[str],
        book_feed_status: Dict[str, Any],
        ws_slo_bootstrap_active: bool,
    ) -> bool:
        if not any(
            reason in {"book_feed_ws_pair_truth_missing", "book_feed_ws_pair_truth_missing_all_pairs"}
            for reason in ws_slo_reasons
        ):
            return False
        if ws_slo_bootstrap_active:
            return False
        if not bool(book_feed_status.get("connected", False)):
            return False
        if bool(getattr(self, "_last_ws_slo_degraded", False)) and str(
            getattr(self, "_last_ws_slo_reason", "") or ""
        ) == ",".join(ws_slo_reasons):
            return False
        request = getattr(self.book_feed, "request_resubscribe", None)
        if not callable(request):
            return False
        request()
        self._reset_ws_slo_bootstrap(
            reason="book_feed_ws_pair_truth_missing",
            activate_grace=False,
        )
        return True

    def _build_fair_probability_map(
        self,
        books: Dict[str, Any],
        *,
        latency_snapshot: LatencySnapshot,
        scope: str = "maker",
    ) -> Dict[str, float]:
        normalized_scope = str(scope or "maker").strip().lower()
        if normalized_scope not in {"maker", "taker"}:
            raise ValueError(f"fair_probability_scope_invalid:{normalized_scope or 'missing'}")
        apply_maker_latency_gates = normalized_scope == "maker"
        symbol_for_targets = self.chainlink_symbol_for_targets
        if not symbol_for_targets:
            return {}
        latest_chainlink = self.chainlink.get_latest(symbol_for_targets)
        if latest_chainlink is None:
            return {}
        if apply_maker_latency_gates and self.latency_verifier.require_armed_for_maker and not latency_snapshot.armed:
            return {}
        tick_age_sec = time.monotonic() - latest_chainlink.received_monotonic
        if tick_age_sec > self.doctrine_oracle_max_tick_age_sec:
            return {}

        out: Dict[str, float] = {}
        now = utc_now()
        for token_id in books.keys():
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            side = self.token_side_by_token.get(token_id)
            strike = self._token_price_anchor(token_id)
            if strike is None or expiry_dt is None or side not in {"YES", "NO"}:
                continue
            if apply_maker_latency_gates and self.latency_verifier.require_armed_for_maker and not self._lag_verified(token_id):
                continue
            if apply_maker_latency_gates and self.latency_verifier.score_enabled:
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
            expiry_dt = self.token_expiry_dt_by_token.get(token_id)
            side = self.token_side_by_token.get(token_id)
            strike = self._token_price_anchor(token_id)
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

    def _taker_context(self) -> Dict[str, Any]:
        now = utc_now()
        doctrine_mode = str(getattr(self, "doctrine_mode", "degraded")).strip().lower() or "degraded"
        arming_horizon_sec = float(getattr(self, "taker_arming_horizon_sec", 20.0))
        near_tokens: Dict[str, float] = {}
        degraded_expiry_fallback_active = False
        for token_id in self.token_ids:
            expiry = self.token_expiry_dt_by_token.get(token_id)
            if expiry is None:
                continue
            sec_to_expiry = (expiry - now).total_seconds()
            if sec_to_expiry < 0:
                continue
            # Canonical taker reachability is now owned by stage truth plus the
            # taker final window, not by the legacy sniper execution-cutoff
            # shell. Keep the broad arming horizon for monitoring/telemetry, but
            # do not let the old cutoff suppress true late-window taker tokens.
            if sec_to_expiry <= arming_horizon_sec:
                near_tokens[token_id] = sec_to_expiry
        if not near_tokens and self.taker_allow_without_expiry_metadata and doctrine_mode == "degraded":
            # Paper/runtime fallback: keep taker evaluable when expiry metadata is absent
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
                        "path": "taker_allow_without_expiry_metadata",
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
        active_tokens = lag_verified_tokens if self.taker_require_lag_verification else list(near_tokens.keys())
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

    def _taker_window_token_ids(
        self,
        *,
        taker_ctx: Mapping[str, Any],
        taker_phase_tokens: Collection[str],
    ) -> list[str]:
        """Canonical taker reachability set for runtime evaluation.

        Keep the raw near-window shell for diagnostics/telemetry, but only hand
        stage-eligible window tokens into the taker runtime path. This removes
        the old activation-vs-execution split without widening taker authority
        beyond the current stage law.
        """

        window_token_ids = [str(token_id).strip() for token_id in list(taker_ctx.get("near_token_ids", [])) if str(token_id).strip()]
        if not window_token_ids:
            window_token_ids = [str(token_id).strip() for token_id in list(taker_ctx.get("token_ids", [])) if str(token_id).strip()]
        if not window_token_ids:
            return []
        taker_phase_token_set = {str(token_id).strip() for token_id in taker_phase_tokens if str(token_id).strip()}
        if not taker_phase_token_set:
            return []
        return [
            token_id
            for token_id in self._unique_ordered(window_token_ids)
            if token_id in taker_phase_token_set
        ]

    def _lifecycle_phase_for_runtime(
        self,
        *,
        sec_to_expiry: Optional[float],
        resolve_required: bool,
        hold_active: bool,
        stage_known: bool,
    ) -> str:
        if resolve_required:
            return "resolve"
        if sec_to_expiry is None or not stage_known:
            return "prepare"
        sec = float(sec_to_expiry)
        if sec < 0.0:
            return "resolve"
        if hold_active:
            return "prepare"
        taker_window_open_sec = float(getattr(self, "lifecycle_taker_window_open_sec", 7.0))
        maker_window_open_sec = float(getattr(self, "lifecycle_maker_window_open_sec", 15.0))
        if sec <= taker_window_open_sec + 1e-9:
            return "taker_window"
        if sec <= maker_window_open_sec + 1e-9:
            return "maker_window"
        return "prepare"

    @staticmethod
    def _lineage_stage_for_sec_to_expiry(sec_to_expiry: Optional[float]) -> str:
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

    def _compat_stage_for_runtime(
        self,
        lineage_stage: str,
        sec_to_expiry: Optional[float],
    ) -> str:
        lineage_bucket = str(lineage_stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN
        if lineage_bucket != STAGE_EXTREME_ONLY:
            return lineage_bucket
        if not isinstance(sec_to_expiry, (int, float)):
            return lineage_bucket
        sec = float(sec_to_expiry)
        if sec < 0.0:
            return lineage_bucket
        taker_window_open_sec = float(getattr(self, "lifecycle_taker_window_open_sec", 7.0))
        maker_window_open_sec = float(getattr(self, "lifecycle_maker_window_open_sec", 15.0))
        if sec <= taker_window_open_sec + 1e-9:
            return STAGE_TAKER_COMMITMENT
        if sec <= maker_window_open_sec + 1e-9:
            return STAGE_MAKER_LATE_WINDOW
        return STAGE_LATE_DIAGNOSTIC

    def _compat_stage_from_lifecycle_info(
        self,
        info: Mapping[str, Any],
        *,
        fallback_stage: Optional[str] = None,
    ) -> str:
        effective = str(
            info.get(EDGE_STAGE_EFFECTIVE_FIELD)
            or info.get("stage")
            or fallback_stage
            or STAGE_UNKNOWN
        ).strip().upper() or STAGE_UNKNOWN
        sec_to_expiry = info.get("sec_to_expiry")
        if effective != STAGE_UNKNOWN:
            if effective == STAGE_EXTREME_ONLY:
                sec = float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None
                return self._compat_stage_for_runtime(STAGE_EXTREME_ONLY, sec)
            return effective

        lifecycle_phase = str(
            info.get(EDGE_LIFECYCLE_PHASE_FIELD)
            or lifecycle_phase_from_payload(info)
            or ""
        ).strip().lower()
        lineage_stage = lineage_stage_from_payload(info)
        if lifecycle_phase == "resolve":
            return STAGE_EXPIRED
        if lifecycle_phase == "taker_window":
            return STAGE_TAKER_COMMITMENT
        if lifecycle_phase == "maker_window":
            return STAGE_MAKER_LATE_WINDOW
        if lineage_stage == STAGE_EXTREME_ONLY:
            sec = float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None
            return self._compat_stage_for_runtime(STAGE_EXTREME_ONLY, sec)
        if lineage_stage != STAGE_UNKNOWN:
            return lineage_stage
        return effective

    def _resolve_taker_required_min_edge(
        self,
        stage: str,
    ) -> float:
        del stage
        # Current live taker threshold authority is top-level taker.min_edge
        # only. Stage-local threshold leaves and extreme-only fallback
        # multipliers are retired authority residue and no longer arm taker.
        return float(self.taker_min_edge)

    def _resolve_taker_cooldown_sec(self, stage: str) -> float:
        del stage
        return max(0.0, float(self.taker_per_token_cooldown_sec))

    def _emit_taker_window_semantic_check(self) -> None:
        if not bool(self.taker_competitiveness_cfg.enabled):
            return

        canonical_window_sec = max(0.0, float(self.lifecycle_taker_window_open_sec))
        maker_window_open_sec = max(canonical_window_sec, float(self.lifecycle_maker_window_open_sec))
        phase_rows: Dict[str, Dict[str, Any]] = {}
        dead_count = 0
        phase_bands: Dict[str, Tuple[Optional[float], Optional[float], bool]] = {
            "scan": (maker_window_open_sec, None, False),
            "prepare": (maker_window_open_sec, None, False),
            "maker_window": (canonical_window_sec, maker_window_open_sec, False),
            "taker_window": (0.0, canonical_window_sec, canonical_window_sec > 0.0),
            "resolve": (None, 0.0, False),
        }
        for phase_name, (lower_exclusive, upper_inclusive, phase_allows_taker) in phase_bands.items():
            effective_window_sec = canonical_window_sec if phase_allows_taker else 0.0
            semantically_live = bool(
                phase_allows_taker
                and isinstance(lower_exclusive, (int, float))
                and effective_window_sec > float(lower_exclusive)
            )
            semantic_dead_reason = None
            if not phase_allows_taker:
                semantic_dead_reason = "phase_disallow_taker"
            elif not semantically_live:
                dead_count += 1
                semantic_dead_reason = "window_non_overlapping_with_phase_interval"
            overlap_high = (
                min(float(upper_inclusive), effective_window_sec)
                if semantically_live and isinstance(upper_inclusive, (int, float))
                else None
            )
            phase_rows[phase_name] = {
                "interval_lower_exclusive_sec": (
                    float(lower_exclusive) if isinstance(lower_exclusive, (int, float)) else None
                ),
                "interval_upper_inclusive_sec": (
                    float(upper_inclusive) if isinstance(upper_inclusive, (int, float)) else None
                ),
                "phase_allows_taker": bool(phase_allows_taker),
                "effective_final_window_sec": float(effective_window_sec),
                "semantically_live": semantically_live,
                "overlap_high_sec": (float(overlap_high) if semantically_live else None),
                "semantic_dead_reason": semantic_dead_reason,
            }

        self.events.log_event(
            EVENT_TAKER_WINDOW_SEMANTIC_CHECK,
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "final_window_enabled": bool(self.taker_competitiveness_cfg.final_window_enabled),
                "default_final_window_sec": float(self.taker_competitiveness_cfg.final_window_sec),
                "canonical_live_final_window_sec": float(canonical_window_sec),
                "phase_rows": phase_rows,
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
        if self.taker_sizing_max_usd > 0.0:
            caps.append(float(self.taker_sizing_max_usd))
        if self.taker_wallet_max_notional_per_order_usdc > 0.0:
            caps.append(float(self.taker_wallet_max_notional_per_order_usdc))
        strategy_share_cap = float(getattr(self.manager, "strategy_max_order_size", 0.0) or 0.0)
        if strategy_share_cap > 0.0:
            caps.append(strategy_share_cap * px)
        risk_share_cap = float(self.taker_max_order_size_shares or 0.0)
        if risk_share_cap > 0.0:
            caps.append(risk_share_cap * px)
        if not caps:
            return None
        value = min(caps)
        return float(value) if value > 0.0 else None

    @staticmethod
    def _binary_complement_side(side: Any) -> str:
        normalized = str(side or "").strip().upper()
        if normalized == "YES":
            return "NO"
        if normalized == "NO":
            return "YES"
        return ""

    def _resolve_complement_token_id(
        self,
        token_id: str,
        *,
        active_token_ids: Optional[set[str]] = None,
    ) -> Tuple[Optional[str], str]:
        token = str(token_id or "").strip()
        if not token:
            return None, "complement_token_mapping_unavailable"
        side = str(self.token_side_by_token.get(token, "")).strip().upper()
        complement_side = self._binary_complement_side(side)
        market_key = str(self.token_market_key_by_token.get(token, "")).strip()
        if complement_side not in {"YES", "NO"}:
            return None, "complement_token_mapping_unavailable"
        if not market_key or "|" not in market_key:
            return None, "complement_token_mapping_unavailable"
        base_key, key_side = market_key.rsplit("|", 1)
        if not base_key or str(key_side or "").strip().upper() != side:
            return None, "complement_token_mapping_unavailable"
        expected_market_key = f"{base_key}|{complement_side}"
        active = {str(item).strip() for item in (active_token_ids or set()) if str(item).strip()}
        matches = [
            candidate_id
            for candidate_id, candidate_market_key in self.token_market_key_by_token.items()
            if str(candidate_id).strip()
            and str(candidate_id).strip() != token
            and (not active or str(candidate_id).strip() in active)
            and str(candidate_market_key or "").strip() == expected_market_key
            and str(self.token_side_by_token.get(str(candidate_id).strip(), "")).strip().upper() == complement_side
        ]
        if len(matches) != 1:
            return None, "complement_token_mapping_unavailable"
        return str(matches[0]).strip(), ""

    def _binary_settlement_value_for_token(self, *, token_id: str, resolution_price: float) -> Optional[float]:
        side = str(self.token_side_by_token.get(token_id, "")).strip().upper()
        strike = self._token_price_anchor(token_id)
        if side not in {"YES", "NO"} or not isinstance(strike, (int, float)):
            return None
        diff = float(resolution_price) - float(strike)
        if abs(diff) <= 1e-9:
            return None
        yes_wins = diff > 0.0
        if side == "YES":
            return 1.0 if yes_wins else 0.0
        return 0.0 if yes_wins else 1.0

    def _apply_postexpiry_binary_settlement(self) -> int:
        symbol_for_targets = str(self.chainlink_symbol_for_targets or "").strip()
        if not symbol_for_targets:
            return 0
        settled_count = 0
        now_dt = utc_now()
        boundary_eps = max(0.0, float(self.expiry_boundary_epsilon_sec))
        for token_id, position in sorted(self.risk.positions.items(), key=lambda item: str(item[0])):
            token = str(token_id or "").strip()
            if not token:
                continue
            net_shares = float(getattr(position, "net_shares", 0.0) or 0.0)
            if abs(net_shares) <= 1e-9:
                continue
            expiry_dt = self.token_expiry_dt_by_token.get(token)
            if not isinstance(expiry_dt, dt.datetime):
                continue
            sec_to_expiry = (expiry_dt - now_dt).total_seconds()
            if float(sec_to_expiry) > -(boundary_eps + 1e-9):
                continue
            settlement_tick = self.chainlink.get_first_at_or_after(symbol_for_targets, utc_iso(expiry_dt))
            if settlement_tick is None:
                continue
            settlement_source_dt = parse_ts(settlement_tick.source_ts_utc)
            if settlement_source_dt is None or settlement_source_dt < expiry_dt:
                continue
            settlement_value = self._binary_settlement_value_for_token(
                token_id=token,
                resolution_price=float(settlement_tick.price),
            )
            if settlement_value is None:
                continue
            risk_settlement = self.risk.settle_binary_position(
                token_id=token,
                settlement_price=float(settlement_value),
            )
            if not isinstance(risk_settlement, dict):
                continue
            wallet_settlement = self.wallet.settle_binary_position(
                token_id=token,
                settlement_side=str(risk_settlement.get("settlement_side") or ""),
                size_shares=float(risk_settlement.get("settlement_size_shares") or 0.0),
                settlement_price=float(settlement_value),
                ts_utc=utc_iso(),
            )
            self.telemetry.incr("binary_position_settled")
            settled_count += 1
            self.events.log_event(
                "binary_position_settlement",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_id": token,
                    "market_key": str(self.token_market_key_by_token.get(token, "") or ""),
                    "token_side": str(self.token_side_by_token.get(token, "") or ""),
                    "strike": self._token_price_anchor(token),
                    "open_anchor_ts_utc": str(self.token_open_anchor_utc_by_token.get(token) or ""),
                    "expiry_ts_utc": utc_iso(expiry_dt),
                    "sec_to_expiry_at_settlement": float(sec_to_expiry),
                    "chainlink_symbol": symbol_for_targets,
                    "settlement_spot_price": float(settlement_tick.price),
                    "settlement_source_ts_utc": settlement_tick.source_ts_utc,
                    "settlement_received_ts_utc": settlement_tick.received_ts_utc,
                    "settlement_value": float(settlement_value),
                    "settlement_side": str(risk_settlement.get("settlement_side") or ""),
                    "settlement_size_shares": float(risk_settlement.get("settlement_size_shares") or 0.0),
                    "settlement_notional_usd": float(risk_settlement.get("settlement_notional_usd") or 0.0),
                    "net_shares_before": float(risk_settlement.get("net_shares_before") or 0.0),
                    "net_shares_after": float(risk_settlement.get("net_shares_after") or 0.0),
                    "wallet_settlement_notional_usd": float(wallet_settlement.get("settlement_notional_usd") or 0.0),
                    "authority": "chainlink_first_source_ts_at_or_after_expiry",
                },
            )
        return settled_count

    def _refresh_taker_multi_oracle_cap_from_wallet(self) -> None:
        pct_cap = float(self.taker_competitiveness_cfg.multi_oracle_capital_pct_cap)
        if pct_cap <= 0.0:
            self.taker_multi_oracle_cap_usd = None
            self.taker_multi_oracle_cap_source = "disabled"
            self.taker_multi_oracle_cap_authority_class = "none"
            return
        wallet_contract = self.wallet.status_contract()
        authority_class = str(wallet_contract.get("authority_status_class") or "").strip().lower()
        deployable_capital = parse_float(wallet_contract.get("deployable_capital"))
        if authority_class == "authoritative" and isinstance(deployable_capital, (int, float)) and deployable_capital > 0.0:
            self.taker_multi_oracle_cap_usd = float(pct_cap * float(deployable_capital))
            self.taker_multi_oracle_cap_source = "wallet_deployable_capital_authoritative"
            self.taker_multi_oracle_cap_authority_class = "live"
            return
        if isinstance(self.taker_multi_oracle_cap_usd, (int, float)) and self.taker_multi_oracle_cap_usd > 0.0:
            self.taker_multi_oracle_cap_source = "orchestration_heuristic_config_fallback"
            self.taker_multi_oracle_cap_authority_class = "derived"
            return
        self.taker_multi_oracle_cap_usd = None
        self.taker_multi_oracle_cap_source = "wallet_contract_unavailable"
        self.taker_multi_oracle_cap_authority_class = "bootstrap"

    def _maker_timing_gate_open(self, sec_to_expiry: Optional[float]) -> bool:
        if not self.maker_comp_timing_gate_enabled:
            return True
        if not isinstance(sec_to_expiry, (int, float)):
            return False
        sec = float(sec_to_expiry)
        return self.maker_comp_timing_gate_min_sec_to_expiry <= sec <= self.maker_comp_timing_gate_max_sec_to_expiry

    @staticmethod
    def _maker_cannon_probe_token_ids(
        *,
        stage_info_by_token: Dict[str, Dict[str, Any]],
        books: Dict[str, Any],
    ) -> set[str]:
        token_ids: set[str] = set()
        if not books:
            return token_ids
        for token_id, info in stage_info_by_token.items():
            sec_to_expiry = info.get("sec_to_expiry")
            if not isinstance(sec_to_expiry, (int, float)):
                continue
            sec = float(sec_to_expiry)
            if 0.0 <= sec <= 20.0 and str(token_id) in books:
                token_ids.add(str(token_id))
        return token_ids

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
        market_reference: Optional[Dict[str, Any]],
        fair_probability: Optional[float],
        secondary_fair_probability: Optional[float],
        secondary_oracle_status: str,
        chainlink_spot_price: Optional[float],
        secondary_oracle_spot_price: Optional[float],
        stage: str,
        lifecycle_phase: Optional[str] = None,
        lineage_stage: Optional[str] = None,
        sec_to_expiry: Optional[float],
        base_size_multiplier: float,
        base_spread_multiplier: float,
        timing_gate_open: bool,
        maker_phase_allowed: bool = False,
    ) -> Dict[str, Any]:
        market_reference = dict(market_reference or {})
        market_probability = (
            float(market_reference.get("market_probability"))
            if isinstance(market_reference.get("market_probability"), (int, float))
            else (
                float(getattr(top, "midpoint"))
                if top is not None and isinstance(getattr(top, "midpoint", None), (int, float))
                else None
            )
        )
        fair = float(fair_probability) if isinstance(fair_probability, (int, float)) else None
        secondary_fair = (
            float(secondary_fair_probability)
            if isinstance(secondary_fair_probability, (int, float))
            else None
        )
        edge_signed = (fair - market_probability) if (fair is not None and market_probability is not None) else None
        edge_abs = abs(edge_signed) if edge_signed is not None else None
        secondary_edge_signed = (
            secondary_fair - market_probability
            if (secondary_fair is not None and market_probability is not None)
            else None
        )
        normalized_secondary_oracle_status = (
            str(secondary_oracle_status or "unknown").strip().lower() or "unknown"
        )
        secondary_oracle_confirmation = False
        if normalized_secondary_oracle_status != "disabled":
            if (
                isinstance(edge_signed, (int, float))
                and isinstance(secondary_edge_signed, (int, float))
                and abs(float(edge_signed)) > 1e-12
                and abs(float(secondary_edge_signed)) > 1e-12
            ):
                secondary_oracle_confirmation = bool(
                    (float(edge_signed) > 0.0) == (float(secondary_edge_signed) > 0.0)
                )
                normalized_secondary_oracle_status = (
                    "confirmed" if secondary_oracle_confirmation else "direction_mismatch"
                )
            elif normalized_secondary_oracle_status == "available":
                normalized_secondary_oracle_status = "unknown"
        secondary_oracle_price_delta_abs = None
        secondary_oracle_price_delta_bps = None
        if isinstance(chainlink_spot_price, (int, float)) and isinstance(secondary_oracle_spot_price, (int, float)):
            secondary_oracle_price_delta_abs = abs(
                float(chainlink_spot_price) - float(secondary_oracle_spot_price)
            )
            if abs(float(chainlink_spot_price)) > 1e-12:
                secondary_oracle_price_delta_bps = (
                    float(secondary_oracle_price_delta_abs) / abs(float(chainlink_spot_price))
                ) * 10000.0
        strength = self._maker_edge_strength(edge_abs)
        size_mult_comp = 1.0 + ((float(self.maker_comp_size_mult_max) - 1.0) * strength)
        spread_mult_comp = 1.0 - ((1.0 - float(self.maker_comp_spread_mult_min)) * strength)
        requote_mult_comp = 1.0 - ((1.0 - float(self.maker_comp_requote_delta_mult_min)) * strength)
        size_multiplier_applied = max(0.01, float(base_size_multiplier) * size_mult_comp)
        spread_multiplier_applied = max(1e-6, float(base_spread_multiplier) * spread_mult_comp)
        requote_delta_applied = max(1e-9, float(self.maker_comp_base_requote_delta) * requote_mult_comp)

        normalized_stage = str(stage or "").strip().upper()
        normalized_lifecycle_phase = str(
            lifecycle_phase or legacy_stage_to_lifecycle_phase(normalized_stage) or ""
        ).strip().lower() or "scan"
        normalized_lineage_stage = str(lineage_stage or normalized_stage).strip().upper() or STAGE_UNKNOWN
        one_sided_authority_allowed = bool(maker_phase_allowed)
        side_policy = "TWO_SIDED"
        one_sided_active = False
        if (
            self.maker_comp_one_sided_enabled
            and one_sided_authority_allowed
            and edge_signed is not None
            and abs(edge_signed) >= float(self.maker_comp_one_sided_edge_threshold_abs)
        ):
            side_policy = "BUY_ONLY" if edge_signed >= 0.0 else "SELL_ONLY"
            one_sided_active = True

        edge_bucket = self._maker_edge_bucket(edge_abs)
        competitiveness_context = {
            "token_id": str(token_id),
            "stage": normalized_stage,
            **lifecycle_phase_surface_fields(lifecycle_phase=normalized_lifecycle_phase),
            **lineage_stage_surface_fields(lineage_stage=normalized_lineage_stage),
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
            "secondary_fair_probability": secondary_fair,
            "market_reference_mode": str(
                market_reference.get("market_reference_mode")
                or ("direct_midpoint" if isinstance(market_probability, (int, float)) else "missing")
            ).strip().lower(),
            "market_reference_basis": str(
                market_reference.get("market_reference_basis")
                or ("direct_book_midpoint" if isinstance(market_probability, (int, float)) else "missing")
            ).strip().lower(),
            "market_reference_source_side": str(
                market_reference.get("market_reference_source_side") or "none"
            ).strip().lower(),
            "market_reference_class": str(
                market_reference.get("market_reference_class")
                or ("authoritative" if isinstance(market_probability, (int, float)) else "not_available")
            ).strip().lower(),
            "secondary_edge_value": (
                float(secondary_edge_signed)
                if isinstance(secondary_edge_signed, (int, float))
                else None
            ),
            "secondary_oracle_status": normalized_secondary_oracle_status,
            "secondary_oracle_confirmation": bool(secondary_oracle_confirmation),
            "chainlink_spot_price": (
                float(chainlink_spot_price)
                if isinstance(chainlink_spot_price, (int, float))
                else None
            ),
            "secondary_oracle_spot_price": (
                float(secondary_oracle_spot_price)
                if isinstance(secondary_oracle_spot_price, (int, float))
                else None
            ),
            "secondary_oracle_price_delta_abs": (
                float(secondary_oracle_price_delta_abs)
                if isinstance(secondary_oracle_price_delta_abs, (int, float))
                else None
            ),
            "secondary_oracle_price_delta_bps": (
                float(secondary_oracle_price_delta_bps)
                if isinstance(secondary_oracle_price_delta_bps, (int, float))
                else None
            ),
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
            "one_sided_allowed_phase": bool(one_sided_authority_allowed),
            "one_sided_allowed_authority": bool(one_sided_authority_allowed),
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

    def _token_lifecycle_info(self, token_id: str) -> Dict[str, Any]:
        now = utc_now()
        expiry = self.token_expiry_dt_by_token.get(token_id)
        market_key = str(self.token_market_key_by_token.get(token_id, "")).strip()
        reason = ""
        sec_to_expiry: Optional[float] = None
        lineage_stage = STAGE_UNKNOWN
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
        lineage_stage = self._lineage_stage_for_sec_to_expiry(sec_to_expiry)
        maker_timing_gate_open = bool(self._maker_timing_gate_open(sec_to_expiry))
        maker_timing_stage_override_active = False
        lifecycle_info = self._lifecycle_management_payload(
            token_id=token_id,
            sec_to_expiry=sec_to_expiry,
        )
        stage_known = bool(market_key) and lineage_stage not in {STAGE_UNKNOWN, STAGE_EXPIRED}
        if stage_known:
            entry_mono = self._market_entry_mono_by_token.get(token_id)
            entry_cycle = self._market_entry_cycle_by_token.get(token_id)
            if entry_mono is not None and entry_cycle is not None:
                elapsed_sec = max(0.0, time.monotonic() - entry_mono)
                elapsed_cycles = max(0, int(self._doctrine_cycle_index) - int(entry_cycle))
                hold_cycles_remaining = max(0, self.doctrine_min_observe_cycles_on_entry - elapsed_cycles)
                hold_seconds_remaining = max(0.0, self.doctrine_min_observe_seconds_on_entry - elapsed_sec)
                hold_active = (hold_cycles_remaining > 0) or (hold_seconds_remaining > 0.0)
                if hold_active:
                    reason = f"observe_hold_active:{lineage_stage}"

        resolve_required = bool(
            lineage_stage == STAGE_EXPIRED
            or lifecycle_info.get("open_order_cleanup_required", False)
            or lifecycle_info.get("settlement_hold_required", False)
            or lifecycle_info.get("unresolved_lifecycle_obligation", False)
            or lifecycle_info.get("cancel_fail_closed", False)
        )
        lifecycle_phase = self._lifecycle_phase_for_runtime(
            sec_to_expiry=sec_to_expiry,
            resolve_required=resolve_required,
            hold_active=hold_active,
            stage_known=stage_known,
        )
        maker_phase_allowed = lifecycle_phase == "maker_window"
        taker_phase_allowed = lifecycle_phase == "taker_window"
        maker_gate_open = bool(maker_phase_allowed)
        taker_gate_open = bool(taker_phase_allowed)
        verdict = "pass" if stage_known else "fail"
        if verdict == "fail" and not reason:
            reason = "market_not_tradeable"
        market_ref = self._market_ref_for_market_key(market_key)
        return {
            **lifecycle_phase_surface_fields(lifecycle_phase=lifecycle_phase),
            **lineage_stage_surface_fields(lineage_stage=lineage_stage),
            **ownership_surface_fields(
                owned_market_ref=market_ref,
                challenger_market_ref=None,
                ownership_drop_reason=None,
                ownership_replacement_reason=None,
            ),
            **market_truth_surface_fields(market_truth_required=bool(market_key)),
            **lane_permission_surface_fields(
                maker_phase_allowed=maker_phase_allowed,
                taker_phase_allowed=taker_phase_allowed,
                maker_gate_open=maker_gate_open,
                taker_gate_open=taker_gate_open,
            ),
            "sec_to_expiry": sec_to_expiry,
            "maker_timing_gate_open": bool(maker_timing_gate_open),
            "maker_timing_stage_override_active": bool(maker_timing_stage_override_active),
            "market_key": market_key,
            "observe_hold_active": hold_active,
            "observe_hold_cycles_remaining": hold_cycles_remaining,
            "observe_hold_seconds_remaining": hold_seconds_remaining,
            **lifecycle_surface_fields(
                open_order_cleanup_required=bool(lifecycle_info.get("open_order_cleanup_required", False)),
                settlement_hold_required=bool(lifecycle_info.get("settlement_hold_required", False)),
                unresolved_lifecycle_obligation=bool(lifecycle_info.get("unresolved_lifecycle_obligation", False)),
                cancel_fail_closed=bool(lifecycle_info.get("cancel_fail_closed", False)),
            ),
            "held_net_shares": float(lifecycle_info.get("net_shares", 0.0) or 0.0),
            "held_open_order_present": bool(lifecycle_info.get("open_order_present", False)),
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
            lineage_stage = lineage_stage_from_payload(info)
            if lineage_stage == STAGE_UNKNOWN:
                lineage_stage = str(info.get("stage") or "").strip().upper() or STAGE_UNKNOWN
            doctrine_lifecycle_phase = str(
                info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or lifecycle_phase_from_payload(info)
                or ""
            ).strip().lower() or "scan"
            base_verdict = str(info.get("doctrine_gate_verdict", "fail"))
            base_reason = str(info.get("reason", ""))
            maker_gate_open = bool(
                info.get(
                    EDGE_MAKER_GATE_OPEN_FIELD,
                    info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, False),
                )
            )
            taker_gate_open = bool(
                info.get(
                    EDGE_TAKER_GATE_OPEN_FIELD,
                    info.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, False),
                )
            )
            maker_phase_allowed = bool(
                info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, doctrine_lifecycle_phase == "maker_window")
            )
            taker_phase_allowed = bool(
                info.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, doctrine_lifecycle_phase == "taker_window")
            )
            open_order_cleanup_required = bool(info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False))
            settlement_hold_required = bool(info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False))
            unresolved_lifecycle_obligation = bool(info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False))
            cancel_fail_closed = bool(info.get(EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD, False))
            market_truth_required = bool(
                info.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD, bool(info.get("market_key")))
            )
            prereq_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip()
            maker_prereq_ok = not bool(prereq_reason)
            verdict = base_verdict
            reason = base_reason
            if self.doctrine_mode == "canonical" and maker_gate_open and prereq_reason:
                verdict = "fail"
                reason = prereq_reason
            signature = (
                doctrine_lifecycle_phase,
                verdict,
                reason,
                maker_gate_open,
                taker_gate_open,
                maker_phase_allowed,
                taker_phase_allowed,
                open_order_cleanup_required,
                settlement_hold_required,
                unresolved_lifecycle_obligation,
                cancel_fail_closed,
                market_truth_required,
                lineage_stage,
            )
            if self._last_doctrine_signature_by_token.get(token_id) == signature:
                continue
            previous_lifecycle_phase = self._last_lifecycle_phase_by_token.get(token_id)
            if previous_lifecycle_phase != doctrine_lifecycle_phase:
                self.events.log_event(
                    "lifecycle_phase_transition",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "token_id": token_id,
                        "market_key": str(info.get("market_key", "")),
                        "from_lifecycle_phase": str(previous_lifecycle_phase or "").strip().lower() or "scan",
                        "to_lifecycle_phase": str(doctrine_lifecycle_phase or "").strip().lower() or "scan",
                        **lifecycle_phase_surface_fields(
                            lifecycle_phase=str(doctrine_lifecycle_phase or "").strip().lower() or "scan"
                        ),
                        **ownership_surface_fields(
                            owned_market_ref=info.get(EDGE_OWNED_MARKET_REF_FIELD),
                            challenger_market_ref=info.get(EDGE_CHALLENGER_MARKET_REF_FIELD),
                            ownership_drop_reason=info.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                            ownership_replacement_reason=info.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
                        ),
                        **market_truth_surface_fields(market_truth_required=market_truth_required),
                        **lane_permission_surface_fields(
                            maker_phase_allowed=maker_phase_allowed,
                            taker_phase_allowed=taker_phase_allowed,
                            maker_gate_open=maker_gate_open,
                            taker_gate_open=taker_gate_open,
                        ),
                        **lineage_stage_surface_fields(lineage_stage=lineage_stage),
                        "sec_to_expiry": info.get("sec_to_expiry"),
                        "doctrine_mode": self.doctrine_mode,
                    },
                )
                self._last_lifecycle_phase_by_token[token_id] = doctrine_lifecycle_phase
            self._last_doctrine_signature_by_token[token_id] = signature
            market_ref = (
                info.get(EDGE_OWNED_MARKET_REF_FIELD)
                or self._market_ref_for_market_key(info.get("market_key"))
            )
            self.events.log_event(
                "doctrine_decision",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_id": token_id,
                    "market_key": str(info.get("market_key", "")),
                    "doctrine_mode": self.doctrine_mode,
                    **lifecycle_phase_surface_fields(lifecycle_phase=doctrine_lifecycle_phase),
                    **ownership_surface_fields(
                        owned_market_ref=market_ref,
                        challenger_market_ref=info.get(EDGE_CHALLENGER_MARKET_REF_FIELD),
                        ownership_drop_reason=info.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                        ownership_replacement_reason=info.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
                    ),
                    **market_truth_surface_fields(market_truth_required=market_truth_required),
                    **lane_permission_surface_fields(
                        maker_phase_allowed=maker_phase_allowed,
                        taker_phase_allowed=taker_phase_allowed,
                        maker_gate_open=maker_gate_open,
                        taker_gate_open=taker_gate_open,
                    ),
                    **lineage_stage_surface_fields(lineage_stage=lineage_stage),
                    "sec_to_expiry": info.get("sec_to_expiry"),
                    "observe_hold_active": bool(info.get("observe_hold_active", False)),
                    "observe_hold_cycles_remaining": int(info.get("observe_hold_cycles_remaining", 0)),
                    "observe_hold_seconds_remaining": float(info.get("observe_hold_seconds_remaining", 0.0)),
                    **lifecycle_surface_fields(
                        open_order_cleanup_required=open_order_cleanup_required,
                        settlement_hold_required=settlement_hold_required,
                        unresolved_lifecycle_obligation=unresolved_lifecycle_obligation,
                        cancel_fail_closed=cancel_fail_closed,
                    ),
                    "maker_prereq_ok": maker_prereq_ok,
                    "doctrine_gate_verdict": verdict,
                    "reason": reason,
                },
            )
            if lineage_stage == STAGE_UNKNOWN and verdict == "fail":
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
                            **lifecycle_phase_surface_fields(lifecycle_phase=doctrine_lifecycle_phase),
                            **lineage_stage_surface_fields(lineage_stage=lineage_stage),
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
    ) -> str:
        if self.token_expiry_dt_by_token.get(token_id) is None:
            return "missing_expiry_metadata"
        if self._token_price_anchor(token_id) is None:
            return "missing_threshold_metadata"
        if str(self.token_side_by_token.get(token_id, "")).strip().upper() not in {"YES", "NO"}:
            return "missing_side_metadata"
        if not oracle_fresh:
            return "oracle_unavailable_or_stale"
        if self.latency_verifier.require_armed_for_maker and (not latency_snapshot.armed):
            return "latency_not_armed_for_maker"
        if self.latency_verifier.require_armed_for_maker and (not self._lag_verified(token_id)):
            return "token_lag_not_verified_for_maker"
        if self.latency_verifier.score_enabled:
            if self.latency_verifier.token_score(token_id) < self.latency_verifier.score_min_for_maker:
                return "token_score_below_maker_min"
        if token_id not in fair_probability_by_token:
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

    def _resolve_taker_market_reference(
        self,
        *,
        top: Any,
    ) -> Dict[str, Any]:
        midpoint: Optional[float] = None
        if top is not None and isinstance(getattr(top, "midpoint", None), (int, float)):
            midpoint = float(getattr(top, "midpoint"))
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
        return decision_input_type_from_book_source(source)

    @staticmethod
    def _execution_realism_class_for_scope(scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if normalized == EDGE_EVAL_SCOPE_MAKER:
            # Queue position / time-priority is not explicitly modeled in paper.
            return "not_modeled"
        return "not_modeled"

    @classmethod
    def _book_source_is_ws(cls, top: Any) -> bool:
        return book_source_is_ws(cls._book_source(top))

    @staticmethod
    def _book_ts_utc(top: Any) -> Optional[dt.datetime]:
        if top is None:
            return None
        return parse_ts(getattr(top, "ts_utc", None))

    @staticmethod
    def _book_side_quote_entry(
        *,
        top: Any,
        side: str,
        fallback_ts: Optional[dt.datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_side = str(side or "").strip().lower()
        if normalized_side == "bid":
            price = getattr(top, "best_bid_price", None)
            size = getattr(top, "best_bid_size", None)
        else:
            price = getattr(top, "best_ask_price", None)
            size = getattr(top, "best_ask_size", None)
        if not isinstance(price, (int, float)):
            return None
        ts = ExecutionRunner._book_ts_utc(top) or fallback_ts or utc_now()
        return {
            "price": float(price),
            "size": (float(size) if isinstance(size, (int, float)) else None),
            "ts": ts,
        }

    def _clear_maker_ws_touch_cache(self, token_id: str) -> None:
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id:
            return
        self._maker_last_ws_bid_quote_by_token.pop(normalized_token_id, None)
        self._maker_last_ws_ask_quote_by_token.pop(normalized_token_id, None)

    def _update_maker_ws_touch_cache(self, *, books: Dict[str, Any]) -> None:
        now = utc_now()
        for raw_token_id, top in books.items():
            token_id = str(raw_token_id or "").strip()
            if not token_id or not self._book_source_is_ws(top):
                continue
            bid_entry = self._book_side_quote_entry(top=top, side="bid", fallback_ts=now)
            ask_entry = self._book_side_quote_entry(top=top, side="ask", fallback_ts=now)
            if bid_entry is not None:
                self._maker_last_ws_bid_quote_by_token[token_id] = bid_entry
            if ask_entry is not None:
                self._maker_last_ws_ask_quote_by_token[token_id] = ask_entry

    def _resolve_maker_paired_touch_reference(
        self,
        *,
        token_id: str,
        top: Any,
        maker_prereq_failure_reason: str,
    ) -> Optional[Tuple[BookTop, Dict[str, Any]]]:
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id or top is None or not self._book_source_is_ws(top):
            return None
        if isinstance(getattr(top, "midpoint", None), (int, float)):
            return None
        if self.doctrine_mode != "canonical":
            return None
        if str(maker_prereq_failure_reason or "").strip():
            return None

        current_ts = self._book_ts_utc(top) or utc_now()
        bid_entry = self._book_side_quote_entry(top=top, side="bid", fallback_ts=current_ts)
        ask_entry = self._book_side_quote_entry(top=top, side="ask", fallback_ts=current_ts)
        if bid_entry is None:
            bid_entry = self._maker_last_ws_bid_quote_by_token.get(normalized_token_id)
        if ask_entry is None:
            ask_entry = self._maker_last_ws_ask_quote_by_token.get(normalized_token_id)
        if not bid_entry or not ask_entry:
            return None
        bid_ts = bid_entry.get("ts")
        ask_ts = ask_entry.get("ts")
        if not isinstance(bid_ts, dt.datetime) or not isinstance(ask_ts, dt.datetime):
            return None
        pair_delta_sec = abs((bid_ts - ask_ts).total_seconds())
        if pair_delta_sec > float(self._maker_paired_touch_max_delta_sec):
            return None

        bid_price = bid_entry.get("price")
        ask_price = ask_entry.get("price")
        if not isinstance(bid_price, (int, float)) or not isinstance(ask_price, (int, float)):
            return None
        bid_value = float(bid_price)
        ask_value = float(ask_price)
        if bid_value <= 0.0 or ask_value <= 0.0 or bid_value > ask_value + 1e-9:
            return None

        synthetic_ts = utc_iso(max(bid_ts, ask_ts))
        resolved_top = BookTop(
            token_id=normalized_token_id,
            ts_utc=synthetic_ts,
            source="ws",
            best_bid_price=bid_value,
            best_bid_size=(
                float(bid_entry["size"])
                if isinstance(bid_entry.get("size"), (int, float))
                else None
            ),
            best_ask_price=ask_value,
            best_ask_size=(
                float(ask_entry["size"])
                if isinstance(ask_entry.get("size"), (int, float))
                else None
            ),
        )
        midpoint = resolved_top.midpoint
        if midpoint is None:
            return None
        return (
            resolved_top,
            {
                "market_probability": float(midpoint),
                "market_reference_mode": "backfilled_paired_touch",
                "market_reference_basis": "ws_recent_paired_touch",
                "market_reference_confidence": "authoritative",
                "market_reference_fallback_used": True,
                "market_reference_source_side": "paired",
                "market_reference_class": "authoritative",
                "decision_input_type_override": None,
                "decision_input_data_class_override": None,
                "market_reference_backfill_pair_delta_sec": float(pair_delta_sec),
            },
        )

    def _resolve_maker_book_reference(
        self,
        *,
        token_id: str,
        top: Any,
        maker_prereq_failure_reason: str,
    ) -> Tuple[Any, Dict[str, Any]]:
        if top is not None and isinstance(getattr(top, "midpoint", None), (int, float)):
            return (
                top,
                self._resolve_maker_market_reference(
                    top=top,
                    maker_prereq_failure_reason=maker_prereq_failure_reason,
                ),
            )

        paired = self._resolve_maker_paired_touch_reference(
            token_id=token_id,
            top=top,
            maker_prereq_failure_reason=maker_prereq_failure_reason,
        )
        if paired is not None:
            return paired
        return (
            top,
            self._resolve_maker_market_reference(
                top=top,
                maker_prereq_failure_reason=maker_prereq_failure_reason,
            ),
        )

    def _resolve_maker_market_reference_inputs(
        self,
        *,
        books: Dict[str, Any],
        maker_token_ids: set[str],
        maker_prereq_failure_by_token: Dict[str, str],
    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        resolved_books: Dict[str, Any] = {}
        market_reference_by_token: Dict[str, Dict[str, Any]] = {}
        for raw_token_id in sorted(str(x) for x in maker_token_ids):
            token_id = str(raw_token_id or "").strip()
            if not token_id:
                continue
            top = books.get(token_id)
            resolved_top, market_reference = self._resolve_maker_book_reference(
                token_id=token_id,
                top=top,
                maker_prereq_failure_reason=str(maker_prereq_failure_by_token.get(token_id, "")).strip(),
            )
            if resolved_top is not None:
                resolved_books[token_id] = resolved_top
            market_reference_by_token[token_id] = dict(market_reference)
        return resolved_books, market_reference_by_token

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
    ) -> set[str]:
        gated_tokens: set[str] = set()
        for token_id in maker_eligible_tokens:
            top = books.get(token_id)
            if top is None:
                gated_tokens.add(token_id)
                continue
            if not cls._book_source_is_ws(top):
                maker_prereq_failure_by_token.setdefault(token_id, "maker_requires_ws_book_source")
                continue
            gated_tokens.add(token_id)
        return gated_tokens

    @staticmethod
    def _market_base_key_from_market_key(market_key: Any) -> str:
        normalized = str(market_key or "").strip()
        if not normalized:
            return ""
        if "|" not in normalized:
            return normalized
        base_key, last_part = normalized.rsplit("|", 1)
        if str(last_part or "").strip().upper() in {"YES", "NO"} and str(base_key or "").strip():
            return str(base_key).strip()
        return normalized

    def _market_ref_for_market_key(self, market_key: Any) -> Optional[str]:
        base_key = self._market_base_key_from_market_key(market_key)
        return base_key or None

    def _market_ref_for_token_ids(self, token_ids: Collection[str]) -> Optional[str]:
        refs = {
            self._market_ref_for_market_key(self.token_market_key_by_token.get(str(token_id).strip(), ""))
            for token_id in token_ids
            if str(token_id).strip()
        }
        refs = {ref for ref in refs if ref}
        if len(refs) != 1:
            return None
        return next(iter(refs))

    def _taker_window_submit_lock_key_for_token(self, token_id: Any) -> str:
        normalized_token_id = str(token_id or "").strip()
        market_key = str(self.token_market_key_by_token.get(normalized_token_id, "")).strip()
        base_key = self._market_base_key_from_market_key(market_key)
        return base_key or normalized_token_id

    @staticmethod
    def _build_maker_handoff_no_submission_reason_by_token(
        *,
        maker_no_submission_reason_by_token: Optional[Dict[str, str]],
        maker_prereq_failure_by_token: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        handoff_reasons: Dict[str, str] = {
            str(token_id): str(reason).strip().lower()
            for token_id, reason in dict(maker_no_submission_reason_by_token or {}).items()
            if str(token_id).strip() and str(reason).strip()
        }
        for token_id, reason in dict(maker_prereq_failure_by_token or {}).items():
            normalized_token_id = str(token_id).strip()
            normalized_reason = str(reason).strip().lower()
            if not normalized_token_id or not normalized_reason:
                continue
            handoff_reasons.setdefault(normalized_token_id, normalized_reason)
        return handoff_reasons

    def _emit_edge_evaluation(
        self,
        *,
        token_id: str,
        target_ref: Optional[str] = None,
        source_token_id: Optional[str] = None,
        source_target_ref: Optional[str] = None,
        evaluation_scope: str,
        lifecycle_phase: Optional[str] = None,
        lineage_stage: Optional[str] = None,
        owned_market_ref: Optional[str] = None,
        challenger_market_ref: Optional[str] = None,
        ownership_drop_reason: Optional[str] = None,
        ownership_replacement_reason: Optional[str] = None,
        market_truth_required: Optional[bool] = None,
        maker_phase_allowed: Optional[bool] = None,
        taker_phase_allowed: Optional[bool] = None,
        maker_gate_open: Optional[bool] = None,
        taker_gate_open: Optional[bool] = None,
        time_remaining_sec: Optional[float],
        fair_probability: Optional[float],
        market_probability: Optional[float],
        edge_value: Optional[float],
        oracle_tick_age_sec: Optional[float],
        latency_state: str,
        maker_gate_state: bool,
        taker_gate_state: bool,
        action_taken: str,
        block_reason: Optional[str],
        submitted: bool,
        filled: bool,
        open_order_cleanup_required: Optional[bool] = None,
        settlement_hold_required: Optional[bool] = None,
        unresolved_lifecycle_obligation: Optional[bool] = None,
        cancel_fail_closed: Optional[bool] = None,
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
        pair_truth_class: Optional[str] = None,
        pair_truth_basis: Optional[str] = None,
        pair_truth_owner_scope: Optional[str] = None,
        pair_missing_token_count: Optional[int] = None,
        pair_one_sided_token_count: Optional[int] = None,
        pair_authoritative_token_count: Optional[int] = None,
        decision_input_type_override: Optional[str] = None,
        decision_input_data_class_override: Optional[str] = None,
        required_min_edge: Optional[float] = None,
        taker_submit_reject_reason: Optional[str] = None,
        held_net_shares: Optional[float] = None,
        held_open_order_present: Optional[bool] = None,
        financial_posture_class: Optional[str] = None,
        secondary_fair_probability: Optional[float] = None,
        secondary_oracle_status: Optional[str] = None,
        secondary_oracle_confirmation: Optional[bool] = None,
        chainlink_spot_price: Optional[float] = None,
        secondary_oracle_spot_price: Optional[float] = None,
        secondary_oracle_price_delta_abs: Optional[float] = None,
        secondary_oracle_price_delta_bps: Optional[float] = None,
        open_maker_orders_total: Optional[int] = None,
        probe_favored_side: Optional[str] = None,
        probe_visible_depth_shares: Optional[float] = None,
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
        normalized_source_token_id = str(source_token_id or "").strip() or None
        normalized_source_target_ref = str(source_target_ref or "").strip() or None
        if normalized_target_ref is None and normalized_token_id:
            normalized_target_ref = hashlib.sha256(normalized_token_id.encode("utf-8")).hexdigest()[:16]
        if normalized_source_target_ref is None and normalized_source_token_id:
            normalized_source_target_ref = hashlib.sha256(normalized_source_token_id.encode("utf-8")).hexdigest()[:16]
        normalized_lifecycle_phase = str(lifecycle_phase or "").strip().lower() or "scan"
        if maker_phase_allowed is None:
            maker_phase_allowed = normalized_lifecycle_phase == "maker_window"
        if taker_phase_allowed is None:
            taker_phase_allowed = normalized_lifecycle_phase == "taker_window"
        if maker_gate_open is None:
            maker_gate_open = maker_gate_state
        if taker_gate_open is None:
            taker_gate_open = taker_gate_state
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
            "source_token_id": normalized_source_token_id,
            "source_target_ref": normalized_source_target_ref,
            "evaluation_scope": normalized_scope,
            "cycle_index": (int(cycle_index) if cycle_index is not None else int(self._doctrine_cycle_index)),
            **lifecycle_phase_surface_fields(lifecycle_phase=normalized_lifecycle_phase),
            **lineage_stage_surface_fields(lineage_stage=lineage_stage),
            **ownership_surface_fields(
                owned_market_ref=owned_market_ref,
                challenger_market_ref=challenger_market_ref,
                ownership_drop_reason=ownership_drop_reason,
                ownership_replacement_reason=ownership_replacement_reason,
            ),
            **market_truth_surface_fields(
                market_truth_required=(
                    bool(market_truth_required) if market_truth_required is not None else bool(owned_market_ref)
                )
            ),
            **lane_permission_surface_fields(
                maker_phase_allowed=bool(maker_phase_allowed),
                taker_phase_allowed=bool(taker_phase_allowed),
                maker_gate_open=bool(maker_gate_open),
                taker_gate_open=bool(taker_gate_open),
            ),
            "time_remaining_sec": (
                float(time_remaining_sec) if isinstance(time_remaining_sec, (int, float)) else None
            ),
            "fair_probability": (float(fair_probability) if isinstance(fair_probability, (int, float)) else None),
            "market_probability": (
                float(market_probability) if isinstance(market_probability, (int, float)) else None
            ),
            "edge_value": (float(edge_value) if isinstance(edge_value, (int, float)) else None),
            "required_min_edge": (
                float(required_min_edge) if isinstance(required_min_edge, (int, float)) else None
            ),
            "oracle_tick_age_sec": (
                float(oracle_tick_age_sec) if isinstance(oracle_tick_age_sec, (int, float)) else None
            ),
            "latency_state": str(latency_state or "").strip().lower() or None,
            **lifecycle_surface_fields(
                open_order_cleanup_required=bool(open_order_cleanup_required),
                settlement_hold_required=bool(settlement_hold_required),
                unresolved_lifecycle_obligation=bool(unresolved_lifecycle_obligation),
                cancel_fail_closed=bool(cancel_fail_closed),
            ),
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
            "held_net_shares": (
                float(held_net_shares) if isinstance(held_net_shares, (int, float)) else None
            ),
            "held_open_order_present": bool(held_open_order_present),
            "financial_posture_class": (
                str(financial_posture_class or "").strip().upper() or None
            ),
            "secondary_fair_probability": (
                float(secondary_fair_probability)
                if isinstance(secondary_fair_probability, (int, float))
                else None
            ),
            "secondary_oracle_status": (
                str(secondary_oracle_status or "").strip().lower() or None
            ),
            "secondary_oracle_confirmation": (
                bool(secondary_oracle_confirmation)
                if secondary_oracle_confirmation is not None
                else None
            ),
            "chainlink_spot_price": (
                float(chainlink_spot_price)
                if isinstance(chainlink_spot_price, (int, float))
                else None
            ),
            "secondary_oracle_spot_price": (
                float(secondary_oracle_spot_price)
                if isinstance(secondary_oracle_spot_price, (int, float))
                else None
            ),
            "secondary_oracle_price_delta_abs": (
                float(secondary_oracle_price_delta_abs)
                if isinstance(secondary_oracle_price_delta_abs, (int, float))
                else None
            ),
            "secondary_oracle_price_delta_bps": (
                float(secondary_oracle_price_delta_bps)
                if isinstance(secondary_oracle_price_delta_bps, (int, float))
                else None
            ),
            "open_maker_orders_total": (
                int(open_maker_orders_total)
                if isinstance(open_maker_orders_total, (int, float))
                else None
            ),
            "probe_favored_side": (
                str(probe_favored_side or "").strip().upper() or None
            ),
            "probe_visible_depth_shares": (
                float(probe_visible_depth_shares)
                if isinstance(probe_visible_depth_shares, (int, float))
                else None
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
            elif normalized_market_reference_mode == "backfilled_paired_touch":
                normalized_market_reference_basis = "ws_recent_paired_touch"
            else:
                normalized_market_reference_basis = "missing"
        normalized_market_reference_confidence = str(market_reference_confidence or "").strip().lower()
        if not normalized_market_reference_confidence:
            if normalized_market_reference_mode == "direct_midpoint":
                normalized_market_reference_confidence = "authoritative"
            elif normalized_market_reference_mode == "backfilled_paired_touch":
                normalized_market_reference_confidence = "authoritative"
            else:
                normalized_market_reference_confidence = "none"
        normalized_market_reference_source_side = str(market_reference_source_side or "").strip().lower()
        if normalized_market_reference_source_side not in {"bid", "ask", "paired"}:
            normalized_market_reference_source_side = "none"
        fallback_used = (
            bool(market_reference_fallback_used)
            if market_reference_fallback_used is not None
            else (normalized_market_reference_mode == "backfilled_paired_touch")
        )
        normalized_market_reference_class = str(market_reference_class or "").strip().lower()
        if not normalized_market_reference_class:
            if normalized_market_reference_mode == "backfilled_paired_touch":
                normalized_market_reference_class = "authoritative"
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
        payload["pair_truth_class"] = str(pair_truth_class or "").strip().lower() or None
        payload["pair_truth_basis"] = str(pair_truth_basis or "").strip().lower() or None
        payload["pair_truth_owner_scope"] = str(pair_truth_owner_scope or "").strip().lower() or None
        payload["pair_missing_token_count"] = (
            int(pair_missing_token_count) if isinstance(pair_missing_token_count, (int, float)) else None
        )
        payload["pair_one_sided_token_count"] = (
            int(pair_one_sided_token_count) if isinstance(pair_one_sided_token_count, (int, float)) else None
        )
        payload["pair_authoritative_token_count"] = (
            int(pair_authoritative_token_count) if isinstance(pair_authoritative_token_count, (int, float)) else None
        )
        decision_input_source = str(payload.get("book_source") or "").strip().lower()
        if decision_input_type_override is not None:
            decision_input_type = str(decision_input_type_override or "").strip().lower()
        else:
            decision_input_type = self._decision_input_type_from_source(decision_input_source)
        if decision_input_type not in {"observed_live", "observed_other", "replayed", "emulated", "unknown"}:
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
            elif decision_input_type in {"replayed", "observed_other"}:
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
        pair_truth_by_base_key: Optional[Dict[str, Dict[str, Any]]] = None,
        stage_info_by_token: Dict[str, Dict[str, Any]],
        maker_eval_token_ids: set[str],
        maker_submitted_token_ids: set[str],
        maker_submitted_order_ids_by_token: Dict[str, List[str]],
        maker_no_submission_reason_by_token: Optional[Dict[str, str]],
        maker_no_submission_category_by_token: Optional[Dict[str, str]],
        maker_prereq_failure_by_token: Dict[str, str],
        fair_probability_by_token: Dict[str, float],
        maker_competitiveness_profiles_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
        oracle_tick_age_sec: Optional[float],
        latency_state: str,
        cycle_index: int,
        maker_market_reference_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        pair_truth_by_base_key = pair_truth_by_base_key or {}
        maker_no_submission_reason_by_token = maker_no_submission_reason_by_token or {}
        maker_no_submission_category_by_token = maker_no_submission_category_by_token or {}
        maker_competitiveness_profiles_by_token = maker_competitiveness_profiles_by_token or {}
        maker_market_reference_by_token = maker_market_reference_by_token or {}
        open_maker_orders_total: Optional[int] = None
        try:
            open_maker_orders_total = int(len(self.tx_manager.get_open_orders()))
        except ORDER_TRANSPORT_EXCEPTIONS:
            open_maker_orders_total = None
        for token_id in sorted(str(x) for x in maker_eval_token_ids):
            info = stage_info_by_token.get(token_id, {})
            lineage_stage = lineage_stage_from_payload(info)
            if lineage_stage == STAGE_UNKNOWN:
                lineage_stage = str(info.get("stage") or "").strip().upper() or STAGE_UNKNOWN
            lifecycle_phase = str(
                info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or lifecycle_phase_from_payload(info)
                or ""
            ).strip().lower() or "scan"
            default_maker_gate_open = lifecycle_phase == "maker_window"
            default_taker_gate_open = lifecycle_phase == "taker_window"
            maker_gate_state = bool(info.get(EDGE_MAKER_GATE_OPEN_FIELD, default_maker_gate_open))
            taker_gate_state = bool(info.get(EDGE_TAKER_GATE_OPEN_FIELD, default_taker_gate_open))
            time_remaining_sec = info.get("sec_to_expiry")
            open_order_cleanup_required = bool(info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False))
            settlement_hold_required = bool(info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False))
            unresolved_lifecycle_obligation = bool(info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False))
            cancel_fail_closed = bool(info.get(EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD, False))
            top = books.get(token_id)
            profile_context = dict(
                (maker_competitiveness_profiles_by_token.get(token_id) or {}).get("context") or {}
            )
            maker_prereq_failure_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip()
            market_reference = dict(maker_market_reference_by_token.get(token_id) or {})
            if not market_reference:
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
                    lifecycle_phase=lifecycle_phase,
                    lineage_stage=lineage_stage,
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
                if open_order_cleanup_required:
                    block_reason = "open_order_cleanup_required"
                elif settlement_hold_required:
                    block_reason = "settlement_hold_required"
                elif not maker_gate_state:
                    block_reason = "phase_disallow_maker"
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
            financial_posture_class = self._resolve_financial_posture_class(
                stage_info_by_token={token_id: info}
            )
            probe_favored_side: Optional[str] = None
            if isinstance(edge_value, (int, float)):
                if float(edge_value) > 1e-12:
                    probe_favored_side = "BUY"
                elif float(edge_value) < -1e-12:
                    probe_favored_side = "SELL"
            probe_visible_depth_shares: Optional[float] = None
            if probe_favored_side == "BUY" and isinstance(getattr(top, "best_bid_size", None), (int, float)):
                probe_visible_depth_shares = float(getattr(top, "best_bid_size"))
            elif probe_favored_side == "SELL" and isinstance(getattr(top, "best_ask_size", None), (int, float)):
                probe_visible_depth_shares = float(getattr(top, "best_ask_size"))
            market_base_key = self._market_base_key_from_market_key(info.get("market_key"))
            pair_truth = self._pair_truth_for_token(
                pair_truth_by_base_key=pair_truth_by_base_key,
                market_base_key=market_base_key,
                token_id=token_id,
            )
            edge_lifecycle_phase = str(
                info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or lifecycle_phase_from_payload(info)
                or ""
            ).strip().lower() or "scan"

            self._emit_edge_evaluation(
                token_id=token_id,
                target_ref=self._target_ref_for_token(token_id),
                evaluation_scope=EDGE_EVAL_SCOPE_MAKER,
                lifecycle_phase=edge_lifecycle_phase,
                lineage_stage=lineage_stage,
                owned_market_ref=(
                    info.get(EDGE_OWNED_MARKET_REF_FIELD)
                    or self._market_ref_for_token_ids(self.token_ids)
                ),
                challenger_market_ref=(
                    info.get(EDGE_CHALLENGER_MARKET_REF_FIELD)
                    or self._market_ref_for_token_ids(self._challenger_token_ids)
                ),
                ownership_drop_reason=info.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                ownership_replacement_reason=info.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
                market_truth_required=bool(
                    info.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD, bool(self.token_ids))
                ),
                maker_phase_allowed=bool(
                    info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, edge_lifecycle_phase == "maker_window")
                ),
                taker_phase_allowed=bool(
                    info.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, edge_lifecycle_phase == "taker_window")
                ),
                maker_gate_open=bool(maker_gate_state),
                taker_gate_open=bool(taker_gate_state),
                time_remaining_sec=time_remaining_sec if isinstance(time_remaining_sec, (int, float)) else None,
                fair_probability=fair_probability if isinstance(fair_probability, (int, float)) else None,
                market_probability=market_probability if isinstance(market_probability, (int, float)) else None,
                edge_value=edge_value if isinstance(edge_value, (int, float)) else None,
                oracle_tick_age_sec=oracle_tick_age_sec,
                latency_state=latency_state,
                maker_gate_state=maker_gate_state,
                taker_gate_state=taker_gate_state,
                open_order_cleanup_required=open_order_cleanup_required,
                settlement_hold_required=settlement_hold_required,
                unresolved_lifecycle_obligation=unresolved_lifecycle_obligation,
                cancel_fail_closed=cancel_fail_closed,
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
                pair_truth_class=str(pair_truth.get("pair_truth_class") or ""),
                pair_truth_basis=str(pair_truth.get("pair_truth_basis") or ""),
                pair_truth_owner_scope=str(pair_truth.get("pair_truth_owner_scope") or ""),
                pair_missing_token_count=pair_truth.get("pair_missing_token_count"),
                pair_one_sided_token_count=pair_truth.get("pair_one_sided_token_count"),
                pair_authoritative_token_count=pair_truth.get("pair_authoritative_token_count"),
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
                held_net_shares=info.get("held_net_shares"),
                held_open_order_present=info.get("held_open_order_present"),
                financial_posture_class=financial_posture_class,
                secondary_fair_probability=profile_context.get("secondary_fair_probability"),
                secondary_oracle_status=profile_context.get("secondary_oracle_status"),
                secondary_oracle_confirmation=profile_context.get("secondary_oracle_confirmation"),
                chainlink_spot_price=profile_context.get("chainlink_spot_price"),
                secondary_oracle_spot_price=profile_context.get("secondary_oracle_spot_price"),
                secondary_oracle_price_delta_abs=profile_context.get("secondary_oracle_price_delta_abs"),
                secondary_oracle_price_delta_bps=profile_context.get("secondary_oracle_price_delta_bps"),
                open_maker_orders_total=open_maker_orders_total,
                probe_favored_side=probe_favored_side,
                probe_visible_depth_shares=probe_visible_depth_shares,
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
            return "extreme_only_on_arrival"
        return "normal_on_arrival"

    def _on_market_key_transition(self, token_id: str, old_key: str, new_key: str) -> None:
        now = utc_now()
        now_mono = time.monotonic()
        old_lock_key = self._market_base_key_from_market_key(old_key) or str(token_id or "").strip()
        self._market_entry_mono_by_token[token_id] = now_mono
        self._market_entry_cycle_by_token[token_id] = int(self._doctrine_cycle_index)
        self.last_midpoint_by_token.pop(token_id, None)
        self.last_midpoint_ts_mono_by_token.pop(token_id, None)
        self.last_volatility_by_token.pop(token_id, None)
        self._last_taker_submit_mono_by_token.pop(token_id, None)
        if old_lock_key and not any(
            self._market_base_key_from_market_key(current_key) == old_lock_key
            for current_token_id, current_key in self.token_market_key_by_token.items()
            if str(current_token_id or "").strip() and str(current_token_id or "").strip() != str(token_id or "").strip()
        ):
            self._taker_window_submit_lock_keys.discard(old_lock_key)
        self._last_doctrine_signature_by_token.pop(token_id, None)
        self._last_doctrine_prereq_failure_by_token.pop(token_id, None)
        self._last_lifecycle_phase_by_token.pop(token_id, None)
        self._clear_maker_ws_touch_cache(token_id)
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

    def _run_taker(
        self,
        *,
        books: Dict[str, Any],
        pair_truth_by_base_key: Optional[Dict[str, Dict[str, Any]]] = None,
        fair_probability_by_token: Dict[str, float],
        realized_volatility_by_token: Optional[Dict[str, float]] = None,
        secondary_fair_probability_by_token: Optional[Dict[str, float]] = None,
        secondary_oracle_base_status: str = "disabled",
        token_ids: list[str],
        stage_info_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
        oracle_tick_age_sec: Optional[float] = None,
        latency_snapshot: Optional[LatencySnapshot] = None,
        mode_state: str = MODE_NORMAL,
        lag_ready_for_taker: bool = True,
        lag_verified_token_ids: Optional[list[str]] = None,
        taker_ramp_allowed: bool = True,
        cycle_index: Optional[int] = None,
        oracle_fresh: bool = True,
        maker_submitted_token_ids: Optional[set[str]] = None,
        maker_no_submission_reason_by_token: Optional[Dict[str, str]] = None,
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
        max_orders = max(0, int(self.taker_max_orders_per_cycle))
        stage_info_by_token = stage_info_by_token or {}
        lag_verified_set = {str(x) for x in (lag_verified_token_ids or [])}
        maker_submitted_token_ids = {str(token_id) for token_id in (maker_submitted_token_ids or set())}
        maker_no_submission_reason_by_token = {
            str(token_id): str(reason).strip().lower()
            for token_id, reason in dict(maker_no_submission_reason_by_token or {}).items()
            if str(token_id).strip() and str(reason).strip()
        }
        latency_state = (
            str(latency_snapshot.state).strip().lower()
            if isinstance(latency_snapshot, LatencySnapshot)
            else str(self._last_latency_state or "").strip().lower()
        )
        competitiveness_enabled = bool(self.taker_competitiveness_cfg.enabled)
        stable_cycle_index = int(self._doctrine_cycle_index if cycle_index is None else cycle_index)
        secondary_fair_probability_by_token = dict(secondary_fair_probability_by_token or {})
        pair_truth_by_base_key = pair_truth_by_base_key or {}
        secondary_oracle_base_status = str(secondary_oracle_base_status or "unknown").strip().lower() or "unknown"

        # Use limited taker budget on strongest edge opportunities first.
        token_order = sorted({str(token_id) for token_id in token_ids})
        token_order.sort(
            key=lambda token_id: (
                0,
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
        active_token_ids = {str(token_id).strip() for token_id in token_order if str(token_id).strip()}
        open_order_token_ids = self._open_order_token_ids()
        normal_side_policy = (
            str(self.taker_competitiveness_cfg.normal_side_policy or "buy_expected_winner_only").strip().lower()
            or "buy_expected_winner_only"
        )
        allow_complement_buy_route = bool(self.taker_competitiveness_cfg.allow_complement_buy_route)
        min_visible_fill_ratio = max(0.0, float(self.taker_competitiveness_cfg.min_visible_fill_ratio))

        def _build_route_block_decision(
            *,
            decision_token_id: str,
            decision_stage: str,
            decision_sec_to_expiry: Optional[float],
            edge_signed_value: Optional[float],
            required_min_edge_value: float,
            token_score_value: Optional[float],
            block_reason_value: str,
            side_class_value: str,
        ) -> TakerDecision:
            edge_abs_value = abs(float(edge_signed_value)) if isinstance(edge_signed_value, (int, float)) else 0.0
            conviction_score_value = self.taker_competitiveness_engine._conviction(
                edge_abs=edge_abs_value,
                token_score=token_score_value,
            )
            timing_window_class_value = self.taker_competitiveness_engine._timing_window_class(
                str(decision_stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN,
                decision_sec_to_expiry,
            )
            return TakerDecision(
                token_id=str(decision_token_id),
                stage=str(decision_stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN,
                should_submit=False,
                block_reason=str(block_reason_value or "taker_submit_rejected"),
                side="BUY",
                price=None,
                edge_abs=float(edge_abs_value),
                required_min_edge=float(required_min_edge_value),
                conviction_score=float(conviction_score_value),
                timing_window_class=timing_window_class_value,
                aggressiveness_level="none",
                price_aggress_bps_applied=0.0,
                target_usd_requested=0.0,
                target_usd_resolved=0.0,
                hard_min_floor_applied=False,
                hard_min_unachievable=False,
                submit_capable_static=False,
                submit_capable_dynamic_predicted=None,
                predicted_dynamic_feasible=None,
                predicted_feasible_target_usd=None,
                predicted_reject_reason=None,
                preview_authority="none",
                dynamic_size_capped_by_risk=False,
                multi_oracle_confirmation=False,
                multi_oracle_boost_eligible=False,
                multi_oracle_boost_applied=False,
                multi_oracle_status="unknown",
                sec_to_expiry=decision_sec_to_expiry,
                normal_side_policy=normal_side_policy,
                normal_taker_side_class=str(side_class_value or "unknown"),
            )

        def _log_taker_decision(
            *,
            source_token_id: str,
            submit_token_id: str,
            source_midpoint: Optional[float],
            source_fair_probability: Optional[float],
            source_edge_value: Optional[float],
            submit_midpoint: Optional[float],
            submit_fair_probability: Optional[float],
            submit_edge_value: Optional[float],
            confidence_score_value: float,
            source_token_score_value: Optional[float],
            submit_token_score_value: Optional[float],
            cooldown_sec_value: float,
            complement_token_id: Optional[str],
            decision_info_row: Dict[str, Any],
            decision_row: TakerDecision,
        ) -> None:
            decision_ts_utc = utc_iso()
            decision_lifecycle_phase = str(
                decision_info_row.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or legacy_stage_to_lifecycle_phase(decision_row.stage)
                or ""
            ).strip().lower() or "scan"
            self.events.log_event(
                EVENT_TAKER_DECISION,
                {
                    "ts_utc": decision_ts_utc,
                    "ts_event_utc": decision_ts_utc,
                    "ts_decision_utc": decision_ts_utc,
                    "run_id": self.run_id,
                    "submission_lane": "taker",
                    "token_id": str(submit_token_id),
                    "source_token_id": str(source_token_id),
                    "submit_token_id": str(submit_token_id),
                    "complement_token_id": (
                        str(complement_token_id).strip() if str(complement_token_id or "").strip() else None
                    ),
                    "complement_route_applied": bool(
                        str(complement_token_id or "").strip()
                        and str(submit_token_id).strip() != str(source_token_id).strip()
                    ),
                    "midpoint": submit_midpoint,
                    "fair_probability": submit_fair_probability,
                    "edge": submit_edge_value,
                    "source_midpoint": source_midpoint,
                    "source_fair_probability": source_fair_probability,
                    "source_edge": source_edge_value,
                    "confidence_score": float(confidence_score_value),
                    "normal_taker_source_token_score": (
                        float(source_token_score_value)
                        if isinstance(source_token_score_value, (int, float))
                        else None
                    ),
                    "normal_taker_submit_token_score": (
                        float(submit_token_score_value)
                        if isinstance(submit_token_score_value, (int, float))
                        else None
                    ),
                    "cooldown_sec_applied": float(cooldown_sec_value),
                    **lifecycle_phase_surface_fields(lifecycle_phase=decision_lifecycle_phase),
                    **ownership_surface_fields(
                        owned_market_ref=decision_info_row.get(EDGE_OWNED_MARKET_REF_FIELD),
                        challenger_market_ref=decision_info_row.get(EDGE_CHALLENGER_MARKET_REF_FIELD),
                        ownership_drop_reason=decision_info_row.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                        ownership_replacement_reason=decision_info_row.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
                    ),
                    **market_truth_surface_fields(
                        market_truth_required=bool(
                            decision_info_row.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD, bool(submit_token_id))
                        )
                    ),
                    **lane_permission_surface_fields(
                        maker_phase_allowed=bool(
                            decision_info_row.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, decision_lifecycle_phase == "maker_window")
                        ),
                        taker_phase_allowed=bool(
                            decision_info_row.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, decision_lifecycle_phase == "taker_window")
                        ),
                        maker_gate_open=bool(
                            decision_info_row.get(EDGE_MAKER_GATE_OPEN_FIELD, decision_lifecycle_phase == "maker_window")
                        ),
                        taker_gate_open=bool(
                            decision_info_row.get(EDGE_TAKER_GATE_OPEN_FIELD, decision_lifecycle_phase == "taker_window")
                        ),
                    ),
                    **decision_row.as_event_payload(),
                },
            )

        for token_id in token_order:
            info = stage_info_by_token.get(token_id, {})
            stage = self._compat_stage_from_lifecycle_info(info)
            lifecycle_phase = str(
                info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or lifecycle_phase_from_payload(info)
                or ""
            ).strip().lower() or "scan"
            lineage_stage = lineage_stage_from_payload(info)
            if lineage_stage == STAGE_UNKNOWN:
                lineage_stage = str(info.get("stage") or "").strip().upper() or STAGE_UNKNOWN
            time_remaining_sec = info.get("sec_to_expiry")
            sec_to_expiry = (
                float(time_remaining_sec)
                if isinstance(time_remaining_sec, (int, float))
                else None
            )
            open_order_cleanup_required = bool(info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False))
            settlement_hold_required = bool(info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False))
            unresolved_lifecycle_obligation = bool(info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False))
            cancel_fail_closed = bool(info.get(EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD, False))
            maker_gate_state = bool(
                info.get(EDGE_MAKER_GATE_OPEN_FIELD, lifecycle_phase == "maker_window")
            )
            taker_gate_state = bool(
                info.get(EDGE_TAKER_GATE_OPEN_FIELD, lifecycle_phase == "taker_window")
            )
            top = books.get(token_id)
            market_reference = self._resolve_taker_market_reference(top=top)
            midpoint = market_reference.get("market_probability")
            fair = fair_probability_by_token.get(token_id)
            edge = compute_edge_value(
                fair_probability=fair,
                market_probability=midpoint,
            )
            decision_token_id = str(token_id)
            decision_info = info
            decision_stage = str(stage or STAGE_UNKNOWN)
            decision_time_remaining_sec = time_remaining_sec
            decision_sec_to_expiry = sec_to_expiry
            decision_top = top
            decision_market_reference = market_reference
            decision_midpoint = midpoint
            decision_fair = fair
            decision_edge = edge
            decision_source_token_id = str(token_id)
            decision_source_midpoint = midpoint
            decision_source_fair = fair
            decision_source_edge = edge
            complement_token_id: Optional[str] = None
            normal_taker_side_class = (
                "buy_expected_winner"
                if isinstance(edge, (int, float)) and float(edge) > 0.0
                else "unknown"
            )
            validation = validate_edge_inputs(
                EdgeInputSnapshot(
                    fair_probability=fair,
                    market_probability=midpoint,
                    time_remaining_sec=time_remaining_sec,
                    oracle_tick_age_sec=oracle_tick_age_sec,
                    latency_state=latency_state,
                    lineage_stage=stage,
                    evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
                ),
                oracle_max_tick_age_sec=float(self.doctrine_oracle_max_tick_age_sec),
                require_latency_state=bool(self.latency_verifier.require_armed_for_taker),
            )

            action_taken = EDGE_ACTION_NONE
            block_reason: Optional[str] = None
            was_submitted = False
            was_filled = False
            emitted_order_id: Optional[str] = None
            taker_submit_reject_reason: Optional[str] = None
            decision_target_ref = self._target_ref_for_token(token_id)
            required_min_edge = self._resolve_taker_required_min_edge(stage)
            decision: Optional[TakerDecision] = None

            if not self.taker_enabled:
                block_reason = "taker_disabled"
            elif max_orders <= 0:
                block_reason = "taker_budget_disabled"
            elif open_order_cleanup_required:
                block_reason = "open_order_cleanup_required"
            elif settlement_hold_required:
                block_reason = "settlement_hold_required"
            elif mode_state == MODE_MAKER_ONLY:
                block_reason = "operating_mode_maker_only"
            elif mode_state == MODE_SAFE_STOP:
                block_reason = "operating_mode_safe_stop"
            elif mode_state != MODE_NORMAL:
                block_reason = "operating_mode_non_normal"
            elif not lag_ready_for_taker:
                block_reason = "latency_not_armed"
            elif not taker_ramp_allowed:
                block_reason = "ramp_taker_disabled"
            elif self.taker_require_lag_verification and token_id not in lag_verified_set:
                block_reason = "token_lag_not_verified"
            elif not taker_gate_state:
                block_reason = "normal_taker_authority_closed"
            elif not oracle_fresh:
                block_reason = "oracle_unavailable_or_stale"
            elif (
                self.doctrine_mode == "canonical"
                and top is not None
                and (not self._book_source_is_ws(top))
            ):
                block_reason = "taker_requires_ws_book_source"
            elif not validation.valid:
                block_reason = str(validation.reason_code or "")
            elif edge is None:
                block_reason = "edge_value_invalid"
            elif abs(float(edge)) < float(required_min_edge):
                block_reason = "edge_below_min"
            else:
                if (
                    block_reason is None
                    and isinstance(edge, (int, float))
                    and float(edge) < 0.0
                    and normal_side_policy == "buy_expected_winner_only"
                ):
                    if not allow_complement_buy_route:
                        block_reason = "complement_route_disabled_pending_validation"
                        normal_taker_side_class = "complement_route_disabled"
                    else:
                        resolved_complement_token_id, complement_block_reason = self._resolve_complement_token_id(
                            token_id=token_id,
                            active_token_ids=active_token_ids,
                        )
                        if not resolved_complement_token_id:
                            block_reason = complement_block_reason or "complement_token_mapping_unavailable"
                            normal_taker_side_class = "complement_mapping_unavailable"
                        else:
                            complement_token_id = str(resolved_complement_token_id)
                            decision_token_id = str(resolved_complement_token_id)
                            decision_info = stage_info_by_token.get(decision_token_id, info) or info
                            decision_stage = self._compat_stage_from_lifecycle_info(
                                decision_info,
                                fallback_stage=stage,
                            )
                            required_min_edge = self._resolve_taker_required_min_edge(decision_stage)
                            decision_time_remaining_sec = decision_info.get("sec_to_expiry", time_remaining_sec)
                            decision_sec_to_expiry = (
                                float(decision_time_remaining_sec)
                                if isinstance(decision_time_remaining_sec, (int, float))
                                else None
                            )
                            decision_top = books.get(decision_token_id)
                            decision_market_reference = self._resolve_taker_market_reference(top=decision_top)
                            decision_midpoint = decision_market_reference.get("market_probability")
                            decision_fair = fair_probability_by_token.get(decision_token_id)
                            decision_edge = compute_edge_value(
                                fair_probability=decision_fair,
                                market_probability=decision_midpoint,
                            )
                            decision_target_ref = self._target_ref_for_token(decision_token_id)
                            normal_taker_side_class = "complement_buy"
                            if decision_fair is None:
                                block_reason = "complement_token_fair_probability_unavailable"
                                normal_taker_side_class = "complement_fair_unavailable"
                            elif decision_top is None or not isinstance(
                                getattr(decision_top, "best_ask_price", None),
                                (int, float),
                            ):
                                block_reason = "complement_token_price_unavailable"
                                normal_taker_side_class = "complement_price_unavailable"
                            else:
                                complement_validation = validate_edge_inputs(
                                    EdgeInputSnapshot(
                                        fair_probability=decision_fair,
                                        market_probability=decision_midpoint,
                                        time_remaining_sec=decision_time_remaining_sec,
                                        oracle_tick_age_sec=oracle_tick_age_sec,
                                        latency_state=latency_state,
                                        lineage_stage=decision_stage,
                                        evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
                                    ),
                                    oracle_max_tick_age_sec=float(self.doctrine_oracle_max_tick_age_sec),
                                    require_latency_state=bool(self.latency_verifier.require_armed_for_taker),
                                )
                                if not complement_validation.valid:
                                    block_reason = str(complement_validation.reason_code or "")
                                elif decision_edge is None:
                                    block_reason = "edge_value_invalid"
                                elif float(decision_edge) <= 0.0:
                                    block_reason = "edge_below_min"
                                    normal_taker_side_class = "complement_edge_not_positive"
                                elif float(decision_edge) < float(required_min_edge):
                                    block_reason = "edge_below_min"
                now_mono = time.monotonic()
                cooldown_token_id = decision_token_id
                taker_window_lock_token_id = decision_source_token_id
                taker_window_lock_key = self._taker_window_submit_lock_key_for_token(taker_window_lock_token_id)
                last_submit = self._last_taker_submit_mono_by_token.get(cooldown_token_id)
                cooldown_sec = self._resolve_taker_cooldown_sec(decision_stage)
                if taker_window_lock_key and taker_window_lock_key in self._taker_window_submit_lock_keys:
                    block_reason = "taker_window_already_submitted"
                elif (
                    last_submit is not None
                    and (now_mono - last_submit) < cooldown_sec
                ):
                    block_reason = "taker_token_cooldown"
                else:
                    score_token_id = decision_token_id
                    submit_confidence_score = float(self.latency_verifier.token_score(score_token_id))
                    source_confidence_score = float(submit_confidence_score)
                    confidence_score = float(submit_confidence_score)
                    if (
                        str(normal_taker_side_class or "").strip().lower() == "complement_buy"
                    ):
                        source_confidence_score = float(
                            self.latency_verifier.token_score(decision_source_token_id)
                        )
                        confidence_score = max(float(submit_confidence_score), float(source_confidence_score))
                    if self.latency_verifier.score_enabled:
                        score = float(confidence_score)
                        if score < self.latency_verifier.score_min_for_taker:
                            block_reason = "token_score_below_taker_min"
                    if (
                        competitiveness_enabled
                        and decision is None
                        and str(normal_taker_side_class or "").strip().lower().startswith("complement_")
                        and block_reason is not None
                    ):
                        decision = _build_route_block_decision(
                            decision_token_id=decision_token_id,
                            decision_stage=decision_stage,
                            decision_sec_to_expiry=decision_sec_to_expiry,
                            edge_signed_value=decision_edge,
                            required_min_edge_value=float(required_min_edge),
                            token_score_value=float(confidence_score),
                            block_reason_value=str(block_reason),
                            side_class_value=normal_taker_side_class,
                        )
                        _log_taker_decision(
                            source_token_id=decision_source_token_id,
                            submit_token_id=decision_token_id,
                            source_midpoint=(
                                float(decision_source_midpoint)
                                if isinstance(decision_source_midpoint, (int, float))
                                else None
                            ),
                            source_fair_probability=(
                                float(decision_source_fair)
                                if isinstance(decision_source_fair, (int, float))
                                else None
                            ),
                            source_edge_value=(
                                float(decision_source_edge)
                                if isinstance(decision_source_edge, (int, float))
                                else None
                            ),
                            submit_midpoint=(
                                float(decision_midpoint)
                                if isinstance(decision_midpoint, (int, float))
                                else None
                            ),
                            submit_fair_probability=(
                                float(decision_fair)
                                if isinstance(decision_fair, (int, float))
                                else None
                            ),
                            submit_edge_value=(
                                float(decision_edge)
                                if isinstance(decision_edge, (int, float))
                                else None
                            ),
                            confidence_score_value=float(confidence_score),
                            source_token_score_value=float(source_confidence_score),
                            submit_token_score_value=float(submit_confidence_score),
                            cooldown_sec_value=float(cooldown_sec),
                            complement_token_id=complement_token_id,
                            decision_info_row=decision_info,
                            decision_row=decision,
                        )
                    if block_reason is None and submitted >= max_orders:
                        block_reason = "taker_order_budget_exhausted"
                    if block_reason is None:
                        side = "BUY" if float(decision_edge) > 0.0 else "SELL"
                        decision_top_for_submit = decision_top
                        touch_price = (
                            decision_top_for_submit.best_ask_price if side == "BUY" else decision_top_for_submit.best_bid_price
                        )
                        if touch_price is None:
                            block_reason = "taker_price_unavailable"
                        else:
                            token_stats = self.latency_verifier.token_stats(decision_token_id)
                            token_median_lag_ms = (
                                float(token_stats.median_lag_ms)
                                if token_stats is not None
                                and isinstance(getattr(token_stats, "median_lag_ms", None), (int, float))
                                else None
                            )
                            multi_oracle_status = secondary_oracle_base_status
                            multi_oracle_confirmation = False
                            if competitiveness_enabled and bool(
                                self.taker_competitiveness_cfg.multi_oracle_boost_enabled
                            ):
                                secondary_fair = secondary_fair_probability_by_token.get(decision_token_id)
                                if secondary_oracle_base_status == "disabled":
                                    multi_oracle_status = "disabled"
                                elif not (
                                    isinstance(secondary_fair, (int, float))
                                    and isinstance(decision_midpoint, (int, float))
                                    and isinstance(decision_fair, (int, float))
                                    and isinstance(decision_edge, (int, float))
                                ):
                                    multi_oracle_status = "unknown"
                                else:
                                    secondary_edge = compute_edge_value(
                                        fair_probability=float(secondary_fair),
                                        market_probability=float(decision_midpoint),
                                    )
                                    if (
                                        secondary_edge is None
                                        or abs(float(secondary_edge)) <= 1e-12
                                        or abs(float(decision_edge)) <= 1e-12
                                    ):
                                        multi_oracle_status = "unknown"
                                    else:
                                        same_direction = (float(secondary_edge) > 0.0) == (float(decision_edge) > 0.0)
                                        multi_oracle_confirmation = bool(same_direction)
                                        multi_oracle_status = "confirmed" if same_direction else "direction_mismatch"
                            if competitiveness_enabled:
                                self._refresh_taker_multi_oracle_cap_from_wallet()
                                static_max_feasible_target_usd = self._taker_effective_max_target_usd(
                                    price=float(touch_price)
                                )
                                dynamic_preview: Dict[str, Any] = {
                                    "predicted_dynamic_feasible": False,
                                    "predicted_feasible_target_usd": None,
                                    "predicted_reject_reason": None,
                                    "preview_authority": "none",
                                }
                                if bool(self.taker_competitiveness_cfg.dynamic_preview_enabled):
                                    dynamic_preview = self.manager.preview_taker_dynamic_feasible_target(
                                        token_id=decision_token_id,
                                        side=side,
                                        price=float(touch_price),
                                        target_usd_cap=static_max_feasible_target_usd,
                                        top=decision_top_for_submit,
                                        reason=TAKER_CHAINLINK_REASON,
                                        stage=decision_stage,
                                        realized_volatility=(
                                            realized_volatility_by_token.get(decision_token_id)
                                            if isinstance(realized_volatility_by_token, dict)
                                            else None
                                        ),
                                        competitiveness_context={
                                            "stage": str(decision_stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN,
                                            "sec_to_expiry": (
                                                float(decision_time_remaining_sec)
                                                if isinstance(decision_time_remaining_sec, (int, float))
                                                else None
                                            ),
                                            "edge_abs": abs(float(decision_edge)),
                                        },
                                    )
                                decision = self.taker_competitiveness_engine.evaluate_batch(
                                    candidates=[
                                        TakerCandidate(
                                            token_id=decision_token_id,
                                            stage=str(decision_stage or STAGE_UNKNOWN),
                                            sec_to_expiry=(
                                                float(decision_time_remaining_sec)
                                                if isinstance(decision_time_remaining_sec, (int, float))
                                                else None
                                            ),
                                            edge_value=float(decision_edge),
                                            required_min_edge=float(required_min_edge),
                                            base_target_usd=float(self.taker_target_usd),
                                            top_best_bid_price=(
                                                float(decision_top_for_submit.best_bid_price)
                                                if isinstance(decision_top_for_submit.best_bid_price, (int, float))
                                                else None
                                            ),
                                            top_best_ask_price=(
                                                float(decision_top_for_submit.best_ask_price)
                                                if isinstance(decision_top_for_submit.best_ask_price, (int, float))
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
                                                float(self.taker_multi_oracle_cap_usd)
                                                if isinstance(self.taker_multi_oracle_cap_usd, (int, float))
                                                and self.taker_multi_oracle_cap_usd > 0.0
                                                else None
                                            ),
                                        )
                                    ],
                                    max_orders_per_cycle=max(0, int(max_orders - submitted)),
                                ).decisions[0]
                                if (
                                    decision.should_submit
                                    and min_visible_fill_ratio > 0.0
                                    and hasattr(self.gateway, "preview_visible_immediate_fill")
                                ):
                                    visible_fill_preview = self.gateway.preview_visible_immediate_fill(
                                        top=decision_top_for_submit,
                                        side=str(decision.side or side).strip().upper(),
                                    )
                                    visible_notional_usd = (
                                        float(visible_fill_preview.get("visible_notional_usd"))
                                        if isinstance(visible_fill_preview, dict)
                                        and isinstance(visible_fill_preview.get("visible_notional_usd"), (int, float))
                                        else None
                                    )
                                    resolved_target_usd = (
                                        float(decision.target_usd_resolved)
                                        if isinstance(decision.target_usd_resolved, (int, float))
                                        else None
                                    )
                                    visible_fill_ratio = (
                                        float(visible_notional_usd) / float(resolved_target_usd)
                                        if visible_notional_usd is not None
                                        and resolved_target_usd is not None
                                        and resolved_target_usd > 0.0
                                        else None
                                    )
                                    decision = dataclasses.replace(
                                        decision,
                                        visible_fill_ratio=visible_fill_ratio,
                                        visible_fill_notional_usd=visible_notional_usd,
                                    )
                                    if (
                                        visible_fill_ratio is not None
                                        and visible_fill_ratio + 1e-9 < min_visible_fill_ratio
                                    ):
                                        decision = dataclasses.replace(
                                            decision,
                                            should_submit=False,
                                            block_reason="taker_visible_fill_ratio_below_min",
                                            submit_capable_static=False,
                                        )
                                if normal_taker_side_class == "complement_buy":
                                    decision = dataclasses.replace(
                                        decision,
                                        normal_side_policy=normal_side_policy,
                                        normal_taker_side_class="complement_buy",
                                    )
                                _log_taker_decision(
                                    source_token_id=decision_source_token_id,
                                    submit_token_id=decision_token_id,
                                    source_midpoint=(
                                        float(decision_source_midpoint)
                                        if isinstance(decision_source_midpoint, (int, float))
                                        else None
                                    ),
                                    source_fair_probability=(
                                        float(decision_source_fair)
                                        if isinstance(decision_source_fair, (int, float))
                                        else None
                                    ),
                                    source_edge_value=(
                                        float(decision_source_edge)
                                        if isinstance(decision_source_edge, (int, float))
                                        else None
                                    ),
                                    submit_midpoint=(
                                        float(decision_midpoint)
                                        if isinstance(decision_midpoint, (int, float))
                                        else None
                                    ),
                                    submit_fair_probability=(
                                        float(decision_fair)
                                        if isinstance(decision_fair, (int, float))
                                        else None
                                    ),
                                    submit_edge_value=(
                                        float(decision_edge)
                                        if isinstance(decision_edge, (int, float))
                                        else None
                                    ),
                                    confidence_score_value=float(confidence_score),
                                    source_token_score_value=float(source_confidence_score),
                                    submit_token_score_value=float(submit_confidence_score),
                                    cooldown_sec_value=float(cooldown_sec),
                                    complement_token_id=complement_token_id,
                                    decision_info_row=decision_info,
                                    decision_row=decision,
                                )
                                if decision.dynamic_size_capped_by_risk:
                                    self.telemetry.incr("taker_dynamic_size_capped_by_risk")
                                if str(decision.multi_oracle_status).strip().lower() == "unknown":
                                    self.telemetry.incr("taker_multi_oracle_unknown")
                                if bool(decision.multi_oracle_confirmation):
                                    self.telemetry.incr("taker_multi_oracle_confirmation")
                                if bool(decision.multi_oracle_boost_applied):
                                    self.telemetry.incr("taker_multi_oracle_boost_applied")
                                if str(decision.normal_taker_side_class or "").strip().lower() == "same_token_sell_blocked":
                                    self.telemetry.incr("taker_same_token_sell_blocked")
                                if str(decision.normal_taker_side_class or "").strip().lower() == "complement_buy":
                                    self.telemetry.incr("taker_complement_buy")
                                if str(decision.normal_taker_side_class or "").strip().lower() == "complement_route_disabled":
                                    self.telemetry.incr("taker_complement_route_disabled")
                                if str(decision.block_reason or "").strip().lower() == "taker_visible_fill_ratio_below_min":
                                    self.telemetry.incr("taker_visible_fill_ratio_blocked")
                                if str(decision.block_reason or "").strip().lower() == "complement_token_mapping_unavailable":
                                    self.telemetry.incr("taker_complement_mapping_failure")
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
                                submit_target_usd = (
                                    float(decision.target_usd_resolved)
                                    if competitiveness_enabled and decision is not None
                                    else float(self.taker_target_usd)
                                )
                                competitiveness_context = (
                                    decision.as_competitiveness_payload()
                                    if competitiveness_enabled and decision is not None
                                    else None
                                )
                                if not isinstance(competitiveness_context, dict):
                                    competitiveness_context = {}
                                competitiveness_context.update(
                                    self._build_submission_lifecycle_context(
                                        token_id=decision_token_id,
                                        info=decision_info,
                                        submission_lane="taker",
                                        stage=decision_stage,
                                        edge_abs=(
                                            abs(float(decision_edge))
                                            if isinstance(decision_edge, (int, float))
                                            else None
                                        ),
                                    )
                                )
                                competitiveness_context.setdefault(
                                    "normal_side_policy",
                                    normal_side_policy,
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_side_class",
                                    normal_taker_side_class
                                    if str(normal_taker_side_class or "").strip()
                                    else ("buy_expected_winner" if str(submit_side).strip().upper() == "BUY" else "unknown"),
                                )
                                competitiveness_context.setdefault("normal_taker_source_token_id", decision_source_token_id)
                                competitiveness_context.setdefault(
                                    "normal_taker_submit_token_id",
                                    decision_token_id,
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_complement_token_id",
                                    (
                                        str(complement_token_id).strip()
                                        if str(complement_token_id or "").strip()
                                        else None
                                    ),
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_complement_route_applied",
                                    bool(
                                        str(complement_token_id or "").strip()
                                        and str(decision_token_id).strip() != str(decision_source_token_id).strip()
                                    ),
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_source_edge",
                                    (
                                        float(decision_source_edge)
                                        if isinstance(decision_source_edge, (int, float))
                                        else None
                                    ),
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_submit_edge",
                                    (
                                        float(decision_edge)
                                        if isinstance(decision_edge, (int, float))
                                        else None
                                    ),
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_source_token_score",
                                    (
                                        float(source_confidence_score)
                                        if isinstance(source_confidence_score, (int, float))
                                        else None
                                    ),
                                )
                                competitiveness_context.setdefault(
                                    "normal_taker_submit_token_score",
                                    (
                                        float(submit_confidence_score)
                                        if isinstance(submit_confidence_score, (int, float))
                                        else None
                                    ),
                                )
                                attempts += 1
                                outcome = self.manager.place_taker_order_with_outcome(
                                    token_id=decision_token_id,
                                    side=submit_side,
                                    price=submit_price,
                                    size=(float(self.taker_order_size) if self.sizing_mode == "shares" else None),
                                    target_usd=submit_target_usd,
                                    top=decision_top_for_submit,
                                    reason=TAKER_CHAINLINK_REASON,
                                    stage=str(decision_stage or STAGE_UNKNOWN),
                                    target_ref=decision_target_ref,
                                    decision_reference_midpoint=(
                                        float(decision_midpoint) if isinstance(decision_midpoint, (int, float)) else None
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
                                        float(realized_volatility_by_token.get(decision_token_id))
                                        if isinstance(realized_volatility_by_token, dict)
                                        and isinstance(realized_volatility_by_token.get(decision_token_id), (int, float))
                                        else None
                                    ),
                                    competitiveness_context=competitiveness_context,
                                )
                                if bool(outcome.get("submitted", False)):
                                    submitted += 1
                                    actual_submit_token_id = decision_token_id
                                    submitted_token_ids.add(actual_submit_token_id)
                                    self._last_taker_submit_mono_by_token[actual_submit_token_id] = now_mono
                                    if taker_window_lock_key:
                                        self._taker_window_submit_lock_keys.add(taker_window_lock_key)
                                    action_taken = EDGE_ACTION_TAKER
                                    was_submitted = True
                                    fills_accepted = int(outcome.get("fills_accepted", 0) or 0)
                                    if fills_accepted > 0:
                                        was_filled = True
                                        filled_token_ids.add(actual_submit_token_id)
                                    fills_accepted_total += max(0, fills_accepted)
                                    emitted_order_id = str(outcome.get("order_id") or "").strip() or None
                                    submit_payload: Dict[str, Any] = {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "submission_lane": "taker",
                                        "token_id": actual_submit_token_id,
                                        "source_token_id": decision_source_token_id,
                                        "order_id": emitted_order_id,
                                        "side": submit_side,
                                        "price": float(submit_price),
                                        "size": (
                                            float(self.taker_order_size)
                                            if self.sizing_mode == "shares"
                                            else None
                                        ),
                                        "target_usd": float(submit_target_usd),
                                        "midpoint": decision_midpoint,
                                        "fair_probability": decision_fair,
                                        "edge": decision_edge,
                                        "edge_abs": (
                                            abs(float(decision_edge)) if isinstance(decision_edge, (int, float)) else None
                                        ),
                                        "edge_bucket": self._taker_edge_bucket(
                                            abs(float(decision_edge)) if isinstance(decision_edge, (int, float)) else None
                                        ),
                                        "edge_unknown_reason": (
                                            None if isinstance(decision_edge, (int, float)) else "missing_edge_value"
                                        ),
                                        **lifecycle_phase_surface_fields(
                                            lifecycle_phase=(
                                                decision_info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                                                or lifecycle_phase_from_payload(decision_info)
                                                or ""
                                            )
                                        ),
                                        **lineage_stage_surface_fields(
                                            lineage_stage=(
                                                lineage_stage_from_payload(decision_info)
                                                if lineage_stage_from_payload(decision_info) != STAGE_UNKNOWN
                                                else (
                                                    str(decision_info.get("stage") or "").strip().upper()
                                                    or decision_stage
                                                )
                                            )
                                        ),
                                        "required_min_edge": float(required_min_edge),
                                        "confidence_score": float(confidence_score),
                                        "open_order_cleanup_required": bool(open_order_cleanup_required),
                                        "settlement_hold_required": bool(settlement_hold_required),
                                        "unresolved_lifecycle_obligation": bool(unresolved_lifecycle_obligation),
                                        "cancel_fail_closed": bool(cancel_fail_closed),
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
                                    self.events.log_event(EVENT_TAKER_SUBMIT, submit_payload)
                                else:
                                    block_reason = "taker_submit_rejected"
                                    taker_submit_reject_reason = (
                                        str(outcome.get("submit_reject_reason") or "").strip().lower() or None
                                    )

            event_market_reference = decision_market_reference
            event_pair_truth = self._pair_truth_for_token(
                pair_truth_by_base_key=pair_truth_by_base_key,
                market_base_key=self._market_base_key_from_market_key(
                    decision_info.get("market_key")
                ),
                token_id=decision_token_id,
            )
            decision_lifecycle_phase = str(
                decision_info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                or lifecycle_phase_from_payload(decision_info)
                or ""
            ).strip().lower() or "scan"
            decision_lineage_stage = lineage_stage_from_payload(decision_info)
            if decision_lineage_stage == STAGE_UNKNOWN:
                decision_lineage_stage = str(decision_stage or "").strip().upper() or STAGE_UNKNOWN

            self._emit_edge_evaluation(
                token_id=decision_token_id,
                target_ref=decision_target_ref,
                source_token_id=decision_source_token_id,
                source_target_ref=self._target_ref_for_token(decision_source_token_id),
                evaluation_scope=EDGE_EVAL_SCOPE_TAKER,
                lifecycle_phase=decision_lifecycle_phase,
                lineage_stage=decision_lineage_stage,
                owned_market_ref=(
                    decision_info.get(EDGE_OWNED_MARKET_REF_FIELD)
                    or self._market_ref_for_token_ids(self.token_ids)
                ),
                challenger_market_ref=(
                    decision_info.get(EDGE_CHALLENGER_MARKET_REF_FIELD)
                    or self._market_ref_for_token_ids(self._challenger_token_ids)
                ),
                ownership_drop_reason=decision_info.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                ownership_replacement_reason=decision_info.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
                market_truth_required=bool(
                    decision_info.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD, bool(self.token_ids))
                ),
                maker_phase_allowed=bool(
                    decision_info.get(
                        EDGE_MAKER_PHASE_ALLOWED_FIELD,
                        decision_lifecycle_phase == "maker_window",
                    )
                ),
                taker_phase_allowed=bool(
                    decision_info.get(
                        EDGE_TAKER_PHASE_ALLOWED_FIELD,
                        decision_lifecycle_phase == "taker_window",
                    )
                ),
                maker_gate_open=bool(maker_gate_state),
                taker_gate_open=bool(taker_gate_state),
                time_remaining_sec=(
                    decision_time_remaining_sec
                    if isinstance(decision_time_remaining_sec, (int, float))
                    else None
                ),
                fair_probability=(
                    decision_fair
                    if isinstance(decision_fair, (int, float))
                    else None
                ),
                market_probability=(
                    decision_midpoint
                    if isinstance(decision_midpoint, (int, float))
                    else None
                ),
                edge_value=decision_edge if isinstance(decision_edge, (int, float)) else None,
                oracle_tick_age_sec=oracle_tick_age_sec,
                latency_state=latency_state,
                maker_gate_state=maker_gate_state,
                taker_gate_state=taker_gate_state,
                open_order_cleanup_required=open_order_cleanup_required,
                settlement_hold_required=settlement_hold_required,
                unresolved_lifecycle_obligation=unresolved_lifecycle_obligation,
                cancel_fail_closed=cancel_fail_closed,
                action_taken=action_taken,
                block_reason=block_reason,
                submitted=was_submitted,
                filled=was_filled,
                result=None,
                cycle_index=stable_cycle_index,
                order_id=emitted_order_id,
                book_source=(self._book_source(decision_top) or None),
                market_reference_mode=str(event_market_reference.get("market_reference_mode") or ""),
                market_reference_basis=str(event_market_reference.get("market_reference_basis") or ""),
                market_reference_confidence=str(event_market_reference.get("market_reference_confidence") or ""),
                market_reference_fallback_used=bool(event_market_reference.get("market_reference_fallback_used", False)),
                market_reference_source_side=str(event_market_reference.get("market_reference_source_side") or "none"),
                market_reference_class=str(event_market_reference.get("market_reference_class") or ""),
                pair_truth_class=str(event_pair_truth.get("pair_truth_class") or ""),
                pair_truth_basis=str(event_pair_truth.get("pair_truth_basis") or ""),
                pair_truth_owner_scope=str(event_pair_truth.get("pair_truth_owner_scope") or ""),
                pair_missing_token_count=event_pair_truth.get("pair_missing_token_count"),
                pair_one_sided_token_count=event_pair_truth.get("pair_one_sided_token_count"),
                pair_authoritative_token_count=event_pair_truth.get("pair_authoritative_token_count"),
                required_min_edge=float(required_min_edge),
                taker_submit_reject_reason=taker_submit_reject_reason,
                held_net_shares=info.get("held_net_shares"),
                held_open_order_present=info.get("held_open_order_present"),
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
        with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
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
        watched.update(self._lifecycle_watch_token_ids())
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
        return FINANCIAL_POSTURE_NORMAL

    def _terminal_unwind_halt_new_risk_active(self, stage_info_by_token: Dict[str, Dict[str, Any]]) -> bool:
        return False

    def _token_terminal_unwind_halt_new_risk_active(self, info: Dict[str, Any]) -> bool:
        return False

    def _emit_lifecycle_context_event(
        self,
        *,
        event_type: str,
        token_id: str,
        submission_lane: str,
        lifecycle_phase: str,
        sec_to_expiry: Optional[float],
        financial_posture_class: str,
        detail: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "ts_utc": utc_iso(),
            "run_id": self.run_id,
            "token_id": str(token_id or ""),
            "submission_lane": str(submission_lane or "unknown"),
            **lifecycle_phase_surface_fields(
                lifecycle_phase=str(lifecycle_phase or "").strip().lower() or "scan"
            ),
            "sec_to_expiry": (float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None),
            "financial_posture_class": str(financial_posture_class or "UNKNOWN").strip().upper() or "UNKNOWN",
            "detail": str(detail or "").strip(),
        }
        if isinstance(extra, dict):
            payload.update(extra)
        self.events.log_event(event_type, payload)

    def _build_submission_lifecycle_context(
        self,
        *,
        token_id: str,
        info: Dict[str, Any],
        submission_lane: str,
        stage: Optional[str] = None,
        edge_abs: Optional[float] = None,
    ) -> Dict[str, Any]:
        token = str(token_id or "").strip()
        lifecycle_info = dict(info or {})
        stage_value = (
            str(stage).strip().upper()
            if str(stage or "").strip()
            else self._compat_stage_from_lifecycle_info(lifecycle_info)
        ) or STAGE_UNKNOWN
        sec_to_expiry = (
            float(lifecycle_info.get("sec_to_expiry"))
            if isinstance(lifecycle_info.get("sec_to_expiry"), (int, float))
            else None
        )
        base_posture = str(self._financial_posture_class or FINANCIAL_POSTURE_NORMAL).strip().upper() or FINANCIAL_POSTURE_NORMAL
        resolved_posture = base_posture
        lifecycle_context_mismatch = False
        lifecycle_context_present = bool(isinstance(sec_to_expiry, float))
        lifecycle_context_missing_reason = ""
        lifecycle_phase_value = str(
            lifecycle_info.get(EDGE_LIFECYCLE_PHASE_FIELD)
            or legacy_stage_to_lifecycle_phase(stage_value)
            or ""
        ).strip().lower() or "scan"
        if not lifecycle_context_present:
            lifecycle_context_missing_reason = "missing_sec_to_expiry"
            self._lifecycle_context_missing_sec_to_expiry_count += 1
            self.telemetry.incr("lifecycle_context_missing_sec_to_expiry")
            self._emit_lifecycle_context_event(
                event_type="lifecycle_context_missing",
                token_id=token,
                submission_lane=submission_lane,
                lifecycle_phase=lifecycle_phase_value,
                sec_to_expiry=None,
                financial_posture_class=resolved_posture,
                detail=lifecycle_context_missing_reason,
                extra={
                    "require_lifecycle_context_for_decisions": bool(self.require_lifecycle_context_for_decisions),
                },
            )
        context: Dict[str, Any] = {
            "submission_lane": str(submission_lane or "unknown"),
            **lifecycle_phase_surface_fields(lifecycle_phase=lifecycle_phase_value),
            **lineage_stage_surface_fields(
                lineage_stage=lineage_stage_from_payload(lifecycle_info) or stage_value
            ),
            **ownership_surface_fields(
                owned_market_ref=lifecycle_info.get(EDGE_OWNED_MARKET_REF_FIELD),
                challenger_market_ref=lifecycle_info.get(EDGE_CHALLENGER_MARKET_REF_FIELD),
                ownership_drop_reason=lifecycle_info.get(EDGE_OWNERSHIP_DROP_REASON_FIELD),
                ownership_replacement_reason=lifecycle_info.get(EDGE_OWNERSHIP_REPLACEMENT_REASON_FIELD),
            ),
            **market_truth_surface_fields(
                market_truth_required=bool(
                    lifecycle_info.get(EDGE_MARKET_TRUTH_REQUIRED_FIELD, bool(token))
                )
            ),
            **lane_permission_surface_fields(
                maker_phase_allowed=bool(
                    lifecycle_info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, lifecycle_phase_value == "maker_window")
                ),
                taker_phase_allowed=bool(
                    lifecycle_info.get(EDGE_TAKER_PHASE_ALLOWED_FIELD, lifecycle_phase_value == "taker_window")
                ),
                maker_gate_open=bool(
                    lifecycle_info.get(EDGE_MAKER_GATE_OPEN_FIELD, lifecycle_phase_value == "maker_window")
                ),
                taker_gate_open=bool(
                    lifecycle_info.get(EDGE_TAKER_GATE_OPEN_FIELD, lifecycle_phase_value == "taker_window")
                ),
            ),
            **lifecycle_surface_fields(
                open_order_cleanup_required=bool(
                    lifecycle_info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False)
                ),
                settlement_hold_required=bool(
                    lifecycle_info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False)
                ),
                unresolved_lifecycle_obligation=bool(
                    lifecycle_info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False)
                ),
                cancel_fail_closed=bool(lifecycle_info.get(EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD, False)),
            ),
            "financial_posture_class": str(resolved_posture),
            "sec_to_expiry": sec_to_expiry,
            "lifecycle_context_present": bool(lifecycle_context_present),
            "lifecycle_context_missing_reason": str(lifecycle_context_missing_reason),
            "lifecycle_context_mismatch": bool(lifecycle_context_mismatch),
            "require_lifecycle_context_for_decisions": bool(self.require_lifecycle_context_for_decisions),
            "maker_timing_gate_open": bool(lifecycle_info.get("maker_timing_gate_open", False)),
            "maker_timing_stage_override_active": bool(
                lifecycle_info.get("maker_timing_stage_override_active", False)
            ),
            "held_net_shares": float(lifecycle_info.get("held_net_shares", 0.0) or 0.0),
            "held_open_order_present": bool(lifecycle_info.get("held_open_order_present", False)),
        }
        if isinstance(edge_abs, (int, float)):
            context["edge_abs"] = float(edge_abs)
        return context

    def _held_unpriceable_cause_class(
        self,
        *,
        token_id: str,
        quote: Optional[Dict[str, Any]],
        now_mono: float,
        hard_reason_parts: List[str],
    ) -> str:
        unpriceable_since = self._held_unpriceable_since_mono_by_token.get(token_id)
        if isinstance(unpriceable_since, (int, float)):
            age_since_unpriceable_sec = max(0.0, float(now_mono - float(unpriceable_since)))
            hard_reason_parts.append(f"held_ws_missing_or_unusable_age_sec={age_since_unpriceable_sec:.3f}")
        expiry_dt = self.token_expiry_dt_by_token.get(token_id)
        sec_to_expiry = (
            (expiry_dt - utc_now()).total_seconds()
            if isinstance(expiry_dt, dt.datetime)
            else None
        )
        if isinstance(sec_to_expiry, (int, float)):
            boundary_eps = max(0.0, float(self.expiry_boundary_epsilon_sec))
            if float(sec_to_expiry) > (boundary_eps + 1e-9):
                return HELD_UNPRICEABLE_CAUSE_PREEXPIRY_WS_MISSING_OR_UNUSABLE
            if float(sec_to_expiry) < -(boundary_eps + 1e-9):
                return HELD_UNPRICEABLE_CAUSE_POSTEXPIRY_MARKET_RETIRED
            return HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP
        return HELD_UNPRICEABLE_CAUSE_UNKNOWN_DATA_GAP

    def _discovery_carry_forward_token_ids(self, discovered_token_ids: List[str]) -> List[str]:
        discovered = set(str(token_id) for token_id in discovered_token_ids if str(token_id or "").strip())
        carry = sorted(token_id for token_id in self._held_exposure_token_ids() if token_id not in discovered)
        return self._unique_ordered(carry)

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
                "held_ws_missing_or_unusable_tracking_active": False,
                "held_ws_missing_or_unusable_refresh_pending": False,
                "open_order_cleanup_required": False,
                "settlement_hold_required": False,
                "cancel_fail_closed": False,
                "held_unpriceable_tracking_active": False,
                "sec_to_expiry": None,
            }
        now_mono_value = float(now_mono) if isinstance(now_mono, (int, float)) else float(time.monotonic())
        open_orders_flag = bool(open_order_present) if isinstance(open_order_present, bool) else bool(
            token in self._open_order_token_ids()
        )
        held_unpriceable_tracking_active = bool(token in self._held_unpriceable_since_mono_by_token)
        forced_refresh_pending_mono = self._held_ws_missing_or_unusable_refresh_next_mono_by_token.get(token)
        held_ws_missing_or_unusable_refresh_pending = bool(
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
        pos = self.risk.positions.get(token)
        net_shares = float(getattr(pos, "net_shares", 0.0) or 0.0)
        open_order_cleanup_required = bool(open_orders_flag)
        settlement_hold_required = bool(abs(net_shares) > 1e-9)
        cancel_fail_closed = bool(open_order_cleanup_required)
        unresolved_lifecycle_obligation = bool(
            open_order_cleanup_required
            or settlement_hold_required
            or held_unpriceable_tracking_active
            or held_ws_missing_or_unusable_refresh_pending
        )
        return {
            "unresolved_lifecycle_obligation": bool(unresolved_lifecycle_obligation),
            "open_order_present": bool(open_orders_flag),
            "held_ws_missing_or_unusable_tracking_active": bool(held_unpriceable_tracking_active),
            "held_ws_missing_or_unusable_refresh_pending": bool(held_ws_missing_or_unusable_refresh_pending),
            "open_order_cleanup_required": bool(open_order_cleanup_required),
            "settlement_hold_required": bool(settlement_hold_required),
            "cancel_fail_closed": bool(cancel_fail_closed),
            "held_unpriceable_tracking_active": bool(held_unpriceable_tracking_active),
            "sec_to_expiry": (float(sec) if isinstance(sec, (int, float)) else None),
        }

    def _lifecycle_management_payload(
        self,
        *,
        token_id: str,
        sec_to_expiry: Optional[float],
    ) -> Dict[str, Any]:
        token = str(token_id or "").strip()
        if not token:
            return {
                "net_shares": 0.0,
                "open_order_present": False,
                "open_order_cleanup_required": False,
                "settlement_hold_required": False,
                "unresolved_lifecycle_obligation": False,
                "cancel_fail_closed": False,
                "sec_to_expiry": sec_to_expiry,
            }
        pos = self.risk.positions.get(token)
        net_shares = float(getattr(pos, "net_shares", 0.0) or 0.0)
        open_order_present = token in self._open_order_token_ids()
        return {
            "net_shares": float(net_shares),
            "open_order_present": bool(open_order_present),
            "open_order_cleanup_required": bool(open_order_present),
            "settlement_hold_required": bool(abs(net_shares) > 1e-9),
            "unresolved_lifecycle_obligation": bool(open_order_present),
            "cancel_fail_closed": bool(open_order_present),
            "sec_to_expiry": sec_to_expiry,
        }

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

    def _handle_ws_missing_or_unusable_tokens(
        self,
        *,
        missing_or_unusable_tokens: List[str],
        held_exposure_tokens: Optional[set[str]] = None,
    ) -> Dict[str, List[str]]:
        if not missing_or_unusable_tokens or not self.discovery.enabled:
            return {"forced_refresh_tokens": [], "suppressed_held_tokens": []}
        unique_tokens = sorted(
            set(str(token_id) for token_id in missing_or_unusable_tokens if str(token_id or "").strip())
        )
        if not unique_tokens:
            return {"forced_refresh_tokens": [], "suppressed_held_tokens": []}
        held_tokens = held_exposure_tokens if held_exposure_tokens is not None else self._held_exposure_token_ids()
        forced_refresh_tokens = [token_id for token_id in unique_tokens if token_id not in held_tokens]
        held_missing_tokens = [token_id for token_id in unique_tokens if token_id in held_tokens]
        forced_held_recovery_tokens: List[str] = []
        suppressed_held_tokens: List[str] = []
        now_mono = time.monotonic()
        for token_id in held_missing_tokens:
            should_force_recovery = False
            if self.held_ws_missing_or_unusable_refresh_interval_sec > 0.0:
                unpriceable_since = self._held_unpriceable_since_mono_by_token.get(token_id)
                unpriceable_age_sec = (
                    max(0.0, float(now_mono - float(unpriceable_since)))
                    if isinstance(unpriceable_since, (int, float))
                    else 0.0
                )
                next_allowed_mono = self._held_ws_missing_or_unusable_refresh_next_mono_by_token.get(token_id, 0.0)
                if (
                    unpriceable_age_sec + 1e-9
                    >= float(self.held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec)
                    and now_mono >= float(next_allowed_mono)
                ):
                    should_force_recovery = True
                    self._held_ws_missing_or_unusable_refresh_next_mono_by_token[token_id] = (
                        now_mono + float(self.held_ws_missing_or_unusable_refresh_interval_sec)
                    )
            if should_force_recovery:
                forced_held_recovery_tokens.append(token_id)
            else:
                suppressed_held_tokens.append(token_id)
        if forced_held_recovery_tokens:
            forced_refresh_tokens.extend(forced_held_recovery_tokens)
            forced_refresh_tokens = sorted(set(str(token_id) for token_id in forced_refresh_tokens if str(token_id)))
            self.telemetry.incr("target_refresh_forced_held_ws_missing_or_unusable_recovery")
            self.events.log_event(
                "target_refresh_forced_held_ws_missing_or_unusable_recovery",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_count": len(forced_held_recovery_tokens),
                    "token_ids": list(forced_held_recovery_tokens),
                    "reason": "persistent_held_ws_missing_or_unusable_recovery_refresh",
                    "min_unpriceable_age_sec": float(self.held_ws_missing_or_unusable_refresh_min_unpriceable_age_sec),
                    "refresh_interval_sec": float(self.held_ws_missing_or_unusable_refresh_interval_sec),
                },
            )
        if forced_refresh_tokens:
            self.telemetry.incr("target_refresh_forced_ws_missing_or_unusable")
            self.events.log_event(
                "target_refresh_forced_ws_missing_or_unusable",
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
            self.telemetry.incr("target_refresh_suppressed_held_ws_missing_or_unusable")
            self.events.log_event(
                "target_refresh_suppressed_held_ws_missing_or_unusable",
                {
                    "ts_utc": utc_iso(),
                    "run_id": self.run_id,
                    "token_count": len(suppressed_held_tokens),
                    "token_ids": list(suppressed_held_tokens),
                    "reason": "held_ws_missing_or_unusable_no_discovery_refresh",
                },
            )
        return {
            "forced_refresh_tokens": list(forced_refresh_tokens),
            "suppressed_held_tokens": list(suppressed_held_tokens),
        }

    def _sync_book_feed_watch_tokens(self) -> None:
        watch_token_ids = self._transport_watch_token_ids()
        previous_watch_token_ids = list(getattr(self, "_last_book_feed_watch_token_ids", []))
        changed = watch_token_ids != previous_watch_token_ids
        self.book_feed.update_token_ids(watch_token_ids)
        if not changed:
            return
        self._last_book_feed_watch_token_ids = list(watch_token_ids)
        active_watch_additions = [
            token_id for token_id in self.token_ids if token_id and token_id not in set(previous_watch_token_ids)
        ]
        apply_active_grace = bool(active_watch_additions)
        self._reset_ws_slo_bootstrap(
            reason="book_feed_watch_tokens_updated",
            activate_grace=apply_active_grace,
        )
        self.events.log_event(
            "book_feed_watch_tokens_updated",
            {
                "ts_utc": utc_iso(),
                "run_id": self.run_id,
                "old_token_count": int(len(previous_watch_token_ids)),
                "new_token_count": int(len(watch_token_ids)),
                "old_token_ids": list(previous_watch_token_ids),
                "new_token_ids": list(watch_token_ids),
                "authoritative_active_token_count": int(len(self.token_ids)),
                "authoritative_active_token_ids": list(self.token_ids),
                "challenger_token_count": int(len(self._challenger_token_ids)),
                "challenger_token_ids": list(self._challenger_token_ids),
                "active_watch_addition_count": int(len(active_watch_additions)),
                "active_watch_addition_token_ids": list(active_watch_additions),
                "ws_slo_grace_applied": bool(apply_active_grace),
            },
        )

    def _refresh_targets(self, *, force: bool = False) -> None:
        if not self.discovery.enabled:
            return
        now = time.monotonic()
        if not force and now < self.next_target_refresh_monotonic:
            return
        self.next_target_refresh_monotonic = now + self.discovery.refresh_interval_sec

        try:
            result = self.discovery.discover()
        except EXECUTION_RUNTIME_EXCEPTIONS as exc:
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
        self._last_discovery_candidate_pairs_token_ids = [
            self._unique_ordered(
                [str(token_id).strip() for token_id in list(pair_ids or []) if str(token_id).strip()]
            )
            for pair_ids in list(getattr(result, "candidate_pairs_token_ids", []) or [])
        ]
        primary_pair_ids = self._discovery_primary_pair_token_ids(discovered_ids)
        carry_forward_ids = self._discovery_carry_forward_token_ids(primary_pair_ids)
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
        discovered_open_anchor_map = {
            str(token_id): str(anchor_utc)
            for token_id, anchor_utc in getattr(result, "token_open_anchor_utc_by_token", {}).items()
            if str(token_id) and str(anchor_utc)
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
        lifecycle_watch_ids = self._lifecycle_watch_token_ids()
        old_authoritative_ids = list(self.token_ids)
        old_pending_ids = list(self._challenger_token_ids)
        old_retained_set = set(old_authoritative_ids) | set(old_pending_ids) | set(lifecycle_watch_ids)
        old_market_key_map = dict(self.token_market_key_by_token)

        if discovered_expiry_map:
            self._apply_token_expiry_map(discovered_expiry_map, source="discovery")
        if discovered_side_map:
            self._apply_token_side_map(discovered_side_map, source="discovery")
        if discovered_open_anchor_map:
            self._apply_token_open_anchor_map(discovered_open_anchor_map, source="discovery")
        if discovered_strike_map:
            self._apply_token_strike_map(discovered_strike_map, source="discovery")
        if discovered_market_key_map:
            self.token_market_key_by_token.update(discovered_market_key_map)

        active_pair_valid = bool(old_authoritative_ids) and self._pair_tokens_market_valid(old_authoritative_ids)
        primary_pair_valid = bool(primary_pair_ids) and self._pair_tokens_market_valid(primary_pair_ids)
        normalized_primary_pair_ids = self._unique_ordered(primary_pair_ids)
        same_as_active = bool(active_pair_valid and normalized_primary_pair_ids == old_authoritative_ids)

        if active_pair_valid:
            next_active_ids = list(old_authoritative_ids if (not primary_pair_valid or not same_as_active) else normalized_primary_pair_ids)
            next_pending_ids = [] if (not primary_pair_valid or same_as_active) else list(normalized_primary_pair_ids)
        else:
            next_active_ids = list(normalized_primary_pair_ids if primary_pair_valid else [])
            next_pending_ids = []
        retained_set = set(next_active_ids) | set(next_pending_ids) | set(lifecycle_watch_ids)

        self.telemetry.set_gauge("target_discovery_active_targets", float(len(next_active_ids)))
        self.telemetry.set_gauge("target_discovery_standdown", 1.0 if (not next_active_ids and not next_pending_ids) else 0.0)
        self.telemetry.set_gauge("target_discovery_challenger_token_count", float(len(next_pending_ids)))
        if not primary_pair_ids and not active_pair_valid:
            self.telemetry.incr("target_discovery_empty")
            self._set_challenger_token_ids([], reason="no_valid_targets_discovered")
            self._set_authoritative_active_token_ids(
                [],
                reason="no_valid_targets_discovered",
                apply_ws_slo_grace=False,
            )
            self.token_market_key_by_token = {
                token_id: old_market_key_map.get(token_id, "")
                for token_id in lifecycle_watch_ids
            }
            self.token_expiry_utc_by_token = {
                token_id: expiry_utc
                for token_id, expiry_utc in self.token_expiry_utc_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self.token_expiry_dt_by_token = {
                token_id: expiry_dt
                for token_id, expiry_dt in self.token_expiry_dt_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self.token_side_by_token = {
                token_id: side
                for token_id, side in self.token_side_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self.token_open_anchor_utc_by_token = {
                token_id: anchor_utc
                for token_id, anchor_utc in self.token_open_anchor_utc_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self.token_open_anchor_dt_by_token = {
                token_id: anchor_dt
                for token_id, anchor_dt in self.token_open_anchor_dt_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self.token_strike_by_token = {
                token_id: strike
                for token_id, strike in self.token_strike_by_token.items()
                if token_id in lifecycle_watch_ids
            }
            self._sync_book_feed_watch_tokens()
            self._prune_removed_tokens(old_set=old_retained_set, active_set=set(lifecycle_watch_ids))
            if old_authoritative_ids or old_pending_ids:
                self.events.log_event(
                    "targets_standdown",
                    {
                        "ts_utc": utc_iso(),
                        "run_id": self.run_id,
                        "old_token_count": len(old_authoritative_ids),
                        "old_token_ids": list(old_authoritative_ids),
                        "old_challenger_token_count": len(old_pending_ids),
                        "old_challenger_token_ids": list(old_pending_ids),
                        "new_token_count": 0,
                        "new_token_ids": [],
                        "reason": "no_valid_targets_discovered",
                        "pairs_selected": int(result.pairs_selected),
                        "scanned_markets": int(result.scanned_markets),
                        "fee_eligible_markets": int(result.fee_eligible_markets),
                        "contract_rejected_pairs": int(result.contract_rejected_pairs),
                        "carry_forward_token_count": len(carry_forward_ids),
                        "carry_forward_token_ids": list(carry_forward_ids),
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
                        "carry_forward_token_count": len(carry_forward_ids),
                        "carry_forward_token_ids": list(carry_forward_ids),
                    },
                )
            self._last_discovery_target_count = 0
            return

        self._set_challenger_token_ids(
            next_pending_ids,
            reason=(
                "discovery_candidate_challenger"
                if next_pending_ids
                else "discovery_candidate_absent_or_owned"
            ),
            event_extra={
                "pairs_selected": int(result.pairs_selected),
                "scanned_markets": int(result.scanned_markets),
                "fee_eligible_markets": int(result.fee_eligible_markets),
                "contract_rejected_pairs": int(result.contract_rejected_pairs),
                "allowlist_enabled": bool(result.allowlist_enabled),
                "allowlist_rejected_pairs": int(result.allowlist_rejected_pairs),
                "primary_pair_token_ids": list(primary_pair_ids),
            },
        )
        self._set_authoritative_active_token_ids(
            next_active_ids,
            reason=(
                "owned_market_retained_or_admitted"
                if next_active_ids
                else "discovery_candidate_absent"
            ),
            apply_ws_slo_grace=bool(
                next_active_ids
                and not set(next_active_ids).issubset(set(old_pending_ids))
                and set(next_active_ids) != set(old_authoritative_ids)
            ),
            event_extra={
                "pairs_selected": int(result.pairs_selected),
                "scanned_markets": int(result.scanned_markets),
                "fee_eligible_markets": int(result.fee_eligible_markets),
                "contract_rejected_pairs": int(result.contract_rejected_pairs),
                "allowlist_enabled": bool(result.allowlist_enabled),
                "allowlist_rejected_pairs": int(result.allowlist_rejected_pairs),
                "discovered_token_count": len(discovered_ids),
                "discovered_token_ids": list(discovered_ids),
                "primary_pair_token_ids": list(primary_pair_ids),
                "challenger_token_count": len(next_pending_ids),
                "challenger_token_ids": list(next_pending_ids),
                "carry_forward_token_count": len(carry_forward_ids),
                "carry_forward_token_ids": list(carry_forward_ids),
            },
        )

        self.token_market_key_by_token = {
            token_id: discovered_market_key_map.get(token_id, old_market_key_map.get(token_id, ""))
            for token_id in retained_set
        }
        self.token_expiry_utc_by_token = {
            token_id: expiry_utc
            for token_id, expiry_utc in self.token_expiry_utc_by_token.items()
            if token_id in retained_set
        }
        self.token_expiry_dt_by_token = {
            token_id: expiry_dt
            for token_id, expiry_dt in self.token_expiry_dt_by_token.items()
            if token_id in retained_set
        }
        self.token_side_by_token = {
            token_id: side
            for token_id, side in self.token_side_by_token.items()
            if token_id in retained_set
        }
        self.token_open_anchor_utc_by_token = {
            token_id: anchor_utc
            for token_id, anchor_utc in self.token_open_anchor_utc_by_token.items()
            if token_id in retained_set
        }
        self.token_open_anchor_dt_by_token = {
            token_id: anchor_dt
            for token_id, anchor_dt in self.token_open_anchor_dt_by_token.items()
            if token_id in retained_set
        }
        self.token_strike_by_token = {
            token_id: strike
            for token_id, strike in self.token_strike_by_token.items()
            if token_id in retained_set
        }
        self._sync_book_feed_watch_tokens()
        self._prune_removed_tokens(old_set=old_retained_set, active_set=retained_set)
        for token_id in sorted(set(next_active_ids) | set(next_pending_ids)):
            self.risk.positions.setdefault(token_id, Position(token_id=token_id))
            old_key = str(old_market_key_map.get(token_id, ""))
            new_key = str(self.token_market_key_by_token.get(token_id, ""))
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
                self._held_ws_missing_or_unusable_refresh_next_mono_by_token.pop(token_id, None)
            self.token_market_key_by_token.pop(token_id, None)
            self.token_open_anchor_utc_by_token.pop(token_id, None)
            self.token_open_anchor_dt_by_token.pop(token_id, None)
            self._market_entry_mono_by_token.pop(token_id, None)
            self._market_entry_cycle_by_token.pop(token_id, None)
            self._last_lifecycle_phase_by_token.pop(token_id, None)
            self._last_taker_submit_mono_by_token.pop(token_id, None)
            self._taker_window_submit_lock_keys.discard(self._taker_window_submit_lock_key_for_token(token_id))
            self._last_doctrine_signature_by_token.pop(token_id, None)
            self._last_doctrine_prereq_failure_by_token.pop(token_id, None)
            self._clear_maker_ws_touch_cache(token_id)
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
            except OSError:
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
            except OSError:
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
                "taker_arming_horizon_sec": float(self.cfg.get("taker", {}).get("arming_horizon_sec", 0.0)),
                "taker_execution_cutoff_sec": float(self.cfg.get("taker", {}).get("execution_cutoff_sec", 0.0)),
                "taker_late_fire_priority_band_sec": float(
                    self.cfg.get("taker", {}).get("late_fire_priority_band_sec", 0.0)
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
        except (OSError, json.JSONDecodeError):
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
        except OSError as exc:
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
        except OSError as exc:
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
            except (OSError, RuntimeError, ValueError):
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
        self._emit_taker_window_semantic_check()
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
            except (OSError, TypeError, ValueError) as exc:
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
            watch_token_ids = self._transport_watch_token_ids()
            self._last_book_feed_watch_token_ids = list(watch_token_ids)
            self.book_feed.start(watch_token_ids)
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
                self._reconcile_pair_authority()
                self._sync_book_feed_watch_tokens()
                self.telemetry.set_gauge("target_count", float(len(self.token_ids)))
                self.telemetry.set_gauge(
                    "challenger_token_count",
                    float(len(self._challenger_token_ids)),
                )
                self.telemetry.set_gauge(
                    "lifecycle_watch_token_count",
                    float(len(self._lifecycle_watch_token_ids())),
                )
                self.telemetry.set_gauge(
                    "transport_watch_token_count",
                    float(len(self._transport_watch_token_ids())),
                )
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
                    self.taker_target_usd = float(self._active_target_usd)
                self.telemetry.set_gauge("active_target_usd", float(self._active_target_usd))
                self.telemetry.set_gauge("taker_ramp_allowed", 1.0 if self._taker_ramp_allowed else 0.0)
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
                settled_positions_cycle = self._apply_postexpiry_binary_settlement()
                self.telemetry.set_gauge("postexpiry_binary_settlements_last_cycle", float(settled_positions_cycle))

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
                ws_updates_target_cycle = 0
                target_token_ids = self._unique_ordered([str(token_id) for token_id in self.token_ids if str(token_id).strip()])
                target_token_set = set(target_token_ids)
                valuation_token_ids = self._valuation_watch_token_ids()
                held_exposure_tokens = self._held_exposure_token_ids()
                pair_truth_by_base_key: Dict[str, Dict[str, Any]] = {}
                pair_missing_base_keys: List[str] = []
                all_target_pairs_missing_ws = False
                if not has_targets:
                    self.telemetry.incr("no_target_cycles")
                self._preexpiry_ws_missing_or_unusable_anomaly_last_cycle = False
                missing_or_unusable_tokens: list[str] = []
                for token_id in valuation_token_ids:
                    top = ws_books.get(token_id)
                    if top is None:
                        missing_or_unusable_tokens.append(token_id)
                        if token_id in held_exposure_tokens:
                            stage_info = self._token_lifecycle_info(token_id)
                            stage_name = self._compat_stage_from_lifecycle_info(stage_info)
                            sec_to_expiry_val = stage_info.get("sec_to_expiry")
                            sec_to_expiry = (
                                float(sec_to_expiry_val)
                                if isinstance(sec_to_expiry_val, (int, float))
                                else None
                            )
                            preexpiry_ws_missing_or_unusable = bool(
                                isinstance(sec_to_expiry, float)
                                and sec_to_expiry > (float(self.expiry_boundary_epsilon_sec) + 1e-9)
                            )
                            if preexpiry_ws_missing_or_unusable:
                                self._preexpiry_ws_missing_or_unusable_anomaly_count += 1
                                self._preexpiry_ws_missing_or_unusable_anomaly_last_cycle = True
                                self.telemetry.incr("preexpiry_ws_missing_or_unusable_anomaly")
                                self.events.log_event(
                                    "preexpiry_ws_missing_or_unusable_anomaly",
                                    {
                                        "ts_utc": utc_iso(),
                                        "run_id": self.run_id,
                                        "token_id": token_id,
                                        "market_key": str(stage_info.get("market_key") or ""),
                                        **lifecycle_phase_surface_fields(
                                            lifecycle_phase=stage_info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                                            or lifecycle_phase_from_payload(stage_info)
                                            or "scan"
                                        ),
                                        **lineage_stage_surface_fields(
                                            lineage_stage=lineage_stage_from_payload(stage_info)
                                            if lineage_stage_from_payload(stage_info) != STAGE_UNKNOWN
                                            else stage_name
                                        ),
                                        "sec_to_expiry": sec_to_expiry,
                                        "held_exposure_token": True,
                                        "open_order_cleanup_required": bool(
                                            stage_info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False)
                                        ),
                                        "settlement_hold_required": bool(
                                            stage_info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False)
                                        ),
                                        "unresolved_lifecycle_obligation": bool(
                                            stage_info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False)
                                        ),
                                        "reason": "ws_book_missing",
                                    },
                                )
                        continue
                    if token_id in held_exposure_tokens and self._ws_quote_unusable_for_held_valuation(
                        token_id=token_id,
                        top=top,
                    ):
                        missing_or_unusable_tokens.append(token_id)
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
                                "raw_book_present": False,
                                "from_ws": self._book_source_is_ws(top),
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
                    if midpoint is not None and prev_mid is not None and sample_triggered:
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
                pair_truth_by_base_key = self._build_pair_truth_map(
                    books=books,
                    token_ids=target_token_ids,
                )
                pair_missing_base_keys, pair_one_sided_base_keys = self._pair_truth_base_keys_by_class(
                    pair_truth_by_base_key
                )
                all_target_pairs_missing_ws = bool(pair_truth_by_base_key) and (
                    len(pair_missing_base_keys) == len(pair_truth_by_base_key)
                )
                pair_truth_class_counts = collections.Counter(
                    str(pair_truth.get("pair_truth_class") or "missing").strip().lower()
                    for pair_truth in pair_truth_by_base_key.values()
                )
                self.telemetry.set_gauge(
                    "pair_truth_authoritative_pair_count",
                    float(pair_truth_class_counts.get("authoritative", 0)),
                )
                self.telemetry.set_gauge(
                    "pair_truth_one_sided_pair_count",
                    float(
                        sum(
                            1
                            for pair_truth in pair_truth_by_base_key.values()
                            if str(pair_truth.get("pair_truth_basis") or "").strip().lower()
                            == "pair_missing_one_sided_only"
                        )
                    ),
                )
                self.telemetry.set_gauge(
                    "pair_truth_missing_pair_count",
                    float(pair_truth_class_counts.get("missing", 0)),
                )
                self.telemetry.set_gauge("pair_truth_pair_count", float(len(pair_truth_by_base_key)))
                self._handle_ws_missing_or_unusable_tokens(
                    missing_or_unusable_tokens=missing_or_unusable_tokens,
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

                stage_info_by_token = {
                    token_id: self._token_lifecycle_info(token_id) for token_id in self.token_ids
                }
                self._financial_posture_class = self._resolve_financial_posture_class(
                    stage_info_by_token=stage_info_by_token
                )
                self.telemetry.set_gauge(
                    "financial_posture_class",
                    self._financial_posture_class_to_gauge(self._financial_posture_class),
                )
                maker_phase_tokens = {
                    token_id
                    for token_id, info in stage_info_by_token.items()
                    if bool(info.get(EDGE_MAKER_GATE_OPEN_FIELD, False))
                }
                maker_cannon_probe_token_ids = self._maker_cannon_probe_token_ids(
                    stage_info_by_token=stage_info_by_token,
                    books=books,
                )
                maker_observational_token_ids = set(maker_phase_tokens) | set(maker_cannon_probe_token_ids)
                taker_phase_tokens = {
                    token_id
                    for token_id, info in stage_info_by_token.items()
                    if bool(info.get(EDGE_TAKER_GATE_OPEN_FIELD, False))
                }
                self.telemetry.set_gauge("doctrine_maker_phase_token_count", float(len(maker_phase_tokens)))
                self.telemetry.set_gauge(
                    "doctrine_maker_cannon_probe_token_count",
                    float(len(maker_cannon_probe_token_ids)),
                )
                self.telemetry.set_gauge(
                    "doctrine_maker_observational_token_count",
                    float(len(maker_observational_token_ids)),
                )
                self.telemetry.set_gauge("doctrine_taker_phase_token_count", float(len(taker_phase_tokens)))
                lifecycle_phase_counts = {
                    "scan": 0,
                    "prepare": 0,
                    "maker_window": 0,
                    "taker_window": 0,
                    "resolve": 0,
                    "unknown": 0,
                }
                lineage_stage_counts = {
                    STAGE_OBSERVE: 0,
                    STAGE_EVALUATE: 0,
                    STAGE_MAKER_POSITION: 0,
                    STAGE_MAKER_TAKER_SELECTIVE: 0,
                    STAGE_SNIPER_PRIMARY: 0,
                    STAGE_LATE_DIAGNOSTIC: 0,
                    STAGE_MAKER_LATE_WINDOW: 0,
                    STAGE_TAKER_COMMITMENT: 0,
                    STAGE_EXTREME_ONLY: 0,
                    STAGE_EXPIRED: 0,
                    STAGE_UNKNOWN: 0,
                }
                doctrine_gate_fail_count = 0
                for info in stage_info_by_token.values():
                    lifecycle_phase_name = str(
                        info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                        or lifecycle_phase_from_payload(info)
                        or "unknown"
                    ).strip().lower() or "unknown"
                    lifecycle_phase_counts[lifecycle_phase_name] = (
                        lifecycle_phase_counts.get(lifecycle_phase_name, 0) + 1
                    )
                    lineage_stage_name = lineage_stage_from_payload(info)
                    if lineage_stage_name == STAGE_UNKNOWN:
                        lineage_stage_name = str(info.get("stage") or "").strip().upper() or STAGE_UNKNOWN
                    lineage_stage_counts[lineage_stage_name] = (
                        lineage_stage_counts.get(lineage_stage_name, 0) + 1
                    )
                    if str(info.get("doctrine_gate_verdict", "fail")) != "pass":
                        doctrine_gate_fail_count += 1
                for lifecycle_phase_name, count in lifecycle_phase_counts.items():
                    self.telemetry.set_gauge(
                        f"doctrine_lifecycle_phase_count.{lifecycle_phase_name.lower()}",
                        float(count),
                    )
                for lineage_stage_name, count in lineage_stage_counts.items():
                    self.telemetry.set_gauge(
                        f"doctrine_lineage_stage_count.{lineage_stage_name.lower()}",
                        float(count),
                    )
                self.telemetry.set_gauge("doctrine_gate_fail_count", float(doctrine_gate_fail_count))
                self.telemetry.set_gauge(
                    "doctrine_unknown_lifecycle_phase_token_count",
                    float(
                        sum(
                            1
                            for info in stage_info_by_token.values()
                            if str(
                                info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                                or lifecycle_phase_from_payload(info)
                                or ""
                            ).strip().lower()
                            not in {"scan", "prepare", "maker_window", "taker_window", "resolve"}
                        )
                    ),
                )

                taker_ctx = self._taker_context()
                degraded_expiry_fallback_active = bool(taker_ctx.get("degraded_expiry_fallback_active", False))
                self.telemetry.set_gauge(
                    "doctrine_degraded_expiry_fallback_active",
                    1.0 if degraded_expiry_fallback_active else 0.0,
                )
                near_tokens = list(taker_ctx.get("near_token_ids", []))
                if not near_tokens:
                    near_tokens = list(taker_ctx.get("token_ids", []))
                self.telemetry.set_gauge("taker_near_token_count", float(len(near_tokens)))
                lag_ready_for_taker = (not self.latency_verifier.require_armed_for_taker) or latency_snapshot.armed
                taker_runtime_token_ids = self._taker_window_token_ids(
                    taker_ctx=taker_ctx,
                    taker_phase_tokens=taker_phase_tokens,
                )
                candidate_taker_tokens = list(taker_runtime_token_ids)
                if mode_state in {MODE_MAKER_ONLY, MODE_SAFE_STOP}:
                    candidate_taker_tokens = []
                taker_active = bool(candidate_taker_tokens) and self.taker_enabled
                fair_probability_by_token = self._build_fair_probability_map(
                    books,
                    latency_snapshot=latency_snapshot,
                    scope="maker",
                )
                taker_fair_probability_by_token = self._build_fair_probability_map(
                    books,
                    latency_snapshot=latency_snapshot,
                    scope="taker",
                )
                secondary_fair_probability_by_token, secondary_oracle_base_status = (
                    self._build_secondary_fair_probability_map(self.token_ids)
                )
                self.telemetry.set_gauge("fair_probability_token_count", float(len(fair_probability_by_token)))
                self.telemetry.set_gauge(
                    "taker_fair_probability_token_count",
                    float(len(taker_fair_probability_by_token)),
                )
                self.telemetry.set_gauge(
                    "secondary_fair_probability_token_count",
                    float(len(secondary_fair_probability_by_token)),
                )
                latest_chainlink_targets = self.chainlink.get_latest(self.chainlink_symbol_for_targets)
                latest_pyth_targets = self.pyth.get_latest(self.pyth_symbol_for_targets)
                oracle_fresh, oracle_tick_age_sec, oracle_freshness_reason = self._oracle_freshness()
                if oracle_tick_age_sec is not None:
                    self.telemetry.set_gauge("doctrine_oracle_tick_age_sec", float(oracle_tick_age_sec))
                self.telemetry.set_gauge("doctrine_oracle_fresh", 1.0 if oracle_fresh else 0.0)
                maker_prereq_failure_by_token: Dict[str, str] = {}
                maker_preclassified_no_submission_reason_by_token: Dict[str, str] = {}
                maker_preclassified_no_submission_category_by_token: Dict[str, str] = {}
                maker_timing_gate_open_by_token: Dict[str, bool] = {
                    token_id: self._maker_timing_gate_open(
                        stage_info_by_token.get(token_id, {}).get("sec_to_expiry")
                    )
                    for token_id in maker_observational_token_ids
                }
                maker_eligible_tokens = set(maker_phase_tokens)
                if self.doctrine_mode == "canonical":
                    maker_eligible_tokens = set()
                    for token_id in maker_phase_tokens:
                        timing_gate_open = bool(maker_timing_gate_open_by_token.get(token_id, False))
                        failure_reason = self._maker_prereq_failure_reason(
                            token_id,
                            fair_probability_by_token=fair_probability_by_token,
                            latency_snapshot=latency_snapshot,
                            oracle_fresh=oracle_fresh,
                        )
                        if failure_reason:
                            maker_prereq_failure_by_token[token_id] = failure_reason
                            continue
                        if self.maker_comp_timing_gate_enabled and (not timing_gate_open):
                            maker_prereq_failure_by_token[token_id] = "maker_timing_gate_closed"
                            continue
                        maker_eligible_tokens.add(token_id)
                    for token_id in sorted(maker_observational_token_ids):
                        info = stage_info_by_token.get(token_id, {})
                        if not bool(info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False)):
                            continue
                        if bool(info.get(EDGE_MAKER_GATE_OPEN_FIELD, False)):
                            continue
                        maker_preclassified_no_submission_reason_by_token.setdefault(
                            str(token_id),
                            "settlement_hold_required",
                        )
                        maker_preclassified_no_submission_category_by_token.setdefault(
                            str(token_id),
                            "settlement_hold_required",
                        )
                    maker_eligible_tokens = self._apply_canonical_maker_ws_source_gate(
                        books=books,
                        maker_eligible_tokens=maker_eligible_tokens,
                        maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                    )
                self.telemetry.set_gauge(
                    "doctrine_maker_prereq_failure_count",
                    float(len(maker_prereq_failure_by_token)),
                )
                self._emit_doctrine_decisions(
                    stage_info_by_token,
                    maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                )
                if taker_active:
                    # Keep taker lane-local. Normal taker authority must not
                    # rewrite whole-cycle pacing or shared maker-manager rate
                    # limits just because the final-fire lane is active.
                    self.telemetry.incr("taker_active_cycles")
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
                        taker_active = False
                        candidate_taker_tokens = []
                if taker_active:
                    effective_poll_interval = min(
                        effective_poll_interval,
                        max(0.0, float(self.taker_poll_interval_sec)),
                    )
                self.telemetry.set_gauge("taker_mode_active", 1.0 if taker_active else 0.0)
                self.telemetry.set_gauge("taker_token_count", float(len(candidate_taker_tokens)))
                self.telemetry.set_gauge("operating_mode_size_mult", mode_size_mult)
                self.telemetry.set_gauge("operating_mode_spread_mult", mode_spread_mult)
                sec_to_expiry_min = taker_ctx.get("sec_to_expiry_min")
                if isinstance(sec_to_expiry_min, (int, float)):
                    self.telemetry.set_gauge("taker_sec_to_expiry_min", float(sec_to_expiry_min))
                self.telemetry.set_gauge(
                    "taker_lag_verified_token_count",
                    float(taker_ctx.get("lag_verified_token_count", 0)),
                )
                if taker_active != self._taker_active:
                    self._taker_active = taker_active
                    self.events.log_event(
                        "taker_mode_transition",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "active": taker_active,
                            "token_count": len(candidate_taker_tokens),
                            "token_ids": candidate_taker_tokens,
                            "sec_to_expiry_min": sec_to_expiry_min,
                            "lag_verified_token_count": taker_ctx.get("lag_verified_token_count", 0),
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
                self._update_maker_ws_touch_cache(books=books)
                maker_market_reference_token_ids = set(maker_observational_token_ids) | set(
                    maker_prereq_failure_by_token.keys()
                )
                maker_resolved_books_by_token, maker_market_reference_by_token = (
                    self._resolve_maker_market_reference_inputs(
                        books=books,
                        maker_token_ids=maker_market_reference_token_ids,
                        maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                    )
                )
                if self.doctrine_mode == "canonical":
                    pre_reference_maker_eligible_tokens = set(maker_eligible_tokens)
                    maker_eligible_tokens = set()
                    for token_id in sorted(pre_reference_maker_eligible_tokens):
                        market_reference = dict(maker_market_reference_by_token.get(token_id) or {})
                        resolved_top = maker_resolved_books_by_token.get(token_id, books.get(token_id))
                        market_reference_mode = str(
                            market_reference.get("market_reference_mode") or ""
                        ).strip().lower()
                        market_reference_class = str(
                            market_reference.get("market_reference_class") or ""
                        ).strip().lower()
                        midpoint = getattr(resolved_top, "midpoint", None)
                        midpoint_present = isinstance(midpoint, (int, float))
                        if (
                            market_reference_class == "authoritative"
                            and market_reference_mode in {"direct_midpoint", "backfilled_paired_touch"}
                            and midpoint_present
                        ):
                            maker_eligible_tokens.add(token_id)
                            continue
                        maker_preclassified_no_submission_reason_by_token[token_id] = (
                            "market_reference_not_authoritative"
                        )
                        maker_preclassified_no_submission_category_by_token[token_id] = (
                            "market_reference_not_authoritative"
                        )
                self.telemetry.set_gauge("doctrine_maker_eligible_token_count", float(len(maker_eligible_tokens)))
                maker_books_for_evaluation = dict(books)
                maker_books_for_evaluation.update(maker_resolved_books_by_token)
                maker_one_sided_buy_active_count = 0
                maker_one_sided_sell_active_count = 0
                for token_id in sorted(maker_observational_token_ids):
                    info = stage_info_by_token.get(token_id, {})
                    sec_to_expiry = info.get("sec_to_expiry")
                    stage = self._compat_stage_from_lifecycle_info(info)
                    top = maker_resolved_books_by_token.get(token_id, books.get(token_id))
                    fair = fair_probability_by_token.get(token_id)
                    base_size_mult = float(size_multiplier_by_token.get(token_id, 1.0))
                    base_spread_mult = float(spread_multiplier_by_token.get(token_id, 1.0))
                    profile = self._maker_competitiveness_profile(
                        token_id=token_id,
                        top=top,
                        market_reference=maker_market_reference_by_token.get(token_id),
                        fair_probability=fair,
                        secondary_fair_probability=secondary_fair_probability_by_token.get(token_id),
                        secondary_oracle_status=secondary_oracle_base_status,
                        chainlink_spot_price=(
                            float(latest_chainlink_targets.price)
                            if latest_chainlink_targets is not None
                            else None
                        ),
                        secondary_oracle_spot_price=(
                            float(latest_pyth_targets.price)
                            if latest_pyth_targets is not None
                            else None
                        ),
                        stage=stage,
                        lifecycle_phase=str(
                            info.get(EDGE_LIFECYCLE_PHASE_FIELD)
                            or lifecycle_phase_from_payload(info)
                            or "scan"
                        ).strip().lower()
                        or "scan",
                        lineage_stage=(
                            lineage_stage_from_payload(info)
                            if lineage_stage_from_payload(info) != STAGE_UNKNOWN
                            else str(stage or STAGE_UNKNOWN).strip().upper() or STAGE_UNKNOWN
                        ),
                        sec_to_expiry=sec_to_expiry,
                        base_size_multiplier=base_size_mult,
                        base_spread_multiplier=base_spread_mult,
                        timing_gate_open=bool(maker_timing_gate_open_by_token.get(token_id, True)),
                        maker_phase_allowed=bool(info.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, False)),
                    )
                    maker_competitiveness_profiles_by_token[token_id] = profile
                    if token_id in maker_eligible_tokens:
                        size_multiplier_by_token[token_id] = float(profile["size_multiplier_applied"])
                        spread_multiplier_by_token[token_id] = float(profile["spread_multiplier_applied"])
                        maker_requote_delta_by_token[token_id] = float(profile["requote_delta_applied"])
                        side_policy = str(profile["side_policy"])
                        context_payload = dict(profile["context"])
                        context_payload.update(
                            self._build_submission_lifecycle_context(
                                token_id=token_id,
                                info=info,
                                submission_lane="maker",
                                stage=stage,
                            )
                        )
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
                    for token_id in sorted(maker_phase_tokens):
                        profile = maker_competitiveness_profiles_by_token.get(token_id, {})
                        context_payload = dict(profile.get("context") or {})
                        block_reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip().lower()
                        self.events.log_event(
                            "maker_competitiveness_decision",
                            {
                                "ts_utc": utc_iso(),
                                "run_id": self.run_id,
                                "token_id": token_id,
                                "maker_phase_allowed": bool(context_payload.get("maker_phase_allowed", True)),
                                "maker_eligible": bool(token_id in maker_eligible_tokens),
                                "block_reason": block_reason or None,
                                "timing_gate_blocked": block_reason == "maker_timing_gate_closed",
                                **context_payload,
                            },
                        )
                maker_timing_gate_blocked_count = sum(
                    1
                    for token_id in maker_phase_tokens
                    if str(maker_prereq_failure_by_token.get(token_id, "")).strip().lower() == "maker_timing_gate_closed"
                )
                self.telemetry.set_gauge("maker_timing_gate_blocked_count_last_cycle", float(maker_timing_gate_blocked_count))
                self.telemetry.set_gauge("maker_one_sided_buy_active_count_last_cycle", float(maker_one_sided_buy_active_count))
                self.telemetry.set_gauge(
                    "maker_one_sided_sell_active_count_last_cycle",
                    float(maker_one_sided_sell_active_count),
                )

                maker_eval_token_ids = set(maker_observational_token_ids) | set(maker_prereq_failure_by_token.keys())
                maker_submitted_token_ids: set[str] = set()
                maker_submitted_order_ids_by_token: Dict[str, List[str]] = {}
                maker_no_submission_reason_by_token: Dict[str, str] = dict(
                    maker_preclassified_no_submission_reason_by_token
                )
                maker_no_submission_category_by_token: Dict[str, str] = dict(
                    maker_preclassified_no_submission_category_by_token
                )
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
                lag_verified_token_ids = [str(x) for x in list(taker_ctx.get("lag_verified_token_ids", []))]
                valuation_state = self._apply_valuation_controls(books=valuation_books, phase="pre_submit")

                if not books:
                    self.telemetry.set_gauge("quote_active", 0.0)
                    if has_targets:
                        orphan_canceled = self.manager.cancel_non_target_orders(
                            self._manager_tracked_token_ids(),
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
                            books=maker_books_for_evaluation,
                            maker_market_reference_by_token=maker_market_reference_by_token,
                            stage_info_by_token=stage_info_by_token,
                            maker_eval_token_ids=maker_eval_token_ids,
                            maker_submitted_token_ids=maker_submitted_token_ids,
                            maker_submitted_order_ids_by_token=maker_submitted_order_ids_by_token,
                            maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                            maker_no_submission_category_by_token=maker_no_submission_category_by_token,
                            maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                            fair_probability_by_token=fair_probability_by_token,
                            maker_competitiveness_profiles_by_token=maker_competitiveness_profiles_by_token,
                            oracle_tick_age_sec=oracle_tick_age_sec,
                            latency_state=latency_snapshot.state,
                            cycle_index=int(self._doctrine_cycle_index),
                        )
                    if taker_runtime_token_ids:
                        maker_handoff_no_submission_reason_by_token = (
                            self._build_maker_handoff_no_submission_reason_by_token(
                                maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                                maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                            )
                        )
                        taker_summary = self._run_taker(
                            books=books,
                            fair_probability_by_token=taker_fair_probability_by_token,
                            realized_volatility_by_token=volatility_by_token,
                            secondary_fair_probability_by_token=secondary_fair_probability_by_token,
                            secondary_oracle_base_status=secondary_oracle_base_status,
                            token_ids=[str(token_id) for token_id in taker_runtime_token_ids],
                            stage_info_by_token=stage_info_by_token,
                            oracle_tick_age_sec=oracle_tick_age_sec,
                            oracle_fresh=oracle_fresh,
                            latency_snapshot=latency_snapshot,
                            mode_state=mode_state,
                            lag_ready_for_taker=lag_ready_for_taker,
                            lag_verified_token_ids=lag_verified_token_ids,
                            taker_ramp_allowed=self._taker_ramp_allowed,
                            cycle_index=int(self._doctrine_cycle_index),
                            maker_submitted_token_ids=maker_submitted_token_ids,
                            maker_no_submission_reason_by_token=maker_handoff_no_submission_reason_by_token,
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
                            token_id: maker_resolved_books_by_token.get(token_id, top)
                            for token_id, top in books.items()
                            if token_id in maker_eligible_tokens
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
                        tracked_tokens_for_manager = self._manager_tracked_token_ids()
                        tracked_token_cancel_reason_by_token: Dict[str, str] = {}
                        if self.doctrine_mode == "canonical":
                            for token_id in sorted(tracked_tokens_for_manager):
                                if token_id in maker_eligible_tokens:
                                    continue
                                reason = str(maker_no_submission_reason_by_token.get(token_id, "")).strip().lower()
                                if not reason:
                                    reason = str(maker_prereq_failure_by_token.get(token_id, "")).strip().lower()
                                if not reason:
                                    reason = "phase_disallow_maker"
                                tracked_token_cancel_reason_by_token[str(token_id)] = reason
                        summary = self.manager.step(
                            books_for_manager,
                            tracked_tokens=tracked_tokens_for_manager,
                            tracked_token_cancel_reason_by_token=tracked_token_cancel_reason_by_token,
                            fair_probability_by_token=fair_for_manager,
                            realized_volatility_by_token=volatility_for_manager,
                            size_multiplier_by_token=size_mult_for_manager,
                            spread_multiplier_by_token=spread_mult_for_manager,
                            requote_delta_by_token=requote_delta_for_manager,
                            side_policy_by_token=side_policy_for_manager,
                            competitiveness_context_by_token=competitiveness_context_for_manager,
                            max_actions_override=max_actions_override,
                            cycle_index=int(self._doctrine_cycle_index),
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
                        manager_maker_no_submission_reason_by_token = {
                            str(token_id): str(reason).strip().lower()
                            for token_id, reason in dict(summary.get("maker_no_submission_reason_by_token", {})).items()
                            if str(token_id).strip() and str(reason).strip()
                        }
                        manager_maker_no_submission_category_by_token = {
                            str(token_id): str(category).strip().lower()
                            for token_id, category in dict(summary.get("maker_no_submission_category_by_token", {})).items()
                            if str(token_id).strip() and str(category).strip()
                        }
                        maker_no_submission_reason_by_token = dict(
                            maker_preclassified_no_submission_reason_by_token
                        )
                        maker_no_submission_reason_by_token.update(
                            manager_maker_no_submission_reason_by_token
                        )
                        maker_no_submission_category_by_token = dict(
                            maker_preclassified_no_submission_category_by_token
                        )
                        maker_no_submission_category_by_token.update(
                            manager_maker_no_submission_category_by_token
                        )
                        maker_handoff_no_submission_reason_by_token = (
                            self._build_maker_handoff_no_submission_reason_by_token(
                                maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                                maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                            )
                        )
                        if maker_eval_token_ids:
                            self._emit_maker_edge_evaluations(
                                books=maker_books_for_evaluation,
                                pair_truth_by_base_key=pair_truth_by_base_key,
                                maker_market_reference_by_token=maker_market_reference_by_token,
                                stage_info_by_token=stage_info_by_token,
                                maker_eval_token_ids=maker_eval_token_ids,
                                maker_submitted_token_ids=maker_submitted_token_ids,
                                maker_submitted_order_ids_by_token=maker_submitted_order_ids_by_token,
                                maker_no_submission_reason_by_token=maker_no_submission_reason_by_token,
                                maker_no_submission_category_by_token=maker_no_submission_category_by_token,
                                maker_prereq_failure_by_token=maker_prereq_failure_by_token,
                                fair_probability_by_token=fair_probability_by_token,
                                maker_competitiveness_profiles_by_token=maker_competitiveness_profiles_by_token,
                                oracle_tick_age_sec=oracle_tick_age_sec,
                                latency_state=latency_snapshot.state,
                                cycle_index=int(self._doctrine_cycle_index),
                            )
                        if taker_runtime_token_ids:
                            taker_summary = self._run_taker(
                                books=books,
                                pair_truth_by_base_key=pair_truth_by_base_key,
                                fair_probability_by_token=taker_fair_probability_by_token,
                                realized_volatility_by_token=volatility_by_token,
                                secondary_fair_probability_by_token=secondary_fair_probability_by_token,
                                secondary_oracle_base_status=secondary_oracle_base_status,
                                token_ids=[str(token_id) for token_id in taker_runtime_token_ids],
                                stage_info_by_token=stage_info_by_token,
                                oracle_tick_age_sec=oracle_tick_age_sec,
                                oracle_fresh=oracle_fresh,
                                latency_snapshot=latency_snapshot,
                                mode_state=mode_state,
                                lag_ready_for_taker=lag_ready_for_taker,
                                lag_verified_token_ids=lag_verified_token_ids,
                                taker_ramp_allowed=self._taker_ramp_allowed,
                                cycle_index=int(self._doctrine_cycle_index),
                                maker_submitted_token_ids=maker_submitted_token_ids,
                                maker_no_submission_reason_by_token=maker_handoff_no_submission_reason_by_token,
                            )
                            if taker_summary["attempts"] > 0:
                                self.telemetry.incr("taker_attempts", taker_summary["attempts"])
                            if taker_summary["submitted"] > 0:
                                self.telemetry.incr("taker_submitted", taker_summary["submitted"])
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
                    except EXECUTION_RUNTIME_EXCEPTIONS as exc:
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
                    pair_missing_base_keys=pair_missing_base_keys,
                    all_target_pairs_missing_ws=all_target_pairs_missing_ws,
                )
                ws_slo_bootstrap_active = bool(self._ws_slo_bootstrap_active)
                self._maybe_request_book_feed_resubscribe_for_target_ws_gap(
                    ws_slo_reasons=ws_slo_reasons,
                    book_feed_status=book_feed_status_live,
                    ws_slo_bootstrap_active=ws_slo_bootstrap_active,
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
                            "reason": str(getattr(self, "_ws_slo_bootstrap_reason", "") or ""),
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
                            "pair_truth_pair_count": int(len(pair_truth_by_base_key)),
                            "pair_missing_base_keys": list(pair_missing_base_keys),
                            "pair_one_sided_base_keys": list(pair_one_sided_base_keys),
                            "all_target_pairs_missing_ws": bool(all_target_pairs_missing_ws),
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
                self.telemetry.set_gauge("ramp_taker_enabled", 1.0 if ramp_snapshot.taker_allowed else 0.0)
                self.telemetry.set_gauge("ramp_reconcile_mismatch_ratio", float(reconcile_mismatch_ratio))
                if ramp_snapshot.enabled:
                    self._active_target_usd = float(ramp_snapshot.target_usd)
                    self._taker_ramp_allowed = bool(ramp_snapshot.taker_allowed)
                    self.manager.sizing_target_usd = float(self._active_target_usd)
                    if self.sizing_mode == "notional":
                        self.taker_target_usd = float(self._active_target_usd)
                if ramp_snapshot.changed:
                    self.events.log_event(
                        "ramp_transition",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "target_usd": float(ramp_snapshot.target_usd),
                            "ramp_taker_enabled": bool(ramp_snapshot.taker_allowed),
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
                    runtime_resource_snapshot = self._runtime_resource_snapshot_from_telemetry(self.telemetry.snapshot())
                    self.events.log_event(
                        "risk_control_engaged",
                        {
                            "ts_utc": utc_iso(),
                            "run_id": self.run_id,
                            "control": "kill_switch",
                            "reason": self.risk.kill_reason,
                            "runtime_state": self._runtime_state,
                            "financial_posture_class": str(self._financial_posture_class),
                            "runtime_resource": runtime_resource_snapshot,
                        },
                    )
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
                    except EXECUTION_RUNTIME_EXCEPTIONS as exc:
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
                    except (OSError, TypeError, ValueError) as exc:
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
                    gateway_status = self.gateway.status()
                    wallet_contract = self.wallet.status_contract()
                    host_time_sync = self._refresh_host_time_sync_snapshot()
                    guard_active, guard_reason = self._read_external_guard_stop()
                    status_ts_utc = utc_iso()
                    runtime_resource = self._runtime_resource_snapshot_from_telemetry(telemetry)
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
                        "taker_market_reference_policy": dict(self._taker_market_reference_policy),
                        "run_id": self.run_id,
                        "lifecycle_phase": self._runtime_lifecycle_phase,
                        "owned_market_ref": self._runtime_owned_market_ref,
                        "challenger_market_ref": self._runtime_challenger_market_ref,
                        "ownership_drop_reason": self._runtime_ownership_drop_reason,
                        "ownership_replacement_reason": self._runtime_ownership_replacement_reason,
                        "market_truth_required": self._runtime_market_truth_required,
                        "maker_phase_allowed": self._runtime_maker_phase_allowed,
                        "taker_phase_allowed": self._runtime_taker_phase_allowed,
                        "maker_gate_open": self._runtime_maker_gate_open,
                        "taker_gate_open": self._runtime_taker_gate_open,
                        "runtime_state": self._runtime_state,
                        "active_targets_present": self._runtime_active_targets_present,
                        "promotion_eligibility_hint": self._runtime_promotion_eligibility_hint,
                        "challenger_token_count": int(len(self._challenger_token_ids)),
                        "challenger_token_ids": list(self._challenger_token_ids),
                        "lifecycle_watch_token_count": int(len(self._lifecycle_watch_token_ids())),
                        "transport_watch_token_count": int(len(self._transport_watch_token_ids())),
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
                        "gateway": gateway_status,
                        "host_time_sync": dict(host_time_sync),
                        "host_time_sync_snapshot_age_sec": max(
                            0.0, float(time.monotonic() - self._host_time_sync_last_refresh_mono)
                        ),
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
                        "held_unpriceable_defect_candidate": bool(self._held_unpriceable_defect_candidate),
                        "held_unpriceable_non_defect_token_ids": list(
                            self._held_unpriceable_non_defect_token_ids
                        ),
                        "held_unpriceable_meaningful_escalation_token_ids": list(
                            self._held_unpriceable_meaningful_escalation_token_ids
                        ),
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
                        "held_dust_raw_hard_degraded_token_count": int(
                            self._held_dust_raw_hard_degraded_token_count
                        ),
                        "valuation_raw_hard_degraded": bool(
                            self._held_dust_raw_hard_degraded_token_count > 0
                        ),
                        "valuation_raw_degraded": bool(
                            self._valuation_degraded or (self._held_dust_raw_hard_degraded_token_count > 0)
                        ),
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
                        "preexpiry_ws_missing_or_unusable_anomaly_count": int(
                            self._preexpiry_ws_missing_or_unusable_anomaly_count
                        ),
                        "preexpiry_ws_missing_or_unusable_anomaly_active": bool(
                            self._preexpiry_ws_missing_or_unusable_anomaly_last_cycle
                        ),
                        "pair_truth_pair_count": int(len(pair_truth_by_base_key)),
                        "pair_truth_missing_pair_count": int(
                            sum(
                                1
                                for pair_truth in pair_truth_by_base_key.values()
                                if str(pair_truth.get("pair_truth_class") or "").strip().lower() == "missing"
                            )
                        ),
                        "pair_truth_one_sided_pair_count": int(
                            sum(
                                1
                                for pair_truth in pair_truth_by_base_key.values()
                                if str(pair_truth.get("pair_truth_basis") or "").strip().lower()
                                == "pair_missing_one_sided_only"
                            )
                        ),
                        "pair_truth_authoritative_pair_count": int(
                            sum(
                                1
                                for pair_truth in pair_truth_by_base_key.values()
                                if str(pair_truth.get("pair_truth_class") or "").strip().lower() == "authoritative"
                            )
                        ),
                        "pair_truth_missing_base_keys": list(pair_missing_base_keys),
                        "pair_truth_one_sided_base_keys": list(pair_one_sided_base_keys),
                        "pair_truth_owner_scope": "market_base_pair",
                        "lifecycle_context_mismatch_count": int(self._lifecycle_context_mismatch_count),
                        "lifecycle_context_missing_sec_to_expiry_count": int(
                            self._lifecycle_context_missing_sec_to_expiry_count
                        ),
                        "settlement_hold_required_count": int(
                            sum(
                                1
                                for info in stage_info_by_token.values()
                                if bool(info.get(EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD, False))
                            )
                        ),
                        "open_order_cleanup_required_count": int(
                            sum(
                                1
                                for info in stage_info_by_token.values()
                                if bool(info.get(EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD, False))
                            )
                        ),
                        "unresolved_lifecycle_obligation_count": int(
                            sum(
                                1
                                for info in stage_info_by_token.values()
                                if bool(info.get(EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD, False))
                            )
                        ),
                        "cancel_fail_closed_count": int(
                            sum(
                                1
                                for info in stage_info_by_token.values()
                                if bool(info.get(EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD, False))
                            )
                        ),
                        "financial_posture_class": str(self._financial_posture_class),
                        "runtime_require_lifecycle_context_for_decisions": bool(
                            self.require_lifecycle_context_for_decisions
                        ),
                        "valuation_live_mid_max_age_sec": float(self.live_mid_max_age_sec),
                        "valuation_one_sided_quote_max_age_sec": float(self.one_sided_quote_max_age_sec),
                        "valuation_last_known_mid_max_age_sec": float(self.last_known_mid_max_age_sec),
                        "valuation_held_unpriceable_escalation_sec": float(self.held_unpriceable_escalation_sec),
                        "taker_multi_oracle_cap_usd": (
                            float(self.taker_multi_oracle_cap_usd)
                            if isinstance(self.taker_multi_oracle_cap_usd, (int, float))
                            else None
                        ),
                        "taker_multi_oracle_cap_source": str(self.taker_multi_oracle_cap_source),
                        "taker_multi_oracle_cap_authority_class": str(
                            self.taker_multi_oracle_cap_authority_class
                        ),
                        "external_guard_active": bool(guard_active),
                        "external_guard": {
                            "configured": self.guard_stop_file is not None,
                            "path": str(self.guard_stop_file) if self.guard_stop_file is not None else "",
                            "active": guard_active,
                            "reason": guard_reason if guard_active else "",
                        },
                        "runtime_resource": dict(runtime_resource),
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
                            "market_truth_required": 1.0 if self._runtime_market_truth_required else 0.0,
                            "maker_phase_allowed": 1.0 if self._runtime_maker_phase_allowed else 0.0,
                            "taker_phase_allowed": 1.0 if self._runtime_taker_phase_allowed else 0.0,
                            "maker_gate_open": 1.0 if self._runtime_maker_gate_open else 0.0,
                            "taker_gate_open": 1.0 if self._runtime_taker_gate_open else 0.0,
                            "active_targets_present": 1.0 if self._runtime_active_targets_present else 0.0,
                            "promotion_eligibility_hint": 1.0 if self._runtime_promotion_eligibility_hint else 0.0,
                        },
                    )
                    LOG.info(
                        "status bot=%s mode=%s lifecycle_phase=%s kill=%s guard=%s cycles=%s book_updates=%s fills=%s open_orders=%s total_pnl=%.4f cl_connected=%s ws_book_connected=%s cl_reconnects=%s ws_book_reconnects=%s positions=%s",
                        self.bot_name,
                        self.cfg["mode"],
                        self._runtime_lifecycle_phase,
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
                resource_metrics = self._sample_runtime_resource_metrics()
                for metric_name, metric_value in resource_metrics.items():
                    self.telemetry.set_gauge(metric_name, float(metric_value))
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
                except EXECUTION_RUNTIME_EXCEPTIONS as exc:
                    self.events.log_error(
                        {
                            "ts_utc": utc_iso(),
                            "component": "runner_shutdown",
                            "action": "cancel_all",
                            "error": str(exc),
                        }
                    )

            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self._dump_state()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self._write_run_manifest_end()
            # Emit the semantic self-audit again on shutdown so bounded tail
            # replays still retain the canonical stage-window owner truth.
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self._emit_taker_window_semantic_check()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.events.log_event("runner_stop", {"ts_utc": utc_iso(), "run_id": self.run_id})
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.events.close()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.tx_manager.close()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.discovery.close()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.book_feed.stop()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.chainlink.stop()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.pyth.stop()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
                self.prometheus.stop()
            with contextlib.suppress(*EXECUTION_RUNTIME_EXCEPTIONS):
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
    except ValueError as exc:
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
