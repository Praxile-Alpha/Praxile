from __future__ import annotations

import json
from pathlib import Path

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_audit_run_asset_and_proposal_json(tmp_path: Path, capsys):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    config.write()

    trajectory = {
        "task_id": "task_audit",
        "user_task": "Record parser audit chain",
        "start_time": utc_now(),
        "end_time": utc_now(),
        "result": {"status": "completed", "summary": "Recorded audit evidence."},
        "task_analysis": {"task_type": "documentation", "risk_level": "low"},
        "plan": ["Inspect memory", "Create proposal"],
        "actions": [
            {
                "step": 1,
                "action_type": "read_file",
                "status": "success",
                "input": {"path": "parser.py"},
                "executor": {"executor_id": "coding_agent", "kind": "agent_runtime"},
                "observation": {"output": "parser contents OPENAI_API_KEY=REDACTION_TEST_VALUE", "risk_level": "low"},
            }
        ],
        "loaded_assets": [
            {
                "path": ".praxile/memory/project.md",
                "type": "memory",
                "final_score": 1.0,
                "matched_terms": ["parser"],
                "matched_fields": ["content"],
                "why_loaded": "matched parser memory",
            }
        ],
        "reward_report": {
            "overall": 0.8,
            "objective_signals": {
                "executor_attribution": {
                    "quality": "complete",
                    "action_executor_counts": {"coding_agent": 1},
                }
            },
            "experience_generation": {"should_generate_experience": True, "evidence_strength": "medium"},
        },
        "experience_candidates": [
            {
                "proposal_id": "prop_audit",
                "type": "memory_update",
                "title": "Record parser memory",
                "target_files": ["memory/project.md"],
            }
        ],
    }
    store.record_trajectory(trajectory)
    store.record_asset_usage("task_audit", trajectory["loaded_assets"], used_in_prompt=True)
    store.write_proposal(
        {
            "proposal_id": "prop_audit",
            "source_task_id": "task_audit",
            "type": "memory_update",
            "title": "Record parser memory",
            "reason": "Audit test proposal.",
            "status": "pending",
            "risk_level": "low",
            "confidence": 0.7,
            "confidence_level": "medium",
            "target_files": ["memory/project.md"],
            "evidence_summary": "Trajectory recorded parser evidence.",
            "evidence": ["Source run task_audit."],
            "changes": [{"path": "memory/project.md", "operation": "append", "content": "Parser memory."}],
        }
    )
    store.index_asset(config.paths.state / "memory" / "project.md")

    assert main(["--project", str(tmp_path), "audit", "run", "task_audit", "--json"]) == 0
    run_stdout = capsys.readouterr().out
    assert "sk-testsecret" not in run_stdout
    run_report = json.loads(run_stdout)
    assert run_report["audit_type"] == "run"
    assert run_report["redaction"]["profile"] == "standard"
    assert run_report["redaction"]["redacted_value_count"] >= 1
    assert run_report["subject"]["task_id"] == "task_audit"
    assert run_report["decision_chain"]["executor_attribution"]["quality"] == "complete"
    assert run_report["proposal_chain"]["proposal_count"] == 1
    assert "[REDACTED]" in run_report["decision_chain"]["actions"][0]["observation_excerpt"]

    assert main(["--project", str(tmp_path), "audit", "run", "task_audit", "--json", "--redaction", "none"]) == 0
    raw_run_stdout = capsys.readouterr().out
    assert "REDACTION_TEST_VALUE" in raw_run_stdout
    raw_run_report = json.loads(raw_run_stdout)
    assert raw_run_report["redaction"]["profile"] == "none"
    assert raw_run_report["redaction"]["applied"] is False

    assert main(["--project", str(tmp_path), "audit", "proposal", "prop_audit", "--json"]) == 0
    proposal_report = json.loads(capsys.readouterr().out)
    assert proposal_report["audit_type"] == "proposal"
    assert proposal_report["subject"]["proposal_id"] == "prop_audit"
    assert proposal_report["source_chain"]["source_task_id"] == "task_audit"

    assert main(["--project", str(tmp_path), "audit", "proposal", "prop_audit", "--json", "--redaction", "strict"]) == 0
    strict_proposal_report = json.loads(capsys.readouterr().out)
    assert strict_proposal_report["redaction"]["profile"] == "strict"
    assert strict_proposal_report["asset_chain"]["changes"][0]["content_excerpt"] == "[redacted:strict:content_excerpt]"

    assert main(["--project", str(tmp_path), "audit", "asset", "memory/project.md", "--json"]) == 0
    asset_report = json.loads(capsys.readouterr().out)
    assert asset_report["audit_type"] == "asset"
    assert asset_report["subject"]["path"] == ".praxile/memory/project.md"
    assert asset_report["proposal_chain"]["related_proposal_count"] == 1

    assert main(["--project", str(tmp_path), "audit", "bundle", "--json", "--limit-runs", "5"]) == 0
    bundle_report = json.loads(capsys.readouterr().out)
    assert bundle_report["audit_type"] == "bundle"
    assert bundle_report["bundle"]["format"] == "praxile_project_audit_bundle_v1"
    assert bundle_report["bundle"]["raw_secret_values"] is False
    assert bundle_report["bundle"]["redaction_profile"] == "standard"
    assert bundle_report["run_chain"]["count"] == 1
    assert bundle_report["proposal_chain"]["pending_count"] == 1
    assert bundle_report["asset_chain"]["count"] >= 1

    output_path = tmp_path / "audit-bundle.json"
    assert main(["--project", str(tmp_path), "audit", "bundle", "--json", "--output", str(output_path)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["audit_type"] == "bundle"
    assert output_path.exists()

    assert main(["--project", str(tmp_path), "audit", "check", "--json"]) == 0
    check_report = json.loads(capsys.readouterr().out)
    assert check_report["audit_type"] == "check"
    assert check_report["check"]["passed"] is True
    assert any(item["code"] == "pending_proposals" for item in check_report["check"]["warnings"])

    assert main(["--project", str(tmp_path), "audit", "check", "--json", "--max-pending", "0"]) == 1
    strict_pending_report = json.loads(capsys.readouterr().out)
    assert strict_pending_report["check"]["passed"] is False
    assert strict_pending_report["check"]["failures"][0]["code"] == "pending_proposals_over_limit"
