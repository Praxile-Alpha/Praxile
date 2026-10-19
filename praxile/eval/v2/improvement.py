"""Descriptive paired evidence, explicitly not a causal promotion decision."""
from collections.abc import Mapping
from copy import deepcopy
import math


def improvement_episodes(experiment_id, candidate, baseline, treatment, comparison, invariant_check):
    left = {row["task_id"]: row for row in baseline.get("tasks", [])}
    right = {row["task_id"]: row for row in treatment.get("tasks", [])}
    episodes = []
    for row in comparison.get("task_results", []):
        task_id = row["task_id"]
        a, b = left.get(task_id, {}), right.get(task_id, {})
        confounders = []
        if invariant_check.get("valid") is not True:
            confounders.append("execution invariants not verified")
        if not a.get("trace_id") or not b.get("trace_id"):
            confounders.append("paired trace references missing")
        resolved = [arm.get("evaluator", {}).get("resolved") for arm in (a, b)]
        if any(type(value) is not bool for value in resolved):
            confounders.append("official outcome missing or invalid")
        activation = b.get("context_activation")
        if not isinstance(activation, Mapping) or not activation.get("injected_context_items"):
            confounders.append("candidate exposure not established")
        deltas = {}
        for name in ("tool_calls", "latency_ms", "cost"):
            if name == "cost" and not comparison.get("cost_comparable"):
                continue
            values = [arm.get("metrics", {}).get(name) for arm in (a, b)]
            if all(type(value) in (int, float) and math.isfinite(value) for value in values):
                deltas[name] = values[1] - values[0]
        transition = ("unknown" if any(type(value) is not bool for value in resolved) else
                      "unchanged" if resolved[0] == resolved[1] else "improved" if resolved[1] else "regressed")
        verdict = "confounded" if confounders else {
            "improved": "helped", "regressed": "hurt", "unchanged": "no_observed_effect"
        }.get(transition, "confounded")
        episodes.append({
            "schema_version": "praxile.improvement_episode.v1",
            "episode_id": f"{experiment_id}:{task_id}", "experiment_id": experiment_id, "task_id": task_id,
            "baseline": {"run_id": baseline.get("eval_run_id"), "manifest_digest": baseline.get("manifest_digest"),
                         "trace_id": a.get("trace_id")},
            "candidate": {"run_id": treatment.get("eval_run_id"), "manifest_digest": treatment.get("manifest_digest"),
                          "trace_id": b.get("trace_id"), "candidate_id": candidate.candidate_id, "digest": candidate.digest},
            "component_diff": {"type": candidate.candidate_type, "candidate_digest": candidate.digest,
                               "source": "frozen experiment candidate", "applied": bool(isinstance(activation, Mapping)
                                                                                and activation.get("injected_context_items"))},
            "behavior_prediction": deepcopy(dict(candidate.expected_effect)),
            "probe": {"type": "paired_task_execution", "trace_ids": [a.get("trace_id"), b.get("trace_id")]},
            "observed_difference": {"outcome_transition": transition, "baseline_resolved": resolved[0],
                                    "candidate_resolved": resolved[1], "measured_deltas": deltas,
                                    "progress": deepcopy(row.get("progress", {}))},
            "confounders": confounders, "verdict": verdict,
            "verdict_scope": "observed task outcome in one pair, not mechanism causality",
            "uncertainty": ["Single pair does not establish statistical significance or transferable causality.",
                            "Metric deltas do not prove the predicted behavior occurred."],
            "prediction_verified": False, "promotion_eligible": False,
            "next_action": "collect missing evidence" if confounders else "repeat and perform component ablation",
            "experience_entry": "NO_ENTRY",
        })
    return episodes
