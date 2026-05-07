import unittest
from unittest.mock import MagicMock, patch
import argparse
from pathlib import Path
from praxile.cli import cmd_accept, cmd_reject

class TestCLIProposalActions(unittest.TestCase):
    @patch("praxile.cli.load")
    def test_cmd_accept_dry_run(self, mock_load):
        store = MagicMock()
        config = MagicMock()
        mock_load.return_value = (config, store)
        
        proposal = {
            "proposal_id": "prop_1",
            "type": "skill_create",
            "risk_level": "low",
            "title": "Low risk skill"
        }
        store.list_proposals.return_value = [proposal]
        
        args = argparse.Namespace(all_low_risk=True, dry_run=True, limit=None)
        
        # Dry run should not apply
        cmd_accept(args, Path("."))
        store.apply_proposal.assert_not_called()

    @patch("praxile.cli.load")
    def test_cmd_accept_yes(self, mock_load):
        store = MagicMock()
        config = MagicMock()
        mock_load.return_value = (config, store)
        
        proposal = {
            "proposal_id": "prop_1",
            "type": "skill_create",
            "risk_level": "low",
            "title": "Low risk skill"
        }
        store.list_proposals.return_value = [proposal]
        store.apply_proposal.return_value = proposal
        
        args = argparse.Namespace(all_low_risk=True, yes=True, dry_run=False, limit=None)
        
        # Yes should apply
        cmd_accept(args, Path("."))
        store.apply_proposal.assert_called_once_with(proposal)

    @patch("praxile.cli.load")
    def test_cmd_accept_skips_high_risk(self, mock_load):
        store = MagicMock()
        config = MagicMock()
        mock_load.return_value = (config, store)
        
        proposal = {
            "proposal_id": "prop_1",
            "type": "architecture_gate", # excluded type
            "risk_level": "low",
            "title": "Gate"
        }
        store.list_proposals.return_value = [proposal]
        
        args = argparse.Namespace(all_low_risk=True, yes=True, dry_run=False, limit=None)
        
        cmd_accept(args, Path("."))
        store.apply_proposal.assert_not_called()

    @patch("praxile.cli.load")
    def test_cmd_reject_low_confidence(self, mock_load):
        store = MagicMock()
        config = MagicMock()
        mock_load.return_value = (config, store)
        
        proposal = {
            "proposal_id": "prop_1",
            "type": "skill_create",
            "confidence_level": "low",
            "title": "Low conf"
        }
        store.list_proposals.return_value = [proposal]
        store.reject_proposal.return_value = proposal
        
        args = argparse.Namespace(low_confidence=True, reason="too generic", dry_run=False, limit=None, proposal_id=None, older_than=None)
        
        cmd_reject(args, Path("."))
        store.reject_proposal.assert_called_once()
        
    @patch("praxile.cli.load")
    def test_low_evidence_defaults_to_reject_or_edit(self, mock_load):
        from praxile.cli import proposal_review_guidance
        store = MagicMock()
        proposal = {
            "type": "skill_create",
            "confidence_level": "low"
        }
        guidance = proposal_review_guidance(store, proposal)
        self.assertEqual(guidance["action"], "reject_or_edit")
        
        proposal_medium = {
            "type": "skill_create",
            "confidence_level": "medium",
            "risk_level": "medium"
        }
        guidance2 = proposal_review_guidance(store, proposal_medium)
        self.assertNotEqual(guidance2["action"], "reject_or_edit")

if __name__ == "__main__":
    unittest.main()
