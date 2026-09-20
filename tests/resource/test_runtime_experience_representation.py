from __future__ import annotations

import pytest
import json

from praxile.config import Config
from praxile.experience_context import RuntimeExperienceContext
from praxile.runtime import AgentRuntime
from praxile.store import ExperienceStore
from praxile.trajectory import TrajectoryLogger


pytestmark = [pytest.mark.resource, pytest.mark.runtime_resource, pytest.mark.sqlite_resource]


def _asset(store: ExperienceStore, root, relative: str, content: str, *, confidence: float | None = None) -> str:
    path = root / ".praxile" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if confidence is not None:
        sidecar = path.with_name(f"{path.name}.meta.json")
        sidecar.write_text(f'{{"confidence": {confidence}}}\n', encoding="utf-8")
    store.index_asset(path)
    return path.relative_to(root).as_posix()


def test_normal_run_routes_approved_assets_and_records_actual_injection(tmp_path, monkeypatch) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    skill = _asset(store, tmp_path, "skills/parser/SKILL.md",
                   "# Parser repair skill\nRun focused parser regression tests before editing the branch.\n")
    failure = _asset(store, tmp_path, "experience/failures/parser.md",
                     "# Parser failure pattern\nNested parser regression needs state-reset verification.\n")
    memory = _asset(store, tmp_path, "memory/parser.md",
                    "# Parser memory\nProject parser tests run with pytest.\n")
    runtime = AgentRuntime(config)
    prompts = []
    analysis_inputs = []
    original_analysis = runtime.analyzer.analyze
    def capture_analysis(task, context):
        analysis_inputs.extend(context)
        return original_analysis(task, context)
    monkeypatch.setattr(runtime.analyzer, "analyze", capture_analysis)
    original = runtime._initial_messages
    def capture(*args, **kwargs):
        messages = original(*args, **kwargs)
        prompts.append(messages[1]["content"])
        return messages
    monkeypatch.setattr(runtime, "_initial_messages", capture)
    trajectory = runtime.run("Fix parser regression", max_steps=1)
    decisions = {item["path"]: item for item in trajectory["experience_representation"]["decisions"]}
    assert decisions[skill]["representation"] == "skill"
    assert decisions[failure]["representation"] == "failure_pattern"
    assert decisions[memory]["representation"] == "summary_memory"
    assert not any(item["path"] in {skill, failure, memory} for item in analysis_inputs)
    assert prompts and "representation=skill" in prompts[0]
    assert "representation=failure_pattern" in prompts[0]
    assert "representation=summary_memory" in prompts[0]
    loaded = {item["path"]: item for item in trajectory["loaded_assets"]}
    assert loaded[skill]["used_in_prompt"] is True
    assert loaded[failure]["used_in_prompt"] is True
    assert loaded[memory]["used_in_prompt"] is True
    funnel = {item["path"]: item for item in trajectory["experience_activation"]["assets"]}
    assert funnel[skill]["stages"]["injected"] is True
    assert funnel[failure]["stages"]["injected"] is True
    assert trajectory["evolution_summary"]["used_assets"] >= 3
    assert trajectory["self_judgment"]["available"] is False
    assert trajectory["verifier_outcome"]["available"] is False
    assert trajectory["judgment_calibration"]["promotion_eligible"] is False
    persisted_judge = runtime.store.get_judge_observation(trajectory["task_id"])
    assert persisted_judge is not None
    assert persisted_judge["schema"] == "praxile.judge_observation.v1"


