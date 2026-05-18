from __future__ import annotations

import dataclasses
from collections import deque
import datetime as dt
import hashlib
import logging
import math
import re
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from .common import parse_float, parse_ts, utc_iso, utc_now
from .edge_truth_contract import (
    EDGE_LIFECYCLE_PHASE_FIELD,
    EDGE_MAKER_PHASE_ALLOWED_FIELD,
    EDGE_STAGE_BUCKET_FIELD,
    lineage_stage_from_payload,
    lifecycle_phase_from_payload,
    lifecycle_phase_surface_fields,
    legacy_stage_to_lifecycle_phase,
    lineage_stage_surface_fields,
    normalize_block_reason,
)
from .execution_quality import ExecutionQualityModel
from .gateway import BaseGateway, GatewayError, PostOnlyRejectError
from .logging_utils import EventLogger
from .models import BookTop, FillEvent, LiveOrder, OrderIntent, Position
from .risk import RiskEngine
from .strategy import MarketMakingStrategy
from .telemetry import Telemetry
from .tx_manager import TransactionManager
from .wallet_doctrine import WalletDoctrineBase, create_wallet_doctrine


LOG = logging.getLogger("prodesk.order_manager")
PAPER_TRADE_ID_RE = re.compile(r"^paper-trade-[0-9a-f]{12}-[1-9][0-9]*$")
ORDER_RESIDUAL_EXPOSURE_EPSILON = 1e-9
ORDER_TRANSPORT_EXCEPTIONS = (
    GatewayError,
    OSError,
    TimeoutError,
    ConnectionError,
    RuntimeError,
    TypeError,
    ValueError,
)
TERMINAL_ORDER_ACK_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CLOSED",
    "ERROR",
    "EXECUTED",
    "EXPIRED",
    "FAILED",
    "FILLED",
    "REJECTED",
}
OPEN_EQUIVALENT_ORDER_ACK_STATUSES = {
    "LIVE",
    "OPEN",
    "PARTIAL",
    "PARTIALLY_FILLED",
    "SUBMITTED",
}
COMMITMENT_CANCEL_CLASS_EXCEPTIONAL = "exceptional_shutdown_or_safety"
COMMITMENT_CANCEL_CLASS_ROUTINE = "legacy_routine"
COMMITMENT_CANCEL_CLASS_TERMINAL = "terminal_window_end"
COMMITMENT_CANCEL_REASON_WINDOW_ENDED = "commitment_window_ended"
COMMITMENT_SUPPRESSION_REASON_ACTIVE_HOLD = "commitment_hold_active_pre_expiry"
COMMITMENT_SUPPRESSION_REASON_MISSING_EXPIRY = "commitment_expiry_context_missing_fail_closed"
COMMITMENT_ROUTINE_CANCEL_ORIGINS = frozenset(
    {
        "maker_selection_gate",
        "maker_replace_logic",
        "maker_no_desired_quote",
        "maker_side_policy",
        "maker_extra_same_side_cleanup",
        "stale_watchdog",
        "tracked_token_cleanup",
        "non_target_cleanup",
        "targeted_token_cleanup",
    }
)
COMMITMENT_EXCEPTIONAL_CANCEL_ORIGINS = frozenset(
    {
        "runner_shutdown",
        "kill_switch_cleanup",
    }
)


def _normalize_soft_limit_pct(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, 1.0)


def _canonical_lifecycle_phase_from_stage(stage: Any) -> str:
    return str(legacy_stage_to_lifecycle_phase(stage) or "").strip().lower()


