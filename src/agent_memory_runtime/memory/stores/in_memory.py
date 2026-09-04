from __future__ import annotations

from copy import deepcopy
from threading import RLock

from agent_memory_runtime.audit.stores.in_memory import InMemoryAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord, normalize_record_temperature
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.exceptions import EventConflictError
from agent_memory_runtime.memory.stores.query import select_candidates

__all__ = [
    "InMemoryAuditStore",
    "InMemoryEventStore",
    "InMemoryMemoryStore",
    "InMemorySnapshotStore",
    "InMemoryTombstoneStore",
]


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._event_by_id: dict[str, Event] = {}
        self._lock = RLock()

    def append(self, event: Event) -> Event:
        with self._lock:
            existing = self._event_by_id.get(event.event_id)
            if existing is not None:
                if not existing.is_retry_of(event):
                    raise EventConflictError(
                        f"event_id {event.event_id!r} is already bound to a different event"
                    )
                return existing
            sequence = event.sequence or len(self._events) + 1
            stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
            self._events.append(stored)
            self._event_by_id[stored.event_id] = stored
            return stored

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return self._event_by_id.get(event_id)

    def list_events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._event_by_id.clear()


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def upsert(self, record: MemoryRecord) -> None:
        record = normalize_record_temperature(record)
        self._records[record.memory_id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._records.get(memory_id)

    def delete(self, memory_id: str) -> None:
        self._records.pop(memory_id, None)

    def list_records(self) -> list[MemoryRecord]:
        return sorted(self._records.values(), key=lambda item: item.memory_id)

    def get_many(self, memory_ids: list[str] | tuple[str, ...]) -> list[MemoryRecord]:
        return [self._records[memory_id] for memory_id in memory_ids if memory_id in self._records]

    def query_records(
        self,
        query: MemoryQuery,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return select_candidates(
            self.list_records(),
            query,
            limit=limit,
            offset=offset,
        )

    def replace_all(self, records: list[MemoryRecord]) -> None:
        self._records = {
            record.memory_id: record
            for record in (normalize_record_temperature(item) for item in records)
        }

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

    def prune(self, *, keep_last: int) -> int:
        keep = max(0, keep_last)
        removed = max(0, len(self._snapshots) - keep)
        if removed:
            self._snapshots = self._snapshots[-keep:] if keep else []
        return removed


class InMemoryTombstoneStore:
    def __init__(self) -> None:
        self._tombstones: dict[str, MemoryTombstone] = {}

    def put(self, tombstone: MemoryTombstone) -> None:
        current = self._tombstones.get(tombstone.memory_id)
        if (
            current is None
            or tombstone.deleted_through_sequence >= current.deleted_through_sequence
        ):
            self._tombstones[tombstone.memory_id] = MemoryTombstone.from_dict(
                tombstone.to_dict()
            )

    def get(self, memory_id: str) -> MemoryTombstone | None:
        value = self._tombstones.get(memory_id)
        return None if value is None else MemoryTombstone.from_dict(value.to_dict())

    def list_tombstones(self) -> list[MemoryTombstone]:
        return [
            MemoryTombstone.from_dict(item.to_dict())
            for item in sorted(
                self._tombstones.values(),
                key=lambda value: value.memory_id,
            )
        ]

    def clear(self) -> None:
        self._tombstones.clear()
