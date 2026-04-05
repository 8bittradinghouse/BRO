from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return float(out)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclasses.dataclass(frozen=True)
class StageAggressiveness:
    size_mult: float = 1.0
    price_aggress_bps: float = 0.0

    @classmethod
    def from_mapping(cls, row: Optional[Mapping[str, Any]]) -> "StageAggressiveness":
        if not isinstance(row, Mapping):
            return cls()
        return cls(
            size_mult=max(0.01, _safe_float(row.get("size_mult"), 1.0)),
            price_aggress_bps=max(0.0, _safe_float(row.get("price_aggress_bps"), 0.0)),
        )


@dataclasses.dataclass(frozen=True)
class SniperToolConfig:
    enabled: bool = False
    hard_min_target_usd: float = 100.0
    hard_min_enforcement: str = "skip_if_unachievable"
    dynamic_size_enabled: bool = True
    dynamic_size_edge_start_abs: float = 0.12
    dynamic_size_edge_full_abs: float = 0.22
    dynamic_size_target_usd_cap: float = 250.0
    conviction_model: str = "edge_plus_latency_score"
    edge_weight: float = 0.65
    latency_score_weight: float = 0.35
    final_window_enabled: bool = True
    final_window_sec: float = 15.0
    aggressive_window_enabled: bool = False
    aggressive_window_sec: float = 10.0
    stage_aggressiveness: Dict[str, StageAggressiveness] = dataclasses.field(default_factory=dict)
    price_aggress_bps_max: float = 8.0
    multi_oracle_boost_enabled: bool = False
    multi_oracle_edge_threshold_abs: float = 0.20
    multi_oracle_target_usd_cap: float = 350.0
    multi_oracle_capital_pct_cap: float = 0.18

    @classmethod
    def from_mapping(cls, row: Optional[Mapping[str, Any]]) -> "SniperToolConfig":
        if not isinstance(row, Mapping):
            return cls()
        stage_rows = row.get("stage_aggressiveness")
        parsed_stage_rows: Dict[str, StageAggressiveness] = {}
        if isinstance(stage_rows, Mapping):
            for stage, payload in stage_rows.items():
                stage_name = str(stage or "").strip().upper()
                if not stage_name:
                    continue
                parsed_stage_rows[stage_name] = StageAggressiveness.from_mapping(payload)
        return cls(
            enabled=bool(row.get("enabled", False)),
            hard_min_target_usd=max(0.0, _safe_float(row.get("hard_min_target_usd"), 100.0)),
            hard_min_enforcement=str(row.get("hard_min_enforcement", "skip_if_unachievable")).strip().lower()
            or "skip_if_unachievable",
            dynamic_size_enabled=bool(row.get("dynamic_size_enabled", True)),
            dynamic_size_edge_start_abs=max(0.0, _safe_float(row.get("dynamic_size_edge_start_abs"), 0.12)),
            dynamic_size_edge_full_abs=max(0.0, _safe_float(row.get("dynamic_size_edge_full_abs"), 0.22)),
            dynamic_size_target_usd_cap=max(0.0, _safe_float(row.get("dynamic_size_target_usd_cap"), 250.0)),
            conviction_model=str(row.get("conviction_model", "edge_plus_latency_score")).strip().lower()
            or "edge_plus_latency_score",
            edge_weight=max(0.0, _safe_float(row.get("edge_weight"), 0.65)),
            latency_score_weight=max(0.0, _safe_float(row.get("latency_score_weight"), 0.35)),
            final_window_enabled=bool(row.get("final_window_enabled", True)),
            final_window_sec=max(0.0, _safe_float(row.get("final_window_sec"), 15.0)),
            aggressive_window_enabled=bool(row.get("aggressive_window_enabled", False)),
            aggressive_window_sec=max(0.0, _safe_float(row.get("aggressive_window_sec"), 10.0)),
            stage_aggressiveness=parsed_stage_rows,
            price_aggress_bps_max=max(0.0, _safe_float(row.get("price_aggress_bps_max"), 8.0)),
            multi_oracle_boost_enabled=bool(row.get("multi_oracle_boost_enabled", False)),
            multi_oracle_edge_threshold_abs=max(0.0, _safe_float(row.get("multi_oracle_edge_threshold_abs"), 0.20)),
            multi_oracle_target_usd_cap=max(0.0, _safe_float(row.get("multi_oracle_target_usd_cap"), 350.0)),
            multi_oracle_capital_pct_cap=max(0.0, _safe_float(row.get("multi_oracle_capital_pct_cap"), 0.18)),
        )


