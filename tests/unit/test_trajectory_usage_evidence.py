import json

import pytest

from praxile.trajectory import TrajectoryLogger


def test_role_totals_keep_unknown_fields_separate_from_known_subtotals():
    logger = TrajectoryLogger('task', {})
    logger.add_model_cost({'prompt_tokens': 10, 'completion_tokens': 2}, route={'purpose': 'coding'})
    logger.add_model_cost({'input_tokens': 5}, route={'purpose': 'experience_reflection'})
    evidence = logger.data['cost']['usage_evidence']
    assert evidence['known_tokens'] == {'prompt_tokens': 15, 'completion_tokens': 2}
    assert evidence['complete_tokens'] == {'prompt_tokens': 15, 'completion_tokens': None}
    assert evidence['by_purpose']['coding']['complete_tokens']['completion_tokens'] == 2
    assert evidence['by_purpose']['experience_reflection']['complete_tokens']['completion_tokens'] is None
    assert evidence['cost_usd'] is None


@pytest.mark.parametrize('usage', [None, [], {'prompt_tokens': True}, {'prompt_tokens': -1},
                                   {'prompt_tokens': '12'}, {'prompt_tokens': 1, 'input_tokens': 2}])
def test_invalid_usage_does_not_crash_or_count_as_reported_zero(usage):
    logger = TrajectoryLogger('task', {})
    logger.add_model_cost(usage)
    assert logger.data['cost']['prompt_tokens'] == 0
    assert logger.data['cost']['usage_evidence']['complete_tokens']['prompt_tokens'] is None


def test_usage_is_detached_and_cannot_be_replaced_by_route_metadata():
    logger = TrajectoryLogger('task', {})
    usage = {'prompt_tokens': 0, 'completion_tokens': 0}
    logger.add_model_cost(usage, route={'usage': {'prompt_tokens': 999}, 'status': 'forged'})
    usage['prompt_tokens'] = 999
    assert logger.data['model_routing']['calls'][0]['usage']['prompt_tokens'] == 0
    assert logger.data['model_routing']['calls'][0]['status'] == 'success'
    assert logger.data['cost']['usage_evidence']['complete_tokens']['prompt_tokens'] == 0


def test_resume_legacy_missing_calls_never_becomes_complete():
    original = TrajectoryLogger('task', {})
    original.add_model_cost({'prompt_tokens': 1, 'completion_tokens': 2})
    original.data['cost']['model_calls'] = 3
    restored = TrajectoryLogger.from_data(json.loads(json.dumps(original.data)))
    restored.add_model_cost({'prompt_tokens': 3, 'completion_tokens': 4}, route={'purpose': 'coding'})
    evidence = restored.data['cost']['usage_evidence']
    assert evidence['calls'] == 4 and evidence['unattributed_calls'] == 2
    assert evidence['complete_tokens'] == {'prompt_tokens': None, 'completion_tokens': None}
