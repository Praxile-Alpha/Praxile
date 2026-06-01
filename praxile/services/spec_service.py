from __future__ import annotations

from typing import Any

from ..config import Config
from ..specs import build_spec_context, check_spec_file, verify_spec_compliance


class SpecService:
    def __init__(self, config: Config):
        self.config = config

    def context(self, explicit_specs: list[str] | None = None) -> dict[str, Any]:
        return build_spec_context(self.config.paths.root, explicit_specs)

    def check(self, spec_path: str | None = None) -> dict[str, Any]:
        return check_spec_file(self.config.paths.root, spec_path)

    def verify(self, trajectory: dict[str, Any], specs: list[str] | None = None) -> dict[str, Any]:
        return verify_spec_compliance(self.config.paths.root, trajectory, specs)
