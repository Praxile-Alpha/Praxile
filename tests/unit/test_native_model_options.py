import pytest

from praxile.eval.native_worker import benchmark_config


def test_native_options_are_validated_and_copied_into_coding_role(tmp_path):
    options = {"reasoning_effort": "low"}
    config = benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=2,
                              model_request_options=options)
    options["reasoning_effort"] = "max"
    assert config.data["model_roles"]["coding_agent"]["request_options"] == {"reasoning_effort": "low"}


@pytest.mark.parametrize("options", [{"max_tokens": 999}, {"messages": []}, {"reasoning_effort": "invalid"}, []])
def test_native_worker_rejects_invalid_options_before_creating_state(tmp_path, options):
    with pytest.raises(ValueError):
        benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=2,
                         model_request_options=options)
    assert not (tmp_path / ".praxile").exists()
