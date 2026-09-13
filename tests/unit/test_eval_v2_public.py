from __future__ import annotations

from praxile.eval.v2.public import _redact


def test_public_redaction_preserves_usage_metrics_and_removes_sensitive_values() -> None:
    value = _redact(
        {
            "token_usage": {"input": 12, "output": 3},
            "token_delta": -4,
            "api_key": "secret-value",
            "message": "failure under /Users/example/private/repository/file.py",
            "url": "https://github.com/Praxile-Alpha/Praxile",
        }
    )

    assert value["token_usage"] == {"input": 12, "output": 3}
    assert value["token_delta"] == -4
    assert value["api_key"] == "<redacted:credential>"
    assert value["message"] == "failure under <redacted:absolute-path>"
    assert value["url"] == "https://github.com/Praxile-Alpha/Praxile"
