import hashlib

import pytest

from praxile.eval.native_input_snapshot import snapshot_inputs


@pytest.fixture
def job(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("PUBLIC TASK")
    (task / "tests").mkdir()
    (task / "tests/rubric.json").write_text("PRIVATE RUBRIC")
    wheel = tmp_path / "worker.whl"
    wheel.write_bytes(b"worker")
    return {"tasks": [{"path": str(task)}], "agents": [{"kwargs": {
        "wheel_path": str(wheel), "wheel_sha256": hashlib.sha256(b"worker").hexdigest(),
        "credential_file": str(tmp_path / "never-read-secret")}}]}


def test_snapshot_is_deterministic_and_does_not_include_file_contents(job):
    snapshot = snapshot_inputs(job)
    assert snapshot == snapshot_inputs(job)
    assert "PRIVATE RUBRIC" not in str(snapshot) and "PUBLIC TASK" not in str(snapshot)
    assert len(snapshot["tasks"][0]["entries"]) == 4


@pytest.mark.parametrize("change", ["content", "added", "deleted", "mode"])
def test_detects_task_changes(job, change):
    from pathlib import Path
    before = snapshot_inputs(job)
    root = Path(job["tasks"][0]["path"])
    path = root / "instruction.md"
    if change == "content":
        path.write_text("CHANGED")
    elif change == "added":
        (root / "new").write_text("new")
    elif change == "deleted":
        path.unlink()
    else:
        path.chmod(0o700)
    assert snapshot_inputs(job) != before


@pytest.mark.parametrize("kind", ["file", "directory", "wheel"])
def test_rejects_symlinks_and_wrong_wheel_digest(job, kind):
    from pathlib import Path
    root = Path(job["tasks"][0]["path"])
    if kind == "wheel":
        Path(job["agents"][0]["kwargs"]["wheel_path"]).write_bytes(b"changed")
    else:
        (root / "linked").symlink_to(root / ("instruction.md" if kind == "file" else "tests"))
    with pytest.raises(ValueError):
        snapshot_inputs(job)


@pytest.mark.parametrize("change", ["plugin", "contract", "new_helper"])
def test_official_shared_verifier_inputs_are_included(job, change):
    from pathlib import Path
    root = Path(job["tasks"][0]["path"]).parent
    (root / "dataset.toml").write_text('[dataset]\nid="fixture"\n')
    shared = root / "shared/verifier"
    shared.mkdir(parents=True)
    (shared / "plugin.py").write_text("# private verifier implementation")
    job["verifier"] = {"import_path": "workbuddy_bench.judge:CompositeVerifier"}
    before = snapshot_inputs(job)
    assert before["schema_version"] == "praxile.native_local_inputs.v2"
    assert "private verifier implementation" not in str(before)
    if change == "plugin":
        (shared / "plugin.py").write_text("# changed")
    elif change == "contract":
        (root / "dataset.toml").write_text('[dataset]\nid="different"\n')
    else:
        (shared / "helper.py").write_text("# added")
    assert snapshot_inputs(job) != before


def test_official_verifier_missing_contract_is_not_silently_unfrozen(job):
    job["verifier"] = {"import_path": "workbuddy_bench.judge:CompositeVerifier"}
    with pytest.raises(ValueError, match="dataset.toml"):
        snapshot_inputs(job)


def test_shared_verifier_symlink_rejected(job):
    from pathlib import Path
    root = Path(job["tasks"][0]["path"]).parent
    (root / "dataset.toml").write_text('[dataset]\nid="fixture"\n')
    (root / "shared").symlink_to(root / "task", target_is_directory=True)
    job["verifier"] = {"import_path": "workbuddy_bench.judge:CompositeVerifier"}
    with pytest.raises(ValueError, match="symlink"):
        snapshot_inputs(job)
