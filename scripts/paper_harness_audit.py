#!/usr/bin/env python3
"""Canonical paper harness audit for realism + integrity readiness."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Optional

import yaml

from prodesk.config import load_execution_config
from prodesk.jsonl_utils import load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.session_phase import enforce_validation_phase
from prodesk.runtime_semantics import (
    RUNTIME_CLASS_INVALID_DEADLOCK,
    RUNTIME_CLASS_INVALID_SAFETY,
    RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION,
)
from scripts.nightly_soak_report import build_report
from scripts.run_integrity_audit import run_audit as run_integrity_audit

DEFAULT_MAX_LINES_PER_FILE = 200000
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SOAK_BUDGET_PATH = (ROOT_DIR / "ops" / "soak_budget.yaml").resolve()
DEFAULT_REALISM_DOCTRINE_PATH = (ROOT_DIR / "BRO_PAPER_HARNESS_REALISM_DOCTRINE.txt").resolve()

DECISION_INPUT_TYPES = ("observed_live", "replayed", "emulated", "bounded_derived", "unknown")
EXECUTION_REALISM_CLASSES = ("authoritative", "bounded_approximation", "not_modeled")
TRUTH_QUALITY_BLOCK_REASONS = frozenset(
    {
        "stale_book",
        "oracle_unavailable_or_stale",
        "latency_not_armed",
        "latency_not_armed_for_maker",
        "maker_requires_ws_book_source",
        "taker_requires_ws_book_source",
        "token_lag_not_verified",
        "token_lag_not_verified_for_maker",
        "fair_probability_unavailable",
    }
)


def _decision_input_type_from_row(row: Dict[str, Any]) -> str:
    explicit = str(row.get("decision_input_type") or "").strip().lower()
    if explicit in DECISION_INPUT_TYPES:
        return explicit
    data_class = str(row.get("decision_input_data_class") or "").strip().lower()
    source = str(row.get("decision_input_source") or row.get("book_source") or "").strip().lower()
    emulated_flag = row.get("decision_input_emulated")
    if data_class == "emulated" or bool(emulated_flag):
        return "emulated"
    if data_class == "observed_live":
        if source == "rest":
            return "bounded_derived"
        return "observed_live"
    if data_class == "observed_other":
        if source in {"replay", "replayed"}:
            return "replayed"
        return "bounded_derived"
    if source in {"replay", "replayed"}:
        return "replayed"
    if source in {"paper", "simulated", "synthetic", "emulated"}:
        return "emulated"
    if source == "rest":
        return "bounded_derived"
    if source in {"ws", "chainlink"}:
        return "observed_live"
    return "unknown"


def _execution_realism_class_from_row(row: Dict[str, Any]) -> str:
    explicit = str(row.get("execution_realism_class") or "").strip().lower()
    if explicit in EXECUTION_REALISM_CLASSES:
        return explicit
    scope = str(row.get("evaluation_scope") or "").strip().lower()
    if scope == "maker":
        return "not_modeled"
    if scope == "taker":
        return "bounded_approximation"
    return "not_modeled"


def _status_files_exist(log_dir: pathlib.Path) -> bool:
    try:
        return any(log_dir.glob("status_*.jsonl"))
    except Exception:
        return False


def _load_market_data_realism_policy(budget_path: pathlib.Path) -> tuple[Dict[str, float], List[str]]:
    defaults = {
        "max_book_updates_rest_ratio": 0.35,
        "min_book_updates_ws_delta": 1.0,
        "min_book_updates_total_delta": 1.0,
    }
    findings: List[str] = []
    resolved = budget_path.resolve()
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        findings.append(f"paper_harness_budget_load_error:{exc.__class__.__name__}:{resolved}")
        return defaults, findings
    if not isinstance(payload, dict):
        findings.append(f"paper_harness_budget_invalid_root:{resolved}")
        return defaults, findings
    websocket_cfg = payload.get("websocket", {})
    if not isinstance(websocket_cfg, dict):
        findings.append(f"paper_harness_budget_websocket_invalid:{resolved}")
        return defaults, findings

    policy = dict(defaults)
    for key in ("max_book_updates_rest_ratio", "min_book_updates_ws_delta", "min_book_updates_total_delta"):
        raw = websocket_cfg.get(key)
        if raw is None:
            findings.append(f"paper_harness_budget_missing:{key}")
            continue
        try:
            value = float(raw)
        except Exception:
            findings.append(f"paper_harness_budget_invalid_value:{key}")
            continue
        if value < 0:
            findings.append(f"paper_harness_budget_negative_value:{key}")
            continue
        policy[key] = value
    return policy, findings


def _load_run_scoped_events(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    run_contract_path: Optional[pathlib.Path],
    session_phase: str,
    max_lines_per_file: int,
) -> tuple[List[Dict[str, Any]], str, List[str]]:
    findings: List[str] = []
    selected_run_id = str(run_id or "").strip()
    if not selected_run_id:
        return [], "", findings
    contract: Optional[Dict[str, Any]] = None
    resolved_contract_path = ""
    if run_contract_path is not None:
        try:
            contract = resolve_run_contract(
                log_dir=log_dir,
                run_id=selected_run_id,
                run_contract_path_override=run_contract_path,
                allow_open=(str(session_phase or "").strip() == "validate_active"),
            )
        except Exception as exc:
            findings.append(str(exc))
            contract = None
        if isinstance(contract, dict):
            resolved_contract_path = str(contract.get("_path") or "")
    event_paths = sorted(log_dir.glob("events_*.jsonl"))
    if isinstance(contract, dict):
        events_slice = run_contract_slice_path(contract, stream="events")
        if events_slice is not None:
            event_paths = [events_slice]
    rows: List[Dict[str, Any]] = []
    for row in load_jsonl(event_paths, max_lines_per_file=max(0, int(max_lines_per_file))):
        if str(row.get("run_id") or "").strip() != selected_run_id:
            continue
        rows.append(row)
    rows = apply_contract_bounds(rows, contract)
    return rows, resolved_contract_path, findings


def run_audit(
    *,
    config_path: pathlib.Path,
    log_dir: Optional[pathlib.Path],
    run_id: str,
    skip_run_integrity: bool,
    min_status_rows: int,
    max_status_age_sec: float,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    budget_path: Optional[pathlib.Path] = None,
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    normalized_phase = enforce_validation_phase(validation_name="paper_harness_audit", session_phase=session_phase)
    checks: Dict[str, Any] = {}

    cfg = load_execution_config(config_path.resolve())
    runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
    doctrine = cfg.get("doctrine", {}) if isinstance(cfg.get("doctrine"), dict) else {}
    profile = cfg.get("profile", {}) if isinstance(cfg.get("profile"), dict) else {}
    strategy = cfg.get("strategy", {}) if isinstance(cfg.get("strategy"), dict) else {}
    execution_quality = (
        strategy.get("execution_quality", {}) if isinstance(strategy.get("execution_quality"), dict) else {}
    )
    market_ws = (
        cfg.get("market_data", {}).get("ws", {})
        if isinstance(cfg.get("market_data", {}), dict)
        and isinstance(cfg.get("market_data", {}).get("ws", {}), dict)
        else {}
    )
    chainlink = cfg.get("chainlink", {}) if isinstance(cfg.get("chainlink"), dict) else {}

    mode = str(cfg.get("mode") or "").strip().lower()
    doctrine_mode = str(doctrine.get("mode") or "").strip().lower()
    checks["mode"] = mode
    checks["doctrine_mode"] = doctrine_mode
    checks["profile_name"] = str(profile.get("name") or "").strip()
    checks["profile_class"] = str(profile.get("class") or "").strip().lower()
    if checks["profile_name"] != "paper_universal":
        findings.append(f"paper_harness_profile_name_invalid:{checks['profile_name'] or 'missing'}")
    if mode != "paper":
        findings.append(f"paper_harness_mode_invalid:{mode}")
    if doctrine_mode != "canonical":
        findings.append(f"paper_harness_doctrine_mode_invalid:{doctrine_mode}")
    if checks["profile_class"] != "canonical":
        findings.append(f"paper_harness_profile_class_invalid:{checks['profile_class'] or 'missing'}")
    checks["paper_realism_doctrine_path"] = str(DEFAULT_REALISM_DOCTRINE_PATH)
    checks["paper_realism_doctrine_present"] = bool(DEFAULT_REALISM_DOCTRINE_PATH.exists())
    if not checks["paper_realism_doctrine_present"]:
        findings.append("paper_harness_realism_doctrine_missing")

    checks["chainlink_enabled"] = bool(chainlink.get("enabled", False))
    checks["book_ws_enabled"] = bool(market_ws.get("enabled", False))
    if not checks["chainlink_enabled"]:
        findings.append("paper_harness_chainlink_disabled")
    if not checks["book_ws_enabled"]:
        findings.append("paper_harness_book_ws_disabled")

    checks["log_book_top"] = bool(runtime.get("log_book_top", False))
    checks["log_leadlag_book_move"] = bool(runtime.get("log_leadlag_book_move", False))
    if not checks["log_book_top"]:
        findings.append("paper_harness_log_book_top_disabled")
    if not checks["log_leadlag_book_move"]:
        warnings.append("paper_harness_log_leadlag_book_move_disabled")

    checks["paper_passive_touch_fill_enabled"] = bool(runtime.get("paper_passive_touch_fill_enabled", False))
    checks["paper_passive_touch_fill_ratio"] = float(runtime.get("paper_passive_touch_fill_ratio", 0.0) or 0.0)
    checks["paper_passive_near_touch_band"] = float(runtime.get("paper_passive_near_touch_band", 0.0) or 0.0)
    checks["paper_passive_near_touch_fill_ratio"] = float(
        runtime.get("paper_passive_near_touch_fill_ratio", 0.0) or 0.0
    )
    checks["paper_background_fill_ratio"] = float(runtime.get("paper_background_fill_ratio", 0.0) or 0.0)
    queue_position_mode = "not_modeled"
    maker_realism_class = "not_modeled"
    maker_policy = {
        "synthetic_touch_fill": "enabled"
        if checks["paper_passive_touch_fill_enabled"] and checks["paper_passive_touch_fill_ratio"] > 0.0
        else "disabled",
        "near_touch_fill": "enabled"
        if checks["paper_passive_touch_fill_enabled"] and checks["paper_passive_near_touch_fill_ratio"] > 0.0
        else "disabled",
        "background_fill": "enabled"
        if checks["paper_passive_touch_fill_enabled"] and checks["paper_background_fill_ratio"] > 0.0
        else "disabled",
        "queue_position_mode": queue_position_mode,
        "maker_realism_class": maker_realism_class,
    }
    checks["maker_policy"] = maker_policy
    checks["taker_policy"] = {
        "price_basis": "best_touch",
        "size_basis": "observed_top_size",
        "latency_model": "none",
        "stale_view_risk": "disclosed_true",
        "taker_realism_class": "bounded_approximation",
    }
    checks["paper_claim_boundary"] = {
        "control_plane_truth": "authoritative",
        "lifecycle_truth": "authoritative",
        "decision_source_truth": "bounded_approximation",
        "action_source_truth": "bounded_approximation",
        "source_truth": "bounded_approximation",
        "source_truth_semantics": "legacy_alias_of_action_source_truth",
        "maker_fill_expectancy": maker_realism_class,
        "taker_fill_expectancy": "bounded_approximation",
        "live_pnl_equivalence": False,
    }
    checks["paper_execution_realism_summary"] = {
        "maker_realism_class": maker_realism_class,
        "taker_realism_class": "bounded_approximation",
        "queue_position_mode": queue_position_mode,
        "latency_model": "none",
        "stale_view_modeling": "disclosed_true",
    }
    if checks["paper_passive_touch_fill_enabled"]:
        findings.append("paper_harness_passive_touch_fill_enabled")
    if checks["paper_passive_touch_fill_ratio"] > 0:
        findings.append("paper_harness_touch_fill_ratio_positive")
    if checks["paper_passive_near_touch_fill_ratio"] > 0:
        findings.append("paper_harness_near_touch_fill_ratio_positive")
    if checks["paper_background_fill_ratio"] > 0:
        findings.append("paper_harness_background_fill_ratio_positive")

    checks["execution_quality_enabled"] = bool(execution_quality.get("enabled", False))
    checks["execution_quality_min_expected_fill_prob"] = float(execution_quality.get("min_expected_fill_prob", 0.0) or 0.0)
    checks["execution_quality_max_queue_ahead_size"] = float(execution_quality.get("max_queue_ahead_size", 0.0) or 0.0)
    if not checks["execution_quality_enabled"]:
        findings.append("paper_harness_execution_quality_disabled")
    if checks["execution_quality_max_queue_ahead_size"] <= 0:
        findings.append("paper_harness_max_queue_ahead_size_nonpositive")

    checks["doctrine_oracle_max_tick_age_sec"] = float(doctrine.get("oracle_max_tick_age_sec", 0.0) or 0.0)
    if checks["doctrine_oracle_max_tick_age_sec"] <= 0:
        findings.append("paper_harness_oracle_max_tick_age_nonpositive")
    elif checks["doctrine_oracle_max_tick_age_sec"] > 5.0:
        warnings.append(
            f"paper_harness_oracle_max_tick_age_high:{checks['doctrine_oracle_max_tick_age_sec']:.3f}>5.0"
        )

    resolved_budget_path = (budget_path or DEFAULT_SOAK_BUDGET_PATH).resolve()
    market_data_policy, policy_findings = _load_market_data_realism_policy(resolved_budget_path)
    findings.extend(policy_findings)
    checks["market_data_policy_source"] = str(resolved_budget_path)

    resolved_log_dir = log_dir.resolve() if log_dir is not None else pathlib.Path(str(cfg["storage"]["log_dir"])).resolve()
    integrity_result: Dict[str, Any] = {"skipped": True}
    if not skip_run_integrity:
        selected_run_id = str(run_id or "").strip()
        if not selected_run_id:
            findings.append("paper_harness_run_id_required_for_integrity")
            integrity_result = {"skipped": False, "ok": False, "findings": ["run_id_required"]}
        elif _status_files_exist(resolved_log_dir):
            integrity_result = run_integrity_audit(
                log_dir=resolved_log_dir,
                run_id=selected_run_id,
                min_status_rows=max(1, int(min_status_rows)),
                status_tail_lines=800,
                event_tail_lines=800,
                max_status_age_sec=max(1.0, float(max_status_age_sec)),
                run_contract_path=run_contract_path,
                session_phase=normalized_phase,
            )
            findings.extend(str(x) for x in integrity_result.get("findings", []))
            warnings.extend(str(x) for x in integrity_result.get("warnings", []))
        else:
            warnings.append(f"paper_harness_run_integrity_skipped_no_status:{resolved_log_dir}")
            integrity_result = {"skipped": True, "reason": "no_status_files"}
    checks["run_integrity"] = integrity_result

    selected_run_id = str(run_id or "").strip()
    runtime_classification: Dict[str, Any] = {}
    if selected_run_id and _status_files_exist(resolved_log_dir):
        runtime_report = build_report(
            resolved_log_dir,
            run_id=selected_run_id,
            auto_resolve_run_id=False,
            max_lines_per_file=max(0, int(max_lines_per_file)),
            run_contract_path=run_contract_path,
            session_phase=normalized_phase,
        )
        runtime_classification = runtime_report.get("runtime_classification", {})
        if not isinstance(runtime_classification, dict):
            runtime_classification = {}
        classification = str(runtime_classification.get("classification") or "").strip().upper()
        promotion_eligible = bool(runtime_classification.get("promotion_eligible", False))
        market_data_source = runtime_report.get("market_data_source", {})
        if not isinstance(market_data_source, dict):
            market_data_source = {}
        checks["market_data_source"] = market_data_source
        max_rest_ratio = float(market_data_policy.get("max_book_updates_rest_ratio", 0.35) or 0.35)
        min_ws_updates = float(market_data_policy.get("min_book_updates_ws_delta", 1.0) or 1.0)
        min_total_updates = float(market_data_policy.get("min_book_updates_total_delta", 1.0) or 1.0)
        ws_delta = float(market_data_source.get("book_updates_ws_delta", 0.0) or 0.0)
        rest_ratio = float(market_data_source.get("book_updates_rest_ratio", 0.0) or 0.0)
        total_delta = float(market_data_source.get("book_updates_total_delta", 0.0) or 0.0)
        checks["paper_max_rest_book_updates_ratio"] = max_rest_ratio
        checks["paper_min_ws_book_updates_delta"] = min_ws_updates
        checks["paper_min_total_book_updates_delta"] = min_total_updates
        if total_delta < min_total_updates:
            findings.append(f"paper_harness_book_updates_total_too_low:{total_delta:.6f}<min:{min_total_updates:.6f}")
        if ws_delta < min_ws_updates:
            findings.append(f"paper_harness_book_updates_ws_too_low:{ws_delta:.6f}<min:{min_ws_updates:.6f}")
        if rest_ratio > max_rest_ratio:
            findings.append(f"paper_harness_book_updates_rest_ratio_high:{rest_ratio:.6f}>max:{max_rest_ratio:.6f}")
        if classification in {RUNTIME_CLASS_INVALID_DEADLOCK, RUNTIME_CLASS_INVALID_SAFETY}:
            findings.append(f"paper_harness_runtime_invalid:{classification}")
        if not promotion_eligible:
            if classification == RUNTIME_CLASS_NON_PROMOTABLE_NO_PARTICIPATION:
                findings.append(f"paper_harness_runtime_non_promotable:{classification}")
            elif classification:
                findings.append(f"paper_harness_runtime_non_promotable:{classification}")
            else:
                findings.append("paper_harness_runtime_non_promotable:UNKNOWN")
    checks["runtime_classification"] = runtime_classification

    if selected_run_id:
        event_rows, resolved_event_contract_path, event_findings = _load_run_scoped_events(
            log_dir=resolved_log_dir,
            run_id=selected_run_id,
            run_contract_path=run_contract_path,
            session_phase=normalized_phase,
            max_lines_per_file=max_lines_per_file,
        )
        if resolved_event_contract_path and not str(run_contract_path or "").strip():
            checks["resolved_event_contract_path"] = resolved_event_contract_path
        findings.extend(event_findings)
        edge_rows = [row for row in event_rows if str(row.get("event_type") or "").strip() == "edge_evaluation"]
        fill_rows = [row for row in event_rows if str(row.get("event_type") or "").strip() == "fill"]
        submit_rows = [row for row in event_rows if str(row.get("event_type") or "").strip() == "order_submit"]
        ws_slo_rows = [row for row in event_rows if str(row.get("event_type") or "").strip() == "ws_slo_state"]
        checks["edge_evaluation_rows"] = int(len(edge_rows))
        checks["fill_rows"] = int(len(fill_rows))
        checks["order_submit_rows"] = int(len(submit_rows))
        checks["ws_slo_rows"] = int(len(ws_slo_rows))

        missing_disclosure_rows = 0
        missing_decision_input_type_rows = 0
        missing_execution_realism_class_rows = 0
        action_on_emulated_rows = 0
        action_on_non_observed_live_rows = 0
        no_action_due_truth_quality_rows = 0
        actions_under_bounded_approx_rows = 0
        decision_input_type_counts: Dict[str, int] = {key: 0 for key in DECISION_INPUT_TYPES}
        action_counts_by_input_type: Dict[str, int] = {key: 0 for key in DECISION_INPUT_TYPES}
        execution_realism_class_counts: Dict[str, int] = {key: 0 for key in EXECUTION_REALISM_CLASSES}
        allow_action_on_emulated = bool(runtime.get("paper_allow_action_on_emulated_input", False))
        for row in edge_rows:
            decision_input_emulated = row.get("decision_input_emulated")
            decision_input_data_class = str(row.get("decision_input_data_class") or "").strip()
            action_taken = str(row.get("action_taken") or "").strip().lower()
            block_reason = str(row.get("block_reason") or "").strip()
            explicit_input_type = str(row.get("decision_input_type") or "").strip().lower()
            explicit_realism = str(row.get("execution_realism_class") or "").strip().lower()
            normalized_input_type = _decision_input_type_from_row(row)
            normalized_realism = _execution_realism_class_from_row(row)
            decision_input_type_counts[normalized_input_type] = int(decision_input_type_counts.get(normalized_input_type, 0)) + 1
            execution_realism_class_counts[normalized_realism] = int(
                execution_realism_class_counts.get(normalized_realism, 0)
            ) + 1
            if action_taken in {"maker", "taker"}:
                action_counts_by_input_type[normalized_input_type] = int(
                    action_counts_by_input_type.get(normalized_input_type, 0)
                ) + 1
                if normalized_input_type == "emulated":
                    action_on_emulated_rows += 1
                if normalized_input_type != "observed_live":
                    action_on_non_observed_live_rows += 1
                if normalized_realism == "bounded_approximation":
                    actions_under_bounded_approx_rows += 1
            elif action_taken == "none" and block_reason in TRUTH_QUALITY_BLOCK_REASONS:
                no_action_due_truth_quality_rows += 1
            if not isinstance(decision_input_emulated, bool):
                missing_disclosure_rows += 1
            if not decision_input_data_class:
                missing_disclosure_rows += 1
            if explicit_input_type not in DECISION_INPUT_TYPES:
                missing_decision_input_type_rows += 1
            if explicit_realism not in EXECUTION_REALISM_CLASSES:
                missing_execution_realism_class_rows += 1

        order_submit_by_id: Dict[str, Dict[str, Any]] = {}
        for row in submit_rows:
            order_id = str(row.get("order_id") or "").strip()
            if order_id:
                order_submit_by_id.setdefault(order_id, row)
        immediate_fill_rows = 0
        passive_fill_rows = 0
        unknown_fill_rows = 0
        degraded_fill_rows = 0
        missing_fill_policy_basis_rows = 0
        fill_policy_basis_counts: Dict[str, int] = {}
        immediate_fill_policy_basis_counts: Dict[str, int] = {}
        passive_fill_policy_basis_counts: Dict[str, int] = {}
        for row in fill_rows:
            order_id = str(row.get("order_id") or "").strip()
            submit_row = order_submit_by_id.get(order_id, {})
            reason_code = str(submit_row.get("reason_code") or "").strip().lower()
            execution_preference = str(submit_row.get("execution_preference") or "").strip().lower()
            fill_basis = str(row.get("fill_policy_basis") or "").strip().lower()
            decision_input_type = str(row.get("decision_input_type") or "").strip().lower()
            if not fill_basis:
                fill_basis = "missing"
                missing_fill_policy_basis_rows += 1
            fill_policy_basis_counts[fill_basis] = int(fill_policy_basis_counts.get(fill_basis, 0)) + 1
            if execution_preference == "taker_only" or reason_code.startswith("sniper_taker"):
                immediate_fill_rows += 1
                immediate_fill_policy_basis_counts[fill_basis] = int(
                    immediate_fill_policy_basis_counts.get(fill_basis, 0)
                ) + 1
            elif execution_preference == "maker_preferred" or reason_code.startswith("mm_"):
                passive_fill_rows += 1
                passive_fill_policy_basis_counts[fill_basis] = int(passive_fill_policy_basis_counts.get(fill_basis, 0)) + 1
            else:
                unknown_fill_rows += 1
            if decision_input_type in {"emulated", "replayed", "bounded_derived", "unknown"}:
                degraded_fill_rows += 1

        synthetic_passive_fills_possible = any(
            checks.get(key, 0.0) > 0.0
            for key in (
                "paper_passive_touch_fill_ratio",
                "paper_passive_near_touch_fill_ratio",
                "paper_background_fill_ratio",
            )
        )
        synthetic_passive_fill_basis_rows = sum(
            int(fill_policy_basis_counts.get(name, 0))
            for name in ("synthetic_touch_fill", "synthetic_near_touch_fill", "synthetic_background_fill")
        )
        synthetic_passive_fills_used = synthetic_passive_fill_basis_rows > 0
        immediate_fills_bounded_visible_only = (
            immediate_fill_rows == 0
            or set(immediate_fill_policy_basis_counts.keys()).issubset({"bounded_visible_liquidity_top_of_book"})
        )
        checks["edge_decision_input_type_counts"] = dict(decision_input_type_counts)
        checks["edge_action_counts_by_input_type"] = dict(action_counts_by_input_type)
        checks["edge_execution_realism_class_counts"] = dict(execution_realism_class_counts)
        checks["edge_action_on_emulated_input_allowed"] = bool(allow_action_on_emulated)
        checks["edge_decision_input_missing_disclosure_rows"] = int(missing_disclosure_rows)
        checks["edge_decision_input_type_missing_rows"] = int(missing_decision_input_type_rows)
        checks["edge_execution_realism_class_missing_rows"] = int(missing_execution_realism_class_rows)
        checks["edge_action_on_emulated_input_rows"] = int(action_on_emulated_rows)
        checks["edge_action_on_non_observed_live_rows"] = int(action_on_non_observed_live_rows)
        checks["paper_fill_policy_truth"] = {
            "synthetic_passive_fills_possible": bool(synthetic_passive_fills_possible),
            "synthetic_passive_fills_used": bool(synthetic_passive_fills_used),
            "missing_fill_policy_basis_rows": int(missing_fill_policy_basis_rows),
            "fill_policy_basis_counts": dict(sorted(fill_policy_basis_counts.items())),
            "immediate_fill_rows": int(immediate_fill_rows),
            "passive_fill_rows": int(passive_fill_rows),
            "unknown_fill_rows": int(unknown_fill_rows),
            "immediate_fill_policy_basis_counts": dict(sorted(immediate_fill_policy_basis_counts.items())),
            "passive_fill_policy_basis_counts": dict(sorted(passive_fill_policy_basis_counts.items())),
            "immediate_fills_bounded_visible_liquidity_only": bool(immediate_fills_bounded_visible_only),
            "fills_under_degraded_data_truth_rows": int(degraded_fill_rows),
        }
        checks["paper_constraint_behavior"] = {
            "no_action_due_truth_quality_rows": int(no_action_due_truth_quality_rows),
            "actions_allowed_under_bounded_approximation_rows": int(actions_under_bounded_approx_rows),
            "actions_blocked_by_truth_quality_policy_rows": int(no_action_due_truth_quality_rows),
        }
        decision_source_truth = (
            "authoritative"
            if (
                decision_input_type_counts.get("emulated", 0) == 0
                and decision_input_type_counts.get("replayed", 0) == 0
                and decision_input_type_counts.get("bounded_derived", 0) == 0
                and decision_input_type_counts.get("unknown", 0) == 0
            )
            else "bounded_approximation"
        )
        action_source_truth = (
            "authoritative"
            if (
                action_counts_by_input_type.get("emulated", 0) == 0
                and action_counts_by_input_type.get("replayed", 0) == 0
                and action_counts_by_input_type.get("bounded_derived", 0) == 0
                and action_counts_by_input_type.get("unknown", 0) == 0
            )
            else "bounded_approximation"
        )
        checks["paper_claim_boundary"] = {
            "control_plane_truth": "authoritative",
            "lifecycle_truth": "authoritative",
            "decision_source_truth": decision_source_truth,
            "action_source_truth": action_source_truth,
            "source_truth": action_source_truth,
            "source_truth_semantics": "legacy_alias_of_action_source_truth",
            "maker_fill_expectancy": str(checks.get("maker_policy", {}).get("maker_realism_class") or "not_modeled"),
            "taker_fill_expectancy": str(checks.get("taker_policy", {}).get("taker_realism_class") or "bounded_approximation"),
            "live_pnl_equivalence": False,
        }
        checks["paper_execution_realism_summary"] = {
            "maker_realism_class": str(checks.get("maker_policy", {}).get("maker_realism_class") or "not_modeled"),
            "taker_realism_class": str(checks.get("taker_policy", {}).get("taker_realism_class") or "bounded_approximation"),
            "queue_position_mode": str(checks.get("maker_policy", {}).get("queue_position_mode") or "not_modeled"),
            "latency_model": str(checks.get("taker_policy", {}).get("latency_model") or "none"),
            "stale_view_modeling": str(checks.get("taker_policy", {}).get("stale_view_risk") or "disclosed_true"),
        }
        checks["paper_source_truth_counts"] = {
            key: int(decision_input_type_counts.get(key, 0)) for key in DECISION_INPUT_TYPES
        }
        checks["paper_source_degradation_state"] = {
            "ws_slo_degraded_rows": int(
                sum(1 for row in ws_slo_rows if bool(row.get("degraded", False)))
            ),
            "bounded_or_emulated_action_rows": int(action_on_non_observed_live_rows),
        }

        if missing_disclosure_rows > 0:
            findings.append(f"paper_harness_edge_decision_input_disclosure_missing:{missing_disclosure_rows}")
        if missing_decision_input_type_rows > 0:
            findings.append(f"paper_harness_edge_decision_input_type_missing:{missing_decision_input_type_rows}")
        if missing_execution_realism_class_rows > 0:
            findings.append(
                f"paper_harness_edge_execution_realism_class_missing:{missing_execution_realism_class_rows}"
            )
        if action_on_emulated_rows > 0 and not allow_action_on_emulated:
            findings.append(f"paper_harness_edge_action_on_emulated_input:{action_on_emulated_rows}")
        if action_on_emulated_rows > 0 and allow_action_on_emulated:
            warnings.append(f"paper_harness_edge_action_on_emulated_input_allowed:{action_on_emulated_rows}")
        if missing_fill_policy_basis_rows > 0:
            findings.append(f"paper_harness_fill_policy_disclosure_missing:{missing_fill_policy_basis_rows}")
        if (not synthetic_passive_fills_possible) and synthetic_passive_fills_used:
            findings.append("paper_harness_synthetic_passive_fill_observed_while_disabled")
        if not immediate_fills_bounded_visible_only:
            findings.append("paper_harness_immediate_fill_policy_not_bounded_visible_liquidity")

    cfg_meta = cfg.get("_meta", {}) if isinstance(cfg.get("_meta"), dict) else {}
    return {
        "config_path": str(config_path.resolve()),
        "session_phase": normalized_phase,
        "run_contract_path": str(run_contract_path.resolve()) if isinstance(run_contract_path, pathlib.Path) else "",
        "profile_name": str(cfg.get("profile", {}).get("name", "")),
        "config_fingerprint_sha256": str(cfg_meta.get("effective_config_sha256", "")),
        "log_dir": str(resolved_log_dir),
        "checks": checks,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "findings": findings,
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro canonical paper harness audit")
    parser.add_argument("--config", default="configs/profiles/paper_universal.yaml", help="Execution config path")
    parser.add_argument("--log-dir", default="", help="Execution log directory (default: config storage.log_dir)")
    parser.add_argument("--run-id", default="", help="Optional explicit run_id for run-integrity check")
    parser.add_argument("--skip-run-integrity", action="store_true", help="Skip run integrity checks over logs")
    parser.add_argument("--min-status-rows", type=int, default=1, help="Minimum required status rows for run integrity")
    parser.add_argument("--max-status-age-sec", type=float, default=3153600000.0, help="Max status staleness for run integrity")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file for runtime classification; set 0 for full-file scans",
    )
    parser.add_argument(
        "--run-contract",
        default="",
        help="Optional run contract JSON path for deterministic replay",
    )
    parser.add_argument(
        "--budget",
        default="ops/soak_budget.yaml",
        help="Soak budget policy path used as market-data realism threshold source",
    )
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_postrun)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).resolve() if str(args.log_dir).strip() else None
    result = run_audit(
        config_path=pathlib.Path(args.config),
        log_dir=log_dir,
        run_id=str(args.run_id),
        skip_run_integrity=bool(args.skip_run_integrity),
        min_status_rows=max(1, int(args.min_status_rows)),
        max_status_age_sec=max(1.0, float(args.max_status_age_sec)),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        budget_path=pathlib.Path(str(args.budget)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
