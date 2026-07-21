from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.agent.errors import AgentRunConflictError
from agent_memory_runtime.agent.runtime import BusinessAgentRuntime


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    runtime: BusinessAgentRuntime
    description: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("registered agent_id cannot be empty")


class AgentDefinitionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> None:
        if definition.agent_id in self._definitions:
            raise AgentRunConflictError(
                f"agent definition {definition.agent_id!r} is already registered"
            )
        self._definitions[definition.agent_id] = definition

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self._definitions.get(agent_id)

    def require(self, agent_id: str) -> AgentDefinition:
        definition = self.get(agent_id)
        if definition is None:
            raise AgentRunConflictError(f"agent {agent_id!r} is not registered")
        return definition

    def list_definitions(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._definitions[name] for name in sorted(self._definitions))
