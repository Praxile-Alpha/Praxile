from __future__ import annotations

from praxile.eval.v2.public import _PUBLIC_EVENT_TYPES, _public_run_metrics, _redact


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


def test_public_evidence_includes_stop_decisions_and_diff_scope() -> None:
    report = {
        "eval_run_id": "run-1",
        "metrics": {},
        "tasks": [
            {
                "task_id": "task-1",
                "status": "completed",
                "diff_scope": {
                    "status": "review_required",
                    "reasons": ["changed_lines=401 exceeds 400"],
                },
                "context_activation": {"status": "abstained", "activated": False},
            }
        ],
    }

    public = _public_run_metrics(report, [])

    assert "STOP_DECISION" in _PUBLIC_EVENT_TYPES
    assert "CONTEXT_ACTIVATION" in _PUBLIC_EVENT_TYPES
    assert public["tasks"][0]["diff_scope"]["status"] == "review_required"
    assert public["tasks"][0]["context_activation"]["status"] == "abstained"
