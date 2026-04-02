import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.websocket_hardening_audit import run_audit


class WebsocketHardeningAuditTests(unittest.TestCase):
    @staticmethod
    def _ordering_policy_payload() -> dict:
        return {
            "primary": "source_timestamp",
            "fallback": "receive_monotonic",
            "tolerance_ms": 0,
            "tie_breaker": "same_timestamp_price_revision",
        }

    @staticmethod
    def _ordering_class_counts(**overrides: int) -> dict:
        payload = {
            "ordered": 1,
            "out_of_order": 0,
            "duplicate": 0,
            "revision": 0,
            "missing_source_time": 0,
        }
        payload.update({k: int(v) for k, v in overrides.items()})
        return payload

    def _write_cfg(self, root: Path) -> Path:
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        # Canonical doctrine fixtures must not set both doctrine and legacy sniper freshness keys.
        cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
        cfg["targets"]["token_ids"] = ["tok1"]
        cfg["chainlink"]["enabled"] = True
        cfg["market_data"]["ws"]["enabled"] = True
        path = root / "execution_config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    def _write_contract(
        self,
        *,
        root: Path,
        run_id: str,
        status_path: Path,
    ) -> Path:
        contract_path = root / f"run_contract_{run_id}.json"
        payload = build_run_contract(
            session_id="sess-ws-audit",
            run_id=run_id,
            phase="validate_postrun",
            session_type="paper",
            authority_level="authoritative",
            allowed_actions=["validate_postrun"],
            manifest_path=root / f"run_manifest_{run_id}.json",
            log_root=root,
            state_root=root,
            start_ts="2026-01-01T00:00:00Z",
            stop_ts="2026-01-01T00:00:10Z",
            evidence_slice_start_ts="2026-01-01T00:00:00Z",
            evidence_slice_end_ts="2026-01-01T00:00:02Z",
            status_path=str(status_path.resolve()),
            events_path=str((root / "events_2026-01-01.jsonl").resolve()),
            errors_path=str((root / "errors_2026-01-01.jsonl").resolve()),
            status_slice_path=str(status_path.resolve()),
            events_slice_path="",
            errors_slice_path="",
        )
        write_run_contract(contract_path, payload, allow_open=False)
        return contract_path

    def test_audit_passes_default_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(Path(td))
            result = run_audit(config_path=cfg_path)
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_audit_flags_invalid_ws_url_and_timing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            cfg["market_data"]["ws"]["url"] = "ws://insecure.local/ws"
            cfg["market_data"]["ws"]["heartbeat_timeout_sec"] = 5
            cfg["market_data"]["ws"]["ping_interval_sec"] = 5
            cfg["market_data"]["ws"]["stale_after_sec"] = 6
            cfg["chainlink"]["max_queue_size"] = 500
            cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            result = run_audit(config_path=cfg_path)
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("market_data.ws:url_not_wss", text)
        self.assertIn("market_data.ws:ping_interval_ge_heartbeat", text)
        self.assertIn("market_data.ws:stale_after_ge_heartbeat", text)
        self.assertIn("chainlink:max_queue_size_too_low_or_invalid", text)

    def test_audit_flags_runtime_websocket_reliability_breaches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            run_id = "run-ws-1"
            rows = [
                {
                    "ts_utc": "2026-01-01T00:00:00.000Z",
                    "run_id": run_id,
                    "book_feed": {"connected": False, "reconnects": 0, "last_msg_age_sec": 30},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 0,
                        "last_tick_age_sec": 30,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                        "ordering_policy": self._ordering_policy_payload(),
                        "ordering_classification_counts": self._ordering_class_counts(out_of_order=1),
                    },
                },
                {
                    "ts_utc": "2026-01-01T00:01:00.000Z",
                    "run_id": run_id,
                    "book_feed": {"connected": False, "reconnects": 50, "last_msg_age_sec": 30},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 50,
                        "last_tick_age_sec": 40,
                        "queue_size": 20000,
                        "dropped_ticks": 10,
                        "ordering_policy": self._ordering_policy_payload(),
                        "ordering_classification_counts": self._ordering_class_counts(
                            ordered=2,
                            out_of_order=2,
                            duplicate=1,
                            revision=1,
                            missing_source_time=1,
                        ),
                    },
                },
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_audit(config_path=cfg_path, log_dir=root, run_id=run_id)
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("websocket_evidence_book_feed_down_ratio_too_high", text)
        self.assertIn("websocket_evidence_chainlink_down_ratio_too_high", text)
        self.assertIn("websocket_evidence_book_feed_reconnects_per_hour_too_high", text)
        self.assertIn("websocket_evidence_chainlink_reconnects_per_hour_too_high", text)
        self.assertIn("websocket_evidence_chainlink_dropped_ticks_too_high", text)
        self.assertIn("websocket_evidence_chainlink_queue_size_too_high", text)
        self.assertIn("BRO-2201", result.get("error_codes", []))

    def test_audit_supports_run_contract_bounded_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            run_id = "run-ws-1"
            status_path = root / "status_2026-01-01.jsonl"
            rows = [
                {
                    "ts_utc": "2026-01-01T00:00:00.500Z",
                    "run_id": run_id,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.1},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 0.2,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                        "ordering_policy": self._ordering_policy_payload(),
                        "ordering_classification_counts": self._ordering_class_counts(ordered=5),
                    },
                },
                {
                    "ts_utc": "2026-01-01T00:00:00.750Z",
                    "run_id": "run-other",
                    "book_feed": {"connected": False, "reconnects": 99, "last_msg_age_sec": 99},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 99,
                        "last_tick_age_sec": 99,
                        "queue_size": 99999,
                        "dropped_ticks": 999,
                        "ordering_policy": self._ordering_policy_payload(),
                        "ordering_classification_counts": self._ordering_class_counts(ordered=9),
                    },
                },
                {
                    "ts_utc": "2026-01-01T00:00:05.000Z",
                    "run_id": run_id,
                    "book_feed": {"connected": False, "reconnects": 99, "last_msg_age_sec": 99},
                    "chainlink": {
                        "connected": False,
                        "reconnects": 99,
                        "last_tick_age_sec": 99,
                        "queue_size": 99999,
                        "dropped_ticks": 999,
                        "ordering_policy": self._ordering_policy_payload(),
                        "ordering_classification_counts": self._ordering_class_counts(ordered=9),
                    },
                },
            ]
            status_path.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            contract_path = self._write_contract(root=root, run_id=run_id, status_path=status_path)
            result = run_audit(
                config_path=cfg_path,
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
        self.assertTrue(result["ok"], msg=str(result["findings"]))
        self.assertEqual(str(result.get("run_id") or ""), run_id)
        self.assertEqual(str(result.get("run_id_resolution") or ""), "contract")
        self.assertEqual(str(result.get("session_phase") or ""), "validate_postrun")
        self.assertEqual(str(result.get("run_contract_path") or ""), str(contract_path.resolve()))
        self.assertEqual(float(result.get("evidence", {}).get("status_rows", 0.0)), 1.0)
        self.assertEqual(int(result.get("evidence", {}).get("ordering_policy_missing_rows", -1)), 0)
        self.assertEqual(int(result.get("evidence", {}).get("ordering_classification_missing_rows", -1)), 0)

    def test_audit_fails_closed_on_run_contract_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            run_id = "run-ws-1"
            status_path = root / "status_2026-01-01.jsonl"
            status_path.write_text("", encoding="utf-8")
            contract_path = self._write_contract(root=root, run_id=run_id, status_path=status_path)
            result = run_audit(
                config_path=cfg_path,
                log_dir=root,
                run_id="run-ws-other",
                run_contract_path=contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
        self.assertFalse(result["ok"])
        findings = "\n".join(str(x) for x in result.get("findings", []))
        self.assertIn("run_contract_run_id_mismatch", findings)

    def test_audit_enforces_validation_phase(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            with self.assertRaises(ValueError):
                run_audit(config_path=cfg_path, session_phase="active")

    def test_audit_requires_run_id_when_log_dir_is_provided_without_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            result = run_audit(config_path=cfg_path, log_dir=root)
        self.assertFalse(result["ok"])
        findings = "\n".join(str(x) for x in result.get("findings", []))
        self.assertIn("websocket_hardening_run_id_required_when_log_dir_provided", findings)

    def test_audit_fails_when_ordering_policy_or_classification_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = self._write_cfg(root)
            run_id = "run-missing-ordering"
            rows = [
                {
                    "ts_utc": "2026-01-01T00:00:00.000Z",
                    "run_id": run_id,
                    "book_feed": {"connected": True, "reconnects": 0, "last_msg_age_sec": 0.1},
                    "chainlink": {
                        "connected": True,
                        "reconnects": 0,
                        "last_tick_age_sec": 0.2,
                        "queue_size": 0,
                        "dropped_ticks": 0,
                    },
                }
            ]
            (root / "status_2026-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            result = run_audit(config_path=cfg_path, log_dir=root, run_id=run_id)
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("websocket_ordering_policy_missing_rows", text)
        self.assertIn("websocket_ordering_classification_missing_rows", text)


if __name__ == "__main__":
    unittest.main()
