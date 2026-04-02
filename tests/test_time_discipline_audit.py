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
            "skew_tolerance_ms": 1000.0,
            "monotonicity_rule": "status_ts_utc_non_decreasing_per_run",
        }

    def test_audit_passes_with_strict_clock_and_monotonic_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_fails_for_disabled_clock_and_non_monotonic_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_run_id_filter_avoids_cross_run_non_monotonic_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_fails_when_time_policy_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_fails_when_event_timestamp_domains_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_exempts_chainlink_subscribe_bootstrap_skew(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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

    def test_audit_fails_cross_domain_skew_for_chainlink_update(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = json.loads(json.dumps(DEFAULT_EXECUTION_CONFIG))
            cfg["sniper"].pop("max_chainlink_tick_age_sec", None)
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
