import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.guardian_watchdog import (
    _apply_disconnect_confirmation,
    _reset_startup_window_if_run_changed,
    _resolve_run_id,
    build_parser,
    evaluate_guard,
    latest_status_row,
    run_watchdog,
)


def _utc_iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class GuardianWatchdogTests(unittest.TestCase):
    def test_build_parser_defaults_disable_manifest_run_id_auto_resolution(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertFalse(bool(args.run_id_from_manifest))

    def test_build_parser_rejects_removed_authoritative_phases_flag(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--authoritative-phases", "active"])

    def test_startup_window_resets_when_run_id_changes(self):
        started_utc = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        last_run_id, started_mono, next_started_utc = _reset_startup_window_if_run_changed(
            active_run_id="r2",
            last_run_id="r1",
            started_mono=123.0,
            started_utc=started_utc,
        )
        self.assertEqual(last_run_id, "r2")
        self.assertGreaterEqual(started_mono, 0.0)
        self.assertGreaterEqual(next_started_utc, started_utc)

    def test_startup_window_unchanged_when_run_id_stable(self):
        started_utc = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        last_run_id, started_mono, next_started_utc = _reset_startup_window_if_run_changed(
            active_run_id="r1",
            last_run_id="r1",
            started_mono=123.0,
            started_utc=started_utc,
        )
        self.assertEqual(last_run_id, "r1")
        self.assertEqual(started_mono, 123.0)
        self.assertEqual(next_started_utc, started_utc)

    def test_evaluate_guard_triggers_missing_status_after_grace(self):
        now = dt.datetime.now(dt.timezone.utc)
        arm, reason, _details = evaluate_guard(
            status_row=None,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=120.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "status_missing")

    def test_evaluate_guard_triggers_error_burst(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
        }
        arm, reason, _details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=10.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=12,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "error_burst")

    def test_evaluate_guard_ignores_prestart_kill_switch_during_grace(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now - dt.timedelta(seconds=20)),
            "kill_switch": True,
            "kill_reason": "prior_run_halt",
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now - dt.timedelta(seconds=10),
            startup_elapsed_sec=10.0,
            startup_grace_sec=90.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")
        self.assertTrue(bool(details.get("kill_switch_prestart_ignored")))

    def test_evaluate_guard_prestart_kill_switch_not_ignored_after_grace(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now - dt.timedelta(seconds=20)),
            "kill_switch": True,
            "kill_reason": "prior_run_halt",
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now - dt.timedelta(minutes=5),
            startup_elapsed_sec=300.0,
            startup_grace_sec=90.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "kill_switch_engaged")
        self.assertEqual(details.get("kill_reason"), "prior_run_halt")

    def test_evaluate_guard_ignores_prestart_stale_during_grace(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now - dt.timedelta(minutes=5)),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now - dt.timedelta(seconds=10),
            startup_elapsed_sec=10.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")
        self.assertTrue(bool(details.get("status_stale_ignored_prestart")))

    def test_evaluate_guard_chainlink_disconnect_respects_min_age(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
            "chainlink": {
                "enabled": True,
                "connected": False,
                "last_tick_age_sec": 5.0,
            },
        }
        arm, reason, _details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=10.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=True,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")

    def test_evaluate_guard_includes_runtime_resource_snapshot_in_details(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
            "runtime_resource": {
                "process_cpu_percent": 21.5,
                "system_load1": 0.75,
            },
            "gauge.process_rss_mb": 256.0,
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=10.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")
        resource = details.get("runtime_resource")
        self.assertIsInstance(resource, dict)
        self.assertAlmostEqual(float(resource.get("process_cpu_percent", 0.0)), 21.5, places=6)
        self.assertAlmostEqual(float(resource.get("system_load1", 0.0)), 0.75, places=6)
        # Fallback gauge extraction remains available when nested snapshot omits it.
        self.assertAlmostEqual(float(resource.get("process_rss_mb", 0.0)), 256.0, places=6)

    def test_evaluate_guard_chainlink_disconnect_arms_after_min_age(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
            "chainlink": {
                "enabled": True,
                "connected": False,
                "last_tick_age_sec": 45.0,
            },
        }
        arm, reason, _details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=10.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=True,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "chainlink_disconnected")

    def test_evaluate_guard_chainlink_disconnect_unknown_age_suppressed_during_startup(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
            "chainlink": {
                "enabled": True,
                "connected": False,
                "last_tick_age_sec": None,
            },
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=10.0,
            startup_grace_sec=90.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=True,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")
        self.assertTrue(bool(details.get("chainlink_age_unknown_startup_suppressed")))

    def test_evaluate_guard_chainlink_disconnect_unknown_age_arms_after_startup(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "gauge.operating_mode_state": 0.0,
            "chainlink": {
                "enabled": True,
                "connected": False,
                "last_tick_age_sec": None,
            },
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now - dt.timedelta(minutes=5),
            startup_elapsed_sec=300.0,
            startup_grace_sec=90.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=True,
            require_book_feed_connected=False,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "chainlink_disconnected")
        self.assertFalse(bool(details.get("chainlink_age_unknown_startup_suppressed")))

    def test_evaluate_guard_scan_phase_does_not_require_market_truth(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "lifecycle_phase": "scan",
            "active_targets_present": False,
            "market_truth_required": False,
            "book_feed": {
                "enabled": True,
                "connected": False,
                "last_msg_age_sec": 45.0,
            },
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=300.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=True,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm)
        self.assertEqual(reason, "")
        self.assertFalse(bool(details.get("market_truth_required")))
        self.assertTrue(bool(details.get("scan_phase")))

    def test_evaluate_guard_active_targets_still_require_market_truth(self):
        now = dt.datetime.now(dt.timezone.utc)
        status = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "lifecycle_phase": "prepare",
            "active_targets_present": True,
            "market_truth_required": True,
            "book_feed": {
                "enabled": True,
                "connected": False,
                "last_msg_age_sec": 45.0,
            },
        }
        arm, reason, details = evaluate_guard(
            status_row=status,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=300.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=True,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertTrue(arm)
        self.assertEqual(reason, "book_feed_disconnected")
        self.assertTrue(bool(details.get("market_truth_required")))

    def test_evaluate_guard_requirement_transitions_with_runtime_context(self):
        now = dt.datetime.now(dt.timezone.utc)
        no_target = {
            "ts_utc": _utc_iso(now),
            "kill_switch": False,
            "lifecycle_phase": "scan",
            "active_targets_present": False,
            "market_truth_required": False,
            "book_feed": {"enabled": True, "connected": False, "last_msg_age_sec": 45.0},
        }
        arm_no_target, reason_no_target, _details_no_target = evaluate_guard(
            status_row=no_target,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=300.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=True,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        active_target = dict(no_target)
        active_target.update(
            {
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
            }
        )
        arm_active, reason_active, _details_active = evaluate_guard(
            status_row=active_target,
            now_utc=now,
            guardian_started_utc=now,
            startup_elapsed_sec=300.0,
            startup_grace_sec=30.0,
            max_status_age_sec=60.0,
            recent_error_count=0,
            max_errors_in_window=10,
            mode_trigger_level=3.0,
            trigger_on_kill_switch=True,
            require_chainlink_connected=False,
            require_book_feed_connected=True,
            chainlink_disconnect_min_age_sec=20.0,
            book_feed_disconnect_min_age_sec=20.0,
        )
        self.assertFalse(arm_no_target)
        self.assertEqual(reason_no_target, "")
        self.assertTrue(arm_active)
        self.assertEqual(reason_active, "book_feed_disconnected")

    def test_run_watchdog_once_arms_guard_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-test-guard-arm"
            session_id = "sess-test-guard-arm"
            session_token = "tok-test-guard-arm"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-01-01.jsonl"),
                events_path=str(logs / "events_2026-01-01.jsonl"),
                errors_path=str(logs / "errors_2026-01-01.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            status_path = logs / "status_2026-01-01.jsonl"
            row = {
                "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                "run_id": run_id,
                "kill_switch": True,
                "kill_reason": "unit_test",
            }
            status_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=3,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertTrue(guard_file.exists())
            first_line = guard_file.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first_line, "kill_switch_engaged")

    def test_run_watchdog_unknown_book_age_requires_min_age_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            status_path = logs / "status_2026-01-01.jsonl"
            row = {
                "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                "kill_switch": False,
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "market_truth_required": True,
                "book_feed": {
                    "enabled": True,
                    "connected": False,
                    "last_msg_age_sec": None,
                },
            }
            status_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=2.0,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=True,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_unknown_chainlink_age_requires_min_age_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            status_path = logs / "status_2026-01-01.jsonl"
            row = {
                "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                "kill_switch": False,
                "lifecycle_phase": "prepare",
                "active_targets_present": True,
                "chainlink": {
                    "enabled": True,
                    "connected": False,
                    "last_tick_age_sec": None,
                },
            }
            status_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=2.0,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=True,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_apply_disconnect_confirmation_unknown_age_requires_distinct_status_rows(self):
        details = {
            "disconnect_signal_strength": "weak_unknown_age",
            "status_ts_utc": "2026-03-17T10:45:20.459Z",
        }
        should_arm_1, streak_1, required_1, last_ts_1 = _apply_disconnect_confirmation(
            details=details,
            streak=0,
            poll_interval_sec=2.0,
            disconnect_confirm_polls=3,
            disconnect_min_age_sec=20.0,
            disconnect_age_sec=None,
            last_status_ts_utc="",
            unknown_age_confirm_rows=2,
        )
        self.assertFalse(should_arm_1)
        self.assertEqual(streak_1, 1)
        self.assertEqual(required_1, 2)
        self.assertEqual(last_ts_1, "2026-03-17T10:45:20.459Z")

        should_arm_2, streak_2, required_2, last_ts_2 = _apply_disconnect_confirmation(
            details=details,
            streak=streak_1,
            poll_interval_sec=2.0,
            disconnect_confirm_polls=3,
            disconnect_min_age_sec=20.0,
            disconnect_age_sec=None,
            last_status_ts_utc=last_ts_1,
            unknown_age_confirm_rows=2,
        )
        self.assertFalse(should_arm_2)
        self.assertEqual(streak_2, 1)
        self.assertEqual(required_2, 2)
        self.assertEqual(last_ts_2, "2026-03-17T10:45:20.459Z")

    def test_apply_disconnect_confirmation_unknown_age_arms_on_second_distinct_status(self):
        details_1 = {
            "disconnect_signal_strength": "weak_unknown_age",
            "status_ts_utc": "2026-03-17T10:45:20.459Z",
        }
        details_2 = {
            "disconnect_signal_strength": "weak_unknown_age",
            "status_ts_utc": "2026-03-17T10:45:50.733Z",
        }
        should_arm_1, streak_1, _required_1, last_ts_1 = _apply_disconnect_confirmation(
            details=details_1,
            streak=0,
            poll_interval_sec=2.0,
            disconnect_confirm_polls=3,
            disconnect_min_age_sec=20.0,
            disconnect_age_sec=None,
            last_status_ts_utc="",
            unknown_age_confirm_rows=2,
        )
        self.assertFalse(should_arm_1)
        should_arm_2, streak_2, required_2, last_ts_2 = _apply_disconnect_confirmation(
            details=details_2,
            streak=streak_1,
            poll_interval_sec=2.0,
            disconnect_confirm_polls=3,
            disconnect_min_age_sec=20.0,
            disconnect_age_sec=None,
            last_status_ts_utc=last_ts_1,
            unknown_age_confirm_rows=2,
        )
        self.assertTrue(should_arm_2)
        self.assertEqual(streak_2, 2)
        self.assertEqual(required_2, 2)
        self.assertEqual(last_ts_2, "2026-03-17T10:45:50.733Z")

    def test_latest_status_row_handles_day_rollover_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status_old = root / "status_2026-02-27.jsonl"
            status_new = root / "status_2026-02-28.jsonl"
            old_row = {"ts_utc": "2026-02-27T23:59:59.000Z", "kill_switch": False}
            status_old.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
            status_new.write_text("", encoding="utf-8")

            row = latest_status_row(root, max_lines=100, max_files=3)
            self.assertIsNotNone(row)
            self.assertEqual(row.get("ts_utc"), "2026-02-27T23:59:59.000Z")

    def test_latest_status_row_run_id_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status = root / "status_2026-02-28.jsonl"
            rows = [
                {"ts_utc": "2026-02-28T01:00:00.000Z", "run_id": "r1", "kill_switch": True},
                {"ts_utc": "2026-02-28T01:00:01.000Z", "run_id": "r2", "kill_switch": False},
            ]
            status.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            row = latest_status_row(root, max_lines=100, max_files=3, run_id="r1")
            self.assertIsNotNone(row)
            self.assertEqual(row.get("run_id"), "r1")

    def test_latest_status_row_run_id_filter_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status = root / "status_2026-02-28.jsonl"
            rows = [
                {"ts_utc": "2026-02-28T01:00:00.000Z", "run_id": "r1"},
                {"ts_utc": "2026-02-28T01:00:01.000Z", "run_id": "r2"},
            ]
            status.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            row = latest_status_row(root, max_lines=100, max_files=3, run_id="missing")
            self.assertIsNone(row)

    def test_run_watchdog_auto_resolves_run_id_from_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            manifest = logs / "run_manifest_abc123.json"
            manifest.write_text(json.dumps({"run_id": "abc123"}), encoding="utf-8")
            status_path = logs / "status_2026-01-01.jsonl"
            rows = [
                {"ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)), "run_id": "other", "kill_switch": True},
                {"ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)), "run_id": "abc123", "kill_switch": False},
            ]
            status_path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=3,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_id_from_manifest=True,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_resolve_run_id_uses_most_recent_manifest_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            old_manifest = logs / "run_manifest_zzzz-old.json"
            new_manifest = logs / "run_manifest_aaaa-new.json"
            old_manifest.write_text("{}", encoding="utf-8")
            new_manifest.write_text("{}", encoding="utf-8")
            # Force deterministic mtime ordering opposite lexical ordering.
            base = dt.datetime.now(dt.timezone.utc).timestamp()
            old_ts = base - 10
            new_ts = base
            os.utime(old_manifest, (old_ts, old_ts))
            os.utime(new_manifest, (new_ts, new_ts))
            run_id = _resolve_run_id(logs, explicit_run_id="", auto_from_manifest=True, max_files=5)
            self.assertEqual(run_id, "aaaa-new")

    def test_run_watchdog_blocks_guard_arm_without_authoritative_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "run_manifest_run-noctx.json").write_text(json.dumps({"run_id": "run-noctx"}), encoding="utf-8")
            (logs / "status_2026-01-01.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": "run-noctx",
                        "kill_switch": True,
                        "kill_reason": "probe_no_context",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file="",
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=True,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_fails_startup_when_authority_required_and_context_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file="",
                session_token="",
                require_authoritative_startup=True,
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 2)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_arms_when_context_is_explicit_and_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-auth"
            session_id = "sess-auth"
            session_token = "tok-auth"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                require_authoritative_startup=True,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertTrue(guard_file.exists())

    def test_run_watchdog_blocks_guard_arm_when_context_token_mismatched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-auth-mismatch-token"
            session_id = "sess-auth-mismatch-token"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": "token-from-context",
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token="token-from-arg",
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_blocks_guard_arm_when_context_run_contract_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-context"
            contract_run_id = "run-contract"
            session_id = "sess-contract-mismatch"
            session_token = "tok-contract-mismatch"
            manifest_path = logs / f"run_manifest_{contract_run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": contract_run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{contract_run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=contract_run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_blocks_guard_arm_when_contract_forbids_guardian_control(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-forbidden-action"
            session_id = "sess-forbidden-action"
            session_token = "tok-forbidden-action"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_blocks_guard_arm_when_context_file_outside_log_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            external = root / "external"
            external.mkdir(parents=True, exist_ok=True)
            run_id = "run-outside-context"
            session_id = "sess-outside-context"
            session_token = "tok-outside-context"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = external / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "active",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_blocks_guard_arm_in_non_authoritative_phase(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            run_id = "run-nonauth-phase"
            session_id = "sess-nonauth-phase"
            session_token = "tok-nonauth-phase"
            manifest_path = logs / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            run_contract_path = logs / f"run_contract_{run_id}.json"
            contract = build_run_contract(
                session_id=session_id,
                run_id=run_id,
                phase="active",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=["guardian_control", "validate_active", "stop_session"],
                manifest_path=manifest_path,
                log_root=logs,
                state_root=root / "data",
                start_ts="2026-03-19T00:00:00.000Z",
                stop_ts="",
                evidence_slice_start_ts="2026-03-19T00:00:00.000Z",
                evidence_slice_end_ts="",
                status_path=str(logs / "status_2026-03-19.jsonl"),
                events_path=str(logs / "events_2026-03-19.jsonl"),
                errors_path=str(logs / "errors_2026-03-19.jsonl"),
            )
            write_run_contract(run_contract_path, contract, allow_open=True)
            context_path = logs / "guardian_session_context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "session_phase": "validate_postrun",
                        "session_token": session_token,
                        "run_id": run_id,
                        "run_contract_path": str(run_contract_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "status_2026-03-19.jsonl").write_text(
                json.dumps(
                    {
                        "ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)),
                        "run_id": run_id,
                        "kill_switch": True,
                        "kill_reason": "probe_authoritative",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=0.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=1,
                disconnect_unknown_age_confirm_rows=1,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="",
                run_contract="",
                session_phase="",
                session_id="",
                session_context_file=str(context_path),
                session_token=session_token,
                authoritative_phases="start,active,validate_active",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())

    def test_run_watchdog_explicit_run_id_ignores_other_run_kill_switch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            status_path = logs / "status_2026-01-01.jsonl"
            rows = [
                {"ts_utc": _utc_iso(dt.datetime.now(dt.timezone.utc)), "run_id": "other", "kill_switch": True},
            ]
            status_path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            guard_file = root / "guard_stop.txt"
            args = SimpleNamespace(
                log_dir=str(logs),
                guard_stop_file=str(guard_file),
                interval_sec=0.1,
                once=True,
                auto_clear=False,
                startup_grace_sec=9999.0,
                max_status_age_sec=60.0,
                error_window_sec=60.0,
                max_errors_in_window=99,
                mode_trigger_level=3.0,
                trigger_on_kill_switch=True,
                require_chainlink_connected=False,
                require_book_feed_connected=False,
                chainlink_disconnect_min_age_sec=20.0,
                book_feed_disconnect_min_age_sec=20.0,
                disconnect_confirm_polls=3,
                status_tail_lines=200,
                status_files_tail=3,
                error_tail_lines=200,
                run_id="target",
                run_id_from_manifest=False,
                manifest_files_tail=3,
            )
            rc = run_watchdog(args)
            self.assertEqual(rc, 0)
            self.assertFalse(guard_file.exists())


if __name__ == "__main__":
    unittest.main()
