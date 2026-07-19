from __future__ import annotations

from typing import Any

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.subject import AuditSubject


def envelope_from_llm_trace(trace: LLMCallTrace) -> AuditEnvelope:
    return AuditEnvelope(
        audit_type="llm_call",
        trace_id=trace.trace_id,
        occurred_at=trace.occurred_at,
        actor_id=trace.agent_id,
        action="llm_completion",
        outcome=trace.outcome,
        decision=(
            AuditDecision.ALLOW.value
            if trace.outcome == "completed"
            else AuditDecision.BLOCK.value
        ),
        subject=AuditSubject(
            subject_type="llm_call",
            subject_id=trace.response_id or trace.trace_id,
            content_hash=trace.request_hash,
        ),
        rule_version=trace.rule_version,
        config_hash=trace.config_hash,
        last_event_sequence=trace.last_event_sequence,
        state_hash=trace.state_hash,
        payload={"trace": trace.to_dict()},
    )


def envelope_from_dict_or_legacy(value: dict[str, Any]) -> AuditEnvelope:
    if "audit_type" in value:
        return AuditEnvelope.from_dict(value)
    return envelope_from_llm_trace(LLMCallTrace.from_dict(value))


def llm_trace_from_envelope(envelope: AuditEnvelope) -> LLMCallTrace | None:
    if envelope.audit_type != "llm_call":
        return None
    trace_payload = envelope.payload.get("trace")
    if not isinstance(trace_payload, dict):
        return None
    return LLMCallTrace.from_dict(trace_payload)
