from __future__ import annotations

from pathlib import Path

from praxile.cli import update_run_reward_after_feedback
from praxile.config import Config
from praxile.feedback import build_feedback, feedback_reward
from praxile.feedback import extract_feedback_intents
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_feedback_reward_aggregates_positive_and_negative():
    positive = build_feedback(target_type="run", target_id="task_1", raw_text="干得好，这次很准确")
    negative = build_feedback(target_type="run", target_id="task_1", raw_text="这个方向错了")

    reward = feedback_reward([positive, negative])

    assert reward["active"] is True
    assert reward["positive_count"] == 1
    assert reward["negative_count"] == 1
    assert 0.0 <= reward["score"] <= 1.0


def test_feedback_updates_latest_run_reward(tmp_path: Path):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    trajectory = {
        "task_id": "task_feedback",
        "user_task": "Fix bug",
        "start_time": utc_now(),
        "end_time": utc_now(),
        "environment_snapshot": {},
        "actions": [],
        "cost": {"tool_calls": 0, "model_calls": 0},
        "result": {"status": "completed", "summary": "done"},
        "reward_report": {},
    }
    store.record_trajectory(trajectory)
    store.record_feedback(
        build_feedback(
            target_type="run",
            target_id="task_feedback",
            raw_text="干得好",
            sentiment="positive",
        )
    )

    update_run_reward_after_feedback(config, store, "task_feedback")
    updated = store.get_trajectory("task_feedback")

    assert updated is not None
    assert updated["reward_report"]["user_feedback_reward"]["positive_count"] == 1
    assert updated["reward_report"]["final_reward"]["effective_weights"]["user_feedback"] > 0


def test_negative_proposal_feedback_lowers_recommendation(tmp_path: Path):
    from praxile.cli import proposal_review_guidance

    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    proposal = {
        "proposal_id": "prop_feedback",
        "source_task_id": "task_feedback",
        "type": "memory_update",
        "title": "Remember too broad thing",
        "status": "pending",
        "risk_level": "low",
        "target_files": ["memory/project.md"],
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "changes": [{"path": "memory/project.md", "operation": "append", "content": "too broad"}],
        "confidence": 0.8,
    }
    store.write_proposal(proposal)
    store.record_feedback(
        build_feedback(
            target_type="proposal",
            target_id="prop_feedback",
            raw_text="这个 proposal 太泛了，不要记",
            sentiment="negative",
        )
    )
    updated = store.find_proposal("prop_feedback")

    assert updated is not None
    assert updated["confidence"] < 0.8
    assert proposal_review_guidance(store, updated)["action"] == "reject_or_edit"


def test_feedback_extractor_splits_multi_intent_feedback():
    intents = extract_feedback_intents("这次整体不错；第二条经验不要记；这条规则误导了你")

    assert len(intents) == 3
    assert intents[0]["target_type"] == "run"
    assert intents[0]["sentiment"] == "positive"
    assert intents[1]["target_type"] == "proposal"
    assert intents[1]["target_hint"] == "nth:2"
    assert intents[1]["sentiment"] == "negative"
    assert intents[1]["effect"]["suppress_asset_write"] is True
    assert intents[2]["target_type"] == "asset"
    assert intents[2]["requires_confirmation"] is True
