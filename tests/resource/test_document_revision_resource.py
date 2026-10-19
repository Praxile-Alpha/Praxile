import hashlib
import json
import os
from pathlib import Path

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.document_revision import revise_docx, rollback_revision
from praxile.security import SafetyPolicy
from praxile.runtime import AgentRuntime

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def setup_document(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Original")
    document.save(tmp_path / "memo.docx")
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    before = (tmp_path / "memo.docx").read_bytes()
    return config, SafetyPolicy(config), before, hashlib.sha256(before).hexdigest()


def spec():
    return {"blocks": [{"type": "paragraph", "text": "Revised shorter memo"}]}


def test_revision_and_rollback_preserve_binary_bytes(tmp_path):
    config, safety, before, digest = setup_document(tmp_path)
    runtime = AgentRuntime(config)
    try:
        result = runtime.tools.execute({"type": "revise_docx", "path": "memo.docx", "document": spec(),
            "expected_sha256": digest}, task_id="revision", step=1)
        assert result["status"] == "success"
        after = (tmp_path / "memo.docx").read_bytes()
        assert after != before
        assert (tmp_path / result["data"]["backup_path"]).read_bytes() == before
        trajectory = {"actions": [{"action_type": "revise_docx", "status": "success", "observation": result}]}
        (tmp_path / "memo.docx").write_bytes(b"human edited")
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "skipped_modified"
        (tmp_path / "memo.docx").write_bytes(after)
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "restored_backup"
        assert (tmp_path / "memo.docx").read_bytes() == before
        assert not list(tmp_path.glob(".praxile-revision-*"))
    finally:
        runtime.tools.close()


def test_revision_conflict_during_render_never_overwrites(tmp_path, monkeypatch):
    import praxile.document_revision as module
    config, safety, before, digest = setup_document(tmp_path)
    assert revise_docx(config, safety, "memo.docx", spec(), "a" * 64).status == "blocked"
    original = module.create_docx
    def render(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "memo.docx").write_bytes(b"concurrent human edit")
        return result
    monkeypatch.setattr(module, "create_docx", render)
    result = revise_docx(config, safety, "memo.docx", spec(), digest)
    assert result.status == "blocked"
    assert (tmp_path / "memo.docx").read_bytes() == b"concurrent human edit"
    assert not list(tmp_path.glob(".praxile-revision-*"))


def test_backup_corruption_and_path_escape_are_rejected(tmp_path):
    config, safety, before, digest = setup_document(tmp_path)
    alias = tmp_path / "alias.docx"
    alias.symlink_to(tmp_path / "memo.docx")
    assert revise_docx(config, safety, "alias.docx", spec(), digest).status == "failure"
    result = revise_docx(config, safety, "memo.docx", spec(), digest)
    assert result.status == "success"
    after = (tmp_path / "memo.docx").read_bytes()
    assert rollback_revision(config, safety, {**result.data, "backup_path": "../outside.docx"})["mode"] == "blocked"
    (tmp_path / result.data["backup_path"]).write_bytes(b"corrupt")
    assert rollback_revision(config, safety, result.data)["mode"] == "backup_digest_mismatch"
    assert (tmp_path / "memo.docx").read_bytes() == after


def test_publication_failure_preserves_original_and_backup(tmp_path, monkeypatch):
    config, safety, before, digest = setup_document(tmp_path)
    original_replace = Path.replace
    def fail(source, target):
        if source.name == "revision.docx":
            raise OSError("fixture publication failure")
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", fail)
    assert revise_docx(config, safety, "memo.docx", spec(), digest).status == "failure"
    assert (tmp_path / "memo.docx").read_bytes() == before
    backups = list((tmp_path / ".praxile/backups/document-revisions").glob("*.docx"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert not list(tmp_path.glob(".praxile-revision-*"))


def test_runtime_dry_run_never_publishes_revision(tmp_path, monkeypatch):
    config, safety, before, digest = setup_document(tmp_path)
    runtime = AgentRuntime(config)
    monkeypatch.setattr(runtime.router, "chat", lambda *a, **k: {"content": json.dumps({
        "type": "revise_docx", "path": "memo.docx", "document": spec(), "expected_sha256": digest}),
        "usage": {}, "route": {}})
    runtime.run("Shorten the memo", dry_run=True, max_steps=1, use_experience=False)
    assert (tmp_path / "memo.docx").read_bytes() == before
    assert not list((tmp_path / ".praxile/backups/document-revisions").glob("*.docx"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX renderer")
def test_runtime_repairs_page_limit_then_completes(tmp_path, monkeypatch):
    renderer = os.environ.get("PRAXILE_TEST_LIBREOFFICE")
    if not renderer:
        pytest.skip("Set PRAXILE_TEST_LIBREOFFICE for real rendering")
    docx = pytest.importorskip("docx")
    pytest.importorskip("pypdf")
    original = docx.Document()
    original.add_paragraph("First page")
    original.add_page_break()
    original.add_paragraph("Second page")
    original.save(tmp_path / "memo.docx")
    before = (tmp_path / "memo.docx").read_bytes()
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["documents"]["renderer_executable"] = renderer
    runtime = AgentRuntime(config)
    actions = iter([
        {"type": "finish", "summary": "claimed one page"},
        {"type": "revise_docx", "path": "memo.docx", "document": spec(), "expected_sha256": hashlib.sha256(before).hexdigest()},
        {"type": "finish", "summary": "shortened and verified"},
    ])
    monkeypatch.setattr(runtime.router, "chat", lambda *a, **k: {
        "content": json.dumps(next(actions)), "usage": {}, "route": {}})
    contract = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "memo.docx", "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "structure": {"max_pages": 1}}]}
    result = runtime.run("Prepare a one-page memo", deliverable_contract=contract, max_steps=3, use_experience=False)
    assert result["result"]["status"] == "completed"
    gates = [a for a in result["actions"] if a["action_type"] == "deliverable_finish_gate"]
    assert [a["status"] for a in gates] == ["failure", "success"]
    assert result["deliverable_verification"]["data"]["outputs"][0]["layout_check"]["data"]["pages"] == 1
    assert runtime.project.rollback_task(result)[0]["mode"] == "restored_backup"
    assert (tmp_path / "memo.docx").read_bytes() == before
