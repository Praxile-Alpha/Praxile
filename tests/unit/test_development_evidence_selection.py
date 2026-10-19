import json

import pytest

from praxile.eval.v2.development_evidence import select_evidence


def item(name, task="task", **facts):
    return {"event_id": name, "task_id": task, "observation": json.dumps(facts)}


def test_failure_priority_carries_successful_revision_and_before_check():
    evidence = [item("before"), item("revision"), item("after",
        revision_event_id="revision", before_layout_event_id="before")]
    assert select_evidence(evidence, [1, 1, 0], 3) == [0, 1, 2]
    assert select_evidence(evidence, [1, 1, 0], 2) == [0, 1]
    assert select_evidence(evidence, [1, 1, 0], 1) == [0]


def test_transitive_dependencies_and_shared_sources_are_not_duplicated():
    evidence = [item("r1"), item("check1", revision_event_id="r1"), item("r2"),
                item("check2", revision_event_id="r2", before_layout_event_id="check1")]
    assert select_evidence(evidence, [1, 1, 1, 0], 4) == [0, 1, 2, 3]
    assert select_evidence(evidence, [1, 0, 1, 0], 4) == [0, 1, 2, 3]


@pytest.mark.parametrize("reference", ["missing", "later", "self", "other-task"])
def test_invalid_dependency_omits_conclusion(reference):
    evidence = [item("other-task", task="different"),
                item("self", revision_event_id=reference), item("later")]
    assert 1 not in select_evidence(evidence, [1, 0, 1], 3)


def test_unrelated_failures_preserve_priority_without_starvation():
    evidence = [item("first"), item("failure"), item("dependent", revision_event_id="first")]
    assert select_evidence(evidence, [1, 0, 0], 2) == [1, 0]
