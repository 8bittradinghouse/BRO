from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Optional, Tuple

from prodesk.session_phase import normalize_session_phase

CAPABILITY_GUARDIAN_CONTROL = "guardian_control"
CAPABILITY_DEPLOY_START = "deploy_start"
CAPABILITY_EXECUTOR_RUN = "executor_run"
CAPABILITY_VALIDATE_ACTIVE = "validate_active"
CAPABILITY_STOP_SESSION = "stop_session"
CAPABILITY_VALIDATE_POSTRUN = "validate_postrun"
CAPABILITY_ARCHIVE_EXPORT = "archive_export"

ACTOR_GUARDIAN_WATCHDOG = "guardian_watchdog"
ACTOR_DEPLOY_PAPER_CLEAN = "deploy_paper_clean"
ACTOR_EXECUTOR = "executor"
ACTOR_CANONICAL_SESSION = "canonical_session"

CANONICAL_ALLOWED_ACTIONS: Tuple[str, ...] = (
    CAPABILITY_GUARDIAN_CONTROL,
    CAPABILITY_DEPLOY_START,
    CAPABILITY_EXECUTOR_RUN,
    CAPABILITY_VALIDATE_ACTIVE,
    CAPABILITY_STOP_SESSION,
    CAPABILITY_VALIDATE_POSTRUN,
    CAPABILITY_ARCHIVE_EXPORT,
)

CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS: Tuple[str, ...] = (
    CAPABILITY_GUARDIAN_CONTROL,
    CAPABILITY_DEPLOY_START,
    CAPABILITY_EXECUTOR_RUN,
    CAPABILITY_VALIDATE_ACTIVE,
    CAPABILITY_STOP_SESSION,
    CAPABILITY_VALIDATE_POSTRUN,
    CAPABILITY_ARCHIVE_EXPORT,
)

CANONICAL_OBSERVATIONAL_ALLOWED_ACTIONS: Tuple[str, ...] = (
    CAPABILITY_VALIDATE_POSTRUN,
    CAPABILITY_ARCHIVE_EXPORT,
)

ACTION_ALLOWED_PHASES: Dict[str, Tuple[str, ...]] = {
    CAPABILITY_GUARDIAN_CONTROL: ("start", "active", "validate_active"),
    CAPABILITY_DEPLOY_START: ("start",),
    CAPABILITY_EXECUTOR_RUN: ("start", "active", "validate_active"),
    CAPABILITY_VALIDATE_ACTIVE: ("validate_active",),
    CAPABILITY_STOP_SESSION: ("stop",),
    CAPABILITY_VALIDATE_POSTRUN: ("validate_postrun",),
    CAPABILITY_ARCHIVE_EXPORT: ("archive_export",),
}

RUN_CONTRACT_ALLOWED_ACTIONS: Tuple[str, ...] = CANONICAL_ALLOWED_ACTIONS

ACTOR_ALLOWED_ACTIONS: Dict[str, Tuple[str, ...]] = {
    ACTOR_GUARDIAN_WATCHDOG: (CAPABILITY_GUARDIAN_CONTROL,),
    ACTOR_DEPLOY_PAPER_CLEAN: (CAPABILITY_DEPLOY_START,),
    ACTOR_EXECUTOR: (CAPABILITY_EXECUTOR_RUN,),
    ACTOR_CANONICAL_SESSION: (
        CAPABILITY_VALIDATE_ACTIVE,
        CAPABILITY_STOP_SESSION,
        CAPABILITY_VALIDATE_POSTRUN,
        CAPABILITY_ARCHIVE_EXPORT,
    ),
}

_ACTION_VOCABULARY = {item.lower() for item in RUN_CONTRACT_ALLOWED_ACTIONS}
_ACTOR_ACTION_POLICY = {k.lower(): {item.lower() for item in v} for k, v in ACTOR_ALLOWED_ACTIONS.items()}


@dataclass(frozen=True)
class AuthorityRequest:
    actor: str
    action: str
    log_dir: pathlib.Path
    session_context_file: Optional[pathlib.Path] = None
    session_token: str = ""
    run_id: str = ""
    run_contract_path: Optional[pathlib.Path] = None
    session_phase: str = ""
    session_id: str = ""
    require_authoritative: bool = True
    allow_open_contract: bool = True
    allowed_authority_levels: Tuple[str, ...] = ("authoritative",)
    require_paths_within_log_dir: bool = True


