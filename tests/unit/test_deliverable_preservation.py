import hashlib

import pytest

from praxile.deliverables import validate_contract, verify_deliverables


def contract(path='memo.bin', content=b'original'):
    return {'schema_version': 'praxile.deliverable_contract.v3', 'source': 'user_request',
            'outputs': [], 'state_equals': {},
            'unchanged_files': {path: hashlib.sha256(content).hexdigest()}}


def test_preservation_checks_final_bytes_not_filename_or_size(tmp_path):
    path = tmp_path / 'memo.bin'
    path.write_bytes(b'original')
    report = verify_deliverables(tmp_path, contract())
    assert report['passed'] is True
    assert report['schema_version'] == 'praxile.deliverable_check.v3'
    assert report['state_transitions_verified'] is False
    path.write_bytes(b'modified')
    report = verify_deliverables(tmp_path, contract())
    assert report['passed'] is False
    assert report['unchanged_files'][0]['reason'] == 'content_changed'
    assert report['preservation_scope'] == 'observed_final_bytes_only'


@pytest.mark.parametrize('kind,reason', [('missing', 'not_found'), ('directory', 'not_a_regular_file'),
                                       ('large', 'size_limit'), ('symlink', 'symlink_forbidden')])
def test_unverifiable_preservation_fails_closed(tmp_path, kind, reason):
    path = tmp_path / 'memo.bin'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'large':
        path.write_bytes(b'x' * 11)
    elif kind == 'symlink':
        (tmp_path / 'real.bin').write_bytes(b'original')
        path.symlink_to(tmp_path / 'real.bin')
    report = verify_deliverables(tmp_path, contract(), max_bytes=10)
    assert not report['passed']
    assert report['unchanged_files'][0]['reason'] == reason


@pytest.mark.parametrize('path', ['../outside', '/tmp/outside', 'a/../../outside'])
def test_preservation_rejects_escape(path):
    with pytest.raises(ValueError):
        validate_contract(contract(path))


@pytest.mark.parametrize('value', [None, True, {}, 'bad', 'A' * 64])
def test_preservation_requires_strict_hash(value):
    request = contract()
    request['unchanged_files']['memo.bin'] = value
    with pytest.raises(ValueError):
        validate_contract(request)


def test_preservation_combines_outputs_and_states(tmp_path):
    (tmp_path / 'memo.bin').write_bytes(b'original')
    request = contract()
    request['outputs'] = [{'path': 'result.json', 'media_type': 'application/json'}]
    request['state_equals'] = {'browser.session_active': True}
    assert not verify_deliverables(tmp_path, request)['passed']
    (tmp_path / 'result.json').write_text('{}')
    assert not verify_deliverables(tmp_path, request)['passed']
    assert verify_deliverables(tmp_path, request, observed_state={'browser': {'session_active': True}})['passed']


def test_old_versions_do_not_silently_accept_new_constraint():
    request = contract()
    for version in ['v1', 'v2']:
        request['schema_version'] = 'praxile.deliverable_contract.' + version
        with pytest.raises(ValueError):
            validate_contract(request)
