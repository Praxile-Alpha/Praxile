from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.evolution import EvolutionEngine
from praxile.runtime import AgentRuntime


class JudgeRouter:
    def chat(self, *args, **kwargs):
        return {
            "content": (
                '{"specificity":0.8,"scope_control":0.7,"evidence_quality":0.9,'
                '"intent_alignment":0.75,"overgeneralization_risk":0.8,'
                '"recommended_action":"accept","score":0.76,'
                '"reasons":["Evidence is concrete","Scope is still broad"]}'
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            "provider": "fake",
            "model": "judge",
            "route": {"target": "fake:judge"},
            "latency_ms": 5,
        }


class UnavailableJudgeRouter:
    def chat(self, *args, **kwargs):
        raise RuntimeError("judge down")


def test_llm_judge_reward_schema_and_overgeneralization_guard(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["reward"]["llm_judge"]["enabled"] = True
    runtime = AgentRuntime(config)
    runtime.router = JudgeRouter()

    result = runtime._llm_judge_reward(
        {
            "task_id": "task_judge",
            "user_task": "record pattern",
            "task_analysis": {},
            "result": {"status": "completed"},
            "diff_summary": {},
            "actions": [],
        }
    )

    assert result is not None
    assert result["active"] is True
    assert result["intent_alignment"] == 0.75
    assert result["recommended_action"] == "inspect"
    assert result["reasons"] == ["Evidence is concrete", "Scope is still broad"]


def test_llm_judge_graceful_fallback(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["reward"]["llm_judge"]["enabled"] = True
    runtime = AgentRuntime(config)
    runtime.router = UnavailableJudgeRouter()

    result = runtime._llm_judge_reward(
        {
            "task_id": "task_judge",
            "user_task": "record pattern",
            "task_analysis": {},
            "result": {"status": "completed"},
            "diff_summary": {},
            "actions": [],
        }
    )

    assert result is not None
    assert result["active"] is False
    assert "Optional LLM judge was unavailable" in result["notes"][0]


def test_llm_judge_influence_lowers_overgeneralized_proposal(tmp_path: Path):
    config = Config.load(tmp_path)
    engine = EvolutionEngine(config)
    proposal = engine._proposal(
        source_task_id="task_judge",
        proposal_type="memory_update",
        title="Remember broad lesson",
        reason="Broad",
        risk_level="low",
        confidence=0.8,
        changes=[{"path": "memory/project.md", "operation": "append", "content": "broad"}],
    )

    engine._apply_llm_judge_to_proposals(
        [proposal],
        {
            "active": True,
            "model_role": "reward_judge",
            "score": 0.6,
            "overgeneralization_risk": 0.9,
            "recommended_action": "reject_or_edit",
            "reasons": ["Too broad"],
        },
    )

    assert proposal["confidence"] < 0.8
    assert proposal["recommended_action_override"] == "reject_or_edit"
    assert proposal["llm_judge"]["reasons"] == ["Too broad"]
