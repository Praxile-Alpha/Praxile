"""Non-positional subtask matching with explicit state-evidence limits."""
from collections import defaultdict
from collections.abc import Mapping
import hashlib
import json


def _snapshot_digest(value):
    if not isinstance(value, Mapping) or not value:
        return None
    try:
        raw = json.dumps(dict(value), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).hexdigest()


def align_subtasks(baseline_events, candidate_events):
    def collect(events):
        events = list(events)
        available = {event.event_id for event in events if event.type != 'SUBTASK_EPISODE'}
        groups, unsupported = defaultdict(list), []
        for event in events:
            if event.type != 'SUBTASK_EPISODE':
                continue
            episode = event.payload.get('episode')
            if (not isinstance(episode, Mapping) or not isinstance(episode.get('goal'), str)
                    or not episode['goal'].strip() or len(episode['goal']) > 2000
                    or not isinstance(episode.get('episode_id'), str) or not episode['episode_id']):
                unsupported.append({'event_id': event.event_id, 'reason': 'invalid_episode'})
                continue
            if episode.get('status') != 'closed':
                unsupported.append({'event_id': event.event_id, 'reason': 'episode_not_closed'})
                continue
            refs, mapping = episode.get('action_refs'), event.payload.get('action_event_map')
            if (not isinstance(refs, list) or not isinstance(mapping, Mapping)
                    or event.payload.get('unresolved_action_refs') != []
                    or any(not isinstance(ref, str) or ref not in mapping or not isinstance(mapping[ref], str)
                           or mapping[ref] not in available for ref in refs)
                    or len(set(refs)) != len(refs)):
                unsupported.append({'event_id': event.event_id, 'reason': 'unresolved_action_evidence'})
                continue
            normalized = ' '.join(episode['goal'].split())
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            groups[digest].append({'event_id': event.event_id, 'episode_id': episode['episode_id'],
                'action_event_ids': [mapping[ref] for ref in refs],
                'state_before_sha256': _snapshot_digest(episode.get('state_before')),
                'state_after_sha256': _snapshot_digest(episode.get('state_after')),
                'claimed_outcome': episode.get('claimed_outcome')})
        return groups, unsupported
    left, left_unknown = collect(baseline_events)
    right, right_unknown = collect(candidate_events)
    pairs, unpaired = [], []
    for digest in sorted(set(left) | set(right)):
        a, b = left.get(digest, []), right.get(digest, [])
        if len(a) != 1 or len(b) != 1:
            unpaired.append({'goal_digest': digest,
                'baseline_event_ids': [x['event_id'] for x in a],
                'candidate_event_ids': [x['event_id'] for x in b],
                'reason': 'ambiguous_goal' if len(a) > 1 or len(b) > 1 else 'no_counterpart'})
            continue
        before = [x['state_before_sha256'] for x in (a[0], b[0])]
        comparison = 'unknown' if None in before else 'equal_recorded_fields' if before[0] == before[1] else 'different_recorded_fields'
        pairs.append({'goal_digest': digest, 'baseline': a[0], 'candidate': b[0],
                      'recorded_state_comparison': comparison, 'state_equivalence': 'unknown'})
    return {'schema_version': 'praxile.subtask_alignment.v1', 'strategy': 'unique_exact_goal',
            'pairs': pairs, 'unpaired': unpaired,
            'unsupported': {'baseline': left_unknown, 'candidate': right_unknown},
            'causal_alignment': False, 'semantic_equivalence_verified': False,
            'limitations': ['Exact goal text does not establish semantic or environmental equivalence.',
                           'Snapshots cover recorded fields only; outcomes remain model claims.']}
