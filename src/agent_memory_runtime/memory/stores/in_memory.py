from __future__ import annotations

from copy import deepcopy

from agent_memory_runtime.audit.stores.in_memory import InMemoryAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord

__all__ = [
    "InMemoryAuditStore",
    "InMemoryEventStore",
    "InMemoryMemoryStore",
    "InMemorySnapshotStore",
]


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> Event:
        sequence = event.sequence or len(self._events) + 1
        stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
        self._events.append(stored)
        return stored

    def list_events(self) -> list[Event]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def upsert(self, record: MemoryRecord) -> None:
        self._records[record.memory_id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._records.get(memory_id)

    def list_records(self) -> list[MemoryRecord]:
        return sorted(self._records.values(), key=lambda item: item.memory_id)

    def replace_all(self, records: list[MemoryRecord]) -> None:
        self._records = {record.memory_id: record for record in records}

    def clear(self) -> None:
        self._records.clear()


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._snapshots: list[dict[str, object]] = []

    def save(self, snapshot: dict[str, object]) -> None:
        self._snapshots.append(deepcopy(snapshot))

    def latest(self) -> dict[str, object] | None:
        if not self._snapshots:
            return None
        return deepcopy(self._snapshots[-1])

    def clear(self) -> None:
        self._snapshots.clear()
