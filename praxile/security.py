from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Config
from .constants import PRAXILE_DIR
from .interop import external_agent_conflict
from .json_utils import RobustJSONError, parse_jsonc_object
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
        self.state_root = config.paths.state.resolve()
        self.sensitive_globs = config.get("safety", "sensitive_globs", default=[])
        self.dangerous_patterns = [p.lower() for p in config.get("safety", "dangerous_command_patterns", default=[])]
        self.allowed_prefixes = config.get("safety", "allowed_command_prefixes", default=[])
        configured_protected = list(config.get("safety", "protected_paths", default=[]) or [])
        self.protected_paths = sorted({PRAXILE_DIR, *configured_protected})
        self.policy_rules: list[dict[str, Any]] = []
        self.policy_file_status: list[dict[str, Any]] = []
        self.policy_errors: list[str] = []
        self._load_policy_rules()

    def policy_status(self) -> dict[str, Any]:
        return {
            "policy_files": self.policy_file_status,
            "rules_count": len(self.policy_rules),
            "errors": list(self.policy_errors),
        }

    def check_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> SafetyDecision:
        args = args or {}
        policy_decision = self._check_policy_rules(tool_name, args, context=context)
        if not policy_decision.allowed:
            return policy_decision
        if tool_name == "run_command":
            return self.check_command(str(args.get("command", "")))
        if tool_name == "edit_file":
            return self.check_path(str(args.get("path", "")), write=True)
        if tool_name in {"read_file", "list_dir"}:
            return self.check_path(str(args.get("path", ".")), write=False)
        if tool_name == "read_files":
            paths = args.get("paths", [])
            if not isinstance(paths, list):
                return SafetyDecision(False, "paths must be a list", "low")
            for path in paths:
                decision = self.check_path(str(path), write=False)
                if not decision.allowed:
                    return decision
            return SafetyDecision(True)
        if tool_name in {"browser_open", "browser_screenshot"}:
            return self._check_browser_url(str(args.get("url", "")))
        return SafetyDecision(True)

    def check_path(self, path: str | Path, *, write: bool = False) -> SafetyDecision:
        candidate = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not path_is_relative_to(candidate, self.root):
            return SafetyDecision(False, f"path escapes project root: {path}", "high")
        rel = candidate.relative_to(self.root).as_posix()
        policy_tool = "edit_file" if write else "read_file"
        policy_decision = self._check_policy_rules(policy_tool, {"path": rel})
        if not policy_decision.allowed:
            return policy_decision
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
        policy_decision = self._check_policy_rules("run_command", {"command": normalized})
        if not policy_decision.allowed:
            return policy_decision
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

    def _load_policy_rules(self) -> None:
        inline_rules = self.config.get("safety", "policy_rules", default=[]) or []
        self._extend_policy_rules(inline_rules, source="config:safety.policy_rules")
        for raw_path in self.config.get("safety", "policy_files", default=[]) or []:
            if not isinstance(raw_path, str) or not raw_path.strip():
                self.policy_errors.append("safety.policy_files contains a non-string path")
                continue
            policy_path = self._resolve_policy_file(raw_path)
            status = {
                "path": raw_path,
                "resolved_path": str(policy_path) if policy_path else None,
                "loaded": False,
                "rules_count": 0,
                "error": None,
            }
            if policy_path is None:
                status["error"] = "policy files must resolve inside .praxile/"
                self.policy_errors.append(f"{raw_path}: {status['error']}")
                self.policy_file_status.append(status)
                continue
            if not policy_path.exists():
                status["error"] = "missing"
                self.policy_file_status.append(status)
                continue
            try:
                payload = parse_jsonc_object(policy_path.read_text(encoding="utf-8"))
            except (OSError, RobustJSONError) as exc:
                status["error"] = str(exc)
                self.policy_errors.append(f"{raw_path}: {exc}")
                self.policy_file_status.append(status)
                continue
            before = len(self.policy_rules)
            self._extend_policy_rules(payload.get("rules", payload), source=raw_path)
            status["loaded"] = True
            status["rules_count"] = len(self.policy_rules) - before
            self.policy_file_status.append(status)

    def _extend_policy_rules(self, rules: Any, *, source: str) -> None:
        if isinstance(rules, dict):
            rules = [rules]
        if not isinstance(rules, list):
            self.policy_errors.append(f"{source}: expected a list of safety rules")
            return
        for index, raw_rule in enumerate(rules):
            if not isinstance(raw_rule, dict):
                self.policy_errors.append(f"{source}#{index + 1}: expected object")
                continue
            rule = self._normalize_policy_rule(raw_rule, source=source, index=index)
            if rule:
                self.policy_rules.append(rule)

    def _normalize_policy_rule(self, raw_rule: dict[str, Any], *, source: str, index: int) -> dict[str, Any] | None:
        if raw_rule.get("enabled", True) is False:
            return None
        action = str(raw_rule.get("action", "deny")).lower()
        if action not in {"deny", "block"}:
            return None
        tools_raw = raw_rule.get("tools", raw_rule.get("tool", "*"))
        if isinstance(tools_raw, str):
            tools = [tools_raw]
        elif isinstance(tools_raw, list):
            tools = [str(item) for item in tools_raw if isinstance(item, str) and item.strip()]
        else:
            tools = ["*"]
        match = raw_rule.get("match", {})
        if not isinstance(match, dict):
            self.policy_errors.append(f"{source}#{index + 1}: match must be an object")
            return None
        rule_id = str(raw_rule.get("id") or f"{source}#{index + 1}")
        return {
            "id": rule_id,
            "source": source,
            "tools": tools or ["*"],
            "match": match,
            "message": str(raw_rule.get("message") or raw_rule.get("reason") or f"blocked by safety policy rule {rule_id}"),
            "risk_level": str(raw_rule.get("risk_level") or "medium"),
        }

    def _resolve_policy_file(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            normalized = raw_path.replace("\\", "/")
            if normalized == PRAXILE_DIR or normalized.startswith(f"{PRAXILE_DIR}/"):
                resolved = (self.root / normalized).resolve()
            else:
                resolved = (self.state_root / normalized).resolve()
        if not path_is_relative_to(resolved, self.state_root):
            return None
        return resolved

    def _check_policy_rules(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> SafetyDecision:
        context = context or {}
        for rule in self.policy_rules:
            if not self._policy_tool_matches(tool_name, rule["tools"]):
                continue
            if not self._policy_match(rule["match"], args, context):
                continue
            return SafetyDecision(False, rule["message"], rule["risk_level"])
        return SafetyDecision(True)

    def _policy_tool_matches(self, tool_name: str, patterns: list[str]) -> bool:
        return any(pattern == "*" or fnmatch.fnmatch(tool_name, pattern) for pattern in patterns)

    def _policy_match(self, match: dict[str, Any], args: dict[str, Any], context: dict[str, Any]) -> bool:
        if not match:
            return True
        checks = 0
        command = " ".join(str(args.get("command", "")).strip().split())
        if "command_contains" in match:
            checks += 1
            values = _string_list(match.get("command_contains"))
            if not any(value.lower() in command.lower() for value in values):
                return False
        if "command_prefix" in match:
            checks += 1
            values = _string_list(match.get("command_prefix"))
            if not any(command == value or command.startswith(value + " ") for value in values):
                return False
        if "path_glob" in match:
            checks += 1
            values = _string_list(match.get("path_glob"))
            paths = self._paths_from_args(args)
            if not any(self._matches_path_pattern(path, Path(path).name, pattern) for path in paths for pattern in values):
                return False
        if "path_contains" in match:
            checks += 1
            values = [value.lower() for value in _string_list(match.get("path_contains"))]
            paths = [path.lower() for path in self._paths_from_args(args)]
            if not any(value in path for path in paths for value in values):
                return False
        if "url_host" in match:
            checks += 1
            values = set(_string_list(match.get("url_host")))
            parsed = urlparse(str(args.get("url", "")))
            if parsed.hostname not in values:
                return False
        if "arg_contains" in match:
            checks += 1
            spec = match.get("arg_contains")
            if not isinstance(spec, dict):
                return False
            for key, raw_values in spec.items():
                values = [value.lower() for value in _string_list(raw_values)]
                haystack = str(args.get(str(key), "")).lower()
                if not any(value in haystack for value in values):
                    return False
        if "context_equals" in match:
            checks += 1
            spec = match.get("context_equals")
            if not isinstance(spec, dict):
                return False
            for key, expected in spec.items():
                if context.get(str(key)) != expected:
                    return False
        return checks > 0 or bool(match.get("always", False))

    def _paths_from_args(self, args: dict[str, Any]) -> list[str]:
        raw_paths = args.get("paths") if "paths" in args else args.get("path")
        if raw_paths is None:
            return []
        if isinstance(raw_paths, list):
            values = [str(item) for item in raw_paths]
        else:
            values = [str(raw_paths)]
        normalized: list[str] = []
        for value in values:
            candidate = Path(value)
            try:
                if candidate.is_absolute():
                    resolved = candidate.resolve()
                    if path_is_relative_to(resolved, self.root):
                        normalized.append(resolved.relative_to(self.root).as_posix())
                    else:
                        normalized.append(value.replace("\\", "/"))
                else:
                    rel = value.replace("\\", "/")
                    while rel.startswith("./"):
                        rel = rel[2:]
                    normalized.append(rel)
            except OSError:
                normalized.append(value.replace("\\", "/"))
        return normalized

    def _check_browser_url(self, url: str) -> SafetyDecision:
        policy_decision = self._check_policy_rules("browser_open", {"url": url})
        if not policy_decision.allowed:
            return policy_decision
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return SafetyDecision(False, f"browser URL must be absolute http(s): {url}", "medium")
        allowed_hosts = self.config.get("browser", "allowed_hosts", default=["localhost", "127.0.0.1", "::1"])
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            return SafetyDecision(False, f"browser host not allowed: {parsed.hostname}", "medium")
        return SafetyDecision(True)


def _string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if item is not None]
    return [str(raw)]
