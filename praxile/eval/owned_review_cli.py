"""Preflight owned text/DOCX/PDF evidence; call the independent judge only explicitly."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from ..config import Config
from .owned_native_cli import load_task
from .owned_content_review import prepare_owned_review
from .content_review_batch import plan_content_review_batch
from .content_review_run import run_content_review


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="Separate judge configuration and result project")
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--execution-dir", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--source-format", choices=["text", "docx_body", "pdf_text"], default="text")
    parser.add_argument("--output-format", choices=["text", "docx_body", "pdf_text"], default="text")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--total-output-tokens", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--timeout-per-call", type=int, default=90)
    parser.add_argument("--execute", action="store_true", help="Permit potentially billable judge requests")
    parser.add_argument("--public", action="store_true", help="Allow sending source/output and private review requirements to the cloud judge")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", args.run_id):
            raise ValueError("unsafe run ID")
        if args.project.resolve().is_relative_to(args.execution_dir.resolve()):
            raise ValueError("judge project must be outside execution storage")
        config = Config.load(args.project)
        role = config.get("model_roles", "content_review_judge", default={})
        if not isinstance(role, dict) or not role.get("provider") or not role.get("model"):
            raise ValueError("explicit judge role required")
        provider = config.get("model_providers", role["provider"], default={})
        if not provider:
            raise ValueError("unknown judge provider")
        endpoint = provider.get("base_url") or ("http://localhost:11434/v1" if provider.get("type") == "ollama" else "")
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("embedded endpoint credentials are not allowed")
        if not args.public and (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("cloud review requires explicit --public")
        task = load_task(args.task)
        inputs, binding = prepare_owned_review(task, execution_dir=args.execution_dir,
            evaluator_root=args.evaluator_root, source_path=args.source_path, output_path=args.output_path,
            source_format=args.source_format, output_format=args.output_format)
        plan = plan_content_review_batch(**inputs, total_output_tokens=args.total_output_tokens,
            batch_size=args.batch_size, timeout_per_call=args.timeout_per_call, private=not args.public,
            citation_mode="units")
        print(json.dumps({"execute": args.execute, "scope": "development_not_official",
            "task_fingerprint": task.fingerprint, "execution_binding": binding,
            "group_count": len(plan["groups"]), "total_output_tokens": args.total_output_tokens,
            "source_format": args.source_format, "output_format": args.output_format,
            "private": not args.public, "promotion_eligible": False}))
        if not args.execute:
            print("Preflight only; no judge request or result directory created. Use --execute to review.")
            return 0
        result = run_content_review(config, run_id=args.run_id, inputs=inputs, execution_binding=binding,
            total_output_tokens=args.total_output_tokens, batch_size=args.batch_size,
            timeout_per_call=args.timeout_per_call, private=not args.public)
        print(json.dumps(result))
        return 0 if result["status"] == "reviewed" else 1
    except KeyboardInterrupt:
        print("Review interrupted; inspect retained state before retrying. No automatic replay.", file=sys.stderr)
        return 130
    except Exception as error:
        print("Owned review failed: " + type(error).__name__ + ". Check configuration, evidence and retained state; no automatic replay.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
