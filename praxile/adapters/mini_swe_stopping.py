from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MiniSweStoppingPolicy:
    enabled: bool = False
    max_steps_without_patch: int = 120
    max_steps_after_patch: int = 20
    verified_grace_steps: int = 5
    repeated_command_limit: int = 3
    poll_interval_seconds: float = 0.5

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "MiniSweStoppingPolicy":
        raw = settings.get("stopping_policy", {})
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", False)),
            max_steps_without_patch=max(1, int(raw.get("max_steps_without_patch", 120))),
            max_steps_after_patch=max(1, int(raw.get("max_steps_after_patch", 20))),
            verified_grace_steps=max(0, int(raw.get("verified_grace_steps", 5))),
            repeated_command_limit=max(2, int(raw.get("repeated_command_limit", 3))),
            poll_interval_seconds=max(0.1, float(raw.get("poll_interval_seconds", 0.5))),
        )


@dataclass(frozen=True)
class MiniSweStoppingDecision:
    reason: str
    action_count: int
    first_patch_action: int | None
    successful_verification_action: int | None
    repeated_tail_count: int
    patch_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "stopped",
            "reason": self.reason,
            "action_count": self.action_count,
            "first_patch_action": self.first_patch_action,
            "successful_verification_action": self.successful_verification_action,
            "repeated_tail_count": self.repeated_tail_count,
            "patch_present": self.patch_present,
        }


class MiniSweStoppingMonitor:
    def __init__(self, policy: MiniSweStoppingPolicy):
        self.policy = policy
        self.first_patch_action: int | None = None

    def evaluate(
        self,
        trajectory: Mapping[str, Any],
        *,
        patch_present: bool,
    ) -> MiniSweStoppingDecision | None:
        if not self.policy.enabled:
            return None
        actions = _actions_with_results(trajectory)
        action_count = len(actions)
        if patch_present and self.first_patch_action is None:
            self.first_patch_action = action_count
        repeated_tail = _repeated_tail_count([item[0] for item in actions])
        verification_action = next(
            (
                index
                for index, (command, returncode) in reversed(list(enumerate(actions, 1)))
                if _looks_like_verification(command) and returncode == 0
            ),
            None,
        )
        if not patch_present and action_count >= self.policy.max_steps_without_patch:
            return self._decision(
                "exploration_budget_exhausted",
                action_count,
                verification_action,
                repeated_tail,
                patch_present,
            )
        if not patch_present or self.first_patch_action is None:
            return None
        post_patch_steps = action_count - self.first_patch_action
        verified_after_patch = bool(
            verification_action is not None and verification_action >= self.first_patch_action
        )
        if verified_after_patch and post_patch_steps >= self.policy.verified_grace_steps:
            return self._decision(
                "patch_verified",
                action_count,
                verification_action,
                repeated_tail,
                patch_present,
            )
        if verified_after_patch and repeated_tail >= self.policy.repeated_command_limit:
            return self._decision(
                "repeated_action_after_verification",
                action_count,
                verification_action,
                repeated_tail,
                patch_present,
            )
        if post_patch_steps >= self.policy.max_steps_after_patch:
            return self._decision(
                "post_patch_budget_exhausted",
                action_count,
                verification_action,
                repeated_tail,
                patch_present,
            )
        return None

    def _decision(
        self,
        reason: str,
        action_count: int,
        verification_action: int | None,
        repeated_tail: int,
        patch_present: bool,
    ) -> MiniSweStoppingDecision:
        return MiniSweStoppingDecision(
            reason=reason,
            action_count=action_count,
            first_patch_action=self.first_patch_action,
            successful_verification_action=verification_action,
            repeated_tail_count=repeated_tail,
            patch_present=patch_present,
        )


def _actions_with_results(trajectory: Mapping[str, Any]) -> list[tuple[str, int | None]]:
    messages = trajectory.get("messages", [])
    if not isinstance(messages, list):
        return []
    actions: list[list[Any]] = []
    pending: list[int] = []
    for raw in messages:
        if not isinstance(raw, Mapping):
            continue
        extra = raw.get("extra")
        extra = extra if isinstance(extra, Mapping) else {}
        raw_actions = extra.get("actions")
        if isinstance(raw_actions, list):
            for action in raw_actions:
                if isinstance(action, Mapping) and isinstance(action.get("command"), str):
                    actions.append([str(action["command"]).strip(), None])
                    pending.append(len(actions) - 1)
        role = str(raw.get("role") or raw.get("type") or "")
        if role in {"tool", "observation"} or bool(extra.get("tool_call_id")) or (
            role == "user" and pending
        ):
            if pending:
                actions[pending.pop(0)][1] = _returncode(raw, extra)
    return [(str(command), value if isinstance(value, int) else None) for command, value in actions]


def _returncode(message: Mapping[str, Any], extra: Mapping[str, Any]) -> int | None:
    for value in (extra.get("returncode"), message.get("returncode")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
        if isinstance(parsed, Mapping):
            value = parsed.get("returncode")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"<returncode>\s*(-?\d+)\s*</returncode>", content)
    return int(match.group(1)) if match else None


def _looks_like_verification(command: str) -> bool:
    return bool(
        re.search(
            r"(?:^|\s|&&|;)(?:python\s+-m\s+)?pytest(?:\s|$)|"
            r"(?:^|\s|&&|;)(?:tox|nox|cargo\s+test|go\s+test|npm\s+test|pnpm\s+test|yarn\s+test|make\s+test)(?:\s|$)",
            command,
        )
    )


def _repeated_tail_count(commands: list[str]) -> int:
    if not commands:
        return 0
    normalized = [re.sub(r"\s+", " ", item).strip() for item in commands]
    last = normalized[-1]
    count = 0
    for item in reversed(normalized):
        if item != last:
            break
        count += 1
    return count
