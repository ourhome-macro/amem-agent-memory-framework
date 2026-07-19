from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.tools.base import Tool, ToolRequest


class ToolPolicyError(Exception):
    pass


@dataclass(frozen=True)
class ToolPolicy:
    allowed_tools: frozenset[str] | None = None
    blocked_tools: frozenset[str] = frozenset()
    allow_side_effects: bool = True

    def __init__(
        self,
        *,
        allowed_tools: set[str] | frozenset[str] | None = None,
        blocked_tools: set[str] | frozenset[str] = frozenset(),
        allow_side_effects: bool = True,
    ) -> None:
        object.__setattr__(
            self,
            "allowed_tools",
            None if allowed_tools is None else frozenset(allowed_tools),
        )
        object.__setattr__(self, "blocked_tools", frozenset(blocked_tools))
        object.__setattr__(self, "allow_side_effects", allow_side_effects)

    def authorize(self, request: ToolRequest, tool: Tool | None) -> None:
        if tool is None:
            raise ToolPolicyError(f"tool {request.tool_name} is not registered")
        if self.allowed_tools is not None and request.tool_name not in self.allowed_tools:
            raise ToolPolicyError(f"tool {request.tool_name} is not allowed")
        if request.tool_name in self.blocked_tools:
            raise ToolPolicyError(f"tool {request.tool_name} is blocked")
        if tool.side_effects and not self.allow_side_effects:
            raise ToolPolicyError(f"tool {request.tool_name} has side effects")
