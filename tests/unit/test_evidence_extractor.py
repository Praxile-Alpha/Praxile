import pytest
from praxile.evidence import EvidenceExtractor

def test_extract_evidence_basic():
    trajectory = {
        "task_id": "test_123",
        "user_task": "fix parser",
        "task_analysis": {"task_type": "bugfix"},
        "result": {"status": "completed"},
        "actions": [
            {
                "step": 1,
                "action_type": "edit_file",
                "status": "success",
                "input": {"path": "parser.py"},
                "observation": {
                    "output": "--- a/parser.py\n+++ b/parser.py\n-old\n+new",
                    "data": {
                        "path": "parser.py",
                        "changed": True,
                        "diff": "--- a/parser.py\n+++ b/parser.py\n-old\n+new",
                    },
                },
            },
            {
                "action_type": "run_test",
                "status": "failure",
                "input": {"command": "pytest parser.py"},
                "observation": {"output": "JSONDecodeError"}
            }
        ]
    }
    
    evidence = EvidenceExtractor.extract(trajectory)
    assert evidence["task_id"] == "test_123"
    assert "parser.py" in evidence["touched_files"]
    assert "pytest parser.py" in evidence["failed_commands"]
    assert "JSONDecodeError" in evidence["failure_signatures"][0]
    assert evidence["failure_excerpts"] == ["JSONDecodeError"]
    assert evidence["fix_actions"][0]["summary"] == "edited `parser.py` (+1/-1)"
    assert evidence["diff_summary"]["insertions"] == 1
    assert evidence["commands"][0]["command"] == "pytest parser.py"
