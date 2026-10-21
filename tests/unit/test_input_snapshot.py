import hashlib

import pytest

from praxile.eval.contracts import InputArtifact
from praxile.eval.input_snapshot import freeze_public_inputs, verify_public_inputs
from praxile.eval import input_snapshot


def artifact(path="nested/task.txt", source="public.txt", content=b"task"):
    return InputArtifact(path, source, "sha256:" + hashlib.sha256(content).hexdigest())


def test_snapshot_excludes_undeclared_files_and_freezes_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "public.txt").write_bytes(b"task")
    (source / "rubric.txt").write_text("private")
    target = tmp_path / "snapshot"
    inputs = (artifact(),)
    identity = freeze_public_inputs(source, inputs, target)
    (source / "public.txt").write_text("changed")
    verify_public_inputs(target, inputs, identity)
    assert (target / "nested/task.txt").read_bytes() == b"task"
    assert not (target / "rubric.txt").exists()
    (target / "extra").write_text("private")
    with pytest.raises(ValueError, match="contents changed"):
        verify_public_inputs(target, inputs, identity)


@pytest.mark.parametrize("kind", ["hash", "symlink", "hardlink", "outside"])
def test_invalid_source_never_publishes_snapshot(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    original = tmp_path / "original"
    original.write_bytes(b"task")
    ref = "public.txt"
    if kind == "symlink":
        (source / ref).symlink_to(original)
    elif kind == "hardlink":
        (source / ref).hardlink_to(original)
    else:
        (source / ref).write_bytes(b"wrong")
    if kind == "outside":
        ref = "../original"
    with pytest.raises(ValueError):
        freeze_public_inputs(source, (artifact(source=ref),), tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize("paths", [("a", "a/b"), ("a", "A")])
def test_snapshot_rejects_path_collisions(tmp_path, paths):
    with pytest.raises(ValueError):
        freeze_public_inputs(tmp_path, tuple(artifact(path=p) for p in paths), tmp_path / "snapshot")


def test_snapshot_mutation_and_root_replacement_rejected(tmp_path):
    (tmp_path / "public.txt").write_bytes(b"task")
    inputs = (artifact(),)
    root = tmp_path / "snapshot"
    identity = freeze_public_inputs(tmp_path, inputs, root)
    target = root / "nested/task.txt"
    target.chmod(0o600)
    target.write_bytes(b"evil")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_public_inputs(root, inputs, identity)
    root.rename(tmp_path / "old")
    root.mkdir()
    with pytest.raises(ValueError, match="replaced"):
        verify_public_inputs(root, inputs, identity)


def test_snapshot_total_budget_applies_across_files(tmp_path, monkeypatch):
    monkeypatch.setattr(input_snapshot, "MAX_INPUT_BYTES", 6)
    (tmp_path / "public.txt").write_bytes(b"task")
    with pytest.raises(ValueError, match="byte limit"):
        freeze_public_inputs(tmp_path, (artifact(path="one"), artifact(path="two")), tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


def test_snapshot_write_failure_does_not_publish_partial_directory(tmp_path, monkeypatch):
    from pathlib import Path
    (tmp_path / "public.txt").write_bytes(b"task")
    original = Path.write_bytes
    def fail_second(path, data):
        if path.name == "two":
            raise OSError("disk full")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_second)
    with pytest.raises(OSError, match="disk full"):
        freeze_public_inputs(tmp_path, (artifact(path="one"), artifact(path="two")), tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
    assert not list(tmp_path.glob(".inputs-*"))
