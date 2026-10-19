import hashlib
import io
import tarfile

import pytest

from praxile.eval.archives import extract_dataset
from praxile.eval.v2.schema import EvalSchemaError


def archive(tmp_path, entries):
    path = tmp_path / "input.tar.gz"
    with tarfile.open(path, "w:gz") as bundle:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o4755
            if kind == tarfile.REGTYPE:
                member.size = 4
                bundle.addfile(member, io.BytesIO(b"data"))
            else:
                member.linkname = "/etc/passwd"
                bundle.addfile(member)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_extract_pinned_archive_without_nested_extraction(tmp_path):
    source, digest = archive(tmp_path, [("dataset/workspace.tar.gz", tarfile.REGTYPE)])
    dest = extract_dataset(source, tmp_path / "dataset", sha256=digest)
    assert (dest / "workspace.tar.gz").read_bytes() == b"data"
    assert (dest / "workspace.tar.gz").stat().st_mode & 0o4000 == 0
    with pytest.raises(FileExistsError):
        extract_dataset(source, dest, sha256=digest)


@pytest.mark.parametrize("name,kind", [
    ("../outside", tarfile.REGTYPE), ("/dataset/absolute", tarfile.REGTYPE),
    ("dataset/../outside", tarfile.REGTYPE), ("wrong/file", tarfile.REGTYPE),
    ("dataset/link", tarfile.SYMTYPE), ("dataset/link", tarfile.LNKTYPE),
    ("dataset/fifo", tarfile.FIFOTYPE), ("dataset/drive:c", tarfile.REGTYPE),
])
def test_rejects_unsafe_members_without_partial_destination(tmp_path, name, kind):
    source, digest = archive(tmp_path, [("dataset/first", tarfile.REGTYPE), (name, kind)])
    with pytest.raises(EvalSchemaError):
        extract_dataset(source, tmp_path / "dataset", sha256=digest)
    assert not (tmp_path / "dataset").exists()
    assert not list(tmp_path.glob(".dataset-staging-*"))


def test_digest_and_resource_limits(tmp_path):
    source, digest = archive(tmp_path, [("dataset/a", tarfile.REGTYPE), ("dataset/b", tarfile.REGTYPE)])
    for options in ({"sha256": "0" * 64}, {"sha256": digest, "max_bytes": 7},
                    {"sha256": digest, "max_members": 1}):
        with pytest.raises(EvalSchemaError):
            extract_dataset(source, tmp_path / "dataset", **options)
        assert not (tmp_path / "dataset").exists()


def test_duplicate_member_is_rejected(tmp_path):
    source, digest = archive(tmp_path, [("dataset/a", tarfile.REGTYPE)] * 2)
    with pytest.raises(EvalSchemaError, match="duplicate"):
        extract_dataset(source, tmp_path / "dataset", sha256=digest)
