from __future__ import annotations

from praxile.cli_parser import build_parser


def test_run_without_experience_selects_retrieval_control_mode():
    args = build_parser().parse_args(["run", "fix parser", "--without-experience"])

    assert args.without_experience is True
    assert args.task == "fix parser"
