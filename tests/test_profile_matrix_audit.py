import tempfile
import unittest
from pathlib import Path

import yaml

from prodesk.config import load_execution_config
from scripts.profile_matrix_audit import DEFAULT_PROFILES, run_audit


def _write_profile(path: Path, *, symbol: str, suffix: str, mode: str = "paper") -> None:
    payload = {
        "bot_name": f"Bro-{symbol}-{suffix}",
        "mode": mode,
        "asset": {
            "symbol": symbol,
            "chainlink_symbols": [f"{symbol.lower()}/usd"],
            "discovery_symbols": [symbol],
        },
        "targets": {"token_ids": [], "discovery": {"enabled": True}},
        "storage": {
            "log_dir": f"./logs_{suffix}",
            "state_path": f"./logs_{suffix}/state.json",
        },
        "runtime": {"guard_stop_file": f"./logs_{suffix}/guard_stop.txt"},
        "security": {"allowed_storage_roots": [f"./logs_{suffix}"]},
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


class ProfileMatrixAuditTests(unittest.TestCase):
    def test_default_profile_matrix_loads_without_errors(self):
        repo_root = Path(__file__).resolve().parents[1]
        result = run_audit(profile_paths=[repo_root / rel for rel in DEFAULT_PROFILES])
        self.assertEqual(result["load_errors"], [], msg=str(result))
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_profile_matrix_audit_passes_isolated_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p1 = root / "btc.yaml"
            p2 = root / "sol.yaml"
            _write_profile(p1, symbol="BTC", suffix="btc")
            _write_profile(p2, symbol="SOL", suffix="sol")
            result = run_audit(profile_paths=[p1, p2])
        self.assertTrue(result["ok"], msg=str(result["findings"]))

    def test_root_wrapper_configs_resolve_to_canonical_paper_profile(self):
        repo_root = Path(__file__).resolve().parents[1]
        canonical = load_execution_config(repo_root / "configs/profiles/paper_universal.yaml")
        execution_wrapper_raw = yaml.safe_load((repo_root / "execution_config.yaml").read_text(encoding="utf-8"))
        config_wrapper_raw = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(execution_wrapper_raw, {"extends": "./configs/profiles/paper_universal.yaml"})
        self.assertEqual(config_wrapper_raw, {"extends": "./configs/profiles/paper_universal.yaml"})

        for rel in ("execution_config.yaml", "config.yaml"):
            cfg = load_execution_config(repo_root / rel)
            runtime = dict(cfg.get("runtime") or {})
            meta = dict(cfg.get("_meta") or {})
            self.assertEqual(str(cfg.get("profile", {}).get("name") or ""), "paper_universal")
            self.assertTrue(bool(runtime.get("paper_enforce_setup_lock")))
            self.assertEqual(str(runtime.get("paper_expected_profile_name") or ""), "paper_universal")
            self.assertEqual(
                str(runtime.get("paper_expected_config_fingerprint_sha256") or ""),
                str(meta.get("effective_config_sha256") or ""),
            )
            self.assertEqual(
                str(meta.get("effective_config_sha256") or ""),
                str((canonical.get("_meta") or {}).get("effective_config_sha256") or ""),
            )

    def test_profile_matrix_audit_flags_non_owner_claiming_canonical_paper_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            canonical = root / "paper_universal.yaml"
            impostor = root / "btc_variant.yaml"
            _write_profile(canonical, symbol="BTC", suffix="canonical")
            _write_profile(impostor, symbol="BTC", suffix="variant")
            canonical_payload = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            canonical_payload["profile"] = {"name": "paper_owner", "class": "canonical"}
            canonical.write_text(yaml.safe_dump(canonical_payload), encoding="utf-8")
            impostor_payload = yaml.safe_load(impostor.read_text(encoding="utf-8"))
            impostor_payload["profile"] = {"name": "paper_universal", "class": "canonical"}
            impostor_payload["runtime"]["paper_expected_profile_name"] = "paper_universal"
            impostor_payload["runtime"]["paper_expected_config_fingerprint_sha256"] = "a" * 64
            impostor.write_text(yaml.safe_dump(impostor_payload), encoding="utf-8")
            result = run_audit(profile_paths=[canonical, impostor])
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("canonical_paper_identity_claim_outside_owner", text)
        self.assertIn("canonical_paper_expected_profile_claim_outside_owner", text)
        self.assertIn("canonical_paper_expected_fingerprint_claim_outside_owner", text)

    def test_profile_matrix_audit_flags_colliding_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p1 = root / "btc.yaml"
            p2 = root / "sol.yaml"
            _write_profile(p1, symbol="BTC", suffix="shared")
            _write_profile(p2, symbol="SOL", suffix="shared")
            result = run_audit(profile_paths=[p1, p2])
        self.assertFalse(result["ok"])
        text = "\n".join(result["findings"])
        self.assertIn("log_dir_collision_with", text)
        self.assertIn("state_path_collision_with", text)

    def test_profile_matrix_audit_reports_per_profile_load_error_without_crash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = root / "good.yaml"
            bad = root / "bad.yaml"
            _write_profile(good, symbol="BTC", suffix="good")
            bad.write_text("runtime:\n  paper_enforce_setup_lock: true\n", encoding="utf-8")
            result = run_audit(profile_paths=[good, bad])
        self.assertFalse(result["ok"])
        self.assertIn("load_errors", result)
        self.assertEqual(len(result["load_errors"]), 1)
        self.assertIn("bad.yaml:load_error:", result["load_errors"][0])
        self.assertIn(result["load_errors"][0], result["findings"])


if __name__ == "__main__":
    unittest.main()
