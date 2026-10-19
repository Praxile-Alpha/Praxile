import os

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.documents import create_docx
from praxile.document_layout import check_layout
from praxile.security import SafetyPolicy

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_compact_writer_preserves_text_and_has_bounded_formatting(tmp_path):
    docx = pytest.importorskip('docx')
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    rows = [['Item', 'Owner', 'Timing', 'Check']] + [
        [f'Packet {i}', 'Records', 'Before release', 'Confirm signed approval'] for i in range(12)]
    spec = {'layout': 'compact', 'blocks': [
        {'type': 'heading', 'text': 'Packet checklist', 'level': 1},
        {'type': 'table', 'rows': rows}]}
    observation = create_docx(config, SafetyPolicy(config), 'checklist.docx', spec)
    assert observation.status == 'success'
    document = docx.Document(tmp_path / 'checklist.docx')
    assert document.styles['Normal'].font.size.pt == 10
    assert document.sections[0].left_margin.inches == pytest.approx(0.6)
    assert [[cell.text for cell in row.cells] for row in document.tables[0].rows] == rows
    assert document.tables[0].rows[0]._tr.xpath('./w:trPr/w:tblHeader')
    for row in document.tables[0].rows:
        assert row._tr.xpath('./w:trPr/w:cantSplit')
        assert row.cells[0].paragraphs[0].runs[0].font.size.pt == 9.5


@pytest.mark.skipif(os.name != 'posix', reason='POSIX renderer')
def test_compact_layout_real_render(tmp_path):
    renderer = os.environ.get('PRAXILE_TEST_LIBREOFFICE')
    if not renderer:
        pytest.skip('Set PRAXILE_TEST_LIBREOFFICE for real rendering')
    pytest.importorskip('docx')
    pytest.importorskip('pypdf')
    test_compact_writer_preserves_text_and_has_bounded_formatting(tmp_path)
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data['documents']['renderer_executable'] = renderer
    observation = check_layout(config, SafetyPolicy(config), 'checklist.docx', max_pages=1)
    assert observation.status == 'success'
    assert observation.data['pages'] == 1
