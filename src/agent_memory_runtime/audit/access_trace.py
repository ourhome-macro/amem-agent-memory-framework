from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.audit.subject import AuditSubject
from agent_memory_runtime.domain.query import RetrievalTrace


@dataclass(frozen=True)
class AccessTrace:
    agent_id: str
    action: str
    query_hash: str
    selected_memory_ids: tuple[str, ...]
    blocked_memory_count: int
    blocked_memory_ids: tuple[str, ...]
    blocked_reasons: dict[str, str]
    context_source: str
    retrieval_timed_out: bool
    retrieval_legs: tuple[str, ...] = ()
    lexical_candidate_count: int = 0
    semantic_candidate_count: int = 0
    semantic_generation: str | None = None
    semantic_timed_out: bool = False
    semantic_error_type: str | None = None

    @classmethod
    def from_retrieval(
        cls,
        trace: RetrievalTrace,
        *,
        action: str,
        selected_memory_ids: tuple[str, ...],
        context_source: str,
        retrieval_timed_out: bool,
    ) -> AccessTrace:
        blocked = {
            result.memory_id: result.blocked_reason or "blocked"
            for result in trace.results
            if result.blocked
        }
        return cls(
            agent_id=trace.query.agent_id,
            action=action,
            query_hash=secure_hash(trace.query.text),
            selected_memory_ids=selected_memory_ids,
            blocked_memory_count=trace.blocked_count,
            blocked_memory_ids=tuple(blocked),
            blocked_reasons=blocked,
            context_source=context_source,
            retrieval_timed_out=retrieval_timed_out,
            retrieval_legs=trace.retrieval_legs,
            lexical_candidate_count=trace.lexical_candidate_count,
            semantic_candidate_count=trace.semantic_candidate_count,
            semantic_generation=trace.semantic_generation,
            semantic_timed_out=trace.semantic_timed_out,
            semantic_error_type=trace.semantic_error_type,
        )

    def to_envelope(
        self,
        *,
        rule_version: str,
        config_hash: str,
        last_event_sequence: int,
        state_hash: str,
    ) -> AuditEnvelope:
        return AuditEnvelope(
            audit_type="access",
            actor_id=self.agent_id,
            action=self.action,
            outcome="blocked" if self.blocked_memory_count else "allowed",
            decision=(
                AuditDecision.BLOCK.value
                if self.blocked_memory_count
                else AuditDecision.ALLOW.value
            ),
            subject=AuditSubject(
                subject_type="query",
                subject_id=self.query_hash[:16],
                content_hash=self.query_hash,
            ),
            rule_version=rule_version,
            config_hash=config_hash,
            last_event_sequence=last_event_sequence,
            state_hash=state_hash,
            payload={
                "selected_memory_ids": list(self.selected_memory_ids),
                "blocked_memory_count": self.blocked_memory_count,
                "blocked_memory_ids": list(self.blocked_memory_ids),
                "blocked_reasons": self.blocked_reasons,
                "context_source": self.context_source,
                "retrieval_timed_out": self.retrieval_timed_out,
                "query_hash": self.query_hash,
                "retrieval_legs": list(self.retrieval_legs),
                "lexical_candidate_count": self.lexical_candidate_count,
                "semantic_candidate_count": self.semantic_candidate_count,
                "semantic_generation": self.semantic_generation,
                "semantic_timed_out": self.semantic_timed_out,
                "semantic_error_type": self.semantic_error_type,
            },
        )
