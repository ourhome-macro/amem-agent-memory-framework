from __future__ import annotations

from dataclasses import dataclass, field

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.subject import AuditSubject


@dataclass(frozen=True)
class ModerationTrace:
    actor_id: str
    subject_type: str
    subject_id: str
    decision: str
    rule_hits: tuple[str, ...] = ()
    classifier_scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""

    def to_envelope(
        self,
        *,
        rule_version: str,
        config_hash: str,
        last_event_sequence: int,
        state_hash: str,
    ) -> AuditEnvelope:
        return AuditEnvelope(
            audit_type="moderation",
            actor_id=self.actor_id,
            action="moderate_content",
            outcome=self.decision,
            decision=self.decision,
            subject=AuditSubject(subject_type=self.subject_type, subject_id=self.subject_id),
            rule_version=rule_version,
            config_hash=config_hash,
            last_event_sequence=last_event_sequence,
            state_hash=state_hash,
            payload={
                "rule_hits": list(self.rule_hits),
                "classifier_scores": self.classifier_scores,
                "reason": self.reason,
                "implemented": self.decision != AuditDecision.OBSERVE.value,
            },
        )
