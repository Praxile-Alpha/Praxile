import pytest
import tempfile
import json
from pathlib import Path
from praxile.patterns import PatternMiner

pytestmark = pytest.mark.resource

def test_pattern_index_store_update():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ep_dir = root / "experience" / "episodes"
        ep_dir.mkdir(parents=True)
        
        ep = {
            "episode_id": "ep_1",
            "category": "test_failure_repair",
            "failure_signature": "ImportError",
            "scope": {"applies_to": ["main.py"]}
        }
        (ep_dir / "ep_1.json").write_text(json.dumps(ep))
        
        patterns = PatternMiner.update_index(root)
        assert len(patterns) == 1
        assert "ImportError" in patterns[0]["signature_terms"][0]
        
        pat_dir = root / "experience" / "patterns"
        assert len(list(pat_dir.glob("*.json"))) == 1
