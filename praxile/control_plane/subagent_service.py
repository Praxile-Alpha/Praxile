from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..adapters import AdapterPolicy, AdapterRunner, AdapterTask, AgentAdapterV2, validate_adapter_v2
from ..adapters.runner import AdapterRunResult
from ..trace import AgentEvent, EventStore, RunHandle
from ..utils import new_id
from .common import ControlPlaneSchemaError
from .subagent import DelegationContract, MergeDecision, SubagentPolicy, validate_delegation_trace


@dataclass(frozen=True)
class DelegationResult:
    contract: DelegationContract
    parent_start: AgentEvent
    child: AdapterRunResult
    parent_end: AgentEvent


class SubagentControlService:
    """Govern delegation while leaving child execution inside an external adapter."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store
        self.runner = AdapterRunner(event_store)

    def delegate(
        self,
        *,
        adapter: AgentAdapterV2,
        parent: RunHandle,
        project_root: Path,
        contract: DelegationContract,
        control_policy: SubagentPolicy,
        policy: AdapterPolicy,
        evaluation: bool = False,
    ) -> DelegationResult:
        control_policy.authorize(contract, evaluation=evaluation)
        capabilities = validate_adapter_v2(adapter)
        if contract.backend != adapter.name:
            raise ControlPlaneSchemaError(
                f"delegation backend {contract.backend!r} does not match adapter {adapter.name!r}"
            )
        if not capabilities.subagent_visibility:
            raise ControlPlaneSchemaError(f"adapter {adapter.name!r} does not expose subagent execution")
        if policy.context and not capabilities.context_injection:
            raise ControlPlaneSchemaError(f"adapter {adapter.name!r} cannot receive delegated context")
        child_run_id = new_id("subrun")
        start = AgentEvent.create(
            trace_id=parent.trace_id,
            run_id=parent.run_id,
            task_id=parent.task_id,
            type="SUBAGENT_START",
            actor="praxile-subagent-control",
            payload={
                "contract_id": contract.contract_id,
                "child_run_id": child_run_id,
                "context_mode": contract.context_mode,
                "backend": contract.backend,
                "model": contract.model,
                "subagent_policy_id": control_policy.policy_id,
                "subagent_policy_version": control_policy.version,
                "budgets": {
                    "tokens": contract.token_budget,
                    "time_seconds": contract.time_budget_seconds,
                    "cost": contract.cost_budget,
                },
            },
        )
        self.event_store.append(start)
        task = AdapterTask(
            task_id=parent.task_id,
            instruction=contract.objective,
            project_root=str(project_root.resolve()),
            metadata={
                "trace_id": parent.trace_id,
                "run_id": child_run_id,
                "parent_run_id": parent.run_id,
                "delegation_contract": contract.to_dict(),
            },
        )
        settings = {
            **dict(policy.settings),
            "model": contract.model,
            "allowed_tools": list(contract.allowed_tools),
            "subagent_policy": control_policy.to_dict(),
            "delegation_contract": contract.to_dict(),
            "context_mode": contract.context_mode,
        }
        budgets = {
            **dict(policy.budgets),
            "token_budget": contract.token_budget,
            "wall_timeout_seconds": contract.time_budget_seconds,
            "max_cost": contract.cost_budget,
        }
        try:
            child = self.runner.execute(
                adapter,
                task,
                AdapterPolicy(policy.policy_id, policy.version, policy.context, budgets, settings),
            )
        except Exception as exc:
            self.event_store.append(
                AgentEvent.create(
                    trace_id=parent.trace_id,
                    run_id=parent.run_id,
                    task_id=parent.task_id,
                    type="SUBAGENT_END",
                    actor="praxile-subagent-control",
                    payload={
                        "contract_id": contract.contract_id,
                        "child_run_id": child_run_id,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    },
                )
            )
            raise
        result_event = next((event for event in reversed(child.events) if event.type in {"FINAL_RESULT", "RUN_END"}), child.events[-1])
        end = AgentEvent.create(
            trace_id=parent.trace_id,
            run_id=parent.run_id,
            task_id=parent.task_id,
            type="SUBAGENT_END",
            actor="praxile-subagent-control",
            payload={"contract_id": contract.contract_id, "child_run_id": child_run_id, "status": result_event.payload.get("status", "unknown")},
            evidence_refs=(result_event.event_id,),
        )
        self.event_store.append(end)
        validate_delegation_trace((*self.event_store.list_events(trace_id=parent.trace_id),), contract, parent_run_id=parent.run_id, child_run_id=child_run_id)
        return DelegationResult(contract, start, child, end)

    def record_merge_decision(self, decision: MergeDecision, *, task_id: str, trace_id: str) -> AgentEvent:
        events = self.event_store.list_events(trace_id=trace_id)
        event_ids = {event.event_id for event in events}
        if any(item.type == "event" and item.ref_id not in event_ids for item in decision.evidence):
            raise ControlPlaneSchemaError("merge decision references an event outside the trace")
        referenced_ids = {item.ref_id for item in decision.evidence if item.type == "event"}
        verifier_evidence = [
            event
            for event in events
            if event.run_id == decision.verifier_run_id
            and event.type == "VERIFICATION"
            and event.event_id in referenced_ids
        ]
        if not verifier_evidence:
            raise ControlPlaneSchemaError("merge decision must cite verifier-run VERIFICATION evidence")
        event = AgentEvent.create(
            trace_id=trace_id,
            run_id=decision.parent_run_id,
            task_id=task_id,
            type="CHECKPOINT",
            actor="praxile-subagent-control",
            payload={"checkpoint_type": "merge_gate", **decision.to_dict()},
            evidence_refs=tuple(item.ref_id for item in decision.evidence),
        )
        self.event_store.append(event)
        return event
