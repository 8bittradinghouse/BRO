#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import statistics
from collections import Counter, defaultdict
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "logs_exec" / "paper_universal" / "fusion_core_profile_latest"
TOOL_ID = "FM-2A1"
TOOL_NAME = "Fusion Core Profiling Tool"
TOOL_ALIAS = "FM-2A1"
TOOL_ROLE = "lathe"
TOOL_SCHEMA_VERSION = 2
FMA_MANIFEST_NAME = "fma_bundle_manifest.json"
RESCUE_RATIO_MIN_ABS_DECISION_DEBT = 1.0
FULL_DEPTH_MIN_DEEP_COVERAGE_RATIO = 0.9
BOUNDED_MIN_SAMPLE_COUNT = 5
STRONG_MIN_SAMPLE_COUNT = 20
STRONG_MIN_SOURCE_RUN_COUNT = 3
REQUIRED_ARTIFACTS = {
    "run_index_jsonl": "run_index.jsonl",
    "anomaly_summary_json": "anomaly_summary.json",
    "metric_catalog_json": "metric_catalog.json",
}
CSV_FIELDS = [
    "profile_id",
    "lane",
    "profile_family",
    "profile_kind",
    "population_type",
    "mode",
    "depth_class",
    "stability_grade",
    "sample_count",
    "source_run_count",
    "basis_class",
    "horizon_class",
    "heuristic_flag_count",
    "suppression_flag_count",
    "headline_metric",
    "headline_value",
]
LANE_REGISTRY: dict[str, dict[str, Any]] = {
    "maker": {
        "display_name": "Solar Slug Maker Cannon",
        "max_depth_class": "full_depth",
        "bounded_only": False,
        "requires_deep_outcome_records_for_full_depth": True,
        "profile_families": [
            "outcome_balance",
            "multifill_wound",
            "singlefill_strength",
            "execution_rescue_geometry",
                "repeat_target_cluster",
                "complement_pair_cluster",
                "friction_burden",
                "viability_shadow",
                "valuation_pressure",
            ],
        },
    "taker": {
        "display_name": "Taker Katana",
        "max_depth_class": "bounded_depth",
        "bounded_only": True,
        "requires_deep_outcome_records_for_full_depth": False,
        "profile_families": [
            "window_conversion_overview",
        ],
    },
}

STABILITY_POLICY: dict[str, Any] = {
    "bounded_min_sample_count": BOUNDED_MIN_SAMPLE_COUNT,
    "strong_min_sample_count": STRONG_MIN_SAMPLE_COUNT,
    "strong_min_source_run_count": STRONG_MIN_SOURCE_RUN_COUNT,
    "specimen_max_grade": "bounded",
    "heuristic_population_max_grade": "bounded",
}
THRESHOLD_PRESSURE_PRESETS: dict[str, dict[str, Any]] = {
    "current": {},
    "tighter": {
        "bounded_min_sample_count": 8,
        "strong_min_sample_count": 30,
        "strong_min_source_run_count": 4,
    },
    "looser": {
        "bounded_min_sample_count": 3,
        "strong_min_sample_count": 15,
        "strong_min_source_run_count": 2,
    },
}


