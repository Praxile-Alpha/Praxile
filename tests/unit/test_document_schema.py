import pytest

from praxile.action_schema import ActionSchemaRegistry


def test_document_action_schema():
    schema = ActionSchemaRegistry()
    assert schema.validate({"type": "read_document", "path": "a.pdf", "start": 1, "limit": 5})[0]
    # Parser memory budgets are per call; concurrent document batches are not enabled.
    assert not schema.validate({"type": "batch", "actions": [{"type": "read_document", "path": "a.pdf"}]})[0]


def test_document_batch_repair_explains_sequential_budget():
    registry = ActionSchemaRegistry()
    valid, errors = registry.validate({"type": "batch", "actions": [{"type": "read_document", "path": "a.pdf"}]})
    assert not valid
    assert any("standalone" in error and "sequential" in error for error in errors)


@pytest.mark.parametrize("field,value", [("start", 0), ("limit", 21), ("limit", True),
                                         ("char_offset", -1), ("start", None)])
def test_document_pagination_rejected_before_dispatch(field, value):
    assert not ActionSchemaRegistry().validate({"type": "read_document", "path": "a.pdf", field: value})[0]
