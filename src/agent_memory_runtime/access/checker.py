from __future__ import annotations

from agent_memory_runtime.access.policy import AccessDecision, AccessPolicy
from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.domain.memory import MemoryRecord


class AccessChecker:
    def __init__(self, policy: AccessPolicy | None = None) -> None:
        self.policy = policy or AccessPolicy()

    def check(self, principal: Principal, record: MemoryRecord) -> AccessDecision:
        return self.policy.decide(principal, record)

