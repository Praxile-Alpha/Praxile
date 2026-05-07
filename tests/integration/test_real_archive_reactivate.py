import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = pytest.mark.integration

def test_archive_reactivate_real_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Initialize
        init_args = ["--project", str(root), "init"]
        with patch.object(sys, "argv", ["praxile"] + init_args):
            main(init_args)
            
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        
        # Create an asset
        asset_path = root / ".praxile/skills/my-skill/SKILL.md"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text("# My Skill", encoding="utf-8")
        store.reindex_all()
        
        # Archive
        archive_args = ["--project", str(root), "asset", "archive", "skills/my-skill/SKILL.md", "--reason", "not needed"]
        with patch.object(sys, "argv", ["praxile"] + archive_args):
            exit_code = main(archive_args)
            assert exit_code == 0
            
        asset = store.get_asset(".praxile/skills/my-skill/SKILL.md")
        assert asset["status"] == "archived"
        
        # Reactivate
        reactivate_args = ["--project", str(root), "asset", "reactivate", "skills/my-skill/SKILL.md", "--reason", "needed again"]
        with patch.object(sys, "argv", ["praxile"] + reactivate_args):
            exit_code = main(reactivate_args)
            assert exit_code == 0
            
        asset = store.get_asset(".praxile/skills/my-skill/SKILL.md")
        assert asset["status"] == "active"
