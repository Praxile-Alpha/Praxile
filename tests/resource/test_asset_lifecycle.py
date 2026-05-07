import unittest
import tempfile
from pathlib import Path
import pytest
from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]

class TestAssetLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config.load(self.root)
        self.store = ExperienceStore(self.config.paths)
        self.store.initialize(self.config)
        
        # Create a dummy asset
        self.asset_path = ".praxile/skills/test/SKILL.md"
        asset_file = self.root / self.asset_path
        asset_file.parent.mkdir(parents=True, exist_ok=True)
        asset_file.write_text("# Test Skill\n", encoding="utf-8")
        self.store.reindex_all()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deprecate_asset(self):
        asset = self.store.update_asset_status(
            self.asset_path,
            status="deprecated",
            replaced_by=".praxile/skills/new/SKILL.md",
            reason="outdated"
        )
        self.assertEqual(asset["status"], "deprecated")
        self.assertEqual(asset["replaced_by"], ".praxile/skills/new/SKILL.md")
        self.assertEqual(asset["deprecated_reason"], "outdated")

    def test_archive_and_reactivate_asset(self):
        asset = self.store.update_asset_status(
            self.asset_path,
            status="archived",
            reason="not used"
        )
        self.assertEqual(asset["status"], "archived")
        
        # Retrieval should default to not return it
        results = self.store.retrieve("test", limit=10)
        self.assertNotIn(".praxile/skills/test/SKILL.md", [r["path"] for r in results])
        self.assertNotIn("skills/test/SKILL.md", [r["path"] for r in results])
        
        # Reactivate
        asset = self.store.update_asset_status(
            self.asset_path,
            status="active",
            reason="needed again"
        )
        self.assertEqual(asset["status"], "active")
        
        # Retrieval should find it now
        results = self.store.retrieve("test", limit=10)
        paths = [r["path"] for r in results]
        self.assertTrue(".praxile/skills/test/SKILL.md" in paths or "skills/test/SKILL.md" in paths)

    def test_asset_status_history_and_inactive_list(self):
        self.store.update_asset_status(self.asset_path, status="archived", reason="testing history")
        
        # Test memory list --include-inactive equivalent
        assets = self.store.list_assets("skill", include_inactive=True)
        self.assertTrue(any(a["path"].endswith("test/SKILL.md") and a["status"] == "archived" for a in assets))
        
        # Verify lifecycle history is appended
        asset = self.store.get_asset("skills/test/SKILL.md") or self.store.get_asset(".praxile/skills/test/SKILL.md")
        events = asset.get("lifecycle_events", [])
        if isinstance(events, str):
            import json
            events = json.loads(events)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[-1]["status"], "archived")

if __name__ == "__main__":
    unittest.main()
