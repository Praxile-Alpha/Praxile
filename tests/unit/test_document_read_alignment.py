import copy
import json
from pathlib import Path

import pytest

from praxile.eval.v2.document_read_alignment import align_document_reads


def read(event_id, **changes):
    return {"event_id": event_id, "facts": {"operation": "read_document", "status": "success",
        "parameter_sha256": "a" * 64, "source_sha256": "b" * 64, "unit_kind": "block",
        "total_units": 23, "returned_units": 20, "start_unit": 1,
        "requested_limit": 20, "requested_char_offset": 0, **changes}}


def test_order_independent_matching_uses_recorded_state():
    a, b = read("a"), read("b", source_sha256="c" * 64)
    c, d = read("c"), read("d", source_sha256="c" * 64)
    result = align_document_reads([a, b], [d, c])
    assert {(p["baseline_event_id"], p["candidate_event_id"]) for p in result["pairs"]} == {("a", "c"), ("b", "d")}
    assert all(p["state_equivalence"] == "unknown" for p in result["pairs"])
    assert result["semantic_equivalence_verified"] is result["causal_alignment"] is False


@pytest.mark.parametrize("change", [{"source_sha256": "d" * 64},
    {"parameter_sha256": "e" * 64}, {"returned_units": 19}, {"unit_kind": "page"},
    {"next_start": 21}])
def test_changed_selection_version_or_shape_is_not_aligned(change):
    result = align_document_reads([read("a")], [read("b", **change)])
    assert not result["pairs"]
    assert len(result["unpaired"]) == 2


def test_duplicates_are_not_aligned_positionally():
    result = align_document_reads([read("a"), read("b")], [read("c"), read("d")])
    assert not result["pairs"]
    assert result["unpaired"][0]["reason"] == "ambiguous_repetition"
    assert result["unpaired"][0]["baseline_event_ids"] == ["a", "b"]


@pytest.mark.parametrize("changes", [{"source_sha256": None}, {"source_sha256": "bad"},
    {"status": "failure"}, {"operation": "revise_docx"}, {"total_units": True}])
def test_ineligible_events_are_visible_as_unsupported(changes):
    result = align_document_reads([read("a", **changes)], [read("b")])
    assert not result["pairs"]
    assert result["unsupported"]["baseline"] == ["a"]


def test_no_input_mutation_or_prose_export():
    left = [read("a", raw_output="PRIVATE", reason="PRIVATE")]
    before = copy.deepcopy(left)
    result = align_document_reads(left, [read("b")])
    assert left == before
    assert "PRIVATE" not in str(result)


def test_public_alignment_recomputes_from_published_projection():
    root = Path(__file__).resolve().parents[2] / "docs/results"
    receipt = json.loads((root / "docx-read-alignment-20261021.json").read_text())
    original = json.loads((root / "docx-behavior-20261021.json").read_text())
    assert receipt["independent_task_count"] == 1
    assert receipt["causal_effect_verified"] is receipt["promotion_eligible"] is False
    matches = []
    for pair, source in zip(receipt["pairs"], original["pairs"], strict=True):
        assert pair["candidate"] == source["candidate"]
        arms = {arm["arm"]: arm["behavior"] for arm in source["arms"]}
        assert pair["source_result_sha256"] == {key: value["source_result_sha256"] for key, value in arms.items()}
        assert pair["alignment"] == align_document_reads(arms["baseline"]["timeline"], arms["candidate"]["timeline"])
        matches.append(len(pair["alignment"]["pairs"]))
    assert matches == [0, 1, 1]
