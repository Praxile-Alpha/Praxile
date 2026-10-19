import os
import pytest
import subprocess
from unittest.mock import Mock

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config, validate_config
from praxile.document_layout import check_layout
from praxile.security import SafetyPolicy


@pytest.mark.parametrize("value", [True, 0, -1, 2001, "1", None])
def test_layout_page_limits_are_strict(value):
    assert not ActionSchemaRegistry().validate({"type": "check_document_layout", "path": "x.docx", "max_pages": value})[0]


def test_layout_is_explicit_not_concurrent_and_not_model_executable(tmp_path):
    schema = ActionSchemaRegistry()
    action = {"type": "check_document_layout", "path": "x.docx", "max_pages": 1}
    assert schema.validate(action)[0]
    assert not schema.validate({**action, "executable": "/bin/sh"})[0]
    assert not schema.validate({"type": "batch", "actions": [action]})[0]
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    safety = SafetyPolicy(config)
    assert check_layout(config, safety, "x.docx").status == "blocked"
    assert check_layout(config, safety, "../outside.docx").status == "blocked"


@pytest.mark.parametrize("documents", [[], {"renderer_executable": True}, {"renderer_executable": "soffice"}])
def test_layout_config_requires_trusted_absolute_path(tmp_path, documents):
    config = default_config(tmp_path)
    config["documents"] = documents
    with pytest.raises(ValueError, match="documents"):
        validate_config(config)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group worker")
def test_layout_timeout_reaps_group_and_does_not_forward_keys(tmp_path, monkeypatch):
    from praxile import document_layout
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["documents"]["renderer_executable"] = "/trusted/soffice"
    (tmp_path / "x.docx").write_bytes(b"snapshot")
    child = Mock(pid=12345)
    child.wait.side_effect = [subprocess.TimeoutExpired("worker", 45), -9]
    popen = Mock(return_value=child)
    kill = Mock()
    monkeypatch.setattr(document_layout.subprocess, "Popen", popen)
    monkeypatch.setattr(document_layout.os, "killpg", kill)
    monkeypatch.setenv("PRAXILE_BENCHMARK_API_KEY", "must-not-inherit")
    result = check_layout(config, SafetyPolicy(config), "x.docx", max_pages=1)
    assert result.status == "failure"
    assert "timed out" in result.output
    assert child.wait.call_count == 2
    kill.assert_called_once()
    assert "PRAXILE_BENCHMARK_API_KEY" not in popen.call_args.kwargs["env"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group worker")
def test_missing_docx_is_not_reported_as_renderer_failure(tmp_path, monkeypatch):
    from praxile import document_layout
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data['documents']['renderer_executable'] = '/trusted/soffice'
    popen = Mock()
    monkeypatch.setattr(document_layout.subprocess, 'Popen', popen)
    result = check_layout(config, SafetyPolicy(config), 'missing.docx', max_pages=1)
    assert result.status == 'failure'
    assert result.data == {'reason': 'input_missing', 'rendered': False}
    popen.assert_not_called()
