import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.guardian_profile_audit import run_audit


class GuardianProfileAuditTests(unittest.TestCase):
    def _write_compose(self, root: Path, command_tokens: list[str]) -> Path:
        payload = {
            "services": {
                "bro-guardian": {
                    "command": command_tokens,
                }
            }
        }
        path = root / "docker-compose.yml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    def _write_config(self, root: Path, *, include_legacy_queue_pressure: bool) -> Path:
        payload = {
            "targets": {
                "token_ids": ["tok1"],
            }
        }
        if include_legacy_queue_pressure:
            payload["strategy"] = {
                "maker_competitiveness": {
                    "queue_pressure": {
                        "enabled": "definitely_not_bool",
                        "allowed_stages": ["EXTREME_ONLY"],
                        "inside_price_ticks": 0,
                    }
                }
            }
        path = root / "execution_config.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    def test_guardian_profile_audit_passes_hardened_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = self._write_compose(
                root,
                [
                    "python",
                    "scripts/guardian_watchdog.py",
                    "--session-context-file",
                    "/logs/paper_universal/guardian_session_context.json",
                    "--session-token",
                    "${BRO_CANONICAL_SESSION_TOKEN:-}",
                    "--require-authoritative-startup",
                    "--no-run-id-from-manifest",
                    "--require-chainlink-connected",
                    "--require-book-feed-connected",
                    "--chainlink-disconnect-min-age-sec",
                    "25",
                    "--book-feed-disconnect-min-age-sec",
                    "25",
                    "--disconnect-confirm-polls",
                    "3",
                    "--startup-grace-sec",
                    "90",
                    "--max-status-age-sec",
                    "120",
                    "--no-trigger-on-kill-switch",
                ],
            )
            clean_cfg = self._write_config(root, include_legacy_queue_pressure=False)
            report = run_audit(compose_path=compose, config_path=clean_cfg)
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertEqual(int(report.get("compatibility_warning_count") or 0), 0)
        self.assertEqual(list(report.get("compatibility_warnings") or []), [])
        self.assertEqual(list(report.get("ignored_compatibility_fields") or []), [])

    def test_guardian_profile_audit_flags_aggressive_profile(self):
        with tempfile.TemporaryDirectory() as td:
            compose = self._write_compose(
                Path(td),
                [
                    "python",
                    "scripts/guardian_watchdog.py",
                    "--require-chainlink-connected",
                    "--require-book-feed-connected",
                    "--chainlink-disconnect-min-age-sec",
                    "5",
                    "--book-feed-disconnect-min-age-sec",
                    "5",
                    "--disconnect-confirm-polls",
                    "1",
                    "--startup-grace-sec",
                    "10",
                    "--max-status-age-sec",
                    "30",
                ],
            )
            report = run_audit(compose_path=compose)
        self.assertFalse(report["ok"])
        text = "\n".join(report["findings"])
        self.assertIn("startup_grace_too_low", text)
        self.assertIn("max_status_age_too_low", text)
        self.assertIn("disconnect_confirm_polls_too_low", text)

    def test_guardian_profile_audit_flags_missing_authority_flags(self):
        with tempfile.TemporaryDirectory() as td:
            compose = self._write_compose(
                Path(td),
                [
                    "python",
                    "scripts/guardian_watchdog.py",
                    "--require-chainlink-connected",
                    "--require-book-feed-connected",
                    "--chainlink-disconnect-min-age-sec",
                    "25",
                    "--book-feed-disconnect-min-age-sec",
                    "25",
                    "--disconnect-confirm-polls",
                    "3",
                    "--startup-grace-sec",
                    "90",
                    "--max-status-age-sec",
                    "120",
                    "--no-trigger-on-kill-switch",
                ],
            )
            report = run_audit(compose_path=compose)
        self.assertFalse(report["ok"])
        text = "\n".join(report["findings"])
        self.assertIn("missing_session_context_file", text)
        self.assertIn("missing_session_token", text)
        self.assertIn("missing_require_authoritative_startup", text)
        self.assertIn("missing_no_run_id_from_manifest", text)

    def test_guardian_profile_audit_surfaces_ignored_legacy_queue_pressure_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compose = self._write_compose(
                root,
                [
                    "python",
                    "scripts/guardian_watchdog.py",
                    "--session-context-file",
                    "/logs/paper_universal/guardian_session_context.json",
                    "--session-token",
                    "${BRO_CANONICAL_SESSION_TOKEN:-}",
                    "--require-authoritative-startup",
                    "--no-run-id-from-manifest",
                    "--require-chainlink-connected",
                    "--require-book-feed-connected",
                    "--chainlink-disconnect-min-age-sec",
                    "25",
                    "--book-feed-disconnect-min-age-sec",
                    "25",
                    "--disconnect-confirm-polls",
                    "3",
                    "--startup-grace-sec",
                    "90",
                    "--max-status-age-sec",
                    "120",
                    "--no-trigger-on-kill-switch",
                ],
            )
            legacy_cfg = self._write_config(root, include_legacy_queue_pressure=True)
            report = run_audit(compose_path=compose, config_path=legacy_cfg)
        self.assertTrue(report["ok"], msg=str(report.get("findings")))
        self.assertEqual(int(report.get("compatibility_warning_count") or 0), 1)
        self.assertIn(
            "strategy.maker_competitiveness.queue_pressure",
            list(report.get("ignored_compatibility_fields") or []),
        )
        warnings_text = "\n".join(str(x) for x in report.get("compatibility_warnings") or [])
        self.assertIn("removed queue-pressure compatibility surface", warnings_text)
        self.assertIn(
            "removed queue-pressure compatibility surface",
            "\n".join(str(x) for x in report.get("warnings") or []),
        )


if __name__ == "__main__":
    unittest.main()
