import os

import pytest

from praxile.eval.artifact_read import read_artifact

pytestmark = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative POSIX reads")


def read(root, path, **kwargs):
    info = root.stat()
    return read_artifact(root, path, root_identity=(info.st_dev, info.st_ino), **kwargs)


def test_nested_and_empty_files_are_read_with_explicit_budget(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/out").write_bytes(b"okay")
    (tmp_path / "empty").touch()
    assert read(tmp_path, "nested/out", max_bytes=4) == b"okay"
    assert read(tmp_path, "empty", max_bytes=1) == b""
    with pytest.raises(ValueError, match="byte limit"):
        read(tmp_path, "nested/out", max_bytes=3)


@pytest.mark.parametrize("path", ["", ".", "../escape", "/absolute", "a//b", "a/./b", "a\\b", "a\0b", None])
def test_invalid_paths_fail_before_open(tmp_path, path):
    with pytest.raises(ValueError):
        read(tmp_path, path)


@pytest.mark.parametrize("limit", [True, 0, -1, 256 * 1024**2 + 1, "100"])
def test_invalid_budget_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match="limit"):
        read(tmp_path, "out", max_bytes=limit)


def test_parent_links_final_links_hardlinks_and_fifo_are_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_bytes(b"private")
    (root / "parent").symlink_to(tmp_path, target_is_directory=True)
    (root / "final").symlink_to(tmp_path / "secret")
    os.link(tmp_path / "secret", root / "hard")
    os.mkfifo(root / "fifo")
    for path in ("parent/secret", "final", "hard", "fifo"):
        with pytest.raises(ValueError):
            read(root, path)


def test_root_identity_replacement_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = root.stat()
    root.rename(tmp_path / "original")
    root.mkdir()
    (root / "out").write_bytes(b"replacement")
    with pytest.raises(ValueError, match="root replaced"):
        read_artifact(root, "out", root_identity=(info.st_dev, info.st_ino))


def test_link_swap_at_open_cannot_read_external_content(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "out"
    target.write_bytes(b"initial")
    (tmp_path / "secret").write_bytes(b"secret")
    original = os.open
    def swapped(path, *args, **kwargs):
        if path == "out":
            target.unlink()
            target.symlink_to(tmp_path / "secret")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapped})
    with pytest.raises(ValueError):
        read(root, "out")


@pytest.mark.parametrize("mode", ["replace", "grow", "rewrite"])
def test_mutation_during_read_is_rejected(tmp_path, monkeypatch, mode):
    target = tmp_path / "out"
    target.write_bytes(b"old")
    original = os.read
    changed = False
    def mutate(fd, count):
        nonlocal changed
        data = original(fd, count)
        if not changed:
            changed = True
            if mode == "replace":
                target.unlink()
                target.write_bytes(b"new")
            else:
                target.write_bytes(b"longer" if mode == "grow" else b"new")
        return data
    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(ValueError, match="changed|limit"):
        read(tmp_path, "out", max_bytes=3)
