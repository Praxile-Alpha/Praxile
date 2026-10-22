import json

import pytest

from praxile.eval.broker_content_review import run_broker_content_review
from praxile.eval.credential_broker import ChatCredentialBroker
from praxile.eval.persistent_chat_budget import PersistentChatBudget


def settings(tmp_path, max_calls=3):
    key = tmp_path / "private-key"
    key.write_text("fixture-provider-secret")
    key.chmod(0o600)
    quota = PersistentChatBudget.create(tmp_path / "quota.sqlite", max_calls=max_calls,
        output_budget=300, ttl_seconds=60)
    quota.reserve("executor", 100)
    return dict(directory=tmp_path / "review", credential_file=key,
        endpoint="https://provider.invalid/v1/chat/completions", model="fixture", shared_budget=quota,
        total_output_tokens=200, batch_size=1, public=True,
        inputs={"source_text": "Keep the date.", "output_text": "Keep the date.", "requirements": [
            {"id": "a", "requirement": "Retain date", "source_quote": "date"},
            {"id": "b", "requirement": "Keep instruction", "source_quote": "Keep"}]})


@pytest.mark.parametrize("exhausted,cancelled", [(False, False), (True, False), (False, True)])
def test_real_review_pipeline_shares_executor_quota_and_retains_evidence(tmp_path, monkeypatch, exhausted, cancelled):
    kwargs = settings(tmp_path, max_calls=2 if exhausted else 3)
    forwarded = []
    brokers = []
    kwargs["cancel_requested"] = lambda: cancelled and bool(forwarded)
    def forward(self, raw):
        brokers.append(self)
        body = json.loads(raw)
        forwarded.append(body)
        data = json.loads(body["messages"][1]["content"])
        items = [{"id": item["id"], "verdict": "preserved", "reason": "Retained in output",
                  "output_refs": [data["output_units"][0]["id"]]} for item in data["requirements"]]
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": items})}}]},
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    path = run_broker_content_review(**kwargs)
    report = json.loads(path.read_text())
    assert report["status"] == ("abstained" if exhausted or cancelled else "reviewed")
    count = 1 if exhausted or cancelled else 2
    assert len(forwarded) == count
    quota = kwargs["shared_budget"].status()
    assert quota["roles"]["executor"]["reserved_calls"] == 1
    assert quota["roles"]["judge"] == {"reserved_calls": count, "reserved_output": count * 100}
    assert quota["remaining_calls"] == (1 if cancelled else 0)
    manifest = json.loads((path.parent / "manifest.json").read_text())
    assert len(manifest["contract_sha256"]) == 2
    assert manifest["shared_budget_start"]["roles"] == {"executor": {"reserved_calls": 1, "reserved_output": 100}}
    assert json.loads((path.parent / "broker-usage.json").read_text())["revoked"] is True
    for file in path.parent.glob("*.json"):
        assert "fixture-provider-secret" not in file.read_text()
        assert brokers[0].capability not in file.read_text()
    with pytest.raises(FileExistsError):
        run_broker_content_review(**kwargs)
    assert len(forwarded) == count


def test_failed_provider_call_is_charged_and_not_retried(tmp_path, monkeypatch):
    kwargs = settings(tmp_path)
    calls = []
    def fail(self, raw):
        calls.append(True)
        raise TimeoutError("private provider error")
    monkeypatch.setattr(ChatCredentialBroker, "_forward", fail)
    path = run_broker_content_review(**kwargs)
    assert json.loads(path.read_text())["status"] == "abstained"
    assert calls == [True]
    assert kwargs["shared_budget"].status()["roles"]["judge"]["reserved_calls"] == 1
    assert "private provider error" not in path.read_text()


@pytest.mark.parametrize("defect", ["private", "extra_input", "no_quota", "unsafe_key"])
def test_preflight_does_not_dispatch_or_create_review(tmp_path, monkeypatch, defect):
    kwargs = settings(tmp_path)
    if defect == "private":
        kwargs["public"] = False
    elif defect == "extra_input":
        kwargs["inputs"]["rubric"] = "not allowed"
    elif defect == "no_quota":
        kwargs["shared_budget"] = None
    else:
        kwargs["credential_file"].chmod(0o644)
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("no dispatch"))
    with pytest.raises(ValueError):
        run_broker_content_review(**kwargs)
    assert not kwargs["directory"].exists()
