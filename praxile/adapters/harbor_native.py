"""Harbor plugin: praxile.adapters.harbor_native:PraxileAgent.

Install Harbor in a separate benchmark environment. Harbor, not this plugin,
owns container creation, termination, networking, and official verification.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import re
import shlex
import tempfile
import time
from pathlib import Path, PurePosixPath

from harbor.agents.base import BaseAgent
from ..utils import new_id, write_json
from ..usage_evidence import summarize_usage


class PraxileAgent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, *args, wheel_path: str, wheel_sha256: str,
                 workspace: str, base_url: str, max_steps: int = 50,
                 timeout_seconds: int = 900, credential_file: str | None = None,
                 task_body_file: str | None = None, task_body_sha256: str | None = None,
                 inherit_task_packages: bool = False, model_timeout_seconds: int = 30,
                 output_directory: str | None = None, renderer_executable: str = "",
                 deliverable_contract: dict | None = None,
                 dependency_wheels: list[dict] | None = None,
                 model_request_options: dict | None = None,
                 development_candidate_file: str | None = None,
                 development_candidate_sha256: str | None = None,
                 development_only: bool = False, repository: str | None = None, **kwargs):
        from ..model_options import validate_request_options
        self.model_request_options = validate_request_options({} if model_request_options is None else model_request_options)
        from ..services.run_service import normalize_deliverable_contract
        public_contract = normalize_deliverable_contract(deliverable_contract)
        self._public_contract_json = (json.dumps(public_contract, sort_keys=True, separators=(",", ":"))
                                      if public_contract is not None else None)
        self.public_contract_sha256 = (hashlib.sha256(self._public_contract_json.encode()).hexdigest()
                                       if self._public_contract_json is not None else None)
        self._development_candidate_json = None
        self.development_context_identity = None
        if development_candidate_file is not None or development_candidate_sha256 is not None:
            from ..eval.native_context import load_candidate, plan_candidate
            if (not isinstance(development_candidate_file, str)
                    or not isinstance(development_candidate_sha256, str)):
                raise ValueError("development candidate file and digest must be configured together")
            value = load_candidate(Path(development_candidate_file), development_candidate_sha256)
            # Validate structure/scope before setup; actual activation uses the task body in the worker.
            validation = plan_candidate(value, "Preflight only", repository, development_only=development_only)
            self._development_candidate_json = json.dumps(value, sort_keys=True, separators=(",", ":"))
            self.development_context_identity = {
                "track": "development", "repository": repository,
                "source_file_sha256": development_candidate_sha256,
                "transferred_file_sha256": hashlib.sha256(self._development_candidate_json.encode()).hexdigest(),
                "candidate_digest": validation["candidate_digest"], "promotion_eligible": False}
        elif development_only is not False or repository is not None:
            raise ValueError("development flags require a candidate file and digest")
        if not isinstance(renderer_executable, str) or (renderer_executable and
                (not PurePosixPath(renderer_executable).is_absolute() or ".." in PurePosixPath(renderer_executable).parts)):
            raise ValueError("renderer_executable must be empty or a trusted absolute container path")
        self.renderer_executable = renderer_executable
        if output_directory is not None:
            output_path = PurePosixPath(output_directory)
            if output_path.is_absolute() or ".." in output_path.parts or not output_path.parts:
                raise ValueError("output directory must be workspace-relative")
        self.output_directory = output_directory
        if type(model_timeout_seconds) is not int or not 1 <= model_timeout_seconds <= 110:
            raise ValueError("model_timeout_seconds must be an integer in [1, 110]")
        self.model_timeout_seconds = model_timeout_seconds
        if type(inherit_task_packages) is not bool:
            raise ValueError("inherit_task_packages must be a boolean")
        self.inherit_task_packages = inherit_task_packages
        if bool(task_body_file) != bool(task_body_sha256):
            raise ValueError("task body file and digest must be configured together")
        if task_body_file:
            body_path = PurePosixPath(task_body_file)
            if body_path.is_absolute() or ".." in body_path.parts or not body_path.parts:
                raise ValueError("task body must be workspace-relative")
            if len(task_body_sha256) != 64 or any(c not in "0123456789abcdef" for c in task_body_sha256):
                raise ValueError("task body SHA-256 required")
        self.task_body_file, self.task_body_sha256 = task_body_file, task_body_sha256
        self.wheel = Path(wheel_path).resolve(strict=True)
        if not self.wheel.name.endswith(".whl") or hashlib.sha256(self.wheel.read_bytes()).hexdigest() != wheel_sha256:
            raise ValueError("Praxile wheel digest mismatch")
        if dependency_wheels is not None and not isinstance(dependency_wheels, list):
            raise ValueError("dependency_wheels must be a list of path/sha256 records")
        self._dependency_wheels = []
        names = {self.wheel.name}
        for item in dependency_wheels or []:
            if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                    or not isinstance(item["path"], str) or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                raise ValueError("invalid dependency wheel record")
            dependency = Path(item["path"]).resolve(strict=True)
            if (not dependency.is_file() or not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", dependency.name)
                    or dependency.name in names
                    or hashlib.sha256(dependency.read_bytes()).hexdigest() != item["sha256"]):
                raise ValueError("dependency wheel digest or filename mismatch")
            names.add(dependency.name)
            self._dependency_wheels.append((dependency, item["sha256"]))
        self._dependency_wheels.sort(key=lambda item: item[0].name)
        self.worker_environment = {"schema_version": "praxile.worker_environment.v1",
            "praxile_wheel_sha256": wheel_sha256, "inherit_task_packages": inherit_task_packages,
            "dependencies": [{"filename": path.name, "sha256": digest}
                             for path, digest in self._dependency_wheels],
            "installation": "offline_no_dependency_resolution"}
        self.worker_environment_sha256 = hashlib.sha256(json.dumps(
            self.worker_environment, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path = PurePosixPath(workspace)
        if not path.is_absolute() or ".." in path.parts or str(path) != workspace or workspace == "/":
            raise ValueError("explicit absolute task workspace required")
        if not 1 <= max_steps <= 1000 or not 1 <= timeout_seconds <= 86400:
            raise ValueError("invalid execution budget")
        self.wheel_sha256 = wheel_sha256
        self.workspace, self.base_url = workspace, base_url
        self.max_steps, self.timeout_seconds = max_steps, timeout_seconds
        self.credential_file = Path(credential_file) if credential_file else None
        if self.credential_file and timeout_seconds > 3540:
            raise ValueError("broker-backed execution requires timeout_seconds <= 3540")
        self.remote_root = "/tmp/praxile-native-" + wheel_sha256[:16]
        if self._dependency_wheels:
            self.remote_root += "-" + self.worker_environment_sha256[:16]
        super().__init__(*args, **kwargs)
        if not self.model_name:
            raise ValueError("model_name is required")
        if self.mcp_servers or self.skills_dir:
            raise ValueError("native benchmark plugin does not yet support Harbor MCP or skill injection")

    @staticmethod
    def name() -> str:
        return "praxile-native"

    def version(self) -> str:
        if self._dependency_wheels:
            return "sha256:" + self.wheel_sha256 + "+worker:" + self.worker_environment_sha256
        return "sha256:" + self.wheel_sha256

    async def setup(self, environment) -> None:
        # Recheck sources before transferring anything; constructor checks may be stale.
        for path, digest in [(self.wheel, self.wheel_sha256), *self._dependency_wheels]:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("worker wheel changed before setup")
        remote_wheel = self.remote_root + "/" + self.wheel.name
        await self._exec(environment, shlex.join(["mkdir", "-p", self.remote_root]))
        await environment.upload_file(self.wheel, remote_wheel)
        verify = ("import hashlib,pathlib,sys; "
                  "assert sys.version_info >= (3,11), 'Python >=3.11 required'; "
                  f"assert hashlib.sha256(pathlib.Path({remote_wheel!r}).read_bytes()).hexdigest()=={self.wheel_sha256!r}")
        await self._exec(environment, shlex.join(["python3", "-c", verify]))
        venv = ["python3", "-m", "venv"]
        if self.inherit_task_packages:
            venv.append("--system-site-packages")
        await self._exec(environment, shlex.join([*venv, self.remote_root + "/venv"]))
        dependencies = []
        for path, digest in self._dependency_wheels:
            remote = self.remote_root + "/" + path.name
            await environment.upload_file(path, remote)
            verify_dependency = ("import hashlib,pathlib; "
                f"assert hashlib.sha256(pathlib.Path({remote!r}).read_bytes()).hexdigest()=={digest!r}")
            await self._exec(environment, shlex.join(["python3", "-c", verify_dependency]))
            dependencies.append(remote)
        await self._exec(environment, shlex.join([self.remote_root + "/venv/bin/python", "-m", "pip",
                                                "install", "--no-index", "--no-deps", remote_wheel, *dependencies]))
        if dependencies:
            await self._exec(environment, shlex.join([
                self.remote_root + "/venv/bin/python", "-m", "pip", "check"]))
        write_json(self.logs_dir / "praxile-worker-environment.json", {
            **self.worker_environment, "digest": self.worker_environment_sha256,
            "status": "installed", "task_image_modified": False,
            "extra_dependency_environment": bool(dependencies),
            "strict_environment_equivalence_verified": False})
        if self.renderer_executable:
            # Probe the worker interpreter, not the image's system Python.
            probe = ("import os,sys; from defusedxml.ElementTree import fromstring; "
                     "from pypdf import PdfReader; from docx import Document; "
                     "sys.exit(0 if os.path.isfile(sys.argv[1]) and "
                     "os.access(sys.argv[1], os.X_OK) and os.name == 'posix' else 1)")
            try:
                await self._exec(environment, shlex.join([
                    self.remote_root + "/venv/bin/python", "-I", "-c", probe,
                    self.renderer_executable]), timeout_seconds=15)
            except Exception as error:
                raise RuntimeError(
                    "Document renderer preflight failed: provision an executable renderer "
                    "and praxile[documents] dependencies in the worker environment before "
                    "running this experiment; no model request was started by setup"
                ) from error

    async def _exec(self, environment, command: str, *, timeout_seconds: int | None = None):
        result = await environment.exec(command, timeout_sec=timeout_seconds or self.timeout_seconds)
        if result.return_code != 0:
            # Do not echo process output that may contain task credentials.
            raise RuntimeError(f"native executor command failed: exit={result.return_code}")
        return result

    async def run(self, instruction, environment, context) -> None:
        if self.credential_file is None:
            return await self._run_with_endpoint(instruction, environment, context)
        from ..eval.credential_broker import ChatCredentialBroker, read_private_key
        broker = ChatCredentialBroker(key=read_private_key(self.credential_file),
            endpoint=self.base_url.rstrip("/") + "/chat/completions", model=self.model_name,
            max_calls=min(self.max_steps + 10, 1000), max_output=6000,
            output_budget=min((self.max_steps + 10) * 6000, 10_000_000),
            ttl_seconds=self.timeout_seconds + 60,
            request_timeout=self.model_timeout_seconds + 10, request_options=self.model_request_options)
        remote = self.remote_root + "/" + new_id("capability")
        try:
            _, port = broker.start()
            with tempfile.TemporaryDirectory(prefix="praxile-broker-") as directory:
                capability = Path(directory) / "capability"
                capability.touch(mode=0o600)
                capability.write_text(broker.capability, encoding="utf-8")
                await environment.upload_file(capability, remote)
            if getattr(environment, "default_user", None) == "dev":
                ownership = await environment.exec(shlex.join(["chown", "dev", remote]), user="root", timeout_sec=5)
                if ownership.return_code != 0:
                    raise RuntimeError("could not assign temporary capability to task user")
            await self._exec(environment, shlex.join(["chmod", "600", remote]))
            await self._run_with_endpoint(instruction, environment, context,
                endpoint=f"http://host.docker.internal:{port}/v1", capability_file=remote)
        finally:
            broker.revoke()
            from ..eval.async_ownership import await_owned
            async def release_broker():
                try:
                    await asyncio.to_thread(broker.close)
                finally:
                    # A revoked leftover capability is unusable even if removal fails.
                    try:
                        await asyncio.wait_for(environment.exec(shlex.join(["rm", "-f", remote]), timeout_sec=5), timeout=6)
                    except (Exception, asyncio.CancelledError):
                        pass
                    write_json(self.logs_dir / "praxile-broker-usage.json", broker.status())
            await await_owned(release_broker())

    async def _run_with_endpoint(self, instruction, environment, context, *, endpoint=None, capability_file=None) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="praxile-public-instruction-") as directory:
            public = Path(directory) / "instruction.txt"
            public.write_text(instruction, encoding="utf-8")
            await environment.upload_file(public, self.remote_root + "/instruction.txt")
            if self._public_contract_json is not None:
                contract_file = Path(directory) / "public-deliverables.json"
                contract_file.write_text(self._public_contract_json, encoding="utf-8")
                await environment.upload_file(contract_file, self.remote_root + "/public-deliverables.json")
            if self._development_candidate_json is not None:
                candidate_file = Path(directory) / "development-candidate.json"
                candidate_file.write_text(self._development_candidate_json, encoding="utf-8")
                await environment.upload_file(candidate_file, self.remote_root + "/development-candidate.json")
        cancel_file = self.remote_root + "/" + new_id("cancel")
        argv = [
            self.remote_root + "/venv/bin/python", "-m", "praxile.eval.process_supervisor",
            "--timeout", str(self.timeout_seconds), "--status", self.remote_root + "/supervision.json",
            "--cancel-file", cancel_file, "--",
            self.remote_root + "/venv/bin/python", "-m", "praxile.eval.native_worker",
            "--workspace", self.workspace, "--instruction", self.remote_root + "/instruction.txt",
            "--result", self.remote_root + "/result.json", "--model", self.model_name,
            "--base-url", endpoint or self.base_url, "--max-steps", str(self.max_steps),
            "--model-timeout-seconds", str(self.model_timeout_seconds),
        ]
        if capability_file:
            argv.extend(["--capability-file", capability_file])
        if self.model_request_options:
            argv.extend(["--model-request-options-json", json.dumps(self.model_request_options, sort_keys=True)])
        if self.task_body_file:
            argv.extend(["--task-body-file", self.task_body_file, "--task-body-sha256", self.task_body_sha256])
        if self.output_directory is not None:
            argv.extend(["--output-directory", self.output_directory])
        if self.renderer_executable:
            argv.extend(["--renderer-executable", self.renderer_executable])
        if self._public_contract_json is not None:
            argv.extend(["--deliverable-contract-file", self.remote_root + "/public-deliverables.json",
                         "--deliverable-contract-sha256", self.public_contract_sha256])
        if self.development_context_identity is not None:
            argv.extend(["--development-only", "--repository", self.development_context_identity["repository"],
                "--development-candidate", self.remote_root + "/development-candidate.json",
                "--development-candidate-sha256", self.development_context_identity["transferred_file_sha256"]])
        command = shlex.join(argv)
        started = time.monotonic()
        record = {"schema_version": "praxile.native_execution.v1", "status": "running",
                  "wheel_sha256": self.wheel_sha256, "result_available": False,
                  "inherit_task_packages": self.inherit_task_packages,
                  "worker_environment": self.worker_environment,
                  "worker_environment_sha256": self.worker_environment_sha256,
                  "output_directory": self.output_directory,
                  "renderer_executable": self.renderer_executable,
                  "public_contract_sha256": self.public_contract_sha256,
                  "model_timeout_seconds": self.model_timeout_seconds}
        if self.model_request_options:
            record["model_request_options"] = self.model_request_options
        if self.development_context_identity is not None:
            record["development_context_identity"] = self.development_context_identity
        write_json(self.logs_dir / "praxile-execution.json", record)
        try:
            await self._exec(environment, command, timeout_seconds=self.timeout_seconds + 10)
        except BaseException as error:
            record.update(status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                          error_type=type(error).__name__)
            if isinstance(error, asyncio.CancelledError):
                await self._cancel_worker(environment, cancel_file, record)
            # Failure evidence must not replace the primary execution exception.
            try:
                await asyncio.wait_for(self._collect_result(environment, context), timeout=15)
                record["result_available"] = True
            except (Exception, asyncio.CancelledError) as collection_error:
                record["collection_error_type"] = type(collection_error).__name__
            await self._record_supervision(environment, record)
            record["elapsed_seconds"] = time.monotonic() - started
            try:
                write_json(self.logs_dir / "praxile-execution.json", record)
            except OSError:
                pass
            raise
        try:
            await asyncio.wait_for(self._collect_result(environment, context), timeout=15)
            record.update(status="completed", result_available=True)
        except BaseException as error:
            record.update(status="collection_failed", error_type=type(error).__name__)
            raise
        finally:
            await self._record_supervision(environment, record)
            record["elapsed_seconds"] = time.monotonic() - started
            try:
                write_json(self.logs_dir / "praxile-execution.json", record)
            except OSError:
                if record["status"] == "completed":
                    raise

    async def _cancel_worker(self, environment, cancel_file, record) -> None:
        record["worker_cancel"] = {"requested": False, "termination_confirmed": False}
        try:
            result = await asyncio.wait_for(environment.exec(shlex.join([
                self.remote_root + "/venv/bin/python", "-c",
                f"from pathlib import Path; Path({cancel_file!r}).touch()"]), timeout_sec=5), timeout=6)
            if result.return_code != 0:
                raise RuntimeError("worker cancellation request failed")
            record["worker_cancel"]["requested"] = True
            async def confirm():
                while True:
                    await self._record_supervision(environment, record)
                    if record.get("worker_supervision", {}).get("status") in {
                            "completed", "failed", "cancelled", "timed_out"}:
                        record["worker_cancel"]["termination_confirmed"] = True
                        return
                    await asyncio.sleep(.1)
            await asyncio.wait_for(confirm(), timeout=6)
        except (Exception, asyncio.CancelledError) as error:
            record["worker_cancel"]["error_type"] = type(error).__name__

    async def _record_supervision(self, environment, record) -> None:
        temporary = self.logs_dir / "praxile-supervision.partial.json"
        record.pop("worker_supervision", None)
        try:
            await asyncio.wait_for(environment.download_file(
                self.remote_root + "/supervision.json", temporary), timeout=5)
            supervision = json.loads(temporary.read_text(encoding="utf-8"))
            if (not isinstance(supervision, dict)
                    or supervision.get("schema_version") != "praxile.worker_supervision.v1"
                    or supervision.get("status") not in {
                        "starting", "running", "completed", "failed", "timed_out", "cancelled", "supervisor_error"}):
                raise ValueError("invalid worker supervision report")
            record["worker_supervision"] = {
                "status": supervision["status"],
                "returncode": supervision.get("returncode") if type(supervision.get("returncode")) is int else None,
            }
        except Exception as error:
            record["supervision_collection_error_type"] = type(error).__name__
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    async def _collect_result(self, environment, context) -> None:
        # Never leave a truncated download at the canonical evidence path.
        temporary = self.logs_dir / "praxile-result.partial.json"
        try:
            await environment.download_file(self.remote_root + "/result.json", temporary)
            result = json.loads(temporary.read_text(encoding="utf-8"))
            if (not isinstance(result, dict) or result.get("schema_version") != "praxile.native_benchmark_run.v1"
                    or not isinstance(result.get("trajectory"), dict)
                    or result.get("experience_mode") != ("development_candidate"
                        if self.development_context_identity is not None else "fresh_disabled")):
                raise ValueError("invalid native worker result")
            if result.get("model_request_options", {}) != self.model_request_options:
                raise ValueError("native worker model request options differ from controller")
            if self.development_context_identity is not None:
                development = result.get("development_context")
                activation = development.get("activation") if isinstance(development, dict) else None
                if (not isinstance(development, dict) or not isinstance(activation, dict)
                        or development.get("schema_version") != "praxile.native_development_context.v1"
                        or development.get("track") != "development"
                        or development.get("candidate_digest") != self.development_context_identity["candidate_digest"]
                        or activation.get("repository") != self.development_context_identity["repository"]
                        or type(activation.get("activated")) is not bool
                        or type(development.get("injected")) is not bool
                        or (development["injected"] and not activation["activated"])
                        or development.get("promotion_eligible") is not False
                        or development.get("permission_granted") is not False):
                    raise ValueError("native development result identity or activation mismatch")
            from ..trace import native_run_to_events
            events = native_run_to_events(result)
            write_json(self.logs_dir / "praxile-events.json", {
                "schema_version": "praxile.native_event_projection.v1",
                "source_result_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
                "events": [event.to_dict() for event in events]})
            temporary.replace(self.logs_dir / "praxile-result.json")
            self.populate_context_post_run(context)
        finally:
            temporary.unlink(missing_ok=True)

    def populate_context_post_run(self, context) -> None:
        result_path = self.logs_dir / "praxile-result.json"
        if not result_path.is_file():
            return
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("schema_version") != "praxile.native_benchmark_run.v1":
            raise ValueError("unsupported native worker result")
        trajectory = result["trajectory"]
        routing = trajectory.get("model_routing")
        calls = routing.get("calls") if isinstance(routing, dict) else None
        records_valid = isinstance(calls, list)
        calls = calls if records_valid else []
        cost = trajectory.get("cost")
        count_present = isinstance(cost, dict) and "model_calls" in cost
        declared_count = cost.get("model_calls") if count_present else len(calls)
        count_valid = type(declared_count) is int and declared_count >= len(calls)
        evidence = summarize_usage(calls, declared_count if count_valid else len(calls))
        evidence["call_count_source"] = "model_calls" if count_present else "recorded_calls_only"
        evidence["call_count_valid"] = count_valid
        # Recompute from raw records, never trust a cached usage summary.
        if not records_valid or not count_valid or not calls:
            evidence["complete_tokens"] = {key: None for key in evidence["complete_tokens"]}
        context.n_input_tokens = evidence["complete_tokens"]["prompt_tokens"]
        context.n_output_tokens = evidence["complete_tokens"]["completion_tokens"]
        context.metadata = {**(context.metadata or {}), "praxile": {
            "source_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "wheel_sha256": self.wheel_sha256, "trajectory_task_id": trajectory.get("task_id"),
            "worker_environment": self.worker_environment,
            "worker_environment_sha256": self.worker_environment_sha256,
            "result": trajectory.get("result"), "official_evaluation": "external",
            "experience_mode": result["experience_mode"],
            "usage_evidence": evidence,
        }}
        if self.development_context_identity is not None:
            context.metadata["praxile"]["development_context_identity"] = self.development_context_identity
            context.metadata["praxile"]["development_context"] = result.get("development_context")


class WorkBuddyPraxileAgent(PraxileAgent):
    """Match WorkBuddy's non-root rollout without modifying upstream agents."""

    @staticmethod
    def name() -> str:
        return "praxile-workbuddy"

    async def setup(self, environment) -> None:
        if self.workspace != "/workspace" or getattr(environment, "default_user", None) != "dev":
            raise ValueError("WorkBuddy native executor requires /workspace and agent_user=dev")
        # This fixed setup command is control-plane code, not an agent tool.
        result = await environment.exec(
            "set -eu; if ! id dev >/dev/null 2>&1; then useradd -m -s /bin/bash dev; fi; "
            'test "$(id -u dev)" != 0; mkdir -p /workspace; chown -hR dev /workspace',
            user="root", cwd="/", timeout_sec=self.timeout_seconds,
        )
        if result.return_code != 0:
            raise RuntimeError("could not initialize WorkBuddy non-root execution user")
        await super().setup(environment)