def test_gate_pause_does_not_credit_retrieved_asset_as_injected(tmp_path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    skill = _asset(store, tmp_path, "skills/parser/SKILL.md",
                   "# Parser skill\nInspect parser auth session schema before changing shared code.\n")
    trajectory = AgentRuntime(config).run("Change parser auth session schema", max_steps=1)
    assert trajectory["result"]["status"] == "needs_human"
    loaded = {item["path"]: item for item in trajectory["loaded_assets"]}
    assert loaded[skill]["used_in_prompt"] is False
    funnel = {item["path"]: item for item in trajectory["experience_activation"]["assets"]}
    assert funnel[skill]["stages"]["retrieved"] is True
    assert funnel[skill]["stages"]["injected"] is False
    assert trajectory["evolution_summary"]["used_assets"] == 0


def test_low_evidence_asset_abstains_and_symlink_cannot_escape(tmp_path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    failure = _asset(store, tmp_path, "experience/failures/parser.md",
                     "# Parser regression\nCheck parser state reset.\n", confidence=0.1)
    outside = tmp_path / "outside.txt"
    outside.write_text("Secret external content", encoding="utf-8")
    link = config.paths.state / "memory" / "escape.md"
    link.symlink_to(outside)
    retrieved = store.retrieve("Fix parser regression", limit=8)
    retrieved.append({"path": ".praxile/memory/escape.md", "kind": "memory", "type": "memory",
                      "snippet": "Secret external content", "matched_terms": ["parser"]})
    prompt, decisions = RuntimeExperienceContext(config, store).plan(
        "Fix parser regression", "task-test", {"task_type": "bugfix"}, retrieved
    )
    by_path = {item["path"]: item for item in decisions}
    assert by_path[failure]["representation"] == "none"
    assert by_path[".praxile/memory/escape.md"]["representation"] == "none"
    assert "Secret external content" not in str(prompt)
    trajectory = AgentRuntime(config).run("Fix parser regression", max_steps=1)
    loaded = {item["path"]: item for item in trajectory["loaded_assets"]}
    assert loaded[failure]["used_in_prompt"] is False
    funnel = {item["path"]: item for item in trajectory["experience_activation"]["assets"]}
    assert funnel[failure]["stages"]["injected"] is False
    config.data["context"]["experience_representation_enabled"] = False
    legacy, legacy_decisions = RuntimeExperienceContext(config, store).plan(
        "Fix parser regression", "legacy-task", {"task_type": "bugfix"}, retrieved
    )
    assert any(item["path"] == failure for item in legacy)
    assert {item["path"]: item for item in legacy_decisions}[failure]["representation"] == "legacy"


def test_trajectory_raw_projection_excludes_observation_payload_and_injection_is_idempotent(tmp_path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    episode = _asset(
        store, tmp_path, "experience/trajectories/parser-task.json",
        '{"task_id":"source-parser","user_task":"Fix parser regression","result":{"status":"completed","summary":"Parser fixed"},'
        '"reward_report":{"overall":0.9},"actions":[{"action_type":"read_file","status":"success",'
        '"observation":{"output":"PRIVATE OBSERVATION BODY"}}]}',
    )
    retrieved = store.retrieve("Fix parser regression", limit=8)
    prompt, decisions = RuntimeExperienceContext(config, store).plan(
        "Fix parser regression", "task-test", {"task_type": "bugfix"}, retrieved
    )
    assert {item["path"]: item for item in decisions}[episode]["representation"] == "raw_episode"
    assert "read_file: success" in str(prompt)
    assert "PRIVATE OBSERVATION BODY" not in str(prompt)
    store.record_asset_usage("task-test", retrieved, used_in_prompt=False)
    store.mark_assets_injected("task-test", prompt)
    store.mark_assets_injected("task-test", prompt)
    events = store.activation_events_for_task("task-test")
    assert sum(item["stage"] == "injected" and item["path"] == episode for item in events) == 1


def test_explicit_asset_anti_scope_overrides_retrieval_match(tmp_path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    path = _asset(store, tmp_path, "memory/parser.md", "# Parser memory\nParser regression notes.\n")
    target = tmp_path / path
    sidecar = target.with_name(f"{target.name}.meta.json")
    sidecar.write_text(json.dumps({"applies_to": {
        "task_signals": ["parser regression"],
        "does_not_apply_when": ["parser regression"],
    }}), encoding="utf-8")
    store.index_asset(target)
    retrieved = store.retrieve("Fix parser regression", limit=8)
    selected, decisions = RuntimeExperienceContext(config, store).plan(
        "Fix parser regression", "task-test", {"task_type": "bugfix"}, retrieved
    )
    assert {item["path"]: item for item in decisions}[path]["activation"] == "abstained"
    assert path not in {item["path"] for item in selected}


def test_resume_rejects_changed_task_after_representation_is_frozen(tmp_path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    logger = TrajectoryLogger("Fix parser regression", {})
    logger.data["experience_representation"] = {"decisions": [{"representation": "skill"}]}
    store.write_checkpoint({"task_id": logger.task_id, "trajectory": logger.data,
                            "context": {"task": "Fix parser regression", "retrieved": []}, "messages": []})
    with pytest.raises(ValueError, match="start a new run"):
        AgentRuntime(config).run("Change unrelated documentation", resume=logger.task_id)
