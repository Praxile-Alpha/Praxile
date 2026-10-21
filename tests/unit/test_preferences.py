import pytest

from praxile.config import Config
from praxile.preferences import compose_preference, usage_summary


@pytest.mark.parametrize("key", ["../escape", "Upper", "", "a/b", "x" * 65])
def test_preference_key_rejects_paths(tmp_path, key):
    with pytest.raises(ValueError):
        compose_preference(Config.load(tmp_path), key=key, text="brief", applies_when="summaries", source_reference="user request")


def test_preference_summary_does_not_claim_quality():
    report = usage_summary([
        {"path": ".praxile/memory/preferences/brief.md"},
        {"path": ".praxile/memory/user.md"},
        {"path": ".praxile/memory/project.md"},
        {"path": ".praxile/memory/preferences/unused.md", "used_in_prompt": False},
    ])
    assert report["personalization_assets_loaded"] == 1
    assert report["legacy_user_memory_unclassified"] == 1
    assert report["other_assets_loaded"] == 1
    assert report["capability_improvement"] == report["personalization_effect"] == "not_measured"


def test_runtime_summary_separates_preference_usage():
    from praxile.runtime import AgentRuntime
    runtime = object.__new__(AgentRuntime)
    summary = runtime._evolution_summary({"loaded_assets": [
        {"path": ".praxile/memory/preferences/brief.md", "kind": "memory"}]}, [])
    assert summary["experience_purpose_usage"]["personalization_assets_loaded"] == 1
    assert summary["experience_purpose_usage"]["capability_improvement"] == "not_measured"


def test_capability_judge_does_not_call_model_for_preferences(tmp_path):
    from praxile.semantic_judges import AttributionJudge
    from unittest.mock import Mock
    config = Config.load(tmp_path)
    config.data["semantic_judges"]["enabled"] = True
    router = Mock()
    path = ".praxile/memory/preferences/brief.md"
    results = AttributionJudge(config, router).judge_loaded_assets(
        {"loaded_assets": [{"path": path, "score": 1, "used_in_prompt": True}]}, "success", [path])
    assert results == []
    router.chat.assert_not_called()
