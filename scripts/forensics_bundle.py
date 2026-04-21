#!/usr/bin/env python3
"""Build one-command incident forensics bundle for a run."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tarfile
from typing import Any, Dict, List

from prodesk.artifact_identity import build_artifact_identity
from prodesk.error_codes import summarize_error_codes
from prodesk.run_contract import (
    apply_contract_bounds,
    resolve_run_contract,
    run_contract_slice_path,
)
from prodesk.session_phase import enforce_validation_phase
from scripts.forensic_snapshot import run_snapshot as run_forensic_snapshot
from scripts.ops_snapshot import run_snapshot as run_ops_snapshot


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_for_run(log_dir: pathlib.Path, run_id: str) -> pathlib.Path:
    return log_dir / f"run_manifest_{run_id}.json"


def _read_tail_filtered(
    paths: List[pathlib.Path],
    *,
    tail_lines: int,
    run_id: str,
    run_contract: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    target = str(run_id or "").strip()
    for path in paths:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if run_contract is None:
            lines = lines[-max(0, int(tail_lines)) :]
        for text in lines:
            text = text.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if target and str(row.get("run_id") or "").strip() != target:
                continue
            rows.append(row)
    return apply_contract_bounds(rows, run_contract) if isinstance(run_contract, dict) else rows


def _contract_stream_paths(
    *,
    contract: Dict[str, Any] | None,
    stream: str,
) -> List[pathlib.Path]:
    if not isinstance(contract, dict):
        return []
    slice_path = run_contract_slice_path(contract, stream=stream)
    if slice_path is not None:
        return [slice_path]
    key = f"{stream}_path"
    raw = str(contract.get(key) or "").strip()
    if not raw:
        return []
    path = pathlib.Path(raw).expanduser().resolve()
    return [path] if path.exists() else []


def run_bundle(
    *,
    log_dir: pathlib.Path,
    config_path: pathlib.Path,
    run_id: str,
    out_dir: pathlib.Path,
    status_tail_lines: int = 800,
    event_tail_lines: int = 800,
    error_tail_lines: int = 400,
    run_contract_path: pathlib.Path | None = None,
    session_phase: str = "validate_postrun",
) -> Dict[str, Any]:
    findings: List[str] = []
    resolved_log_dir = log_dir.resolve()
    resolved_config = config_path.resolve()
    resolved_out = out_dir.resolve()
    resolved_out.mkdir(parents=True, exist_ok=True)
    normalized_phase = enforce_validation_phase(
        validation_name="forensics_bundle",
        session_phase=session_phase,
    )

    chosen_run_id = str(run_id or "").strip()
    if not chosen_run_id:
        findings.append("run_id_required")
    run_contract = resolve_run_contract(
        log_dir=resolved_log_dir,
        run_id=(chosen_run_id or None),
        run_contract_path_override=run_contract_path,
        allow_open=(normalized_phase == "validate_active"),
    )

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = chosen_run_id or "unknown_run"
    bundle_root = resolved_out / f"incident_bundle_{tag}_{ts}"
    bundle_root.mkdir(parents=True, exist_ok=True)

    status_paths = _contract_stream_paths(contract=run_contract, stream="status")
    if not status_paths:
        status_paths = sorted(resolved_log_dir.glob("status_*.jsonl"))[-2:]
    event_paths = _contract_stream_paths(contract=run_contract, stream="events")
    if not event_paths:
        event_paths = sorted(resolved_log_dir.glob("events_*.jsonl"))[-2:]
    error_paths = _contract_stream_paths(contract=run_contract, stream="errors")
    if not error_paths:
        error_paths = sorted(resolved_log_dir.glob("errors_*.jsonl"))[-2:]

    status_rows = _read_tail_filtered(
        status_paths,
        tail_lines=int(status_tail_lines),
        run_id=chosen_run_id,
        run_contract=run_contract,
    )
    event_rows = _read_tail_filtered(
        event_paths,
        tail_lines=int(event_tail_lines),
        run_id=chosen_run_id,
        run_contract=run_contract,
    )
    error_rows = _read_tail_filtered(
        error_paths,
        tail_lines=int(error_tail_lines),
        run_id=chosen_run_id,
        run_contract=run_contract,
    )

    if not status_rows:
        findings.append("status_rows_missing")

    forensic = (
        run_forensic_snapshot(
            resolved_log_dir,
            chosen_run_id,
            run_contract_path=run_contract_path,
            session_phase=normalized_phase,
        )
        if chosen_run_id
        else {}
    )
    ops = run_ops_snapshot(
        log_dir=resolved_log_dir,
        run_id=chosen_run_id,
        compose_project_name="",
        min_status_rows=1,
        max_status_age_sec=3153600000.0,
        run_contract_path=run_contract_path,
        session_phase=normalized_phase,
    )

    cfg_fingerprint = ""
    if resolved_config.exists():
        cfg_fingerprint = _sha256_file(resolved_config)
    else:
        findings.append(f"config_missing:{resolved_config}")

    manifest = _manifest_for_run(resolved_log_dir, chosen_run_id) if chosen_run_id else None
    if manifest is not None and manifest.exists():
        (bundle_root / manifest.name).write_text(manifest.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    else:
        findings.append("run_manifest_missing")

    (bundle_root / "status_tail.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in status_rows) + ("\n" if status_rows else ""),
        encoding="utf-8",
    )
    (bundle_root / "events_tail.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in event_rows) + ("\n" if event_rows else ""),
        encoding="utf-8",
    )
    (bundle_root / "errors_tail.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in error_rows) + ("\n" if error_rows else ""),
        encoding="utf-8",
    )

    summary = {
        "ts_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": chosen_run_id,
        "session_phase": normalized_phase,
        "run_contract_path": str(run_contract.get("_path", "")) if isinstance(run_contract, dict) else "",
        "log_dir": str(resolved_log_dir),
        "config_path": str(resolved_config),
        "config_fingerprint_sha256": cfg_fingerprint,
        "artifact_identity": build_artifact_identity(log_dir=resolved_log_dir, run_id=chosen_run_id or None),
        "status_row_count": len(status_rows),
        "event_row_count": len(event_rows),
        "error_row_count": len(error_rows),
        "forensic_snapshot": forensic,
        "ops_snapshot": ops,
        "findings": findings,
        "error_codes": summarize_error_codes(findings),
        "ok": len(findings) == 0,
    }
    (bundle_root / "incident_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tar_path = resolved_out / f"{bundle_root.name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(bundle_root, arcname=bundle_root.name)

    return {
        "bundle_dir": str(bundle_root),
        "bundle_tar_gz": str(tar_path),
        "run_id": chosen_run_id,
        "finding_count": len(findings),
        "findings": findings,
        "ok": len(findings) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro one-command incident forensics bundle")
    parser.add_argument("--log-dir", default="./logs_exec/paper_universal", help="Execution log directory")
    parser.add_argument("--config", default="execution_config.yaml", help="Config path for fingerprint")
    parser.add_argument("--run-id", required=True, help="Explicit run_id")
    parser.add_argument("--run-contract", default="", help="Optional run contract JSON path for deterministic replay")
    parser.add_argument(
        "--session-phase",
        default="validate_postrun",
        help="Declared lifecycle phase (validate_active|validate_postrun)",
    )
    parser.add_argument("--out-dir", default="./exports", help="Output directory for bundle artifacts")
    parser.add_argument("--status-tail-lines", type=int, default=800, help="Status tail lines to include")
    parser.add_argument("--event-tail-lines", type=int, default=800, help="Event tail lines to include")
    parser.add_argument("--error-tail-lines", type=int, default=400, help="Error tail lines to include")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    result = run_bundle(
        log_dir=pathlib.Path(args.log_dir),
        config_path=pathlib.Path(args.config),
        run_id=str(args.run_id),
        out_dir=pathlib.Path(args.out_dir),
        status_tail_lines=max(20, int(args.status_tail_lines)),
        event_tail_lines=max(20, int(args.event_tail_lines)),
        error_tail_lines=max(20, int(args.error_tail_lines)),
        run_contract_path=(pathlib.Path(args.run_contract) if str(args.run_contract).strip() else None),
        session_phase=str(args.session_phase),
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
