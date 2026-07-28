from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace

if TYPE_CHECKING:
    from agent_memory_runtime.memory.intake.models import MemoryAuditLog


class AuditStore(Protocol):
    def append_envelope(self, envelope: AuditEnvelope) -> None:
        ...

    def list_envelopes(self) -> list[AuditEnvelope]:
        ...

    def append_trace(self, trace: LLMCallTrace) -> None:
        ...

    def list_traces(self) -> list[LLMCallTrace]:
        ...

    def append_memory_log(self, log: MemoryAuditLog) -> None:
        ...

    def list_memory_logs(self) -> list[MemoryAuditLog]:
        ...

    def clear(self) -> None:
        ...
