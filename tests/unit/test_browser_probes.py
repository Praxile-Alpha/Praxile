from types import SimpleNamespace

import pytest

from praxile.browser_probes import observe_probes, validate_probes
from praxile.experience_conditions import check_conditions


def probes(kind="visible"):
    return validate_probes({"ready": {"origin": "https://example.test", "selector": "#ready", "kind": kind}})


def page(count=1, visible=True):
    return SimpleNamespace(url="https://example.test/form?private=value", is_closed=lambda: False,
        locator=lambda selector: SimpleNamespace(count=lambda: count, is_visible=lambda: visible))


def test_observation_is_typed_and_conditions_use_it_without_page_secrets():
    result = observe_probes(page(), probes())
    assert result["dom"] == {"ready": True}
    assert len(result["dom_profile_sha256"]) == 64
    assert "private" not in str(result)
    assert check_conditions({"state_equals": {"browser.dom.ready": True}},
                            {"state": {"browser": result}})["status"] == "matched"


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_visibility_is_unknown(count):
    result = observe_probes(page(count=count), probes())
    assert result["dom"] == {}
    assert check_conditions({"state_equals": {"browser.dom.ready": False}},
                            {"state": {"browser": result}})["status"] == "abstained"


def test_count_zero_and_invisible_single_element_are_real_observations():
    assert observe_probes(page(count=0), probes("count"))["dom"] == {"ready": 0}
    assert observe_probes(page(visible=False), probes())["dom"] == {"ready": False}


def test_wrong_origin_never_queries_dom():
    target = page()
    target.url = "https://other.test/form"
    target.locator = lambda selector: pytest.fail("cross-origin probe")
    assert observe_probes(target, probes())["dom"] == {}


def test_navigation_during_observation_invalidates_all_results():
    target = page()
    def locator(selector):
        target.url = "https://example.test/different"
        return SimpleNamespace(count=lambda: 1, is_visible=lambda: True)
    target.locator = locator
    assert observe_probes(target, probes())["dom"] == {}


def test_selector_failure_is_unknown_and_profile_changes_are_visible():
    target = page()
    def fail(selector):
        raise ValueError("private page text")
    target.locator = fail
    result = observe_probes(target, probes())
    assert result["dom"] == {}
    assert "private" not in str(result)
    assert result["dom_profile_sha256"] != observe_probes(page(), probes("count"))["dom_profile_sha256"]


@pytest.mark.parametrize("value", [[], {"bad.name": {}}, {"ready": {"origin": "https://example.test/path", "selector": "x", "kind": "count"}},
    {"ready": {"origin": "https://u:p@example.test", "selector": "x", "kind": "count"}},
    {"ready": {"origin": "https://example.test", "selector": "x", "kind": "evaluate"}}])
def test_invalid_probe_definitions_rejected(value):
    with pytest.raises(ValueError):
        validate_probes(value)


def test_config_validation_reports_probe_path_and_browser_freezes_profile(tmp_path):
    from praxile.browser import BrowserEnv
    from praxile.config import Config, ProjectPaths, default_config, validate_config
    data = default_config(tmp_path)
    data["browser"]["observation_probes"] = {"bad.name": {}}
    with pytest.raises(ValueError, match="browser.observation_probes"):
        validate_config(data)
    data["browser"]["observation_probes"] = probes()
    validate_config(data)
    with BrowserEnv(Config(data, ProjectPaths(tmp_path))) as browser:
        data["browser"]["observation_probes"]["ready"]["selector"] = "#different"
        assert browser._observation_probes["ready"]["selector"] == "#ready"
