from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from agent_memory_runtime.tools.policy import ToolPolicyError


@dataclass(frozen=True)
class FunctionTool:
    name: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    description: str = ""
    side_effects: bool = False
    input_schema: dict[str, Any] | None = None
    idempotent: bool | None = None
    risk_level: str | None = None
    requires_approval: bool = False

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return dict(self.handler(arguments))


@dataclass(frozen=True)
class FileReadTool:
    root: Path
    name: str = "file.read"
    description: str = "Read a UTF-8 text file inside the configured root."
    side_effects: bool = False
    idempotent: bool = True
    risk_level: str = "low"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = _safe_path(self.root, str(arguments.get("path", "")))
        if not target.is_file():
            raise ToolPolicyError("file does not exist")
        return {
            "path": str(target.relative_to(self.root.resolve())),
            "content": target.read_text(encoding="utf-8"),
        }


@dataclass(frozen=True)
class FileWriteTool:
    root: Path
    name: str = "file.write"
    description: str = "Write a UTF-8 text file inside the configured root."
    side_effects: bool = True
    idempotent: bool = True
    risk_level: str = "high"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = _safe_path(self.root, str(arguments.get("path", "")))
        content = str(arguments.get("content", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": str(target.relative_to(self.root.resolve())),
            "bytes_written": len(content.encode("utf-8")),
        }


class WebSearchProvider(Protocol):
    def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        ...


@dataclass(frozen=True)
class StaticWebSearchProvider:
    results: list[dict[str, str]]

    def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return [dict(item) for item in self.results[:max_results]]


@dataclass(frozen=True)
class WebSearchTool:
    provider: WebSearchProvider
    name: str = "web.search"
    description: str = "Search the web through a configured provider."
    side_effects: bool = False
    idempotent: bool = True
    risk_level: str = "low"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ToolPolicyError("web.search requires a non-empty query")
        max_results = max(1, min(int(arguments.get("max_results", 5)), 10))
        return {"results": self.provider.search(query, max_results=max_results)}


def _safe_path(root: Path, user_path: str) -> Path:
    root_path = root.resolve()
    target = (root_path / user_path).resolve()
    if not target.is_relative_to(root_path):
        raise ToolPolicyError("file path escapes configured root")
    return target
