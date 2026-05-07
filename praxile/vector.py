from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Any


DEFAULT_VECTOR_DIMS = 256


def embed_text(
    text: str,
    *,
    provider: str = "local_hash",
    model: str | None = None,
    dims: int = DEFAULT_VECTOR_DIMS,
) -> list[float]:
    provider = (provider or "local_hash").lower()
    if provider in {"sentence_transformers", "sentence-transformers"}:
        return _sentence_transformers_embedding(text, model=model)
    return _local_hash_embedding(text, dims=max(16, min(int(dims or DEFAULT_VECTOR_DIMS), 2048)))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def vector_settings(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {
            "provider": "local_hash",
            "model": None,
            "dims": DEFAULT_VECTOR_DIMS,
            "enabled": False,
            "hybrid_enabled": False,
        }
    provider = config.get("retrieval", "vector_provider", default=None)
    if provider is None:
        provider = config.get("retrieval", "embedding_provider", default="local_hash")
    return {
        "provider": provider,
        "model": config.get("retrieval", "embedding_model", default=None),
        "dims": int(config.get("retrieval", "vector_dims", default=DEFAULT_VECTOR_DIMS)),
        "enabled": bool(config.get("retrieval", "vector_enabled", default=False)),
        "hybrid_enabled": bool(config.get("retrieval", "hybrid_enabled", default=False)),
    }


def _local_hash_embedding(text: str, *, dims: int) -> list[float]:
    vector = [0.0] * dims
    tokens = _tokens(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = -1.0 if digest[4] & 1 else 1.0
        weight = 1.0 + min(3, len(token)) * 0.1
        vector[bucket] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 8) for value in vector]


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_]{2,}", lowered)
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    chinese_bigrams = ["".join(chinese_chars[index : index + 2]) for index in range(max(0, len(chinese_chars) - 1))]
    return latin + chinese_chars + chinese_bigrams


def _sentence_transformers_embedding(text: str, *, model: str | None) -> list[float]:
    encoder = _sentence_transformer(model or "sentence-transformers/all-MiniLM-L6-v2")
    values = encoder.encode([text[:8000]], normalize_embeddings=True)[0]
    return [float(value) for value in values]


@lru_cache(maxsize=2)
def _sentence_transformer(model: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("sentence-transformers is not installed; install praxile[vector].") from exc
    return SentenceTransformer(model)
