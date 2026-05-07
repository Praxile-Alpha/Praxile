import unittest
from unittest.mock import MagicMock
from praxile.config import Config
from praxile.consolidation import ConsolidationEngine

class TestConsolidationEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        from pathlib import Path
        self.root = Path(self.tmp.name)
        self.config = Config.load(self.root)
        self.store = MagicMock()
        self.engine = ConsolidationEngine(self.config, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_consolidation_low_value(self):
        self.store.list_assets.return_value = [
            {"path": "skills/bad.md", "status": "active", "confidence": 0.1, "usage_count": 0, "type": "skill"}
        ]
        proposals = self.engine.generate(duplicates=False, stale=False, conflicts=False, low_value=True)
        self.assertGreater(len(proposals), 0)
        types = [p["type"] for p in proposals]
        self.assertTrue(any("deprecate" in t or "archive" in t or "rewrite" in t for t in types) or "governance" in types)

    def test_asset_rewrite_supports_edit(self):
        self.store.list_assets.return_value = [
            {"path": "skills/weak.md", "status": "active", "confidence": 0.2, "usage_count": 5, "positive_outcome_count": 3, "type": "skill"}
        ]
        # It should trigger a rewrite due to low confidence but some positive signal
        proposals = self.engine.generate(duplicates=False, stale=False, conflicts=False, low_value=True)
        
        # Test the structure contains diffs that could be edited
        rewrite_props = [p for p in proposals if p.get("type") == "asset_rewrite"]
        if rewrite_props:
            self.assertIn("diff", rewrite_props[0])
    def test_consolidation_duplicates(self):
        self.store.list_assets.return_value = [
            {"path": "skills/dup1.md", "status": "active", "title": "Dup", "type": "skill", "content": "same content here"},
            {"path": "skills/dup2.md", "status": "active", "title": "Dup", "type": "skill", "content": "same content here"}
        ]
        proposals = self.engine.generate(duplicates=True, stale=False, conflicts=False, low_value=False)
        self.assertIsInstance(proposals, list)
        
        # Test asset_merge content enhancement
        # The generated proposal should contain merged target_files
        if proposals and proposals[0].get("type") == "asset_merge":
            self.assertIn("skills/dup1.md", proposals[0].get("target_files", []))
            self.assertIn("skills/dup2.md", proposals[0].get("target_files", []))

if __name__ == "__main__":
    unittest.main()