@dataclasses.dataclass(frozen=True)
class SniperCandidate:
    token_id: str
    stage: str
    sec_to_expiry: Optional[float]
    edge_value: float
    required_min_edge: float
    base_target_usd: float
    top_best_bid_price: Optional[float]
    top_best_ask_price: Optional[float]
    token_score: Optional[float] = None
    max_feasible_target_usd: Optional[float] = None
    multi_oracle_confirmation: bool = False
    multi_oracle_boost_applied: bool = False
    multi_oracle_status: str = "disabled"
    multi_oracle_cap_usd: Optional[float] = None


@dataclasses.dataclass(frozen=True)
class SniperDecision:
    token_id: str
    stage: str
    should_submit: bool
    block_reason: Optional[str]
    side: Optional[str]
    price: Optional[float]
    edge_abs: float
    required_min_edge: float
    conviction_score: float
    timing_window_class: str
    aggressiveness_level: str
    price_aggress_bps_applied: float
    target_usd_requested: float
    target_usd_resolved: float
    hard_min_floor_applied: bool
    hard_min_unachievable: bool
    dynamic_size_capped_by_risk: bool
    multi_oracle_confirmation: bool
    multi_oracle_boost_applied: bool
    multi_oracle_status: str

    def as_event_payload(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "stage": self.stage,
            "conviction_score": float(self.conviction_score),
            "edge_abs": float(self.edge_abs),
            "required_min_edge": float(self.required_min_edge),
            "timing_window_class": self.timing_window_class,
            "aggressiveness_level": self.aggressiveness_level,
            "price_aggress_bps_applied": float(self.price_aggress_bps_applied),
            "target_usd_requested": float(self.target_usd_requested),
            "target_usd_resolved": float(self.target_usd_resolved),
            "hard_min_floor_applied": bool(self.hard_min_floor_applied),
            "hard_min_unachievable": bool(self.hard_min_unachievable),
            "dynamic_size_capped_by_risk": bool(self.dynamic_size_capped_by_risk),
            "multi_oracle_confirmation": bool(self.multi_oracle_confirmation),
            "multi_oracle_boost_applied": bool(self.multi_oracle_boost_applied),
            "multi_oracle_status": str(self.multi_oracle_status or "unknown"),
            "block_reason": self.block_reason,
            "side": self.side,
            "price": self.price,
            "should_submit": bool(self.should_submit),
        }

    def as_competitiveness_payload(self) -> Dict[str, Any]:
        return {
            "conviction_score": float(self.conviction_score),
            "edge_abs": float(self.edge_abs),
            "required_min_edge": float(self.required_min_edge),
            "timing_window_class": self.timing_window_class,
            "aggressiveness_level": self.aggressiveness_level,
            "price_aggress_bps_applied": float(self.price_aggress_bps_applied),
            "target_usd_requested": float(self.target_usd_requested),
            "target_usd_resolved": float(self.target_usd_resolved),
            "hard_min_floor_applied": bool(self.hard_min_floor_applied),
            "hard_min_unachievable": bool(self.hard_min_unachievable),
            "dynamic_size_capped_by_risk": bool(self.dynamic_size_capped_by_risk),
            "multi_oracle_confirmation": bool(self.multi_oracle_confirmation),
            "multi_oracle_boost_applied": bool(self.multi_oracle_boost_applied),
            "multi_oracle_status": str(self.multi_oracle_status or "unknown"),
        }


@dataclasses.dataclass(frozen=True)
class SniperBatchResult:
    decisions: List[SniperDecision]


