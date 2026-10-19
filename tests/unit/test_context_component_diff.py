from types import SimpleNamespace
from copy import deepcopy
import hashlib

import pytest

from praxile.eval.v2.component_diff import context_component_diff
from praxile.eval.v2.schema import canonical_json


def manifest(context):
    return SimpleNamespace(reproducibility={"policy": {
        "context": context, "settings": {"private_setting": "do-not-export"}}})


@pytest.mark.parametrize("before,after", [([], [{"content": "search narrowly"}]),
    ([{"content": "old"}], [{"content": "new"}]),
    ([{"content": "remove"}], []),
    ([{"content": "a"}, {"content": "b"}], [{"content": "b"}, {"content": "a"}])])
def test_exact_patch_replays_only_context(before, after):
    result = context_component_diff(manifest(before), manifest(after))
    state = {"context": deepcopy(before)}
    for operation in result["json_patch"]:
        assert operation["path"] == "/context"
        if operation["op"] == "test":
            assert state["context"] == operation["value"]
        else:
            assert operation["op"] == "replace"
            state["context"] = operation["value"]
    assert state["context"] == after
    assert result["after_sha256"] == hashlib.sha256(canonical_json(after).encode()).hexdigest()
    assert "do-not-export" not in canonical_json(result)
    assert not result["runtime_application_verified"]
    frozen_after = deepcopy(after)
    after.clear()
    assert result["json_patch"][-1]["value"] == frozen_after


def test_unchanged_context_is_not_a_change():
    result = context_component_diff(manifest([]), manifest([]))
    assert result["status"] == "recorded"
    assert result["changed"] is False and result["json_patch"] == []


@pytest.mark.parametrize("invalid", [None, {}, "text", [None]])
def test_missing_or_invalid_context_is_not_empty_context(invalid):
    result = context_component_diff(manifest(invalid), manifest([]))
    assert result["status"] == "unavailable"
    assert "json_patch" not in result
