from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.redaction import redact_audit_payload
from agent_memory_runtime.audit.subject import AuditSubject


@dataclass(frozen=True)
class AuditEnvelope:
    audit_type: str
    actor_id: str
    action: str
    outcome: str
    subject: AuditSubject
    rule_version: str
    config_hash: str
    state_hash: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    decision: str = AuditDecision.OBSERVE.value
    last_event_sequence: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AuditEnvelope:
        return cls(
            audit_type=str(value["audit_type"]),
            trace_id=str(value["trace_id"]),
            occurred_at=str(value["occurred_at"]),
            actor_id=str(value["actor_id"]),
            action=str(value["action"]),
            outcome=str(value["outcome"]),
            decision=str(value.get("decision", AuditDecision.OBSERVE.value)),
            subject=AuditSubject.from_dict(dict(value["subject"])),
            rule_version=str(value.get("rule_version", "")),
            config_hash=str(value.get("config_hash", "")),
            last_event_sequence=int(value.get("last_event_sequence", 0)),
            state_hash=str(value.get("state_hash", "")),
            payload=dict(redact_audit_payload(value.get("payload", {}))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_type": self.audit_type,
            "trace_id": self.trace_id,
            "occurred_at": self.occurred_at,
            "actor_id": self.actor_id,
            "action": self.action,
            "outcome": self.outcome,
            "decision": self.decision,
            "subject": self.subject.to_dict(),
            "rule_version": self.rule_version,
            "config_hash": self.config_hash,
            "last_event_sequence": self.last_event_sequence,
            "state_hash": self.state_hash,
            "payload": redact_audit_payload(self.payload),
        }
