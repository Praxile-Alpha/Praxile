from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .constants import PRAXILE_DIR
from .interop import external_agent_conflict
from .utils import path_is_relative_to


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str = ""
    risk_level: str = "low"


class SafetyPolicy:
    def __init__(self, config: Config):
        self.config = config
        self.root = config.paths.root.resolve()
        self.sensitive_globs = config.get("safety", "sensitive_globs", default=[])
        self.dangerous_patterns = [p.lower() for p in config.get("safety", "dangerous_command_patterns", default=[])]
        self.allowed_prefixes = config.get("safety", "allowed_command_prefixes", default=[])
        configured_protected = list(config.get("safety", "protected_paths", default=[]) or [])
        self.protected_paths = sorted({PRAXILE_DIR, *configured_protected})

    def check_path(self, path: str | Path, *, write: bool = False) -> SafetyDecision:
        candidate = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not path_is_relative_to(candidate, self.root):
            return SafetyDecision(False, f"path escapes project root: {path}", "high")
        rel = candidate.relative_to(self.root).as_posix()
        for pattern in self.sensitive_globs:
            if self._matches_path_pattern(rel, candidate.name, pattern):
                return SafetyDecision(False, f"protected sensitive file: {rel}", "high")
        if write:
            conflict = external_agent_conflict(self.config)
            if conflict.get("blocked"):
                signals = ", ".join(
                    signal.get("path") or signal.get("name") or str(signal)
                    for signal in conflict.get("signals", [])
                )
                return SafetyDecision(
                    False,
                    f"external agent write-conflict guard is active: {signals}",
                    "high",
                )
            for protected in self.protected_paths:
                if rel == protected or rel.startswith(protected.rstrip("/") + "/"):
                    return SafetyDecision(False, f"protected harness path: {rel}", "medium")
        return SafetyDecision(True)

    def _matches_path_pattern(self, rel: str, name: str, pattern: str) -> bool:
        normalized = pattern.replace("\\", "/")
        candidates = [rel, name]
        patterns = [normalized]
        if normalized.startswith("**/"):
            patterns.append(normalized[3:])
        return any(fnmatch.fnmatch(value, candidate_pattern) for value in candidates for candidate_pattern in patterns)

    def check_command(self, command: str) -> SafetyDecision:
        normalized = " ".join(command.strip().split())
        lower = normalized.lower()
        if not normalized:
            return SafetyDecision(False, "empty command", "low")
        for pattern in self.dangerous_patterns:
            if pattern in lower:
                return SafetyDecision(False, f"dangerous command pattern blocked: {pattern}", "high")
        if "$(" in lower or "`" in lower:
            return SafetyDecision(False, "command substitution is blocked in shell commands", "high")
        has_shell_features = any(token in lower for token in [" && ", " || ", ";", "`", "$(", ">", "<"]) or "|" in lower
        shell_features_allowed = bool(self.config.get("shell", "allow_shell_features", default=False))
        if has_shell_features and not shell_features_allowed:
            if "|" in lower and self._check_safe_tee_pipe(normalized).allowed:
                pass
            else:
                return SafetyDecision(
                    False,
                    "shell features require shell.allow_shell_features=true and an allowed command prefix",
                    "medium",
                )
        if not shell_features_allowed and any(token in lower for token in [" && ", " || ", ";", "`", "$("]):
            return SafetyDecision(False, "compound shell commands require manual execution outside the harness", "medium")
        if not shell_features_allowed and "|" in lower:
            tee_decision = self._check_safe_tee_pipe(normalized)
            if not tee_decision.allowed:
                return tee_decision
        if not shell_features_allowed:
            try:
                parts = shlex.split(normalized)
            except ValueError as exc:
                return SafetyDecision(False, f"invalid shell quoting: {exc}", "medium")
            if not parts:
                return SafetyDecision(False, "empty command", "low")
        elif has_shell_features:
            return self._check_shell_feature_segments(normalized)
        for prefix in self.allowed_prefixes:
            if normalized == prefix or normalized.startswith(prefix + " "):
                return SafetyDecision(True)
        return SafetyDecision(
            False,
            "command is not in allowed prefixes. Add a reviewed prefix to .praxile/config.json.",
            "medium",
        )

    def _check_safe_tee_pipe(self, command: str) -> SafetyDecision:
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            return SafetyDecision(False, f"invalid shell quoting: {exc}", "medium")
        if tokens.count("|") != 1:
            return SafetyDecision(False, "only a single pipe to tee may be considered in safe mode", "medium")
        pipe_index = tokens.index("|")
        left = shlex.join(tokens[:pipe_index])
        right = tokens[pipe_index + 1 :]
        if not right or right[0] != "tee":
            return SafetyDecision(False, "pipes are blocked in safe mode except restricted `| tee <project-path>`", "medium")
        tee_args = right[1:]
        if tee_args and tee_args[0] == "-a":
            tee_args = tee_args[1:]
        if len(tee_args) != 1:
            return SafetyDecision(False, "safe-mode tee requires exactly one project-relative output path", "medium")
        target_decision = self.check_path(tee_args[0], write=True)
        if not target_decision.allowed:
            return SafetyDecision(False, f"unsafe tee output path: {target_decision.reason}", target_decision.risk_level)
        if not self._matches_allowed_prefix(left):
            return SafetyDecision(False, f"command before tee is not in allowed prefixes: {left}", "medium")
        return SafetyDecision(True)

    def _check_shell_feature_segments(self, command: str) -> SafetyDecision:
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            return SafetyDecision(False, f"invalid shell quoting: {exc}", "medium")

        command_tokens: list[str] = []
        operators = {"|", "&&", "||", ";"}
        redirections = {">", ">>", "<"}
        blocked_ops = {"&", "<<", "<<<", "<>", ">|"}

        def flush_segment() -> SafetyDecision | None:
            if not command_tokens:
                return SafetyDecision(False, "empty command segment in shell command", "medium")
            segment = shlex.join(command_tokens)
            if self._matches_allowed_prefix(segment):
                command_tokens.clear()
                return None
            return SafetyDecision(
                False,
                f"shell command segment is not in allowed prefixes: {segment}",
                "medium",
            )

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in blocked_ops:
                return SafetyDecision(False, f"unsupported shell operator blocked: {token}", "medium")
            if token in operators:
                decision = flush_segment()
                if decision:
                    return decision
                index += 1
                continue
            if token in redirections:
                if token == "<":
                    write = False
                else:
                    write = True
                index += 1
                if index >= len(tokens):
                    return SafetyDecision(False, f"missing target for redirection {token}", "medium")
                target = tokens[index]
                if target.startswith("&"):
                    index += 1
                    continue
                target_decision = self.check_path(target, write=write)
                if not target_decision.allowed:
                    return SafetyDecision(
                        False,
                        f"unsafe redirection target: {target_decision.reason}",
                        target_decision.risk_level,
                    )
                index += 1
                continue
            command_tokens.append(token)
            index += 1

        decision = flush_segment()
        return decision or SafetyDecision(True)

    def _matches_allowed_prefix(self, command: str) -> bool:
        normalized = " ".join(command.strip().split())
        return any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in self.allowed_prefixes)
