import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.environment import FileSystemEnv
from praxile.security import SafetyPolicy


@pytest.mark.parametrize("suffix", [".docx", ".xlsx", ".pptx", ".pdf", ".DOCX"])
@pytest.mark.parametrize("exists", [False, True])
def test_text_writer_cannot_create_or_corrupt_binary_documents(tmp_path, suffix, exists):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    fs = FileSystemEnv(config, SafetyPolicy(config))
    target = tmp_path / ("output" + suffix)
    original = b"PK\x03\x04\xff\x00binary"
    if exists:
        target.write_bytes(original)
    result = fs.write_file(target.name, "not a document", task_id="fixture", step=1)
    assert result.status == "failure"
    assert result.data["changed"] is False
    assert target.exists() is exists
    if exists:
        assert target.read_bytes() == original
    assert not config.paths.backups.exists()


def test_text_writer_still_handles_text(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    fs = FileSystemEnv(config, SafetyPolicy(config))
    result = fs.write_file("memo.md", "# Memo", task_id="fixture", step=1)
    assert result.status == "success"
    assert (tmp_path / "memo.md").read_text() == "# Memo"
