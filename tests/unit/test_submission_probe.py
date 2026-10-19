import json
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from praxile.eval.submission_probe import probe_submission
from praxile.trajectory import TrajectoryLogger


@pytest.mark.parametrize("mutation, expected", [
    (lambda value: value, "passed"),
    (lambda value: dict(value, candidates=value["candidates"] * 2), "failed"),
    (lambda value: dict(value, candidates=[dict(item, start=str(item["start"])) for item in value["candidates"]]), "failed"),
    (lambda value: dict(value, candidates=[dict(item, evidence_ref="PRIVATE") for item in value["candidates"]]), "failed"),
])
def test_probe_checks_exact_structure_without_executing_or_retaining_payload(mutation, expected):
    calls = []
    def chat(messages, **options):
        calls.append(options)
        return {"content": json.dumps(mutation(json.loads(messages[1]["content"]))),
                "usage": {"prompt_tokens": 5, "completion_tokens": 10}, "finish_reason": "tool_calls"}
    logger = TrajectoryLogger("probe", {})
    report = probe_submission(SimpleNamespace(chat=chat), logger)
    assert report["status"] == expected
    assert report["promotion_eligible"] is False
    assert report["capabilities_updated"] is False
    assert "PRIVATE" not in json.dumps(report)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 1024
    assert logger.data["cost"]["model_calls"] == 1


def test_probe_sanitizes_provider_failure_and_does_not_retry():
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("PRIVATE")
    report = probe_submission(SimpleNamespace(chat=chat), TrajectoryLogger("probe", {}))
    assert report["status"] == "error"
    assert report["error_type"] == "TimeoutError"
    assert "PRIVATE" not in json.dumps(report)
    assert calls == [1]


def test_candidate_probe_reuses_actual_contract_and_never_generates_assets():
    from praxile.eval.v2.candidate_response import candidate_response_contract
    from praxile.eval.v2.executor_profile import profile_context
    calls = []
    def chat(messages, **options):
        calls.append(options)
        assert json.loads(messages[1]["content"])["composition"]["action_examples"][0]["start"] == 1
        return {"content": messages[1]["content"], "finish_reason": "tool_calls"}
    report = probe_submission(SimpleNamespace(chat=chat), TrajectoryLogger("probe", {}), candidate_contract=True)
    capability = profile_context({"schema_version": "praxile.executor_action_profile.v2",
        "executor": "praxile", "version": "synthetic-protocol-v1",
        "actions": ["read_document", "revise_docx", "check_document_layout"]})
    assert calls[0]["response_contract"] == candidate_response_contract(["event:probe:first", "event:probe:second"], capability)
    assert report["status"] == "passed"
    assert report["contract_profile"] == "candidate"
    assert report["capabilities_updated"] is False


@pytest.mark.parametrize("field, value, failed_check", [
    ("content", "PRIVATE", "exact_text"),
    ("evidence_refs", ["PRIVATE"], "exact_references"),
    ("action_examples", [{"type": "read_document", "path": "synthetic.docx", "start": "1", "limit": 5}], "exact_actions_and_types"),
])
def test_candidate_copy_failure_distinguishes_text_references_and_actions(field, value, failed_check):
    def chat(messages, **kwargs):
        response = json.loads(messages[1]["content"])
        response["candidates"][0][field] = value
        return {"content": json.dumps(response), "finish_reason": "tool_calls"}
    report = probe_submission(SimpleNamespace(chat=chat), TrajectoryLogger("probe", {}), candidate_contract=True)
    assert report["status"] == "failed"
    assert report["checks"][failed_check] is False
    for check in {"exact_text", "exact_references", "exact_actions_and_types"} - {failed_check}:
        assert report["checks"][check] is True
    assert "PRIVATE" not in json.dumps(report)


def test_probe_persists_and_refuses_duplicate_id(tmp_path, monkeypatch):
    from praxile.eval import submission_probe as module
    calls, closed = [], []
    def chat(messages, **kwargs):
        calls.append(1)
        assert json.loads((tmp_path / "eval/v2/submission_probes/p1/report.json").read_text())["status"] == "unknown"
        return {"content": messages[1]["content"], "usage": {}}
    @contextmanager
    def router(*args, **kwargs):
        try:
            yield SimpleNamespace(chat=chat), {"model": "fixture"}
        finally:
            closed.append(1)
    monkeypatch.setattr(module, "configured_design_router", router)
    config = SimpleNamespace(paths=SimpleNamespace(state=tmp_path))
    assert module.run_probe(config, probe_id="p1")["status"] == "passed"
    root = tmp_path / "eval/v2/submission_probes/p1"
    assert (root / "trajectory.json").is_file()
    with pytest.raises(FileExistsError):
        module.run_probe(config, probe_id="p1")
    assert calls == [1]
    assert len(closed) == 2
