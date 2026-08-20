"""Service-layer entry points for Praxile application workflows.

Imports are resolved lazily so runtime-owned services can import sibling modules
without making package import order part of the public contract.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "AssetService",
    "AuditService",
    "ContextJuiceService",
    "GovernanceLoopService",
    "GraphService",
    "ModelService",
    "PolicyService",
    "ProposalService",
    "ReflectService",
    "RepositoryMemoryTreeService",
    "RepositoryContextService",
    "RunService",
    "ServiceError",
    "SpecService",
    "WorkflowService",
    "format_context_status",
]

_EXPORTS = {
    "AssetService": ("asset_service", "AssetService"),
    "AuditService": ("audit_service", "AuditService"),
    "ContextJuiceService": ("context_juice_service", "ContextJuiceService"),
    "GovernanceLoopService": ("governance_loop_service", "GovernanceLoopService"),
    "GraphService": ("graph_service", "GraphService"),
    "ModelService": ("model_service", "ModelService"),
    "PolicyService": ("policy_service", "PolicyService"),
    "ProposalService": ("proposal_service", "ProposalService"),
    "ReflectService": ("reflect_service", "ReflectService"),
    "RepositoryMemoryTreeService": ("memory_tree_service", "RepositoryMemoryTreeService"),
    "RepositoryContextService": ("context_service", "RepositoryContextService"),
    "RunService": ("run_service", "RunService"),
    "ServiceError": ("errors", "ServiceError"),
    "SpecService": ("spec_service", "SpecService"),
    "WorkflowService": ("workflow_service", "WorkflowService"),
    "format_context_status": ("context_service", "format_context_status"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
