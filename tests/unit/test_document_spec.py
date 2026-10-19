import pytest
from praxile.action_schema import ActionSchemaRegistry
from praxile.document_spec import validate_docx_spec
from praxile.tools import MUTATING_ACTIONS


@pytest.mark.parametrize("spec", [{}, {"blocks": []}, {"blocks": [{"type": "python", "text": "exec"}]},
    {"blocks": [{"type": "heading", "text": "x", "level": True}]},
    {"blocks": [{"type": "table", "rows": [["a"], ["b", "c"]]}]},
    {"blocks": [{"type": "paragraph", "text": "x", "url": "https://example.com"}]}])
def test_invalid_specs_rejected_before_execution(spec):
    with pytest.raises(ValueError):
        validate_docx_spec(spec)
    assert not ActionSchemaRegistry().validate({"type": "create_docx", "path": "a.docx", "document": spec})[0]


def test_writer_is_mutating_and_not_batchable():
    action = {"type": "create_docx", "path": "a.docx", "document": {"blocks": [{"type": "paragraph", "text": "Hello"}]}}
    assert "create_docx" in MUTATING_ACTIONS
    assert ActionSchemaRegistry().validate(action)[0]
    assert not ActionSchemaRegistry().validate({"type": "batch", "actions": [action]})[0]


def test_table_errors_identify_exact_location():
    with pytest.raises(ValueError, match=r"blocks\[0\].rows\[1\] must contain exactly 2 cells"):
        validate_docx_spec({"blocks": [{"type": "table", "rows": [["a", "b"], ["c"]]}]})
    with pytest.raises(ValueError, match=r"rows\[0\]\[1\] must be a string"):
        validate_docx_spec({"blocks": [{"type": "table", "rows": [["a", 1]]}]})
