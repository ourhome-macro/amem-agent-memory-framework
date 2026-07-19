from __future__ import annotations

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.stores.serialization import (
    envelope_from_llm_trace,
    llm_trace_from_envelope,
)


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._envelopes: list[AuditEnvelope] = []

    def append_envelope(self, envelope: AuditEnvelope) -> None:
        self._envelopes.append(AuditEnvelope.from_dict(envelope.to_dict()))

    def list_envelopes(self) -> list[AuditEnvelope]:
        return list(self._envelopes)

    def append_trace(self, trace: LLMCallTrace) -> None:
        self.append_envelope(envelope_from_llm_trace(trace))

    def list_traces(self) -> list[LLMCallTrace]:
        return [
            trace
            for envelope in self._envelopes
            if (trace := llm_trace_from_envelope(envelope)) is not None
        ]

    def clear(self) -> None:
        self._envelopes.clear()
