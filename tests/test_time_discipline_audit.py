import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.canonical_authority import CAPABILITY_VALIDATE_POSTRUN
from prodesk.config import DEFAULT_EXECUTION_CONFIG
from prodesk.run_contract import build_run_contract, write_run_contract
from scripts.time_discipline_audit import run_audit


class TimeDisciplineAuditTests(unittest.TestCase):
    @staticmethod
    def _time_policy() -> dict:
        return {
            "source_of_truth": "utc_wall_clock",
            "fallback_logic": "source_ts_then_receive_ts_then_event_ts",
            "skew_tolerance_ms": 120.0,
            "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
        }

    @staticmethod
    def _write_host_time_sync_artifacts(
        *,
        log_dir: Path,
        session_id: str,
        run_id: str,
        sample_count: int = 1,
        offset_ms: float = 2.5,
        jitter_ms: float = 1.5,
        root_distance_ms: float = 36.0,
        stratum: int = 2,
    ) -> None:
        report_root = log_dir / "sessions" / session_id / "reports"
        report_root.mkdir(parents=True, exist_ok=True)
        base_payload = {
            "session_id": session_id,
            "run_id": run_id,
            "available": True,
            "clock_state": "synced",
            "system_clock_synchronized": True,
            "ntp_service_active": True,
            "stratum": int(stratum),
            "offset_ms": float(offset_ms),
            "jitter_ms": float(jitter_ms),
            "root_distance_ms": float(root_distance_ms),
        }
        start_payload = dict(base_payload)
        start_payload["phase"] = "active_start"
        stop_payload = dict(base_payload)
        stop_payload["phase"] = "active_stop"
        (report_root / "host_time_sync_active_start.json").write_text(
            json.dumps(start_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (report_root / "host_time_sync_active_stop.json").write_text(
            json.dumps(stop_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sample_rows = []
        for idx in range(sample_count):
            sample_payload = dict(base_payload)
            sample_payload["phase"] = "active_sample"
            sample_payload["elapsed_active_sec"] = float((idx + 1) * 60)
            sample_rows.append(sample_payload)
        (report_root / "host_time_sync_active_samples.jsonl").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in sample_rows) + ("\n" if sample_rows else ""),
            encoding="utf-8",
        )

    def test_audit_passes_with_strict_clock_and_monotonic_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 1.5
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=3,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertEqual(int(result.get("warning_count") or 0), 0)
            self.assertIn("timing_watchboard", result)

    def test_audit_fails_for_disabled_clock_and_non_monotonic_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = False
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "time_policy": self._time_policy(),
                },
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.5,
                max_status_age_sec=30.0,
                min_status_rows=1,
            )
            self.assertFalse(result["ok"])
            findings = " ".join(result["findings"])
            self.assertIn("preflight_clock_sync_disabled", findings)
            self.assertIn("status_ts_non_monotonic_rows", findings)

    def test_audit_emits_warn_only_timing_watchboard_signals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["lifecycle"]["phase"]["taker_window_open_sec"] = 15.0
            cfg["lifecycle"]["phase"]["maker_window_close_sec"] = 15.0
            cfg["risk"]["min_sec_to_expiry_for_new_exposure_by_lane"]["maker"] = 15.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                    "book_feed": {"last_msg_age_sec": 9.5},
                    "chainlink": {"last_tick_age_sec": 24.5},
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                    "book_feed": {"last_msg_age_sec": 9.4},
                    "chainlink": {"last_tick_age_sec": 24.0},
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "order_submit",
                    "run_id": "r1",
                    "submission_state": "accepted",
                    "submission_lane": "maker",
                    "decision_reference_ts_utc": (now - dt.timedelta(seconds=1.25)).isoformat().replace("+00:00", "Z"),
                    "decision_to_submit_latency_ms": 12.0,
                    "sec_to_expiry": 18.0,
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=1.05)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1.2)).isoformat().replace("+00:00", "Z"),
                    "maker_market_viability": {
                        "sec_to_expiry": 18.0,
                        "maker_phase_allowed": True,
                        "maker_gate_open": True,
                        "market_reference_mode": "direct_midpoint",
                        "market_reference_class": "authoritative",
                    },
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            contract_payload = build_run_contract(
                session_id="sid-watchboard",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                stop_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                evidence_slice_start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                evidence_slice_end_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(
                log_dir=log_dir,
                session_id="sid-watchboard",
                run_id="r1",
                sample_count=2,
                offset_ms=7.8,
                jitter_ms=7.7,
                root_distance_ms=74.0,
            )

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=0.25,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertGreaterEqual(int(result.get("warning_count") or 0), 1)
            self.assertNotIn(
                "timing_watch_duplicate_selection_gate_timing_owner_active",
                result.get("warnings", []),
            )
            self.assertTrue(
                any(str(x).startswith("timing_watch_host_jitter_warn_band:") for x in result.get("warnings", [])),
                msg=result.get("warnings", []),
            )
            board = result.get("timing_watchboard", {})
            self.assertTrue(board.get("ownership_entry_authority", {}).get("enabled"))
            self.assertAlmostEqual(
                float(board.get("ownership_entry_authority", {}).get("max_sec_to_expiry") or 0.0),
                90.0,
                places=9,
            )
            self.assertAlmostEqual(
                float(board.get("ownership_entry_authority", {}).get("min_market_age_sec") or 0.0),
                60.0,
                places=9,
            )
            self.assertFalse(board.get("maker_timing_authority", {}).get("selection_gate_timing_duplicate_owner_active"))
            self.assertAlmostEqual(
                float(board.get("maker_timing_authority", {}).get("timing_gate_min_sec_to_expiry") or 0.0),
                15.0,
                places=9,
            )
            self.assertAlmostEqual(
                float(board.get("maker_timing_authority", {}).get("timing_gate_max_sec_to_expiry") or 0.0),
                15.0,
                places=9,
            )
            self.assertEqual(
                float(board.get("submit_latency", {}).get("accepted_submit_latency_ms_summary", {}).get("max_ms") or 0.0),
                12.0,
            )

    def test_audit_run_id_filter_avoids_cross_run_non_monotonic_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 1.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "run_id": "other",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "other",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "target",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "target",
                    "time_policy": self._time_policy(),
                },
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="target",
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertEqual(result["checked_status_rows"], 2)
            self.assertEqual(result["non_monotonic_rows"], 0)

    def test_audit_accepts_run_contract_binding_and_resolves_run_id_from_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 1.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "run-contract"
            now = dt.datetime.now(dt.timezone.utc)
            in_slice = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
                    "run_id": run_id,
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                    "run_id": run_id,
                    "time_policy": self._time_policy(),
                },
            ]
            out_of_slice = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "run_id": run_id,
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text(
                "\n".join(json.dumps(r) for r in [*in_slice, *out_of_slice]) + "\n",
                encoding="utf-8",
            )
            status_slice_path = log_dir / "status_slice.jsonl"
            status_slice_path.write_text("\n".join(json.dumps(r) for r in in_slice) + "\n", encoding="utf-8")

            start_ts = (now - dt.timedelta(seconds=8)).isoformat().replace("+00:00", "Z")
            stop_ts = (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-contract",
                run_id=run_id,
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / f"run_manifest_{run_id}.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path="",
                errors_path="",
                status_slice_path=str(status_slice_path),
            )
            run_contract_path = log_dir / f"run_contract_{run_id}.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-contract", run_id=run_id)

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_contract_path=run_contract_path,
                session_phase="validate_postrun",
                max_lines_per_file=0,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertEqual(result["run_id_filter"], run_id)
            self.assertEqual(result["run_id_resolution"], "contract")
            self.assertTrue(bool(result["run_contract_path"]))
            self.assertEqual(result["checked_status_rows"], 2)
            self.assertEqual(result["contract_authority_level"], "authoritative")

    def test_observational_contract_does_not_require_host_time_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            contract_payload = build_run_contract(
                session_id="sid-observational",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="observational",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                stop_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                evidence_slice_start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                evidence_slice_end_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                status_path=str(status_path),
                events_path="",
                errors_path="",
                status_slice_path=str(status_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=0.25,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            self.assertEqual(result["contract_authority_level"], "observational")
            self.assertEqual(result["host_time_sync"], {})

    def test_audit_fails_when_time_policy_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 1.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            rows = [
                {"ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"), "run_id": "r1"},
                {"ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"), "run_id": "r1"},
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            status_path = log_dir / "status_2099-01-01.jsonl"
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("", encoding="utf-8")
            start_ts = (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            stop_ts = (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-missing-policy",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-missing-policy", run_id="r1")
            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertFalse(result["ok"])
            self.assertIn("time_policy_missing_rows", " ".join(result["findings"]))

    def test_audit_fails_when_accepted_maker_submit_missing_timing_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "order_submit",
                    "run_id": "r1",
                    "submission_state": "accepted",
                    "submission_lane": "maker",
                    "decision_reference_ts_utc": (now - dt.timedelta(seconds=1.4)).isoformat().replace("+00:00", "Z"),
                    "decision_to_submit_latency_ms": 14.0,
                    "sec_to_expiry": 18.0,
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=1.05)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1.2)).isoformat().replace("+00:00", "Z"),
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            contract_payload = build_run_contract(
                session_id="sid-maker-context",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                stop_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                evidence_slice_start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                evidence_slice_end_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-maker-context", run_id="r1")

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=0.25,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertFalse(result["ok"])
            self.assertIn("maker_timing_rows_missing_context:1/1", " ".join(result["findings"]))

    def test_audit_fails_when_event_timestamp_domains_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 2.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            (log_dir / "status_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in status_rows) + "\n",
                encoding="utf-8",
            )
            event_rows = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "r1",
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": None,
                    "ts_source_utc": None,
                    # ts_decision_utc intentionally missing
                }
            ]
            (log_dir / "events_2099-01-01.jsonl").write_text(
                "\n".join(json.dumps(r) for r in event_rows) + "\n",
                encoding="utf-8",
            )
            status_path = log_dir / "status_2099-01-01.jsonl"
            events_path = log_dir / "events_2099-01-01.jsonl"
            start_ts = (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            stop_ts = (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-missing-event-domain",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(
                log_dir=log_dir,
                session_id="sid-missing-event-domain",
                run_id="r1",
            )
            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertFalse(result["ok"])
            joined = " ".join(str(x) for x in result.get("findings", []))
            self.assertIn("event_timestamp_domain_fields_missing_rows", joined)

    def test_critical_timing_evidence_uses_canonical_taker_keys_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 2.0
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "taker_decision",
                    "run_id": "r1",
                    "sec_to_expiry": 6.0,
                    "timing_window_class": "extreme_only_final_window",
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=1.05)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1.2)).isoformat().replace("+00:00", "Z"),
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")
            contract_payload = build_run_contract(
                session_id="sid-canonical-taker-keys",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                stop_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                evidence_slice_start_ts=(now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                evidence_slice_end_ts=(now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(
                log_dir=log_dir,
                session_id="sid-canonical-taker-keys",
                run_id="r1",
            )

            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.0,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            evidence = result["critical_timing_evidence"]
            self.assertEqual(evidence["taker_decision_rows"], 1)
            self.assertEqual(evidence["taker_decision_missing_decision_ts_rows"], 0)
            self.assertEqual(evidence["taker_decision_missing_sec_to_expiry_rows"], 0)
            self.assertEqual(evidence["taker_decision_missing_timing_window_rows"], 0)
            for key in evidence:
                self.assertFalse(key.startswith("sniper_"))

    def test_audit_exempts_chainlink_subscribe_bootstrap_skew(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 2.5
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "chainlink_tick",
                    "run_id": "r1",
                    "msg_type": "subscribe",
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=61)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            start_ts = (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            stop_ts = now.isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-subscribe-skew",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-subscribe-skew", run_id="r1")
            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.5,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            domain = result["event_timestamp_domain_audit"]
            self.assertEqual(domain["cross_domain_skew_exempt_rows"], 1)
            self.assertEqual(domain["cross_domain_skew_checked_rows"], 0)
            self.assertEqual(domain["cross_domain_skew_exceeded_rows"], 0)

    def test_audit_exempts_cross_domain_skew_for_chainlink_update(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 2.5
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "chainlink_tick",
                    "run_id": "r1",
                    "msg_type": "update",
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=61)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            start_ts = (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            stop_ts = now.isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-update-skew",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-update-skew", run_id="r1")
            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.5,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertTrue(result["ok"], msg=result["findings"])
            domain = result["event_timestamp_domain_audit"]
            self.assertEqual(domain["cross_domain_skew_exempt_rows"], 1)
            self.assertEqual(domain["cross_domain_skew_checked_rows"], 0)
            self.assertEqual(domain["cross_domain_skew_exceeded_rows"], 0)

    def test_audit_fails_cross_domain_skew_for_non_chainlink_event(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["taker"].pop("max_chainlink_tick_age_sec", None)
            cfg["storage"]["log_dir"] = str(root / "logs")
            cfg["preflight"]["check_clock_sync"] = True
            cfg["preflight"]["max_clock_skew_sec"] = 2.5
            cfg["targets"]["discovery"]["enabled"] = True
            cfg_path = root / "cfg.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            log_dir = Path(cfg["storage"]["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            now = dt.datetime.now(dt.timezone.utc)
            status_rows = [
                {
                    "ts_utc": (now - dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
                {
                    "ts_utc": (now - dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "run_id": "r1",
                    "time_policy": self._time_policy(),
                },
            ]
            status_path = log_dir / "status_2099-01-01.jsonl"
            status_path.write_text("\n".join(json.dumps(r) for r in status_rows) + "\n", encoding="utf-8")
            event_rows = [
                {
                    "event_type": "edge_evaluation",
                    "run_id": "r1",
                    "msg_type": "evaluate",
                    "ts_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_event_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_receive_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "ts_source_utc": (now - dt.timedelta(seconds=61)).isoformat().replace("+00:00", "Z"),
                    "ts_decision_utc": (now - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                }
            ]
            events_path = log_dir / "events_2099-01-01.jsonl"
            events_path.write_text("\n".join(json.dumps(r) for r in event_rows) + "\n", encoding="utf-8")

            start_ts = (now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            stop_ts = now.isoformat().replace("+00:00", "Z")
            contract_payload = build_run_contract(
                session_id="sid-non-chainlink-skew",
                run_id="r1",
                phase="validate_postrun",
                session_type="paper_canonical",
                authority_level="authoritative",
                allowed_actions=[CAPABILITY_VALIDATE_POSTRUN],
                manifest_path=(log_dir / "run_manifest_r1.json"),
                log_root=log_dir,
                state_root=root,
                start_ts=start_ts,
                stop_ts=stop_ts,
                evidence_slice_start_ts=start_ts,
                evidence_slice_end_ts=stop_ts,
                status_path=str(status_path),
                events_path=str(events_path),
                errors_path="",
                status_slice_path=str(status_path),
                events_slice_path=str(events_path),
            )
            run_contract_path = log_dir / "run_contract_r1.json"
            write_run_contract(run_contract_path, contract_payload, allow_open=False)
            self._write_host_time_sync_artifacts(log_dir=log_dir, session_id="sid-non-chainlink-skew", run_id="r1")
            result = run_audit(
                config_path=cfg_path,
                max_allowed_skew_sec=2.5,
                max_status_age_sec=30.0,
                min_status_rows=2,
                run_id="r1",
                run_contract_path=run_contract_path,
            )
            self.assertFalse(result["ok"])
            joined = " ".join(str(x) for x in result.get("findings", []))
            self.assertIn("event_ts_cross_domain_skew_exceeded_rows", joined)
            domain = result["event_timestamp_domain_audit"]
            self.assertEqual(domain["cross_domain_skew_exempt_rows"], 0)
            self.assertEqual(domain["cross_domain_skew_checked_rows"], 1)
            self.assertEqual(domain["cross_domain_skew_exceeded_rows"], 1)


if __name__ == "__main__":
    unittest.main()
