import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prodesk import state_store


class StateStoreTests(unittest.TestCase):
    def test_load_state_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing_state.json"
            payload = state_store.load_state(path)
        self.assertEqual(payload.get("positions"), {})
        self.assertEqual(payload.get("seen_trade_ids"), [])
        self.assertIsNone(payload.get("last_fill_ts_utc"))
        self.assertIsNone(payload.get("last_status_ts_utc"))

    def test_save_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            payload = {
                "positions": {"tok-1": {"size": 3}},
                "seen_trade_ids": ["t-1"],
                "last_fill_ts_utc": "2026-04-21T00:00:00.000Z",
                "last_status_ts_utc": "2026-04-21T00:00:01.000Z",
            }
            state_store.save_state(path, payload)
            loaded = state_store.load_state(path)
        self.assertEqual(loaded, payload)

    def test_dir_fsync_failure_warns_once_and_remains_best_effort(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            state_store._DIR_FSYNC_WARNING_EMITTED = False  # reset module sentinel for deterministic test
            real_os_open = state_store.os.open

            def _open_side_effect(target: str, flags: int, mode: int = 0o777):
                if str(target) == str(path.parent):
                    raise OSError("boom")
                return real_os_open(target, flags, mode)

            with mock.patch("prodesk.state_store.LOG.warning") as warning_mock:
                with mock.patch("prodesk.state_store.os.open", side_effect=_open_side_effect):
                    state_store.save_state(path, {"positions": {}})
                    state_store.save_state(path, {"positions": {"tok-1": {"size": 1}}})
            self.assertTrue(path.exists())
            warning_mock.assert_called_once()
            loaded = state_store.load_state(path)
            self.assertEqual(loaded.get("positions"), {"tok-1": {"size": 1}})


if __name__ == "__main__":
    unittest.main()
