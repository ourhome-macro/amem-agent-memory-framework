from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.domain.event import Event


@dataclass(frozen=True)
class ToolRequest:
    tool_name: str
    arguments: dict[str, Any]
    actor_id: str
    agent_id: str
    session_id: str = "default"
    request_id: str = field(default_factory=lambda: str(uuid4()))
    labels: tuple[str, ...] = ("private",)
    tags: tuple[str, ...] = ()
    tenant_id: str = "default"
    user_id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_hash: str | None = None
    output_hash: str = ""
    duration_ms: int = 0

    @classmethod
    def succeeded(
        cls,
        *,
        tool_name: str,
        output: dict[str, Any],
        duration_ms: int,
    ) -> ToolResult:
        return cls(
            tool_name=tool_name,
            status="succeeded",
            output=output,
            output_hash=secure_hash(output),
            duration_ms=duration_ms,
        )

    @classmethod
    def blocked(
        cls,
        *,
        tool_name: str,
        error: Exception,
        duration_ms: int,
    ) -> ToolResult:
        return cls(
            tool_name=tool_name,
            status="blocked",
            error_type=type(error).__name__,
            error_hash=secure_hash({"type": type(error).__name__, "message": str(error)}),
            output_hash=secure_hash({}),
            duration_ms=duration_ms,
        )

    @classmethod
    def failed(cls, *, tool_name: str, error: Exception, duration_ms: int) -> ToolResult:
        return cls(
            tool_name=tool_name,
            status="failed",
            error_type=type(error).__name__,
            error_hash=secure_hash({"type": type(error).__name__, "message": str(error)}),
            output_hash=secure_hash({}),
            duration_ms=duration_ms,
        )


@dataclass(frozen=True)
class ToolExecution:
    request: ToolRequest
    result: ToolResult
    event: Event


class Tool(Protocol):
    name: str
    description: str
    side_effects: bool

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ...
