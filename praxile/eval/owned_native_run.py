"""One-shot native execution of a public benchmark task in an owned lease.

No private evaluator is invoked here. The returned record is execution evidence,
not a quality score or promotion decision. Existing Trial runners are unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .async_ownership import await_owned
from .contracts import BenchmarkTask
from .harbor_lease import HarborDockerLease
from ..utils import write_json


async def run_owned_native(task: BenchmarkTask, *, state_root: Path, image: str,
                           wheel: Path, wheel_sha256: str, model: str, base_url: str,
                           input_source_root: Path | None = None,
                           credential_file: Path | None = None,
                           max_steps: int = 50, timeout_seconds: int = 900,
                           run_id: str | None = None, resume: bool = False,
                           persist_workspace: bool = False,
                           dependency_wheels: list[dict] | None = None,
                           required_capabilities: list[str] | None = None,
                           shared_chat_budget=None, task_local_memory_enabled: bool = False,
                           development_candidate_file: Path | None = None,
                           development_candidate_sha256: str | None = None,
                           development_only: bool = False, repository: str | None = None):
    if type(task_local_memory_enabled) is not bool:
        raise ValueError("task_local_memory_enabled must be boolean")
    from .credential_broker import SharedChatBudget
    if shared_chat_budget is not None:
        if not isinstance(shared_chat_budget, SharedChatBudget) or credential_file is None:
            raise ValueError("shared chat budget requires a SharedChatBudget and credential file")
        if resume:
            raise ValueError("shared-budget executions cannot be resumed")
    shared_start = shared_chat_budget.status() if shared_chat_budget is not None else None
    from .worker_requirements import validate_requirements, identity
    dependencies, capabilities = validate_requirements(dependency_wheels, required_capabilities,
                                                       worker_filename=wheel.name)
    requirements = identity(dependencies, capabilities)
    if type(persist_workspace) is not bool:
        raise ValueError("persist_workspace must be boolean")
    if not isinstance(task, BenchmarkTask) or task.environment.kind != "harbor":
        raise ValueError("owned native execution requires a Harbor BenchmarkTask")
    from .native_context import owned_candidate_identity
    candidate_identity = owned_candidate_identity(task, development_candidate_file,
        development_candidate_sha256, repository, development_only=development_only)
    if task.environment.revision != image:
        raise ValueError("image must match the frozen task environment revision")
    for output in task.outputs:
        if output.path == "inputs" or output.path.startswith("inputs/"):
            raise ValueError("output contract overlaps mounted public inputs")
    if resume and run_id is None:
        raise ValueError("resume requires an explicit run ID")
    claim = None
    if run_id is not None:
        from .native_run_claim import NativeRunClaim
        plan = {
            "task_fingerprint": task.fingerprint, "image": image, "wheel_sha256": wheel_sha256,
            "model": model, "endpoint_sha256": hashlib.sha256(base_url.encode()).hexdigest(),
            "max_steps": max_steps, "timeout_seconds": timeout_seconds,
            "credential_broker": credential_file is not None}
        plan["task_local_memory_enabled"] = task_local_memory_enabled
        if candidate_identity is not None:
            plan["development_context_identity"] = candidate_identity
        if persist_workspace:
            plan["persist_workspace"] = True
        if dependencies or capabilities:
            plan["worker_requirements"] = requirements
        if shared_start is not None:
            plan["shared_chat_budget_start"] = shared_start
        claim = NativeRunClaim(state_root, run_id, plan)
        if resume:
            return claim.resume()
        claim.acquire()
    from harbor.models.agent.context import AgentContext
    from ..adapters.harbor_native import PraxileAgent

    lease = HarborDockerLease(state_root, image=image, inputs=task.inputs,
                              input_source_root=input_source_root, persist_workspace=persist_workspace)
    logs = lease.root / "agent"
    logs.mkdir()
    report_path = lease.root / "execution.json"
    if claim is not None:
        claim.bind_report(report_path)
    report = {"schema_version": "praxile.owned_native_execution.v1",
        "task_id": task.task_id, "task_fingerprint": task.fingerprint,
        "image": image, "wheel_sha256": wheel_sha256, "model": model,
        "max_steps": max_steps, "timeout_seconds": timeout_seconds,
        "persist_workspace": persist_workspace,
        "public_inputs": lease.public_inputs, "status": "preparing",
        "outputs": [], "cleanup_verified": False,
        "official_evaluation": "not_run", "promotion_eligible": False}
    report["task_local_memory_enabled"] = task_local_memory_enabled
    if candidate_identity is not None:
        report["development_context_identity"] = candidate_identity
    report["worker_requirements"] = requirements
    if shared_start is not None:
        report["shared_chat_budget_start"] = shared_start
    write_json(report_path, report)
    # Build an allowlisted execution view; never serialize evaluator/source refs.
    instruction = task.instruction + "\n\nPublic input paths and SHA-256:\n" + json.dumps(lease.public_inputs)
    instruction += "\nOutput requirements:\n" + json.dumps([
        {"path": item.path, "media_type": item.media_type, "required": item.required}
        for item in task.outputs])
    try:
        from .worker_preflight import check_worker_wheel
        check_worker_wheel(wheel, wheel_sha256)
        agent = PraxileAgent(logs_dir=logs, wheel_path=str(wheel), wheel_sha256=wheel_sha256,
            workspace="/workspace", base_url=base_url, model_name=model,
            max_steps=max_steps, timeout_seconds=timeout_seconds,
            credential_file=str(credential_file) if credential_file else None,
            dependency_wheels=dependencies, required_capabilities=capabilities,
            shared_chat_budget=shared_chat_budget, task_local_memory_enabled=task_local_memory_enabled,
            development_candidate_file=str(development_candidate_file) if development_candidate_file is not None else None,
            development_candidate_sha256=development_candidate_sha256,
            development_only=development_only, repository=repository)
        report["worker_environment_sha256"] = agent.worker_environment_sha256
        await lease.prepare()
        if lease.workspace_path is not None:
            report["workspace"] = lease.workspace_path.relative_to(lease.root).as_posix()
        report["status"] = "running"
        write_json(report_path, report)
        context = AgentContext()
        await lease.run_adapter(agent, instruction, context)
        result = logs / "praxile-result.json"
        if not result.is_file():
            raise ValueError("native adapter returned without trajectory evidence")
        report["source_result_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
        if shared_chat_budget is not None:
            usage = logs / "praxile-broker-usage.json"
            if not usage.is_file() or usage.is_symlink():
                raise ValueError("shared budget execution requires a broker usage receipt")
            report["broker_usage_sha256"] = hashlib.sha256(usage.read_bytes()).hexdigest()
        report["context"] = context.model_dump(mode="json")
        report["status"] = "executor_returned"
        artifacts = lease.root / "outputs"
        artifacts.mkdir()
        for output in task.outputs:
            record = {"path": output.path, "required": output.required, "status": "unavailable"}
            report["outputs"].append(record)
            try:
                content = await lease.collect(output.path)
            except ValueError:
                # Missing/unsafe/oversized artifacts are not silently treated as delivered.
                continue
            target = artifacts / output.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            record.update(status="collected", sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        report["required_outputs_collected"] = all(
            item["status"] == "collected" for item in report["outputs"] if item["required"])
    except BaseException as error:
        report["status"] = "interrupted" if not isinstance(error, Exception) else "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        async def release():
            try:
                await lease.cleanup()
                report["cleanup_verified"] = True
            except BaseException as error:
                report["cleanup_error_type"] = type(error).__name__
                raise
            finally:
                if shared_chat_budget is not None:
                    report["shared_chat_budget_end"] = shared_chat_budget.status()
                write_json(report_path, report)
        await await_owned(release())
    if claim is not None and report.get("required_outputs_collected") is True:
        prefix = lease.root.relative_to(claim.root).as_posix() + "/"
        files = {prefix + "execution.json": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                 prefix + "agent/praxile-result.json": report["source_result_sha256"]}
        if shared_chat_budget is not None:
            files[prefix + "agent/praxile-broker-usage.json"] = report["broker_usage_sha256"]
        files.update({prefix + "inputs/" + item.path: item.content_digest.removeprefix("sha256:") for item in task.inputs})
        files.update({prefix + "outputs/" + item["path"]: item["sha256"] for item in report["outputs"] if item["status"] == "collected"})
        for name in ("praxile-worker-environment.json", "praxile-capabilities.json"):
            evidence = logs / name
            if capabilities and not evidence.is_file():
                raise ValueError("required worker capability evidence missing")
            if evidence.is_file():
                files[prefix + "agent/" + name] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        claim.complete(files)
    return report_path
