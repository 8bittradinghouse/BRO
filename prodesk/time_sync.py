from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Dict, Optional

from .common import utc_iso


_KEY_VALUE_RE = re.compile(r"^\s*([^:]+):\s*(.*?)\s*$")
_DURATION_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(us|ms|s)")


def _parse_key_value_lines(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        match = _KEY_VALUE_RE.match(raw_line)
        if not match:
            continue
        key = str(match.group(1) or "").strip().lower().replace(" ", "_")
        value = str(match.group(2) or "").strip()
        if key:
            parsed[key] = value
    return parsed


def _parse_bool(text: Any) -> Optional[bool]:
    value = str(text or "").strip().lower()
    if not value:
        return None
    if value in {"yes", "true", "active", "enabled"}:
        return True
    if value in {"no", "false", "inactive", "disabled"}:
        return False
    return None


def _parse_duration_ms(text: Any) -> Optional[float]:
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _DURATION_RE.search(raw)
    if match is None:
        return None
    try:
        magnitude = float(match.group(1))
    except (TypeError, ValueError):
        return None
    unit = str(match.group(2) or "").strip().lower()
    if unit == "s":
        return magnitude * 1000.0
    if unit == "ms":
        return magnitude
    if unit == "us":
        return magnitude / 1000.0
    return None


def parse_timedatectl_status(text: str) -> Dict[str, Any]:
    fields = _parse_key_value_lines(text)
    return {
        "system_clock_synchronized": _parse_bool(fields.get("system_clock_synchronized")),
        "ntp_service_active": _parse_bool(fields.get("ntp_service")),
        "timezone": str(fields.get("time_zone") or "").strip() or None,
    }


def parse_timedatectl_timesync_status(text: str) -> Dict[str, Any]:
    fields = _parse_key_value_lines(text)
    stratum_text = str(fields.get("stratum") or "").strip()
    stratum_value: Optional[int] = None
    if stratum_text:
        try:
            stratum_value = int(float(stratum_text))
        except (TypeError, ValueError):
            stratum_value = None
    return {
        "server": str(fields.get("server") or "").strip() or None,
        "poll_interval": str(fields.get("poll_interval") or "").strip() or None,
        "leap": str(fields.get("leap") or "").strip() or None,
        "stratum": stratum_value,
        "offset_ms": _parse_duration_ms(fields.get("offset")),
        "jitter_ms": _parse_duration_ms(fields.get("jitter")),
        "root_distance_ms": _parse_duration_ms(fields.get("root_distance")),
        "network_delay_ms": _parse_duration_ms(fields.get("delay")),
    }


def capture_host_time_sync_snapshot(
    *,
    cmd_runner: Callable[..., Any] = subprocess.run,
    timeout_sec: float = 2.0,
) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "available": False,
        "clock_state": "unknown",
        "sample_ts_utc": utc_iso(),
        "source": "timedatectl",
        "status_command_ok": False,
        "timesync_command_ok": False,
    }

    status_text = ""
    timesync_text = ""
    errors = []
    command_unavailable = False

    try:
        status_proc = cmd_runner(
            ["timedatectl", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
        )
        status_text = str(getattr(status_proc, "stdout", "") or "")
        snapshot["status_command_ok"] = int(getattr(status_proc, "returncode", 1)) == 0
    except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        if isinstance(exc, FileNotFoundError):
            command_unavailable = True
        errors.append(f"status:{exc.__class__.__name__}:{exc}")

    try:
        timesync_proc = cmd_runner(
            ["timedatectl", "timesync-status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
        )
        timesync_text = str(getattr(timesync_proc, "stdout", "") or "")
        snapshot["timesync_command_ok"] = int(getattr(timesync_proc, "returncode", 1)) == 0
    except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        if isinstance(exc, FileNotFoundError):
            command_unavailable = True
        errors.append(f"timesync:{exc.__class__.__name__}:{exc}")

    status_payload = parse_timedatectl_status(status_text)
    timesync_payload = parse_timedatectl_timesync_status(timesync_text)
    snapshot.update(status_payload)
    snapshot.update(timesync_payload)

    available = bool(snapshot["status_command_ok"] or snapshot["timesync_command_ok"])
    snapshot["available"] = available
    if errors:
        snapshot["errors"] = errors

    sync_ok = snapshot.get("system_clock_synchronized")
    ntp_ok = snapshot.get("ntp_service_active")
    if available and sync_ok is True and ntp_ok is True:
        snapshot["clock_state"] = "synced"
    elif available and (sync_ok is False or ntp_ok is False):
        snapshot["clock_state"] = "unsynced"
    elif available:
        snapshot["clock_state"] = "partial_visibility"
    elif command_unavailable:
        # Runtime/container context may lack host clock visibility even when
        # canonical host-side session artifacts can still capture it.
        snapshot["clock_state"] = "partial_visibility"

    return snapshot
