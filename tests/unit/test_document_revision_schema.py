import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.tools import MUTATING_ACTIONS


@pytest.mark.parametrize("digest", [None, "", "a" * 63, "X" * 64, True])
def test_revision_requires_observed_digest(digest):
    action = {"type": "revise_docx", "path": "memo.docx", "expected_sha256": digest,
              "document": {"blocks": [{"type": "paragraph", "text": "short memo"}]}}
    assert not ActionSchemaRegistry().validate(action)[0]


def test_revision_is_mutating_and_not_batchable():
    action = {"type": "revise_docx", "path": "memo.docx", "expected_sha256": "a" * 64,
              "document": {"blocks": [{"type": "paragraph", "text": "short memo"}]}}
    schema = ActionSchemaRegistry()
    assert schema.validate(action)[0]
    assert "revise_docx" in MUTATING_ACTIONS
    assert not schema.validate({"type": "batch", "actions": [action]})[0]
