from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class GraphStoreMixin:
    def rebuild_experience_graph(self) -> dict[str, Any]:
        self.reindex_all()
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}

        def add_node(node_id: str, node_type: str, *, ref_path: str | None = None, title: str | None = None, created_at: str | None = None) -> None:
            nodes.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "ref_path": ref_path,
                    "title": title or ref_path or node_id,
                    "created_at": created_at or utc_now(),
                },
            )

        def add_edge(
            source: str,
            target: str,
            relation_type: str,
            *,
            confidence: float = 1.0,
            evidence: dict[str, Any] | str | None = None,
        ) -> None:
            if source not in nodes or target not in nodes:
                return
            edge_id = "edge:" + stable_hash(f"{source}|{target}|{relation_type}|{json.dumps(evidence, sort_keys=True, ensure_ascii=False)}", length=24)
            edges.setdefault(
                edge_id,
                {
                    "edge_id": edge_id,
                    "source_node_id": source,
                    "target_node_id": target,
                    "relation_type": relation_type,
                    "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
                    "evidence": json.dumps(evidence or {}, ensure_ascii=False) if not isinstance(evidence, str) else evidence,
                    "created_at": utc_now(),
                },
            )

        with self._connection() as conn:
            asset_rows = [self._asset_row_with_lifecycle(row) for row in conn.execute("SELECT * FROM assets").fetchall()]
            task_rows = [dict(row) for row in conn.execute("SELECT * FROM tasks").fetchall()]
            usage_rows = [dict(row) for row in conn.execute("SELECT * FROM asset_usage").fetchall()]
            activation_rows = [dict(row) for row in conn.execute("SELECT * FROM asset_activation_events ORDER BY id").fetchall()]

        activation_by_usage: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in activation_rows:
            key = (str(event.get("task_id") or ""), str(event.get("path") or ""))
            activation_by_usage.setdefault(key, []).append(event)

        proposals = self.list_proposals(status=None, limit=10000)
        trajectories: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            task_id = str(row.get("task_id") or "")
            if not task_id:
                continue
            trajectory_path = self.paths.root / str(row.get("trajectory_path") or "")
            trajectory = read_json(trajectory_path, {}) if trajectory_path.exists() else {}
            trajectories[task_id] = trajectory
            add_node(
                _graph_run_node_id(task_id),
                "run",
                ref_path=str(row.get("trajectory_path") or ""),
                title=str(row.get("user_task") or task_id),
                created_at=str(row.get("created_at") or utc_now()),
            )
            for spec_path in (trajectory.get("spec_context") or {}).get("spec_files") or []:
                spec_node = _graph_spec_node_id(str(spec_path))
                add_node(spec_node, "spec", ref_path=str(spec_path), title=Path(str(spec_path)).name)
                add_edge(spec_node, _graph_run_node_id(task_id), "derived_from_spec", evidence={"task_id": task_id})
            compliance = trajectory.get("spec_compliance") if isinstance(trajectory.get("spec_compliance"), dict) else {}
            for spec_path in compliance.get("spec_files") or []:
                spec_node = _graph_spec_node_id(str(spec_path))
                add_node(spec_node, "spec", ref_path=str(spec_path), title=Path(str(spec_path)).name)
                status = str(compliance.get("status") or "unknown")
                relation = "satisfies_spec" if status == "full" else "violates_spec" if status in {"partial", "failed"} else "derived_from_spec"
                confidence = _graph_confidence(compliance.get("score"), default=0.6)
                add_edge(
                    _graph_run_node_id(task_id),
                    spec_node,
                    relation,
                    confidence=confidence,
                    evidence={
                        "status": status,
                        "missing": len(compliance.get("missing") or []),
                        "violations": len(compliance.get("violations") or []),
                    },
                )
            action_counts_by_executor: dict[str, int] = {}
            for action in trajectory.get("actions") or []:
                executor = action.get("executor") if isinstance(action, dict) else {}
                if isinstance(executor, dict):
                    executor_id = str(executor.get("executor_id") or "")
                    if executor_id:
                        action_counts_by_executor[executor_id] = action_counts_by_executor.get(executor_id, 0) + 1
            for executor in trajectory.get("executors") or []:
                if not isinstance(executor, dict):
                    continue
                executor_id = str(executor.get("executor_id") or "")
                if not executor_id:
                    continue
                executor_node = _graph_executor_node_id(task_id, executor_id)
                add_node(
                    executor_node,
                    "executor",
                    ref_path=f"{task_id}:{executor_id}",
                    title=str(executor.get("role") or executor_id),
                    created_at=str(executor.get("registered_at") or row.get("created_at") or utc_now()),
                )
                add_edge(
                    executor_node,
                    _graph_run_node_id(task_id),
                    "participated_in_run",
                    confidence=1.0,
                    evidence={
                        "executor_id": executor_id,
                        "kind": executor.get("kind"),
                        "role": executor.get("role"),
                        "action_count": action_counts_by_executor.get(executor_id, 0),
                    },
                )
            reward_graph = (trajectory.get("reward_report") or {}).get("evidence_graph") or {}
            for evidence_item in reward_graph.get("nodes") or []:
                evidence_id = str(evidence_item.get("evidence_id") or "")
                if evidence_id:
                    add_node(evidence_id, "reward_evidence", ref_path=evidence_item.get("source_ref"), title=str(evidence_item.get("type") or evidence_id))
            for claim_item in reward_graph.get("claims") or []:
                claim_id = str(claim_item.get("claim_id") or "")
                if not claim_id:
                    continue
                add_node(claim_id, "reward_claim", ref_path=f"{task_id}:{claim_item.get('claim_type')}", title=str(claim_item.get("claim_type") or claim_id))
                add_edge(
                    claim_id,
                    _graph_run_node_id(task_id),
                    "contributes_to_reward",
                    evidence={"value": claim_item.get("value"), "provenance": claim_item.get("provenance"), "profile_version": claim_item.get("profile_version")},
                )
                for evidence_ref in claim_item.get("evidence_refs") or []:
                    add_edge(str(evidence_ref), claim_id, "supports_reward_claim", evidence={"claim_type": claim_item.get("claim_type")})

        for asset in asset_rows:
            path = str(asset.get("path") or "")
            if not path:
                continue
            node_id = _graph_asset_node_id(path)
            add_node(
                node_id,
                str(asset.get("type") or "asset"),
                ref_path=path,
                title=str(asset.get("title") or path),
                created_at=str(asset.get("created_at") or utc_now()),
            )
        for proposal in proposals:
            proposal_id = str(proposal.get("proposal_id") or "")
            if not proposal_id:
                continue
            proposal_node = _graph_proposal_node_id(proposal_id)
            add_node(
                proposal_node,
                "proposal",
                ref_path=str(proposal.get("path") or proposal_id),
                title=str(proposal.get("title") or proposal_id),
                created_at=str(proposal.get("created_at") or utc_now()),
            )
            source_task_id = str(proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id") or "")
            if source_task_id:
                add_node(_graph_run_node_id(source_task_id), "run", ref_path=None, title=source_task_id)
                add_edge(_graph_run_node_id(source_task_id), proposal_node, "generated_from_run", evidence={"proposal_type": proposal.get("type")})
            relation = "approved_by" if proposal.get("status") == "accepted" else "supports_proposal"
            for target in proposal.get("target_files") or []:
                asset_node = _graph_asset_node_id(_normalize_graph_asset_path(str(target)))
                if asset_node not in nodes:
                    add_node(asset_node, "asset", ref_path=_normalize_graph_asset_path(str(target)), title=str(target))
                add_edge(
                    proposal_node,
                    asset_node,
                    relation,
                    confidence=_graph_confidence(proposal.get("confidence"), default=0.7),
                    evidence={"proposal_id": proposal_id, "status": proposal.get("status"), "risk_level": proposal.get("risk_level")},
                )
            if proposal.get("type") == "asset_deprecate":
                for target in proposal.get("target_files") or []:
                    add_edge(proposal_node, _graph_asset_node_id(_normalize_graph_asset_path(str(target))), "deprecated_by", evidence={"proposal_id": proposal_id})
            for left, right, conflict_evidence in _graph_conflict_pairs(proposal):
                left_node = _graph_asset_node_id(left)
                right_node = _graph_asset_node_id(right)
                for asset_node, asset_path in [(left_node, left), (right_node, right)]:
                    if asset_node not in nodes:
                        add_node(asset_node, "asset", ref_path=_normalize_graph_asset_path(asset_path), title=asset_path)
                add_edge(
                    left_node,
                    right_node,
                    "contradicts_asset",
                    confidence=_graph_confidence(conflict_evidence.get("confidence"), default=0.65),
                    evidence={"proposal_id": proposal_id, **conflict_evidence},
                )

        for asset in asset_rows:
            path = str(asset.get("path") or "")
            replacement = str(asset.get("replaced_by") or "").strip()
            if path and replacement:
                add_node(_graph_asset_node_id(_normalize_graph_asset_path(replacement)), "asset", ref_path=_normalize_graph_asset_path(replacement), title=replacement)
                add_edge(
                    _graph_asset_node_id(_normalize_graph_asset_path(replacement)),
                    _graph_asset_node_id(path),
                    "supersedes",
                    confidence=0.9,
                    evidence={"reason": asset.get("superseded_reason") or asset.get("deprecated_reason") or asset.get("archived_reason")},
                )
        for row in usage_rows:
            path = str(row.get("path") or "")
            task_id = str(row.get("task_id") or "")
            if not path or not task_id:
                continue
            asset_node = _graph_asset_node_id(path)
            run_node = _graph_run_node_id(task_id)
            if asset_node not in nodes or run_node not in nodes:
                continue
            row_copy = dict(row)
            row_copy["used_in_prompt"] = bool(row_copy.get("used_in_prompt"))
            row_copy["referenced"] = bool(row_copy.get("referenced"))
            row_copy["used_explicitly"] = bool(row_copy.get("used_explicitly"))
            row_copy["semantic_attribution"] = _decode_json_dict(row_copy.get("semantic_attribution"))
            semantic = row_copy.get("semantic_attribution") if isinstance(row_copy.get("semantic_attribution"), dict) else {}
            has_causal_credit = bool(
                semantic.get("semantic_judge", {}).get("active")
                and semantic.get("should_update_asset_outcome")
            )
            level = _normalize_attribution_level(semantic.get("attribution_level")) if has_causal_credit else _usage_attribution_level(row_copy)
            relation = "helped_run" if level in {"weak_positive", "strong_positive"} else "misled_run" if level in {"weak_negative", "harmful"} else "retrieved_in_run"
            activation_events = activation_by_usage.get((task_id, path), [])
            add_edge(
                asset_node,
                run_node,
                relation,
                confidence=_graph_confidence(row_copy.get("score"), default=0.55),
                evidence={
                    "attribution_level": level,
                    "outcome": row_copy.get("outcome"),
                    "why_loaded": row_copy.get("why_loaded"),
                    "causal_credit": has_causal_credit,
                    "activation_stages": list(dict.fromkeys(str(event.get("stage") or "") for event in activation_events)),
                    "activation_event_ids": [str(event.get("event_id") or "") for event in activation_events],
                },
            )

        with self._connection() as conn:
            conn.execute("DELETE FROM experience_edges")
            conn.execute("DELETE FROM experience_nodes")
            for node in nodes.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experience_nodes(node_id, node_type, ref_path, title, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (node["node_id"], node["node_type"], node.get("ref_path"), node.get("title"), node["created_at"]),
                )
            for edge in edges.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experience_edges(edge_id, source_node_id, target_node_id, relation_type, confidence, evidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge["edge_id"],
                        edge["source_node_id"],
                        edge["target_node_id"],
                        edge["relation_type"],
                        edge["confidence"],
                        edge["evidence"],
                        edge["created_at"],
                    ),
                )
        return {"nodes": len(nodes), "edges": len(edges), "relation_counts": _count_graph_edges(edges.values())}
    def graph_status(self) -> dict[str, Any]:
        self._init_db()
        with self._connection() as conn:
            node_count = int(conn.execute("SELECT COUNT(*) AS count FROM experience_nodes").fetchone()["count"])
            edge_count = int(conn.execute("SELECT COUNT(*) AS count FROM experience_edges").fetchone()["count"])
            node_rows = conn.execute("SELECT node_type, COUNT(*) AS count FROM experience_nodes GROUP BY node_type ORDER BY node_type").fetchall()
            edge_rows = conn.execute("SELECT relation_type, COUNT(*) AS count FROM experience_edges GROUP BY relation_type ORDER BY relation_type").fetchall()
        return {
            "nodes": node_count,
            "edges": edge_count,
            "node_counts": {row["node_type"]: int(row["count"]) for row in node_rows},
            "relation_counts": {row["relation_type"]: int(row["count"]) for row in edge_rows},
        }
    def graph_explain(self, ref: str, *, depth: int = 1, limit: int = 100) -> dict[str, Any]:
        self._init_db()
        start = self._resolve_graph_node(ref)
        if not start:
            return {"ref": ref, "found": False, "nodes": [], "edges": []}
        max_depth = max(0, min(4, int(depth or 1)))
        max_edges = max(1, int(limit or 100))
        seen_nodes = {start["node_id"]}
        frontier = {start["node_id"]}
        collected_edges: dict[str, dict[str, Any]] = {}
        with self._connection() as conn:
            for _ in range(max_depth):
                if not frontier or len(collected_edges) >= max_edges:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM experience_edges
                    WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})
                    ORDER BY created_at DESC, relation_type
                    LIMIT ?
                    """,
                    [*frontier, *frontier, max_edges - len(collected_edges)],
                ).fetchall()
                next_frontier: set[str] = set()
                for row in rows:
                    edge = _graph_edge_row(row)
                    collected_edges[edge["edge_id"]] = edge
                    for node_id in [edge["source_node_id"], edge["target_node_id"]]:
                        if node_id not in seen_nodes:
                            seen_nodes.add(node_id)
                            next_frontier.add(node_id)
                frontier = next_frontier
            if seen_nodes:
                placeholders = ",".join("?" for _ in seen_nodes)
                node_rows = conn.execute(
                    f"SELECT * FROM experience_nodes WHERE node_id IN ({placeholders}) ORDER BY node_type, title",
                    list(seen_nodes),
                ).fetchall()
            else:
                node_rows = []
        nodes = [_graph_node_row(row) for row in node_rows]
        edges_list = list(collected_edges.values())
        return {
            "ref": ref,
            "found": True,
            "start_node": start,
            "nodes": nodes,
            "edges": edges_list,
            "relation_counts": _count_graph_edges(edges_list),
        }
    def _resolve_graph_node(self, ref: str) -> dict[str, Any] | None:
        candidates = _graph_node_candidates(ref)
        with self._connection() as conn:
            for node_id in candidates:
                row = conn.execute("SELECT * FROM experience_nodes WHERE node_id = ?", (node_id,)).fetchone()
                if row:
                    return _graph_node_row(row)
            row = conn.execute(
                """
                SELECT *
                FROM experience_nodes
                WHERE ref_path = ? OR title = ?
                ORDER BY CASE WHEN ref_path = ? THEN 0 ELSE 1 END, node_type
                LIMIT 1
                """,
                (ref, ref, ref),
            ).fetchone()
        return _graph_node_row(row) if row else None



class GraphRepository(StoreRepository):
    def status(self) -> dict[str, Any]:
        return self.store.graph_status()

    def rebuild(self) -> dict[str, Any]:
        return self.store.rebuild_experience_graph()

    def explain(self, ref: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.graph_explain(ref, **kwargs)
