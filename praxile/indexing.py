from __future__ import annotations

from pathlib import Path
from typing import Any

from .retrieval import HybridRetriever, experience_docs


def build_experience_index(state_root: Path, *, db_dir: Path | None = None, **retriever_options: Any) -> dict[str, Any]:
    """Build the standalone retrieval index for `.praxile/` experience assets."""

    target_db = db_dir or (state_root / "db" / "hybrid")
    retriever = HybridRetriever(target_db, **retriever_options)
    return retriever.build_index(experience_docs(state_root))
