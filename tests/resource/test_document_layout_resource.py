import hashlib
import os

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.document_layout import check_layout
from praxile.security import SafetyPolicy
from praxile.tools import ToolRegistry

pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX renderer lifecycle")]


def test_real_renderer_detects_page_overflow_and_revision(tmp_path):
    renderer = os.environ.get("PRAXILE_TEST_LIBREOFFICE")
    if not renderer:
        pytest.skip("Set PRAXILE_TEST_LIBREOFFICE to a trusted absolute renderer path")
    docx = pytest.importorskip("docx")
    pytest.importorskip("pypdf")
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["documents"]["renderer_executable"] = renderer
    original = docx.Document()
    original.add_paragraph("Checklist first page")
    original.add_page_break()
    original.add_paragraph("Checklist second page")
    original.save(tmp_path / "checklist.docx")
    before = (tmp_path / "checklist.docx").read_bytes()
    tools = ToolRegistry(config)
    try:
        result = tools.execute({"type": "check_document_layout", "path": "checklist.docx", "max_pages": 1}, task_id="layout", step=1)
        assert result["status"] == "failure"
        assert result["data"]["pages"] == 2
        assert result["data"]["source_sha256"] == hashlib.sha256(before).hexdigest()
        assert result["data"]["visual_quality_verified"] is False
        assert (tmp_path / "checklist.docx").read_bytes() == before
        revised = docx.Document()
        revised.add_paragraph("Short checklist")
        revised.save(tmp_path / "revised.docx")
        assert check_layout(config, SafetyPolicy(config), "revised.docx", max_pages=1).data["page_limit_met"] is True
    finally:
        tools.close()
