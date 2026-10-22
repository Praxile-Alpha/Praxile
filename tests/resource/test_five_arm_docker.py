"""Real owned containers and worker; scripted upstream, not model-quality evidence."""
import asyncio
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("harbor")
pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.slow]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.skipif(not (os.environ.get("PRAXILE_TEST_LEASE_IMAGE") and os.environ.get("PRAXILE_TEST_WHEEL")),
                    reason="requires pinned Docker image and fresh worker wheel")
@pytest.mark.parametrize("exercise_reflection", [False, True], ids=["delivery", "reflection"])
def test_real_five_arm_lifecycle_and_audit(tmp_path, monkeypatch, exercise_reflection):
    from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
    from praxile.eval.credential_broker import ChatCredentialBroker
    from praxile.eval.five_arm_execution import execute_study
    from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan
    from praxile.eval.five_arm_selection import selection_policy
    from praxile.eval.five_arm_judge import judge_identity
    from praxile.eval.five_arm_audit import audit_study
    from praxile.eval.five_arm_public import public_summary
    from praxile.eval.v2.candidate import ContextCandidate
    from praxile.eval.v2.schema import canonical_json
    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    image = os.environ["PRAXILE_TEST_LEASE_IMAGE"]
    inputs = tmp_path / "public"
    inputs.mkdir()
    source = b"public checklist"
    (inputs / "source.txt").write_bytes(source)
    private = tmp_path / "judge-private"
    private.mkdir()
    requirements = {"requirements": [{"id": "r", "requirement": "Preserve public checklist", "source_quote": "public checklist"}]}
    raw_requirements = json.dumps(requirements).encode()
    (private / "requirements.json").write_bytes(raw_requirements)
    task = BenchmarkTask(task_id="docker-five-arm", benchmark="scripted-lifecycle", dataset_revision="1",
        split="development", instruction="Read inputs/source.txt and preserve its public checklist in answer.txt.",
        environment=EnvironmentSpec("harbor", "fixture", image),
        evaluator=EvaluatorReference("praxile-content-review", "1", "requirements.json", "sha256:" + sha(raw_requirements)),
        inputs=(InputArtifact("source.txt", "source.txt", "sha256:" + sha(source)),),
        outputs=(OutputContract("answer.txt", "text/plain"),))
    candidate = ContextCandidate(candidate_id="fixture", version="1", title="Inspect source", candidate_type="context_policy",
        context_item={"content": "Read the source checklist before writing the answer."}, confidence=0.0,
        evidence_refs=("event:scripted-fixture",), expected_effect={"hypothesis": "lifecycle only"},
        applies_to={"repositories": ["public/docs"], "task_signals": ["checklist"], "does_not_apply_when": ["unrelated"]})
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate.to_dict()))
    key = tmp_path / "fake-key"
    key.write_text("fixture-not-a-real-key")
    key.chmod(0o600)
    policy = selection_policy("first_delivered")
    judge = {"credential_file": key, "endpoint": "https://judge.invalid/v1/chat/completions", "model": "scripted-judge",
        "public": True, "total_output_tokens": 1000, "evaluator_root": private,
        "artifacts": {task.task_id: {"source_path": "source.txt", "output_path": "answer.txt"}}}
    identities = {name: "sha256:" + "a" * 64 for name in IDENTITIES}
    identities["terminal_selection"] = "sha256:" + sha(canonical_json(policy).encode())
    identities["judge"] = "sha256:" + sha(canonical_json(judge_identity(judge, {task.task_id: task})).encode())
    plan = compile_plan({"study_id": "docker-five-arm", "task_ids": [task.task_id], "heldout_task_ids": ["unused-hidden"],
        "repetitions": 1, "repeat_attempts": 2, "identities": identities,
        "budget": {"total_tokens": 100000, "model_calls": 20 if exercise_reflection else 12, "wall_seconds": 180}, "arm_order": list(ARMS)})
    calls = defaultdict(int)
    primary_steps = defaultdict(int)
    reflection_calls, note_uses = [], []
    actions = [{"type": "read_file", "path": "inputs/source.txt"},
               {"type": "edit_file", "path": "answer.txt", "content": source.decode()},
               {"type": "finish", "summary": "Scripted lifecycle only", "status": "completed"}]
    if exercise_reflection:
        actions = [{"type": "subtask_begin", "goal": "Inspect public checklist"}, actions[0],
            {"type": "subtask_end", "claimed_outcome": "completed", "summary": "Source inspected; outcome unverified"},
            {"type": "subtask_reflect", "source_episode_id": "filled-from-observation", "ttl_steps": 5,
             "requirements": {"state_equals": {"git.is_repo": False}}}, actions[1], actions[2]]
    def forward(broker, raw):
        body = json.loads(raw)
        calls[broker] += 1
        if broker._budget_role == "judge":
            data = json.loads(body["messages"][1]["content"])
            message = {"tool_calls": [{"type": "function", "function": {
                "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": [
                    {"id": "r", "verdict": "preserved", "output_refs": [data["output_units"][0]["id"]], "reason": "Scripted"}]})}}]}
        elif body["messages"][0]["content"].startswith("Reflect on one subtask."):
            evidence = json.loads(body["messages"][1]["content"])["evidence"]
            reflection_calls.append(broker)
            message = {"content": json.dumps({"decision": "note", "note_text": "SCRIPTED_NOTE: preserve the inspected checklist.",
                                               "evidence_refs": [evidence[0]["action_id"]]})}
        else:
            assert "requirements.json" not in raw.decode()
            assert "fixture-not-a-real-key" not in raw.decode()
            index = primary_steps[broker]
            primary_steps[broker] += 1
            action = dict(actions[min(index, len(actions) - 1)])
            if action["type"] == "subtask_reflect":
                observations = [json.loads(item["content"].removeprefix("Observation:\n"))
                    for item in body["messages"] if item.get("content", "").startswith("Observation:\n")]
                episode_ids = [item["data"]["subtask_episode_id"] for item in observations
                               if isinstance(item.get("data"), dict) and "subtask_episode_id" in item["data"]]
                assert episode_ids
                action["source_episode_id"] = episode_ids[-1]
            if any("Task-local unverified hypotheses" in item.get("content", "")
                   and "SCRIPTED_NOTE" in item["content"] for item in body["messages"]):
                note_uses.append(broker)
            message = {"content": json.dumps(action)}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    execution = {"wheel": wheel, "wheel_sha256": sha(wheel.read_bytes()), "model": "scripted-executor",
        "base_url": "https://provider.invalid/v1", "credential_file": key, "output_budget": 100000,
        "max_steps": 8 if exercise_reflection else 6, "model_request_options": {"reasoning_effort": "low"},
        "development_candidate_file": candidate_path, "development_candidate_sha256": sha(candidate_path.read_bytes()),
        "repository": "public/docs", "terminal_selection": policy}
    path = asyncio.run(execute_study(plan, {task.task_id: task}, directory=tmp_path / "study",
        execution=execution, input_source_roots={task.task_id: inputs}, judge=judge))
    args = dict(directory=path.parent, study_sha256=sha(path.read_bytes()), evaluator_root=private)
    audit = audit_study(plan, {task.task_id: task}, **args)
    assert audit["complete_task_count"] == 1
    assert len(calls) == 11  # Six fresh executors plus five independent judges.
    assert len(reflection_calls) == (2 if exercise_reflection else 0)
    assert set(note_uses) == set(reflection_calls)
    assert audit["promotion_eligible"] is False
    for row in audit["cell_reviews"]:
        for attempt in row["mechanism_observations"]:
            observed = attempt["observations"]
            assert observed["task_local_actions"] is not None
            if exercise_reflection:
                enabled = row["cell_id"].endswith((".task_local_only", ".full_praxile"))
                counts = observed["task_local_actions"]["subtask_reflect"]
                assert counts["attempted"] == 1
                assert counts["success"] == int(enabled)
                assert counts["blocked"] == int(not enabled)
                if enabled:
                    assert observed["reflection_outcomes"]["note_created"] == 1
            if row["cell_id"].endswith((".experience_only", ".full_praxile")):
                assert observed["activation_observed"] is True
                assert observed["injection_observed"] is True
    report = json.loads(path.read_text())
    for cell in report["cells"]:
        cell_path = path.parent / cell["report"]
        for attempt in json.loads(cell_path.read_text())["attempts"]:
            execution_path = cell_path.parent / attempt["report"]
            run = json.loads(execution_path.read_text())
            assert run["cleanup_verified"] is True
            assert run["required_outputs_collected"] is True
            assert (execution_path.parent / "outputs/answer.txt").read_bytes() == source
            assert json.loads((execution_path.parent / "agent/praxile-broker-usage.json").read_text())["revoked"] is True
            trajectory = json.loads((execution_path.parent / "agent/praxile-result.json").read_text())["trajectory"]
            notes = trajectory.get("task_notes", [])
            enabled = run["task_local_memory_enabled"] and exercise_reflection
            assert len(notes) == int(enabled)
            assert all(note["independently_verified"] is False and note["reflection_evidence_refs"] for note in notes)
    public = public_summary(plan, {task.task_id: task}, **args, public=True)
    assert public["complete_label_tasks"] == 1
    assert "fixture-not-a-real-key" not in json.dumps(public)
