"""Replayable descriptive probes over control-plane operation alignment."""
from collections.abc import Mapping


def probe_repetition(prediction, alignment):
    result = {"schema_version": "praxile.behavior_probe.v1", "status": "abstained",
              "prediction_verified": False, "causal_attribution": False,
              "limitations": ["Identical parameters need not refer to identical environment states.",
                              "Fewer repetitions do not establish correctness or overall improvement."]}
    measurement = prediction.get("measurement") if isinstance(prediction, Mapping) else None
    if measurement is None:
        return {**result, "reason": "no_frozen_measurement"}
    if (not isinstance(measurement, Mapping) or set(measurement) != {"kind", "operation", "direction"}
            or any(not isinstance(value, str) for value in measurement.values())
            or measurement.get("kind") != "repeated_exact_operations"
            or measurement.get("operation") not in {"search", "read_file", "run_command"}
            or measurement.get("direction") not in {"decrease", "increase"}):
        return {**result, "reason": "unsupported_measurement"}
    if not isinstance(alignment, Mapping) or alignment.get("schema_version") != "praxile.operation_alignment.v1":
        return {**result, "reason": "alignment_missing"}
    unsupported = alignment.get("unsupported")
    known_kinds = {"search", "read_file", "run_command", "edit_file", "create_docx", "revise_docx",
                   "finish", "list_files", "list_dir", "read_document", "check_deliverables", "check_document_layout"}
    kinds = alignment.get("unsupported_kinds", {})
    if not isinstance(unsupported, Mapping) or not isinstance(kinds, Mapping):
        return {**result, "reason": "incomplete_operation_coverage"}
    for arm in ("baseline", "candidate"):
        refs, arm_kinds = unsupported.get(arm), kinds.get(arm, {})
        if not isinstance(refs, list) or not isinstance(arm_kinds, Mapping):
            return {**result, "reason": "incomplete_operation_coverage"}
        for ref in refs:
            kind = arm_kinds.get(ref) if isinstance(ref, str) else None
            if not isinstance(kind, str) or kind not in known_kinds or kind == measurement["operation"]:
                return {**result, "reason": "incomplete_operation_coverage"}
    groups = []
    try:
        for item in alignment["pairs"]:
            groups.append({"operation": item["operation"], "identity_digest": item["identity_digest"],
                           "baseline_event_ids": [item["baseline_event_id"]],
                           "candidate_event_ids": [item["candidate_event_id"]]})
        groups.extend(alignment["unpaired"])
        seen = {"baseline": set(), "candidate": set()}
        totals = {"baseline": 0, "candidate": 0}
        evidence = []
        identities = set()
        for group in groups:
            identity = (group["operation"], group["identity_digest"])
            if identity in identities:
                raise ValueError("duplicate identity")
            identities.add(identity)
            for arm in seen:
                refs = group[arm + "_event_ids"]
                if not isinstance(refs, list):
                    raise ValueError("invalid event refs")
                for ref in refs:
                    if not isinstance(ref, str) or not ref or ref in seen[arm]:
                        raise ValueError("duplicate or missing event ref")
                    seen[arm].add(ref)
                if group["operation"] == measurement["operation"]:
                    totals[arm] += max(0, len(refs) - 1)
            if group["operation"] == measurement["operation"]:
                evidence.append({key: group[key] for key in (
                    "identity_digest", "baseline_event_ids", "candidate_event_ids")})
    except (KeyError, TypeError, ValueError):
        return {**result, "reason": "invalid_alignment_evidence"}
    if not evidence:
        return {**result, "reason": "operation_not_observed"}
    delta = totals["candidate"] - totals["baseline"]
    return {**result, "status": "measured", "reason": "exact_parameter_repetition_count",
            "measurement": dict(measurement), "baseline": totals["baseline"], "candidate": totals["candidate"],
            "delta": delta, "event_evidence": evidence,
            "prediction_verified": delta < 0 if measurement["direction"] == "decrease" else delta > 0}
