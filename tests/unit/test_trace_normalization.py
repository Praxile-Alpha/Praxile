from __future__ import annotations

import json

from praxile.adapters.trace_normalization import bounded_native_message


def test_raw_output_is_replaced_by_bounded_metadata() -> None:
    raw_output = "begin\n" + ("x" * 100_000) + "\nend"

    result = bounded_native_message(
        {"role": "tool", "content": "short", "extra": {"raw_output": raw_output}},
        raw_output_limit=1_000,
    )

    assert "raw_output" not in result["extra"]
    metadata = result["extra"]["raw_output_meta"]
    assert metadata["truncated"] is True
    assert metadata["original_chars"] == len(raw_output)
    assert len(metadata["preview"]) <= 1_000
    assert metadata["full_content_location"] == "native_trajectory_artifact"
    assert len(json.dumps(result)) < 2_000


def test_other_large_native_strings_are_bounded() -> None:
    result = bounded_native_message({"content": "y" * 50_000}, string_limit=2_000)

    assert len(result["content"]) <= 2_000
    assert "truncated" in result["content"]
