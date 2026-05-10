from types import SimpleNamespace
import datetime as dt
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from prodesk.canonical_authority import CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS
from prodesk.run_contract import build_run_contract, write_run_contract
from executor import ExecutionRunner, enforce_operator_entry_policy


class ExecutorHardeningTests(unittest.TestCase):
    def _write_open_run_contract(
        self,
        *,
        log_dir: str,
        run_id: str,
        session_id: str,
        allowed_actions: list[str] | None = None,
    ) -> str:
        root = tempfile.mkdtemp(dir=log_dir)
        root_path = os.path.abspath(root)
        manifest_path = os.path.join(log_dir, f"run_manifest_{run_id}.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"run_id": run_id}, fh)
        payload = build_run_contract(
            session_id=session_id,
            run_id=run_id,
            phase="start",
            session_type="paper_canonical",
            authority_level="authoritative",
            allowed_actions=list(allowed_actions or CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            manifest_path=pathlib.Path(manifest_path),
            log_root=pathlib.Path(log_dir),
            state_root=pathlib.Path(root_path),
            start_ts="2026-03-21T00:00:00.000Z",
            stop_ts="",
            evidence_slice_start_ts="2026-03-21T00:00:00.000Z",
            evidence_slice_end_ts="",
            status_path=os.path.join(log_dir, "status_2026-03-21.jsonl"),
            events_path=os.path.join(log_dir, "events_2026-03-21.jsonl"),
            errors_path=os.path.join(log_dir, "errors_2026-03-21.jsonl"),
        )
        out_path = pathlib.Path(log_dir) / f"run_contract_{run_id}.json"
        write_run_contract(out_path, payload, allow_open=True)
        return str(out_path)

    def test_operator_entry_policy_blocks_direct_paper_execution_without_canonical_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                enforce_operator_entry_policy(mode="paper")
        self.assertIn("disabled for paper mode", str(ctx.exception))

    def test_operator_entry_policy_allows_canonical_session_env(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = os.path.join(td, "guardian_session_context.json")
            run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            run_contract = self._write_open_run_contract(log_dir=td, run_id=run_id, session_id="sess-1")
            payload = {
                "session_id": "sess-1",
                "session_phase": "start",
                "session_token": "tok-1",
                "run_id": run_id,
                "run_contract_path": run_contract,
            }
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with mock.patch.dict(
                os.environ,
                {
                    "BRO_CANONICAL_SESSION_CALL": "1",
                    "BRO_CANONICAL_SESSION_TOKEN": "tok-1",
                    "BRO_CANONICAL_SESSION_CONTEXT_FILE": context_path,
                    "BRO_RUN_ID": run_id,
                },
                clear=True,
            ):
                enforce_operator_entry_policy(mode="paper", config={"storage": {"log_dir": td}})

    def test_operator_entry_policy_rejects_missing_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = os.path.join(td, "guardian_session_context.json")
            payload = {
                "session_id": "sess-1",
                "session_phase": "start",
                "session_token": "tok-1",
                "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            }
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with mock.patch.dict(
                os.environ,
                {
                    "BRO_CANONICAL_SESSION_CALL": "1",
                    "BRO_CANONICAL_SESSION_TOKEN": "tok-1",
                    "BRO_CANONICAL_SESSION_CONTEXT_FILE": context_path,
                },
                clear=True,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    enforce_operator_entry_policy(mode="paper")
        self.assertIn("run_id missing", str(ctx.exception))

    def test_operator_entry_policy_rejects_missing_canonical_session_handshake(self):
        with mock.patch.dict(os.environ, {"BRO_CANONICAL_SESSION_CALL": "1"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                enforce_operator_entry_policy(mode="paper")
        self.assertIn("canonical session handshake missing", str(ctx.exception))

    def test_operator_entry_policy_rejects_canonical_session_token_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = os.path.join(td, "guardian_session_context.json")
            run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            run_contract = self._write_open_run_contract(log_dir=td, run_id=run_id, session_id="sess-1")
            payload = {
                "session_id": "sess-1",
                "session_phase": "start",
                "session_token": "tok-from-file",
                "run_id": run_id,
                "run_contract_path": run_contract,
            }
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with mock.patch.dict(
                os.environ,
                {
                    "BRO_CANONICAL_SESSION_CALL": "1",
                    "BRO_CANONICAL_SESSION_TOKEN": "tok-mismatch",
                    "BRO_CANONICAL_SESSION_CONTEXT_FILE": context_path,
                    "BRO_RUN_ID": run_id,
                },
                clear=True,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    enforce_operator_entry_policy(mode="paper", config={"storage": {"log_dir": td}})
        self.assertIn("session_token_mismatch", str(ctx.exception))

    def test_operator_entry_policy_rejects_canonical_session_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = os.path.join(td, "guardian_session_context.json")
            run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            run_contract = self._write_open_run_contract(log_dir=td, run_id=run_id, session_id="sess-1")
            payload = {
                "session_id": "sess-1",
                "session_phase": "start",
                "session_token": "tok-1",
                "run_id": run_id,
                "run_contract_path": run_contract,
            }
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with mock.patch.dict(
                os.environ,
                {
                    "BRO_CANONICAL_SESSION_CALL": "1",
                    "BRO_CANONICAL_SESSION_TOKEN": "tok-1",
                    "BRO_CANONICAL_SESSION_CONTEXT_FILE": context_path,
                    "BRO_RUN_ID": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                },
                clear=True,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    enforce_operator_entry_policy(mode="paper", config={"storage": {"log_dir": td}})
        self.assertIn("run_contract_run_id_mismatch", str(ctx.exception))

    def test_operator_entry_policy_rejects_missing_executor_capability(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = os.path.join(td, "guardian_session_context.json")
            run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            run_contract = self._write_open_run_contract(
                log_dir=td,
                run_id=run_id,
                session_id="sess-1",
                allowed_actions=["guardian_control"],
            )
            payload = {
                "session_id": "sess-1",
                "session_phase": "start",
                "session_token": "tok-1",
                "run_id": run_id,
                "run_contract_path": run_contract,
            }
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            with mock.patch.dict(
                os.environ,
                {
                    "BRO_CANONICAL_SESSION_CALL": "1",
                    "BRO_CANONICAL_SESSION_TOKEN": "tok-1",
                    "BRO_CANONICAL_SESSION_CONTEXT_FILE": context_path,
                    "BRO_RUN_ID": run_id,
                },
                clear=True,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    enforce_operator_entry_policy(mode="paper", config={"storage": {"log_dir": td}})
        self.assertIn("run_contract_action_forbidden", str(ctx.exception))

    def test_operator_entry_policy_rejects_legacy_break_glass_env_override(self):
        with mock.patch.dict(os.environ, {"BRO_ALLOW_DIRECT_EXECUTOR": "1"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                enforce_operator_entry_policy(mode="paper")
        self.assertIn("disabled for paper mode", str(ctx.exception))

    def test_operator_entry_policy_ignores_non_paper_modes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            enforce_operator_entry_policy(mode="live")

    def test_effective_risk_rejects_excludes_kill_switch_rejects(self):
        self.assertEqual(ExecutionRunner._effective_risk_rejects(10, 3), 7)
        self.assertEqual(ExecutionRunner._effective_risk_rejects(3, 10), 0)

    def test_stale_auto_stop_eligible_requires_samples_and_stale_events(self):
        snapshot = SimpleNamespace(sample_count=11, stale_reject_count=7, risk_reject_count=20)
        self.assertFalse(
            ExecutionRunner._stale_auto_stop_eligible(
                snapshot,
                min_samples=12,
                min_stale_rejects=8,
                min_risk_rejects=24,
            )
        )
        snapshot_low_rr = SimpleNamespace(sample_count=15, stale_reject_count=9, risk_reject_count=12)
        self.assertFalse(
            ExecutionRunner._stale_auto_stop_eligible(
                snapshot_low_rr,
                min_samples=12,
                min_stale_rejects=8,
                min_risk_rejects=24,
            )
        )
        snapshot_ok = SimpleNamespace(sample_count=15, stale_reject_count=9, risk_reject_count=25)
        self.assertTrue(
            ExecutionRunner._stale_auto_stop_eligible(
                snapshot_ok,
                min_samples=12,
                min_stale_rejects=8,
                min_risk_rejects=24,
            )
        )

    def test_taker_context_falls_back_when_expiry_metadata_missing_and_allowed(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.token_ids = ["t1", "t2"]
        runner.token_expiry_dt_by_token = {}
        runner.taker_arming_horizon_sec = 600.0
        runner.taker_execution_cutoff_sec = 10.0
        runner.taker_require_lag_verification = True
        runner.taker_allow_without_expiry_metadata = True
        runner._lag_verified = lambda _token_id: True

        ctx = ExecutionRunner._taker_context(runner)
        self.assertTrue(ctx["active"])
        self.assertEqual(set(ctx["token_ids"]), {"t1", "t2"})
        self.assertEqual(set(ctx["near_token_ids"]), {"t1", "t2"})

    def test_taker_context_stays_blocked_when_expiry_metadata_missing_and_not_allowed(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.token_ids = ["t1", "t2"]
        runner.token_expiry_dt_by_token = {}
        runner.taker_arming_horizon_sec = 600.0
        runner.taker_execution_cutoff_sec = 10.0
        runner.taker_require_lag_verification = True
        runner.taker_allow_without_expiry_metadata = False
        runner._lag_verified = lambda _token_id: True

        ctx = ExecutionRunner._taker_context(runner)
        self.assertFalse(ctx["active"])
        self.assertEqual(ctx["token_ids"], [])

    def test_taker_context_falls_back_when_no_tokens_in_window_and_allowed(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.token_ids = ["t1"]
        runner.token_expiry_dt_by_token = {"t1": dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5)}
        runner.taker_arming_horizon_sec = 600.0
        runner.taker_execution_cutoff_sec = 10.0
        runner.taker_require_lag_verification = True
        runner.taker_allow_without_expiry_metadata = True
        runner._lag_verified = lambda _token_id: True

        ctx = ExecutionRunner._taker_context(runner)
        self.assertTrue(ctx["active"])
        self.assertEqual(ctx["token_ids"], ["t1"])

    def test_taker_context_keeps_true_late_window_tokens_below_legacy_cutoff(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.token_ids = ["t1", "t2"]
        runner.token_expiry_dt_by_token = {
            "t1": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=6),
            "t2": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=12),
        }
        runner.taker_arming_horizon_sec = 600.0
        runner.taker_execution_cutoff_sec = 10.0
        runner.taker_require_lag_verification = True
        runner.taker_allow_without_expiry_metadata = False
        runner._lag_verified = lambda _token_id: True

        ctx = ExecutionRunner._taker_context(runner)
        self.assertTrue(ctx["active"])
        self.assertEqual(set(ctx["near_token_ids"]), {"t1", "t2"})
        self.assertEqual(set(ctx["token_ids"]), {"t1", "t2"})

    def test_taker_stage_window_token_ids_use_stage_eligible_window_tokens(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner._unique_ordered = ExecutionRunner._unique_ordered

        token_ids = ExecutionRunner._taker_stage_window_token_ids(
            runner,
            taker_ctx={
                "near_token_ids": ["obs", "live", "live", "unverified"],
                "token_ids": ["live"],
            },
            taker_stage_tokens={"live", "unverified"},
        )

        self.assertEqual(token_ids, ["live", "unverified"])

    def test_taker_stage_window_token_ids_falls_back_to_active_tokens_when_near_missing(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner._unique_ordered = ExecutionRunner._unique_ordered

        token_ids = ExecutionRunner._taker_stage_window_token_ids(
            runner,
            taker_ctx={
                "near_token_ids": [],
                "token_ids": ["t1", "t2", "t1"],
            },
            taker_stage_tokens={"t2", "t1"},
        )

        self.assertEqual(token_ids, ["t1", "t2"])

    def test_ws_slo_degraded_cycle_flags_disconnect_and_stale_age(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_require_book_connected = True
        runner.operating_mode_ws_slo_require_chainlink_connected = True
        runner.operating_mode_ws_slo_max_book_last_msg_age_sec = 12.0
        runner.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = 30.0

        degraded, reasons = ExecutionRunner._ws_slo_degraded_cycle(
            runner,
            has_targets=True,
            book_feed_status={"connected": False, "last_msg_age_sec": 20.0},
            chainlink_status={"connected": True, "last_tick_age_sec": 1.0},
        )
        self.assertTrue(degraded)
        self.assertIn("book_feed_disconnected", reasons)
        self.assertIn("book_feed_last_msg_age_high", reasons)

    def test_ws_slo_degraded_cycle_suppresses_disconnects_during_bootstrap_grace(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_require_book_connected = True
        runner.operating_mode_ws_slo_require_chainlink_connected = True
        runner.operating_mode_ws_slo_max_book_last_msg_age_sec = 12.0
        runner.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = 30.0
        runner.operating_mode_ws_slo_bootstrap_grace_sec = 45.0
        runner._ws_slo_bootstrap_started_mono = time.monotonic()

        degraded, reasons = ExecutionRunner._ws_slo_degraded_cycle(
            runner,
            has_targets=True,
            book_feed_status={"connected": False, "last_msg_age_sec": None},
            chainlink_status={"connected": False, "last_tick_age_sec": None},
        )
        self.assertFalse(degraded)
        self.assertEqual(reasons, [])
        self.assertTrue(runner._ws_slo_bootstrap_active)

    def test_ws_slo_degraded_cycle_enforces_after_bootstrap_grace(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_require_book_connected = True
        runner.operating_mode_ws_slo_require_chainlink_connected = True
        runner.operating_mode_ws_slo_max_book_last_msg_age_sec = 12.0
        runner.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = 30.0
        runner.operating_mode_ws_slo_bootstrap_grace_sec = 45.0
        runner._ws_slo_bootstrap_started_mono = time.monotonic() - 120.0

        degraded, reasons = ExecutionRunner._ws_slo_degraded_cycle(
            runner,
            has_targets=True,
            book_feed_status={"connected": False, "last_msg_age_sec": None},
            chainlink_status={"connected": True, "last_tick_age_sec": 1.0},
        )
        self.assertTrue(degraded)
        self.assertIn("book_feed_disconnected", reasons)
        self.assertFalse(runner._ws_slo_bootstrap_active)

    def test_ws_slo_bootstrap_guard_resets_invalid_monotonic_marker(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_bootstrap_grace_sec = 45.0
        runner._ws_slo_bootstrap_started_mono = float("nan")

        active = ExecutionRunner._ws_slo_bootstrap_guard_active(runner, has_targets=True)
        self.assertTrue(active)
        self.assertTrue(runner._ws_slo_bootstrap_active)
        self.assertTrue(float(runner._ws_slo_bootstrap_started_mono) > 0.0)

    def test_ws_slo_degraded_cycle_passes_healthy_status(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_require_book_connected = True
        runner.operating_mode_ws_slo_require_chainlink_connected = True
        runner.operating_mode_ws_slo_max_book_last_msg_age_sec = 12.0
        runner.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = 30.0

        degraded, reasons = ExecutionRunner._ws_slo_degraded_cycle(
            runner,
            has_targets=True,
            book_feed_status={"connected": True, "last_msg_age_sec": 1.0},
            chainlink_status={"connected": True, "last_tick_age_sec": 1.0},
        )
        self.assertFalse(degraded)
        self.assertEqual(reasons, [])

    def test_ws_slo_degraded_cycle_flags_all_target_ws_missing_with_rest_fallback(self):
        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.cfg = {"market_data": {"ws": {"enabled": True}}, "chainlink": {"enabled": True}}
        runner.operating_mode_ws_slo_enforce = True
        runner.operating_mode_ws_slo_require_book_connected = True
        runner.operating_mode_ws_slo_require_chainlink_connected = True
        runner.operating_mode_ws_slo_max_book_last_msg_age_sec = 12.0
        runner.operating_mode_ws_slo_max_chainlink_last_tick_age_sec = 30.0
        runner.operating_mode_ws_slo_bootstrap_grace_sec = 0.0
        runner._ws_slo_bootstrap_started_mono = 0.0

        degraded, reasons = ExecutionRunner._ws_slo_degraded_cycle(
            runner,
            has_targets=True,
            book_feed_status={"connected": True, "last_msg_age_sec": 1.0},
            chainlink_status={"connected": True, "last_tick_age_sec": 1.0},
            all_targets_missing_ws_books=True,
            rest_fallback_used_cycle=True,
        )
        self.assertTrue(degraded)
        self.assertIn("book_feed_ws_books_missing_all_targets", reasons)

    def test_missing_book_not_found_error_detection(self):
        self.assertTrue(
            ExecutionRunner._is_missing_book_not_found_error(
                "404 Client Error: Not Found for url: https://clob.polymarket.com/book?token_id=abc"
            )
        )
        self.assertFalse(
            ExecutionRunner._is_missing_book_not_found_error(
                "503 Server Error: Service Unavailable for url: https://clob.polymarket.com/book?token_id=abc"
            )
        )

    def test_cancel_all_open_orders_releases_locks_only_for_confirmed_cancels(self):
        released: list[str] = []
        logged: dict[str, object] = {}
        counters: list[str] = []

        runner = ExecutionRunner.__new__(ExecutionRunner)
        runner.run_id = "rid-1"
        runner.tx_manager = SimpleNamespace(
            cancel_all_with_summary=lambda: {
                "gateway_reported_canceled_count": 3,
                "open_before_count": 3,
                "open_after_count": 1,
                "confirmed_canceled_count": 2,
                "confirmed_canceled_order_ids": ["ord-1", "ord-2"],
                "unconfirmed_order_ids": ["ord-3"],
            }
        )
        runner.wallet = SimpleNamespace(release_order_lock=lambda order_id: released.append(str(order_id)))
        runner.telemetry = SimpleNamespace(incr=lambda name: counters.append(str(name)))
        runner.events = SimpleNamespace(
            log_event=lambda event_name, payload: logged.update({"event_name": event_name, "payload": payload})
        )

        ExecutionRunner._cancel_all_open_orders(
            runner,
            event_name="cancel_all_on_exit",
            reason="runner_shutdown",
            telemetry_counter="shutdown_cancel_all_calls",
        )

        self.assertEqual(released, ["ord-1", "ord-2"])
        self.assertIn("shutdown_cancel_all_calls", counters)
        self.assertEqual(logged.get("event_name"), "cancel_all_on_exit")
        payload = logged.get("payload")
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(payload.get("canceled_count"), 2)
        self.assertEqual(payload.get("released_lock_count"), 2)
        self.assertEqual(payload.get("unconfirmed_open_count"), 1)


if __name__ == "__main__":
    unittest.main()
