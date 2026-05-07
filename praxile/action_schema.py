from __future__ import annotations

from typing import Any


READ_ONLY_ACTIONS = {"list_files", "project_map", "list_dir", "find_files", "search", "read_file", "read_files"}


class ActionSchemaRegistry:
    """Small dependency-free JSON action schema registry."""

    def __init__(self) -> None:
        self.schemas: dict[str, dict[str, Any]] = {
            "list_files": {"required": {}, "optional": {}},
            "project_map": {"required": {}, "optional": {"refresh": bool}},
            "list_dir": {"required": {}, "optional": {"path": str, "max_files": int}},
            "find_files": {"required": {"query": str}, "optional": {"limit": int}},
            "search": {"required": {"pattern": str}, "optional": {"limit": int}},
            "read_file": {"required": {"path": str}, "optional": {"max_chars": int, "start_line": int, "end_line": int}},
            "read_files": {"required": {"paths": list}, "optional": {"max_chars_each": int}},
            "batch": {"required": {"actions": list}, "optional": {}},
            "browser_open": {"required": {"url": str}, "optional": {}},
            "browser_screenshot": {"required": {"url": str}, "optional": {"name": str}},
            "edit_file": {"required": {"path": str, "content": str}, "optional": {}},
            "run_command": {"required": {"command": str}, "optional": {}},
            "finish": {"required": {"summary": str}, "optional": {"status": str}},
        }

    def validate(self, action: Any) -> tuple[bool, list[str]]:
        if not isinstance(action, dict):
            return False, ["action must be a JSON object"]
        action_type = action.get("type")
        if not isinstance(action_type, str) or not action_type:
            return False, ["action.type is required and must be a string"]
        schema = self.schemas.get(action_type)
        if not schema:
            return False, [f"unknown action.type `{action_type}`"]
        errors: list[str] = []
        allowed = {"type", *schema["required"].keys(), *schema["optional"].keys()}
        for key in action:
            if key not in allowed:
                errors.append(f"{action_type}.{key} is not allowed by the action schema")
        for key, expected_type in schema["required"].items():
            if key not in action:
                errors.append(f"{action_type}.{key} is required")
            elif not isinstance(action[key], expected_type):
                errors.append(f"{action_type}.{key} must be {expected_type.__name__}")
        for key, expected_type in schema["optional"].items():
            if key in action and action[key] is not None and not isinstance(action[key], expected_type):
                errors.append(f"{action_type}.{key} must be {expected_type.__name__}")
        if action_type == "read_files":
            paths = action.get("paths")
            if isinstance(paths, list) and not all(isinstance(item, str) for item in paths):
                errors.append("read_files.paths must be a list of strings")
        if action_type == "finish" and "status" in action and action["status"] not in {"completed", "needs_human", "failed"}:
            errors.append("finish.status must be completed, needs_human, or failed")
        if action_type == "batch":
            nested = action.get("actions")
            if isinstance(nested, list):
                if len(nested) > 8:
                    errors.append("batch.actions may contain at most 8 actions")
                for index, item in enumerate(nested):
                    ok, nested_errors = self.validate(item)
                    if not ok:
                        errors.extend(f"batch.actions[{index}].{error}" for error in nested_errors)
                        continue
                    nested_type = item.get("type")
                    if nested_type not in READ_ONLY_ACTIONS:
                        errors.append(f"batch.actions[{index}].type `{nested_type}` is not read-only")
        return not errors, errors

    def repair_prompt(self, errors: list[str]) -> str:
        allowed = ", ".join(sorted(self.schemas))
        return (
            "Your previous response was parsed as JSON but failed the Praxile Action Schema.\n"
            f"Errors:\n{chr(10).join(f'- {error}' for error in errors)}\n\n"
            f"Allowed action types: {allowed}.\n"
            "Return exactly one JSON object. Do not include prose or markdown."
        )

    def prompt_summary(self) -> str:
        lines = ["Action Schema Registry:"]
        for action_type in sorted(self.schemas):
            schema = self.schemas[action_type]
            required = ", ".join(schema["required"].keys()) or "(none)"
            optional = ", ".join(schema["optional"].keys()) or "(none)"
            lines.append(f"- {action_type}: required={required}; optional={optional}")
        return "\n".join(lines)
