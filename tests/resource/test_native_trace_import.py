import hashlib
import json

import pytest

from praxile.config import ProjectPaths
from praxile.services.native_trace_import import NativeTraceImportService
from praxile.trace import native_run_to_events

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.sqlite_resource]


def files(tmp_path, mode="baseline"):
    result = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
        "trajectory": {"task_id": "native-task", "start_time": "2026-10-19T00:00:00Z",
                       "user_task": "Fixture task", "result": {"status": "completed"}}}
    if mode != "baseline":
        active = mode == "active"
        result["experience_mode"] = "development_candidate"
        result["development_context"] = {"schema_version": "praxile.native_development_context.v1",
            "track": "development", "candidate_digest": "sha256:" + "a" * 64,
            "task_body_sha256": "b" * 64, "activation": {"activated": active}, "injected": active,
            "promotion_eligible": False, "permission_granted": False}
        if active:
            result["trajectory"]["experimental_context"] = {"content": "fixture suggestion",
                "injected": True, "sha256": hashlib.sha256(b"fixture suggestion").hexdigest()}
    result_path, events_path = tmp_path / "result.json", tmp_path / "events.json"
    result_path.write_text(json.dumps(result))
    projection = {"schema_version": "praxile.native_event_projection.v1",
        "source_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "events": [event.to_dict() for event in native_run_to_events(result)]}
    events_path.write_text(json.dumps(projection))
    return result_path, events_path, projection


@pytest.mark.parametrize("mode", ["baseline", "active", "abstain"])
def test_cli_import_is_idempotent_and_rebuildable(tmp_path, capsys, mode):
    from praxile.cli_parser import build_parser
    result, events, _ = files(tmp_path, mode)
    project = tmp_path / "project"
    project.mkdir()
    args = build_parser().parse_args(["eval", "import-native", "--result", str(result), "--events", str(events)])
    assert args.func(args, project) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["appended_events"] == first["event_count"] > 0
    assert args.func(args, project) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["appended_events"] == 0
    service = NativeTraceImportService(ProjectPaths(project))
    service.store.rebuild_index()
    assert len(service.store.list_events(trace_id=first["trace_id"])) == first["event_count"]
    assert len(service.store.list_events(event_type="CONTEXT_ACTIVATION")) == int(mode != "baseline")
    assert len(service.store.list_events(event_type="CONTEXT_INJECT")) == int(mode == "active")
    assert first["official_evaluation"] == "not_imported" and first["provenance_attested"] is False


@pytest.mark.parametrize("tamper", ["source", "event", "missing_event"])
def test_mismatch_rejected_before_any_store_write(tmp_path, tamper):
    result, events, projection = files(tmp_path)
    if tamper == "source":
        result.write_text(result.read_text() + " ")
    elif tamper == "event":
        projection["events"][0]["payload"]["user_task"] = "forged"
    else:
        projection["events"].pop()
    events.write_text(json.dumps(projection))
    paths = ProjectPaths(tmp_path / "project")
    with pytest.raises(ValueError, match="mismatch|differs"):
        NativeTraceImportService(paths).import_files(result, events)
    assert not paths.state.exists()


def test_receipt_failure_can_resume_without_duplicate_events(tmp_path, monkeypatch):
    import praxile.services.native_trace_import as module
    result, events, _ = files(tmp_path)
    service = NativeTraceImportService(ProjectPaths(tmp_path / "project"))
    original = module.write_json
    def fail(*args, **kwargs):
        raise OSError("simulated receipt failure")
    monkeypatch.setattr(module, "write_json", fail)
    with pytest.raises(OSError, match="receipt failure"):
        service.import_files(result, events)
    monkeypatch.setattr(module, "write_json", original)
    receipt = service.import_files(result, events)
    assert receipt["appended_events"] == 0
    assert receipt["already_present_events"] == receipt["event_count"]
