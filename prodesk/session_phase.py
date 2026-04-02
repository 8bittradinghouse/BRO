from __future__ import annotations

from typing import Dict, List, Sequence, Set

SESSION_PHASE_SEQUENCE: Sequence[str] = (
    "preflight",
    "start",
    "active",
    "validate_active",
    "stop",
    "validate_postrun",
    "archive_export",
    "complete",
)

SESSION_PHASE_INDEX: Dict[str, int] = {name: idx for idx, name in enumerate(SESSION_PHASE_SEQUENCE)}

VALIDATION_ALLOWED_PHASES: Dict[str, Set[str]] = {
    "run_integrity_audit": {"validate_active", "validate_postrun"},
    "websocket_reliability_gate": {"validate_active", "validate_postrun"},
    "websocket_hardening_audit": {"validate_active", "validate_postrun"},
    "nightly_soak_report": {"validate_active", "validate_postrun"},
    "paper_harness_audit": {"validate_postrun"},
    "outcome_truth_audit": {"validate_postrun"},
    "readiness_gate": {"validate_postrun"},
    "soak_hardening_gate": {"validate_postrun"},
}

PHASE_VALIDATION_SURFACE: Dict[str, Dict[str, List[str]]] = {
    "preflight": {
        "legal_validations": [],
        "actionable_failures": [
            "config_lock_failure",
            "runtime_path_mismatch",
            "missing_runtime_prerequisites",
        ],
        "informational_failures": [],
    },
    "start": {
        "legal_validations": [],
        "actionable_failures": [
            "stack_start_failure",
            "manifest_missing",
            "run_contract_missing",
        ],
        "informational_failures": [],
    },
    "active": {
        "legal_validations": [],
        "actionable_failures": [
            "process_unhealthy",
            "run_contract_unbound",
        ],
        "informational_failures": [],
    },
    "validate_active": {
        "legal_validations": [
            "run_integrity_audit",
            "websocket_reliability_gate",
            "nightly_soak_report",
        ],
        "actionable_failures": [
            "run_integrity_audit",
            "websocket_reliability_gate",
        ],
        "informational_failures": [
            "nightly_soak_report",
        ],
    },
    "stop": {
        "legal_validations": [],
        "actionable_failures": [
            "stack_stop_failure",
            "missing_stop_timestamp",
            "run_slice_generation_failure",
        ],
        "informational_failures": [],
    },
    "validate_postrun": {
        "legal_validations": [
            "paper_harness_audit",
            "websocket_hardening_audit",
            "readiness_gate",
            "nightly_soak_report",
            "outcome_truth_audit",
            "soak_hardening_gate",
        ],
        "actionable_failures": [
            "paper_harness_audit",
            "websocket_hardening_audit",
            "readiness_gate",
            "outcome_truth_audit",
            "soak_hardening_gate",
        ],
        "informational_failures": [
            "nightly_soak_report",
        ],
    },
    "archive_export": {
        "legal_validations": [],
        "actionable_failures": [
            "archive_export_failure",
        ],
        "informational_failures": [
            "archive_export_skipped",
        ],
    },
    "complete": {
        "legal_validations": [],
        "actionable_failures": [],
        "informational_failures": [],
    },
}


def normalize_session_phase(value: str) -> str:
    text = str(value or "").strip().lower()
    if text not in SESSION_PHASE_INDEX:
        raise ValueError(f"session_phase_invalid:{text or 'missing'}")
    return text


def assert_valid_phase_transition(current_phase: str, next_phase: str) -> None:
    current = normalize_session_phase(current_phase)
    next_value = normalize_session_phase(next_phase)
    expected_idx = int(SESSION_PHASE_INDEX[current]) + 1
    if expected_idx >= len(SESSION_PHASE_SEQUENCE):
        raise ValueError(f"session_phase_terminal:{current}->{next_value}")
    expected = str(SESSION_PHASE_SEQUENCE[expected_idx])
    if next_value != expected:
        raise ValueError(f"session_phase_invalid_transition:{current}->{next_value}:expected:{expected}")


def validation_surface_for_phase(phase: str) -> Dict[str, List[str]]:
    normalized = normalize_session_phase(phase)
    payload = PHASE_VALIDATION_SURFACE.get(normalized)
    if payload is None:
        return {"legal_validations": [], "actionable_failures": [], "informational_failures": []}
    return {
        "legal_validations": list(payload.get("legal_validations", [])),
        "actionable_failures": list(payload.get("actionable_failures", [])),
        "informational_failures": list(payload.get("informational_failures", [])),
    }


def enforce_validation_phase(*, validation_name: str, session_phase: str) -> str:
    normalized_phase = normalize_session_phase(session_phase)
    name = str(validation_name or "").strip()
    allowed = VALIDATION_ALLOWED_PHASES.get(name, set())
    if allowed and normalized_phase not in allowed:
        ordered = ",".join(sorted(allowed))
        raise ValueError(f"validation_phase_invalid:{name}:{normalized_phase}:allowed:{ordered}")
    return normalized_phase
