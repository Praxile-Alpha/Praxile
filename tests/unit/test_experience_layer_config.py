import pytest

from praxile.config import default_config, validate_config


@pytest.mark.parametrize("budgets", [None, {"strategic": 1},
    {"strategic": -1, "procedural": 1, "tool": 1},
    {"strategic": True, "procedural": 1, "tool": 1}])
def test_invalid_layer_budgets_fail_at_configuration(tmp_path, budgets):
    data = default_config(tmp_path)
    data["context"]["experience_layer_budget_units"] = budgets
    with pytest.raises(ValueError, match="experience_layer_budget_units"):
        validate_config(data)
