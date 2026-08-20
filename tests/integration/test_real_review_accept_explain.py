import tempfile
import sys
import json
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

        validation_tests = root / "validation_tests"
        validation_tests.mkdir()
        (validation_tests / "test_skill.py").write_text(
            "from pathlib import Path\n"
            "import unittest\n\n"
            "class SkillTest(unittest.TestCase):\n"
            "    def test_skill_exists(self):\n"
            "        self.assertTrue(Path('.praxile/skills/test/SKILL.md').exists())\n",
            encoding="utf-8",
        )
        suite = root / "skill-suite.json"
        suite.write_text(
            json.dumps(
                {
                    "owner": "project_maintainer",
                    "expected_owner": "independent_eval_owner",
                    "cases": [
                        {
                            "name": "skill exists",
                            "set_type": "sealed",
                            "input": {"command": ["python", "-m", "unittest", "discover", "-s", "validation_tests"]},
                            "expected": {"returncode": 0},
                            "metrics": ["returncode_match"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        
        # Review (just check it doesn't crash)
        review_args = ["--project", str(root), "review", "--pending"]
        with patch.object(sys, "argv", ["praxile"] + review_args):
            main(review_args)
            
        # Validate in isolated baseline/candidate workspaces, then accept.
        validate_args = [
            "--project",
            str(root),
            "proposal",
            "validate",
            "prop_123",
            "--suite",
            str(suite),
        ]
        with patch.object(sys, "argv", ["praxile"] + validate_args):
            assert main(validate_args) == 0

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
