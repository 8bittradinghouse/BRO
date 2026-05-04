import subprocess
import unittest

from prodesk.time_sync import (
    capture_host_time_sync_snapshot,
    parse_timedatectl_status,
    parse_timedatectl_timesync_status,
)


class TimeSyncTests(unittest.TestCase):
    def test_parse_timedatectl_status_extracts_sync_flags(self) -> None:
        payload = parse_timedatectl_status(
            """
               Local time: Fri 2026-04-24 01:41:09 UTC
           Universal time: Fri 2026-04-24 01:41:09 UTC
                 RTC time: Fri 2026-04-24 01:41:09
                Time zone: Etc/UTC (UTC, +0000)
System clock synchronized: yes
              NTP service: active
          RTC in local TZ: no
"""
        )
        self.assertTrue(payload["system_clock_synchronized"])
        self.assertTrue(payload["ntp_service_active"])
        self.assertEqual(payload["timezone"], "Etc/UTC (UTC, +0000)")

    def test_parse_timedatectl_timesync_status_extracts_latency_fields(self) -> None:
        payload = parse_timedatectl_timesync_status(
            """
       Server: 2001:19f0:200:144b::1000 (1.time.constant.com)
Poll interval: 34min 8s (min: 32s; max 34min 8s)
         Leap: normal
      Version: 4
      Stratum: 2
    Reference: 81060F1C
    Precision: 1us (-24)
Root distance: 36.338ms (max: 5s)
       Offset: -1.844ms
        Delay: 149.887ms
       Jitter: 1.224ms
 Packet count: 81
    Frequency: +7.715ppm
"""
        )
        self.assertEqual(payload["server"], "2001:19f0:200:144b::1000 (1.time.constant.com)")
        self.assertEqual(payload["stratum"], 2)
        self.assertAlmostEqual(float(payload["offset_ms"] or 0.0), -1.844, places=6)
        self.assertAlmostEqual(float(payload["jitter_ms"] or 0.0), 1.224, places=6)
        self.assertAlmostEqual(float(payload["root_distance_ms"] or 0.0), 36.338, places=6)
        self.assertAlmostEqual(float(payload["network_delay_ms"] or 0.0), 149.887, places=6)

    def test_capture_host_time_sync_snapshot_combines_status_and_timesync(self) -> None:
        def _runner(cmd, **_: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["timedatectl", "status"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        "System clock synchronized: yes\n"
                        "NTP service: active\n"
                        "Time zone: Etc/UTC (UTC, +0000)\n"
                    ),
                    stderr="",
                )
            if cmd == ["timedatectl", "timesync-status"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        "Server: 1.time.constant.com\n"
                        "Stratum: 2\n"
                        "Root distance: 36.338ms (max: 5s)\n"
                        "Offset: -1.844ms\n"
                        "Jitter: 1.224ms\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {cmd!r}")

        snapshot = capture_host_time_sync_snapshot(cmd_runner=_runner, timeout_sec=1.0)
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["clock_state"], "synced")
        self.assertEqual(snapshot["server"], "1.time.constant.com")
        self.assertAlmostEqual(float(snapshot["offset_ms"] or 0.0), -1.844, places=6)
        self.assertAlmostEqual(float(snapshot["jitter_ms"] or 0.0), 1.224, places=6)

    def test_capture_host_time_sync_snapshot_marks_command_unavailable_as_partial_visibility(self) -> None:
        def _runner(cmd, **_: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])

        snapshot = capture_host_time_sync_snapshot(cmd_runner=_runner, timeout_sec=1.0)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["clock_state"], "partial_visibility")
        self.assertFalse(snapshot["status_command_ok"])
        self.assertFalse(snapshot["timesync_command_ok"])
        self.assertIn("errors", snapshot)
        self.assertTrue(any("FileNotFoundError" in str(err) for err in snapshot["errors"]))