def _json_load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl_load(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(json.loads(raw))
    return rows


def _write_json(path: pathlib.Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _safe_ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.fmean(values)


def _numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p50": None,
            "p90": None,
        }
    ordered = sorted(values)
    p90_index = int((len(ordered) - 1) * 0.9)
    return {
        "count": len(values),
        "min": min(ordered),
        "max": max(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p50": statistics.median(ordered),
        "p90": ordered[p90_index],
    }


def _stability_policy_for_lane(lane: str) -> dict[str, Any]:
    lane_registry = LANE_REGISTRY.get(lane) or {}
    policy = dict(STABILITY_POLICY)
    for key in ("bounded_min_sample_count", "strong_min_sample_count", "strong_min_source_run_count"):
        if key in lane_registry:
            policy[key] = lane_registry[key]
    return policy


def _stability_policy_with_overrides(lane: str, overrides: dict[str, Any]) -> dict[str, Any]:
    policy = _stability_policy_for_lane(lane)
    policy.update(overrides)
    return policy


def _metric_drift_threshold(metric_name: str) -> float:
    lowered = metric_name.lower()
    if "ratio" in lowered or "rate" in lowered:
        return 0.05
    if any(token in lowered for token in ("count", "rows", "total", "sum")):
        return 1.0
    return 0.0


def _sorted_counter_dict(counter: Counter[str]) -> dict[str, float]:
    return {key: float(counter[key]) for key in sorted(counter)}


def _deterministic_profile_id(
    *,
    lane: str,
    profile_family: str,
    population_type: str,
    basis_class: str,
    horizon_class: str,
    cohort_dimensions: dict[str, Any],
) -> str:
    parts = [
        lane,
        profile_family,
        population_type,
        f"basis={basis_class}",
        f"horizon={horizon_class}",
    ]
    for key in sorted(cohort_dimensions):
        value = cohort_dimensions[key]
        parts.append(f"{key}={value}")
    return "|".join(parts)


def _resolve_artifact_paths(
    *,
    bundle_dir: pathlib.Path | None,
    run_index_path: pathlib.Path | None,
    anomaly_summary_path: pathlib.Path | None,
    metric_catalog_path: pathlib.Path | None,
) -> tuple[dict[str, pathlib.Path], pathlib.Path | None, str]:
    if bundle_dir is not None:
        bundle_dir = bundle_dir.resolve()
        paths = {key: bundle_dir / filename for key, filename in REQUIRED_ARTIFACTS.items()}
        return paths, bundle_dir / FMA_MANIFEST_NAME, "bundle_dir"

    explicit = {
        "run_index_jsonl": run_index_path,
        "anomaly_summary_json": anomaly_summary_path,
        "metric_catalog_json": metric_catalog_path,
    }
    missing = [key for key, value in explicit.items() if value is None]
    if missing:
        raise SystemExit(f"explicit artifact mode requires paths for: {', '.join(sorted(missing))}")
    paths = {key: pathlib.Path(value).resolve() for key, value in explicit.items() if value is not None}
    return paths, None, "explicit_paths"


def _derive_manifest(
    *,
    artifact_paths: dict[str, pathlib.Path],
    rows: list[dict[str, Any]],
    anomaly_summary: dict[str, Any],
    metric_catalog: dict[str, Any],
    manifest_status: str,
) -> dict[str, Any]:
    artifact_files: dict[str, dict[str, Any]] = {}
    for key, path in sorted(artifact_paths.items()):
        artifact_files[path.name] = {
            "output_key": key,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return {
        "manifest_schema_version": 1,
        "bundle_kind": "fma_export_bundle",
        "tool_id": "FM-1A1",
        "tool_alias": "FMA",
        "tool_name": "Forge Masters Archiver",
        "tool_schema_version": metric_catalog.get("schema_version") or anomaly_summary.get("schema_version"),
        "bundle_closed_snapshot": False,
        "manifest_status": manifest_status,
        "selected_run_count": len(rows),
        "selected_run_ids": sorted(str(row.get("run_id") or "").strip() for row in rows if str(row.get("run_id") or "").strip()),
        "artifact_files": artifact_files,
    }


def _build_deep_artifact_coverage_summary(
    rows: list[dict[str, Any]], deep_artifacts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    run_rows_with_report_dir = 0
    records_runs = 0
    audit_runs = 0
    soak_runs = 0
    for row in rows:
        report_dir_raw = row.get("report_dir")
        if isinstance(report_dir_raw, str) and report_dir_raw.strip():
            run_rows_with_report_dir += 1
        run_id = str(row.get("run_id") or "").strip()
        payload = deep_artifacts.get(run_id) or {}
        if payload.get("outcome_truth_records"):
            records_runs += 1
        if payload.get("outcome_truth_audit"):
            audit_runs += 1
        if payload.get("nightly_soak_report"):
            soak_runs += 1
    return {
        "run_rows_with_report_dir": run_rows_with_report_dir,
        "runs_with_outcome_truth_records": records_runs,
        "runs_with_outcome_truth_audit": audit_runs,
        "runs_with_nightly_soak_report": soak_runs,
    }


def _load_contract(
    *,
    bundle_dir: pathlib.Path | None,
    run_index_path: pathlib.Path | None,
    anomaly_summary_path: pathlib.Path | None,
    metric_catalog_path: pathlib.Path | None,
) -> dict[str, Any]:
    artifact_paths, manifest_path, bundle_origin = _resolve_artifact_paths(
        bundle_dir=bundle_dir,
        run_index_path=run_index_path,
        anomaly_summary_path=anomaly_summary_path,
        metric_catalog_path=metric_catalog_path,
    )

    missing_required = [name for name, path in artifact_paths.items() if not path.exists()]
    if missing_required:
        raise SystemExit(f"missing required artifacts: {', '.join(sorted(missing_required))}")

    rows = _jsonl_load(artifact_paths["run_index_jsonl"])
    anomaly_summary = _json_load(artifact_paths["anomaly_summary_json"])
    metric_catalog = _json_load(artifact_paths["metric_catalog_json"])

    manifest_status = "legacy_derived"
    manifest_payload: dict[str, Any]
    if manifest_path is not None and manifest_path.exists():
        manifest_status = "present"
        manifest_payload = _json_load(manifest_path)
    else:
        if bundle_origin == "explicit_paths":
            manifest_status = "explicit_paths_derived"
        manifest_payload = _derive_manifest(
            artifact_paths=artifact_paths,
            rows=rows,
            anomaly_summary=anomaly_summary,
            metric_catalog=metric_catalog,
            manifest_status=manifest_status,
        )

    return {
        "artifact_paths": artifact_paths,
        "bundle_origin": bundle_origin,
        "bundle_dir": bundle_dir.resolve() if bundle_dir is not None else None,
        "manifest_path": manifest_path.resolve() if manifest_path is not None and manifest_path.exists() else None,
        "manifest_status": manifest_status,
        "manifest": manifest_payload,
        "rows": rows,
        "anomaly_summary": anomaly_summary,
        "metric_catalog": metric_catalog,
    }


def _audit_input_contract(
    contract: dict[str, Any],
    requested_mode: str,
    deep_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = contract["rows"]
    anomaly_summary: dict[str, Any] = contract["anomaly_summary"]
    metric_catalog: dict[str, Any] = contract["metric_catalog"]
    manifest: dict[str, Any] = contract["manifest"]
    manifest_status: str = contract["manifest_status"]
    artifact_paths: dict[str, pathlib.Path] = contract["artifact_paths"]

    error_findings: list[str] = []
    warning_findings: list[str] = []
    run_index_count = len(rows)
    anomaly_run_count = _coerce_int(anomaly_summary.get("run_count"))
    metric_catalog_run_count = _coerce_int(metric_catalog.get("run_count"))
    if anomaly_run_count is not None and anomaly_run_count != run_index_count:
        error_findings.append(f"anomaly_summary_run_count_mismatch:{anomaly_run_count}!={run_index_count}")
    if metric_catalog_run_count is not None and metric_catalog_run_count != run_index_count:
        error_findings.append(f"metric_catalog_run_count_mismatch:{metric_catalog_run_count}!={run_index_count}")
    if not rows:
        error_findings.append("run_index_empty")

    if manifest_status == "present":
        if not bool(manifest.get("bundle_closed_snapshot")):
            warning_findings.append("manifest_present_but_bundle_not_closed_snapshot")
        snapshot_contract_status = "explicit_closed_snapshot" if bool(manifest.get("bundle_closed_snapshot")) else "explicit_snapshot_unclear"
        snapshot_integrity_class = "closed_snapshot" if bool(manifest.get("bundle_closed_snapshot")) else "snapshot_unclear"
        manifest_derivation_reason = "preferred_manifest_present"
    else:
        if manifest_status == "explicit_paths_derived":
            snapshot_contract_status = "explicit_artifact_contract_derived"
            snapshot_integrity_class = "explicit_paths_contract_derived"
            manifest_derivation_reason = "explicit_artifact_paths_no_bundle_manifest"
        else:
            snapshot_contract_status = "legacy_snapshot_assumed"
            snapshot_integrity_class = "legacy_snapshot_assumed"
            manifest_derivation_reason = "legacy_bundle_manifest_missing"
        warning_findings.append(f"manifest_missing_using_{manifest_status}")

    if requested_mode not in {"auto", "specimen", "corpus"}:
        error_findings.append(f"invalid_requested_mode:{requested_mode}")

    artifact_files: dict[str, Any] = {}
    for key, path in sorted(artifact_paths.items()):
        artifact_files[path.name] = {
            "output_key": key,
            "path": str(path),
            "exists": path.exists(),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }

    findings = error_findings + warning_findings
    ok = not error_findings
    ok_with_warnings = ok and bool(warning_findings)
    contract_health = "failed" if not ok else ("warning" if warning_findings else "clean")

    return {
        "tool_id": TOOL_ID,
        "tool_name": TOOL_NAME,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "bundle_origin": contract["bundle_origin"],
        "bundle_dir": str(contract["bundle_dir"]) if contract["bundle_dir"] is not None else None,
        "manifest_status": manifest_status,
        "manifest_derivation_reason": manifest_derivation_reason,
        "snapshot_contract_status": snapshot_contract_status,
        "snapshot_integrity_class": snapshot_integrity_class,
        "requested_mode": requested_mode,
        "required_artifacts": artifact_files,
        "schema_versions": {
            "manifest_schema_version": _coerce_int(manifest.get("manifest_schema_version")),
            "fma_tool_schema_version": _coerce_int(manifest.get("tool_schema_version")),
            "anomaly_summary_schema_version": _coerce_int(anomaly_summary.get("schema_version")),
            "metric_catalog_schema_version": _coerce_int(metric_catalog.get("schema_version")),
        },
        "deep_artifact_coverage_summary": _build_deep_artifact_coverage_summary(rows, deep_artifacts),
        "run_count_checks": {
            "run_index_rows": run_index_count,
            "anomaly_summary_run_count": anomaly_run_count,
            "metric_catalog_run_count": metric_catalog_run_count,
            "aligned": not any("run_count_mismatch" in finding for finding in error_findings),
        },
        "error_findings": error_findings,
        "warning_findings": warning_findings,
        "error_count": len(error_findings),
        "warning_count": len(warning_findings),
        "findings": findings,
        "bundle_contract_findings": findings,
        "ok": ok,
        "ok_with_warnings": ok_with_warnings,
        "contract_health": contract_health,
    }


def _load_optional_deep_artifacts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    deep_artifacts: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "").strip()
        report_dir_raw = row.get("report_dir")
        if not run_id or not isinstance(report_dir_raw, str) or not report_dir_raw.strip():
            continue
        report_dir = pathlib.Path(report_dir_raw)
        per_run: dict[str, Any] = {}
        records_path = report_dir / "outcome_truth_records.jsonl"
        if records_path.exists():
            per_run["outcome_truth_records"] = _jsonl_load(records_path)
        audit_path = report_dir / "outcome_truth_audit.json"
        if audit_path.exists():
            per_run["outcome_truth_audit"] = _json_load(audit_path)
        soak_path = report_dir / "nightly_soak_report.json"
        if soak_path.exists():
            per_run["nightly_soak_report"] = _json_load(soak_path)
        if per_run:
            deep_artifacts[run_id] = per_run
    return deep_artifacts


def _determine_mode(requested_mode: str, rows: list[dict[str, Any]]) -> str:
    if requested_mode in {"specimen", "corpus"}:
        return requested_mode
    return "specimen" if len(rows) <= 1 else "corpus"


def _build_lane_readiness(
    *,
    rows: list[dict[str, Any]],
    deep_artifacts: dict[str, dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    readiness: dict[str, Any] = {
        "tool_id": TOOL_ID,
        "tool_name": TOOL_NAME,
        "mode": mode,
        "lanes": {},
    }

    maker_deep_run_count = sum(1 for payload in deep_artifacts.values() if payload.get("outcome_truth_records"))
    maker_support_rows = sum(
        1 for row in rows if _coerce_float(row.get("maker_complete_record_count")) is not None or _coerce_float(row.get("maker_submits")) is not None
    )
    maker_deep_coverage_ratio = _safe_ratio(maker_deep_run_count, maker_support_rows)
    if maker_deep_run_count <= 0:
        maker_depth = "bounded_depth"
        maker_reason_codes = ["maker_deep_outcome_records_missing_bounded_only"]
        maker_promotion_blockers = ["deep_outcome_records_missing_for_full_depth"]
        maker_promotion_requirements = [
            {
                "kind": "coverage_ratio",
                "metric": "deep_coverage_ratio",
                "current": maker_deep_coverage_ratio,
                "required_min": FULL_DEPTH_MIN_DEEP_COVERAGE_RATIO,
            }
        ]
        maker_promotion_path = "earn_deep_outcome_coverage_for_full_depth"
    elif (maker_deep_coverage_ratio or 0.0) >= FULL_DEPTH_MIN_DEEP_COVERAGE_RATIO:
        maker_depth = "full_depth"
        maker_reason_codes = ["maker_deep_outcome_records_threshold_satisfied"]
        maker_promotion_blockers = []
        maker_promotion_requirements = []
        maker_promotion_path = "full_depth_ready"
    else:
        maker_depth = "mixed_depth_partial_deep"
        maker_reason_codes = ["maker_partial_deep_outcome_coverage"]
        maker_promotion_blockers = ["deep_coverage_below_full_depth_threshold"]
        maker_promotion_requirements = [
            {
                "kind": "coverage_ratio",
                "metric": "deep_coverage_ratio",
                "current": maker_deep_coverage_ratio,
                "required_min": FULL_DEPTH_MIN_DEEP_COVERAGE_RATIO,
            }
        ]
        maker_promotion_path = "increase_deep_outcome_coverage_for_full_depth"
    readiness["lanes"]["maker"] = {
        "display_name": LANE_REGISTRY["maker"]["display_name"],
        "depth_class": maker_depth,
        "can_emit_profiles": maker_support_rows > 0,
        "source_row_count": maker_support_rows,
        "deep_outcome_run_count": maker_deep_run_count,
        "deep_coverage_ratio": maker_deep_coverage_ratio,
        "full_depth_min_coverage_ratio": FULL_DEPTH_MIN_DEEP_COVERAGE_RATIO,
        "supported_profile_families": LANE_REGISTRY["maker"]["profile_families"],
        "reason_codes": maker_reason_codes,
        "promotion_blockers": maker_promotion_blockers,
        "promotion_requirements": maker_promotion_requirements,
        "promotion_path": maker_promotion_path,
    }

    taker_support_rows = sum(
        1 for row in rows if _coerce_float(row.get("taker_decision_count")) is not None or _coerce_float(row.get("taker_submits")) is not None
    )
    readiness["lanes"]["taker"] = {
        "display_name": LANE_REGISTRY["taker"]["display_name"],
        "depth_class": "bounded_depth",
        "can_emit_profiles": taker_support_rows > 0,
        "source_row_count": taker_support_rows,
        "deep_outcome_run_count": 0,
        "supported_profile_families": LANE_REGISTRY["taker"]["profile_families"],
        "reason_codes": ["taker_bounded_depth_only", "taker_summary_surfaces_present" if taker_support_rows > 0 else "taker_summary_surfaces_missing"],
        "promotion_blockers": ["lane_registry_bounded_only", "deep_taker_truth_mapping_not_earned"],
        "promotion_requirements": [
            {
                "kind": "mapping_packet",
                "metric": "taker_truth_mapping",
                "current": "bounded_summary_only",
                "required_state": "earned_deeper_truth_mapping",
            }
        ],
        "promotion_path": "earn_deeper_taker_truth_mapping_before_promotion",
    }
    return readiness


def _select_maker_records(deep_artifacts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    combined: list[dict[str, Any]] = []
    by_run: dict[str, list[dict[str, Any]]] = {}
    for run_id in sorted(deep_artifacts):
        raw_records = deep_artifacts[run_id].get("outcome_truth_records") or []
        maker_records = [
            record
            for record in raw_records
            if str(record.get("submission_lane_truth") or record.get("submission_scope_hint") or "").strip().lower() == "maker"
        ]
        if maker_records:
            by_run[run_id] = maker_records
            for record in maker_records:
                enriched = dict(record)
                enriched["_run_id"] = run_id
                combined.append(enriched)
    return combined, by_run


def _basis_class_from_records(records: list[dict[str, Any]]) -> str:
    pairs = sorted(
        {
            (
                str(record.get("decision_reference_basis") or "unknown"),
                str(record.get("eval_reference_basis") or "unknown"),
            )
            for record in records
        }
    )
    if not pairs:
        return "unknown"
    if len(pairs) == 1:
        return f"{pairs[0][0]}__{pairs[0][1]}"
    return "mixed_basis"


def _horizon_class_from_records(records: list[dict[str, Any]]) -> str:
    horizons = sorted({_coerce_int(record.get("evaluation_horizon_ms")) for record in records if _coerce_int(record.get("evaluation_horizon_ms")) is not None})
    if not horizons:
        return "unknown"
    if len(horizons) == 1:
        return f"h{horizons[0]}"
    return "mixed_horizon"


def _mean_decision_debt(records: list[dict[str, Any]]) -> float | None:
    values = [_coerce_float(record.get("decision_component_x_size")) for record in records]
    usable = [value for value in values if value is not None]
    return _safe_mean(usable)


def _mean_execution_rescue(records: list[dict[str, Any]]) -> float | None:
    values = [_coerce_float(record.get("execution_component_x_size")) for record in records]
    usable = [value for value in values if value is not None]
    return _safe_mean(usable)


def _weighted_ratio_from_records(records: list[dict[str, Any]], *, key: str, match: str) -> float | None:
    if not records:
        return None
    hits = sum(1 for record in records if str(record.get(key) or "").strip().lower() == match)
    return _safe_ratio(hits, len(records))


def _classify_stability(
    *,
    sample_count: int,
    source_run_count: int,
    mode: str,
    semantic_clean: bool,
    compatible: bool,
    heuristic_only: bool,
    suppression_flags: list[str],
    policy: dict[str, Any],
) -> str:
    if suppression_flags or not semantic_clean or not compatible or sample_count <= 0:
        return "suppressed"
    if mode == "specimen":
        if sample_count >= int(policy["bounded_min_sample_count"]):
            return "bounded"
        return "thin"
    if heuristic_only:
        if sample_count >= int(policy["bounded_min_sample_count"]) and source_run_count >= 1:
            return "bounded"
        return "thin"
    if sample_count >= int(policy["strong_min_sample_count"]) and source_run_count >= int(policy["strong_min_source_run_count"]):
        return "strong"
    if sample_count >= int(policy["bounded_min_sample_count"]):
        return "bounded"
    return "thin"


def _cap_stability_grade(stability_grade: str, allowed_max: str) -> str:
    order = {"suppressed": 0, "thin": 1, "bounded": 2, "strong": 3}
    inverse = {value: key for key, value in order.items()}
    return inverse[min(order[stability_grade], order[allowed_max])]


def _derive_grade_reason_codes(
    *,
    stability_grade: str,
    sample_count: int,
    source_run_count: int,
    mode: str,
    depth_class: str,
    heuristic_flags: list[str],
    suppression_flags: list[str],
    compatibility_flags: list[str],
    policy: dict[str, Any],
) -> tuple[list[str], list[str], str]:
    downgrade_reason_codes: list[str] = []
    suppression_reason_codes: list[str] = []

    if sample_count <= 0:
        suppression_reason_codes.append("zero_eligible_records")
    if depth_class == "bounded_depth":
        downgrade_reason_codes.append("lane_depth_cap")
    if depth_class == "mixed_depth_partial_deep":
        downgrade_reason_codes.append("lane_partial_deep_coverage")
    if mode == "specimen" and sample_count > 0:
        downgrade_reason_codes.append("mode_cap_specimen_only")
    if 0 < sample_count < int(policy["bounded_min_sample_count"]):
        downgrade_reason_codes.append("sample_count_below_bounded_floor")
    if mode == "corpus" and sample_count >= int(policy["bounded_min_sample_count"]) and sample_count < int(policy["strong_min_sample_count"]):
        downgrade_reason_codes.append("sample_count_below_strong_floor")
    if mode == "corpus" and sample_count >= int(policy["bounded_min_sample_count"]) and source_run_count < int(policy["strong_min_source_run_count"]):
        downgrade_reason_codes.append("cross_run_recurrence_below_strong_floor")
    if heuristic_flags:
        downgrade_reason_codes.append("heuristic_only_population")

    for flag in compatibility_flags:
        if flag not in suppression_reason_codes:
            suppression_reason_codes.append(flag)
    for flag in suppression_flags:
        if flag.startswith("incompatible_") or flag.startswith("population_") or flag.startswith("missing_required_"):
            if flag not in suppression_reason_codes:
                suppression_reason_codes.append(flag)
        elif flag not in downgrade_reason_codes and flag not in suppression_reason_codes:
            if stability_grade == "suppressed":
                suppression_reason_codes.append(flag)
            else:
                downgrade_reason_codes.append(flag)

    downgrade_reason_codes = sorted(set(downgrade_reason_codes))
    suppression_reason_codes = sorted(set(suppression_reason_codes))
    if stability_grade == "strong":
        rationale_summary = "strong profile; passed current stability gates without downgrade or suppression."
    elif stability_grade == "bounded":
        rationale_summary = "bounded profile; truth is usable, but current evidence or mode caps stronger promotion."
    elif stability_grade == "thin":
        rationale_summary = "thin profile; evidence exists, but sample depth is below the bounded promotion floor."
    else:
        rationale_summary = "suppressed profile; current stock is missing, incompatible, or unsafe to promote."
    return downgrade_reason_codes, suppression_reason_codes, rationale_summary


def _build_promotion_readiness(
    *,
    mode: str,
    depth_class: str,
    sample_count: int,
    source_run_count: int,
    heuristic_flags: list[str],
    suppression_flags: list[str],
    compatibility_flags: list[str],
    policy: dict[str, Any],
    grade_cap_reason_codes: list[str],
) -> dict[str, Any]:
    bounded_blockers: list[str] = []
    strong_blockers: list[str] = []
    bounded_min = int(policy["bounded_min_sample_count"])
    strong_min = int(policy["strong_min_sample_count"])
    strong_runs_min = int(policy["strong_min_source_run_count"])

    if sample_count <= 0:
        bounded_blockers.append("zero_eligible_records")
        strong_blockers.append("zero_eligible_records")
    if sample_count < bounded_min:
        bounded_blockers.append("sample_count_below_bounded_floor")
    if sample_count < strong_min:
        strong_blockers.append("sample_count_below_strong_floor")
    if mode == "specimen":
        strong_blockers.append("mode_cap_specimen_only")
    if depth_class == "bounded_depth":
        strong_blockers.append("lane_depth_cap")
    if depth_class == "mixed_depth_partial_deep":
        strong_blockers.append("lane_partial_deep_coverage")
    if source_run_count < strong_runs_min:
        strong_blockers.append("cross_run_recurrence_below_strong_floor")
    if heuristic_flags:
        strong_blockers.append("heuristic_only_population")

    for flag in compatibility_flags + suppression_flags + list(grade_cap_reason_codes):
        if flag.startswith("incompatible_") or flag.startswith("population_") or flag.startswith("missing_required_"):
            if flag not in bounded_blockers:
                bounded_blockers.append(flag)
            if flag not in strong_blockers:
                strong_blockers.append(flag)
        elif flag not in strong_blockers:
            strong_blockers.append(flag)

    bounded_blockers = sorted(set(bounded_blockers))
    strong_blockers = sorted(set(strong_blockers))
    return {
        "bounded_ready": not bounded_blockers,
        "strong_ready": not strong_blockers,
        "bounded_blockers": bounded_blockers,
        "strong_blockers": strong_blockers,
        "sample_gap_to_bounded": max(0, bounded_min - sample_count),
        "sample_gap_to_strong": max(0, strong_min - sample_count),
        "source_run_gap_to_strong": max(0, strong_runs_min - source_run_count),
    }


def _default_population_accounting(*, lane: str, population_type: str) -> dict[str, Any]:
    if population_type == "run_summary":
        return {
            "summary_level": "run_summary_aggregate",
            "note": f"{lane} profile aggregates run-level harvested summary rows rather than raw event rows.",
        }
    if population_type == "complete_outcome":
        return {
            "summary_level": "complete_outcome_records",
            "note": f"{lane} profile counts matured complete outcome records only.",
        }
    if population_type == "maker_outcome_records":
        return {
            "summary_level": "maker_outcome_records",
            "note": f"{lane} profile counts maker outcome records across complete and incomplete lifecycle geometry.",
        }
    return {
        "summary_level": population_type,
        "note": f"{lane} profile uses {population_type} population semantics.",
    }


def _lifecycle_basis_from_records(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not records:
        return None, None
    canonical_count = sum(1 for record in records if record.get("lifecycle_completeness") is not None)
    if canonical_count == len(records):
        return "canonical_field", "all contributing records carried explicit lifecycle_completeness fields"
    if canonical_count > 0:
        return "mixed", "some contributing records carried lifecycle_completeness fields while others required derived fill geometry"
    return "derived_fill_geometry", "lifecycle shape was derived from outcome status and fill geometry because explicit lifecycle fields were absent"


def _build_profile(
    *,
    lane: str,
    profile_family: str,
    profile_kind: str,
    population_type: str,
    mode: str,
    depth_class: str,
    basis_class: str,
    horizon_class: str,
    sample_count: int,
    source_runs: list[str],
    cohort_dimensions: dict[str, Any],
    metrics: dict[str, Any],
    heuristic_flags: list[str],
    suppression_flags: list[str],
    compatibility_flags: list[str],
    top_signals: list[str],
    population_accounting: dict[str, Any] | None = None,
    lifecycle_basis: str | None = None,
    lifecycle_basis_detail: str | None = None,
    grade_cap: str | None = None,
    grade_cap_reason_codes: list[str] | None = None,
    signal_posture: str | None = None,
) -> dict[str, Any]:
    policy = _stability_policy_for_lane(lane)
    profile_id = _deterministic_profile_id(
        lane=lane,
        profile_family=profile_family,
        population_type=population_type,
        basis_class=basis_class,
        horizon_class=horizon_class,
        cohort_dimensions=cohort_dimensions,
    )
    compatibility = not any(flag.startswith("incompatible_") for flag in compatibility_flags)
    stability_grade = _classify_stability(
        sample_count=sample_count,
        source_run_count=len(source_runs),
        mode=mode,
        semantic_clean=not any(flag.startswith("population_") for flag in suppression_flags),
        compatible=compatibility,
        heuristic_only=bool(heuristic_flags),
        suppression_flags=suppression_flags,
        policy=policy,
    )
    effective_grade_cap = grade_cap
    effective_grade_cap_reason_codes = sorted(set(grade_cap_reason_codes or []))
    if depth_class == "bounded_depth":
        if effective_grade_cap is None or effective_grade_cap == "strong":
            effective_grade_cap = "bounded"
        if "lane_depth_cap" not in effective_grade_cap_reason_codes:
            effective_grade_cap_reason_codes.append("lane_depth_cap")
    if effective_grade_cap is not None:
        capped_grade = _cap_stability_grade(stability_grade, effective_grade_cap)
        if capped_grade != stability_grade:
            stability_grade = capped_grade
    downgrade_reason_codes, suppression_reason_codes, rationale_summary = _derive_grade_reason_codes(
        stability_grade=stability_grade,
        sample_count=sample_count,
        source_run_count=len(source_runs),
        mode=mode,
        depth_class=depth_class,
        heuristic_flags=heuristic_flags,
        suppression_flags=suppression_flags,
        compatibility_flags=compatibility_flags,
        policy=policy,
    )
    for reason_code in effective_grade_cap_reason_codes:
        if reason_code not in downgrade_reason_codes and reason_code not in suppression_reason_codes:
            downgrade_reason_codes.append(reason_code)
    downgrade_reason_codes = sorted(set(downgrade_reason_codes))
    promotion_readiness = _build_promotion_readiness(
        mode=mode,
        depth_class=depth_class,
        sample_count=sample_count,
        source_run_count=len(source_runs),
        heuristic_flags=heuristic_flags,
        suppression_flags=suppression_flags,
        compatibility_flags=compatibility_flags,
        policy=policy,
        grade_cap_reason_codes=effective_grade_cap_reason_codes,
    )
    explainability = {
        "summary": f"{lane} {profile_family} built from {sample_count} relevant records across {len(source_runs)} runs.",
        "top_signals": top_signals,
        "downgrade_reasons": downgrade_reason_codes,
        "suppression_reasons": suppression_reason_codes,
        "grade_rationale_summary": rationale_summary,
    }
    effective_population_accounting = population_accounting or _default_population_accounting(
        lane=lane,
        population_type=population_type,
    )
    return {
        "profile_id": profile_id,
        "lane": lane,
        "profile_family": profile_family,
        "profile_kind": profile_kind,
        "population_type": population_type,
        "mode": mode,
        "depth_class": depth_class,
        "stability_grade": stability_grade,
        "sample_count": sample_count,
        "source_runs": sorted(source_runs),
        "source_run_count": len(source_runs),
        "basis_class": basis_class,
        "horizon_class": horizon_class,
        "stability_policy": policy,
        "stability_inputs": {
            "sample_count": sample_count,
            "source_run_count": len(source_runs),
            "mode": mode,
            "depth_class": depth_class,
            "heuristic_flag_count": len(set(heuristic_flags)),
        },
        "grade_cap": effective_grade_cap,
        "grade_cap_reason_codes": sorted(set(effective_grade_cap_reason_codes)),
        "promotion_readiness": promotion_readiness,
        "cohort_signature": {
            "lane": lane,
            "population_type": population_type,
            "basis_class": basis_class,
            "horizon_class": horizon_class,
            **{key: cohort_dimensions[key] for key in sorted(cohort_dimensions)},
        },
        "metrics": metrics,
        "heuristic_flags": sorted(set(heuristic_flags)),
        "suppression_flags": sorted(set(suppression_flags)),
        "compatibility_flags": sorted(set(compatibility_flags)),
        "downgrade_reason_codes": downgrade_reason_codes,
        "suppression_reason_codes": suppression_reason_codes,
        "grade_rationale_summary": rationale_summary,
        "signal_posture": signal_posture,
        "population_accounting": effective_population_accounting,
        "lifecycle_basis": lifecycle_basis,
        "lifecycle_basis_detail": lifecycle_basis_detail,
        "provenance": {
            "source_runs": sorted(source_runs),
            "record_source": "deep_outcome_records" if population_type != "run_summary" else "fma_run_index",
            "lifecycle_basis": lifecycle_basis,
            "lifecycle_basis_detail": lifecycle_basis_detail,
        },
        "explainability": explainability,
    }


def _build_maker_profiles(
    *,
    rows: list[dict[str, Any]],
    deep_artifacts: dict[str, dict[str, Any]],
    readiness: dict[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    lane_readiness = readiness["lanes"]["maker"]
    source_runs = sorted(str(row.get("run_id") or "").strip() for row in rows if str(row.get("run_id") or "").strip())
    depth_class = lane_readiness["depth_class"]
    profiles: list[dict[str, Any]] = []

    row_by_run = {str(row.get("run_id") or "").strip(): row for row in rows if str(row.get("run_id") or "").strip()}
    maker_records, records_by_run = _select_maker_records(deep_artifacts)
    complete_records = [
        record for record in maker_records if str(record.get("outcome_truth_status") or "").strip().lower() == "complete"
    ]
    incomplete_records = [record for record in maker_records if record not in complete_records]
    basis_class = _basis_class_from_records(maker_records) if maker_records else "unknown"
    horizon_class = _horizon_class_from_records(maker_records) if maker_records else "unknown"
    compatibility_flags: list[str] = []
    if basis_class == "mixed_basis":
        compatibility_flags.append("incompatible_basis")
    if horizon_class == "mixed_horizon":
        compatibility_flags.append("incompatible_horizon")
    lifecycle_basis, lifecycle_basis_detail = _lifecycle_basis_from_records(maker_records)
    soak_by_run = {
        run_id: (payload.get("nightly_soak_report") or {})
        for run_id, payload in deep_artifacts.items()
        if payload.get("nightly_soak_report")
    }

    if depth_class in {"full_depth", "mixed_depth_partial_deep"} and maker_records:
        complete_bad_ratio = _weighted_ratio_from_records(complete_records, key="decision_quality", match="incorrect")
        incomplete_bad_ratio = _weighted_ratio_from_records(incomplete_records, key="decision_quality", match="incorrect")
        outcome_metrics = {
            "complete_record_count": len(complete_records),
            "incomplete_record_count": len(incomplete_records),
            "complete_bad_ratio": complete_bad_ratio,
            "incomplete_bad_ratio": incomplete_bad_ratio,
            "decision_debt_mean": _mean_decision_debt(complete_records),
            "execution_rescue_mean": _mean_execution_rescue(complete_records),
        }
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="outcome_balance",
                profile_kind="overview",
                population_type="maker_outcome_records",
                mode=mode,
                depth_class=depth_class,
                basis_class=basis_class,
                horizon_class=horizon_class,
                sample_count=len(maker_records),
                source_runs=sorted(records_by_run),
                cohort_dimensions={"lifecycle": "complete_vs_incomplete", "slice": "all"},
                metrics=outcome_metrics,
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=compatibility_flags,
                top_signals=[
                    f"complete_bad_ratio={complete_bad_ratio}",
                    f"incomplete_bad_ratio={incomplete_bad_ratio}",
                ],
                lifecycle_basis=lifecycle_basis,
                lifecycle_basis_detail=lifecycle_basis_detail,
            )
        )

        multifill_records = [record for record in complete_records if (_coerce_int(record.get("fill_count")) or 0) >= 2]
        multifill_incorrect_ratio = _weighted_ratio_from_records(multifill_records, key="decision_quality", match="incorrect")
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="multifill_wound",
                profile_kind="wound",
                population_type="complete_outcome",
                mode=mode,
                depth_class=depth_class,
                basis_class=basis_class,
                horizon_class=horizon_class,
                sample_count=len(multifill_records),
                source_runs=sorted({record["_run_id"] for record in multifill_records}),
                cohort_dimensions={"fill_geometry": "multifill", "lifecycle": "complete"},
                metrics={
                    "multifill_complete_count": len(multifill_records),
                    "multifill_incorrect_ratio": multifill_incorrect_ratio,
                    "decision_debt_mean": _mean_decision_debt(multifill_records),
                    "execution_rescue_mean": _mean_execution_rescue(multifill_records),
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=compatibility_flags,
                top_signals=[
                    f"multifill_complete_count={len(multifill_records)}",
                    f"multifill_incorrect_ratio={multifill_incorrect_ratio}",
                ],
                lifecycle_basis=lifecycle_basis,
                lifecycle_basis_detail=lifecycle_basis_detail,
            )
        )

        singlefill_records = [record for record in complete_records if (_coerce_int(record.get("fill_count")) or 0) == 1]
        singlefill_correct_ratio = _weighted_ratio_from_records(singlefill_records, key="decision_quality", match="correct")
        singlefill_signal_posture = (
            "positive_strength"
            if (singlefill_correct_ratio or 0.0) >= 0.6
            else "weak_strength_signal"
        )
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="singlefill_strength",
                profile_kind="strength",
                population_type="complete_outcome",
                mode=mode,
                depth_class=depth_class,
                basis_class=basis_class,
                horizon_class=horizon_class,
                sample_count=len(singlefill_records),
                source_runs=sorted({record["_run_id"] for record in singlefill_records}),
                cohort_dimensions={"fill_geometry": "singlefill", "lifecycle": "complete"},
                metrics={
                    "singlefill_complete_count": len(singlefill_records),
                    "singlefill_correct_ratio": singlefill_correct_ratio,
                    "decision_debt_mean": _mean_decision_debt(singlefill_records),
                    "execution_rescue_mean": _mean_execution_rescue(singlefill_records),
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=compatibility_flags,
                top_signals=[
                    f"singlefill_complete_count={len(singlefill_records)}",
                    f"singlefill_correct_ratio={singlefill_correct_ratio}",
                ],
                lifecycle_basis=lifecycle_basis,
                lifecycle_basis_detail=lifecycle_basis_detail,
                grade_cap="bounded" if (singlefill_correct_ratio or 0.0) < 0.6 else None,
                grade_cap_reason_codes=["family_signal_not_positive"] if (singlefill_correct_ratio or 0.0) < 0.6 else [],
                signal_posture=singlefill_signal_posture,
            )
        )

        rescue_records = []
        rescue_ratio_records = []
        rescue_ratio_records_excluded_low_debt = 0
        rescue_ratios: list[float] = []
        rescue_overcome_count = 0
        for record in complete_records:
            decision_debt = _coerce_float(record.get("decision_component_x_size"))
            execution_rescue = _coerce_float(record.get("execution_component_x_size"))
            if decision_debt is None or execution_rescue is None or decision_debt == 0:
                continue
            rescue_records.append(record)
            if abs(decision_debt) >= RESCUE_RATIO_MIN_ABS_DECISION_DEBT:
                rescue_ratio_records.append(record)
                rescue_ratios.append(abs(execution_rescue) / abs(decision_debt))
            else:
                rescue_ratio_records_excluded_low_debt += 1
            if abs(execution_rescue) > abs(decision_debt):
                rescue_overcome_count += 1
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="execution_rescue_geometry",
                profile_kind="overview",
                population_type="complete_outcome",
                mode=mode,
                depth_class=depth_class,
                basis_class=basis_class,
                horizon_class=horizon_class,
                sample_count=len(rescue_records),
                source_runs=sorted({record["_run_id"] for record in rescue_records}),
                cohort_dimensions={"slice": "rescue_capable_complete"},
                metrics={
                    "execution_rescue_overcome_count": rescue_overcome_count,
                    "execution_rescue_overcome_rate": _safe_ratio(rescue_overcome_count, len(rescue_records)),
                    "execution_rescue_ratio_summary": _numeric_summary(rescue_ratios),
                    "execution_rescue_ratio_eligible_count": len(rescue_ratio_records),
                    "execution_rescue_ratio_excluded_low_debt_count": rescue_ratio_records_excluded_low_debt,
                    "execution_rescue_ratio_min_abs_decision_debt": RESCUE_RATIO_MIN_ABS_DECISION_DEBT,
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=compatibility_flags,
                top_signals=[
                    f"execution_rescue_overcome_count={rescue_overcome_count}",
                    f"execution_rescue_ratio_mean={_numeric_summary(rescue_ratios)['mean']}",
                ],
                lifecycle_basis=lifecycle_basis,
                lifecycle_basis_detail=lifecycle_basis_detail,
                signal_posture="rescue_geometry",
            )
        )

    repeat_cluster_total = sum(int(_coerce_float(row.get("maker_same_target_repeat_cluster_count")) or 0) for row in rows)
    if repeat_cluster_total > 0:
        repeat_runs = [
            str(row.get("run_id") or "").strip()
            for row in rows
            if (_coerce_float(row.get("maker_same_target_repeat_cluster_count")) or 0) > 0
        ]
        repeat_summary = {
            str(row.get("run_id") or "").strip(): row.get("maker_same_target_repeat_cluster_summary")
            for row in rows
            if row.get("maker_same_target_repeat_cluster_summary")
        }
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="repeat_target_cluster",
                profile_kind="wound",
                population_type="run_summary",
                mode=mode,
                depth_class=depth_class,
                basis_class="run_summary_mixed",
                horizon_class="run_summary_mixed",
                sample_count=repeat_cluster_total,
                source_runs=repeat_runs,
                cohort_dimensions={"slice": "repeat_target_clusters"},
                metrics={
                    "repeat_cluster_count_total": repeat_cluster_total,
                    "repeat_cluster_runs": len(repeat_runs),
                    "repeat_cluster_summary_by_run": repeat_summary,
                },
                heuristic_flags=["bounded_repeat_target_heuristic"],
                suppression_flags=[],
                compatibility_flags=[],
                top_signals=[
                    f"repeat_cluster_count_total={repeat_cluster_total}",
                    f"repeat_cluster_runs={len(repeat_runs)}",
                ],
            )
        )

    complement_pair_total = sum(int(_coerce_float(row.get("maker_complement_pair_cluster_count")) or 0) for row in rows)
    complement_pair_debt_sum = sum(float(_coerce_float(row.get("maker_complement_pair_cluster_decision_debt_sum")) or 0.0) for row in rows)
    if complement_pair_total > 0:
        example_map = {
            str(row.get("run_id") or "").strip(): row.get("maker_complement_pair_cluster_examples")
            for row in rows
            if row.get("maker_complement_pair_cluster_examples")
        }
        complement_runs = [
            str(row.get("run_id") or "").strip()
            for row in rows
            if (_coerce_float(row.get("maker_complement_pair_cluster_count")) or 0) > 0
        ]
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="complement_pair_cluster",
                profile_kind="wound",
                population_type="run_summary",
                mode=mode,
                depth_class=depth_class,
                basis_class="run_summary_mixed",
                horizon_class="run_summary_mixed",
                sample_count=complement_pair_total,
                source_runs=complement_runs,
                cohort_dimensions={"slice": "complement_pair_clusters"},
                metrics={
                    "complement_pair_cluster_count_total": complement_pair_total,
                    "complement_pair_cluster_decision_debt_sum": complement_pair_debt_sum,
                    "complement_pair_examples_by_run": example_map,
                },
                heuristic_flags=["bounded_complement_pair_heuristic"],
                suppression_flags=[],
                compatibility_flags=[],
                top_signals=[
                    f"complement_pair_cluster_count_total={complement_pair_total}",
                    f"complement_pair_cluster_decision_debt_sum={complement_pair_debt_sum}",
                ],
            )
        )

    maker_submits_total = sum(float(_coerce_float(row.get("maker_submits")) or 0.0) for row in rows)
    quote_skip_total = sum(float(_coerce_float(row.get("maker_quote_quality_skip_total_count")) or 0.0) for row in rows)
    sizing_reject_total = sum(float(_coerce_float(row.get("maker_sizing_reject_total_count")) or 0.0) for row in rows)
    no_submit_total = sum(float(_coerce_float(row.get("maker_no_submit_total_count")) or 0.0) for row in rows)
    if maker_submits_total > 0 or quote_skip_total > 0 or sizing_reject_total > 0 or no_submit_total > 0:
        no_submit_category_distribution: Counter[str] = Counter()
        no_submit_cause_distribution: Counter[str] = Counter()
        sizing_reject_side_event_count = 0.0
        sizing_resolution_row_count = 0.0
        for run_id in source_runs:
            soak = soak_by_run.get(run_id) or {}
            edge_truth = soak.get("edge_truth") or {}
            sizing = soak.get("maker_sizing_competitiveness") or {}
            for key, value in (edge_truth.get("maker_no_submission_category_distribution") or {}).items():
                no_submit_category_distribution[str(key)] += int(_coerce_float(value) or 0.0)
            for key, value in (edge_truth.get("maker_no_submission_cause_distribution") or {}).items():
                no_submit_cause_distribution[str(key)] += int(_coerce_float(value) or 0.0)
            sizing_reject_side_event_count += float(_coerce_float(sizing.get("maker_sizing_reject_rows")) or 0.0)
            sizing_resolution_row_count += float(_coerce_float(sizing.get("maker_size_resolution_rows")) or 0.0)
        population_accounting = {
            "summary_level": "decision_row_and_submit_row_aggregate",
            "submit_row_count": maker_submits_total,
            "no_submit_decision_row_count": no_submit_total,
            "quote_quality_skip_decision_row_count": quote_skip_total,
            "sizing_reject_decision_row_count": sizing_reject_total,
            "sizing_reject_side_event_count": sizing_reject_side_event_count,
            "sizing_resolution_row_count": sizing_resolution_row_count,
            "note": (
                "Friction summaries blend submit-row and decision-row populations. "
                "Raw local reject event counts can exceed summarized counts because paired per-side rejects may collapse into single decision-row outcomes."
            ),
        }
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="friction_burden",
                profile_kind="wound",
                population_type="run_summary",
                mode=mode,
                depth_class=depth_class,
                basis_class="run_summary_mixed",
                horizon_class="run_summary_mixed",
                sample_count=int(round(max(maker_submits_total, 0.0))),
                source_runs=source_runs,
                cohort_dimensions={"slice": "friction_burden"},
                metrics={
                    "maker_submits_total": maker_submits_total,
                    "quote_quality_skip_total": quote_skip_total,
                    "sizing_reject_total": sizing_reject_total,
                    "no_submit_total": no_submit_total,
                    "quote_quality_skip_per_submit": _safe_ratio(quote_skip_total, maker_submits_total),
                    "sizing_reject_per_submit": _safe_ratio(sizing_reject_total, maker_submits_total),
                    "no_submit_per_submit": _safe_ratio(no_submit_total, maker_submits_total),
                    "maker_no_submission_category_distribution": _sorted_counter_dict(no_submit_category_distribution),
                    "maker_no_submission_cause_distribution": _sorted_counter_dict(no_submit_cause_distribution),
                    "sizing_reject_side_event_count": sizing_reject_side_event_count,
                    "sizing_resolution_row_count": sizing_resolution_row_count,
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=[],
                top_signals=[
                    f"maker_submits_total={maker_submits_total}",
                    f"quote_quality_skip_total={quote_skip_total}",
                    f"sizing_reject_total={sizing_reject_total}",
                ],
                population_accounting=population_accounting,
            )
        )

    viable_row_total = sum(float(_coerce_float(row.get("maker_window_viable_row_count")) or 0.0) for row in rows)
    impossible_row_total = sum(float(_coerce_float(row.get("maker_window_impossible_row_count")) or 0.0) for row in rows)
    unknown_viability_row_total = sum(
        float(_coerce_float(row.get("maker_window_unknown_viability_row_count")) or 0.0) for row in rows
    )
    viable_target_total = sum(float(_coerce_float(row.get("maker_window_viable_target_count")) or 0.0) for row in rows)
    impossible_target_total = sum(
        float(_coerce_float(row.get("maker_window_impossible_target_count")) or 0.0) for row in rows
    )
    mixed_target_total = sum(
        float(_coerce_float(row.get("maker_window_mixed_viability_target_count")) or 0.0) for row in rows
    )
    queue_depth_on_viable_targets_total = sum(
        float(_coerce_float(row.get("maker_window_queue_depth_on_viable_targets_count")) or 0.0)
        for row in rows
    )
    queue_depth_on_impossible_targets_total = sum(
        float(_coerce_float(row.get("maker_window_queue_depth_on_impossible_targets_count")) or 0.0)
        for row in rows
    )
    raw_queue_depth_near_threshold_total = sum(
        float(_coerce_float(row.get("maker_raw_queue_depth_near_threshold_event_count")) or 0.0)
        for row in rows
    )
    raw_queue_depth_hard_miss_total = sum(
        float(_coerce_float(row.get("maker_raw_queue_depth_hard_miss_event_count")) or 0.0)
        for row in rows
    )
    conflict_rows_total = sum(
        float(_coerce_float(row.get("maker_min_notional_max_shares_conflict_rows")) or 0.0)
        for row in rows
    )
    viability_floors = [
        float(value)
        for value in (
            _coerce_float(row.get("maker_window_low_price_viability_floor"))
            for row in rows
        )
        if value is not None
    ]
    low_price_band_mins = []
    low_price_band_p50s = []
    low_price_band_maxs = []
    for row in rows:
        band = row.get("maker_window_low_price_conflict_price_band")
        if not isinstance(band, dict):
            continue
        band_min = _coerce_float(band.get("min"))
        band_p50 = _coerce_float(band.get("p50"))
        band_max = _coerce_float(band.get("max"))
        if band_min is not None:
            low_price_band_mins.append(float(band_min))
        if band_p50 is not None:
            low_price_band_p50s.append(float(band_p50))
        if band_max is not None:
            low_price_band_maxs.append(float(band_max))
    viability_shadow_total = viable_row_total + impossible_row_total + unknown_viability_row_total
    if viability_shadow_total > 0 or viability_floors or conflict_rows_total > 0:
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="viability_shadow",
                profile_kind="overview",
                population_type="run_summary",
                mode=mode,
                depth_class=depth_class,
                basis_class="run_summary_mixed",
                horizon_class="run_summary_mixed",
                sample_count=int(round(viability_shadow_total)) if viability_shadow_total > 0 else len(source_runs),
                source_runs=source_runs,
                cohort_dimensions={"slice": "viability_shadow"},
                metrics={
                    "viability_floor_summary": _numeric_summary(viability_floors),
                    "viable_row_total": viable_row_total,
                    "impossible_row_total": impossible_row_total,
                    "unknown_viability_row_total": unknown_viability_row_total,
                    "impossible_row_ratio": _safe_ratio(
                        impossible_row_total,
                        viable_row_total + impossible_row_total + unknown_viability_row_total,
                    ),
                    "viable_target_total": viable_target_total,
                    "impossible_target_total": impossible_target_total,
                    "mixed_target_total": mixed_target_total,
                    "queue_depth_on_viable_targets_total": queue_depth_on_viable_targets_total,
                    "queue_depth_on_impossible_targets_total": queue_depth_on_impossible_targets_total,
                    "raw_queue_depth_near_threshold_event_count_total": raw_queue_depth_near_threshold_total,
                    "raw_queue_depth_hard_miss_event_count_total": raw_queue_depth_hard_miss_total,
                    "maker_min_notional_max_shares_conflict_rows_total": conflict_rows_total,
                    "low_price_conflict_band_min_summary": _numeric_summary(low_price_band_mins),
                    "low_price_conflict_band_p50_summary": _numeric_summary(low_price_band_p50s),
                    "low_price_conflict_band_max_summary": _numeric_summary(low_price_band_maxs),
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=[],
                top_signals=[
                    f"impossible_row_total={impossible_row_total}",
                    f"queue_depth_on_impossible_targets_total={queue_depth_on_impossible_targets_total}",
                    f"conflict_rows_total={conflict_rows_total}",
                ],
                population_accounting={
                    "summary_level": "active_window_geometry_shadow",
                    "note": (
                        "Viability shadow metrics are geometry-first active-window summaries. "
                        "Raw queue-depth near-threshold vs hard-miss counts come from quote_quality_skip events "
                        "and are not the same population as edge-evaluation no-submit assignments."
                    ),
                },
            )
        )

    valuation_status_rows = 0.0
    valuation_degraded_rows = 0.0
    valuation_hard_degraded_rows = 0.0
    held_unpriceable_started_count = 0.0
    held_unpriceable_recovered_count = 0.0
    maker_reference_fallback_activity = 0.0
    valuation_bruise_state_counter: Counter[str] = Counter()
    valuation_reason_family_counter: Counter[str] = Counter()
    for run_id in source_runs:
        soak = soak_by_run.get(run_id) or {}
        valuation_truth = soak.get("valuation_truth") or {}
        valuation_status_rows += float(_coerce_float(valuation_truth.get("status_rows")) or 0.0)
        valuation_degraded_rows += float(_coerce_float(valuation_truth.get("valuation_degraded_rows")) or 0.0)
        valuation_hard_degraded_rows += float(_coerce_float(valuation_truth.get("valuation_hard_degraded_rows")) or 0.0)
        held_unpriceable_started_count += float(_coerce_float(valuation_truth.get("held_unpriceable_started_count")) or 0.0)
        held_unpriceable_recovered_count += float(_coerce_float(valuation_truth.get("held_unpriceable_recovered_count")) or 0.0)
        valuation_bruise_state_raw = row_by_run.get(run_id, {}).get("valuation_bruise_state") or valuation_truth.get("valuation_bruise_state")
        valuation_bruise_state = str(valuation_bruise_state_raw).strip() if valuation_bruise_state_raw is not None else ""
        if valuation_bruise_state:
            valuation_bruise_state_counter[valuation_bruise_state] += 1
        maker_reference_fallback_activity += float(_coerce_float(row_by_run.get(run_id, {}).get("maker_reference_bounded_fallback_activity")) or 0.0)
        for key, value in (valuation_truth.get("valuation_degraded_reason_family_counts_run") or {}).items():
            valuation_reason_family_counter[str(key)] += int(_coerce_float(value) or 0.0)
    if not valuation_bruise_state_counter:
        valuation_bruise_state = None
    elif len(valuation_bruise_state_counter) == 1:
        valuation_bruise_state = next(iter(valuation_bruise_state_counter))
    else:
        valuation_bruise_state = "mixed_bruise_states"
    valuation_pressure_present = (
        (valuation_bruise_state not in {None, "none"})
        or valuation_degraded_rows > 0.0
        or valuation_hard_degraded_rows > 0.0
        or held_unpriceable_started_count > 0.0
        or maker_reference_fallback_activity > 0.0
    )
    if valuation_pressure_present:
        profiles.append(
            _build_profile(
                lane="maker",
                profile_family="valuation_pressure",
                profile_kind="overview",
                population_type="run_summary",
                mode=mode,
                depth_class=depth_class,
                basis_class="run_summary_mixed",
                horizon_class="run_summary_mixed",
                sample_count=int(round(valuation_status_rows)) if valuation_status_rows > 0 else len(source_runs),
                source_runs=source_runs,
                cohort_dimensions={"slice": "valuation_pressure"},
                metrics={
                    "valuation_bruise_state": valuation_bruise_state,
                    "valuation_bruise_state_distribution": _sorted_counter_dict(valuation_bruise_state_counter),
                    "valuation_degraded_rows": valuation_degraded_rows,
                    "valuation_hard_degraded_rows": valuation_hard_degraded_rows,
                    "valuation_degraded_ratio": _safe_ratio(valuation_degraded_rows, valuation_status_rows),
                    "valuation_hard_degraded_ratio": _safe_ratio(valuation_hard_degraded_rows, valuation_status_rows),
                    "held_unpriceable_started_count": held_unpriceable_started_count,
                    "held_unpriceable_recovered_count": held_unpriceable_recovered_count,
                    "maker_reference_bounded_fallback_activity": maker_reference_fallback_activity,
                    "valuation_degraded_reason_family_counts_run": _sorted_counter_dict(valuation_reason_family_counter),
                },
                heuristic_flags=[],
                suppression_flags=[],
                compatibility_flags=[],
                top_signals=[
                    f"valuation_bruise_state={valuation_bruise_state}",
                    f"valuation_degraded_rows={valuation_degraded_rows}",
                    f"held_unpriceable_started_count={held_unpriceable_started_count}",
                ],
                population_accounting={
                    "summary_level": "run_summary_and_nightly_soak_valuation_truth",
                    "status_row_count": valuation_status_rows,
                    "note": "Valuation pressure summarizes runtime status-window pressure from nightly soak valuation truth, even when the final bruise state is recovered clean.",
                },
            )
        )

    return profiles