@dataclass(frozen=True)
class AuthorityDecision:
    authorized: bool
    authoritative: bool
    reason_code: str
    reason_detail: str
    actor: str
    action: str
    run_id: str
    session_id: str
    session_phase: str
    run_contract_path: str
    context_path: str
    authority_level: str

    def as_log_fields(self) -> Dict[str, Any]:
        return {
            "authorized": bool(self.authorized),
            "authoritative": bool(self.authoritative),
            "reason_code": str(self.reason_code or ""),
            "reason_detail": str(self.reason_detail or ""),
            "actor": str(self.actor or ""),
            "action": str(self.action or ""),
            "run_id": str(self.run_id or ""),
            "session_id": str(self.session_id or ""),
            "phase": str(self.session_phase or ""),
            "session_phase": str(self.session_phase or ""),
            "run_contract_path": str(self.run_contract_path or ""),
            "context_path": str(self.context_path or ""),
            "authority_level": str(self.authority_level or ""),
        }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _resolve_mapped_path(*, raw: str, log_dir: pathlib.Path) -> pathlib.Path:
    text = _clean(raw)
    candidate = pathlib.Path(text).expanduser()
    if candidate.is_absolute():
        if text.startswith("/logs/"):
            rel = PurePosixPath(text).relative_to("/logs")
            mapped = (log_dir.parent / pathlib.Path(*rel.parts)).resolve()
            return mapped
        return candidate.resolve()
    return (log_dir / candidate).resolve()


