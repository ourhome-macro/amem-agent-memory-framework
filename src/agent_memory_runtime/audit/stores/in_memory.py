from __future__ import annotations

from typing import TYPE_CHECKING

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.stores.serialization import (
    envelope_from_llm_trace,
    llm_trace_from_envelope,
)

if TYPE_CHECKING:
    from agent_memory_runtime.memory.intake.models import MemoryAuditLog


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._envelopes: list[AuditEnvelope] = []
        self._memory_logs: list[MemoryAuditLog] = []

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

    def append_memory_log(self, log: MemoryAuditLog) -> None:
        self._memory_logs.append(_memory_log_from_dict(log.to_dict()))

    def list_memory_logs(self) -> list[MemoryAuditLog]:
        return [_memory_log_from_dict(log.to_dict()) for log in self._memory_logs]

    def clear(self) -> None:
        self._envelopes.clear()
        self._memory_logs.clear()


def _memory_log_from_dict(value: dict[str, object]) -> MemoryAuditLog:
    from agent_memory_runtime.domain.memory import MemoryRecord
    from agent_memory_runtime.memory.intake.models import MemoryAuditLog

    before = value.get("before_record")
    after = value.get("after_record")
    return MemoryAuditLog(
        audit_id=str(value["audit_id"]),
        memory_id=None if value.get("memory_id") is None else str(value["memory_id"]),
        proposal_id=str(value["proposal_id"]),
        action=str(value["action"]),
        actor_id=str(value["actor_id"]),
        agent_id=None if value.get("agent_id") is None else str(value["agent_id"]),
        tenant_id=str(value.get("tenant_id") or "default"),
        user_id=None if value.get("user_id") is None else str(value["user_id"]),
        before_record=(
            None if before is None else MemoryRecord.from_dict(dict(before))  # type: ignore[arg-type]
        ),
        after_record=(
            None if after is None else MemoryRecord.from_dict(dict(after))  # type: ignore[arg-type]
        ),
        source_message_ids=tuple(str(item) for item in value.get("source_message_ids", ())),
        source_memory_ids=tuple(str(item) for item in value.get("source_memory_ids", ())),
        evidence_text=str(value.get("evidence_text") or ""),
        confidence=float(value.get("confidence") or 0.0),
        reason=str(value.get("reason") or ""),
        created_at=str(value.get("created_at") or ""),
    )
