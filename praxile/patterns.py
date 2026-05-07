from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .constants import PRAXILE_DIR
from .semantic_judges import PatternSemanticJudge
from .utils import slugify, stable_hash, utc_now


PATTERN_SCORE_WEIGHTS = {
    "failure_signature_overlap": 0.20,
    "affected_files_overlap": 0.18,
    "verification_commands_overlap": 0.15,
    "fix_action_similarity": 0.15,
    "task_type_match": 0.10,
    "loaded_assets_overlap": 0.10,
}
PATTERN_CANDIDATE_THRESHOLD = 0.55


class PatternMiner:
    """
    Mines cross-task patterns from Episodes.
    """

    @staticmethod
    def mine_from_episodes(
        episodes: list[dict[str, Any]],
        feedback_context: dict[str, Any] | None = None,
        *,
        min_similarity: float = PATTERN_CANDIDATE_THRESHOLD,
        semantic_judge: PatternSemanticJudge | None = None,
    ) -> list[dict[str, Any]]:
        feedback_context = feedback_context or {}
        if not episodes:
            return []
        pair_cache: dict[tuple[int, int], dict[str, Any]] = {}
        components = _cluster_episodes(
            episodes,
            min_similarity=min_similarity,
            semantic_judge=semantic_judge,
            pair_cache=pair_cache,
        )
        patterns: list[dict[str, Any]] = []
        for component in components:
            eps = [episodes[index] for index in component]
            pair_scores = [
                _episode_similarity_cached(episodes, left, right, semantic_judge=semantic_judge, pair_cache=pair_cache)
                for offset, left in enumerate(component)
                for right in component[offset + 1 :]
            ]
            aggregate = _aggregate_similarity(pair_scores, eps)
            pattern = _build_pattern(eps, aggregate, feedback_context)
            patterns.append(pattern)
        patterns.sort(key=lambda item: (-float(item.get("confidence") or 0), item.get("pattern_id", "")))
        return patterns

    @staticmethod
    def load_all_episodes(state_dir: Path) -> list[dict[str, Any]]:
        ep_dir = state_dir / "experience" / "episodes"
        if not ep_dir.exists():
            return []

        episodes = []
        for p in ep_dir.glob("*.json"):
            try:
                episodes.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return episodes

    @staticmethod
    def load_feedback_context(state_dir: Path) -> dict[str, Any]:
        return {
            "feedback": _load_feedback(state_dir),
            "rejected_proposals": _load_proposals(state_dir / "experience" / "proposals" / "rejected"),
            "accepted_proposals": _load_proposals(state_dir / "experience" / "proposals" / "accepted"),
        }

    @staticmethod
    def update_index(state_dir: Path, config: Any | None = None, router: Any | None = None) -> list[dict[str, Any]]:
        episodes = PatternMiner.load_all_episodes(state_dir)
        context = PatternMiner.load_feedback_context(state_dir)
        semantic_judge = PatternSemanticJudge(config, router) if config is not None and router is not None else None
        patterns = PatternMiner.mine_from_episodes(episodes, context, semantic_judge=semantic_judge)

        idx_dir = state_dir / "experience" / "patterns"
        idx_dir.mkdir(parents=True, exist_ok=True)

        for pat in patterns:
            pat_path = idx_dir / f"{pat['pattern_id']}.json"
            pat_path.write_text(json.dumps(pat, indent=2, ensure_ascii=False), encoding="utf-8")

        return patterns


