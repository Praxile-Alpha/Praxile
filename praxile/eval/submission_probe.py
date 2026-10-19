"""Explicit synthetic protocol check, not a model quality or task benchmark."""
import argparse
import hashlib
import json
from pathlib import Path

from ..config import Config
from ..trajectory import TrajectoryLogger
from ..utils import write_json
from .v2.configured_composer import configured_design_router
from .v2.schema import canonical_json


def probe_submission(router, logger, *, private=False, candidate_contract=False):
    sample = {"decision": "propose", "candidates": [
        {"evidence_ref": "event:probe:first", "start": 1, "limit": 5},
        {"evidence_ref": "event:probe:second", "start": 2, "limit": 3}]}
    item = {"type": "object", "properties": {
        "evidence_ref": {"type": "string", "enum": ["event:probe:first", "event:probe:second"]},
        "start": {"type": "integer", "minimum": 1, "maximum": 100000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
        "required": ["evidence_ref", "start", "limit"], "additionalProperties": False}
    contract = {"name": "submit_protocol_probe_v1", "schema": {"type": "object",
        "properties": {"decision": {"type": "string", "enum": ["propose"]},
            "candidates": {"type": "array", "minItems": 2, "maxItems": 2, "items": item}},
        "required": ["decision", "candidates"], "additionalProperties": False}}
    if candidate_contract:
        from .v2.candidate_response import candidate_response_contract
        from .v2.executor_profile import profile_context
        capability = profile_context({"schema_version": "praxile.executor_action_profile.v2",
            "executor": "praxile", "version": "synthetic-protocol-v1",
            "actions": ["read_document", "revise_docx", "check_document_layout"]})
        refs = ["event:probe:first", "event:probe:second"]
        contract = candidate_response_contract(refs, capability)
        def draft(text, ref):
            return {"content": text, "hypothesis": "Synthetic copy check only.", "evidence_refs": [ref],
                "action_examples": [{"type": "read_document", "path": "synthetic.docx", "start": 1, "limit": 5}]}
        sample = {"decision": "propose", "candidates": [draft("First synthetic draft", refs[0]),
            draft("Second synthetic draft", refs[1])], "composition": draft("Synthetic composition", refs[0])}
    record = {"schema_version": "praxile.submission_probe.v1", "status": "unknown",
        "scope": "one_synthetic_submission", "contract_sha256": hashlib.sha256(canonical_json(contract).encode()).hexdigest(),
        "contract_profile": "candidate" if candidate_contract else "basic",
        "promotion_eligible": False, "capabilities_updated": False}
    try:
        response = router.chat([
            {"role": "system", "content": "Submit the supplied synthetic JSON exactly using the named function. "
                "Keep numbers as JSON numbers, not strings. No tools execute and no files are accessed."},
            {"role": "user", "content": canonical_json(sample)}],
            purpose="proposal_composer", private=private, max_tokens=1024, timeout=60,
            temperature=0, response_contract=contract)
        logger.add_model_cost(response.get("usage", {}), route={**response.get("route", {}),
            "purpose": "proposal_composer", "latency_ms": response.get("latency_ms")})
        record["transport_evidence"] = response.get("transport_evidence")
        record["finish_reason"] = response.get("finish_reason")
        content = response.get("content", "")
        record["response_sha256"] = hashlib.sha256(content.encode()).hexdigest()
        record["response_chars"] = len(content)
        value = json.loads(content)
        candidates = value.get("candidates") if isinstance(value, dict) else None
        checks = {
            "top_level_fields": isinstance(value, dict) and set(value) == set(sample),
            "decision": isinstance(value, dict) and value.get("decision") == "propose",
            "exactly_two_candidates": isinstance(candidates, list) and len(candidates) == 2,
            "not_truncated": response.get("finish_reason") not in {"length", "max_tokens"},
        }
        checks["exact_payload_and_types"] = canonical_json(value) == canonical_json(sample)
        if candidate_contract:
            drafts = [*candidates, value.get("composition")] if checks["exactly_two_candidates"] else []
            expected = [*sample["candidates"], sample["composition"]]
            checks["draft_fields"] = len(drafts) == 3 and all(
                isinstance(draft, dict) and set(draft) == set(wanted) for draft, wanted in zip(drafts, expected))
            for name, fields in {"exact_references": ["evidence_refs"],
                                 "exact_actions_and_types": ["action_examples"],
                                 "exact_text": ["content", "hypothesis"]}.items():
                checks[name] = checks["draft_fields"] and all(
                    canonical_json(draft[field]) == canonical_json(wanted[field])
                    for draft, wanted in zip(drafts, expected) for field in fields)
        record.update(status="passed" if all(checks.values()) else "failed", checks=checks)
    except Exception as error:
        record.update(status="error", error_type=type(error).__name__)
    return record


def run_probe(config, *, probe_id, private=False, candidate_contract=False):
    scope = {"repositories": ["synthetic/protocol"], "task_signals": ["synthetic submission"],
             "does_not_apply_when": ["real task evaluation"]}
    with configured_design_router(config, cycle_id=probe_id, applies_to=scope, private=private) as (router, identity):
        root = config.paths.state / "eval/v2/submission_probes" / probe_id
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        write_json(root / "manifest.json", {"schema_version": "praxile.submission_probe_run.v1",
            "probe_id": probe_id, "model": identity, "max_designer_calls": 1,
            "contract_profile": "candidate" if candidate_contract else "basic",
            "max_output_tokens": 1024, "request_timeout_seconds": 60,
            "limitations": ["Configured transport retries remain in effect.",
                            "A pass does not attest complex schemas, task quality or future reliability."]})
        write_json(root / "report.json", {"status": "unknown", "automatic_replay_allowed": False})
        logger = TrajectoryLogger("Synthetic submission protocol check", {})
        try:
            report = probe_submission(router, logger, private=private, candidate_contract=candidate_contract)
            write_json(root / "report.json", report)
        finally:
            write_json(root / "trajectory.json", logger.data)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--candidate-contract", action="store_true",
                        help="Use the actual candidate function schema with fixed synthetic drafts")
    args = parser.parse_args()
    report = run_probe(Config.load(args.project), probe_id=args.probe_id, private=args.private,
                       candidate_contract=args.candidate_contract)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
