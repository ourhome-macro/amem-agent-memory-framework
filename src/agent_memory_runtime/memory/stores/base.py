from __future__ import annotations

from typing import Protocol

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord


class EventStore(Protocol):
    def append(self, event: Event) -> Event:
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

