from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from praxile.config import Config
from praxile.evolution import EvolutionEngine
from praxile.gateway import GatewayApp
from praxile.store import ExperienceStore
from praxile.utils import utc_now

pytestmark = [pytest.mark.resource, pytest.mark.gateway_resource, pytest.mark.sqlite_resource]


def test_gateway_chat_first_console_and_api_routes(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    (config.paths.state / "rules" / "safety-policy.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "deny-gateway-tool-check",
                        "tool": "run_command",
                        "message": "blocked by gateway safety policy",
                        "match": {"command_contains": ["--gateway-blocked"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    memory = config.paths.state / "memory" / "console.md"
    memory.write_text("# Console Memory\n\nChat-first console evidence.\n", encoding="utf-8")
    store.index_asset(memory)
    (tmp_path / "spec.md").write_text(
        "\n".join(
            [
                "# Console Spec",
                "## Problem Statement",
                "- Inspect the web console governance state.",
                "## Success Metrics",
                "- Console inspection reaches 1 completed run.",
                "## User Stories",
                "- As a maintainer, I can inspect the console.",
                "## Acceptance Criteria",
                "- Console inspected.",
                "## Non-goals",
                "- Do not deploy.",
                "## Constraints",
                "- Use local project state only.",
            ]
        ),
        encoding="utf-8",
    )
    store.record_trajectory(
        {
            "task_id": "task_console",
            "user_task": "Inspect the console",
            "start_time": utc_now(),
            "end_time": utc_now(),
            "result": {"status": "completed", "summary": "Console inspected."},
            "reward_report": {"overall": 0.8},
            "loaded_assets": [{"path": ".praxile/memory/console.md", "why_loaded": "test"}],
            "silent_failure_signals": [],
            "experience_candidates": [],
            "diff_summary": {"is_repo": True, "stat": "src/app.py | 2 +-", "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@\n-old\n+new\n"},
            "actions": [
                {"step": 1, "action_type": "read_file", "status": "success", "observation": {"output": "ok"}},
                {
                    "step": 2,
                    "action_type": "run_test",
                    "status": "success",
                    "input": {"command": "python -m pytest"},
                    "observation": {"status": "success", "output": "2 passed", "data": {"command": "python -m pytest", "returncode": 0}},
                },
            ],
        }
    )
    ci_dir = config.paths.state / "experience" / "ci"
    ci_dir.mkdir(parents=True, exist_ok=True)
    (ci_dir / "pr-report.json").write_text(
        json.dumps({"kind": "github_pr", "status": "passed", "summary": "Praxile PR report", "run_id": "task_console", "created_at": utc_now()}),
        encoding="utf-8",
    )
    config.data["github"]["repository"] = "Praxile-Alpha/Praxile"
    config.write()

    app = GatewayApp(tmp_path)
    page = app.dispatch("GET", "/")
    assert "Chat Workspace" in page.html
    assert "/api/chat/sessions" in page.html
    assert "Reflect Dashboard" in page.html
    assert "Graph Explorer" in page.html
    assert "Audit Dashboard" in page.html
    assert "Spec Verify" in page.html
    assert "Tool / Safety Policy" in page.html
    assert "Retry last" in page.html
    assert "Edit Role Route" in page.html
    assert "Channel Bindings" in page.html
    assert "CI / PR Reports" in page.html
    assert "Multi-repo Dashboard" in page.html
    assert "Structured Proposal Editor" in page.html

    status = app.dispatch("GET", "/api/status")
    assert status["counts"]["runs"] == 1
    assert status["latest_run"]["task_id"] == "task_console"

    session = app.dispatch("POST", "/api/chat/sessions", payload={"title": "Console test"})
    assert session["session_id"].startswith("sess_")
    sessions = app.dispatch("GET", "/api/chat/sessions")
    assert sessions[0]["session_id"] == session["session_id"]
    stopped = app.dispatch("POST", f"/api/chat/sessions/{session['session_id']}/stop", payload={"reason": "test stop"})
    assert stopped["stopped"] is False
    assert stopped["session"]["stop_requests"][0]["status"] == "recorded"

    run = app.dispatch("GET", "/api/runs/task_console")
    assert run["governance_summary"]["loaded_assets"] == 1
    assert run["actions"][0]["type"] == "read_file"
    assert run["artifacts"]["tests"][0]["command"] == "python -m pytest"
    artifacts = app.dispatch("GET", "/api/runs/task_console/artifacts")
    assert artifacts["diffs"][0]["type"] == "git_diff"
    evidence = app.dispatch("GET", "/api/runs/task_console/evidence")
    assert evidence["task_id"] == "task_console"
    job_session = app.dispatch("POST", "/api/chat/sessions", payload={"title": "Async test"})
    job_started = app.dispatch(
        "POST",
        f"/api/chat/sessions/{job_session['session_id']}/message-async",
        payload={"task": "Inspect async gateway cancellation", "dry_run": True, "max_steps": 0},
    )
    job_id = job_started["job"]["job_id"]
    assert job_started["session"]["active_job_id"] == job_id
    terminal = None
    for _ in range(60):
        terminal = app.dispatch("GET", f"/api/runs/jobs/{job_id}", query={"after": ["0"]})
        if terminal["status"] in {"completed", "failed", "cancelled", "needs_human"}:
            break
        time.sleep(0.05)
    assert terminal is not None
    assert terminal["events"]
    runtime_stages = {event["stage"] for event in terminal["events"] if event["type"] == "runtime_stage"}
    assert {"analyze", "retrieve", "route", "evolve"} <= runtime_stages
    job_events = app.dispatch("GET", f"/api/runs/jobs/{job_id}/events", query={"format": ["json"], "after": ["0"]})
    assert job_events["job_id"] == job_id
    cancel_started = app.dispatch(
        "POST",
        "/api/runs/jobs",
        payload={"task": "Inspect cancellable gateway job", "dry_run": True, "max_steps": 0},
    )
    cancelled = app.dispatch("POST", f"/api/runs/jobs/{cancel_started['job_id']}/cancel", payload={"reason": "test cancellation"})
    assert cancelled["event"]["type"] == "stop_requested"

    roles = app.dispatch("GET", "/api/models/roles")
    assert {row["role"] for row in roles} >= {"coding_agent", "embedding"}
    providers = app.dispatch("GET", "/api/models/providers")
    assert providers[0]["api_key_status"] != "configured"
    with pytest.raises(Exception):
        app.dispatch(
            "POST",
            "/api/models/providers",
            payload={"confirm": True, "provider_id": "badsecret", "api_key": "do-not-store"},
        )
    provider = app.dispatch(
        "POST",
        "/api/models/providers",
        payload={
            "confirm": True,
            "provider_id": "openai",
            "type": "openai_compatible",
            "base_url": "https://api.example.test/v1",
            "api_key_env": "OPENAI_API_KEY",
            "models": "gpt-test",
            "timeout_seconds": 3,
        },
    )
    assert provider["provider_id"] == "openai"
    role = app.dispatch(
        "PATCH",
        "/api/models/roles/coding_agent",
        payload={"confirm": True, "provider": "openai", "model": "gpt-test", "mode": "required", "fallback": "local:local_hash"},
    )
    assert role["provider"] == "openai"
    role_test = app.dispatch("POST", "/api/models/test", payload={"role": "embedding"})
    assert role_test["routes"][0]["status"] == "ok"

    channels = app.dispatch("GET", "/api/channels")
    assert "telegram" in channels["supported_platforms"]
    binding = app.dispatch(
        "POST",
        "/api/channels/bind",
        payload={"confirm": True, "platform": "telegram", "channel_id": "12345", "name": "alerts"},
    )
    assert binding["id"] == "telegram:12345"
    channels = app.dispatch("GET", "/api/channels")
    assert channels["bindings"][0]["token_env_status"] == "missing"
    unbound = app.dispatch("POST", "/api/channels/telegram:12345/unbind", payload={"confirm": True})
    assert unbound["id"] == "telegram:12345"

    tools = app.dispatch("GET", "/api/tools")
    assert any(item["name"] == "run_command" for item in tools["tools"])
    assert tools["batch_read_only_only"] is True
    policy = app.dispatch("GET", "/api/safety/policy")
    assert ".praxile" in policy["protected_paths"]
    assert policy["policy_rules_count"] == 1
    command_check = app.dispatch("POST", "/api/safety/check-command", payload={"command": "rm -rf /"})
    assert command_check["allowed"] is False
    path_check = app.dispatch("POST", "/api/safety/check-path", payload={"path": ".praxile/config.json", "write": True})
    assert path_check["allowed"] is False
    tool_check = app.dispatch(
        "POST",
        "/api/safety/check-tool",
        payload={"tool": "run_command", "args": {"command": "python -m pytest --gateway-blocked"}},
    )
    assert tool_check["allowed"] is False
    assert tool_check["reason"] == "blocked by gateway safety policy"
    direct_tool_check = app.check_tool_call("run_command", {"command": "python -m pytest --gateway-blocked"})
    assert direct_tool_check["allowed"] is False

    assets = app.dispatch("GET", "/api/assets")
    assert any(item["path"] == ".praxile/memory/console.md" for item in assets)
    asset = app.dispatch("GET", "/api/assets/.praxile/memory/console.md")
    assert "Chat-first console evidence" in asset["content"]
    usage = app.dispatch("GET", "/api/assets/.praxile/memory/console.md/usage")
    assert usage["path"] == ".praxile/memory/console.md"
    asset_graph = app.dispatch("GET", "/api/assets/.praxile/memory/console.md/graph")
    assert asset_graph["ref"] == ".praxile/memory/console.md"
    archived = app.dispatch(
        "POST",
        "/api/assets/.praxile/memory/console.md/archive",
        payload={"confirm": True, "reason": "web console test"},
    )
    assert archived["status"] == "archived"
    reactivated = app.dispatch(
        "POST",
        "/api/assets/.praxile/memory/console.md/reactivate",
        payload={"confirm": True, "reason": "web console test"},
    )
    assert reactivated["status"] == "active"

    reflect = app.dispatch(
        "POST",
        "/api/reflect/run",
        payload={"modes": ["stale"], "stale_days": -1, "write_proposals": True},
    )
    assert reflect["no_assets_modified"] is True
    reports = app.dispatch("GET", "/api/reflect/reports")
    assert reports
    loaded_report = app.dispatch("GET", f"/api/reflect/reports/{reports[0]['reflect_id']}")
    assert loaded_report["reflect_id"] == reports[0]["reflect_id"]

    graph_status = app.dispatch("GET", "/api/graph/status")
    assert "nodes" in graph_status
    graph_rebuild = app.dispatch("POST", "/api/graph/rebuild")
    assert "nodes" in graph_rebuild
    graph_explain = app.dispatch("GET", "/api/graph/explain", query={"ref": [".praxile/memory/console.md"]})
    assert graph_explain["ref"] == ".praxile/memory/console.md"
    graph_view = app.dispatch("GET", "/api/graph/view", query={"ref": [".praxile/memory/console.md"]})
    assert "view" in graph_view

    audit_check = app.dispatch("POST", "/api/audit/check", payload={"strict": False})
    assert audit_check["audit_type"] == "check"
    audit_bundle = app.dispatch("POST", "/api/audit/bundle", payload={"include_reflect": True})
    assert audit_bundle["audit_type"] == "bundle"
    ci_reports = app.dispatch("GET", "/api/ci/reports")
    assert ci_reports[0]["summary"] == "Praxile PR report"
    ci_report = app.dispatch("GET", f"/api/ci/reports/{ci_reports[0]['report_id']}")
    assert ci_report["status"] == "passed"
    generated_ci = app.dispatch("POST", "/api/ci/reports", payload={"confirm": True, "run_id": "task_console"})
    assert generated_ci["run_id"] == "task_console"
    assert generated_ci["comment_publishing"]["supported"] is True
    github_context = app.dispatch("GET", "/api/github/context")
    assert github_context["repository"] == "Praxile-Alpha/Praxile"
    preview_comment = app.dispatch(
        "POST",
        "/api/github/pr-comments",
        payload={"confirm": True, "preview_only": True, "report_id": generated_ci["report_id"], "pr_number": 3},
    )
    assert preview_comment["status"] == "preview"
    assert "Praxile Report" in preview_comment["body"]
    repos = app.dispatch("GET", "/api/repos")
    assert repos["repos"][0]["current"] is True

    specs = app.dispatch("GET", "/api/specs")
    assert "spec.md" in specs["spec_files"]
    spec_check = app.dispatch("POST", "/api/spec/check", payload={"spec": "spec.md"})
    assert spec_check["spec_files"] == ["spec.md"]
    spec_verify = app.dispatch("POST", "/api/spec/verify", payload={"run_id": "task_console", "specs": ["spec.md"]})
    assert spec_verify["task_id"] == "task_console"


def test_gateway_proposal_accept_and_reject_require_confirmation(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    engine = EvolutionEngine(config)
    app = GatewayApp(tmp_path)

    accept_proposal = engine._proposal(
        source_task_id="task_console",
        proposal_type="memory_update",
        title="Record console memory",
        reason="Gateway proposal test.",
        risk_level="low",
        evidence=["Console test evidence."],
        confidence=0.8,
        changes=[{"path": "memory/project.md", "operation": "append", "content": "Console accepted memory."}],
    )
    store.write_proposal(accept_proposal)
    with pytest.raises(Exception):
        app.dispatch("POST", f"/api/proposals/{accept_proposal['proposal_id']}/accept", payload={})
    with pytest.raises(Exception):
        app.dispatch("POST", f"/api/proposals/{accept_proposal['proposal_id']}/edit", payload={"title": "Edit without confirmation"})
    edited = app.dispatch(
        "POST",
        f"/api/proposals/{accept_proposal['proposal_id']}/edit",
        payload={
            "confirm": True,
            "proposal": {**accept_proposal, "title": "Edited console memory"},
            "reason": "tighten title",
        },
    )
    assert edited["title"] == "Edited console memory"
    assert edited["status"] == "pending"
    assert edited["user_edits"][0]["edited_by"] == "web_console"
    accepted = app.dispatch(
        "POST",
        f"/api/proposals/{accept_proposal['proposal_id']}/accept",
        payload={"confirm": True},
    )
    assert accepted["status"] == "accepted"

    reject_proposal = engine._proposal(
        source_task_id="task_console",
        proposal_type="memory_update",
        title="Reject console memory",
        reason="Gateway rejection test.",
        risk_level="low",
        evidence=["Console test evidence."],
        confidence=0.8,
        changes=[{"path": "memory/project.md", "operation": "append", "content": "Rejected memory."}],
    )
    store.write_proposal(reject_proposal)
    rejected = app.dispatch(
        "POST",
        f"/api/proposals/{reject_proposal['proposal_id']}/reject",
        payload={"reason": "weak evidence"},
    )
    assert rejected["status"] == "rejected"
