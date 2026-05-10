import json
import tempfile
import unittest
from pathlib import Path

from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.forensic_snapshot import run_snapshot


class ForensicSnapshotTests(unittest.TestCase):
    def test_run_snapshot_aggregates_run_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            status = root / "status_2026-01-01.jsonl"
            events = root / "events_2026-01-01.jsonl"
            run_id = "r1"

            status_rows = [
                {
                    "run_id": run_id,
                    "ts_utc": "2026-01-01T00:00:00.000Z",
                    "kill_switch": False,
                    "external_guard": {"active": False},
                    "chainlink": {"connected": True, "last_tick_age_sec": 0.4},
                    "book_feed": {"connected": True, "last_msg_age_sec": 1.2},
                    "counter.fills": 2,
                    "counter.orders_submitted": 3,
                    "counter.orders_canceled": 1,
                    "counter.risk_rejects": 4,
                    "counter.risk_reject_position_cap": 2,
                    "counter.risk_reject_notional_cap": 1,
                    "counter.risk_reject_stale_book": 1,
                    "gauge.total_pnl": 1.0,
                    "gauge.taker_mode_active": 0.0,
                    "gauge.taker_token_count": 0.0,
                    "gauge.taker_lag_verified_token_count": 0.0,
                    "gauge.latency_verifier_state": 2.0,
                    "gauge.latency_verifier_sample_count": 100.0,
                    "gauge.latency_sampling_inactive_cycles": 0.0,
                },
                {
                    "run_id": run_id,
                    "ts_utc": "2026-01-01T00:01:00.000Z",
                    "kill_switch": False,
                    "external_guard": {"active": False},
                    "chainlink": {"connected": True, "last_tick_age_sec": 0.9},
                    "book_feed": {"connected": True, "last_msg_age_sec": 3.4},
                    "counter.fills": 5,
                    "counter.orders_submitted": 7,
                    "counter.orders_canceled": 6,
                    "counter.risk_rejects": 9,
                    "counter.risk_reject_position_cap": 5,
                    "counter.risk_reject_notional_cap": 3,
                    "counter.risk_reject_stale_book": 1,
                    "gauge.total_pnl": -0.5,
                    "gauge.taker_mode_active": 1.0,
                    "gauge.taker_token_count": 2.0,
                    "gauge.taker_lag_verified_token_count": 1.0,
                    "gauge.latency_verifier_state": 2.0,
                    "gauge.latency_verifier_sample_count": 150.0,
                    "gauge.latency_sampling_inactive_cycles": 0.0,
                },
            ]
            event_rows = [
                {"run_id": run_id, "event_type": "fill"},
                {"run_id": run_id, "event_type": "order_submit"},
                {"run_id": run_id, "event_type": "order_cancel"},
                {"run_id": run_id, "event_type": "taker_submit"},
                {
                    "run_id": run_id,
                    "event_type": "edge_evaluation",
                    "action_taken": "none",
                    "block_reason": "edge_below_min",
                },
            ]

            status.write_text("\n".join(json.dumps(x) for x in status_rows) + "\n", encoding="utf-8")
            events.write_text("\n".join(json.dumps(x) for x in event_rows) + "\n", encoding="utf-8")

            report = run_snapshot(root, run_id)
            self.assertEqual(report["execution"]["fills"], 5)
            self.assertEqual(report["execution"]["orders_submitted"], 7)
            self.assertEqual(report["risk"]["risk_rejects"], 9)
            self.assertEqual(report["events"]["taker_submit"], 1)
            self.assertEqual(report["edge_block_breakdown"]["edge_below_min"], 1)
            self.assertEqual(report["pnl"]["first"], 1.0)
            self.assertEqual(report["pnl"]["last"], -0.5)
            self.assertEqual(report["taker"]["taker_mode_max"], 1.0)

    def test_run_snapshot_contract_bounds_limit_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "r1"
            status = root / "status_2026-01-01.jsonl"
            events = root / "events_2026-01-01.jsonl"
            errors = root / "errors_2026-01-01.jsonl"
            manifest = root / f"run_manifest_{run_id}.json"
            contract_path = root / f"run_contract_{run_id}.json"

            manifest.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            errors.write_text("", encoding="utf-8")
            status.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2026-01-01T00:00:00.000Z",
                                "counter.orders_submitted": 1,
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "ts_utc": "2026-01-01T00:01:00.000Z",
                                "counter.orders_submitted": 9,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            events.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_id": run_id,
                                "event_type": "edge_evaluation",
                                "action_taken": "none",
                                "block_reason": "stale_book",
                                "ts_utc": "2026-01-01T00:00:00.500Z",
                            }
                        ),
                        json.dumps(
                            {
                                "run_id": run_id,
                                "event_type": "edge_evaluation",
                                "action_taken": "none",
                                "block_reason": "stale_book",
                                "ts_utc": "2026-01-01T00:01:10.000Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            contract = build_run_contract(
                session_id="sid-1",
                run_id=run_id,
                phase="validate_postrun",
                session_type="paper",
                authority_level="authoritative",
                allowed_actions=["validate_postrun"],
                manifest_path=manifest,
                log_root=root,
                state_root=root / "state.json",
                start_ts="2026-01-01T00:00:00.000Z",
                stop_ts="2026-01-01T00:00:10.000Z",
                evidence_slice_start_ts="2026-01-01T00:00:00.000Z",
                evidence_slice_end_ts="2026-01-01T00:00:10.000Z",
                status_path=str(status),
                events_path=str(events),
                errors_path=str(errors),
            )
            write_run_contract(contract_path, contract, allow_open=False)

            report = run_snapshot(
                root,
                run_id,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
            )
            self.assertEqual(report["window"]["status_rows"], 1)
            self.assertEqual(report["execution"]["orders_submitted"], 1)
            self.assertEqual(report["edge_block_breakdown"]["stale_book"], 1)
            self.assertEqual(report["run_contract_path"], str(contract_path.resolve()))


if __name__ == "__main__":
    unittest.main()
