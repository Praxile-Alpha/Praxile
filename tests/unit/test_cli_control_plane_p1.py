from __future__ import annotations

import argparse
import json
from pathlib import Path

from praxile.cli_commands.evals import (
    cmd_harness_candidate_evaluate,
    cmd_harness_candidate_list,
    cmd_harness_candidate_promote,
    cmd_harness_candidate_register,
    cmd_harness_candidate_rollback,
    cmd_harness_skill_evaluate,
)
from praxile.cli_parser import build_parser
from praxile.control_plane import (
    AssetMeta,
    EvidenceRef,
    HarnessCandidate,
    HarnessEvolutionRegistry,
    SkillAsset,
)
from praxile.utils import write_json


def _candidate() -> HarnessCandidate:
    return HarnessCandidate(
        candidate_id="cli_candidate",
        type="context_policy",
        component_key="context.default",
        base_version="1",
        candidate_version="2",
        hypothesis="Reduce irrelevant context.",
        source_evidence=(EvidenceRef("event", "event_1"),),
        payload={"policy_id": "focused"},
    )


def test_candidate_cli_register_evaluate_promote_list_and_rollback(tmp_path: Path, capsys) -> None:
    candidate_path = tmp_path / "candidate.json"
    report_path = tmp_path / "ab.json"
    write_json(candidate_path, _candidate().to_dict())
    write_json(
        report_path,
        {
            "baseline": {"eval_run_id": "baseline_1"},
            "candidate": {"eval_run_id": "candidate_1"},
            "invariant_check": {"valid": True},
                "comparison": {
                    "decision": "improve",
                    "totals": {"regressions": 0, "cost_delta": 0.0},
                    "task_results": [
                        {"diff_scope": {"candidate_status": "passed", "passed": True}}
                    ],
                },
        },
    )
    assert cmd_harness_candidate_register(argparse.Namespace(candidate=str(candidate_path), json=False), tmp_path) == 0
    assert cmd_harness_candidate_evaluate(
        argparse.Namespace(
            candidate_id="cli_candidate",
            ab_report=str(report_path),
            reviewer="maintainer",
            approve_human=True,
            max_regressions=0,
            max_cost_increase=0.0,
            allow_quality_tie=False,
            json=False,
        ),
        tmp_path,
    ) == 0
    assert cmd_harness_candidate_promote(argparse.Namespace(candidate_id="cli_candidate", approved_by="maintainer"), tmp_path) == 0
    assert cmd_harness_candidate_list(argparse.Namespace(json=True), tmp_path) == 0
    listed = json.loads(capsys.readouterr().out.split("Promoted cli_candidate\n")[-1])
    key = "context.default::default::default"
    assert listed["active"][key]["version"] == "2"
    assert cmd_harness_candidate_rollback(
        argparse.Namespace(
            component_key="context.default",
            executor_profile="default",
            task_family="default",
            approved_by="maintainer",
        ),
        tmp_path,
    ) == 0
    assert HarnessEvolutionRegistry(tmp_path).snapshot()["active"][key]["version"] == "1"


def test_skill_evaluate_cli_writes_markdown_projection(tmp_path: Path) -> None:
    skill = SkillAsset(
        meta=AssetMeta(
            asset_id="skill_cli",
            type="skill",
            source_evidence=(EvidenceRef("event", "event_1"),),
            scope={"repository": "example/project"},
            applicable_conditions=("Parser failure",),
            confidence=0.8,
            version="1",
            created_from_run="run_1",
        ),
        name="Parser repair",
        input_schema={"type": "object"},
        preconditions=("Reproduced",),
        context_requirements=("Parser source",),
        allowed_tools=("read_file", "run_test"),
        procedure=("Repair",),
        verification_contract=("Tests pass",),
        failure_modes=("Wrong layer",),
        eval_cases=("case_1",),
    )
    skill_path = tmp_path / "skill.json"
    result_path = tmp_path / "results.json"
    markdown_path = tmp_path / "SKILL.md"
    write_json(skill_path, skill.to_dict())
    write_json(result_path, {"case_1": {"eval_case_id": "case_1", "passed": True, "evidence": [{"type": "eval", "ref_id": "eval_1"}]}})
    result = cmd_harness_skill_evaluate(
        argparse.Namespace(skill=str(skill_path), results=str(result_path), markdown_output=str(markdown_path), json=False),
        tmp_path,
    )
    assert result == 0
    assert "## Verification Contract" in markdown_path.read_text(encoding="utf-8")


def test_parser_exposes_p1_commands() -> None:
    parser = build_parser()
    assert parser.parse_args(["harness", "candidate-list"]).harness_command == "candidate-list"
    args = parser.parse_args(
        [
            "eval",
            "context-ablation",
            "a.json",
            "b.json",
            "--context-a",
            "a-context.json",
            "--context-b",
            "b-context.json",
            "--experiment-id",
            "p1",
            "--model",
            "fixture",
        ]
    )
    assert args.eval_command == "context-ablation"
