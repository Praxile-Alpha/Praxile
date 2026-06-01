from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..utils import read_json, utc_now, write_json


DEFAULT_POLICY_LAYERS: dict[str, dict[str, Any]] = {
    "default": {
        "layer": "default",
        "description": "Built-in Praxile governance defaults. Project files can narrow or document these rules.",
        "tool_policy": {
            "safe_mode": True,
            "dangerous_commands_blocked": True,
            "sensitive_paths_protected": True,
        },
        "proposal_gate": {
            "human_approval_required": True,
            "auto_accept": False,
            "high_risk_requires_review": True,
        },
        "context": {
            "sync_allowed": True,
            "compression_writes_only_context_assets": True,
        },
        "governance_loop": {
            "may_edit_code": False,
            "may_accept_proposals": False,
            "may_generate_reports": True,
        },
    },
    "context": {
        "layer": "context",
        "compression_by_role": {
            "coding_agent": {
                "preserve": ["diff_hunks", "file_paths", "error_signatures", "test_commands"],
                "max_chars": 60000,
            },
            "proposal_composer": {
                "preserve": ["evidence_summary", "scope", "anti_scope", "source_run", "target_files"],
                "max_chars": 30000,
            },
            "reward_judge": {
                "preserve": ["reward_signals", "tests", "blocked_actions", "scope_control", "silent_risks"],
                "max_chars": 25000,
            },
        },
    },
    "proposal_gate": {
        "layer": "proposal_gate",
        "proposal_gate": {
            "human_approval_required": True,
            "architecture_gate_blocks_implementation": True,
            "require_source_evidence": True,
        },
    },
    "tool_policy": {
        "layer": "tool_policy",
        "tool_policy": {
            "write_operations_require_project_root": True,
            "shell_features_default_disabled": True,
            "browser_hosts_local_by_default": True,
        },
    },
}

POLICY_LAYER_ORDER = ["default", "user", "project", "reflect", "context", "proposal_gate", "tool_policy", "model_roles"]


