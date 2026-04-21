from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any, Dict, Iterable, List, Optional

from prodesk.canonical_authority import RUN_CONTRACT_ALLOWED_ACTIONS
from prodesk.session_phase import normalize_session_phase

RUN_CONTRACT_SCHEMA_VERSION = 2
RUN_CONTRACT_AUTHORITY_LEVELS = {"authoritative", "observational"}
RUN_CONTRACT_ALLOWED_ACTIONS_LOWER = {str(item).strip().lower() for item in RUN_CONTRACT_ALLOWED_ACTIONS}

RUN_CONTRACT_REQUIRED_TEXT_FIELDS = (
    "schema_version",
    "session_id",
    "run_id",
    "phase",
    "session_type",
    "authority_level",
    "manifest_path",
    "log_root",
    "state_root",
    "start_ts",
    "evidence_slice_start_ts",
)


def _parse_ts(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
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


def _ensure_text(payload: Dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"run_contract_missing_field:{key}")
    return value


def run_contract_path(*, log_dir: pathlib.Path, run_id: str) -> pathlib.Path:
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_contract_run_id_missing")
    return log_dir.resolve() / f"run_contract_{rid}.json"


def build_run_contract(
    *,
    session_id: str,
    run_id: str,
    phase: str,
    session_type: str,
    authority_level: str,
    allowed_actions: Iterable[str],
    manifest_path: pathlib.Path,
    log_root: pathlib.Path,
    state_root: pathlib.Path,
    start_ts: str,
    stop_ts: str,
    evidence_slice_start_ts: str,
    evidence_slice_end_ts: str,
    status_path: str,
    events_path: str,
    errors_path: str,
    status_slice_path: str = "",
    events_slice_path: str = "",
    errors_slice_path: str = "",
    git_commit: str = "",
    config_fingerprint_sha256: str = "",
    code_fingerprint_sha256: str = "",
    code_fingerprint_file_count: Any = "",
) -> Dict[str, Any]:
    return {
        "schema_version": int(RUN_CONTRACT_SCHEMA_VERSION),
        "session_id": str(session_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "phase": str(phase or "").strip(),
        "session_type": str(session_type or "").strip(),
        "authority_level": str(authority_level or "").strip().lower(),
        "allowed_actions": [str(item or "").strip() for item in list(allowed_actions)],
        "manifest_path": str(pathlib.Path(manifest_path).resolve()),
        "log_root": str(pathlib.Path(log_root).resolve()),
        "state_root": str(pathlib.Path(state_root).resolve()),
        "start_ts": str(start_ts or "").strip(),
        "stop_ts": str(stop_ts or "").strip(),
        "evidence_slice_start_ts": str(evidence_slice_start_ts or "").strip(),
        "evidence_slice_end_ts": str(evidence_slice_end_ts or "").strip(),
        "status_path": str(status_path or "").strip(),
        "events_path": str(events_path or "").strip(),
        "errors_path": str(errors_path or "").strip(),
        "status_slice_path": str(status_slice_path or "").strip(),
        "events_slice_path": str(events_slice_path or "").strip(),
        "errors_slice_path": str(errors_slice_path or "").strip(),
        "git_commit": str(git_commit or "").strip(),
        "config_fingerprint_sha256": str(config_fingerprint_sha256 or "").strip(),
        "code_fingerprint_sha256": str(code_fingerprint_sha256 or "").strip(),
        "code_fingerprint_file_count": (
            int(code_fingerprint_file_count)
            if isinstance(code_fingerprint_file_count, (int, float, str))
            and str(code_fingerprint_file_count).strip()
            and str(code_fingerprint_file_count).strip().lstrip("-").isdigit()
            else ""
        ),
    }


def validate_run_contract(payload: Dict[str, Any], *, allow_open: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("run_contract_invalid_payload_type")
    schema = int(payload.get("schema_version") or 0)
    if schema < RUN_CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"run_contract_schema_version_invalid:{schema}<min:{RUN_CONTRACT_SCHEMA_VERSION}")
    for key in RUN_CONTRACT_REQUIRED_TEXT_FIELDS:
        _ensure_text(payload, key)
    try:
        payload["phase"] = normalize_session_phase(str(payload.get("phase") or ""))
    except ValueError as exc:
        raise ValueError(f"run_contract_phase_invalid:{payload.get('phase')!r}") from exc
    authority_level = str(payload.get("authority_level") or "").strip().lower()
    if authority_level not in RUN_CONTRACT_AUTHORITY_LEVELS:
        ordered = ",".join(sorted(RUN_CONTRACT_AUTHORITY_LEVELS))
        raise ValueError(f"run_contract_authority_level_invalid:{authority_level or 'missing'}:allowed:{ordered}")
    payload["authority_level"] = authority_level
    allowed_actions_raw = payload.get("allowed_actions")
    if not isinstance(allowed_actions_raw, list) or len(allowed_actions_raw) == 0:
        raise ValueError("run_contract_allowed_actions_missing")
    normalized_actions: List[str] = []
    seen_actions: set[str] = set()
    for raw in allowed_actions_raw:
        action = str(raw or "").strip()
        if not action:
            raise ValueError("run_contract_allowed_actions_invalid_entry")
        if action.lower() not in RUN_CONTRACT_ALLOWED_ACTIONS_LOWER:
            raise ValueError(f"run_contract_allowed_action_unknown:{action}")
        if action in seen_actions:
            continue
        seen_actions.add(action)
        normalized_actions.append(action)
    if len(normalized_actions) == 0:
        raise ValueError("run_contract_allowed_actions_empty")
    payload["allowed_actions"] = normalized_actions
    start_ts = _parse_ts(payload.get("start_ts"))
    stop_raw = str(payload.get("stop_ts") or "").strip()
    stop_ts = _parse_ts(stop_raw)
    slice_start = _parse_ts(payload.get("evidence_slice_start_ts"))
    slice_end_raw = str(payload.get("evidence_slice_end_ts") or "").strip()
    slice_end = _parse_ts(slice_end_raw)
    if start_ts is None:
        raise ValueError("run_contract_start_ts_invalid")
    if slice_start is None:
        raise ValueError("run_contract_evidence_slice_start_ts_invalid")
    if not stop_raw and not allow_open:
        raise ValueError("run_contract_stop_ts_missing")
    if stop_raw and stop_ts is None:
        raise ValueError("run_contract_stop_ts_invalid")
    if not slice_end_raw and not allow_open:
        raise ValueError("run_contract_evidence_slice_end_ts_missing")
    if slice_end_raw and slice_end is None:
        raise ValueError("run_contract_evidence_slice_end_ts_invalid")
    if stop_ts is not None and start_ts > stop_ts:
        raise ValueError("run_contract_stop_before_start")
    if slice_end is not None and slice_start > slice_end:
        raise ValueError("run_contract_slice_end_before_start")
    if slice_start < start_ts:
        raise ValueError("run_contract_slice_start_before_run_start")
    if stop_ts is not None and slice_end is not None and slice_end > stop_ts:
        raise ValueError("run_contract_slice_end_after_run_stop")
    return payload


def load_run_contract(path: pathlib.Path, *, allow_open: bool = False) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run_contract_invalid_json_root")
    return validate_run_contract(payload, allow_open=allow_open)


def write_run_contract(path: pathlib.Path, payload: Dict[str, Any], *, allow_open: bool = False) -> pathlib.Path:
    validated = validate_run_contract(payload, allow_open=allow_open)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def resolve_run_contract(
    *,
    log_dir: pathlib.Path,
    run_id: Optional[str],
    run_contract_path_override: Optional[pathlib.Path] = None,
    allow_open: bool = True,
) -> Optional[Dict[str, Any]]:
    if run_contract_path_override is not None:
        path = pathlib.Path(run_contract_path_override).resolve()
        if not path.exists():
            raise ValueError(f"run_contract_missing:{path}")
        payload = load_run_contract(path, allow_open=allow_open)
        if run_id and str(payload.get("run_id") or "").strip() != str(run_id).strip():
            raise ValueError(
                f"run_contract_run_id_mismatch:{payload.get('run_id')}!=requested:{str(run_id).strip()}"
            )
        payload["_path"] = str(path)
        return payload

    rid = str(run_id or "").strip()
    if not rid:
        return None
    candidate = run_contract_path(log_dir=log_dir, run_id=rid)
    if not candidate.exists():
        return None
    payload = load_run_contract(candidate, allow_open=allow_open)
    payload["_path"] = str(candidate)
    return payload


def row_within_contract_bounds(row: Dict[str, Any], contract: Dict[str, Any]) -> bool:
    rid = str(contract.get("run_id") or "").strip()
    if rid and str(row.get("run_id") or "").strip() != rid:
        return False
    row_ts = _parse_ts(row.get("ts_utc"))
    if row_ts is None:
        return False
    start_ts = _parse_ts(contract.get("evidence_slice_start_ts"))
    end_ts = _parse_ts(contract.get("evidence_slice_end_ts"))
    if end_ts is None:
        end_ts = _parse_ts(contract.get("stop_ts"))
    if end_ts is None:
        end_ts = dt.datetime.now(dt.timezone.utc)
    if start_ts is None:
        return False
    return bool(start_ts <= row_ts <= end_ts)


def apply_contract_bounds(rows: Iterable[Dict[str, Any]], contract: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if contract is None:
        return [row for row in rows if isinstance(row, dict)]
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row_within_contract_bounds(row, contract):
            out.append(row)
    return out


def run_contract_slice_path(contract: Dict[str, Any], *, stream: str) -> Optional[pathlib.Path]:
    key = f"{stream}_slice_path"
    value = str(contract.get(key) or "").strip()
    if not value:
        return None
    path = pathlib.Path(value).expanduser().resolve()
    if not path.exists():
        return None
    return path


def contract_allows_action(contract: Dict[str, Any], *, action: str) -> bool:
    desired = str(action or "").strip().lower()
    if not desired:
        return False
    raw = contract.get("allowed_actions")
    if not isinstance(raw, list):
        return False
    for item in raw:
        if str(item or "").strip().lower() == desired:
            return True
    return False
