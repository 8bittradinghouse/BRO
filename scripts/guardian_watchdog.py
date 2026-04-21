#!/usr/bin/env python3
"""Watch Bro execution logs and arm an external guard stop file when risk signals degrade."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import pathlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from prodesk.canonical_authority import (
    ACTOR_GUARDIAN_WATCHDOG,
    CAPABILITY_GUARDIAN_CONTROL,
    AuthorityRequest,
    render_authority_denial,
    resolve_authority_decision,
)
from prodesk.runtime_semantics import resolve_guard_connectivity_requirements


def parse_ts(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def utc_iso(value: Optional[dt.datetime] = None) -> str:
    ts = value or dt.datetime.now(dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tail_jsonl(path: pathlib.Path, max_lines: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if max_lines <= 0 or not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in deque(fh, maxlen=max_lines):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return out
    return out


def _extract_run_id_from_manifest_name(path: pathlib.Path) -> str:
    # run_manifest_<uuid>.json
    stem = path.stem
    prefix = "run_manifest_"
    if not stem.startswith(prefix):
        return ""
    return stem[len(prefix) :].strip()


def _resolve_run_id(log_dir: pathlib.Path, *, explicit_run_id: str, auto_from_manifest: bool, max_files: int) -> str:
    explicit = explicit_run_id.strip()
    if explicit:
        return explicit
    if not auto_from_manifest:
        return ""
    manifests = list(log_dir.glob("run_manifest_*.json"))
    manifests.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)
    for path in manifests[: max(1, int(max_files))]:
        run_id = _extract_run_id_from_manifest_name(path)
        if run_id:
            return run_id
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            payload = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(payload, dict):
            value = str(payload.get("run_id") or "").strip()
            if value:
                return value
    return ""


@dataclass
class GuardAuthorityContext:
    authoritative: bool
    run_id: str
    run_contract_path: str
    session_phase: str
    session_id: str
    reason: str
    reason_code: str = ""
    reason_detail: str = ""
    decision_fields: Dict[str, Any] = field(default_factory=dict)
    denial_rendered: str = ""


def _resolve_authority_context(*, args: argparse.Namespace, log_dir: pathlib.Path) -> GuardAuthorityContext:
    explicit_run_id = str(getattr(args, "run_id", "") or "").strip()
    explicit_contract = str(getattr(args, "run_contract", "") or "").strip()
    explicit_phase = str(getattr(args, "session_phase", "") or "").strip().lower()
    explicit_session_id = str(getattr(args, "session_id", "") or "").strip()
    decision = resolve_authority_decision(
        AuthorityRequest(
            actor=ACTOR_GUARDIAN_WATCHDOG,
            action=CAPABILITY_GUARDIAN_CONTROL,
            log_dir=log_dir,
            session_context_file=(
                pathlib.Path(str(getattr(args, "session_context_file", "")).strip())
                if str(getattr(args, "session_context_file", "")).strip()
                else None
            ),
            session_token=str(getattr(args, "session_token", "") or os.getenv("BRO_CANONICAL_SESSION_TOKEN", "")).strip(),
            run_id=explicit_run_id,
            run_contract_path=pathlib.Path(explicit_contract) if explicit_contract else None,
            session_phase=explicit_phase,
            session_id=explicit_session_id,
            require_authoritative=True,
            allow_open_contract=True,
        )
    )
    decision_fields = dict(decision.as_log_fields())
    reason = str(decision.reason_code or "")
    if str(decision.reason_detail or ""):
        reason = f"{reason}:{decision.reason_detail}" if reason else str(decision.reason_detail)
    return GuardAuthorityContext(
        authoritative=bool(decision.authorized),
        run_id=str(decision.run_id or ""),
        run_contract_path=str(decision.run_contract_path or ""),
        session_phase=str(decision.session_phase or ""),
        session_id=str(decision.session_id or ""),
        reason=reason,
        reason_code=str(decision.reason_code or ""),
        reason_detail=str(decision.reason_detail or ""),
        decision_fields=decision_fields,
        denial_rendered=render_authority_denial(decision, prefix="canonical_guardian_authority_denied"),
    )


def latest_status_row(
    log_dir: pathlib.Path,
    *,
    max_lines: int,
    max_files: int = 3,
    run_id: str = "",
) -> Optional[Dict[str, Any]]:
    status_files = sorted(log_dir.glob("status_*.jsonl"))
    if not status_files:
        return None
    candidates = status_files[-max(1, int(max_files)) :]
    latest_row: Optional[Dict[str, Any]] = None
    latest_ts: Optional[dt.datetime] = None
    fallback_row: Optional[Dict[str, Any]] = None

    for path in candidates:
        rows = _tail_jsonl(path, max_lines=max_lines)
        if rows and not run_id:
            fallback_row = rows[-1]
        for row in rows:
            if run_id and str(row.get("run_id") or "").strip() != run_id:
                continue
            row_ts = parse_ts(row.get("ts_utc"))
            if row_ts is None:
                continue
            if latest_ts is None or row_ts > latest_ts:
                latest_ts = row_ts
                latest_row = row
    if latest_row is not None:
        return latest_row
    return fallback_row


def recent_error_rows(
    log_dir: pathlib.Path,
    *,
    now_utc: dt.datetime,
    window_sec: float,
    max_lines_per_file: int,
    run_id: str = "",
) -> List[Dict[str, Any]]:
    error_files = sorted(log_dir.glob("errors_*.jsonl"))
    if not error_files:
        return []
    min_ts = now_utc - dt.timedelta(seconds=max(0.0, float(window_sec)))
    out: List[Dict[str, Any]] = []
    for path in error_files[-2:]:
        for row in _tail_jsonl(path, max_lines=max_lines_per_file):
            if run_id and str(row.get("run_id") or "").strip() != run_id:
                continue
            ts = parse_ts(row.get("ts_utc"))
            if ts is None:
                continue
            if ts >= min_ts:
                out.append(row)
    return out


def evaluate_guard(
    *,
    status_row: Optional[Dict[str, Any]],
    now_utc: dt.datetime,
    guardian_started_utc: Optional[dt.datetime] = None,
    startup_elapsed_sec: float,
    startup_grace_sec: float,
    max_status_age_sec: float,
    recent_error_count: int,
    max_errors_in_window: int,
    mode_trigger_level: float,
    trigger_on_kill_switch: bool,
    require_chainlink_connected: bool,
    require_book_feed_connected: bool,
    chainlink_disconnect_min_age_sec: float,
    book_feed_disconnect_min_age_sec: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    details: Dict[str, Any] = {
        "startup_elapsed_sec": startup_elapsed_sec,
        "recent_error_count": recent_error_count,
    }
    if status_row is None:
        if startup_elapsed_sec >= startup_grace_sec:
            return True, "status_missing", details
        return False, "", details

    status_ts = parse_ts(status_row.get("ts_utc"))
    if status_ts is None:
        if startup_elapsed_sec >= startup_grace_sec:
            return True, "status_ts_invalid", details
        return False, "", details

    status_age = max(0.0, (now_utc - status_ts).total_seconds())
    details["status_age_sec"] = status_age
    details["status_ts_utc"] = utc_iso(status_ts)

    if status_age > max_status_age_sec:
        # Ignore stale rows from prior runs during startup grace.
        if (
            startup_elapsed_sec < startup_grace_sec
            and guardian_started_utc is not None
            and status_ts < guardian_started_utc
        ):
            details["status_stale_ignored_prestart"] = True
            return False, "", details
        return True, "status_stale", details

    if trigger_on_kill_switch and bool(status_row.get("kill_switch", False)):
        if (
            startup_elapsed_sec < startup_grace_sec
            and guardian_started_utc is not None
            and status_ts < guardian_started_utc
        ):
            details["kill_switch_prestart_ignored"] = True
            return False, "", details
        details["kill_reason"] = str(status_row.get("kill_reason") or "")
        return True, "kill_switch_engaged", details

    mode_value = status_row.get("gauge.operating_mode_state")
    if isinstance(mode_value, (int, float)) and float(mode_value) >= mode_trigger_level:
        details["operating_mode_state"] = float(mode_value)
        return True, "operating_mode_degraded", details

    if recent_error_count >= max_errors_in_window:
        return True, "error_burst", details

    guard_requirements = resolve_guard_connectivity_requirements(
        status_row=status_row,
        require_book_feed_connected_config=bool(require_book_feed_connected),
    )
    details["runtime_state"] = str(guard_requirements.get("runtime_state") or "")
    details["active_targets_present"] = bool(guard_requirements.get("active_targets_present", False))
    details["no_target_standdown"] = bool(guard_requirements.get("no_target_standdown", False))
    details["book_feed_required"] = bool(guard_requirements.get("book_feed_required", False))

    if require_chainlink_connected:
        chainlink = status_row.get("chainlink")
        if isinstance(chainlink, dict) and bool(chainlink.get("enabled", False)):
            if not bool(chainlink.get("connected", False)):
                tick_age = chainlink.get("last_tick_age_sec")
                age_val = float(tick_age) if isinstance(tick_age, (int, float)) else None
                details["chainlink"] = {
                    "enabled": True,
                    "connected": False,
                    "last_tick_age_sec": age_val,
                    "min_disconnect_age_sec": float(chainlink_disconnect_min_age_sec),
                }
                if age_val is None:
                    # Avoid startup false positives when feed has not emitted a first tick yet.
                    details["chainlink_age_unknown_startup_suppressed"] = startup_elapsed_sec < startup_grace_sec
                    if startup_elapsed_sec >= startup_grace_sec:
                        details["disconnect_signal_strength"] = "weak_unknown_age"
                        return True, "chainlink_disconnected", details
                elif age_val >= float(chainlink_disconnect_min_age_sec):
                    details["disconnect_signal_strength"] = "strong_age_threshold"
                    return True, "chainlink_disconnected", details

    if bool(guard_requirements.get("book_feed_required", False)):
        book_feed = status_row.get("book_feed")
        if isinstance(book_feed, dict) and bool(book_feed.get("enabled", False)):
            if not bool(book_feed.get("connected", False)):
                msg_age = book_feed.get("last_msg_age_sec")
                age_val = float(msg_age) if isinstance(msg_age, (int, float)) else None
                details["book_feed"] = {
                    "enabled": True,
                    "connected": False,
                    "last_msg_age_sec": age_val,
                    "min_disconnect_age_sec": float(book_feed_disconnect_min_age_sec),
                }
                if age_val is None:
                    details["book_feed_age_unknown_startup_suppressed"] = startup_elapsed_sec < startup_grace_sec
                    if startup_elapsed_sec >= startup_grace_sec:
                        details["disconnect_signal_strength"] = "weak_unknown_age"
                        return True, "book_feed_disconnected", details
                elif age_val >= float(book_feed_disconnect_min_age_sec):
                    details["disconnect_signal_strength"] = "strong_age_threshold"
                    return True, "book_feed_disconnected", details

    return False, "", details


def _apply_disconnect_confirmation(
    *,
    details: Dict[str, Any],
    streak: int,
    poll_interval_sec: float,
    disconnect_confirm_polls: int,
    disconnect_min_age_sec: float,
    disconnect_age_sec: Optional[float],
    last_status_ts_utc: str,
    unknown_age_confirm_rows: int,
) -> Tuple[bool, int, int, str]:
    required_polls = max(1, int(disconnect_confirm_polls))
    signal_strength = str(details.get("disconnect_signal_strength") or "").strip().lower()
    if signal_strength == "weak_unknown_age":
        required_polls = max(2, int(unknown_age_confirm_rows))
        details["disconnect_unknown_age_policy"] = "status_row_confirm"
        status_ts_utc = str(details.get("status_ts_utc") or "").strip()
        if status_ts_utc and status_ts_utc == last_status_ts_utc:
            details["disconnect_status_row_reused"] = True
            return False, streak, required_polls, last_status_ts_utc
        streak += 1
        if status_ts_utc:
            last_status_ts_utc = status_ts_utc
    else:
        streak += 1
        if disconnect_age_sec is None:
            required_polls = max(
                required_polls,
                int(math.ceil(max(0.0, float(disconnect_min_age_sec)) / max(0.1, float(poll_interval_sec)))),
            )
            details["disconnect_unknown_age_policy"] = "min_age_window"
        last_status_ts_utc = ""
    return streak >= required_polls, streak, required_polls, last_status_ts_utc


def write_guard_stop_file(path: pathlib.Path, *, reason: str, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = reason.strip() or "triggered"
    body = rendered + "\n" + json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
    path.write_text(body, encoding="utf-8")


def clear_guard_stop_file(path: pathlib.Path) -> None:
    if path.exists():
        path.unlink()


def _reset_startup_window_if_run_changed(
    *,
    active_run_id: str,
    last_run_id: str,
    started_mono: float,
    started_utc: dt.datetime,
) -> Tuple[str, float, dt.datetime]:
    if active_run_id == last_run_id:
        return last_run_id, started_mono, started_utc
    return active_run_id, time.monotonic(), dt.datetime.now(dt.timezone.utc)


def run_watchdog(args: argparse.Namespace) -> int:
    log_dir = pathlib.Path(args.log_dir).resolve()
    guard_stop_file = pathlib.Path(args.guard_stop_file).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    started_mono = time.monotonic()
    started_utc = dt.datetime.now(dt.timezone.utc)
    last_reason = ""
    active_run_id = ""
    last_run_id = ""
    chainlink_disconnect_streak = 0
    book_feed_disconnect_streak = 0
    chainlink_disconnect_status_ts_utc = ""
    book_feed_disconnect_status_ts_utc = ""
    startup_authority_checked = False
    require_authoritative_startup = bool(getattr(args, "require_authoritative_startup", False))

    while True:
        authority = _resolve_authority_context(args=args, log_dir=log_dir)
        if require_authoritative_startup and (not startup_authority_checked):
            if not bool(authority.authoritative):
                logging.error(
                    "guardian startup authority requirement failed: denial=%s",
                    str(authority.denial_rendered or ""),
                )
                return 2
            startup_authority_checked = True
        active_run_id = authority.run_id
        if not active_run_id:
            active_run_id = _resolve_run_id(
                log_dir,
                explicit_run_id=str(getattr(args, "run_id", "") or ""),
                auto_from_manifest=bool(getattr(args, "run_id_from_manifest", False)),
                max_files=int(getattr(args, "manifest_files_tail", 5)),
            )
        prev_run_id = last_run_id
        last_run_id, started_mono, started_utc = _reset_startup_window_if_run_changed(
            active_run_id=active_run_id,
            last_run_id=last_run_id,
            started_mono=started_mono,
            started_utc=started_utc,
        )
        if active_run_id != prev_run_id:
            if active_run_id:
                logging.info("guardian active run_id=%s", active_run_id)
            else:
                logging.info("guardian active run_id=<none>")
        now_utc = dt.datetime.now(dt.timezone.utc)
        status_row = latest_status_row(
            log_dir,
            max_lines=int(args.status_tail_lines),
            max_files=int(args.status_files_tail),
            run_id=active_run_id,
        )
        recent_errors = recent_error_rows(
            log_dir,
            now_utc=now_utc,
            window_sec=float(args.error_window_sec),
            max_lines_per_file=int(args.error_tail_lines),
            run_id=active_run_id,
        )
        arm, reason, details = evaluate_guard(
            status_row=status_row,
            now_utc=now_utc,
            guardian_started_utc=started_utc,
            startup_elapsed_sec=(time.monotonic() - started_mono),
            startup_grace_sec=float(args.startup_grace_sec),
            max_status_age_sec=float(args.max_status_age_sec),
            recent_error_count=len(recent_errors),
            max_errors_in_window=int(args.max_errors_in_window),
            mode_trigger_level=float(args.mode_trigger_level),
            trigger_on_kill_switch=bool(args.trigger_on_kill_switch),
            require_chainlink_connected=bool(args.require_chainlink_connected),
            require_book_feed_connected=bool(args.require_book_feed_connected),
            chainlink_disconnect_min_age_sec=float(args.chainlink_disconnect_min_age_sec),
            book_feed_disconnect_min_age_sec=float(args.book_feed_disconnect_min_age_sec),
        )
        details["run_id"] = active_run_id or None
        details["authoritative_guard_mode"] = bool(authority.authoritative)
        details["authoritative_context_reason"] = str(authority.reason or "")
        details["authoritative_context_reason_code"] = str(authority.reason_code or "")
        details["authoritative_context_reason_detail"] = str(authority.reason_detail or "")
        details["authoritative_context_decision"] = dict(authority.decision_fields or {})
        details["session_phase"] = str(authority.session_phase or "")
        details["run_contract_path"] = str(authority.run_contract_path or "")
        details["session_id"] = str(authority.session_id or "")
        disconnect_confirm_polls = max(1, int(args.disconnect_confirm_polls))
        disconnect_unknown_age_confirm_rows = max(1, int(getattr(args, "disconnect_unknown_age_confirm_rows", 2)))
        poll_interval_sec = max(0.1, float(args.interval_sec))
        if reason == "chainlink_disconnected":
            chainlink_details = details.get("chainlink") if isinstance(details.get("chainlink"), dict) else {}
            chainlink_last_age = chainlink_details.get("last_tick_age_sec")
            should_arm, chainlink_disconnect_streak, required_polls, chainlink_disconnect_status_ts_utc = (
                _apply_disconnect_confirmation(
                    details=details,
                    streak=chainlink_disconnect_streak,
                    poll_interval_sec=poll_interval_sec,
                    disconnect_confirm_polls=disconnect_confirm_polls,
                    disconnect_min_age_sec=float(args.chainlink_disconnect_min_age_sec),
                    disconnect_age_sec=float(chainlink_last_age)
                    if isinstance(chainlink_last_age, (int, float))
                    else None,
                    last_status_ts_utc=chainlink_disconnect_status_ts_utc,
                    unknown_age_confirm_rows=disconnect_unknown_age_confirm_rows,
                )
            )
            book_feed_disconnect_streak = 0
            book_feed_disconnect_status_ts_utc = ""
            details["disconnect_streak"] = chainlink_disconnect_streak
            details["disconnect_confirm_polls"] = required_polls
            if not should_arm:
                arm = False
                reason = ""
        elif reason == "book_feed_disconnected":
            book_details = details.get("book_feed") if isinstance(details.get("book_feed"), dict) else {}
            book_last_age = book_details.get("last_msg_age_sec")
            should_arm, book_feed_disconnect_streak, required_polls, book_feed_disconnect_status_ts_utc = (
                _apply_disconnect_confirmation(
                    details=details,
                    streak=book_feed_disconnect_streak,
                    poll_interval_sec=poll_interval_sec,
                    disconnect_confirm_polls=disconnect_confirm_polls,
                    disconnect_min_age_sec=float(args.book_feed_disconnect_min_age_sec),
                    disconnect_age_sec=float(book_last_age) if isinstance(book_last_age, (int, float)) else None,
                    last_status_ts_utc=book_feed_disconnect_status_ts_utc,
                    unknown_age_confirm_rows=disconnect_unknown_age_confirm_rows,
                )
            )
            chainlink_disconnect_streak = 0
            chainlink_disconnect_status_ts_utc = ""
            details["disconnect_streak"] = book_feed_disconnect_streak
            details["disconnect_confirm_polls"] = required_polls
            if not should_arm:
                arm = False
                reason = ""
        else:
            chainlink_disconnect_streak = 0
            book_feed_disconnect_streak = 0
            chainlink_disconnect_status_ts_utc = ""
            book_feed_disconnect_status_ts_utc = ""

        control_authorized = bool(authority.authoritative)

        if arm and control_authorized:
            payload = {
                "ts_utc": utc_iso(now_utc),
                "reason": reason,
                "details": details,
                "log_dir": str(log_dir),
                "run_id": active_run_id,
            }
            if reason != last_reason or not guard_stop_file.exists():
                write_guard_stop_file(guard_stop_file, reason=reason, payload=payload)
                logging.error("guard stop armed: reason=%s details=%s", reason, details)
            last_reason = reason
        elif arm and not control_authorized:
            logging.warning(
                "guardian observational mode blocked guard-stop arm: trigger_reason=%s authority_denial=%s details=%s",
                reason,
                str(authority.denial_rendered or ""),
                details,
            )
            last_reason = ""
        else:
            if control_authorized and bool(args.auto_clear) and guard_stop_file.exists():
                clear_guard_stop_file(guard_stop_file)
                logging.warning("guard stop cleared")
            last_reason = ""

        if bool(args.once):
            break
        time.sleep(max(0.1, float(args.interval_sec)))

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bro guard watchdog")
    parser.add_argument("--log-dir", default="./logs_exec", help="Execution log directory")
    parser.add_argument(
        "--guard-stop-file",
        default="./logs_exec/guard_stop.txt",
        help="Path to external guard stop file used by executor runtime.guard_stop_file",
    )
    parser.add_argument("--interval-sec", type=float, default=2.0, help="Polling interval")
    parser.add_argument("--once", action="store_true", help="Evaluate once and exit")
    parser.add_argument("--auto-clear", action="store_true", help="Auto-clear guard file once conditions recover")
    parser.add_argument("--startup-grace-sec", type=float, default=90.0, help="Grace period before status-missing trigger")
    parser.add_argument("--max-status-age-sec", type=float, default=120.0, help="Max tolerated age for latest status row")
    parser.add_argument("--error-window-sec", type=float, default=120.0, help="Rolling error window for burst detection")
    parser.add_argument("--max-errors-in-window", type=int, default=25, help="Error count threshold to arm guard")
    parser.add_argument(
        "--mode-trigger-level",
        type=float,
        default=3.0,
        help="Trigger when gauge.operating_mode_state is at or above this numeric level",
    )
    parser.add_argument(
        "--trigger-on-kill-switch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Arm guard when latest status has kill_switch=true",
    )
    parser.add_argument(
        "--require-chainlink-connected",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Arm guard when chainlink feed is enabled but disconnected",
    )
    parser.add_argument(
        "--require-book-feed-connected",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Arm guard when book feed is enabled but disconnected",
    )
    parser.add_argument(
        "--chainlink-disconnect-min-age-sec",
        type=float,
        default=20.0,
        help="Require chainlink last_tick_age_sec to exceed this before arming disconnect guard",
    )
    parser.add_argument(
        "--book-feed-disconnect-min-age-sec",
        type=float,
        default=20.0,
        help="Require book feed last_msg_age_sec to exceed this before arming disconnect guard",
    )
    parser.add_argument(
        "--disconnect-confirm-polls",
        type=int,
        default=3,
        help="Consecutive watchdog polls required before disconnect guard is armed",
    )
    parser.add_argument(
        "--disconnect-unknown-age-confirm-rows",
        type=int,
        default=2,
        help="Distinct status rows required for unknown-age disconnect signals",
    )
    parser.add_argument("--status-tail-lines", type=int, default=400, help="Tail lines read from status file")
    parser.add_argument("--status-files-tail", type=int, default=3, help="How many recent status files to scan")
    parser.add_argument("--error-tail-lines", type=int, default=800, help="Tail lines read per error file")
    parser.add_argument("--run-id", default="", help="Optional run_id filter")
    parser.add_argument("--run-contract", default="", help="Explicit run contract path (required for authoritative mode)")
    parser.add_argument(
        "--session-phase",
        default="",
        help="Declared canonical session phase (required for authoritative mode)",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Optional session_id guard for run-contract/context binding",
    )
    parser.add_argument(
        "--session-context-file",
        default="",
        help="Canonical session context JSON path carrying run_id/run_contract/session_phase",
    )
    parser.add_argument(
        "--session-token",
        default="",
        help=(
            "Expected canonical session token for authoritative context verification. "
            "If empty, falls back to BRO_CANONICAL_SESSION_TOKEN env var."
        ),
    )
    parser.add_argument(
        "--require-authoritative-startup",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Fail closed during startup unless canonical authoritative context is already valid. "
            "Recommended for canonical runtime launch surfaces."
        ),
    )
    parser.add_argument(
        "--run-id-from-manifest",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Auto-detect run_id from latest run_manifest_*.json when --run-id is not set "
            "(observational convenience only; canonical authoritative mode requires explicit session context)"
        ),
    )
    parser.add_argument("--manifest-files-tail", type=int, default=5, help="How many recent manifests to scan")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    level_name = str(args.log_level).strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | guardian | %(message)s")
    raise SystemExit(run_watchdog(args))


if __name__ == "__main__":
    main()
