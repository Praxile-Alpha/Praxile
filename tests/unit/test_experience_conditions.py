import pytest

from praxile.experience_conditions import check_conditions, observe_tool_versions


@pytest.mark.parametrize("observations", [None, {}, {"tool_versions": None}, {"tool_versions": []}])
def test_missing_or_malformed_observation_abstains(observations):
    result = check_conditions({"tool_versions": {"python": "3.12.0"}}, observations)
    assert result["status"] == "abstained"
    assert result["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize("requirements", [{}, [], {"other": {}}, {"state_equals": {}},
    {"state_equals": {"git.is_repo": None}}, {"tool_versions": {"python": 3}},
    {"state_equals": {"git..branch": "main"}}])
def test_invalid_requirements_abstain(requirements):
    assert check_conditions(requirements, {})["status"] == "abstained"


def test_exact_typed_state_and_version_checks_do_not_disclose_values():
    requirements = {"tool_versions": {"python": "3.12.0"}, "state_equals": {"git.is_repo": False}}
    observed = {"tool_versions": {"python": "3.12.0"}, "state": {"git": {"is_repo": False}}}
    assert check_conditions(requirements, observed)["status"] == "matched"
    observed["state"]["git"]["is_repo"] = 0
    assert check_conditions(requirements, observed)["status"] == "abstained"
    observed["tool_versions"]["python"] = "PRIVATE_VALUE"
    assert "PRIVATE_VALUE" not in str(check_conditions(requirements, observed))
    assert check_conditions(None, None)["status"] == "not_required"


def test_version_observer_uses_installed_metadata(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    def version(name):
        if name == "playwright":
            return "1.64.0"
        raise PackageNotFoundError(name)

    monkeypatch.setattr("praxile.experience_conditions.importlib.metadata.version", version)
    monkeypatch.setattr("praxile.experience_conditions.platform.python_version", lambda: "3.12.0")
    assert observe_tool_versions() == {"python": "3.12.0", "python_package:playwright": "1.64.0"}
