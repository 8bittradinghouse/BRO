#!/usr/bin/env python3
"""Fail-closed canonical edge-truth validator."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from prodesk.config import load_execution_config
from prodesk.edge_truth_contract import (
    EDGE_ACTION_MAKER,
    EDGE_ACTION_NONE,
    EDGE_ACTION_TAKER,
    EDGE_ACTIONS,
    EDGE_BLOCK_REASONS,
    EDGE_EVAL_SCOPE_MAKER,
    EDGE_EVAL_SCOPE_TAKER,
    EDGE_EVAL_SCOPES,
    EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD,
    EDGE_LIFECYCLE_PHASE_FIELD,
    EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD,
    EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD,
    EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD,
    LIFECYCLE_PHASES,
    EDGE_MAKER_GATE_OPEN_FIELD,
    EDGE_MAKER_PHASE_ALLOWED_FIELD,
    EDGE_TAKER_GATE_OPEN_FIELD,
    EDGE_TAKER_PHASE_ALLOWED_FIELD,
    EdgeInputSnapshot,
    compute_edge_value,
    is_canonical_block_reason,
    normalize_edge_action,
    normalize_edge_scope,
    phase_allows_action,
    phase_policy,
    validate_edge_inputs,
)
from prodesk.historical_recovery_replay_compat import (
    HISTORICAL_PREEXPIRY_REDUCE_ONLY_ACTIVE_FIELD as HISTORICAL_PREEXPIRY_REDUCE_ONLY_ACTIVE_FIELD,
    HISTORICAL_RECOVERY_ACTIVE_FIELD as HISTORICAL_RECOVERY_ACTIVE_FIELD,
    HISTORICAL_RECOVERY_REASON_FIELD as HISTORICAL_RECOVERY_REASON_FIELD,
)
from prodesk.jsonl_utils import load_jsonl
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.session_phase import enforce_validation_phase

DEFAULT_MAX_LINES_PER_FILE = 50000

REQUIRED_FIELDS = (
    "run_id",
    "token_id",
    "timestamp_utc",
    EDGE_LIFECYCLE_PHASE_FIELD,
    "time_remaining_sec",
    "fair_probability",
    "market_probability",
    "edge_value",
    "oracle_tick_age_sec",
    EDGE_MAKER_PHASE_ALLOWED_FIELD,
    EDGE_TAKER_PHASE_ALLOWED_FIELD,
    EDGE_MAKER_GATE_OPEN_FIELD,
    EDGE_TAKER_GATE_OPEN_FIELD,
    "action_taken",
    "submitted",
    "filled",
    "evaluation_scope",
    "cycle_index",
)

AUDIT_RULE_SET = (
    "required_fields_present",
    "required_types_and_ranges",
    "scope_and_action_vocabulary",
    "row_run_id_matches_selected_run",
    "eligibility_matches_phase_contract",
    "action_requires_open_phase_gate",
    "no_action_requires_canonical_block_reason",
    "action_requires_submission",
    "scope_action_consistency",
    "invalid_edge_inputs_must_not_act",
    "result_must_be_null",
    "filled_requires_submitted",
    "edge_value_consistency",
    "opportunity_identity_and_uniqueness",
)
def _sha256_json_payload(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _is_prob(value: Any) -> bool:
    parsed = _safe_float(value)
    if parsed is None:
        return False
    return 0.0 <= float(parsed) <= 1.0


def _filter_rows_by_run_id(rows: List[Dict[str, Any]], run_id: str) -> List[Dict[str, Any]]:
    target = str(run_id or "").strip()
    if not target:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("run_id") or "").strip() == target:
            out.append(row)
    return out


def _load_event_rows(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    run_contract_path: Optional[pathlib.Path],
    max_lines_per_file: int,
) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    if run_contract_path is None:
        raise ValueError("edge_truth_run_contract_required")
    contract = resolve_run_contract(
        log_dir=log_dir,
        run_id=run_id,
        run_contract_path_override=run_contract_path,
        allow_open=False,
    )
    if contract is None:
        raise ValueError("edge_truth_run_contract_missing")
    slice_path = run_contract_slice_path(contract, stream="events")
    if slice_path is None:
        raise ValueError("edge_truth_events_slice_missing")
    events_files = [slice_path]
    rows = load_jsonl(events_files, max_lines_per_file=max(0, int(max_lines_per_file)))
    bounded = apply_contract_bounds(rows, contract)
    return bounded, str(contract.get("_path") or ""), contract


def _row_key(row: Dict[str, Any]) -> str:
    # Use a full-row canonical fingerprint for duplicate detection.
    # Token IDs may be redacted in event logs, so coarse opportunity keys
    # can create false positives across distinct opportunities.
    fingerprint = {
        "run_id": str(row.get("run_id") or "").strip(),
        "token_id": str(row.get("token_id") or "").strip(),
        "target_ref": str(row.get("target_ref") or "").strip(),
        "source_token_id": str(row.get("source_token_id") or "").strip(),
        "source_target_ref": str(row.get("source_target_ref") or "").strip(),
        "cycle_index": row.get("cycle_index"),
        "evaluation_scope": str(row.get("evaluation_scope") or "").strip().lower(),
        "timestamp_utc": str(row.get("timestamp_utc") or "").strip(),
        EDGE_LIFECYCLE_PHASE_FIELD: str(row.get(EDGE_LIFECYCLE_PHASE_FIELD) or "").strip().lower(),
        "time_remaining_sec": _safe_float(row.get("time_remaining_sec")),
        "fair_probability": _safe_float(row.get("fair_probability")),
        "market_probability": _safe_float(row.get("market_probability")),
        "edge_value": _safe_float(row.get("edge_value")),
        "oracle_tick_age_sec": _safe_float(row.get("oracle_tick_age_sec")),
        EDGE_MAKER_PHASE_ALLOWED_FIELD: row.get(EDGE_MAKER_PHASE_ALLOWED_FIELD),
        EDGE_TAKER_PHASE_ALLOWED_FIELD: row.get(EDGE_TAKER_PHASE_ALLOWED_FIELD),
        EDGE_MAKER_GATE_OPEN_FIELD: row.get(EDGE_MAKER_GATE_OPEN_FIELD),
        EDGE_TAKER_GATE_OPEN_FIELD: row.get(EDGE_TAKER_GATE_OPEN_FIELD),
        "action_taken": str(row.get("action_taken") or "").strip().lower(),
        "block_reason": str(row.get("block_reason") or "").strip().lower(),
        "submitted": row.get("submitted"),
        "filled": row.get("filled"),
        "order_id": str(row.get("order_id") or "").strip(),
    }
    return json.dumps(
        fingerprint,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _opportunity_key(
    *,
    run_id: str,
    identity: str,
    cycle_index: Optional[int],
    evaluation_scope: str,
) -> str:
    payload = {
        "run_id": str(run_id or "").strip(),
        "identity": str(identity or "").strip(),
        "cycle_index": (int(cycle_index) if isinstance(cycle_index, int) else None),
        "evaluation_scope": str(evaluation_scope or "").strip().lower(),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        try:
            return int(text)
        except (TypeError, ValueError):
            return None
    return None


def _has_lifecycle_residue_truth(row: Dict[str, Any]) -> bool:
    return any(
        bool(row.get(field))
        for field in (
            EDGE_LIFECYCLE_OPEN_ORDER_CLEANUP_REQUIRED_FIELD,
            EDGE_LIFECYCLE_SETTLEMENT_HOLD_REQUIRED_FIELD,
            EDGE_LIFECYCLE_UNRESOLVED_OBLIGATION_FIELD,
            EDGE_LIFECYCLE_CANCEL_FAIL_CLOSED_FIELD,
        )
    )


def _has_historical_lifecycle_lineage(row: Dict[str, Any]) -> bool:
    if bool(row.get(HISTORICAL_RECOVERY_ACTIVE_FIELD, False)):
        return True
    if bool(row.get(HISTORICAL_PREEXPIRY_REDUCE_ONLY_ACTIVE_FIELD, False)):
        return True
    if str(row.get(HISTORICAL_RECOVERY_REASON_FIELD) or "").strip():
        return True
    return False


def _opportunity_identity(row: Dict[str, Any]) -> Optional[str]:
    source_token_id = str(row.get("source_token_id") or "").strip()
    if source_token_id and source_token_id != "[REDACTED]":
        return f"source_token_id:{source_token_id}"
    source_target_ref = str(row.get("source_target_ref") or "").strip()
    if source_target_ref:
        return f"source_target_ref:{source_target_ref}"
    token_id = str(row.get("token_id") or "").strip()
    if token_id and token_id != "[REDACTED]":
        return f"token_id:{token_id}"
    target_ref = str(row.get("target_ref") or "").strip()
    if target_ref:
        return f"target_ref:{target_ref}"
    return None


def _status_indicates_scan_phase(
    *,
    contract: Dict[str, Any],
    max_lines_per_file: int,
) -> bool:
    status_slice = run_contract_slice_path(contract, stream="status")
    if status_slice is None:
        return False
    rows = load_jsonl([status_slice], max_lines_per_file=max(0, int(max_lines_per_file)))
    bounded_rows = apply_contract_bounds(rows, contract)
    if not bounded_rows:
        return False
    saw_scan_phase = False
    for row in bounded_rows:
        if not isinstance(row, dict):
            continue
        lifecycle_phase = str(row.get("lifecycle_phase") or "").strip().lower()
        if lifecycle_phase:
            if lifecycle_phase != "scan":
                return False
        else:
            return False
        saw_scan_phase = True
        target_count = _coerce_int(row.get("target_count"))
        if target_count is not None and int(target_count) != 0:
            return False
        kill_switch = _coerce_bool(row.get("kill_switch"))
        guard_active = _coerce_bool(row.get("external_guard_active"))
        if kill_switch is True or guard_active is True:
            return False
    return saw_scan_phase


def run_audit(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    config_path: pathlib.Path,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="edge_truth_audit", session_phase=session_phase)
    selected_run_id = str(run_id or "").strip()
    findings: List[str] = []
    if not selected_run_id:
        findings.append("edge_truth_run_id_required")
        return {
            "ok": False,
            "finding_count": len(findings),
            "findings": findings,
            "run_id": "",
            "log_dir": str(log_dir.resolve()),
            "session_phase": normalized_phase,
        }
    if run_contract_path is None:
        findings.append("edge_truth_run_contract_required")
    if int(max_lines_per_file) > 0:
        findings.append("edge_truth_max_lines_must_be_zero_for_authoritative_scan")

    cfg = load_execution_config(config_path.resolve())
    doctrine_cfg = cfg.get("doctrine", {}) if isinstance(cfg.get("doctrine"), dict) else {}
    oracle_max_tick_age_sec = float(doctrine_cfg.get("oracle_max_tick_age_sec", 1.5))

    all_rows: List[Dict[str, Any]] = []
    resolved_contract_path = ""
    contract: Dict[str, Any] = {}
    if not findings:
        try:
            all_rows, resolved_contract_path, contract = _load_event_rows(
                log_dir=log_dir.resolve(),
                run_id=selected_run_id,
                run_contract_path=run_contract_path,
                max_lines_per_file=max_lines_per_file,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            findings.append(str(exc))
            all_rows = []
            contract = {}
    edge_rows = [
        row
        for row in all_rows
        if isinstance(row, dict) and str(row.get("event_type") or "").strip() == "edge_evaluation"
    ]
    if not edge_rows:
        scan_only = False
        if contract:
            scan_only = _status_indicates_scan_phase(
                contract=contract,
                max_lines_per_file=max_lines_per_file,
            )
        if not scan_only:
            findings.append("edge_truth_rows_missing")

    seen_keys: set[str] = set()
    seen_opportunity_keys: set[str] = set()
    canonical_rows: List[Dict[str, Any]] = []
    maker_rows = 0
    taker_rows = 0
    action_rows = 0
    blocked_rows = 0
    opportunity_identity_unverifiable_rows = 0

    for idx, row in enumerate(edge_rows):
        row_prefix = f"edge_row[{idx}]"
        key = _row_key(row)
        if key in seen_keys:
            findings.append(f"edge_non_deterministic_duplicate_key:{key}")
        seen_keys.add(key)

        for field in REQUIRED_FIELDS:
            if field not in row:
                findings.append(f"{row_prefix}_missing_field:{field}")

        scope = normalize_edge_scope(row.get("evaluation_scope"))
        action = normalize_edge_action(row.get("action_taken"))
        row_run_id = str(row.get("run_id") or "").strip()
        token_id = str(row.get("token_id") or "").strip()
        lifecycle_phase = str(row.get(EDGE_LIFECYCLE_PHASE_FIELD) or "").strip().lower()
        timestamp_utc = str(row.get("timestamp_utc") or "").strip()

        if not token_id:
            findings.append(f"{row_prefix}_token_id_missing")
        if not timestamp_utc:
            findings.append(f"{row_prefix}_timestamp_utc_missing")
        if row_run_id != selected_run_id:
            findings.append(f"{row_prefix}_run_id_mismatch:{row_run_id or 'missing'}")
        if scope not in EDGE_EVAL_SCOPES:
            findings.append(f"{row_prefix}_evaluation_scope_invalid:{scope or 'missing'}")
        if action not in EDGE_ACTIONS:
            findings.append(f"{row_prefix}_action_taken_invalid:{action or 'missing'}")

        maker_phase_allowed = row.get(EDGE_MAKER_PHASE_ALLOWED_FIELD)
        taker_phase_allowed = row.get(EDGE_TAKER_PHASE_ALLOWED_FIELD)
        maker_gate_open = row.get(EDGE_MAKER_GATE_OPEN_FIELD)
        taker_gate_open = row.get(EDGE_TAKER_GATE_OPEN_FIELD)
        lifecycle_residue_truth_active = _has_lifecycle_residue_truth(row)
        historical_lifecycle_lineage_active = _has_historical_lifecycle_lineage(row)
        submitted = row.get("submitted")
        filled = row.get("filled")
        if not lifecycle_phase:
            findings.append(f"{row_prefix}_lifecycle_phase_missing")
        if not isinstance(maker_phase_allowed, bool):
            findings.append(f"{row_prefix}_maker_phase_allowed_not_bool")
        if not isinstance(taker_phase_allowed, bool):
            findings.append(f"{row_prefix}_taker_phase_allowed_not_bool")
        if not isinstance(maker_gate_open, bool):
            findings.append(f"{row_prefix}_maker_gate_open_not_bool")
        if not isinstance(taker_gate_open, bool):
            findings.append(f"{row_prefix}_taker_gate_open_not_bool")
        if not isinstance(submitted, bool):
            findings.append(f"{row_prefix}_submitted_not_bool")
        if not isinstance(filled, bool):
            findings.append(f"{row_prefix}_filled_not_bool")
        expected_maker_phase_allowed, expected_taker_phase_allowed = phase_policy(lifecycle_phase)
        if (
            isinstance(maker_phase_allowed, bool)
            and (not historical_lifecycle_lineage_active)
            and bool(maker_phase_allowed) != bool(expected_maker_phase_allowed)
        ):
            findings.append(
                f"{row_prefix}_maker_phase_allowed_mismatch:{lifecycle_phase}:{maker_phase_allowed}:{expected_maker_phase_allowed}"
            )
        if (
            isinstance(taker_phase_allowed, bool)
            and (not historical_lifecycle_lineage_active)
            and bool(taker_phase_allowed) != bool(expected_taker_phase_allowed)
        ):
            findings.append(
                f"{row_prefix}_taker_phase_allowed_mismatch:{lifecycle_phase}:{taker_phase_allowed}:{expected_taker_phase_allowed}"
            )

        cycle_index = row.get("cycle_index")
        if not isinstance(cycle_index, int) or int(cycle_index) < 0:
            findings.append(f"{row_prefix}_cycle_index_invalid")
        identity = _opportunity_identity(row)
        if identity is None:
            opportunity_identity_unverifiable_rows += 1
        else:
            opportunity_key = _opportunity_key(
                run_id=row_run_id,
                identity=identity,
                cycle_index=(int(cycle_index) if isinstance(cycle_index, int) else None),
                evaluation_scope=scope,
            )
            if opportunity_key in seen_opportunity_keys:
                findings.append(f"edge_duplicate_opportunity_key:{opportunity_key}")
            seen_opportunity_keys.add(opportunity_key)

        for prob_field in ("fair_probability", "market_probability"):
            value = row.get(prob_field)
            if value is not None and not _is_prob(value):
                findings.append(f"{row_prefix}_{prob_field}_invalid")
        # time_remaining_sec may legitimately be negative for recently expired opportunities
        # (for example during explicit reduce-only recovery windows). Treat it as numeric-only.
        time_remaining_value = row.get("time_remaining_sec")
        time_remaining_parsed = _safe_float(time_remaining_value)
        if time_remaining_value is not None and time_remaining_parsed is None:
            findings.append(f"{row_prefix}_time_remaining_sec_invalid")

        oracle_tick_age_value = row.get("oracle_tick_age_sec")
        oracle_tick_age_parsed = _safe_float(oracle_tick_age_value)
        if oracle_tick_age_value is not None and (oracle_tick_age_parsed is None or oracle_tick_age_parsed < 0.0):
            findings.append(f"{row_prefix}_oracle_tick_age_sec_invalid")
        if row.get("edge_value") is not None and _safe_float(row.get("edge_value")) is None:
            findings.append(f"{row_prefix}_edge_value_invalid")

        computed_edge = compute_edge_value(
            fair_probability=row.get("fair_probability"),
            market_probability=row.get("market_probability"),
        )
        row_edge = _safe_float(row.get("edge_value"))
        if computed_edge is not None and row_edge is not None and abs(float(computed_edge) - float(row_edge)) > 1e-9:
            findings.append(f"{row_prefix}_edge_value_mismatch")

        validation = validate_edge_inputs(
            EdgeInputSnapshot(
                fair_probability=_safe_float(row.get("fair_probability")),
                market_probability=_safe_float(row.get("market_probability")),
                time_remaining_sec=_safe_float(row.get("time_remaining_sec")),
                oracle_tick_age_sec=_safe_float(row.get("oracle_tick_age_sec")),
                lifecycle_phase=lifecycle_phase or None,
                evaluation_scope=scope,
            ),
            oracle_max_tick_age_sec=float(oracle_max_tick_age_sec),
        )

        block_reason = str(row.get("block_reason") or "").strip()
        normalized_block_reason = block_reason.lower()
        if action == EDGE_ACTION_NONE:
            blocked_rows += 1
            if not block_reason:
                findings.append(f"{row_prefix}_block_reason_missing_for_no_action")
            elif not is_canonical_block_reason(normalized_block_reason):
                findings.append(f"{row_prefix}_block_reason_invalid:{normalized_block_reason}")
            if submitted is True:
                findings.append(f"{row_prefix}_no_action_submitted_true")
            if filled is True:
                findings.append(f"{row_prefix}_no_action_filled_true")
        elif block_reason:
            findings.append(f"{row_prefix}_block_reason_present_for_action")

        if action == EDGE_ACTION_MAKER and maker_phase_allowed is not True:
            findings.append(f"{row_prefix}_action_requires_maker_phase_allowed_true")
        if action == EDGE_ACTION_TAKER and taker_phase_allowed is not True:
            findings.append(f"{row_prefix}_action_requires_taker_phase_allowed_true")
        if action == EDGE_ACTION_MAKER and maker_gate_open is not True:
            findings.append(f"{row_prefix}_action_requires_maker_gate_open_true")
        if action == EDGE_ACTION_TAKER and taker_gate_open is not True:
            findings.append(f"{row_prefix}_action_requires_taker_gate_open_true")
        allow_historical_lifecycle_missing_probability_input = (
            action != EDGE_ACTION_NONE
            and bool(historical_lifecycle_lineage_active)
            and str(validation.reason_code or "").strip().lower()
            in {"fair_probability_missing", "market_probability_missing"}
        )
        if (
            (not validation.valid)
            and action != EDGE_ACTION_NONE
            and (not allow_historical_lifecycle_missing_probability_input)
        ):
            findings.append(f"{row_prefix}_action_with_invalid_edge_inputs:{validation.reason_code}")

        if action != EDGE_ACTION_NONE:
            action_rows += 1
            if (
                (not historical_lifecycle_lineage_active)
                and (not phase_allows_action(lifecycle_phase, action))
            ):
                findings.append(f"{row_prefix}_phase_action_mismatch:{lifecycle_phase}:{action}")
            if submitted is not True:
                findings.append(f"{row_prefix}_action_without_submission")
            if action == EDGE_ACTION_MAKER and scope != EDGE_EVAL_SCOPE_MAKER:
                findings.append(f"{row_prefix}_scope_action_mismatch:{scope}:{action}")
            if action == EDGE_ACTION_TAKER and scope != EDGE_EVAL_SCOPE_TAKER:
                findings.append(f"{row_prefix}_scope_action_mismatch:{scope}:{action}")
        if row.get("result") is not None:
            findings.append(f"{row_prefix}_result_must_be_null")
        if filled is True and submitted is not True:
            findings.append(f"{row_prefix}_filled_without_submission")

        if scope == EDGE_EVAL_SCOPE_MAKER:
            maker_rows += 1
        elif scope == EDGE_EVAL_SCOPE_TAKER:
            taker_rows += 1

        # Keep deterministic canonical row shape for replay fingerprinting.
        canonical_rows.append(
            {
                "run_id": str(row.get("run_id") or "").strip(),
                "token_id": token_id,
                "target_ref": (str(row.get("target_ref") or "").strip() or None),
                "timestamp_utc": timestamp_utc,
                EDGE_LIFECYCLE_PHASE_FIELD: lifecycle_phase or None,
                "time_remaining_sec": _safe_float(row.get("time_remaining_sec")),
                "fair_probability": _safe_float(row.get("fair_probability")),
                "market_probability": _safe_float(row.get("market_probability")),
                "edge_value": _safe_float(row.get("edge_value")),
                "oracle_tick_age_sec": _safe_float(row.get("oracle_tick_age_sec")),
                EDGE_MAKER_PHASE_ALLOWED_FIELD: (
                    bool(maker_phase_allowed) if isinstance(maker_phase_allowed, bool) else None
                ),
                EDGE_TAKER_PHASE_ALLOWED_FIELD: (
                    bool(taker_phase_allowed) if isinstance(taker_phase_allowed, bool) else None
                ),
                EDGE_MAKER_GATE_OPEN_FIELD: (
                    bool(maker_gate_open) if isinstance(maker_gate_open, bool) else None
                ),
                EDGE_TAKER_GATE_OPEN_FIELD: (
                    bool(taker_gate_open) if isinstance(taker_gate_open, bool) else None
                ),
                "action_taken": action,
                "block_reason": (block_reason or None),
                "submitted": bool(submitted) if isinstance(submitted, bool) else None,
                "filled": bool(filled) if isinstance(filled, bool) else None,
                "result": row.get("result"),
                "evaluation_scope": scope,
                "cycle_index": int(cycle_index) if isinstance(cycle_index, int) else None,
                "order_id": (str(row.get("order_id") or "").strip() or None),
            }
        )

    if opportunity_identity_unverifiable_rows > 0:
        findings.append(
            f"edge_opportunity_identity_unverifiable_rows:{int(opportunity_identity_unverifiable_rows)}"
        )

    findings = sorted(set(str(item) for item in findings if str(item).strip()))
    canonical_rows_sorted = sorted(
        canonical_rows,
        key=lambda row: (
            str(row.get("token_id") or ""),
            int(row.get("cycle_index")) if isinstance(row.get("cycle_index"), int) else -1,
            str(row.get("evaluation_scope") or ""),
            str(row.get("timestamp_utc") or ""),
        ),
    )
    canonical_json = json.dumps(
        canonical_rows_sorted,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    edge_records_sha256 = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    required_fields_sha256 = _sha256_json_payload(sorted(REQUIRED_FIELDS))
    block_reason_taxonomy_sha256 = _sha256_json_payload(sorted(EDGE_BLOCK_REASONS))
    phase_policy_sha256 = _sha256_json_payload(
        {
            key: [bool(val[0]), bool(val[1])]
            for key, val in sorted(
                ((phase_name, phase_policy(phase_name)) for phase_name in LIFECYCLE_PHASES),
                key=lambda item: str(item[0]),
            )
        }
    )
    audit_rule_set_sha256 = _sha256_json_payload(sorted(AUDIT_RULE_SET))

    return {
        "ok": len(findings) == 0,
        "finding_count": len(findings),
        "findings": findings,
        "run_id": selected_run_id,
        "log_dir": str(log_dir.resolve()),
        "session_phase": normalized_phase,
        "config_path": str(config_path.resolve()),
        "run_contract_path": (
            str(run_contract_path.resolve())
            if isinstance(run_contract_path, pathlib.Path)
            else str(resolved_contract_path or "")
        ),
        "metrics": {
            "edge_rows": float(len(edge_rows)),
            "maker_rows": float(maker_rows),
            "taker_rows": float(taker_rows),
            "action_rows": float(action_rows),
            "blocked_rows": float(blocked_rows),
            "opportunity_identity_unverifiable_rows": float(opportunity_identity_unverifiable_rows),
        },
        "edge_records_sha256": edge_records_sha256,
        "required_fields_sha256": required_fields_sha256,
        "block_reason_taxonomy_sha256": block_reason_taxonomy_sha256,
        "phase_policy_sha256": phase_policy_sha256,
        "audit_rule_set_sha256": audit_rule_set_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BRO canonical edge-truth audit")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument(
        "--config",
        default="configs/profiles/paper_universal.yaml",
        help="Execution config path used for doctrine thresholds",
    )
    parser.add_argument("--run-contract", required=True, help="Run contract JSON path")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_postrun)",
    )
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Tail-row bound per JSONL file; set 0 for full-file scans",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        config_path=pathlib.Path(args.config),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if str(args.out).strip():
        out_path = pathlib.Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if bool(result.get("ok", False)) else 2)


if __name__ == "__main__":
    main()
