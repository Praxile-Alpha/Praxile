from copy import deepcopy
from types import SimpleNamespace

import pytest

from praxile.eval.v2.alignment import align_operations
from praxile.eval.v2.behavior_probe import probe_repetition
from praxile.eval.v2.improvement import improvement_episodes


def prediction():
    return {'measurement': {'kind': 'repeated_exact_operations', 'operation': 'search', 'direction': 'decrease'}}


def event(name, pattern='shared base'):
    return SimpleNamespace(event_id=name, type='TOOL_CALL', payload={'action': {'type': 'search', 'pattern': pattern}})


def alignment():
    return align_operations([event('a1'), event('a2'), event('a3')], [event('b1')])


def test_repetition_uses_event_identity_not_positional_pairing():
    result = probe_repetition(prediction(), alignment())
    assert result['baseline'] == 2 and result['candidate'] == 0
    assert result['delta'] == -2 and result['prediction_verified']
    assert result['event_evidence'][0]['baseline_event_ids'] == ['a1', 'a2', 'a3']
    assert not result['causal_attribution']
    assert 'shared base' not in str(result)


@pytest.mark.parametrize('change', ['missing', 'unsupported', 'duplicate', 'bad_measurement', 'absent_operation'])
def test_incomplete_or_ambiguous_measurements_abstain(change):
    evidence = alignment()
    expected = prediction()
    if change == 'missing':
        evidence = None
    elif change == 'unsupported':
        evidence['unsupported']['baseline'] = ['unknown']
    elif change == 'duplicate':
        evidence['unpaired'][0]['baseline_event_ids'].append('a1')
    elif change == 'bad_measurement':
        expected['measurement']['operation'] = []
    else:
        expected['measurement']['operation'] = 'read_file'
    result = probe_repetition(expected, evidence)
    assert result['status'] == 'abstained'
    assert not result['prediction_verified']


def test_behavior_improvement_is_separate_from_task_failure():
    candidate = SimpleNamespace(candidate_id='c', digest='sha256:c', candidate_type='context_policy',
                                expected_effect=prediction())
    left = {'tasks': [{'task_id': 't', 'trace_id': 'left', 'evaluator': {'resolved': False}}]}
    right = deepcopy(left)
    right['tasks'][0].update(trace_id='right', context_activation={'injected_context_items': ['c']})
    comparison = {'task_results': [{'task_id': 't', 'operation_alignment': alignment()}]}
    episode = improvement_episodes('experiment', candidate, left, right, comparison, {'valid': True})[0]
    assert episode['verdict'] == 'no_observed_effect'
    assert episode['behavior_probe']['verdict'] == 'helped'
    assert episode['behavior_probe']['prediction_verified']
    assert not episode['promotion_eligible']
    assert episode['experience_entry'] == 'NO_ENTRY'
    right['tasks'][0]['context_activation'] = {}
    episode = improvement_episodes('experiment', candidate, left, right, comparison, {'valid': True})[0]
    assert episode['behavior_probe']['verdict'] == 'confounded'
    assert not episode['behavior_probe']['prediction_verified']


def test_equal_measurements_do_not_verify_strict_direction():
    result = probe_repetition(prediction(), align_operations([event('a')], [event('b')]))
    assert result['delta'] == 0 and not result['prediction_verified']


def test_known_other_actions_do_not_make_search_probe_unusable():
    edit = SimpleNamespace(event_id='edit', type='TOOL_CALL', payload={'action': {'type': 'edit_file', 'path': 'a'}})
    evidence = align_operations([event('a1'), event('a2'), edit], [event('b1')])
    assert probe_repetition(prediction(), evidence)['prediction_verified']
    truncated = event('partial', '...[truncated]...')
    evidence = align_operations([event('a1'), event('a2'), truncated], [event('b1')])
    assert probe_repetition(prediction(), evidence)['status'] == 'abstained'
