from praxile.cli_parser import build_parser


def test_harness_p3_parser_contracts() -> None:
    parser = build_parser()
    mine = parser.parse_args(["harness", "mine", "--json"])
    propose = parser.parse_args(["harness", "propose", "pathology_123"])
    monitor = parser.parse_args(["harness", "monitor", "task_123", "--apply"])
    export = parser.parse_args(["harness", "export", "proposal_123", "--output", "bundle.zip"])

    assert mine.harness_command == "mine"
    assert propose.pathology_id == "pathology_123"
    assert monitor.apply is True
    assert export.output == "bundle.zip"
