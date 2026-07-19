from __future__ import annotations

from typing import Protocol

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace


class AuditStore(Protocol):
    def append_envelope(self, envelope: AuditEnvelope) -> None:
        ...

    def list_envelopes(self) -> list[AuditEnvelope]:
        ...

    def append_trace(self, trace: LLMCallTrace) -> None:
        ...

    def list_traces(self) -> list[LLMCallTrace]:
        ...

    def clear(self) -> None:
        ...
