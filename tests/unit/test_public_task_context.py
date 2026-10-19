import hashlib
import json

import pytest

from praxile.eval.v2.public_task_context import load_public_task_context, validate_public_task_context
from praxile.eval.v2.schema import EvalSchemaError


def context():
    return {"schema_version": "praxile.public_task_context.v1", "visibility": "public_task",
            "task_id": "dev", "goal": "Split workbook", "constraints": ["Preserve source"]}


@pytest.mark.parametrize("field,value", [("goal", ""), ("goal", "x" * 4001),
    ("constraints", []), ("constraints", ["x"] * 21), ("constraints", ["x" * 1001]),
    ("constraints", [False]), ("task_id", "heldout")])
def test_bounded_public_context(field, value):
    with pytest.raises(EvalSchemaError):
        validate_public_task_context({**context(), field: value}, ["dev"], ["heldout"])


def test_digest_duplicate_fields_and_size_rejected(tmp_path):
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context()))
    with pytest.raises(EvalSchemaError, match="digest"):
        load_public_task_context(path, "0" * 64, "dev", [])
    path.write_text('{"goal":"a","goal":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_public_task_context(path, hashlib.sha256(path.read_bytes()).hexdigest(), "dev", [])
    path.write_text(json.dumps(context()) + " " * 65536)
    with pytest.raises(EvalSchemaError, match="64 KiB"):
        load_public_task_context(path, hashlib.sha256(path.read_bytes()).hexdigest(), "dev", [])


def test_context_does_not_alias_input():
    value = context()
    validated = validate_public_task_context(value, ["dev"], [])
    validated["constraints"].append("Changed")
    assert value == context()


def test_symlink_context_rejected(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(context()))
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        load_public_task_context(link, hashlib.sha256(source.read_bytes()).hexdigest(), "dev", [])
