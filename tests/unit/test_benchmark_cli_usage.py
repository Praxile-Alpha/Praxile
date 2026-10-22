from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from praxile.cli_commands import evals


@pytest.mark.parametrize("complete,json_output", [(False, False), (True, False), (False, True)])
def test_benchmark_cli_distinguishes_known_subtotals_from_complete_totals(
        tmp_path, monkeypatch, capsys, complete, json_output):
    state = tmp_path / ".praxile"
    monkeypatch.setattr(evals, "load", lambda root: (
        SimpleNamespace(paths=SimpleNamespace(state=state)), MagicMock()))
    adapter = MagicMock()
    adapter.availability.return_value = (True, "fixture")
    monkeypatch.setattr(evals, "MiniSweAgentAdapter", lambda **kwargs: adapter)
    evaluator = MagicMock()
    evaluator.availability.return_value = (True, "fixture")
    monkeypatch.setattr(evals, "OfficialSWEbenchEvaluator", lambda **kwargs: evaluator)
    monkeypatch.setattr(evals, "SWEbenchTaskLoader", lambda **kwargs: MagicMock())
    monkeypatch.setattr(evals, "EventStore", lambda paths: None)
    metrics = {"completed_count": 0, "task_count": 1, "resolved_count": 0,
               "resolution_rate": None, "tokens": {"input": 0, "output": 0, "cache": 0},
               "cost": 0, "tool_calls": 0 if complete else None}
    if complete:
        metrics.update(tokens_total=metrics["tokens"], tokens_complete=True,
                       cost_total=0, cost_complete=True, reported_cost_subtotal=0)
    report = {"eval_run_id": "fixture", "task_set": "fixture", "manifest_path": "manifest.json",
              "metrics": metrics}
    runner = MagicMock()
    runner.run.return_value = report
    monkeypatch.setattr(evals, "BenchmarkEvalRunner", lambda *args: runner)
    args = SimpleNamespace(resume=False, run_id="fixture", model="fixture", model_class="fixture",
        adapter_config=[], cost_tracking="ignore_errors", timeout=1, dataset_name="fixture", split="test",
        instance_id=[], development_size=None, seed=0, tasks=None, source=[], max_cost=None,
        step_limit=1, keep_workspaces=False, json=json_output)
    assert evals.cmd_eval_benchmark(args, tmp_path) == 1
    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output) == report
    elif complete:
        assert "Cost: total=0  known_subtotal=0" in output
        assert "Tokens: total={'input': 0, 'output': 0, 'cache': 0}" in output
        assert "Tool calls: 0" in output
    else:
        assert "Cost: total=unknown  known_subtotal=0" in output
        assert "Tokens: total=unknown" in output
        assert "Tool calls: unknown" in output
    adapter.__exit__.assert_called_once()
