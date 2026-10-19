"""Explicit control-plane separation, never inferred from model-authored text."""
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class TaskInput:
    body: str
    environment_instructions: str
    source: str

    def __post_init__(self):
        for value in (self.body, self.environment_instructions, self.source):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("task input fields must be nonempty strings")

    def prompt(self) -> str:
        return ("=== TASK BODY ===\n" + self.body
                + "\n\n=== ENVIRONMENT INSTRUCTIONS ===\n" + self.environment_instructions)

    def record(self) -> dict:
        return {"schema_version": "praxile.task_input.v1", "source": self.source,
                "body": self.body, "environment_instructions": self.environment_instructions,
                "body_sha256": hashlib.sha256(self.body.encode()).hexdigest(),
                "environment_sha256": hashlib.sha256(self.environment_instructions.encode()).hexdigest()}
