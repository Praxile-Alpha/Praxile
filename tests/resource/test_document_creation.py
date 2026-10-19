import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.documents import create_docx
from praxile.runtime import AgentRuntime
from praxile.security import SafetyPolicy

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_docx_create_roundtrip_and_no_overwrite(tmp_path):
    docx = pytest.importorskip("docx")
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    safety = SafetyPolicy(config)
    spec = {"blocks": [{"type": "heading", "text": "Award memo", "level": 1},
        {"type": "paragraph", "text": "Draft for review."},
        {"type": "bullet", "text": "Verify recipient"},
        {"type": "table", "rows": [["Award", "Amount"], ["Exhibition", "$35,000"]]},
        {"type": "page_break"}, {"type": "paragraph", "text": "Checklist"}]}
    result = create_docx(config, safety, "memo.docx", spec)
    assert result.status == "success"
    assert not result.data["before_exists"]
    document = docx.Document(tmp_path / "memo.docx")
    assert document.paragraphs[0].text == "Award memo"
    assert document.tables[0].cell(1, 1).text == "$35,000"
    original = (tmp_path / "memo.docx").read_bytes()
    assert create_docx(config, safety, "memo.docx", spec).status == "blocked"
    assert (tmp_path / "memo.docx").read_bytes() == original
    assert not list(tmp_path.glob(".praxile-docx-*"))


def test_docx_dispatch_rollback_preserves_later_changes(tmp_path):
    pytest.importorskip("docx")
    runtime = AgentRuntime(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    try:
        obs = runtime.tools.execute({"type": "create_docx", "path": "memo.docx",
            "document": {"blocks": [{"type": "paragraph", "text": "Memo"}]}}, task_id="test", step=1)
        assert obs["status"] == "success"
        trajectory = {"actions": [{"action_type": "create_docx", "status": "success", "observation": obs}]}
        original = (tmp_path / "memo.docx").read_bytes()
        (tmp_path / "memo.docx").write_bytes(b"human changes")
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "skipped_modified"
        (tmp_path / "memo.docx").write_bytes(original)
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "deleted_new_file"
        assert not (tmp_path / "memo.docx").exists()
    finally:
        runtime.tools.close()


def test_docx_paths_and_invalid_spec_leave_no_output(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    safety = SafetyPolicy(config)
    spec = {"blocks": [{"type": "paragraph", "text": "Memo"}]}
    assert create_docx(config, safety, "../outside.docx", spec).status == "blocked"
    assert create_docx(config, safety, "missing/memo.docx", spec).status == "blocked"
    assert create_docx(config, safety, "bad.docx", {"script": "arbitrary"}).status == "failure"
    assert not list(tmp_path.iterdir())
