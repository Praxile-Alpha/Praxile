from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .utils import utc_now


SUPPORTED_PLATFORMS = {"telegram", "discord"}
DEFAULT_TOKEN_ENVS = {
    "telegram": "TELEGRAM_BOT_TOKEN",
    "discord": "DISCORD_BOT_TOKEN",
}


@dataclass(frozen=True)
class ChannelBinding:
    id: str
    platform: str
    channel_id: str
    name: str | None
    kind: str
    enabled: bool
    mode: str
    token_env: str
    guild_id: str | None = None
    thread_id: str | None = None
    require_mention: bool = True
    allow_free_response: bool = False
    auto_thread: bool | None = None
    skill: str | None = None
    prompt: str | None = None
    project_scope: str = "current"
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "thread_id": self.thread_id,
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "mode": self.mode,
            "token_env": self.token_env,
            "require_mention": self.require_mention,
            "allow_free_response": self.allow_free_response,
            "auto_thread": self.auto_thread,
            "skill": self.skill,
            "prompt": self.prompt,
            "project_scope": self.project_scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelBinding":
        return cls(
            id=str(data["id"]),
            platform=str(data["platform"]),
            channel_id=str(data["channel_id"]),
            guild_id=str(data["guild_id"]) if data.get("guild_id") is not None else None,
            thread_id=str(data["thread_id"]) if data.get("thread_id") is not None else None,
            name=str(data["name"]) if data.get("name") is not None else None,
            kind=str(data.get("kind") or "home"),
            enabled=bool(data.get("enabled", True)),
            mode=str(data.get("mode") or "notify"),
            token_env=str(data.get("token_env") or DEFAULT_TOKEN_ENVS.get(str(data["platform"]), "")),
            require_mention=bool(data.get("require_mention", True)),
            allow_free_response=bool(data.get("allow_free_response", False)),
            auto_thread=data.get("auto_thread") if data.get("auto_thread") is None else bool(data.get("auto_thread")),
            skill=str(data["skill"]) if data.get("skill") is not None else None,
            prompt=str(data["prompt"]) if data.get("prompt") is not None else None,
            project_scope=str(data.get("project_scope") or "current"),
            created_at=str(data["created_at"]) if data.get("created_at") else None,
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )


