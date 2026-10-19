from types import SimpleNamespace

from praxile.eval.v2.alignment import align_operations


def event(identity, command):
    return SimpleNamespace(event_id=identity, type="TOOL_CALL", payload={"action": {"command": command}})


def test_unique_operations_align_despite_different_order_without_disclosing_command():
    a = [event("a1", "cat sensitive_path"), event("a2", "python -m pytest")]
    b = [event("b1", "python -m pytest"), event("b2", "cat sensitive_path")]
    result = align_operations(a, b)
    assert {(row["baseline_event_id"], row["candidate_event_id"]) for row in result["pairs"]} == {("a1", "b2"), ("a2", "b1")}
    assert "sensitive_path" not in str(result)
    assert not result["causal_alignment"]
    assert all(row["state_equivalence"] == "unknown" for row in result["pairs"])


def test_repeated_commands_are_not_paired_by_step_number():
    result = align_operations([event("a1", "ls"), event("a2", "ls")], [event("b1", "ls"), event("b2", "ls")])
    assert not result["pairs"]
    assert result["unpaired"][0]["reason"] == "ambiguous repetition"


def test_native_and_mini_commands_align_but_unknown_or_truncated_actions_do_not():
    native = SimpleNamespace(event_id="native", type="TOOL_CALL",
        payload={"tool": "run_command", "input": {"type": "run_command", "command": "pytest"}})
    unknown = SimpleNamespace(event_id="unknown", type="TOOL_CALL", payload={"action": {"type": "edit_file"}})
    truncated = event("truncated", "head...[truncated 200 chars in normalized event]...tail")
    result = align_operations([native, unknown, truncated], [event("mini", "pytest")])
    assert result["pairs"][0]["baseline_event_id"] == "native"
    assert result["unsupported"]["baseline"] == ["unknown", "truncated"]


def test_different_arguments_are_not_aligned():
    result = align_operations([event("a", "pytest tests/a.py")], [event("b", "pytest tests/b.py")])
    assert not result["pairs"]
    assert len(result["unpaired"]) == 2
