import hashlib
import json

import pytest

from praxile.eval.native_artifacts import audit_output


@pytest.fixture
def retained(tmp_path):
    target = tmp_path / "artifacts/workspace/output"
    target.mkdir(parents=True)
    (target / "result.txt").write_bytes(b"result")
    (tmp_path / "artifacts/manifest.json").write_text(json.dumps([{
        "source": "/workspace/output", "destination": "artifacts/workspace/output",
        "type": "directory", "status": "ok", "service": None}]))
    return tmp_path, {"config": {"agent": {"kwargs": {"output_directory": "output"}}}}


def test_retention_hashes_files_not_correctness(retained):
    root, trial = retained
    report = audit_output(root, trial)
    assert report["status"] == "retained" and report["content_verified"] is False
    assert report["files"] == [{"path": "result.txt", "size": 6, "sha256": hashlib.sha256(b"result").hexdigest()}]
    (root / "artifacts/workspace/output/result.txt").write_bytes(b"different")
    assert audit_output(root, trial)["files_sha256"] != report["files_sha256"]


def test_missing_and_empty_are_not_retained(retained):
    root, trial = retained
    assert audit_output(root, {})["status"] == "not_requested"
    (root / "artifacts/workspace/output/result.txt").unlink()
    assert audit_output(root, trial)["status"] == "empty"
    (root / "artifacts/workspace/output").rmdir()
    assert audit_output(root, trial)["status"] == "missing_files"
    (root / "artifacts/manifest.json").write_text("[]")
    assert audit_output(root, trial)["status"] == "not_collected"
    (root / "artifacts/manifest.json").unlink()
    assert audit_output(root, trial)["status"] == "missing_manifest"


@pytest.mark.parametrize("attack", ["symlink", "escape", "duplicate", "fifo"])
def test_rejects_unsafe_artifacts(retained, attack):
    root, trial = retained
    path = root / "artifacts/workspace/output/result.txt"
    manifest = root / "artifacts/manifest.json"
    if attack == "symlink":
        path.unlink()
        path.symlink_to(manifest)
    elif attack == "fifo":
        import os
        path.unlink()
        os.mkfifo(path)
    else:
        entries = json.loads(manifest.read_text())
        if attack == "escape":
            entries[0]["destination"] = "../escape"
        else:
            entries *= 2
        manifest.write_text(json.dumps(entries))
    with pytest.raises(ValueError):
        audit_output(root, trial)
