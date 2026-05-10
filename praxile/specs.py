from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import path_is_relative_to, shorten


SPEC_CANDIDATES = [
    "spec.md",
    "plan.md",
    "tasks.md",
    "constitution.md",
    ".specify/spec.md",
    ".specify/plan.md",
    ".specify/tasks.md",
]

SECTION_ALIASES = {
    "problem_statement": ["problem statement", "problem", "背景", "问题", "目标"],
    "success_metrics": ["success metrics", "success metric", "metrics", "成功标准", "指标"],
    "user_stories": ["user stories", "user story", "用户故事", "使用场景"],
    "acceptance_criteria": ["acceptance criteria", "acceptance", "验收标准", "验收项"],
    "non_goals": ["non-goals", "non goals", "out of scope", "不做什么", "非目标", "范围外"],
    "constraints": ["constraints", "constraint", "约束", "限制"],
}

REQUIRED_SECTIONS = [
    "problem_statement",
    "success_metrics",
    "user_stories",
    "acceptance_criteria",
    "non_goals",
    "constraints",
]

MEASURABLE_RE = re.compile(r"(\d+|p95|p99|latency|ms|seconds?|秒|%|percent|coverage|覆盖|throughput|qps)", re.I)
HOW_RE = re.compile(
    r"\b(use|using|implement with|build with|采用|使用|基于)\s+"
    r"(redis|postgres|mysql|sqlite|mongodb|kafka|rabbitmq|casbin|react|vue|fastapi|django|express|zset)\b",
    re.I,
)
TECH_TERMS = {
    "redis",
    "postgres",
    "postgresql",
    "mysql",
    "sqlite",
    "mongodb",
    "mongo",
    "kafka",
    "rabbitmq",
    "casbin",
    "react",
    "vue",
    "fastapi",
    "django",
    "express",
    "zset",
    "semantic search",
    "vector",
    "embedding",
}


