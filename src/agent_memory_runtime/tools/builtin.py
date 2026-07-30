from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from agent_memory_runtime.domain.enums import MemorySessionPolicy
from agent_memory_runtime.domain.query import MemoryQuery
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


@dataclass(frozen=True)
class MemorySearchTool:
    runtime: Any
    default_agent_id: str = "agent"
    default_tenant_id: str = "default"
    default_user_id: str | None = None
    default_session_id: str | None = None
    name: str = "memory.search"
    description: str = (
        "Search authorized memory for the current agent, tenant, user, and session."
    )
    side_effects: bool = False
    idempotent: bool = True
    risk_level: str = "low"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "session_policy": {
                "type": "string",
                "enum": [
                    MemorySessionPolicy.EXACT.value,
                    MemorySessionPolicy.PROFILE.value,
                    MemorySessionPolicy.ALL.value,
                ],
            },
            "scopes": {"type": "array", "items": {"type": "string"}},
            "memory_types": {"type": "array", "items": {"type": "string"}},
            "layers": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source_memory_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._project(
            arguments,
            agent_id=self.default_agent_id,
            tenant_id=self.default_tenant_id,
            user_id=self.default_user_id,
            session_id=_optional_text(arguments.get("session_id")) or self.default_session_id,
        )

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        request = context.request
        return self._project(
            arguments,
            agent_id=request.agent_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=_optional_text(arguments.get("session_id")) or request.session_id,
        )

    def _project(
        self,
        arguments: dict[str, Any],
        *,
        agent_id: str,
        tenant_id: str,
        user_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise ToolPolicyError("memory.search requires non-empty text")
        session_policy = str(
            arguments.get("session_policy") or MemorySessionPolicy.PROFILE.value
        )
        try:
            MemorySessionPolicy(session_policy)
        except ValueError as error:
            raise ToolPolicyError("memory.search session_policy is unsupported") from error
        limit = _bounded_limit(arguments.get("limit"), default=5, maximum=20)
        context = self.runtime.project(
            MemoryQuery(
                agent_id=agent_id,
                text=text,
                session_id=session_id,
                scopes=_string_tuple(arguments.get("scopes")),
                memory_types=_string_tuple(arguments.get("memory_types")),
                layers=_string_tuple(arguments.get("layers")),
                tags=_string_tuple(arguments.get("tags")),
                source_memory_ids=_string_tuple(arguments.get("source_memory_ids")),
                limit=limit,
                tenant_id=tenant_id,
                user_id=user_id,
                session_policy=session_policy,
            )
        )
        trace = context.trace
        return {
            "selected_memory_ids": list(context.selected_memory_ids),
            "blocked_memory_count": context.blocked_memory_count,
            "memories": [dict(item) for item in context.memories],
            "projected_context": context.projected_context,
            "personalization": dict(context.personalization),
            "metadata": {
                "context_source": context.metadata.get("context_source"),
                "estimated_memory_tokens": context.metadata.get("estimated_memory_tokens"),
                "memory_token_budget": context.metadata.get("memory_token_budget"),
            },
            "trace": {
                "candidate_count": trace.candidate_count,
                "retrieval_legs": list(trace.retrieval_legs),
                "semantic_timed_out": trace.semantic_timed_out,
                "semantic_error_type": trace.semantic_error_type,
            },
        }


def _safe_path(root: Path, user_path: str) -> Path:
    root_path = root.resolve()
    target = (root_path / user_path).resolve()
    if not target.is_relative_to(root_path):
        raise ToolPolicyError("file path escapes configured root")
    return target


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bounded_limit(value: object, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))