class SniperTool:
    def __init__(self, cfg: SniperToolConfig):
        self.cfg = cfg

    def _edge_norm(self, edge_abs: float) -> float:
        start = max(0.0, float(self.cfg.dynamic_size_edge_start_abs))
        full = max(start, float(self.cfg.dynamic_size_edge_full_abs))
        if edge_abs <= start:
            return 0.0
        if edge_abs >= full:
            return 1.0
        span = max(1e-9, full - start)
        return _clamp((edge_abs - start) / span, 0.0, 1.0)

    def _conviction(self, *, edge_abs: float, token_score: Optional[float]) -> float:
        edge_norm = self._edge_norm(edge_abs)
        score_norm = _clamp(_safe_float(token_score, 0.0), 0.0, 1.0)
        if self.cfg.conviction_model != "edge_plus_latency_score":
            return edge_norm
        edge_weight = max(0.0, float(self.cfg.edge_weight))
        score_weight = max(0.0, float(self.cfg.latency_score_weight))
        total = edge_weight + score_weight
        if total <= 0.0:
            return edge_norm
        return _clamp(((edge_weight * edge_norm) + (score_weight * score_norm)) / total, 0.0, 1.0)

    def _timing_window_class(self, sec_to_expiry: Optional[float]) -> str:
        if not self.cfg.final_window_enabled:
            return "window_disabled"
        if not isinstance(sec_to_expiry, (int, float)):
            return "outside_window"
        sec = float(sec_to_expiry)
        if sec < 0.0 or sec > float(self.cfg.final_window_sec):
            return "outside_window"
        if self.cfg.aggressive_window_enabled and sec <= float(self.cfg.aggressive_window_sec):
            return "final10"
        return "final15"

    def _stage_aggressiveness(self, stage: str) -> StageAggressiveness:
        normalized = str(stage or "").strip().upper()
        return self.cfg.stage_aggressiveness.get(normalized, StageAggressiveness())

    def _target_usd(
        self,
        *,
        base_target_usd: float,
        conviction: float,
        stage_mult: float,
        max_dynamic_cap_override: Optional[float] = None,
    ) -> tuple[float, float, bool]:
        floor = max(0.0, float(self.cfg.hard_min_target_usd))
        requested = max(float(base_target_usd), floor)
        hard_min_floor_applied = requested <= floor + 1e-9
        if self.cfg.dynamic_size_enabled:
            dynamic_cap = max(floor, float(self.cfg.dynamic_size_target_usd_cap))
            if isinstance(max_dynamic_cap_override, (int, float)):
                dynamic_cap = max(floor, float(max_dynamic_cap_override))
            requested = floor + (_clamp(conviction, 0.0, 1.0) * (dynamic_cap - floor))
            hard_min_floor_applied = True
        requested *= max(0.01, float(stage_mult))
        # Hard floor is non-negotiable under all overlays.
        if requested < floor:
            requested = floor
            hard_min_floor_applied = True
        return requested, floor, hard_min_floor_applied

    def evaluate_batch(
        self,
        *,
        candidates: Sequence[SniperCandidate],
        max_orders_per_cycle: int,
    ) -> SniperBatchResult:
        decisions: List[SniperDecision] = []
        if not self.cfg.enabled:
            for candidate in candidates:
                decisions.append(
                    SniperDecision(
                        token_id=str(candidate.token_id),
                        stage=str(candidate.stage),
                        should_submit=False,
                        block_reason="sniper_taker_competitiveness_disabled",
                        side=None,
                        price=None,
                        edge_abs=abs(float(candidate.edge_value)),
                        required_min_edge=max(0.0, float(candidate.required_min_edge)),
                        conviction_score=0.0,
                        timing_window_class="window_disabled",
                        aggressiveness_level="disabled",
                        price_aggress_bps_applied=0.0,
                        target_usd_requested=0.0,
                        target_usd_resolved=0.0,
                        hard_min_floor_applied=False,
                        hard_min_unachievable=False,
                        dynamic_size_capped_by_risk=False,
                        multi_oracle_confirmation=False,
                        multi_oracle_boost_applied=False,
                        multi_oracle_status="disabled",
                    )
                )
            return SniperBatchResult(decisions=decisions)

        provisional: List[SniperDecision] = []
        for candidate in candidates:
            token_id = str(candidate.token_id)
            stage = str(candidate.stage or "").strip().upper() or "UNKNOWN"
            edge_signed = float(candidate.edge_value)
            edge_abs = abs(edge_signed)
            required_min_edge = max(0.0, float(candidate.required_min_edge))
            conviction_score = self._conviction(edge_abs=edge_abs, token_score=candidate.token_score)
            timing_window_class = self._timing_window_class(candidate.sec_to_expiry)
            multi_oracle_status = str(candidate.multi_oracle_status or "unknown").strip().lower() or "unknown"
            multi_oracle_confirmation = bool(candidate.multi_oracle_confirmation)

            if edge_abs < required_min_edge:
                provisional.append(
                    SniperDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="edge_below_min",
                        side=None,
                        price=None,
                        edge_abs=edge_abs,
                        required_min_edge=required_min_edge,
                        conviction_score=conviction_score,
                        timing_window_class=timing_window_class,
                        aggressiveness_level="none",
                        price_aggress_bps_applied=0.0,
                        target_usd_requested=0.0,
                        target_usd_resolved=0.0,
                        hard_min_floor_applied=False,
                        hard_min_unachievable=False,
                        dynamic_size_capped_by_risk=False,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_applied=False,
                        multi_oracle_status=multi_oracle_status,
                    )
                )
                continue

            if timing_window_class == "outside_window":
                provisional.append(
                    SniperDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="taker_outside_final_window",
                        side=None,
                        price=None,
                        edge_abs=edge_abs,
                        required_min_edge=required_min_edge,
                        conviction_score=conviction_score,
                        timing_window_class=timing_window_class,
                        aggressiveness_level="none",
                        price_aggress_bps_applied=0.0,
                        target_usd_requested=0.0,
                        target_usd_resolved=0.0,
                        hard_min_floor_applied=False,
                        hard_min_unachievable=False,
                        dynamic_size_capped_by_risk=False,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_applied=False,
                        multi_oracle_status=multi_oracle_status,
                    )
                )
                continue

            stage_profile = self._stage_aggressiveness(stage)
            aggress_bps = float(stage_profile.price_aggress_bps)
            if timing_window_class == "final10" and self.cfg.aggressive_window_enabled:
                aggress_bps = max(aggress_bps, aggress_bps * 1.25)
            aggress_bps = _clamp(aggress_bps, 0.0, float(self.cfg.price_aggress_bps_max))
            aggressiveness_level = "final10" if timing_window_class == "final10" else "final15"

            boost_allowed = (
                bool(self.cfg.multi_oracle_boost_enabled)
                and multi_oracle_confirmation
                and edge_abs >= float(self.cfg.multi_oracle_edge_threshold_abs)
                and timing_window_class in {"final15", "final10"}
            )
            boost_cap_override = None
            if boost_allowed:
                boost_cap_override = max(
                    float(self.cfg.dynamic_size_target_usd_cap),
                    float(self.cfg.multi_oracle_target_usd_cap),
                )
                if isinstance(candidate.multi_oracle_cap_usd, (int, float)):
                    boost_cap_override = min(boost_cap_override, max(0.0, float(candidate.multi_oracle_cap_usd)))

            target_usd_requested, floor_usd, hard_min_floor_applied = self._target_usd(
                base_target_usd=float(candidate.base_target_usd),
                conviction=conviction_score,
                stage_mult=float(stage_profile.size_mult),
                max_dynamic_cap_override=boost_cap_override,
            )
            max_feasible_target = (
                float(candidate.max_feasible_target_usd)
                if isinstance(candidate.max_feasible_target_usd, (int, float))
                else None
            )
            if max_feasible_target is not None and max_feasible_target > 0.0:
                target_usd_resolved = min(target_usd_requested, max_feasible_target)
            else:
                target_usd_resolved = target_usd_requested
            dynamic_size_capped_by_risk = (
                max_feasible_target is not None and max_feasible_target + 1e-9 < target_usd_requested
            )
            hard_min_unachievable = target_usd_resolved + 1e-9 < floor_usd
            if hard_min_unachievable and self.cfg.hard_min_enforcement == "skip_if_unachievable":
                provisional.append(
                    SniperDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="taker_hard_min_notional_unachievable",
                        side=None,
                        price=None,
                        edge_abs=edge_abs,
                        required_min_edge=required_min_edge,
                        conviction_score=conviction_score,
                        timing_window_class=timing_window_class,
                        aggressiveness_level=aggressiveness_level,
                        price_aggress_bps_applied=aggress_bps,
                        target_usd_requested=target_usd_requested,
                        target_usd_resolved=target_usd_resolved,
                        hard_min_floor_applied=hard_min_floor_applied,
                        hard_min_unachievable=True,
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_applied=boost_allowed,
                        multi_oracle_status=multi_oracle_status,
                    )
                )
                continue

            side = "BUY" if edge_signed > 0.0 else "SELL"
            base_price = (
                float(candidate.top_best_ask_price)
                if side == "BUY" and isinstance(candidate.top_best_ask_price, (int, float))
                else float(candidate.top_best_bid_price)
                if side == "SELL" and isinstance(candidate.top_best_bid_price, (int, float))
                else None
            )
            if base_price is None or base_price <= 0.0:
                provisional.append(
                    SniperDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="taker_price_unavailable",
                        side=side,
                        price=None,
                        edge_abs=edge_abs,
                        required_min_edge=required_min_edge,
                        conviction_score=conviction_score,
                        timing_window_class=timing_window_class,
                        aggressiveness_level=aggressiveness_level,
                        price_aggress_bps_applied=aggress_bps,
                        target_usd_requested=target_usd_requested,
                        target_usd_resolved=target_usd_resolved,
                        hard_min_floor_applied=hard_min_floor_applied,
                        hard_min_unachievable=hard_min_unachievable,
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_applied=boost_allowed,
                        multi_oracle_status=multi_oracle_status,
                    )
                )
                continue

            if side == "BUY":
                price = base_price * (1.0 + (aggress_bps / 10000.0))
            else:
                price = base_price * (1.0 - (aggress_bps / 10000.0))
            if not math.isfinite(price) or price <= 0.0 or price >= 1.0:
                provisional.append(
                    SniperDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="taker_price_unavailable",
                        side=side,
                        price=None,
                        edge_abs=edge_abs,
                        required_min_edge=required_min_edge,
                        conviction_score=conviction_score,
                        timing_window_class=timing_window_class,
                        aggressiveness_level=aggressiveness_level,
                        price_aggress_bps_applied=aggress_bps,
                        target_usd_requested=target_usd_requested,
                        target_usd_resolved=target_usd_resolved,
                        hard_min_floor_applied=hard_min_floor_applied,
                        hard_min_unachievable=hard_min_unachievable,
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_applied=boost_allowed,
                        multi_oracle_status=multi_oracle_status,
                    )
                )
                continue

            provisional.append(
                SniperDecision(
                    token_id=token_id,
                    stage=stage,
                    should_submit=True,
                    block_reason=None,
                    side=side,
                    price=round(float(price), 9),
                    edge_abs=edge_abs,
                    required_min_edge=required_min_edge,
                    conviction_score=conviction_score,
                    timing_window_class=timing_window_class,
                    aggressiveness_level=aggressiveness_level,
                    price_aggress_bps_applied=aggress_bps,
                    target_usd_requested=target_usd_requested,
                    target_usd_resolved=target_usd_resolved,
                    hard_min_floor_applied=hard_min_floor_applied,
                    hard_min_unachievable=hard_min_unachievable,
                    dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                    multi_oracle_confirmation=multi_oracle_confirmation,
                    multi_oracle_boost_applied=boost_allowed,
                    multi_oracle_status=multi_oracle_status,
                )
            )

        submit_candidates = [row for row in provisional if row.should_submit]
        submit_candidates_sorted = sorted(
            submit_candidates,
            key=lambda row: (-float(row.conviction_score), -float(row.edge_abs), str(row.token_id)),
        )
        allowed_count = max(0, int(max_orders_per_cycle))
        allowed_ids = {row.token_id for row in submit_candidates_sorted[:allowed_count]}

        for row in provisional:
            if row.should_submit and row.token_id not in allowed_ids:
                decisions.append(
                    dataclasses.replace(
                        row,
                        should_submit=False,
                        block_reason="taker_order_budget_exhausted",
                    )
                )
                continue
            decisions.append(row)
        return SniperBatchResult(decisions=decisions)
