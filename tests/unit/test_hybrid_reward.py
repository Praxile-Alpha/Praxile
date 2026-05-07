import pytest
from praxile.config import Config
from praxile.reward import RewardEngine
from pathlib import Path
import tempfile

def test_hybrid_reward_calculation():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.load(Path(tmp))
        config.data["reward"] = {
            "mode": "hybrid",
            "weights": {
                "objective": 0.6,
                "user_feedback": 0.3,
                "llm_judge": 0.1
            }
        }
        
        engine = RewardEngine(config)
        
        trajectory = {
            "task_analysis": {},
            "actions": [],
            "result": {"status": "completed"},
            "user_feedback_reward": {"score": 1.0},
            "llm_judge_reward": {"score": 0.8}
        }
        
        report = engine.build_report(trajectory, [])
        
        assert "objective_reward" in report
        assert "user_feedback_reward" in report
        assert "llm_judge_reward" in report
        
        obj_score = report["objective_score_component"]
        overall = report["overall"]
        
        expected_overall = round(obj_score * 0.6 + 1.0 * 0.3 + 0.8 * 0.1, 3)
        assert overall == expected_overall

def test_objective_only_reward_calculation():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.load(Path(tmp))
        config.data["reward"] = {
            "mode": "objective_only"
        }
        
        engine = RewardEngine(config)
        
        trajectory = {
            "task_analysis": {},
            "actions": [],
            "result": {"status": "completed"},
            "user_feedback_reward": {"score": 1.0},
            "llm_judge_reward": {"score": 0.8}
        }
        
        report = engine.build_report(trajectory, [])
        
        obj_score = report["objective_score_component"]
        overall = report["overall"]
        
        # objective_only mode forces weights: obj=1.0, user=0.0, llm=0.0
        assert overall == round(obj_score, 3)
