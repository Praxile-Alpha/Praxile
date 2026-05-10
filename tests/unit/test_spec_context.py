from __future__ import annotations

from pathlib import Path

from praxile.specs import build_spec_context, check_spec_file, verify_spec_compliance


def test_spec_check_extracts_acceptance_and_constraints(tmp_path: Path):
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Feature\n\n"
        "## Problem Statement\nNeed faster search.\n\n"
        "## Success Metrics\n- API P95 < 200ms\n\n"
        "## User Stories\n- As a user I can search documents.\n\n"
        "## Acceptance Criteria\n- [ ] Search returns top 5 documents.\n\n"
        "## Non-Goals\n- Do not implement semantic search.\n\n"
        "## Constraints\n- Use existing PostgreSQL.\n",
        encoding="utf-8",
    )
    report = check_spec_file(tmp_path, "spec.md")
    assert report["quality_label"] == "high"
    assert report["missing_sections"] == []
    assert "Search returns top 5 documents." in report["acceptance_criteria"]
    assert "Use existing PostgreSQL." in report["constraints"]


def test_spec_context_rejects_path_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-spec.md"
    outside.write_text("# Spec\n", encoding="utf-8")
    try:
        build_spec_context(tmp_path, ["../outside-spec.md"])
    except ValueError as exc:
        assert "escapes project root" in str(exc)
    else:
        raise AssertionError("expected path escape rejection")


def test_spec_verify_detects_missing_metric_and_constraint_violation(tmp_path: Path):
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Feature\n\n"
        "## Problem Statement\nNeed faster search.\n\n"
        "## Success Metrics\n- API P95 < 200ms\n\n"
        "## User Stories\n- As a user I can search documents.\n\n"
        "## Acceptance Criteria\n- Search endpoint returns top 5 documents.\n\n"
        "## Non-Goals\n- Do not implement semantic search.\n\n"
        "## Constraints\n- Use existing PostgreSQL only.\n",
        encoding="utf-8",
    )
    trajectory = {
        "task_id": "task_spec",
        "user_task": "Implement search endpoint",
        "result": {"summary": "Search endpoint returns top 5 documents."},
        "diff_summary": {"diff": "+ import redis\n+ def semantic_search(): pass\n"},
        "actions": [
            {
                "action_type": "edit_file",
                "input": {"path": "search.py"},
                "observation": {"output": "Added Redis-backed semantic search."},
                "status": "success",
            }
        ],
        "reward_report": {"regression_passed": True, "notes": ["Configured tests/lint/build passed."]},
    }

    report = verify_spec_compliance(tmp_path, trajectory, explicit_specs=["spec.md"])

    assert report["status"] == "partial"
    assert report["satisfied"]
    assert any(item["type"] == "constraint" for item in report["violations"])
    assert any(item["type"] == "non_goal" for item in report["violations"])
    assert report["success_metric_coverage"][0]["covered"] is False
    assert report["reverse_spec_update_needed"] is True
