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
