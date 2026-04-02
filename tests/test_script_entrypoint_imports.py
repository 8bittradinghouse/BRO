import unittest
from pathlib import Path


class ScriptEntrypointImportsTests(unittest.TestCase):
    def test_scripts_do_not_use_sys_path_bootstrap_hacks(self):
        repo_root = Path(__file__).resolve().parents[1]
        scripts_dir = repo_root / "scripts"
        offenders = []
        for path in sorted(scripts_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "from prodesk" not in text and "import prodesk" not in text and "from scripts" not in text:
                continue
            if "sys.path.insert(" in text:
                offenders.append(str(path.relative_to(repo_root)))
        self.assertEqual(offenders, [], msg=f"disallowed sys.path bootstrap in scripts: {offenders}")


if __name__ == "__main__":
    unittest.main()
