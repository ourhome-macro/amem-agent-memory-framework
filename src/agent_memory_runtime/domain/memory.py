from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_memory_runtime.domain.enums import (
    MemoryLabel,
    MemoryLayer,
    MemoryOperation,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)


@dataclass(frozen=True)
class MemoryCandidate:
    memory_id: str
    memory_type: str
    scope: str
    layer: str
    session_id: str
    subject_id: str
    content: str
    source_event_ids: tuple[str, ...]
    rule_id: str
    operation: str = MemoryOperation.CREATE.value
    owner_id: str | None = None
    visible_to: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    labels: tuple[str, ...] = (MemoryLabel.PUBLIC.value,)
    tags: tuple[str, ...] = ()
    salience: float = 0.5
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "scope": self.scope,
            "layer": self.layer,
            "session_id": self.session_id,
            "subject_id": self.subject_id,
            "content": self.content,
            "source_event_ids": list(self.source_event_ids),
            "rule_id": self.rule_id,
            "operation": self.operation,
            "owner_id": self.owner_id,
            "visible_to": list(self.visible_to),
            "source_memory_ids": list(self.source_memory_ids),
            "labels": list(self.labels),
            "tags": list(self.tags),
            "salience": self.salience,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    memory_type: str
    scope: str
    layer: str
    session_id: str
    subject_id: str
    content: str
    source_event_ids: tuple[str, ...]
    rule_id: str
    owner_id: str | None = None
    visible_to: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    labels: tuple[str, ...] = (MemoryLabel.PUBLIC.value,)
    tags: tuple[str, ...] = ()
    salience: float = 0.5
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = MemoryStatus.ACTIVE.value
    reinforcement_count: int = 1
    created_at: str = ""
    updated_at: str = ""
    last_event_sequence: int = 0
    last_operation: str = MemoryOperation.CREATE.value

    @classmethod
    def from_candidate(cls, candidate: MemoryCandidate, *, now: str, sequence: int) -> MemoryRecord:
        return cls(
            memory_id=candidate.memory_id,
            memory_type=candidate.memory_type,
            scope=candidate.scope,
            layer=candidate.layer,
            session_id=candidate.session_id,
            subject_id=candidate.subject_id,
            content=candidate.content,
            source_event_ids=tuple(candidate.source_event_ids),
            rule_id=candidate.rule_id,
            owner_id=candidate.owner_id,
            visible_to=tuple(candidate.visible_to),
            source_memory_ids=tuple(candidate.source_memory_ids),
            labels=tuple(candidate.labels),
            tags=tuple(candidate.tags),
            salience=_clamp(candidate.salience),
            confidence=_clamp(candidate.confidence),
            metadata=dict(candidate.metadata),
            status=MemoryStatus.ACTIVE.value,
            reinforcement_count=1,
            created_at=now,
            updated_at=now,
            last_event_sequence=sequence,
            last_operation=candidate.operation,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemoryRecord:
        return cls(
            memory_id=str(value["memory_id"]),
            memory_type=str(value.get("memory_type", MemoryType.EPISODIC.value)),
            scope=str(value.get("scope", MemoryScope.PRIVATE.value)),
            layer=str(value.get("layer", MemoryLayer.WORKING.value)),
            session_id=str(value.get("session_id", "default")),
            subject_id=str(value.get("subject_id", "")),
            content=str(value.get("content", "")),
            source_event_ids=tuple(str(item) for item in value.get("source_event_ids", ())),
            rule_id=str(value.get("rule_id", "")),
            owner_id=_optional_str(value.get("owner_id")),
            visible_to=tuple(str(item) for item in value.get("visible_to", ())),
            source_memory_ids=tuple(str(item) for item in value.get("source_memory_ids", ())),
            labels=tuple(str(item) for item in value.get("labels", (MemoryLabel.PUBLIC.value,))),
            tags=tuple(str(item) for item in value.get("tags", ())),
            salience=_clamp(float(value.get("salience", 0.5))),
            confidence=_clamp(float(value.get("confidence", 1.0))),
            metadata=dict(value.get("metadata", {})),
            status=str(value.get("status", MemoryStatus.ACTIVE.value)),
            reinforcement_count=int(value.get("reinforcement_count", 1)),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            last_event_sequence=int(value.get("last_event_sequence", 0)),
            last_operation=str(value.get("last_operation", MemoryOperation.CREATE.value)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "scope": self.scope,
            "layer": self.layer,
            "session_id": self.session_id,
            "subject_id": self.subject_id,
            "content": self.content,
            "source_event_ids": list(self.source_event_ids),
            "rule_id": self.rule_id,
            "owner_id": self.owner_id,
            "visible_to": list(self.visible_to),
            "source_memory_ids": list(self.source_memory_ids),
            "labels": list(self.labels),
            "tags": list(self.tags),
            "salience": self.salience,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "status": self.status,
            "reinforcement_count": self.reinforcement_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_sequence": self.last_event_sequence,
            "last_operation": self.last_operation,
        }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 4)

