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

    def test_guardian_profile_audit_passes_hardened_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            compose = self._write_compose(
                Path(td),
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
            report = run_audit(compose_path=compose)
        self.assertTrue(report["ok"], msg=str(report.get("findings")))

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


if __name__ == "__main__":
    unittest.main()
