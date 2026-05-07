import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore
import json

pytestmark = pytest.mark.integration

def test_mine_patterns_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        
        # Initialize
        init_args = ["--project", str(root), "init"]
        with patch.object(sys, "argv", ["praxile"] + init_args):
            main(init_args)
            
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        
        # Manually create some mock episodes to be mined
        ep_dir = store.paths.state / "experience" / "episodes"
        ep_dir.mkdir(parents=True, exist_ok=True)
        
        for i in range(3):
            ep = {
                "episode_id": f"ep_mock_{i}",
                "task_id": f"task_mock_{i}",
                "category": "test_failure_repair",
                "failure_signature": "JSONDecodeError",
                "symptom": "Encountered JSONDecodeError",
                "root_cause": "Needs further cross-run pattern mining to determine",
                "fix_pattern": "parser.py",
                "verification": ["pytest parser.py"],
                "scope": {
                    "applies_to": ["parser.py"],
                    "does_not_apply_to": ["unrelated modules"]
                },
                "evidence_refs": [f"task_mock_{i}"],
                "confidence": "medium"
            }
            (ep_dir / f"ep_mock_{i}.json").write_text(json.dumps(ep))
            
        # Mine patterns
        mine_args = ["--project", str(root), "mine-patterns"]
        with patch.object(sys, "argv", ["praxile"] + mine_args):
            exit_code = main(mine_args)
            assert exit_code == 0
            
        # Verify that a proposal was generated
        proposals = store.list_proposals(status="pending")
        assert len(proposals) == 1
        assert proposals[0]["type"] == "project_pattern"
        assert "jsondecodeerror" in proposals[0]["title"].lower()