def _cluster_episodes(
    episodes: list[dict[str, Any]],
    *,
    min_similarity: float,
    semantic_judge: PatternSemanticJudge | None,
    pair_cache: dict[tuple[int, int], dict[str, Any]],
) -> list[list[int]]:
    parent = list(range(len(episodes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left in range(len(episodes)):
        for right in range(left + 1, len(episodes)):
            similarity = _episode_similarity_cached(
                episodes,
                left,
                right,
                semantic_judge=semantic_judge,
                pair_cache=pair_cache,
            )
            same_category = str(episodes[left].get("category") or "") == str(episodes[right].get("category") or "")
            if similarity["score"] >= min_similarity or (same_category and similarity["score"] >= min_similarity + 0.08):
                union(left, right)

    groups: dict[int, list[int]] = {}
    for index in range(len(episodes)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda group: (episodes[group[0]].get("category", ""), group[0]))


def _episode_similarity_cached(
    episodes: list[dict[str, Any]],
    left: int,
    right: int,
    *,
    semantic_judge: PatternSemanticJudge | None,
    pair_cache: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    key = (min(left, right), max(left, right))
    if key not in pair_cache:
        pair_cache[key] = _episode_similarity(episodes[left], episodes[right], semantic_judge=semantic_judge)
    return pair_cache[key]


def _episode_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    semantic_judge: PatternSemanticJudge | None = None,
) -> dict[str, Any]:
    dimensions = {
        "failure_signature_overlap": _signature_overlap(left, right),
        "affected_files_overlap": _jaccard(_affected_files(left), _affected_files(right)),
        "verification_commands_overlap": _jaccard(_commands(left), _commands(right)),
        "fix_action_similarity": _jaccard(_fix_terms(left), _fix_terms(right)),
        "task_type_match": 1.0
        if str(left.get("task_type") or left.get("category") or "")
        == str(right.get("task_type") or right.get("category") or "")
        else 0.0,
        "loaded_assets_overlap": _jaccard(_loaded_assets(left), _loaded_assets(right)),
    }
    score = round(
        sum(dimensions[name] * PATTERN_SCORE_WEIGHTS[name] for name in PATTERN_SCORE_WEIGHTS),
        4,
    )
    semantic_result = semantic_judge.judge_pair(left, right, score) if semantic_judge else None
    if semantic_result and semantic_result.get("active"):
        semantic_similarity = float(semantic_result.get("semantic_similarity") or 0.0)
        outcome_score = _pair_outcome_score(left, right)
        fused_score = round(0.45 * score + 0.40 * semantic_similarity + 0.15 * outcome_score, 4)
        if semantic_result.get("should_merge") is False:
            fused_score = min(fused_score, PATTERN_CANDIDATE_THRESHOLD - 0.01)
        dimensions.update(
            {
                "semantic_similarity": semantic_similarity,
                "root_cause_similarity": float(semantic_result.get("root_cause_similarity") or semantic_similarity),
                "semantic_fix_strategy_similarity": float(
                    semantic_result.get("fix_strategy_similarity") or semantic_similarity
                ),
                "semantic_verification_similarity": float(
                    semantic_result.get("verification_similarity") or semantic_similarity
                ),
                "outcome_score": outcome_score,
            }
        )
        score = max(score, fused_score) if semantic_result.get("should_merge") else fused_score
    reasons = [
        name
        for name, value in dimensions.items()
        if value >= 0.5
    ]
    result = {"score": score, "dimensions": dimensions, "reasons": reasons}
    if semantic_result:
        result["semantic_judge"] = semantic_result
        if semantic_result.get("active"):
            if semantic_result.get("reason"):
                result.setdefault("semantic_reasons", []).append(semantic_result.get("reason"))
            if semantic_result.get("recommended_pattern_claim"):
                result["recommended_pattern_claim"] = semantic_result.get("recommended_pattern_claim")
    return result


def _aggregate_similarity(pair_scores: list[dict[str, Any]], eps: list[dict[str, Any]]) -> dict[str, Any]:
    if pair_scores:
        dimension_names = sorted({name for item in pair_scores for name in (item.get("dimensions") or {})})
        dimensions = {
            name: round(sum(float(item["dimensions"].get(name) or 0) for item in pair_scores) / len(pair_scores), 4)
            for name in dimension_names
        }
        base = round(sum(float(item["score"] or 0) for item in pair_scores) / len(pair_scores), 4)
        semantic_reasons = []
        recommended_claims = []
        for item in pair_scores:
            semantic_reasons.extend(str(value) for value in item.get("semantic_reasons", []) if value)
            if item.get("recommended_pattern_claim"):
                recommended_claims.append(str(item.get("recommended_pattern_claim")))
    else:
        dimensions = {name: 0.0 for name in PATTERN_SCORE_WEIGHTS}
        dimensions["failure_signature_overlap"] = 1.0 if _failure_signatures(eps[0]) else 0.0
        dimensions["affected_files_overlap"] = 1.0 if _affected_files(eps[0]) else 0.0
        dimensions["verification_commands_overlap"] = 1.0 if _commands(eps[0]) else 0.0
        dimensions["fix_action_similarity"] = 1.0 if _fix_terms(eps[0]) else 0.0
        dimensions["task_type_match"] = 1.0
        dimensions["loaded_assets_overlap"] = 1.0 if _loaded_assets(eps[0]) else 0.0
        base = round(sum(dimensions[name] * PATTERN_SCORE_WEIGHTS[name] for name in PATTERN_SCORE_WEIGHTS), 4)
        base = min(base, 0.52)
        semantic_reasons = []
        recommended_claims = []
    return {
        "base_similarity": base,
        "dimensions": dimensions,
        "match_reasons": [
            f"{name}={value}"
            for name, value in dimensions.items()
            if float(value or 0) >= 0.5
        ],
        "semantic_reasons": list(dict.fromkeys(semantic_reasons))[:5],
        "recommended_pattern_claim": recommended_claims[0] if recommended_claims else "",
    }


def _build_pattern(eps: list[dict[str, Any]], aggregate: dict[str, Any], feedback_context: dict[str, Any]) -> dict[str, Any]:
    affected_files: list[str] = []
    applies_when: list[str] = []
    does_not_apply_when: list[str] = []
    failure_signatures: list[str] = []
    fix_strategy: list[str] = []
    verification_commands: list[str] = []
    evidence_items: list[str] = []
    source_episodes: list[dict[str, Any]] = []
    counterexamples: list[dict[str, Any]] = []
    loaded_assets: list[str] = []
    task_types: list[str] = []
    for ep in eps:
        scope = ep.get("scope", {}) if isinstance(ep.get("scope"), dict) else {}
        affected_files.extend(_affected_files(ep))
        applies_when.extend(_as_list(ep.get("applies_when")))
        applies_when.extend(f"Scope includes `{item}`." for item in _as_list(scope.get("applies_to"))[:4])
        does_not_apply_when.extend(_as_list(ep.get("does_not_apply_when")))
        does_not_apply_when.extend(_as_list(scope.get("does_not_apply_to")))
        failure_signatures.extend(_failure_signatures(ep))
        fix_strategy.extend(_fix_lines(ep))
        verification_commands.extend(_commands(ep))
        loaded_assets.extend(_loaded_assets(ep))
        if ep.get("task_type"):
            task_types.append(str(ep.get("task_type")))
        evidence_items.extend(_as_list(ep.get("evidence")))
        if ep.get("symptom"):
            evidence_items.append(f"{ep.get('episode_id')}: {ep.get('symptom')}")
        if ep.get("root_cause") and ep.get("root_cause") != "Needs further cross-run pattern mining to determine":
            evidence_items.append(f"{ep.get('episode_id')}: root cause `{ep.get('root_cause')}`")
        source_episodes.append(_source_episode(ep))
        if _is_negative_episode(ep):
            counterexamples.append(
                _counterexample(
                    source=ep,
                    counterexample_type="negative_outcome",
                    reason=f"Episode ended with outcome `{ep.get('outcome') or ep.get('status')}`.",
                    delta=-0.18,
                )
            )

    affected_files = _unique(affected_files)
    applies_when = _unique(applies_when)
    does_not_apply_when = _unique(does_not_apply_when)
    failure_signatures = _unique(failure_signatures)
    fix_strategy = _unique(fix_strategy)
    verification_commands = _unique(verification_commands)
    evidence_items = _unique(evidence_items)
    loaded_assets = _unique(loaded_assets)
    task_types = _unique(task_types)

    pattern_id = _pattern_id(eps, failure_signatures, affected_files, fix_strategy)
    claim = str(aggregate.get("recommended_pattern_claim") or "") or _candidate_claim(
        category=eps[0].get("category", "unknown"),
        signatures=failure_signatures or [eps[0].get("failure_signature", "unknown")],
        affected_files=affected_files,
        fix_strategy=fix_strategy,
        verification_commands=verification_commands,
    )
    rejected_similarity = _proposal_similarity(claim, evidence_items, feedback_context.get("rejected_proposals", []))
    accepted_similarity = _proposal_similarity(claim, evidence_items, feedback_context.get("accepted_proposals", []))
    feedback_summary = _pattern_feedback(pattern_id, affected_files, loaded_assets, feedback_context.get("feedback", []))
    if rejected_similarity > 0:
        counterexamples.append(
            {
                "counterexample_id": f"ce_{stable_hash(pattern_id + '-rejected', length=12)}",
                "type": "rejected_proposal_similarity",
                "reason": "A similar proposal was previously rejected by the user.",
                "effect": "lower_confidence",
                "confidence_delta": round(-0.10 * rejected_similarity, 3),
                "recommended_action": "inspect_or_edit",
            }
        )
    if feedback_summary["negative_feedback_count"]:
        counterexamples.append(
            {
                "counterexample_id": f"ce_{stable_hash(pattern_id + '-feedback', length=12)}",
                "type": "negative_pattern_feedback",
                "reason": "Negative user feedback matched this pattern, asset, or affected file.",
                "effect": "lower_confidence",
                "confidence_delta": feedback_summary["confidence_adjustment_from_feedback"],
                "recommended_action": "inspect_or_edit",
            }
        )

    success_count = sum(1 for ep in eps if _positive_episode(ep))
    negative_count = sum(1 for ep in eps if _is_negative_episode(ep))
    evidence_count = len(eps)
    positive_ratio = round(success_count / max(1, evidence_count), 4)
    negative_ratio = round(negative_count / max(1, evidence_count), 4)
    base_similarity = float(aggregate["base_similarity"])
    pattern_score = round(
        max(
            0.0,
            min(
                1.0,
                base_similarity
                + 0.07 * positive_ratio
                - 0.10 * negative_ratio
                - 0.10 * rejected_similarity
                + 0.05 * accepted_similarity
                + feedback_summary["confidence_adjustment_from_feedback"],
            ),
        ),
        4,
    )
    evidence_bonus = 0.0
    if evidence_count >= 2:
        evidence_bonus += 0.08
    if evidence_count >= 3:
        evidence_bonus += 0.08
    confidence = round(max(0.1, min(0.99, pattern_score + evidence_bonus - 0.04 * len(counterexamples))), 4)
    dimensions = dict(aggregate["dimensions"])
    dimensions.update(
        {
            "positive_outcome_ratio": positive_ratio,
            "negative_outcome_ratio": negative_ratio,
            "accepted_proposal_similarity": accepted_similarity,
            "rejected_proposal_similarity": rejected_similarity,
            "feedback_adjustment": feedback_summary["confidence_adjustment_from_feedback"],
        }
    )
    return {
        "pattern_id": pattern_id,
        "category": eps[0].get("category", "unknown"),
        "task_types": task_types,
        "signature_terms": failure_signatures or [eps[0].get("failure_signature", "unknown")],
        "failure_signatures": failure_signatures or [eps[0].get("failure_signature", "unknown")],
        "affected_files": affected_files,
        "loaded_assets": loaded_assets,
        "episodes": [ep.get("episode_id") for ep in eps],
        "source_episodes": source_episodes,
        "applies_when": applies_when
        or ["Future work matches the same category, signature, files, and verification context."],
        "does_not_apply_when": does_not_apply_when
        or ["Future work lacks matching evidence or changes unrelated architecture/security/data-flow areas."],
        "evidence_items": evidence_items
        or [f"{evidence_count} episode(s) share category `{eps[0].get('category', 'unknown')}` and signature `{eps[0].get('failure_signature', 'unknown')}`."],
        "fix_strategy": fix_strategy
        or ["Use the successful source episodes as a scoped repair recipe, then verify with project commands."],
        "verification_commands": verification_commands,
        "counterexamples": counterexamples,
        "success_count": success_count,
        "failure_count": negative_count,
        "pattern_score": pattern_score,
        "match_dimensions": dimensions,
        "match_reasons": aggregate["match_reasons"],
        "semantic_reasons": aggregate.get("semantic_reasons", []),
        "recommended_pattern_claim": aggregate.get("recommended_pattern_claim"),
        "positive_feedback_count": feedback_summary["positive_feedback_count"],
        "negative_feedback_count": feedback_summary["negative_feedback_count"],
        "latest_feedback": feedback_summary["latest_feedback"],
        "confidence_adjustment_from_feedback": feedback_summary["confidence_adjustment_from_feedback"],
        "last_seen_at": utc_now(),
        "candidate_hypothesis": claim,
        "expected_future_use": _expected_future_use(
            signatures=failure_signatures,
            affected_files=affected_files,
            verification_commands=verification_commands,
            loaded_assets=loaded_assets,
        ),
        "confidence": confidence,
        "confidence_rationale": (
            f"pattern_score={pattern_score}; evidence_count={evidence_count}; "
            f"positive_ratio={positive_ratio}; negative_ratio={negative_ratio}; "
            f"counterexamples={len(counterexamples)}; feedback_adjustment={feedback_summary['confidence_adjustment_from_feedback']}."
        ),
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", str(text or ""))
        if len(token.strip()) > 1
    }


def _jaccard(left: list[Any] | set[str], right: list[Any] | set[str]) -> float:
    left_set = {str(item).lower() for item in left if str(item).strip()}
    right_set = {str(item).lower() for item in right if str(item).strip()}
    if not left_set or not right_set:
        return 0.0
    return round(len(left_set & right_set) / len(left_set | right_set), 4)


def _signature_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_signatures = _failure_signatures(left)
    right_signatures = _failure_signatures(right)
    if not left_signatures or not right_signatures:
        return 0.0
    if {item.lower() for item in left_signatures} & {item.lower() for item in right_signatures}:
        return 1.0
    return _jaccard(_tokens(" ".join(left_signatures)), _tokens(" ".join(right_signatures)))


def _failure_signatures(ep: dict[str, Any]) -> list[str]:
    signatures = []
    signatures.extend(_as_list(ep.get("failure_signatures")))
    if ep.get("failure_signature"):
        signatures.append(ep.get("failure_signature"))
    return _unique(signatures)


def _affected_files(ep: dict[str, Any]) -> list[str]:
    paths = []
    paths.extend(_as_list(ep.get("affected_files")))
    paths.extend(_as_list(ep.get("touched_files")))
    scope = ep.get("scope", {}) if isinstance(ep.get("scope"), dict) else {}
    paths.extend(_as_list(scope.get("applies_to")))
    return _unique(paths)


def _commands(ep: dict[str, Any]) -> list[str]:
    commands = []
    commands.extend(_as_list(ep.get("verification_commands")))
    commands.extend(_as_list(ep.get("verification")))
    return _unique(commands)


def _fix_lines(ep: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.extend(str(item) for item in _as_list(ep.get("fix_strategy")) if item)
    if ep.get("fix_pattern"):
        lines.append(str(ep.get("fix_pattern")))
    for item in _as_list(ep.get("fix_actions")):
        if isinstance(item, dict):
            value = item.get("summary") or item.get("file") or item.get("path")
            if value:
                lines.append(str(value))
        elif item:
            lines.append(str(item))
    return _unique(lines)


def _fix_terms(ep: dict[str, Any]) -> set[str]:
    return _tokens(" ".join(_fix_lines(ep)))


def _loaded_assets(ep: dict[str, Any]) -> list[str]:
    paths = []
    for item in _as_list(ep.get("loaded_assets")):
        if isinstance(item, dict):
            value = item.get("path") or item.get("asset_id")
            if value:
                paths.append(str(value))
        elif item:
            paths.append(str(item))
    return _unique(paths)


def _source_episode(ep: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": ep.get("episode_id"),
        "task_id": ep.get("task_id"),
        "category": ep.get("category"),
        "task_type": ep.get("task_type"),
        "failure_signature": ep.get("failure_signature"),
        "symptom": ep.get("symptom"),
        "root_cause": ep.get("root_cause"),
        "fix_pattern": ep.get("fix_pattern"),
        "fix_strategy": _fix_lines(ep),
        "verification_commands": _commands(ep),
        "affected_files": _affected_files(ep),
        "outcome": ep.get("outcome"),
        "confidence": ep.get("confidence_score", ep.get("confidence")),
    }


def _positive_episode(ep: dict[str, Any]) -> bool:
    outcome = str(ep.get("outcome") or ep.get("status") or "").lower()
    return outcome in {"success", "completed", "positive"} or not _is_negative_episode(ep)


def _pair_outcome_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    positives = sum(1 for item in [left, right] if _positive_episode(item))
    negatives = sum(1 for item in [left, right] if _is_negative_episode(item))
    return round(max(0.0, min(1.0, 0.5 + 0.25 * positives - 0.25 * negatives)), 4)


def _is_negative_episode(ep: dict[str, Any]) -> bool:
    outcome = str(ep.get("outcome") or ep.get("status") or "").lower()
    return outcome in {"failed", "failure", "negative", "rejected", "needs_human"}


def _counterexample(*, source: dict[str, Any], counterexample_type: str, reason: str, delta: float) -> dict[str, Any]:
    key = f"{source.get('episode_id')}-{counterexample_type}-{reason}"
    return {
        "counterexample_id": f"ce_{stable_hash(key, length=12)}",
        "source_task_id": source.get("task_id"),
        "episode_id": source.get("episode_id"),
        "type": counterexample_type,
        "reason": reason,
        "effect": "lower_confidence",
        "confidence_delta": delta,
        "recommended_action": "inspect_or_edit",
    }


def _pattern_id(eps: list[dict[str, Any]], signatures: list[str], files: list[str], fix_strategy: list[str]) -> str:
    signature = signatures[0] if signatures else eps[0].get("failure_signature", "unknown")
    category = eps[0].get("category", "unknown")
    file_hint = Path(files[0]).stem if files else ""
    key = "|".join(str(ep.get("episode_id") or ep.get("task_id") or index) for index, ep in enumerate(eps))
    suffix = stable_hash(key + "|" + " ".join(fix_strategy[:2]), length=8)
    return "pat_" + slugify(f"{category}-{signature}-{file_hint}-{suffix}", max_length=110)


def _candidate_claim(
    *,
    category: str,
    signatures: list[str],
    affected_files: list[str],
    fix_strategy: list[str],
    verification_commands: list[str],
) -> str:
    signature = signatures[0] if signatures else "matching failure"
    file_scope = ", ".join(affected_files[:3]) if affected_files else "the same project scope"
    fix = fix_strategy[0] if fix_strategy else "a scoped repair"
    verification = verification_commands[0] if verification_commands else "the recorded project verification"
    return (
        f"When `{signature}` appears in `{file_scope}` during `{category}`, "
        f"start with {fix} and verify with `{verification}`."
    )


def _expected_future_use(
    *,
    signatures: list[str],
    affected_files: list[str],
    verification_commands: list[str],
    loaded_assets: list[str],
) -> str:
    parts = []
    if signatures:
        parts.append(f"matching signature `{signatures[0]}`")
    if affected_files:
        parts.append(f"touching `{affected_files[0]}` or nearby files")
    if verification_commands:
        parts.append(f"verified by `{verification_commands[0]}`")
    if loaded_assets:
        parts.append(f"after loading `{loaded_assets[0]}`")
    if not parts:
        return "Use as a low-priority project pattern only when future evidence is closely similar."
    return "Load before implementation for future tasks with " + ", ".join(parts) + "."


def _proposal_similarity(claim: str, evidence_items: list[str], proposals: list[dict[str, Any]]) -> float:
    if not proposals:
        return 0.0
    query_terms = _tokens(" ".join([claim, *evidence_items]))
    if not query_terms:
        return 0.0
    best = 0.0
    for proposal in proposals:
        fields = [
            str(proposal.get("type") or ""),
            str(proposal.get("title") or ""),
            str(proposal.get("reason") or ""),
            str(proposal.get("evidence_summary") or ""),
        ]
        fields.extend(str(item) for item in proposal.get("evidence") or [])
        score = _jaccard(query_terms, _tokens(" ".join(fields)))
        best = max(best, score)
    return round(best, 4)


def _pattern_feedback(
    pattern_id: str,
    affected_files: list[str],
    loaded_assets: list[str],
    feedback: list[dict[str, Any]],
) -> dict[str, Any]:
    positive = 0
    negative = 0
    latest: list[dict[str, Any]] = []
    targets = {pattern_id, f"{PRAXILE_DIR}/experience/patterns/{pattern_id}.md", f"experience/patterns/{pattern_id}.md"}
    targets.update(affected_files)
    targets.update(loaded_assets)
    for item in feedback:
        target_id = str(item.get("target_id") or "")
        target_type = str(item.get("target_type") or "")
        if target_type not in {"pattern", "asset", "proposal"}:
            continue
        if target_id not in targets and not any(path and path in target_id for path in affected_files[:4]):
            continue
        sentiment = str(item.get("sentiment") or "neutral")
        if sentiment == "positive":
            positive += 1
        elif sentiment == "negative":
            negative += 1
        latest.append(
            {
                "feedback_id": item.get("feedback_id"),
                "sentiment": sentiment,
                "target_type": target_type,
                "target_id": target_id,
                "raw_text": item.get("raw_text"),
            }
        )
    adjustment = round(min(0.18, positive * 0.04) - min(0.30, negative * 0.08), 4)
    return {
        "positive_feedback_count": positive,
        "negative_feedback_count": negative,
        "latest_feedback": latest[-5:],
        "confidence_adjustment_from_feedback": adjustment,
    }


def _load_feedback(state_dir: Path) -> list[dict[str, Any]]:
    root = state_dir / "experience" / "feedback"
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _load_proposals(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows
