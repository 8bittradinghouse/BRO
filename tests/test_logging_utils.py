import json
import tempfile
import unittest
from pathlib import Path

from prodesk.logging_utils import DailyJsonlWriter, EventLogger


class LoggingUtilsTests(unittest.TestCase):
    def test_writer_handles_non_serializable_values(self):
        with tempfile.TemporaryDirectory() as td:
            writer = DailyJsonlWriter(Path(td), "events")
            try:
                writer.write({"payload": object()})
                self.assertIsNotNone(writer.current_path)
                with writer.current_path.open("r", encoding="utf-8") as fh:  # type: ignore[union-attr]
                    row = json.loads(fh.readline())
                self.assertIn("payload", row)
                self.assertIsInstance(row["payload"], str)
            finally:
                writer.close()

    def test_async_writer_flushes_on_close(self):
        with tempfile.TemporaryDirectory() as td:
            writer = DailyJsonlWriter(
                Path(td),
                "events",
                async_flush=True,
                flush_every_records=50,
                flush_interval_sec=5.0,
            )
            try:
                writer.write({"k": 1})
                self.assertIsNotNone(writer.current_path)
            finally:
                writer.close()
            with writer.current_path.open("r", encoding="utf-8") as fh:  # type: ignore[union-attr]
                rows = [line for line in fh if line.strip()]
            self.assertEqual(len(rows), 1)

    def test_event_logger_applies_default_fields_without_overwriting_payload(self):
        with tempfile.TemporaryDirectory() as td:
            logger = EventLogger(Path(td), default_fields={"run_id": "run-123", "bot_name": "Bro"})
            try:
                logger.log_event("sample", {"bot_name": "Override"})
                logger.close()
                events_path = sorted(Path(td).glob("events_*.jsonl"))[0]
                row = json.loads(events_path.read_text(encoding="utf-8").strip())
                self.assertEqual(row["run_id"], "run-123")
                self.assertEqual(row["bot_name"], "Override")
                self.assertEqual(row["event_type"], "sample")
            finally:
                logger.close()


if __name__ == "__main__":
    unittest.main()
