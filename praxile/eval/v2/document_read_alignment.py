"""Match unique projected document reads by recorded selection and byte version."""
from collections import defaultdict
import hashlib
import re

from .schema import canonical_json


def _identity(facts):
    if facts.get("operation") != "read_document" or facts.get("status") != "success":
        return None
    for field in ("parameter_sha256", "source_sha256"):
        if not isinstance(facts.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", facts[field]):
            return None
    if facts.get("unit_kind") not in {"page", "block", "row"}:
        return None
    for field in ("total_units", "returned_units", "start_unit"):
        if type(facts.get(field)) is not int or facts[field] < 0:
            return None
    # Include observed extraction shape: identical bytes alone do not imply identical reads.
    shape = {field: facts.get(field) for field in (
        "unit_kind", "total_units", "returned_units", "start_unit", "requested_limit",
        "requested_char_offset", "next_start", "columns", "columns_truncated")}
    try:
        digest = hashlib.sha256(canonical_json(shape).encode()).hexdigest()
    except (ValueError, TypeError):
        return None
    return facts["parameter_sha256"], facts["source_sha256"], digest


def align_document_reads(baseline_timeline, candidate_timeline):
    """Inputs are structured native projections; no prose, model or evaluator is used."""
    def collect(timeline):
        groups, unsupported = defaultdict(list), []
        for entry in timeline:
            identity = _identity(entry["facts"])
            if identity is None:
                unsupported.append(entry["event_id"])
            else:
                groups[identity].append(entry["event_id"])
        return groups, unsupported

    left, left_unknown = collect(baseline_timeline)
    right, right_unknown = collect(candidate_timeline)
    pairs, unpaired = [], []
    for identity in sorted(set(left) | set(right)):
        a, b = left.get(identity, []), right.get(identity, [])
        common = dict(parameter_sha256=identity[0], source_sha256=identity[1],
                      read_shape_sha256=identity[2])
        if len(a) == len(b) == 1:
            pairs.append({**common, "baseline_event_id": a[0], "candidate_event_id": b[0],
                "recorded_version_relation": "same_bytes_digest", "state_equivalence": "unknown"})
        else:
            unpaired.append({**common, "baseline_event_ids": a, "candidate_event_ids": b,
                "reason": "ambiguous_repetition" if len(a) > 1 or len(b) > 1 else "no_exact_counterpart"})
    return {"schema_version": "praxile.document_read_alignment.v1",
        "strategy": "unique_recorded_selection_version_and_shape",
        "pairs": pairs, "unpaired": unpaired,
        "unsupported": {"baseline": left_unknown, "candidate": right_unknown},
        "semantic_equivalence_verified": False, "causal_alignment": False,
        "limitations": ["Digests are recorded evidence, not independent file authentication.",
            "Extractor versions, returned prose and full environment state are not compared.",
            "Repeated reads are never paired by order; missing counterparts remain unknown.",
            "Only successful document reads are supported; revisions and checks are not aligned."]}
