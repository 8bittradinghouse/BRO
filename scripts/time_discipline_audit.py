#!/usr/bin/env python3
"""Audit clock/time discipline controls from config + status stream."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


from prodesk.config import load_execution_config
from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import DEFAULT_MAX_LINES_PER_FILE, load_jsonl
from prodesk.run_contract import apply_contract_bounds, resolve_run_contract, run_contract_slice_path
from prodesk.session_phase import enforce_validation_phase

TIME_POLICY_REQUIRED_KEYS = ("source_of_truth", "fallback_logic", "skew_tolerance_ms", "monotonicity_rule")
TIMESTAMP_DOMAIN_FIELDS = ("ts_event_utc", "ts_receive_utc", "ts_source_utc", "ts_decision_utc")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_rows_from_slice(path: pathlib.Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except Exception:
                    continue
                if isinstance(row, dict):
                    yield row
    except Exception:
        return


def _status_rows(
    *,
    status_paths: List[pathlib.Path],
    run_id: str = "",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    files = list(status_paths)
    rows = load_jsonl(files, max_lines_per_file=max(0, int(max_lines_per_file)))
    scoped_rows: List[Dict[str, Any]] = []
    run_filter = str(run_id or "").strip()
    for row in rows:
        if run_filter and str(row.get("run_id") or "").strip() != run_filter:
            continue
        scoped_rows.append(row)
    return apply_contract_bounds(scoped_rows, run_contract)


def _latest_status_ts(
    *,
    status_paths: List[pathlib.Path],
    run_id: str = "",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    run_contract: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[datetime], int, int]:
    latest_ts: Optional[datetime] = None
    checked = 0
    non_monotonic = 0
    bounded_rows = _status_rows(
        status_paths=status_paths,
        run_id=run_id,
        max_lines_per_file=max_lines_per_file,
        run_contract=run_contract,
    )
    prev: Optional[datetime] = None
    for row in bounded_rows:
        ts = _parse_ts(row.get("ts_utc"))
        if ts is None:
            continue
        checked += 1
        if prev is not None and ts < prev:
            non_monotonic += 1
        prev = ts
        latest_ts = ts if latest_ts is None else max(latest_ts, ts)
    return latest_ts, checked, non_monotonic


def _validate_time_policy(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    missing_rows = 0
    invalid_rows = 0
    specs: Dict[str, int] = {}
    selected_policy: Dict[str, Any] = {}
    for row in rows:
        payload = row.get("time_policy")
        if payload is None:
            missing_rows += 1
            continue
        if not isinstance(payload, dict):
            invalid_rows += 1
            continue
        missing_keys = [key for key in TIME_POLICY_REQUIRED_KEYS if key not in payload]
        if missing_keys:
            invalid_rows += 1
            continue
        try:
            skew_tolerance_ms = float(payload.get("skew_tolerance_ms"))
        except Exception:
            invalid_rows += 1
            continue
        if skew_tolerance_ms < 0:
            invalid_rows += 1
            continue
        normalized = {
            "source_of_truth": str(payload.get("source_of_truth") or "").strip(),
            "fallback_logic": str(payload.get("fallback_logic") or "").strip(),
            "skew_tolerance_ms": skew_tolerance_ms,
            "monotonicity_rule": str(payload.get("monotonicity_rule") or "").strip(),
        }
        if not normalized["source_of_truth"] or not normalized["fallback_logic"] or not normalized["monotonicity_rule"]:
            invalid_rows += 1
            continue
        fingerprint = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        specs[fingerprint] = int(specs.get(fingerprint, 0)) + 1
        selected_policy = normalized
    if missing_rows > 0:
        findings.append(f"time_policy_missing_rows:{missing_rows}")
    if invalid_rows > 0:
        findings.append(f"time_policy_invalid_rows:{invalid_rows}")
    if len(specs) == 0:
        findings.append("time_policy_unobserved")
    if len(specs) > 1:
        findings.append(f"time_policy_non_deterministic_specs:{len(specs)}")
    evidence = {
        "required_keys": list(TIME_POLICY_REQUIRED_KEYS),
        "observed_specs": [json.loads(text) for text in sorted(specs.keys())],
        "missing_rows": int(missing_rows),
        "invalid_rows": int(invalid_rows),
        "selected_policy": selected_policy,
    }
    return evidence, findings


def _event_domain_audit(
    *,
    event_rows: List[Dict[str, Any]],
    skew_tolerance_ms: float,
) -> Tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    required_presence_missing = 0
    invalid_event_ts_rows = 0
    invalid_receive_ts_rows = 0
    invalid_source_ts_rows = 0
    invalid_decision_ts_rows = 0
    ts_event_mismatch_rows = 0
    cross_domain_skew_exceeded_rows = 0
    cross_domain_skew_checked_rows = 0
    cross_domain_skew_exempt_rows = 0
    decision_before_event_rows = 0

    for row in event_rows:
        missing_any = False
        for key in TIMESTAMP_DOMAIN_FIELDS:
            if key not in row:
                missing_any = True
        if missing_any:
            required_presence_missing += 1
            continue

        ts_utc = _parse_ts(row.get("ts_utc"))
        ts_event = _parse_ts(row.get("ts_event_utc"))
        if ts_event is None:
            invalid_event_ts_rows += 1
            continue
        if ts_utc is not None and ts_event != ts_utc:
            ts_event_mismatch_rows += 1

        ts_receive_raw = row.get("ts_receive_utc")
        ts_source_raw = row.get("ts_source_utc")
        ts_decision_raw = row.get("ts_decision_utc")

        ts_receive = _parse_ts(ts_receive_raw) if ts_receive_raw is not None else None
        ts_source = _parse_ts(ts_source_raw) if ts_source_raw is not None else None
        ts_decision = _parse_ts(ts_decision_raw) if ts_decision_raw is not None else None

        if ts_receive_raw not in (None, "") and ts_receive is None:
            invalid_receive_ts_rows += 1
        if ts_source_raw not in (None, "") and ts_source is None:
            invalid_source_ts_rows += 1
        if ts_decision is None:
            invalid_decision_ts_rows += 1
        elif ts_decision < ts_event:
            decision_before_event_rows += 1

        if ts_source is not None and ts_receive is not None:
            # Bootstrap subscribe ticks can carry backfilled source timestamps;
            # keep them observable but do not treat them as live skew violations.
            event_type = str(row.get("event_type") or "").strip().lower()
            msg_type = str(row.get("msg_type") or "").strip().lower()
            if event_type == "chainlink_tick" and msg_type == "subscribe":
                cross_domain_skew_exempt_rows += 1
                continue
            cross_domain_skew_checked_rows += 1
            skew_ms = abs((ts_receive - ts_source).total_seconds() * 1000.0)
            if skew_ms > float(skew_tolerance_ms):
                cross_domain_skew_exceeded_rows += 1

    if required_presence_missing > 0:
        findings.append(f"event_timestamp_domain_fields_missing_rows:{required_presence_missing}")
    if invalid_event_ts_rows > 0:
        findings.append(f"event_ts_event_invalid_rows:{invalid_event_ts_rows}")
    if invalid_receive_ts_rows > 0:
        findings.append(f"event_ts_receive_invalid_rows:{invalid_receive_ts_rows}")
    if invalid_source_ts_rows > 0:
        findings.append(f"event_ts_source_invalid_rows:{invalid_source_ts_rows}")
    if invalid_decision_ts_rows > 0:
        findings.append(f"event_ts_decision_invalid_rows:{invalid_decision_ts_rows}")
    if ts_event_mismatch_rows > 0:
        findings.append(f"event_ts_event_mismatch_rows:{ts_event_mismatch_rows}")
    if decision_before_event_rows > 0:
        findings.append(f"event_ts_decision_before_event_rows:{decision_before_event_rows}")
    if cross_domain_skew_exceeded_rows > 0:
        findings.append(
            f"event_ts_cross_domain_skew_exceeded_rows:{cross_domain_skew_exceeded_rows}>tolerance_ms:{float(skew_tolerance_ms):.3f}"
        )

    evidence = {
        "required_domain_fields": list(TIMESTAMP_DOMAIN_FIELDS),
        "event_rows_considered": int(len(event_rows)),
        "required_presence_missing_rows": int(required_presence_missing),
        "invalid_event_ts_rows": int(invalid_event_ts_rows),
        "invalid_receive_ts_rows": int(invalid_receive_ts_rows),
        "invalid_source_ts_rows": int(invalid_source_ts_rows),
        "invalid_decision_ts_rows": int(invalid_decision_ts_rows),
        "ts_event_mismatch_rows": int(ts_event_mismatch_rows),
        "decision_before_event_rows": int(decision_before_event_rows),
        "cross_domain_skew_exemption_policy": "chainlink_tick_msg_type_subscribe",
        "cross_domain_skew_checked_rows": int(cross_domain_skew_checked_rows),
        "cross_domain_skew_exempt_rows": int(cross_domain_skew_exempt_rows),
        "cross_domain_skew_exceeded_rows": int(cross_domain_skew_exceeded_rows),
        "cross_domain_skew_tolerance_ms": float(skew_tolerance_ms),
    }
    return evidence, findings


def run_audit(
    *,
    config_path: pathlib.Path,
    max_allowed_skew_sec: float,
    max_status_age_sec: float,
    min_status_rows: int,
    log_dir: Optional[pathlib.Path] = None,
    run_id: Optional[str] = None,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> Dict[str, Any]:
    normalized_phase = enforce_validation_phase(validation_name="time_discipline_audit", session_phase=session_phase)
    cfg = load_execution_config(config_path.resolve())
    findings: List[str] = []
    warnings: List[str] = []
    preflight = cfg.get("preflight", {})

    if not bool(preflight.get("check_clock_sync", False)):
        findings.append("preflight_clock_sync_disabled")

    configured_skew = float(preflight.get("max_clock_skew_sec", 0.0) or 0.0)
    if configured_skew <= 0:
        findings.append("preflight_max_clock_skew_invalid")
    elif configured_skew > float(max_allowed_skew_sec):
        findings.append(f"preflight_max_clock_skew_too_loose:{configured_skew:.3f}>max:{float(max_allowed_skew_sec):.3f}")

    configured_log_dir = pathlib.Path(str(cfg.get("storage", {}).get("log_dir", "./logs_exec"))).resolve()
    effective_log_dir = log_dir.resolve() if isinstance(log_dir, pathlib.Path) else configured_log_dir
    run_filter = str(run_id or "").strip()
    run_id_resolution = "missing"
    if run_filter:
        run_id_resolution = "explicit"

    contract: Optional[Dict[str, Any]] = None
    resolved_contract_path = ""
    if run_contract_path is not None:
        resolve_log_dir = effective_log_dir if effective_log_dir is not None else pathlib.Path(".").resolve()
        try:
            contract = resolve_run_contract(
                log_dir=resolve_log_dir,
                run_id=run_filter or None,
                run_contract_path_override=run_contract_path,
                allow_open=(normalized_phase == "validate_active"),
            )
        except Exception as exc:
            findings.append(str(exc))
            contract = None
        if isinstance(contract, dict):
            resolved_contract_path = str(contract.get("_path") or "")
            if not run_filter:
                run_filter = str(contract.get("run_id") or "").strip()
                if run_filter:
                    run_id_resolution = "contract"
            log_root = str(contract.get("log_root") or "").strip()
            if log_root:
                effective_log_dir = pathlib.Path(log_root).expanduser().resolve()

    status_paths = sorted(effective_log_dir.glob("status_*.jsonl"))
    if isinstance(contract, dict):
        status_slice = run_contract_slice_path(contract, stream="status")
        if status_slice is not None:
            status_paths = [status_slice]

    latest_ts, checked_rows, non_monotonic = _latest_status_ts(
        status_paths=status_paths,
        run_id=run_filter,
        max_lines_per_file=max(0, int(max_lines_per_file)),
        run_contract=contract,
    )
    scoped_status_rows = _status_rows(
        status_paths=status_paths,
        run_id=run_filter,
        max_lines_per_file=max(0, int(max_lines_per_file)),
        run_contract=contract,
    )
    if checked_rows < int(min_status_rows):
        findings.append(f"status_rows_too_few:{checked_rows}<min:{int(min_status_rows)}")
    if non_monotonic > 0:
        findings.append(f"status_ts_non_monotonic_rows:{non_monotonic}")

    time_policy_evidence, time_policy_findings = _validate_time_policy(scoped_status_rows)
    strict_time_policy_enforcement = isinstance(contract, dict)
    if strict_time_policy_enforcement:
        findings.extend(time_policy_findings)
    else:
        warnings.extend(time_policy_findings)

    event_paths = sorted(effective_log_dir.glob("events_*.jsonl"))
    if isinstance(contract, dict):
        events_slice = run_contract_slice_path(contract, stream="events")
        if events_slice is not None:
            event_paths = [events_slice]
    event_rows: List[Dict[str, Any]] = []
    for row in load_jsonl(event_paths, max_lines_per_file=max(0, int(max_lines_per_file))):
        if run_filter and str(row.get("run_id") or "").strip() != run_filter:
            continue
        event_rows.append(row)
    event_rows = apply_contract_bounds(event_rows, contract)

    selected_policy = time_policy_evidence.get("selected_policy", {})
    policy_skew_tolerance_ms = float(
        selected_policy.get("skew_tolerance_ms")
        if isinstance(selected_policy, dict) and selected_policy.get("skew_tolerance_ms") is not None
        else max_allowed_skew_sec * 1000.0
    )
    event_domain_evidence, event_domain_findings = _event_domain_audit(
        event_rows=event_rows,
        skew_tolerance_ms=policy_skew_tolerance_ms,
    )
    if strict_time_policy_enforcement:
        findings.extend(event_domain_findings)
    else:
        warnings.extend(event_domain_findings)

    if configured_skew > 0:
        configured_skew_ms = configured_skew * 1000.0
        if policy_skew_tolerance_ms > configured_skew_ms:
            message = (
                f"time_policy_skew_tolerance_too_loose:{policy_skew_tolerance_ms:.3f}>configured_max_clock_skew_ms:{configured_skew_ms:.3f}"
            )
            if strict_time_policy_enforcement:
                findings.append(message)
            else:
                warnings.append(message)

    status_age_sec = None
    if latest_ts is None:
        findings.append("status_ts_missing")
    else:
        now = datetime.now(timezone.utc)
        status_age_sec = max(0.0, (now - latest_ts).total_seconds())
        if status_age_sec > float(max_status_age_sec):
            findings.append(f"status_ts_too_stale:{status_age_sec:.3f}>max:{float(max_status_age_sec):.3f}")

    return {
        "config_path": str(config_path.resolve()),
        "session_phase": normalized_phase,
        "log_dir": str(effective_log_dir),
        "configured_log_dir": str(configured_log_dir),
        "run_contract_path": resolved_contract_path,
        "run_id_filter": run_filter,
        "run_id_resolution": run_id_resolution,
        "status_source_paths": [str(path.resolve()) for path in status_paths],
        "event_source_paths": [str(path.resolve()) for path in event_paths],
        "checked_status_rows": int(checked_rows),
        "checked_event_rows": int(len(event_rows)),
        "non_monotonic_rows": int(non_monotonic),
        "status_age_sec": status_age_sec,
        "configured_max_clock_skew_sec": configured_skew,
        "time_policy": time_policy_evidence,
        "event_timestamp_domain_audit": event_domain_evidence,
        "finding_count": len(findings),
        "findings": findings,
        "warnings": warnings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro clock/time discipline audit")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config path")
    parser.add_argument("--max-allowed-skew-sec", type=float, default=2.5, help="Strict upper bound for preflight.max_clock_skew_sec")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Maximum allowed age of latest status ts_utc")
    parser.add_argument("--min-status-rows", type=int, default=5, help="Minimum status rows required to evaluate monotonicity")
    parser.add_argument("--log-dir", default="", help="Optional explicit status log directory override")
    parser.add_argument("--run-id", default="", help="Optional run_id filter")
    parser.add_argument("--run-contract", default="", help="Optional explicit run contract path")
    parser.add_argument("--session-phase", default="validate_postrun", help="Validation phase context")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=DEFAULT_MAX_LINES_PER_FILE,
        help="Max lines per jsonl file (0 means full file)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        config_path=pathlib.Path(args.config),
        max_allowed_skew_sec=max(0.1, float(args.max_allowed_skew_sec)),
        max_status_age_sec=max(1.0, float(args.max_status_age_sec)),
        min_status_rows=max(1, int(args.min_status_rows)),
        log_dir=(pathlib.Path(args.log_dir).resolve() if str(args.log_dir).strip() else None),
        run_id=(str(args.run_id).strip() or None),
        run_contract_path=(pathlib.Path(args.run_contract).resolve() if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    out = str(args.out).strip()
    if out:
        out_path = pathlib.Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
