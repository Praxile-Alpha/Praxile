"""Frozen, opt-in context composition experiments on declared development tasks."""
from copy import deepcopy
import re

from ...utils import file_lock, read_json, write_json
from .candidate import ContextCandidate
from .cycle_usage import CycleUsageCheckpoint
from .executor_profile import common_parent_capability
from .experiment import ControlledABExperiment, _write_once
from .schema import EvalSchemaError


class DevelopmentContextCycle:
    def __init__(self, state_root, event_store):
        self.state_root = state_root.resolve()
        self.experiments = ControlledABExperiment(self.state_root, event_store)

    def run(self, task_set, *, candidates, composed_candidate=None, development_task_ids,
            heldout_task_ids, cycle_id, adapter, evaluator, baseline_policy, model,
            resume=False, source_overrides=None, keep_workspaces=False,
            composer=None, composer_identity=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", cycle_id):
            raise EvalSchemaError("unsafe development cycle ID")
        if len(candidates) != 2:
            raise EvalSchemaError("development cycle requires exactly two candidates")
        parent_capability = common_parent_capability(candidates)
        adaptive = composer is not None
        if adaptive and (composed_candidate is not None or not isinstance(composer_identity, dict) or not composer_identity):
            raise EvalSchemaError("probe-driven composition requires a frozen composer identity and no preset composition")
        if not adaptive and (composed_candidate is None or composer_identity is not None):
            raise EvalSchemaError("provide either a preset composition or a composer")
        versions = [*candidates] if adaptive else [*candidates, composed_candidate]
        if parent_capability is not None:
            common_parent_capability(versions)
        if len({item.candidate_id for item in versions}) != len(versions):
            raise EvalSchemaError("composition must have a distinct candidate ID")
        task_ids = {task.task_id for task in task_set.tasks}
        if (not task_ids or task_ids != set(development_task_ids)
                or task_ids & set(heldout_task_ids)):
            raise EvalSchemaError("cycle tasks must match the explicit development split and exclude held-out")
        if baseline_policy.context:
            raise EvalSchemaError("development baseline must have empty context")
        for candidate in versions:
            hypothesis = candidate.expected_effect.get("hypothesis")
            if not isinstance(hypothesis, str) or not hypothesis.strip():
                raise EvalSchemaError("each candidate, including composition, requires an explicit hypothesis")
            if set(candidate.source_task_ids) & set(heldout_task_ids):
                raise EvalSchemaError("development candidates cannot use declared held-out source tasks")
        root = self.state_root / "eval" / "v2" / "development_cycles" / cycle_id
        plan = {
            "schema_version": "praxile.development_context_cycle.v1", "cycle_id": cycle_id,
            "track": "development", "task_set_digest": task_set.digest,
            "development_task_ids": sorted(task_ids), "heldout_task_ids": sorted(set(heldout_task_ids)),
            "candidate_digests": [candidate.digest for candidate in versions],
            "candidates": [candidate.to_dict() for candidate in versions],
            "composition_parents": [candidate.digest for candidate in candidates],
            "model": deepcopy(dict(model)), "baseline_policy": baseline_policy.to_dict(),
            "adapter": {"name": adapter.name, "protocol_version": adapter.protocol_version},
            "evaluator": dict(evaluator.identity()),
            "experiment_ids": [f"{cycle_id}.{role}" for role in ("first", "second", "composition")],
            "selection_rule": "composition_objective_gain_without_regression_and_verified_exposure",
        }
        if adaptive:
            plan["composition_mode"] = "after_development_probes"
            plan["composer_identity"] = deepcopy(composer_identity)
        with file_lock(root / "cycle"):
            if (root / "manifest.json").exists() and not resume:
                raise EvalSchemaError("development cycle already exists; resume explicitly")
            _write_once(root / "manifest.json", plan)
            if (adaptive and (root / "composition-attempt.json").exists()
                    and not (root / "composition.json").exists()):
                raise EvalSchemaError("composition attempt has no durable result; automatic replay refused")
            reports = []
            usage_checkpoint = CycleUsageCheckpoint(root, reports, composer_requested=adaptive)
            for index, experiment_id in enumerate(plan["experiment_ids"]):
                if adaptive and index == 2:
                    frozen_path = root / "composition.json"
                    frozen = read_json(frozen_path, None)
                    if frozen is None:
                        attempt_path = root / "composition-attempt.json"
                        if attempt_path.exists():
                            raise EvalSchemaError("composition attempt has no durable result; automatic replay refused")
                        _write_once(attempt_path, {"status": "requested", "composer_identity": composer_identity,
                                                  "parents": plan["composition_parents"]})
                        with usage_checkpoint.operation("proposal_composer"):
                            composed_candidate = composer(tuple(candidates), deepcopy(reports))
                            frozen = {"candidate": composed_candidate.to_dict() if composed_candidate is not None else None,
                                      "parents": plan["composition_parents"],
                                      "composer_identity": composer_identity}
                            _write_once(frozen_path, frozen)
                    if (frozen.get("parents") != plan["composition_parents"]
                            or frozen.get("composer_identity") != composer_identity):
                        raise EvalSchemaError("frozen composition lineage mismatch")
                    if frozen["candidate"] is None:
                        break
                    composed_candidate = ContextCandidate.from_dict(frozen["candidate"])
                    if parent_capability is not None:
                        common_parent_capability([*candidates, composed_candidate])
                    if (composed_candidate.candidate_id in {item.candidate_id for item in candidates}
                            or set(composed_candidate.source_task_ids) & set(heldout_task_ids)
                            or not isinstance(composed_candidate.expected_effect.get("hypothesis"), str)
                            or not composed_candidate.expected_effect["hypothesis"].strip()):
                        raise EvalSchemaError("invalid probe-driven composition")
                    versions.append(composed_candidate)
                candidate = versions[index]
                # Completed children are resumed, never replaced or treated as inherited results.
                child = self.state_root / "eval" / "v2" / "experiments" / experiment_id / "manifest.json"
                with usage_checkpoint.operation(experiment_id):
                    reports.append(self.experiments.run(
                        task_set, adapter=adapter, evaluator=evaluator, baseline_policy=baseline_policy,
                        candidate=candidate, model=model, experiment_id=experiment_id,
                        resume=resume and child.exists(), development_only=True,
                        keep_workspaces=keep_workspaces, source_overrides=source_overrides))
                write_json(root / "progress.json", {"completed_experiments": [
                    item["experiment_id"] for item in reports], "promotion_eligible": False})
            composition = reports[-1] if len(reports) == 3 else {}
            episodes = composition.get("improvement_episodes", [])
            verified = (composition.get("invariant_check", {}).get("valid") is True
                        and len(episodes) == len(task_ids)
                        and {episode.get("task_id") for episode in episodes} == task_ids
                        and all(episode.get("verdict") in {"helped", "no_observed_effect"}
                                and not episode.get("confounders") for episode in episodes)
                        and any(episode.get("verdict") == "helped" for episode in episodes))
            report = {
                "schema_version": "praxile.development_context_cycle_report.v1", "cycle_id": cycle_id,
                "track": "development", "experiments": [{
                    "experiment_id": item["experiment_id"],
                    "candidate_digest": item["candidate_digest"],
                    "comparison": item["comparison"],
                } for item in reports],
                "decision": "TASK_LOCAL_TRIAL_RECOMMENDED" if verified else "KEEP_BASE",
                "selected_candidate_digest": composed_candidate.digest if verified else None,
                "composition_status": "executed" if len(reports) == 3 else "abstained",
                "usage": usage_checkpoint.save(status="complete"),
                "adopted": False, "promotion_eligible": False, "experience_entry": "NO_ENTRY",
                "limitations": ["Split membership is explicitly declared, not independently attested.",
                    "Composition uses preset content or development feedback only, never held-out feedback.",
                    "Single development pairs do not establish transfer or statistical significance.",
                    "No production policy or long-term asset is modified."],
            }
            write_json(root / "report.json", report)
            return report
