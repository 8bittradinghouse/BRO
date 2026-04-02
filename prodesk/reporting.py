from __future__ import annotations

from typing import Any, Dict, List


def decision_item(
    *,
    check: str,
    level: str,
    metric: str,
    comparator: str,
    value: float,
    threshold: float,
    passed: bool,
    note: str = "",
) -> Dict[str, Any]:
    return {
        "check": check,
        "level": level,
        "metric": metric,
        "comparator": comparator,
        "value": float(value),
        "threshold": float(threshold),
        "passed": bool(passed),
        "note": str(note or ""),
    }


def apply_trace(
    *,
    trace: List[Dict[str, Any]],
    findings: List[str],
    check: str,
    metric: str,
    comparator: str,
    value: float,
    threshold: float,
    finding_msg: str,
    level: str = "hard_fail",
    note: str = "",
) -> bool:
    if comparator == "max":
        passed = float(value) <= float(threshold)
    elif comparator == "min":
        passed = float(value) >= float(threshold)
    else:
        raise ValueError(f"unsupported comparator: {comparator}")
    trace.append(
        decision_item(
            check=check,
            level=level,
            metric=metric,
            comparator=comparator,
            value=float(value),
            threshold=float(threshold),
            passed=passed,
            note=note,
        )
    )
    if (not passed) and level == "hard_fail":
        findings.append(finding_msg)
    return passed

