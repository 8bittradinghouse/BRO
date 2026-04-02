from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prodesk.canonical_authority import (
    ACTOR_DEPLOY_PAPER_CLEAN,
    ACTOR_EXECUTOR,
    ACTOR_GUARDIAN_WATCHDOG,
    CAPABILITY_DEPLOY_START,
    CAPABILITY_EXECUTOR_RUN,
    CAPABILITY_GUARDIAN_CONTROL,
    CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS,
    AuthorityRequest,
    render_authority_denial,
    resolve_authority_decision,
)
from prodesk.run_contract import build_run_contract, write_run_contract


class CanonicalAuthorityTests(unittest.TestCase):
    def _write_contract_and_context(
        self,
        *,
        log_dir: Path,
        run_id: str,
        session_id: str,
        session_token: str,
        session_phase: str,
        allowed_actions: list[str],
    ) -> Path:
        manifest_path = log_dir / f"run_manifest_{run_id}.json"
        manifest_path.write_text('{"run_id":"%s"}\n' % run_id, encoding="utf-8")
        run_contract_path = log_dir / f"run_contract_{run_id}.json"
        payload = build_run_contract(
            session_id=session_id,
            run_id=run_id,
            phase="start",
            session_type="paper_canonical",
            authority_level="authoritative",
            allowed_actions=allowed_actions,
            manifest_path=manifest_path,
            log_root=log_dir,
            state_root=log_dir.parent / "data",
            start_ts="2026-03-21T00:00:00.000Z",
            stop_ts="",
            evidence_slice_start_ts="2026-03-21T00:00:00.000Z",
            evidence_slice_end_ts="",
            status_path=str(log_dir / "status_2026-03-21.jsonl"),
            events_path=str(log_dir / "events_2026-03-21.jsonl"),
            errors_path=str(log_dir / "errors_2026-03-21.jsonl"),
        )
        write_run_contract(run_contract_path, payload, allow_open=True)
        context_path = log_dir / "guardian_session_context.json"
        context_path.write_text(
            (
                "{"
                f"\"schema_version\":1,"
                f"\"session_id\":\"{session_id}\","
                f"\"session_phase\":\"{session_phase}\","
                f"\"session_token\":\"{session_token}\","
                f"\"run_id\":\"{run_id}\","
                f"\"run_contract_path\":\"/logs/paper_universal/run_contract_{run_id}.json\""
                "}\n"
            ),
            encoding="utf-8",
        )
        return context_path

    def test_executor_authority_accepts_contract_backed_context(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            session_id = "sess-1"
            token = "tok-1"
            context_path = self._write_contract_and_context(
                log_dir=log_dir,
                run_id=run_id,
                session_id=session_id,
                session_token=token,
                session_phase="start",
                allowed_actions=list(CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            )
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor=ACTOR_EXECUTOR,
                    action=CAPABILITY_EXECUTOR_RUN,
                    log_dir=log_dir,
                    session_context_file=context_path,
                    session_token=token,
                    run_id=run_id,
                )
            )
            self.assertTrue(decision.authorized)
            self.assertEqual(decision.reason_code, "authorized")

    def test_authority_denies_on_session_token_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            context_path = self._write_contract_and_context(
                log_dir=log_dir,
                run_id=run_id,
                session_id="sess-2",
                session_token="token-from-context",
                session_phase="start",
                allowed_actions=list(CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            )
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor=ACTOR_EXECUTOR,
                    action=CAPABILITY_EXECUTOR_RUN,
                    log_dir=log_dir,
                    session_context_file=context_path,
                    session_token="token-from-arg",
                    run_id=run_id,
                )
            )
            self.assertFalse(decision.authorized)
            self.assertEqual(decision.reason_code, "session_token_mismatch")

    def test_authority_denies_when_contract_missing_requested_capability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            context_path = self._write_contract_and_context(
                log_dir=log_dir,
                run_id=run_id,
                session_id="sess-3",
                session_token="tok-3",
                session_phase="start",
                allowed_actions=[CAPABILITY_GUARDIAN_CONTROL],
            )
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor=ACTOR_EXECUTOR,
                    action=CAPABILITY_EXECUTOR_RUN,
                    log_dir=log_dir,
                    session_context_file=context_path,
                    session_token="tok-3",
                    run_id=run_id,
                )
            )
            self.assertFalse(decision.authorized)
            self.assertEqual(decision.reason_code, "run_contract_action_forbidden")

    def test_authority_denies_when_actor_action_pair_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            context_path = self._write_contract_and_context(
                log_dir=log_dir,
                run_id=run_id,
                session_id="sess-4",
                session_token="tok-4",
                session_phase="start",
                allowed_actions=list(CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            )
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor=ACTOR_GUARDIAN_WATCHDOG,
                    action=CAPABILITY_EXECUTOR_RUN,
                    log_dir=log_dir,
                    session_context_file=context_path,
                    session_token="tok-4",
                    run_id=run_id,
                )
            )
            self.assertFalse(decision.authorized)
            self.assertEqual(decision.reason_code, "actor_action_forbidden")

    def test_authority_denies_when_action_phase_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            run_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
            context_path = self._write_contract_and_context(
                log_dir=log_dir,
                run_id=run_id,
                session_id="sess-5",
                session_token="tok-5",
                session_phase="active",
                allowed_actions=list(CANONICAL_AUTHORITATIVE_ALLOWED_ACTIONS),
            )
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor=ACTOR_DEPLOY_PAPER_CLEAN,
                    action=CAPABILITY_DEPLOY_START,
                    log_dir=log_dir,
                    session_context_file=context_path,
                    session_token="tok-5",
                    run_id=run_id,
                )
            )
            self.assertFalse(decision.authorized)
            self.assertEqual(decision.reason_code, "session_phase_action_forbidden")

    def test_render_authority_denial_includes_reason_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs_exec" / "paper_universal"
            log_dir.mkdir(parents=True, exist_ok=True)
            decision = resolve_authority_decision(
                AuthorityRequest(
                    actor="unknown_actor",
                    action=CAPABILITY_EXECUTOR_RUN,
                    log_dir=log_dir,
                    session_context_file=None,
                    session_token="tok",
                    run_id="rid",
                )
            )
            rendered = render_authority_denial(decision, prefix="authority_denied")
            self.assertTrue(rendered.startswith("authority_denied:{"))
            self.assertIn('"reason_code":"actor_unknown"', rendered)
            self.assertIn('"authorized":false', rendered)
            self.assertIn('"phase":""', rendered)


if __name__ == "__main__":
    unittest.main()
