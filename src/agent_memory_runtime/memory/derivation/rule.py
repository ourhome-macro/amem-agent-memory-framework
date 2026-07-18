from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate


class DerivationRule(Protocol):
    rule_id: str

    def derive(self, event: Event) -> list[MemoryCandidate]:
        ...


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    produced_memory_ids: tuple[str, ...]

