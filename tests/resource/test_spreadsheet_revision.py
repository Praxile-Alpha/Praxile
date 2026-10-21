import hashlib
import json
from pathlib import Path

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.documents import create_xlsx
from praxile.document_revision import revise_xlsx, rollback_revision
from praxile.runtime import AgentRuntime
from praxile.security import SafetyPolicy

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def workbook(name="Inventory", value=2):
    return {"sheets": [{"name": name, "rows": [["Count"], [value]]}]}


@pytest.fixture
def original(tmp_path):
    pytest.importorskip("openpyxl")
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    safety = SafetyPolicy(config)
    assert create_xlsx(config, safety, "inventory.xlsx", workbook("Draft")).status == "success"
    raw = (tmp_path / "inventory.xlsx").read_bytes()
    return config, safety, raw, hashlib.sha256(raw).hexdigest()


def test_workbook_revision_roundtrip_and_rollback(original, tmp_path):
    import openpyxl
    config, safety, before, digest = original
    result = revise_xlsx(config, safety, "inventory.xlsx", workbook(), digest)
    assert result.status == "success"
    target = tmp_path / "inventory.xlsx"
    after = target.read_bytes()
    loaded = openpyxl.load_workbook(target)
    try:
        assert loaded.sheetnames == ["Inventory"]
        assert loaded["Inventory"]["A2"].value == 2
    finally:
        loaded.close()
    assert (tmp_path / result.data["backup_path"]).read_bytes() == before
    target.write_bytes(b"human edit")
    assert rollback_revision(config, safety, result.data)["mode"] == "skipped_modified"
    target.write_bytes(after)
    assert rollback_revision(config, safety, result.data)["mode"] == "restored_backup"
    assert target.read_bytes() == before


def test_stale_or_concurrent_revision_is_not_published(original, tmp_path, monkeypatch):
    import praxile.document_revision as module
    config, safety, before, digest = original
    assert revise_xlsx(config, safety, "inventory.xlsx", workbook(), "a" * 64).status == "blocked"
    create = module.create_xlsx
    def concurrent(*args, **kwargs):
        result = create(*args, **kwargs)
        (tmp_path / "inventory.xlsx").write_bytes(b"human edit during rendering")
        return result
    monkeypatch.setattr(module, "create_xlsx", concurrent)
    assert revise_xlsx(config, safety, "inventory.xlsx", workbook(), digest).status == "blocked"
    assert (tmp_path / "inventory.xlsx").read_bytes() == b"human edit during rendering"
    assert not list(tmp_path.glob(".praxile-revision-*"))


def test_publication_failure_retains_original_and_backup(original, tmp_path, monkeypatch):
    config, safety, before, digest = original
    replace = Path.replace
    def fail(source, target):
        if source.name == "revision.xlsx":
            raise OSError("publication failed")
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", fail)
    assert revise_xlsx(config, safety, "inventory.xlsx", workbook(), digest).status == "failure"
    assert (tmp_path / "inventory.xlsx").read_bytes() == before
    backups = list((tmp_path / ".praxile/backups/document-revisions").glob("*.xlsx"))
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_corrupt_backup_and_symlinks_rejected(original, tmp_path):
    config, safety, before, digest = original
    (tmp_path / "alias.xlsx").symlink_to(tmp_path / "inventory.xlsx")
    assert revise_xlsx(config, safety, "alias.xlsx", workbook(), digest).status == "failure"
    result = revise_xlsx(config, safety, "inventory.xlsx", workbook(), digest)
    assert result.status == "success"
    (tmp_path / result.data["backup_path"]).write_bytes(b"corrupt")
    assert rollback_revision(config, safety, result.data)["mode"] == "backup_digest_mismatch"


def test_runtime_repairs_missing_sheet_contract_then_completes(original, tmp_path, monkeypatch):
    config, safety, before, digest = original
    runtime = AgentRuntime(config)
    actions = iter([
        {"type": "finish", "summary": "claimed ready"},
        {"type": "revise_xlsx", "path": "inventory.xlsx", "workbook": workbook(), "expected_sha256": digest},
        {"type": "finish", "summary": "repaired required sheet"}])
    monkeypatch.setattr(runtime.router, "chat", lambda *a, **k: {
        "content": json.dumps(next(actions)), "usage": {}, "route": {}})
    contract = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "inventory.xlsx", "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "structure": {"required_sheets": ["Inventory"]}}]}
    try:
        result = runtime.run("Prepare inventory workbook", deliverable_contract=contract, max_steps=3, use_experience=False)
        assert result["result"]["status"] == "completed"
        gates = [a for a in result["actions"] if a["action_type"] == "deliverable_finish_gate"]
        assert [a["status"] for a in gates] == ["failure", "success"]
        assert any(item["action_type"] == "revise_xlsx" for item in result["snapshot_refreshes"])
        assert runtime.project.rollback_task(result)[0]["mode"] == "restored_backup"
        assert (tmp_path / "inventory.xlsx").read_bytes() == before
    finally:
        runtime.tools.close()
