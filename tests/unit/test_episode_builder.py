import pytest
from praxile.episodes import EpisodeBuilder

def test_build_test_failure_repair_episode():
    evidence = {
        "task_id": "task_1",
        "outcome": "completed",
        "touched_files": ["app.py"],
        "failure_signatures": ["ValueError: invalid input"],
        "failure_excerpts": ["ValueError: invalid input at app.py:4"],
        "failed_commands": ["pytest app.py"],
        "passed_commands": ["pytest app.py"],
        "fix_actions": [{"file": "app.py", "summary": "edited `app.py` (+2/-1)"}]
    }
    
    episodes = EpisodeBuilder.build(evidence)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["category"] == "test_failure_repair"
    assert ep["failure_signature"] == "ValueError: invalid input"
    assert "app.py" in ep["scope"]["applies_to"]
    assert "edited `app.py` (+2/-1)" in ep["fix_strategy"]
    assert ep["verification_commands"] == ["pytest app.py"]
    assert any("ValueError" in line for line in ep["applies_when"])
    assert any("pytest app.py" in line for line in ep["evidence"])
