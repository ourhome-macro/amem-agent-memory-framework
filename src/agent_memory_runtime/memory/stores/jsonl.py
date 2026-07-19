from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_runtime.audit.stores.jsonl import JsonlAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord

__all__ = [
    "JsonlAuditStore",
    "JsonlEventStore",
    "JsonlMemoryStore",
    "JsonlSnapshotStore",
]


class JsonlEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, event: Event) -> Event:
        sequence = event.sequence or len(self.list_events()) + 1
        stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stored.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")
        return stored

    def list_events(self) -> list[Event]:
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
