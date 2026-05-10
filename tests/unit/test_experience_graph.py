from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_experience_graph_rebuild_links_specs_runs_proposals_and_assets(tmp_path: Path):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    (tmp_path / "spec.md").write_text(
        "# Parser Spec\n\n"
        "## Acceptance Criteria\n"
        "- Parser strips fenced JSON.\n",
        encoding="utf-8",
    )
    now = utc_now()
    task_id = "task_graph"
    asset_path = ".praxile/memory/project.md"
    store.record_asset_usage(
        task_id,
        [
            {
                "path": asset_path,
                "title": "Project Memory",
                "final_score": 0.83,
                "why_loaded": "matched parser repair experience",
                "referenced": True,
            }
        ],
        used_in_prompt=True,
    )
    store.update_asset_usage_outcome(task_id, "success", referenced_paths=[asset_path])
    store.record_trajectory(
        {
            "task_id": task_id,
            "user_task": "Fix fenced JSON parser",
            "start_time": now,
            "end_time": now,
            "actions": [
                {
                    "step": 1,
                    "action_type": "edit_file",
                    "input": {"path": "parser.py"},
                    "observation": {"output": "Parser strips fenced JSON."},
                    "status": "success",
                    "executor": {"executor_id": "coding_agent", "kind": "agent_runtime", "role": "coding_agent"},
                }
            ],
            "executors": [
                {
                    "executor_id": "coding_agent",
                    "kind": "agent_runtime",
                    "role": "coding_agent",
                    "registered_at": now,
                }
            ],
            "result": {"status": "completed", "summary": "Parser strips fenced JSON."},
            "spec_context": {"enabled": True, "spec_files": ["spec.md"]},
            "spec_compliance": {
                "status": "partial",
                "score": 0.66,
                "spec_files": ["spec.md"],
                "missing": ["Non-goals not checked"],
                "violations": [],
            },
            "reward_report": {"overall": 0.74, "regression_passed": True, "notes": ["Tests passed."]},
        }
    )
    store.write_proposal(
        {
            "proposal_id": "prop_graph",
            "source_task_id": task_id,
            "type": "memory_update",
            "title": "Record parser repair memory",
            "status": "accepted",
            "risk_level": "low",
            "target_files": ["memory/project.md"],
            "confidence": 0.8,
            "conflicts": [
                {
                    "left": "memory/project.md",
                    "right": "memory/failures.md",
                    "reason": "test-only conflicting guidance",
                    "confidence": 0.71,
                }
            ],
            "created_at": now,
            "updated_at": now,
            "changes": [],
        }
    )

    result = store.rebuild_experience_graph()

    assert result["nodes"] >= 4
    assert result["relation_counts"]["derived_from_spec"] >= 1
    assert result["relation_counts"]["violates_spec"] >= 1
    assert result["relation_counts"]["generated_from_run"] >= 1
    assert result["relation_counts"]["approved_by"] >= 1
    assert result["relation_counts"]["helped_run"] >= 1
    assert result["relation_counts"]["contradicts_asset"] >= 1
    assert result["relation_counts"]["participated_in_run"] >= 1

    report = store.graph_explain(asset_path, depth=2)
    assert report["found"] is True
    relations = {edge["relation_type"] for edge in report["edges"]}
    assert {"approved_by", "helped_run"} <= relations
    assert any(node["node_id"] == "proposal:prop_graph" for node in report["nodes"])
