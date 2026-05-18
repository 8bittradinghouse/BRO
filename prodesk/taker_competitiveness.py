from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .edge_truth_contract import lifecycle_phase_surface_fields, legacy_stage_to_lifecycle_phase

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
class TakerCompetitivenessConfig:
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
    price_aggress_bps_max: float = 8.0
    dynamic_preview_enabled: bool = False
    multi_oracle_boost_enabled: bool = False
    multi_oracle_boost_window_sec: float = 15.0
    multi_oracle_edge_threshold_abs: float = 0.20
    multi_oracle_target_usd_cap: float = 350.0
    multi_oracle_capital_pct_cap: float = 0.18
    normal_side_policy: str = "buy_expected_winner_only"
    allow_complement_buy_route: bool = True
    min_visible_fill_ratio: float = 0.0

    @classmethod
    def from_mapping(cls, row: Optional[Mapping[str, Any]], *, strict: bool = False) -> "TakerCompetitivenessConfig":
        if not isinstance(row, Mapping):
            return cls()
        cfg = cls(
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
            price_aggress_bps_max=max(0.0, _safe_float(row.get("price_aggress_bps_max"), 8.0)),
            dynamic_preview_enabled=bool(row.get("dynamic_preview_enabled", False)),
            multi_oracle_boost_enabled=bool(row.get("multi_oracle_boost_enabled", False)),
            multi_oracle_boost_window_sec=max(0.0, _safe_float(row.get("multi_oracle_boost_window_sec"), 15.0)),
            multi_oracle_edge_threshold_abs=max(0.0, _safe_float(row.get("multi_oracle_edge_threshold_abs"), 0.20)),
            multi_oracle_target_usd_cap=max(0.0, _safe_float(row.get("multi_oracle_target_usd_cap"), 350.0)),
            multi_oracle_capital_pct_cap=max(0.0, _safe_float(row.get("multi_oracle_capital_pct_cap"), 0.18)),
            normal_side_policy=str(row.get("normal_side_policy", "buy_expected_winner_only")).strip().lower()
            or "buy_expected_winner_only",
            allow_complement_buy_route=bool(row.get("allow_complement_buy_route", True)),
            min_visible_fill_ratio=max(0.0, min(1.0, _safe_float(row.get("min_visible_fill_ratio"), 0.0))),
        )
        if strict:
            _validate_taker_competitiveness_policy(cfg, row=row)
        return cfg

    def as_mapping(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "hard_min_target_usd": float(self.hard_min_target_usd),
            "hard_min_enforcement": str(self.hard_min_enforcement),
            "dynamic_size_enabled": bool(self.dynamic_size_enabled),
            "dynamic_size_edge_start_abs": float(self.dynamic_size_edge_start_abs),
            "dynamic_size_edge_full_abs": float(self.dynamic_size_edge_full_abs),
            "dynamic_size_target_usd_cap": float(self.dynamic_size_target_usd_cap),
            "conviction_model": str(self.conviction_model),
            "edge_weight": float(self.edge_weight),
            "latency_score_weight": float(self.latency_score_weight),
            "final_window_enabled": bool(self.final_window_enabled),
            "final_window_sec": float(self.final_window_sec),
            "aggressive_window_enabled": bool(self.aggressive_window_enabled),
            "aggressive_window_sec": float(self.aggressive_window_sec),
            "price_aggress_bps_max": float(self.price_aggress_bps_max),
            "dynamic_preview_enabled": bool(self.dynamic_preview_enabled),
            "multi_oracle_boost_enabled": bool(self.multi_oracle_boost_enabled),
            "multi_oracle_boost_window_sec": float(self.multi_oracle_boost_window_sec),
            "multi_oracle_edge_threshold_abs": float(self.multi_oracle_edge_threshold_abs),
            "multi_oracle_target_usd_cap": float(self.multi_oracle_target_usd_cap),
            "multi_oracle_capital_pct_cap": float(self.multi_oracle_capital_pct_cap),
            "normal_side_policy": str(self.normal_side_policy),
            "allow_complement_buy_route": bool(self.allow_complement_buy_route),
            "min_visible_fill_ratio": float(self.min_visible_fill_ratio),
        }


