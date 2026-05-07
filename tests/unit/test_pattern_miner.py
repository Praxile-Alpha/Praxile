import pytest
from praxile.patterns import PatternMiner

def test_pattern_miner_groups_episodes():
    episodes = [
        {
            "episode_id": "ep1",
            "task_id": "task1",
            "category": "test_failure_repair",
            "failure_signature": "JSONError",
            "scope": {"applies_to": ["a.py"]},
            "fix_strategy": ["edited `a.py` (+1/-1)"],
            "verification_commands": ["pytest a.py"],
            "evidence": ["task1 failed with JSONError"],
        },
        {
            "episode_id": "ep2",
            "task_id": "task2",
            "category": "test_failure_repair",
            "failure_signature": "JSONError",
            "scope": {"applies_to": ["b.py"]},
            "fix_strategy": ["edited `b.py` (+1/-0)"],
            "verification_commands": ["pytest b.py"],
            "evidence": ["task2 failed with JSONError"],
        },
        {"episode_id": "ep3", "category": "other", "failure_signature": "ValueError", "scope": {"applies_to": ["c.py"]}},
    ]
    
    patterns = PatternMiner.mine_from_episodes(episodes)
    assert len(patterns) == 3
    
    json_pat = next(p for p in patterns if "JSONError" in p["signature_terms"][0])
    assert json_pat["success_count"] == 1
    assert len(json_pat["episodes"]) == 1
    assert json_pat["confidence"] < 0.7
    assert "a.py" in json_pat["affected_files"] or "b.py" in json_pat["affected_files"]
    assert "JSONError" in json_pat["failure_signatures"]
    assert any(item.startswith("edited `") for item in json_pat["fix_strategy"])
    assert any(command.startswith("pytest ") for command in json_pat["verification_commands"])
    assert json_pat["source_episodes"][0]["episode_id"] in {"ep1", "ep2"}
    assert any("JSONError" in item for item in json_pat["evidence_items"])
    singletons = [pattern for pattern in patterns if len(pattern["episodes"]) == 1]
    assert len(singletons) == 3
    assert all(pattern["confidence"] < 0.7 for pattern in singletons)
