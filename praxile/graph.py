from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import stable_hash, utc_now


class ProvenanceGraph:
    """Portable provenance graph for runs, proposals, and experience assets."""

    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}

    def add_asset(self, asset_id: str, asset_type: str, trajectory_id: str | None = None) -> None:
        self.add_node(asset_id, "asset", asset_type=asset_type, trajectory_id=trajectory_id)
        if trajectory_id:
            self.add_relation(f"run:{trajectory_id}", asset_id, relation="generated_asset")

    def add_node(self, node_id: str, node_type: str, **metadata: Any) -> None:
        current = self.nodes.get(node_id, {})
        self.nodes[node_id] = {
            **current,
            "node_id": node_id,
            "node_type": node_type,
            "metadata": {**current.get("metadata", {}), **metadata},
            "updated_at": utc_now(),
        }

    def add_relation(
        self,
        from_id: str,
        to_id: str,
        relation: str = "derived_from",
        *,
        confidence: float = 1.0,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.add_node(from_id, self.nodes.get(from_id, {}).get("node_type", "unknown"))
        self.add_node(to_id, self.nodes.get(to_id, {}).get("node_type", "unknown"))
        edge_id = stable_hash(f"{from_id}|{relation}|{to_id}", 20)
        self.edges[edge_id] = {
            "edge_id": edge_id,
            "source": from_id,
            "target": to_id,
            "relation": relation,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "evidence": evidence or {},
            "created_at": utc_now(),
        }

    def explain(self, ref: str) -> dict[str, Any]:
        node = self.nodes.get(ref)
        connected = [
            edge
            for edge in self.edges.values()
            if edge.get("source") == ref or edge.get("target") == ref
        ]
        return {"ref": ref, "node": node, "edges": connected}

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}

    def save(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def load(self, input_path: Path) -> None:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        self.nodes = {
            str(item.get("node_id")): item
            for item in payload.get("nodes", [])
            if isinstance(item, dict) and item.get("node_id")
        }
        self.edges = {
            str(item.get("edge_id")): item
            for item in payload.get("edges", [])
            if isinstance(item, dict) and item.get("edge_id")
        }
