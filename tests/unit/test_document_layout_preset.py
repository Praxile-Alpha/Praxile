import pytest

from praxile.document_spec import validate_docx_spec, docx_spec_prompt


@pytest.mark.parametrize('layout', ['standard', 'compact'])
def test_named_layout_is_supported(layout):
    spec = {'layout': layout, 'blocks': [{'type': 'paragraph', 'text': 'Content'}]}
    assert validate_docx_spec(spec) is spec
    assert '"layout":"compact"' in docx_spec_prompt()


@pytest.mark.parametrize('layout', [None, True, 1, {}, [], 'tiny', 'file:///template'])
def test_layout_cannot_inject_arbitrary_settings(layout):
    with pytest.raises(ValueError, match='layout'):
        validate_docx_spec({'layout': layout, 'blocks': [{'type': 'paragraph', 'text': 'Content'}]})
