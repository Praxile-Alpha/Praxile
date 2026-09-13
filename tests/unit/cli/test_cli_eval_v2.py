from __future__ import annotations

from praxile.cli_parser import build_parser


def test_eval_benchmark_parser_exposes_reproducibility_controls() -> None:
    args = build_parser().parse_args(
        [
            "eval",
            "benchmark",
            "tasks.jsonl",
            "--model",
            "openai/model",
            "--development-size",
            "5",
            "--seed",
            "fixed",
            "--run-id",
            "baseline-1",
            "--cost-tracking",
            "ignore_errors",
            "--resume",
        ]
    )

    assert args.eval_command == "benchmark"
    assert args.model == "openai/model"
    assert args.development_size == 5
    assert args.seed == "fixed"
    assert args.run_id == "baseline-1"
    assert args.cost_tracking == "ignore_errors"
    assert args.step_limit == 50
    assert args.resume is True
    assert args.func.__name__ == "cmd_eval_benchmark"


def test_eval_benchmark_tasks_file_is_optional_for_huggingface_loading() -> None:
    args = build_parser().parse_args(["eval", "benchmark", "--model", "openai/model"])

    assert args.tasks is None
    assert args.dataset_name == "SWE-bench/SWE-bench_Lite"
    assert args.cost_tracking == "default"
    assert args.step_limit == 50


def test_eval_diagnose_and_ab_parsers_expose_p0_c_controls() -> None:
    parser = build_parser()
    diagnose = parser.parse_args(["eval", "diagnose", "baseline-1", "--json"])
    analyze = parser.parse_args(["eval", "ab-analyze", "p0-c-1", "--json"])
    export = parser.parse_args(
        ["eval", "export-public", "p0-c-1", "--output", "public-result", "--json"]
    )
    experiment = parser.parse_args(
        [
            "eval",
            "ab",
            "candidate.json",
            "--experiment-id",
            "p0-c-1",
            "--model",
            "ollama/model",
            "--instance-id",
            "owner__repo-1",
            "--resume",
        ]
    )

    assert diagnose.func.__name__ == "cmd_eval_diagnose"
    assert diagnose.run_id == "baseline-1"
    assert analyze.func.__name__ == "cmd_eval_ab_analyze"
    assert analyze.experiment_id == "p0-c-1"
    assert export.func.__name__ == "cmd_eval_export_public"
    assert export.output == "public-result"
    assert experiment.func.__name__ == "cmd_eval_ab"
    assert experiment.experiment_id == "p0-c-1"
    assert experiment.step_limit == 50
    assert experiment.resume is True
