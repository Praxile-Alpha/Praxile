from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import Config
from ..context_profiles import DEFAULT_ROLE_PROFILES
from ..store import ExperienceStore
from ..utils import new_id, read_json, shorten, utc_now, write_json
from .errors import ServiceError


class ContextJuiceService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def profiles(self) -> dict[str, dict[str, Any]]:
        profiles = dict(DEFAULT_ROLE_PROFILES)
        policy = _safe_read_json(self.config.paths.state / "policies" / "context.json", {})
        if isinstance(policy, dict):
            custom = policy.get("compression_by_role")
            if isinstance(custom, dict):
                for role, profile in custom.items():
                    if isinstance(profile, dict):
                        profiles[str(role)] = {**profiles.get(str(role), {}), **profile}
        config_profiles = self.config.get("context", "compression_by_role", default={}) or {}
        if isinstance(config_profiles, dict):
            for role, profile in config_profiles.items():
                if isinstance(profile, dict):
                    profiles[str(role)] = {**profiles.get(str(role), {}), **profile}
        return profiles

    def status(self) -> dict[str, Any]:
        output_dir = self._output_dir()
        outputs = []
        if output_dir.exists():
            for path in sorted(output_dir.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
                data = _safe_read_json(path, {})
                if isinstance(data, dict):
                    outputs.append(
                        {
                            "compression_id": data.get("compression_id") or path.stem,
                            "role": data.get("role"),
                            "source_type": data.get("source_type"),
                            "ratio": data.get("ratio"),
                            "created_at": data.get("created_at"),
                            "path": str(path.relative_to(self.config.paths.root)),
                        }
                    )
        return {
            "profiles": self.profiles(),
            "latest": outputs[0] if outputs else None,
            "outputs": outputs[:20],
        }

    def compress_file(self, source: str, *, role: str = "coding_agent", write: bool = True) -> dict[str, Any]:
        path = (self.config.paths.root / source).resolve()
        try:
            path.relative_to(self.config.paths.root.resolve())
        except ValueError as exc:
            raise ServiceError(400, "source path escapes project root") from exc
        if not path.exists() or not path.is_file():
            raise ServiceError(404, "source file not found")
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.compress_text(
            text,
            source_type="file",
            source_format=self._infer_format(path.name, text),
            role=role,
            source_id=str(path.relative_to(self.config.paths.root)),
            write=write,
        )

    def compress_run(self, run_id: str = "latest", *, role: str = "proposal_composer", write: bool = True) -> dict[str, Any]:
        trajectory = self.store.latest_trajectory() if run_id == "latest" else self.store.get_trajectory(run_id)
        if not trajectory:
            raise ServiceError(404, "run not found")
        text = self._trajectory_text(trajectory)
        task_id = str(trajectory.get("task_id") or run_id)
        return self.compress_text(text, source_type="run", role=role, source_id=task_id, write=write)

    def compress_text(
        self,
        text: str,
        *,
        source_type: str,
        role: str,
        source_id: str | None = None,
        source_format: str | None = None,
        write: bool = True,
    ) -> dict[str, Any]:
        profile = self.profiles().get(role, self.profiles().get("coding_agent", {}))
        max_chars = int(profile.get("max_chars") or 30000)
        raw_chars = len(text)
        source_format = source_format or self._infer_format(source_id or source_type, text)
        preserved = self._preserved_evidence(text)
        body, strategy = self._compress_body(text, max_chars=max_chars, source_format=source_format)
        markdown = self._format_markdown(source_type=source_type, source_id=source_id, role=role, profile=profile, preserved=preserved, body=body)
        compressed_chars = len(markdown)
        compression_id = new_id("cj")
        payload = {
            "compression_id": compression_id,
            "kind": "context_juice",
            "source_type": source_type,
            "source_format": source_format,
            "strategy": strategy,
            "source_id": source_id,
            "role": role,
            "profile": profile,
            "created_at": utc_now(),
            "raw_chars": raw_chars,
            "compressed_chars": compressed_chars,
            "ratio": round(compressed_chars / raw_chars, 4) if raw_chars else 0.0,
            "estimated_savings": round(max(0.0, 1 - (compressed_chars / raw_chars)), 4) if raw_chars else 0.0,
            "preserved_evidence": preserved,
            "markdown": markdown,
        }
        if write:
            md_path = self._output_dir() / f"{compression_id}.md"
            json_path = self._output_dir() / f"{compression_id}.json"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(markdown, encoding="utf-8")
            write_json(json_path, {**payload, "markdown_path": str(md_path.relative_to(self.config.paths.root))})
            write_json(self._output_dir() / "latest.json", {**payload, "markdown_path": str(md_path.relative_to(self.config.paths.root))})
            payload["path"] = str(json_path.relative_to(self.config.paths.root))
            payload["markdown_path"] = str(md_path.relative_to(self.config.paths.root))
        return payload

    def _compress_body(self, text: str, *, max_chars: int, source_format: str) -> tuple[str, str]:
        normalized = self._normalize_source(text, source_format=source_format)
        if len(normalized) <= max_chars:
            return normalized, f"{source_format}-preserve"
        if source_format == "diff":
            compressed = self._compress_diff(normalized, max_chars=max_chars)
            return compressed, "diff-hunks-and-file-headers"
        if source_format == "test_log":
            compressed = self._compress_test_log(normalized, max_chars=max_chars)
            return compressed, "failing-tests-and-error-context"
        if source_format in {"markdown", "html"}:
            compressed = self._compress_headings(normalized, max_chars=max_chars)
            return compressed, f"{source_format}-heading-aware"
        return self._compress_generic(normalized, max_chars=max_chars), "important-lines-head-tail"

    def _compress_generic(self, text: str, *, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        lines = text.splitlines()
        important = [line for line in lines if _IMPORTANT_RE.search(line)]
        head = "\n".join(lines[:80])
        tail = "\n".join(lines[-80:])
        middle = "\n".join(important[:240])
        return shorten("\n\n".join(part for part in [head, "## Preserved Important Lines\n" + middle if middle else "", tail] if part), max_chars)

    def _compress_diff(self, text: str, *, max_chars: int) -> str:
        kept: list[str] = []
        for line in text.splitlines():
            if line.startswith(("diff --git", "--- ", "+++ ", "@@", "+", "-", "rename ", "new file", "deleted file")):
                kept.append(line)
            if len("\n".join(kept)) >= max_chars:
                break
        return shorten("\n".join(kept) or text, max_chars)

    def _compress_test_log(self, text: str, *, max_chars: int) -> str:
        lines = text.splitlines()
        kept: list[str] = []
        for index, line in enumerate(lines):
            if _IMPORTANT_RE.search(line) or _COMMAND_RE.search(line) or "::" in line:
                start = max(0, index - 2)
                end = min(len(lines), index + 3)
                kept.extend(lines[start:end])
                kept.append("---")
            if len("\n".join(kept)) >= max_chars:
                break
        return shorten("\n".join(_dedupe_preserve_order(kept)) or self._compress_generic(text, max_chars=max_chars), max_chars)

    def _compress_headings(self, text: str, *, max_chars: int) -> str:
        lines = text.splitlines()
        kept = [line for line in lines if line.lstrip().startswith("#") or _IMPORTANT_RE.search(line) or _PATH_RE.search(line)]
        if len("\n".join(kept)) < max_chars // 2:
            kept = lines[:80] + ["", "## Important Lines", *kept[:240], "", "## Tail", *lines[-40:]]
        return shorten("\n".join(_dedupe_preserve_order(kept)) or text, max_chars)

    def _normalize_source(self, text: str, *, source_format: str) -> str:
        if source_format != "html":
            return text
        cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
        cleaned = re.sub(r"(?i)</h([1-6])>", "\n", cleaned)
        cleaned = re.sub(r"(?i)<h([1-6])[^>]*>", lambda match: "\n" + "#" * int(match.group(1)) + " ", cleaned)
        cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"(?i)</p>", "\n\n", cleaned)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _infer_format(self, name: str, text: str) -> str:
        lower = name.lower()
        sample = text[:4000].lower()
        if lower.endswith((".html", ".htm")) or "<html" in sample:
            return "html"
        if lower.endswith((".diff", ".patch")) or "diff --git" in sample or "\n@@" in sample:
            return "diff"
        if lower.endswith((".md", ".mdx", ".rst")):
            return "markdown"
        if "traceback" in sample or "failed " in sample or "pytest" in sample or lower.endswith((".log", ".txt")):
            return "test_log"
        if lower.endswith((".json", ".jsonl")):
            return "json"
        return "plain"

    def _preserved_evidence(self, text: str) -> dict[str, Any]:
        commands = sorted(set(match.group(0).strip() for match in _COMMAND_RE.finditer(text)))[:30]
        paths = sorted(set(match.group(0).strip("`'\"") for match in _PATH_RE.finditer(text)))[:80]
        signatures = sorted(set(line.strip() for line in text.splitlines() if _IMPORTANT_RE.search(line)))[:80]
        diff_hunks = sorted(set(line.strip() for line in text.splitlines() if line.startswith("@@")))[:40]
        return {
            "commands": commands,
            "file_paths": paths,
            "error_signatures": signatures,
            "diff_hunks": diff_hunks,
            "line_count": len(text.splitlines()),
        }

    def _trajectory_text(self, trajectory: dict[str, Any]) -> str:
        parts = [
            f"# Run {trajectory.get('task_id')}",
            f"Task: {trajectory.get('user_task')}",
            f"Result: {trajectory.get('result')}",
            f"Reward: {trajectory.get('reward_report')}",
            f"Silent failures: {trajectory.get('silent_failure_signals')}",
            f"Loaded assets: {trajectory.get('loaded_assets')}",
            f"Diff summary: {trajectory.get('diff_summary')}",
        ]
        for action in trajectory.get("actions") or []:
            if not isinstance(action, dict):
                continue
            observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
            parts.append(f"## Action {action.get('step')} {action.get('action_type')}\nInput: {action.get('input')}\nStatus: {action.get('status')}\nOutput:\n{observation.get('output')}")
        return "\n\n".join(parts)

    def _format_markdown(
        self,
        *,
        source_type: str,
        source_id: str | None,
        role: str,
        profile: dict[str, Any],
        preserved: dict[str, Any],
        body: str,
    ) -> str:
        return (
            f"# ContextJuice: {source_type} {source_id or ''}\n\n"
            f"- role: `{role}`\n"
            f"- preserve: {', '.join(profile.get('preserve') or [])}\n"
            f"- generated_at: {utc_now()}\n\n"
            "## Preserved Evidence Metadata\n\n"
            f"- commands: {', '.join(f'`{item}`' for item in preserved.get('commands', [])[:12]) or '(none)'}\n"
            f"- file paths: {', '.join(f'`{item}`' for item in preserved.get('file_paths', [])[:18]) or '(none)'}\n"
            f"- error signatures: {len(preserved.get('error_signatures') or [])}\n\n"
            "## Compressed Context\n\n"
            f"{body}\n"
        )

    def _output_dir(self) -> Path:
        return self.config.paths.state / "context" / "summaries"


_IMPORTANT_RE = re.compile(r"(error|failed|failure|traceback|exception|assert|exit code|returncode|diff --git|@@|warning|blocked)", re.I)
_PATH_RE = re.compile(r"\b(?:(?:[A-Za-z0-9_.-]+/)+)?[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx|go|rs|md|json|toml|yaml|yml|html|css|log|txt)\b")
_COMMAND_RE = re.compile(r"\b(?:python -m pytest|pytest|npm (?:run )?\w+|pnpm (?:run )?\w+|yarn \w+|go test ./\.\.\.|cargo test)\b")


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _safe_read_json(path: Path, default: Any = None) -> Any:
    try:
        return read_json(path, default)
    except (OSError, ValueError):
        return default
