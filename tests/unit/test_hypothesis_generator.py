import pytest
from praxile.hypothesis import HypothesisGenerator, CounterexampleChecker

def test_hypothesis_generator():
    patterns = [
        {
            "pattern_id": "pat_1",
            "episodes": ["ep1", "ep2", "ep3"],
            "confidence": 0.85,
            "candidate_hypothesis": "Test",
            "applies_when": ["same parser module"],
            "fix_strategy": ["edited parser.py"],
            "verification_commands": ["pytest parser.py"],
            "source_episodes": [{"episode_id": "ep1", "task_id": "task1"}],
        }
    ]
    hypotheses = HypothesisGenerator.generate(patterns)
    assert len(hypotheses) == 1
    assert hypotheses[0]["suggested_asset_type"] == "project_pattern"
    assert hypotheses[0]["confidence"] == 0.85
    assert hypotheses[0]["applies_when"] == ["same parser module"]
    assert hypotheses[0]["fix_strategy"] == ["edited parser.py"]
    assert hypotheses[0]["source_episodes"][0]["episode_id"] == "ep1"

def test_counterexample_checker():
    hypotheses = [
        {"confidence": 0.8, "suggested_asset_type": "project_pattern"}
    ]
    validated = CounterexampleChecker.validate(hypotheses, [])
    assert validated[0]["recommended_action"] == "accept"
