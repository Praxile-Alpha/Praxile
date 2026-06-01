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
                    if attribution and not _attribution_allows_outcome_update(attribution, success=True):
                        continue
                    if not attribution and not (int(row["referenced"] or 0) or int(row["used_explicitly"] or 0)):
                        continue
                    conn.execute(
                        "UPDATE assets SET positive_outcome_count = positive_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )
            elif normalized == "failed":
                for row in rows:
                    attribution = attribution_by_path.get(str(row["path"]))
                    if attribution and not _attribution_allows_outcome_update(attribution, success=False):
                        continue
                    if not attribution and not (int(row["referenced"] or 0) or int(row["used_explicitly"] or 0)):
                        continue
                    conn.execute(
                        "UPDATE assets SET negative_outcome_count = negative_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )
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
