from __future__ import annotations

import json
from pathlib import Path

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_reflect_summary_and_write_proposals(tmp_path: Path, capsys, monkeypatch):
    config = Config.load(tmp_path)
    config.write()
    store = ExperienceStore(config.paths)
    store.initialize(config)

    first = config.paths.state / "memory" / "parser-fixture-a.md"
    second = config.paths.state / "memory" / "parser-fixture-b.md"
    first.write_text("# Parser Fixture Repair\n\nUse the parser fixture repair pattern.\n", encoding="utf-8")
    second.write_text("# Parser Fixture Repair\n\nDuplicate parser fixture guidance.\n", encoding="utf-8")
    store.index_asset(first)
    store.index_asset(second)

    for index in range(2):
        task_id = f"task_reflect_{index}"
        store.record_trajectory(
            {
                "task_id": task_id,
                "user_task": "Reflect silent failure signal",
                "start_time": utc_now(),
                "end_time": utc_now(),
                "result": {"status": "completed", "summary": "completed"},
                "reward_report": {"overall": 0.7, "experience_generation": {"evidence_strength": "medium"}},
                "silent_failure_signals": [
                    {
                        "type": "broad_diff_without_spec",
                        "risk": "medium",
                        "reason": "Broad edit without attached spec.",
                    }
                ],
                "actions": [],
            }
        )

    args = [
        "--project",
        str(tmp_path),
        "reflect",
        "--duplicates",
        "--stale",
        "--stale-days",
        "-1",
        "--silent-failures",
        "--report",
        "json",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    finding_types = {item["type"] for item in report["findings"]}
    assert "duplicate_asset" in finding_types
    assert "stale_asset" in finding_types
    assert "silent_failure_pattern" in finding_types
    assert report["no_assets_modified"] is True
    assert report["written_proposal_paths"] == []
    assert store.list_proposals(status="pending") == []

    assert main(["--project", str(tmp_path), "reflect", "--asset", ".praxile/memory/parser-fixture-a.md", "--report", "json"]) == 0
    asset_report = json.loads(capsys.readouterr().out)
    assert asset_report["asset_summary"]["found"] is True
    assert asset_report["asset_summary"]["path"] == ".praxile/memory/parser-fixture-a.md"
    assert asset_report["asset_summary"]["usage_count"] == 0

    output_path = tmp_path / "reflect.md"
    assert main(args[:-2] + ["--write-proposals", "--report", "markdown", "--output", str(output_path)]) == 0
    stdout = capsys.readouterr().out
    assert "Wrote reflect report:" in stdout
    assert output_path.exists()
    pending = store.list_proposals(status="pending", limit=20)
    proposal_types = {item["type"] for item in pending}
    assert "asset_merge" in proposal_types
    assert "asset_deprecate" in proposal_types
    assert "harness_rule_create" in proposal_types
    assert all(item["generated_by"] == "reflect" for item in pending)

    assert main(["--project", str(tmp_path), "audit", "bundle", "--include-reflect", "--json"]) == 0
    audit_bundle = json.loads(capsys.readouterr().out)
    reflect_chain = audit_bundle["reflect_chain"]
    assert reflect_chain["included"] is True
    assert reflect_chain["count"] >= 1
    assert reflect_chain["reports"][0]["no_assets_modified"] is True

    ci_dir = tmp_path / ".praxile" / "experience" / "reflect" / "ci-test"
    step_summary = tmp_path / "github-step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    assert (
        main(
            [
                "--project",
                str(tmp_path),
                "reflect",
                "--ci",
                "--report",
                "json",
                "--ci-output-dir",
                str(ci_dir),
                "--max-findings",
                "99",
                "--github-step-summary",
            ]
        )
        == 0
    )
    ci_report = json.loads(capsys.readouterr().out)
    assert ci_report["ci"]["passed"] is True
    assert ci_report["ci_artifacts"]["latest_json"].endswith("latest.json")
    assert (ci_dir / "latest.json").exists()
    assert (ci_dir / "latest.md").exists()
    assert "Praxile Reflect CI" in step_summary.read_text(encoding="utf-8")

    assert (
        main(
            [
                "--project",
                str(tmp_path),
                "reflect",
                "--ci",
                "--report",
                "json",
                "--ci-output-dir",
                str(ci_dir),
                "--max-findings",
                "0",
                "--no-github-step-summary",
            ]
        )
        == 1
    )
    failing_report = json.loads(capsys.readouterr().out)
    assert failing_report["ci"]["passed"] is False
    assert failing_report["ci"]["failures"][0]["code"] == "reflect_findings_over_limit"