def _validate_taker_competitiveness_policy(
    cfg: "TakerCompetitivenessConfig",
    *,
    row: Optional[Mapping[str, Any]] = None,
) -> None:
    source = row if isinstance(row, Mapping) else {}

    if float(cfg.hard_min_target_usd) <= 0.0:
        raise ValueError("taker.competitiveness.hard_min_target_usd must be > 0")
    if float(cfg.dynamic_size_edge_start_abs) < 0.0:
        raise ValueError("taker.competitiveness.dynamic_size_edge_start_abs must be >= 0")
    if float(cfg.dynamic_size_edge_full_abs) < float(cfg.dynamic_size_edge_start_abs):
        raise ValueError(
            "taker.competitiveness.dynamic_size_edge_full_abs must be >= dynamic_size_edge_start_abs"
        )
    if float(cfg.dynamic_size_target_usd_cap) <= 0.0:
        raise ValueError("taker.competitiveness.dynamic_size_target_usd_cap must be > 0")
    if float(cfg.dynamic_size_target_usd_cap) + 1e-9 < float(cfg.hard_min_target_usd):
        raise ValueError(
            "taker.competitiveness.dynamic_size_target_usd_cap must be >= hard_min_target_usd"
        )
    if not (0.0 <= float(cfg.edge_weight) <= 1.0):
        raise ValueError("taker.competitiveness.edge_weight must be within [0, 1]")
    if not (0.0 <= float(cfg.latency_score_weight) <= 1.0):
        raise ValueError("taker.competitiveness.latency_score_weight must be within [0, 1]")
    if (float(cfg.edge_weight) + float(cfg.latency_score_weight)) <= 0.0:
        raise ValueError(
            "taker.competitiveness.edge_weight + latency_score_weight must be > 0"
        )
    if float(cfg.final_window_sec) <= 0.0:
        raise ValueError("taker.competitiveness.final_window_sec must be > 0")
    if float(cfg.aggressive_window_sec) < 0.0:
        raise ValueError("taker.competitiveness.aggressive_window_sec must be >= 0")
    if float(cfg.aggressive_window_sec) > float(cfg.final_window_sec):
        raise ValueError(
            "taker.competitiveness.aggressive_window_sec must be <= final_window_sec"
        )
    if float(cfg.price_aggress_bps_max) < 0.0:
        raise ValueError("taker.competitiveness.price_aggress_bps_max must be >= 0")
    if float(cfg.multi_oracle_boost_window_sec) <= 0.0:
        raise ValueError("taker.competitiveness.multi_oracle_boost_window_sec must be > 0")
    if float(cfg.multi_oracle_boost_window_sec) > float(cfg.final_window_sec):
        raise ValueError(
            "taker.competitiveness.multi_oracle_boost_window_sec must be <= final_window_sec"
        )
    if float(cfg.multi_oracle_edge_threshold_abs) < 0.0:
        raise ValueError("taker.competitiveness.multi_oracle_edge_threshold_abs must be >= 0")
    if float(cfg.multi_oracle_target_usd_cap) <= 0.0:
        raise ValueError("taker.competitiveness.multi_oracle_target_usd_cap must be > 0")
    if float(cfg.multi_oracle_target_usd_cap) + 1e-9 < float(cfg.dynamic_size_target_usd_cap):
        raise ValueError(
            "taker.competitiveness.multi_oracle_target_usd_cap must be >= dynamic_size_target_usd_cap"
        )
    if not (0.0 <= float(cfg.multi_oracle_capital_pct_cap) <= 1.0):
        raise ValueError("taker.competitiveness.multi_oracle_capital_pct_cap must be within [0, 1]")
    if str(cfg.hard_min_enforcement).strip().lower() not in {"skip_if_unachievable"}:
        raise ValueError(
            "taker.competitiveness.hard_min_enforcement must be skip_if_unachievable"
        )
    if str(cfg.conviction_model).strip().lower() not in {"edge_plus_latency_score"}:
        raise ValueError(
            "taker.competitiveness.conviction_model must be edge_plus_latency_score"
        )
    if str(cfg.normal_side_policy).strip().lower() not in {"buy_expected_winner_only"}:
        raise ValueError(
            "taker.competitiveness.normal_side_policy must be buy_expected_winner_only"
        )
    if not (0.0 <= float(cfg.min_visible_fill_ratio) <= 1.0):
        raise ValueError("taker.competitiveness.min_visible_fill_ratio must be within [0, 1]")

    retired_stage_window_rows = source.get("stage_final_window_sec_by_stage", {})
    if retired_stage_window_rows is not None and not isinstance(retired_stage_window_rows, Mapping):
        raise ValueError("taker.competitiveness.stage_final_window_sec_by_stage must be a mapping")
    if retired_stage_window_rows:
        raise ValueError(
            "taker.competitiveness.stage_final_window_sec_by_stage is retired; use lifecycle.phase.taker_window_open_sec"
        )
    retired_stage_aggr_rows = source.get("stage_aggressiveness", {})
    if retired_stage_aggr_rows is not None and not isinstance(retired_stage_aggr_rows, Mapping):
        raise ValueError("taker.competitiveness.stage_aggressiveness must be a mapping")
    if retired_stage_aggr_rows:
        raise ValueError(
            "taker.competitiveness.stage_aggressiveness is retired for current configs"
        )
    if bool(source.get("stage_priority_enabled", False)):
        raise ValueError(
            "taker.competitiveness.stage_priority_enabled is retired for current configs"
        )


