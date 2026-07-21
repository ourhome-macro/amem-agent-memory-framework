from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.domain.enums import EventKind, MemoryLabel


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    kind: str
    actor_id: str
    session_id: str = "default"
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    sequence: int = 0
    occurred_at: str = field(default_factory=utc_now_iso)
    caused_by_event_id: str | None = None
    labels: tuple[str, ...] = (MemoryLabel.PUBLIC.value,)
    tags: tuple[str, ...] = ()
    # Appended to preserve the pre-v0.2 positional constructor contract.
    tenant_id: str = "default"
    user_id: str | None = None
    agent_id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Event:
        data = dict(value)
        if "type" in data and "kind" not in data:
            data["kind"] = data.pop("type")
        kind = str(data.get("kind", EventKind.NOTE.value))
        payload = _dict(data.get("payload"))
        return cls(
            event_id=str(data.get("event_id") or data.get("id") or uuid4()),
            sequence=int(data.get("sequence", 0)),
            kind=kind,
            actor_id=str(data.get("actor_id", "unknown")),
            session_id=str(data.get("session_id", "default")),
            tenant_id=str(data.get("tenant_id") or payload.get("tenant_id") or "default"),
            user_id=_optional_str(data.get("user_id") or payload.get("user_id")),
            agent_id=_optional_str(data.get("agent_id") or payload.get("agent_id")),
            payload=payload,
            occurred_at=str(data.get("occurred_at") or data.get("created_at") or utc_now_iso()),
            caused_by_event_id=_optional_str(data.get("caused_by_event_id")),
            labels=tuple(str(item) for item in data.get("labels", (MemoryLabel.PUBLIC.value,))),
            tags=tuple(str(item) for item in data.get("tags", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "occurred_at": self.occurred_at,
            "caused_by_event_id": self.caused_by_event_id,
            "labels": list(self.labels),
            "tags": list(self.tags),
        }

    def idempotency_payload(self) -> dict[str, Any]:
        """Return the immutable event identity, excluding store-assigned sequence."""
        value = self.to_dict()
        value.pop("sequence", None)
        return value

    def is_retry_of(self, other: Event) -> bool:
        return (
            self.event_id == other.event_id
            and self.idempotency_payload() == other.idempotency_payload()
        )


def _dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
