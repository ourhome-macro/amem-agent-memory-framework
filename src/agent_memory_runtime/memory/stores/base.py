from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from agent_memory_runtime.audit.stores.base import AuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.domain.tombstone import MemoryTombstone

__all__ = [
    "AuditStore",
    "EventStore",
    "MemoryStore",
    "SnapshotStore",
    "TombstoneStore",
    "TransactionManager",
]


class EventStore(Protocol):
    def append(self, event: Event) -> Event:
        ...

    def get(self, event_id: str) -> Event | None:
        ...

    def list_events(self) -> list[Event]:
        ...

    def clear(self) -> None:
        ...


class MemoryStore(Protocol):
    def upsert(self, record: MemoryRecord) -> None:
        ...

    def get(self, memory_id: str) -> MemoryRecord | None:
        ...

    def list_records(self) -> list[MemoryRecord]:
        ...

    def query_records(
        self,
        query: MemoryQuery,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        ...

    def replace_all(self, records: list[MemoryRecord]) -> None:
        ...

    def clear(self) -> None:
        ...


class SnapshotStore(Protocol):
    def save(self, snapshot: dict[str, object]) -> None:
        ...

    def latest(self) -> dict[str, object] | None:
        ...

    def clear(self) -> None:
        ...

    def prune(self, *, keep_last: int) -> int:
        ...


class TombstoneStore(Protocol):
    def put(self, tombstone: MemoryTombstone) -> None:
        ...

    def get(self, memory_id: str) -> MemoryTombstone | None:
        ...

    def list_tombstones(self) -> list[MemoryTombstone]:
        ...

    def clear(self) -> None:
        ...


class TransactionManager(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        ...
