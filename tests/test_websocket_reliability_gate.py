import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.websocket_reliability_gate import run_gate


class WebsocketReliabilityGateTests(unittest.TestCase):
    def test_gate_passes_on_healthy_status_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 1.0,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T01:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 2, "last_msg_age_sec": 2.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 2,
                        "last_tick_age_sec": 2.0,
                        "queue_size": 10,
                        "dropped_ticks": 0,
                    },
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=2,
                max_book_feed_down_ratio=0.2,
                max_chainlink_down_ratio=0.2,
                max_book_feed_reconnects_per_hour=10.0,
                max_chainlink_reconnects_per_hour=10.0,
                max_book_feed_last_msg_age_sec=10.0,
                max_chainlink_last_tick_age_sec=10.0,
                max_book_feed_last_msg_age_spike_rows=0,
                max_chainlink_last_tick_age_spike_rows=0,
                max_book_feed_last_msg_age_spike_ratio=0.0,
                max_chainlink_last_tick_age_spike_ratio=0.0,
                max_book_feed_last_msg_age_p95_sec=10.0,
                max_chainlink_last_tick_age_p95_sec=10.0,
                max_chainlink_dropped_ticks=0.0,
                max_chainlink_queue_size=1000.0,
            )
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_gate_prefers_steady_reconnect_counter_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {
                        "connected": True,
                        "reconnects": 12,  # startup + steady total
                        "reconnects_startup": 12,
                        "reconnects_steady": 0,
                        "last_msg_age_sec": 1.0,
                    },
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 1.0, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T01:00:00Z",
                    "book_feed": {
                        "connected": True,
                        "reconnects": 12,
                        "reconnects_startup": 12,
                        "reconnects_steady": 0,
                        "last_msg_age_sec": 1.0,
                    },
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 1.0, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=2,
                max_book_feed_down_ratio=0.2,
                max_chainlink_down_ratio=0.2,
                max_book_feed_reconnects_per_hour=5.0,
                max_chainlink_reconnects_per_hour=10.0,
                max_book_feed_last_msg_age_sec=10.0,
                max_chainlink_last_tick_age_sec=10.0,
                max_book_feed_last_msg_age_spike_rows=0,
                max_chainlink_last_tick_age_spike_rows=0,
                max_book_feed_last_msg_age_spike_ratio=0.0,
                max_chainlink_last_tick_age_spike_ratio=0.0,
                max_book_feed_last_msg_age_p95_sec=10.0,
                max_chainlink_last_tick_age_p95_sec=10.0,
                max_chainlink_dropped_ticks=0.0,
                max_chainlink_queue_size=1000.0,
            )
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_gate_allows_null_age_fields_when_feed_disconnected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": False, "reconnects": 0, "last_msg_age_sec": None},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 0,
                        "last_tick_age_sec": None,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:05:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 1.0,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=2,
                max_book_feed_down_ratio=1.0,
                max_chainlink_down_ratio=1.0,
                max_book_feed_reconnects_per_hour=1000000.0,
                max_chainlink_reconnects_per_hour=1000000.0,
                max_book_feed_last_msg_age_sec=10.0,
                max_chainlink_last_tick_age_sec=10.0,
                max_book_feed_last_msg_age_spike_rows=1000,
                max_chainlink_last_tick_age_spike_rows=1000,
                max_book_feed_last_msg_age_spike_ratio=1.0,
                max_chainlink_last_tick_age_spike_ratio=1.0,
                max_book_feed_last_msg_age_p95_sec=10.0,
                max_chainlink_last_tick_age_p95_sec=10.0,
                max_chainlink_dropped_ticks=0.0,
                max_chainlink_queue_size=1000.0,
            )
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_gate_fails_on_down_ratio_and_drops(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": False, "reconnects": 0, "last_msg_age_sec": 20.0},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 0,
                        "last_tick_age_sec": 20.0,
                        "queue_size": 2000,
                        "dropped_ticks": 5,
                    },
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:10:00Z",
                    "book_feed": {"connected": False, "reconnects": 100, "last_msg_age_sec": 30.0},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 100,
                        "last_tick_age_sec": 40.0,
                        "queue_size": 3000,
                        "dropped_ticks": 7,
                    },
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=1,
                max_book_feed_down_ratio=0.2,
                max_chainlink_down_ratio=0.2,
                max_book_feed_reconnects_per_hour=10.0,
                max_chainlink_reconnects_per_hour=10.0,
                max_book_feed_last_msg_age_sec=10.0,
                max_chainlink_last_tick_age_sec=10.0,
                max_book_feed_last_msg_age_spike_rows=0,
                max_chainlink_last_tick_age_spike_rows=0,
                max_book_feed_last_msg_age_spike_ratio=0.0,
                max_chainlink_last_tick_age_spike_ratio=0.0,
                max_book_feed_last_msg_age_p95_sec=10.0,
                max_chainlink_last_tick_age_p95_sec=10.0,
                max_chainlink_dropped_ticks=0.0,
                max_chainlink_queue_size=1000.0,
            )
            self.assertFalse(out["ok"])
            text = "\n".join(out["findings"])
            self.assertIn("websocket_slo_book_feed_down_ratio_too_high", text)
            self.assertIn("websocket_slo_chainlink_dropped_ticks_too_high", text)
            self.assertIn("BRO-2201", out["error_codes"])

    def test_gate_allows_configured_single_age_spike(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 1.0, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:05:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 20.0},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 1.0, "queue_size": 0, "dropped_ticks": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:10:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 2.0},
                    "chainlink": {"connected": True, "reconnects": 0, "last_tick_age_sec": 2.0, "queue_size": 0, "dropped_ticks": 0},
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=1,
                max_book_feed_down_ratio=0.2,
                max_chainlink_down_ratio=0.2,
                max_book_feed_reconnects_per_hour=10.0,
                max_chainlink_reconnects_per_hour=10.0,
                max_book_feed_last_msg_age_sec=10.0,
                max_chainlink_last_tick_age_sec=10.0,
                max_book_feed_last_msg_age_spike_rows=1,
                max_chainlink_last_tick_age_spike_rows=0,
                max_book_feed_last_msg_age_spike_ratio=1.0,
                max_chainlink_last_tick_age_spike_ratio=0.0,
                max_book_feed_last_msg_age_p95_sec=25.0,
                max_chainlink_last_tick_age_p95_sec=10.0,
                max_chainlink_dropped_ticks=0.0,
                max_chainlink_queue_size=1000.0,
            )
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_gate_respects_max_lines_per_file_tail_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r-target"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 1.0,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                }
            ]
            for idx in range(40):
                rows.append(
                    {
                        "run_id": "other",
                        "ts_utc": f"2099-01-01T00:10:{idx:02d}Z",
                        "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                        "chainlink": {
                            "connected": True,
                            "reconnects": 0,
                            "last_tick_age_sec": 1.0,
                            "queue_size": 0,
                            "dropped_ticks": 0,
                        },
                    }
                )
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=1,
                max_book_feed_down_ratio=1.0,
                max_chainlink_down_ratio=1.0,
                max_book_feed_reconnects_per_hour=1000000.0,
                max_chainlink_reconnects_per_hour=1000000.0,
                max_book_feed_last_msg_age_sec=1000000.0,
                max_chainlink_last_tick_age_sec=1000000.0,
                max_book_feed_last_msg_age_spike_rows=1000,
                max_chainlink_last_tick_age_spike_rows=1000,
                max_book_feed_last_msg_age_spike_ratio=1.0,
                max_chainlink_last_tick_age_spike_ratio=1.0,
                max_book_feed_last_msg_age_p95_sec=1000000.0,
                max_chainlink_last_tick_age_p95_sec=1000000.0,
                max_chainlink_dropped_ticks=1000000.0,
                max_chainlink_queue_size=1000000.0,
                max_lines_per_file=10,
            )
            self.assertFalse(out["ok"])
            self.assertTrue(
                any(str(x).startswith("websocket_slo_status_rows_too_few:") for x in out["findings"]),
                msg=out["findings"],
            )

    def test_gate_allows_full_scan_when_max_lines_per_file_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r-target"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 1.0,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                }
            ]
            for idx in range(40):
                rows.append(
                    {
                        "run_id": "other",
                        "ts_utc": f"2099-01-01T00:10:{idx:02d}Z",
                        "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                        "chainlink": {
                            "connected": True,
                            "reconnects": 0,
                            "last_tick_age_sec": 1.0,
                            "queue_size": 0,
                            "dropped_ticks": 0,
                        },
                    }
                )
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=1,
                max_book_feed_down_ratio=1.0,
                max_chainlink_down_ratio=1.0,
                max_book_feed_reconnects_per_hour=1000000.0,
                max_chainlink_reconnects_per_hour=1000000.0,
                max_book_feed_last_msg_age_sec=1000000.0,
                max_chainlink_last_tick_age_sec=1000000.0,
                max_book_feed_last_msg_age_spike_rows=1000,
                max_chainlink_last_tick_age_spike_rows=1000,
                max_book_feed_last_msg_age_spike_ratio=1.0,
                max_chainlink_last_tick_age_spike_ratio=1.0,
                max_book_feed_last_msg_age_p95_sec=1000000.0,
                max_chainlink_last_tick_age_p95_sec=1000000.0,
                max_chainlink_dropped_ticks=1000000.0,
                max_chainlink_queue_size=1000000.0,
                max_lines_per_file=0,
            )
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_candidate_search_is_lazy_when_rows_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 1.0},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 1.0,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                }
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            with patch("scripts.websocket_reliability_gate.candidate_run_log_dirs") as candidate_mock:
                out = run_gate(
                    log_dir=root,
                    run_id=run_id,
                    min_status_rows=1,
                    max_book_feed_down_ratio=1.0,
                    max_chainlink_down_ratio=1.0,
                    max_book_feed_reconnects_per_hour=1000000.0,
                    max_chainlink_reconnects_per_hour=1000000.0,
                    max_book_feed_last_msg_age_sec=1000000.0,
                    max_chainlink_last_tick_age_sec=1000000.0,
                    max_book_feed_last_msg_age_spike_rows=1000,
                    max_chainlink_last_tick_age_spike_rows=1000,
                    max_book_feed_last_msg_age_spike_ratio=1.0,
                    max_chainlink_last_tick_age_spike_ratio=1.0,
                    max_book_feed_last_msg_age_p95_sec=1000000.0,
                    max_chainlink_last_tick_age_p95_sec=1000000.0,
                    max_chainlink_dropped_ticks=1000000.0,
                    max_chainlink_queue_size=1000000.0,
                )
            self.assertTrue(out["ok"], msg=out["findings"])
            candidate_mock.assert_not_called()

    def test_candidate_search_runs_when_rows_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            with patch(
                "scripts.websocket_reliability_gate.candidate_run_log_dirs",
                return_value=[str((root / "other").resolve())],
            ) as candidate_mock:
                out = run_gate(
                    log_dir=root,
                    run_id=run_id,
                    min_status_rows=1,
                    max_book_feed_down_ratio=1.0,
                    max_chainlink_down_ratio=1.0,
                    max_book_feed_reconnects_per_hour=1000000.0,
                    max_chainlink_reconnects_per_hour=1000000.0,
                    max_book_feed_last_msg_age_sec=1000000.0,
                    max_chainlink_last_tick_age_sec=1000000.0,
                    max_book_feed_last_msg_age_spike_rows=1000,
                    max_chainlink_last_tick_age_spike_rows=1000,
                    max_book_feed_last_msg_age_spike_ratio=1.0,
                    max_chainlink_last_tick_age_spike_ratio=1.0,
                    max_book_feed_last_msg_age_p95_sec=1000000.0,
                    max_chainlink_last_tick_age_p95_sec=1000000.0,
                    max_chainlink_dropped_ticks=1000000.0,
                    max_chainlink_queue_size=1000000.0,
                )
            self.assertFalse(out["ok"])
            self.assertIn("websocket_slo_run_context_candidate_log_dirs_present", out["warnings"])
            candidate_mock.assert_called_once()

    def test_gate_fails_closed_on_missing_required_numeric_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:00:00Z",
                    "book_feed": {"connected": True, "reconnects": 0},
                    "chainlink": {"connected": True, "reconnects": 0},
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2099-01-01T00:10:00Z",
                    "book_feed": {"connected": True, "last_msg_age_sec": "nan"},
                    "chainlink": {
                        "connected": True,
                        "last_tick_age_sec": "invalid",
                        "queue_size": "bad",
                        "dropped_ticks": "bad",
                    },
                },
            ]
            (root / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = run_gate(
                log_dir=root,
                run_id=run_id,
                min_status_rows=1,
                max_book_feed_down_ratio=1.0,
                max_chainlink_down_ratio=1.0,
                max_book_feed_reconnects_per_hour=1000000.0,
                max_chainlink_reconnects_per_hour=1000000.0,
                max_book_feed_last_msg_age_sec=1000000.0,
                max_chainlink_last_tick_age_sec=1000000.0,
                max_book_feed_last_msg_age_spike_rows=1000,
                max_chainlink_last_tick_age_spike_rows=1000,
                max_book_feed_last_msg_age_spike_ratio=1.0,
                max_chainlink_last_tick_age_spike_ratio=1.0,
                max_book_feed_last_msg_age_p95_sec=1000000.0,
                max_chainlink_last_tick_age_p95_sec=1000000.0,
                max_chainlink_dropped_ticks=1000000.0,
                max_chainlink_queue_size=1000000.0,
            )
            self.assertFalse(out["ok"])
            findings = "\n".join(out["findings"])
            self.assertIn("websocket_slo_book_feed_reconnects_missing_or_invalid_rows", findings)
            self.assertIn("websocket_slo_chainlink_reconnects_missing_or_invalid_rows", findings)
            self.assertIn("websocket_slo_book_feed_last_msg_age_missing_or_invalid_rows", findings)
            self.assertIn("websocket_slo_chainlink_last_tick_age_missing_or_invalid_rows", findings)
            self.assertIn("websocket_slo_chainlink_queue_size_missing_or_invalid_rows", findings)
            self.assertIn("websocket_slo_chainlink_dropped_ticks_missing_or_invalid_rows", findings)


if __name__ == "__main__":
    unittest.main()