def build_taker_competitiveness_policy(
    row: Optional[Mapping[str, Any]],
    *,
    strict: bool = False,
) -> "TakerCompetitivenessConfig":
    return TakerCompetitivenessConfig.from_mapping(row, strict=strict)


@dataclasses.dataclass(frozen=True)
class TakerCandidate:
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
    predicted_dynamic_feasible_target_usd: Optional[float] = None
    predicted_dynamic_reject_reason: Optional[str] = None
    multi_oracle_confirmation: bool = False
    multi_oracle_boost_applied: bool = False
    multi_oracle_status: str = "disabled"
    multi_oracle_cap_usd: Optional[float] = None


@dataclasses.dataclass(frozen=True)
class TakerDecision:
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
    submit_capable_static: bool
    submit_capable_dynamic_predicted: Optional[bool]
    predicted_dynamic_feasible: Optional[bool]
    predicted_feasible_target_usd: Optional[float]
    predicted_reject_reason: Optional[str]
    preview_authority: str
    dynamic_size_capped_by_risk: bool
    multi_oracle_confirmation: bool
    multi_oracle_boost_eligible: bool
    multi_oracle_boost_applied: bool
    multi_oracle_status: str
    sec_to_expiry: Optional[float] = None
    normal_side_policy: str = "buy_expected_winner_only"
    normal_taker_side_class: str = "unknown"
    visible_fill_ratio: Optional[float] = None
    visible_fill_notional_usd: Optional[float] = None

    def as_event_payload(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            **lifecycle_phase_surface_fields(lifecycle_phase=legacy_stage_to_lifecycle_phase(self.stage)),
            "conviction_score": float(self.conviction_score),
            "edge_abs": float(self.edge_abs),
            "required_min_edge": float(self.required_min_edge),
            "timing_window_class": self.timing_window_class,
            "sec_to_expiry": (
                float(self.sec_to_expiry) if isinstance(self.sec_to_expiry, (int, float)) else None
            ),
            "aggressiveness_level": self.aggressiveness_level,
            "price_aggress_bps_applied": float(self.price_aggress_bps_applied),
            "target_usd_requested": float(self.target_usd_requested),
            "target_usd_resolved": float(self.target_usd_resolved),
            "hard_min_floor_applied": bool(self.hard_min_floor_applied),
            "hard_min_unachievable": bool(self.hard_min_unachievable),
            "submit_capable_static": bool(self.submit_capable_static),
            "submit_capable_dynamic_predicted": (
                bool(self.submit_capable_dynamic_predicted)
                if isinstance(self.submit_capable_dynamic_predicted, bool)
                else None
            ),
            "predicted_dynamic_feasible": (
                bool(self.predicted_dynamic_feasible)
                if isinstance(self.predicted_dynamic_feasible, bool)
                else None
            ),
            "predicted_feasible_target_usd": (
                float(self.predicted_feasible_target_usd)
                if isinstance(self.predicted_feasible_target_usd, (int, float))
                else None
            ),
            "predicted_reject_reason": (
                str(self.predicted_reject_reason).strip().lower()
                if str(self.predicted_reject_reason or "").strip()
                else None
            ),
            "preview_authority": str(self.preview_authority or "none").strip().lower() or "none",
            "dynamic_size_capped_by_risk": bool(self.dynamic_size_capped_by_risk),
            "multi_oracle_confirmation": bool(self.multi_oracle_confirmation),
            "multi_oracle_boost_eligible": bool(self.multi_oracle_boost_eligible),
            "multi_oracle_boost_applied": bool(self.multi_oracle_boost_applied),
            "multi_oracle_status": str(self.multi_oracle_status or "unknown"),
            "normal_side_policy": str(self.normal_side_policy or "buy_expected_winner_only"),
            "normal_taker_side_class": str(self.normal_taker_side_class or "unknown"),
            "visible_fill_ratio": (
                float(self.visible_fill_ratio) if isinstance(self.visible_fill_ratio, (int, float)) else None
            ),
            "visible_fill_notional_usd": (
                float(self.visible_fill_notional_usd)
                if isinstance(self.visible_fill_notional_usd, (int, float))
                else None
            ),
            "block_reason": self.block_reason,
            "side": self.side,
            "price": self.price,
            "should_submit": bool(self.should_submit),
        }

    def as_competitiveness_payload(self) -> Dict[str, Any]:
        return {
            **lifecycle_phase_surface_fields(lifecycle_phase=legacy_stage_to_lifecycle_phase(self.stage)),
            "conviction_score": float(self.conviction_score),
            "edge_abs": float(self.edge_abs),
            "required_min_edge": float(self.required_min_edge),
            "timing_window_class": self.timing_window_class,
            "sec_to_expiry": (
                float(self.sec_to_expiry) if isinstance(self.sec_to_expiry, (int, float)) else None
            ),
            "aggressiveness_level": self.aggressiveness_level,
            "price_aggress_bps_applied": float(self.price_aggress_bps_applied),
            "target_usd_requested": float(self.target_usd_requested),
            "target_usd_resolved": float(self.target_usd_resolved),
            "hard_min_floor_applied": bool(self.hard_min_floor_applied),
            "hard_min_unachievable": bool(self.hard_min_unachievable),
            "submit_capable_static": bool(self.submit_capable_static),
            "submit_capable_dynamic_predicted": (
                bool(self.submit_capable_dynamic_predicted)
                if isinstance(self.submit_capable_dynamic_predicted, bool)
                else None
            ),
            "predicted_dynamic_feasible": (
                bool(self.predicted_dynamic_feasible)
                if isinstance(self.predicted_dynamic_feasible, bool)
                else None
            ),
            "predicted_feasible_target_usd": (
                float(self.predicted_feasible_target_usd)
                if isinstance(self.predicted_feasible_target_usd, (int, float))
                else None
            ),
            "predicted_reject_reason": (
                str(self.predicted_reject_reason).strip().lower()
                if str(self.predicted_reject_reason or "").strip()
                else None
            ),
            "preview_authority": str(self.preview_authority or "none").strip().lower() or "none",
            "dynamic_size_capped_by_risk": bool(self.dynamic_size_capped_by_risk),
            "multi_oracle_confirmation": bool(self.multi_oracle_confirmation),
            "multi_oracle_boost_eligible": bool(self.multi_oracle_boost_eligible),
            "multi_oracle_boost_applied": bool(self.multi_oracle_boost_applied),
            "multi_oracle_status": str(self.multi_oracle_status or "unknown"),
            "normal_side_policy": str(self.normal_side_policy or "buy_expected_winner_only"),
            "normal_taker_side_class": str(self.normal_taker_side_class or "unknown"),
            "visible_fill_ratio": (
                float(self.visible_fill_ratio) if isinstance(self.visible_fill_ratio, (int, float)) else None
            ),
            "visible_fill_notional_usd": (
                float(self.visible_fill_notional_usd)
                if isinstance(self.visible_fill_notional_usd, (int, float))
                else None
            ),
        }


