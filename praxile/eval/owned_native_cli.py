"""Validate a native task plan; execute only with explicit --execute."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from .contracts import BenchmarkTask
from .worker_preflight import check_worker_wheel


def load_task(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
        raise ValueError("task must be a regular JSON file of at most 1 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate task JSON key")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("nonfinite task JSON value")
    return BenchmarkTask.from_dict(json.loads(path.read_text(encoding="utf-8"),
        object_pairs_hook=unique, parse_constant=nonfinite))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--input-source-root", type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--dependency-wheel", nargs=2, action="append", metavar=("PATH", "SHA256"),
                        help="Repeat for each fixed offline dependency wheel")
    parser.add_argument("--require-capability", action="append", default=[],
                        help="Versioned worker capability checked before model execution")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--execute", action="store_true", help="Start Docker and potentially billable model calls")
    parser.add_argument("--run-id", help="Exclusive durable identity; reuse requires --resume")
    parser.add_argument("--resume", action="store_true", help="Reuse verified completion only; never replay interrupted execution")
    parser.add_argument("--persist-workspace", action="store_true", help="Retain per-generation workspace on host; does not enable action resume")
    args = parser.parse_args(argv)
    try:
        from .native_run_claim import validate_run_id
        if args.run_id is not None:
            validate_run_id(args.run_id)
        if args.resume and not args.run_id:
            raise ValueError("resume requires run ID")
        task = load_task(args.task)
        if task.environment.kind != "harbor" or not re.fullmatch(
                r"[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}", task.environment.revision):
            raise ValueError("task requires digest-pinned Harbor image")
        if not args.model.strip() or not 1 <= args.max_steps <= 1000 or not 1 <= args.timeout <= 86400:
            raise ValueError("invalid model or execution budget")
        endpoint = urlsplit(args.base_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError("invalid model endpoint")
        if args.credential_file is not None and args.timeout > 3540:
            raise ValueError("broker execution timeout must not exceed 3540 seconds")
        if bool(task.inputs) != (args.input_source_root is not None):
            raise ValueError("public inputs require an explicit source root")
        check_worker_wheel(args.wheel, args.wheel_sha256)
        from .worker_requirements import validate_requirements, identity
        dependencies, capabilities = validate_requirements(
            [{"path": path, "sha256": digest} for path, digest in args.dependency_wheel or []],
            args.require_capability, worker_filename=args.wheel.name)
        plan = {"task_id": task.task_id, "task_fingerprint": task.fingerprint,
                "image": task.environment.revision, "model": args.model,
                "wheel_sha256": args.wheel_sha256, "input_count": len(task.inputs),
                "output_count": len(task.outputs), "max_steps": args.max_steps,
                "timeout_seconds": args.timeout, "execute": args.execute,
                "run_id": args.run_id, "resume": args.resume,
                "persist_workspace": args.persist_workspace,
                "official_evaluation": "not_run"}
        plan["worker_requirements"] = identity(dependencies, capabilities)
        print(json.dumps(plan, ensure_ascii=False))
        if not args.execute:
            print("Plan only: no Docker/model calls; source inputs and infrastructure are not yet verified.")
            return 0
        from .owned_native_run import run_owned_native
        report_path = asyncio.run(run_owned_native(task, state_root=args.state_root,
            image=task.environment.revision, wheel=args.wheel, wheel_sha256=args.wheel_sha256,
            model=args.model, base_url=args.base_url, input_source_root=args.input_source_root,
            credential_file=args.credential_file, max_steps=args.max_steps, timeout_seconds=args.timeout,
            run_id=args.run_id, resume=args.resume, persist_workspace=args.persist_workspace,
            dependency_wheels=dependencies, required_capabilities=capabilities))
        report = json.loads(report_path.read_text())
        print("Execution report: " + str(report_path))
        print("Official evaluation not run; inspect trajectory and outputs before assessing task success.")
        return 0 if report.get("cleanup_verified") and report.get("required_outputs_collected") else 1
    except KeyboardInterrupt:
        print("Interrupted; inspect the execution report before starting another run.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        # Error details may contain private paths, task material or endpoint secrets.
        print("Native execution rejected or failed: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
