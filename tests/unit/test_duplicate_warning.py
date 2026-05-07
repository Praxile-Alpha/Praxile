from __future__ import annotations
import unittest
from praxile.cli import proposal_duplicate_warning, _duplicate_warning_confidence

class TestDuplicateWarning(unittest.TestCase):
    def test_proposal_duplicate_warning_empty(self):
        # When store is None
        self.assertEqual(proposal_duplicate_warning(None, {}), "")

    def test_duplicate_warning_confidence(self):
        self.assertEqual(_duplicate_warning_confidence("High duplicate confidence: similar_asset=foo"), "high")
        self.assertIn(_duplicate_warning_confidence("No match"), ["low", "medium", ""])

if __name__ == "__main__":
    unittest.main()
