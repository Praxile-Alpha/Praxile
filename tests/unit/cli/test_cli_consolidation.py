import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import argparse
from praxile.cli import cmd_consolidate, cmd_accept

class TestCLIConsolidation(unittest.TestCase):
    @patch("praxile.cli.load")
    @patch("praxile.cli.ConsolidationEngine")
    def test_consolidate_and_accept(self, mock_engine_cls, mock_load):
        config = MagicMock()
        store = MagicMock()
        mock_load.return_value = (config, store)
        
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        
        # Mock consolidation returning a merge proposal
        proposal = {
            "proposal_id": "merge_1",
            "type": "asset_merge",
            "title": "Merge duplicate skills",
            "risk_level": "low"
        }
        mock_engine.generate.return_value = [proposal]
        
        # 1. Consolidate
        args_consolidate = argparse.Namespace(
            all=False, duplicates=True, stale=False, conflicts=False, low_value=False, stale_days=None, summary=False
        )
        cmd_consolidate(args_consolidate, Path("."))
        
        mock_engine.generate.assert_called_once()
        store.write_proposal.assert_called_once_with(proposal)
        
        # 2. Accept
        args_accept = argparse.Namespace(proposal_id="merge_1", all_low_risk=False)
        store.find_proposal.return_value = proposal
        store.apply_proposal.return_value = {**proposal, "status": "accepted"}
        
        cmd_accept(args_accept, Path("."))
        store.apply_proposal.assert_called_once_with(proposal)

if __name__ == "__main__":
    unittest.main()