def make_binding_id(
    platform: str,
    channel_id: str,
    *,
    guild_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    parts = [platform]
    if guild_id:
        parts.append(str(guild_id))
    parts.append(str(channel_id))
    if thread_id:
        parts.append(str(thread_id))
    return ":".join(parts)


def parse_channel_target(target: str) -> dict[str, str | None]:
    parts = target.split(":")
    if len(parts) < 2:
        raise ValueError("Channel target must look like telegram:<chat_id> or discord:<guild_id>:<channel_id>")
    platform = parts[0]
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported channel platform: {platform}")
    if platform == "telegram":
        if len(parts) > 3:
            raise ValueError("Telegram target must look like telegram:<chat_id>[:thread_id]")
        return {
            "platform": platform,
            "guild_id": None,
            "channel_id": parts[1],
            "thread_id": parts[2] if len(parts) == 3 else None,
        }
    if len(parts) == 2:
        return {"platform": platform, "guild_id": None, "channel_id": parts[1], "thread_id": None}
    if len(parts) == 3:
        return {"platform": platform, "guild_id": parts[1], "channel_id": parts[2], "thread_id": None}
    if len(parts) == 4:
        return {"platform": platform, "guild_id": parts[1], "channel_id": parts[2], "thread_id": parts[3]}
    raise ValueError("Discord target must look like discord:<channel_id> or discord:<guild_id>:<channel_id>[:thread_id]")


class ChannelSystem:
    def __init__(self, config: Config):
        self.config = config
        self._ensure_shape()

    def list_bindings(self) -> list[ChannelBinding]:
        bindings = self.config.get("channels", "bindings", default={}) or {}
        return [ChannelBinding.from_dict(bindings[key]) for key in sorted(bindings)]

    def get(self, binding_id: str) -> ChannelBinding | None:
        data = self.config.get("channels", "bindings", binding_id)
        return ChannelBinding.from_dict(data) if isinstance(data, dict) else None

    def bind(
        self,
        platform: str,
        channel_id: str,
        *,
        name: str | None = None,
        kind: str = "home",
        enabled: bool = True,
        mode: str = "notify",
        token_env: str | None = None,
        guild_id: str | None = None,
        thread_id: str | None = None,
        require_mention: bool = True,
        allow_free_response: bool = False,
        auto_thread: bool | None = None,
        skill: str | None = None,
        prompt: str | None = None,
        project_scope: str = "current",
        make_default: bool = False,
    ) -> ChannelBinding:
        self._validate(platform, mode, kind)
        binding_id = make_binding_id(platform, channel_id, guild_id=guild_id, thread_id=thread_id)
        now = utc_now()
        existing = self.get(binding_id)
        binding = ChannelBinding(
            id=binding_id,
            platform=platform,
            channel_id=str(channel_id),
            guild_id=str(guild_id) if guild_id else None,
            thread_id=str(thread_id) if thread_id else None,
            name=name,
            kind=kind,
            enabled=enabled,
            mode=mode,
            token_env=token_env or DEFAULT_TOKEN_ENVS[platform],
            require_mention=require_mention,
            allow_free_response=allow_free_response,
            auto_thread=auto_thread,
            skill=skill,
            prompt=prompt,
            project_scope=project_scope,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        channels = self.config.data["channels"]
        channels["bindings"][binding_id] = binding.to_dict()
        if make_default or not channels.get("default"):
            channels["default"] = binding_id
        self.config.data.setdefault("gateway", {})["channels_enabled"] = True
        self._sync_platform(binding)
        self.config.write()
        return binding

    def unbind(self, binding_id: str) -> ChannelBinding:
        bindings = self.config.data["channels"]["bindings"]
        if binding_id not in bindings:
            raise ValueError(f"No channel binding found: {binding_id}")
        binding = ChannelBinding.from_dict(bindings.pop(binding_id))
        channels = self.config.data["channels"]
        if channels.get("default") == binding_id:
            channels["default"] = next(iter(sorted(bindings))) if bindings else None
        if not bindings:
            self.config.data.setdefault("gateway", {})["channels_enabled"] = False
        self._remove_platform_refs(binding)
        self.config.write()
        return binding

    def as_env_overlay(self) -> dict[str, str]:
        overlay: dict[str, str] = {}
        for binding in self.list_bindings():
            if not binding.enabled:
                continue
            if binding.platform == "telegram":
                overlay.setdefault("TELEGRAM_HOME_CHANNEL", binding.id)
                overlay.setdefault("TELEGRAM_BOT_TOKEN_ENV", binding.token_env)
            elif binding.platform == "discord":
                overlay.setdefault("DISCORD_HOME_CHANNEL", binding.id)
                overlay.setdefault("DISCORD_BOT_TOKEN_ENV", binding.token_env)
        return overlay

    def _ensure_shape(self) -> None:
        channels = self.config.data.setdefault("channels", {})
        channels.setdefault("version", 1)
        channels.setdefault("default", None)
        channels.setdefault("bindings", {})
        platforms = channels.setdefault("platforms", {})
        platforms.setdefault("telegram", {})
        platforms.setdefault("discord", {})
        platforms["telegram"].setdefault("token_env", DEFAULT_TOKEN_ENVS["telegram"])
        platforms["telegram"].setdefault("home_channel", None)
        platforms["telegram"].setdefault("free_response_chats", [])
        platforms["telegram"].setdefault("group_allowed_chats", [])
        platforms["telegram"].setdefault("require_mention", True)
        platforms["discord"].setdefault("token_env", DEFAULT_TOKEN_ENVS["discord"])
        platforms["discord"].setdefault("home_channel", None)
        platforms["discord"].setdefault("free_response_channels", [])
        platforms["discord"].setdefault("allowed_channels", [])
        platforms["discord"].setdefault("channel_skill_bindings", {})
        platforms["discord"].setdefault("channel_prompts", {})
        platforms["discord"].setdefault("require_mention", True)

    def _validate(self, platform: str, mode: str, kind: str) -> None:
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported channel platform: {platform}")
        if mode not in {"notify", "task", "bidirectional"}:
            raise ValueError("Channel mode must be notify, task, or bidirectional")
        if kind not in {"home", "project", "alert", "review"}:
            raise ValueError("Channel kind must be home, project, alert, or review")

    def _sync_platform(self, binding: ChannelBinding) -> None:
        platforms = self.config.data["channels"]["platforms"]
        platform = platforms[binding.platform]
        platform["enabled"] = True
        platform["token_env"] = binding.token_env
        if binding.kind == "home":
            platform["home_channel"] = binding.id
        if binding.platform == "telegram":
            platform["require_mention"] = binding.require_mention
            if binding.allow_free_response:
                self._append_unique(platform["free_response_chats"], binding.channel_id)
            self._append_unique(platform["group_allowed_chats"], binding.channel_id)
        if binding.platform == "discord":
            platform["require_mention"] = binding.require_mention
            if binding.allow_free_response:
                self._append_unique(platform["free_response_channels"], binding.channel_id)
            self._append_unique(platform["allowed_channels"], binding.channel_id)
            if binding.auto_thread is not None:
                platform["auto_thread"] = binding.auto_thread
            if binding.skill:
                platform["channel_skill_bindings"][binding.channel_id] = binding.skill
            if binding.prompt:
                platform["channel_prompts"][binding.channel_id] = binding.prompt

    def _remove_platform_refs(self, binding: ChannelBinding) -> None:
        platforms = self.config.data["channels"]["platforms"]
        platform = platforms[binding.platform]
        if platform.get("home_channel") == binding.id:
            platform["home_channel"] = None
        if binding.platform == "telegram":
            self._remove_value(platform.get("free_response_chats", []), binding.channel_id)
            self._remove_value(platform.get("group_allowed_chats", []), binding.channel_id)
        if binding.platform == "discord":
            self._remove_value(platform.get("free_response_channels", []), binding.channel_id)
            self._remove_value(platform.get("allowed_channels", []), binding.channel_id)
            platform.get("channel_skill_bindings", {}).pop(binding.channel_id, None)
            platform.get("channel_prompts", {}).pop(binding.channel_id, None)

    def _append_unique(self, values: list[Any], value: str) -> None:
        if value not in values:
            values.append(value)

    def _remove_value(self, values: list[Any], value: str) -> None:
        while value in values:
            values.remove(value)
