from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from agent_memory_runtime.audit.stores.jsonl import JsonlAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.exceptions import EventConflictError
from agent_memory_runtime.memory.stores.query import select_candidates

__all__ = [
    "JsonlAuditStore",
    "JsonlEventStore",
    "JsonlMemoryStore",
    "JsonlSnapshotStore",
    "JsonlTombstoneStore",
]


class JsonlEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.RLock()

    def append(self, event: Event) -> Event:
        with self._lock:
            events = self.list_events()
            existing = next((item for item in events if item.event_id == event.event_id), None)
            if existing is not None:
                if not existing.is_retry_of(event):
                    raise EventConflictError(
                        f"event_id {event.event_id!r} is already bound to a different event"
                    )
                return existing
            sequence = event.sequence or len(events) + 1
            stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(stored.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return stored

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return next((item for item in self.list_events() if item.event_id == event_id), None)

    def list_events(self) -> list[Event]:
        with self._lock:
            events: list[Event] = []
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        events.append(Event.from_dict(json.loads(line)))
            return events

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


class JsonlMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def upsert(self, record: MemoryRecord) -> None:
        records = {item.memory_id: item for item in self.list_records()}
        records[record.memory_id] = record
        self.replace_all(sorted(records.values(), key=lambda item: item.memory_id))

    def get(self, memory_id: str) -> MemoryRecord | None:
        return next((item for item in self.list_records() if item.memory_id == memory_id), None)

    def list_records(self) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(MemoryRecord.from_dict(json.loads(line)))
        return records

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
        with self.path.open("w", encoding="utf-8") as handle:
            for record in sorted(records, key=lambda item: item.memory_id):
                handle.write(json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


class JsonlSnapshotStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def save(self, snapshot: dict[str, object]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=True, sort_keys=True) + "\n")

    def latest(self) -> dict[str, object] | None:
        latest: dict[str, Any] | None = None
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    latest = json.loads(line)
        return latest

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")

    def prune(self, *, keep_last: int) -> int:
        keep = max(0, keep_last)
        with self.path.open("r", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        removed = max(0, len(lines) - keep)
        if not removed:
            return 0
        retained = lines[-keep:] if keep else []
        temporary = self.path.with_name(f".{self.path.name}.prune.tmp")
        temporary.write_text("".join(retained), encoding="utf-8")
        os.replace(temporary, self.path)
        return removed


class JsonlTombstoneStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.RLock()

    def put(self, tombstone: MemoryTombstone) -> None:
        with self._lock:
            records = {item.memory_id: item for item in self.list_tombstones()}
            current = records.get(tombstone.memory_id)
            if (
                current is not None
                and current.deleted_through_sequence > tombstone.deleted_through_sequence
            ):
                return
            records[tombstone.memory_id] = tombstone
            temporary = self.path.with_name(f".{self.path.name}.replace.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                for item in sorted(records.values(), key=lambda value: value.memory_id):
                    handle.write(
                        json.dumps(item.to_dict(), ensure_ascii=True, sort_keys=True) + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)

    def get(self, memory_id: str) -> MemoryTombstone | None:
        with self._lock:
            return next(
                (item for item in self.list_tombstones() if item.memory_id == memory_id),
                None,
            )

    def list_tombstones(self) -> list[MemoryTombstone]:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                return [
                    MemoryTombstone.from_dict(json.loads(line))
                    for line in handle
                    if line.strip()
                ]

    def clear(self) -> None:
        with self._lock:
            self.path.write_text("", encoding="utf-8")
