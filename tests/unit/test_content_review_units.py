import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_review import output_units, review_content


def test_units_losslessly_bind_offsets_and_snapshot():
    text = "alpha\n\t" + "字" * 1030
    units = output_units(text)
    assert "".join(unit["text"] for unit in units) == text
    for unit in units:
        assert text[unit["start"]:unit["end"]] == unit["text"]
        assert len(unit["text"]) <= 512
    assert output_units(text) == units
    assert set(u["id"] for u in units).isdisjoint(u["id"] for u in output_units(text + "x"))


def execute(refs):
    text = "A" * 600
    router = Mock()
    router.chat.return_value = {"content": json.dumps({"items": [
        {"id": "r", "verdict": "preserved", "output_refs": refs, "reason": "Evidence present"}]}),
        "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    result = review_content(router, logger, source_text="source", output_text=text,
        requirements=[{"id": "r", "requirement": "Keep source", "source_quote": "source"}],
        citation_mode="units", response_mode="function")
    return result, router


def test_multiple_spans_are_resolved_by_controller_not_model():
    units = output_units("A" * 600)
    result, router = execute([u["id"] for u in units])
    assert result["status"] == "reviewed"
    assert result["items"][0]["output_spans"] == units
    assert "output_quote" not in result["items"][0]
    assert result["content_preservation_verified"] is False
    data = json.loads(router.chat.call_args.args[0][1]["content"])
    assert data["output_units"] == units
    assert "output_text" not in data
    assert router.chat.call_args.kwargs["response_contract"]["name"] == "submit_content_review_units_v1"


@pytest.mark.parametrize("refs", [[], ["invented"], [1], "not a list",
    [u["id"] for u in output_units("different snapshot")],
    [output_units("A" * 600)[0]["id"]] * 2])
def test_missing_duplicate_foreign_or_invalid_ids_abstain(refs):
    result, _ = execute(refs)
    assert result["status"] == "abstained"
    assert result["validation_code"] == "invalid_output_refs"
    assert "items" not in result
