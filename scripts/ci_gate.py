#!/usr/bin/env python3
"""Local/CI hard gate for Bro."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import datetime as dt
import yaml


def run_step(name: str, cmd: list[str]) -> None:
    print(f"[ci_gate] step={name} cmd={' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _fixture_manifest_payload(
    *,
    run_id: str,
    profile_name: str,
    status_path: pathlib.Path,
    events_path: pathlib.Path,
) -> dict:
    return {
        "run_id": str(run_id),
        "manifest_schema_version": 2,
        "profile_name": str(profile_name),
        "git_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "config_fingerprint_sha256": "a" * 64,
        "status_path": str(status_path.resolve()),
        "events_path": str(events_path.resolve()),
        "start_ts": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bro CI gate")
    parser.add_argument("--config", default="execution_config.yaml", help="Execution config for security/readiness checks")
    parser.add_argument("--readiness-log-dir", default="", help="Optional log dir for readiness gate")
    parser.add_argument("--readiness-run-id", default="", help="Run_id to use with --readiness-log-dir")
    parser.add_argument("--policy", default="ops/ramp_policy.yaml", help="Readiness policy path")
    parser.add_argument("--skip-pip-audit", action="store_true", help="Skip optional pip-audit check")
    args = parser.parse_args()

    py = sys.executable
    log_dir = str(args.readiness_log_dir).strip()

    print(f"[ci_gate] step=editable_install cmd={py} -m pip install -e .")
    editable = subprocess.run([py, "-m", "pip", "install", "-e", "."], check=False)
    if editable.returncode != 0:
        run_step(
            "editable_install_user",
            [py, "-m", "pip", "install", "--break-system-packages", "--user", "-e", "."],
        )
    run_step("compileall", [py, "-m", "compileall", "executor.py", "observer.py", "simulator.py", "prodesk", "scripts", "tests"])
    run_step("dependency_repro_audit", [py, "scripts/dependency_repro_audit.py", "--lock-manifest", "ops/dependency_lock.json"])
    run_step(
        "config_consistency_audit",
        [py, "scripts/config_consistency_audit.py", "--primary", "execution_config.yaml", "--secondary", "config.yaml"],
    )
    run_step("prestart_gate", [py, "scripts/prestart_gate.py", "--config", args.config, "--allow-kill-switch", "--allow-guard-file"])
    run_step("runtime_hardening_audit", [py, "scripts/runtime_hardening_audit.py"])
    run_step("websocket_hardening_audit", [py, "scripts/websocket_hardening_audit.py", "--config", args.config])
    run_step("alert_profile_audit", [py, "scripts/alert_profile_audit.py", "--config", args.config])
    run_step("profile_matrix_audit", [py, "scripts/profile_matrix_audit.py"])
    run_step("doctrine_truth_audit", [py, "scripts/doctrine_truth_audit.py"])
    run_step("guardian_profile_audit", [py, "scripts/guardian_profile_audit.py", "--compose", "docker-compose.yml"])
    run_step("sim_harness_audit", [py, "scripts/sim_harness_audit.py", "--config", args.config])
    run_step("pytest", [py, "-m", "pytest", "-q"])
    run_step("security_audit", [py, "scripts/security_audit.py", "--config", args.config, "--mode", "paper"])
    with tempfile.TemporaryDirectory() as td_backup:
        backup_root = pathlib.Path(td_backup)
        backup_log_dir = backup_root / "logs"
        backup_log_dir.mkdir(parents=True, exist_ok=True)
        backup_status_path = backup_log_dir / "status_2026-01-01.jsonl"
        backup_events_path = backup_log_dir / "events_2026-01-01.jsonl"
        backup_status_path.write_text('{"ok":1}\n', encoding="utf-8")
        backup_events_path.write_text("", encoding="utf-8")
        (backup_log_dir / "run_manifest_demo.json").write_text(
            json.dumps(
                _fixture_manifest_payload(
                    run_id="demo",
                    profile_name="ci-backup",
                    status_path=backup_status_path,
                    events_path=backup_events_path,
                )
            ),
            encoding="utf-8",
        )
        state_path = backup_root / "state.json"
        state_path.write_text('{"state":"ok"}\n', encoding="utf-8")
        backup_dir = backup_root / "out"
        run_step(
            "backup_daily",
            [
                py,
                "scripts/backup_daily.py",
                "--log-dir",
                str(backup_log_dir),
                "--state-path",
                str(state_path),
                "--out-dir",
                str(backup_dir),
                "--require-files-min",
                "2",
            ],
        )
        run_step(
            "rollback_drill",
            [
                py,
                "scripts/rollback_drill.py",
                "--backup-dir",
                str(backup_dir),
                "--require-state",
                "--require-manifest",
            ],
        )
    with tempfile.TemporaryDirectory() as td_run:
        run_root = pathlib.Path(td_run)
        run_log_dir = run_root / "logs"
        run_log_dir.mkdir(parents=True, exist_ok=True)
        run_id = "ci-run-1"
        run_status_path = run_log_dir / "status_2099-01-01.jsonl"
        run_events_path = run_log_dir / "events_2099-01-01.jsonl"
        status_rows = [
            '{"ts_utc":"2099-01-01T00:00:00.000Z","run_id":"ci-run-1","gauge.open_orders":1,"book_feed":{"connected":true,"reconnects":0,"last_msg_age_sec":0.0},"chainlink":{"connected":true,"reconnects":0,"last_tick_age_sec":0.0,"queue_size":0,"dropped_ticks":0}}',
            '{"ts_utc":"2099-01-01T00:00:01.000Z","run_id":"ci-run-1","gauge.open_orders":1,"book_feed":{"connected":true,"reconnects":0,"last_msg_age_sec":0.0},"chainlink":{"connected":true,"reconnects":0,"last_tick_age_sec":0.0,"queue_size":0,"dropped_ticks":0}}',
        ]
        run_status_path.write_text("\n".join(status_rows) + "\n", encoding="utf-8")
        run_events_path.write_text(
            '{"ts_utc":"2099-01-01T00:00:00.500Z","run_id":"ci-run-1","event_type":"cycle"}\n',
            encoding="utf-8",
        )
        (run_log_dir / f"run_manifest_{run_id}.json").write_text(
            json.dumps(
                _fixture_manifest_payload(
                    run_id=run_id,
                    profile_name="ci-run",
                    status_path=run_status_path,
                    events_path=run_events_path,
                )
            ),
            encoding="utf-8",
        )
        run_step(
            "run_integrity_audit",
            [
                py,
                "scripts/run_integrity_audit.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--min-status-rows",
                "2",
                "--max-status-age-sec",
                "3153600000",
            ],
        )
        run_step(
            "websocket_hardening_audit_run_evidence",
            [
                py,
                "scripts/websocket_hardening_audit.py",
                "--config",
                args.config,
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
            ],
        )
        run_step(
            "websocket_reliability_gate",
            [
                py,
                "scripts/websocket_reliability_gate.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--min-status-rows",
                "1",
                "--max-book-feed-down-ratio",
                "1.0",
                "--max-chainlink-down-ratio",
                "1.0",
                "--max-book-feed-reconnects-per-hour",
                "1000000",
                "--max-chainlink-reconnects-per-hour",
                "1000000",
                "--max-book-feed-last-msg-age-sec",
                "1000000",
                "--max-chainlink-last-tick-age-sec",
                "1000000",
                "--max-book-feed-last-msg-age-p95-sec",
                "1000000",
                "--max-chainlink-last-tick-age-p95-sec",
                "1000000",
                "--max-chainlink-dropped-ticks",
                "1000000",
                "--max-chainlink-queue-size",
                "1000000",
            ],
        )
        run_step(
            "ops_snapshot",
            [
                py,
                "scripts/ops_snapshot.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--min-status-rows",
                "2",
                "--max-status-age-sec",
                "3153600000",
            ],
        )
        run_step(
            "ops_brief",
            [
                py,
                "scripts/ops_brief.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--min-status-rows",
                "2",
                "--max-status-age-sec",
                "3153600000",
                "--json",
            ],
        )
        run_step(
            "performance_budget_gate",
            [
                py,
                "scripts/performance_budget_gate.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--min-status-rows",
                "1",
                "--max-cycle-latency-p95-ms",
                "5000",
                "--max-cycle-latency-max-ms",
                "10000",
                "--max-process-rss-mb",
                "4096",
                "--max-order-capacity-used-ratio",
                "1.0",
                "--max-cancel-capacity-used-ratio",
                "1.0",
                "--max-latency-inactive-cycles",
                "200",
                "--max-market-data-span-ms",
                "5000",
                "--max-strategy-exec-span-ms",
                "5000",
                "--max-state-io-span-ms",
                "5000",
                "--max-status-io-span-ms",
                "5000",
                "--max-cycle-residual-span-ms",
                "5000",
                "--out",
                str(run_root / "performance_budget.json"),
            ],
        )
        run_step(
            "nightly_soak_report_run",
            [
                py,
                "scripts/nightly_soak_report.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--out",
                str(run_root / "nightly_run.json"),
            ],
        )
        run_step(
            "regression_envelope_audit",
            [
                py,
                "scripts/regression_envelope_audit.py",
                "--baseline",
                "ops/regression_envelope_ci.json",
                "--nightly-report",
                str(run_root / "nightly_run.json"),
                "--performance-report",
                str(run_root / "performance_budget.json"),
            ],
        )
        run_step(
            "forensics_bundle",
            [
                py,
                "scripts/forensics_bundle.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--config",
                args.config,
                "--out-dir",
                str(run_root),
            ],
        )
        td_cfg = run_root / "time_cfg.yaml"
        cfg_payload = yaml.safe_load(pathlib.Path(args.config).read_text(encoding="utf-8")) or {}
        storage = cfg_payload.get("storage", {})
        if not isinstance(storage, dict):
            storage = {}
            cfg_payload["storage"] = storage
        runtime = cfg_payload.get("runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            cfg_payload["runtime"] = runtime
        # The CI fixture rewrites storage paths, so setup-lock fingerprint checks
        # must be disabled for this temporary config copy.
        runtime["paper_enforce_setup_lock"] = False
        runtime["paper_expected_config_fingerprint_sha256"] = ""
        storage["log_dir"] = str(run_log_dir)
        td_cfg.write_text(yaml.safe_dump(cfg_payload, sort_keys=False), encoding="utf-8")
        run_step(
            "time_discipline_audit",
            [
                py,
                "scripts/time_discipline_audit.py",
                "--config",
                str(td_cfg),
                "--max-status-age-sec",
                "3153600000",
                "--min-status-rows",
                "1",
            ],
        )
        soak_policy = run_root / "soak_policy.yaml"
        soak_policy.write_text(
            yaml.safe_dump(
                {
                    "stage_order": ["paper"],
                    "stages": {"paper": {"min_status_rows": 1}},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        soak_budget = run_root / "soak_budget.yaml"
        soak_budget.write_text(
            yaml.safe_dump(
                {
                    "integrity": {"min_status_rows": 1, "max_status_age_sec": 3153600000},
                    "performance": {
                        "min_status_rows": 1,
                        "max_cycle_latency_p95_ms": 5000,
                        "max_cycle_latency_max_ms": 10000,
                        "max_process_rss_mb": 4096,
                        "max_order_capacity_used_ratio": 1.0,
                        "max_cancel_capacity_used_ratio": 1.0,
                        "max_latency_inactive_cycles": 200,
                        "max_market_data_span_ms": 5000,
                        "max_strategy_exec_span_ms": 5000,
                        "max_state_io_span_ms": 5000,
                        "max_status_io_span_ms": 5000,
                        "max_cycle_residual_span_ms": 5000,
                    },
                    "websocket": {
                        "min_status_rows": 1,
                        "max_book_feed_down_ratio": 1.0,
                        "max_chainlink_down_ratio": 1.0,
                        "max_book_feed_reconnects_per_hour": 1000000.0,
                        "max_chainlink_reconnects_per_hour": 1000000.0,
                        "max_book_feed_last_msg_age_sec": 1000000.0,
                        "max_chainlink_last_tick_age_sec": 1000000.0,
                        "max_chainlink_dropped_ticks": 1000000.0,
                        "max_chainlink_queue_size": 1000000.0,
                    },
                    "readiness": {"policy": str(soak_policy), "required_stage": "paper"},
                    "soak": {
                        "min_duration_minutes": 0.0,
                        "min_quote_uptime_ratio": 0.0,
                        "max_error_rows": 10,
                        "min_maker_submits": 0,
                        "min_taker_bonus_submits": 0,
                        "min_taker_bonus_fills": 0,
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        run_step(
            "soak_hardening_gate",
            [
                py,
                "scripts/soak_hardening_gate.py",
                "--log-dir",
                str(run_log_dir),
                "--run-id",
                run_id,
                "--budget",
                str(soak_budget),
            ],
        )
    with tempfile.TemporaryDirectory() as td:
        out_path = pathlib.Path(td) / "readiness.json"
        if log_dir:
            readiness_dir = pathlib.Path(log_dir).resolve()
            readiness_run_id = str(args.readiness_run_id).strip()
            if not readiness_run_id:
                raise SystemExit("--readiness-run-id is required when --readiness-log-dir is provided")
        else:
            readiness_dir = pathlib.Path(td) / "readiness_fixture"
            readiness_dir.mkdir(parents=True, exist_ok=True)
            readiness_run_id = "ci-readiness-1"
            status_path = readiness_dir / "status_2026-01-01.jsonl"
            events_path = readiness_dir / "events_2026-01-01.jsonl"
            errors_path = readiness_dir / "errors_2026-01-01.jsonl"
            status_rows = [
                json.dumps({"run_id": readiness_run_id, "gauge.open_orders": 1, "gauge.operating_mode_state": 0.0})
            ] * 40
            status_path.write_text("\n".join(status_rows) + "\n", encoding="utf-8")
            events_path.write_text("", encoding="utf-8")
            errors_path.write_text("", encoding="utf-8")
            (readiness_dir / f"run_manifest_{readiness_run_id}.json").write_text(
                json.dumps(
                    _fixture_manifest_payload(
                        run_id=readiness_run_id,
                        profile_name="ci-readiness",
                        status_path=status_path,
                        events_path=events_path,
                    )
                ),
                encoding="utf-8",
            )
        run_step(
            "readiness_gate",
            [
                py,
                "scripts/readiness_gate.py",
                "--log-dir",
                str(readiness_dir),
                "--run-id",
                readiness_run_id,
                "--policy",
                args.policy,
                "--out",
                str(out_path),
            ],
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        if not payload.get("highest_passing_stage"):
            raise SystemExit("readiness_gate fixture did not pass any stage")
    with tempfile.TemporaryDirectory() as td_reports:
        reports_root = pathlib.Path(td_reports)
        reports_log_dir = reports_root / "logs"
        reports_log_dir.mkdir(parents=True, exist_ok=True)
        reports_run_id = "ci-reports-1"
        date_str = "2099-01-01"
        reports_events_path = reports_log_dir / f"events_{date_str}.jsonl"
        reports_status_path = reports_log_dir / f"status_{date_str}.jsonl"
        reports_events_path.write_text(
            "\n".join(
                [
                    json.dumps({"run_id": reports_run_id, "event_type": "order_submit", "order_id": "o1", "reason": "maker_quote"}),
                    json.dumps({"run_id": reports_run_id, "event_type": "fill", "order_id": "o1", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 1.0}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        reports_status_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "run_id": reports_run_id,
                            "ts_utc": "2099-01-01T00:00:00.000Z",
                            "gauge.open_orders": 1,
                            "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.0},
                            "chainlink": {
                                "connected": True,
                                "reconnects": 0,
                                "last_tick_age_sec": 0.0,
                                "queue_size": 0,
                                "dropped_ticks": 0,
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "run_id": reports_run_id,
                            "ts_utc": "2099-01-01T00:00:01.000Z",
                            "gauge.open_orders": 1,
                            "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.0},
                            "chainlink": {
                                "connected": True,
                                "reconnects": 0,
                                "last_tick_age_sec": 0.0,
                                "queue_size": 0,
                                "dropped_ticks": 0,
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (reports_log_dir / f"errors_{date_str}.jsonl").write_text("", encoding="utf-8")
        (reports_log_dir / f"run_manifest_{reports_run_id}.json").write_text(
            json.dumps(
                _fixture_manifest_payload(
                    run_id=reports_run_id,
                    profile_name="ci-reports",
                    status_path=reports_status_path,
                    events_path=reports_events_path,
                )
            ),
            encoding="utf-8",
        )
        contract_samples = pathlib.Path("ops/api_contract_samples.json").resolve()
        run_step(
            "api_contract_drift_audit",
            [
                py,
                "scripts/api_contract_drift_audit.py",
                "--samples",
                str(contract_samples),
            ],
        )
        websocket_soak_out = reports_root / "websocket_soak.json"
        run_step(
            "websocket_reliability_gate_soak",
            [
                py,
                "scripts/websocket_reliability_gate.py",
                "--log-dir",
                str(reports_log_dir),
                "--run-id",
                reports_run_id,
                "--min-status-rows",
                "1",
                "--max-book-feed-down-ratio",
                "1.0",
                "--max-chainlink-down-ratio",
                "1.0",
                "--max-book-feed-reconnects-per-hour",
                "1000000",
                "--max-chainlink-reconnects-per-hour",
                "1000000",
                "--max-book-feed-last-msg-age-sec",
                "1000000",
                "--max-chainlink-last-tick-age-sec",
                "1000000",
                "--max-book-feed-last-msg-age-p95-sec",
                "1000000",
                "--max-chainlink-last-tick-age-p95-sec",
                "1000000",
                "--max-chainlink-dropped-ticks",
                "1000000",
                "--max-chainlink-queue-size",
                "1000000",
                "--out",
                str(websocket_soak_out),
            ],
        )
        soak_out = reports_root / "nightly.json"
        run_step(
            "nightly_soak_report",
            [
                py,
                "scripts/nightly_soak_report.py",
                "--log-dir",
                str(reports_log_dir),
                "--run-id",
                reports_run_id,
                "--out",
                str(soak_out),
            ],
        )
        soak_payload = json.loads(soak_out.read_text(encoding="utf-8"))
        if int(soak_payload.get("schema_version", 0)) < 2:
            raise SystemExit("nightly_soak_report schema_version missing")
        reconcile_out = reports_root / "reconcile.json"
        run_step(
            "reconcile_daily",
            [
                py,
                "scripts/reconcile_daily.py",
                "--config",
                args.config,
                "--log-dir",
                str(reports_log_dir),
                "--run-id",
                reports_run_id,
                "--date",
                date_str,
                "--out",
                str(reconcile_out),
            ],
        )
        reconcile_payload = json.loads(reconcile_out.read_text(encoding="utf-8"))
        if int(reconcile_payload.get("schema_version", 0)) < 3:
            raise SystemExit("reconcile_daily schema_version missing")
        if not str(reconcile_payload.get("verification_level", "")).strip():
            raise SystemExit("reconcile_daily verification_level missing")
        desk_out = reports_root / "desk_trade_report.json"
        run_step(
            "desk_trade_report",
            [
                py,
                "scripts/desk_trade_report.py",
                "--log-dir",
                str(reports_log_dir),
                "--date",
                date_str,
                "--out",
                str(desk_out),
            ],
        )
        desk_payload = json.loads(desk_out.read_text(encoding="utf-8"))
        if int(desk_payload.get("schema_version", 0)) < 1:
            raise SystemExit("desk_trade_report schema_version missing")
        promotion_out = reports_root / "promotion_gate.json"
        run_step(
            "promotion_evidence_gate",
            [
                py,
                "scripts/promotion_evidence_gate.py",
                "--policy",
                "ops/promotion_policy.yaml",
                "--soak-report",
                str(soak_out),
                "--reconcile-report",
                str(reconcile_out),
                "--websocket-report",
                str(websocket_soak_out),
                "--out",
                str(promotion_out),
            ],
        )
        promotion_payload = json.loads(promotion_out.read_text(encoding="utf-8"))
        if not isinstance(promotion_payload.get("ok"), bool):
            raise SystemExit("promotion_evidence_gate output missing ok")
        evidence_root = reports_root / "evidence_runs"
        evidence_root.mkdir(parents=True, exist_ok=True)
        for idx in range(1, 4):
            d = evidence_root / f"soak45_fixture_{idx}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "soak_hardening.json").write_text(
                json.dumps(
                    {
                        "lanes": {
                            "reliability": {"ok": True},
                            "utilization": {"ok": idx <= 2},
                        }
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            (d / "promotion.json").write_text(json.dumps({"ok": True}, indent=2, sort_keys=True), encoding="utf-8")
            (d / "websocket_reliability.json").write_text(
                json.dumps({"ok": True}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (d / "nightly.json").write_text(
                json.dumps({"duration_minutes": 45.0}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        run_step(
            "soak_evidence_window_gate",
            [
                py,
                "scripts/soak_evidence_window_gate.py",
                "--reports-root",
                str(evidence_root),
                "--policy",
                "ops/soak_evidence_policy.yaml",
            ],
        )
        live_soak_out = reports_root / "nightly_live.json"
        live_payload = dict(soak_payload)
        live_payload["quote_uptime_ratio"] = max(0.0, min(1.0, float(soak_payload.get("quote_uptime_ratio", 0.0)) - 0.01))
        live_payload["error_rows"] = float(soak_payload.get("error_rows", 0.0))
        eq = dict(live_payload.get("execution_quality", {}) or {})
        eq["capture_minus_adverse"] = float(eq.get("capture_minus_adverse", 0.0))
        live_payload["execution_quality"] = eq
        live_soak_out.write_text(json.dumps(live_payload, indent=2, sort_keys=True), encoding="utf-8")
        parity_out = reports_root / "parity.json"
        run_step(
            "paper_live_parity",
            [
                py,
                "scripts/paper_live_parity.py",
                "--paper-report",
                str(soak_out),
                "--live-report",
                str(live_soak_out),
                "--max-uptime-gap",
                "0.20",
                "--max-error-rows-gap",
                "10",
                "--max-capture-gap",
                "10",
                "--max-sniper-fill-rate-gap",
                "0.8",
                "--max-latency-p90-gap-ms",
                "500",
                "--out",
                str(parity_out),
            ],
        )
        parity_payload = json.loads(parity_out.read_text(encoding="utf-8"))
        if not isinstance(parity_payload.get("ok"), bool):
            raise SystemExit("paper_live_parity output missing ok")
    with tempfile.TemporaryDirectory() as td_drills:
        drills_dir = pathlib.Path(td_drills)
        drill_rows = [
            ("dns_failure", "2099-01-01T00:00:00Z"),
            ("packet_loss", "2099-01-01T00:01:00Z"),
            ("latency_spike", "2099-01-01T00:02:00Z"),
            ("endpoint_flap", "2099-01-01T00:03:00Z"),
        ]
        for idx, (fault, ts) in enumerate(drill_rows, start=1):
            payload = {"fault_type": fault, "ts_utc": ts, "ok": True}
            (drills_dir / f"drill_{idx}.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
        run_step("network_fault_drill", [py, "scripts/network_fault_drill.py", "--drills-dir", str(drills_dir), "--max-age-days", "36500"])

    if not args.skip_pip_audit and shutil.which("pip-audit"):
        run_step("pip_audit", [py, "-m", "pip_audit"])
    else:
        print("[ci_gate] step=pip_audit skipped (tool not installed)")

    print("[ci_gate] all steps passed")


if __name__ == "__main__":
    main()
