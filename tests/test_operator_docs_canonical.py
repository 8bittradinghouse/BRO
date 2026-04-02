import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class OperatorDocsCanonicalTests(unittest.TestCase):
    def test_operator_docs_do_not_advertise_direct_executor_invocation(self):
        targets = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "DRILLBOOK.md",
            REPO_ROOT / "docs" / "LIVE_CANARY.md",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("python executor.py", text, msg=f"non-canonical direct executor command in {path}")

    def test_readme_points_to_canonical_paper_session_entrypoint(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("./scripts/canonical_paper_session.sh", text)

    def test_readme_uses_canonical_paper_argument_vocabulary(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("broctl paper -- --duration-min", text)
        self.assertNotIn("broctl paper -- --active-minutes", text)
        self.assertIn("./scripts/canonical_paper_session.sh --active-minutes 10 --wait-sec 25", text)


if __name__ == "__main__":
    unittest.main()
