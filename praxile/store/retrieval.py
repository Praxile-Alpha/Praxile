from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class RetrievalStoreMixin:
    def retrieve(self, query: str, *, kinds: list[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
        self._init_db()
        settings = vector_settings(self.config)
        try:
            with self._connection() as conn:
                if settings["enabled"] and settings["hybrid_enabled"]:
                    keyword = self._retrieve_fts(conn, query, kinds=kinds, limit=limit * 2)
                    vector = self._retrieve_vector(conn, query, kinds=kinds, limit=limit * 2)
                    return self._merge_retrieval_results(keyword, vector, limit=limit)
                if settings["enabled"]:
                    vector = self._retrieve_vector(conn, query, kinds=kinds, limit=limit)
                    if vector:
                        return vector
                return self._retrieve_fts(conn, query, kinds=kinds, limit=limit)
        except sqlite3.Error:
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)
    def _retrieve_vector(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        kinds: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        settings = vector_settings(self.config)
        try:
            query_vector = embed_text(
                query,
                provider=settings["provider"],
                model=settings.get("model"),
                dims=int(settings.get("dims") or 256),
            )
        except Exception:
            return []
        filters = _asset_type_filter(kinds)
        params: list[Any] = [settings["provider"], int(settings.get("dims") or 256)]
        where = "WHERE v.provider = ? AND v.dims = ? AND a.status = 'active'"
        model = settings.get("model")
        if model is None:
            where += " AND v.model IS NULL"
        else:
            where += " AND v.model = ?"
            params.append(model)
        if filters:
            where += " AND a.type IN (%s)" % ",".join("?" for _ in filters)
            params.extend(filters)
        rows = conn.execute(
            f"""
            SELECT
              a.path, a.type, a.title, a.summary, a.tags, a.source_task_id, a.confidence,
              a.usage_count, a.positive_outcome_count, a.negative_outcome_count, a.last_used_at,
              v.vector_json
            FROM asset_vectors v
            JOIN assets a ON a.path = v.path
            {where}
            """,
            params,
        ).fetchall()
        results: list[dict[str, Any]] = []
        min_score = float(self.config.get("retrieval", "vector_min_score", default=0.05) if self.config else 0.05)
        for row in rows:
            try:
                vector = json.loads(row["vector_json"])
            except json.JSONDecodeError:
                continue
            vector_score = cosine_similarity(query_vector, [float(value) for value in vector])
            if vector_score < min_score:
                continue
            asset_type = row["type"]
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            priority_boost = round((9 - priority) * self._retrieval_weight("vector_priority_weight", 0.03), 4)
            usage = self._usage_adjustment(row)
            base_score = vector_score + priority_boost
            score = round(max(0.0, base_score + usage["score_impact"]), 4)
            explanation = _retrieval_explanation(
                query,
                {
                    "path": row["path"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "content": row["summary"],
                },
                mode="vector",
                asset_type=asset_type,
            )
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "vector_score": round(vector_score, 4),
                    "fts_rank": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": score,
                    "retrieval_mode": "vector",
                    "matched_terms": explanation["matched_terms"],
                    "matched_fields": explanation["matched_fields"],
                    "why_loaded": explanation["why_loaded"],
                    "reason": f"Vector match in {asset_type}; provider={settings['provider']}; priority={priority}.",
                    "snippet": shorten(row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -item["score"], item["path"]))
        return results[:limit]
    def _merge_retrieval_results(
        self,
        keyword_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        rank_boost = self._retrieval_weight("hybrid_rank_boost", 1.0)
        for index, item in enumerate(keyword_results):
            copy = dict(item)
            copy["retrieval_mode"] = "hybrid_keyword"
            copy["hybrid_score"] = float(copy.get("score") or 0) + rank_boost / (index + 1)
            merged[copy["path"]] = copy
        for index, item in enumerate(vector_results):
            existing = merged.get(item["path"])
            boost = float(item.get("score") or 0) + rank_boost / (index + 1)
            if existing:
                existing["retrieval_mode"] = "hybrid"
                existing["vector_score"] = item.get("vector_score")
                existing["hybrid_score"] = float(existing.get("hybrid_score") or 0) + boost
                existing["final_score"] = round(float(existing.get("hybrid_score") or 0), 4)
                existing["reason"] = f"{existing.get('reason')} Vector corroborated the match."
                existing["why_loaded"] = f"{existing.get('why_loaded')} Vector similarity also matched."
            else:
                copy = dict(item)
                copy["retrieval_mode"] = "hybrid_vector"
                copy["hybrid_score"] = boost
                copy["final_score"] = round(float(boost), 4)
                merged[copy["path"]] = copy
        results = list(merged.values())
        results.sort(key=lambda item: (item["load_priority"], -float(item.get("hybrid_score") or 0), item["path"]))
        return results[:limit]
    def _retrieval_weight(self, key: str, default: float) -> float:
        try:
            return float(self.config.get("retrieval", key, default=default) if self.config else default)
        except (TypeError, ValueError):
            return default
    def _retrieve_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        kinds: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._fts_available(conn):
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)
        fts_query = _fts_query(query)
        params: list[Any] = []
        where = ""
        if fts_query:
            where = "WHERE assets_fts MATCH ? AND a.status = 'active'"
            params.append(fts_query)
        else:
            where = "WHERE a.status = 'active'"
        filters = _asset_type_filter(kinds)
        if filters:
            where += (" AND " if where else "WHERE ") + "a.type IN (%s)" % ",".join("?" for _ in filters)
            params.extend(filters)
        sql = f"""
            SELECT
              a.path, a.type, a.title, a.summary, a.tags, a.source_task_id, a.confidence,
              a.usage_count, a.positive_outcome_count, a.negative_outcome_count, a.last_used_at,
              bm25(assets_fts) AS rank,
              snippet(assets_fts, 2, '', '', ' ... ', 12) AS snippet
            FROM assets_fts
            JOIN assets a ON a.path = assets_fts.path
            {where}
        """
        rows = conn.execute(sql, params).fetchall()
        if not rows and fts_query:
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)
        results: list[dict[str, Any]] = []
        for row in rows:
            asset_type = row["type"]
            rank = float(row["rank"] or 0)
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            priority_boost = round((9 - priority) * self._retrieval_weight("keyword_priority_weight", 0.05), 4)
            usage = self._usage_adjustment(row)
            base_score = max(0.0, 1.0 / (1.0 + abs(rank))) + priority_boost
            score = round(max(0.0, base_score + usage["score_impact"]), 4)
            explanation = _retrieval_explanation(
                query,
                {
                    "path": row["path"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "content": row["snippet"] or row["summary"],
                },
                mode="fts",
                asset_type=asset_type,
            )
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "fts_rank": round(rank, 6),
                    "vector_score": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": score,
                    "matched_terms": explanation["matched_terms"],
                    "matched_fields": explanation["matched_fields"],
                    "why_loaded": explanation["why_loaded"],
                    "reason": f"FTS match in {asset_type}; priority={priority}.",
                    "snippet": shorten(row["snippet"] or row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -item["score"], item["path"]))
        return results[:limit]
    def _retrieve_fallback(self, query: str, *, kinds: list[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
        filters = _asset_type_filter(kinds)
        with self._connection() as conn:
            if filters:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE status = 'active' AND type IN (%s)" % ",".join("?" for _ in filters),
                    filters,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM assets WHERE status = 'active'").fetchall()
        words = [word.lower() for word in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(word) > 1]
        results: list[dict[str, Any]] = []
        for row in rows:
            haystack = f"{row['path']} {row['title']} {row['summary']} {row['tags']}".lower()
            score = sum(haystack.count(word) for word in words) if words else 1
            if score <= 0:
                continue
            asset_type = row["type"]
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            usage = self._usage_adjustment(row)
            priority_boost = round((9 - priority) * self._retrieval_weight("keyword_priority_weight", 0.05), 4)
            final_score = round(max(0.0, float(score) + priority_boost + usage["score_impact"]), 4)
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "fts_rank": None,
                    "vector_score": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": final_score,
                    "matched_terms": [word for word in words if word in haystack],
                    "matched_fields": ["metadata"],
                    "why_loaded": "Loaded by fallback metadata term matching.",
                    "reason": "Fallback metadata match.",
                    "snippet": shorten(row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -float(item.get("final_score") or item["score"]), item["path"]))
        return results[:limit]
    def _usage_adjustment(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        usage_count = _int_from_row(row, "usage_count")
        positive = _int_from_row(row, "positive_outcome_count")
        negative = _int_from_row(row, "negative_outcome_count")
        last_used_at = _value_from_row(row, "last_used_at")
        usage_score = round(math.log1p(max(0, usage_count)) * self._retrieval_weight("usage_log_weight", 0.02), 4)
        positive_boost = round(max(0, positive) * self._retrieval_weight("positive_outcome_weight", 0.10), 4)
        negative_penalty = round(max(0, negative) * self._retrieval_weight("negative_outcome_weight", 0.20), 4)
        staleness_penalty = 0.0
        if usage_count > 0 and last_used_at:
            parsed = _parse_iso_datetime(last_used_at)
            stale_days = int(self.config.get("retrieval", "stale_usage_days", default=90) if self.config else 90)
            if parsed and parsed <= datetime.now(timezone.utc) - timedelta(days=stale_days):
                staleness_penalty = round(self._retrieval_weight("stale_usage_penalty", 0.10), 4)
        score_impact = round(usage_score + positive_boost - negative_penalty - staleness_penalty, 4)
        return {
            "usage_count": usage_count,
            "positive_outcome_count": positive,
            "negative_outcome_count": negative,
            "last_used_at": last_used_at,
            "usage_score": usage_score,
            "positive_boost": positive_boost,
            "negative_penalty": negative_penalty,
            "staleness_penalty": staleness_penalty,
            "score_impact": score_impact,
        }



class RetrievalRepository(StoreRepository):
    def retrieve(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.retrieve(query, **kwargs)