def _build_taker_profiles(*, rows: list[dict[str, Any]], readiness: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    lane_readiness = readiness["lanes"]["taker"]
    if not lane_readiness["can_emit_profiles"]:
        return []

    source_runs = sorted(str(row.get("run_id") or "").strip() for row in rows if str(row.get("run_id") or "").strip())
    decision_total = sum(float(_coerce_float(row.get("taker_decision_count")) or 0.0) for row in rows)
    submit_total = sum(float(_coerce_float(row.get("taker_submits")) or 0.0) for row in rows)
    fill_total = sum(float(_coerce_float(row.get("taker_fills")) or 0.0) for row in rows)
    final_window_total = sum(float(_coerce_float(row.get("taker_final_window_decision_count")) or 0.0) for row in rows)
    outside_window_total = sum(float(_coerce_float(row.get("taker_outside_window_decision_count")) or 0.0) for row in rows)

    profile = _build_profile(
        lane="taker",
        profile_family="window_conversion_overview",
        profile_kind="overview",
        population_type="run_summary",
        mode=mode,
        depth_class=lane_readiness["depth_class"],
        basis_class="run_summary_mixed",
        horizon_class="run_summary_mixed",
        sample_count=int(round(decision_total)),
        source_runs=source_runs,
        cohort_dimensions={"slice": "window_conversion"},
        metrics={
            "decision_count_total": decision_total,
            "submit_count_total": submit_total,
            "fill_count_total": fill_total,
            "decision_to_submit_rate_total": _safe_ratio(submit_total, decision_total),
            "submit_to_fill_rate_total": _safe_ratio(fill_total, submit_total),
            "final_window_decision_ratio_total": _safe_ratio(final_window_total, decision_total),
            "outside_window_decision_ratio_total": _safe_ratio(outside_window_total, decision_total),
        },
        heuristic_flags=[],
        suppression_flags=["zero_decision_count_total"] if decision_total <= 0 else [],
        compatibility_flags=[],
        top_signals=[
            f"decision_count_total={decision_total}",
            f"submit_count_total={submit_total}",
            f"fill_count_total={fill_total}",
        ],
    )
    return [profile]


def _build_profiles(contract: dict[str, Any], audit: dict[str, Any], readiness: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = contract["rows"]
    deep_artifacts = _load_optional_deep_artifacts(rows)
    profiles = []
    profiles.extend(_build_maker_profiles(rows=rows, deep_artifacts=deep_artifacts, readiness=readiness, mode=mode))
    profiles.extend(_build_taker_profiles(rows=rows, readiness=readiness, mode=mode))
    profiles.sort(key=lambda item: item["profile_id"])
    return profiles


def _build_stability_matrix(profiles: list[dict[str, Any]], mode: str, readiness: dict[str, Any]) -> dict[str, Any]:
    grade_counts = Counter(profile["stability_grade"] for profile in profiles)
    lane_grade_counts: dict[str, dict[str, int]] = {}
    for lane in readiness["lanes"]:
        lane_counts = Counter(profile["stability_grade"] for profile in profiles if profile["lane"] == lane)
        lane_grade_counts[lane] = {key: int(lane_counts[key]) for key in sorted(lane_counts)}
    return {
        "tool_id": TOOL_ID,
        "tool_name": TOOL_NAME,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "mode": mode,
        "profile_count": len(profiles),
        "grade_counts": {key: int(grade_counts[key]) for key in sorted(grade_counts)},
        "lane_grade_counts": lane_grade_counts,
        "lane_depth_classes": {lane: payload["depth_class"] for lane, payload in sorted(readiness["lanes"].items())},
    }


def _build_calibration_audit(
    *,
    profiles: list[dict[str, Any]],
    readiness: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    lane_summary: dict[str, Any] = {}
    pressure_profiles: list[dict[str, Any]] = []
    for lane, payload in sorted(readiness["lanes"].items()):
        lane_profiles = [profile for profile in profiles if profile["lane"] == lane]
        grade_counts = Counter(profile["stability_grade"] for profile in lane_profiles)
        strong_blocker_counts: Counter[str] = Counter()
        bounded_blocker_counts: Counter[str] = Counter()
        near_strong_profile_ids: list[str] = []
        for profile in lane_profiles:
            readiness_surface = profile.get("promotion_readiness") or {}
            for blocker in readiness_surface.get("strong_blockers") or []:
                strong_blocker_counts[blocker] += 1
            for blocker in readiness_surface.get("bounded_blockers") or []:
                bounded_blocker_counts[blocker] += 1
            if (
                profile["stability_grade"] != "strong"
                and (readiness_surface.get("sample_gap_to_strong") or 0) <= 5
                and (readiness_surface.get("source_run_gap_to_strong") or 0) <= 1
            ):
                near_strong_profile_ids.append(profile["profile_id"])
                pressure_profiles.append(
                    {
                        "profile_id": profile["profile_id"],
                        "lane": lane,
                        "profile_family": profile["profile_family"],
                        "stability_grade": profile["stability_grade"],
                        "sample_gap_to_strong": readiness_surface.get("sample_gap_to_strong"),
                        "source_run_gap_to_strong": readiness_surface.get("source_run_gap_to_strong"),
                        "strong_blockers": readiness_surface.get("strong_blockers") or [],
                    }
                )
        lane_summary[lane] = {
            "depth_class": payload["depth_class"],
            "promotion_blockers": payload.get("promotion_blockers") or [],
            "promotion_requirements": payload.get("promotion_requirements") or [],
            "promotion_path": payload.get("promotion_path"),
            "profile_count": len(lane_profiles),
            "grade_counts": {key: int(grade_counts[key]) for key in sorted(grade_counts)},
            "strong_ready_profile_count": sum(1 for profile in lane_profiles if (profile.get("promotion_readiness") or {}).get("strong_ready")),
            "bounded_ready_profile_count": sum(1 for profile in lane_profiles if (profile.get("promotion_readiness") or {}).get("bounded_ready")),
            "strong_blocker_counts": {key: int(strong_blocker_counts[key]) for key in sorted(strong_blocker_counts)},
            "bounded_blocker_counts": {key: int(bounded_blocker_counts[key]) for key in sorted(bounded_blocker_counts)},
            "near_strong_profile_ids": sorted(near_strong_profile_ids),
        }
    pressure_profiles.sort(key=lambda item: (item["lane"], item["sample_gap_to_strong"], item["source_run_gap_to_strong"], item["profile_id"]))
    return {
        "tool_id": TOOL_ID,
        "tool_name": TOOL_NAME,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "mode": mode,
        "policy": {
            "global": STABILITY_POLICY,
            "per_lane": {lane: _stability_policy_for_lane(lane) for lane in sorted(readiness["lanes"])},
        },
        "lane_summary": lane_summary,
        "pressure_profiles": pressure_profiles,
    }


def _project_profile_under_policy(profile: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    suppression_flags = list(profile.get("suppression_flags") or [])
    compatibility_flags = list(profile.get("compatibility_flags") or [])
    heuristic_flags = list(profile.get("heuristic_flags") or [])
    grade_cap_reason_codes = list(profile.get("grade_cap_reason_codes") or [])
    semantic_clean = not any(flag.startswith("population_") for flag in suppression_flags)
    compatible = not any(flag.startswith("incompatible_") for flag in compatibility_flags)
    projected_grade = _classify_stability(
        sample_count=int(profile.get("sample_count") or 0),
        source_run_count=int(profile.get("source_run_count") or 0),
        mode=str(profile.get("mode") or "unknown"),
        semantic_clean=semantic_clean,
        compatible=compatible,
        heuristic_only=bool(heuristic_flags),
        suppression_flags=suppression_flags,
        policy=policy,
    )
    effective_grade_cap = profile.get("grade_cap")
    effective_grade_cap_reason_codes = sorted(set(grade_cap_reason_codes))
    if profile.get("depth_class") == "bounded_depth":
        if effective_grade_cap is None or effective_grade_cap == "strong":
            effective_grade_cap = "bounded"
        if "lane_depth_cap" not in effective_grade_cap_reason_codes:
            effective_grade_cap_reason_codes.append("lane_depth_cap")
    if effective_grade_cap is not None:
        projected_grade = _cap_stability_grade(projected_grade, effective_grade_cap)
    downgrade_reason_codes, suppression_reason_codes, rationale_summary = _derive_grade_reason_codes(
        stability_grade=projected_grade,
        sample_count=int(profile.get("sample_count") or 0),
        source_run_count=int(profile.get("source_run_count") or 0),
        mode=str(profile.get("mode") or "unknown"),
        depth_class=str(profile.get("depth_class") or "unknown"),
        heuristic_flags=heuristic_flags,
        suppression_flags=suppression_flags,
        compatibility_flags=compatibility_flags,
        policy=policy,
    )
    for reason_code in effective_grade_cap_reason_codes:
        if reason_code not in downgrade_reason_codes and reason_code not in suppression_reason_codes:
            downgrade_reason_codes.append(reason_code)
    promotion_readiness = _build_promotion_readiness(
        mode=str(profile.get("mode") or "unknown"),
        depth_class=str(profile.get("depth_class") or "unknown"),
        sample_count=int(profile.get("sample_count") or 0),
        source_run_count=int(profile.get("source_run_count") or 0),
        heuristic_flags=heuristic_flags,
        suppression_flags=suppression_flags,
        compatibility_flags=compatibility_flags,
        policy=policy,
        grade_cap_reason_codes=effective_grade_cap_reason_codes,
    )
    return {
        "stability_grade": projected_grade,
        "downgrade_reason_codes": sorted(set(downgrade_reason_codes)),
        "suppression_reason_codes": sorted(set(suppression_reason_codes)),
        "grade_rationale_summary": rationale_summary,
        "promotion_readiness": promotion_readiness,
        "policy": policy,
    }


def _build_threshold_pressure_matrix(
    *,
    profiles: list[dict[str, Any]],
    readiness: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    preset_projections: dict[str, dict[str, Any]] = {}
    profile_pressure: list[dict[str, Any]] = []
    robust_strong_profiles: list[str] = []
    threshold_invariant_profiles: list[dict[str, Any]] = []
    profile_grade_map: dict[str, dict[str, str]] = {}

    for preset_name, overrides in THRESHOLD_PRESSURE_PRESETS.items():
        lane_counts: dict[str, dict[str, int]] = {}
        projections_for_preset: dict[str, dict[str, Any]] = {}
        for lane in sorted(readiness["lanes"]):
            lane_profiles = [profile for profile in profiles if profile["lane"] == lane]
            lane_counter: Counter[str] = Counter()
            for profile in lane_profiles:
                projection = _project_profile_under_policy(
                    profile,
                    _stability_policy_with_overrides(profile["lane"], overrides),
                )
                projections_for_preset[profile["profile_id"]] = projection
                lane_counter[projection["stability_grade"]] += 1
            lane_counts[lane] = {key: int(lane_counter[key]) for key in sorted(lane_counter)}
        preset_projections[preset_name] = {
            "policy": {
                lane: _stability_policy_with_overrides(lane, overrides) for lane in sorted(readiness["lanes"])
            },
            "lane_grade_counts": lane_counts,
            "profile_projections": projections_for_preset,
        }

    for profile in profiles:
        profile_id = profile["profile_id"]
        grades_by_preset = {
            preset_name: preset_projections[preset_name]["profile_projections"][profile_id]["stability_grade"]
            for preset_name in THRESHOLD_PRESSURE_PRESETS
        }
        profile_grade_map[profile_id] = grades_by_preset
        unique_grades = sorted(set(grades_by_preset.values()))
        if unique_grades == ["strong"]:
            robust_strong_profiles.append(profile_id)
        current_projection = preset_projections["current"]["profile_projections"][profile_id]
        looser_projection = preset_projections["looser"]["profile_projections"][profile_id]
        tighter_projection = preset_projections["tighter"]["profile_projections"][profile_id]
        if len(unique_grades) > 1:
            pressure_item = {
                "profile_id": profile_id,
                "lane": profile["lane"],
                "profile_family": profile["profile_family"],
                "current_grade": profile["stability_grade"],
                "grades_by_preset": grades_by_preset,
                "current_strong_blockers": (profile.get("promotion_readiness") or {}).get("strong_blockers") or [],
            }
            pressure_item["tighter_strong_blockers"] = tighter_projection["promotion_readiness"]["strong_blockers"]
            pressure_item["looser_strong_blockers"] = looser_projection["promotion_readiness"]["strong_blockers"]
            profile_pressure.append(pressure_item)
        else:
            blocker_sets = {
                "current": current_projection["promotion_readiness"]["strong_blockers"],
                "looser": looser_projection["promotion_readiness"]["strong_blockers"],
                "tighter": tighter_projection["promotion_readiness"]["strong_blockers"],
            }
            all_blockers = [
                tuple(blocker_sets["current"]),
                tuple(blocker_sets["looser"]),
                tuple(blocker_sets["tighter"]),
            ]
            threshold_invariant_profiles.append(
                {
                    "profile_id": profile_id,
                    "lane": profile["lane"],
                    "profile_family": profile["profile_family"],
                    "stable_grade": unique_grades[0],
                    "structural_blocker_invariant": len(set(all_blockers)) == 1 and bool(blocker_sets["current"]),
                    "strong_blockers_by_preset": blocker_sets,
                }
            )

    profile_pressure.sort(key=lambda item: (item["lane"], item["profile_family"], item["profile_id"]))
    robust_strong_profiles = sorted(robust_strong_profiles)
    threshold_invariant_profiles.sort(key=lambda item: (item["lane"], item["profile_family"], item["profile_id"]))
    return {
        "tool_id": TOOL_ID,
        "tool_name": TOOL_NAME,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "mode": mode,
        "preset_projections": preset_projections,
        "pressure_sensitive_profiles": profile_pressure,
        "robust_strong_profiles": robust_strong_profiles,
        "threshold_invariant_profiles": threshold_invariant_profiles,
        "threshold_sensitive_profile_count": len(profile_pressure),
        "threshold_invariant_profile_count": len(threshold_invariant_profiles),
        "structural_blocker_invariant_profile_count": sum(
            1 for item in threshold_invariant_profiles if item["structural_blocker_invariant"]
        ),
        "profile_grade_map": profile_grade_map,
    }


def _build_candidate_blanks(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blanks: list[dict[str, Any]] = []
    for profile in profiles:
        if profile["lane"] != "maker":
            continue
        if profile["stability_grade"] not in {"strong", "bounded"}:
            continue
        family = profile["profile_family"]
        metrics = profile["metrics"]
        focus = None
        if family == "multifill_wound" and (_coerce_float(metrics.get("multifill_incorrect_ratio")) or 0.0) >= 0.6:
            focus = "bounded experimentation around multi-fill maker geometry and completion debt"
        elif family == "repeat_target_cluster" and (_coerce_float(metrics.get("repeat_cluster_count_total")) or 0.0) > 0.0:
            focus = "inspect repeated target engagement clusters before tuning aggression"
        elif family == "complement_pair_cluster" and (_coerce_float(metrics.get("complement_pair_cluster_count_total")) or 0.0) > 0.0:
            focus = "inspect mirrored complement-pair wounds before changing maker doctrine"
        elif family == "friction_burden" and (_coerce_float(metrics.get("quote_quality_skip_per_submit")) or 0.0) > 0.1:
            focus = "reduce maker pre-fire friction before touching live behavior"
        elif family == "singlefill_strength" and (_coerce_float(metrics.get("singlefill_correct_ratio")) or 0.0) >= 0.6:
            focus = "preserve and isolate stable single-fill maker geometry"
        elif family == "outcome_balance" and (_coerce_float(metrics.get("complete_bad_ratio")) or 0.0) >= 0.6:
            focus = "separate bad completed fights from incomplete-lifecycle noise"
        elif family == "valuation_pressure" and (
            str(metrics.get("valuation_bruise_state") or "") != "none"
            or (_coerce_float(metrics.get("valuation_degraded_ratio")) or 0.0) > 0.0
            or (_coerce_float(metrics.get("held_unpriceable_started_count")) or 0.0) > 0.0
        ):
            focus = "inspect recovered-clean valuation pressure before assuming the lane was calm"
        if focus is None:
            continue
        blanks.append(
            {
                "blank_id": f"blank|{profile['profile_id']}",
                "lane": profile["lane"],
                "profile_id": profile["profile_id"],
                "profile_family": family,
                "profile_kind": profile["profile_kind"],
                "depth_class": profile["depth_class"],
                "stability_grade": profile["stability_grade"],
                "downgrade_reason_codes": profile.get("downgrade_reason_codes") or [],
                "suppression_reason_codes": profile.get("suppression_reason_codes") or [],
                "focus": focus,
                "evidence_summary": _compact_blank_evidence(profile),
                "safety_note": "candidate blank only; no live threshold, sizing, or policy instruction",
            }
        )
    blanks.sort(key=lambda item: item["blank_id"])
    return blanks


def _flatten_headline_metric(profile: dict[str, Any]) -> tuple[str | None, Any]:
    metrics = profile.get("metrics") or {}
    for key in (
        "complete_bad_ratio",
        "multifill_incorrect_ratio",
        "singlefill_correct_ratio",
        "valuation_degraded_ratio",
        "execution_rescue_overcome_rate",
        "complement_pair_cluster_count_total",
        "repeat_cluster_count_total",
        "decision_to_submit_rate_total",
        "quote_quality_skip_per_submit",
    ):
        if key in metrics:
            return key, metrics[key]
    if metrics:
        first_key = sorted(metrics)[0]
        return first_key, metrics[first_key]
    return None, None


def _compact_blank_evidence(profile: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(profile.get("metrics") or {})
    family = profile.get("profile_family")
    if family == "complement_pair_cluster":
        examples = metrics.pop("complement_pair_examples_by_run", {}) or {}
        sample_examples: list[dict[str, Any]] = []
        run_ids = sorted(examples)
        for run_id in run_ids:
            for example in examples[run_id][:1]:
                sample_examples.append(
                    {
                        "run_id": run_id,
                        "order_submit_id_a": example.get("order_submit_id_a"),
                        "order_submit_id_b": example.get("order_submit_id_b"),
                        "combined_decision_debt_sum": example.get("combined_decision_debt_sum"),
                        "pair_score": example.get("pair_score"),
                    }
                )
            if len(sample_examples) >= 3:
                break
        metrics["complement_pair_example_run_count"] = len(run_ids)
        metrics["sample_complement_pair_examples"] = sample_examples[:3]
    if family == "repeat_target_cluster":
        summary = metrics.pop("repeat_cluster_summary_by_run", {}) or {}
        normalized_runs: list[dict[str, Any]] = []
        for run_id, payload in summary.items():
            if isinstance(payload, list):
                entries = [entry for entry in payload if isinstance(entry, dict)]
                submit_total = sum(float(_coerce_float(entry.get("submit_count")) or 0.0) for entry in entries)
                normalized_runs.append(
                    {
                        "run_id": run_id,
                        "target_count": len(entries),
                        "submit_count_total": submit_total,
                        "sample_target_refs": [entry.get("target_ref") for entry in entries[:3]],
                    }
                )
            elif isinstance(payload, dict):
                entries = [entry for entry in payload.values() if isinstance(entry, dict)]
                submit_total = sum(float(_coerce_float(entry.get("submit_count")) or 0.0) for entry in entries)
                normalized_runs.append(
                    {
                        "run_id": run_id,
                        "target_count": len(entries),
                        "submit_count_total": submit_total,
                        "sample_target_refs": [],
                    }
                )
        metrics["repeat_cluster_top_runs"] = sorted(
            normalized_runs,
            key=lambda item: (item["submit_count_total"], item["target_count"], item["run_id"]),
            reverse=True,
        )[:5]
    return metrics


def _metric_delta_change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    before_metric, before_value = _flatten_headline_metric(before)
    after_metric, after_value = _flatten_headline_metric(after)
    if before_metric is None or after_metric is None or before_metric != after_metric:
        return None
    before_numeric = _coerce_float(before_value)
    after_numeric = _coerce_float(after_value)
    if before_numeric is None or after_numeric is None or before_numeric == after_numeric:
        return None
    absolute_delta = after_numeric - before_numeric
    relative_delta = None if before_numeric == 0 else absolute_delta / abs(before_numeric)
    threshold = _metric_drift_threshold(before_metric)
    significant = abs(absolute_delta) >= threshold
    return {
        "profile_id": before["profile_id"],
        "metric": before_metric,
        "before": before_numeric,
        "after": after_numeric,
        "absolute_delta": absolute_delta,
        "relative_delta": relative_delta,
        "threshold": threshold,
        "significant": significant,
        "stability_grade_before": before.get("stability_grade"),
        "stability_grade_after": after.get("stability_grade"),
    }


def _write_profile_csv(path: pathlib.Path, profiles: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for profile in profiles:
            headline_key, headline_value = _flatten_headline_metric(profile)
            writer.writerow(
                {
                    "profile_id": profile["profile_id"],
                    "lane": profile["lane"],
                    "profile_family": profile["profile_family"],
                    "profile_kind": profile["profile_kind"],
                    "population_type": profile["population_type"],
                    "mode": profile["mode"],
                    "depth_class": profile["depth_class"],
                    "stability_grade": profile["stability_grade"],
                    "sample_count": profile["sample_count"],
                    "source_run_count": profile["source_run_count"],
                    "basis_class": profile["basis_class"],
                    "horizon_class": profile["horizon_class"],
                    "heuristic_flag_count": len(profile.get("heuristic_flags") or []),
                    "suppression_flag_count": len(profile.get("suppression_flags") or []),
                    "headline_metric": headline_key,
                    "headline_value": json.dumps(headline_value, sort_keys=True) if isinstance(headline_value, (dict, list)) else headline_value,
                }
            )


def _build_profile_cards(
    *,
    audit: dict[str, Any],
    readiness: dict[str, Any],
    profiles: list[dict[str, Any]],
    candidate_blanks: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {TOOL_NAME}",
        "",
        "## Contract",
        f"- Bundle origin: `{audit['bundle_origin']}`",
        f"- Manifest status: `{audit['manifest_status']}`",
        f"- Snapshot contract: `{audit['snapshot_contract_status']}`",
        f"- Contract health: `{audit['contract_health']}`",
        f"- Warning count: `{audit['warning_count']}`",
        f"- Requested mode: `{audit['requested_mode']}`",
        f"- Contract OK: `{audit['ok']}`",
        "",
        "## Lane Readiness",
    ]
    for lane, payload in sorted(readiness["lanes"].items()):
        coverage_suffix = ""
        if payload.get("deep_coverage_ratio") is not None:
            coverage_suffix = f" deep_coverage_ratio=`{payload['deep_coverage_ratio']}`"
        blocker_suffix = ""
        if payload.get("promotion_blockers"):
            blocker_suffix = f" blockers=`{', '.join(payload['promotion_blockers'])}`"
        lines.append(
            f"- `{lane}`: depth=`{payload['depth_class']}` can_emit_profiles=`{payload['can_emit_profiles']}`{coverage_suffix} reasons=`{', '.join(payload['reason_codes'])}`{blocker_suffix}"
        )
    lines.extend(["", "## Candidate Blanks"])
    if candidate_blanks:
        for blank in candidate_blanks:
            lines.append(
                f"- `{blank['profile_family']}` (`{blank['stability_grade']}`): {blank['focus']}"
            )
    else:
        lines.append("- No candidate blanks met the stability and signal gates in this slice.")
    lines.extend(["", "## Profile Catalog"])
    for profile in profiles:
        headline_key, headline_value = _flatten_headline_metric(profile)
        lines.append(
            f"- `{profile['profile_id']}`"
            f" lane=`{profile['lane']}` grade=`{profile['stability_grade']}`"
            f" sample_count=`{profile['sample_count']}`"
            f" headline=`{headline_key}={headline_value}`"
            f" signal=`{profile.get('signal_posture') or 'unspecified'}`"
            f" reasons=`{', '.join(profile.get('downgrade_reason_codes') or profile.get('suppression_reason_codes') or ['none'])}`"
            f" strong_blockers=`{', '.join((profile.get('promotion_readiness') or {}).get('strong_blockers') or ['none'])}`"
        )
    return "\n".join(lines) + "\n"


def _build_profile_diff(
    *,
    baseline_dir: pathlib.Path,
    current_profiles: list[dict[str, Any]],
    current_blanks: list[dict[str, Any]],
    current_audit: dict[str, Any],
) -> dict[str, Any]:
    baseline_profiles_path = baseline_dir / "fusion_core_profile_catalog.json"
    baseline_blanks_path = baseline_dir / "fusion_core_candidate_blanks.json"
    baseline_audit_path = baseline_dir / "fusion_core_input_contract_audit.json"
    baseline_profiles = json.loads(baseline_profiles_path.read_text(encoding="utf-8")) if baseline_profiles_path.exists() else []
    baseline_blanks = json.loads(baseline_blanks_path.read_text(encoding="utf-8")) if baseline_blanks_path.exists() else []
    baseline_audit = json.loads(baseline_audit_path.read_text(encoding="utf-8")) if baseline_audit_path.exists() else {}

    baseline_by_id = {profile["profile_id"]: profile for profile in baseline_profiles}
    current_by_id = {profile["profile_id"]: profile for profile in current_profiles}
    added_profiles = sorted(profile_id for profile_id in current_by_id if profile_id not in baseline_by_id)
    removed_profiles = sorted(profile_id for profile_id in baseline_by_id if profile_id not in current_by_id)

    baseline_mode = baseline_audit.get("resolved_mode") or "unknown"
    current_mode = current_audit.get("resolved_mode") or "unknown"
    comparison_class = f"{baseline_mode}_vs_{current_mode}"

    grade_changes = []
    expected_mode_cap_downgrades = []
    regression_candidate_changes = []
    metric_value_changes = []
    metric_drift_candidates = []
    for profile_id in sorted(set(baseline_by_id) & set(current_by_id)):
        before = baseline_by_id[profile_id]
        after = current_by_id[profile_id]
        metric_change = _metric_delta_change(before, after)
        if metric_change is not None:
            metric_value_changes.append(metric_change)
            if (
                before.get("stability_grade") == after.get("stability_grade")
                and metric_change["significant"]
            ):
                metric_drift_candidates.append(metric_change)
        if before.get("stability_grade") != after.get("stability_grade"):
            change = {
                "profile_id": profile_id,
                "before": before.get("stability_grade"),
                "after": after.get("stability_grade"),
                "after_downgrade_reason_codes": after.get("downgrade_reason_codes") or [],
                "after_suppression_reason_codes": after.get("suppression_reason_codes") or [],
            }
            grade_changes.append(change)
            expected_by_mode_cap = (
                baseline_mode == "corpus"
                and current_mode == "specimen"
                and (
                    "mode_cap_specimen_only" in change["after_downgrade_reason_codes"]
                    or "sample_count_below_bounded_floor" in change["after_downgrade_reason_codes"]
                    or "zero_eligible_records" in change["after_suppression_reason_codes"]
                )
            )
            if expected_by_mode_cap:
                expected_mode_cap_downgrades.append(change)
            else:
                regression_candidate_changes.append(change)

    blank_ids_before = sorted(blank["blank_id"] for blank in baseline_blanks)
    blank_ids_after = sorted(blank["blank_id"] for blank in current_blanks)
    return {
        "tool_id": TOOL_ID,
        "baseline_dir": str(baseline_dir),
        "baseline_mode": baseline_mode,
        "current_mode": current_mode,
        "comparison_class": comparison_class,
        "added_profile_ids": added_profiles,
        "removed_profile_ids": removed_profiles,
        "grade_changes": grade_changes,
        "expected_mode_cap_downgrades": expected_mode_cap_downgrades,
        "regression_candidate_changes": regression_candidate_changes,
        "metric_value_changes": metric_value_changes,
        "metric_drift_candidates": metric_drift_candidates,
        "added_blank_ids": sorted(blank_id for blank_id in blank_ids_after if blank_id not in set(blank_ids_before)),
        "removed_blank_ids": sorted(blank_id for blank_id in blank_ids_before if blank_id not in set(blank_ids_after)),
    }


def build_profiles(
    *,
    bundle_dir: pathlib.Path | None = None,
    run_index_path: pathlib.Path | None = None,
    anomaly_summary_path: pathlib.Path | None = None,
    metric_catalog_path: pathlib.Path | None = None,
    out_dir: pathlib.Path = DEFAULT_OUT_DIR,
    mode: str = "auto",
    diff_baseline_dir: pathlib.Path | None = None,
) -> dict[str, pathlib.Path]:
    contract = _load_contract(
        bundle_dir=bundle_dir,
        run_index_path=run_index_path,
        anomaly_summary_path=anomaly_summary_path,
        metric_catalog_path=metric_catalog_path,
    )
    resolved_mode = _determine_mode(mode, contract["rows"])
    deep_artifacts = _load_optional_deep_artifacts(contract["rows"])
    audit = _audit_input_contract(contract, mode, deep_artifacts)
    audit["resolved_mode"] = resolved_mode
    readiness = _build_lane_readiness(rows=contract["rows"], deep_artifacts=deep_artifacts, mode=resolved_mode)
    profiles = _build_profiles(contract, audit, readiness, resolved_mode)
    candidate_blanks = _build_candidate_blanks(profiles)
    stability_matrix = _build_stability_matrix(profiles, resolved_mode, readiness)
    calibration_audit = _build_calibration_audit(profiles=profiles, readiness=readiness, mode=resolved_mode)
    threshold_pressure_matrix = _build_threshold_pressure_matrix(profiles=profiles, readiness=readiness, mode=resolved_mode)

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    contract_path = out_dir / "fusion_core_input_contract_audit.json"
    readiness_path = out_dir / "fusion_core_lane_readiness.json"
    profile_catalog_path = out_dir / "fusion_core_profile_catalog.json"
    profile_cards_path = out_dir / "fusion_core_profile_cards.md"
    stability_path = out_dir / "fusion_core_stability_matrix.json"
    calibration_path = out_dir / "fusion_core_calibration_audit.json"
    threshold_pressure_path = out_dir / "fusion_core_threshold_pressure_matrix.json"
    blanks_path = out_dir / "fusion_core_candidate_blanks.json"
    comparison_path = out_dir / "fusion_core_cohort_comparison.csv"

    _write_json(contract_path, audit)
    _write_json(readiness_path, readiness)
    _write_json(profile_catalog_path, profiles)
    profile_cards_path.write_text(
        _build_profile_cards(audit=audit, readiness=readiness, profiles=profiles, candidate_blanks=candidate_blanks),
        encoding="utf-8",
    )
    _write_json(stability_path, stability_matrix)
    _write_json(calibration_path, calibration_audit)
    _write_json(threshold_pressure_path, threshold_pressure_matrix)
    _write_json(blanks_path, candidate_blanks)
    _write_profile_csv(comparison_path, profiles)

    outputs = {
        "fusion_core_input_contract_audit_json": contract_path,
        "fusion_core_lane_readiness_json": readiness_path,
        "fusion_core_profile_catalog_json": profile_catalog_path,
        "fusion_core_profile_cards_md": profile_cards_path,
        "fusion_core_stability_matrix_json": stability_path,
        "fusion_core_calibration_audit_json": calibration_path,
        "fusion_core_threshold_pressure_matrix_json": threshold_pressure_path,
        "fusion_core_candidate_blanks_json": blanks_path,
        "fusion_core_cohort_comparison_csv": comparison_path,
    }

    if diff_baseline_dir is not None:
        diff_payload = _build_profile_diff(
            baseline_dir=diff_baseline_dir.resolve(),
            current_profiles=profiles,
            current_blanks=candidate_blanks,
            current_audit=audit,
        )
        diff_path = out_dir / "fusion_core_profile_diff.json"
        _write_json(diff_path, diff_payload)
        outputs["fusion_core_profile_diff_json"] = diff_path

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shape FMA bundles into deterministic profiling outputs without touching runtime behavior.")
    parser.add_argument("--bundle-dir", type=pathlib.Path, default=None, help="FMA bundle directory containing required exported artifacts.")
    parser.add_argument("--run-index", type=pathlib.Path, default=None, help="Explicit run_index.jsonl path when not using --bundle-dir.")
    parser.add_argument("--anomaly-summary", type=pathlib.Path, default=None, help="Explicit anomaly_summary.json path when not using --bundle-dir.")
    parser.add_argument("--metric-catalog", type=pathlib.Path, default=None, help="Explicit metric_catalog.json path when not using --bundle-dir.")
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT_DIR, help="Output directory for FM-2A1 artifacts.")
    parser.add_argument("--mode", choices=["auto", "specimen", "corpus"], default="auto", help="Profile scope mode.")
    parser.add_argument("--diff-baseline-dir", type=pathlib.Path, default=None, help="Optional previous FM-2A1 output directory for deterministic diffing.")
    parser.add_argument("--explain-profile-id", default=None, help="Optional profile_id or blank_id to print after output generation.")
    return parser.parse_args()


def _print_explainer(out_dir: pathlib.Path, identifier: str) -> None:
    profile_catalog = json.loads((out_dir / "fusion_core_profile_catalog.json").read_text(encoding="utf-8"))
    candidate_blanks = json.loads((out_dir / "fusion_core_candidate_blanks.json").read_text(encoding="utf-8"))
    for profile in profile_catalog:
        if profile.get("profile_id") == identifier:
            print(json.dumps(profile["explainability"], indent=2, sort_keys=True))
            return
    for blank in candidate_blanks:
        if blank.get("blank_id") == identifier:
            print(json.dumps(blank, indent=2, sort_keys=True))
            return
    raise SystemExit(f"unknown profile or blank id: {identifier}")


def main() -> int:
    args = parse_args()
    outputs = build_profiles(
        bundle_dir=args.bundle_dir,
        run_index_path=args.run_index,
        anomaly_summary_path=args.anomaly_summary,
        metric_catalog_path=args.metric_catalog,
        out_dir=args.out_dir,
        mode=args.mode,
        diff_baseline_dir=args.diff_baseline_dir,
    )
    print(json.dumps({key: str(value) for key, value in outputs.items()}, indent=2, sort_keys=True))
    if args.explain_profile_id:
        _print_explainer(args.out_dir.resolve(), args.explain_profile_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
