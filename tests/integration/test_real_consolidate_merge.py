import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = pytest.mark.integration

def test_consolidate_merge_real_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Initialize
        init_args = ["--project", str(root), "init"]
        with patch.object(sys, "argv", ["praxile"] + init_args):
            main(init_args)
            
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        
        # Create duplicate assets
        asset1 = root / ".praxile/skills/dup1/SKILL.md"
        asset1.parent.mkdir(parents=True, exist_ok=True)
        asset1.write_text("# Same Skill", encoding="utf-8")
        
        asset2 = root / ".praxile/skills/dup2/SKILL.md"
        asset2.parent.mkdir(parents=True, exist_ok=True)
        asset2.write_text("# Same Skill", encoding="utf-8")
        
        store.reindex_all()
        
        # Consolidate
        # We need to mock the LLM call inside ConsolidationEngine to return a valid proposal
        consolidate_args = ["--project", str(root), "consolidate", "--duplicates"]
        
        mock_proposal = [{
            "proposal_id": "merge_123",
            "type": "asset_merge",
            "title": "Merge duplicates",
            "risk_level": "low",
            "target_files": ["skills/dup1/SKILL.md"],
            "changes": [
                {"operation": "write", "path": "skills/dup1/SKILL.md", "content": "# Merged Skill"},
                {
                    "operation": "metadata_update",
                    "path": "skills/dup2/SKILL.md",
                    "metadata": {
                        "status": "superseded",
                        "replaced_by": "skills/dup1/SKILL.md"
                    }
                }
            ]
        }]
        
        with patch("praxile.consolidation.ConsolidationEngine.generate", return_value=mock_proposal):
            with patch.object(sys, "argv", ["praxile"] + consolidate_args):
                exit_code = main(consolidate_args)
                assert exit_code == 0
                
        # Accept the merge proposal
        accept_args = ["--project", str(root), "accept", "merge_123"]
        with patch.object(sys, "argv", ["praxile"] + accept_args):
            exit_code = main(accept_args)
            assert exit_code == 0
            
        # Check effects
        asset1_meta = store.get_asset(".praxile/skills/dup1/SKILL.md")
        asset2_meta = store.get_asset(".praxile/skills/dup2/SKILL.md")
        
        assert asset1_meta["status"] == "active"
        assert asset2_meta["status"] == "superseded"
