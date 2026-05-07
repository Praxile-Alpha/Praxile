import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
import json

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = pytest.mark.integration

def test_cross_run_project_pattern_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        
        init_args = ["--project", str(root), "init"]
        with patch.object(sys, "argv", ["praxile"] + init_args):
            main(init_args)
            
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        
        # Write 3 mock episodes
        ep_dir = store.paths.state / "experience" / "episodes"
        ep_dir.mkdir(parents=True, exist_ok=True)
        
        for i in range(3):
            ep = {
                "episode_id": f"ep_cross_{i}",
                "task_id": f"task_cross_{i}",
                "category": "test_failure_repair",
                "failure_signature": "SyntaxError",
                "symptom": "SyntaxError in model output",
                "root_cause": "Bad JSON",
                "fix_pattern": "parser.py",
                "verification": ["pytest parser.py"],
                "scope": {"applies_to": ["parser.py"], "does_not_apply_to": []},
                "evidence_refs": [f"task_cross_{i}"],
                "confidence": "medium"
            }
            (ep_dir / f"ep_cross_{i}.json").write_text(json.dumps(ep))
            
        # Mine
        mine_args = ["--project", str(root), "mine-patterns"]
        with patch.object(sys, "argv", ["praxile"] + mine_args):
            main(mine_args)
            
        proposals = store.list_proposals(status="pending")
        assert any(p["type"] == "project_pattern" and "syntaxerror" in p["title"].lower() for p in proposals)
