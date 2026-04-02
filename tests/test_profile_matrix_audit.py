import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.profile_matrix_audit import run_audit


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
    def test_profile_matrix_audit_passes_isolated_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p1 = root / "btc.yaml"
            p2 = root / "sol.yaml"
            _write_profile(p1, symbol="BTC", suffix="btc")
            _write_profile(p2, symbol="SOL", suffix="sol")
            result = run_audit(profile_paths=[p1, p2])
        self.assertTrue(result["ok"], msg=str(result["findings"]))

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


if __name__ == "__main__":
    unittest.main()
