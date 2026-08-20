from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class FeedbackStoreMixin:
    def record_asset_usage(
        self,
        task_id: str,
        assets: list[dict[str, Any]],
        *,
        used_in_prompt: bool = True,
        outcome: str = "unknown",
    ) -> None:
        if not assets:
            return
        self._init_db()
        now = utc_now()
        with self._connection() as conn:
            for item in assets:
                path = str(item.get("path") or "")
                if not path:
                    continue
                conn.execute(
                    """
                    INSERT INTO asset_usage
                    (path, task_id, matched_terms, matched_fields, why_loaded, score, used_in_prompt,
                     referenced, used_explicitly, outcome, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        task_id,
                        json.dumps(item.get("matched_terms") or [], ensure_ascii=False),
                        json.dumps(item.get("matched_fields") or [], ensure_ascii=False),
                        item.get("why_loaded") or item.get("reason") or "",
                        item.get("final_score", item.get("score")),
                        1 if used_in_prompt else 0,
                        1 if item.get("referenced") else 0,
                        1 if item.get("used_explicitly") else 0,
                        outcome,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE assets
                    SET usage_count = usage_count + 1, last_used_at = ?, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, now, path),
                )
                event_context = {
                    "asset_version": item.get("content_hash") or item.get("asset_version"),
                    "model_role": item.get("model_role"),
                    "executor_id": item.get("executor_id"),
                    "evidence": {
                        "matched_terms": item.get("matched_terms") or [],
                        "matched_fields": item.get("matched_fields") or [],
                        "why_loaded": item.get("why_loaded") or item.get("reason") or "",
                    },
                    "metadata": {
                        "score": item.get("final_score", item.get("score")),
                        "source_task_id": item.get("source_task_id"),
                    },
                }
                self._insert_activation_event(conn, task_id, path, "eligible", now=now, **event_context)
                self._insert_activation_event(conn, task_id, path, "retrieved", now=now, **event_context)
                if used_in_prompt:
                    self._insert_activation_event(conn, task_id, path, "injected", now=now, **event_context)
    def update_asset_usage_outcome(
        self,
        task_id: str,
        outcome: str,
        *,
        referenced_paths: list[str] | None = None,
        used_explicitly_paths: list[str] | None = None,
        attribution_results: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized = outcome if outcome in {"success", "failed", "needs_human", "unknown"} else "unknown"
        self._init_db()
        now = utc_now()
        referenced = set(referenced_paths or [])
        used_explicitly = set(used_explicitly_paths or [])
        attribution_by_path = {
            str(item.get("path") or ""): item
            for item in (attribution_results or [])
            if isinstance(item, dict) and str(item.get("path") or "")
        }
        with self._connection() as conn:
            for path, attribution in attribution_by_path.items():
                if attribution.get("referenced"):
                    referenced.add(path)
                if attribution.get("used_explicitly"):
                    used_explicitly.add(path)
                conn.execute(
                    """
                    UPDATE asset_usage
                    SET semantic_attribution = ?, updated_at = ?
                    WHERE task_id = ? AND path = ?
                    """,
                    (json.dumps(attribution, ensure_ascii=False), now, task_id, path),
                )
                evidence = {
                    "reason": attribution.get("reason"),
                    "evidence": attribution.get("evidence") or [],
                    "confidence": attribution.get("confidence"),
                    "attribution_level": attribution.get("attribution_level"),
                }
                judge = attribution.get("semantic_judge") if isinstance(attribution.get("semantic_judge"), dict) else {}
                if attribution.get("referenced"):
                    self._insert_activation_event(
                        conn,
                        task_id,
                        path,
                        "referenced",
                        now=now,
                        model_role=judge.get("role"),
                        evidence=evidence,
                    )
                if attribution.get("used_explicitly"):
                    self._insert_activation_event(
                        conn,
                        task_id,
                        path,
                        "complied_with",
                        now=now,
                        model_role=judge.get("role"),
                        evidence=evidence,
                    )
                if attribution.get("should_update_asset_outcome"):
                    contribution = _activation_contribution(attribution.get("attribution_level"))
                    self._insert_activation_event(
                        conn,
                        task_id,
                        path,
                        "outcome_attributed",
                        now=now,
                        outcome=normalized,
                        contribution=contribution,
                        model_role=judge.get("role"),
                        evidence=evidence,
                    )
            for path in referenced | used_explicitly:
                conn.execute(
                    """
                    UPDATE asset_usage
                    SET referenced = CASE WHEN ? THEN 1 ELSE referenced END,
                        used_explicitly = CASE WHEN ? THEN 1 ELSE used_explicitly END,
                        updated_at = ?
                    WHERE task_id = ? AND path = ?
                    """,
                    (1 if path in referenced else 0, 1 if path in used_explicitly else 0, now, task_id, path),
                )
                if path not in attribution_by_path:
                    self._insert_activation_event(
                        conn,
                        task_id,
                        path,
                        "referenced",
                        now=now,
                        evidence={"source": "runtime_heuristic", "causal_credit": False},
                    )
            rows = conn.execute(
                """
                SELECT path, MAX(referenced) AS referenced, MAX(used_explicitly) AS used_explicitly
                FROM asset_usage
                WHERE task_id = ? AND outcome = 'unknown'
                GROUP BY path
                """,
                (task_id,),
            ).fetchall()
            conn.execute(
                "UPDATE asset_usage SET outcome = ?, updated_at = ? WHERE task_id = ? AND outcome = 'unknown'",
                (normalized, now, task_id),
            )
            if normalized == "success":
                for row in rows:
                    attribution = attribution_by_path.get(str(row["path"]))
                    if not attribution or not _attribution_allows_outcome_update(attribution, success=True):
                        continue
                    conn.execute(
                        "UPDATE assets SET positive_outcome_count = positive_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )
            elif normalized == "failed":
                for row in rows:
                    attribution = attribution_by_path.get(str(row["path"]))
                    if not attribution or not _attribution_allows_outcome_update(attribution, success=False):
                        continue
                    conn.execute(
                        "UPDATE assets SET negative_outcome_count = negative_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )

    def _insert_activation_event(
        self,
        conn,
        task_id: str,
        path: str,
        stage: str,
        *,
        now: str | None = None,
        asset_version: str | None = None,
        outcome: str = "unknown",
        contribution: str = "unknown",
        model_role: str | None = None,
        executor_id: str | None = None,
        evidence: dict[str, Any] | list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        allowed = {"eligible", "retrieved", "injected", "referenced", "complied_with", "outcome_attributed"}
        if stage not in allowed:
            raise ValueError(f"Unsupported asset activation stage: {stage}")
        prior = conn.execute(
            """
            SELECT asset_version, model_role, executor_id
            FROM asset_activation_events
            WHERE task_id = ? AND path = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, path),
        ).fetchone()
        version = asset_version
        if not version and prior:
            version = prior["asset_version"]
        if not version:
            row = conn.execute("SELECT content_hash FROM assets WHERE path = ?", (path,)).fetchone()
            version = str(row["content_hash"] or "") if row else ""
        model_role = model_role or (prior["model_role"] if prior else None)
        executor_id = executor_id or (prior["executor_id"] if prior else None)
        conn.execute(
            """
            INSERT INTO asset_activation_events
            (event_id, task_id, path, asset_version, stage, outcome, contribution,
             model_role, executor_id, evidence, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("activation"),
                task_id,
                path,
                version or None,
                stage,
                outcome,
                contribution,
                model_role,
                executor_id,
                json.dumps(evidence or {}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
                now or utc_now(),
            ),
        )

    def activation_events_for_task(self, task_id: str) -> list[dict[str, Any]]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM asset_activation_events WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [_decode_activation_event(dict(row)) for row in rows]

    def activation_funnel_for_task(self, task_id: str) -> dict[str, Any]:
        events = self.activation_events_for_task(task_id)
        stages = ["eligible", "retrieved", "injected", "referenced", "complied_with", "outcome_attributed"]
        by_path: dict[str, dict[str, Any]] = {}
        for event in events:
            path = str(event.get("path") or "")
            item = by_path.setdefault(
                path,
                {
                    "path": path,
                    "asset_version": event.get("asset_version"),
                    "stages": {stage: False for stage in stages},
                    "outcome": "unknown",
                    "contribution": "unknown",
                    "events": [],
                },
            )
            item["stages"][str(event.get("stage"))] = True
            item["asset_version"] = event.get("asset_version") or item.get("asset_version")
            if event.get("stage") == "outcome_attributed":
                item["outcome"] = event.get("outcome") or "unknown"
                item["contribution"] = event.get("contribution") or "unknown"
            item["events"].append(event)
        counts = {stage: sum(1 for item in by_path.values() if item["stages"].get(stage)) for stage in stages}
        base = max(1, counts["eligible"])
        attributed = counts["outcome_attributed"]
        positive = sum(1 for item in by_path.values() if item.get("contribution") == "positive")
        negative = sum(1 for item in by_path.values() if item.get("contribution") == "negative")
        return {
            "task_id": task_id,
            "stage_counts": counts,
            "metrics": {
                "activation_rate": round(counts["referenced"] / base, 4),
                "compliance_rate": round(counts["complied_with"] / base, 4),
                "attribution_coverage": round(attributed / base, 4),
                "positive_contribution_rate": round(positive / max(1, attributed), 4),
                "harmful_rate": round(negative / max(1, attributed), 4),
            },
            "assets": list(by_path.values()),
        }
    def record_feedback(self, feedback: dict[str, Any]) -> Path:
        self._init_db()
        feedback_id = str(feedback["feedback_id"])
        path = self.paths.feedback / f"{feedback_id}.json"
        write_json(path, feedback)
        append_jsonl(self.paths.logs / "feedback.jsonl", {"event": "feedback_recorded", **feedback})
        target_type = str(feedback.get("target_type") or "")
        target_id = str(feedback.get("target_id") or "")
        sentiment = str(feedback.get("sentiment") or "neutral")
        if target_type == "asset":
            self._apply_asset_feedback(target_id, sentiment)
        elif target_type == "proposal":
            self._apply_proposal_feedback(target_id, feedback)
        elif target_type == "pattern":
            self._apply_pattern_feedback(target_id, feedback)
        return path
    def list_feedback(self, *, target_type: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.paths.feedback.exists():
            return rows
        for path in sorted(self.paths.feedback.glob("*.json")):
            item = read_json(path, {})
            if not isinstance(item, dict):
                continue
            if target_type and item.get("target_type") != target_type:
                continue
            if target_id and item.get("target_id") != target_id:
                continue
            rows.append(item)
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows
    def feedback_reward_for(self, target_type: str, target_id: str) -> dict[str, Any]:
        return feedback_reward(self.list_feedback(target_type=target_type, target_id=target_id))
    def _apply_asset_feedback(self, target_id: str, sentiment: str) -> None:
        normalized = target_id if target_id.startswith(f"{PRAXILE_DIR}/") else f"{PRAXILE_DIR}/{target_id}"
        now = utc_now()
        with self._connection() as conn:
            if sentiment == "positive":
                conn.execute(
                    """
                    UPDATE assets
                    SET positive_outcome_count = positive_outcome_count + 1, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, normalized),
                )
            elif sentiment == "negative":
                conn.execute(
                    """
                    UPDATE assets
                    SET negative_outcome_count = negative_outcome_count + 1, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, normalized),
                )
    def _apply_proposal_feedback(self, target_id: str, feedback: dict[str, Any]) -> None:
        proposal = self.find_proposal(target_id)
        if not proposal:
            return
        counts = proposal.get("user_feedback") if isinstance(proposal.get("user_feedback"), dict) else {}
        sentiment = str(feedback.get("sentiment") or "neutral")
        if sentiment == "positive":
            counts["positive_count"] = int(counts.get("positive_count") or 0) + 1
            proposal["confidence"] = min(1.0, float(proposal.get("confidence") or 0.5) + 0.08)
        elif sentiment == "negative":
            counts["negative_count"] = int(counts.get("negative_count") or 0) + 1
            proposal["confidence"] = max(0.0, float(proposal.get("confidence") or 0.5) - 0.16)
            proposal.setdefault("feedback_influence", []).append(
                {
                    "type": "user_negative_feedback",
                    "feedback_id": feedback.get("feedback_id"),
                    "effect": "lowered_confidence_and_recommended_reject_or_edit",
                }
            )
            proposal["recommended_action_override"] = "reject_or_edit"
        else:
            counts["neutral_count"] = int(counts.get("neutral_count") or 0) + 1
        counts["last_feedback_id"] = feedback.get("feedback_id")
        counts["last_user_feedback_at"] = feedback.get("created_at") or utc_now()
        proposal["user_feedback"] = counts
        self.write_proposal(proposal)
    def _apply_pattern_feedback(self, target_id: str, feedback: dict[str, Any]) -> None:
        candidates = []
        raw = str(target_id or "").removeprefix(f"{PRAXILE_DIR}/")
        if raw:
            candidates.append(raw)
        if raw and not raw.endswith(".md"):
            candidates.append(f"experience/patterns/{raw}.md")
        sentiment = str(feedback.get("sentiment") or "neutral")
        for candidate in candidates:
            try:
                target = self._resolve_proposal_target(candidate)
            except PermissionError:
                continue
            if not target.exists():
                continue
            sidecar = self._asset_metadata_sidecar(target)
            current = read_json(sidecar, {}) if sidecar.exists() else {}
            if not isinstance(current, dict):
                current = {}
            positive = int(current.get("positive_feedback_count") or 0)
            negative = int(current.get("negative_feedback_count") or 0)
            if sentiment == "positive":
                positive += 1
            elif sentiment == "negative":
                negative += 1
            latest = current.get("latest_feedback") if isinstance(current.get("latest_feedback"), list) else []
            latest.append(
                {
                    "feedback_id": feedback.get("feedback_id"),
                    "sentiment": sentiment,
                    "raw_text": feedback.get("raw_text"),
                    "created_at": feedback.get("created_at") or utc_now(),
                }
            )
            current.update(
                {
                    "positive_feedback_count": positive,
                    "negative_feedback_count": negative,
                    "latest_feedback": latest[-10:],
                    "confidence_adjustment_from_feedback": round(min(0.18, positive * 0.04) - min(0.30, negative * 0.08), 4),
                    "updated_at": utc_now(),
                }
            )
            write_json(sidecar, current)
            self.index_asset(target)
            return
    def usage_for_task(self, task_id: str) -> list[dict[str, Any]]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM asset_usage
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["matched_terms"] = _decode_json_list(item.get("matched_terms"))
            item["matched_fields"] = _decode_json_list(item.get("matched_fields"))
            item["used_in_prompt"] = bool(item.get("used_in_prompt"))
            item["referenced"] = bool(item.get("referenced"))
            item["used_explicitly"] = bool(item.get("used_explicitly"))
            item["semantic_attribution"] = _decode_json_dict(item.get("semantic_attribution"))
            item["attribution_level"] = (
                _normalize_attribution_level(item["semantic_attribution"].get("attribution_level"))
                if isinstance(item.get("semantic_attribution"), dict)
                and item["semantic_attribution"].get("semantic_judge", {}).get("active")
                else _usage_attribution_level(item)
            )
            results.append(item)
        return results
    def attribution_history_for_asset(self, path: str, *, limit: int = 10) -> list[dict[str, Any]]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT task_id, outcome, referenced, used_explicitly, semantic_attribution, created_at, updated_at
                FROM asset_usage
                WHERE path = ? AND semantic_attribution IS NOT NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (path, max(1, int(limit))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["referenced"] = bool(item.get("referenced"))
            item["used_explicitly"] = bool(item.get("used_explicitly"))
            item["semantic_attribution"] = _decode_json_dict(item.get("semantic_attribution"))
            result.append(item)
        return result



class FeedbackRepository(StoreRepository):
    def record(self, feedback: dict[str, Any]):
        return self.store.record_feedback(feedback)

    def list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.list_feedback(**kwargs)


def _activation_contribution(level: object) -> str:
    normalized = _normalize_attribution_level(level)
    if normalized in {"weak_positive", "strong_positive"}:
        return "positive"
    if normalized in {"weak_negative", "harmful"}:
        return "negative"
    if normalized == "neutral":
        return "neutral"
    return "unknown"


def _decode_activation_event(item: dict[str, Any]) -> dict[str, Any]:
    item["evidence"] = _decode_json_value(item.get("evidence"), {})
    item["metadata"] = _decode_json_value(item.get("metadata"), {})
    return item


def _decode_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
