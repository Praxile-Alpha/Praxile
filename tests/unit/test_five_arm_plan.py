from copy import deepcopy
import json

import pytest

from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan, freeze_plan, iter_schedule, main, plan_digest
from praxile.eval.v2.schema import EvalSchemaError


def specification():
    return {"study_id": "study", "task_ids": ["dev-b", "dev-a"], "heldout_task_ids": ["heldout"],
            "repetitions": 3, "repeat_attempts": 2,
            "identities": {key: "sha256:" + "a" * 64 for key in IDENTITIES},
            "budget": {"total_tokens": 20000, "model_calls": 10, "wall_seconds": 300},
            "arm_order": list(ARMS)}


def test_five_arms_have_distinct_mechanisms_but_shared_budget_and_no_updates():
    spec = specification()
    plan = compile_plan(spec)
    rows = list(iter_schedule(plan))
    assert len(rows) == len({row["cell_id"] for row in rows}) == 30
    assert [row["arm"] for row in rows[:5]] == list(ARMS)
    assert [rows[index]["task_id"] for index in (0, 5, 10)] == ["dev-b", "dev-a", "dev-b"]
    for row in rows:
        assert row["shared_budget"] == spec["budget"]
        assert row["persistent_updates_allowed"] is False
        assert row["max_attempts"] == (2 if row["arm"] == "budget_matched_repeat" else 1)
    assert plan["arms"]["experience_only"]["experience_enabled"] is True
    assert plan["arms"]["experience_only"]["task_local_enabled"] is False
    assert plan["arms"]["task_local_only"]["experience_enabled"] is False
    assert plan["arms"]["full_praxile"]["task_local_enabled"] is True
    assert plan["budget_enforcement_verified"] is False
    spec["task_ids"].reverse()
    assert plan["task_order"] == ["dev-b", "dev-a"]
    rows[0]["shared_budget"]["total_tokens"] = 0
    assert plan["arms"]["frozen_executor"]["shared_budget"]["total_tokens"] == 20000


@pytest.mark.parametrize("field,value", [
    ("study_id", "../escape"), ("task_ids", []), ("task_ids", ["x", "x"]),
    ("heldout_task_ids", ["dev-a"]), ("repetitions", True), ("repeat_attempts", 1),
    ("arm_order", list(ARMS[:-1])), ("arm_order", [{}] * 5),
    ("budget", {"total_tokens": 0, "model_calls": 10, "wall_seconds": 10}),
    ("budget", {"total_tokens": 100, "model_calls": 1, "wall_seconds": 10}),
    ("identities", {key: "latest" for key in IDENTITIES}),
])
def test_invalid_plan_rejected_before_any_state(tmp_path, field, value):
    spec = specification()
    spec[field] = value
    with pytest.raises(EvalSchemaError):
        freeze_plan(spec, tmp_path / "study")
    assert not (tmp_path / "study").exists()


def test_frozen_manifest_is_idempotent_but_changed_model_or_budget_rejected(tmp_path):
    spec = specification()
    first = freeze_plan(spec, tmp_path)
    assert freeze_plan(spec, tmp_path) == first
    for field in ("identities", "budget", "task_ids"):
        changed = deepcopy(spec)
        if field == "identities":
            changed[field]["model"] = "sha256:" + "b" * 64
        elif field == "budget":
            changed[field]["model_calls"] += 1
        else:
            changed[field].reverse()
        with pytest.raises(EvalSchemaError):
            freeze_plan(changed, tmp_path)
        assert plan_digest(compile_plan(changed)) != first["plan_digest"]
    assert json.loads((tmp_path / "manifest.json").read_text()) == compile_plan(spec)


def test_schedule_refuses_tampered_arm_flags():
    plan = compile_plan(specification())
    plan["arms"]["frozen_executor"]["experience_enabled"] = True
    with pytest.raises(EvalSchemaError):
        list(iter_schedule(plan))


def test_cli_only_freezes_plan(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(specification()))
    output = tmp_path / "plan"
    monkeypatch.setattr("sys.argv", ["five_arm_plan", "--spec", str(source), "--output", str(output)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "plan_only"
    assert result["scheduled_cells"] == 30
    assert result["promotion_eligible"] is False
