"""Offline lineage and rerun checks for prefix composition, never promotion."""
from pathlib import Path

from .online_probe_feedback import audit_online_probe, _audit_probe_records
from .online_stream import OnlineStream
from .owned_content_review import _reader, _json, _sha
from .v2.candidate import ContextCandidate
from .v2.executor_profile import common_parent_capability
from .v2.probe_composer import _composition_payload
from .v2.schema import canonical_json


def audit_online_composition(stream_directory, *, probe_directory, directory, manifest_sha256, report_sha256):
    root = Path(directory).absolute()
    read = _reader(root)
    manifest_raw, report_raw = read("manifest.json"), read("report.json")
    if _sha(manifest_raw) != manifest_sha256 or _sha(report_raw) != report_sha256:
        raise ValueError("composition manifest or report digest mismatch")
    manifest, report = _json(manifest_raw), _json(report_raw)
    if (manifest.get("schema_version") != "praxile.online_composition.v1"
            or manifest.get("track") != "online_prefix_composition"
            or manifest.get("promotion_eligible") is not False
            or report.get("schema_version") != "praxile.online_composition_report.v1"
            or report.get("status") not in {"composition_abstained", "composition_rerun_not_scored"}
            or report.get("decision") != "KEEP_BASE"
            or any(report.get(key) is not False for key in ("candidate_adopted", "promotion_eligible",
                "automatic_replay_allowed", "official_evaluation", "task_success_proven"))):
        raise ValueError("composition has no conservative terminal result")
    feedback = audit_online_probe(stream_directory, directory=probe_directory,
        manifest_sha256=manifest["source_probe_manifest_sha256"], report_sha256=manifest["source_probe_report_sha256"])
    source_raw = _reader(probe_directory)("manifest.json")
    if _sha(source_raw) != manifest["source_probe_manifest_sha256"]:
        raise ValueError("source probe changed during audit")
    source = _json(source_raw)
    parents = [ContextCandidate.from_dict(value) for value in source["candidates"]]
    if (manifest.get("parents") != [parent.digest for parent in parents]
            or manifest.get("composition_id") in {parent.candidate_id for parent in parents}
            or manifest.get("source_ledger_sha256") != feedback["source_ledger_sha256"]):
        raise ValueError("composition parent lineage mismatch")
    evidence = report.get("evidence_sha256")
    if not isinstance(evidence, dict) or set(evidence) != {"composition.json", "feedback.json", "trajectory.json"}:
        raise ValueError("composition evidence missing")
    files = {}
    for name, digest in evidence.items():
        raw = read(name, max_bytes=16 * 1024**2)
        if _sha(raw) != digest:
            raise ValueError("composition evidence changed")
        files[name] = _json(raw)
    if files["feedback.json"] != feedback:
        raise ValueError("composition feedback differs from replay")
    records = files["trajectory.json"].get("development_compositions")
    if (not isinstance(records, list) or len(records) != 1
            or records[0].get("candidate_id") != manifest["composition_id"]):
        raise ValueError("composition trajectory does not bind the candidate")
    payload, _, _, allowed_refs = _composition_payload(parents, feedback["parent_reports"], applies_to=manifest["applies_to"])
    if records[0].get("input_sha256") != _sha(canonical_json(payload).encode()):
        raise ValueError("composition prompt differs from audited feedback")
    generated = files["composition.json"]
    if set(generated) != {"candidate"}:
        raise ValueError("invalid composition result")
    rerun_feedback = None
    if generated["candidate"] is None:
        if report["status"] != "composition_abstained" or records[0].get("status") != "abstained":
            raise ValueError("composition abstention mismatch")
    else:
        candidate = ContextCandidate.from_dict(generated["candidate"])
        if (report["status"] != "composition_rerun_not_scored" or report.get("candidate_digest") != candidate.digest
                or records[0].get("candidate_digest") != candidate.digest or records[0].get("status") != "drafted"
                or candidate.candidate_id != manifest["composition_id"] or candidate.applies_to != manifest["applies_to"]
                or not candidate.evidence_refs or not set(candidate.evidence_refs) <= set(allowed_refs)
                or report.get("rerun_report") != "rerun/report.json"):
            raise ValueError("composition candidate or rerun mismatch")
        common_parent_capability([*parents, candidate])
        stream = OnlineStream(stream_directory)
        expected = stream.probe_snapshot(source["task_indices"], [parents[0].to_dict(), candidate.to_dict()])
        expected.update(schema_version="praxile.online_composition_rerun.v1", track="online_prefix_composition",
            candidates=[candidate.to_dict()], source_probe_manifest_sha256=manifest["source_probe_manifest_sha256"],
            source_probe_report_sha256=manifest["source_probe_report_sha256"])
        rerun_raw, result_raw = read("rerun/manifest.json"), read("rerun/report.json", max_bytes=16 * 1024**2)
        if (_sha(rerun_raw) != report.get("rerun_manifest_sha256") or _sha(result_raw) != report.get("rerun_report_sha256")
                or _json(rerun_raw) != expected):
            raise ValueError("composition rerun plan or receipt changed")
        rerun_feedback = _audit_probe_records(stream, root / "rerun", expected, _json(result_raw),
            _sha(rerun_raw), _sha(result_raw), arms=("baseline", "composition"))
    if OnlineStream(stream_directory).status()["ledger_sha256"] != feedback["source_ledger_sha256"]:
        raise ValueError("stream advanced during composition audit")
    return {"schema_version": "praxile.online_composition_audit.v1", "track": "online_prefix_composition",
        "status": report["status"], "manifest_sha256": manifest_sha256, "report_sha256": report_sha256,
        "source_ledger_sha256": feedback["source_ledger_sha256"], "rerun_feedback": rerun_feedback,
        "local_file_consistency_checked": True, "semantic_accuracy_verified": False,
        "decision": "KEEP_BASE", "promotion_eligible": False, "task_success_proven": False}
