from __future__ import annotations

from typing import Any, Protocol

from ..config import Config
from ..model import ModelRouter


class ChatRouter(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str = "default",
        private: bool = False,
        high_risk: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        ...


class LLMClient:
    """Small role-scoped LLM client used by Praxile self-evolution flows."""

    def __init__(
        self,
        config: Config,
        *,
        model_role: str = "proposal_composer",
        timeout: int = 30,
        max_tokens: int = 2048,
        router: ChatRouter | None = None,
    ):
        self.config = config
        self.model_role = model_role
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.router: ChatRouter = router or ModelRouter(config)

    def complete(
        self,
        prompt: str,
        *,
        private: bool = False,
        high_risk: bool = False,
        temperature: float = 0.1,
    ) -> str:
        response = self.complete_messages(
            [{"role": "user", "content": prompt}],
            private=private,
            high_risk=high_risk,
            temperature=temperature,
        )
        return str(response.get("content") or "")

    def complete_messages(
        self,
        messages: list[dict[str, str]],
        *,
        private: bool = False,
        high_risk: bool = False,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        return self.router.chat(
            messages,
            purpose=self.model_role,
            private=private,
            high_risk=high_risk,
            temperature=temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )

    def describe_route(self, *, private: bool = False, high_risk: bool = False) -> dict[str, Any] | None:
        describe = getattr(self.router, "describe_route", None)
        if not callable(describe):
            return None
        return describe(self.model_role, private=private, high_risk=high_risk)