def build_spec_context(project_root: Path, explicit_specs: list[str] | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    explicit_specs = explicit_specs or []
    spec_files = _resolve_explicit_specs(root, explicit_specs) if explicit_specs else _discover_specs(root)
    plan_files = [path for path in spec_files if path.name.lower() == "plan.md"]
    task_files = [path for path in spec_files if path.name.lower() == "tasks.md"]
    constitution_files = _discover_constitutions(root)
    analyzed_specs = [_analyze_markdown(path, root) for path in spec_files if path.exists()]
    merged = _merge_spec_analyses(analyzed_specs)
    constitution_rel = [_rel(path, root) for path in constitution_files]
    return {
        "enabled": bool(spec_files or constitution_files),
        "spec_files": [_rel(path, root) for path in spec_files],
        "plan_files": [_rel(path, root) for path in plan_files],
        "task_files": [_rel(path, root) for path in task_files],
        "constitution_files": constitution_rel,
        "quality_score": merged["quality_score"],
        "quality_label": _quality_label(merged["quality_score"]),
        "missing_sections": merged["missing_sections"],
        "present_sections": merged["present_sections"],
        "success_metrics": merged["success_metrics"],
        "acceptance_criteria": merged["acceptance_criteria"],
        "non_goals": merged["non_goals"],
        "constraints": merged["constraints"],
        "risks": merged["risks"],
        "suggested_actions": merged["suggested_actions"],
        "files": analyzed_specs,
    }


def check_spec_file(project_root: Path, spec_path: str | None = None) -> dict[str, Any]:
    context = build_spec_context(project_root, [spec_path] if spec_path else None)
    return {
        "quality_score": context["quality_score"],
        "quality_label": context["quality_label"],
        "spec_files": context["spec_files"],
        "constitution_files": context["constitution_files"],
        "missing_sections": context["missing_sections"],
        "present_sections": context["present_sections"],
        "success_metrics": context["success_metrics"],
        "acceptance_criteria": context["acceptance_criteria"],
        "non_goals": context["non_goals"],
        "constraints": context["constraints"],
        "risks": context["risks"],
        "suggested_actions": context["suggested_actions"],
    }


def format_spec_check(report: dict[str, Any]) -> str:
    lines = [f"Spec quality: {report.get('quality_label', 'unknown')} ({report.get('quality_score', 0)})"]
    if report.get("spec_files"):
        lines.append("\nFiles:")
        lines.extend(f"- {item}" for item in report["spec_files"])
    else:
        lines.append("\nFiles: none detected")
    if report.get("missing_sections"):
        lines.append("\nMissing:")
        lines.extend(f"- {_section_title(item)}" for item in report["missing_sections"])
    if report.get("risks"):
        lines.append("\nRisks:")
        lines.extend(f"- {item}" for item in report["risks"])
    if report.get("suggested_actions"):
        lines.append("\nSuggested actions:")
        lines.extend(f"- {item}" for item in report["suggested_actions"])
    if report.get("acceptance_criteria"):
        lines.append("\nAcceptance criteria:")
        lines.extend(f"- {item}" for item in report["acceptance_criteria"][:8])
    return "\n".join(lines)


def verify_spec_compliance(
    project_root: Path,
    trajectory: dict[str, Any],
    explicit_specs: list[str] | None = None,
) -> dict[str, Any]:
    spec_files = explicit_specs if explicit_specs is not None else (trajectory.get("spec_context") or {}).get("spec_files") or None
    context = build_spec_context(project_root, spec_files)
    corpus = _trajectory_implementation_corpus(trajectory)
    acceptance = context.get("acceptance_criteria") or []
    metrics = context.get("success_metrics") or []
    non_goals = context.get("non_goals") or []
    constraints = context.get("constraints") or []
    satisfied: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in acceptance:
        match = _requirement_match(item, corpus)
        record = {
            "type": "acceptance_criteria",
            "text": item,
            "evidence": match["evidence"],
            "score": match["score"],
        }
        if match["satisfied"]:
            satisfied.append(record)
        else:
            missing.append(record)

    metric_coverage: list[dict[str, Any]] = []
    for item in metrics:
        match = _metric_match(item, corpus)
        metric_coverage.append(
            {
                "metric": item,
                "covered": match["satisfied"],
                "evidence": match["evidence"],
                "score": match["score"],
            }
        )

    violations: list[dict[str, Any]] = []
    for item in non_goals:
        match = _forbidden_match(item, corpus)
        if match["violated"]:
            violations.append(
                {
                    "type": "non_goal",
                    "text": item,
                    "evidence": match["evidence"],
                    "score": match["score"],
                }
            )
    for item in constraints:
        for match in _constraint_violations(item, corpus):
            violations.append(
                {
                    "type": "constraint",
                    "text": item,
                    "evidence": match["evidence"],
                    "score": match["score"],
                }
            )

    metric_missing = [item for item in metric_coverage if not item["covered"]]
    acceptance_total = max(1, len(acceptance))
    acceptance_score = len(satisfied) / acceptance_total if acceptance else 0.0
    metric_score = (len(metric_coverage) - len(metric_missing)) / max(1, len(metric_coverage)) if metric_coverage else 0.0
    penalty = min(0.5, len(violations) * 0.2)
    score = round(max(0.0, min(1.0, acceptance_score * 0.65 + metric_score * 0.25 + context.get("quality_score", 0.0) * 0.1 - penalty)), 3)
    if not context.get("spec_files"):
        status = "unknown"
    elif violations:
        status = "failed" if score < 0.25 else "partial"
    elif missing or metric_missing:
        status = "partial"
    else:
        status = "full"

    suggestions: list[str] = []
    if missing:
        suggestions.append("Add or inspect implementation/test work for missing acceptance criteria.")
    if metric_missing:
        suggestions.append("Add verification for unproven success metrics.")
    if violations:
        suggestions.append("Inspect the implementation and either revert the violating change or explicitly update the spec.")
    if not suggestions and status == "full":
        suggestions.append("Keep the run as evidence for this spec-backed task.")
    reverse_spec_update_needed = bool(violations) or bool(context.get("missing_sections") and trajectory.get("diff_summary", {}).get("changed_files"))
    return {
        "schema_version": 1,
        "task_id": trajectory.get("task_id"),
        "status": status,
        "score": score,
        "spec_files": context.get("spec_files") or [],
        "spec_quality": {
            "label": context.get("quality_label"),
            "score": context.get("quality_score"),
            "missing_sections": context.get("missing_sections") or [],
        },
        "satisfied": satisfied,
        "missing": missing,
        "violations": violations,
        "success_metric_coverage": metric_coverage,
        "reverse_spec_update_needed": reverse_spec_update_needed,
        "suggested_proposals": suggestions,
        "notes": _compliance_notes(context, trajectory),
    }


def format_spec_compliance(report: dict[str, Any]) -> str:
    lines = [f"Spec compliance: {report.get('status', 'unknown')} ({report.get('score', 0)})"]
    if report.get("spec_files"):
        lines.append("\nSpec files:")
        lines.extend(f"- {item}" for item in report["spec_files"])
    else:
        lines.append("\nSpec files: none")
    for title, key in [("Satisfied", "satisfied"), ("Missing", "missing"), ("Violations", "violations")]:
        values = report.get(key) or []
        if not values:
            continue
        lines.append(f"\n{title}:")
        for item in values[:12]:
            prefix = item.get("type", "item")
            lines.append(f"- [{prefix}] {item.get('text')}")
            if item.get("evidence"):
                lines.append(f"  evidence: {item.get('evidence')}")
    metrics = report.get("success_metric_coverage") or []
    if metrics:
        lines.append("\nSuccess metrics:")
        for item in metrics[:12]:
            state = "covered" if item.get("covered") else "missing"
            lines.append(f"- [{state}] {item.get('metric')}")
    if report.get("reverse_spec_update_needed"):
        lines.append("\nReverse spec update: likely needed")
    if report.get("suggested_proposals"):
        lines.append("\nSuggested proposal:")
        lines.extend(f"- {item}" for item in report["suggested_proposals"])
    return "\n".join(lines)


def spec_context_prompt(context: dict[str, Any]) -> str:
    if not context or not context.get("enabled"):
        return "(none)"
    parts = [
        f"quality={context.get('quality_label')} score={context.get('quality_score')}",
        "spec_files=" + (", ".join(context.get("spec_files") or []) or "none"),
        "constitution_files=" + (", ".join(context.get("constitution_files") or []) or "none"),
    ]
    if context.get("missing_sections"):
        parts.append("missing=" + ", ".join(context["missing_sections"]))
    for key, label in [
        ("acceptance_criteria", "Acceptance criteria"),
        ("constraints", "Constraints"),
        ("non_goals", "Non-goals"),
    ]:
        values = context.get(key) or []
        if values:
            parts.append(label + ":\n" + "\n".join(f"- {shorten(str(value), 240)}" for value in values[:8]))
    return "\n\n".join(parts)


def _resolve_explicit_specs(root: Path, specs: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for item in specs:
        path = (root / item).resolve()
        if not path_is_relative_to(path, root):
            raise ValueError(f"spec path escapes project root: {item}")
        if not path.exists():
            raise FileNotFoundError(f"spec file not found: {item}")
        resolved.append(path)
    return _dedupe(resolved)


def _discover_specs(root: Path) -> list[Path]:
    found = [root / item for item in SPEC_CANDIDATES if (root / item).exists()]
    docs_specs = root / "docs" / "specs"
    if docs_specs.exists():
        found.extend(sorted(docs_specs.glob("*.md")))
    return _dedupe(path.resolve() for path in found if path.is_file())


def _discover_constitutions(root: Path) -> list[Path]:
    candidates = [
        root / ".praxile" / "constitution.md",
        root / "constitution.md",
        root / ".specify" / "constitution.md",
    ]
    return _dedupe(path.resolve() for path in candidates if path.exists() and path.is_file())


def _analyze_markdown(path: Path, root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    sections = _sections(text)
    found: dict[str, list[str]] = {}
    for key, aliases in SECTION_ALIASES.items():
        values: list[str] = []
        for title, body in sections.items():
            normalized = title.lower().strip()
            if any(alias in normalized for alias in aliases):
                values.extend(_section_items(body))
        if values:
            found[key] = values
    present = sorted(found)
    missing = [key for key in REQUIRED_SECTIONS if key not in found]
    risks: list[str] = []
    if "success_metrics" in found and not any(MEASURABLE_RE.search(item) for item in found["success_metrics"]):
        risks.append("Success Metrics exist but do not look measurable.")
    if "acceptance_criteria" in found and not found["acceptance_criteria"]:
        risks.append("Acceptance Criteria section exists but has no concrete checklist items.")
    searchable_text = "\n".join(body for title, body in sections.items() if "constraint" not in title.lower())
    if HOW_RE.search(searchable_text):
        risks.append("Spec may mix implementation HOW into WHAT; keep technical choices in Constraints or Plan.")
    score = _score(present, risks)
    return {
        "path": _rel(path, root),
        "quality_score": score,
        "present_sections": present,
        "missing_sections": missing,
        "success_metrics": found.get("success_metrics", []),
        "acceptance_criteria": found.get("acceptance_criteria", []),
        "non_goals": found.get("non_goals", []),
        "constraints": found.get("constraints", []),
        "risks": risks,
    }


def _merge_spec_analyses(items: list[dict[str, Any]]) -> dict[str, Any]:
    present = sorted({section for item in items for section in item.get("present_sections", [])})
    missing = [key for key in REQUIRED_SECTIONS if key not in present]
    risks = sorted({risk for item in items for risk in item.get("risks", [])})
    result = {
        "quality_score": round(sum(item.get("quality_score", 0.0) for item in items) / len(items), 3) if items else 0.0,
        "present_sections": present,
        "missing_sections": missing,
        "success_metrics": _merge_values(items, "success_metrics"),
        "acceptance_criteria": _merge_values(items, "acceptance_criteria"),
        "non_goals": _merge_values(items, "non_goals"),
        "constraints": _merge_values(items, "constraints"),
        "risks": risks,
        "suggested_actions": _suggestions(missing, risks),
    }
    if items and len(missing) < len(REQUIRED_SECTIONS):
        coverage_score = (len(REQUIRED_SECTIONS) - len(missing)) / len(REQUIRED_SECTIONS)
        result["quality_score"] = round((result["quality_score"] + coverage_score) / 2, 3)
    return result


def _sections(text: str) -> dict[str, str]:
    result: dict[str, list[str]] = {"document": []}
    current = "document"
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip()
            result.setdefault(current, [])
        else:
            result.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in result.items()}


def _section_items(body: str) -> list[str]:
    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        stripped = re.sub(r"^[-*+]\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
        stripped = re.sub(r"^\[[ xX]\]\s+", "", stripped)
        if stripped:
            items.append(shorten(stripped, 300))
    if not items and body.strip():
        items.append(shorten(body.strip(), 300))
    return items[:24]


def _score(present: list[str], risks: list[str]) -> float:
    if not REQUIRED_SECTIONS:
        return 0.0
    score = len(present) / len(REQUIRED_SECTIONS)
    score -= min(0.35, 0.08 * len(risks))
    return round(max(0.0, min(1.0, score)), 3)


def _suggestions(missing: list[str], risks: list[str]) -> list[str]:
    suggestions = []
    if "success_metrics" in missing:
        suggestions.append("Add at least one measurable success metric.")
    if "acceptance_criteria" in missing:
        suggestions.append("Add verifiable acceptance criteria.")
    if "non_goals" in missing:
        suggestions.append("Add non-goals to prevent unrelated implementation.")
    if "constraints" in missing:
        suggestions.append("Add security, performance, compatibility, or dependency constraints.")
    if any("HOW" in risk for risk in risks):
        suggestions.append("Move implementation choices out of Spec unless they are hard external constraints.")
    return suggestions


def _trajectory_implementation_corpus(trajectory: dict[str, Any]) -> str:
    values: list[str] = [
        str(trajectory.get("user_task") or ""),
        str((trajectory.get("result") or {}).get("summary") or ""),
        str((trajectory.get("diff_summary") or {}).get("diff") or ""),
    ]
    for item in trajectory.get("plan") or []:
        values.append(str(item))
    for action in trajectory.get("actions") or []:
        values.append(str(action.get("action_type") or ""))
        input_data = action.get("input") or action.get("input_data") or {}
        if isinstance(input_data, dict):
            values.extend(str(value) for value in input_data.values() if isinstance(value, (str, int, float)))
        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        values.append(str(observation.get("output") or ""))
        data = observation.get("data")
        if isinstance(data, dict):
            values.extend(str(value) for value in data.values() if isinstance(value, (str, int, float)))
    report = trajectory.get("reward_report") or {}
    values.extend(str(item) for item in report.get("notes") or [])
    return "\n".join(values).lower()


def _requirement_match(requirement: str, corpus: str) -> dict[str, Any]:
    tokens = _important_tokens(requirement)
    if not tokens:
        return {"satisfied": False, "score": 0.0, "evidence": ""}
    matched = [token for token in tokens if token in corpus]
    score = len(matched) / max(1, len(tokens))
    return {
        "satisfied": score >= 0.45 or _normalized_phrase(requirement) in corpus,
        "score": round(score, 3),
        "evidence": ", ".join(matched[:8]),
    }


def _metric_match(metric: str, corpus: str) -> dict[str, Any]:
    tokens = _important_tokens(metric)
    metric_terms = [token for token in tokens if MEASURABLE_RE.search(token) or token in {"benchmark", "perf", "performance", "latency", "coverage", "p95", "p99"}]
    matched = [token for token in tokens if token in corpus]
    verification_terms = ["test", "pytest", "benchmark", "perf", "latency", "coverage", "assert", "passed", "success"]
    has_verification = any(term in corpus for term in verification_terms)
    score = len(matched) / max(1, len(tokens))
    return {
        "satisfied": bool(metric_terms and has_verification and score >= 0.35),
        "score": round(score, 3),
        "evidence": ", ".join(matched[:8]),
    }


def _forbidden_match(text: str, corpus: str) -> dict[str, Any]:
    forbidden = _forbidden_terms(text)
    matched = [term for term in forbidden if term in corpus]
    score = len(matched) / max(1, len(forbidden))
    return {
        "violated": bool(matched) and score >= 0.5,
        "score": round(score, 3),
        "evidence": ", ".join(matched[:8]),
    }


def _constraint_violations(text: str, corpus: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    violations: list[dict[str, Any]] = []
    forbidden = _forbidden_terms(text)
    matched_forbidden = [term for term in forbidden if term in corpus]
    if matched_forbidden:
        violations.append({"score": 1.0, "evidence": ", ".join(matched_forbidden[:8])})
    if any(marker in lowered for marker in ["only", "仅", "只", "不得", "必须使用", "use existing"]):
        allowed = [term for term in TECH_TERMS if term in lowered]
        introduced = [term for term in TECH_TERMS if term in corpus and term not in allowed]
        if introduced and allowed:
            violations.append(
                {
                    "score": 1.0,
                    "evidence": f"allowed={', '.join(allowed[:4])}; introduced={', '.join(introduced[:6])}",
                }
            )
    return violations


def _forbidden_terms(text: str) -> list[str]:
    lowered = text.lower()
    terms = [term for term in TECH_TERMS if term in lowered]
    patterns = [
        r"(?:do not|don't|must not|never|禁止|不要|不得|非目标)\s+(.+)",
        r"(?:out of scope|范围外)[:：]?\s*(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        terms.extend(_important_tokens(match.group(1))[:6])
    return _unique_terms(terms)


def _important_tokens(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]{2,}", lowered)
    stop = {
        "the",
        "and",
        "or",
        "for",
        "with",
        "without",
        "must",
        "should",
        "shall",
        "can",
        "user",
        "users",
        "existing",
        "returns",
        "return",
        "implemented",
        "implement",
        "支持",
        "用户",
        "需要",
        "必须",
        "应该",
    }
    return _unique_terms(token.strip("-_./:") for token in tokens if len(token.strip("-_./:")) > 1 and token not in stop)


def _normalized_phrase(text: str) -> str:
    return " ".join(_important_tokens(text))


def _unique_terms(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _compliance_notes(context: dict[str, Any], trajectory: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not context.get("spec_files"):
        notes.append("No spec file was attached or discovered; compliance is unknown.")
    if context.get("missing_sections"):
        notes.append("Spec quality gaps reduce compliance confidence.")
    if not (trajectory.get("reward_report") or {}).get("regression_passed"):
        notes.append("Verification did not conclusively pass, so compliance needs human review.")
    return notes


def _merge_values(items: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in items:
        for value in item.get(key, []) or []:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def _quality_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _section_title(key: str) -> str:
    return key.replace("_", " ").title()


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _dedupe(paths: Any) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = Path(path).resolve()
        key = resolved.as_posix()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result
