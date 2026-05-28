import copy
import json
import tempfile
import unittest
from pathlib import Path

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from scripts.reconcile_daily import build_reconciliation


class ReconcileDailyTests(unittest.TestCase):
    def test_build_reconciliation_in_paper_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "rid-1"
            events_path = root / "events_2026-03-03.jsonl"
            events = [
                {"run_id": run_id, "event_type": "order_submit", "order_id": "o1", "reason": "maker_quote"},
                {"run_id": run_id, "event_type": "order_submit", "order_id": "o2", "reason": "taker_chainlink"},
                {"run_id": run_id, "event_type": "fill", "order_id": "o1", "token_id": "t1", "side": "BUY", "price": 0.5, "size": 10},
                {"run_id": run_id, "event_type": "fill", "order_id": "o2", "token_id": "t1", "side": "SELL", "price": 0.6, "size": 5},
            ]
            events_path.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
            (root / f"run_manifest_{run_id}.json").write_text(
                json.dumps({"run_id": run_id, "manifest_schema_version": 2}),
                encoding="utf-8",
            )

            cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
            cfg["mode"] = "paper"
            cfg["simulation"]["fee_category_override"] = "economics"
            cfg["simulation"]["fees_enabled_override"] = True

            report = build_reconciliation(
                cfg=cfg,
                log_dir=root,
                date_str="2026-03-03",
                run_id=run_id,
                mismatch_threshold=0.05,
            )

            self.assertEqual(report.get("schema_version"), 3)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["verification_level"], "paper_sim_verified")
            self.assertEqual(report["verification_scope"], "paper_wallet_simulation_verified")
            self.assertEqual(report["mismatch_ratio_semantics"], "paper_wallet_simulation")
            self.assertIn("decision_trace", report)
            self.assertEqual(report["bot_truth"]["orders_placed"], 2.0)
            self.assertEqual(report["bot_truth"]["fills"], 2.0)
            self.assertAlmostEqual(report["bot_truth"]["fees_paid_taker_estimate"], 0.06, places=6)
            self.assertAlmostEqual(report["bot_truth"]["maker_rebate_estimate"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
