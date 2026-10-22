"""Manage an online stream; model execution and candidate approval are explicit."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from .native_context import load_candidate
from .online_stream import OnlineStream
from .owned_content_review import _json, _reader
from .owned_native_cli import load_task
from .worker_preflight import check_worker_wheel
from .worker_requirements import validate_requirements


def _load(path):
    return _json(_reader(path.parent)(path.name))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Freeze task order/configuration; never execute")
    create.add_argument("--stream", type=Path, required=True)
    create.add_argument("--task", type=Path, action="append", required=True, help="Repeat in execution order")
    create.add_argument("--execution-config", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--input-roots", type=Path)
    for name in ("status", "next", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--stream", type=Path, required=True)
        if name == "next":
            command.add_argument("--execute", action="store_true", help="Start one isolated task and potentially billable model calls")
        elif name == "accept":
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--candidate-sha256", required=True)
            command.add_argument("--approve", action="store_true", help="Explicitly approve the frozen candidate for later tasks")
            command.add_argument("--approved-by", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            tasks = [load_task(path) for path in args.task]
            options = _load(args.execution_config)
            if not isinstance(options, dict):
                raise ValueError("execution configuration must be an object")
            wheel = Path(options["wheel"])
            check_worker_wheel(wheel, options["wheel_sha256"])
            dependencies, capabilities = validate_requirements(options.get("dependency_wheels"),
                options.get("required_capabilities"), worker_filename=wheel.name)
            options.update(dependency_wheels=dependencies, required_capabilities=capabilities)
            roots = _load(args.input_roots) if args.input_roots else {}
            if not isinstance(roots, dict) or any(not isinstance(value, str) or not value.strip() for value in roots.values()):
                raise ValueError("input roots must be a task-to-path mapping")
            stream = OnlineStream.create(args.stream, tasks, execution=options, repository=args.repository,
                input_source_roots={key: Path(value).absolute() for key, value in roots.items()})
            result = {**stream.status(), "action": "created_not_executed"}
        else:
            stream = OnlineStream(args.stream)
            if args.command == "status":
                result = stream.status()
            elif args.command == "next":
                result = ({**asyncio.run(stream.run_next()), "action": "execution_completed_not_scored"}
                          if args.execute else {**stream.status(), "action": "preview_only"})
            else:
                value = load_candidate(args.candidate, args.candidate_sha256)
                result = stream.preview_candidate(value)
                if args.approve:
                    result = {**stream.accept_candidate(value, approved=True, approved_by=args.approved_by),
                              "action": "candidate_approved_for_future_tasks"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Inspect stream status; uncertain tasks are never automatically replayed.", file=sys.stderr)
        return 130
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError) as error:
        print("Online stream rejected or failed: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
