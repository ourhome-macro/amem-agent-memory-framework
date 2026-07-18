from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
                """
            )


class SQLiteEventStore(SQLiteStore):
    def append(self, event: Event) -> Event:
        with self._connect() as connection:
            current = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()
            sequence = event.sequence or int(current[0]) + 1
            stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
            connection.execute(
                "INSERT INTO events(sequence, event_id, payload) VALUES (?, ?, ?)",
                (
                    stored.sequence,
                    stored.event_id,
                    json.dumps(stored.to_dict(), ensure_ascii=True, sort_keys=True),
                ),
            )
        return stored

    def list_events(self) -> list[Event]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
        return [Event.from_dict(json.loads(row[0])) for row in rows]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM events")


class SQLiteMemoryStore(SQLiteStore):
    def upsert(self, record: MemoryRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO memories(memory_id, payload) VALUES (?, ?)",
                (
                    record.memory_id,
                    json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True),
                ),
            )

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return MemoryRecord.from_dict(json.loads(row[0]))

    def list_records(self) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM memories ORDER BY memory_id").fetchall()
        return [MemoryRecord.from_dict(json.loads(row[0])) for row in rows]

    def replace_all(self, records: list[MemoryRecord]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM memories")
            connection.executemany(
                "INSERT INTO memories(memory_id, payload) VALUES (?, ?)",
                [
                    (
                        record.memory_id,
                        json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True),
                    )
                    for record in records
                ],
            )

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM memories")


class SQLiteSnapshotStore(SQLiteStore):
    def save(self, snapshot: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO snapshots(payload) VALUES (?)",
                (json.dumps(snapshot, ensure_ascii=True, sort_keys=True),),
            )

    def latest(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(json.loads(row[0]))

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM snapshots")

