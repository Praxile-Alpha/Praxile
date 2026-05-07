import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = pytest.mark.integration

def test_review_accept_explain_real_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Initialize
        init_args = ["--project", str(root), "init"]
        with patch.object(sys, "argv", ["praxile"] + init_args):
            main(init_args)
        
        # Create a mock proposal
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        proposal = {
            "proposal_id": "prop_123",
            "type": "skill_create",
            "title": "A new skill",
            "risk_level": "low",
            "target_files": ["skills/test/SKILL.md"],
            "changes": [
                {
                    "operation": "write",
                    "path": "skills/test/SKILL.md",
                    "content": "# Test Skill"
                }
            ],
            "status": "pending"
        }
        store.write_proposal(proposal)
        
        # Review (just check it doesn't crash)
        review_args = ["--project", str(root), "review", "--pending"]
        with patch.object(sys, "argv", ["praxile"] + review_args):
            main(review_args)
            
        # Accept
        accept_args = ["--project", str(root), "accept", "prop_123"]
        with patch.object(sys, "argv", ["praxile"] + accept_args):
            exit_code = main(accept_args)
            assert exit_code == 0
            
        # Explain latest
        explain_args = ["--project", str(root), "explain", "latest"]
        with patch.object(sys, "argv", ["praxile"] + explain_args):
            # Might print "No trajectory found" but shouldn't crash
            main(explain_args)
