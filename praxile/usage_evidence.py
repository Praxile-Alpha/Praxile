"""Provider-reported token coverage, distinct from monetary cost."""
from collections.abc import Mapping


def token_value(usage, primary, alias):
    if not isinstance(usage, Mapping):
        return None
    values = [usage[key] for key in (primary, alias) if key in usage]
    if not values or any(type(value) is not int or not 0 <= value <= 10**12 for value in values):
        return None
    return values[0] if all(value == values[0] for value in values) else None


def summarize_usage(calls, expected_calls):
    def group(records, count):
        known, coverage = {}, {}
        for field, alias in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            values = [token_value(record.get('usage'), field, alias) for record in records]
            known[field] = sum(value for value in values if value is not None)
            coverage[field] = sum(value is not None for value in values)
        return {'calls': count, 'recorded_calls': len(records), 'known_tokens': known,
                'reported_calls': coverage,
                'complete_tokens': {field: known[field] if coverage[field] == count else None for field in known}}
    records = [call for call in calls if isinstance(call, Mapping)]
    count = max(expected_calls, len(calls))
    result = group(records, count)
    purposes = {}
    for record in records:
        purpose = record.get('purpose')
        purpose = purpose if isinstance(purpose, str) and purpose else 'unknown'
        purposes.setdefault(purpose, []).append(record)
    result.update(schema_version='praxile.trajectory_usage.v1',
                  by_purpose={name: group(items, len(items)) for name, items in purposes.items()},
                  unattributed_calls=count - len(records), cost_usd=None,
                  scope='calls_recorded_by_trajectory_logger')
    return result
