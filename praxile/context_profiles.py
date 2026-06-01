from __future__ import annotations

from typing import Any


DEFAULT_ROLE_PROFILES: dict[str, dict[str, Any]] = {
    "coding_agent": {
        "preserve": ["diff_hunks", "file_paths", "error_signatures", "test_commands"],
        "max_chars": 60000,
    },
    "evidence_extraction": {
        "preserve": ["tool_output", "test_result", "diff_summary", "failure_excerpts"],
        "max_chars": 35000,
    },
    "proposal_composer": {
        "preserve": ["evidence_summary", "scope", "anti_scope", "source_run", "target_files"],
        "max_chars": 30000,
    },
    "reward_judge": {
        "preserve": ["reward_signals", "tests", "blocked_actions", "scope_control", "silent_risks"],
        "max_chars": 25000,
    },
    "attribution_judge": {
        "preserve": ["loaded_assets", "action_history", "referenced_paths", "outcome"],
        "max_chars": 25000,
    },
    "pattern_mining": {
        "preserve": ["failure_signatures", "affected_files", "verification_commands", "fix_actions"],
        "max_chars": 50000,
    },
}
