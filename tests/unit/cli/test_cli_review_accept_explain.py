import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import argparse
from praxile.cli import cmd_explain, cmd_review, cmd_accept

class TestCLIReviewAcceptExplain(unittest.TestCase):
    @patch("praxile.cli.load")
    def test_review_accept_explain_flow(self, mock_load):
        # Setup mock store and config
        config = MagicMock()
        store = MagicMock()
        mock_load.return_value = (config, store)
        
        # 1. Review
        # Mock pending proposals
        proposal = {
            "proposal_id": "test_prop_123",
            "type": "skill_create",
            "status": "pending",
            "title": "A new skill",
            "risk_level": "low"
        }
        store.list_proposals.return_value = [proposal]
        
        args_review = argparse.Namespace(pending=True, recommended=None, summary=False, id=None, pager=False, interactive=False, proposal_type=None, risk=None, confidence=None, source_run=None, older_than=None, high_risk=False)
        # Just ensure cmd_review doesn't crash when listing
        try:
            cmd_review(args_review, Path("."))
        except Exception as e:
            self.fail(f"cmd_review failed: {e}")
            
        # 2. Accept
        args_accept = argparse.Namespace(proposal_id="test_prop_123", all_low_risk=False)
        store.find_proposal.return_value = proposal
        store.apply_proposal.return_value = {**proposal, "status": "accepted"}
        
        try:
            cmd_accept(args_accept, Path("."))
        except Exception as e:
            self.fail(f"cmd_accept failed: {e}")
            
        store.apply_proposal.assert_called_once_with(proposal)
        
        # 3. Explain
        args_explain = argparse.Namespace(id="latest", json=False)
        trajectory = {
            "task_id": "task_1",
            "user_task": "Do something",
            "result": {"status": "completed"},
            "loaded_assets": [],
            "experience_candidates": [proposal]
        }
        store.latest_trajectory.return_value = trajectory
        
        try:
            cmd_explain(args_explain, Path("."))
        except Exception as e:
            self.fail(f"cmd_explain failed: {e}")

if __name__ == "__main__":
    unittest.main()
