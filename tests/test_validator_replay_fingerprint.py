from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validator_replay_fingerprint import compute_replay_fingerprints


class ValidatorReplayFingerprintTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_compute_replay_fingerprints_normalizes_known_volatile_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nightly_a = root / "nightly_a.json"
            nightly_b = root / "nightly_b.json"
            soak_a = root / "soak_a.json"
            soak_b = root / "soak_b.json"

            self._write_json(
                nightly_a,
                {
                    "ok": True,
                    "run_id_filter": "rid",
                    "ts_utc": "2026-03-26T01:00:00Z",
                    "findings": [],
                },
            )
            self._write_json(
                nightly_b,
                {
                    "ok": True,
                    "run_id_filter": "rid",
                    "ts_utc": "2026-03-26T01:05:00Z",
                    "findings": [],
                },
            )
            self._write_json(
                soak_a,
                {
                    "ok": True,
                    "readiness": {"report": {"ts_utc": "2026-03-26T01:00:00Z"}},
                    "integrity": {"warnings": ["latest_status_stale:12.34"]},
                },
            )
            self._write_json(
                soak_b,
                {
                    "ok": True,
                    "readiness": {"report": {"ts_utc": "2026-03-26T01:07:00Z"}},
                    "integrity": {"warnings": ["latest_status_stale:29.11"]},
                },
            )

            payload = compute_replay_fingerprints(
                [
                    ("nightly_soak_report", nightly_a, nightly_b),
                    ("soak_hardening_gate", soak_a, soak_b),
                ]
            )

            self.assertTrue(bool(payload.get("determinism_ok")))
            validators = payload.get("validators")
            self.assertIsInstance(validators, dict)
            nightly = validators.get("nightly_soak_report")
            soak = validators.get("soak_hardening_gate")
            self.assertIsInstance(nightly, dict)
            self.assertIsInstance(soak, dict)
            self.assertTrue(bool(nightly.get("replay_match")))
            self.assertTrue(bool(soak.get("replay_match")))

    def test_compute_replay_fingerprints_fails_closed_on_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = root / "primary.json"
            replay = root / "missing.json"
            self._write_json(primary, {"ok": True})
            payload = compute_replay_fingerprints([("paper_harness_audit", primary, replay)])
            self.assertFalse(bool(payload.get("determinism_ok")))
            validators = payload.get("validators")
            self.assertIsInstance(validators, dict)
            entry = validators.get("paper_harness_audit")
            self.assertIsInstance(entry, dict)
            self.assertEqual(str(entry.get("replay_error") or ""), "missing")


if __name__ == "__main__":
    unittest.main()
