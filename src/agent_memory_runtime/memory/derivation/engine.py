from __future__ import annotations

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate
from agent_memory_runtime.memory.derivation.registry import DerivationRegistry


class DerivationEngine:
    def __init__(self, registry: DerivationRegistry | None = None) -> None:
        self.registry = registry or DerivationRegistry()

    def derive(self, event: Event) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for rule in self.registry.list_rules():
            candidates.extend(rule.derive(event))
        return candidates

