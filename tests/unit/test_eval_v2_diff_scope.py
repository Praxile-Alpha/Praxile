from __future__ import annotations

from praxile.eval.v2 import (
    EvalTask,
    RepositorySpec,
    SWEbenchEvaluationSpec,
    analyze_diff_scope,
)


def _task() -> EvalTask:
    return EvalTask(
        task_id="sympy__sympy-20590",
        instruction="Fix comparison",
        repository=RepositorySpec(
            repo="sympy/sympy",
            base_commit="abc123",
            clone_url="https://github.com/sympy/sympy.git",
        ),
        evaluation=SWEbenchEvaluationSpec(dataset_name="princeton-nlp/SWE-bench", split="test"),
    )


def _patch(*paths: str) -> str:
    return "".join(
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
        for path in paths
    )


def test_source_and_colocated_test_pass_minimality_gate() -> None:
    result = analyze_diff_scope(
        _task(),
        _patch("sympy/core/basic.py", "sympy/core/tests/test_basic.py"),
    )

    assert result["status"] == "passed"
    assert result["cross_scope_test_files"] == []
    assert result["changed_files"] == ["sympy/core/basic.py", "sympy/core/tests/test_basic.py"]


def test_unrelated_test_component_requires_review() -> None:
    result = analyze_diff_scope(
        _task(),
        _patch(
            "sympy/core/basic.py",
            "sympy/core/tests/test_basic.py",
            "sympy/codegen/tests/test_cnodes.py",
        ),
    )

    assert result["status"] == "review_required"
    assert result["cross_scope_test_files"] == ["sympy/codegen/tests/test_cnodes.py"]
    assert "test changes extend beyond" in result["reasons"][0]


def test_file_and_line_limits_are_explicit_policy() -> None:
    result = analyze_diff_scope(
        _task(),
        _patch("a.py", "b.py"),
        {"max_files_changed": 1, "max_changed_lines": 2},
    )

    assert result["status"] == "review_required"
    assert len(result["reasons"]) == 2
