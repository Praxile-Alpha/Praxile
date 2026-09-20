from praxile.cli_parser import build_parser


def test_reward_explain_parser_contract() -> None:
    args = build_parser().parse_args(["reward", "explain", "task_123", "--json"])
    assert args.reward_command == "explain"
    assert args.id == "task_123"
    assert args.json is True


def test_judge_calibration_parser_contract() -> None:
    args = build_parser().parse_args(["judge", "calibrate", "suite.json", "--write-proposal"])
    assert args.judge_command == "calibrate"
    assert args.suite == "suite.json"
    assert args.write_proposal is True


def test_judge_metrics_parser_contract() -> None:
    args = build_parser().parse_args(["judge", "metrics", "--limit", "50", "--json"])
    assert args.judge_command == "metrics"
    assert args.limit == 50
    assert args.json is True


def test_harness_lab_parser_contract() -> None:
    args = build_parser().parse_args([
        "harness", "lab-run", "manifest.json",
        "--development-tasks", "dev.json",
        "--heldout-tasks", "heldout.json",
        "--resume",
    ])
    assert args.manifest == "manifest.json"
    assert args.development_tasks == "dev.json"
    assert args.heldout_tasks == "heldout.json"
    assert args.resume is True
