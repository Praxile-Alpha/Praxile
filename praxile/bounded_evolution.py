from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import Config
from .harness_components import HarnessComponentRegistry
from .store import ExperienceStore
from .utils import new_id, read_json, slugify, stable_hash, utc_now, write_json


class FailurePathologyMiner:
    """Mine repeated failure modes into component-scoped evolution candidates."""

    def __init__(self, config: Config):
        self.config = config

    def mine(self) -> list[dict[str, Any]]:
        episode_root = self.config.paths.state / "experience" / "episodes"
        episodes = [read_json(path, {}) for path in sorted(episode_root.glob("*.json"))] if episode_root.exists() else []
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for episode in episodes:
            signature = str(episode.get("failure_signature") or "").strip().lower()
            if not signature or signature == "n/a":
                continue
            component = _affected_component(episode)
            groups[(_normalize_signature(signature), component)].append(episode)
        minimum = int(self.config.get("harness_evolution", "minimum_pathology_episodes", default=2))
        previous = read_json(self._archive_path(), {})
        previous_entries = {item.get("pathology_id"): item for item in previous.get("entries") or []}
        pathologies = [self._pathology(signature, component, rows) for (signature, component), rows in groups.items() if len(rows) >= minimum]
        for item in pathologies:
            prior = previous_entries.get(item["pathology_id"]) or {}
            item["alternatives"] = list(prior.get("alternatives") or [])
        pathologies.sort(key=lambda item: (-item["episode_count"], item["pathology_id"]))
        write_json(self._archive_path(), {"schema_version": 1, "updated_at": utc_now(), "entries": pathologies})
        return pathologies

    def propose(self, pathology_id: str) -> dict[str, Any]:
        entries = (read_json(self._archive_path(), {}) or {}).get("entries") or self.mine()
        pathology = next((item for item in entries if item.get("pathology_id") == pathology_id), None)
        if not pathology:
            raise ValueError(f"Failure pathology not found: {pathology_id}")
        component = str(pathology["component_id"])
        proposal_type, target = _proposal_target(component, pathology)
        claim = f"Prevent repeated `{pathology['signature']}` failures in component `{component}`."
        content = (
            f"# Harness Alternative: {pathology['title']}\n\n"
            f"## Claim\n{claim}\n\n"
            "## Applies When\n" + "\n".join(f"- {item}" for item in pathology["applies_when"]) + "\n\n"
            "## Does Not Apply When\n" + "\n".join(f"- {item}" for item in pathology["does_not_apply_when"]) + "\n\n"
            "## Evidence\n" + "\n".join(f"- Episode `{item}`" for item in pathology["source_episodes"]) + "\n\n"
            "## Minimal Alternative\n"
            f"- Change only component `{component}`.\n- Add a precondition or route guard for `{pathology['signature']}`.\n"
            "- Preserve all outer-anchor safety and approval behavior.\n\n"
            "## Validation\nRun source, regression, and sealed cases in isolated baseline/candidate workspaces.\n"
        )
        proposal = {
            "proposal_id": new_id("proposal"),
            "source_task_id": pathology["source_task_ids"][-1] if pathology["source_task_ids"] else None,
            "type": proposal_type,
            "title": pathology["title"],
            "status": "proposed",
            "risk_level": "medium",
            "confidence": pathology["confidence"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "target_files": [target],
            "changes": [{"path": target, "operation": "write", "content": content}],
            "applicability_scope": pathology["applies_when"],
            "anti_scope": pathology["does_not_apply_when"],
            "source": {"pathology_id": pathology_id, "episode_ids": pathology["source_episodes"]},
            "requires_human_approval": True,
        }
        proposal["component_change"] = HarnessComponentRegistry(self.config).component_change_for(proposal_type, proposal["changes"])
        self._record_alternative(pathology_id, proposal)
        return proposal

    def _pathology(self, signature: str, component: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        episode_ids = [str(item.get("episode_id")) for item in rows if item.get("episode_id")]
        task_ids = list(dict.fromkeys(str(item.get("task_id")) for item in rows if item.get("task_id")))
        applies = _unique([value for item in rows for value in _as_list(item.get("applies_when"))])
        anti = _unique([value for item in rows for value in _as_list(item.get("does_not_apply_when"))])
        confidence = round(min(0.95, 0.55 + 0.08 * len(set(task_ids)) + 0.03 * len(rows)), 3)
        return {
            "pathology_id": "pathology_" + stable_hash(f"{signature}|{component}", length=16),
            "title": f"Bound repeated {signature}",
            "signature": signature,
            "component_id": component,
            "episode_count": len(rows),
            "distinct_task_count": len(set(task_ids)),
            "source_episodes": episode_ids,
            "source_task_ids": task_ids,
            "applies_when": applies or [f"The same `{signature}` failure recurs."],
            "does_not_apply_when": anti or ["The failure signature or owning component differs."],
            "confidence": confidence,
            "quality_dimensions": {"evidence_density": len(rows), "diversity": len(set(task_ids)), "scope": component},
            "alternatives": [],
            "updated_at": utc_now(),
        }

    def _record_alternative(self, pathology_id: str, proposal: dict[str, Any]) -> None:
        archive = read_json(self._archive_path(), {})
        for item in archive.get("entries") or []:
            if item.get("pathology_id") == pathology_id:
                item.setdefault("alternatives", []).append(
                    {
                        "proposal_id": proposal["proposal_id"],
                        "component_id": proposal["component_change"]["component_id"],
                        "candidate_version": proposal["component_change"]["candidate_version"],
                        "status": proposal["status"],
                    }
                )
        archive["updated_at"] = utc_now()
        write_json(self._archive_path(), archive)

    def _archive_path(self) -> Path:
        return self.config.paths.state / "experience" / "harness" / "quality-diversity-archive.json"


class BoundedHarnessEvolution:
    OUTER_ANCHOR = {
        "constitution.md",
        "rules/safety-policy.json",
        "evals/sealed",
        "evals/scorers",
    }

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.registry = HarnessComponentRegistry(config)

    def promotion_manifest(self, proposal: dict[str, Any]) -> dict[str, Any]:
        change = proposal.get("component_change") or {}
        validation = proposal.get("validation") or {}
        if proposal.get("status") != "accepted" or validation.get("status") != "validated":
            raise PermissionError("Only human-accepted, validated harness candidates can be promoted")
        component_id = str(change.get("component_id") or "")
        active = self.registry.describe(component_id)
        manifest_path = self._manifest_path()
        manifest = read_json(manifest_path, {"schema_version": 1, "components": {}, "promotion_history": []})
        previous = (manifest.get("components") or {}).get(component_id)
        entry = {
            "component_id": component_id,
            "active_version": active["version"],
            "candidate_version": change.get("candidate_version"),
            "base_version": change.get("base_version"),
            "proposal_id": proposal.get("proposal_id"),
            "validation": validation,
            "approved_by": "human",
            "approval_event": "explicit_accept",
            "activated_at": utc_now(),
            "rollback_target": {
                "proposal_id": proposal.get("proposal_id"),
                "snapshot_id": proposal.get("pre_apply_snapshot_id"),
                "previous_active_version": previous.get("active_version") if isinstance(previous, dict) else change.get("base_version"),
            },
            "activated_assets": [str(item.get("path")) for item in proposal.get("changes") or [] if isinstance(item, dict)],
            "monitoring": {"status": "active", "observed_runs": 0},
        }
        manifest.setdefault("components", {})[component_id] = entry
        manifest.setdefault("promotion_history", []).append(entry)
        manifest["updated_at"] = utc_now()
        write_json(manifest_path, manifest)
        return entry

    def record_experiment(self, proposal: dict[str, Any], validation_report: dict[str, Any]) -> dict[str, Any]:
        change = proposal.get("component_change") or {}
        pathology_id = str((proposal.get("source") or {}).get("pathology_id") or "unclassified")
        experiment = {
            "experiment_id": new_id("experiment"),
            "pathology_id": pathology_id,
            "proposal_id": proposal.get("proposal_id"),
            "component_id": change.get("component_id"),
            "base_version": change.get("base_version"),
            "candidate_version": change.get("candidate_version"),
            "status": validation_report.get("status"),
            "comparison": validation_report.get("comparison") or {},
            "created_at": utc_now(),
        }
        path = self.config.paths.state / "experience" / "harness" / "experiments" / f"{experiment['experiment_id']}.json"
        write_json(path, experiment)
        experiment["path"] = str(path.relative_to(self.config.paths.root))
        archive_path = self.config.paths.state / "experience" / "harness" / "quality-diversity-archive.json"
        archive = read_json(archive_path, {})
        for item in archive.get("entries") or []:
            if item.get("pathology_id") != pathology_id:
                continue
            for alternative in item.get("alternatives") or []:
                if alternative.get("proposal_id") == proposal.get("proposal_id"):
                    alternative["status"] = validation_report.get("status")
                    alternative["experiment_id"] = experiment["experiment_id"]
                    alternative["score_delta"] = (validation_report.get("comparison") or {}).get("score_delta")
        if archive:
            archive["updated_at"] = utc_now()
            write_json(archive_path, archive)
        return experiment

    def monitor(self, trajectory: dict[str, Any], *, apply_rollback: bool | None = None) -> list[dict[str, Any]]:
        manifest = read_json(self._manifest_path(), {})
        enabled = bool(self.config.get("harness_evolution", "automatic_rollback", "enabled", default=True))
        apply = enabled if apply_rollback is None else bool(apply_rollback)
        events: list[dict[str, Any]] = []
        report = trajectory.get("reward_report") or {}
        score = float(report.get("overall", 0.0) or 0.0)
        safety = float(report.get("process_safety", 1.0) or 0.0)
        regression_failed = report.get("regression_passed") is False
        for component_id, entry in (manifest.get("components") or {}).items():
            monitoring = entry.setdefault("monitoring", {"status": "active", "observed_runs": 0})
            if monitoring.get("status") != "active":
                continue
            if not _component_participated(component_id, entry, trajectory):
                monitoring["unattributed_runs"] = int(monitoring.get("unattributed_runs", 0)) + 1
                continue
            monitoring["observed_runs"] = int(monitoring.get("observed_runs", 0)) + 1
            scores = list(monitoring.get("reward_scores") or [])[-9:] + [score]
            monitoring["reward_scores"] = scores
            baseline = float(((entry.get("validation") or {}).get("comparison") or {}).get("candidate_score", score) or score)
            minimum_runs = int(self.config.get("harness_evolution", "automatic_rollback", "minimum_runs", default=3))
            tolerance = float(self.config.get("harness_evolution", "automatic_rollback", "reward_drop_tolerance", default=0.2))
            operational = regression_failed or safety < float(self.config.get("harness_evolution", "automatic_rollback", "minimum_safety", default=0.5))
            statistical = len(scores) >= minimum_runs and sum(scores) / len(scores) < baseline - tolerance
            if not (operational or statistical):
                continue
            event = {
                "component_id": component_id,
                "proposal_id": entry.get("proposal_id"),
                "trigger": "operational_regression" if operational else "reward_regression",
                "task_id": trajectory.get("task_id"),
                "observed_average": round(sum(scores) / len(scores), 4),
                "baseline": baseline,
                "applied": False,
                "created_at": utc_now(),
            }
            if apply:
                self.store.rollback_proposal(str(entry.get("proposal_id")))
                event["applied"] = True
                monitoring["status"] = "rolled_back"
                monitoring["rolled_back_at"] = utc_now()
                monitoring["rollback_trigger"] = event["trigger"]
                entry["rolled_back_from_version"] = entry.get("active_version")
                entry["active_version"] = self.registry.describe(component_id)["version"]
            events.append(event)
        if manifest:
            manifest["updated_at"] = utc_now()
            write_json(self._manifest_path(), manifest)
        return events

    def routing_proposal(self) -> dict[str, Any] | None:
        trajectories = [read_json(path, {}) for path in sorted(self.config.paths.trajectories.glob("*.json"))]
        signals: list[dict[str, Any]] = []
        for trajectory in trajectories:
            analysis = trajectory.get("task_analysis") or {}
            reward = trajectory.get("reward_report") or {}
            for event in (trajectory.get("model_routing") or {}).get("performance") or []:
                signals.append(
                    {
                        "task_id": trajectory.get("task_id"),
                        "task_class": analysis.get("task_type") or "general",
                        "private": bool(analysis.get("privacy_sensitive")),
                        "high_risk": bool(analysis.get("high_risk")),
                        "status": event.get("status"),
                        "provider": event.get("provider"),
                        "model": event.get("model"),
                        "cost": (trajectory.get("cost") or {}).get("estimated_usd", 0.0),
                        "reward": reward.get("overall"),
                        "judge_reliable": not bool((reward.get("escalation") or {}).get("reasons") and "judge_disagreement" in (reward.get("escalation") or {}).get("reasons", [])),
                    }
                )
        minimum = int(self.config.get("harness_evolution", "routing", "minimum_signals", default=3))
        if len(signals) < minimum:
            return None
        failures = [item for item in signals if item["status"] in {"unavailable", "invalid_action", "failure"}]
        if not failures:
            return None
        proposal_id = new_id("proposal")
        target = f"rules/harness-rules/model-routing/routing-evidence-{slugify(str(failures[0]['task_class']))}.md"
        content = (
            "# Evidence-based Routing Alternative\n\n"
            "## Applies When\n"
            f"- Task class: `{failures[0]['task_class']}`\n- Privacy/high-risk context matches recorded evidence.\n\n"
            "## Does Not Apply When\n- Judge reliability is unknown or evidence comes from unrelated task classes.\n\n"
            "## Evidence\n" + "\n".join(
                f"- `{item['task_id']}` status={item['status']} provider={item['provider']} model={item['model']} reward={item['reward']} cost={item['cost']} judge_reliable={item['judge_reliable']}"
                for item in signals[-12:]
            ) + "\n\n## Proposal\nReview a fallback route for this task class; preserve local routing for private work and require strong-model approval for high-risk work.\n"
        )
        proposal = {
            "proposal_id": proposal_id, "source_task_id": failures[-1]["task_id"], "type": "routing",
            "title": f"Tune routing for {failures[0]['task_class']} from measured outcomes", "status": "proposed",
            "risk_level": "high", "confidence": round(min(0.9, 0.55 + 0.05 * len(failures)), 3),
            "created_at": utc_now(), "updated_at": utc_now(), "target_files": [target],
            "changes": [{"path": target, "operation": "write", "content": content}],
            "applicability_scope": [failures[0]["task_class"]],
            "anti_scope": ["unrelated task classes", "private tasks without an approved local route"],
            "source": {"signal_count": len(signals), "failure_count": len(failures)},
            "requires_human_approval": True,
        }
        proposal["component_change"] = self.registry.component_change_for("routing", proposal["changes"])
        return proposal

    def export_bundle(self, proposal_id: str, output: Path, *, include_private: bool = False) -> Path:
        if include_private and not self.config.get("harness_evolution", "export_private_repository_content", default=False):
            raise PermissionError("Private repository experiment export is disabled by project policy")
        proposal = self.store.find_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal not found: {proposal_id}")
        validation = proposal.get("validation") or {}
        validation_path = self.config.paths.root / str(validation.get("report_path") or "")
        validation_report = read_json(validation_path, {}) if validation_path.is_file() else {}
        bundle_proposal = proposal if include_private else _redacted_proposal(proposal)
        manifest = {
            "schema_version": 1,
            "bundle_id": new_id("experiment"),
            "created_at": utc_now(),
            "private_repository_content_included": bool(include_private),
            "proposal_id": proposal_id,
            "component_change": proposal.get("component_change"),
            "validation_summary": {
                "status": validation_report.get("status"),
                "comparison": validation_report.get("comparison"),
                "isolation": validation_report.get("isolation"),
            },
            "files": ["manifest.json", "proposal.json", "validation-summary.json"],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            archive.writestr("proposal.json", json.dumps(bundle_proposal, indent=2, ensure_ascii=False))
            archive.writestr("validation-summary.json", json.dumps(manifest["validation_summary"], indent=2, ensure_ascii=False))
        return output

    def _manifest_path(self) -> Path:
        return self.config.paths.state / "experience" / "harness" / "active-manifest.json"


def _affected_component(episode: dict[str, Any]) -> str:
    category = str(episode.get("category") or "").lower()
    signature = str(episode.get("failure_signature") or "").lower()
    if "model" in category or "model" in signature or "route" in signature:
        return "model_routing"
    if "shell" in category or "blocked" in signature or "command" in signature:
        return "tool_policy"
    if "context" in category or "token" in signature:
        return "compression_profile"
    if "retriev" in category or "memory" in signature:
        return "retrieval_policy"
    return "rules"


def _proposal_target(component: str, pathology: dict[str, Any]) -> tuple[str, str]:
    slug = slugify(f"pathology-{pathology['signature']}", max_length=54)
    specialized = {
        "model_routing": ("routing", "model-routing"),
        "tool_policy": ("tool_policy_update", "tool-policy"),
        "compression_profile": ("compression_profile_update", "compression-profile"),
        "retrieval_policy": ("retrieval_policy_update", "retrieval-policy"),
        "stopping_policy": ("stopping_policy_update", "stopping-policy"),
    }
    if component in specialized:
        proposal_type, directory = specialized[component]
        return proposal_type, f"rules/harness-rules/{directory}/{slug}.md"
    return "harness_rule", f"rules/harness-rules/{slug}.md"


def _normalize_signature(value: str) -> str:
    return " ".join(value.lower().split())[:160]


def _as_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else ([str(value)] if value else [])


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _redacted_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    result = {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "status": proposal.get("status"),
        "risk_level": proposal.get("risk_level"),
        "confidence": proposal.get("confidence"),
        "component_change": proposal.get("component_change"),
        "validation": {
            "status": (proposal.get("validation") or {}).get("status"),
            "comparison": (proposal.get("validation") or {}).get("comparison"),
        },
        "requires_human_approval": True,
    }
    result["changes"] = [
        {
            "path": Path(str(item.get("path") or "asset")).name,
            "operation": item.get("operation"),
            "content_hash": stable_hash(str(item.get("content") or item.get("metadata") or ""), length=20),
        }
        for item in proposal.get("changes") or [] if isinstance(item, dict)
    ]
    result["source_task_id"] = stable_hash(str(proposal.get("source_task_id") or "none"), length=16)
    result["source"] = {"redacted": True}
    return result


def _component_participated(component_id: str, entry: dict[str, Any], trajectory: dict[str, Any]) -> bool:
    explicit = trajectory.get("harness_component_attribution") or []
    if component_id in explicit:
        return True
    if component_id == "model_routing" and (trajectory.get("model_routing") or {}).get("performance"):
        return True
    proposal_targets: set[str] = set()
    for item in trajectory.get("loaded_assets") or []:
        if isinstance(item, dict) and item.get("path"):
            proposal_targets.add(str(item["path"]).removeprefix(".praxile/"))
    source_paths = entry.get("activated_assets") or []
    return bool(proposal_targets & {str(item).removeprefix(".praxile/") for item in source_paths})