def _canonical_lifecycle_phase_from_payload(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(lifecycle_phase_from_payload(payload) or "").strip().lower()


def _compat_lineage_stage_from_payload(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""
    lineage_stage = str(payload.get("lineage_stage") or "").strip().upper()
    if lineage_stage:
        return lineage_stage
    lineage_stage = str(lineage_stage_from_payload(payload) or "").strip().upper()
    if lineage_stage:
        return lineage_stage
    lifecycle_phase = _canonical_lifecycle_phase_from_payload(payload)
    if lifecycle_phase == "maker_window":
        return "MAKER_LATE_WINDOW"
    if lifecycle_phase == "taker_window":
        return "TAKER_COMMITMENT"
    if lifecycle_phase == "resolve":
        return "EXPIRED"
    if lifecycle_phase == "prepare":
        return "SNIPER_PRIMARY"
    if lifecycle_phase == "scan":
        return "OBSERVE"
    return ""


def _resolve_event_lifecycle_phase(
    *,
    intent_stage: Any = None,
    risk_context: Optional[Dict[str, Any]] = None,
    risk_basis: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Optional[str]]:
    intent_phase = _canonical_lifecycle_phase_from_stage(intent_stage)
    if intent_phase:
        return intent_phase, "intent_stage_compat", None
    basis_phase = _canonical_lifecycle_phase_from_payload(risk_basis)
    if basis_phase:
        basis_has_direct_field = isinstance(risk_basis, dict) and bool(risk_basis.get(EDGE_LIFECYCLE_PHASE_FIELD))
        return basis_phase, ("risk_decision_basis" if basis_has_direct_field else "risk_decision_basis_stage_compat"), None
    context_phase = _canonical_lifecycle_phase_from_payload(risk_context)
    if context_phase:
        context_has_direct_field = isinstance(risk_context, dict) and bool(risk_context.get(EDGE_LIFECYCLE_PHASE_FIELD))
        return context_phase, ("risk_context" if context_has_direct_field else "risk_context_stage_compat"), None
    return "scan", "unknown", "missing_intent_and_lifecycle_context"


class OrderManager:
    def __init__(
        self,
        gateway: BaseGateway,
        strategy: MarketMakingStrategy,
        risk: RiskEngine,
        events: EventLogger,
        telemetry: Telemetry,
        runtime_cfg: Dict[str, float],
        strategy_cfg: Dict[str, float],
        sizing_cfg: Optional[Dict[str, Any]] = None,
        now_fn: Optional[Callable[[], dt.datetime]] = None,
        mode: str = "paper",
        wallet: Optional[WalletDoctrineBase] = None,
        tx_manager: Optional[TransactionManager] = None,
    ):
        self.gateway = gateway
        selected_mode = str(mode or "paper").strip().lower() or "paper"
        self.tx_manager = tx_manager or TransactionManager(gateway)
        if wallet is None:
            if selected_mode != "paper":
                raise ValueError(
                    "OrderManager requires explicit wallet doctrine injection for non-paper mode; "
                    "live fallback construction is forbidden"
                )
            wallet_cfg = runtime_cfg.get("wallet", {}) if isinstance(runtime_cfg, dict) else {}
            if not isinstance(wallet_cfg, dict):
                wallet_cfg = {}
            self.wallet = create_wallet_doctrine(
                wallet_cfg,
                mode="paper",
                gateway=gateway,
                event_logger=events.log_event,
                auth_cfg={"live_order_submission_enabled": False},
            )
        else:
            self.wallet = wallet
        self.wallet.register_nonce_authority(self.tx_manager.nonce_authority())
        self.wallet.register_pending_tx_provider(self.tx_manager.pending_tx_snapshot)
        self.strategy = strategy
        self.risk = risk
        self.events = events
        self.telemetry = telemetry
        self._now_fn = now_fn or utc_now
        self.max_actions_per_cycle = int(runtime_cfg["max_actions_per_cycle"])
        self.cancel_orphan_orders = bool(runtime_cfg.get("cancel_orphan_orders", True))
        self.max_quote_age_sec = float(runtime_cfg.get("max_quote_age_sec", 20.0))
        self.maker_replace_min_rest_sec = max(0.0, float(runtime_cfg.get("maker_replace_min_rest_sec", 0.0)))
        self.order_rate_soft_limit_pct = _normalize_soft_limit_pct(
            runtime_cfg.get("order_rate_soft_limit_pct", 0.98),
            0.98,
        )
        self.cancel_rate_soft_limit_pct = _normalize_soft_limit_pct(
            runtime_cfg.get("cancel_rate_soft_limit_pct", 0.98),
            0.98,
        )
        self.requote_delta = float(strategy_cfg["quote_refresh_min_delta"])
        self.tick_size = max(1e-9, float(strategy_cfg.get("tick_size", 0.001)))
        self.strategy_min_spread = max(0.0, float(strategy_cfg.get("min_spread", 0.0)))
        self.seen_trade_ids_max = int(runtime_cfg.get("seen_trade_ids_max", 200000))
        self.sizing_cfg = sizing_cfg or {}
        self.sizing_mode = str(self.sizing_cfg.get("mode", "shares")).strip().lower()
        self.sizing_min_usd = float(self.sizing_cfg.get("min_usd", 1.0))
        self.sizing_max_usd = float(self.sizing_cfg.get("max_usd", 20.0))
        self.sizing_target_usd = float(self.sizing_cfg.get("target_usd", 5.0))
        self.sizing_rounding = str(self.sizing_cfg.get("rounding", "floor")).strip().lower()
        self.sizing_price_source = str(self.sizing_cfg.get("price_source", "mid")).strip().lower()
        self.sizing_share_step = max(1e-9, float(self.sizing_cfg.get("share_step", 0.01)))
        self.maker_competitive_min_notional_usd = max(
            0.0,
            float(self.sizing_cfg.get("maker_competitive_min_notional_usd", 0.0)),
        )
        self.maker_competitive_max_notional_usd = max(
            0.0,
            float(self.sizing_cfg.get("maker_competitive_max_notional_usd", 0.0)),
        )
        self.maker_competitive_min_shares = max(
            0.0,
            float(self.sizing_cfg.get("maker_competitive_min_shares", 0.0)),
        )
        self.maker_competitive_max_shares = max(
            0.0,
            float(self.sizing_cfg.get("maker_competitive_max_shares", 0.0)),
        )
        self.maker_depth_target_min_ratio = max(
            0.0,
            min(1.0, float(self.sizing_cfg.get("maker_depth_target_min_ratio", 0.0))),
        )
        self.maker_depth_target_max_ratio = max(
            0.0,
            min(1.0, float(self.sizing_cfg.get("maker_depth_target_max_ratio", 0.0))),
        )
        self.maker_depth_target_ratio = max(
            0.0,
            min(1.0, float(self.sizing_cfg.get("maker_depth_target_ratio", 0.0))),
        )
        self.maker_liquidity_tod_scaler_enabled = bool(
            self.sizing_cfg.get("maker_liquidity_tod_scaler_enabled", False)
        )
        self.maker_liquidity_tod_start_hour_utc = int(
            float(self.sizing_cfg.get("maker_liquidity_tod_start_hour_utc", 2))
        )
        self.maker_liquidity_tod_end_hour_utc = int(
            float(self.sizing_cfg.get("maker_liquidity_tod_end_hour_utc", 6))
        )
        self.maker_liquidity_tod_depth_multiplier = max(
            0.0,
            float(self.sizing_cfg.get("maker_liquidity_tod_depth_multiplier", 1.0)),
        )
        self.base_order_size = max(1e-9, float(strategy_cfg.get("base_order_size", 1.0)))
        self.strategy_min_order_size = max(1e-9, float(strategy_cfg.get("min_order_size", 1.0)))
        self.strategy_max_order_size = max(self.strategy_min_order_size, float(strategy_cfg.get("max_order_size", 200.0)))
        self._client_seq = 0
        self.seen_trade_ids: set[str] = set()
        self._seen_trade_ids_queue: Deque[str] = deque()
        self.last_fill_ts_utc: Optional[str] = None
        self.quality = ExecutionQualityModel(strategy_cfg.get("execution_quality", {}))
        maker_competitiveness_cfg = strategy_cfg.get("maker_competitiveness", {})
        if not isinstance(maker_competitiveness_cfg, dict):
            maker_competitiveness_cfg = {}
        selection_gate_cfg = maker_competitiveness_cfg.get("selection_gate", {})
        if not isinstance(selection_gate_cfg, dict):
            selection_gate_cfg = {}
        lifecycle_cfg = runtime_cfg.get("lifecycle", {}) if isinstance(runtime_cfg, dict) else {}
        if not isinstance(lifecycle_cfg, dict):
            lifecycle_cfg = {}
        lifecycle_selection_cfg = lifecycle_cfg.get("selection", {})
        if not isinstance(lifecycle_selection_cfg, dict):
            lifecycle_selection_cfg = {}
        lifecycle_phase_cfg = lifecycle_cfg.get("phase", {})
        if not isinstance(lifecycle_phase_cfg, dict):
            lifecycle_phase_cfg = {}
        selection_owner_cfg = lifecycle_selection_cfg if lifecycle_selection_cfg else selection_gate_cfg
        self.maker_selection_gate_enabled = bool(selection_owner_cfg.get("enabled", False))
        self.maker_selection_gate_require_secondary_oracle_confirmation = bool(
            selection_owner_cfg.get("require_secondary_oracle_confirmation", True)
        )
        selection_gate_min_sec_to_expiry = selection_owner_cfg.get(
            "min_sec_to_expiry",
            None,
        )
        selection_gate_max_sec_to_expiry = selection_owner_cfg.get(
            "max_sec_to_expiry",
            None,
        )
        if lifecycle_phase_cfg:
            # Lifecycle selection owns market admission. Maker submit legality
            # must stay inside the canonical maker window instead of inheriting
            # the much earlier market-admission floor.
            selection_gate_min_sec_to_expiry = lifecycle_phase_cfg.get(
                "taker_window_open_sec",
                selection_gate_min_sec_to_expiry,
            )
            selection_gate_max_sec_to_expiry = lifecycle_phase_cfg.get(
                "maker_window_open_sec",
                selection_gate_max_sec_to_expiry,
            )
        self.maker_selection_gate_min_sec_to_expiry = (
            float(selection_gate_min_sec_to_expiry)
            if isinstance(selection_gate_min_sec_to_expiry, (int, float))
            and not isinstance(selection_gate_min_sec_to_expiry, bool)
            else None
        )
        self.maker_selection_gate_max_sec_to_expiry = (
            float(selection_gate_max_sec_to_expiry)
            if isinstance(selection_gate_max_sec_to_expiry, (int, float))
            and not isinstance(selection_gate_max_sec_to_expiry, bool)
            else None
        )
        self.maker_selection_gate_cannon_target_notional_usd = max(
            1e-9,
            float(selection_owner_cfg.get("cannon_target_notional_usd", 350.0) or 350.0),
        )
        self.maker_selection_gate_min_depth_multiple = max(
            0.0,
            float(
                selection_owner_cfg.get(
                    "maker_min_depth_multiple",
                    selection_owner_cfg.get("min_depth_multiple", 1.5),
                )
                or 0.0
            ),
        )
        max_same_target_submit_count_prior = selection_owner_cfg.get(
            "max_same_target_submit_count_prior",
            1,
        )
        self.maker_selection_gate_max_same_target_submit_count_prior = max(
            0,
            int(float(max_same_target_submit_count_prior or 0)),
        )
        max_same_target_side_submit_count_prior = selection_owner_cfg.get(
            "max_same_target_side_submit_count_prior",
            1,
        )
        self.maker_selection_gate_max_same_target_side_submit_count_prior = max(
            0,
            int(float(max_same_target_side_submit_count_prior or 0)),
        )
        self._maker_admission_shadow_seq = 0
        self._maker_target_shadow_count_by_ref: Dict[str, int] = {}
        self._maker_target_submit_count_by_ref: Dict[str, int] = {}
        self._maker_target_side_shadow_count_by_ref: Dict[str, int] = {}
        self._maker_target_side_submit_count_by_ref: Dict[str, int] = {}

    def _next_client_order_id(self, token_id: str, side: str) -> str:
        self._client_seq += 1
        return f"{token_id[:8]}-{side[0]}-{self._client_seq}"

    @staticmethod
    def _derive_target_ref(token_id: str) -> Optional[str]:
        normalized = str(token_id or "").strip()
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _submission_lane(intent: OrderIntent) -> str:
        explicit = str(intent.submission_lane or "").strip().lower()
        if explicit:
            return explicit
        tif = str(intent.tif or "GTC").upper()
        is_taker = bool(intent.post_only is False) or tif in {"IOC", "FOK"}
        return "taker" if is_taker else "maker"

    @staticmethod
    def _is_commitment_candidate_intent(intent: OrderIntent, lane: str) -> bool:
        return bool(
            str(lane or "").strip().lower() == "maker"
            and bool(intent.post_only is not False)
            and str(intent.tif or "GTC").upper() == "GTC"
        )

    @staticmethod
    def _normalize_cancel_request_origin(request_origin: Optional[str], requested_reason: str) -> str:
        explicit = str(request_origin or "").strip().lower()
        if explicit:
            return explicit
        normalized_reason = str(requested_reason or "").strip().lower()
        if normalized_reason == COMMITMENT_CANCEL_REASON_WINDOW_ENDED:
            return "commitment_expiry_cleanup"
        return normalized_reason or "unknown"

    @staticmethod
    def _commitment_expiry_dt(order: LiveOrder) -> Optional[dt.datetime]:
        raw = str(getattr(order, "commitment_expiry_ts_utc", "") or "").strip()
        if not raw:
            return None
        return parse_ts(raw)

    @staticmethod
    def _commitment_hold_configured(order: LiveOrder) -> bool:
        return bool(
            str(getattr(order, "submission_lane", "") or "").strip().lower() == "maker"
            and bool(getattr(order, "commitment_hold_active", False))
        )

    def _commitment_hold_active(self, order: LiveOrder, *, now: Optional[dt.datetime] = None) -> bool:
        if not self._commitment_hold_configured(order):
            return False
        expiry_dt = self._commitment_expiry_dt(order)
        if expiry_dt is None:
            return True
        now_dt = now or self._now_fn()
        return bool(now_dt < expiry_dt)

    def _commitment_window_ended(self, order: LiveOrder, *, now: Optional[dt.datetime] = None) -> bool:
        if not self._commitment_hold_configured(order):
            return False
        expiry_dt = self._commitment_expiry_dt(order)
        if expiry_dt is None:
            return False
        now_dt = now or self._now_fn()
        return bool(now_dt >= expiry_dt)

    def _cancel_request_class(
        self,
        *,
        order: LiveOrder,
        requested_reason: str,
        request_origin: str,
    ) -> str:
        if self._commitment_window_ended(order):
            return COMMITMENT_CANCEL_CLASS_TERMINAL
        if request_origin == "commitment_expiry_cleanup":
            return COMMITMENT_CANCEL_CLASS_TERMINAL
        if request_origin in COMMITMENT_EXCEPTIONAL_CANCEL_ORIGINS:
            return COMMITMENT_CANCEL_CLASS_EXCEPTIONAL
        return COMMITMENT_CANCEL_CLASS_ROUTINE

    def _commitment_metadata_from_order(self, order: LiveOrder) -> Dict[str, Any]:
        return {
            "submission_lane": (
                str(getattr(order, "submission_lane", "") or "").strip().lower() or None
            ),
            "commitment_hold_active": bool(getattr(order, "commitment_hold_active", False)),
            "commitment_hold_reason": (
                str(getattr(order, "commitment_hold_reason", "") or "").strip() or None
            ),
            "commitment_expiry_ts_utc": (
                str(getattr(order, "commitment_expiry_ts_utc", "") or "").strip() or None
            ),
        }

    def _active_committed_maker_order(self, order: Optional[LiveOrder]) -> bool:
        if order is None:
            return False
        return bool(
            str(getattr(order, "submission_lane", "") or "").strip().lower() == "maker"
            and self._commitment_hold_active(order)
        )

    @staticmethod
    def _resolve_submit_sec_to_expiry(
        *,
        risk_basis: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
        competitiveness_context: Optional[Dict[str, Any]],
    ) -> Optional[float]:
        for source in (risk_basis, risk_context, competitiveness_context):
            if not isinstance(source, dict):
                continue
            raw = source.get("sec_to_expiry")
            if isinstance(raw, (int, float)):
                return float(raw)
        return None

    def _decorate_intent_with_commitment_metadata(
        self,
        *,
        intent: OrderIntent,
        lane: str,
        submit_ts_utc: str,
        sec_to_expiry: Optional[float],
    ) -> Optional[OrderIntent]:
        normalized_lane = str(lane or "").strip().lower() or self._submission_lane(intent)
        if not self._is_commitment_candidate_intent(intent, normalized_lane):
            return dataclasses.replace(
                intent,
                submission_lane=normalized_lane,
                commitment_hold_active=False,
                commitment_hold_reason=None,
                commitment_expiry_ts_utc=None,
            )
        submit_ts = parse_ts(submit_ts_utc)
        if submit_ts is None or not isinstance(sec_to_expiry, (int, float)):
            return None
        expiry_ts_utc = utc_iso(submit_ts + dt.timedelta(seconds=float(sec_to_expiry)))
        return dataclasses.replace(
            intent,
            submission_lane=normalized_lane,
            commitment_hold_active=True,
            commitment_hold_reason="late_window_commitment",
            commitment_expiry_ts_utc=expiry_ts_utc,
        )

    def _cleanup_expired_commitment_orders(
        self,
        open_orders: List[LiveOrder],
        *,
        max_actions: int,
    ) -> Tuple[List[LiveOrder], int]:
        if max_actions <= 0:
            return list(open_orders), 0
        keep: List[LiveOrder] = []
        actions = 0
        for order in open_orders:
            if not self._commitment_window_ended(order):
                keep.append(order)
                continue
            if actions >= max_actions:
                keep.append(order)
                continue
            result = self._request_cancel_order(
                order,
                COMMITMENT_CANCEL_REASON_WINDOW_ENDED,
                request_origin="commitment_expiry_cleanup",
            )
            if bool(result.get("executed", False)):
                actions += 1
            else:
                keep.append(order)
        return keep, actions

    def _next_maker_admission_shadow_id(self, token_id: str, side: str) -> str:
        self._maker_admission_shadow_seq += 1
        return f"maker-shadow-{str(token_id or '')[:8]}-{str(side or '')[:1]}-{self._maker_admission_shadow_seq}"

    @staticmethod
    def _target_side_ref(target_ref: Optional[str], side: str) -> str:
        return f"{str(target_ref or '').strip() or 'unknown'}|{str(side or '').strip().upper() or 'UNKNOWN'}"

    @staticmethod
    def _maker_queue_severity_class(queue_delta_shares: Optional[float]) -> str:
        if not isinstance(queue_delta_shares, (int, float)):
            return "unknown"
        delta = float(queue_delta_shares)
        if delta <= 0.0:
            return "within_threshold"
        if delta <= 25.0:
            return "within_25"
        if delta <= 50.0:
            return "25_to_50"
        return "gt_50"

    def _evaluate_maker_selection_gate(
        self,
        *,
        shadow_event: Dict[str, Any],
        competitiveness_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        lifecycle_phase = str(
            shadow_event.get(EDGE_LIFECYCLE_PHASE_FIELD)
            or competitiveness_context.get(EDGE_LIFECYCLE_PHASE_FIELD)
            or competitiveness_context.get("lifecycle_phase")
                    or legacy_stage_to_lifecycle_phase(
                        shadow_event.get("stage")
                        or _compat_lineage_stage_from_payload(competitiveness_context)
                    )
        ).strip().lower()
        maker_phase_allowed = bool(
            shadow_event.get(
                EDGE_MAKER_PHASE_ALLOWED_FIELD,
                competitiveness_context.get(EDGE_MAKER_PHASE_ALLOWED_FIELD, False),
            )
        )
        gate_applied = bool(self.maker_selection_gate_enabled)
        depth_multiple_vs_cannon_target = None
        visible_depth_notional_usd = None
        desired_quote_price = parse_float(shadow_event.get("desired_quote_price"))
        visible_depth_shares = parse_float(shadow_event.get("visible_depth_shares"))
        if isinstance(desired_quote_price, (int, float)) and isinstance(visible_depth_shares, (int, float)):
            visible_depth_notional_usd = float(desired_quote_price) * float(visible_depth_shares)
        if isinstance(visible_depth_notional_usd, (int, float)):
            depth_multiple_vs_cannon_target = (
                float(visible_depth_notional_usd)
                / float(self.maker_selection_gate_cannon_target_notional_usd)
            )
        cannon_depth_requirement_met = (
            bool(depth_multiple_vs_cannon_target >= self.maker_selection_gate_min_depth_multiple)
            if isinstance(depth_multiple_vs_cannon_target, (int, float))
            else None
        )
        repeat_target_side_submit_count_prior = int(
            max(0, int(float(shadow_event.get("same_target_side_submit_count_prior") or 0.0)))
        )
        repeat_target_submit_count_prior = int(
            max(0, int(float(shadow_event.get("same_target_submit_count_prior") or 0.0)))
        )
        repeat_target_side_calm = (
            repeat_target_side_submit_count_prior
            <= self.maker_selection_gate_max_same_target_side_submit_count_prior
        )
        repeat_target_calm = (
            repeat_target_submit_count_prior
            <= self.maker_selection_gate_max_same_target_submit_count_prior
        )
        secondary_oracle_confirmation = bool(
            shadow_event.get("secondary_oracle_confirmation", False)
        )
        one_sided_active = bool(
            shadow_event.get("one_sided_active", competitiveness_context.get("one_sided_active", False))
        )
        sec_to_expiry = parse_float(
            shadow_event.get("sec_to_expiry")
            if shadow_event.get("sec_to_expiry") is not None
            else competitiveness_context.get("sec_to_expiry")
        )
        timing_window_configured = (
            isinstance(self.maker_selection_gate_min_sec_to_expiry, (int, float))
            or isinstance(self.maker_selection_gate_max_sec_to_expiry, (int, float))
        )
        timing_window_met = None
        if timing_window_configured and isinstance(sec_to_expiry, (int, float)):
            timing_window_met = True
            if isinstance(self.maker_selection_gate_min_sec_to_expiry, (int, float)):
                timing_window_met = bool(
                    timing_window_met
                    and float(sec_to_expiry) >= float(self.maker_selection_gate_min_sec_to_expiry) - 1e-9
                )
            if isinstance(self.maker_selection_gate_max_sec_to_expiry, (int, float)):
                timing_window_met = bool(
                    timing_window_met
                    and float(sec_to_expiry) <= float(self.maker_selection_gate_max_sec_to_expiry) + 1e-9
                )

        reject_reasons: List[str] = []
        if gate_applied:
            if timing_window_configured:
                if not isinstance(sec_to_expiry, (int, float)):
                    reject_reasons.append("timing_window_unknown")
                elif timing_window_met is not True:
                    reject_reasons.append("timing_window_out_of_band")
            if (
                self.maker_selection_gate_require_secondary_oracle_confirmation
                and not secondary_oracle_confirmation
            ):
                reject_reasons.append("secondary_oracle_not_confirmed")
            if not repeat_target_calm:
                reject_reasons.append("selection_prior_target_submit")
            if cannon_depth_requirement_met is not True:
                reject_reasons.append("insufficient_depth_multiple")
            if not repeat_target_side_calm:
                reject_reasons.append("selection_prior_same_side_submit")

        return {
            "enabled": bool(self.maker_selection_gate_enabled),
            "applied": bool(gate_applied),
            "passed": bool(gate_applied and not reject_reasons) if gate_applied else None,
            EDGE_LIFECYCLE_PHASE_FIELD: lifecycle_phase,
            EDGE_MAKER_PHASE_ALLOWED_FIELD: bool(maker_phase_allowed),
            "one_sided_active": bool(one_sided_active),
            "cannon_target_notional_usd": float(self.maker_selection_gate_cannon_target_notional_usd),
            "cannon_min_depth_multiple": float(self.maker_selection_gate_min_depth_multiple),
            "visible_depth_notional_usd": (
                float(visible_depth_notional_usd)
                if isinstance(visible_depth_notional_usd, (int, float))
                else None
            ),
            "depth_multiple_vs_cannon_target": (
                float(depth_multiple_vs_cannon_target)
                if isinstance(depth_multiple_vs_cannon_target, (int, float))
                else None
            ),
            "cannon_depth_requirement_met": cannon_depth_requirement_met,
            "require_secondary_oracle_confirmation": bool(
                self.maker_selection_gate_require_secondary_oracle_confirmation
            ),
            "min_sec_to_expiry": (
                float(self.maker_selection_gate_min_sec_to_expiry)
                if isinstance(self.maker_selection_gate_min_sec_to_expiry, (int, float))
                else None
            ),
            "max_sec_to_expiry": (
                float(self.maker_selection_gate_max_sec_to_expiry)
                if isinstance(self.maker_selection_gate_max_sec_to_expiry, (int, float))
                else None
            ),
            "sec_to_expiry": float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None,
            "timing_window_met": timing_window_met,
            "secondary_oracle_confirmation": bool(secondary_oracle_confirmation),
            "max_same_target_submit_count_prior": int(
                self.maker_selection_gate_max_same_target_submit_count_prior
            ),
            "same_target_submit_count_prior": int(repeat_target_submit_count_prior),
            "repeat_target_calm": bool(repeat_target_calm),
            "max_same_target_side_submit_count_prior": int(
                self.maker_selection_gate_max_same_target_side_submit_count_prior
            ),
            "same_target_side_submit_count_prior": int(repeat_target_side_submit_count_prior),
            "repeat_target_side_calm": bool(repeat_target_side_calm),
            "reject_reasons": list(reject_reasons),
            "primary_reject_reason": reject_reasons[0] if reject_reasons else None,
        }

    def _build_maker_fight_admission_shadow_event(
        self,
        *,
        token_id: str,
        side: str,
        top: BookTop,
        desired_intent: OrderIntent,
        competitiveness_context: Dict[str, Any],
        cycle_index: Optional[int],
        primary: Optional[LiveOrder],
        open_maker_orders_total: int,
        open_orders_for_token_count: int,
        open_orders_same_side_count: int,
    ) -> Dict[str, Any]:
        target_ref = (
            str(desired_intent.target_ref).strip()
            if str(desired_intent.target_ref or "").strip()
            else self._derive_target_ref(token_id)
        )
        target_side_ref = self._target_side_ref(target_ref, side)
        same_target_shadow_count_prior = int(
            self._maker_target_shadow_count_by_ref.get(str(target_ref or "").strip(), 0)
        )
        same_target_submit_count_prior = int(
            self._maker_target_submit_count_by_ref.get(str(target_ref or "").strip(), 0)
        )
        same_target_side_shadow_count_prior = int(
            self._maker_target_side_shadow_count_by_ref.get(target_side_ref, 0)
        )
        same_target_side_submit_count_prior = int(
            self._maker_target_side_submit_count_by_ref.get(target_side_ref, 0)
        )
        geometry_floor_price = self._maker_geometry_floor_price()
        sizing_price_used = self._sizing_price(top, side)
        resolved_size, size_resolution = self._resolve_order_size_shares_with_details(
            desired_intent,
            top,
        )
        size_decision_reasons = (
            list(size_resolution.get("size_decision_reasons") or [])
            if isinstance(size_resolution, dict)
            else []
        )
        sizing_conflict = bool(resolved_size is None)
        if not sizing_conflict:
            sizing_conflict = any(
                reason in {
                    "maker_hard_min_notional_failed_after_rounding",
                    "maker_hard_max_notional_failed_after_rounding",
                    "global_notional_bounds_after_rounding",
                    "rounded_shares_nonpositive",
                    "price_unavailable",
                }
                for reason in size_decision_reasons
            )
        viability_class = "unknown_viability"
        if isinstance(sizing_price_used, (int, float)) and isinstance(geometry_floor_price, (int, float)):
            if float(sizing_price_used) + 1e-9 < float(geometry_floor_price):
                viability_class = "impossible_only"
            else:
                viability_class = "viable_only"
        elif isinstance(sizing_price_used, (int, float)):
            viability_class = "viable_only"

        expected_fill_prob = None
        min_expected_fill_prob = None
        fill_prob_margin = None
        queue_ahead_size = None
        max_queue_ahead_size = None
        queue_delta_shares = None
        if self.quality.enabled:
            quality = self.quality.assess_quote(intent=desired_intent, top=top)
            expected_fill_prob = float(quality.expected_fill_prob)
            min_expected_fill_prob = float(self.quality.min_expected_fill_prob)
            fill_prob_margin = float(expected_fill_prob - min_expected_fill_prob)
            queue_ahead_size = float(quality.queue_ahead_size)
            max_queue_ahead_size = float(self.quality.max_queue_ahead_size)
            queue_delta_shares = float(queue_ahead_size - max_queue_ahead_size)

        visible_depth_shares = float(self._maker_visible_depth_shares(top, side))
        intended_size_shares = (
            float(resolved_size)
            if isinstance(resolved_size, (int, float))
            else None
        )
        intended_notional_usd = None
        if isinstance(size_resolution, dict) and isinstance(size_resolution.get("resolved_notional_usd"), (int, float)):
            intended_notional_usd = float(size_resolution.get("resolved_notional_usd"))
        size_to_visible_depth_ratio = None
        if isinstance(intended_size_shares, (int, float)) and visible_depth_shares > 0.0:
            size_to_visible_depth_ratio = float(intended_size_shares / visible_depth_shares)

        replace_guard_would_block = False
        if primary is not None and self.maker_replace_min_rest_sec > 0.0:
            age_sec = self._order_age_sec(primary)
            replace_guard_would_block = bool(
                isinstance(age_sec, (int, float)) and float(age_sec) < self.maker_replace_min_rest_sec
            )

        fair_probability = parse_float(competitiveness_context.get("fair_probability"))
        market_probability = parse_float(competitiveness_context.get("market_probability"))
        sec_to_expiry = parse_float(competitiveness_context.get("sec_to_expiry"))
        secondary_fair_probability = parse_float(
            competitiveness_context.get("secondary_fair_probability")
        )
        secondary_edge_value = parse_float(
            competitiveness_context.get("secondary_edge_value")
        )
        chainlink_spot_price = parse_float(
            competitiveness_context.get("chainlink_spot_price")
        )
        secondary_oracle_spot_price = parse_float(
            competitiveness_context.get("secondary_oracle_spot_price")
        )
        secondary_oracle_price_delta_abs = parse_float(
            competitiveness_context.get("secondary_oracle_price_delta_abs")
        )
        secondary_oracle_price_delta_bps = parse_float(
            competitiveness_context.get("secondary_oracle_price_delta_bps")
        )
        market_reference_class = str(
            competitiveness_context.get("market_reference_class") or ""
        ).strip().lower()
        if not market_reference_class:
            market_reference_class = (
                "authoritative"
                if isinstance(market_probability, (int, float))
                else "not_available"
            )

        return {
            "ts_utc": utc_iso(),
            "admission_shadow_id": self._next_maker_admission_shadow_id(token_id, side),
            "token_id": str(token_id),
            "target_ref": target_ref,
            "target_side_ref": target_side_ref,
            "side": str(side).strip().upper(),
            "one_sided_active": bool(competitiveness_context.get("one_sided_active", False)),
            "side_policy": str(competitiveness_context.get("side_policy") or "TWO_SIDED").strip().upper(),
            **lifecycle_phase_surface_fields(
                lifecycle_phase=(
                    competitiveness_context.get(EDGE_LIFECYCLE_PHASE_FIELD)
                    or competitiveness_context.get("lifecycle_phase")
                    or legacy_stage_to_lifecycle_phase(
                        desired_intent.stage
                        or _compat_lineage_stage_from_payload(competitiveness_context)
                    )
                )
            ),
            **lineage_stage_surface_fields(
                lineage_stage=(
                    competitiveness_context.get("lineage_stage")
                    or competitiveness_context.get(EDGE_STAGE_BUCKET_FIELD)
                    or competitiveness_context.get("lineage_stage")
                )
            ),
            "cycle_index": int(cycle_index) if isinstance(cycle_index, int) else None,
            "ts_decision_utc": (
                str(desired_intent.decision_reference_ts_utc).strip()
                if str(desired_intent.decision_reference_ts_utc or "").strip()
                else (
                    str(desired_intent.timestamp_utc).strip()
                    if str(desired_intent.timestamp_utc or "").strip()
                    else (
                        str(top.ts_utc).strip()
                        if str(getattr(top, "ts_utc", "") or "").strip()
                        else utc_iso()
                    )
                )
            ),
            "fair_probability": float(fair_probability) if isinstance(fair_probability, (int, float)) else None,
            "market_probability": float(market_probability) if isinstance(market_probability, (int, float)) else None,
            "edge_value": parse_float(competitiveness_context.get("edge_signed")),
            "sec_to_expiry": float(sec_to_expiry) if isinstance(sec_to_expiry, (int, float)) else None,
            "market_reference_class": market_reference_class,
            "market_reference_mode": (
                str(competitiveness_context.get("market_reference_mode") or "").strip().lower() or None
            ),
            "secondary_fair_probability": (
                float(secondary_fair_probability)
                if isinstance(secondary_fair_probability, (int, float))
                else None
            ),
            "secondary_edge_value": (
                float(secondary_edge_value)
                if isinstance(secondary_edge_value, (int, float))
                else None
            ),
            "secondary_oracle_status": str(
                competitiveness_context.get("secondary_oracle_status") or "unknown"
            ).strip().lower()
            or "unknown",
            "secondary_oracle_confirmation": bool(
                competitiveness_context.get("secondary_oracle_confirmation", False)
            ),
            "maker_timing_gate_open": bool(
                competitiveness_context.get("maker_timing_gate_open", False)
            ),
            "maker_timing_stage_override_active": bool(
                competitiveness_context.get("maker_timing_stage_override_active", False)
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
            "viability_class": viability_class,
            "geometry_floor_price": float(geometry_floor_price) if isinstance(geometry_floor_price, (int, float)) else None,
            "sizing_price_used": float(sizing_price_used) if isinstance(sizing_price_used, (int, float)) else None,
            "sizing_conflict": bool(sizing_conflict),
            "desired_quote_price": float(desired_intent.price),
            "intended_size_shares": float(intended_size_shares) if isinstance(intended_size_shares, (int, float)) else None,
            "intended_notional_usd": float(intended_notional_usd) if isinstance(intended_notional_usd, (int, float)) else None,
            "expected_fill_prob": expected_fill_prob,
            "min_expected_fill_prob": min_expected_fill_prob,
            "fill_prob_margin": fill_prob_margin,
            "queue_ahead_size": queue_ahead_size,
            "max_queue_ahead_size": max_queue_ahead_size,
            "queue_delta_shares": queue_delta_shares,
            "queue_severity_class": self._maker_queue_severity_class(queue_delta_shares),
            "visible_depth_shares": float(visible_depth_shares),
            "size_to_visible_depth_ratio": (
                float(size_to_visible_depth_ratio)
                if isinstance(size_to_visible_depth_ratio, (int, float))
                else None
            ),
            "same_target_shadow_count_prior": int(same_target_shadow_count_prior),
            "same_target_submit_count_prior": int(same_target_submit_count_prior),
            "same_target_side_shadow_count_prior": int(same_target_side_shadow_count_prior),
            "same_target_side_submit_count_prior": int(same_target_side_submit_count_prior),
            "open_maker_orders_total": int(max(0, open_maker_orders_total)),
            "open_orders_for_token_count": int(max(0, open_orders_for_token_count)),
            "open_orders_same_side_count": int(max(0, open_orders_same_side_count)),
            "financial_posture_class": str(
                competitiveness_context.get("financial_posture_class") or "UNKNOWN"
            ).strip().upper(),
            "replace_guard_would_block": bool(replace_guard_would_block),
            "decision_result": None,
            "decision_block_reason": None,
            "selection_gate_primary_reject_reason": None,
            "selection_gate_all_reject_reasons": [],
            "order_submit_id": None,
        }

    def _log_maker_fight_admission_shadow_event(
        self,
        shadow_event: Optional[Dict[str, Any]],
        *,
        decision_result: str,
        decision_block_reason: Optional[str] = None,
        order_submit_id: Optional[str] = None,
    ) -> None:
        if not isinstance(shadow_event, dict):
            return
        target_side_ref = str(shadow_event.get("target_side_ref") or "").strip()
        target_ref = str(shadow_event.get("target_ref") or "").strip()
        if not target_side_ref:
            return
        if target_ref:
            self._maker_target_shadow_count_by_ref[target_ref] = (
                int(self._maker_target_shadow_count_by_ref.get(target_ref, 0)) + 1
            )
        self._maker_target_side_shadow_count_by_ref[target_side_ref] = (
            int(self._maker_target_side_shadow_count_by_ref.get(target_side_ref, 0)) + 1
        )
        if str(order_submit_id or "").strip():
            if target_ref:
                self._maker_target_submit_count_by_ref[target_ref] = (
                    int(self._maker_target_submit_count_by_ref.get(target_ref, 0)) + 1
                )
            self._maker_target_side_submit_count_by_ref[target_side_ref] = (
                int(self._maker_target_side_submit_count_by_ref.get(target_side_ref, 0)) + 1
            )
        payload = dict(shadow_event)
        # This event captures the finalized shadow outcome after downstream selection,
        # replace, and submit work has run, so stamp all local event/decision domains
        # from the authoritative manager clock at log time rather than preserving an
        # older upstream competitiveness anchor.
        finalized_shadow_ts_utc = utc_iso(self._now_fn())
        payload["ts_utc"] = finalized_shadow_ts_utc
        payload["ts_event_utc"] = finalized_shadow_ts_utc
        payload["ts_decision_utc"] = finalized_shadow_ts_utc
        payload["decision_result"] = str(decision_result or "").strip().lower() or "unknown"
        payload["decision_block_reason"] = (
            str(decision_block_reason).strip().lower()
            if str(decision_block_reason or "").strip()
            else None
        )
        payload["order_submit_id"] = str(order_submit_id).strip() if str(order_submit_id or "").strip() else None
        self.events.log_event("maker_fight_admission_shadow", payload)

    @staticmethod
    def _would_post_only_cross_touch(intent: OrderIntent, top: BookTop) -> bool:
        tif = str(intent.tif or "GTC").upper()
        if bool(intent.post_only is False) or tif in {"IOC", "FOK"}:
            return False
        if intent.side == "BUY" and top.best_ask_price is not None:
            return float(intent.price) >= float(top.best_ask_price)
        if intent.side == "SELL" and top.best_bid_price is not None:
            return float(intent.price) <= float(top.best_bid_price)
        return False

    def _maybe_clamp_post_only_intent(
        self,
        intent: OrderIntent,
        top: BookTop,
    ) -> Tuple[OrderIntent, Optional[Dict[str, Any]]]:
        """Clamp maker post-only quotes to a non-crossing level when deterministically possible."""
        tif = str(intent.tif or "GTC").upper()
        if bool(intent.post_only is False) or tif in {"IOC", "FOK"}:
            return intent, None
        if not self._would_post_only_cross_touch(intent, top):
            return intent, None

        side = str(intent.side or "").upper()
        original_price = float(intent.price)
        tick = max(1e-9, float(self.tick_size))
        adjusted_price: Optional[float] = None

        if side == "BUY" and top.best_ask_price is not None:
            best_ask = float(top.best_ask_price)
            limit = best_ask - tick
            if limit <= 0.0:
                return intent, None
            adjusted_price = min(original_price, limit)
            adjusted_price = math.floor(adjusted_price / tick) * tick
            if adjusted_price >= best_ask:
                adjusted_price = best_ask - tick
        elif side == "SELL" and top.best_bid_price is not None:
            best_bid = float(top.best_bid_price)
            limit = best_bid + tick
            adjusted_price = max(original_price, limit)
            adjusted_price = math.ceil(adjusted_price / tick) * tick
            if adjusted_price <= best_bid:
                adjusted_price = best_bid + tick
        else:
            return intent, None

        if adjusted_price is None or not math.isfinite(adjusted_price):
            return intent, None
        adjusted_price = round(float(adjusted_price), 9)
        if adjusted_price <= 0.0 or adjusted_price >= 1.0:
            return intent, None
        if abs(adjusted_price - original_price) <= 1e-12:
            return intent, None

        payload = dict(intent.__dict__)
        payload["price"] = adjusted_price
        adjusted_intent = OrderIntent(**payload)
        if self._would_post_only_cross_touch(adjusted_intent, top):
            return intent, None
        return adjusted_intent, {
            "original_price": original_price,
            "adjusted_price": adjusted_price,
            "tick_size": tick,
            "adjustment_reason": "post_only_cross_touch_clamp",
        }

    @staticmethod
    def _normalize_maker_no_submission_category(reason: str) -> str:
        normalized = normalize_block_reason(reason)
        if not normalized:
            return "unknown"
        if normalized.startswith("submit_rejected_"):
            normalized = normalized.removeprefix("submit_rejected_")
        if normalized.startswith("risk_reject"):
            return "risk_reject"
        mapping = {
            "pre_submit_cross_guarded": "pre_submit_cross_guarded",
            "post_only_reject": "post_only_reject",
            "order_soft_throttle": "soft_throttle",
            "quote_quality_skip_fill_probability": "quote_quality_skip_fill_probability",
            "quote_quality_skip_queue_depth": "quote_quality_skip_queue_depth",
            "one_sided_mode_disallow_side": "one_sided_mode_disallow_side",
            "maker_timing_gate_closed": "maker_timing_gate_closed",
            "phase_disallow_maker": "phase_disallow_maker",
            "risk_reject": "risk_reject",
            "replace_guard_min_rest": "replace_guard_min_rest",
            "replace_cancel_unavailable": "replace_cancel_unavailable",
            "action_budget_exhausted": "action_budget_exhausted",
            "no_desired_quote": "no_desired_quote",
            "quote_unchanged": "quote_unchanged",
            "sizing_reject": "sizing_reject",
            "wallet_reject": "wallet_reject",
            "order_submit_exception": "order_submit_exception",
            "maker_commitment_hold_active": "maker_commitment_hold_active",
            "maker_commitment_context_missing": "maker_commitment_context_missing",
            "open_order_cleanup_required": "open_order_cleanup_required",
            "settlement_hold_required": "settlement_hold_required",
        }
        return str(mapping.get(normalized, "unknown"))

    def _order_age_sec(self, order: LiveOrder) -> Optional[float]:
        if not order.created_ts_utc:
            return None
        ts = parse_ts(order.created_ts_utc)
        if ts is None:
            return None
        return (self._now_fn() - ts).total_seconds()

    def _needs_replace(self, order: LiveOrder, intent: OrderIntent, *, requote_delta: Optional[float] = None) -> bool:
        delta = self.requote_delta if requote_delta is None else max(1e-9, float(requote_delta))
        if abs(order.price - intent.price) >= delta:
            return True
        if abs(order.remaining_size - intent.size) >= max(1.0, intent.size * 0.15):
            return True
        age_sec = self._order_age_sec(order)
        if age_sec is not None and age_sec > self.max_quote_age_sec:
            return True
        return False

    def _remove_token_order_if_present(
        self,
        token_orders: List[LiveOrder],
        order: LiveOrder,
        *,
        remove_reason: str,
    ) -> bool:
        if order in token_orders:
            token_orders.remove(order)
            return True
        self.telemetry.incr("token_order_local_remove_miss")
        self.events.log_event(
            "token_order_local_remove_miss",
            {
                "ts_utc": utc_iso(),
                "token_id": order.token_id,
                "order_id": order.order_id,
                "side": order.side,
                "remove_reason": str(remove_reason or "").strip().lower() or "unknown",
            },
        )
        return False

    def _request_cancel_order(
        self,
        order: LiveOrder,
        requested_reason: str,
        *,
        request_origin: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_reason = str(requested_reason or "").strip().lower() or "unknown"
        normalized_origin = self._normalize_cancel_request_origin(request_origin, normalized_reason)
        commitment_metadata = self._commitment_metadata_from_order(order)
        cancel_class = self._cancel_request_class(
            order=order,
            requested_reason=normalized_reason,
            request_origin=normalized_origin,
        )
        commitment_active = self._commitment_hold_active(order)
        suppress_routine = bool(
            cancel_class == COMMITMENT_CANCEL_CLASS_ROUTINE
            and commitment_active
        )
        missing_expiry_fail_closed = bool(
            cancel_class == COMMITMENT_CANCEL_CLASS_ROUTINE
            and self._commitment_hold_configured(order)
            and self._commitment_expiry_dt(order) is None
        )
        if suppress_routine or missing_expiry_fail_closed:
            suppression_reason = (
                COMMITMENT_SUPPRESSION_REASON_MISSING_EXPIRY
                if missing_expiry_fail_closed
                else COMMITMENT_SUPPRESSION_REASON_ACTIVE_HOLD
            )
            self.events.log_event(
                "order_cancel_suppressed",
                {
                    "ts_utc": utc_iso(),
                    "order_id": order.order_id,
                    "token_id": order.token_id,
                    "side": order.side,
                    "price": order.price,
                    "remaining_size": order.remaining_size,
                    "requested_cancel_reason": normalized_reason,
                    "request_origin": normalized_origin,
                    "cancel_class_requested": cancel_class,
                    "suppression_reason": suppression_reason,
                    **commitment_metadata,
                },
            )
            return {
                "executed": False,
                "suppressed": True,
                "cancel_class": cancel_class,
                "effective_cancel_reason": None,
                "suppression_reason": suppression_reason,
            }

        effective_reason = (
            COMMITMENT_CANCEL_REASON_WINDOW_ENDED
            if cancel_class == COMMITMENT_CANCEL_CLASS_TERMINAL
            else normalized_reason
        )
        bypass_internal_cancel_limits = bool(
            cancel_class in {
                COMMITMENT_CANCEL_CLASS_TERMINAL,
                COMMITMENT_CANCEL_CLASS_EXCEPTIONAL,
            }
        )
        if (
            not bypass_internal_cancel_limits
            and self.risk.remaining_cancel_capacity(self.cancel_rate_soft_limit_pct) <= 0
        ):
            self.telemetry.incr("cancel_soft_throttle_skips")
            return {
                "executed": False,
                "suppressed": False,
                "cancel_class": cancel_class,
                "effective_cancel_reason": effective_reason,
                "suppression_reason": None,
            }
        decision = self.risk.can_cancel()
        if not bypass_internal_cancel_limits and not decision.allowed:
            self.telemetry.incr("cancel_rate_rejects")
            self.events.log_event(
                "risk_reject",
                {
                    "ts_utc": utc_iso(),
                    "token_id": order.token_id,
                    "side": order.side,
                    "price": order.price,
                    "size": order.remaining_size,
                    "reason": decision.reason,
                    "detail": decision.detail,
                    "requested_cancel_reason": normalized_reason,
                    "request_origin": normalized_origin,
                },
            )
            return {
                "executed": False,
                "suppressed": False,
                "cancel_class": cancel_class,
                "effective_cancel_reason": effective_reason,
                "suppression_reason": None,
            }

        try:
            ok = self.tx_manager.cancel_order(order.order_id)
        except ORDER_TRANSPORT_EXCEPTIONS as exc:
            self.telemetry.incr("cancel_failures")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "order_manager",
                    "action": "cancel",
                    "order_id": order.order_id,
                    "reason": "gateway_cancel_exception",
                    "requested_cancel_reason": normalized_reason,
                    "request_origin": normalized_origin,
                    "error": str(exc),
                }
            )
            return {
                "executed": False,
                "suppressed": False,
                "cancel_class": cancel_class,
                "effective_cancel_reason": effective_reason,
                "suppression_reason": None,
            }
        if ok:
            self.risk.on_order_canceled()
            self.wallet.release_order_lock(order.order_id)
            self.telemetry.incr("orders_canceled")
            self.events.log_event(
                "order_cancel",
                {
                    "ts_utc": utc_iso(),
                    "order_id": order.order_id,
                    "token_id": order.token_id,
                    "side": order.side,
                    "price": order.price,
                    "size": order.remaining_size,
                    "reason": effective_reason,
                    "requested_cancel_reason": normalized_reason,
                    "request_origin": normalized_origin,
                    "cancel_class": cancel_class,
                    "cancel_allowed_under_commitment_doctrine": True,
                    **commitment_metadata,
                },
            )
        else:
            self.telemetry.incr("cancel_failures")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "order_manager",
                    "action": "cancel",
                    "order_id": order.order_id,
                    "reason": "gateway_cancel_failed",
                    "requested_cancel_reason": normalized_reason,
                    "request_origin": normalized_origin,
                }
            )
        return {
            "executed": bool(ok),
            "suppressed": False,
            "cancel_class": cancel_class,
            "effective_cancel_reason": effective_reason,
            "suppression_reason": None,
        }

    def _cancel_order(
        self,
        order: LiveOrder,
        reason: str,
        *,
        request_origin: Optional[str] = None,
    ) -> bool:
        result = self._request_cancel_order(order, reason, request_origin=request_origin)
        return bool(result.get("executed", False))

    @staticmethod
    def _extract_remaining_size(order: LiveOrder) -> Optional[float]:
        raw = getattr(order, "remaining_size", None)
        if raw is None:
            return None
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            return None
        return max(0.0, parsed)

    @staticmethod
    def _order_status_norm(order: LiveOrder) -> str:
        return str(getattr(order, "status", "") or "").strip().upper()

    def _residual_exposure_exists(self, order: LiveOrder) -> bool:
        remaining_size = self._extract_remaining_size(order)
        if remaining_size is not None:
            return bool(remaining_size > ORDER_RESIDUAL_EXPOSURE_EPSILON)
        status_norm = self._order_status_norm(order)
        if status_norm in TERMINAL_ORDER_ACK_STATUSES:
            return False
        if status_norm in OPEN_EQUIVALENT_ORDER_ACK_STATUSES:
            return True
        # Unknown/non-terminal states are treated as potentially open.
        return True

    def _cleanup_failed_submission(
        self,
        *,
        wallet_lock_id: str,
        submission_lane: str,
        cleanup_reason: str,
        release_submission_reservation: bool = True,
    ) -> bool:
        released_submission_reservation = False
        if release_submission_reservation:
            released_submission_reservation = bool(self.risk.release_order_submission_reservation())
            if released_submission_reservation:
                self.telemetry.incr("order_submission_released")
                self.telemetry.incr(f"order_submission_released_{submission_lane}")
        self.wallet.release_pending_lock(wallet_lock_id)
        self.events.log_event(
            "wallet_reservation_cleanup",
            {
                "ts_utc": utc_iso(),
                "cleanup_reason": str(cleanup_reason or "unknown"),
                "lock_id": str(wallet_lock_id or ""),
                "submission_lane": str(submission_lane or ""),
                "submission_reservation_released": bool(released_submission_reservation),
            },
        )
        return released_submission_reservation

    def _place_order(
        self,
        intent: OrderIntent,
        top: BookTop,
        open_orders_for_token: List[LiveOrder],
        open_orders_total: int,
        open_orders_all: Optional[List[LiveOrder]] = None,
        reference_mid_by_token: Optional[Dict[str, Optional[float]]] = None,
        risk_context: Optional[Dict[str, Any]] = None,
        notional_target_usd: Optional[float] = None,
        competitiveness_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[LiveOrder], Optional[str]]:
        lane = self._submission_lane(intent)

        def _local_reject(reason: str, *, detail: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Tuple[None, str]:
            normalized_reason = str(reason or "").strip().lower() or "unknown"
            self.telemetry.incr("order_submission_rejected_local")
            self.telemetry.incr(f"order_submission_rejected_local_{normalized_reason}")
            self.telemetry.incr(f"order_submission_rejected_local_{lane}")
            payload: Dict[str, Any] = {
                "ts_utc": utc_iso(),
                "token_id": intent.token_id,
                "side": intent.side,
                "price": intent.price,
                "size": intent.size,
                "submission_lane": lane,
                "submission_reject_class": "rejected_local",
                "reason": normalized_reason,
                "detail": (str(detail).strip() if str(detail or "").strip() else None),
            }
            if isinstance(extra, dict):
                payload.update(extra)
            self.events.log_event("order_submission_rejected_local", payload)
            return None, normalized_reason

        risk_context_payload: Dict[str, Any] = dict(risk_context) if isinstance(risk_context, dict) else {}
        financial_posture_class = str(risk_context_payload.get("financial_posture_class") or "UNKNOWN").strip().upper()
        order_capacity = self.risk.order_capacity_state(self.order_rate_soft_limit_pct)
        soft_remaining_for_intent = int(order_capacity.get("orders_soft_remaining", 0))
        if soft_remaining_for_intent <= 0:
            self.telemetry.incr("order_soft_throttle_skips")
            self.events.log_event(
                "order_soft_throttle",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent.token_id,
                    "side": intent.side,
                    "price": intent.price,
                    "size": intent.size,
                    "submission_lane": lane,
                    "soft_throttle_decision_basis": {
                        "pool": "shared_order_rate_pool",
                        "threshold_basis": "order_rate_soft_limit_pct",
                        "orders_limit_60s": int(order_capacity.get("orders_limit", 0)),
                        "orders_soft_limit_60s": int(order_capacity.get("orders_soft_limit", 0)),
                        "orders_soft_effective_used_60s": int(order_capacity.get("orders_soft_effective_used", 0)),
                        "orders_used_accepted_60s": int(order_capacity.get("orders_used_accepted", 0)),
                        "orders_reserved_outstanding_60s": int(order_capacity.get("orders_reserved_outstanding", 0)),
                        "orders_transport_attempted_60s": int(
                            order_capacity.get("orders_transport_attempted_recent", 0)
                        ),
                        "orders_soft_remaining_60s": int(order_capacity.get("orders_soft_remaining", 0)),
                        "lane_attribution": "shared_pool_maker_and_taker",
                    },
                },
            )
            return _local_reject(
                "order_soft_throttle",
            )

        resolved_size, size_resolution = self._resolve_order_size_shares_with_details(
            intent,
            top,
            notional_target_usd=notional_target_usd,
        )

        if resolved_size is None:
            self.telemetry.incr("sizing_rejects")
            risk_context_payload = dict(risk_context) if isinstance(risk_context, dict) else {}
            event_lifecycle_phase, lifecycle_phase_source, lifecycle_phase_unknown_reason = _resolve_event_lifecycle_phase(
                intent_stage=intent.stage,
                risk_context=risk_context_payload,
            )
            event_financial_posture_class = str(
                risk_context_payload.get("financial_posture_class") or "UNKNOWN"
            ).strip().upper()
            event_sec_to_expiry = (
                float(risk_context_payload["sec_to_expiry"])
                if isinstance(risk_context_payload.get("sec_to_expiry"), (int, float))
                else None
            )
            self.events.log_event(
                "risk_reject",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent.token_id,
                    "side": intent.side,
                    "price": intent.price,
                    "size": intent.size,
                    "submission_lane": lane,
                    EDGE_LIFECYCLE_PHASE_FIELD: event_lifecycle_phase,
                    "lifecycle_phase_source": lifecycle_phase_source,
                    "lifecycle_phase_unknown_reason": lifecycle_phase_unknown_reason,
                    "financial_posture_class": event_financial_posture_class,
                    "sec_to_expiry": event_sec_to_expiry,
                    "reason": "size_notional_bounds",
                    "detail": f"mode={self.sizing_mode}",
                    "risk_decision_basis": {
                        "risk_authority": "sizing_pre_risk",
                        "submission_lane": lane,
                        EDGE_LIFECYCLE_PHASE_FIELD: event_lifecycle_phase,
                        "financial_posture_class": event_financial_posture_class,
                        "sec_to_expiry": event_sec_to_expiry,
                        "lifecycle_context_present": bool(event_sec_to_expiry is not None),
                    },
                    "size_resolution": size_resolution,
                },
            )
            return _local_reject(
                "sizing_reject",
                detail=f"mode={self.sizing_mode}",
                extra={"size_resolution": size_resolution},
            )
        intent_ts = utc_iso()
        default_execution_pref = (
            "taker_only"
            if bool(intent.post_only is False) or str(intent.tif or "GTC").upper() in {"IOC", "FOK"}
            else "maker_preferred"
        )
        intent_sized = OrderIntent(
            token_id=intent.token_id,
            side=intent.side,
            price=intent.price,
            size=resolved_size,
            tif=intent.tif,
            post_only=intent.post_only,
            reason=intent.reason,
            market_id=intent.market_id or intent.token_id,
            window_id=intent.window_id or intent_ts[:16],
            stage=intent.stage,
            reason_code=intent.reason_code or intent.reason,
            timestamp_utc=intent.timestamp_utc or intent_ts,
            execution_preference=intent.execution_preference or default_execution_pref,
            target_ref=(
                str(intent.target_ref).strip()
                if str(intent.target_ref or "").strip()
                else self._derive_target_ref(intent.token_id)
            ),
            decision_reference_midpoint=(
                float(intent.decision_reference_midpoint)
                if isinstance(intent.decision_reference_midpoint, (int, float))
                else (float(top.midpoint) if isinstance(top.midpoint, (int, float)) else None)
            ),
            decision_reference_source=(
                str(intent.decision_reference_source).strip()
                if str(intent.decision_reference_source or "").strip()
                else "order_submit_top_midpoint"
            ),
            decision_reference_lookup_key=(
                str(intent.decision_reference_lookup_key).strip()
                if str(intent.decision_reference_lookup_key or "").strip()
                else None
            ),
            decision_reference_ts_utc=(
                str(intent.decision_reference_ts_utc).strip()
                if str(intent.decision_reference_ts_utc or "").strip()
                else intent_ts
            ),
        )
        adjusted_intent, cross_clamp = self._maybe_clamp_post_only_intent(intent_sized, top)
        if cross_clamp is not None:
            intent_sized = adjusted_intent
            self.telemetry.incr("pre_submit_cross_guard_adjusted")
            self.events.log_event(
                "pre_submit_cross_guard_adjusted",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_sized.token_id,
                    "side": intent_sized.side,
                    "submission_lane": lane,
                    "original_price": cross_clamp.get("original_price"),
                    "adjusted_price": cross_clamp.get("adjusted_price"),
                    "tick_size": cross_clamp.get("tick_size"),
                    "best_bid_price": top.best_bid_price,
                    "best_ask_price": top.best_ask_price,
                    "adjustment_reason": cross_clamp.get("adjustment_reason"),
                    "submission_reject_class": "adjusted_local",
                },
            )

        if self._would_post_only_cross_touch(intent_sized, top):
            self.telemetry.incr("pre_submit_cross_guarded")
            self.events.log_event(
                "pre_submit_cross_guard",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_sized.token_id,
                    "side": intent_sized.side,
                    "price": intent_sized.price,
                    "size": intent_sized.size,
                    "best_bid_price": top.best_bid_price,
                    "best_ask_price": top.best_ask_price,
                    "submission_lane": lane,
                    "submission_reject_class": "rejected_local",
                },
            )
            return _local_reject("pre_submit_cross_guarded", detail="post_only_would_cross_touch")

        risk_context_payload = dict(risk_context) if isinstance(risk_context, dict) else {}
        risk_context_payload.setdefault("submission_lane", lane)
        risk_context_payload.setdefault(
            EDGE_LIFECYCLE_PHASE_FIELD,
            _canonical_lifecycle_phase_from_payload(risk_context_payload)
            or _canonical_lifecycle_phase_from_stage(intent_sized.stage)
            or "scan",
        )
        decision = self.risk.validate_order(
            intent_sized,
            top,
            open_orders_for_token,
            open_orders_total,
            open_orders_all=open_orders_all,
            reference_mid_by_token=reference_mid_by_token,
            risk_context=risk_context_payload,
        )
        if not decision.allowed:
            risk_basis = decision.basis if isinstance(decision.basis, dict) else None
            event_lifecycle_phase, lifecycle_phase_source, lifecycle_phase_unknown_reason = _resolve_event_lifecycle_phase(
                intent_stage=intent_sized.stage,
                risk_context=risk_context_payload,
                risk_basis=risk_basis,
            )
            self.telemetry.incr("risk_rejects")
            self.telemetry.incr(f"risk_reject_{decision.reason}")
            self.events.log_event(
                "risk_reject",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_sized.token_id,
                    "side": intent_sized.side,
                    "price": intent_sized.price,
                    "size": intent_sized.size,
                    "submission_lane": lane,
                    EDGE_LIFECYCLE_PHASE_FIELD: event_lifecycle_phase,
                    "lifecycle_phase_source": lifecycle_phase_source,
                    "lifecycle_phase_unknown_reason": lifecycle_phase_unknown_reason,
                    "reason": decision.reason,
                    "detail": decision.detail,
                    "risk_decision_basis": risk_basis,
                },
            )
            return _local_reject(
                f"risk_reject_{decision.reason}",
                detail=f"{decision.reason}:{decision.detail}",
                extra={"risk_decision_basis": risk_basis},
            )

        quality_fields: Dict[str, float] = {}
        if self.quality.enabled and intent_sized.post_only is not False and intent_sized.tif.upper() == "GTC":
            quality = self.quality.assess_quote(intent=intent_sized, top=top)
            default_min_expected_fill_prob = float(self.quality.min_expected_fill_prob)
            default_max_queue_ahead_size = float(self.quality.max_queue_ahead_size)
            effective_min_expected_fill_prob = float(default_min_expected_fill_prob)
            effective_max_queue_ahead_size = float(default_max_queue_ahead_size)
            pos = self.risk.positions.get(intent_sized.token_id)
            net_shares = float(getattr(pos, "net_shares", 0.0) or 0.0)
            is_pure_risk_reducing = RiskEngine._is_pure_risk_reducing_intent(
                net_shares=net_shares,
                side=intent_sized.side,
                size=float(intent_sized.size),
            )
            quality_fields = {
                "expected_fill_prob": quality.expected_fill_prob,
                "quality_score": quality.expected_quality_score,
                "queue_ahead_size": quality.queue_ahead_size,
                "distance_to_touch": quality.distance_to_touch,
                "adverse_selection_risk": quality.adverse_selection_risk,
            }
            self.telemetry.set_gauge(
                f"quote_quality_fill_prob.{intent_sized.token_id}.{intent_sized.side}",
                quality.expected_fill_prob,
            )
            self.telemetry.set_gauge(
                f"quote_quality_score.{intent_sized.token_id}.{intent_sized.side}",
                quality.expected_quality_score,
            )
            if quality.queue_ahead_size > float(effective_max_queue_ahead_size):
                self.telemetry.incr("low_quality_quote_skips")
                self.events.log_event(
                    "quote_quality_skip",
                    {
                        "ts_utc": utc_iso(),
                        "token_id": intent_sized.token_id,
                        "side": intent_sized.side,
                        "price": intent_sized.price,
                        "size": intent_sized.size,
                        "skip_reason": "queue_ahead_too_deep",
                        "queue_ahead_size": quality.queue_ahead_size,
                        "max_queue_ahead_size": float(effective_max_queue_ahead_size),
                        "expected_fill_prob": quality.expected_fill_prob,
                        "quality_score": quality.expected_quality_score,
                        "distance_to_touch": quality.distance_to_touch,
                        "default_min_expected_fill_prob": float(default_min_expected_fill_prob),
                        "default_max_queue_ahead_size": float(default_max_queue_ahead_size),
                        "effective_min_expected_fill_prob": float(effective_min_expected_fill_prob),
                        "effective_max_queue_ahead_size": float(effective_max_queue_ahead_size),
                    },
                )
                return _local_reject(
                    "quote_quality_skip_queue_depth",
                    detail="queue_ahead_too_deep",
                )
            if quality.expected_fill_prob < float(effective_min_expected_fill_prob):
                self.telemetry.incr("low_quality_quote_skips")
                self.events.log_event(
                    "quote_quality_skip",
                    {
                        "ts_utc": utc_iso(),
                        "token_id": intent_sized.token_id,
                        "side": intent_sized.side,
                        "price": intent_sized.price,
                        "size": intent_sized.size,
                        "skip_reason": "expected_fill_prob_below_min",
                        "expected_fill_prob": quality.expected_fill_prob,
                        "min_expected_fill_prob": float(effective_min_expected_fill_prob),
                        "quality_score": quality.expected_quality_score,
                        "queue_ahead_size": quality.queue_ahead_size,
                        "distance_to_touch": quality.distance_to_touch,
                        "default_min_expected_fill_prob": float(default_min_expected_fill_prob),
                        "default_max_queue_ahead_size": float(default_max_queue_ahead_size),
                        "effective_min_expected_fill_prob": float(effective_min_expected_fill_prob),
                        "effective_max_queue_ahead_size": float(effective_max_queue_ahead_size),
                    },
                )
                return _local_reject(
                    "quote_quality_skip_fill_probability",
                    detail="expected_fill_prob_below_min",
                )

        wallet_auth = self.wallet.authorize_intent(intent_sized)
        if not wallet_auth.allowed:
            self.telemetry.incr("wallet_rejects")
            if wallet_auth.halt or self.wallet.is_halted():
                self.telemetry.incr("wallet_halts")
                self.risk.set_kill_switch(f"wallet_halt:{self.wallet.halt_reason() or wallet_auth.reason}")
            self.events.log_event(
                "wallet_reject",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_sized.token_id,
                    "side": intent_sized.side,
                    "price": intent_sized.price,
                    "size": intent_sized.size,
                    "reason": wallet_auth.reason,
                    "detail": wallet_auth.detail,
                    "halt": wallet_auth.halt,
                },
            )
            return _local_reject("wallet_reject", detail=f"{wallet_auth.reason}:{wallet_auth.detail}")

        intent_authorized = intent_sized
        if wallet_auth.approved_size + 1e-9 < intent_sized.size:
            intent_authorized = OrderIntent(
                token_id=intent_sized.token_id,
                side=intent_sized.side,
                price=intent_sized.price,
                size=wallet_auth.approved_size,
                tif=intent_sized.tif,
                post_only=intent_sized.post_only,
                reason=intent_sized.reason,
                market_id=intent_sized.market_id,
                window_id=intent_sized.window_id,
                stage=intent_sized.stage,
                reason_code=intent_sized.reason_code,
                timestamp_utc=intent_sized.timestamp_utc,
                execution_preference=intent_sized.execution_preference,
                target_ref=intent_sized.target_ref,
                decision_reference_midpoint=intent_sized.decision_reference_midpoint,
                decision_reference_source=intent_sized.decision_reference_source,
                decision_reference_lookup_key=intent_sized.decision_reference_lookup_key,
                decision_reference_ts_utc=intent_sized.decision_reference_ts_utc,
            )
            self.telemetry.incr("wallet_authorize_reduce")
        else:
            self.telemetry.incr("wallet_authorize_approve")

        self.events.log_event(
            "wallet_authorization",
            {
                "ts_utc": utc_iso(),
                "token_id": intent_authorized.token_id,
                "side": intent_authorized.side,
                "price": intent_authorized.price,
                "requested_size": intent_sized.size,
                "approved_size": intent_authorized.size,
                "action": wallet_auth.action,
                "reason": wallet_auth.reason,
                "detail": wallet_auth.detail,
                "lock_id": wallet_auth.lock_id,
            },
        )
        if float(intent_authorized.size) + 1e-9 < float(intent_sized.size):
            post_wallet_context = dict(risk_context_payload)
            post_wallet_context["post_wallet_authorization_resize"] = True
            post_wallet_context["requested_size_before_wallet"] = float(intent_sized.size)
            post_wallet_context["approved_size_after_wallet"] = float(intent_authorized.size)
            post_wallet_decision = self.risk.validate_order(
                intent_authorized,
                top,
                open_orders_for_token,
                open_orders_total,
                open_orders_all=open_orders_all,
                reference_mid_by_token=reference_mid_by_token,
                risk_context=post_wallet_context,
            )
            if not post_wallet_decision.allowed:
                released = self._cleanup_failed_submission(
                    wallet_lock_id=wallet_auth.lock_id,
                    submission_lane=lane,
                    cleanup_reason="post_wallet_risk_reject",
                    release_submission_reservation=False,
                )
                risk_basis = post_wallet_decision.basis if isinstance(post_wallet_decision.basis, dict) else None
                event_lifecycle_phase, lifecycle_phase_source, lifecycle_phase_unknown_reason = _resolve_event_lifecycle_phase(
                    intent_stage=intent_authorized.stage,
                    risk_context=post_wallet_context,
                    risk_basis=risk_basis,
                )
                self.telemetry.incr("risk_rejects")
                self.telemetry.incr(f"risk_reject_{post_wallet_decision.reason}")
                self.events.log_event(
                    "risk_reject",
                    {
                        "ts_utc": utc_iso(),
                        "token_id": intent_authorized.token_id,
                        "side": intent_authorized.side,
                        "price": intent_authorized.price,
                        "size": intent_authorized.size,
                        "requested_size_before_wallet": float(intent_sized.size),
                        "submission_lane": lane,
                        EDGE_LIFECYCLE_PHASE_FIELD: event_lifecycle_phase,
                        "lifecycle_phase_source": lifecycle_phase_source,
                        "lifecycle_phase_unknown_reason": lifecycle_phase_unknown_reason,
                        "reason": post_wallet_decision.reason,
                        "detail": post_wallet_decision.detail,
                        "risk_decision_basis": risk_basis,
                        "post_wallet_authorization_revalidate": True,
                        "submission_reserved_released": bool(released),
                    },
                )
                return _local_reject(
                    f"risk_reject_{post_wallet_decision.reason}",
                    detail=f"{post_wallet_decision.reason}:{post_wallet_decision.detail}",
                    extra={
                        "risk_decision_basis": risk_basis,
                        "post_wallet_authorization_revalidate": True,
                    },
                )

        submit_ts_utc = utc_iso()
        risk_basis_event = decision.basis if isinstance(decision.basis, dict) else {}
        commitment_sec_to_expiry = self._resolve_submit_sec_to_expiry(
            risk_basis=risk_basis_event,
            risk_context=risk_context_payload,
            competitiveness_context=(
                competitiveness_context if isinstance(competitiveness_context, dict) else None
            ),
        )
        intent_authorized_with_commitment = self._decorate_intent_with_commitment_metadata(
            intent=intent_authorized,
            lane=lane,
            submit_ts_utc=submit_ts_utc,
            sec_to_expiry=commitment_sec_to_expiry,
        )
        if intent_authorized_with_commitment is None:
            released = self._cleanup_failed_submission(
                wallet_lock_id=wallet_auth.lock_id,
                submission_lane=lane,
                cleanup_reason="maker_commitment_context_missing",
                release_submission_reservation=False,
            )
            return _local_reject(
                "maker_commitment_context_missing",
                detail="missing_submit_expiry_context",
                extra={
                    "submission_reserved_released": bool(released),
                    "risk_decision_basis": risk_basis_event,
                    "risk_context_sec_to_expiry": (
                        float(risk_context_payload["sec_to_expiry"])
                        if isinstance(risk_context_payload.get("sec_to_expiry"), (int, float))
                        else None
                    ),
                    "competitiveness_sec_to_expiry": (
                        float(competitiveness_context["sec_to_expiry"])
                        if isinstance(competitiveness_context, dict)
                        and isinstance(competitiveness_context.get("sec_to_expiry"), (int, float))
                        else None
                    ),
                },
            )
        intent_authorized = intent_authorized_with_commitment

        client_order_id = self._next_client_order_id(intent_authorized.token_id, intent_authorized.side)
        self.risk.reserve_order_submission()
        self.telemetry.incr("order_submission_reserved")
        self.telemetry.incr(f"order_submission_reserved_{lane}")
        self.risk.mark_order_submission_transport_attempted()
        self.telemetry.incr("order_submission_transport_attempted")
        self.telemetry.incr(f"order_submission_transport_attempted_{lane}")
        try:
            order = self.tx_manager.submit_order(
                intent_authorized,
                client_order_id=client_order_id,
                wallet_authorization=wallet_auth,
            )
        except PostOnlyRejectError as exc:
            released = self._cleanup_failed_submission(
                wallet_lock_id=wallet_auth.lock_id,
                submission_lane=lane,
                cleanup_reason="post_only_reject",
                release_submission_reservation=True,
            )
            self.telemetry.incr("post_only_rejects")
            self.events.log_event(
                "post_only_reject",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_authorized.token_id,
                    "side": intent_authorized.side,
                    "price": intent_authorized.price,
                    "size": intent_authorized.size,
                    "detail": str(exc),
                    "submission_lane": lane,
                    "submission_reject_class": "transport_attempted_reject",
                    "submission_reserved_released": bool(released),
                },
            )
            return None, "post_only_reject"
        except ORDER_TRANSPORT_EXCEPTIONS as exc:
            released = self._cleanup_failed_submission(
                wallet_lock_id=wallet_auth.lock_id,
                submission_lane=lane,
                cleanup_reason="submit_exception",
                release_submission_reservation=True,
            )
            self.telemetry.incr("order_submit_failures")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "order_manager",
                    "action": "place_order",
                    "token_id": intent_authorized.token_id,
                    "side": intent_authorized.side,
                    "submission_lane": lane,
                    "submission_reject_class": "transport_attempted_reject",
                    "submission_reserved_released": bool(released),
                    "error": str(exc),
                }
            )
            return None, "order_submit_exception"

        status_norm = self._order_status_norm(order)
        remaining_size = self._extract_remaining_size(order)
        order_open = self._residual_exposure_exists(order)
        order_id = str(order.order_id or "").strip()
        if not order_id:
            released = self._cleanup_failed_submission(
                wallet_lock_id=wallet_auth.lock_id,
                submission_lane=lane,
                cleanup_reason="submit_no_ack_missing_order_id",
                release_submission_reservation=True,
            )
            self.telemetry.incr("order_submit_no_ack")
            self.risk.set_kill_switch("order_submit_no_ack_missing_order_id")
            self.events.log_event(
                "order_submit_no_ack",
                {
                    "ts_utc": utc_iso(),
                    "token_id": intent_authorized.token_id,
                    "side": intent_authorized.side,
                    "price": intent_authorized.price,
                    "size": intent_authorized.size,
                    "submission_lane": lane,
                    "status": status_norm,
                    "remaining_size": remaining_size,
                    "order_open": bool(order_open),
                    "submission_reserved_released": bool(released),
                    "lock_id": str(wallet_auth.lock_id or ""),
                },
            )
            return None, "order_submit_no_ack"

        self.risk.on_order_submitted()
        self.telemetry.incr("order_submission_accepted")
        self.telemetry.incr(f"order_submission_accepted_{lane}")
        if not self.wallet.confirm_submission(lock_id=wallet_auth.lock_id, order_id=order.order_id, order_open=order_open):
            self.telemetry.incr("wallet_halts")
            self.risk.set_kill_switch(f"wallet_halt:{self.wallet.halt_reason() or 'confirm_submission_failed'}")
            if order_open:
                try:
                    self.tx_manager.cancel_order(order.order_id)
                except ORDER_TRANSPORT_EXCEPTIONS as exc:
                    self.telemetry.incr("wallet_confirm_submission_cancel_failures")
                    self.events.log_error(
                        {
                            "ts_utc": utc_iso(),
                            "component": "order_manager",
                            "action": "wallet_confirm_submission_cancel",
                            "order_id": order.order_id,
                            "token_id": intent_authorized.token_id,
                            "side": intent_authorized.side,
                            "submission_lane": lane,
                            "error": str(exc),
                        }
                    )
            return None, "wallet_confirm_submission_failed"

        self.telemetry.incr("orders_submitted")
        competitiveness_payload = (
            dict(competitiveness_context) if isinstance(competitiveness_context, dict) else {}
        )
        event_lifecycle_phase, lifecycle_phase_source, lifecycle_phase_unknown_reason = _resolve_event_lifecycle_phase(
            intent_stage=intent_authorized.stage,
            risk_context=risk_context_payload,
            risk_basis=risk_basis_event,
        )
        event_financial_posture_class = str(
            risk_basis_event.get("financial_posture_class") or "UNKNOWN"
        ).strip().upper()
        event_sec_to_expiry = (
            float(risk_basis_event["sec_to_expiry"])
            if isinstance(risk_basis_event.get("sec_to_expiry"), (int, float))
            else None
        )
        size_resolution_payload = dict(size_resolution)
        if competitiveness_payload:
            if lane == "maker":
                size_resolution_payload["maker_competitiveness"] = competitiveness_payload
            elif lane == "taker":
                size_resolution_payload["taker_competitiveness"] = competitiveness_payload
        submit_ts = parse_ts(submit_ts_utc)
        decision_reference_ts = parse_ts(intent_authorized.decision_reference_ts_utc)
        decision_to_submit_latency_ms = None
        if submit_ts is not None and decision_reference_ts is not None:
            decision_to_submit_latency_ms = max(
                0.0, (submit_ts - decision_reference_ts).total_seconds() * 1000.0
            )

        self.events.log_event(
            "order_submit",
            {
                "ts_utc": submit_ts_utc,
                "ts_event_utc": submit_ts_utc,
                "ts_decision_utc": (
                    str(intent_authorized.decision_reference_ts_utc).strip()
                    if str(intent_authorized.decision_reference_ts_utc or "").strip()
                    else submit_ts_utc
                ),
                "token_id": intent_authorized.token_id,
                "side": intent_authorized.side,
                "price": intent_authorized.price,
                "size": intent_authorized.size,
                "order_id": order.order_id,
                "client_order_id": client_order_id,
                "submission_lane": lane,
                "submission_reject_class": None,
                "submission_state": "accepted",
                "risk_decision_basis": risk_basis_event,
                "tif": intent_authorized.tif,
                "post_only": intent_authorized.post_only,
                "reason": intent_authorized.reason,
                EDGE_LIFECYCLE_PHASE_FIELD: event_lifecycle_phase,
                "lifecycle_phase_source": lifecycle_phase_source,
                "lifecycle_phase_unknown_reason": lifecycle_phase_unknown_reason,
                "financial_posture_class": event_financial_posture_class,
                "sec_to_expiry": event_sec_to_expiry,
                "market_id": intent_authorized.market_id,
                "window_id": intent_authorized.window_id,
                "reason_code": intent_authorized.reason_code,
                "execution_preference": intent_authorized.execution_preference,
                "target_ref": (
                    str(intent_authorized.target_ref).strip()
                    if str(intent_authorized.target_ref or "").strip()
                    else None
                ),
                "decision_linkage_key": (
                    (
                        "target_ref:"
                        + str(intent_authorized.target_ref).strip()
                        + "|decision_ts:"
                        + str(intent_authorized.decision_reference_ts_utc).strip()
                    )
                    if str(intent_authorized.target_ref or "").strip()
                    and str(intent_authorized.decision_reference_ts_utc or "").strip()
                    else None
                ),
                "decision_reference_midpoint": (
                    float(intent_authorized.decision_reference_midpoint)
                    if isinstance(intent_authorized.decision_reference_midpoint, (int, float))
                    else None
                ),
                "decision_reference_source": (
                    str(intent_authorized.decision_reference_source).strip()
                    if str(intent_authorized.decision_reference_source or "").strip()
                    else None
                ),
                "decision_reference_lookup_key": (
                    str(intent_authorized.decision_reference_lookup_key).strip()
                    if str(intent_authorized.decision_reference_lookup_key or "").strip()
                    else None
                ),
                "decision_reference_ts_utc": (
                    str(intent_authorized.decision_reference_ts_utc).strip()
                    if str(intent_authorized.decision_reference_ts_utc or "").strip()
                    else None
                ),
                "decision_reference_recoverable": isinstance(
                    intent_authorized.decision_reference_midpoint, (int, float)
                ),
                "decision_to_submit_latency_ms": (
                    float(decision_to_submit_latency_ms)
                    if isinstance(decision_to_submit_latency_ms, (int, float))
                    else None
                ),
                "commitment_hold_active": bool(intent_authorized.commitment_hold_active),
                "commitment_hold_reason": (
                    str(intent_authorized.commitment_hold_reason).strip()
                    if str(intent_authorized.commitment_hold_reason or "").strip()
                    else None
                ),
                "commitment_expiry_ts_utc": (
                    str(intent_authorized.commitment_expiry_ts_utc).strip()
                    if str(intent_authorized.commitment_expiry_ts_utc or "").strip()
                    else None
                ),
                "sizing_mode": self.sizing_mode,
                "sizing_target_usd": float(notional_target_usd) if notional_target_usd is not None else None,
                "size_selection_authority": "order_manager_notional_sizing_v2",
                "size_resolution": size_resolution_payload,
                "maker_competitiveness": (
                    competitiveness_payload if lane == "maker" and competitiveness_payload else None
                ),
                "taker_competitiveness": (
                    competitiveness_payload if lane == "taker" and competitiveness_payload else None
                ),
                **quality_fields,
            },
        )
        return order, None

    def _round_shares(self, shares: float) -> float:
        step = self.sizing_share_step
        if self.sizing_rounding == "nearest":
            units = int(round(shares / step))
            return max(0.0, float(units) * step)
        units = int(math.floor(shares / step))
        return max(0.0, float(units) * step)

    def _sizing_price(self, top: BookTop, side: str) -> Optional[float]:
        if self.sizing_price_source == "best_bid":
            return top.best_bid_price
        if self.sizing_price_source == "best_ask":
            return top.best_ask_price
        # mid is the default; fall back to touch to avoid dropping orders in one-sided books.
        if top.midpoint is not None:
            return top.midpoint
        if side.upper() == "BUY":
            return top.best_ask_price or top.best_bid_price
        return top.best_bid_price or top.best_ask_price

    def _maker_geometry_floor_price(self) -> Optional[float]:
        if self.maker_competitive_min_notional_usd <= 0.0 or self.maker_competitive_max_shares <= 0.0:
            return None
        return float(self.maker_competitive_min_notional_usd / self.maker_competitive_max_shares)

    @staticmethod
    def _is_maker_lane(intent: OrderIntent) -> bool:
        tif = str(intent.tif or "GTC").upper()
        return not (bool(intent.post_only is False) or tif in {"IOC", "FOK"})

    @staticmethod
    def _hour_in_window(*, hour_utc: int, start_hour: int, end_hour: int) -> bool:
        start = max(0, min(23, int(start_hour)))
        end = max(0, min(23, int(end_hour)))
        if start == end:
            return True
        if start < end:
            return start <= hour_utc < end
        return hour_utc >= start or hour_utc < end

    def _maker_liquidity_tod_scale(self) -> Tuple[float, str]:
        if not self.maker_liquidity_tod_scaler_enabled:
            return 1.0, "disabled"
        now = self._now_fn()
        active = self._hour_in_window(
            hour_utc=int(now.hour),
            start_hour=self.maker_liquidity_tod_start_hour_utc,
            end_hour=self.maker_liquidity_tod_end_hour_utc,
        )
        if active:
            return max(0.0, float(self.maker_liquidity_tod_depth_multiplier)), "overnight_window_active"
        return 1.0, "outside_window"

    @staticmethod
    def _maker_visible_depth_shares(top: BookTop, side: str) -> float:
        normalized_side = str(side or "").strip().upper()
        if normalized_side == "BUY":
            return max(0.0, float(top.best_bid_size) if top.best_bid_size is not None else 0.0)
        return max(0.0, float(top.best_ask_size) if top.best_ask_size is not None else 0.0)

    def _resolve_order_size_shares_with_details(
        self,
        intent: OrderIntent,
        top: BookTop,
        *,
        notional_target_usd: Optional[float] = None,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        mode = str(self.sizing_mode).strip().lower()
        lane = "maker" if self._is_maker_lane(intent) else "taker"
        details: Dict[str, Any] = {
            "sizing_mode": mode,
            "submission_lane": lane,
            "price_source": str(self.sizing_price_source),
            "global_min_usd": float(self.sizing_min_usd),
            "global_max_usd": float(self.sizing_max_usd),
            "size_decision_reasons": [],
        }
        if mode != "notional":
            passthrough = float(intent.size)
            details["size_decision_reasons"] = ["shares_mode_passthrough"]
            details["resolved_shares"] = float(passthrough)
            details["resolved_notional_usd"] = None
            return passthrough, details

        price = self._sizing_price(top, intent.side)
        if price is None or price <= 0:
            details["size_decision_reasons"] = ["price_unavailable"]
            details["resolved_shares"] = None
            details["resolved_notional_usd"] = None
            return None, details
        details["price_used"] = float(price)

        if notional_target_usd is None:
            scale = max(0.01, float(intent.size) / self.base_order_size)
            desired_usd = self.sizing_target_usd * scale
            details["base_target_usd"] = float(self.sizing_target_usd)
            details["intent_size_scale"] = float(scale)
        else:
            desired_usd = float(notional_target_usd)
            details["override_target_usd"] = float(notional_target_usd)
        details["desired_usd_initial"] = float(desired_usd)

        desired_usd = max(self.sizing_min_usd, min(self.sizing_max_usd, desired_usd))
        details["desired_usd_after_global_bounds"] = float(desired_usd)

        if lane == "maker":
            visible_depth_shares = self._maker_visible_depth_shares(top, intent.side)
            tod_depth_multiplier, tod_mode = self._maker_liquidity_tod_scale()
            effective_depth_shares = visible_depth_shares * tod_depth_multiplier
            details["maker_hard_floor_active"] = bool(
                self.maker_competitive_min_notional_usd > 0.0
                or self.maker_competitive_min_shares > 0.0
            )
            details["maker_hard_notional_range_usd"] = {
                "min": float(self.maker_competitive_min_notional_usd),
                "max": float(self.maker_competitive_max_notional_usd),
            }
            details["maker_hard_share_range"] = {
                "min": float(self.maker_competitive_min_shares),
                "max": float(self.maker_competitive_max_shares),
            }
            details["visible_depth_shares"] = float(visible_depth_shares)
            details["maker_liquidity_tod_depth_multiplier"] = float(tod_depth_multiplier)
            details["maker_liquidity_tod_mode"] = tod_mode
            details["effective_depth_shares"] = float(effective_depth_shares)

            depth_target_ratio = max(
                self.maker_depth_target_min_ratio,
                float(self.maker_depth_target_ratio),
            )
            if self.maker_depth_target_max_ratio > 0.0:
                depth_target_ratio = min(depth_target_ratio, self.maker_depth_target_max_ratio)
            details["maker_depth_target_ratio_applied"] = float(depth_target_ratio)
            details["maker_depth_target_ratio_window"] = {
                "min": float(self.maker_depth_target_min_ratio),
                "target": float(self.maker_depth_target_ratio),
                "max": float(self.maker_depth_target_max_ratio),
            }
            details["maker_depth_scaling_active"] = bool(
                effective_depth_shares > 0.0 and depth_target_ratio > 0.0
            )
            if effective_depth_shares > 0.0 and depth_target_ratio > 0.0:
                depth_target_shares = effective_depth_shares * depth_target_ratio
                depth_target_usd = depth_target_shares * float(price)
                details["depth_target_shares"] = float(depth_target_shares)
                details["depth_target_notional_usd"] = float(depth_target_usd)
                if depth_target_usd > desired_usd:
                    desired_usd = float(depth_target_usd)
                    details["size_decision_reasons"].append("maker_depth_target_notional_floor")
            else:
                details["depth_target_shares"] = 0.0
                details["depth_target_notional_usd"] = 0.0

            if self.maker_competitive_min_notional_usd > 0.0:
                if desired_usd < self.maker_competitive_min_notional_usd:
                    desired_usd = float(self.maker_competitive_min_notional_usd)
                    details["size_decision_reasons"].append("maker_hard_min_notional_floor")
                details["maker_hard_min_notional_usd"] = float(self.maker_competitive_min_notional_usd)
            if self.maker_competitive_max_notional_usd > 0.0:
                if desired_usd > self.maker_competitive_max_notional_usd:
                    desired_usd = float(self.maker_competitive_max_notional_usd)
                    details["size_decision_reasons"].append("maker_hard_max_notional_cap")
                details["maker_hard_max_notional_usd"] = float(self.maker_competitive_max_notional_usd)

        details["desired_usd_after_lane_overlays"] = float(desired_usd)
        raw_shares = desired_usd / float(price)
        details["raw_shares"] = float(raw_shares)
        if lane == "maker":
            if self.maker_competitive_min_shares > 0.0 and raw_shares < self.maker_competitive_min_shares:
                raw_shares = float(self.maker_competitive_min_shares)
                details["size_decision_reasons"].append("maker_hard_min_shares_floor")
            if self.maker_competitive_max_shares > 0.0 and raw_shares > self.maker_competitive_max_shares:
                raw_shares = float(self.maker_competitive_max_shares)
                details["size_decision_reasons"].append("maker_hard_max_shares_cap")
            if self.maker_competitive_min_shares > 0.0:
                details["maker_hard_min_shares"] = float(self.maker_competitive_min_shares)
            if self.maker_competitive_max_shares > 0.0:
                details["maker_hard_max_shares"] = float(self.maker_competitive_max_shares)

        rounded = self._round_shares(raw_shares)
        rounded = min(rounded, self.strategy_max_order_size)
        details["rounded_shares"] = float(rounded)
        if rounded <= 0.0:
            details["size_decision_reasons"].append("rounded_shares_nonpositive")
            details["resolved_shares"] = None
            details["resolved_notional_usd"] = None
            return None, details

        rounded_notional = rounded * float(price)
        details["rounded_notional_usd"] = float(rounded_notional)
        if rounded_notional < self.sizing_min_usd or rounded_notional > self.sizing_max_usd:
            details["size_decision_reasons"].append("global_notional_bounds_after_rounding")
            details["resolved_shares"] = None
            details["resolved_notional_usd"] = None
            return None, details
        if lane == "maker":
            if (
                self.maker_competitive_min_notional_usd > 0.0
                and rounded_notional + 1e-9 < self.maker_competitive_min_notional_usd
            ):
                # Floor-rounding can undershoot a hard maker notional floor by a small
                # step fraction. Try one deterministic step-up before rejecting.
                share_step = max(1e-9, float(self.sizing_share_step))
                min_notional_exact_shares = float(self.maker_competitive_min_notional_usd) / float(price)
                min_required_units = int(math.ceil(max(0.0, min_notional_exact_shares - 1e-12) / share_step))
                rounded_up = float(min_required_units) * share_step
                rounded_up = min(float(rounded_up), float(self.strategy_max_order_size))
                rounded_up_notional = float(rounded_up) * float(price)
                can_step_up = bool(
                    rounded_up > float(rounded) + 1e-12
                    and rounded_up_notional + 1e-9 >= float(self.maker_competitive_min_notional_usd)
                    and rounded_up_notional <= float(self.sizing_max_usd) + 1e-9
                    and (
                        self.maker_competitive_max_shares <= 0.0
                        or rounded_up <= float(self.maker_competitive_max_shares) + 1e-9
                    )
                    and (
                        self.maker_competitive_max_notional_usd <= 0.0
                        or rounded_up_notional <= float(self.maker_competitive_max_notional_usd) + 1e-9
                    )
                )
                if can_step_up:
                    rounded = float(rounded_up)
                    rounded_notional = float(rounded_up_notional)
                    details["rounded_shares"] = float(rounded)
                    details["rounded_notional_usd"] = float(rounded_notional)
                    details["size_decision_reasons"].append("maker_hard_min_notional_roundup_to_step")
                else:
                    details["size_decision_reasons"].append("maker_hard_min_notional_failed_after_rounding")
                    details["resolved_shares"] = None
                    details["resolved_notional_usd"] = None
                    return None, details
            if (
                self.maker_competitive_max_notional_usd > 0.0
                and rounded_notional - 1e-9 > self.maker_competitive_max_notional_usd
            ):
                details["size_decision_reasons"].append("maker_hard_max_notional_failed_after_rounding")
                details["resolved_shares"] = None
                details["resolved_notional_usd"] = None
                return None, details

        if not details["size_decision_reasons"]:
            details["size_decision_reasons"] = ["baseline_notional_sizing"]
        details["resolved_shares"] = float(rounded)
        details["resolved_notional_usd"] = float(rounded_notional)
        return rounded, details

    def _resolve_order_size_shares(
        self,
        intent: OrderIntent,
        top: BookTop,
        *,
        notional_target_usd: Optional[float] = None,
    ) -> Optional[float]:
        resolved, _details = self._resolve_order_size_shares_with_details(
            intent,
            top,
            notional_target_usd=notional_target_usd,
        )
        return resolved

    def _remember_trade_id(self, trade_id: str) -> bool:
        if trade_id in self.seen_trade_ids:
            return False
        self.seen_trade_ids.add(trade_id)
        self._seen_trade_ids_queue.append(trade_id)
        while len(self._seen_trade_ids_queue) > self.seen_trade_ids_max:
            old = self._seen_trade_ids_queue.popleft()
            self.seen_trade_ids.discard(old)
        return True

    def _handle_fill(self, fill: FillEvent) -> bool:
        source = str(fill.source or "").strip().lower()
        if source == "paper" and not PAPER_TRADE_ID_RE.match(str(fill.trade_id or "")):
            self.telemetry.incr("artifact_integrity_failures")
            self.events.log_error(
                {
                    "ts_utc": utc_iso(),
                    "component": "order_manager",
                    "action": "fill_integrity_check",
                    "reason": "invalid_paper_trade_id",
                    "trade_id": str(fill.trade_id or ""),
                }
            )
            self.risk.set_kill_switch(f"invalid_paper_trade_id:{fill.trade_id}")
            return False
        if not self._remember_trade_id(fill.trade_id):
            return False
        fill_ts = parse_ts(fill.ts_utc)
        if fill_ts is not None:
            last_ts = parse_ts(self.last_fill_ts_utc)
            if last_ts is None or fill_ts >= last_ts:
                self.last_fill_ts_utc = utc_iso(fill_ts)
        self.wallet.on_fill(fill)
        if self.wallet.is_halted():
            self.telemetry.incr("wallet_halts")
            self.risk.set_kill_switch(f"wallet_halt:{self.wallet.halt_reason()}")
            return False
        self.risk.on_fill(fill)
        self.telemetry.incr("fills")
        self.events.log_event(
            "fill",
            {
                "ts_utc": fill.ts_utc,
                "trade_id": fill.trade_id,
                "order_id": fill.order_id,
                "token_id": fill.token_id,
                "side": fill.side,
                "price": fill.price,
                "size": fill.size,
                "source": fill.source,
                "fill_policy_basis": fill.fill_policy_basis,
                "execution_realism_class": fill.execution_realism_class,
                "decision_input_type": fill.decision_input_type,
                "target_ref": (
                    str(fill.target_ref).strip() if str(fill.target_ref or "").strip() else None
                ),
                "paper_liquidity_depth_multiplier": (
                    float(fill.paper_liquidity_depth_multiplier)
                    if isinstance(fill.paper_liquidity_depth_multiplier, (int, float))
                    else None
                ),
                "paper_queue_position_mode": (
                    str(fill.paper_queue_position_mode).strip()
                    if str(fill.paper_queue_position_mode or "").strip()
                    else None
                ),
                "paper_queue_fill_multiplier": (
                    float(fill.paper_queue_fill_multiplier)
                    if isinstance(fill.paper_queue_fill_multiplier, (int, float))
                    else None
                ),
                "paper_maker_depth_consumption_ratio": (
                    float(fill.paper_maker_depth_consumption_ratio)
                    if isinstance(fill.paper_maker_depth_consumption_ratio, (int, float))
                    else None
                ),
                "paper_maker_eligible_depth": (
                    float(fill.paper_maker_eligible_depth)
                    if isinstance(fill.paper_maker_eligible_depth, (int, float))
                    else None
                ),
                "paper_chainlink_lag_class": (
                    str(fill.paper_chainlink_lag_class).strip()
                    if str(fill.paper_chainlink_lag_class or "").strip()
                    else None
                ),
                "paper_chainlink_lag_sec_effective": (
                    float(fill.paper_chainlink_lag_sec_effective)
                    if isinstance(fill.paper_chainlink_lag_sec_effective, (int, float))
                    else None
                ),
                "paper_chainlink_lag_penalty_bps": (
                    float(fill.paper_chainlink_lag_penalty_bps)
                    if isinstance(fill.paper_chainlink_lag_penalty_bps, (int, float))
                    else None
                ),
            },
        )
        return True

    @staticmethod
    def _open_order_ids(open_orders: List[LiveOrder]) -> set[str]:
        return {str(order.order_id or "").strip() for order in open_orders if str(order.order_id or "").strip()}

    def _release_closed_order_locks_for_fills(self, fills: List[FillEvent]) -> None:
        filled_order_ids = {str(fill.order_id or "").strip() for fill in fills if str(fill.order_id or "").strip()}
        if not filled_order_ids:
            return
        open_order_ids = self._open_order_ids(self.tx_manager.get_open_orders())
        closed_filled_order_ids = sorted(filled_order_ids - open_order_ids)
        for order_id in closed_filled_order_ids:
            self.wallet.release_order_lock(order_id)
            self.telemetry.incr("wallet_order_lock_released_closed_after_fill")
            self.events.log_event(
                "wallet_reservation_cleanup",
                {
                    "ts_utc": utc_iso(),
                    "cleanup_reason": "filled_order_no_longer_open",
                    "order_id": order_id,
                    "release_kind": "order",
                },
            )

    def process_fills(self) -> int:
        fills = self.tx_manager.poll_fills()
        accepted = 0
        for fill in fills:
            if self._handle_fill(fill):
                accepted += 1
        if fills:
            self._release_closed_order_locks_for_fills(fills)
        return accepted

    def position_snapshot(self) -> Dict[str, Position]:
        return self.risk.positions

    def restore_seen_trade_ids(self, trade_ids: List[str]) -> None:
        self.seen_trade_ids = set()
        self._seen_trade_ids_queue = deque()
        for trade_id in trade_ids:
            self._remember_trade_id(str(trade_id))

    def restore_last_fill_ts(self, value: Optional[str]) -> None:
        parsed = parse_ts(value)
        self.last_fill_ts_utc = utc_iso(parsed) if parsed is not None else None

    def snapshot_seen_trade_ids(self, limit: Optional[int] = None) -> List[str]:
        items = list(self._seen_trade_ids_queue)
        if limit is None:
            return items
        max_items = max(0, int(limit))
        if max_items == 0:
            return []
        if len(items) <= max_items:
            return items
        return items[-max_items:]

    def snapshot_last_fill_ts(self) -> Optional[str]:
        return self.last_fill_ts_utc

    def set_soft_rate_limits(self, order_pct: float, cancel_pct: float) -> None:
        self.order_rate_soft_limit_pct = _normalize_soft_limit_pct(order_pct, self.order_rate_soft_limit_pct)
        self.cancel_rate_soft_limit_pct = _normalize_soft_limit_pct(cancel_pct, self.cancel_rate_soft_limit_pct)

    def place_taker_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: Optional[float],
        target_usd: Optional[float],
        top: BookTop,
        reason: str,
        target_ref: Optional[str] = None,
        decision_reference_midpoint: Optional[float] = None,
        decision_reference_source: Optional[str] = None,
        decision_reference_lookup_key: Optional[str] = None,
        decision_reference_ts_utc: Optional[str] = None,
        token_median_lag_ms: Optional[float] = None,
        oracle_tick_age_sec: Optional[float] = None,
        realized_volatility: Optional[float] = None,
        stage: Optional[str] = None,
        competitiveness_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        outcome = self.place_taker_order_with_outcome(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            target_usd=target_usd,
            top=top,
            reason=reason,
            target_ref=target_ref,
            decision_reference_midpoint=decision_reference_midpoint,
            decision_reference_source=decision_reference_source,
            decision_reference_lookup_key=decision_reference_lookup_key,
            decision_reference_ts_utc=decision_reference_ts_utc,
            token_median_lag_ms=token_median_lag_ms,
            oracle_tick_age_sec=oracle_tick_age_sec,
            realized_volatility=realized_volatility,
            stage=stage,
            competitiveness_context=competitiveness_context,
        )
        return bool(outcome.get("submitted", False))

    def place_taker_order_with_outcome(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: Optional[float],
        target_usd: Optional[float],
        top: BookTop,
        reason: str,
        target_ref: Optional[str] = None,
        decision_reference_midpoint: Optional[float] = None,
        decision_reference_source: Optional[str] = None,
        decision_reference_lookup_key: Optional[str] = None,
        decision_reference_ts_utc: Optional[str] = None,
        token_median_lag_ms: Optional[float] = None,
        oracle_tick_age_sec: Optional[float] = None,
        realized_volatility: Optional[float] = None,
        stage: Optional[str] = None,
        competitiveness_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_size = float(size) if size is not None else float(self.base_order_size)
        competitiveness_stage = None
        if isinstance(competitiveness_context, dict):
            compat_stage = str(_compat_lineage_stage_from_payload(competitiveness_context) or "").strip()
            competitiveness_stage = compat_stage.upper() if compat_stage else None
        explicit_stage = str(stage or "").strip()
        resolved_stage = explicit_stage.upper() if explicit_stage else competitiveness_stage
        intent = OrderIntent(
            token_id=token_id,
            side=side,
            price=price,
            size=base_size,
            tif="IOC",
            post_only=False,
            reason=reason,
            stage=resolved_stage,
            target_ref=(str(target_ref).strip() if str(target_ref or "").strip() else None),
            decision_reference_midpoint=(
                float(decision_reference_midpoint)
                if isinstance(decision_reference_midpoint, (int, float))
                else (float(top.midpoint) if isinstance(top.midpoint, (int, float)) else None)
            ),
            decision_reference_source=(
                str(decision_reference_source).strip()
                if str(decision_reference_source or "").strip()
                else "taker_decision_top_midpoint"
            ),
            decision_reference_lookup_key=(
                str(decision_reference_lookup_key).strip()
                if str(decision_reference_lookup_key or "").strip()
                else None
            ),
            decision_reference_ts_utc=(
                str(decision_reference_ts_utc).strip()
                if str(decision_reference_ts_utc or "").strip()
                else utc_iso()
            ),
            token_median_lag_ms=(
                float(token_median_lag_ms)
                if isinstance(token_median_lag_ms, (int, float))
                else None
            ),
            oracle_tick_age_sec=(
                float(oracle_tick_age_sec)
                if isinstance(oracle_tick_age_sec, (int, float))
                else None
            ),
        )
        open_orders = self.tx_manager.get_open_orders()
        token_orders = [o for o in open_orders if o.token_id == token_id]
        risk_context_payload = dict(competitiveness_context) if isinstance(competitiveness_context, dict) else {}
        risk_context_payload.setdefault("submission_lane", "taker")
        risk_context_payload.setdefault(
            EDGE_LIFECYCLE_PHASE_FIELD,
            _canonical_lifecycle_phase_from_payload(risk_context_payload)
            or _canonical_lifecycle_phase_from_stage(resolved_stage)
            or "scan",
        )
        risk_context_payload.setdefault(
            "lineage_stage",
            str(
                risk_context_payload.get("lineage_stage")
                or lineage_stage_from_payload(risk_context_payload)
                or resolved_stage
                or "UNKNOWN"
            ).strip().upper()
            or "UNKNOWN",
        )
        risk_context_payload.setdefault("stage", str(intent.stage or "").strip().upper() or "UNKNOWN")
        risk_context_payload.setdefault("financial_posture_class", "UNKNOWN")
        if isinstance(realized_volatility, (int, float)):
            risk_context_payload["realized_volatility"] = float(realized_volatility)
        placed, _submit_reject_reason = self._place_order(
            intent,
            top,
            open_orders_for_token=token_orders,
            open_orders_total=len(open_orders),
            open_orders_all=open_orders,
            reference_mid_by_token={
                token_id: (float(top.midpoint) if isinstance(top.midpoint, (int, float)) else None)
            },
            risk_context=risk_context_payload,
            notional_target_usd=target_usd,
            competitiveness_context=competitiveness_context,
        )
        if placed is None:
            return {
                "submitted": False,
                "fills_accepted": 0,
                "order_id": None,
                "submit_reject_reason": (str(_submit_reject_reason).strip() if str(_submit_reject_reason or "").strip() else None),
            }
        self.telemetry.incr("taker_orders_submitted")
        # Pull immediate IOC fills into state now rather than waiting for next cycle.
        accepted = self.process_fills()
        if accepted > 0:
            self.telemetry.incr("taker_orders_filled", accepted)
        return {
            "submitted": True,
            "fills_accepted": int(max(0, accepted)),
            "order_id": str(placed.order_id or ""),
        }

    def preview_taker_dynamic_feasible_target(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        target_usd_cap: Optional[float],
        top: BookTop,
        reason: str,
        stage: Optional[str] = None,
        realized_volatility: Optional[float] = None,
        competitiveness_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Read-only advisory preview for dynamic taker feasibility.

        This helper is intentionally non-authoritative:
        - no submission reservation
        - no order placement
        - no exposure mutation
        Final authority remains RiskEngine.validate_order on real submit.
        """
        cap = float(target_usd_cap) if isinstance(target_usd_cap, (int, float)) else 0.0
        if cap <= 0.0:
            return {
                "predicted_dynamic_feasible": False,
                "predicted_feasible_target_usd": 0.0,
                "predicted_reject_reason": "target_cap_unavailable",
                "preview_authority": "advisory_read_only",
            }

        explicit_stage = str(stage or "").strip()
        competitiveness_stage = None
        if isinstance(competitiveness_context, dict):
            compat_stage = str(_compat_lineage_stage_from_payload(competitiveness_context) or "").strip()
            competitiveness_stage = compat_stage.upper() if compat_stage else None
        resolved_stage = explicit_stage.upper() if explicit_stage else competitiveness_stage
        base_intent = OrderIntent(
            token_id=token_id,
            side=side,
            price=float(price),
            size=float(self.base_order_size),
            tif="IOC",
            post_only=False,
            reason=reason,
            stage=resolved_stage,
        )
        open_orders = self.tx_manager.get_open_orders()
        token_orders = [o for o in open_orders if o.token_id == token_id]
        risk_context_payload = dict(competitiveness_context) if isinstance(competitiveness_context, dict) else {}
        risk_context_payload.setdefault("submission_lane", "taker")
        risk_context_payload.setdefault(
            EDGE_LIFECYCLE_PHASE_FIELD,
            _canonical_lifecycle_phase_from_payload(risk_context_payload)
            or _canonical_lifecycle_phase_from_stage(resolved_stage)
            or "scan",
        )
        risk_context_payload.setdefault(
            "lineage_stage",
            str(
                risk_context_payload.get("lineage_stage")
                or lineage_stage_from_payload(risk_context_payload)
                or resolved_stage
                or "UNKNOWN"
            ).strip().upper()
            or "UNKNOWN",
        )
        risk_context_payload.setdefault("stage", str(base_intent.stage or "").strip().upper() or "UNKNOWN")
        risk_context_payload.setdefault("financial_posture_class", "UNKNOWN")
        if isinstance(realized_volatility, (int, float)):
            risk_context_payload["realized_volatility"] = float(realized_volatility)
        reference_mid = float(top.midpoint) if isinstance(top.midpoint, (int, float)) else None
        reference_mid_by_token = {token_id: reference_mid}

        def _probe_allowed(target_usd: float) -> Tuple[bool, str]:
            if target_usd <= 0.0:
                return False, "target_nonpositive"
            sized, _details = self._resolve_order_size_shares_with_details(
                base_intent,
                top,
                notional_target_usd=float(target_usd),
            )
            if sized is None or float(sized) <= 0.0:
                return False, "size_notional_bounds"
            probe_intent = OrderIntent(
                token_id=base_intent.token_id,
                side=base_intent.side,
                price=base_intent.price,
                size=float(sized),
                tif=base_intent.tif,
                post_only=base_intent.post_only,
                reason=base_intent.reason,
                stage=base_intent.stage,
            )
            decision = self.risk.preview_order_feasibility(
                probe_intent,
                top,
                open_orders_for_token=token_orders,
                open_orders_total=len(open_orders),
                open_orders_all=open_orders,
                reference_mid_by_token=reference_mid_by_token,
                risk_context=risk_context_payload,
            )
            return bool(decision.allowed), str(decision.reason or "unknown")

        allowed_at_cap, reject_reason_at_cap = _probe_allowed(cap)
        if allowed_at_cap:
            return {
                "predicted_dynamic_feasible": True,
                "predicted_feasible_target_usd": float(cap),
                "predicted_reject_reason": None,
                "preview_authority": "advisory_read_only",
            }

        lower = 0.0
        upper = float(cap)
        best = 0.0
        best_fail_reason = str(reject_reason_at_cap or "unknown")
        for _ in range(14):
            mid = (lower + upper) / 2.0
            if mid <= 0.0:
                break
            allowed, reject_reason = _probe_allowed(mid)
            if allowed:
                best = mid
                lower = mid
            else:
                upper = mid
                if reject_reason:
                    best_fail_reason = str(reject_reason)

        return {
            "predicted_dynamic_feasible": bool(best > 0.0),
            "predicted_feasible_target_usd": float(max(0.0, best)),
            "predicted_reject_reason": (best_fail_reason if best <= 0.0 else None),
            "preview_authority": "advisory_read_only",
        }

    def cancel_non_target_orders(self, tracked_tokens: Set[str], action_budget: Optional[int] = None) -> int:
        if not self.cancel_orphan_orders:
            return 0
        if action_budget is None:
            action_budget = self.max_actions_per_cycle
        if action_budget <= 0:
            return 0
        actions = 0
        for order in self.tx_manager.get_open_orders():
            if order.token_id in tracked_tokens:
                continue
            if actions >= action_budget:
                break
            if self._cancel_order(order, "orphan_token_order", request_origin="non_target_cleanup"):
                actions += 1
        return actions

    def cancel_stale_orders(self, action_budget: Optional[int] = None) -> int:
        if action_budget is None:
            action_budget = self.max_actions_per_cycle
        if action_budget <= 0:
            return 0
        actions = 0
        for order in self.tx_manager.get_open_orders():
            if actions >= action_budget:
                break
            age_sec = self._order_age_sec(order)
            if age_sec is None or age_sec <= self.max_quote_age_sec:
                continue
            if self._cancel_order(order, "stale_quote_watchdog", request_origin="stale_watchdog"):
                actions += 1
        return actions

    def cancel_orders_for_tokens(
        self,
        token_ids: Set[str],
        *,
        reason: str,
        request_origin: Optional[str] = None,
        action_budget: Optional[int] = None,
    ) -> int:
        tracked = {str(token_id or "").strip() for token_id in set(token_ids or set()) if str(token_id or "").strip()}
        if not tracked:
            return 0
        if action_budget is None:
            action_budget = self.max_actions_per_cycle
        if action_budget <= 0:
            return 0
        actions = 0
        for order in self.tx_manager.get_open_orders():
            if order.token_id not in tracked:
                continue
            if actions >= action_budget:
                break
            if self._cancel_order(order, reason, request_origin=request_origin):
                actions += 1
        return actions

    def step(
        self,
        books: Dict[str, BookTop],
        tracked_tokens: Optional[Set[str]] = None,
        tracked_token_cancel_reason_by_token: Optional[Dict[str, str]] = None,
        fair_probability_by_token: Optional[Dict[str, float]] = None,
        realized_volatility_by_token: Optional[Dict[str, float]] = None,
        size_multiplier_by_token: Optional[Dict[str, float]] = None,
        spread_multiplier_by_token: Optional[Dict[str, float]] = None,
        requote_delta_by_token: Optional[Dict[str, float]] = None,
        side_policy_by_token: Optional[Dict[str, str]] = None,
        competitiveness_context_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
        max_actions_override: Optional[int] = None,
        cycle_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        max_actions = self.max_actions_per_cycle if max_actions_override is None else max(1, int(max_actions_override))
        actions = 0
        fills = self.process_fills()
        maker_submitted_token_ids: set[str] = set()
        maker_submitted_order_ids_by_token: Dict[str, List[str]] = {}
        maker_no_submission_reason_by_token: Dict[str, str] = {}
        maker_no_submission_category_by_token: Dict[str, str] = {}
        reason_priority = {
            "action_budget_exhausted": 0,
            "replace_cancel_unavailable": 1,
            "submit_rejected": 2,
            "post_only_reject": 2,
            "pre_submit_cross_guarded": 2,
            "order_soft_throttle": 2,
            "quote_quality_skip_fill_probability": 2,
            "quote_quality_skip_queue_depth": 2,
            "risk_reject": 2,
            "open_order_cleanup_required": 2,
            "settlement_hold_required": 2,
            "replace_guard_min_rest": 3,
            "maker_commitment_hold_active": 3,
            "no_desired_quote": 4,
            "quote_unchanged": 5,
            "unspecified": 99,
        }

        def _reason_rank(value: str) -> int:
            normalized = str(value or "").strip().lower()
            if normalized.startswith("submit_rejected"):
                return 2
            return int(reason_priority.get(normalized, 1000))

        def _record_maker_no_submission_reason(token_id: str, reason: str) -> None:
            normalized_token_id = str(token_id or "").strip()
            normalized_reason = str(reason or "").strip().lower()
            if not normalized_token_id or not normalized_reason:
                return
            if normalized_token_id in maker_submitted_token_ids:
                return
            current = maker_no_submission_reason_by_token.get(normalized_token_id, "")
            if not current:
                maker_no_submission_reason_by_token[normalized_token_id] = normalized_reason
                maker_no_submission_category_by_token[normalized_token_id] = self._normalize_maker_no_submission_category(
                    normalized_reason
                )
                return
            if _reason_rank(normalized_reason) < _reason_rank(current):
                maker_no_submission_reason_by_token[normalized_token_id] = normalized_reason
                maker_no_submission_category_by_token[normalized_token_id] = self._normalize_maker_no_submission_category(
                    normalized_reason
                )

        rl = self.risk.rate_limit_snapshot()
        self.telemetry.set_gauge("orders_used_60s", float(rl["orders_used"]))
        self.telemetry.set_gauge("orders_limit_60s", float(rl["orders_limit"]))
        self.telemetry.set_gauge("orders_reserved_outstanding_60s", float(rl.get("orders_reserved_outstanding", 0.0)))
        self.telemetry.set_gauge(
            "orders_transport_attempted_60s",
            float(rl.get("orders_transport_attempted_recent", 0.0)),
        )
        self.telemetry.set_gauge("cancels_used_60s", float(rl["cancels_used"]))
        self.telemetry.set_gauge("cancels_limit_60s", float(rl["cancels_limit"]))
        self.telemetry.set_gauge(
            "orders_soft_remaining_60s",
            float(self.risk.remaining_order_capacity(self.order_rate_soft_limit_pct)),
        )
        self.telemetry.set_gauge(
            "cancels_soft_remaining_60s",
            float(self.risk.remaining_cancel_capacity(self.cancel_rate_soft_limit_pct)),
        )
        open_orders = self.tx_manager.get_open_orders()
        open_orders, expired_commitment_cancel_actions = self._cleanup_expired_commitment_orders(
            open_orders,
            max_actions=max(0, max_actions - actions),
        )
        actions += expired_commitment_cancel_actions
        tracked_cancel_reason_by_token: Dict[str, str] = {}
        if isinstance(tracked_token_cancel_reason_by_token, dict):
            for token_id, reason in tracked_token_cancel_reason_by_token.items():
                normalized_token_id = str(token_id or "").strip()
                normalized_reason = str(reason or "").strip().lower()
                if not normalized_token_id or not normalized_reason:
                    continue
                tracked_cancel_reason_by_token[normalized_token_id] = normalized_reason
        if tracked_cancel_reason_by_token:
            keep: List[LiveOrder] = []
            canceled_actions = 0
            cancel_budget_remaining = max(0, max_actions - actions)
            for order in open_orders:
                cancel_reason = tracked_cancel_reason_by_token.get(str(order.token_id or "").strip())
                if not cancel_reason or canceled_actions >= cancel_budget_remaining:
                    keep.append(order)
                    continue
                result = self._request_cancel_order(
                    order,
                    cancel_reason,
                    request_origin="tracked_token_cleanup",
                )
                if bool(result.get("executed", False)):
                    _record_maker_no_submission_reason(order.token_id, cancel_reason)
                    canceled_actions += 1
                else:
                    keep.append(order)
            open_orders = keep
            actions += canceled_actions
        if self.cancel_orphan_orders:
            tracked = tracked_tokens if tracked_tokens is not None else set(books.keys())
            tracked = set(tracked) | set(tracked_cancel_reason_by_token.keys())
            open_orders, canceled_actions = self._cancel_orphan_orders(
                open_orders,
                tracked,
                max_actions=max(0, max_actions - actions),
            )
            actions += canceled_actions
        open_orders_total = len(open_orders)
        reference_mid_by_token: Dict[str, Optional[float]] = {
            str(token_id): (
                float(top.midpoint)
                if top is not None and isinstance(getattr(top, "midpoint", None), (int, float))
                else None
            )
            for token_id, top in books.items()
        }
        by_token: Dict[str, List[LiveOrder]] = {}
        for order in open_orders:
            by_token.setdefault(order.token_id, []).append(order)

        for token_id, top in books.items():
            token_requote_delta = None
            if isinstance(requote_delta_by_token, dict):
                token_requote_delta_raw = requote_delta_by_token.get(token_id)
                if isinstance(token_requote_delta_raw, (int, float)):
                    token_requote_delta = max(1e-9, float(token_requote_delta_raw))
            token_side_policy = "TWO_SIDED"
            if isinstance(side_policy_by_token, dict):
                token_side_policy = str(side_policy_by_token.get(token_id, "TWO_SIDED") or "TWO_SIDED").strip().upper()
            if token_side_policy not in {"TWO_SIDED", "BUY_ONLY", "SELL_ONLY"}:
                token_side_policy = "TWO_SIDED"
            token_competitiveness_context = (
                competitiveness_context_by_token.get(token_id)
                if isinstance(competitiveness_context_by_token, dict)
                else None
            )
            if actions >= max_actions:
                _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                continue
            token_orders = by_token.get(token_id, [])
            committed_token_orders = [order for order in token_orders if self._active_committed_maker_order(order)]
            committed_token_orders.sort(key=lambda order: order.created_ts_utc or "")
            if committed_token_orders:
                _record_maker_no_submission_reason(token_id, "maker_commitment_hold_active")
                primary_committed_order = committed_token_orders[0]
                non_committed_extras = [
                    order
                    for order in token_orders
                    if order is not primary_committed_order and not self._active_committed_maker_order(order)
                ]
                for order in non_committed_extras:
                    if actions >= max_actions:
                        _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                        break
                    result = self._request_cancel_order(
                        order,
                        "maker_commitment_hold_active",
                        request_origin="maker_commitment_token_lock",
                    )
                    if bool(result.get("executed", False)):
                        actions += 1
                        open_orders_total = max(0, open_orders_total - 1)
                        self._remove_token_order_if_present(
                            token_orders,
                            order,
                            remove_reason="maker_commitment_hold_active",
                        )
                continue
            position = self.risk.positions.setdefault(token_id, Position(token_id=token_id))
            fair_prob = None if fair_probability_by_token is None else fair_probability_by_token.get(token_id)
            realized_vol = None if realized_volatility_by_token is None else realized_volatility_by_token.get(token_id)
            size_mult = 1.0 if size_multiplier_by_token is None else float(size_multiplier_by_token.get(token_id, 1.0))
            spread_mult = 1.0 if spread_multiplier_by_token is None else float(
                spread_multiplier_by_token.get(token_id, 1.0)
            )
            intents = self.strategy.make_quotes(
                token_id,
                top,
                position,
                fair_probability=fair_prob,
                realized_volatility=realized_vol,
                size_multiplier=size_mult,
                spread_multiplier=spread_mult,
            )
            desired = {intent.side: intent for intent in intents}

            for side in ("BUY", "SELL"):
                if actions >= max_actions:
                    _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                    break
                side_orders = [o for o in token_orders if o.side == side]
                side_orders.sort(key=lambda x: x.created_ts_utc or "")
                committed_side_orders = [o for o in side_orders if self._active_committed_maker_order(o)]
                primary: Optional[LiveOrder] = committed_side_orders[0] if committed_side_orders else (
                    side_orders[0] if side_orders else None
                )
                extras = [o for o in side_orders if o is not primary]
                if self._active_committed_maker_order(primary):
                    for order in extras:
                        if actions >= max_actions:
                            break
                        result = self._request_cancel_order(
                            order,
                            "extra_same_side_order",
                            request_origin="maker_extra_same_side_cleanup",
                        )
                        if bool(result.get("executed", False)):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="extra_same_side_order",
                            )
                    continue
                desired_intent = desired.get(side)

                if desired_intent is None:
                    _record_maker_no_submission_reason(token_id, "no_desired_quote")
                    for order in side_orders:
                        if actions >= max_actions:
                            _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                            break
                        if self._cancel_order(
                            order,
                            "no_desired_quote",
                            request_origin="maker_no_desired_quote",
                        ):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="no_desired_quote",
                            )
                    continue

                side_allowed = (
                    token_side_policy == "TWO_SIDED"
                    or (token_side_policy == "BUY_ONLY" and side == "BUY")
                    or (token_side_policy == "SELL_ONLY" and side == "SELL")
                )
                if not side_allowed:
                    _record_maker_no_submission_reason(token_id, "one_sided_mode_disallow_side")
                    for order in side_orders:
                        if actions >= max_actions:
                            _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                            break
                        if self._cancel_order(
                            order,
                            "one_sided_mode_disallow_side",
                            request_origin="maker_side_policy",
                        ):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="one_sided_mode_disallow_side",
                            )
                    continue

                desired_effective = desired_intent
                shadow_effective, shadow_cross_guard_preview = self._maybe_clamp_post_only_intent(
                    desired_effective,
                    top,
                )
                effective_competitiveness_context = (
                    dict(token_competitiveness_context)
                    if isinstance(token_competitiveness_context, dict)
                    else {}
                )
                effective_competitiveness_context["strategy_quote_price"] = float(desired_intent.price)
                effective_competitiveness_context["submission_candidate_quote_price"] = float(
                    shadow_effective.price
                )
                if shadow_cross_guard_preview is not None:
                    effective_competitiveness_context["pre_submit_cross_guard_preview_applied"] = True
                    effective_competitiveness_context["pre_submit_cross_guard_preview_original_price"] = float(
                        shadow_cross_guard_preview.get("original_price", desired_effective.price)
                    )
                    effective_competitiveness_context["pre_submit_cross_guard_preview_adjusted_price"] = float(
                        shadow_cross_guard_preview.get("adjusted_price", shadow_effective.price)
                    )
                    effective_competitiveness_context["pre_submit_cross_guard_preview_reason"] = str(
                        shadow_cross_guard_preview.get("adjustment_reason") or ""
                    ).strip().lower() or "post_only_cross_touch_clamp"
                else:
                    effective_competitiveness_context["pre_submit_cross_guard_preview_applied"] = False
                    effective_competitiveness_context["pre_submit_cross_guard_preview_original_price"] = float(
                        desired_effective.price
                    )
                    effective_competitiveness_context["pre_submit_cross_guard_preview_adjusted_price"] = float(
                        shadow_effective.price
                    )
                    effective_competitiveness_context["pre_submit_cross_guard_preview_reason"] = None
                shadow_event = self._build_maker_fight_admission_shadow_event(
                    token_id=token_id,
                    side=side,
                    top=top,
                    desired_intent=shadow_effective,
                    competitiveness_context=effective_competitiveness_context,
                    cycle_index=cycle_index,
                    primary=primary,
                    open_maker_orders_total=int(open_orders_total),
                    open_orders_for_token_count=len(token_orders),
                    open_orders_same_side_count=len(side_orders),
                )
                shadow_event["strategy_quote_price"] = float(desired_intent.price)
                shadow_event["submission_candidate_quote_price"] = float(shadow_effective.price)
                shadow_event["pre_submit_cross_guard_preview_applied"] = bool(
                    shadow_cross_guard_preview is not None
                )
                shadow_event["pre_submit_cross_guard_preview_original_price"] = (
                    float(shadow_cross_guard_preview.get("original_price"))
                    if isinstance(shadow_cross_guard_preview, dict)
                    and isinstance(shadow_cross_guard_preview.get("original_price"), (int, float))
                    else float(desired_effective.price)
                )
                shadow_event["pre_submit_cross_guard_preview_adjusted_price"] = (
                    float(shadow_cross_guard_preview.get("adjusted_price"))
                    if isinstance(shadow_cross_guard_preview, dict)
                    and isinstance(shadow_cross_guard_preview.get("adjusted_price"), (int, float))
                    else float(shadow_effective.price)
                )
                shadow_event["pre_submit_cross_guard_preview_reason"] = (
                    str(shadow_cross_guard_preview.get("adjustment_reason")).strip().lower()
                    if isinstance(shadow_cross_guard_preview, dict)
                    and str(shadow_cross_guard_preview.get("adjustment_reason") or "").strip()
                    else None
                )
                selection_gate = self._evaluate_maker_selection_gate(
                    shadow_event=shadow_event,
                    competitiveness_context=effective_competitiveness_context,
                )
                shadow_event["launch_safe_selection_enabled"] = bool(selection_gate.get("enabled", False))
                shadow_event["launch_safe_selection_applied"] = bool(selection_gate.get("applied", False))
                shadow_event["launch_safe_selection_passed"] = selection_gate.get("passed")
                shadow_event["launch_safe_selection_primary_reason"] = selection_gate.get(
                    "primary_reject_reason"
                )
                shadow_event["launch_safe_selection_reject_reasons"] = list(
                    selection_gate.get("reject_reasons") or []
                )
                shadow_event["selection_gate_primary_reject_reason"] = selection_gate.get(
                    "primary_reject_reason"
                )
                shadow_event["selection_gate_all_reject_reasons"] = list(
                    selection_gate.get("reject_reasons") or []
                )
                shadow_event["selection_gate_min_sec_to_expiry"] = selection_gate.get(
                    "min_sec_to_expiry"
                )
                shadow_event["selection_gate_max_sec_to_expiry"] = selection_gate.get(
                    "max_sec_to_expiry"
                )
                shadow_event["launch_safe_selection_timing_window_met"] = selection_gate.get(
                    "timing_window_met"
                )
                shadow_event["cannon_target_notional_usd"] = selection_gate.get(
                    "cannon_target_notional_usd"
                )
                shadow_event["cannon_min_depth_multiple"] = selection_gate.get(
                    "cannon_min_depth_multiple"
                )
                shadow_event["visible_depth_notional_usd"] = selection_gate.get(
                    "visible_depth_notional_usd"
                )
                shadow_event["depth_multiple_vs_cannon_target"] = selection_gate.get(
                    "depth_multiple_vs_cannon_target"
                )
                shadow_event["cannon_depth_requirement_met"] = selection_gate.get(
                    "cannon_depth_requirement_met"
                )
                shadow_event["same_target_submit_count_prior"] = selection_gate.get(
                    "same_target_submit_count_prior"
                )
                shadow_event["repeat_target_calm"] = selection_gate.get(
                    "repeat_target_calm"
                )
                shadow_event["repeat_target_side_calm"] = selection_gate.get(
                    "repeat_target_side_calm"
                )
                shadow_event["max_same_target_submit_count_prior"] = selection_gate.get(
                    "max_same_target_submit_count_prior"
                )
                shadow_event["max_same_target_side_submit_count_prior"] = selection_gate.get(
                    "max_same_target_side_submit_count_prior"
                )
                effective_competitiveness_context["launch_safe_selection_enabled"] = bool(
                    selection_gate.get("enabled", False)
                )
                effective_competitiveness_context["launch_safe_selection_applied"] = bool(
                    selection_gate.get("applied", False)
                )
                effective_competitiveness_context["launch_safe_selection_passed"] = selection_gate.get(
                    "passed"
                )
                effective_competitiveness_context["launch_safe_selection_primary_reason"] = selection_gate.get(
                    "primary_reject_reason"
                )
                effective_competitiveness_context["launch_safe_selection_reject_reasons"] = list(
                    selection_gate.get("reject_reasons") or []
                )
                effective_competitiveness_context["selection_gate_primary_reject_reason"] = selection_gate.get(
                    "primary_reject_reason"
                )
                effective_competitiveness_context["selection_gate_all_reject_reasons"] = list(
                    selection_gate.get("reject_reasons") or []
                )
                effective_competitiveness_context["selection_gate_min_sec_to_expiry"] = selection_gate.get(
                    "min_sec_to_expiry"
                )
                effective_competitiveness_context["selection_gate_max_sec_to_expiry"] = selection_gate.get(
                    "max_sec_to_expiry"
                )
                effective_competitiveness_context["launch_safe_selection_timing_window_met"] = selection_gate.get(
                    "timing_window_met"
                )
                effective_competitiveness_context["cannon_target_notional_usd"] = selection_gate.get(
                    "cannon_target_notional_usd"
                )
                effective_competitiveness_context["cannon_min_depth_multiple"] = selection_gate.get(
                    "cannon_min_depth_multiple"
                )
                effective_competitiveness_context["visible_depth_notional_usd"] = selection_gate.get(
                    "visible_depth_notional_usd"
                )
                effective_competitiveness_context["depth_multiple_vs_cannon_target"] = selection_gate.get(
                    "depth_multiple_vs_cannon_target"
                )
                effective_competitiveness_context["cannon_depth_requirement_met"] = selection_gate.get(
                    "cannon_depth_requirement_met"
                )
                effective_competitiveness_context["same_target_submit_count_prior"] = selection_gate.get(
                    "same_target_submit_count_prior"
                )
                effective_competitiveness_context["repeat_target_calm"] = selection_gate.get(
                    "repeat_target_calm"
                )
                effective_competitiveness_context["repeat_target_side_calm"] = selection_gate.get(
                    "repeat_target_side_calm"
                )
                effective_competitiveness_context["max_same_target_submit_count_prior"] = selection_gate.get(
                    "max_same_target_submit_count_prior"
                )
                effective_competitiveness_context["max_same_target_side_submit_count_prior"] = selection_gate.get(
                    "max_same_target_side_submit_count_prior"
                )
                effective_competitiveness_context["admission_shadow_id"] = str(
                    shadow_event.get("admission_shadow_id") or ""
                )
                effective_competitiveness_context["target_side_ref"] = str(
                    shadow_event.get("target_side_ref") or ""
                )
                if bool(selection_gate.get("applied", False)) and not bool(selection_gate.get("passed", False)):
                    primary_reject_reason = str(
                        selection_gate.get("primary_reject_reason") or "selection_gate_reject"
                    ).strip().lower() or "selection_gate_reject"
                    gate_reject_label = (
                        f"launch_safe_{primary_reject_reason}"
                        if primary_reject_reason.startswith("selection_")
                        else f"launch_safe_selection_{primary_reject_reason}"
                    )
                    _record_maker_no_submission_reason(
                        token_id,
                        gate_reject_label,
                    )
                    for order in side_orders:
                        if actions >= max_actions:
                            _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                            break
                        if self._cancel_order(
                            order,
                            "launch_safe_selection_reject",
                            request_origin="maker_selection_gate",
                        ):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="launch_safe_selection_reject",
                            )
                    self._log_maker_fight_admission_shadow_event(
                        shadow_event,
                        decision_result="selection_rejected",
                        decision_block_reason=gate_reject_label,
                    )
                    continue

                replace_needed = (
                    primary is None
                    or self._needs_replace(primary, desired_effective, requote_delta=token_requote_delta)
                )

                if replace_needed:
                    if primary is not None and self.maker_replace_min_rest_sec > 0.0:
                        age_sec = self._order_age_sec(primary)
                        if age_sec is not None and age_sec < self.maker_replace_min_rest_sec:
                            self._log_maker_fight_admission_shadow_event(
                                shadow_event,
                                decision_result="replace_guard_blocked",
                                decision_block_reason="replace_guard_min_rest",
                            )
                            _record_maker_no_submission_reason(token_id, "replace_guard_min_rest")
                            continue

                    cancel_failed = False
                    # Cancel old side orders first to keep behavior deterministic.
                    for order in side_orders:
                        if actions >= max_actions:
                            _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                            break
                        if self._cancel_order(
                            order,
                            "replace_quote",
                            request_origin="maker_replace_logic",
                        ):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="replace_quote",
                            )
                        else:
                            cancel_failed = True
                            _record_maker_no_submission_reason(token_id, "replace_cancel_unavailable")
                    if actions >= max_actions:
                        self._log_maker_fight_admission_shadow_event(
                            shadow_event,
                            decision_result="action_budget_exhausted",
                            decision_block_reason="action_budget_exhausted",
                        )
                        break
                    risk_context_payload = dict(effective_competitiveness_context)
                    risk_context_payload.setdefault("submission_lane", "maker")
                    risk_context_payload.setdefault(
                        "stage",
                        str(desired_effective.stage or "").strip().upper() or "UNKNOWN",
                    )
                    risk_context_payload.setdefault("financial_posture_class", "UNKNOWN")
                    if isinstance(realized_vol, (int, float)):
                        risk_context_payload["realized_volatility"] = float(realized_vol)
                    placed, submit_reject_reason = self._place_order(
                        desired_effective,
                        top,
                        open_orders_for_token=token_orders,
                        open_orders_total=open_orders_total,
                        open_orders_all=[order for rows in by_token.values() for order in rows],
                        reference_mid_by_token=reference_mid_by_token,
                        risk_context=risk_context_payload,
                        competitiveness_context=effective_competitiveness_context,
                    )
                    if placed is not None:
                        actions += 1
                        open_orders_total += 1
                        token_orders.append(placed)
                        maker_submitted_token_ids.add(str(token_id))
                        maker_submitted_order_ids_by_token.setdefault(str(token_id), []).append(str(placed.order_id))
                        self._log_maker_fight_admission_shadow_event(
                            shadow_event,
                            decision_result="submitted",
                            order_submit_id=str(placed.order_id),
                        )
                    elif not cancel_failed:
                        if str(submit_reject_reason or "").strip():
                            _record_maker_no_submission_reason(token_id, f"submit_rejected_{submit_reject_reason}")
                        else:
                            _record_maker_no_submission_reason(token_id, "submit_rejected")
                        self._log_maker_fight_admission_shadow_event(
                            shadow_event,
                            decision_result="submit_rejected",
                            decision_block_reason=str(submit_reject_reason or "submit_rejected"),
                        )
                    else:
                        self._log_maker_fight_admission_shadow_event(
                            shadow_event,
                            decision_result="submit_rejected",
                            decision_block_reason=(
                                str(submit_reject_reason).strip()
                                if str(submit_reject_reason or "").strip()
                                else "replace_cancel_unavailable"
                            ),
                        )
                else:
                    self._log_maker_fight_admission_shadow_event(
                        shadow_event,
                        decision_result="quote_unchanged",
                        decision_block_reason="quote_unchanged",
                    )
                    _record_maker_no_submission_reason(token_id, "quote_unchanged")
                    for order in extras:
                        if actions >= max_actions:
                            _record_maker_no_submission_reason(token_id, "action_budget_exhausted")
                            break
                        if self._cancel_order(
                            order,
                            "extra_same_side_order",
                            request_origin="maker_extra_same_side_cleanup",
                        ):
                            actions += 1
                            open_orders_total = max(0, open_orders_total - 1)
                            self._remove_token_order_if_present(
                                token_orders,
                                order,
                                remove_reason="extra_same_side_order",
                            )

        self.telemetry.incr("cycles")
        open_orders_after = max(0, int(open_orders_total))
        return {
            "actions": actions,
            "fills": fills,
            "open_orders": open_orders_after,
            "maker_submitted_token_ids": sorted(maker_submitted_token_ids),
            "maker_submitted_order_ids_by_token": {
                token_id: sorted(order_ids)
                for token_id, order_ids in sorted(maker_submitted_order_ids_by_token.items())
            },
            "maker_no_submission_reason_by_token": {
                token_id: reason
                for token_id, reason in sorted(maker_no_submission_reason_by_token.items())
                if token_id not in maker_submitted_token_ids and str(reason).strip()
            },
            "maker_no_submission_category_by_token": {
                token_id: category
                for token_id, category in sorted(maker_no_submission_category_by_token.items())
                if token_id not in maker_submitted_token_ids and str(category).strip()
            },
        }

    def _cancel_orphan_orders(
        self,
        open_orders: List[LiveOrder],
        tracked_tokens: Set[str],
        *,
        max_actions: int,
    ) -> Tuple[List[LiveOrder], int]:
        keep: List[LiveOrder] = []
        actions = 0
        for order in open_orders:
            if order.token_id in tracked_tokens:
                keep.append(order)
                continue
            if actions >= max_actions:
                keep.append(order)
                continue
            if self._cancel_order(order, "orphan_token_order", request_origin="non_target_cleanup"):
                actions += 1
            else:
                keep.append(order)
        return keep, actions