class PolicyService:
    """Policy-as-code helpers for project-local Praxile governance layers."""

    def __init__(self, config: Config):
        self.config = config

    def list_layers(self) -> dict[str, Any]:
        layers = []
        for name in POLICY_LAYER_ORDER:
            path = self._policy_path(name)
            data = self._load_layer(name)
            layers.append(
                {
                    "name": name,
                    "path": path.relative_to(self.config.paths.root).as_posix(),
                    "exists": path.exists(),
                    "source": "file" if path.exists() else "builtin" if name in DEFAULT_POLICY_LAYERS else "missing",
                    "keys": sorted(data.keys()) if isinstance(data, dict) else [],
                    "description": data.get("description") if isinstance(data, dict) else None,
                }
            )
        return {
            "generated_at": utc_now(),
            "policy_root": self._policy_dir().relative_to(self.config.paths.root).as_posix(),
            "layers": layers,
            "effective": self.effective_policy(),
        }

    def check(self, *, write_defaults: bool = False) -> dict[str, Any]:
        if write_defaults:
            self.write_defaults()
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        layers_payload = self.list_layers()
        for layer in layers_payload["layers"]:
            name = layer["name"]
            path = self._policy_path(name)
            if not path.exists():
                if name in {"user", "project", "reflect", "model_roles"}:
                    warnings.append({"layer": name, "code": "missing_optional_layer", "message": f"{name}.json is not present."})
                continue
            try:
                data = read_json(path, None)
            except Exception as exc:
                errors.append({"layer": name, "code": "invalid_json", "message": str(exc)})
                continue
            if not isinstance(data, dict):
                errors.append({"layer": name, "code": "invalid_json_object", "message": "Policy layer must be a JSON object."})
                continue
            if data.get("layer") and str(data.get("layer")) != name:
                warnings.append({"layer": name, "code": "layer_name_mismatch", "message": f"File declares layer={data.get('layer')!r}."})
            self._validate_layer(name, data, errors, warnings)
        return {
            "ok": not errors,
            "generated_at": utc_now(),
            "errors": errors,
            "warnings": warnings,
            "layers": layers_payload["layers"],
            "effective": layers_payload["effective"],
        }

    def explain(self, topic: str) -> dict[str, Any]:
        topic = str(topic or "").strip()
        matches: list[dict[str, Any]] = []
        effective: dict[str, Any] = {}
        for name in POLICY_LAYER_ORDER:
            data = self._load_layer(name)
            if not isinstance(data, dict) or "_invalid" in data:
                continue
            found = self._extract_topic(data, topic)
            if found is not None:
                matches.append(
                    {
                        "layer": name,
                        "source": "file" if self._policy_path(name).exists() else "builtin",
                        "path": self._policy_path(name).relative_to(self.config.paths.root).as_posix(),
                        "value": found,
                    }
                )
                effective = _deep_merge(effective, found if isinstance(found, dict) else {"value": found})
        return {
            "topic": topic,
            "generated_at": utc_now(),
            "matched_layers": matches,
            "effective": effective,
            "why": self._why(topic, matches),
        }

    def write_defaults(self) -> list[str]:
        written: list[str] = []
        for name in ("default", "context", "proposal_gate", "tool_policy"):
            path = self._policy_path(name)
            if path.exists():
                continue
            payload = {**DEFAULT_POLICY_LAYERS[name], "generated_by": "praxile policy check --write-defaults", "created_at": utc_now()}
            write_json(path, payload)
            written.append(path.relative_to(self.config.paths.root).as_posix())
        return written

    def effective_policy(self) -> dict[str, Any]:
        effective: dict[str, Any] = {}
        for name in POLICY_LAYER_ORDER:
            data = self._load_layer(name)
            if isinstance(data, dict) and "_invalid" not in data:
                effective = _deep_merge(effective, data)
        for metadata_key in ("description", "layer", "generated_by", "created_at", "updated_at"):
            effective.pop(metadata_key, None)
        return effective

    def _validate_layer(
        self,
        name: str,
        data: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        compression = data.get("compression_by_role")
        if compression is not None:
            if not isinstance(compression, dict):
                errors.append({"layer": name, "code": "invalid_compression_by_role", "message": "compression_by_role must be an object."})
            else:
                for role, profile in compression.items():
                    if not isinstance(profile, dict):
                        errors.append({"layer": name, "code": "invalid_compression_profile", "message": f"{role} profile must be an object."})
                        continue
                    max_chars = profile.get("max_chars")
                    if max_chars is not None and (not isinstance(max_chars, int) or max_chars <= 0):
                        errors.append({"layer": name, "code": "invalid_max_chars", "message": f"{role}.max_chars must be a positive integer."})
                    preserve = profile.get("preserve")
                    if preserve is not None and not isinstance(preserve, list):
                        errors.append({"layer": name, "code": "invalid_preserve", "message": f"{role}.preserve must be a list."})
        for key in ("tool_policy", "proposal_gate", "governance_loop", "model_roles", "context"):
            value = data.get(key)
            if value is not None and not isinstance(value, dict):
                errors.append({"layer": name, "code": "invalid_policy_section", "message": f"{key} must be an object."})
        if name in {"tool_policy", "proposal_gate"} and not any(key in data for key in ("tool_policy", "proposal_gate")):
            warnings.append({"layer": name, "code": "empty_policy_layer", "message": "Policy file exists but does not define a known policy section."})

    def _extract_topic(self, data: dict[str, Any], topic: str) -> Any:
        if topic in data:
            return data[topic]
        for key, value in data.items():
            if isinstance(value, dict):
                if topic in value:
                    return value[topic]
                nested = self._extract_topic(value, topic)
                if nested is not None:
                    return nested
        return None

    def _why(self, topic: str, matches: list[dict[str, Any]]) -> str:
        if not matches:
            return f"No active policy layer explicitly defines `{topic}`."
        chain = " -> ".join(match["layer"] for match in matches)
        return f"`{topic}` is defined by policy layer precedence: {chain}. Later layers override earlier fields."

    def _load_layer(self, name: str) -> dict[str, Any]:
        path = self._policy_path(name)
        if path.exists():
            try:
                data = read_json(path, {})
            except Exception as exc:
                return {"layer": name, "_invalid": str(exc)}
            return data if isinstance(data, dict) else {}
        return dict(DEFAULT_POLICY_LAYERS.get(name, {}))

    def _policy_path(self, name: str) -> Path:
        return self._policy_dir() / f"{name}.json"

    def _policy_dir(self) -> Path:
        return self.config.paths.state / "policies"


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
