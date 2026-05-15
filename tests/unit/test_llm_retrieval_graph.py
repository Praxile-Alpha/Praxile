from __future__ import annotations

import json
from pathlib import Path

import pytest

from praxile.config import Config
from praxile.evolution import EvolutionEngine
from praxile.graph import ProvenanceGraph
from praxile.indexing import build_experience_index
from praxile.llm import LLMProposalParseError, parse_proposal_response
from praxile.retrieval import HybridRetriever, experience_docs


def test_llm_parser_accepts_content_only_proposal_and_evolution_converts_it(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    config.data["proposal_gate"]["enabled"] = False
    engine = EvolutionEngine(config)
    response = json.dumps(
        {
            "proposals": [
                {
                    "type": "memory_update",
                    "title": "Record retry lesson",
                    "rationale": "The trace showed retry timeout evidence.",
                    "content": "Retry timeout work should verify backoff commands.",
                    "confidence": 0.8,
                    "evidence": ["run command mentioned retry timeout"],
                    "applicability_scope": "retry timeout fixes",
                    "anti_scope": "unrelated UI work",
                }
            ]
        }
    )

    raw = engine._parse_proposals(response)
    proposal = engine._llm_item_to_proposal(
        {"task_id": "task_llm", "actions": []},
        raw[0],
    )

    assert proposal is not None
    assert proposal["type"] == "memory_update"
    assert proposal["changes"][0]["path"].startswith("memory/")


def test_llm_parser_rejects_invalid_json_and_missing_required_fields() -> None:
    with pytest.raises(LLMProposalParseError):
        parse_proposal_response("not json")
    with pytest.raises(LLMProposalParseError):
        parse_proposal_response(json.dumps([{"type": "memory_update", "confidence": 0.5}]))


def test_hybrid_retriever_indexes_state_and_returns_top_k(tmp_path: Path) -> None:
    state = tmp_path / ".praxile"
    (state / "memory").mkdir(parents=True)
    (state / "rules" / "harness-rules").mkdir(parents=True)
    (state / "memory" / "project.md").write_text("# Retry\n\nUse retry timeout backoff verification.\n", encoding="utf-8")
    (state / "rules" / "harness-rules" / "ui.md").write_text("# UI\n\nHuman checklist for selected button state.\n", encoding="utf-8")
    retriever = HybridRetriever(state / "db" / "hybrid")

    docs = experience_docs(state)
    report = retriever.build_index(docs)
    results = retriever.vector_search("retry timeout backoff", top_k=2)

    assert report["documents"] == 2
    assert results
    assert results[0]["doc"]["path"].endswith("memory/project.md")

    index_report = build_experience_index(state)
    assert index_report["documents"] == 2


def test_hybrid_retriever_empty_index_returns_empty(tmp_path: Path) -> None:
    retriever = HybridRetriever(tmp_path / "db")
    assert retriever.vector_search("anything") == []


def test_provenance_graph_persists_and_explains_relations(tmp_path: Path) -> None:
    graph = ProvenanceGraph()
    graph.add_asset("asset:.praxile/memory/project.md", "memory", trajectory_id="task_graph")
    graph.add_relation("proposal:prop_graph", "asset:.praxile/memory/project.md", relation="approved_by")
    path = tmp_path / "graph.json"
    graph.save(path)

    loaded = ProvenanceGraph()
    loaded.load(path)
    explanation = loaded.explain("asset:.praxile/memory/project.md")

    assert explanation["node"]["node_type"] == "asset"
    assert any(edge["relation"] == "approved_by" for edge in explanation["edges"])
