from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_memory_runtime.agent.errors import AgentRunConflictError
from agent_memory_runtime.agent.models import AgentRequest
from agent_memory_runtime.tools.base import Tool


class AgentModule(Protocol):
    """Business capability package independent from transport and host adapters."""

    name: str
    version: str

    def instructions(self, request: AgentRequest) -> Sequence[str]:
        ...

    def tools(self) -> Sequence[Tool]:
        ...


@dataclass(frozen=True)
class StaticAgentModule:
    name: str
    version: str = "1"
    system_instructions: tuple[str, ...] = ()
    module_tools: tuple[Tool, ...] = ()

    def instructions(self, request: AgentRequest) -> Sequence[str]:
        return self.system_instructions

    def tools(self) -> Sequence[Tool]:
        return self.module_tools


class AgentModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, AgentModule] = {}

    def register(self, module: AgentModule) -> None:
        name = module.name.strip()
        if not name:
            raise ValueError("agent module name cannot be empty")
        if name in self._modules:
            raise AgentRunConflictError(f"agent module {name!r} is already registered")
        self._modules[name] = module

    def get(self, name: str) -> AgentModule | None:
        return self._modules.get(name)

    def resolve(self, names: tuple[str, ...]) -> tuple[AgentModule, ...]:
        selected_names = tuple(dict.fromkeys(names)) if names else tuple(sorted(self._modules))
        missing = [name for name in selected_names if name not in self._modules]
        if missing:
            raise AgentRunConflictError(
                f"unknown agent module(s): {', '.join(sorted(missing))}"
            )
        return tuple(self._modules[name] for name in selected_names)

    def list_modules(self) -> tuple[AgentModule, ...]:
        return tuple(self._modules[name] for name in sorted(self._modules))

    def instructions_for(self, request: AgentRequest) -> tuple[str, ...]:
        instructions: list[str] = []
        for module in self.resolve(request.module_names):
            instructions.extend(str(item).strip() for item in module.instructions(request))
        return tuple(item for item in instructions if item)

    def tools_for(self, request: AgentRequest) -> dict[str, Tool]:
        tools: dict[str, Tool] = {}
        for module in self.resolve(request.module_names):
            for tool in module.tools():
                if tool.name in tools:
                    raise AgentRunConflictError(
                        f"tool {tool.name!r} is contributed by multiple active modules"
                    )
                tools[tool.name] = tool
        return tools
