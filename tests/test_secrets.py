import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prodesk.secrets import SecretLoadError, load_auth_secrets


class SecretsTests(unittest.TestCase):
    def test_load_auth_secrets_env_default(self):
        auth = {
            "private_key_env": "POLYMARKET_PRIVATE_KEY",
            "funder_env": "POLYMARKET_FUNDER",
        }
        env = {
            "POLYMARKET_PRIVATE_KEY": "0x" + ("a" * 64),
            "POLYMARKET_FUNDER": "0x" + ("b" * 40),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            pk, funder, meta = load_auth_secrets(auth)
        self.assertTrue(pk.startswith("0x"))
        self.assertTrue(funder.startswith("0x"))
        self.assertEqual(meta["private_key_source"], "env:POLYMARKET_PRIVATE_KEY")

    def test_load_auth_secrets_file_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pk_file = root / "pk.txt"
            funder_file = root / "funder.txt"
            pk_file.write_text("0x" + ("c" * 64), encoding="utf-8")
            funder_file.write_text("0x" + ("d" * 40), encoding="utf-8")
            auth = {
                "private_key_env": "UNUSED_PK_ENV",
                "funder_env": "UNUSED_FUNDER_ENV",
                "private_key_source": {"mode": "file", "path": str(pk_file)},
                "funder_source": {"mode": "file", "path": str(funder_file)},
            }
            pk, funder, meta = load_auth_secrets(auth)
        self.assertEqual(pk, "0x" + ("c" * 64))
        self.assertEqual(funder, "0x" + ("d" * 40))
        self.assertEqual(meta["private_key_source"], "file")
        self.assertEqual(meta["funder_source"], "file")

    def test_load_auth_secrets_manager_source(self):
        auth = {
            "private_key_env": "UNUSED_PK_ENV",
            "funder_env": "UNUSED_FUNDER_ENV",
            "private_key_source": {"mode": "manager", "argv": ["/bin/sh", "-lc", "printf %s 0x" + ("e" * 64)]},
            "funder_source": {"mode": "manager", "argv": ["/bin/sh", "-lc", "printf %s 0x" + ("f" * 40)]},
        }
        pk, funder, meta = load_auth_secrets(auth)
        self.assertEqual(pk, "0x" + ("e" * 64))
        self.assertEqual(funder, "0x" + ("f" * 40))
        self.assertEqual(meta["private_key_source"], "manager")
        self.assertEqual(meta["funder_source"], "manager")

    def test_load_auth_secrets_raises_on_missing(self):
        with self.assertRaises(SecretLoadError):
            load_auth_secrets({"private_key_env": "NOT_SET_A", "funder_env": "NOT_SET_B"})


if __name__ == "__main__":
    unittest.main()
