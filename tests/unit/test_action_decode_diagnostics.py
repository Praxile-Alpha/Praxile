import hashlib
import json

import pytest

from praxile.action_protocol import decode_action


@pytest.mark.parametrize("content,reason", [
    ('<think>secret', 'unclosed_reasoning'),
    ('<think><think>secret</think>{}', 'nested_reasoning'),
    ('<think>secret</think>', 'missing_final_action'),
    ('[]', 'invalid_json_object'),
    ('{"path":"private detail", "type":}', 'invalid_json_object'),
])
def test_decode_failure_has_digest_and_reason_without_source_text(content, reason):
    action, evidence = decode_action(content)
    assert action is None
    assert evidence['reason'] == reason
    assert evidence['content_chars'] == len(content)
    assert evidence['content_sha256'] == hashlib.sha256(content.encode()).hexdigest()
    assert 'secret' not in json.dumps(evidence)
    assert 'private detail' not in json.dumps(evidence)


def test_long_revision_is_not_rejected_for_length():
    action = {'type': 'revise_docx', 'path': 'output/report.docx',
              'expected_sha256': 'a' * 64,
              'document': {'blocks': [{'type': 'paragraph', 'text': 'x' * 3000}] * 20}}
    decoded, evidence = decode_action('<think>Planning</think>\n' + json.dumps(action))
    assert decoded == action
    assert evidence['reason'] == 'parsed'
    assert evidence['reasoning_blocks_removed'] == 1
    assert evidence['final_chars'] > 60000


def test_parser_offset_is_retained():
    _, evidence = decode_action('{"type":}')
    assert 'at 8' in evidence['parser_error']
