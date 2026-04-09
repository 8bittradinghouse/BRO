import unittest
from pathlib import Path

from scripts.doctrine_truth_audit import (
    CANONICAL_ALLOWLIST,
    CANONICAL_PHRASE_REQUIRED_DOCS,
    CANONICAL_PHRASE_SOURCE,
    TARGETED_DEPRECATED_SURFACE_GUARDS,
    run_audit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class DoctrineTruthAuditTests(unittest.TestCase):
    def test_doctrine_truth_audit_passes_repo(self) -> None:
        result = run_audit(repo_root=REPO_ROOT)
        self.assertTrue(bool(result.get("ok")), msg=str(result.get("errors")))

    def test_phrase_source_and_required_docs_are_in_allowlist(self) -> None:
        allowlist = {str(path) for path in CANONICAL_ALLOWLIST}
        for required_doc in CANONICAL_PHRASE_REQUIRED_DOCS:
            self.assertIn(str(required_doc), allowlist)
        self.assertTrue((REPO_ROOT / CANONICAL_PHRASE_SOURCE).exists())

    def test_targeted_deprecated_guards_are_explicit_and_narrow(self) -> None:
        self.assertGreaterEqual(len(TARGETED_DEPRECATED_SURFACE_GUARDS), 1)
        for guard in TARGETED_DEPRECATED_SURFACE_GUARDS:
            self.assertTrue(str(guard.path).startswith("prodesk/wallet/"))
            self.assertTrue(str(guard.function_name).strip())
            self.assertGreaterEqual(len(tuple(guard.forbidden_patterns)), 1)


if __name__ == "__main__":
    unittest.main()
