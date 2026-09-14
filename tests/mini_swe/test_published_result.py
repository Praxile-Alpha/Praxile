from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.mini_swe

RESULT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "mini_swe"
    / "P1_MINIMAX_M3_HELDOUT5_STOPPING_V1"
)


def test_published_result_is_inert_redacted_evidence() -> None:
    manifest = json.loads(
        (RESULT_ROOT / "experiment-manifest.json").read_text(encoding="utf-8")
    )
    metrics = json.loads((RESULT_ROOT / "raw-metrics.json").read_text(encoding="utf-8"))
    evidence = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(RESULT_ROOT.iterdir()) if path.is_file()
    )

    assert manifest["schema_version"] == "praxile.public_experiment_manifest.v1"
    assert manifest["redaction"] == {
        "absolute_paths": True,
        "credentials": True,
        "task_instruction": True,
        "context_content": True,
        "native_payload_refs": True,
    }
    assert metrics["comparison"]["decision"] == "inconclusive"
    assert metrics["baseline"]["metrics"]["task_count"] == 5
    assert metrics["candidate"]["metrics"]["task_count"] == 5
    assert "/Users/" not in evidence
    assert "sk-cp-" not in evidence
