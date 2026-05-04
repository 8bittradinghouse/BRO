#!/usr/bin/env python3
"""Promotion gate requiring objective evidence across soak + reconciliation."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
from typing import Any, Dict, List, Optional

from prodesk.error_codes import summarize_error_codes
from prodesk.reporting import decision_item
import yaml


SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _identity_value(identity: Dict[str, Any], key: str) -> str:
    if not isinstance(identity, dict):
        return ""
    return str(identity.get(key) or "").strip()


def _is_sha256_hex(value: str) -> bool:
    return bool(SHA256_HEX_RE.match(str(value or "").strip().lower()))


def _lineage_presence_and_validity_checks(
    *,
    label: str,
    identity: Dict[str, Any],
    findings: List[str],
    decision_trace: List[Dict[str, Any]],
) -> None:
    required_text_fields = ("run_id", "git_commit", "profile_name")
    required_hash_fields = ("config_fingerprint_sha256", "code_fingerprint_sha256")
    manifest_present = bool(identity.get("manifest_present", False))
    manifest_load_error = str(identity.get("manifest_load_error") or "").strip()

    if not manifest_present:
        findings.append(f"artifact_identity_missing_manifest:{label}")
    decision_trace.append(
        decision_item(
            check=f"artifact_identity_manifest_present:{label}",
            level="hard_fail",
            metric="manifest_present",
            comparator="eq",
            value=1.0 if manifest_present else 0.0,
            threshold=1.0,
            passed=manifest_present,
            note=label,
        )
    )

    if manifest_load_error:
        findings.append(f"artifact_identity_manifest_load_error:{label}:{manifest_load_error}")
    decision_trace.append(
        decision_item(
            check=f"artifact_identity_manifest_load_error:{label}",
            level="hard_fail",
            metric="manifest_load_error",
            comparator="eq",
            value=0.0 if manifest_load_error else 1.0,
            threshold=1.0,
            passed=(not manifest_load_error),
            note=manifest_load_error or label,
        )
    )

    for key in required_text_fields:
        value = _identity_value(identity, key)
        if not value:
            findings.append(f"artifact_identity_missing_field:{label}:{key}")
        decision_trace.append(
            decision_item(
                check=f"artifact_identity_present:{label}:{key}",
                level="hard_fail",
                metric=f"present_{key}",
                comparator="min",
                value=1.0 if value else 0.0,
                threshold=1.0,
                passed=bool(value),
                note=value or label,
            )
        )

    for key in required_hash_fields:
        value = _identity_value(identity, key)
        if not value:
            findings.append(f"artifact_identity_missing_field:{label}:{key}")
        elif not _is_sha256_hex(value):
            findings.append(f"artifact_identity_invalid_sha256:{label}:{key}")
        decision_trace.append(
            decision_item(
                check=f"artifact_identity_valid_sha256:{label}:{key}",
                level="hard_fail",
                metric=f"sha256_{key}",
                comparator="eq",
                value=1.0 if _is_sha256_hex(value) else 0.0,
                threshold=1.0,
                passed=_is_sha256_hex(value),
                note=value or label,
            )
        )


def _require_soak_lineage_consistency(
    *,
    soak: Dict[str, Any],
    soak_identity: Dict[str, Any],
    findings: List[str],
    decision_trace: List[Dict[str, Any]],
) -> None:
    lineage = soak.get("run_commit_lineage") if isinstance(soak.get("run_commit_lineage"), dict) else {}
    lineage_complete = bool(lineage.get("complete", False))
    if not lineage_complete:
        findings.append("soak_run_commit_lineage_incomplete")
    decision_trace.append(
        decision_item(
            check="soak_run_commit_lineage_complete",
            level="hard_fail",
            metric="run_commit_lineage_complete",
            comparator="eq",
            value=1.0 if lineage_complete else 0.0,
            threshold=1.0,
            passed=lineage_complete,
            note="nightly soak lineage completeness",
        )
    )
    for key in ("run_id", "git_commit", "config_fingerprint_sha256", "code_fingerprint_sha256"):
        left = _identity_value(soak_identity, key)
        right = _identity_value(lineage, key)
        mismatch = bool(left and right and left != right)
        if mismatch:
            findings.append(f"soak_run_commit_lineage_mismatch:{key}")
        decision_trace.append(
            decision_item(
                check=f"soak_run_commit_lineage_{key}",
                level="hard_fail",
                metric=f"lineage_{key}",
                comparator="eq",
                value=1.0 if not mismatch else 0.0,
                threshold=1.0,
                passed=(not mismatch),
                note=f"artifact_identity={left or 'missing'} run_commit_lineage={right or 'missing'}",
            )
        )


def run_gate(
    *,
    soak_report_path: pathlib.Path,
    reconcile_report_path: pathlib.Path,
    websocket_report_path: pathlib.Path | None,
    min_uptime_ratio: float,
    max_error_rows: int,
    min_execution_quality_net: float,
    max_reconcile_mismatch_ratio: float,
    max_websocket_book_feed_down_ratio: float,
    max_websocket_chainlink_down_ratio: float,
    max_websocket_book_feed_reconnects_per_hour: float,
    max_websocket_chainlink_reconnects_per_hour: float,
    max_websocket_chainlink_dropped_ticks: float,
    websocket_report_required: bool = False,
    allowed_nonvenue_verification_levels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    findings: List[str] = []
    warnings: List[str] = []
    advisories: List[str] = []
    decision_trace: List[Dict[str, Any]] = []
    soak = json.loads(soak_report_path.read_text(encoding="utf-8"))
    reconcile = json.loads(reconcile_report_path.read_text(encoding="utf-8"))
    soak_identity = soak.get("artifact_identity") if isinstance(soak.get("artifact_identity"), dict) else {}
    reconcile_identity = (
        reconcile.get("artifact_identity") if isinstance(reconcile.get("artifact_identity"), dict) else {}
    )
    websocket_identity: Dict[str, Any] = {}

    quote_uptime = _safe_float(soak.get("quote_uptime_ratio"))
    error_rows = int(_safe_float(soak.get("error_rows")))
    exec_quality = _safe_float((soak.get("execution_quality") or {}).get("capture_minus_adverse"))
    mismatch_ratio = _safe_float(reconcile.get("mismatch_ratio"))
    verification_level = str(reconcile.get("verification_level") or "").strip().lower()
    if not verification_level:
        verification_level = "unknown"
    allowed_nonvenue_levels = {
        str(value).strip().lower()
        for value in (allowed_nonvenue_verification_levels or [])
        if str(value).strip()
    }
    verification_level_ok = bool(
        verification_level == "venue_verified" or verification_level in allowed_nonvenue_levels
    )
    websocket_metrics: Dict[str, float] = {}

    websocket = None
    if websocket_report_path is not None:
        websocket = json.loads(websocket_report_path.read_text(encoding="utf-8"))
        metrics = dict(websocket.get("metrics") or {})
        websocket_identity = websocket.get("artifact_identity") if isinstance(websocket.get("artifact_identity"), dict) else {}
        websocket_metrics = {
            "book_feed_down_ratio": _safe_float(metrics.get("book_feed_down_ratio")),
            "chainlink_down_ratio": _safe_float(metrics.get("chainlink_down_ratio")),
            "book_feed_reconnects_per_hour": _safe_float(metrics.get("book_feed_reconnects_per_hour")),
            "chainlink_reconnects_per_hour": _safe_float(metrics.get("chainlink_reconnects_per_hour")),
            "chainlink_dropped_ticks_max": _safe_float(metrics.get("chainlink_dropped_ticks_max")),
        }

    _lineage_presence_and_validity_checks(
        label="soak",
        identity=soak_identity,
        findings=findings,
        decision_trace=decision_trace,
    )
    _lineage_presence_and_validity_checks(
        label="reconcile",
        identity=reconcile_identity,
        findings=findings,
        decision_trace=decision_trace,
    )
    if websocket is not None:
        _lineage_presence_and_validity_checks(
            label="websocket",
            identity=websocket_identity,
            findings=findings,
            decision_trace=decision_trace,
        )
    _require_soak_lineage_consistency(
        soak=soak,
        soak_identity=soak_identity,
        findings=findings,
        decision_trace=decision_trace,
    )

    if quote_uptime < float(min_uptime_ratio):
        findings.append(f"quote_uptime_ratio_too_low:{quote_uptime:.6f}<min:{float(min_uptime_ratio):.6f}")
    decision_trace.append(
        decision_item(
            check="soak_uptime_ratio",
            level="hard_fail",
            metric="quote_uptime_ratio",
            comparator="min",
            value=quote_uptime,
            threshold=float(min_uptime_ratio),
            passed=(quote_uptime >= float(min_uptime_ratio)),
            note="hard fail threshold",
        )
    )
    if error_rows > int(max_error_rows):
        findings.append(f"error_rows_too_high:{error_rows}>max:{int(max_error_rows)}")
    decision_trace.append(
        decision_item(
            check="soak_error_rows",
            level="hard_fail",
            metric="error_rows",
            comparator="max",
            value=float(error_rows),
            threshold=float(max_error_rows),
            passed=(error_rows <= int(max_error_rows)),
            note="hard fail threshold",
        )
    )
    if exec_quality < float(min_execution_quality_net):
        findings.append(
            f"execution_quality_net_too_low:{exec_quality:.6f}<min:{float(min_execution_quality_net):.6f}"
        )
    decision_trace.append(
        decision_item(
            check="execution_quality_net",
            level="hard_fail",
            metric="capture_minus_adverse",
            comparator="min",
            value=exec_quality,
            threshold=float(min_execution_quality_net),
            passed=(exec_quality >= float(min_execution_quality_net)),
            note="hard fail threshold",
        )
    )
    if mismatch_ratio > float(max_reconcile_mismatch_ratio):
        findings.append(
            f"reconcile_mismatch_ratio_too_high:{mismatch_ratio:.6f}>max:{float(max_reconcile_mismatch_ratio):.6f}"
        )
    reconcile_status = str(reconcile.get("status") or "").strip().lower()
    if reconcile_status in {"mismatch", "failed", "error"}:
        findings.append(f"reconcile_status_not_ok:{reconcile_status}")
    reconcile_exceeds_threshold = bool(reconcile.get("exceeds_threshold", False))
    if reconcile_exceeds_threshold:
        findings.append("reconcile_exceeds_threshold_true")
    decision_trace.append(
        decision_item(
            check="reconcile_mismatch_ratio",
            level="hard_fail",
            metric="mismatch_ratio",
            comparator="max",
            value=mismatch_ratio,
            threshold=float(max_reconcile_mismatch_ratio),
            passed=(mismatch_ratio <= float(max_reconcile_mismatch_ratio)),
            note="hard fail threshold (when available)",
        )
    )
    decision_trace.append(
        decision_item(
            check="reconcile_status",
            level="hard_fail",
            metric="status",
            comparator="neq",
            value=0.0 if reconcile_status in {"mismatch", "failed", "error"} else 1.0,
            threshold=1.0,
            passed=(reconcile_status not in {"mismatch", "failed", "error"}),
            note=f"status={reconcile_status or 'unknown'}",
        )
    )
    decision_trace.append(
        decision_item(
            check="reconcile_exceeds_threshold",
            level="hard_fail",
            metric="exceeds_threshold",
            comparator="max",
            value=1.0 if reconcile_exceeds_threshold else 0.0,
            threshold=0.0,
            passed=(not reconcile_exceeds_threshold),
            note="reconcile report threshold breach must block promotion",
        )
    )

    for key in ("run_id", "config_fingerprint_sha256", "code_fingerprint_sha256", "git_commit", "profile_name"):
        left = _identity_value(soak_identity, key)
        right = _identity_value(reconcile_identity, key)
        mismatch = bool(left and right and left != right)
        if mismatch:
            findings.append(f"artifact_identity_mismatch:{key}:soak_vs_reconcile")
        decision_trace.append(
            decision_item(
                check=f"artifact_identity_{key}_soak_vs_reconcile",
                level="hard_fail",
                metric=f"identity_{key}",
                comparator="eq",
                value=1.0 if not mismatch else 0.0,
                threshold=1.0,
                passed=(not mismatch),
                note=f"soak={left or 'missing'} reconcile={right or 'missing'}",
            )
        )
        if websocket is not None:
            ws_val = _identity_value(websocket_identity, key)
            mismatch_ws = bool(left and ws_val and left != ws_val)
            if mismatch_ws:
                findings.append(f"artifact_identity_mismatch:{key}:soak_vs_websocket")
            decision_trace.append(
                decision_item(
                    check=f"artifact_identity_{key}_soak_vs_websocket",
                    level="hard_fail",
                    metric=f"identity_{key}",
                    comparator="eq",
                    value=1.0 if not mismatch_ws else 0.0,
                    threshold=1.0,
                    passed=(not mismatch_ws),
                    note=f"soak={left or 'missing'} websocket={ws_val or 'missing'}",
                )
            )

    if not verification_level_ok:
        advisories.append(f"reconcile_not_fully_venue_verified:{verification_level}")
    decision_trace.append(
        decision_item(
            check="reconcile_verification_level",
            level="advisory",
            metric="verification_level",
            comparator="eq",
            value=1.0 if verification_level_ok else 0.0,
            threshold=1.0,
            passed=verification_level_ok,
            note=(
                f"verification_level={verification_level}"
                + (
                    ""
                    if not allowed_nonvenue_levels
                    else f":allowed_nonvenue={','.join(sorted(allowed_nonvenue_levels))}"
                )
            ),
        )
    )
    if websocket_report_required and websocket is None:
        findings.append("websocket_promotion_report_missing")
    decision_trace.append(
        decision_item(
            check="websocket_report_presence",
            level="hard_fail" if websocket_report_required else "advisory",
            metric="report_present",
            comparator="min",
            value=1.0 if websocket is not None else 0.0,
            threshold=1.0 if websocket_report_required else 0.0,
            passed=(websocket is not None) if websocket_report_required else True,
            note="websocket report can be policy-required",
        )
    )
    if websocket is not None:
        if websocket_metrics["book_feed_down_ratio"] > float(max_websocket_book_feed_down_ratio):
            findings.append(
                "websocket_promotion_book_feed_down_ratio_too_high:"
                + f"{websocket_metrics['book_feed_down_ratio']:.6f}>max:{float(max_websocket_book_feed_down_ratio):.6f}"
            )
        if websocket_metrics["chainlink_down_ratio"] > float(max_websocket_chainlink_down_ratio):
            findings.append(
                "websocket_promotion_chainlink_down_ratio_too_high:"
                + f"{websocket_metrics['chainlink_down_ratio']:.6f}>max:{float(max_websocket_chainlink_down_ratio):.6f}"
            )
        if websocket_metrics["book_feed_reconnects_per_hour"] > float(max_websocket_book_feed_reconnects_per_hour):
            findings.append(
                "websocket_promotion_book_feed_reconnects_per_hour_too_high:"
                + f"{websocket_metrics['book_feed_reconnects_per_hour']:.6f}>max:{float(max_websocket_book_feed_reconnects_per_hour):.6f}"
            )
        if websocket_metrics["chainlink_reconnects_per_hour"] > float(max_websocket_chainlink_reconnects_per_hour):
            findings.append(
                "websocket_promotion_chainlink_reconnects_per_hour_too_high:"
                + f"{websocket_metrics['chainlink_reconnects_per_hour']:.6f}>max:{float(max_websocket_chainlink_reconnects_per_hour):.6f}"
            )
        if websocket_metrics["chainlink_dropped_ticks_max"] > float(max_websocket_chainlink_dropped_ticks):
            findings.append(
                "websocket_promotion_chainlink_dropped_ticks_too_high:"
                + f"{websocket_metrics['chainlink_dropped_ticks_max']:.6f}>max:{float(max_websocket_chainlink_dropped_ticks):.6f}"
            )
        decision_trace.extend(
            [
                decision_item(
                    check="websocket_book_feed_down_ratio",
                    level="hard_fail",
                    metric="book_feed_down_ratio",
                    comparator="max",
                    value=websocket_metrics["book_feed_down_ratio"],
                    threshold=float(max_websocket_book_feed_down_ratio),
                    passed=(websocket_metrics["book_feed_down_ratio"] <= float(max_websocket_book_feed_down_ratio)),
                    note="hard fail threshold",
                ),
                decision_item(
                    check="websocket_chainlink_down_ratio",
                    level="hard_fail",
                    metric="chainlink_down_ratio",
                    comparator="max",
                    value=websocket_metrics["chainlink_down_ratio"],
                    threshold=float(max_websocket_chainlink_down_ratio),
                    passed=(websocket_metrics["chainlink_down_ratio"] <= float(max_websocket_chainlink_down_ratio)),
                    note="hard fail threshold",
                ),
                decision_item(
                    check="websocket_book_feed_reconnects_per_hour",
                    level="warning",
                    metric="book_feed_reconnects_per_hour",
                    comparator="max",
                    value=websocket_metrics["book_feed_reconnects_per_hour"],
                    threshold=float(max_websocket_book_feed_reconnects_per_hour),
                    passed=(
                        websocket_metrics["book_feed_reconnects_per_hour"]
                        <= float(max_websocket_book_feed_reconnects_per_hour)
                    ),
                    note="stability warning signal",
                ),
                decision_item(
                    check="websocket_chainlink_reconnects_per_hour",
                    level="warning",
                    metric="chainlink_reconnects_per_hour",
                    comparator="max",
                    value=websocket_metrics["chainlink_reconnects_per_hour"],
                    threshold=float(max_websocket_chainlink_reconnects_per_hour),
                    passed=(
                        websocket_metrics["chainlink_reconnects_per_hour"]
                        <= float(max_websocket_chainlink_reconnects_per_hour)
                    ),
                    note="stability warning signal",
                ),
                decision_item(
                    check="websocket_chainlink_dropped_ticks_max",
                    level="hard_fail",
                    metric="chainlink_dropped_ticks_max",
                    comparator="max",
                    value=websocket_metrics["chainlink_dropped_ticks_max"],
                    threshold=float(max_websocket_chainlink_dropped_ticks),
                    passed=(
                        websocket_metrics["chainlink_dropped_ticks_max"]
                        <= float(max_websocket_chainlink_dropped_ticks)
                    ),
                    note="hard fail threshold",
                ),
            ]
        )
        warnings.extend(
            [
                f"decision_trace_warn:{item['check']}"
                for item in decision_trace
                if (item.get("level") == "warning" and not bool(item.get("passed", False)))
            ]
        )

    return {
        "ts_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": str(os.getenv("BRO_ENV", "")).strip() or "unknown",
        "soak_report_path": str(soak_report_path.resolve()),
        "reconcile_report_path": str(reconcile_report_path.resolve()),
        "websocket_report_path": str(websocket_report_path.resolve()) if websocket_report_path is not None else "",
        "metrics": {
            "quote_uptime_ratio": quote_uptime,
            "error_rows": error_rows,
            "execution_quality_net": exec_quality,
            "maker_reference_direct_midpoint_activity": _safe_float(
                soak.get("maker_reference_direct_midpoint_activity")
            ),
            "maker_reference_bounded_fallback_activity": _safe_float(
                soak.get("maker_reference_bounded_fallback_activity")
            ),
            "maker_market_reference_fallback_bid_count": _safe_float(
                soak.get("maker_market_reference_fallback_bid_count")
            ),
            "maker_market_reference_fallback_ask_count": _safe_float(
                soak.get("maker_market_reference_fallback_ask_count")
            ),
            "reconcile_mismatch_ratio": mismatch_ratio,
            "reconcile_verification_level": verification_level,
            "reconcile_status": reconcile_status,
            "reconcile_exceeds_threshold": 1.0 if reconcile_exceeds_threshold else 0.0,
            **websocket_metrics,
        },
        "artifact_identity": {
            "soak": soak_identity,
            "reconcile": reconcile_identity,
            "run_id": str(soak_identity.get("run_id") or reconcile_identity.get("run_id") or ""),
            "config_fingerprint_sha256": str(
                soak_identity.get("config_fingerprint_sha256")
                or reconcile_identity.get("config_fingerprint_sha256")
                or ""
            ),
            "code_fingerprint_sha256": str(
                soak_identity.get("code_fingerprint_sha256")
                or reconcile_identity.get("code_fingerprint_sha256")
                or ""
            ),
            "git_commit": str(soak_identity.get("git_commit") or reconcile_identity.get("git_commit") or ""),
            "dependency_lock_sha256": str(
                soak_identity.get("dependency_lock_sha256")
                or reconcile_identity.get("dependency_lock_sha256")
                or ""
            ),
            "profile_name": str(soak_identity.get("profile_name") or reconcile_identity.get("profile_name") or ""),
            "manifest_present": (
                bool(soak_identity.get("manifest_present", False))
                if isinstance(soak_identity, dict) and len(soak_identity) > 0
                else bool(reconcile_identity.get("manifest_present", False))
            ),
            "manifest_load_error": str(
                soak_identity.get("manifest_load_error")
                or reconcile_identity.get("manifest_load_error")
                or ""
            ),
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "environment": str(os.getenv("BRO_ENV", "")).strip() or "unknown",
        },
        "thresholds": {
            "min_uptime_ratio": float(min_uptime_ratio),
            "max_error_rows": int(max_error_rows),
            "min_execution_quality_net": float(min_execution_quality_net),
            "max_reconcile_mismatch_ratio": float(max_reconcile_mismatch_ratio),
            "max_websocket_book_feed_down_ratio": float(max_websocket_book_feed_down_ratio),
            "max_websocket_chainlink_down_ratio": float(max_websocket_chainlink_down_ratio),
            "max_websocket_book_feed_reconnects_per_hour": float(max_websocket_book_feed_reconnects_per_hour),
            "max_websocket_chainlink_reconnects_per_hour": float(max_websocket_chainlink_reconnects_per_hour),
            "max_websocket_chainlink_dropped_ticks": float(max_websocket_chainlink_dropped_ticks),
            "websocket_report_required": bool(websocket_report_required),
            "allowed_nonvenue_verification_levels": sorted(allowed_nonvenue_levels),
        },
        "finding_count": len(findings),
        "findings": findings,
        "warning_count": len(sorted(set(warnings))),
        "warnings": sorted(set(warnings)),
        "advisory_count": len(sorted(set(advisories))),
        "advisories": sorted(set(advisories)),
        "decision_trace": decision_trace,
        "threshold_semantics": {
            "hard_fail": [
                "quote_uptime_ratio",
                "error_rows",
                "execution_quality_net",
                "reconcile_mismatch_ratio",
                "reconcile_status",
                "reconcile_exceeds_threshold",
                "artifact identity consistency across evidence artifacts",
                "websocket report presence (if required)",
                "websocket down ratios",
                "websocket chainlink_dropped_ticks_max",
            ],
            "warning": [
                "websocket reconnect rates",
            ],
            "advisory": [
                "reconcile_verification_level",
            ],
        },
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro promotion-by-evidence gate")
    parser.add_argument("--policy", default="ops/promotion_policy.yaml", help="Promotion policy YAML path")
    parser.add_argument("--soak-report", required=True, help="Path to nightly_soak_report JSON")
    parser.add_argument("--reconcile-report", required=True, help="Path to reconcile_daily JSON")
    parser.add_argument("--websocket-report", default="", help="Optional websocket reliability gate JSON")
    parser.add_argument("--min-uptime-ratio", type=float, default=None, help="Minimum quote_uptime_ratio")
    parser.add_argument("--max-error-rows", type=int, default=None, help="Maximum tolerated soak error_rows")
    parser.add_argument("--min-exec-quality-net", type=float, default=None, help="Minimum capture_minus_adverse")
    parser.add_argument("--max-reconcile-mismatch-ratio", type=float, default=None, help="Maximum mismatch_ratio")
    parser.add_argument("--max-websocket-book-feed-down-ratio", type=float, default=None)
    parser.add_argument("--max-websocket-chainlink-down-ratio", type=float, default=None)
    parser.add_argument("--max-websocket-book-feed-reconnects-per-hour", type=float, default=None)
    parser.add_argument("--max-websocket-chainlink-reconnects-per-hour", type=float, default=None)
    parser.add_argument("--max-websocket-chainlink-dropped-ticks", type=float, default=None)
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    policy_payload = yaml.safe_load(pathlib.Path(args.policy).resolve().read_text(encoding="utf-8")) or {}
    if not isinstance(policy_payload, dict):
        raise ValueError("promotion policy root must be a mapping")
    soak_payload = json.loads(pathlib.Path(args.soak_report).resolve().read_text(encoding="utf-8"))
    soak_identity = soak_payload.get("artifact_identity") if isinstance(soak_payload, dict) else {}
    profile_name = str((soak_identity or {}).get("profile_name") or "").strip()
    ws_policy = policy_payload.get("websocket", {})
    if not isinstance(ws_policy, dict):
        ws_policy = {}
    reconcile_policy = policy_payload.get("reconcile", {})
    if not isinstance(reconcile_policy, dict):
        reconcile_policy = {}
    profiles_policy = policy_payload.get("profiles", {})
    if not isinstance(profiles_policy, dict):
        profiles_policy = {}
    profile_policy = profiles_policy.get(profile_name, {})
    if not isinstance(profile_policy, dict):
        profile_policy = {}
    profile_ws_policy = profile_policy.get("websocket", {})
    if not isinstance(profile_ws_policy, dict):
        profile_ws_policy = {}
    profile_reconcile_policy = profile_policy.get("reconcile", {})
    if not isinstance(profile_reconcile_policy, dict):
        profile_reconcile_policy = {}

    def _pick_float(value: Any, fallback: float) -> float:
        if value is None:
            return fallback
        return float(value)

    def _pick_int(value: Any, fallback: int) -> int:
        if value is None:
            return fallback
        return int(value)

    min_uptime_ratio = _pick_float(
        args.min_uptime_ratio,
        float(profile_policy.get("min_uptime_ratio", policy_payload.get("min_uptime_ratio", 0.99))),
    )
    max_error_rows = _pick_int(
        args.max_error_rows,
        int(profile_policy.get("max_error_rows", policy_payload.get("max_error_rows", 0))),
    )
    min_exec_quality_net = _pick_float(
        args.min_exec_quality_net,
        float(profile_policy.get("min_execution_quality_net", policy_payload.get("min_execution_quality_net", 0.0))),
    )
    max_reconcile_mismatch_ratio = _pick_float(
        args.max_reconcile_mismatch_ratio,
        float(profile_policy.get("max_reconcile_mismatch_ratio", policy_payload.get("max_reconcile_mismatch_ratio", 0.02))),
    )
    max_websocket_book_feed_down_ratio = _pick_float(
        args.max_websocket_book_feed_down_ratio,
        float(profile_ws_policy.get("max_book_feed_down_ratio", ws_policy.get("max_book_feed_down_ratio", 0.2))),
    )
    max_websocket_chainlink_down_ratio = _pick_float(
        args.max_websocket_chainlink_down_ratio,
        float(profile_ws_policy.get("max_chainlink_down_ratio", ws_policy.get("max_chainlink_down_ratio", 0.2))),
    )
    max_websocket_book_feed_reconnects_per_hour = _pick_float(
        args.max_websocket_book_feed_reconnects_per_hour,
        float(
            profile_ws_policy.get(
                "max_book_feed_reconnects_per_hour",
                ws_policy.get("max_book_feed_reconnects_per_hour", 40.0),
            )
        ),
    )
    max_websocket_chainlink_reconnects_per_hour = _pick_float(
        args.max_websocket_chainlink_reconnects_per_hour,
        float(
            profile_ws_policy.get(
                "max_chainlink_reconnects_per_hour",
                ws_policy.get("max_chainlink_reconnects_per_hour", 40.0),
            )
        ),
    )
    max_websocket_chainlink_dropped_ticks = _pick_float(
        args.max_websocket_chainlink_dropped_ticks,
        float(profile_ws_policy.get("max_chainlink_dropped_ticks", ws_policy.get("max_chainlink_dropped_ticks", 0.0))),
    )
    websocket_report_required = bool(profile_ws_policy.get("report_required", ws_policy.get("report_required", False)))
    allowed_nonvenue_verification_levels = profile_reconcile_policy.get(
        "allowed_nonvenue_verification_levels",
        reconcile_policy.get("allowed_nonvenue_verification_levels", []),
    )
    if not isinstance(allowed_nonvenue_verification_levels, list):
        allowed_nonvenue_verification_levels = []

    raw_websocket = str(args.websocket_report).strip()
    result = run_gate(
        soak_report_path=pathlib.Path(args.soak_report),
        reconcile_report_path=pathlib.Path(args.reconcile_report),
        websocket_report_path=(pathlib.Path(raw_websocket) if raw_websocket else None),
        min_uptime_ratio=max(0.0, min(1.0, float(min_uptime_ratio))),
        max_error_rows=max(0, int(max_error_rows)),
        min_execution_quality_net=float(min_exec_quality_net),
        max_reconcile_mismatch_ratio=max(0.0, float(max_reconcile_mismatch_ratio)),
        max_websocket_book_feed_down_ratio=max(0.0, min(1.0, float(max_websocket_book_feed_down_ratio))),
        max_websocket_chainlink_down_ratio=max(0.0, min(1.0, float(max_websocket_chainlink_down_ratio))),
        max_websocket_book_feed_reconnects_per_hour=max(0.0, float(max_websocket_book_feed_reconnects_per_hour)),
        max_websocket_chainlink_reconnects_per_hour=max(0.0, float(max_websocket_chainlink_reconnects_per_hour)),
        max_websocket_chainlink_dropped_ticks=max(0.0, float(max_websocket_chainlink_dropped_ticks)),
        websocket_report_required=websocket_report_required,
        allowed_nonvenue_verification_levels=[
            str(value).strip().lower()
            for value in allowed_nonvenue_verification_levels
            if str(value).strip()
        ],
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
