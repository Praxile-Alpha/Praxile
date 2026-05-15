from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .vector import cosine_similarity, embed_text


class HybridRetriever:
    """Small local vector retriever for governed Praxile experience docs."""

    def __init__(
        self,
        db_dir: Path,
        *,
        provider: str = "local_hash",
        model_name: str | None = None,
        dims: int = 256,
    ):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.model_name = model_name
        self.dims = dims
        self.embedding_path = self.db_dir / "embeddings.json"
        self.doc_path = self.db_dir / "documents.jsonl"

    def build_index(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        normalized_docs = [_normalize_doc(doc, index) for index, doc in enumerate(docs)]
        vectors = [
            embed_text(
                doc["content"],
                provider=self.provider,
                model=self.model_name,
                dims=self.dims,
            )
            for doc in normalized_docs
        ]
        self.embedding_path.write_text(json.dumps(vectors, ensure_ascii=False), encoding="utf-8")
        with self.doc_path.open("w", encoding="utf-8") as handle:
            for doc in normalized_docs:
                handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
        return {"documents": len(normalized_docs), "embedding_path": str(self.embedding_path), "doc_path": str(self.doc_path)}

    def build_from_state(self, state_root: Path) -> dict[str, Any]:
        docs = experience_docs(state_root)
        return self.build_index(docs)

    def vector_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not self.embedding_path.exists() or not self.doc_path.exists():
            return []
        try:
            vectors = json.loads(self.embedding_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        docs: list[dict[str, Any]] = []
        try:
            for line in self.doc_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        docs.append(parsed)
        except (OSError, json.JSONDecodeError):
            return []
        if not vectors or not docs:
            return []
        query_vector = embed_text(query, provider=self.provider, model=self.model_name, dims=self.dims)
        scored: list[dict[str, Any]] = []
        for index, doc in enumerate(docs[: len(vectors)]):
            vector = vectors[index]
            if not isinstance(vector, list):
                continue
            score = cosine_similarity(query_vector, [float(value) for value in vector])
            scored.append({"score": round(float(score), 4), "doc": doc})
        scored.sort(key=lambda item: (-float(item["score"]), str(item["doc"].get("path") or "")))
        return scored[: max(1, int(top_k or 5))]


def experience_docs(state_root: Path) -> list[dict[str, Any]]:
    roots = [
        state_root / "experience",
        state_root / "rules",
        state_root / "skills",
        state_root / "memory",
    ]
    docs: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".json"}:
                continue
            if any(part in {"db", "cache", "snapshots", "__pycache__"} for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(state_root).as_posix()
            docs.append({"id": rel, "path": f".praxile/{rel}", "content": content})
    return docs


def _normalize_doc(doc: dict[str, Any], index: int) -> dict[str, Any]:
    content = str(doc.get("content") or doc.get("text") or "")
    path = str(doc.get("path") or doc.get("id") or f"doc-{index}")
    return {
        "id": str(doc.get("id") or path),
        "path": path,
        "title": str(doc.get("title") or Path(path).name),
        "content": content,
        "metadata": doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {},
    }
