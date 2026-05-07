from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.feedback import FeedbackSemanticClassifier
from praxile.hypothesis import CounterexampleChecker
from praxile.patterns import PatternMiner
from praxile.semantic_judges import AttributionJudge, CounterexampleSemanticChecker, PatternSemanticJudge
from praxile.store import ExperienceStore


class FakeRouter:
    def __init__(self, *contents: str):
        self.contents = list(contents)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.contents:
            raise RuntimeError("no fake response")
        return {
            "content": self.contents.pop(0),
            "provider": "fake",
            "model": "semantic",
            "route": {"target": "fake:semantic"},
            "latency_ms": 1,
            "usage": {},
        }


def enable_semantics(config: Config) -> None:
    config.data["semantic_judges"]["enabled"] = True
    config.data["semantic_judges"]["feedback_classifier"]["use_for_complex_feedback_only"] = False
    config.data["semantic_judges"]["pattern_mining"]["only_after_heuristic_score"] = 0.0


def test_feedback_semantic_classifier_splits_complex_feedback(tmp_path: Path):
    config = Config.load(tmp_path)
    enable_semantics(config)
    router = FakeRouter(
        """
        {
          "feedback_events": [
            {
              "target_type": "run",
              "target_ref": "latest",
              "sentiment": "positive",
              "feedback_type": "satisfaction",
              "strength": 0.7,
              "requires_confirmation": false,
              "reason": "overall result was good"
            },
            {
              "target_type": "proposal",
              "target_ref": "second",
              "sentiment": "negative",
              "feedback_type": "do_not_persist",
              "strength": 0.9,
              "requires_confirmation": true,
              "reason": "second proposal should not be remembered"
            }
          ]
        }
        """
    )

    result = FeedbackSemanticClassifier(config, router).classify("整体不错，但第二条不要记", {})

    assert result["active"] is True
    assert result["feedback_events"][0]["target_type"] == "run"
    assert result["feedback_events"][1]["target_hint"] == "nth:2"
    assert result["feedback_events"][1]["requires_confirmation"] is True
    assert router.calls[0]["purpose"] == "feedback_classifier"


def test_attribution_judge_updates_only_semantically_supported_asset(tmp_path: Path):
    config = Config.load(tmp_path)
    enable_semantics(config)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    memory = tmp_path / ".praxile" / "memory" / "project.md"
    memory.write_text("# Parser memory\n\nNormalize fenced JSON before validation.\n", encoding="utf-8")
    store.index_asset(memory)
    store.record_asset_usage(
        "task_sem_attr",
        [{"path": ".praxile/memory/project.md", "score": 0.9, "why_loaded": "parser"}],
        used_in_prompt=True,
    )
    router = FakeRouter(
        """
        {
          "attribution_level": "strong_positive",
          "used_explicitly": true,
          "confidence": 0.84,
          "evidence": ["Fix followed the memory strategy."],
          "should_update_asset_outcome": true,
          "reason": "asset strategy matched the applied fix"
        }
        """
    )
    trajectory = {
        "task_id": "task_sem_attr",
        "user_task": "Fix parser JSON action failure",
        "loaded_assets": [{"path": ".praxile/memory/project.md", "score": 0.9, "why_loaded": "parser"}],
        "actions": [{"action_type": "edit_file", "status": "success", "observation": {"output": "normalized fenced JSON"}}],
        "result": {"status": "completed"},
    }

    attributions = AttributionJudge(config, router).judge_loaded_assets(
        trajectory,
        "success",
        [".praxile/memory/project.md"],
    )
    store.update_asset_usage_outcome(
        "task_sem_attr",
        "success",
        referenced_paths=[".praxile/memory/project.md"],
        attribution_results=attributions,
    )

    asset = store.get_asset(".praxile/memory/project.md")
    usage = store.usage_for_task("task_sem_attr")
    assert asset is not None
    assert asset["positive_outcome_count"] == 1
    assert usage[0]["attribution_level"] == "strong_positive"
    assert usage[0]["semantic_attribution"]["reason"] == "asset strategy matched the applied fix"


def test_pattern_semantic_judge_can_merge_different_signatures(tmp_path: Path):
    config = Config.load(tmp_path)
    enable_semantics(config)
    router = FakeRouter(
        """
        {
          "same_underlying_pattern": true,
          "semantic_similarity": 0.92,
          "root_cause_similarity": 0.9,
          "fix_strategy_similarity": 0.86,
          "verification_similarity": 0.8,
          "should_merge": true,
          "recommended_pattern_claim": "Normalize model action JSON before schema validation.",
          "reason": "Different errors share the same model-output normalization root cause."
        }
        """
    )
    judge = PatternSemanticJudge(config, router)
    episodes = [
        {
            "episode_id": "ep_a",
            "category": "test_failure_repair",
            "failure_signature": "JSONDecodeError",
            "affected_files": ["praxile/parser.py"],
            "fix_actions": ["strip trailing comma"],
            "verification_commands": ["python -m pytest tests/unit/test_action_schema.py"],
            "outcome": "success",
        },
        {
            "episode_id": "ep_b",
            "category": "test_failure_repair",
            "failure_signature": "Invalid action schema",
            "affected_files": ["praxile/action_schema.py"],
            "fix_actions": ["extract fenced JSON"],
            "verification_commands": ["python -m pytest tests/unit/test_action_schema.py"],
            "outcome": "success",
        },
    ]

    patterns = PatternMiner.mine_from_episodes(episodes, semantic_judge=judge)

    assert len(patterns) == 1
    assert patterns[0]["episodes"] == ["ep_a", "ep_b"]
    assert patterns[0]["candidate_hypothesis"] == "Normalize model action JSON before schema validation."
    assert patterns[0]["match_dimensions"]["semantic_similarity"] == 0.92
    assert patterns[0]["semantic_reasons"]


def test_counterexample_semantic_checker_can_override_same_signature(tmp_path: Path):
    config = Config.load(tmp_path)
    enable_semantics(config)
    router = FakeRouter(
        """
        {
          "is_counterexample": false,
          "counterexample_type": "not_counterexample",
          "confidence_delta": -0.01,
          "recommended_action": "inspect",
          "reason": "Same signature and same fix strategy are compatible with the hypothesis."
        }
        """
    )
    checker = CounterexampleSemanticChecker(config, router)
    hypotheses = [
        {
            "hypothesis_id": "hyp_1",
            "claim": "JSON parser failures need output normalization.",
            "category": "test_failure_repair",
            "evidence": ["ep_a"],
            "failure_signatures": ["JSONDecodeError"],
            "fix_strategy": ["normalize output"],
            "affected_files": ["praxile/parser.py"],
            "confidence": 0.8,
        }
    ]
    episodes = [
        {
            "episode_id": "ep_a",
            "category": "test_failure_repair",
            "failure_signature": "JSONDecodeError",
            "fix_strategy": ["normalize output"],
            "affected_files": ["praxile/parser.py"],
            "outcome": "success",
        },
        {
            "episode_id": "ep_b",
            "category": "test_failure_repair",
            "failure_signature": "JSONDecodeError",
            "fix_strategy": ["provider config"],
            "affected_files": ["praxile/parser.py"],
            "outcome": "success",
        },
    ]

    validated = CounterexampleChecker.validate(hypotheses, episodes, {}, semantic_checker=checker)

    assert validated[0]["counterexamples"] == []
    assert validated[0]["recommended_action"] == "accept"
