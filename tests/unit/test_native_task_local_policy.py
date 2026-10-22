import pytest

from praxile.config import default_config, validate_config
from praxile.eval.native_worker import benchmark_config


def test_native_baseline_disables_task_local_memory_but_normal_default_is_unchanged(tmp_path):
    assert default_config(tmp_path)["runtime"]["task_local_memory_enabled"] is True
    config = benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=3)
    assert config.data["runtime"]["task_local_memory_enabled"] is False


def test_explicit_task_local_policy_routes_reflection_to_same_benchmark_endpoint(tmp_path):
    config = benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=3,
                              task_local_memory_enabled=True)
    assert config.data["runtime"]["task_local_memory_enabled"] is True
    assert config.data["model_roles"]["experience_reflection"] == {"provider": "benchmark", "model": "fixture"}


@pytest.mark.parametrize("value", [1, "false", None])
def test_invalid_policy_type_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=3,
                         task_local_memory_enabled=value)
    config = default_config(tmp_path)
    config["runtime"]["task_local_memory_enabled"] = value
    with pytest.raises(ValueError):
        validate_config(config)


@pytest.mark.parametrize("legacy", [False, True])
def test_existing_task_cannot_silently_switch_memory_policy(tmp_path, legacy):
    from praxile.config import Config, ProjectPaths
    from praxile.runtime import AgentRuntime
    from praxile.trajectory import TrajectoryLogger
    runtime = AgentRuntime.__new__(AgentRuntime)
    data = default_config(tmp_path)
    data["runtime"]["task_local_memory_enabled"] = False
    runtime.config = Config(data, ProjectPaths(tmp_path))
    logger = TrajectoryLogger("existing", {})
    if legacy:
        logger.data["task_notes"] = [{"note_text": "old context"}]
    else:
        logger.data["task_local_policy"] = {"memory_enabled": True}
    status, message = runtime._run_action_loop("existing", logger, [], max_steps=2, private=False, high_risk=False)
    assert status == "needs_human" and "policy changed" in message
