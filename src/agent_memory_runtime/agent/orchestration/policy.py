from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.agent.errors import AgentPolicyError
from agent_memory_runtime.agent.orchestration.models import OrchestrationRequest


@dataclass(frozen=True)
class OrchestrationPolicy:
    allowed_agents: frozenset[str] | None = None
    max_nodes: int = 16
    max_fan_out: int = 8
    max_depth: int = 3
    max_parallelism: int = 4
    max_total_tokens: int = 250_000
    max_dependency_payload_chars: int = 200_000
    timeout_seconds: float = 600.0
    lease_seconds: float = 30.0
    busy_retry_seconds: float = 0.25

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_agents",
            None if self.allowed_agents is None else frozenset(self.allowed_agents),
        )
        limits = (
            self.max_nodes,
            self.max_fan_out,
            self.max_depth,
            self.max_parallelism,
            self.max_total_tokens,
            self.max_dependency_payload_chars,
        )
        if any(value <= 0 for value in limits):
            raise ValueError("orchestration policy limits must be positive")
        if self.timeout_seconds <= 0 or self.lease_seconds <= 0:
            raise ValueError("orchestration timeouts and leases must be positive")
        if self.busy_retry_seconds < 0:
            raise ValueError("orchestration busy retry delay cannot be negative")

    def validate(self, request: OrchestrationRequest) -> None:
        graph = request.graph
        if len(graph.tasks) > self.max_nodes:
            raise AgentPolicyError("orchestration graph exceeds max_nodes")
        if graph.maximum_fan_out > self.max_fan_out:
            raise AgentPolicyError("orchestration graph exceeds max_fan_out")
        if graph.maximum_depth > self.max_depth:
            raise AgentPolicyError("orchestration graph exceeds max_depth")
        if request.depth > self.max_depth:
            raise AgentPolicyError("orchestration request exceeds max_depth")
        if self.allowed_agents is not None:
            blocked = sorted(
                {task.agent_id for task in graph.tasks} - self.allowed_agents
            )
            if blocked:
                raise AgentPolicyError(
                    f"orchestration uses disallowed agents: {', '.join(blocked)}"
                )
