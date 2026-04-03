import json
import tempfile
import unittest
from pathlib import Path

from scripts.soak_delta_report import run_report


class SoakDeltaReportTests(unittest.TestCase):
    def _write_bundle(
        self,
        root: Path,
        name: str,
        *,
        uptime: float,
        errors: float,
        capture: float,
        maker_submits: float,
        taker_submits: float,
        taker_fills: float,
        ws_book_down: float,
        ws_chain_down: float,
        ws_book_age_p95: float,
        ws_chain_age_p95: float,
        stage_nets=None,
    ) -> Path:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        taker_stage_breakout = {
            stage: {
                "fills_scored": 0.0,
                "capture": max(0.0, float(net)),
                "adverse_selection": max(0.0, -float(net)),
                "net": float(net),
            }
            for stage, net in (stage_nets or {}).items()
        }
        (d / "nightly.json").write_text(
            json.dumps(
                {
                    "duration_minutes": 45.0,
                    "quote_uptime_ratio": uptime,
                    "error_rows": errors,
                    "execution_quality": {"capture_minus_adverse": capture},
                    "execution_paths": {
                        "maker_submits": maker_submits,
                        "taker_bonus_submits": taker_submits,
                        "taker_bonus_fills": taker_fills,
                    },
                    "taker_stage_net_breakout": taker_stage_breakout,
                }
            ),
            encoding="utf-8",
        )
        (d / "websocket_reliability.json").write_text(
            json.dumps(
                {
                    "metrics": {
                        "book_feed_down_ratio": ws_book_down,
                        "chainlink_down_ratio": ws_chain_down,
                        "book_feed_last_msg_age_p95_sec": ws_book_age_p95,
                        "chainlink_last_tick_age_p95_sec": ws_chain_age_p95,
                    }
                }
            ),
            encoding="utf-8",
        )
        (d / "soak_hardening.json").write_text(
            json.dumps({"lanes": {"reliability": {"ok": True}, "utilization": {"ok": True}}}),
            encoding="utf-8",
        )
        (d / "promotion.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        return d

    def test_delta_passes_when_within_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = self._write_bundle(
                root,
                "b",
                uptime=0.80,
                errors=0,
                capture=100,
                maker_submits=100,
                taker_submits=10,
                taker_fills=5,
                ws_book_down=0.01,
                ws_chain_down=0.01,
                ws_book_age_p95=3.0,
                ws_chain_age_p95=3.0,
            )
            c = self._write_bundle(
                root,
                "c",
                uptime=0.79,
                errors=0,
                capture=95,
                maker_submits=95,
                taker_submits=9,
                taker_fills=4,
                ws_book_down=0.02,
                ws_chain_down=0.02,
                ws_book_age_p95=4.0,
                ws_chain_age_p95=4.0,
            )
            out = run_report(baseline_dir=b, candidate_dir=c, min_uptime_delta=-0.05, max_error_rows_delta=1.0, min_capture_delta=-10.0, min_maker_submits_delta=-10.0, min_taker_bonus_submits_delta=-5.0, min_taker_bonus_fills_delta=-5.0, max_ws_book_down_ratio_delta=0.1, max_ws_chain_down_ratio_delta=0.1, max_ws_book_age_p95_delta=5.0, max_ws_chain_age_p95_delta=5.0)
            self.assertTrue(out["ok"], msg=out["findings"])

    def test_delta_fails_on_regression(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = self._write_bundle(
                root,
                "b",
                uptime=0.80,
                errors=0,
                capture=100,
                maker_submits=100,
                taker_submits=10,
                taker_fills=5,
                ws_book_down=0.01,
                ws_chain_down=0.01,
                ws_book_age_p95=3.0,
                ws_chain_age_p95=3.0,
            )
            c = self._write_bundle(
                root,
                "c",
                uptime=0.70,
                errors=3,
                capture=50,
                maker_submits=40,
                taker_submits=1,
                taker_fills=0,
                ws_book_down=0.30,
                ws_chain_down=0.30,
                ws_book_age_p95=20.0,
                ws_chain_age_p95=20.0,
            )
            out = run_report(baseline_dir=b, candidate_dir=c, min_uptime_delta=-0.02, max_error_rows_delta=0.0, min_capture_delta=-10.0, min_maker_submits_delta=-10.0, min_taker_bonus_submits_delta=-3.0, min_taker_bonus_fills_delta=-2.0, max_ws_book_down_ratio_delta=0.05, max_ws_chain_down_ratio_delta=0.05, max_ws_book_age_p95_delta=2.0, max_ws_chain_age_p95_delta=2.0)
            self.assertFalse(out["ok"])
            self.assertIn("BRO-2403", out["error_codes"])

    def test_delta_includes_stage_level_taker_net_breakout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = self._write_bundle(
                root,
                "b",
                uptime=0.80,
                errors=0,
                capture=100,
                maker_submits=100,
                taker_submits=10,
                taker_fills=5,
                ws_book_down=0.01,
                ws_chain_down=0.01,
                ws_book_age_p95=3.0,
                ws_chain_age_p95=3.0,
                stage_nets={"MAKER_TAKER_SELECTIVE": -5.0, "SNIPER_PRIMARY": 8.0},
            )
            c = self._write_bundle(
                root,
                "c",
                uptime=0.80,
                errors=0,
                capture=101,
                maker_submits=102,
                taker_submits=10,
                taker_fills=5,
                ws_book_down=0.01,
                ws_chain_down=0.01,
                ws_book_age_p95=3.0,
                ws_chain_age_p95=3.0,
                stage_nets={"MAKER_TAKER_SELECTIVE": 2.0, "SNIPER_PRIMARY": 6.0},
            )
            out = run_report(
                baseline_dir=b,
                candidate_dir=c,
                min_uptime_delta=-0.05,
                max_error_rows_delta=1.0,
                min_capture_delta=-10.0,
                min_maker_submits_delta=-10.0,
                min_taker_bonus_submits_delta=-5.0,
                min_taker_bonus_fills_delta=-5.0,
                max_ws_book_down_ratio_delta=0.1,
                max_ws_chain_down_ratio_delta=0.1,
                max_ws_book_age_p95_delta=5.0,
                max_ws_chain_age_p95_delta=5.0,
            )
            breakout = out.get("taker_stage_net_breakout", {})
            self.assertIn("MAKER_TAKER_SELECTIVE", breakout)
            self.assertAlmostEqual(float(breakout["MAKER_TAKER_SELECTIVE"].get("delta_net", 0.0)), 7.0, places=6)


if __name__ == "__main__":
    unittest.main()
