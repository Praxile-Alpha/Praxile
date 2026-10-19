import json
from dataclasses import asdict

import pytest

from praxile.eval.workbuddy import WorkBuddyDataset, prepare_copy
from praxile.eval.v2.schema import EvalSchemaError


@pytest.fixture
def package(tmp_path):
    files = {
        "dataset.toml": '[dataset]\nid="wb-bench-office-v1.0"\nschema="workbuddy.dataset.v1"\ntask_count=1\n[layout]\ntask_root="tasks"\n[verifier]\nschema="workbuddy.verifier.v1"\nengine="composite"\n',
        "shared/verifier/plugin.py": "# private official plugin\n",
        "tasks/example/task.toml": '[task]\nname="workbuddy/example"\n[agent]\ntimeout_sec=60\n[verifier]\ntimeout_sec=60\n',
        "tasks/example/instruction.md": "Create the requested document.",
        "tasks/example/environment/workspace.tar.gz": "opaque nested archive",
        "tasks/example/tests/rubric.json": '{"private":"hidden answer"}',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def load(root, **kwargs):
    return WorkBuddyDataset(root, revision="a" * 40, **kwargs)


def test_readiness_and_private_projection(package):
    dataset = load(package)
    assert dataset.preparation_reasons == {"example": [
        "bind_official_composite_verifier", "configure_nonroot_task_users"]}
    task, = dataset.select(("example",))
    view = json.dumps(asdict(task.to_adapter_task("/workspace", trace_id="t", run_id="r")))
    for secret in ("hidden answer", "rubric", "tests", str(package)):
        assert secret not in view
    config = package / "tasks/example/task.toml"
    config.write_text(config.read_text().replace('[agent]', '[agent]\nuser="dev"').replace(
        '[verifier]', '[verifier]\nuser="dev"\nimport_path="workbuddy_bench.judge:CompositeVerifier"'))
    assert load(package).preparation_required == []


def test_inventory_freezes_private_materials(package):
    before = load(package)
    assert load(package, expected_digest=before.digest).digest == before.digest
    (package / "tasks/example/tests/rubric.json").write_text("changed")
    with pytest.raises(EvalSchemaError, match="digest mismatch"):
        load(package, expected_digest=before.digest)
    assert load(package).tasks["example"].evaluator != before.tasks["example"].evaluator


@pytest.mark.parametrize("ids", [("missing",), ("example", "example")])
def test_selection_rejects_unknown_and_duplicate_ids(package, ids):
    with pytest.raises(EvalSchemaError, match="unknown or duplicate"):
        load(package).select(ids)


def test_rejects_unapproved_verifier(package):
    config = package / "tasks/example/task.toml"
    config.write_text(config.read_text() + 'import_path="fake:Verifier"\n')
    with pytest.raises(EvalSchemaError, match="CompositeVerifier"):
        load(package)


def test_rejects_symlink_and_mutable_revision(package):
    with pytest.raises(EvalSchemaError, match="immutable"):
        WorkBuddyDataset(package, revision="main")
    (package / "link").symlink_to(package / "dataset.toml")
    with pytest.raises(EvalSchemaError):
        load(package)


def test_official_preparation_on_copy(package, tmp_path_factory):
    pytest.importorskip("workbuddy_bench.runner.prepare_tasks")
    original = load(package)
    destination = tmp_path_factory.mktemp("prepared") / "dataset"
    prepared = prepare_copy(package, destination, revision="a" * 40, expected_digest=original.digest)
    assert prepared.preparation_required == []
    assert load(package).digest == original.digest
    record = json.loads((destination / "praxile-preparation.json").read_text())
    assert record["source_inventory_sha256"] == original.digest
    assert record["task_users_changed"] == 1
    assert record["verifier_bindings_changed"] == 1
    assert len(record["helper_sha256"]) == 64
    with pytest.raises(FileExistsError):
        prepare_copy(package, destination, revision="a" * 40, expected_digest=original.digest)
    with pytest.raises(EvalSchemaError, match="outside"):
        prepare_copy(package, package / "nested", revision="a" * 40, expected_digest=original.digest)


def test_preparation_failure_does_not_publish_or_modify_source(package, tmp_path_factory, monkeypatch):
    helpers = pytest.importorskip("workbuddy_bench.runner.prepare_tasks")
    original = load(package)
    parent = tmp_path_factory.mktemp("failure")

    def fail(path, **kwargs):
        (path / "example/task.toml").write_text("partial modification")
        raise OSError("simulated preparation failure")

    monkeypatch.setattr(helpers, "ensure_task_user", fail)
    with pytest.raises(OSError, match="simulated"):
        prepare_copy(package, parent / "dataset", revision="a" * 40, expected_digest=original.digest)
    assert not (parent / "dataset").exists()
    assert not list(parent.glob(".workbuddy-prepare-*"))
    assert load(package).digest == original.digest