@dataclasses.dataclass(frozen=True)
class TakerBatchResult:
    decisions: List[TakerDecision]


class TakerCompetitivenessEngine:
    def __init__(self, cfg: TakerCompetitivenessConfig):
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

    def _effective_final_window_sec(self, stage: str) -> float:
        del stage
        return float(self.cfg.final_window_sec)

    def _timing_window_class(self, stage: str, sec_to_expiry: Optional[float]) -> str:
        del stage
        if not self.cfg.final_window_enabled:
            return "window_disabled"
        if not isinstance(sec_to_expiry, (int, float)):
            return "outside_window"
        sec = float(sec_to_expiry)
        final_window_sec = float(self.cfg.final_window_sec)
        if sec < 0.0 or sec > final_window_sec:
            return "outside_window"
        if abs(float(final_window_sec) - 15.0) <= 1e-9:
            return "final15"
        return "final_window"

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

    def _submit_candidate_sort_key(self, row: "TakerDecision") -> tuple[Any, ...]:
        # Canonical taker ordering is edge-first. Conviction, token-score, and
        # dynamic-preview plumbing remain emitted for diagnostic continuity only.
        return (
            -float(row.edge_abs),
            str(row.token_id),
        )

    def evaluate_batch(
        self,
        *,
        candidates: Sequence[TakerCandidate],
        max_orders_per_cycle: int,
    ) -> TakerBatchResult:
        decisions: List[TakerDecision] = []
        if not self.cfg.enabled:
            for candidate in candidates:
                decisions.append(
                    TakerDecision(
                        token_id=str(candidate.token_id),
                        stage=str(candidate.stage),
                        should_submit=False,
                        block_reason="taker_competitiveness_disabled",
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
                        multi_oracle_status="disabled",
                    )
                )
            return TakerBatchResult(decisions=decisions)

        provisional: List[TakerDecision] = []
        normal_side_policy = str(self.cfg.normal_side_policy or "buy_expected_winner_only").strip().lower()
        for candidate in candidates:
            token_id = str(candidate.token_id)
            stage = str(candidate.stage or "").strip().upper() or "UNKNOWN"
            sec_to_expiry_value = (
                float(candidate.sec_to_expiry) if isinstance(candidate.sec_to_expiry, (int, float)) else None
            )
            edge_signed = float(candidate.edge_value)
            edge_abs = abs(edge_signed)
            required_min_edge = max(0.0, float(candidate.required_min_edge))
            conviction_score = self._conviction(edge_abs=edge_abs, token_score=candidate.token_score)
            timing_window_class = self._timing_window_class(stage, candidate.sec_to_expiry)
            multi_oracle_status = str(candidate.multi_oracle_status or "unknown").strip().lower() or "unknown"
            multi_oracle_confirmation = bool(candidate.multi_oracle_confirmation)
            if multi_oracle_status == "unknown":
                multi_oracle_confirmation = False

            if edge_abs < required_min_edge:
                provisional.append(
                    TakerDecision(
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=None,
                        predicted_dynamic_feasible=None,
                        predicted_feasible_target_usd=None,
                        predicted_reject_reason=None,
                        preview_authority="none",
                        dynamic_size_capped_by_risk=False,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=False,
                        multi_oracle_boost_applied=False,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                    )
                )
                continue

            if timing_window_class == "outside_window":
                provisional.append(
                    TakerDecision(
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=None,
                        predicted_dynamic_feasible=None,
                        predicted_feasible_target_usd=None,
                        predicted_reject_reason=None,
                        preview_authority="none",
                        dynamic_size_capped_by_risk=False,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=False,
                        multi_oracle_boost_applied=False,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                    )
                )
                continue

            aggress_bps = 0.0
            if timing_window_class == "final10" and self.cfg.aggressive_window_enabled:
                aggress_bps = max(aggress_bps, aggress_bps * 1.25)
            aggress_bps = _clamp(aggress_bps, 0.0, float(self.cfg.price_aggress_bps_max))
            aggressiveness_level = "final10" if timing_window_class == "final10" else timing_window_class

            boost_window_sec = float(self.cfg.multi_oracle_boost_window_sec)
            boost_eligible = (
                bool(self.cfg.multi_oracle_boost_enabled)
                and multi_oracle_confirmation
                and edge_abs >= float(self.cfg.multi_oracle_edge_threshold_abs)
                and isinstance(sec_to_expiry_value, (int, float))
                and sec_to_expiry_value >= 0.0
                and sec_to_expiry_value <= boost_window_sec
            )
            boost_cap_override = None
            if boost_eligible:
                boost_cap_override = max(
                    float(self.cfg.dynamic_size_target_usd_cap),
                    float(self.cfg.multi_oracle_target_usd_cap),
                )
                if isinstance(candidate.multi_oracle_cap_usd, (int, float)):
                    boost_cap_override = min(boost_cap_override, max(0.0, float(candidate.multi_oracle_cap_usd)))

            target_usd_requested, floor_usd, hard_min_floor_applied = self._target_usd(
                base_target_usd=float(candidate.base_target_usd),
                conviction=conviction_score,
                stage_mult=1.0,
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
            predicted_feasible_target_usd = (
                float(candidate.predicted_dynamic_feasible_target_usd)
                if isinstance(candidate.predicted_dynamic_feasible_target_usd, (int, float))
                else None
            )
            predicted_dynamic_reject_reason = (
                str(candidate.predicted_dynamic_reject_reason).strip().lower()
                if str(candidate.predicted_dynamic_reject_reason or "").strip()
                else None
            )
            predicted_dynamic_feasible: Optional[bool] = None
            submit_capable_dynamic_predicted: Optional[bool] = None
            if self.cfg.dynamic_preview_enabled:
                if isinstance(predicted_feasible_target_usd, (int, float)):
                    predicted_dynamic_feasible = (
                        float(predicted_feasible_target_usd) + 1e-9 >= float(target_usd_requested)
                    )
                    submit_capable_dynamic_predicted = bool(predicted_dynamic_feasible)
                else:
                    predicted_dynamic_feasible = None
                    submit_capable_dynamic_predicted = None
            hard_min_unachievable = target_usd_resolved + 1e-9 < floor_usd
            if hard_min_unachievable and self.cfg.hard_min_enforcement == "skip_if_unachievable":
                provisional.append(
                    TakerDecision(
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=submit_capable_dynamic_predicted,
                        predicted_dynamic_feasible=predicted_dynamic_feasible,
                        predicted_feasible_target_usd=predicted_feasible_target_usd,
                        predicted_reject_reason=predicted_dynamic_reject_reason,
                        preview_authority=("advisory_read_only" if self.cfg.dynamic_preview_enabled else "none"),
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=boost_eligible,
                        multi_oracle_boost_applied=boost_eligible,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                    )
                )
                continue

            if edge_signed < 0.0 and normal_side_policy == "buy_expected_winner_only":
                provisional.append(
                    TakerDecision(
                        token_id=token_id,
                        stage=stage,
                        should_submit=False,
                        block_reason="normal_taker_same_token_sell_forbidden",
                        side="SELL",
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=submit_capable_dynamic_predicted,
                        predicted_dynamic_feasible=predicted_dynamic_feasible,
                        predicted_feasible_target_usd=predicted_feasible_target_usd,
                        predicted_reject_reason=predicted_dynamic_reject_reason,
                        preview_authority=("advisory_read_only" if self.cfg.dynamic_preview_enabled else "none"),
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=boost_eligible,
                        multi_oracle_boost_applied=boost_eligible,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                        normal_side_policy=normal_side_policy,
                        normal_taker_side_class="same_token_sell_blocked",
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
                    TakerDecision(
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=submit_capable_dynamic_predicted,
                        predicted_dynamic_feasible=predicted_dynamic_feasible,
                        predicted_feasible_target_usd=predicted_feasible_target_usd,
                        predicted_reject_reason=predicted_dynamic_reject_reason,
                        preview_authority=("advisory_read_only" if self.cfg.dynamic_preview_enabled else "none"),
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=boost_eligible,
                        multi_oracle_boost_applied=boost_eligible,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                    )
                )
                continue

            if side == "BUY":
                price = base_price * (1.0 + (aggress_bps / 10000.0))
            else:
                price = base_price * (1.0 - (aggress_bps / 10000.0))
            if not math.isfinite(price) or price <= 0.0 or price >= 1.0:
                provisional.append(
                    TakerDecision(
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
                        submit_capable_static=False,
                        submit_capable_dynamic_predicted=submit_capable_dynamic_predicted,
                        predicted_dynamic_feasible=predicted_dynamic_feasible,
                        predicted_feasible_target_usd=predicted_feasible_target_usd,
                        predicted_reject_reason=predicted_dynamic_reject_reason,
                        preview_authority=("advisory_read_only" if self.cfg.dynamic_preview_enabled else "none"),
                        dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                        multi_oracle_confirmation=multi_oracle_confirmation,
                        multi_oracle_boost_eligible=boost_eligible,
                        multi_oracle_boost_applied=boost_eligible,
                        multi_oracle_status=multi_oracle_status,
                        sec_to_expiry=sec_to_expiry_value,
                    )
                )
                continue

            provisional.append(
                TakerDecision(
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
                    submit_capable_static=True,
                    submit_capable_dynamic_predicted=submit_capable_dynamic_predicted,
                    predicted_dynamic_feasible=predicted_dynamic_feasible,
                    predicted_feasible_target_usd=predicted_feasible_target_usd,
                    predicted_reject_reason=predicted_dynamic_reject_reason,
                    preview_authority=("advisory_read_only" if self.cfg.dynamic_preview_enabled else "none"),
                    dynamic_size_capped_by_risk=dynamic_size_capped_by_risk,
                    multi_oracle_confirmation=multi_oracle_confirmation,
                    multi_oracle_boost_eligible=boost_eligible,
                    multi_oracle_boost_applied=boost_eligible,
                    multi_oracle_status=multi_oracle_status,
                    sec_to_expiry=sec_to_expiry_value,
                    normal_side_policy=normal_side_policy,
                    normal_taker_side_class="buy_expected_winner",
                )
            )

        submit_candidates = [row for row in provisional if row.should_submit]
        submit_candidates_sorted = sorted(
            submit_candidates,
            key=self._submit_candidate_sort_key,
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
        return TakerBatchResult(decisions=decisions)
