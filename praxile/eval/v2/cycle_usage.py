"""Explicit scope accounting for development execution and composition."""
import math

from ...usage_evidence import summarize_usage


def cycle_usage(reports, *, composer_requested, composer_trajectory=None):
    fields = ("input", "output", "cache")
    components = []
    for report in reports:
        for arm in ("baseline", "candidate"):
            summary = report.get(arm) or {}
            metrics = summary.get("metrics") or {}
            values = metrics.get("tokens") or {}
            tokens = {key: value if type(value := values.get(key)) is int and value >= 0 else None
                      for key in fields}
            complete = metrics.get("tokens_complete") is True and all(value is not None for value in tokens.values())
            cost = metrics.get("cost_total")
            cost_valid = (metrics.get("cost_complete") is True and type(cost) in (int, float)
                          and math.isfinite(cost) and cost >= 0)
            subtotal = metrics.get("reported_cost_subtotal", cost)
            subtotal_valid = type(subtotal) in (int, float) and math.isfinite(subtotal) and subtotal >= 0
            components.append({"role": "execution", "arm": arm, "run_id": summary.get("eval_run_id"),
                               "known_tokens": tokens, "tokens_complete": complete,
                               "known_cost_subtotal": subtotal if subtotal_valid else None,
                               "cost_total": cost if cost_valid else None})
    if composer_requested:
        evidence = None
        if isinstance(composer_trajectory, dict):
            calls = composer_trajectory.get("model_routing", {}).get("calls")
            count = composer_trajectory.get("cost", {}).get("model_calls")
            if isinstance(calls, list) and type(count) is int and count >= len(calls):
                evidence = summarize_usage(calls, count)
        components.append({"role": "proposal_composer", "known_tokens": {
            "input": evidence["known_tokens"]["prompt_tokens"] if evidence else None,
            "output": evidence["known_tokens"]["completion_tokens"] if evidence else None,
            "cache": None}, "tokens_complete": False, "cost_total": None, "known_cost_subtotal": None,
            "usage_evidence": evidence})
    totals = {}
    for field in fields:
        values = [item["known_tokens"][field] for item in components]
        totals[field] = {"known_subtotal": sum(value for value in values if value is not None),
                         "reported_components": sum(value is not None for value in values),
                         "complete_total": sum(values) if components and all(
                             item["tokens_complete"] for item in components) else None}
    costs = [item["cost_total"] for item in components]
    return {"schema_version": "praxile.development_cycle_usage.v1", "components": components,
            "tokens": totals, "component_count": len(components),
            "known_cost_subtotal": sum(item["known_cost_subtotal"] for item in components
                                       if item["known_cost_subtotal"] is not None),
            "recorded_scope_cost_total": sum(costs) if costs and all(value is not None for value in costs) else None,
            "whole_experiment_cost_total": None, "whole_experiment_complete": False,
            "unaccounted_scope": ["upstream candidate generation", "independent judge calls", "unrecorded requests"],
            "scope": "reported execution arms and optional composer; not a full experiment ledger"}
