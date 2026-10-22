"""Compose from audited prefix probes and independently rerun; never adopt."""
from pathlib import Path

from ..trajectory import TrajectoryLogger
from .native_run_claim import validate_run_id
from .online_probe import _execute_probe_plan
from .online_probe_feedback import audit_online_probe
from .online_stream import OnlineStream, _persist
from .owned_content_review import _reader, _json, _sha
from .v2.candidate import ContextCandidate
from .v2.configured_composer import configured_design_router
from .v2.probe_composer import compose_after_development_probes


async def run_online_composition(config, stream_directory, *, probe_directory, manifest_sha256,
                                 report_sha256, directory, composition_id, applies_to):
    validate_run_id(composition_id)
    feedback = audit_online_probe(stream_directory, directory=probe_directory,
                                 manifest_sha256=manifest_sha256, report_sha256=report_sha256)
    raw = _reader(probe_directory)("manifest.json")
    if _sha(raw) != manifest_sha256:
        raise ValueError("probe manifest changed after audit")
    plan = _json(raw)
    parents = [ContextCandidate.from_dict(value) for value in plan["candidates"]]
    if composition_id in {value.candidate_id for value in parents}:
        raise ValueError("composition requires a distinct candidate ID")
    stream, root = OnlineStream(stream_directory), Path(directory).absolute()
    if (root.resolve() != root or root.exists() or root.is_relative_to(stream.root)
            or root.is_relative_to(Path(probe_directory).resolve())):
        raise ValueError("composition directory must be new and outside source state")
    quota = stream.shared_budget()
    budget_options = {"shared_chat_budget": quota} if quota else {}
    with configured_design_router(config, cycle_id=composition_id, applies_to=applies_to,
                                  private=False, **budget_options) as (router, identity):
        if stream.status()["ledger_sha256"] != feedback["source_ledger_sha256"]:
            raise ValueError("stream advanced before composition")
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        _persist(root / "manifest.json", {"schema_version": "praxile.online_composition.v1",
            "track": "online_prefix_composition", "composition_id": composition_id, "composer": identity,
            "source_probe_manifest_sha256": manifest_sha256, "source_probe_report_sha256": report_sha256,
            "source_ledger_sha256": feedback["source_ledger_sha256"], "applies_to": applies_to,
            "parents": [value.digest for value in parents], "promotion_eligible": False})
        _persist(root / "feedback.json", feedback)
        logger = TrajectoryLogger("Compose audited online prefix probes", {})
        report = {"schema_version": "praxile.online_composition_report.v1", "status": "outcome_unknown",
            "decision": "KEEP_BASE", "candidate_adopted": False, "promotion_eligible": False,
            "automatic_replay_allowed": False, "official_evaluation": False, "task_success_proven": False}
        _persist(root / "report.json", report)
        _persist(root / "request-intent.json", {"status": "outcome_unknown", "automatic_replay_allowed": False})
        if quota:
            logger.data["shared_chat_budget_start"] = quota.status()
        try:
            candidate = compose_after_development_probes(parents, feedback["parent_reports"],
                router=router, logger=logger, candidate_id=composition_id, applies_to=applies_to)
            _persist(root / "trajectory.json", logger.data)
            _persist(root / "composition.json", {"candidate": candidate.to_dict() if candidate else None})
            if candidate is None:
                report["status"] = "composition_abstained"
            else:
                fresh = stream.probe_snapshot(plan["task_indices"], [parents[0].to_dict(), candidate.to_dict()])
                if fresh["source_ledger_sha256"] != plan["source_ledger_sha256"]:
                    raise ValueError("stream advanced during composition")
                rerun_plan = {**fresh, "schema_version": "praxile.online_composition_rerun.v1",
                    "track": "online_prefix_composition", "candidates": [candidate.to_dict()],
                    "source_probe_manifest_sha256": manifest_sha256, "source_probe_report_sha256": report_sha256}
                path = await _execute_probe_plan(stream_directory, rerun_plan, directory=root / "rerun",
                                                arms=("baseline", "composition"))
                report.update(status="composition_rerun_not_scored", candidate_digest=candidate.digest,
                              rerun_report="rerun/report.json", rerun_report_sha256=_sha(path.read_bytes()),
                              rerun_manifest_sha256=_sha((root / "rerun/manifest.json").read_bytes()))
        except BaseException as error:
            report["error_type"] = type(error).__name__
            raise
        finally:
            if quota:
                logger.data["shared_chat_budget_end"] = quota.status()
            _persist(root / "trajectory.json", logger.data)
            report["evidence_sha256"] = {name: _sha((root / name).read_bytes())
                for name in ("composition.json", "feedback.json", "trajectory.json") if (root / name).is_file()}
            _persist(root / "report.json", report)
    return root / "report.json"
