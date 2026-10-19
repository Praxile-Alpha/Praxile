import json
from unittest.mock import Mock

import pytest

from praxile.cli_parser import build_parser
from praxile.cli_commands import run_review
from praxile.config import Config, ProjectPaths, default_config


@pytest.mark.parametrize("mode", ["in-place", "copy"])
def test_cli_loads_contract_before_dispatch(tmp_path, monkeypatch, mode):
    request = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
               "outputs": [{"path": "report.json", "media_type": "application/json"}]}
    (tmp_path / "requirements.json").write_text(json.dumps(request))
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    monkeypatch.setattr(run_review, "load", lambda root: (config, Mock()))
    monkeypatch.setattr(run_review, "apply_run_overrides", lambda *args: {})
    args = build_parser().parse_args(["run", "Produce report", "--deliverable-contract", "requirements.json"])
    args.workspace_mode = mode
    if mode != "in-place":
        def isolated(args, *rest):
            assert args.bound_deliverable_contract == request
            return 0
        monkeypatch.setattr(run_review, "run_in_isolated_workspace", isolated)
        assert run_review.cmd_run(args, tmp_path) == 0
    else:
        class Observed(Exception):
            pass
        class Service:
            def __init__(self, *args):
                pass
            def run(self, task, **kwargs):
                assert kwargs["deliverable_contract"] == request
                raise Observed()
        monkeypatch.setattr(run_review, "RunService", Service)
        with pytest.raises(Observed):
            run_review.cmd_run(args, tmp_path)
