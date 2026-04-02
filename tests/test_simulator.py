import copy
import json
import tempfile
import unittest
from pathlib import Path

from prodesk.config import DEFAULT_EXECUTION_CONFIG
from simulator import _build_aggregate_summary, run_scenario


class SimulatorTests(unittest.TestCase):
    def _cfg(self):
        cfg = copy.deepcopy(DEFAULT_EXECUTION_CONFIG)
        cfg["mode"] = "paper"
        cfg["targets"]["discovery"]["enabled"] = False
        cfg["targets"]["token_ids"] = ["bootstrap_yes", "bootstrap_no"]
        cfg["chainlink"]["enabled"] = False
        cfg["alerts"]["enabled"] = False
        cfg["metrics"]["enabled"] = False
        cfg["runtime"]["cancel_all_on_exit"] = False
        return cfg

    def test_stale_books_scenario_triggers_stale_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="stale_books",
                cfg=self._cfg(),
                steps=60,
                dt_sec=1.0,
                seed=123,
                out_dir=Path(td),
            )
            self.assertGreater(res.stale_rejects, 0)
            self.assertEqual(res.completed_steps, 60)

    def test_crossed_books_scenario_triggers_crossed_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="crossed_books",
                cfg=self._cfg(),
                steps=60,
                dt_sec=1.0,
                seed=321,
                out_dir=Path(td),
            )
            self.assertGreater(res.crossed_rejects, 0)
            self.assertEqual(res.completed_steps, 60)

    def test_future_skew_scenario_triggers_future_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="future_skew",
                cfg=self._cfg(),
                steps=80,
                dt_sec=1.0,
                seed=654,
                out_dir=Path(td),
            )
            self.assertGreater(res.future_rejects, 0)
            self.assertEqual(res.completed_steps, 80)

    def test_lag_stable_scenario_arms_latency_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="lag_stable",
                cfg=self._cfg(),
                steps=180,
                dt_sec=1.0,
                seed=222,
                out_dir=Path(td),
            )
            self.assertGreater(res.latency_armed_steps, 0)
            self.assertTrue(res.passed, msg=f"unexpected notes: {res.notes}")

    def test_lag_collapse_scenario_disarms_latency_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="lag_collapse",
                cfg=self._cfg(),
                steps=180,
                dt_sec=1.0,
                seed=333,
                out_dir=Path(td),
            )
            self.assertGreater(res.latency_armed_steps, 0)
            self.assertGreater(res.latency_disarmed_steps, 0)
            self.assertNotEqual(res.final_latency_state, "armed")
            self.assertTrue(res.passed, msg=f"unexpected notes: {res.notes}")

    def test_baseline_honors_pair_override_for_higher_concurrency(self):
        cfg = self._cfg()
        cfg["runtime"]["max_actions_per_cycle"] = 64
        cfg["risk"]["max_total_open_orders"] = 80
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="baseline",
                cfg=cfg,
                steps=40,
                dt_sec=1.0,
                seed=777,
                out_dir=Path(td),
                target_pairs_override=5,
            )
            # 5 windows -> 10 tokens -> 20 resting quotes when fully populated.
            self.assertGreaterEqual(res.max_open_orders, 16)
            self.assertEqual(res.completed_steps, 40)

    def test_chaos_day_nightmare_injects_faults_without_crash(self):
        cfg = self._cfg()
        with tempfile.TemporaryDirectory() as td:
            res = run_scenario(
                name="chaos_day",
                cfg=cfg,
                steps=180,
                dt_sec=1.0,
                seed=909,
                out_dir=Path(td),
                difficulty="nightmare",
                target_pairs_override=4,
            )
            self.assertEqual(res.completed_steps, 180)
            self.assertGreater(res.outage_steps, 0)
            self.assertGreater(res.shock_events, 0)
            self.assertGreater(res.forced_rotations, 0)
            self.assertGreater(res.distinct_tokens_seen, 8)
            self.assertTrue(res.passed, msg=f"unexpected notes: {res.notes}")

    def test_run_writes_scenario_meta_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            seed = 1234
            run_scenario(
                name="baseline",
                cfg=self._cfg(),
                steps=20,
                dt_sec=1.0,
                seed=seed,
                out_dir=out_dir,
            )
            meta_path = out_dir / "baseline" / f"seed_{seed}" / "scenario_meta.json"
            self.assertTrue(meta_path.exists())
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("scenario"), "baseline")
            self.assertEqual(payload.get("seed"), seed)
            self.assertTrue(str(payload.get("config_fingerprint_sha256", "")))

    def test_run_writes_wallet_sim_summary_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            seed = 2026
            run_scenario(
                name="baseline",
                cfg=self._cfg(),
                steps=30,
                dt_sec=1.0,
                seed=seed,
                out_dir=out_dir,
            )
            wallet_path = out_dir / "baseline" / f"seed_{seed}" / "wallet_sim_summary.json"
            self.assertTrue(wallet_path.exists())
            payload = json.loads(wallet_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(payload.get("enabled")))
            self.assertTrue(bool(payload.get("connected")))
            self.assertEqual(int(payload.get("chain_id", 0)), 137)
            self.assertGreater(int(payload.get("order_submit_signatures", 0)), 0)
            self.assertEqual(int(payload.get("policy_violations", -1)), 0)

    def test_wallet_sim_security_probe_blocks_restricted_actions(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            cfg = self._cfg()
            cfg.setdefault("simulation", {})
            cfg["simulation"]["wallet_sim_security_probe_every_steps"] = 5
            seed = 4242
            run_scenario(
                name="baseline",
                cfg=cfg,
                steps=20,
                dt_sec=1.0,
                seed=seed,
                out_dir=out_dir,
            )
            wallet_path = out_dir / "baseline" / f"seed_{seed}" / "wallet_sim_summary.json"
            payload = json.loads(wallet_path.read_text(encoding="utf-8"))
            self.assertGreater(int(payload.get("blocked_attempts", 0)), 0)
            self.assertEqual(int(payload.get("policy_violations", -1)), 0)

    def test_aggregate_summary_counts_runs_and_pass_rate(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            results = [
                run_scenario(
                    name="baseline",
                    cfg=self._cfg(),
                    steps=20,
                    dt_sec=1.0,
                    seed=111,
                    out_dir=out_dir,
                ),
                run_scenario(
                    name="baseline",
                    cfg=self._cfg(),
                    steps=20,
                    dt_sec=1.0,
                    seed=222,
                    out_dir=out_dir,
                ),
            ]
            agg = _build_aggregate_summary(results)
            self.assertEqual(len(agg), 1)
            self.assertEqual(agg[0]["scenario"], "baseline")
            self.assertEqual(agg[0]["runs"], 2)
            self.assertGreaterEqual(float(agg[0]["pass_rate"]), 0.0)

    def test_same_seed_baseline_is_reproducible_on_key_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            cfg = self._cfg()
            a = run_scenario(
                name="baseline",
                cfg=cfg,
                steps=50,
                dt_sec=1.0,
                seed=13579,
                out_dir=out_dir,
                run_label="a",
            )
            b = run_scenario(
                name="baseline",
                cfg=cfg,
                steps=50,
                dt_sec=1.0,
                seed=13579,
                out_dir=out_dir,
                run_label="b",
            )
            self.assertEqual(a.fills, b.fills)
            self.assertEqual(a.actions, b.actions)
            self.assertEqual(a.risk_rejects, b.risk_rejects)
            self.assertEqual(a.stale_rejects, b.stale_rejects)
            self.assertEqual(a.crossed_rejects, b.crossed_rejects)
            self.assertEqual(a.future_rejects, b.future_rejects)
            self.assertAlmostEqual(a.final_pnl, b.final_pnl, places=9)
            self.assertAlmostEqual(a.net_pnl_after_costs, b.net_pnl_after_costs, places=9)

    def test_run_scenario_rejects_invalid_steps_and_dt(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            with self.assertRaises(ValueError):
                run_scenario(
                    name="baseline",
                    cfg=self._cfg(),
                    steps=0,
                    dt_sec=1.0,
                    seed=1,
                    out_dir=out_dir,
                )
            with self.assertRaises(ValueError):
                run_scenario(
                    name="baseline",
                    cfg=self._cfg(),
                    steps=10,
                    dt_sec=0.0,
                    seed=1,
                    out_dir=out_dir,
                )


if __name__ == "__main__":
    unittest.main()
