import pytest

from praxile.eval import five_arm_public as module
from praxile.eval.five_arm_plan import ARMS, iter_schedule
from test_five_arm_cost_analysis import fixture


def test_public_projection_excludes_private_fields_and_retains_missing_cells(monkeypatch):
    plan, _ = fixture()
    first = next(iter_schedule(plan))
    fake = {"plan_digest": "sha256:" + "a" * 64, "task_count": 2, "complete_task_count": 0,
        "private_prompt": "PRIVATE_SENTINEL", "model": "PRIVATE_SENTINEL",
        "cell_reviews": [{"cell_id": first["cell_id"], "status": "unknown", "verdict_counts": None,
                          "execution_status": "budget_exhausted", "review_status": "abstained",
                          "raw_output": "PRIVATE_SENTINEL"}],
        "comparisons": {arm: {"sample_count": 0, "descriptive_delta": None, "familywise_ci95": None} for arm in ARMS[1:]},
        "broker_usage": {"known_tokens": {"input": 10, "output": 0}, "total_tokens": {"input": None, "output": None}},
        "broker_cost_comparison": {"comparisons": {arm: {"sample_count": 0, "token_delta": {"input": None, "output": None}} for arm in ARMS[1:]}}}
    monkeypatch.setattr(module, "audit_study", lambda *args, **kwargs: fake)
    report = module.public_summary(plan, {}, directory="PRIVATE_PATH", study_sha256="b" * 64,
                                   evaluator_root="PRIVATE_PATH", public=True)
    assert len(report["cells"]) == 20
    assert report["cells"][0]["review_status"] == "abstained"
    assert report["cells"][0]["execution_status"] == "budget_exhausted"
    assert report["cells"][1]["status"] == "not_recorded"
    assert report["total_tokens"]["input"] is None
    assert "PRIVATE" not in str(report)
    rendered = module.render_markdown(report)
    assert "abstained" in rendered and "unknown" in rendered
    assert "PRIVATE" not in rendered
    assert first["cell_id"] not in rendered
    assert report["promotion_eligible"] is False


def test_no_disclosure_without_optin(monkeypatch):
    monkeypatch.setattr(module, "audit_study", lambda *a, **k: pytest.fail("no evidence reads before approval"))
    with pytest.raises(ValueError):
        module.public_summary({}, {}, directory="unused", study_sha256="unused", evaluator_root="unused")


def test_cli_optin_required_before_opening_files(capsys):
    with pytest.raises(SystemExit) as error:
        module.main(["--study", "PRIVATE_PATH", "--study-sha256", "unused", "--task", "PRIVATE_TASK",
                     "--evaluator-root", "PRIVATE_EVALUATOR"])
    assert error.value.code == 2
    assert "PRIVATE" not in capsys.readouterr().err