def _load_context_payload(path: pathlib.Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"context_file_invalid_json:{exc.__class__.__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError("context_file_invalid_root")
    return payload


def _deny(
    *,
    reason_code: str,
    reason_detail: str = "",
    actor: str,
    action: str,
    run_id: str,
    session_id: str,
    session_phase: str,
    run_contract_path: str,
    context_path: str,
    authority_level: str = "",
) -> AuthorityDecision:
    return AuthorityDecision(
        authorized=False,
        authoritative=False,
        reason_code=_clean(reason_code),
        reason_detail=_clean(reason_detail),
        actor=_clean(actor),
        action=_clean(action),
        run_id=_clean(run_id),
        session_id=_clean(session_id),
        session_phase=_clean(session_phase),
        run_contract_path=_clean(run_contract_path),
        context_path=_clean(context_path),
        authority_level=_clean(authority_level),
    )


def _allow(
    *,
    actor: str,
    action: str,
    run_id: str,
    session_id: str,
    session_phase: str,
    run_contract_path: str,
    context_path: str,
    authority_level: str,
) -> AuthorityDecision:
    return AuthorityDecision(
        authorized=True,
        authoritative=True,
        reason_code="authorized",
        reason_detail="authoritative_context_verified",
        actor=_clean(actor),
        action=_clean(action),
        run_id=_clean(run_id),
        session_id=_clean(session_id),
        session_phase=_clean(session_phase),
        run_contract_path=_clean(run_contract_path),
        context_path=_clean(context_path),
        authority_level=_clean(authority_level),
    )


def resolve_authority_decision(request: AuthorityRequest) -> AuthorityDecision:
    actor = _clean(request.actor) or "unknown_actor"
    action = _clean(request.action).lower()
    log_dir = pathlib.Path(request.log_dir).resolve()
    run_id = _clean(request.run_id)
    session_id = _clean(request.session_id)
    session_phase = _clean(request.session_phase).lower()
    session_token = _clean(request.session_token)
    run_contract_raw = _clean(request.run_contract_path)
    context_path_raw = _clean(request.session_context_file)
    context_payload: Dict[str, Any] = {}
    context_path_text = ""

    if action not in _ACTION_VOCABULARY:
        return _deny(
            reason_code="action_unknown",
            reason_detail=action or "missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    actor_allowed_actions = _ACTOR_ACTION_POLICY.get(actor.lower())
    if actor_allowed_actions is None:
        return _deny(
            reason_code="actor_unknown",
            reason_detail=actor,
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    if action not in actor_allowed_actions:
        ordered = ",".join(sorted(actor_allowed_actions))
        return _deny(
            reason_code="actor_action_forbidden",
            reason_detail=f"{actor}:{action}:allowed:{ordered}",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )

    if context_path_raw:
        context_path = _resolve_mapped_path(raw=context_path_raw, log_dir=log_dir)
        context_path_text = str(context_path)
        if not context_path.exists():
            return _deny(
                reason_code="context_file_missing",
                reason_detail=str(context_path),
                actor=actor,
                action=action,
                run_id=run_id,
                session_id=session_id,
                session_phase=session_phase,
                run_contract_path=run_contract_raw,
                context_path=context_path_text,
            )
        if request.require_paths_within_log_dir and not _path_within(context_path, log_dir):
            return _deny(
                reason_code="context_file_outside_log_dir",
                reason_detail=str(context_path),
                actor=actor,
                action=action,
                run_id=run_id,
                session_id=session_id,
                session_phase=session_phase,
                run_contract_path=run_contract_raw,
                context_path=context_path_text,
            )
        try:
            context_payload = _load_context_payload(context_path)
        except ValueError as exc:
            return _deny(
                reason_code="context_file_invalid",
                reason_detail=str(exc),
                actor=actor,
                action=action,
                run_id=run_id,
                session_id=session_id,
                session_phase=session_phase,
                run_contract_path=run_contract_raw,
                context_path=context_path_text,
            )
    elif request.require_authoritative:
        return _deny(
            reason_code="context_file_missing",
            reason_detail="missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )

    payload_token = _clean(context_payload.get("session_token"))
    if request.require_authoritative and not session_token:
        return _deny(
            reason_code="session_token_expected_missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    if request.require_authoritative and not payload_token:
        return _deny(
            reason_code="session_token_missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    if session_token and payload_token and session_token != payload_token:
        return _deny(
            reason_code="session_token_mismatch",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )

    run_id = run_id or _clean(context_payload.get("run_id"))
    run_contract_raw = run_contract_raw or _clean(context_payload.get("run_contract_path"))
    session_phase = session_phase or _clean(context_payload.get("session_phase")).lower()
    session_id = session_id or _clean(context_payload.get("session_id"))

    if request.require_authoritative and not run_id:
        return _deny(
            reason_code="run_id_missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    if request.require_authoritative and not run_contract_raw:
        return _deny(
            reason_code="run_contract_missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )
    if request.require_authoritative and not session_phase:
        return _deny(
            reason_code="session_phase_missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )

    try:
        session_phase = normalize_session_phase(session_phase)
    except Exception:
        return _deny(
            reason_code="session_phase_invalid",
            reason_detail=session_phase or "missing",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_raw,
            context_path=context_path_text,
        )

    run_contract_path = _resolve_mapped_path(raw=run_contract_raw, log_dir=log_dir)
    run_contract_path_text = str(run_contract_path)
    if not run_contract_path.exists():
        return _deny(
            reason_code="run_contract_path_missing",
            reason_detail=run_contract_path_text,
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )
    if request.require_paths_within_log_dir and not _path_within(run_contract_path, log_dir):
        return _deny(
            reason_code="run_contract_outside_log_dir",
            reason_detail=run_contract_path_text,
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )

    try:
        from prodesk.run_contract import contract_allows_action, load_run_contract
    except Exception as exc:
        return _deny(
            reason_code="run_contract_module_import_failed",
            reason_detail=exc.__class__.__name__,
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )

    try:
        contract = load_run_contract(run_contract_path, allow_open=bool(request.allow_open_contract))
    except Exception as exc:
        return _deny(
            reason_code="run_contract_invalid",
            reason_detail=str(exc),
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )

    contract_run_id = _clean(contract.get("run_id"))
    if contract_run_id != run_id:
        return _deny(
            reason_code="run_contract_run_id_mismatch",
            reason_detail=f"{contract_run_id or 'missing'}!={run_id}",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )

    contract_session_id = _clean(contract.get("session_id"))
    if session_id and contract_session_id and session_id != contract_session_id:
        return _deny(
            reason_code="run_contract_session_id_mismatch",
            reason_detail=f"{contract_session_id}!={session_id}",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
        )

    authority_level = _clean(contract.get("authority_level")).lower()
    allowed_levels = {_clean(item).lower() for item in request.allowed_authority_levels if _clean(item)}
    if request.require_authoritative and authority_level not in allowed_levels:
        ordered = ",".join(sorted(allowed_levels)) if allowed_levels else "missing"
        return _deny(
            reason_code="run_contract_authority_invalid",
            reason_detail=f"{authority_level or 'missing'}:allowed:{ordered}",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
            authority_level=authority_level,
        )

    if not contract_allows_action(contract, action=action):
        return _deny(
            reason_code="run_contract_action_forbidden",
            reason_detail=action,
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
            authority_level=authority_level,
        )

    phase_allowlist: Iterable[str] = ACTION_ALLOWED_PHASES.get(action, ())
    allowed_phases = {normalize_session_phase(_clean(item).lower()) for item in phase_allowlist if _clean(item)}
    if allowed_phases and session_phase not in allowed_phases:
        ordered = ",".join(sorted(allowed_phases))
        return _deny(
            reason_code="session_phase_action_forbidden",
            reason_detail=f"{session_phase}:allowed:{ordered}",
            actor=actor,
            action=action,
            run_id=run_id,
            session_id=session_id,
            session_phase=session_phase,
            run_contract_path=run_contract_path_text,
            context_path=context_path_text,
            authority_level=authority_level,
        )

    return _allow(
        actor=actor,
        action=action,
        run_id=run_id,
        session_id=session_id,
        session_phase=session_phase,
        run_contract_path=run_contract_path_text,
        context_path=context_path_text,
        authority_level=authority_level,
    )


def render_authority_denial(decision: AuthorityDecision, *, prefix: str = "canonical_authority_denied") -> str:
    fields = dict(decision.as_log_fields())
    return f"{_clean(prefix)}:{json.dumps(fields, sort_keys=True, separators=(',', ':'))}"
