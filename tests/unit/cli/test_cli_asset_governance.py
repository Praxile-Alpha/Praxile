import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import argparse
from praxile.cli import cmd_asset_archive, cmd_asset_reactivate, cmd_memory_list

class TestCLIAssetGovernance(unittest.TestCase):
    @patch("praxile.cli.load")
    def test_asset_archive_reactivate_flow(self, mock_load):
        config = MagicMock()
        store = MagicMock()
        mock_load.return_value = (config, store)
        
        # 1. Archive
        args_archive = argparse.Namespace(path="memory/test.md", reason="no longer needed")
        store.update_asset_status.return_value = {"path": ".praxile/memory/test.md", "status": "archived"}
        cmd_asset_archive(args_archive, Path("."))
        store.update_asset_status.assert_called_with(".praxile/memory/test.md", status="archived", reason="no longer needed")
        
        # 2. Memory List (include inactive)
        args_list = argparse.Namespace(include_inactive=True)
        store.list_assets.return_value = [{"path": ".praxile/memory/test.md", "status": "archived", "title": "Test"}]
        cmd_memory_list(args_list, Path("."))
        store.list_assets.assert_called_with("memory", include_inactive=True)
        
        # 3. Reactivate
        args_reactivate = argparse.Namespace(path="memory/test.md", reason="needed again")
        store.update_asset_status.return_value = {"path": ".praxile/memory/test.md", "status": "active"}
        cmd_asset_reactivate(args_reactivate, Path("."))
        store.update_asset_status.assert_called_with(".praxile/memory/test.md", status="active", reason="needed again")

if __name__ == "__main__":
    unittest.main()
