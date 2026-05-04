#!/usr/bin/env python3
"""Audit run identity integrity across manifest/status/events logs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Optional


from prodesk.artifact_identity import candidate_run_log_dirs
from prodesk.error_codes import summarize_error_codes
from prodesk.jsonl_utils import tail_lines as jsonl_tail_lines
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.session_phase import enforce_validation_phase


MONOTONIC_COUNTER_KEYS = (
    "counter.cycles",
    "counter.book_updates",
    "counter.orders_submitted",
    "counter.orders_canceled",
    "counter.fills",
    "counter.risk_rejects",
)

PAPER_TRADE_ID_RE = re.compile(r"^paper-trade-[0-9a-f]{12}-[1-9][0-9]*$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
DOCKER_IMAGE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _parse_ts(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc)


def _record_manifest_lineage_issue(
    *,
    allow_legacy_manifest: bool,
    findings: List[str],
    warnings: List[str],
    message: str,
) -> None:
    if allow_legacy_manifest:
        warnings.append(message)
    else:
        findings.append(message)


def _read_tail_jsonl(paths: List[pathlib.Path], tail_lines: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    limit = max(0, int(tail_lines))
    for path in paths:
        try:
            lines = jsonl_tail_lines(path, limit=limit)
        except OSError:
            continue
        for text in lines:
            text = text.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _iter_jsonl_rows(path: pathlib.Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def _count_jsonl_parse_errors(paths: List[pathlib.Path], *, tail_lines: Optional[int] = None) -> int:
    parse_errors = 0
    scan_tail = tail_lines is not None and int(tail_lines) > 0
    for path in paths:
        if scan_tail:
            try:
                source = jsonl_tail_lines(path, limit=int(tail_lines))
            except OSError:
                continue
            iterator = source
        else:
            try:
                fh = path.open("r", encoding="utf-8", errors="ignore")
            except OSError:
                continue
            iterator = fh
        for line in iterator:
            text = str(line).strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if not isinstance(payload, dict):
                parse_errors += 1
        if not scan_tail:
            fh.close()
    return int(parse_errors)


def _count_jsonl_parse_error_details(
    paths: List[pathlib.Path], *, tail_lines: Optional[int] = None
) -> Dict[str, int]:
    details: Dict[str, int] = {}
    scan_tail = tail_lines is not None and int(tail_lines) > 0
    for path in paths:
        path_errors = 0
        if scan_tail:
            try:
                source = jsonl_tail_lines(path, limit=int(tail_lines))
            except OSError:
                continue
            iterator = source
        else:
            try:
                fh = path.open("r", encoding="utf-8", errors="ignore")
            except OSError:
                continue
            iterator = fh
        for line in iterator:
            text = str(line).strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                path_errors += 1
                continue
            if not isinstance(payload, dict):
                path_errors += 1
        if not scan_tail:
            fh.close()
        if path_errors > 0:
            details[str(path)] = int(path_errors)
    return details


def _unreadable_paths(paths: List[pathlib.Path]) -> List[pathlib.Path]:
    unreadable: List[pathlib.Path] = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.read(0)
        except OSError:
            unreadable.append(path)
    return unreadable


def _read_run_scoped_rows(
    *,
    paths: List[pathlib.Path],
    run_id: str,
    contract: Optional[Dict[str, Any]],
    tail_lines: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if contract is None:
        rows = _read_tail_jsonl(paths, tail_lines=tail_lines)
        return [r for r in rows if str(r.get("run_id") or "").strip() == run_id]
    for path in paths:
        for row in _iter_jsonl_rows(path):
            if str(row.get("run_id") or "").strip() != run_id:
                continue
            rows.append(row)
    return apply_contract_bounds(rows, contract)


def _manifest_scoped_path(
    *,
    manifest_payload: Dict[str, Any],
    key: str,
) -> Optional[pathlib.Path]:
    text = str(manifest_payload.get(key) or "").strip()
    if not text:
        return None
    path = pathlib.Path(text).expanduser().resolve()
    if not path.exists():
        return None
    return path


def _scan_fill_events_for_run(paths: List[pathlib.Path], run_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    target = str(run_id or "").strip()
    if not target:
        return out
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("run_id") or "").strip() != target:
                        continue
                    if str(row.get("event_type") or row.get("type") or "") != "fill":
                        continue
                    out.append(row)
        except OSError:
            continue
    return out


def _manifest_for_run(log_dir: pathlib.Path, run_id: str) -> pathlib.Path:
    return log_dir / f"run_manifest_{run_id}.json"


def run_audit(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    min_status_rows: int,
    status_tail_lines: int,
    event_tail_lines: int,
    max_status_age_sec: float,
    allow_legacy_manifest: bool = False,
    run_contract_path: Optional[pathlib.Path] = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    normalized_phase = enforce_validation_phase(validation_name="run_integrity_audit", session_phase=session_phase)
    resolved = log_dir.resolve()
    if not resolved.exists():
        findings.append(f"log_dir_missing:{resolved}")
        return {
            "ok": False,
            "session_phase": normalized_phase,
            "finding_count": len(findings),
            "warning_count": len(warnings),
            "findings": findings,
            "warnings": warnings,
        }

    target_run_id = str(run_id or "").strip()
    if not target_run_id:
        findings.append("run_id_required")
        return {
            "ok": False,
            "session_phase": normalized_phase,
            "finding_count": len(findings),
            "warning_count": len(warnings),
            "findings": findings,
            "warnings": warnings,
        }
    context_hints: Dict[str, Any] = {
        "candidate_log_dirs_for_run": candidate_run_log_dirs(log_dir=resolved, run_id=target_run_id, max_depth=3),
    }

    manifest = _manifest_for_run(resolved, target_run_id)
    manifest_run_id = ""
    manifest_payload: Dict[str, Any] = {}
    if not manifest.exists():
        findings.append("run_manifest_missing")
        if context_hints["candidate_log_dirs_for_run"]:
            warnings.append("run_context_mismatch_candidate_log_dirs_present")
    else:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                manifest_payload = payload
            manifest_run_id = str(manifest_payload.get("run_id") or "").strip()
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            findings.append(f"run_manifest_invalid_json:{exc.__class__.__name__}")
    run_contract = resolve_run_contract(
        log_dir=resolved,
        run_id=target_run_id,
        run_contract_path_override=run_contract_path,
        allow_open=(normalized_phase == "validate_active"),
    )

    status_manifest_path = _manifest_scoped_path(manifest_payload=manifest_payload, key="status_path")
    events_manifest_path = _manifest_scoped_path(manifest_payload=manifest_payload, key="events_path")
    status_files = [status_manifest_path] if status_manifest_path is not None else sorted(resolved.glob("status_*.jsonl"))[-2:]
    event_files = [events_manifest_path] if events_manifest_path is not None else sorted(resolved.glob("events_*.jsonl"))
    error_files = sorted(resolved.glob("errors_*.jsonl"))
    status_slice = None
    events_slice = None
    if run_contract is not None:
        status_slice = run_contract_slice_path(run_contract, stream="status")
        events_slice = run_contract_slice_path(run_contract, stream="events")
        errors_slice = run_contract_slice_path(run_contract, stream="errors")
        if status_slice is not None:
            status_files = [status_slice]
        if events_slice is not None:
            event_files = [events_slice]
        if errors_slice is not None:
            error_files = [errors_slice]
    if not status_files:
        findings.append("status_files_missing")
        if context_hints["candidate_log_dirs_for_run"]:
            warnings.append("status_files_missing_in_selected_log_dir")
    status_unreadable = _unreadable_paths(status_files)
    event_unreadable = _unreadable_paths(event_files)
    error_unreadable = _unreadable_paths(error_files)
    if status_unreadable:
        msg = (
            f"status_files_unreadable:{len(status_unreadable)}:"
            + ",".join(str(path) for path in status_unreadable)
        )
        if normalized_phase == "validate_active":
            warnings.append(f"{msg}:phase=validate_active")
        else:
            findings.append(msg)
    if event_unreadable:
        msg = (
            f"event_files_unreadable:{len(event_unreadable)}:"
            + ",".join(str(path) for path in event_unreadable)
        )
        if normalized_phase == "validate_active":
            warnings.append(f"{msg}:phase=validate_active")
        else:
            findings.append(msg)
    if error_unreadable:
        warnings.append(
            "error_files_unreadable:"
            + f"{len(error_unreadable)}:"
            + ",".join(str(path) for path in error_unreadable)
        )

    # During validate_active, open contracts have no deterministic slice paths yet.
    # In that phase we intentionally scope to recent tails to avoid scanning full-day
    # logs (hundreds of MB) that can stall short-run post-validation.
    tail_scoped_active_contract = (
        normalized_phase == "validate_active"
        and run_contract is not None
        and status_slice is None
        and events_slice is None
    )
    status_contract_for_read = None if tail_scoped_active_contract else run_contract
    events_contract_for_read = None if tail_scoped_active_contract else run_contract

    status_rows = _read_run_scoped_rows(
        paths=status_files,
        run_id=target_run_id,
        contract=status_contract_for_read,
        tail_lines=int(status_tail_lines),
    )
    event_rows = _read_run_scoped_rows(
        paths=event_files[-2:] if events_contract_for_read is None else event_files,
        run_id=target_run_id,
        contract=events_contract_for_read,
        tail_lines=int(event_tail_lines),
    )
    status_parse_tail = int(status_tail_lines) if (normalized_phase == "validate_active" or status_contract_for_read is None) else None
    event_parse_tail = int(event_tail_lines) if (normalized_phase == "validate_active" or events_contract_for_read is None) else None
    status_parse_error_count = _count_jsonl_parse_errors(
        status_files,
        tail_lines=status_parse_tail,
    )
    status_parse_error_details = _count_jsonl_parse_error_details(
        status_files,
        tail_lines=status_parse_tail,
    )
    event_parse_error_count = _count_jsonl_parse_errors(
        event_files[-2:] if event_parse_tail is not None else event_files,
        tail_lines=event_parse_tail,
    )
    event_parse_error_details = _count_jsonl_parse_error_details(
        event_files[-2:] if event_parse_tail is not None else event_files,
        tail_lines=event_parse_tail,
    )
    if status_parse_error_count > 0:
        msg = f"status_json_parse_errors:{status_parse_error_count}"
        if normalized_phase == "validate_active":
            warnings.append(f"{msg}:phase=validate_active")
        else:
            findings.append(msg)
        warnings.append(
            "status_json_parse_error_paths:" + json.dumps(status_parse_error_details, sort_keys=True)
        )
    if event_parse_error_count > 0:
        msg = f"events_json_parse_errors:{event_parse_error_count}"
        if normalized_phase == "validate_active":
            warnings.append(f"{msg}:phase=validate_active")
        else:
            findings.append(msg)
        warnings.append(
            "events_json_parse_error_paths:" + json.dumps(event_parse_error_details, sort_keys=True)
        )

    if len(status_rows) < int(min_status_rows):
        findings.append(f"status_rows_below_min:{len(status_rows)}<min:{int(min_status_rows)}")

    monotonic_violations: Dict[str, int] = {}
    for key in MONOTONIC_COUNTER_KEYS:
        prev: Optional[float] = None
        violations = 0
        for row in status_rows:
            value = row.get(key)
            if not isinstance(value, (int, float)):
                continue
            cur = float(value)
            if prev is not None and cur < prev:
                violations += 1
            prev = cur
        if violations > 0:
            monotonic_violations[key] = violations
    for key, count in sorted(monotonic_violations.items()):
        findings.append(f"status_counter_non_monotonic:{key}:{count}")

    latest_status_ts: Optional[dt.datetime] = None
    for row in status_rows[-50:]:
        ts = _parse_ts(row.get("ts_utc"))
        if ts is None:
            continue
        if latest_status_ts is None or ts > latest_status_ts:
            latest_status_ts = ts
    if latest_status_ts is None:
        missing_msg = "latest_status_ts_missing"
        if normalized_phase == "validate_active":
            findings.append(missing_msg)
        else:
            warnings.append(f"{missing_msg}:phase={normalized_phase}")
    else:
        age_reference_ts = dt.datetime.now(dt.timezone.utc)
        if normalized_phase == "validate_postrun" and isinstance(run_contract, dict):
            contract_end = _parse_ts(run_contract.get("evidence_slice_end_ts"))
            if contract_end is None:
                contract_end = _parse_ts(run_contract.get("stop_ts"))
            if contract_end is not None:
                age_reference_ts = contract_end
        age_sec = max(0.0, (age_reference_ts - latest_status_ts).total_seconds())
        if age_sec > float(max_status_age_sec):
            stale_msg = f"latest_status_stale:{age_sec:.1f}>max:{float(max_status_age_sec):.1f}"
            if normalized_phase == "validate_active":
                findings.append(stale_msg)
            else:
                warnings.append(f"{stale_msg}:phase={normalized_phase}")

    if manifest_run_id and target_run_id and manifest_run_id != target_run_id:
        findings.append(f"manifest_run_id_mismatch:{manifest_run_id}!={target_run_id}")

    if target_run_id and not event_rows:
        warnings.append("event_rows_missing_for_run")

    cancel_all_events = [
        row
        for row in event_rows
        if str(row.get("event_type") or row.get("type") or "") in {"cancel_all_on_exit", "kill_switch_cancel_all"}
    ]
    for row in cancel_all_events:
        canceled_raw = row.get("canceled_count")
        released_raw = row.get("released_lock_count")
        if not isinstance(canceled_raw, (int, float)):
            findings.append("cancel_all_event_missing_canceled_count")
            continue
        if not isinstance(released_raw, (int, float)):
            findings.append("cancel_all_event_missing_released_lock_count")
            continue
        canceled_count = max(0, int(canceled_raw))
        released_count = max(0, int(released_raw))
        if released_count > canceled_count:
            findings.append(f"cancel_all_lock_release_exceeds_canceled:{released_count}>{canceled_count}")

    for key in (
        "profile_name",
        "git_commit",
        "config_fingerprint_sha256",
        "code_fingerprint_sha256",
        "status_path",
        "events_path",
        "start_ts",
    ):
        if not str(manifest_payload.get(key) or "").strip():
            _record_manifest_lineage_issue(
                allow_legacy_manifest=allow_legacy_manifest,
                findings=findings,
                warnings=warnings,
                message=f"run_manifest_missing_field:{key}",
            )

    for key in ("config_fingerprint_sha256", "code_fingerprint_sha256"):
        value = str(manifest_payload.get(key) or "").strip().lower()
        if value and not SHA256_HEX_RE.match(value):
            _record_manifest_lineage_issue(
                allow_legacy_manifest=allow_legacy_manifest,
                findings=findings,
                warnings=warnings,
                message=f"run_manifest_invalid_sha256:{key}",
            )

    runtime_identity = manifest_payload.get("runtime_identity")
    if not isinstance(runtime_identity, dict):
        warnings.append("run_manifest_runtime_identity_missing")
    else:
        for key in ("effective_config_sha256", "dependency_lock_sha256", "docker_image_hash"):
            value = str(runtime_identity.get(key) or "").strip()
            if value:
                continue
            if allow_legacy_manifest:
                warnings.append(f"run_manifest_runtime_identity_missing_field:{key}")
            else:
                findings.append(f"run_manifest_runtime_identity_missing_field:{key}")

        manifest_cfg_hash = str(manifest_payload.get("config_fingerprint_sha256") or "").strip().lower()
        runtime_cfg_hash = str(runtime_identity.get("effective_config_sha256") or "").strip().lower()
        if manifest_cfg_hash and runtime_cfg_hash and manifest_cfg_hash != runtime_cfg_hash:
            findings.append(
                f"run_manifest_runtime_identity_mismatch:effective_config_sha256:{runtime_cfg_hash}!={manifest_cfg_hash}"
            )

        dependency_lock_hash = str(runtime_identity.get("dependency_lock_sha256") or "").strip().lower()
        if dependency_lock_hash and not SHA256_HEX_RE.match(dependency_lock_hash):
            findings.append("run_manifest_runtime_identity_invalid_sha256:dependency_lock_sha256")

        docker_image_hash = str(runtime_identity.get("docker_image_hash") or "").strip().lower()
        if docker_image_hash and not DOCKER_IMAGE_HASH_RE.match(docker_image_hash):
            findings.append("run_manifest_runtime_identity_invalid_image_hash:docker_image_hash")

    if tail_scoped_active_contract:
        fill_events = [
            row
            for row in event_rows
            if str(row.get("event_type") or row.get("type") or "") == "fill"
        ]
    else:
        fill_events = _scan_fill_events_for_run(event_files, target_run_id)
    if run_contract is not None and not tail_scoped_active_contract:
        fill_events = apply_contract_bounds(fill_events, run_contract)
    fill_ids = [str(row.get("trade_id") or "").strip() for row in fill_events if str(row.get("trade_id") or "").strip()]
    duplicate_fill_ids = len(fill_ids) - len(set(fill_ids))
    if duplicate_fill_ids > 0:
        findings.append(f"duplicate_fill_trade_ids:{duplicate_fill_ids}")

    mode = str(manifest_payload.get("mode") or "").strip().lower()
    if not mode and status_rows:
        mode = str(status_rows[-1].get("mode") or "").strip().lower()
    if mode == "paper":
        invalid_paper_trade_ids = 0
        for trade_id in fill_ids:
            if not PAPER_TRADE_ID_RE.match(trade_id):
                invalid_paper_trade_ids += 1
        if invalid_paper_trade_ids > 0:
            findings.append(f"paper_trade_id_format_invalid:{invalid_paper_trade_ids}")

    status_fill_counter: Optional[int] = None
    for row in status_rows:
        value = row.get("counter.fills")
        if isinstance(value, (int, float)):
            status_fill_counter = int(value)
    if status_fill_counter is not None and status_fill_counter != len(fill_events):
        mismatch_msg = f"fill_count_mismatch:events={len(fill_events)}:status_counter={status_fill_counter}"
        if normalized_phase == "validate_active":
            if tail_scoped_active_contract:
                warnings.append(
                    f"fill_count_check_tail_scoped:events_tail={len(fill_events)}:status_counter={status_fill_counter}"
                )
            else:
                warnings.append(f"{mismatch_msg}:phase=validate_active")
        else:
            fills_after_latest_status = 0
            if latest_status_ts is not None:
                for row in fill_events:
                    fill_ts = _parse_ts(row.get("ts_utc"))
                    if fill_ts is not None and fill_ts > latest_status_ts:
                        fills_after_latest_status += 1
            if len(fill_events) == (status_fill_counter + fills_after_latest_status) and fills_after_latest_status > 0:
                warnings.append(
                    f"{mismatch_msg}:postrun_status_lag:fills_after_latest_status={fills_after_latest_status}"
                )
            else:
                findings.append(mismatch_msg)

    latest_status: Dict[str, Any] = status_rows[-1] if status_rows else {}
    taker_submitted = latest_status.get("counter.taker_orders_submitted")
    taker_filled = latest_status.get("counter.taker_orders_filled")
    if isinstance(taker_submitted, (int, float)) and isinstance(taker_filled, (int, float)):
        if float(taker_filled) > float(taker_submitted):
            findings.append(
                f"taker_fill_counter_invalid:filled={float(taker_filled):.0f}>submitted={float(taker_submitted):.0f}"
            )

    return {
        "log_dir": str(resolved),
        "manifest_path": str(manifest.resolve()) if manifest else "",
        "run_id": target_run_id,
        "session_phase": normalized_phase,
        "run_contract_path": str(run_contract.get("_path", "")) if isinstance(run_contract, dict) else "",
        "status_row_count": len(status_rows),
        "event_row_count": len(event_rows),
        "status_json_parse_error_count": int(status_parse_error_count),
        "events_json_parse_error_count": int(event_parse_error_count),
        "status_json_parse_error_paths": dict(sorted(status_parse_error_details.items())),
        "events_json_parse_error_paths": dict(sorted(event_parse_error_details.items())),
        "status_unreadable_count": int(len(status_unreadable)),
        "event_unreadable_count": int(len(event_unreadable)),
        "error_unreadable_count": int(len(error_unreadable)),
        "cancel_all_event_count": len(cancel_all_events),
        "fill_event_count": len(fill_events),
        "duplicate_fill_trade_id_count": duplicate_fill_ids,
        "finding_count": len(findings),
        "warning_count": len(warnings),
        "monotonic_violations": monotonic_violations,
        "context_hints": context_hints,
        "allow_legacy_manifest": bool(allow_legacy_manifest),
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "warnings": warnings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro run identity integrity audit")
    parser.add_argument("--log-dir", required=True, help="Execution log directory")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument("--min-status-rows", type=int, default=5, help="Minimum required status rows")
    parser.add_argument("--status-tail-lines", type=int, default=800, help="Status tail lines to inspect")
    parser.add_argument("--event-tail-lines", type=int, default=800, help="Event tail lines to inspect")
    parser.add_argument("--max-status-age-sec", type=float, default=180.0, help="Maximum age of latest status row")
    parser.add_argument(
        "--run-contract",
        default="",
        help="Optional explicit run contract JSON path for deterministic run slicing",
    )
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument(
        "--allow-legacy-manifest",
        action="store_true",
        help="Downgrade missing run-manifest fields to warnings (legacy forensic mode)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_audit(
        log_dir=pathlib.Path(args.log_dir),
        run_id=str(args.run_id),
        min_status_rows=max(1, int(args.min_status_rows)),
        status_tail_lines=max(10, int(args.status_tail_lines)),
        event_tail_lines=max(10, int(args.event_tail_lines)),
        max_status_age_sec=max(1.0, float(args.max_status_age_sec)),
        allow_legacy_manifest=bool(args.allow_legacy_manifest),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
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
