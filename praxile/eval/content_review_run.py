"""Run an explicit, durable development content review without automatic replay."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Config
from ..model import ModelRouter
from ..trajectory import TrajectoryLogger
from ..utils import write_json
from .content_review import _digest
from .content_review_batch import review_content_batched
from .v2.manifest import _redact


def validate_execution_binding(value, *, output_text=None, source_text=None):
    if value is None:
        return None
    fields = {"task_fingerprint", "execution_sha256", "trajectory_sha256", "source_sha256",
              "output_sha256", "evaluator_sha256"}
    if (not isinstance(value, dict) or not fields <= set(value) <= fields | {"output_extraction", "source_extraction", "artifact_set_sha256"}
            or any(not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                   for key, digest in value.items() if key in fields | {"artifact_set_sha256"})):
        raise ValueError("invalid execution evidence binding")
    result = dict(value)
    for side, text in (("output", output_text), ("source", source_text)):
        name = side + "_extraction"
        if name not in value:
            continue
        extraction = value[name]
        if (not isinstance(extraction, dict) or set(extraction) != {"profile", "parser_version", "worker_sha256", "text_sha256"}
                or extraction.get("profile") not in ("praxile.docx_body_text.v1", "praxile.pdf_page_text.v1")
                or not isinstance(extraction.get("parser_version"), str)
                or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", extraction["parser_version"])
                or any(not isinstance(extraction.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", extraction[key])
                       for key in ("worker_sha256", "text_sha256"))
                or (text is not None and extraction["text_sha256"] != _digest(text))):
            raise ValueError(f"invalid {side} extraction binding")
        result[name] = dict(extraction)
    return result


def run_content_review(config, *, run_id, inputs, total_output_tokens, batch_size=4,
                       output_allocations=None, private=True, retain_responses=False,
                       timeout_per_call=90, role="content_review_judge", execution_binding=None):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", run_id):
        raise ValueError("unsafe content review run ID")
    if not isinstance(inputs, dict) or set(inputs) != {"source_text", "output_text", "requirements"}:
        raise ValueError("inputs require source_text, output_text and requirements only")
    execution_binding = validate_execution_binding(execution_binding, output_text=inputs["output_text"],
                                                   source_text=inputs["source_text"])
    # Require an explicit role; a default coding route is not an independent judge declaration.
    role_config = config.get("model_roles", role, default={})
    if not isinstance(role_config, dict) or not role_config.get("provider") or not role_config.get("model"):
        raise ValueError("configure an explicit content review judge role")
    root = config.paths.root.resolve()
    parent = root
    for component in (".praxile", "eval", "v2", "content_reviews"):
        parent = parent / component
        if parent.is_symlink():
            raise ValueError("review state directory must not be a symlink")
        parent.mkdir(mode=0o700, exist_ok=True)
    run = parent / run_id
    run.mkdir(mode=0o700, exist_ok=False)
    logger = TrajectoryLogger("Explicit development content review", {})
    router = None
    report = {"status": "pending", "automatic_replay_allowed": False, "promotion_eligible": False}
    write_json(run / "report.json", report)
    try:
        router = ModelRouter(config)
        route = router.describe_route(role, private=private)
        if private:
            provider_config = config.get("model_providers", route["provider"], default={})
            endpoint = provider_config.get("base_url", "")
            if provider_config.get("type") == "ollama" and not endpoint:
                endpoint = "http://localhost:11434/v1"
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("private review requires an explicit loopback endpoint; cloud use requires --public")
        identity = {"role": role, "provider": route["provider"], "model": route["model"],
            "private": private, "config_digest": _digest(json.dumps(_redact(config.data), sort_keys=True)),
            "review_code_sha256": _digest(Path(__file__).with_name("content_review.py").read_text()),
            "batch_code_sha256": _digest(Path(__file__).with_name("content_review_batch.py").read_text())}
        manifest = {"schema_version": "praxile.content_review_run.v1", "run_id": run_id,
            "scope": "development_not_official", "judge_identity": identity,
            "judge_identity_sha256": _digest(json.dumps(identity, sort_keys=True)),
            "input_sha256": _digest(json.dumps(inputs, sort_keys=True)),
            "total_output_tokens": total_output_tokens, "batch_size": batch_size,
            "output_allocations": output_allocations, "timeout_per_call": timeout_per_call,
            "retain_responses": retain_responses, "automatic_replay_allowed": False,
            "promotion_eligible": False}
        if execution_binding is not None:
            manifest["execution_binding"] = execution_binding
        write_json(run / "manifest.json", manifest)
        write_json(run / "inputs.json", inputs)

        def checkpoint(payload):
            if retain_responses:
                write_json(run / f"response-{payload['group_index']}.json", payload)
            write_json(run / "trajectory.json", logger.data)

        def progress_checkpoint(batch):
            write_json(run / "trajectory.json", logger.data)
            write_json(run / "report.json", {**batch, "automatic_replay_allowed": False})

        report = review_content_batched(router, logger, **inputs, total_output_tokens=total_output_tokens,
            batch_size=batch_size, output_allocations=output_allocations, role=role, private=private,
            timeout_per_call=timeout_per_call, citation_mode="units", response_mode="function",
            response_checkpoint=checkpoint, progress_checkpoint=progress_checkpoint)
    except BaseException:
        batches = logger.data.get("content_review_batches", [])
        report = {**(batches[-1] if batches else {}),
                  "status": "interrupted_or_failed", "automatic_replay_allowed": False,
                  "promotion_eligible": False}
        raise
    finally:
        try:
            write_json(run / "trajectory.json", logger.data)
            write_json(run / "report.json", report)
        finally:
            if router is not None:
                for provider in router.providers.values():
                    close = getattr(getattr(provider, "transport", None), "close", None)
                    if callable(close):
                        close()
    return {"run_directory": str(run), "status": report["status"], "promotion_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--total-output-tokens", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--output-allocations", type=int, nargs="+")
    parser.add_argument("--timeout-per-call", type=int, default=90)
    parser.add_argument("--public", action="store_true", help="Explicitly allow public inputs to use the configured cloud judge")
    parser.add_argument("--retain-responses", action="store_true", help="Retain untrusted model response text in the private run directory")
    args = parser.parse_args()
    try:
        with args.inputs.open("rb") as handle:
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("input exceeds 1 MiB")
        from ..structured_response import parse_submission_object
        inputs = parse_submission_object(raw.decode("utf-8"))
        result = run_content_review(Config.load(args.project), run_id=args.run_id, inputs=inputs,
            total_output_tokens=args.total_output_tokens, batch_size=args.batch_size,
            output_allocations=args.output_allocations, timeout_per_call=args.timeout_per_call,
            private=not args.public, retain_responses=args.retain_responses)
    except Exception:
        parser.exit(2, "Content review failed; check input/configuration and retained run state. No automatic replay.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "reviewed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
