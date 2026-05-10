#!/usr/bin/env python3
"""Canonical paper session runner with explicit lifecycle phase machine."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from prodesk.canonical_authority import (
    CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS,
    CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS,
)
from prodesk.config import load_execution_config
from prodesk.time_sync import capture_host_time_sync_snapshot
from prodesk.run_contract import build_run_contract, run_contract_path, write_run_contract
from prodesk.session_phase import (
    assert_valid_phase_transition,
    validation_surface_for_phase,
)


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
CANONICAL_CONFIG_PATH = (ROOT_DIR / "configs/profiles/paper_universal.yaml").resolve()
CANONICAL_LOG_DIR = (ROOT_DIR / "logs_exec/paper_universal").resolve()
CANONICAL_STATE_PATH = (ROOT_DIR / "data/paper_universal/state.json").resolve()
CANONICAL_GUARDIAN_CONTEXT_PATH = (ROOT_DIR / "logs_exec/paper_universal/guardian_session_context.json").resolve()
CANONICAL_VALIDATION_SCRIPT = (ROOT_DIR / "scripts/canonical_paper_validation.sh").resolve()
DEPLOY_SCRIPT = (ROOT_DIR / "scripts/deploy_paper_clean.sh").resolve()
DOCKER_COMPOSE_PS_TIMEOUT_SEC = 30.0
CANONICAL_CMD_TIMEOUT_SEC = 900.0
CANONICAL_ACTIVE_VALIDATION_TIMEOUT_SEC = 300.0
CANONICAL_POSTRUN_VALIDATION_TIMEOUT_SEC = 900.0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(value: Optional[dt.datetime] = None) -> str:
    ts = value or utc_now()
    return ts.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: Any) -> Optional[dt.datetime]:
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


def _resolve_runtime_path(*, config_dir: pathlib.Path, raw_value: str, fallback: pathlib.Path) -> pathlib.Path:
    text = str(raw_value or "").strip()
    if not text:
        return fallback.resolve()
    if text.startswith("/logs"):
        rel = PurePosixPath(text).relative_to("/logs")
        return (ROOT_DIR / "logs_exec" / pathlib.Path(*rel.parts)).resolve()
    if text.startswith("/data"):
        rel = PurePosixPath(text).relative_to("/data")
        return (ROOT_DIR / "data" / pathlib.Path(*rel.parts)).resolve()
    candidate = pathlib.Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (config_dir / candidate).resolve()
    return candidate.resolve()


def _compute_active_timing(
    *,
    requested_active_sec: float,
    runtime_duration_sec: Optional[float],
    cutoff_buffer_sec: float,
    pre_active_elapsed_sec: float,
) -> Dict[str, Any]:
    """Compute deterministic active-phase wait semantics.

    Rule:
    - When requested runtime is below container runtime duration, honor the full
      requested active duration from active-phase entry (no pre-active subtraction).
    - When requested runtime would hit/exceed container runtime duration, cap with
      a safety buffer and subtract pre-active elapsed time to keep the total within
      the configured runtime window.
    """

    requested = max(1.0, float(requested_active_sec))
    pre_elapsed = max(0.0, float(pre_active_elapsed_sec))
    runtime_cap = None
    runtime_capped = False
    if runtime_duration_sec is not None:
        runtime_cap = max(0.0, float(runtime_duration_sec))
        runtime_capped = requested >= runtime_cap and runtime_cap > 0.0

    effective = requested
    if runtime_capped and runtime_cap is not None:
        effective = max(1.0, runtime_cap - max(0.0, float(cutoff_buffer_sec)))

    if runtime_capped:
        active_wait = max(0.0, effective - pre_elapsed)
        elapsed_source = "contract_start"
    else:
        active_wait = effective
        elapsed_source = "active_phase"

    return {
        "requested_active_sec": float(requested),
        "effective_active_sec": float(effective),
        "runtime_duration_sec": float(runtime_cap) if runtime_cap is not None else None,
        "runtime_cutoff_buffer_sec": float(max(0.0, float(cutoff_buffer_sec))),
        "pre_active_elapsed_sec": float(pre_elapsed),
        "runtime_capped": bool(runtime_capped),
        "active_wait_sec": float(active_wait),
        "elapsed_source": str(elapsed_source),
    }


def _to_container_logs_path(host_path: pathlib.Path) -> str:
    host = pathlib.Path(host_path).resolve()
    logs_root = (ROOT_DIR / "logs_exec").resolve()
    try:
        rel = host.relative_to(logs_root)
    except ValueError:
        return str(host)
    return str(PurePosixPath("/logs") / PurePosixPath(*rel.parts))


def _to_container_config_path(host_path: pathlib.Path) -> str:
    host = pathlib.Path(host_path).resolve()
    config_root = (ROOT_DIR / "configs").resolve()
    try:
        rel = host.relative_to(config_root)
    except ValueError as exc:
        raise RuntimeError(f"config_path_outside_configs_root:{host}") from exc
    return str(PurePosixPath("/config") / PurePosixPath(*rel.parts))


def _load_manifest_for_run(log_dir: pathlib.Path, run_id: str) -> Dict[str, Any]:
    manifest_path = log_dir / f"run_manifest_{run_id}.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"manifest_invalid_root:{manifest_path}")
    return payload


def _observe_manifest_for_run_id(
    *,
    log_dir: pathlib.Path,
    run_id: str,
    timeout_sec: float,
) -> Dict[str, Any]:
    rid = str(run_id or "").strip()
    if not rid:
        raise RuntimeError("run_id_missing_for_manifest_observation")
    manifest_path = (log_dir / f"run_manifest_{rid}.json").resolve()
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    last_reason = ""
    first_pass = True
    while first_pass or time.monotonic() <= deadline:
        first_pass = False
        if manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                last_reason = f"manifest_invalid_json:{exc.__class__.__name__}"
                time.sleep(0.5)
                continue
            if not isinstance(payload, dict):
                last_reason = "manifest_invalid_root"
                time.sleep(0.5)
                continue
            observed_run_id = str(payload.get("run_id") or "").strip()
            if observed_run_id and observed_run_id != rid:
                raise RuntimeError(f"run_manifest_run_id_mismatch:{observed_run_id}!={rid}")
            if observed_run_id == rid:
                return {
                    "observed": True,
                    "reason": "manifest_observed",
                    "manifest_path": str(manifest_path),
                    "observed_run_id": observed_run_id,
                }
            last_reason = "manifest_run_id_missing"
        if time.monotonic() <= deadline:
            time.sleep(min(0.5, max(0.01, float(timeout_sec))))
    return {
        "observed": False,
        "reason": last_reason or "run_manifest_not_observed",
        "manifest_path": str(manifest_path),
        "observed_run_id": "",
    }


def _load_manifest_for_run_with_timeout(*, log_dir: pathlib.Path, run_id: str, timeout_sec: float) -> Dict[str, Any]:
    rid = str(run_id or "").strip()
    if not rid:
        raise RuntimeError("run_id_missing_for_manifest_load")
    manifest_path = (log_dir / f"run_manifest_{rid}.json").resolve()
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    last_reason = ""
    first_pass = True
    while first_pass or time.monotonic() <= deadline:
        first_pass = False
        if manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                last_reason = f"manifest_invalid_json:{exc.__class__.__name__}"
            else:
                if not isinstance(payload, dict):
                    last_reason = "manifest_invalid_root"
                else:
                    observed_run_id = str(payload.get("run_id") or "").strip()
                    if observed_run_id and observed_run_id != rid:
                        raise RuntimeError(f"run_manifest_run_id_mismatch:{observed_run_id}!={rid}")
                    if observed_run_id == rid:
                        return payload
                    last_reason = "manifest_run_id_missing"
        if time.monotonic() <= deadline:
            time.sleep(min(0.5, max(0.01, float(timeout_sec))))
    raise RuntimeError(
        f"run_manifest_not_observed_within_timeout:{manifest_path}:{last_reason or 'run_manifest_not_observed'}"
    )


def _compute_workspace_code_fingerprint(root_dir: pathlib.Path) -> tuple[str, int]:
    """Match executor runtime manifest fingerprint algorithm for consistency checks."""
    root = root_dir.resolve()
    candidates = [root / "executor.py"]
    candidates.extend(sorted((root / "prodesk").rglob("*.py")))
    digest = hashlib.sha256()
    count = 0
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def _load_manifest_for_run_optional(log_dir: pathlib.Path, run_id: str) -> Dict[str, Any]:
    try:
        return _load_manifest_for_run(log_dir, run_id)
    except (FileNotFoundError, json.JSONDecodeError, OSError, RuntimeError):
        return {}


def _default_status_events_paths(log_dir: pathlib.Path, *, at_ts: Optional[dt.datetime] = None) -> tuple[pathlib.Path, pathlib.Path]:
    current = (at_ts or utc_now()).date().isoformat()
    status_path = (log_dir / f"status_{current}.jsonl").resolve()
    events_path = (log_dir / f"events_{current}.jsonl").resolve()
    return status_path, events_path


def _stream_source_paths_for_window(
    *,
    log_dir: pathlib.Path,
    prefix: str,
    start_dt: dt.datetime,
    stop_dt: dt.datetime,
    preferred_path: Optional[pathlib.Path] = None,
) -> List[pathlib.Path]:
    paths: List[pathlib.Path] = []
    seen: set[pathlib.Path] = set()

    def _add(candidate: pathlib.Path) -> None:
        resolved = candidate.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        paths.append(resolved)

    day_count = max(0, (stop_dt.date() - start_dt.date()).days)
    for day_offset in range(0, day_count + 1):
        day = start_dt.date() + dt.timedelta(days=day_offset)
        _add(log_dir / f"{prefix}_{day.isoformat()}.jsonl")
    # Preserve chronological day order for cross-midnight slices. Preferred
    # paths still matter, but they must not prepend the later day ahead of the
    # earlier day and fabricate non-monotonic postrun evidence.
    if preferred_path is not None:
        _add(preferred_path)
    if len(paths) == 0:
        for candidate in sorted(log_dir.glob(f"{prefix}_*.jsonl")):
            _add(candidate)
    return paths


def _docker_compose_ps_lines() -> List[str]:
    proc = subprocess.run(
        ["docker", "compose", "ps"],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
        check=True,
        timeout=float(DOCKER_COMPOSE_PS_TIMEOUT_SEC),
    )
    return [line.rstrip("\n") for line in str(proc.stdout or "").splitlines()]


def _service_up(lines: Iterable[str], service_name: str) -> bool:
    for line in lines:
        text = str(line)
        if service_name in text and "Up" in text:
            return True
    return False


def _service_present(lines: Iterable[str], service_name: str) -> bool:
    for line in lines:
        if service_name in str(line):
            return True
    return False


def _stream_jsonl_rows(path: pathlib.Path) -> Iterable[Dict[str, Any]]:
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
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _filter_rows_for_run(
    *,
    source_paths: Iterable[pathlib.Path],
    destination_path: pathlib.Path,
    run_id: str,
    start_ts: dt.datetime,
    end_ts: dt.datetime,
) -> int:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination_path.open("w", encoding="utf-8") as out:
        for src in source_paths:
            if not src.exists():
                continue
            for row in _stream_jsonl_rows(src):
                if str(row.get("run_id") or "").strip() != run_id:
                    continue
                row_ts = parse_ts(row.get("ts_utc"))
                if row_ts is not None and not (start_ts <= row_ts <= end_ts):
                    continue
                out.write(json.dumps(row, ensure_ascii=True) + "\n")
                count += 1
    return count


def _read_json_object(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _write_host_time_sync_artifact(
    *,
    report_root: pathlib.Path,
    artifact_name: str,
    session_id: str,
    run_id: str,
    phase: str,
    requested_active_minutes: float,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "session_id": str(session_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "phase": str(phase or "").strip(),
        "requested_active_minutes": float(max(0.0, float(requested_active_minutes))),
    }
    payload.update(capture_host_time_sync_snapshot())
    artifact_path = (report_root / artifact_name).resolve()
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _append_host_time_sync_sample_artifact(
    *,
    report_root: pathlib.Path,
    artifact_name: str,
    session_id: str,
    run_id: str,
    phase: str,
    requested_active_minutes: float,
    elapsed_active_sec: float,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "session_id": str(session_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "phase": str(phase or "").strip(),
        "requested_active_minutes": float(max(0.0, float(requested_active_minutes))),
        "elapsed_active_sec": float(max(0.0, float(elapsed_active_sec))),
    }
    payload.update(capture_host_time_sync_snapshot())
    artifact_path = (report_root / artifact_name).resolve()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def summarize_postrun_validation(
    *,
    run_id: str,
    report_dir: pathlib.Path,
    script_exit_code: int,
) -> Dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    expected_files: Dict[str, pathlib.Path] = {
        "paper_harness_audit": (report_dir / "paper_harness_audit.json").resolve(),
        "paper_harness_audit_replay": (report_dir / "paper_harness_audit_replay.json").resolve(),
        "websocket_hardening_audit": (report_dir / "websocket_hardening_audit.json").resolve(),
        "websocket_hardening_audit_replay": (report_dir / "websocket_hardening_audit_replay.json").resolve(),
        "time_discipline_audit": (report_dir / "time_discipline_audit.json").resolve(),
        "time_discipline_audit_replay": (report_dir / "time_discipline_audit_replay.json").resolve(),
        "guardian_profile_audit": (report_dir / "guardian_profile_audit.json").resolve(),
        "guardian_profile_audit_replay": (report_dir / "guardian_profile_audit_replay.json").resolve(),
        "readiness_gate": (report_dir / "readiness_gate.json").resolve(),
        "readiness_gate_replay": (report_dir / "readiness_gate_replay.json").resolve(),
        "nightly_soak_report": (report_dir / "nightly_soak_report.json").resolve(),
        "nightly_soak_report_replay": (report_dir / "nightly_soak_report_replay.json").resolve(),
        "edge_truth_audit": (report_dir / "edge_truth_audit.json").resolve(),
        "edge_truth_audit_replay": (report_dir / "edge_truth_audit_replay.json").resolve(),
        "order_lifecycle_audit": (report_dir / "order_lifecycle_audit.json").resolve(),
        "order_lifecycle_audit_replay": (report_dir / "order_lifecycle_audit_replay.json").resolve(),
        "outcome_truth_audit": (report_dir / "outcome_truth_audit.json").resolve(),
        "outcome_truth_audit_replay": (report_dir / "outcome_truth_audit_replay.json").resolve(),
        "soak_hardening_gate": (report_dir / "soak_hardening_gate.json").resolve(),
        "soak_hardening_gate_replay": (report_dir / "soak_hardening_gate_replay.json").resolve(),
        "validation_summary": (report_dir / "validation_summary.json").resolve(),
    }
    missing_reports: List[str] = []
    parse_error_reports: List[str] = []
    payloads: Dict[str, Dict[str, Any]] = {}
    for name, path in expected_files.items():
        if not path.exists():
            missing_reports.append(name)
            continue
        parsed = _read_json_object(path)
        if parsed is None:
            parse_error_reports.append(name)
            continue
        payloads[name] = parsed

    runtime_classification = ""
    promotion_eligible: Optional[bool] = None
    highest_passing_stage = ""
    blocking_stage = ""
    recommended_next_stage = ""
    run_commit_lineage: Dict[str, Any] = {}
    nightly_payload = payloads.get("nightly_soak_report", {})
    if isinstance(nightly_payload, dict):
        runtime_payload = nightly_payload.get("runtime_classification")
        if isinstance(runtime_payload, dict):
            runtime_classification = str(runtime_payload.get("classification") or "").strip()
            raw_promotion_eligible = runtime_payload.get("promotion_eligible")
            if isinstance(raw_promotion_eligible, bool):
                promotion_eligible = raw_promotion_eligible
            else:
                normalized = str(raw_promotion_eligible or "").strip().lower()
                if normalized in {"true", "false"}:
                    promotion_eligible = normalized == "true"
        lineage_payload = nightly_payload.get("run_commit_lineage")
        if isinstance(lineage_payload, dict):
            run_commit_lineage = dict(lineage_payload)
    readiness_payload = payloads.get("readiness_gate", {})
    if isinstance(readiness_payload, dict):
        raw_highest_stage = readiness_payload.get("highest_passing_stage")
        highest_passing_stage = str(raw_highest_stage or "").strip()
        if (not highest_passing_stage) and raw_highest_stage is None:
            highest_passing_stage = "none"
        blocking_stage = str(readiness_payload.get("blocking_stage") or "").strip()
        recommended_next_stage = str(readiness_payload.get("recommended_next_stage") or "").strip()

    summary_payload = payloads.get("validation_summary", {})
    validator_exit_codes = summary_payload.get("validator_exit_codes")
    if not isinstance(validator_exit_codes, dict):
        validator_exit_codes = {}
    determinism_consistent = False
    summary_determinism_flag = summary_payload.get("validator_determinism_ok")
    if summary_determinism_flag is None:
        # Backward-compatible fallback for older validation summaries.
        summary_determinism_flag = summary_payload.get("edge_truth_determinism_ok")
    if isinstance(summary_determinism_flag, bool):
        determinism_consistent = bool(summary_determinism_flag)
    else:
        summary_determinism_text = str(summary_determinism_flag or "").strip().lower()
        if summary_determinism_text in {"true", "false"}:
            determinism_consistent = summary_determinism_text == "true"
        else:
            determinism_payload = summary_payload.get("edge_truth_determinism")
            if isinstance(determinism_payload, dict):
                structural = determinism_payload.get("structural_consistency")
                if not isinstance(structural, dict):
                    structural = {}
                determinism_consistent = (
                    bool(str(determinism_payload.get("edge_records_sha256") or "").strip())
                    and bool(str(determinism_payload.get("replay_edge_records_sha256") or "").strip())
                    and bool(determinism_payload.get("replay_match", False))
                    and bool(structural.get("replay_required_fields_match", False))
                    and bool(structural.get("replay_block_reason_taxonomy_match", False))
                    and bool(structural.get("replay_stage_policy_match", False))
                    and bool(structural.get("replay_audit_rule_set_match", False))
                )
    overall_exit_code = summary_payload.get("overall_exit_code")
    summary_exit_matches = False
    if isinstance(overall_exit_code, int):
        summary_exit_matches = overall_exit_code == int(script_exit_code)
    elif isinstance(overall_exit_code, str) and str(overall_exit_code).strip().isdigit():
        summary_exit_matches = int(str(overall_exit_code).strip()) == int(script_exit_code)

    reports_complete = (len(missing_reports) == 0) and (len(parse_error_reports) == 0)
    known_policy_exit = int(script_exit_code) in (0, 2)
    execution_error = (
        (not known_policy_exit)
        or (not reports_complete)
        or (not summary_exit_matches)
        or (not determinism_consistent)
    )
    gate_passed = int(script_exit_code) == 0
    policy_failed = int(script_exit_code) == 2

    status = "pass"
    if execution_error:
        status = "execution_error"
    elif policy_failed:
        status = "policy_failed"

    return {
        "run_id": normalized_run_id,
        "report_dir": str(report_dir.resolve()),
        "status": status,
        "runtime_classification": runtime_classification,
        "promotion_eligible": promotion_eligible,
        "highest_passing_stage": highest_passing_stage,
        "blocking_stage": blocking_stage,
        "recommended_next_stage": recommended_next_stage,
        "run_commit_lineage": run_commit_lineage,
        "script_exit_code": int(script_exit_code),
        "known_policy_exit": bool(known_policy_exit),
        "execution_error": bool(execution_error),
        "policy_failed": bool(policy_failed),
        "gate_passed": bool(gate_passed),
        "reports_complete": bool(reports_complete),
        "missing_reports": list(missing_reports),
        "parse_error_reports": list(parse_error_reports),
        "summary_exit_matches": bool(summary_exit_matches),
        "determinism_consistent": bool(determinism_consistent),
        "validator_exit_codes": {str(k): int(v) for k, v in validator_exit_codes.items() if str(v).strip().lstrip("-").isdigit()},
        "summary_path": str(expected_files["validation_summary"]),
    }


@dataclass
class PhaseRecord:
    phase: str
    entered_ts: str
    entry_conditions: List[Dict[str, Any]]
    exited_ts: str = ""
    exit_conditions: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "running"


@dataclass
class SessionContext:
    session_id: str
    config_path: pathlib.Path
    active_minutes: float
    wait_sec: float
    do_build: bool
    archive_export: bool
    max_lines_per_file: int
    session_type: str = "paper_canonical"
    expected_profile_name: str = "paper_universal"
    validation_script_path: pathlib.Path = CANONICAL_VALIDATION_SCRIPT
    validation_artifact_name: str = "canonical_paper_validation.json"
    allow_noncanonical_config: bool = False
    require_canonical_roots: bool = True
    log_dir: pathlib.Path = CANONICAL_LOG_DIR
    state_path: pathlib.Path = CANONICAL_STATE_PATH
    guardian_context_path: pathlib.Path = CANONICAL_GUARDIAN_CONTEXT_PATH
    resolved_profile_name: str = ""
    resolved_config_fingerprint_sha256: str = ""
    container_config_path: str = ""
    container_log_dir: str = ""
    container_guard_stop_path: str = ""
    run_id: str = ""
    run_manifest_path: pathlib.Path = pathlib.Path()
    run_contract_path: pathlib.Path = pathlib.Path()
    run_contract_payload: Dict[str, Any] = field(default_factory=dict)
    postrun_validation: Dict[str, Any] = field(default_factory=dict)
    session_token: str = ""
    current_phase: str = ""
    phase_history: List[PhaseRecord] = field(default_factory=list)
    session_root: pathlib.Path = pathlib.Path()
    session_state_path: pathlib.Path = pathlib.Path()
    report_root: pathlib.Path = pathlib.Path()

    def initialize_paths(self) -> None:
        self.session_root = (self.log_dir / "sessions" / self.session_id).resolve()
        self.report_root = (self.session_root / "reports").resolve()
        self.session_state_path = (self.session_root / "session_state.json").resolve()
        self.guardian_context_path = (self.log_dir / "guardian_session_context.json").resolve()
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.report_root.mkdir(parents=True, exist_ok=True)


class SessionRunner:
    def __init__(self, ctx: SessionContext):
        self.ctx = ctx
        self.ctx.initialize_paths()
        self._write_state()

    def _write_state(self) -> None:
        run_manifest_path_text = ""
        if self.ctx.run_manifest_path != pathlib.Path():
            run_manifest_path_text = str(self.ctx.run_manifest_path)
        run_contract_path_text = ""
        if self.ctx.run_contract_path != pathlib.Path():
            run_contract_path_text = str(self.ctx.run_contract_path)
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "session_id": self.ctx.session_id,
            "ts_utc": utc_iso(),
            "phase": self.ctx.current_phase,
            "run_id": self.ctx.run_id,
            "session_type": str(self.ctx.session_type or ""),
            "expected_profile_name": str(self.ctx.expected_profile_name or ""),
            "resolved_profile_name": str(self.ctx.resolved_profile_name or ""),
            "effective_config_sha256": str(self.ctx.resolved_config_fingerprint_sha256 or ""),
            "config_path": str(self.ctx.config_path),
            "log_dir": str(self.ctx.log_dir),
            "state_path": str(self.ctx.state_path),
            "container_config_path": str(self.ctx.container_config_path or ""),
            "container_log_dir": str(self.ctx.container_log_dir or ""),
            "container_guard_stop_path": str(self.ctx.container_guard_stop_path or ""),
            "validation_script_path": str(self.ctx.validation_script_path),
            "validation_artifact_name": str(self.ctx.validation_artifact_name or ""),
            "run_manifest_path": run_manifest_path_text,
            "run_contract_path": run_contract_path_text,
            "postrun_validation": dict(self.ctx.postrun_validation),
            "phase_validation_surface": (
                validation_surface_for_phase(self.ctx.current_phase)
                if self.ctx.current_phase
                else {"legal_validations": [], "actionable_failures": [], "informational_failures": []}
            ),
            "phase_history": [
                {
                    "phase": rec.phase,
                    "entered_ts": rec.entered_ts,
                    "entry_conditions": rec.entry_conditions,
                    "exited_ts": rec.exited_ts,
                    "exit_conditions": rec.exit_conditions,
                    "status": rec.status,
                }
                for rec in self.ctx.phase_history
            ],
        }
        self.ctx.session_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.session_state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        identity_payload: Dict[str, Any] = {
            "schema_version": 1,
            "ts_utc": utc_iso(),
            "session_id": self.ctx.session_id,
            "run_id": self.ctx.run_id,
            "session_type": str(self.ctx.session_type or ""),
            "config_path": str(self.ctx.config_path),
            "selected_profile_name": str(self.ctx.resolved_profile_name or self.ctx.expected_profile_name or ""),
            "expected_profile_name": str(self.ctx.expected_profile_name or ""),
            "effective_config_sha256": str(self.ctx.resolved_config_fingerprint_sha256 or ""),
            "selected_log_root": str(self.ctx.log_dir),
            "selected_state_path": str(self.ctx.state_path),
            "container_config_path": str(self.ctx.container_config_path or ""),
            "container_log_dir": str(self.ctx.container_log_dir or ""),
            "container_guard_stop_path": str(self.ctx.container_guard_stop_path or ""),
            "validation_script_path": str(self.ctx.validation_script_path),
        }
        (self.ctx.report_root / "session_identity.json").write_text(
            json.dumps(identity_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._write_guardian_context()

    def _write_guardian_context(self) -> None:
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "ts_utc": utc_iso(),
            "session_id": self.ctx.session_id,
            "session_token": self.ctx.session_token,
            "session_phase": self.ctx.current_phase,
            "run_id": self.ctx.run_id,
            "run_contract_path": (
                _to_container_logs_path(self.ctx.run_contract_path)
                if self.ctx.run_contract_path != pathlib.Path()
                else ""
            ),
        }
        self.ctx.guardian_context_path.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.guardian_context_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _phase_enter(self, phase: str, entry_conditions: List[Dict[str, Any]]) -> None:
        if self.ctx.current_phase:
            assert_valid_phase_transition(self.ctx.current_phase, phase)
        rec = PhaseRecord(phase=phase, entered_ts=utc_iso(), entry_conditions=entry_conditions)
        self.ctx.phase_history.append(rec)
        self.ctx.current_phase = phase
        self._write_state()

    def _phase_exit(self, exit_conditions: List[Dict[str, Any]]) -> None:
        if not self.ctx.phase_history:
            raise RuntimeError("phase_history_missing")
        failing = [c for c in exit_conditions if not bool(c.get("passed", False))]
        rec = self.ctx.phase_history[-1]
        rec.exited_ts = utc_iso()
        rec.exit_conditions = exit_conditions
        rec.status = "failed" if failing else "completed"
        self._write_state()
        if failing:
            labels = ",".join(str(c.get("name") or "unknown") for c in failing)
            raise RuntimeError(f"phase_exit_failed:{rec.phase}:{labels}")

    def _run_cmd(
        self,
        args: List[str],
        *,
        env: Optional[Dict[str, str]] = None,
        timeout_sec: Optional[float] = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = dict(os.environ)
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})
        timeout_value = float(timeout_sec) if isinstance(timeout_sec, (int, float)) else float(CANONICAL_CMD_TIMEOUT_SEC)
        try:
            return subprocess.run(
                args,
                cwd=str(ROOT_DIR),
                text=True,
                capture_output=True,
                check=True,
                env=merged_env,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "subprocess_timeout:"
                + " ".join(str(x) for x in args)
                + f":timeout_sec={timeout_value:.1f}"
            ) from exc

    def _condition(self, name: str, passed: bool, detail: str) -> Dict[str, Any]:
        return {"name": name, "passed": bool(passed), "detail": str(detail)}

    def _finalize_failure_closeout(self, failure: BaseException) -> None:
        """Best-effort closeout so failed sessions do not leave open run contracts.

        This path is intentionally defensive: any closeout sub-step failure is
        recorded to disk and does not mask the original exception.
        """
        if not str(self.ctx.run_id or "").strip():
            return
        if not str(self.ctx.run_contract_path or "").strip():
            self.ctx.run_contract_path = run_contract_path(log_dir=self.ctx.log_dir, run_id=self.ctx.run_id)

        closeout_start_mono = time.monotonic()
        failure_note: Dict[str, Any] = {
            "ts_utc": utc_iso(),
            "run_id": self.ctx.run_id,
            "phase": self.ctx.current_phase,
            "error_type": str(failure.__class__.__name__),
            "error_message": str(failure),
            "error": f"{failure.__class__.__name__}:{failure}",
            "stack_shutdown_attempted": False,
            "stack_shutdown_ok": False,
            "run_contract_closeout_attempted": False,
            "run_contract_closeout_ok": False,
            "closeout_elapsed_sec": 0.0,
        }
        traceback_path = (self.ctx.report_root / "failure_finalize.traceback.log").resolve()
        try:
            traceback_path.write_text(
                "".join(traceback.format_exception(type(failure), failure, failure.__traceback__)),
                encoding="utf-8",
            )
            failure_note["traceback_path"] = str(traceback_path)
        except OSError as exc:
            failure_note["traceback_write_error"] = f"{exc.__class__.__name__}:{exc}"

        # Freeze/stop the stack best-effort to avoid dangling runtime activity.
        failure_note["stack_shutdown_attempted"] = True
        try:
            down_proc = subprocess.run(
                ["docker", "compose", "down"],
                cwd=str(ROOT_DIR),
                text=True,
                capture_output=True,
                timeout=float(CANONICAL_CMD_TIMEOUT_SEC),
            )
            failure_note["stack_shutdown_ok"] = down_proc.returncode == 0
            failure_note["stack_shutdown_exit_code"] = int(down_proc.returncode)
            (self.ctx.report_root / "failure_finalize.docker_down.stdout.log").write_text(
                str(down_proc.stdout or ""),
                encoding="utf-8",
            )
            (self.ctx.report_root / "failure_finalize.docker_down.stderr.log").write_text(
                str(down_proc.stderr or ""),
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired as exc:
            failure_note["stack_shutdown_error"] = (
                "TimeoutExpired:"
                + f"timeout_sec={float(CANONICAL_CMD_TIMEOUT_SEC):.1f}"
            )
            (self.ctx.report_root / "failure_finalize.docker_down.stdout.log").write_text(
                str(exc.stdout or ""),
                encoding="utf-8",
            )
            (self.ctx.report_root / "failure_finalize.docker_down.stderr.log").write_text(
                str(exc.stderr or ""),
                encoding="utf-8",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failure_note["stack_shutdown_error"] = f"{exc.__class__.__name__}:{exc}"

        # Close the run contract fail-closed even if phase_stop never ran.
        failure_note["run_contract_closeout_attempted"] = True
        try:
            existing: Dict[str, Any] = {}
            try:
                existing = json.loads(self.ctx.run_contract_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            start_ts = str(existing.get("start_ts") or self.ctx.run_contract_payload.get("start_ts") or "").strip()
            if not start_ts:
                start_ts = utc_iso()
            stop_ts = utc_iso()
            if parse_ts(stop_ts) is not None and parse_ts(start_ts) is not None and parse_ts(stop_ts) < parse_ts(start_ts):
                stop_ts = start_ts
            existing["phase"] = str(self.ctx.current_phase or "stop")
            existing["stop_ts"] = stop_ts
            existing["evidence_slice_end_ts"] = stop_ts
            existing["start_ts"] = start_ts
            existing["evidence_slice_start_ts"] = str(
                existing.get("evidence_slice_start_ts") or start_ts
            ).strip() or start_ts
            existing["manifest_path"] = str(
                pathlib.Path(str(existing.get("manifest_path") or self.ctx.run_manifest_path)).resolve()
            )
            existing["log_root"] = str(pathlib.Path(str(existing.get("log_root") or self.ctx.log_dir)).resolve())
            existing["state_root"] = str(pathlib.Path(str(existing.get("state_root") or self.ctx.state_path.parent)).resolve())
            existing["session_id"] = str(existing.get("session_id") or self.ctx.session_id)
            existing["run_id"] = str(existing.get("run_id") or self.ctx.run_id)
            existing["session_type"] = str(existing.get("session_type") or self.ctx.session_type or "paper_canonical")
            existing["authority_level"] = str(
                existing.get("authority_level") or self.ctx.run_contract_payload.get("authority_level") or "observational"
            )
            actions = existing.get("allowed_actions")
            if not isinstance(actions, list) or len(actions) == 0:
                existing["allowed_actions"] = list(CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS)
            existing["status_path"] = str(
                existing.get("status_path") or (self.ctx.log_dir / f"status_{utc_now().date().isoformat()}.jsonl")
            )
            existing["events_path"] = str(
                existing.get("events_path") or (self.ctx.log_dir / f"events_{utc_now().date().isoformat()}.jsonl")
            )
            existing["errors_path"] = str(existing.get("errors_path") or "")
            # Best-effort lineage enrichment from run manifest if available.
            manifest_payload = _load_manifest_for_run_optional(self.ctx.log_dir, self.ctx.run_id)
            if isinstance(manifest_payload, dict):
                existing["git_commit"] = str(
                    existing.get("git_commit")
                    or manifest_payload.get("git_commit")
                    or ""
                ).strip()
                existing["config_fingerprint_sha256"] = str(
                    existing.get("config_fingerprint_sha256")
                    or manifest_payload.get("config_fingerprint_sha256")
                    or ""
                ).strip()
                existing["code_fingerprint_sha256"] = str(
                    existing.get("code_fingerprint_sha256")
                    or manifest_payload.get("code_fingerprint_sha256")
                    or ""
                ).strip()
                raw_count = existing.get("code_fingerprint_file_count")
                if not (isinstance(raw_count, int) and raw_count >= 0):
                    try:
                        observed_count = int(manifest_payload.get("code_fingerprint_file_count"))
                    except (TypeError, ValueError):
                        observed_count = None
                    existing["code_fingerprint_file_count"] = (
                        int(observed_count) if isinstance(observed_count, int) and observed_count >= 0 else ""
                    )
            write_run_contract(self.ctx.run_contract_path, existing, allow_open=False)
            self.ctx.run_contract_payload = dict(existing)
            failure_note["run_contract_closeout_ok"] = True
        except (OSError, RuntimeError, ValueError) as exc:
            failure_note["run_contract_closeout_error"] = f"{exc.__class__.__name__}:{exc}"

        failure_note["closeout_elapsed_sec"] = round(max(0.0, time.monotonic() - closeout_start_mono), 3)
        (self.ctx.report_root / "failure_finalize.json").write_text(
            json.dumps(failure_note, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._write_state()

    def phase_preflight(self) -> None:
        entry = [
            self._condition("session_not_started", self.ctx.current_phase == "", f"current_phase={self.ctx.current_phase or 'none'}"),
            self._condition("canonical_config_exists", self.ctx.config_path.exists(), str(self.ctx.config_path)),
        ]
        self._phase_enter("preflight", entry)

        prev_docker_mode = os.environ.get("BRO_DOCKER_MODE")
        os.environ["BRO_DOCKER_MODE"] = "1"
        try:
            cfg = load_execution_config(self.ctx.config_path)
        finally:
            if prev_docker_mode is None:
                os.environ.pop("BRO_DOCKER_MODE", None)
            else:
                os.environ["BRO_DOCKER_MODE"] = prev_docker_mode
        mode = str(cfg.get("mode") or "").strip().lower()
        profile_name = str((cfg.get("profile") or {}).get("name") or "").strip()
        runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), dict) else {}
        storage = cfg.get("storage", {}) if isinstance(cfg.get("storage"), dict) else {}
        cfg_dir = self.ctx.config_path.parent
        observed_fingerprint = str((cfg.get("_meta") or {}).get("effective_config_sha256") or "").strip().lower()
        expected_fingerprint = str(runtime.get("paper_expected_config_fingerprint_sha256") or "").strip().lower()
        expected_profile = str(runtime.get("paper_expected_profile_name") or "").strip()
        resolved_log = _resolve_runtime_path(
            config_dir=cfg_dir,
            raw_value=str(storage.get("log_dir", "")),
            fallback=CANONICAL_LOG_DIR,
        )
        resolved_state = _resolve_runtime_path(
            config_dir=cfg_dir,
            raw_value=str(storage.get("state_path", "")),
            fallback=CANONICAL_STATE_PATH,
        )
        guard_stop_path_container = str(runtime.get("guard_stop_file") or "").strip()
        setup_lock_enabled = bool(runtime.get("paper_enforce_setup_lock", False))

        self.ctx.log_dir = resolved_log
        self.ctx.state_path = resolved_state
        self.ctx.resolved_profile_name = profile_name
        self.ctx.resolved_config_fingerprint_sha256 = observed_fingerprint
        self.ctx.container_config_path = _to_container_config_path(self.ctx.config_path)
        self.ctx.container_log_dir = _to_container_logs_path(resolved_log)
        self.ctx.container_guard_stop_path = (
            guard_stop_path_container
            or str(PurePosixPath(self.ctx.container_log_dir) / "guard_stop.txt")
        )
        self.ctx.initialize_paths()

        exit_conditions = [
            self._condition("mode_is_paper", mode == "paper", f"mode={mode or 'missing'}"),
            self._condition(
                "profile_matches_expected",
                profile_name == self.ctx.expected_profile_name,
                f"profile={profile_name or 'missing'} expected={self.ctx.expected_profile_name or 'missing'}",
            ),
            self._condition("setup_lock_enabled", setup_lock_enabled, f"paper_enforce_setup_lock={setup_lock_enabled}"),
            self._condition(
                "setup_lock_profile_matches_expected",
                bool(expected_profile) and (expected_profile == profile_name),
                f"expected={expected_profile or 'missing'} observed={profile_name or 'missing'}",
            ),
            self._condition(
                "setup_lock_fingerprint_matches_meta",
                bool(expected_fingerprint) and (expected_fingerprint == observed_fingerprint),
                f"expected={expected_fingerprint or 'missing'} observed={observed_fingerprint or 'missing'}",
            ),
            self._condition(
                "validation_script_exists",
                pathlib.Path(self.ctx.validation_script_path).exists(),
                str(self.ctx.validation_script_path),
            ),
        ]
        if self.ctx.require_canonical_roots:
            exit_conditions.extend(
                [
                    self._condition(
                        "canonical_log_root",
                        self.ctx.log_dir.resolve() == CANONICAL_LOG_DIR.resolve(),
                        f"resolved={self.ctx.log_dir}",
                    ),
                    self._condition(
                        "canonical_state_root",
                        self.ctx.state_path.resolve() == CANONICAL_STATE_PATH.resolve(),
                        f"resolved={self.ctx.state_path}",
                    ),
                ]
            )
        else:
            exit_conditions.extend(
                [
                    self._condition(
                        "noncanonical_log_root",
                        self.ctx.log_dir.resolve() != CANONICAL_LOG_DIR.resolve(),
                        f"resolved={self.ctx.log_dir}",
                    ),
                    self._condition(
                        "noncanonical_state_root",
                        self.ctx.state_path.resolve() != CANONICAL_STATE_PATH.resolve(),
                        f"resolved={self.ctx.state_path}",
                    ),
                ]
            )
        self._phase_exit(exit_conditions)

    def phase_start(self) -> None:
        self.ctx.run_manifest_path = (self.ctx.log_dir / f"run_manifest_{self.ctx.run_id}.json").resolve()
        self.ctx.run_contract_path = run_contract_path(log_dir=self.ctx.log_dir, run_id=self.ctx.run_id)
        if not str(self.ctx.run_id or "").strip():
            raise RuntimeError("run_id_missing_before_start_phase")
        if self.ctx.run_manifest_path.exists():
            raise RuntimeError(f"run_manifest_preexisting_for_run_id:{self.ctx.run_manifest_path}")
        entry = [
            self._condition("preflight_completed", self.ctx.current_phase == "preflight", f"current={self.ctx.current_phase}"),
            self._condition("deploy_script_exists", DEPLOY_SCRIPT.exists(), str(DEPLOY_SCRIPT)),
            self._condition("run_id_present", bool(self.ctx.run_id), f"run_id={self.ctx.run_id or 'missing'}"),
            self._condition(
                "run_manifest_not_preexisting",
                not self.ctx.run_manifest_path.exists(),
                str(self.ctx.run_manifest_path),
            ),
        ]
        sessions_root = (self.ctx.log_dir / "sessions").resolve()
        open_conflicts: List[str] = []
        if sessions_root.exists():
            for state_path in sorted(sessions_root.glob("*/session_state.json")):
                try:
                    payload = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                other_session_id = str(payload.get("session_id") or state_path.parent.name).strip()
                if not other_session_id or other_session_id == self.ctx.session_id:
                    continue
                other_session_type = str(payload.get("session_type") or "").strip()
                if other_session_type and other_session_type != str(self.ctx.session_type or "paper_canonical"):
                    continue
                other_phase = str(payload.get("phase") or "").strip()
                contract_path_raw = str(payload.get("run_contract_path") or "").strip()
                contract_phase = ""
                contract_stop_ts = ""
                if contract_path_raw:
                    try:
                        contract_payload = json.loads(pathlib.Path(contract_path_raw).resolve().read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError, UnicodeError):
                        contract_payload = {}
                    if isinstance(contract_payload, dict):
                        contract_phase = str(contract_payload.get("phase") or "").strip()
                        contract_stop_ts = str(contract_payload.get("stop_ts") or "").strip()
                if contract_stop_ts:
                    continue
                if other_phase == "complete":
                    continue
                other_run_id = str(payload.get("run_id") or "").strip()
                open_conflicts.append(
                    "session="
                    + f"{other_session_id}:run={other_run_id or 'missing'}:phase={other_phase or 'missing'}:"
                    + f"contract_phase={contract_phase or 'missing'}"
                )
        if open_conflicts:
            raise RuntimeError("concurrent_open_canonical_session:" + ";".join(open_conflicts))

        self._phase_enter("start", entry)

        provisional_start_ts = utc_iso()
        default_status_path, default_events_path = _default_status_events_paths(self.ctx.log_dir)
        provisional_contract = build_run_contract(
            session_id=self.ctx.session_id,
            run_id=self.ctx.run_id,
            phase="start",
            session_type=str(self.ctx.session_type or "paper_canonical"),
            authority_level="authoritative",
            allowed_actions=list(CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            manifest_path=self.ctx.run_manifest_path,
            log_root=self.ctx.log_dir,
            state_root=self.ctx.state_path.parent,
            start_ts=provisional_start_ts,
            stop_ts="",
            evidence_slice_start_ts=provisional_start_ts,
            evidence_slice_end_ts="",
            status_path=str(default_status_path),
            events_path=str(default_events_path),
            errors_path="",
            git_commit="",
            config_fingerprint_sha256="",
            code_fingerprint_sha256="",
            code_fingerprint_file_count="",
        )
        write_run_contract(self.ctx.run_contract_path, provisional_contract, allow_open=True)
        self.ctx.run_contract_payload = provisional_contract
        self._write_state()

        cmd = [
            str(DEPLOY_SCRIPT),
            "--wait-sec",
            str(int(max(1.0, float(self.ctx.wait_sec)))),
            "--no-verify",
            "--run-id",
            self.ctx.run_id,
        ]
        if self.ctx.do_build:
            cmd.append("--build")
        else:
            cmd.append("--no-build")
        deploy_env = {
            "BRO_CONFIG_PATH": str(self.ctx.config_path),
            "BRO_CONFIG_CONTAINER_PATH": str(self.ctx.container_config_path),
            "BRO_LOG_DIR": str((ROOT_DIR / "logs_exec").resolve()),
            "BRO_DATA_DIR": str((ROOT_DIR / "data").resolve()),
            "BRO_INTERNAL_SESSION_CALL": "1",
            "BRO_CANONICAL_SESSION_TOKEN": str(self.ctx.session_token),
            "BRO_CANONICAL_SESSION_CONTEXT_FILE_HOST": str(self.ctx.guardian_context_path),
            "BRO_CANONICAL_SESSION_CONTEXT_FILE": _to_container_logs_path(self.ctx.guardian_context_path),
            "BRO_GUARDIAN_LOG_DIR": str(self.ctx.container_log_dir),
            "BRO_GUARDIAN_GUARD_STOP_FILE": str(self.ctx.container_guard_stop_path),
            "BRO_GUARDIAN_SESSION_CONTEXT_FILE": _to_container_logs_path(self.ctx.guardian_context_path),
            "BRO_RUN_ID": self.ctx.run_id,
        }
        if self.ctx.allow_noncanonical_config:
            deploy_env["BRO_ALLOW_NONCANONICAL_PAPER_CONFIG"] = "1"
        (self.ctx.report_root / "start_command.json").write_text(
            json.dumps(
                {
                    "cmd": list(cmd),
                    "cwd": str(ROOT_DIR),
                    "env_keys": sorted(deploy_env.keys()),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            proc = self._run_cmd(
                cmd,
                env=deploy_env,
            )
        except subprocess.CalledProcessError as exc:
            (self.ctx.report_root / "start_stdout.log").write_text(str(exc.stdout or ""), encoding="utf-8")
            (self.ctx.report_root / "start_stderr.log").write_text(str(exc.stderr or ""), encoding="utf-8")
            (self.ctx.report_root / "start_command_failure.json").write_text(
                json.dumps(
                    {
                        "cmd": [str(part) for part in getattr(exc, "cmd", cmd)],
                        "cwd": str(ROOT_DIR),
                        "returncode": int(getattr(exc, "returncode", 1)),
                        "run_id": self.ctx.run_id,
                        "session_id": self.ctx.session_id,
                        "ts_utc": utc_iso(),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            raise
        (self.ctx.report_root / "start_stdout.log").write_text(str(proc.stdout or ""), encoding="utf-8")
        (self.ctx.report_root / "start_stderr.log").write_text(str(proc.stderr or ""), encoding="utf-8")

        # Manifest is observability/evidence only, not lifecycle authority.
        # Keep this probe non-blocking for diagnostics; fingerprint consistency
        # checks use an explicit bounded manifest retrieval below.
        manifest_observation = _observe_manifest_for_run_id(
            log_dir=self.ctx.log_dir,
            run_id=self.ctx.run_id,
            timeout_sec=0.0,
        )
        observed_manifest = bool(manifest_observation.get("observed"))
        observed_run_id = str(manifest_observation.get("observed_run_id") or "")
        observed_manifest_path = str(manifest_observation.get("manifest_path") or "")
        if observed_manifest_path:
            self.ctx.run_manifest_path = pathlib.Path(observed_manifest_path).resolve()
        (self.ctx.report_root / "start_manifest_observation.json").write_text(
            json.dumps(manifest_observation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        manifest_wait_sec = max(1.0, min(10.0, float(self.ctx.wait_sec)))
        manifest_load_error = ""
        manifest_evidence_available = False
        try:
            manifest_payload = _load_manifest_for_run_with_timeout(
                log_dir=self.ctx.log_dir,
                run_id=self.ctx.run_id,
                timeout_sec=manifest_wait_sec,
            )
            manifest_evidence_available = True
        except RuntimeError as exc:
            manifest_payload = {}
            manifest_load_error = str(exc)
        manifest_code_hash = str(manifest_payload.get("code_fingerprint_sha256") or "").strip()
        manifest_code_count: Optional[int] = None
        try:
            raw_count = manifest_payload.get("code_fingerprint_file_count")
            if isinstance(raw_count, (int, float, str)) and str(raw_count).strip():
                manifest_code_count = int(raw_count)
        except (TypeError, ValueError):
            manifest_code_count = None
        workspace_code_hash, workspace_code_count = _compute_workspace_code_fingerprint(ROOT_DIR)
        fingerprint_match = bool(
            manifest_evidence_available
            and bool(manifest_code_hash)
            and manifest_code_hash == workspace_code_hash
            and manifest_code_count == workspace_code_count
        )
        (self.ctx.report_root / "start_code_fingerprint_check.json").write_text(
            json.dumps(
                {
                    "run_id": self.ctx.run_id,
                    "manifest_path": str(self.ctx.run_manifest_path),
                    "manifest_observed": observed_manifest,
                    "manifest_wait_sec": manifest_wait_sec,
                    "manifest_evidence_available": manifest_evidence_available,
                    "manifest_load_error": manifest_load_error,
                    "manifest_code_fingerprint_sha256": manifest_code_hash,
                    "manifest_code_fingerprint_file_count": manifest_code_count,
                    "workspace_code_fingerprint_sha256": workspace_code_hash,
                    "workspace_code_fingerprint_file_count": workspace_code_count,
                    "match": fingerprint_match,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        exit_conditions = [
            self._condition("run_id_bound", bool(self.ctx.run_id), f"run_id={self.ctx.run_id}"),
            self._condition(
                "run_contract_written",
                self.ctx.run_contract_path.exists(),
                str(self.ctx.run_contract_path),
            ),
            self._condition(
                "manifest_consistency_non_authoritative",
                (not observed_manifest) or (observed_run_id == self.ctx.run_id),
                (
                    f"observed={observed_run_id or 'missing'} "
                    f"expected={self.ctx.run_id} observed_manifest={observed_manifest}"
                ),
            ),
            self._condition(
                "runtime_code_fingerprint_matches_workspace",
                fingerprint_match,
                (
                    f"manifest_evidence_available={manifest_evidence_available} "
                    f"manifest_load_error={manifest_load_error or 'none'} "
                    f"manifest_hash={manifest_code_hash or 'missing'} "
                    f"workspace_hash={workspace_code_hash} "
                    f"manifest_count={manifest_code_count if manifest_code_count is not None else 'missing'} "
                    f"workspace_count={workspace_code_count}"
                ),
            ),
        ]
        self._phase_exit(exit_conditions)

    def phase_active(self) -> None:
        entry = [
            self._condition("start_completed", self.ctx.current_phase == "start", f"current={self.ctx.current_phase}"),
            self._condition("run_id_present", bool(self.ctx.run_id), f"run_id={self.ctx.run_id or 'missing'}"),
        ]
        self._phase_enter("active", entry)
        requested_active_sec = max(1.0, float(self.ctx.active_minutes) * 60.0)
        runtime_duration_sec: Optional[float] = None
        cutoff_buffer_sec = 30.0
        try:
            manifest_payload = _load_manifest_for_run(self.ctx.log_dir, self.ctx.run_id)
            runtime_cfg = manifest_payload.get("config", {}).get("runtime", {})
            runtime_duration_min = float(runtime_cfg.get("duration_min")) if isinstance(runtime_cfg, dict) else 0.0
            if runtime_duration_min > 0:
                runtime_duration_sec = max(0.0, runtime_duration_min * 60.0)
        except (RuntimeError, OSError, json.JSONDecodeError, ValueError, TypeError):
            runtime_duration_sec = None
        effective_active_sec = requested_active_sec
        if runtime_duration_sec is not None and requested_active_sec >= runtime_duration_sec:
            effective_active_sec = max(1.0, runtime_duration_sec - cutoff_buffer_sec)
        contract_start_ts = parse_ts(self.ctx.run_contract_payload.get("start_ts"))
        elapsed_before_active = 0.0
        if contract_start_ts is not None:
            elapsed_before_active = max(0.0, (utc_now() - contract_start_ts).total_seconds())
        timing = _compute_active_timing(
            requested_active_sec=requested_active_sec,
            runtime_duration_sec=runtime_duration_sec,
            cutoff_buffer_sec=cutoff_buffer_sec,
            pre_active_elapsed_sec=elapsed_before_active,
        )
        active_wait_sec = float(timing["active_wait_sec"])
        effective_active_sec = float(timing["effective_active_sec"])
        (self.ctx.report_root / "active_timing.json").write_text(
            json.dumps(timing, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        _write_host_time_sync_artifact(
            report_root=self.ctx.report_root,
            artifact_name="host_time_sync_active_start.json",
            session_id=self.ctx.session_id,
            run_id=self.ctx.run_id,
            phase="active_start",
            requested_active_minutes=self.ctx.active_minutes,
        )
        _append_host_time_sync_sample_artifact(
            report_root=self.ctx.report_root,
            artifact_name="host_time_sync_active_samples.jsonl",
            session_id=self.ctx.session_id,
            run_id=self.ctx.run_id,
            phase="active_sample",
            requested_active_minutes=self.ctx.active_minutes,
            elapsed_active_sec=0.0,
        )
        start_mono = time.monotonic()
        current_mono = start_mono
        next_host_sync_sample_elapsed_sec = 60.0
        while (current_mono - start_mono) < active_wait_sec:
            time.sleep(min(5.0, max(0.5, max(1.0, active_wait_sec) / 12.0)))
            current_mono = time.monotonic()
            elapsed_active_sec = max(0.0, current_mono - start_mono)
            while elapsed_active_sec >= next_host_sync_sample_elapsed_sec:
                _append_host_time_sync_sample_artifact(
                    report_root=self.ctx.report_root,
                    artifact_name="host_time_sync_active_samples.jsonl",
                    session_id=self.ctx.session_id,
                    run_id=self.ctx.run_id,
                    phase="active_sample",
                    requested_active_minutes=self.ctx.active_minutes,
                    elapsed_active_sec=next_host_sync_sample_elapsed_sec,
                )
                next_host_sync_sample_elapsed_sec += 60.0
            ps_during_active = _docker_compose_ps_lines()
            maker_up_during_active = _service_up(ps_during_active, "bro-maker")
            guardian_up_during_active = _service_up(ps_during_active, "bro-guardian")
            if (not maker_up_during_active) or (not guardian_up_during_active):
                (self.ctx.report_root / "active_compose_ps.early_exit.log").write_text(
                    "\n".join(ps_during_active) + "\n",
                    encoding="utf-8",
                )
                raise RuntimeError(
                    "active_phase_stack_died_early:"
                    + f"maker_up={maker_up_during_active}:guardian_up={guardian_up_during_active}:"
                    + f"elapsed_sec={elapsed_active_sec:.3f}:"
                    + f"active_wait_sec={active_wait_sec:.3f}"
                )

        ps_before_stop = _docker_compose_ps_lines()
        maker_up_before_stop = _service_up(ps_before_stop, "bro-maker")
        guardian_up_before_stop = _service_up(ps_before_stop, "bro-guardian")
        (self.ctx.report_root / "active_compose_ps.before_stop.log").write_text(
            "\n".join(ps_before_stop) + "\n",
            encoding="utf-8",
        )
        # Freeze the stack before validate_active so restart policies cannot create
        # a second run segment under the same run_id.
        freeze_proc = self._run_cmd(["docker", "compose", "stop", "bro-maker", "bro-guardian"])
        (self.ctx.report_root / "active_freeze_stdout.log").write_text(str(freeze_proc.stdout or ""), encoding="utf-8")
        (self.ctx.report_root / "active_freeze_stderr.log").write_text(str(freeze_proc.stderr or ""), encoding="utf-8")
        ps_after_stop = _docker_compose_ps_lines()
        maker_up_after_stop = _service_up(ps_after_stop, "bro-maker")
        guardian_up_after_stop = _service_up(ps_after_stop, "bro-guardian")
        (self.ctx.report_root / "active_compose_ps.after_stop.log").write_text(
            "\n".join(ps_after_stop) + "\n",
            encoding="utf-8",
        )
        _write_host_time_sync_artifact(
            report_root=self.ctx.report_root,
            artifact_name="host_time_sync_active_stop.json",
            session_id=self.ctx.session_id,
            run_id=self.ctx.run_id,
            phase="active_stop",
            requested_active_minutes=self.ctx.active_minutes,
        )

        elapsed_total_sec = time.monotonic() - start_mono
        if bool(timing.get("runtime_capped")) and contract_start_ts is not None:
            elapsed_total_sec = max(0.0, (utc_now() - contract_start_ts).total_seconds())
        exit_conditions = [
            self._condition(
                "active_duration_elapsed",
                elapsed_total_sec >= effective_active_sec,
                (
                    f"requested_sec={requested_active_sec:.2f} "
                    f"effective_sec={effective_active_sec:.2f} "
                    f"runtime_duration_sec={runtime_duration_sec if runtime_duration_sec is not None else 'na'}"
                ),
            ),
            self._condition("bro_maker_up_before_freeze", maker_up_before_stop, "docker compose ps (before stop)"),
            self._condition("bro_guardian_up_before_freeze", guardian_up_before_stop, "docker compose ps (before stop)"),
            self._condition(
                "runtime_frozen_before_validate_active",
                (not maker_up_after_stop) and (not guardian_up_after_stop),
                "docker compose stop bro-maker bro-guardian",
            ),
        ]
        self._phase_exit(exit_conditions)

    def phase_validate_active(self) -> None:
        entry = [
            self._condition("active_completed", self.ctx.current_phase == "active", f"current={self.ctx.current_phase}"),
            self._condition("run_contract_exists", self.ctx.run_contract_path.exists(), str(self.ctx.run_contract_path)),
        ]
        self._phase_enter("validate_active", entry)
        out_dir = (self.ctx.report_root / "validate_active").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        actionable_ok = True
        cmds = [
            (
                "run_integrity",
                [
                    "./.venv/bin/python",
                    "scripts/run_integrity_audit.py",
                    "--log-dir",
                    str(self.ctx.log_dir),
                    "--run-id",
                    self.ctx.run_id,
                    "--session-phase",
                    "validate_active",
                    "--run-contract",
                    str(self.ctx.run_contract_path),
                    "--min-status-rows",
                    "1",
                    "--max-status-age-sec",
                    "600",
                    "--out",
                    str(out_dir / "run_integrity.json"),
                ],
                True,
            ),
            (
                "websocket_reliability",
                [
                    "./.venv/bin/python",
                    "scripts/websocket_reliability_gate.py",
                    "--log-dir",
                    str(self.ctx.log_dir),
                    "--run-id",
                    self.ctx.run_id,
                    "--session-phase",
                    "validate_active",
                    "--run-contract",
                    str(self.ctx.run_contract_path),
                    "--min-status-rows",
                    "1",
                    "--max-book-feed-down-ratio",
                    "1.0",
                    "--max-chainlink-down-ratio",
                    "1.0",
                    "--out",
                    str(out_dir / "websocket_reliability_gate.json"),
                ],
                True,
            ),
            (
                "nightly_soak_report",
                [
                    "./.venv/bin/python",
                    "scripts/nightly_soak_report.py",
                    "--log-dir",
                    str(self.ctx.log_dir),
                    "--run-id",
                    self.ctx.run_id,
                    "--session-phase",
                    "validate_active",
                    "--run-contract",
                    str(self.ctx.run_contract_path),
                    "--max-lines-per-file",
                    str(int(self.ctx.max_lines_per_file)),
                    "--out",
                    str(out_dir / "nightly_soak_report.json"),
                ],
                True,
            ),
        ]

        for name, cmd, actionable in cmds:
            timeout_note = ""
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT_DIR),
                    text=True,
                    capture_output=True,
                    timeout=float(CANONICAL_ACTIVE_VALIDATION_TIMEOUT_SEC),
                )
            except subprocess.TimeoutExpired as exc:
                proc = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=124,
                    stdout=str(exc.stdout or ""),
                    stderr=str(exc.stderr or ""),
                )
                timeout_note = (
                    f"subprocess_timeout:{name}:"
                    + f"timeout_sec={float(CANONICAL_ACTIVE_VALIDATION_TIMEOUT_SEC):.1f}"
                )
            (out_dir / f"{name}.stdout.log").write_text(str(proc.stdout or ""), encoding="utf-8")
            stderr_text = str(proc.stderr or "")
            if timeout_note:
                stderr_text = (stderr_text + "\n" if stderr_text else "") + timeout_note
            (out_dir / f"{name}.stderr.log").write_text(stderr_text, encoding="utf-8")
            if actionable and proc.returncode != 0:
                actionable_ok = False

        exit_conditions = [
            self._condition("active_actionable_validations_passed", actionable_ok, "run_integrity+websocket+nightly"),
        ]
        self._phase_exit(exit_conditions)

    def phase_stop(self) -> None:
        entry = [
            self._condition("validate_active_completed", self.ctx.current_phase == "validate_active", f"current={self.ctx.current_phase}"),
            self._condition("run_id_present", bool(self.ctx.run_id), f"run_id={self.ctx.run_id or 'missing'}"),
        ]
        self._phase_enter("stop", entry)

        stop_proc = self._run_cmd(["docker", "compose", "down"])
        (self.ctx.report_root / "stop_stdout.log").write_text(str(stop_proc.stdout or ""), encoding="utf-8")
        (self.ctx.report_root / "stop_stderr.log").write_text(str(stop_proc.stderr or ""), encoding="utf-8")
        ps_lines = _docker_compose_ps_lines()
        (self.ctx.report_root / "stop_compose_ps.log").write_text("\n".join(ps_lines) + "\n", encoding="utf-8")
        maker_present = _service_present(ps_lines, "bro-maker")
        guardian_present = _service_present(ps_lines, "bro-guardian")

        manifest = _load_manifest_for_run_optional(self.ctx.log_dir, self.ctx.run_id)
        (self.ctx.report_root / "stop_manifest_observation.json").write_text(
            json.dumps(
                {
                    "manifest_observed": bool(manifest),
                    "manifest_path": str(self.ctx.run_manifest_path),
                    "run_id": self.ctx.run_id,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        start_ts = str(manifest.get("start_ts") or "").strip() or str(self.ctx.run_contract_payload.get("start_ts") or "")
        stop_ts = str(manifest.get("end_ts") or "").strip() or utc_iso()
        start_dt = parse_ts(start_ts)
        stop_dt = parse_ts(stop_ts)
        if start_dt is None:
            start_dt = utc_now()
            start_ts = utc_iso(start_dt)
        if stop_dt is None:
            stop_dt = utc_now()
            stop_ts = utc_iso(stop_dt)
        if stop_dt < start_dt:
            stop_dt = start_dt
            stop_ts = utc_iso(stop_dt)

        status_path = _resolve_runtime_path(
            config_dir=ROOT_DIR,
            raw_value=str(manifest.get("status_path") or ""),
            fallback=self.ctx.log_dir / f"status_{start_dt.date().isoformat()}.jsonl",
        )
        events_path = _resolve_runtime_path(
            config_dir=ROOT_DIR,
            raw_value=str(manifest.get("events_path") or ""),
            fallback=self.ctx.log_dir / f"events_{start_dt.date().isoformat()}.jsonl",
        )
        status_candidates = _stream_source_paths_for_window(
            log_dir=self.ctx.log_dir,
            prefix="status",
            start_dt=start_dt,
            stop_dt=stop_dt,
            preferred_path=status_path,
        )
        events_candidates = _stream_source_paths_for_window(
            log_dir=self.ctx.log_dir,
            prefix="events",
            start_dt=start_dt,
            stop_dt=stop_dt,
            preferred_path=events_path,
        )
        errors_candidates: List[pathlib.Path] = []
        for date_offset in range(0, (stop_dt.date() - start_dt.date()).days + 1):
            day = start_dt.date() + dt.timedelta(days=date_offset)
            errors_candidates.append((self.ctx.log_dir / f"errors_{day.isoformat()}.jsonl").resolve())
        if not errors_candidates:
            errors_candidates = sorted(self.ctx.log_dir.glob("errors_*.jsonl"))

        slices_dir = (self.ctx.session_root / "slices").resolve()
        status_slice = slices_dir / "status_slice.jsonl"
        events_slice = slices_dir / "events_slice.jsonl"
        errors_slice = slices_dir / "errors_slice.jsonl"
        status_count = _filter_rows_for_run(
            source_paths=status_candidates,
            destination_path=status_slice,
            run_id=self.ctx.run_id,
            start_ts=start_dt,
            end_ts=stop_dt,
        )
        event_count = _filter_rows_for_run(
            source_paths=events_candidates,
            destination_path=events_slice,
            run_id=self.ctx.run_id,
            start_ts=start_dt,
            end_ts=stop_dt,
        )
        error_count = _filter_rows_for_run(
            source_paths=errors_candidates,
            destination_path=errors_slice,
            run_id=self.ctx.run_id,
            start_ts=start_dt,
            end_ts=stop_dt,
        )

        contract = build_run_contract(
            session_id=self.ctx.session_id,
            run_id=self.ctx.run_id,
            phase="validate_postrun",
            session_type=str(self.ctx.session_type or "paper_canonical"),
            authority_level="authoritative",
            allowed_actions=list(CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS),
            manifest_path=self.ctx.run_manifest_path,
            log_root=self.ctx.log_dir,
            state_root=self.ctx.state_path.parent,
            start_ts=start_ts,
            stop_ts=stop_ts,
            evidence_slice_start_ts=start_ts,
            evidence_slice_end_ts=stop_ts,
            status_path=str(status_path),
            events_path=str(events_path),
            errors_path=";".join(str(p) for p in errors_candidates),
            status_slice_path=str(status_slice),
            events_slice_path=str(events_slice),
            errors_slice_path=str(errors_slice),
            git_commit=str(manifest.get("git_commit") or "").strip(),
            config_fingerprint_sha256=str(manifest.get("config_fingerprint_sha256") or "").strip(),
            code_fingerprint_sha256=str(manifest.get("code_fingerprint_sha256") or "").strip(),
            code_fingerprint_file_count=(
                int(manifest.get("code_fingerprint_file_count"))
                if isinstance(manifest.get("code_fingerprint_file_count"), (int, float, str))
                and str(manifest.get("code_fingerprint_file_count")).strip()
                and str(manifest.get("code_fingerprint_file_count")).strip().lstrip("-").isdigit()
                else ""
            ),
        )
        write_run_contract(self.ctx.run_contract_path, contract, allow_open=False)
        self.ctx.run_contract_payload = contract

        (self.ctx.report_root / "slice_counts.json").write_text(
            json.dumps(
                {
                    "status_rows": int(status_count),
                    "event_rows": int(event_count),
                    "error_rows": int(error_count),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        exit_conditions = [
            self._condition("docker_stack_stopped", (not maker_present) and (not guardian_present), "docker compose ps"),
            self._condition("run_contract_closed", bool(str(contract.get("stop_ts") or "").strip()), f"stop_ts={contract.get('stop_ts')}"),
            self._condition("status_slice_exists", status_slice.exists(), str(status_slice)),
            self._condition("events_slice_exists", events_slice.exists(), str(events_slice)),
            self._condition("errors_slice_exists", errors_slice.exists(), str(errors_slice)),
        ]
        self._phase_exit(exit_conditions)

    def phase_validate_postrun(self) -> None:
        entry = [
            self._condition("stop_completed", self.ctx.current_phase == "stop", f"current={self.ctx.current_phase}"),
            self._condition("run_contract_closed", self.ctx.run_contract_path.exists(), str(self.ctx.run_contract_path)),
        ]
        self._phase_enter("validate_postrun", entry)

        cmd = [
            str(self.ctx.validation_script_path),
            self.ctx.run_id,
            "--config",
            str(self.ctx.config_path),
            "--log-dir",
            str(self.ctx.log_dir),
            "--session-phase",
            "validate_postrun",
            "--run-contract",
            str(self.ctx.run_contract_path),
            "--max-lines-per-file",
            str(int(self.ctx.max_lines_per_file)),
        ]
        timeout_note = ""
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT_DIR),
                text=True,
                capture_output=True,
                timeout=float(CANONICAL_POSTRUN_VALIDATION_TIMEOUT_SEC),
            )
        except subprocess.TimeoutExpired as exc:
            proc = subprocess.CompletedProcess(
                args=cmd,
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
            )
            validation_label = pathlib.Path(str(self.ctx.validation_script_path)).stem or "paper_validation"
            timeout_note = (
                f"subprocess_timeout:{validation_label}"
                + f":timeout_sec={float(CANONICAL_POSTRUN_VALIDATION_TIMEOUT_SEC):.1f}"
            )
        (self.ctx.report_root / "validate_postrun.stdout.log").write_text(str(proc.stdout or ""), encoding="utf-8")
        stderr_text = str(proc.stderr or "")
        if timeout_note:
            stderr_text = (stderr_text + "\n" if stderr_text else "") + timeout_note
        (self.ctx.report_root / "validate_postrun.stderr.log").write_text(stderr_text, encoding="utf-8")

        report_dir = (self.ctx.log_dir / "reports" / self.ctx.run_id).resolve()
        postrun_validation = summarize_postrun_validation(
            run_id=self.ctx.run_id,
            report_dir=report_dir,
            script_exit_code=proc.returncode,
        )
        self.ctx.postrun_validation = dict(postrun_validation)
        report_dir.mkdir(parents=True, exist_ok=True)
        validation_path = (
            report_dir / str(self.ctx.validation_artifact_name or "canonical_paper_validation.json")
        ).resolve()
        validation_payload = {
            "schema_version": 1,
            "ts_utc": utc_iso(),
            "session_phase": "validate_postrun",
            **postrun_validation,
        }
        validation_path.write_text(
            json.dumps(validation_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._write_state()

        exit_conditions = [
            self._condition(
                "postrun_validation_execution_ok",
                not bool(postrun_validation.get("execution_error", True)),
                (
                    f"status={postrun_validation.get('status')} "
                    f"exit_code={postrun_validation.get('script_exit_code')} "
                    f"reports_complete={postrun_validation.get('reports_complete')}"
                ),
            ),
            self._condition(
                "postrun_validation_reports_complete",
                bool(postrun_validation.get("reports_complete", False)),
                (
                    f"missing={','.join(str(x) for x in postrun_validation.get('missing_reports', [])) or 'none'} "
                    f"parse_errors={','.join(str(x) for x in postrun_validation.get('parse_error_reports', [])) or 'none'}"
                ),
            ),
            self._condition(
                "postrun_validation_summary_consistent",
                bool(postrun_validation.get("summary_exit_matches", False)),
                f"summary_exit_matches={postrun_validation.get('summary_exit_matches')}",
            ),
            self._condition(
                "postrun_validation_determinism_consistent",
                bool(postrun_validation.get("determinism_consistent", False)),
                f"determinism_consistent={postrun_validation.get('determinism_consistent')}",
            ),
        ]
        self._phase_exit(exit_conditions)

    def phase_archive_export(self) -> None:
        entry = [
            self._condition(
                "validate_postrun_completed",
                self.ctx.current_phase == "validate_postrun",
                f"current={self.ctx.current_phase}",
            )
        ]
        self._phase_enter("archive_export", entry)
        archive_path = ROOT_DIR / "exports" / f"paper_session_{self.ctx.run_id}.zip"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if self.ctx.archive_export:
            proc = self._run_cmd(
                [
                    "zip",
                    "-rq",
                    str(archive_path),
                    str(self.ctx.session_root.relative_to(ROOT_DIR)),
                    str(self.ctx.run_contract_path.relative_to(ROOT_DIR)),
                ]
            )
            (self.ctx.report_root / "archive_stdout.log").write_text(str(proc.stdout or ""), encoding="utf-8")
            (self.ctx.report_root / "archive_stderr.log").write_text(str(proc.stderr or ""), encoding="utf-8")
            exit_conditions = [
                self._condition("archive_created", archive_path.exists(), str(archive_path)),
            ]
        else:
            exit_conditions = [
                self._condition("archive_skipped", True, "archive_export_disabled"),
            ]
        self._phase_exit(exit_conditions)

    def phase_complete(self) -> None:
        entry = [
            self._condition(
                "archive_phase_completed",
                self.ctx.current_phase == "archive_export",
                f"current={self.ctx.current_phase}",
            )
        ]
        self._phase_enter("complete", entry)
        exit_conditions = [
            self._condition("session_complete", True, f"run_id={self.ctx.run_id}"),
        ]
        self._phase_exit(exit_conditions)

    def run(self) -> Dict[str, Any]:
        try:
            self.phase_preflight()
            self.phase_start()
            self.phase_active()
            self.phase_validate_active()
            self.phase_stop()
            self.phase_validate_postrun()
            self.phase_archive_export()
            self.phase_complete()
        except BaseException as exc:
            self._finalize_failure_closeout(exc)
            raise
        return {
            "session_id": self.ctx.session_id,
            "run_id": self.ctx.run_id,
            "session_type": str(self.ctx.session_type or ""),
            "phase": self.ctx.current_phase,
            "postrun_validation": dict(self.ctx.postrun_validation),
            "session_state_path": str(self.ctx.session_state_path),
            "run_contract_path": str(self.ctx.run_contract_path),
            "report_root": str(self.ctx.report_root),
        }


def build_common_parser(*, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--active-minutes", type=float, default=15.0, help="Active runtime duration in minutes")
    parser.add_argument("--wait-sec", type=float, default=25.0, help="Deploy wait seconds before active phase")
    parser.add_argument(
        "--build",
        dest="build_images",
        action="store_true",
        help="Build docker images during start phase (default behavior)",
    )
    parser.add_argument(
        "--no-build",
        dest="build_images",
        action="store_false",
        help="Skip docker image build during start phase (non-canonical fast path)",
    )
    parser.set_defaults(build_images=True)
    parser.add_argument("--archive-export", action="store_true", help="Create an archive artifact in exports/")
    parser.add_argument("--session-id", default="", help="Optional explicit session id")
    parser.add_argument("--run-id", default="", help="Optional explicit run id; defaults to a generated UUID")
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=int(os.environ.get("BRO_REPORT_MAX_LINES_PER_FILE", "50000")),
        help="Bound passed to postrun report tools; 0 means full-file scan",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    return build_common_parser(description="BRO canonical paper session runner")


def main() -> None:
    args = build_parser().parse_args()
    session_id = str(args.session_id or "").strip() or str(uuid.uuid4())
    run_id = str(args.run_id or "").strip() or str(uuid.uuid4())
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise SystemExit(f"invalid run_id (must be UUID): {run_id!r}") from exc
    ctx = SessionContext(
        session_id=session_id,
        run_id=run_id,
        session_token=str(uuid.uuid4()),
        config_path=CANONICAL_CONFIG_PATH,
        active_minutes=max(0.1, float(args.active_minutes)),
        wait_sec=max(1.0, float(args.wait_sec)),
        do_build=bool(args.build_images),
        archive_export=bool(args.archive_export),
        max_lines_per_file=max(0, int(args.max_lines_per_file)),
    )
    runner = SessionRunner(ctx)
    result = runner.run()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
