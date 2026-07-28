from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_memory_runtime.domain.event import Event


@dataclass(frozen=True)
class MemoryToolIdentity:
    actor_id: str
    agent_id: str
    session_id: str = "default"
    tenant_id: str = "default"
    user_id: str | None = None
    labels: tuple[str, ...] = ("private",)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryToolResult:
    status: str
    action: str
    event: Event | None = None
    memory_ids: tuple[str, ...] = ()
    tombstoned_memory_ids: tuple[str, ...] = ()
    archived_memory_ids: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "event_id": None if self.event is None else self.event.event_id,
            "event_kind": None if self.event is None else self.event.kind,
            "memory_ids": list(self.memory_ids),
            "tombstoned_memory_ids": list(self.tombstoned_memory_ids),
            "archived_memory_ids": list(self.archived_memory_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DreamCheckpoint:
    last_processed_sequence: int = 0
    last_state_hash: str | None = None
    dream_version: str = "auto-dream-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_processed_sequence": self.last_processed_sequence,
            "last_state_hash": self.last_state_hash,
            "dream_version": self.dream_version,
        }


@dataclass(frozen=True)
class DreamProposal:
    proposal_id: str
    action: str
    kind: str | None
    key: str | None
    content: str
    confidence: float
    salience: float
    evidence_event_ids: tuple[str, ...] = ()
    target_memory_id: str | None = None
    reason: str = ""
    recommended_action: str = "review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "kind": self.kind,
            "key": self.key,
            "content": self.content,
            "confidence": self.confidence,
            "salience": self.salience,
            "evidence_event_ids": list(self.evidence_event_ids),
            "target_memory_id": self.target_memory_id,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
        }


@dataclass(frozen=True)
class AutoDreamReport:
    source_sequence_range: tuple[int, int] | None
    base_state_hash: str
    proposals: tuple[DreamProposal, ...] = ()
    checkpoint: DreamCheckpoint = field(default_factory=DreamCheckpoint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sequence_range": (
                None
                if self.source_sequence_range is None
                else list(self.source_sequence_range)
            ),
            "base_state_hash": self.base_state_hash,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "checkpoint": self.checkpoint.to_dict(),
        }
