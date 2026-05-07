import pytest
from praxile.hypothesis import CounterexampleChecker

def test_counterexample_checker_lowers_confidence():
    hypotheses = [
        {
            "confidence": 0.8,
            "suggested_asset_type": "project_pattern",
            "category": "test_failure_repair",
            "failure_signatures": ["JSONDecodeError"],
            "evidence": ["ep_good"],
            "fix_strategy": ["edited parser.py"],
        }
    ]
    episodes = [
        {
            "episode_id": "ep_bad",
            "task_id": "task_bad",
            "category": "test_failure_repair",
            "failure_signature": "JSONDecodeError",
            "outcome": "failed",
            "fix_pattern": "changed config",
        }
    ]
    validated = CounterexampleChecker.validate(hypotheses, episodes)
    assert validated[0]["recommended_action"] == "inspect_or_edit"
    assert validated[0]["confidence"] < 0.8
    assert validated[0]["counterexamples"][0]["episode_id"] == "ep_bad"
