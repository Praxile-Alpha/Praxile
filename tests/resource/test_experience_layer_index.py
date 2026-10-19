import json

import pytest

from praxile.config import Config
from praxile.store import ExperienceStore

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


def asset(store, config, layer):
    path = config.paths.state / "memory" / f"layer-{layer}.md"
    path.write_text("# Parser\nparser regression tests and investigation\n")
    path.with_name(path.name + ".meta.json").write_text(json.dumps({"experience_layer": layer}))
    store.index_asset(path)
    return path


@pytest.mark.parametrize("mode", ["fts", "fallback", "vector", "hybrid"])
def test_layer_filter_is_applied_before_retrieval_limit(tmp_path, monkeypatch, mode):
    config = Config.load(tmp_path)
    config.data["retrieval"].update(vector_enabled=mode in {"vector", "hybrid"},
                                    hybrid_enabled=mode == "hybrid", vector_min_score=-1)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    for layer in ("strategic", "procedural", "tool"):
        asset(store, config, layer)
    if mode == "fallback":
        monkeypatch.setattr(store, "_fts_available", lambda conn: False)
    matches = store.retrieve("parser regression", layers=["tool"], limit=1)
    assert len(matches) == 1
    assert matches[0]["path"].endswith("layer-tool.md")
    with pytest.raises(ValueError):
        store.retrieve("parser", layers=["invalid"])


def test_layer_migration_preserves_usage_and_explicit_reindex_fills_column(tmp_path):
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    path = asset(store, config, "tool")
    relative = path.relative_to(tmp_path).as_posix()
    with store._connection() as conn:
        conn.execute("UPDATE assets SET usage_count=7 WHERE path=?", (relative,))
        conn.execute("DROP INDEX assets_layer_status_idx")
        conn.execute("ALTER TABLE assets DROP COLUMN experience_layer")
    store._init_db()
    with store._connection() as conn:
        row = conn.execute("SELECT * FROM assets WHERE path=?", (relative,)).fetchone()
        assert row["usage_count"] == 7
        assert row["experience_layer"] is None
    store.reindex_all()
    with store._connection() as conn:
        row = conn.execute("SELECT * FROM assets WHERE path=?", (relative,)).fetchone()
        assert row["usage_count"] == 7
        assert row["experience_layer"] == "tool"
    assert store.retrieve("parser", layers=["tool"])


def test_approved_metadata_validation_rejects_unknown_layer(tmp_path):
    store = ExperienceStore(Config.load(tmp_path).paths)
    with pytest.raises(ValueError, match="experience_layer"):
        store._normalized_lifecycle_metadata({"experience_layer": "system_override"})
