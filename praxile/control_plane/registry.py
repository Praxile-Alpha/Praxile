from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from ..utils import file_lock, utc_now
from .common import ControlPlaneSchemaError, canonical_json, non_empty
from .evolution import CandidateEvaluation, HarnessCandidate, promotion_key


REGISTRY_SCHEMA_VERSION = "praxile.harness_registry.v2"
LEGACY_REGISTRY_SCHEMA_VERSION = "praxile.harness_registry.v1"


class HarnessEvolutionRegistry:
    """Atomic local registry for candidates, promotion decisions, and rollback pointers."""

    def __init__(self, project_root: Path):
        self.path = project_root.resolve() / ".praxile" / "control-plane" / "registry.json"

    def register(self, candidate: HarnessCandidate) -> None:
        def mutate(state: dict[str, Any]) -> None:
            current = state["candidates"].get(candidate.candidate_id)
            record = {"candidate": candidate.to_dict(), "status": "proposed", "registered_at": utc_now()}
            if current and canonical_json(current["candidate"]) != canonical_json(record["candidate"]):
                raise ControlPlaneSchemaError(f"candidate_id collision: {candidate.candidate_id}")
            if not current:
                state["candidates"][candidate.candidate_id] = record

        self._mutate(mutate)

    def record_evaluation(self, evaluation: CandidateEvaluation) -> None:
        def mutate(state: dict[str, Any]) -> None:
            record = state["candidates"].get(evaluation.candidate_id)
            if not record:
                raise ControlPlaneSchemaError(f"unknown candidate: {evaluation.candidate_id}")
            candidate = HarnessCandidate.from_dict(record["candidate"])
            if evaluation.decision == "promote" and (
                evaluation.rollback_target.get("component_key") != candidate.component_key
                or evaluation.rollback_target.get("executor_profile", "default") != candidate.executor_profile
                or evaluation.rollback_target.get("task_family", "default") != candidate.task_family
                or str(evaluation.rollback_target.get("version") or "") != candidate.base_version
            ):
                raise ControlPlaneSchemaError("rollback_target must identify the candidate component and base version")
            state["evaluations"][evaluation.candidate_id] = evaluation.to_dict()
            record["status"] = "approved" if evaluation.decision == "promote" else evaluation.decision

        self._mutate(mutate)

    def promote(self, candidate_id: str, *, approved_by: str) -> None:
        non_empty(approved_by, "approved_by")

        def mutate(state: dict[str, Any]) -> None:
            record = state["candidates"].get(candidate_id)
            raw_evaluation = state["evaluations"].get(candidate_id)
            if not record or not raw_evaluation:
                raise ControlPlaneSchemaError("candidate must be registered and evaluated before promotion")
            evaluation = CandidateEvaluation.from_dict(raw_evaluation)
            if evaluation.decision != "promote":
                raise ControlPlaneSchemaError("candidate evaluation does not permit promotion")
            candidate = HarnessCandidate.from_dict(record["candidate"])
            key = candidate.promotion_key
            active = state["active"].get(key)
            if active and str(active.get("version") or "") != candidate.base_version:
                raise ControlPlaneSchemaError(
                    f"stale candidate base_version {candidate.base_version!r}; "
                    f"active version is {active.get('version')!r}"
                )
            previous = active or dict(evaluation.rollback_target)
            current = {"candidate_id": candidate_id, "version": candidate.candidate_version}
            state["active"][key] = current
            record["status"] = "active"
            state["history"].append(
                {
                    "action": "promote",
                    "component_key": candidate.component_key,
                    "executor_profile": candidate.executor_profile,
                    "task_family": candidate.task_family,
                    "promotion_key": key,
                    "previous": previous,
                    "current": current,
                    "approved_by": approved_by,
                    "at": utc_now(),
                }
            )

        self._mutate(mutate)

    def rollback(
        self,
        component_key: str,
        *,
        approved_by: str,
        executor_profile: str = "default",
        task_family: str = "default",
    ) -> None:
        non_empty(approved_by, "approved_by")
        key = promotion_key(component_key, executor_profile, task_family)

        def mutate(state: dict[str, Any]) -> None:
            current = state["active"].get(key)
            promotions = [item for item in state["history"] if item["action"] == "promote" and item.get("promotion_key", promotion_key(item["component_key"], item.get("executor_profile", "default"), item.get("task_family", "default"))) == key and item["current"] == current]
            if not current or not promotions:
                raise ControlPlaneSchemaError(f"no active promotion can be rolled back for {key!r}")
            target = promotions[-1]["previous"]
            if target is None:
                state["active"].pop(key, None)
            else:
                state["active"][key] = target
            state["candidates"][current["candidate_id"]]["status"] = "rolled_back"
            state["history"].append(
                {
                    "action": "rollback",
                    "component_key": component_key,
                    "executor_profile": executor_profile,
                    "task_family": task_family,
                    "promotion_key": key,
                    "previous": current,
                    "current": target,
                    "approved_by": approved_by,
                    "at": utc_now(),
                }
            )

        self._mutate(mutate)

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") not in {REGISTRY_SCHEMA_VERSION, LEGACY_REGISTRY_SCHEMA_VERSION}:
            raise ControlPlaneSchemaError("unsupported or corrupt harness registry")
        return self._migrate(value)

    def _mutate(self, operation: Callable[[dict[str, Any]], None]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path):
            state = self.snapshot()
            operation(state)
            payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temp.write_text(payload, encoding="utf-8")
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "candidates": {}, "evaluations": {}, "active": {}, "history": []}

    @staticmethod
    def _migrate(value: dict[str, Any]) -> dict[str, Any]:
        if value.get("schema_version") == REGISTRY_SCHEMA_VERSION:
            return value
        migrated = json.loads(json.dumps(value))
        migrated["schema_version"] = REGISTRY_SCHEMA_VERSION
        for record in migrated.get("candidates", {}).values():
            candidate = HarnessCandidate.from_dict(record["candidate"])
            record["candidate"] = candidate.to_dict()
        active = migrated.get("active", {})
        migrated["active"] = {
            (key if "::" in key else promotion_key(key, "default", "default")): pointer
            for key, pointer in active.items()
        }
        for item in migrated.get("history", []):
            item.setdefault("executor_profile", "default")
            item.setdefault("task_family", "default")
            item.setdefault(
                "promotion_key",
                promotion_key(item["component_key"], item["executor_profile"], item["task_family"]),
            )
        return migrated
