from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryTombstone:
    memory_id: str
    tenant_id: str
    deleted_through_sequence: int
    deleted_at: str
    reason: str
    source_event_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "deleted_through_sequence": self.deleted_through_sequence,
            "deleted_at": self.deleted_at,
            "reason": self.reason,
            "source_event_ids": list(self.source_event_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemoryTombstone:
        return cls(
            memory_id=str(value["memory_id"]),
            tenant_id=str(value.get("tenant_id") or "default"),
            deleted_through_sequence=int(value.get("deleted_through_sequence") or 0),
            deleted_at=str(value.get("deleted_at") or ""),
            reason=str(value.get("reason") or "deleted"),
            source_event_ids=tuple(str(item) for item in value.get("source_event_ids", ())),
            metadata=dict(value.get("metadata", {})),
        )
