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
                           max_steps: int = 50, timeout_seconds: int = 900):
    from harbor.models.agent.context import AgentContext
    from ..adapters.harbor_native import PraxileAgent

    if not isinstance(task, BenchmarkTask) or task.environment.kind != "harbor":
        raise ValueError("owned native execution requires a Harbor BenchmarkTask")
    if task.environment.revision != image:
        raise ValueError("image must match the frozen task environment revision")
    for output in task.outputs:
        if output.path == "inputs" or output.path.startswith("inputs/"):
            raise ValueError("output contract overlaps mounted public inputs")
    lease = HarborDockerLease(state_root, image=image, inputs=task.inputs,
                              input_source_root=input_source_root)
    logs = lease.root / "agent"
    logs.mkdir()
    report_path = lease.root / "execution.json"
    report = {"schema_version": "praxile.owned_native_execution.v1",
        "task_id": task.task_id, "task_fingerprint": task.fingerprint,
        "image": image, "wheel_sha256": wheel_sha256, "model": model,
        "max_steps": max_steps, "timeout_seconds": timeout_seconds,
        "public_inputs": lease.public_inputs, "status": "preparing",
        "outputs": [], "cleanup_verified": False,
        "official_evaluation": "not_run", "promotion_eligible": False}
    write_json(report_path, report)
    # Build an allowlisted execution view; never serialize evaluator/source refs.
    instruction = task.instruction + "\n\nPublic input paths and SHA-256:\n" + json.dumps(lease.public_inputs)
    instruction += "\nOutput requirements:\n" + json.dumps([
        {"path": item.path, "media_type": item.media_type, "required": item.required}
        for item in task.outputs])
    try:
        agent = PraxileAgent(logs_dir=logs, wheel_path=str(wheel), wheel_sha256=wheel_sha256,
            workspace="/workspace", base_url=base_url, model_name=model,
            max_steps=max_steps, timeout_seconds=timeout_seconds,
            credential_file=str(credential_file) if credential_file else None)
        await lease.prepare()
        report["status"] = "running"
        write_json(report_path, report)
        context = AgentContext()
        await lease.run_adapter(agent, instruction, context)
        result = logs / "praxile-result.json"
        if not result.is_file():
            raise ValueError("native adapter returned without trajectory evidence")
        report["source_result_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
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
                write_json(report_path, report)
        await await_owned(release())
    return report_path
