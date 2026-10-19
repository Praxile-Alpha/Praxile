from copy import deepcopy
from types import SimpleNamespace

from praxile.eval.v2.subtask_alignment import align_subtasks as align_raw


def align_subtasks(left, right):
    def stream(items):
        return items + [SimpleNamespace(event_id=item.event_id + '-tool', type='TOOL_CALL', payload={}) for item in items]
    return align_raw(stream(left), stream(right))


def episode(name, goal, state=None):
    return SimpleNamespace(event_id=name, type='SUBTASK_EPISODE', payload={
        'episode': {'episode_id': name + '-episode', 'goal': goal, 'status': 'closed',
                    'action_refs': ['action'], 'state_before': state or {}, 'state_after': {},
                    'claimed_outcome': 'completed'},
        'action_event_map': {'action': name + '-tool'}, 'unresolved_action_refs': []})


def test_subtasks_match_unique_goals_not_step_order():
    result = align_subtasks([episode('a', 'Inspect parser'), episode('b', 'Run tests')],
                            [episode('c', 'Run tests'), episode('d', 'Inspect  parser')])
    pairs = {(p['baseline']['event_id'], p['candidate']['event_id']) for p in result['pairs']}
    assert pairs == {('a', 'd'), ('b', 'c')}
    assert not result['causal_alignment'] and not result['semantic_equivalence_verified']
    assert 'Inspect parser' not in str(result)
    assert all(p['state_equivalence'] == 'unknown' for p in result['pairs'])


def test_matching_snapshots_are_not_claimed_to_be_complete_state():
    a = episode('a', 'Inspect', {'files': ['a.py'], 'git': {'head': '123'}})
    b = episode('b', 'Inspect', {'git': {'head': '123'}, 'files': ['a.py']})
    pair = align_subtasks([a], [b])['pairs'][0]
    assert pair['recorded_state_comparison'] == 'equal_recorded_fields'
    assert pair['state_equivalence'] == 'unknown'
    b.payload['episode']['state_before']['git']['head'] = '456'
    assert align_subtasks([a], [b])['pairs'][0]['recorded_state_comparison'] == 'different_recorded_fields'


def test_repeated_goal_and_different_goal_are_not_forced_into_pairs():
    result = align_subtasks([episode('a', 'Check'), episode('b', 'Check')], [episode('c', 'Check')])
    assert not result['pairs']
    assert result['unpaired'][0]['reason'] == 'ambiguous_goal'
    result = align_subtasks([episode('a', 'Check parser')], [episode('c', 'Check renderer')])
    assert not result['pairs'] and len(result['unpaired']) == 2


def test_unresolved_or_interrupted_episodes_stay_unsupported():
    a = episode('a', 'Check')
    b = deepcopy(a)
    b.event_id = 'b'
    a.payload['unresolved_action_refs'] = ['missing']
    b.payload['episode']['status'] = 'interrupted'
    result = align_subtasks([a, b], [episode('c', 'Check')])
    assert not result['pairs']
    assert {x['reason'] for x in result['unsupported']['baseline']} == {
        'unresolved_action_evidence', 'episode_not_closed'}


def test_nan_snapshot_is_unknown_and_evidence_is_detached():
    a = episode('a', 'Check', {'invalid': float('nan')})
    b = episode('b', 'Check', {'value': 1})
    result = align_subtasks([a], [b])
    pair = result['pairs'][0]
    assert pair['recorded_state_comparison'] == 'unknown'
    a.payload['action_event_map']['action'] = 'tampered'
    assert pair['baseline']['action_event_ids'] == ['a-tool']


def test_dangling_mapping_is_not_accepted_as_evidence():
    result = align_raw([episode('a', 'Check')], [episode('b', 'Check')])
    assert not result['pairs']
    assert result['unsupported']['baseline'][0]['reason'] == 'unresolved_action_evidence'
