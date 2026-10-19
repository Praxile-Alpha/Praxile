from __future__ import annotations

import pytest

from praxile.trace import events_to_v1_trajectory, v1_trajectory_to_events


def _trajectory() -> dict:
    return {
        "schema": "praxile_trajectory_v1",
        "task_id": "task_compat_1",
        "user_task": "Fix the retry test",
        "start_time": "2026-09-11T08:00:00+00:00",
        "end_time": "2026-09-11T08:00:05+00:00",
        "environment_snapshot": {"branch": "main"},
        "task_analysis": {"high_risk": False},
        "plan": ["Read the test", "Apply the fix"],
        "loaded_assets": [{"path": ".praxile/skills/retry/SKILL.md", "score": 0.8}],
        "model_routing": {
            "calls": [
                {
                    "created_at": "2026-09-11T08:00:01+00:00",
                    "provider": "local",
                    "model": "fixture",
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5},
                }
            ]
        },
        "actions": [
            {
                "step": 1,
                "action_type": "run_command",
                "input": {"command": "python -m pytest"},
                "observation": {"status": "success", "output": "1 passed"},
                "status": "success",
                "created_at": "2026-09-11T08:00:03+00:00",
            }
        ],
        "artifacts": [{"artifact_id": "artifact_patch_1", "type": "patch", "path": "fix.patch"}],
        "reward_report": {
            "overall": 0.9,
            "test_results": [{"status": "success", "command": "python -m pytest"}],
        },
        "result": {"status": "completed", "summary": "Retry fixed."},
        "experience_candidates": [],
    }


def test_v1_projection_is_deterministic_and_covers_required_event_types() -> None:
    first = v1_trajectory_to_events(_trajectory())
    second = v1_trajectory_to_events(_trajectory())

    assert [item.event_id for item in first] == [item.event_id for item in second]
    assert {item.type for item in first} >= {
        "RUN_START",
        "CONTEXT_INJECT",
        "MODEL_CALL",
        "TOOL_CALL",
        "TOOL_RESULT",
        "ARTIFACT_CHANGE",
        "VERIFICATION",
        "FINAL_RESULT",
        "RUN_END",
    }
    assert all(item.extensions["compatibility_source"] == "praxile_trajectory_v1" for item in first)


def test_subtask_projection_preserves_old_ids_and_roundtrips_action_references():
    trajectory = _trajectory()
    trajectory["actions"][0]["action_id"] = "action-original"
    before = v1_trajectory_to_events(trajectory)
    episode = {"schema_version": "praxile.subtask_episode.v1", "episode_id": "episode-one",
        "task_id": trajectory["task_id"], "goal": "verify", "status": "closed",
        "state_before": {"branch": "main"}, "state_after": {"branch": "main"},
        "claimed_outcome": "completed", "action_refs": ["action-original", "missing-action"]}
    trajectory["subtask_episodes"] = [episode]
    events = v1_trajectory_to_events(trajectory)
    assert [item.to_dict() for item in events[:-1]] == [item.to_dict() for item in before]
    projected = events[-1]
    assert projected.type == "SUBTASK_EPISODE"
    assert projected.payload["unresolved_action_refs"] == ["missing-action"]
    assert projected.payload["retrospective"] is True
    assert projected.evidence_refs == (next(item.event_id for item in events if item.type == "TOOL_CALL"),)
    restored = events_to_v1_trajectory(events)
    assert restored["subtask_episodes"] == [episode]
    assert restored["actions"][0]["action_id"] == "action-original"
    assert [item.event_id for item in v1_trajectory_to_events(trajectory)] == [item.event_id for item in events]


def test_subtask_cross_task_reference_rejected():
    trajectory = _trajectory()
    trajectory["subtask_episodes"] = [{"task_id": "unrelated", "action_refs": []}]
    with pytest.raises(ValueError, match="belong"):
        v1_trajectory_to_events(trajectory)


def test_normalized_events_project_back_to_v1_read_model() -> None:
    events = v1_trajectory_to_events(_trajectory())

    projected = events_to_v1_trajectory(reversed(events))

    assert projected["task_id"] == "task_compat_1"
    assert projected["user_task"] == "Fix the retry test"
    assert projected["actions"][0]["action_type"] == "run_command"
    assert projected["actions"][0]["observation"]["output"] == "1 passed"
    assert projected["result"] == {"status": "completed", "summary": "Retry fixed."}
    assert projected["v2_projection"]["source_of_truth"] == "normalized_event_stream"


def test_v1_projection_rejects_mixed_traces() -> None:
    events = v1_trajectory_to_events(_trajectory())
    mixed = [events[0], events[1].__class__.from_dict({**events[1].to_dict(), "trace_id": "trace_other"})]

    with pytest.raises(ValueError, match="exactly one trace"):
        events_to_v1_trajectory(mixed)
