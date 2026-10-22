"""Manage an online stream; model execution and candidate approval are explicit."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import sqlite3

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
    create.add_argument("--judge-config", type=Path, help="Freeze independent content judge before any execution")
    create.add_argument("--chat-budget", type=Path, help="Freeze shared executor/reflection/judge admission budget")
    for name in ("status", "next", "accept", "design", "review", "audit-review", "summary", "probe", "probe-feedback", "compose", "audit-composition", "review-composition"):
        command = commands.add_parser(name)
        command.add_argument("--stream", type=Path, required=True)
        if name in ("probe-feedback", "compose", "audit-composition", "review-composition"):
            command.add_argument("--probe-directory", type=Path, required=True)
            command.add_argument("--manifest-sha256", required=True)
            command.add_argument("--report-sha256", required=True)
            if name in ("audit-composition", "review-composition"):
                command.add_argument("--composition-directory", type=Path, required=True)
            if name == "review-composition":
                command.add_argument("--task-index", type=int, required=True)
                command.add_argument("--judge-config", type=Path, required=True)
                command.add_argument("--output", type=Path, required=True)
                command.add_argument("--execute", action="store_true")
            if name == "compose":
                command.add_argument("--project", type=Path, required=True)
                command.add_argument("--composition-id", required=True)
                command.add_argument("--scope", type=Path, required=True)
                command.add_argument("--output", type=Path, required=True)
                command.add_argument("--execute", action="store_true")
        elif name == "probe":
            command.add_argument("--task-index", type=int, action="append", required=True)
            command.add_argument("--candidate", type=Path, action="append", required=True)
            command.add_argument("--candidate-sha256", action="append", required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--execute", action="store_true")
        elif name == "summary":
            command.add_argument("--reviews", type=Path, required=True, help="JSON object containing pinned reviews")
        elif name == "next":
            command.add_argument("--execute", action="store_true", help="Start one isolated task and potentially billable model calls")
        elif name == "accept":
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--candidate-sha256", required=True)
            command.add_argument("--approve", action="store_true", help="Explicitly approve the frozen candidate for later tasks")
            command.add_argument("--approved-by", default="")
        elif name == "design":
            command.add_argument("--project", type=Path, required=True, help="Project with proposal_composer role configuration")
            command.add_argument("--design-id", required=True)
            command.add_argument("--scope", type=Path, required=True)
            command.add_argument("--generate", action="store_true", help="Send projected public evidence to the configured model")
            command.add_argument("--resume", action="store_true")
            command.add_argument("--max-output-tokens", type=int, default=4000)
            command.add_argument("--request-timeout", type=int, default=60)
        elif name in ("review", "audit-review"):
            command.add_argument("--task-index", type=int, required=True, help="Zero-based completed task index")
            command.add_argument("--judge-config", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            if name == "review":
                command.add_argument("--execute", action="store_true", help="Send declared artifacts/requirements to the independent judge")
            else:
                command.add_argument("--receipt-sha256", required=True)
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
                judge=_load(args.judge_config) if args.judge_config else None,
                chat_budget=_load(args.chat_budget) if args.chat_budget else None,
                input_source_roots={key: Path(value).absolute() for key, value in roots.items()})
            result = {**stream.status(), "action": "created_not_executed"}
        else:
            stream = OnlineStream(args.stream)
            if args.command == "status":
                result = stream.status()
            elif args.command == "review-composition":
                from .online_composition_review import prepare_composition_reviews, run_composition_reviews
                options = {"probe_directory": args.probe_directory, "composition_directory": args.composition_directory,
                    "manifest_sha256": args.manifest_sha256, "report_sha256": args.report_sha256,
                    "judge": _load(args.judge_config), "directory": args.output}
                if args.execute:
                    path = run_composition_reviews(args.stream, args.task_index, **options)
                    report = _load(path)
                    result = {"status": report["status"], "decision": "KEEP_BASE", "promotion_eligible": False,
                              "reviews_returned": len(report["reviews"])}
                else:
                    prepared = prepare_composition_reviews(args.stream, args.task_index, **options)
                    result = {"action": "composition_review_preview_only", "arms": [item["receipt"]["arm"] for item in prepared],
                              "promotion_eligible": False}
            elif args.command == "audit-composition":
                from .online_composition_audit import audit_online_composition
                result = audit_online_composition(args.stream, probe_directory=args.probe_directory,
                    directory=args.composition_directory, manifest_sha256=args.manifest_sha256,
                    report_sha256=args.report_sha256)
            elif args.command == "compose":
                from .native_run_claim import validate_run_id
                validate_run_id(args.composition_id)
                if args.execute:
                    from ..config import Config
                    from .online_composition import run_online_composition
                    path = asyncio.run(run_online_composition(Config.load(args.project), args.stream,
                        probe_directory=args.probe_directory, manifest_sha256=args.manifest_sha256,
                        report_sha256=args.report_sha256, directory=args.output,
                        composition_id=args.composition_id, applies_to=_load(args.scope)))
                    report = _load(path)
                    result = {"status": report["status"], "decision": report["decision"], "promotion_eligible": False}
                else:
                    from .online_probe_feedback import audit_online_probe
                    feedback = audit_online_probe(args.stream, directory=args.probe_directory,
                        manifest_sha256=args.manifest_sha256, report_sha256=args.report_sha256)
                    result = {"action": "composition_feedback_preview_only", "promotion_eligible": False,
                              "source_ledger_sha256": feedback["source_ledger_sha256"]}
            elif args.command == "probe-feedback":
                from .online_probe_feedback import audit_online_probe
                result = audit_online_probe(args.stream, directory=args.probe_directory,
                    manifest_sha256=args.manifest_sha256, report_sha256=args.report_sha256)
            elif args.command == "probe":
                from .online_probe import prepare_online_probe, run_online_probe
                if len(args.candidate) != 2 or len(args.candidate_sha256) != 2:
                    raise ValueError("probe requires two candidate files and their digests")
                parents = [load_candidate(path, digest) for path, digest in zip(args.candidate, args.candidate_sha256)]
                if args.execute:
                    path = asyncio.run(run_online_probe(args.stream, args.task_index, parents, directory=args.output))
                    report = _load(path)
                    result = {"track": report["track"], "status": report["status"],
                              "attempt_count": len(report["attempts"]), "promotion_eligible": False}
                else:
                    plan = prepare_online_probe(args.stream, args.task_index, parents, directory=args.output)
                    result = {"track": plan["track"], "action": "probe_preview_only",
                              "source_ledger_sha256": plan["source_ledger_sha256"],
                              "planned_executions": len(plan["tasks"]) * 3, "promotion_eligible": False}
            elif args.command == "summary":
                from .online_summary import summarize_online_stream
                manifest = _load(args.reviews)
                if set(manifest) != {"reviews"}:
                    raise ValueError("summary manifest requires only reviews")
                result = summarize_online_stream(args.stream, manifest["reviews"])
            elif args.command == "next":
                result = ({**asyncio.run(stream.run_next()), "action": "execution_completed_not_scored"}
                          if args.execute else {**stream.status(), "action": "preview_only"})
            elif args.command == "design":
                from .online_design import collect_online_evidence, run_online_design
                from .native_run_claim import validate_run_id
                validate_run_id(args.design_id)
                scope = _load(args.scope)
                if args.generate:
                    from ..config import Config
                    result = run_online_design(Config.load(args.project), args.stream, design_id=args.design_id,
                        applies_to=scope, resume=args.resume, max_output_tokens=args.max_output_tokens,
                        request_timeout_seconds=args.request_timeout)
                else:
                    bundle = collect_online_evidence(args.stream)
                    result = {"track": "online_stream", "action": "design_preview_only",
                        "source_ledger_sha256": bundle["ledger_sha256"], "evidence_count": len(bundle["evidence"]),
                        "completed_tasks": len(bundle["completed_task_ids"]), "promotion_eligible": False}
            elif args.command == "audit-review":
                from .online_review import audit_online_review
                result = audit_online_review(args.stream, args.task_index, judge=_load(args.judge_config),
                    directory=args.output, receipt_sha256=args.receipt_sha256)
            elif args.command == "review":
                from .online_review import prepare_online_review, run_online_review
                judge = _load(args.judge_config)
                if args.execute:
                    path = run_online_review(args.stream, args.task_index, judge=judge, directory=args.output)
                    receipt = _load(path)
                    result = {"track": "online_stream", "status": receipt["status"],
                        "action": "independent_review_returned", "official_evaluation": False,
                        "promotion_eligible": False, "learning_feedback_allowed": False,
                        "metric": receipt["metric"], "verdict_counts": receipt["verdict_counts"]}
                else:
                    prepared = prepare_online_review(args.stream, args.task_index, judge=judge, directory=args.output)
                    result = {"track": "online_stream", "action": "review_preview_only", "task_index": args.task_index,
                        "completion_event_sha256": prepared["receipt"]["completion_event_sha256"],
                        "official_evaluation": False, "promotion_eligible": False}
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
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError, sqlite3.Error) as error:
        print("Online stream rejected or failed: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
