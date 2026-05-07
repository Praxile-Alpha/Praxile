import pytest
import tempfile
import json
from pathlib import Path
from praxile.evolution import EvolutionEngine
from praxile.config import Config

pytestmark = pytest.mark.resource

def test_evidence_store_write():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.load(Path(tmp))
        engine = EvolutionEngine(config)
        
        trajectory = {
            "task_id": "test_evidence",
            "task_analysis": {"task_type": "bugfix"},
            "user_task": "test",
            "result": {"status": "completed"}
        }
        
        engine.generate(trajectory)
        
        evidence_file = config.paths.state / "experience" / "evidence" / "test_evidence.json"
        assert evidence_file.exists()
        evidence = json.loads(evidence_file.read_text())
        assert evidence["task_id"] == "test_evidence"
