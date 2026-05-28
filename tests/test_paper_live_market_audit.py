import pathlib
import tempfile
import unittest
import datetime as dt
import time
from unittest import mock

from scripts.paper_live_market_audit import (
    ConditionAuditState,
    PaperLiveMarketAudit,
    books_all_404,
    launch_broctl,
    normalize_outcome_label,
    outcome_hint_from_market_key,
    parse_args,
    parse_owned_market_ref,
    run_audit,
    snapshot_dir_name,
    should_snapshot_for_event,
    summarize_book,
)


class PaperLiveMarketAuditTests(unittest.TestCase):
    def _make_audit(self) -> PaperLiveMarketAudit:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = pathlib.Path(tempdir.name)
        audit = PaperLiveMarketAudit(
            log_dir=root,
            out_dir=root / "out",
            run_id="run-1",
            snapshot_cadence_sec=5.0,
        )
        return audit

    def test_parse_owned_market_ref_extracts_condition_and_window(self):
        payload = parse_owned_market_ref("0xabc123|2026-05-27T04:35:00.000Z|na")
        self.assertEqual(
            payload,
            {
                "condition_id": "0xabc123",
                "window_ts_utc": "2026-05-27T04:35:00.000Z",
                "suffix": "na",
                "owned_market_ref": "0xabc123|2026-05-27T04:35:00.000Z|na",
            },
        )

    def test_parse_owned_market_ref_rejects_blank_or_non_condition(self):
        self.assertIsNone(parse_owned_market_ref(""))
        self.assertIsNone(parse_owned_market_ref("market-1|foo|bar"))

    def test_outcome_hint_handles_yes_no_and_up_down(self):
        self.assertEqual(normalize_outcome_label("Up"), "YES")
        self.assertEqual(normalize_outcome_label("down"), "NO")
        self.assertEqual(outcome_hint_from_market_key("abc|def|YES"), "YES")
        self.assertEqual(outcome_hint_from_market_key("abc|def|NO"), "NO")

    def test_summarize_book_counts_levels_and_tops(self):
        payload = {
            "bids": [{"price": "0.45", "size": "100"}, {"price": "0.48", "size": "75"}],
            "asks": [{"price": "0.55", "size": "200"}, {"price": "0.52", "size": "50"}],
        }
        self.assertEqual(
            summarize_book(payload),
            {
                "bid_levels": 2,
                "ask_levels": 2,
                "top_bid": {"price": "0.48", "size": "75"},
                "top_ask": {"price": "0.52", "size": "50"},
            },
        )

    def test_snapshot_dir_name_uses_subsecond_precision(self):
        stamp = dt.datetime(2026, 5, 27, 5, 4, 46, 11000, tzinfo=dt.timezone.utc)
        self.assertEqual(snapshot_dir_name(stamp), "snapshot_20260527T050446011000Z")

    def test_books_all_404_requires_every_token_closed(self):
        self.assertTrue(
            books_all_404(
                {
                    "tok1": {"status_code": 404},
                    "tok2": {"status_code": 404},
                }
            )
        )
        self.assertFalse(
            books_all_404(
                {
                    "tok1": {"status_code": 404},
                    "tok2": {"status_code": 200},
                }
            )
        )

    def test_should_snapshot_for_key_event_types(self):
        self.assertTrue(should_snapshot_for_event({"event_type": "order_submit"}))
        self.assertTrue(should_snapshot_for_event({"event_type": "fill"}))
        self.assertTrue(
            should_snapshot_for_event(
                {
                    "event_type": "edge_evaluation",
                    "action_taken": "maker",
                }
            )
        )
        self.assertFalse(
            should_snapshot_for_event(
                {
                    "event_type": "edge_evaluation",
                    "action_taken": "none",
                }
            )
        )

    def test_infer_condition_from_submit_market_id_when_owned_market_ref_missing(self):
        audit = self._make_audit()
        condition = ConditionAuditState(
            condition_id="0xcond1",
            owned_market_ref="0xcond1|2026-05-27T04:55:00.000Z|na",
            market_slug="btc-updown-5m-1",
            question="Question",
            dir=pathlib.Path("/tmp/market_1"),
            tokens=["tok-yes", "tok-no"],
            first_event_type="lifecycle_phase_transition",
            first_seen_utc="2026-05-27T04:54:45.000Z",
        )
        audit.conditions[condition.condition_id] = condition
        inferred = audit._infer_condition_from_event(
            {
                "event_type": "order_submit",
                "market_id": "tok-yes",
                "owned_market_ref": "",
            }
        )
        self.assertIs(inferred, condition)

    def test_order_binding_recovers_cancel_without_owned_market_ref(self):
        audit = self._make_audit()
        condition = ConditionAuditState(
            condition_id="0xcond1",
            owned_market_ref="0xcond1|2026-05-27T04:55:00.000Z|na",
            market_slug="btc-updown-5m-1",
            question="Question",
            dir=pathlib.Path("/tmp/market_1"),
            tokens=["tok-yes", "tok-no"],
            first_event_type="lifecycle_phase_transition",
            first_seen_utc="2026-05-27T04:54:45.000Z",
        )
        audit.conditions[condition.condition_id] = condition
        audit._remember_order_binding(
            event={
                "event_type": "order_submit",
                "order_id": "paper-order-2",
            },
            condition=condition,
        )
        inferred = audit._infer_condition_from_event(
            {
                "event_type": "order_cancel",
                "order_id": "paper-order-2",
                "owned_market_ref": "",
            }
        )
        self.assertIs(inferred, condition)

    def test_should_exit_early_requires_terminal_market_state(self):
        audit = self._make_audit()
        condition = ConditionAuditState(
            condition_id="0xcond1",
            owned_market_ref="0xcond1|2026-05-27T04:55:00.000Z|na",
            market_slug="btc-updown-5m-1",
            question="Question",
            dir=pathlib.Path("/tmp/market_1"),
            tokens=["tok-yes", "tok-no"],
            first_event_type="lifecycle_phase_transition",
            first_seen_utc="2026-05-27T04:54:45.000Z",
        )
        audit.conditions[condition.condition_id] = condition
        self.assertFalse(audit.should_exit_early(idle_sec=1.0))
        audit.market_terminal_reason = "transition_scan"
        audit.market_terminal_monotonic = time.monotonic() - 2.0
        self.assertTrue(audit.should_exit_early(idle_sec=1.0))
        self.assertFalse(audit.should_exit_early(idle_sec=0.0))

    def test_lifecycle_transition_scan_closes_active_condition_without_waiting_for_404(self):
        audit = self._make_audit()
        condition = ConditionAuditState(
            condition_id="0xcond1",
            owned_market_ref="0xcond1|2026-05-27T04:55:00.000Z|na",
            market_slug="btc-updown-5m-1",
            question="Question",
            dir=pathlib.Path("/tmp/market_1"),
            tokens=["tok-yes", "tok-no"],
            first_event_type="lifecycle_phase_transition",
            first_seen_utc="2026-05-27T04:54:45.000Z",
        )
        audit.conditions[condition.condition_id] = condition
        with mock.patch.object(audit, "_capture_condition_snapshot") as capture_mock:
            audit._handle_event(
                {
                    "event_type": "lifecycle_phase_transition",
                    "lifecycle_phase": "scan",
                    "owned_market_ref": "",
                    "ts_event_utc": "2026-05-27T04:55:01.000Z",
                },
                initial_backfill=False,
            )
        capture_mock.assert_called_once()
        self.assertTrue(condition.closed)
        self.assertEqual(condition.close_reason, "bro_transition_scan")
        self.assertEqual(condition.close_detected_utc, "2026-05-27T04:55:01.000Z")
        self.assertEqual(audit.market_terminal_reason, "bro_transition_scan")

    def test_parse_args_supports_no_build_fast_path(self):
        args = parse_args(["--active-minutes", "20", "--wait-sec", "25", "--no-build"])
        self.assertEqual(args.active_minutes, 20)
        self.assertEqual(args.wait_sec, 25)
        self.assertFalse(args.build_images)

    def test_launch_broctl_passes_no_build_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = pathlib.Path(tmp)
            fake_proc = object()
            with mock.patch("scripts.paper_live_market_audit.subprocess.Popen", return_value=fake_proc) as popen_mock:
                proc, stdout_path = launch_broctl(
                    active_minutes=20,
                    wait_sec=25,
                    out_dir=out_dir,
                    do_build=False,
                )
            self.assertIs(proc, fake_proc)
            self.assertEqual(stdout_path, out_dir / "broctl_stdout.log")
            cmd = popen_mock.call_args.kwargs.get("args") or popen_mock.call_args.args[0]
            self.assertEqual(
                cmd,
                ["broctl", "paper", "--", "--active-minutes", "20", "--wait-sec", "25", "--no-build"],
            )

    def test_run_audit_does_not_terminate_launched_proc_on_idle_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            manifest_path = root / "run_manifest_run-1.json"
            manifest_path.write_text('{"run_id":"run-1"}\n', encoding="utf-8")
            fake_proc = mock.Mock()
            poll_values = iter([None, 0])

            def _poll() -> int | None:
                return next(poll_values, 0)

            fake_proc.poll.side_effect = _poll
            fake_audit = mock.Mock()
            fake_audit.should_exit_early.return_value = True
            fake_audit.conditions = {}
            fake_audit.events_tail.matched_count = 0
            fake_audit.status_tail.matched_count = 0
            fake_audit.errors_tail.matched_count = 0
            with mock.patch("scripts.paper_live_market_audit.PaperLiveMarketAudit", return_value=fake_audit):
                with mock.patch("scripts.paper_live_market_audit.time.sleep", return_value=None):
                    out_dir = run_audit(
                        active_minutes=20,
                        wait_sec=25,
                        log_dir=root,
                        out_root=root / "out",
                        snapshot_cadence_sec=5.0,
                        run_id="run-1",
                        proc=fake_proc,
                        stdout_path=None,
                        manifest_path=manifest_path,
                    )
            self.assertTrue((out_dir / "final_summary.json").exists())
            fake_proc.terminate.assert_not_called()
            fake_proc.kill.assert_not_called()
            self.assertGreaterEqual(fake_audit.poll.call_count, 2)


if __name__ == "__main__":
    unittest.main()
