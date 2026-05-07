import pytest
from praxile.proposals import ProposalComposer
from praxile.evolution import EvolutionEngine
from praxile.config import Config
from pathlib import Path

def test_project_pattern_proposal_composer(tmp_path):
    config = Config.load(Path(tmp_path))
    engine = EvolutionEngine(config)
    
    hypotheses = [
        {
            "claim": "Test claim",
            "evidence": ["ep1", "ep2"],
            "evidence_items": ["ep1 failed with JSONDecodeError", "ep2 verified parser repair"],
            "evidence_count": 2,
            "applies_when": ["Parser task sees JSONDecodeError"],
            "does_not_apply_when": ["No parser files are touched"],
            "failure_signatures": ["JSONDecodeError"],
            "fix_strategy": ["edited `parser.py` (+1/-1)"],
            "verification_commands": ["pytest parser.py"],
            "source_episodes": [
                {
                    "episode_id": "ep1",
                    "task_id": "task1",
                    "failure_signature": "JSONDecodeError",
                    "symptom": "parser crashed",
                    "fix_pattern": "edited `parser.py` (+1/-1)",
                    "verification_commands": ["pytest parser.py"],
                }
            ],
            "expected_future_use": "Load before parser JSON repair tasks.",
            "confidence": 0.8,
            "suggested_asset_type": "project_pattern"
        }
    ]
    
    proposals = ProposalComposer.compose(hypotheses, engine)
    assert len(proposals) == 1
    assert proposals[0]["type"] == "project_pattern"
    assert proposals[0]["confidence"] == 0.8
    assert "test-claim" in proposals[0]["title"].lower()
    content = proposals[0]["changes"][0]["content"]
    for section in [
        "## Claim",
        "## Applies When",
        "## Does Not Apply When",
        "## Evidence",
        "## Failure Signatures",
        "## Fix Strategy",
        "## Verification Commands",
        "## Counterexamples",
        "## Source Episodes",
        "## Confidence",
        "## Expected Future Use",
    ]:
        assert section in content
    assert "JSONDecodeError" in content
    assert "edited `parser.py` (+1/-1)" in content
    assert "`pytest parser.py`" in content
